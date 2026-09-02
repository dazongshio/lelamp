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


class MeetingCommandsMixin:
    def execute_meeting_voice_command(
        self,
        command: MeetingVoiceCommand,
        text: str,
        ctx: RequestContext,
    ) -> dict[str, object]:
        try:
            result = self._execute_meeting_voice_command(command, ctx)
        except ApiError as exc:
            result = {
                "status": "blocked",
                "reply": self.meeting_voice_error_reply(command, exc.message),
                "command": command.as_dict(),
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            }
        except Exception as exc:
            result = {
                "status": "error",
                "reply": self.meeting_voice_error_reply(command, str(exc)),
                "command": command.as_dict(),
                "error": str(exc)[:1000],
            }
        return {
            "handled": True,
            "text": text,
            "command": command.as_dict(),
            "ai_assistant_kept_online": True,
            "qwen_omni_called": False,
            **result,
        }
    def _execute_meeting_voice_command(self, command: MeetingVoiceCommand, ctx: RequestContext) -> dict[str, object]:
        action = command.action
        if action == "meeting_status":
            meeting_status = self.api_meeting_status(ctx)
            realtime_status = self.api_meeting_realtime_status(None, ctx)
            local_status = self.api_meeting_local_realtime_status(ctx)
            return self.meeting_voice_result(command, self.meeting_status_reply(meeting_status, realtime_status, local_status), {
                "meeting_mode": meeting_status,
                "realtime": realtime_status,
                "local_realtime": local_status,
            })
        if action == "meeting_provider_status":
            provider = self.api_meeting_provider_status(ctx)
            return self.meeting_voice_result(command, self.meeting_provider_reply(provider), {"provider": provider})
        if action == "start_realtime_meeting":
            active = self.tingwu.active_meeting_id()
            if active:
                realtime_status = self.api_meeting_realtime_status(active, ctx)
                return self.meeting_voice_result(command, f"实时会议已经在运行：{active}。AI 助手仍保持在线。", {"realtime": realtime_status}, status="already_running")
            title = command.title or default_meeting_title()
            if title == "LeLamp 实时会议":
                title = default_meeting_title()
            realtime = self.api_meeting_realtime_start(
                {"title": title, "participants": list(command.participants) or ["Unknown"], "max_seconds": command.max_seconds},
                ctx,
            )
            reply = f"已开始实时会议记录：{realtime.get('meeting_id') or title}。AI 助手仍保持在线。"
            return self.meeting_voice_result(command, reply, {"realtime": realtime}, status="running")
        if action == "stop_realtime_meeting":
            active = self.tingwu.active_meeting_id()
            if not active:
                realtime_status = self.api_meeting_realtime_status(None, ctx)
                return self.meeting_voice_result(command, "现在没有正在运行的实时会议。AI 助手仍保持在线。", {"realtime": realtime_status}, status="not_running")
            realtime = self.api_meeting_realtime_stop({"meeting_id": active, "run_followup": True}, ctx)
            reply = f"已停止实时会议记录：{active}。AI 助手仍保持在线。"
            return self.meeting_voice_result(command, reply, {"realtime": realtime}, status=str(realtime.get("status") or "completed"))
        if action == "fetch_realtime_minutes":
            meeting_id = self.latest_realtime_meeting_id()
            if not meeting_id:
                return self.meeting_voice_result(command, "还没有可拉取纪要的实时会议。", {"realtime": {"status": "idle"}}, status="blocked")
            realtime = self.api_meeting_realtime_fetch_minutes({"meeting_id": meeting_id, "run_followup": True}, ctx)
            return self.meeting_voice_result(command, "已拉取听悟 AI 会议结果。", {"realtime": realtime}, status=str(realtime.get("status") or "completed"))
        if action == "enable_meeting_mode":
            title = command.title or default_meeting_title("LeLamp 本地会议")
            meeting = self.api_meeting_mode_enable({"title": title, "participants": list(command.participants) or ["Unknown"]}, ctx)
            return self.meeting_voice_result(command, command.reply, {"meeting_mode": meeting})
        if action == "disable_meeting_mode":
            meeting = self.api_meeting_mode_disable(ctx)
            return self.meeting_voice_result(command, command.reply, {"meeting_mode": meeting})
        if action == "local_realtime_status":
            local = self.api_meeting_local_realtime_status(ctx)
            return self.meeting_voice_result(command, self.local_realtime_reply(local), {"local_realtime": local})
        if action == "export_transcript":
            exported = self.api_meeting_local_realtime_export({"source": "meeting_voice_command"}, ctx)
            return self.meeting_voice_result(command, f"已导出会议转写：{exported.get('workspace_name') or exported.get('transcript_path') or ''}", {"export": exported})
        if action == "generate_minutes":
            minutes = self.api_meeting_minutes({"title": command.title or ""}, ctx)
            return self.meeting_voice_result(command, f"已生成会议纪要：{minutes.get('path') or ''}", {"minutes": minutes})
        if action == "extract_decisions":
            decisions = self.api_meeting_extract_step("decisions", {"title": command.title or ""}, ctx)
            return self.meeting_voice_result(command, f"已提取会议决策：{len(decisions.get('items') or [])} 条。", {"decisions": decisions})
        if action == "extract_action_items":
            action_items = self.api_meeting_extract_step("action_items", {"title": command.title or ""}, ctx)
            return self.meeting_voice_result(command, f"已提取会议待办：{len(action_items.get('items') or [])} 条。", {"action_items": action_items})
        if action == "create_reminders":
            reminders = self.api_meeting_reminders({"title": command.title or ""}, ctx)
            return self.meeting_voice_result(command, f"已生成会议提醒草稿：{reminders.get('count') or 0} 条。", {"reminders": reminders})
        if action == "create_followup_package":
            followup = self.api_meeting_followup({"title": command.title or "", "render_projection": True, "create_reminders": True}, ctx)
            return self.meeting_voice_result(command, "已生成会议跟进包。", {"followup": followup}, status=str(followup.get("status") or "completed"))
        if action == "render_projection_confirmation":
            projection = self.api_meeting_projection_confirmation({"title": command.title or "会议确认"}, ctx)
            return self.meeting_voice_result(command, "已生成会议投影确认页。", {"projection_confirmation": projection})
        if action == "export_package":
            followup = self.api_meeting_export_package({"title": command.title or "", "authorized": True, "render_projection": True, "create_reminders": True}, ctx)
            return self.meeting_voice_result(command, "已导出会议材料包。", {"export_package": followup}, status=str(followup.get("status") or "completed"))
        return self.meeting_voice_result(command, "这个会议命令暂时不支持。", {}, status="unsupported")
    def meeting_voice_result(
        self,
        command: MeetingVoiceCommand,
        reply: str,
        result: dict[str, object],
        *,
        status: str = "completed",
    ) -> dict[str, object]:
        return {
            "status": status,
            "reply": reply,
            "command": command.as_dict(),
            "meeting_result": result,
        }
    def meeting_voice_error_reply(self, command: MeetingVoiceCommand, message: str) -> str:
        if command.action == "start_realtime_meeting":
            return f"实时会议没有启动：{message}。AI 助手仍保持在线。"
        if command.action == "stop_realtime_meeting":
            return f"实时会议没有停止：{message}。AI 助手仍保持在线。"
        if command.action == "fetch_realtime_minutes":
            return f"会议 AI 结果没有生成：{message}"
        return f"会议命令没有完成：{message}"
    def meeting_status_reply(
        self,
        meeting_status: dict[str, object],
        realtime_status: dict[str, object],
        local_status: dict[str, object],
    ) -> str:
        realtime_value = str(realtime_status.get("status") or "idle")
        meeting_mode = "开启" if bool(meeting_status.get("meeting_mode_enabled")) else "关闭"
        turns = safe_int(local_status.get("turn_count"), 0)
        active = str(realtime_status.get("meeting_id") or realtime_status.get("active_meeting_id") or "")
        if realtime_value in {"starting", "running", "stopping", "finalizing"}:
            return f"实时会议正在运行：{active or realtime_value}；本地会议模式{meeting_mode}，已有 {turns} 条转写。AI 助手仍保持在线。"
        return f"当前没有运行中的实时会议；本地会议模式{meeting_mode}，已有 {turns} 条转写。AI 助手仍保持在线。"
    def meeting_provider_reply(self, provider: dict[str, object]) -> str:
        primary = provider.get("providers") if isinstance(provider.get("providers"), dict) else {}
        tingwu = primary.get("tongyi_tingwu") if isinstance(primary.get("tongyi_tingwu"), dict) else {}
        status = str(provider.get("status") or tingwu.get("status") or "unknown")
        active = str(tingwu.get("active_meeting_id") or "")
        suffix = f"，当前会议 {active}" if active else ""
        return f"听悟会议服务状态：{status}{suffix}。"
    def local_realtime_reply(self, local: dict[str, object]) -> str:
        enabled = "开启" if bool(local.get("meeting_mode_enabled")) else "关闭"
        turns = safe_int(local.get("turn_count"), 0)
        speakers = local.get("speaker_counts") if isinstance(local.get("speaker_counts"), dict) else {}
        return f"本地会议模式{enabled}，已有 {turns} 条转写，识别到 {len(speakers)} 个说话人标签。"
    def latest_realtime_meeting_id(self) -> str:
        active = self.tingwu.active_meeting_id()
        if active:
            return active
        for task in self.load_tasks(limit=80):
            task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
            output = task.get("output") if isinstance(task.get("output"), dict) else {}
            if str(task_input.get("provider") or output.get("provider") or "") != "tongyi_tingwu":
                continue
            meeting_id = str(task_input.get("meeting_id") or output.get("meeting_id") or "").strip()
            if meeting_id:
                return meeting_id
        return ""
    def load_meeting_transcript(self, payload: dict[str, Any], ctx: RequestContext, *, action: str) -> tuple[str, str]:
        transcript = payload.get("transcript") or payload.get("file_path")
        if transcript:
            safe = self.ensure_allowed_path(str(transcript), ctx, action=action)
            title = str(payload.get("title") or Path(safe.workspace_name).stem)
            try:
                self.runtime.meeting.parse_transcript_file(
                    safe.workspace_name,
                    title,
                    list_string(payload.get("participants")) or ["Unknown"],
                )
            except ValueError as exc:
                self.record_audit(action, "blocked", safe.workspace_name, {"reason": str(exc)}, ctx)
                raise ApiError("invalid_meeting_transcript", str(exc), status=400) from exc
            return safe.workspace_name, title
        status = self.runtime.meeting.status()
        title = str(payload.get("title") or status.get("active_title") or "Meeting")
        return "active_meeting", title
    def meeting_highlights_path(self, meeting_id: str) -> Path:
        root = (self.runtime.config.workspace_dir / "meetings" / safe_filename(meeting_id, default="meeting")).resolve()
        expected = (self.runtime.config.workspace_dir / "meetings").resolve()
        if not root.is_relative_to(expected):
            raise ApiError("invalid_meeting_id", "Invalid meeting id.", status=400)
        return root / "highlights.json"
    def tingwu_start_failure_details(self, exc: TingwuMeetingError) -> dict[str, object]:
        error_details = exc.details if isinstance(getattr(exc, "details", None), dict) else {}
        provider = self.tingwu.status()
        mic_probe = error_details.get("mic_probe") if isinstance(error_details.get("mic_probe"), dict) else None
        capture_probe = error_details.get("capture_probe") if isinstance(error_details.get("capture_probe"), dict) else None
        if mic_probe:
            provider = {**provider, "mic_probe": mic_probe}
            provider["probe_status_before_capture"] = provider.get("status")
            provider["status"] = "unavailable"
            provider["mic_status"] = mic_probe.get("status") or provider.get("mic_status")
            provider["selected_mic_device"] = mic_probe.get("selected_device") or provider.get("selected_mic_device") or provider.get("mic_device")
            if capture_probe:
                provider["capture_probe"] = capture_probe
        details: dict[str, object] = {
            "error": str(exc),
            "provider": provider,
        }
        if mic_probe:
            details["mic_probe"] = mic_probe
        if capture_probe:
            details["capture_probe"] = capture_probe
        if error_details:
            details["diagnostics"] = error_details
        return sanitize_event_payload(details)
    def build_tingwu_start_failure_notification(self, title: str, details: dict[str, object]) -> dict[str, object]:
        provider = details.get("provider") if isinstance(details.get("provider"), dict) else {}
        provider_status = str(provider.get("status") or "unavailable")
        mic_status = str(provider.get("mic_status") or "")
        capture_probe = details.get("capture_probe") if isinstance(details.get("capture_probe"), dict) else {}
        if not capture_probe:
            provider_mic_probe = provider.get("mic_probe") if isinstance(provider.get("mic_probe"), dict) else {}
            capture_probe = provider_mic_probe.get("capture_probe") if isinstance(provider_mic_probe.get("capture_probe"), dict) else {}
        error = str(details.get("error") or "")
        if provider_status == "needs_config":
            reason = "缺少 TINGWU_API_KEY/DASHSCOPE_API_KEY 或 TINGWU_APP_ID/TINGWU_MEETING_APP_ID。"
        elif mic_status and mic_status != "available":
            reason = f"麦克风不可用：{provider.get('message') or mic_status}"
        else:
            reason = error or f"provider 状态为 {provider_status}。"
        return {
            "event": "meeting_realtime_start_failed",
            "text": f"实时会议「{title}」没有启动成功，{reason}",
            "status": "failed",
            "attachment": "",
            "payload": {"title": title, "provider": provider, "capture_probe": capture_probe, "error": error},
        }
    def build_tingwu_assistant_notification(
        self,
        action: str,
        session: dict[str, object],
        result: dict[str, object],
    ) -> dict[str, object]:
        title = str(session.get("title") or "Meeting")
        meeting_id = session.get("meeting_id")
        result_status = str(result.get("status") or session.get("status") or "failed")
        provider_status = str(result.get("provider_status") or session.get("status") or result_status)
        openclaw_status = str(result.get("openclaw_status") or "")
        provider_ok = provider_status == "completed"
        openclaw_ok = openclaw_status == "completed"
        action_is_fetch = action == "fetch_minutes"
        minutes = result.get("minutes") if isinstance(result.get("minutes"), dict) else {}
        content_status = str(result.get("content_status") or minutes.get("content_status") or "")

        if openclaw_ok and content_status == "no_speech_detected":
            event = "meeting_no_speech_detected"
            text = f"实时会议「{title}」已保存音频和诊断，但没有识别到可用发言；OpenClaw 已生成空会议诊断纪要。"
            status = "warning"
        elif not action_is_fetch and provider_status == "stopped" and openclaw_ok:
            event = "meeting_realtime_stopped"
            text = f"实时会议「{title}」已停止，转写和音频已保存，OpenClaw 后处理已生成；可以继续拉取通义听悟 AI 纪要。"
            status = "completed"
        elif provider_ok and result_status == "completed":
            event = "meeting_ai_minutes_ready" if action_is_fetch else "meeting_realtime_completed"
            text = (
                f"实时会议「{title}」的通义听悟 AI 纪要已拉取完成，OpenClaw 后处理已保存。"
                if action_is_fetch
                else f"实时会议「{title}」已停止，通义听悟纪要和 OpenClaw 后处理已保存。"
            )
            status = "completed"
        elif not provider_ok and openclaw_ok:
            event = "meeting_ai_minutes_provider_failed" if action_is_fetch else "meeting_realtime_provider_failed"
            text = (
                f"实时会议「{title}」的通义听悟 AI 纪要未完成；已保存转写和诊断结果，并用 OpenClaw 基于转写生成了后处理输出。"
                if action_is_fetch
                else f"实时会议「{title}」已停止，但通义听悟 AI 纪要未完成；已保存转写和诊断结果，并用 OpenClaw 基于转写生成了后处理输出。"
            )
            status = "warning"
        elif provider_ok and not openclaw_ok:
            event = "meeting_ai_minutes_openclaw_failed" if action_is_fetch else "meeting_realtime_openclaw_failed"
            text = (
                f"实时会议「{title}」的通义听悟 AI 纪要已拉取，但 OpenClaw 后处理未完成，请查看任务和审计日志。"
                if action_is_fetch
                else f"实时会议「{title}」已停止，通义听悟纪要已保存，但 OpenClaw 后处理未完成，请查看任务和审计日志。"
            )
            status = "failed"
        else:
            event = "meeting_ai_minutes_failed" if action_is_fetch else "meeting_realtime_failed"
            text = (
                f"实时会议「{title}」的 AI 纪要拉取和 OpenClaw 后处理均未完成，请查看任务和审计日志。"
                if action_is_fetch
                else f"实时会议「{title}」已停止，但通义听悟 AI 纪要和 OpenClaw 后处理均未完成，请查看任务和审计日志。"
            )
            status = "failed"

        transcript_path = str(session.get("transcript_path") or "")
        tingwu_minutes_path = str(session.get("minutes_path") or "")
        openclaw_minutes_path = str(minutes.get("path") or result.get("path") or "")
        provider_error = redact_sensitive_text(str(session.get("error") or result.get("provider_error") or minutes.get("provider_error") or ""))[:1000]
        openclaw_error = redact_sensitive_text(str(minutes.get("error") or result.get("error") or ""))[:1000]
        error = provider_error or openclaw_error
        return {
            "event": event,
            "text": text,
            "status": status,
            "attachment": "",
            "payload": {
                "meeting_id": meeting_id,
                "status": result_status,
                "provider_status": provider_status,
                "openclaw_status": openclaw_status,
                "content_status": content_status,
                "transcript_path": transcript_path,
                "tingwu_minutes_path": tingwu_minutes_path,
                "openclaw_minutes_path": openclaw_minutes_path,
                "manifest_path": str(result.get("manifest_path") or ""),
                "error": error,
                "provider_error": provider_error,
                "openclaw_error": openclaw_error,
            },
        }
