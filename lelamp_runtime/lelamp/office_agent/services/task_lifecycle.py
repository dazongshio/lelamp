from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..llm import LLMError, ResponsesLLM, ResponsesLLMConfig
from ..tingwu_meeting import redact_sensitive_text, sanitize_event_payload
from ..utils import safe_filename
from ..routes._base import ApiError, RequestContext

def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)
def atomic_write_bytes(*a,**kw): return _helper("atomic_write_bytes")(*a,**kw)
def atomic_write_json(*a,**kw): return _helper("atomic_write_json")(*a,**kw)
def atomic_write_text_file(*a,**kw): return _helper("atomic_write_text_file")(*a,**kw)
def compact_meeting_step_output(*a,**kw): return _helper("compact_meeting_step_output")(*a,**kw)
def first_output_path(*a,**kw): return _helper("first_output_path")(*a,**kw)
def meeting_step_result(*a,**kw): return _helper("meeting_step_result")(*a,**kw)
def meeting_step_understanding(*a,**kw): return _helper("meeting_step_understanding")(*a,**kw)
def normalize_task_status(*a,**kw): return _helper("normalize_task_status")(*a,**kw)
def now_iso(*a,**kw): return _helper("now_iso")(*a,**kw)
def safe_float(*a,**kw): return _helper("safe_float")(*a,**kw)
def safe_int(*a,**kw): return _helper("safe_int")(*a,**kw)
def sanitize_id(*a,**kw): return _helper("sanitize_id")(*a,**kw)
def summarize_dict(*a,**kw): return _helper("summarize_dict")(*a,**kw)
def tingwu_realtime_task_summary(*a,**kw): return _helper("tingwu_realtime_task_summary")(*a,**kw)


class TaskLifecycleMixin:
    def task_dir(self) -> Path:
        path = (self.runtime.config.workspace_dir / "web_tasks").resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    def create_task(
        self,
        title: str,
        task_type: str,
        status: str,
        input_payload: dict[str, object],
        output: object,
        error: object | None = None,
    ) -> dict[str, object]:
        task_id = uuid4().hex
        now = now_iso()
        clean_title = redact_sensitive_text(title)[:240]
        payload = {
            "task_id": task_id,
            "title": clean_title,
            "type": task_type,
            "status": normalize_task_status(status),
            "progress": 1.0 if normalize_task_status(status) in {"completed", "blocked", "failed", "unsupported"} else 0.5,
            "created_at": now,
            "updated_at": now,
            "input": sanitize_event_payload(input_payload),
            "output": sanitize_event_payload(output),
            "error": sanitize_event_payload(error),
        }
        with self._task_lock:
            path = self.task_dir() / f"{task_id}.json"
            atomic_write_json(path, payload)
        return payload
    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        output: object | None = None,
        error: object | None = None,
    ) -> dict[str, object]:
        with self._task_lock:
            path = self.task_dir() / f"{sanitize_id(task_id)}.json"
            if not path.is_file() or not path.resolve().is_relative_to(self.task_dir()):
                raise ApiError("not_found", "Task not found.", status=404)
            task = json.loads(path.read_text(encoding="utf-8"))
            if status is not None:
                task["status"] = normalize_task_status(status)
            if progress is not None:
                task["progress"] = max(0.0, min(1.0, float(progress)))
            if output is not None:
                existing_output = task.get("output") if isinstance(task.get("output"), dict) else {}
                if isinstance(output, dict) and isinstance(existing_output, dict) and "events" not in output and existing_output.get("events"):
                    output = {**output, "events": existing_output.get("events")}
                task["output"] = sanitize_event_payload(output)
            if error is not None:
                task["error"] = sanitize_event_payload(error)
            task["updated_at"] = now_iso()
            atomic_write_json(path, task)
            return task
    def append_task_event(self, task_id: str, event: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        with self._task_lock:
            path = self.task_dir() / f"{sanitize_id(task_id)}.json"
            if not path.is_file() or not path.resolve().is_relative_to(self.task_dir()):
                raise ApiError("not_found", "Task not found.", status=404)
            task = json.loads(path.read_text(encoding="utf-8"))
            output = task.get("output") if isinstance(task.get("output"), dict) else {}
            events = output.get("events") if isinstance(output.get("events"), list) else []
            clean_payload = sanitize_event_payload(payload or {})
            item = {"event": redact_sensitive_text(event)[:160], "timestamp": now_iso(), **clean_payload}
            events.append(item)
            output["events"] = events[-_helper("MAX_TASK_EVENTS"):]
            task["output"] = sanitize_event_payload(output)
            task["updated_at"] = now_iso()
            atomic_write_json(path, task)
            return item
    def find_meeting_step_task(self, *, meeting_id: str = "", transcript: str = "", step_name: str = "") -> dict[str, object] | None:
        transcript_ref = self.normalize_meeting_transcript_ref(transcript)
        for task in self.load_tasks(limit=300):
            if task.get("type") != "meeting":
                continue
            task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
            if step_name and str(task_input.get("step") or "") != step_name:
                continue
            same_meeting = meeting_id and str(task_input.get("meeting_id") or "") == meeting_id
            same_transcript = (
                transcript_ref
                and self.normalize_meeting_transcript_ref(str(task_input.get("transcript") or "")) == transcript_ref
            )
            if same_meeting or same_transcript:
                return task
        return None
    def sync_realtime_capture_task(self, session: dict[str, object]) -> None:
        meeting_id = str(session.get("meeting_id") or "")
        if not meeting_id:
            return
        task = self.find_meeting_step_task(meeting_id=meeting_id, step_name="realtime_capture")
        if not task:
            return
        status = normalize_task_status(str(session.get("status") or task.get("status") or "running"))
        if status == "stopped":
            status = "running"
        final_count = safe_int(session.get("final_count"), 0)
        audio_seconds = float(session.get("audio_seconds") or 0.0)
        websocket_audio_frames = safe_int(session.get("websocket_audio_frames"), 0)
        progress = 0.5
        if status in {"completed", "failed", "blocked"}:
            progress = 1.0
        elif audio_seconds > 0 or final_count > 0:
            progress = 0.7
        output = tingwu_realtime_task_summary(session)
        output["monitor"] = {"final_count": final_count, "audio_seconds": audio_seconds, "websocket_audio_frames": websocket_audio_frames, "last_status_poll": now_iso()}
        task_output = task.get("output") if isinstance(task.get("output"), dict) else {}
        if "events" not in output and isinstance(task_output.get("events"), list):
            output["events"] = task_output["events"]
        task_payload = session.get("task_payload") if isinstance(session.get("task_payload"), dict) else {}
        provider_events = task_payload.get("events") if isinstance(task_payload.get("events"), list) else []
        if provider_events:
            existing = output.get("events") if isinstance(output.get("events"), list) else []
            seen = {
                (str(item.get("event") or ""), str(item.get("timestamp") or ""))
                for item in existing
                if isinstance(item, dict)
            }
            merged = list(existing)
            for item in provider_events:
                if not isinstance(item, dict):
                    continue
                marker = (str(item.get("event") or ""), str(item.get("timestamp") or ""))
                if marker in seen:
                    continue
                seen.add(marker)
                merged.append(item)
            output["events"] = merged[-200:]
        self.update_task(str(task.get("task_id") or ""), status=status, progress=progress, output=output)
    def add_realtime_monitor_to_output(self, output: object) -> object:
        if not isinstance(output, dict):
            return output
        if "monitor" in output:
            return output
        if not any(key in output for key in ("websocket_audio_frames", "audio_seconds", "final_count")):
            return output
        return {
            **output,
            "monitor": {
                "final_count": safe_int(output.get("final_count"), 0),
                "audio_seconds": float(output.get("audio_seconds") or 0.0),
                "websocket_audio_frames": safe_int(output.get("websocket_audio_frames"), 0),
                "last_status_poll": now_iso(),
            },
        }
    def append_realtime_task_events(self, meeting_id: str, events: list[dict[str, object]]) -> None:
        if not events:
            return
        task = self.find_meeting_step_task(meeting_id=meeting_id, step_name="realtime_capture")
        if not task:
            return
        task_id = str(task.get("task_id") or "")
        task_output = task.get("output") if isinstance(task.get("output"), dict) else {}
        existing_events = task_output.get("events") if isinstance(task_output.get("events"), list) else []
        seen = {
            (str(item.get("event") or ""), str(item.get("timestamp") or ""))
            for item in existing_events
            if isinstance(item, dict)
        }
        for event in events:
            event_name = str(event.get("event") or event.get("type") or "realtime_event")
            marker = (event_name, str(event.get("timestamp") or ""))
            if marker in seen:
                continue
            seen.add(marker)
            payload = {key: value for key, value in event.items() if key != "event"}
            self.append_task_event(task_id, event_name, payload)
    def upsert_meeting_step_task(
        self,
        title: str,
        transcript: str,
        step_name: str,
        status: str,
        output: object,
        *,
        meeting_id: str = "",
        provider: str = "",
    ) -> dict[str, object]:
        clean_title = redact_sensitive_text(title)[:240] or "Meeting"
        transcript_ref = self.normalize_meeting_transcript_ref(transcript)
        input_payload = {
            "transcript": transcript_ref,
            "step": step_name,
            "meeting_title": clean_title,
            "meeting_id": meeting_id,
            "provider": provider,
        }
        with self._task_lock:
            existing_path: Path | None = None
            for path in sorted(self.task_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
                try:
                    task = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if task.get("type") != "meeting":
                    continue
                task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
                same_step = str(task_input.get("step") or "") == step_name
                same_meeting = meeting_id and str(task_input.get("meeting_id") or "") == meeting_id
                same_transcript = (
                    transcript_ref
                    and self.normalize_meeting_transcript_ref(str(task_input.get("transcript") or "")) == transcript_ref
                )
                if same_step and (same_meeting or same_transcript):
                    existing_path = path
                    break

            if existing_path is None:
                task_id = uuid4().hex
                now = now_iso()
                task_output = self.add_realtime_monitor_to_output(output) if step_name == "realtime_capture" else output
                task = {
                    "task_id": task_id,
                    "title": f"会议工作流：{clean_title}",
                    "type": "meeting",
                    "status": normalize_task_status(status),
                    "progress": 1.0 if normalize_task_status(status) in {"completed", "blocked", "failed"} else 0.5,
                    "created_at": now,
                    "updated_at": now,
                    "input": input_payload,
                    "output": sanitize_event_payload(task_output),
                    "error": None,
                }
                path = self.task_dir() / f"{task_id}.json"
                atomic_write_json(path, task)
                return task

            task = json.loads(existing_path.read_text(encoding="utf-8"))
            task["title"] = f"会议工作流：{clean_title}"
            task["status"] = normalize_task_status(status)
            task["progress"] = 1.0 if normalize_task_status(status) in {"completed", "blocked", "failed"} else 0.5
            task["updated_at"] = now_iso()
            task["input"] = input_payload
            existing_output = task.get("output") if isinstance(task.get("output"), dict) else {}
            if isinstance(output, dict) and isinstance(existing_output, dict) and "events" not in output and existing_output.get("events"):
                output = {**output, "events": existing_output.get("events")}
            if step_name == "realtime_capture":
                output = self.add_realtime_monitor_to_output(output)
            clean_output = sanitize_event_payload(output)
            task["output"] = clean_output
            task["error"] = None if normalize_task_status(status) != "failed" else {"code": "meeting_step_failed", "message": summarize_dict(clean_output)}
            atomic_write_json(existing_path, task)
            return task
    def load_tasks(self, *, limit: int = 20) -> list[dict[str, object]]:
        tasks: list[dict[str, object]] = []
        for path in sorted(self.task_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(task, dict):
                tasks.append(task)
            if len(tasks) >= limit:
                break
        return tasks
