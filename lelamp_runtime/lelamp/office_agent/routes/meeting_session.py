from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.parse
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from ..meeting_voice_skill import parse_meeting_voice_command
from ..tingwu_meeting import TingwuMeetingError, feature_markdown, first_feature_value, preflight_arecord_capture, redact_sensitive_text, sanitize_event_payload
from ..utils import safe_filename
from ._base import ApiError, NOT_HANDLED, RequestContext, exact_payload


def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)


def atomic_write_json(*a, **kw): return _helper("atomic_write_json")(*a, **kw)
def atomic_write_text_file(*a, **kw): return _helper("atomic_write_text_file")(*a, **kw)
def capture_probe_matches_selected_microphone(*a, **kw): return _helper("capture_probe_matches_selected_microphone")(*a, **kw)
def dedupe_events(*a, **kw): return _helper("dedupe_events")(*a, **kw)
def endpoint_matches(*a, **kw): return _helper("endpoint_matches")(*a, **kw)
def is_real_tingwu_microphone(*a, **kw): return _helper("is_real_tingwu_microphone")(*a, **kw)
def list_string(*a, **kw): return _helper("list_string")(*a, **kw)
def normalize_task_status(*a, **kw): return _helper("normalize_task_status")(*a, **kw)
def now_iso(*a, **kw): return _helper("now_iso")(*a, **kw)
def parse_datetime(*a, **kw): return _helper("parse_datetime")(*a, **kw)
def require_file_path(*a, **kw): return _helper("require_file_path")(*a, **kw)
def require_string(*a, **kw): return _helper("require_string")(*a, **kw)
def safe_int(*a, **kw): return _helper("safe_int")(*a, **kw)
def sanitize_id(*a, **kw): return _helper("sanitize_id")(*a, **kw)
def status_to_audit(*a, **kw): return _helper("status_to_audit")(*a, **kw)
def tingwu_provider_acceptance_checklist(*a, **kw): return _helper("tingwu_provider_acceptance_checklist")(*a, **kw)
def tingwu_provider_preflight_next_actions(*a, **kw): return _helper("tingwu_provider_preflight_next_actions")(*a, **kw)



class MeetingSessionRoutesMixin:
    def api_meeting_status(self, ctx: RequestContext) -> dict[str, object]:
        status = self.runtime.meeting.status()
        self.record_audit("meeting_status", "ok", "meeting_mode", status, ctx)
        return {"status": "ok", **status}
    def api_meeting_voice_command(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        text = str(payload.get("text") or payload.get("message") or "").strip()
        if not text:
            raise ApiError("missing_text", "Missing meeting command text.", status=400)
        command = parse_meeting_voice_command(text)
        result = self.runtime.meeting_voice.handle_text(
            text,
            executor=lambda parsed, raw_text: self.execute_meeting_voice_command(parsed, raw_text, ctx),
        )
        if not bool(result.get("handled")):
            result = {
                **result,
                "status": "not_handled",
                "reply": "未识别为会议控制命令。",
            }
        self.record_audit(
            "meeting_voice_command",
            status_to_audit(str(result.get("status") or "blocked")),
            command.label if command is not None else "not_handled",
            {
                "handled": result.get("handled"),
                "command": result.get("command"),
                "qwen_omni_called": False,
                "ai_assistant_kept_online": True,
            },
            ctx,
        )
        return result
    def api_meeting_local_realtime_status(self, ctx: RequestContext) -> dict[str, object]:
        summary = self.runtime.meeting.realtime_summary()
        self.record_audit("meeting_local_realtime.status", "ok", "active_meeting", {"turn_count": summary.get("turn_count")}, ctx)
        return {"status": "completed", **summary}
    def api_meeting_local_realtime_turn(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        speaker = str(payload.get("speaker") or "Unknown").strip() or "Unknown"
        text = require_string(payload, "text")
        try:
            item = self.runtime.meeting.append_transcript(speaker, text)
        except PermissionError as exc:
            self.record_audit("meeting_local_realtime.turn", "blocked", speaker, {"reason": str(exc)}, ctx)
            raise ApiError("meeting_mode_disabled", str(exc), status=409) from exc
        summary = self.runtime.meeting.realtime_summary()
        result = {
            "status": "completed",
            "turn": item,
            **summary,
            "source": str(payload.get("source") or "manual_realtime_turn"),
        }
        self.record_audit("meeting_local_realtime.turn", "ok", speaker, {"chars": len(text), "turn_count": summary.get("turn_count")}, ctx)
        return result
    def api_meeting_local_realtime_export(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        try:
            transcript = self.runtime.meeting.export_transcript()
        except ValueError as exc:
            raise ApiError("no_active_meeting", str(exc), status=409) from exc
        summary = self.runtime.meeting.realtime_summary()
        result = {
            "status": "completed",
            "transcript_path": transcript["path"],
            "workspace_name": self.workspace_relative_path(transcript["path"]),
            **summary,
        }
        task = self.create_task("本地实时转写导出", "meeting", "completed", {"source": payload.get("source") or "local_realtime"}, result)
        self.record_audit("meeting_local_realtime.export", "ok", str(transcript["path"]), {"task_id": task["task_id"], "turn_count": summary.get("turn_count")}, ctx)
        return {"task_id": task["task_id"], **result}
    def api_meeting_mode_enable(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = str(payload.get("title") or f"会议模式 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        participants = list_string(payload.get("participants")) or ["Unknown"]
        result = self.runtime.meeting.enable(title, participants)
        self.record_audit("meeting_mode_enable", "ok", title, result, ctx)
        return {"status": "completed", **result}
    def api_meeting_mode_disable(self, ctx: RequestContext) -> dict[str, object]:
        result = self.runtime.meeting.disable()
        self.record_audit("meeting_mode_disable", "ok", "meeting_mode", result, ctx)
        return {"status": "completed", **result}
    def api_meeting_import_transcript(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(require_file_path(payload), ctx, action="meeting_import_transcript")
        title = str(payload.get("title") or Path(safe.workspace_name).stem)
        participants = list_string(payload.get("participants")) or ["Unknown"]
        try:
            parsed = self.runtime.meeting.parse_transcript_file(safe.workspace_name, title, participants)
        except ValueError as exc:
            self.record_audit("meeting_import_transcript", "blocked", safe.workspace_name, {"reason": str(exc)}, ctx)
            raise ApiError("invalid_meeting_transcript", str(exc), status=400) from exc
        job = self.create_meeting_job(title, safe.workspace_name, "import_transcript", "completed", parsed)
        self.record_audit("meeting_import_transcript", "ok", safe.workspace_name, {"job_id": job["job_id"], **parsed}, ctx)
        return job
    def api_meeting_import_text(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = str(payload.get("title") or f"meeting_text_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        text = require_string(payload, "text")
        participants = list_string(payload.get("participants")) or ["Unknown"]
        file_item = self.shared_space.put_note(title, text, source="meeting_text_import")
        shared = self.shared_file_dto(file_item.as_dict())
        job = self.api_meeting_import_transcript(
            {
                "file_path": shared["relative_path"],
                "title": title,
                "participants": participants,
            },
            ctx,
        )
        result = {
            "status": "completed",
            "source": "meeting_text_import",
            "file": shared,
            "job": job,
            "participants": participants,
        }
        self.record_audit(
            "meeting_import_text",
            "ok",
            str(shared["relative_path"]),
            {"job_id": job.get("job_id"), "chars": len(text), "participants": participants},
            ctx,
        )
        return result
    def api_meeting_provider_status(self, ctx: RequestContext) -> dict[str, object]:
        status = self.tingwu.status()
        self.record_audit("meeting_provider.status", status_to_audit(str(status.get("status"))), "tongyi_tingwu", status, ctx)
        return {
            "status": status.get("status"),
            "primary_provider": "tongyi_tingwu",
            "providers": {"tongyi_tingwu": status},
        }
    def api_meeting_provider_preflight(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        provider = self.tingwu.status()
        credential_diagnostics = (
            provider.get("credential_diagnostics")
            if isinstance(provider.get("credential_diagnostics"), dict)
            else {}
        )
        checks: dict[str, object] = {
            "tingwu_api_key_configured": bool(provider.get("api_key_configured")),
            "tingwu_app_id_configured": bool(provider.get("app_id_configured")),
            "provider_configured": bool(provider.get("configured")),
            "official_tingwu_endpoint": endpoint_matches(provider.get("http_url"), _helper("OFFICIAL_TINGWU_HTTP_URL"))
            and endpoint_matches(provider.get("ws_url"), _helper("OFFICIAL_TINGWU_WS_URL")),
            "microphone_selected": bool(provider.get("selected_mic_device")),
            "microphone_available": str(provider.get("mic_status") or "") == "available",
            "real_microphone_device": False,
            "microphone_capture_device_matches": False,
            "microphone_capture_open": False,
            "microphone_capture_signal": False,
        }
        selected = str(provider.get("selected_mic_device") or provider.get("mic_device") or "").strip()
        mic_probe = provider.get("mic_probe") if isinstance(provider.get("mic_probe"), dict) else {}
        checks["real_microphone_device"] = is_real_tingwu_microphone(selected, mic_probe)
        capture_probe: dict[str, object] = {}
        capture_seconds = max(1, min(10, safe_int(payload.get("capture_seconds"), self.runtime.config.tingwu_preflight_capture_seconds)))
        if bool(payload.get("skip_capture", False)):
            capture_probe = {"status": "skipped", "selected_device": selected, "duration_seconds": 0}
        elif checks["real_microphone_device"]:
            capture_probe = preflight_arecord_capture(selected, self.runtime.config.tingwu_sample_rate, duration_seconds=capture_seconds)
            checks["microphone_capture_device_matches"] = capture_probe_matches_selected_microphone(selected, capture_probe)
            checks["microphone_capture_open"] = str(capture_probe.get("status") or "") == "available"
            checks["microphone_capture_signal"] = (
                bool(checks["microphone_capture_open"])
                and safe_int(capture_probe.get("audio_bytes"), 0) > 0
                and safe_int(capture_probe.get("audio_rms"), 0) > 0
                and safe_int(capture_probe.get("audio_peak"), 0) > 0
            )
        else:
            capture_probe = {
                "status": "blocked",
                "selected_device": selected,
                "message": "Select a real ALSA capture device before running microphone capture preflight.",
            }
        ready = all(bool(checks[key]) for key in (
            "tingwu_api_key_configured",
            "tingwu_app_id_configured",
            "provider_configured",
            "official_tingwu_endpoint",
            "microphone_selected",
            "microphone_available",
            "real_microphone_device",
            "microphone_capture_device_matches",
            "microphone_capture_open",
            "microphone_capture_signal",
        ))
        status = "available" if ready else "needs_config" if not bool(provider.get("configured")) else "unavailable"
        result = {
            "status": status,
            "provider": "tongyi_tingwu",
            "ready": ready,
            "checks": checks,
            "next_actions": tingwu_provider_preflight_next_actions(checks, credential_diagnostics=credential_diagnostics),
            "acceptance_checklist": tingwu_provider_acceptance_checklist(checks),
            "provider_status": provider,
            "capture_probe": capture_probe,
            "credential_diagnostics": credential_diagnostics,
            "capture_seconds": capture_seconds,
            "selected_mic_device": selected,
            "sample_rate": self.runtime.config.tingwu_sample_rate,
            "audio_format": self.runtime.config.tingwu_audio_format,
        }
        self.record_audit(
            "meeting_provider.preflight",
            status_to_audit(status),
            "tongyi_tingwu",
            sanitize_event_payload(result),
            ctx,
        )
        return result
    def api_meeting_realtime_start(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = str(payload.get("title") or f"LeLamp 实时会议 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        participants = list_string(payload.get("participants")) or ["Unknown"]
        max_seconds = max(30, min(8 * 60 * 60, safe_int(payload.get("max_seconds"), 2 * 60 * 60)))
        try:
            session = self.tingwu.start_realtime_meeting(
                title=title,
                participants=participants,
                max_seconds=max_seconds,
                audio_file=str(payload.get("_audio_file") or ""),
            )
        except TingwuMeetingError as exc:
            details = self.tingwu_start_failure_details(exc)
            self.record_audit("meeting_realtime_start", status_to_audit(str(details["provider"].get("status"))), "tongyi_tingwu", details, ctx)
            self.push_assistant_notification(**self.build_tingwu_start_failure_notification(title, details))
            raise ApiError("meeting_provider_unavailable", str(exc), status=409, details=details) from exc
        stored_title = str(session.get("title") or redact_sensitive_text(title) or "Tingwu Meeting")
        task = self.upsert_meeting_step_task(
            stored_title,
            self.workspace_relative_path(str(session.get("transcript_path") or "")),
            "realtime_capture",
            "running",
            session,
            meeting_id=str(session.get("meeting_id") or ""),
            provider="tongyi_tingwu",
        )
        result = {
            **session,
            "status": "running",
            "task_id": task["task_id"],
            "task_id_web": task["task_id"],
            "provider_task_id": session.get("task_id"),
            "job": self.meeting_job_from_task(task),
        }
        self.record_audit(
            "meeting_realtime_start",
            "ok",
            str(session.get("meeting_id") or ""),
            {"task_id": task["task_id"], "provider_task_id": session.get("task_id"), "title": stored_title},
            ctx,
        )
        return result
    def api_meeting_import_media(self, content_type: str, body: bytes, ctx: RequestContext) -> dict[str, object]:
        upload = self.api_shared_upload(content_type, body, ctx)
        files = upload.get("files") if isinstance(upload.get("files"), list) else []
        if len(files) != 1 or not isinstance(files[0], dict):
            raise ApiError("single_media_required", "请一次上传一个音频或视频文件。", status=400)
        workspace_name = str(files[0].get("workspace_name") or files[0].get("relative_path") or "")
        source = self.ensure_allowed_path(workspace_name, ctx, action="meeting_media_import").path
        if source.suffix.lower() not in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".mp4", ".mov", ".mkv", ".webm"}:
            raise ApiError("unsupported_media", "不支持该媒体格式。", status=400)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ApiError("ffmpeg_unavailable", "设备未安装 ffmpeg，无法转换媒体。", status=503)
        import_root = (self.runtime.config.workspace_dir / "meeting_imports").resolve()
        import_root.mkdir(parents=True, exist_ok=True)
        wav_path = import_root / f"{uuid4().hex}.wav"
        completed = subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-i", str(source), "-ac", "1", "-ar", str(self.runtime.config.tingwu_sample_rate), "-c:a", "pcm_s16le", str(wav_path)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if completed.returncode != 0 or not wav_path.is_file():
            raise ApiError("media_conversion_failed", "媒体转换失败。", status=422, details={"error": completed.stderr[-1000:]})
        title = source.stem[:180] or "导入会议"
        result = self.api_meeting_realtime_start(
            {"title": title, "participants": ["待识别"], "max_seconds": 8 * 60 * 60, "_audio_file": str(wav_path)},
            ctx,
        )
        self.record_audit("meeting_media_import", "ok", str(result.get("meeting_id") or ""), {"source": workspace_name, "bytes": source.stat().st_size}, ctx)
        return result
    def api_meeting_realtime_status(self, meeting_id: str | None, ctx: RequestContext) -> dict[str, object]:
        try:
            status = self.tingwu.session_status(meeting_id)
        except TingwuMeetingError as exc:
            raise ApiError("not_found", str(exc), status=404) from exc
        if status.get("meeting_id"):
            self.sync_realtime_capture_task(status)
        self.record_audit(
            "meeting_realtime_status",
            status_to_audit(str(status.get("status"))),
            str(status.get("meeting_id") or "idle"),
            {"provider": "tongyi_tingwu", "final_count": status.get("final_count")},
            ctx,
        )
        return status
    def api_meeting_realtime_events(self, meeting_id: str, ctx: RequestContext) -> dict[str, object]:
        drained_events = self.tingwu.drain_events(meeting_id, limit=200)
        session_events: list[dict[str, object]] = []
        try:
            session = self.tingwu.session_status(meeting_id)
        except TingwuMeetingError:
            session = {}
        task_payload = session.get("task_payload") if isinstance(session.get("task_payload"), dict) else {}
        persisted_events = task_payload.get("events") if isinstance(task_payload.get("events"), list) else []
        session_events = [item for item in persisted_events if isinstance(item, dict)]
        events = dedupe_events([*session_events, *drained_events])[-200:]
        self.append_realtime_task_events(meeting_id, events)
        self.record_audit("meeting_realtime_events", "ok", meeting_id, {"count": len(events)}, ctx)
        return {"status": "ok", "meeting_id": meeting_id, "events": events, "total": len(events)}
    def api_meeting_realtime_audio(self, meeting_id: str, ctx: RequestContext) -> Path:
        meeting_id = str(meeting_id or "").strip()
        if not meeting_id:
            raise ApiError("missing_meeting_id", "Missing meeting_id.", status=400)
        try:
            session = self.tingwu.session_status(meeting_id)
        except TingwuMeetingError as exc:
            raise ApiError("not_found", str(exc), status=404) from exc
        path = Path(str(session.get("audio_path") or "")).expanduser().resolve()
        meeting_root = (self.runtime.config.workspace_dir / "meetings" / safe_filename(meeting_id, default="meeting")).resolve()
        if not path.is_file() or not path.is_relative_to(meeting_root):
            raise ApiError("audio_not_found", "会议音频尚未生成。", status=404)
        self.record_audit("meeting_realtime.audio", "ok", meeting_id, {"bytes": path.stat().st_size}, ctx)
        return path
    def api_meeting_realtime_stop(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        meeting_id = str(payload.get("meeting_id") or "").strip() or None
        try:
            session = self.tingwu.stop_realtime_meeting(meeting_id)
        except TingwuMeetingError as exc:
            raise ApiError("meeting_stop_failed", str(exc), status=409) from exc
        if str(session.get("status") or "") in {"starting", "running", "stopping", "finalizing"}:
            task = self.upsert_meeting_step_task(
                str(session.get("title") or "Tingwu Meeting"),
                self.workspace_relative_path(str(session.get("transcript_path") or "")),
                "realtime_capture",
                str(session.get("status") or "stopping"),
                session,
                meeting_id=str(session.get("meeting_id") or ""),
                provider="tongyi_tingwu",
            )
            result = {
                "status": session.get("status"),
                "task_id": task["task_id"],
                "task_id_web": task["task_id"],
                "provider_task_id": session.get("task_id"),
                "job": self.meeting_job_from_task(task),
                "session": session,
            }
            self.record_audit(
                "meeting_realtime_stop",
                "running",
                str(session.get("meeting_id") or ""),
                {
                    "task_id": task["task_id"],
                    "message": "Realtime stream is still stopping; final outputs are not registered until the stream thread exits.",
                },
                ctx,
            )
            return result
        result = self.register_tingwu_outputs(session, ctx, run_followup=bool(payload.get("run_followup", False)))
        self.record_audit(
            "meeting_realtime_stop",
            status_to_audit(str(result.get("status"))),
            str(session.get("meeting_id") or ""),
            {"job_id": result.get("job", {}).get("job_id") if isinstance(result.get("job"), dict) else None, "minutes_path": session.get("minutes_path")},
            ctx,
        )
        self.push_assistant_notification(**self.build_tingwu_assistant_notification("stop", session, result))
        return result
