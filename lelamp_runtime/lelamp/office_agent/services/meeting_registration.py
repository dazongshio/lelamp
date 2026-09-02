from __future__ import annotations

import shutil
import smtplib
import subprocess
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from ..audio_api import OpenAIAudioAPI
from ..dashscope_asr import DashScopeASR
from ..groq_asr import GroqASR
from ..meeting_voice_skill import MeetingVoiceCommand, default_meeting_title
from ..tingwu_meeting import TingwuMeetingError, normalize_minutes_payload, redact_sensitive_text, sanitize_event_payload
from ..utils import safe_filename
from ..routes._base import ApiError, RequestContext

def _helper(name:str):
    from .. import web_console
    return getattr(web_console,name)
def extract_email_subject(*a,**kw): return _helper("extract_email_subject")(*a,**kw)
def list_string(*a,**kw): return _helper("list_string")(*a,**kw)
def safe_int(*a,**kw): return _helper("safe_int")(*a,**kw)
def status_to_audit(*a,**kw): return _helper("status_to_audit")(*a,**kw)
def tingwu_capture_status(*a,**kw): return _helper("tingwu_capture_status")(*a,**kw)


class MeetingRegistrationMixin:
    def register_tingwu_outputs(self, session: dict[str, object], ctx: RequestContext, *, run_followup: bool) -> dict[str, object]:
        title = str(session.get("title") or "Tingwu Meeting")
        meeting_id = str(session.get("meeting_id") or "")
        transcript_path = str(session.get("transcript_path") or "")
        transcript_workspace = self.workspace_relative_path(transcript_path)
        capture_status = tingwu_capture_status(session)
        provider_status = str(session.get("status") or "completed")
        ai_minutes = session.get("ai_minutes") if isinstance(session.get("ai_minutes"), dict) else {}
        tingwu_minutes = normalize_minutes_payload(ai_minutes) if ai_minutes else {"summary": "", "decisions": [], "action_items": []}
        transcript_file_has_content = self.tingwu_transcript_file_has_content(transcript_path)
        self.upsert_meeting_step_task(
            title,
            transcript_workspace,
            "realtime_capture",
            capture_status,
            session,
            meeting_id=meeting_id,
            provider="tongyi_tingwu",
        )
        fallback_transcript: dict[str, object] | None = None
        empty_transcript_import: dict[str, object] | None = None
        parsed: dict[str, object] | None = None
        parsed_count = 0
        parse_error = ""

        if transcript_workspace:
            try:
                parsed = self.runtime.meeting.parse_transcript_file(transcript_workspace, title, list_string(session.get("participants")) or ["Unknown"])
                parsed_count = safe_int(parsed.get("parsed_count"), 0)
            except Exception as exc:
                parsed = None
                parsed_count = 0
                parse_error = str(exc)[:1000]
                self.record_audit("meeting_realtime_import_transcript", "error", transcript_workspace, {"error": str(exc)[:1000]}, ctx)

        if parsed_count <= 0:
            fallback_transcript = self.create_tingwu_asr_fallback_transcript(session, ctx)
            if fallback_transcript.get("status") == "completed":
                transcript_path = str(fallback_transcript.get("path") or transcript_path)
                transcript_workspace = str(fallback_transcript.get("workspace_name") or self.workspace_relative_path(transcript_path))
                transcript_file_has_content = self.tingwu_transcript_file_has_content(transcript_path)
                session = {
                    **session,
                    "transcript_path": transcript_path,
                    "realtime_transcript": fallback_transcript.get("transcript_text") or "",
                    "transcript_fallback": fallback_transcript,
                }
                try:
                    parsed = self.runtime.meeting.parse_transcript_file(transcript_workspace, title, list_string(session.get("participants")) or ["Unknown"])
                    parsed_count = safe_int(parsed.get("parsed_count"), 0)
                except Exception as exc:
                    parsed = None
                    parsed_count = 0
                    parse_error = str(exc)[:1000]
                    fallback_transcript = {**fallback_transcript, "parse_error": str(exc)}

        if parsed_count > 0 and parsed is not None and transcript_workspace:
            self.upsert_meeting_step_task(
                title,
                transcript_workspace,
                "import_transcript",
                "completed",
                {
                    **parsed,
                    "source": "asr_fallback" if fallback_transcript else "tongyi_tingwu_realtime",
                    **({"fallback_transcript": fallback_transcript} if fallback_transcript else {}),
                },
                meeting_id=meeting_id,
                provider="tongyi_tingwu",
            )
        elif transcript_workspace and not transcript_file_has_content and Path(transcript_path).expanduser().is_file() and provider_status in {"completed", "stopped"}:
            empty_transcript_import = self.build_empty_tingwu_import_result(
                session=session,
                transcript_workspace=transcript_workspace,
                parsed_count=parsed_count,
                fallback_transcript=fallback_transcript,
                parse_error=parse_error,
            )
            self.upsert_meeting_step_task(
                title,
                transcript_workspace,
                "import_transcript",
                "completed",
                empty_transcript_import,
                meeting_id=meeting_id,
                provider="tongyi_tingwu",
            )
        else:
            import_error = "Tingwu transcript is outside workspace, missing, or could not be parsed."
            if transcript_workspace and not transcript_file_has_content and Path(transcript_path).expanduser().is_file():
                import_error = "Tingwu transcript produced no speaker turns; OpenClaw follow-up was blocked."
            self.upsert_meeting_step_task(
                title,
                transcript_path,
                "import_transcript",
                "failed",
                {
                    "status": "failed",
                    "error": import_error,
                    "transcript_path": transcript_path,
                    "parse_error": parse_error,
                    "fallback_transcript": fallback_transcript,
                },
                meeting_id=meeting_id,
                provider="tongyi_tingwu",
            )
        result_status = provider_status or "completed"
        minutes_step_status = result_status
        minutes_result: dict[str, object] = {
            "status": result_status,
            "provider_status": provider_status,
            "title": title,
            "provider": "tongyi_tingwu",
            "meeting_id": meeting_id,
            "provider_task_id": str(session.get("task_id") or ""),
            "transcript_path": transcript_path,
            "transcript": transcript_workspace,
            "tingwu_minutes_path": str(session.get("minutes_path") or ""),
            "tingwu_minutes": tingwu_minutes,
            "ai_minutes": ai_minutes,
            "realtime_transcript": str(session.get("realtime_transcript") or ""),
            "transcript_fallback": fallback_transcript,
            "output_dir": str(session.get("output_dir") or ""),
            "provider_error": str(session.get("error") or ""),
        }
        if parsed_count <= 0 and empty_transcript_import is None:
            minutes_result["status"] = "failed"
            minutes_step_status = "failed"
            minutes_result["openclaw_status"] = "failed"
            minutes_result["error"] = "Transcript import failed or produced no speaker turns; OpenClaw minutes were not generated."
        else:
            try:
                if parsed_count > 0:
                    generated = self.runtime.meeting.generate_minutes()
                    generated = self.materialize_tingwu_workspace_file(
                        generated,
                        output_dir=str(session.get("output_dir") or ""),
                        filename="openclaw_minutes.md",
                        meeting_id=meeting_id,
                        ctx=ctx,
                    )
                else:
                    generated = self.create_empty_tingwu_openclaw_minutes(
                        session=session,
                        transcript_workspace=transcript_workspace,
                        import_result=empty_transcript_import or {},
                        ctx=ctx,
                    )
                if provider_status == "completed":
                    result_status = "completed"
                    minutes_step_status = "completed"
                elif provider_status == "stopped":
                    result_status = "stopped"
                    minutes_step_status = "completed"
                else:
                    result_status = provider_status or "completed"
                    minutes_step_status = result_status
                minutes_result.update({
                    "status": result_status,
                    "openclaw_status": "completed",
                    "path": generated.get("path"),
                    "turn_count": generated.get("turn_count"),
                    "decisions": generated.get("decisions", []),
                    "action_items": generated.get("action_items", []),
                    "speaker_counts": generated.get("speaker_counts", {}),
                })
                if generated.get("content_status"):
                    minutes_result["content_status"] = generated.get("content_status")
                    minutes_result["message"] = str(generated.get("message") or "")
                if generated.get("diagnostics"):
                    minutes_result["diagnostics"] = generated.get("diagnostics")
                if generated.get("quality_notes"):
                    minutes_result["quality_notes"] = generated.get("quality_notes")
                if generated.get("transcript_fallback"):
                    minutes_result["transcript_fallback"] = generated.get("transcript_fallback")
                if provider_status == "stopped" and not generated.get("content_status"):
                    minutes_result["message"] = "Realtime capture stopped; Tongyi Tingwu AI minutes have not been fetched yet."
                elif provider_status != "completed":
                    minutes_result["error"] = str(session.get("error") or "Tongyi Tingwu provider did not complete; OpenClaw fallback outputs were generated from transcript.")
            except Exception as exc:
                minutes_result["status"] = "failed"
                minutes_step_status = "failed"
                minutes_result["openclaw_status"] = "failed"
                minutes_result["error"] = str(exc)
        minutes_task = self.upsert_meeting_step_task(
            title,
            transcript_workspace or transcript_path,
            "minutes",
            minutes_step_status,
            minutes_result,
            meeting_id=meeting_id,
            provider="tongyi_tingwu",
        )
        outputs: list[dict[str, object]] = []
        for key, output_type in (
            ("transcript_path", "markdown"),
            ("audio_path", "wav"),
            ("minutes_path", "markdown"),
        ):
            path_value = str(session.get(key) or "")
            if path_value:
                outputs.append({"path": path_value, "type": output_type, "source": "tongyi_tingwu"})
        output_dir = str(session.get("output_dir") or "")
        if output_dir:
            outputs.append({"path": str(Path(output_dir) / "session.json"), "type": "json", "source": "tongyi_tingwu"})
        outputs.extend(collect_outputs(minutes_result))
        followup_task: dict[str, object] | None = None
        followup: dict[str, object] | None = None
        if minutes_result.get("openclaw_status") == "completed":
            decisions = [str(item) for item in list_string(minutes_result.get("decisions"))]
            action_items = [str(item) for item in list_string(minutes_result.get("action_items"))]
            decisions_output = self.write_meeting_items_output(
                title,
                transcript_workspace,
                "decisions",
                decisions,
                minutes_result,
                output_dir=str(session.get("output_dir") or ""),
                meeting_id=meeting_id,
                ctx=ctx,
            )
            self.upsert_meeting_step_task(
                title,
                transcript_workspace,
                "decisions",
                "completed",
                decisions_output,
                meeting_id=meeting_id,
                provider="tongyi_tingwu",
            )
            outputs.extend(collect_outputs(decisions_output))
            actions_output = self.write_meeting_items_output(
                title,
                transcript_workspace,
                "action_items",
                action_items,
                minutes_result,
                output_dir=str(session.get("output_dir") or ""),
                meeting_id=meeting_id,
                ctx=ctx,
            )
            self.upsert_meeting_step_task(
                title,
                transcript_workspace,
                "action_items",
                "completed",
                actions_output,
                meeting_id=meeting_id,
                provider="tongyi_tingwu",
            )
            outputs.extend(collect_outputs(actions_output))

            if run_followup and transcript_workspace:
                try:
                    reminders: dict[str, object] | None = None
                    projection: dict[str, object] | None = None
                    if minutes_result.get("content_status") == "no_speech_detected":
                        followup = self.create_empty_tingwu_followup_outputs(
                            session=session,
                            minutes_result=minutes_result,
                            transcript_workspace=transcript_workspace,
                            ctx=ctx,
                        )
                        projection = followup.get("projection") if isinstance(followup.get("projection"), dict) else None
                        reminders = followup.get("reminders") if isinstance(followup.get("reminders"), dict) else None
                    else:
                        projection_dir_before = self.latest_projection_mtime()
                        package = self.runtime.p0.generate_meeting_followup_package(
                            recipient="待填写收件人",
                            create_reminders=True,
                            render_projection=True,
                        )
                        projection = package.get("projection") if isinstance(package.get("projection"), dict) else None
                        if projection is not None:
                            projection = self.materialize_tingwu_projection_output(
                                projection,
                                meeting_id=meeting_id,
                                projection_dir_before=projection_dir_before,
                                ctx=ctx,
                            )
                            package["projection"] = projection
                        followup = {
                            **package,
                            "status": "completed",
                            "source_status": str(package.get("status") or ""),
                            "step": "followup",
                            "meeting_id": meeting_id,
                        }
                        followup = self.materialize_tingwu_followup_outputs(followup, session=session, ctx=ctx)
                        reminders = followup.get("reminders") if isinstance(followup.get("reminders"), dict) else None
                        package_minutes = followup.get("minutes") if isinstance(followup.get("minutes"), dict) else {}
                        package_transcript = followup.get("transcript") if isinstance(followup.get("transcript"), dict) else {}
                        followup["required_output_paths"] = {
                            "openclaw_minutes": str(package_minutes.get("path") or ""),
                            "transcript_export": str(package_transcript.get("path") or ""),
                            "email_draft": str(followup.get("email_draft_path") or ""),
                            "reminders": str(reminders.get("store_path") if isinstance(reminders, dict) else ""),
                            "projection_confirmation": str(projection.get("path") if isinstance(projection, dict) else ""),
                        }
                    generated_title = self.generate_meeting_content_title(session, fallback=title)
                    final_result = self.materialize_meeting_final_markdown(
                        title=generated_title,
                        meeting_id=meeting_id,
                        started_at=str(session.get("started_at") or session.get("created_at") or ""),
                        followup=followup,
                        minutes_result=minutes_result,
                        ctx=ctx,
                    )
                    final_result["generated_title"] = generated_title
                    followup["final_result"] = final_result
                    followup_task = self.upsert_meeting_step_task(
                        title,
                        transcript_workspace,
                        "followup",
                        "completed",
                        followup,
                        meeting_id=meeting_id,
                        provider="tongyi_tingwu",
                    )
                    self.upsert_meeting_step_task(
                        title,
                        transcript_workspace,
                        "final_result",
                        "completed",
                        final_result,
                        meeting_id=meeting_id,
                        provider="tongyi_tingwu",
                    )
                    outputs.extend(collect_outputs(followup))
                    if reminders is not None:
                        reminders_output = {
                            "status": "completed",
                            "step": "reminders",
                            "meeting_id": meeting_id,
                            "message": "已创建本地 reminder 草稿，不会同步外部日历或自动通知。",
                            **reminders,
                        }
                        self.upsert_meeting_step_task(
                            title,
                            transcript_workspace,
                            "reminders",
                            "completed",
                            reminders_output,
                            meeting_id=meeting_id,
                            provider="tongyi_tingwu",
                        )
                        outputs.extend(collect_outputs(reminders_output))
                    projection = followup.get("projection") if isinstance(followup.get("projection"), dict) else None
                    if projection is not None:
                        projection_output = {
                            "status": "completed",
                            "step": "projection_confirmation",
                            "meeting_id": meeting_id,
                            "projection": projection,
                            "path": projection.get("path"),
                            "decisions": decisions,
                            "action_items": action_items,
                        }
                        self.upsert_meeting_step_task(
                            title,
                            transcript_workspace,
                            "projection_confirmation",
                            "completed",
                            projection_output,
                            meeting_id=meeting_id,
                            provider="tongyi_tingwu",
                        )
                        outputs.extend(collect_outputs(projection_output))
                except Exception as exc:
                    followup = {"status": "failed", "step": "followup", "meeting_id": meeting_id, "error": str(exc)}
                    self.upsert_meeting_step_task(
                        title,
                        transcript_workspace,
                        "followup",
                        "failed",
                        followup,
                        meeting_id=meeting_id,
                        provider="tongyi_tingwu",
                    )
        self.record_audit(
            "meeting_realtime_import_transcript",
            status_to_audit(str(minutes_result.get("status"))),
            transcript_workspace or transcript_path,
            {"meeting_id": meeting_id, "task_id": minutes_task["task_id"], "outputs": len(outputs)},
            ctx,
        )
        job = self.find_aggregated_meeting_job(transcript_workspace or transcript_path, meeting_id=meeting_id) or self.meeting_job_from_task(minutes_task)
        manifest_path = self.write_tingwu_meeting_manifest(
            session=session,
            minutes=minutes_result,
            followup=followup,
            outputs=outputs,
            job=job,
            ctx=ctx,
        )
        if manifest_path:
            outputs.append({"path": manifest_path, "type": "json"})
            if followup_task is not None and isinstance(followup, dict):
                self.update_task(str(followup_task.get("task_id") or ""), output={**followup, "manifest_path": manifest_path})
        return {
            "status": minutes_result.get("status"),
            "provider_status": minutes_result.get("provider_status"),
            "openclaw_status": minutes_result.get("openclaw_status"),
            "content_status": minutes_result.get("content_status"),
            "task_id": minutes_task["task_id"],
            "job": job,
            "session": session,
            "minutes": minutes_result,
            "followup": followup,
            "manifest_path": manifest_path,
            "outputs": outputs,
        }
    def build_empty_tingwu_import_result(
        self,
        *,
        session: dict[str, object],
        transcript_workspace: str,
        parsed_count: int,
        fallback_transcript: dict[str, object] | None,
        parse_error: str = "",
    ) -> dict[str, object]:
        fallback_reason = ""
        if isinstance(fallback_transcript, dict):
            fallback_reason = str(fallback_transcript.get("reason") or fallback_transcript.get("status") or "")
        diagnostics = self.empty_tingwu_diagnostics(session=session, fallback_transcript=fallback_transcript)
        message = (
            "实时会议音频和转写文件已保存，但通义听悟实时转写没有返回最终发言；"
            "本地 ASR fallback 也没有识别到可用语音。OpenClaw 将生成空会议诊断纪要，不伪造发言。"
        )
        return {
            "status": "completed",
            "content_status": "no_speech_detected",
            "message": message,
            "transcript": transcript_workspace,
            "parsed_count": parsed_count,
            "meeting_mode_enabled": True,
            "fallback_transcript": fallback_transcript,
            "fallback_reason": fallback_reason,
            "parse_error": parse_error,
            "diagnostics": diagnostics,
            "quality_notes": [
                "没有可导入的 speaker turns。",
                "决策和行动项保持为空，需要重新录制或导入会议文本后再生成正式纪要。",
            ],
            "confirmation": {"required": False},
        }
    def tingwu_transcript_file_has_content(self, transcript_path: str) -> bool:
        value = str(transcript_path or "").strip()
        if not value:
            return False
        path = Path(value).expanduser()
        if not path.is_file():
            return False
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        for line in text.splitlines():
            clean = line.strip()
            if clean and not clean.startswith("#"):
                return True
        return False
    def create_empty_tingwu_openclaw_minutes(
        self,
        *,
        session: dict[str, object],
        transcript_workspace: str,
        import_result: dict[str, object],
        ctx: RequestContext,
    ) -> dict[str, object]:
        title = str(session.get("title") or "Tingwu Meeting")
        meeting_id = str(session.get("meeting_id") or "")
        diagnostics = import_result.get("diagnostics") if isinstance(import_result.get("diagnostics"), dict) else self.empty_tingwu_diagnostics(session=session, fallback_transcript=None)
        quality_notes = [str(item) for item in list_string(import_result.get("quality_notes"))] or [
            "通义听悟和本地 ASR 均未识别到可用发言。",
        ]
        lines = [
            f"# {title}",
            "",
            "## 状态",
            "- content_status: no_speech_detected",
            "- OpenClaw 后处理已完成：没有生成虚假发言、决策或行动项。",
            "",
            "## 诊断",
            f"- transcript: {transcript_workspace or '-'}",
            f"- audio_seconds: {diagnostics.get('audio_seconds', 0)}",
            f"- audio_bytes: {diagnostics.get('audio_bytes', 0)}",
            f"- websocket_audio_frames: {diagnostics.get('websocket_audio_frames', 0)}",
            f"- audio_rms: {diagnostics.get('audio_rms', 0)}",
            f"- audio_peak: {diagnostics.get('audio_peak', 0)}",
            f"- realtime_final_turns: {diagnostics.get('realtime_final_turns', 0)}",
            f"- asr_fallback_status: {diagnostics.get('asr_fallback_status', '-')}",
            f"- asr_fallback_reason: {diagnostics.get('asr_fallback_reason', '-')}",
            "",
            "## Decisions",
            "- 暂无明确决策，需要重新录制或导入会议文本后补充。",
            "",
            "## Action Items",
            "- 暂无明确待办，需要重新录制或导入会议文本后补充。",
            "",
            "## Quality Notes",
            *[f"- {item}" for item in quality_notes],
            "",
        ]
        path = self.write_meeting_output_text(
            str(session.get("output_dir") or ""),
            "openclaw_minutes.md",
            "\n".join(lines),
            action="meeting.minutes_generate",
            meeting_id=meeting_id,
            ctx=ctx,
        )
        payload = {
            "status": "completed",
            "provider": "openclaw",
            "content_status": "no_speech_detected",
            "path": str(path),
            "title": title,
            "turn_count": 0,
            "speaker_counts": {},
            "decisions": [],
            "action_items": [],
            "diagnostics": diagnostics,
            "quality_notes": quality_notes,
            "transcript_fallback": import_result.get("fallback_transcript"),
            "message": "No usable speaker turns were available; generated an auditable empty meeting report.",
        }
        self.record_audit("meeting.minutes_generated", "ok", str(path), payload, ctx)
        return payload
    def create_empty_tingwu_followup_outputs(
        self,
        *,
        session: dict[str, object],
        minutes_result: dict[str, object],
        transcript_workspace: str,
        ctx: RequestContext,
    ) -> dict[str, object]:
        title = str(session.get("title") or "Tingwu Meeting")
        meeting_id = str(session.get("meeting_id") or "")
        output_dir = str(session.get("output_dir") or "")
        diagnostics = minutes_result.get("diagnostics") if isinstance(minutes_result.get("diagnostics"), dict) else {}
        transcript_export_payload = {
            "title": title,
            "participants": list_string(session.get("participants")) or ["Unknown"],
            "started_at": session.get("started_at") or session.get("created_at") or "",
            "transcript": [],
            "content_status": "no_speech_detected",
            "source_transcript": transcript_workspace,
            "diagnostics": diagnostics,
        }
        transcript_export_path = self.write_meeting_output_json(
            output_dir,
            "followup_transcript.json",
            transcript_export_payload,
            action="meeting.transcript_export",
            meeting_id=meeting_id,
            ctx=ctx,
        )
        email_body = "\n".join(
            [
                f"# {title} Follow-up Email Draft",
                "",
                "To: 待填写收件人",
                f"Subject: {title} - 会议记录待补充",
                "",
                "本次实时会议已保存音频和诊断，但没有识别到可用发言。",
                "",
                "请重新录制会议、导入会议文本，或人工补充纪要后再发送正式会后邮件。",
                "",
                "## Diagnostics",
                f"- audio_seconds: {diagnostics.get('audio_seconds', 0)}",
                f"- websocket_audio_frames: {diagnostics.get('websocket_audio_frames', 0)}",
                f"- asr_fallback_status: {diagnostics.get('asr_fallback_status', '-')}",
                f"- asr_fallback_reason: {diagnostics.get('asr_fallback_reason', '-')}",
                "",
            ]
        )
        email_path = self.write_meeting_output_text(
            output_dir,
            "followup_email.md",
            email_body,
            action="p0.meeting_followup_email_write",
            meeting_id=meeting_id,
            ctx=ctx,
        )
        reminders = {
            "status": "completed",
            "count": 0,
            "items": [],
            "message": "没有识别到行动项，因此没有创建 reminder 草稿。",
        }
        reminder_path = self.write_meeting_output_json(
            output_dir,
            "reminders.json",
            reminders,
            action="meeting.reminders_snapshot",
            meeting_id=meeting_id,
            ctx=ctx,
        )
        reminders = {**reminders, "store_path": str(reminder_path)}
        projection = self.runtime.projection.render_status_card(
            f"{title} - 会议诊断",
            "no_speech_detected",
            [
                "音频和诊断已保存。",
                "没有识别到可用发言。",
                "请重新录制或导入会议文本后生成正式纪要。",
            ],
            accent="amber",
        )
        projection = self.materialize_tingwu_projection_output(
            projection,
            meeting_id=meeting_id,
            projection_dir_before=0,
            ctx=ctx,
        )
        followup = {
            "status": "completed",
            "source_status": "no_speech_detected",
            "step": "followup",
            "meeting_id": meeting_id,
            "content_status": "no_speech_detected",
            "minutes": minutes_result,
            "transcript": {"status": "completed", "path": str(transcript_export_path), "content_status": "no_speech_detected"},
            "email": {"status": "completed", "email_draft_path": str(email_path), "content_status": "no_speech_detected"},
            "email_draft_path": str(email_path),
            "reminders": reminders,
            "projection": projection,
            "required_output_paths": {
                "openclaw_minutes": str(minutes_result.get("path") or ""),
                "transcript_export": str(transcript_export_path),
                "email_draft": str(email_path),
                "reminders": str(reminder_path),
                "projection_confirmation": str(projection.get("path") if isinstance(projection, dict) else ""),
            },
            "message": "没有识别到可用发言；已生成诊断 follow-up 草稿，不自动发送。",
        }
        self.record_audit("p0.meeting_followup_package", "ok", title, followup, ctx)
        return followup
    def empty_tingwu_diagnostics(
        self,
        *,
        session: dict[str, object],
        fallback_transcript: dict[str, object] | None,
    ) -> dict[str, object]:
        transcript_items = session.get("transcript") if isinstance(session.get("transcript"), list) else []
        fallback = fallback_transcript if isinstance(fallback_transcript, dict) else {}
        return {
            "provider_status": str(session.get("status") or ""),
            "audio_seconds": float(session.get("audio_seconds") or 0),
            "audio_bytes": safe_int(session.get("audio_bytes"), 0),
            "websocket_audio_frames": safe_int(session.get("websocket_audio_frames"), 0),
            "audio_rms": safe_int(session.get("audio_rms"), 0),
            "audio_peak": safe_int(session.get("audio_peak"), 0),
            "realtime_final_turns": len([item for item in transcript_items if isinstance(item, dict) and item.get("final")]),
            "realtime_turns": len(transcript_items),
            "asr_fallback_status": str(fallback.get("status") or "not_run"),
            "asr_fallback_reason": str(fallback.get("reason") or ""),
            "asr_fallback_provider": str(fallback.get("provider") or self.runtime.config.asr_provider or ""),
        }
