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



class MeetingWorkflowRoutesMixin:
    def api_meeting_extract_step(self, step_name: str, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        target, title = self.load_meeting_transcript(payload, ctx, action=f"meeting_{step_name}")
        minutes = self.runtime.meeting.generate_minutes()
        items_key = "decisions" if step_name == "decisions" else "action_items"
        items = [str(item) for item in minutes.get(items_key, [])]
        label = "decisions" if step_name == "decisions" else "action_items"
        filename = safe_filename(title, default="meeting", suffix=f"_{label}.json")
        output_path = self.runtime.workspace.write_json(
            filename,
            {
                "title": title,
                "transcript": target,
                "step": step_name,
                "items": items,
                "source_minutes_path": minutes.get("path"),
                "generated_at": now_iso(),
                "confirmation_required": False,
            },
            action=f"meeting.{step_name}_extract",
        )
        status = "completed"
        result = {
            "status": status,
            "step": step_name,
            items_key: items,
            "items": items,
            "path": str(output_path),
            "source_minutes_path": minutes.get("path"),
            "confirmation": {
                "required": False,
                "summary": "内容已生成。",
            },
            "message": "已从 transcript 生成步骤输出。",
        }
        task = self.create_task(
            f"会议工作流：{title}",
            "meeting",
            status,
            {"transcript": target, "step": step_name},
            result,
        )
        job = self.meeting_job_from_task(task)
        self.record_audit(f"meeting_{step_name}", status_to_audit(status), target, {"task_id": task["task_id"], "path": str(output_path), "count": len(items)}, ctx)
        return {"task_id": task["task_id"], "job": job, **result}
    def api_meeting_followup(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        target, title = self.load_meeting_transcript(payload, ctx, action="meeting_followup")
        result = self.runtime.p0.generate_meeting_followup_package(
            recipient=str(payload.get("recipient") or "待填写收件人"),
            create_reminders=bool(payload.get("create_reminders", True)),
            render_projection=bool(payload.get("render_projection", False)),
        )
        status = normalize_task_status(str(result.get("status") or "completed"))
        task = self.create_task("会议跟进包", "meeting", status, {"transcript": target, "step": "followup", "meeting_title": title}, result)
        job = self.create_meeting_job(title, target, "followup", status, result)
        self.record_audit("meeting_followup", status_to_audit(status), target, {"task_id": task["task_id"]}, ctx)
        return {"status": status, "task_id": task["task_id"], "job": job, **result}
    def api_meeting_export_package(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        if not bool(payload.get("authorized", False)):
            result = {
                "status": "needs_confirmation",
                "message": "Export requires explicit user authorization.",
                "confirmation": {"required": True, "scope": "meeting_followup_export"},
            }
            self.record_audit("meeting_export_package", "blocked", "meeting_export", result, ctx)
            return result
        target, title = self.load_meeting_transcript(payload, ctx, action="meeting_export_package")
        package = self.runtime.p0.generate_meeting_followup_package(
            recipient=str(payload.get("recipient") or "待填写收件人"),
            create_reminders=bool(payload.get("create_reminders", True)),
            render_projection=bool(payload.get("render_projection", True)),
        )
        paths = self.collect_existing_workspace_paths(package)
        if not paths:
            result = {"status": "backend_missing", "message": "No meeting outputs are available to export.", "source_status": package.get("status")}
            self.record_audit("meeting_export_package", "blocked", target, result, ctx)
            return result
        zip_path = self.runtime.workspace.path_for_new_file(safe_filename(title, default="meeting", suffix="_followup_export.zip"))
        manifest = {
            "title": title,
            "transcript": target,
            "created_at": now_iso(),
            "source_status": package.get("status"),
            "files": [self.export_archive_name(path) for path in paths],
            "note": "Export package created only after explicit user authorization.",
        }
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for path in paths:
                archive.write(path, arcname=self.export_archive_name(path))
        shared = self.shared_file_dto(
            self.shared_space.put_bytes(zip_path.name, zip_path.read_bytes(), source="meeting_export_package").as_dict()
        )
        result = {
            "status": "completed",
            "path": str(zip_path),
            "shared_file": shared,
            "download_url": f"/api/shared/download?file={urllib.parse.quote(str(shared['relative_path']))}",
            "file_count": len(paths),
            "manifest": manifest,
            "source_package": package,
        }
        task = self.create_task("会议跟进包导出", "meeting", "completed", {"transcript": target, "authorized": True}, result)
        result["task_id"] = task["task_id"]
        self.record_audit("meeting_export_package", "ok", str(zip_path), {"task_id": task["task_id"], "file_count": len(paths), "shared_file": shared.get("relative_path")}, ctx)
        return result
    def api_meeting_send_email(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        if not bool(payload.get("authorized", False)):
            result = {
                "status": "needs_confirmation",
                "message": "Email sending requires explicit user authorization.",
                "confirmation": {"required": True, "scope": "meeting_followup_email_send"},
            }
            self.record_audit("meeting_email_send", "blocked", "meeting_email", result, ctx)
            return result

        recipient = str(payload.get("recipient") or "").strip()
        if not recipient or recipient == "待填写收件人":
            raise ApiError("missing_recipient", "Recipient is required before sending email.", status=400)
        target, title = self.load_meeting_transcript(payload, ctx, action="meeting_email_send")
        package = self.runtime.p0.generate_meeting_followup_package(
            recipient=recipient,
            create_reminders=False,
            render_projection=False,
        )
        email_path = Path(str(package.get("email_draft_path") or ""))
        if not email_path.is_file() or not email_path.is_relative_to(self.runtime.workspace.root):
            result = {"status": "backend_missing", "message": "Email draft was not generated.", "source_package": package}
            self.record_audit("meeting_email_send", "blocked", target, result, ctx)
            return result
        smtp_result = self.send_email_draft(email_path=email_path, recipient=recipient, subject=f"{title} 会后跟进")
        status = str(smtp_result.get("status") or "backend_missing")
        result = {
            "status": status,
            "recipient": recipient,
            "email_draft_path": str(email_path),
            "provider": "smtp",
            "smtp": smtp_result,
            "source_package": package,
        }
        task = self.create_task("会议邮件发送", "meeting", normalize_task_status(status), {"transcript": target, "recipient": recipient, "authorized": True}, result)
        result["task_id"] = task["task_id"]
        self.record_audit("meeting_email_send", status_to_audit(status), target, {"task_id": task["task_id"], "recipient": recipient, "smtp_status": status}, ctx)
        return result
    def api_meeting_reminders(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        target, title = self.load_meeting_transcript(payload, ctx, action="meeting_reminders")
        minutes = self.runtime.meeting.generate_minutes()
        action_items = [str(item) for item in minutes.get("action_items", [])]
        reminders = self.runtime.daily.create_reminders_from_action_items(action_items) if action_items else {"count": 0, "reminders": []}
        output_path = self.runtime.workspace.write_json(
            safe_filename(title, default="meeting", suffix="_reminders.json"),
            {
                "title": title,
                "transcript": target,
                "source_minutes_path": minutes.get("path"),
                "created_at": now_iso(),
                **reminders,
            },
            action="meeting.reminders_create",
        )
        result = {
            "status": "completed",
            "step": "reminders",
            "path": str(output_path),
            "source_minutes_path": minutes.get("path"),
            "reminders": reminders.get("reminders", []),
            "count": reminders.get("count", 0),
            "message": "已创建本地 reminder 草稿，不会同步外部日历或自动通知。",
        }
        task = self.create_task(
            f"会议工作流：{title}",
            "meeting",
            "completed",
            {"transcript": target, "step": "reminders"},
            result,
        )
        self.record_audit("meeting_reminders", "ok", target, {"task_id": task["task_id"], "path": str(output_path), "count": result["count"]}, ctx)
        return {"task_id": task["task_id"], "job": self.meeting_job_from_task(task), **result}
    def api_meeting_projection_confirmation(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        target = "active_meeting"
        title = str(payload.get("title") or "会议确认")
        decisions = list_string(payload.get("decisions"))
        action_items = list_string(payload.get("action_items"))
        if payload.get("transcript") or payload.get("file_path"):
            target, title = self.load_meeting_transcript(payload, ctx, action="meeting_projection_confirmation")
            minutes = self.runtime.meeting.generate_minutes()
            decisions = decisions or [str(item) for item in minutes.get("decisions", [])]
            action_items = action_items or [str(item) for item in minutes.get("action_items", [])]
        result = self.runtime.projection.render_confirmation(title, decisions, action_items)
        output = {"status": "completed", "step": "projection_confirmation", "projection": result, "path": result.get("path"), "decisions": decisions, "action_items": action_items}
        task = self.create_task(
            f"会议工作流：{title}",
            "meeting",
            "completed",
            {"transcript": target, "step": "projection_confirmation"},
            output,
        )
        self.record_audit("meeting_projection_confirmation", "ok", str(result.get("path")), {"title": title, "task_id": task["task_id"]}, ctx)
        return {"task_id": task["task_id"], "job": self.meeting_job_from_task(task), **output}
    def api_meeting_confirm_step(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        task_id = sanitize_id(require_string(payload, "task_id"))
        task = self.api_task_get(task_id, ctx)
        if task.get("type") != "meeting":
            self.record_audit("meeting_confirm_step", "blocked", task_id, {"reason": "not a meeting task"}, ctx)
            raise ApiError("blocked", "Only meeting workflow tasks can be confirmed here.", status=403)
        output = task.get("output") if isinstance(task.get("output"), dict) else {}
        input_payload = task.get("input") if isinstance(task.get("input"), dict) else {}
        output["confirmation"] = {
            **(output.get("confirmation") if isinstance(output.get("confirmation"), dict) else {}),
            "required": False,
            "confirmed": True,
            "confirmed_at": now_iso(),
            "confirmed_by": ctx.actor,
            "note": str(payload.get("note") or "用户已确认会议步骤。"),
        }
        task = self.update_task(task_id, status="completed", progress=1.0, output=output)
        step_name = str(input_payload.get("step") or "meeting_step")
        self.record_audit("meeting_confirm_step", "ok", task_id, {"step": step_name, "transcript": input_payload.get("transcript")}, ctx)
        return {"status": "completed", "task_id": task_id, "step": step_name, "job": self.meeting_job_from_task(task), "confirmation": output["confirmation"]}
    def api_meeting_jobs(self, ctx: RequestContext) -> dict[str, object]:
        jobs = self.aggregate_meeting_jobs(self.load_tasks(limit=100))
        self.record_audit("meeting_jobs.list", "ok", "meeting_jobs", {"count": len(jobs)}, ctx)
        return {"items": jobs, "total": len(jobs)}
    def api_meeting_job(self, job_id: str, ctx: RequestContext) -> dict[str, object]:
        for job in self.api_meeting_jobs(ctx)["items"]:
            if job.get("job_id") == job_id:
                return job
        raise ApiError("not_found", "Meeting job not found.", status=404)
