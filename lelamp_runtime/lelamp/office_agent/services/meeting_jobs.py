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


class MeetingJobsMixin:
    def find_aggregated_meeting_job(self, transcript: str = "", *, meeting_id: str = "") -> dict[str, object] | None:
        transcript_ref = self.normalize_meeting_transcript_ref(transcript)
        for job in self.aggregate_meeting_jobs(self.load_tasks(limit=200)):
            if meeting_id and job.get("meeting_id") == meeting_id:
                return job
            if transcript_ref and job.get("transcript") == transcript_ref:
                return job
            steps = job.get("steps") if isinstance(job.get("steps"), list) else []
            if transcript_ref and any(isinstance(step, dict) and step.get("input_file") == transcript_ref for step in steps):
                return job
        return None
    def meeting_job_from_task(self, task: dict[str, object]) -> dict[str, object]:
        output = task.get("output") if isinstance(task.get("output"), dict) else {}
        input_payload = task.get("input") if isinstance(task.get("input"), dict) else {}
        status = str(task.get("status") or "completed")
        step_name = str(input_payload.get("step") or "minutes")
        title = str(input_payload.get("meeting_title") or task.get("title") or "Meeting")
        return {
            "job_id": str(task.get("task_id")),
            "status": status,
            "title": title,
            "meeting_id": str(input_payload.get("meeting_id") or output.get("meeting_id") or ""),
            "transcript": self.normalize_meeting_transcript_ref(str(input_payload.get("transcript") or "")),
            "steps": [
                self.meeting_step_from_task(task, step_name, status, output, input_payload)
            ],
        }
    def meeting_step_from_task(
        self,
        task: dict[str, object],
        step_name: str,
        status: str,
        output: dict[str, object],
        input_payload: dict[str, object],
    ) -> dict[str, object]:
        display_status = status
        if (
            step_name == "realtime_capture"
            and status == "failed"
            and (
                output.get("transcript_path")
                or output.get("audio_path")
                or safe_float(output.get("audio_seconds"), 0.0) > 0
            )
        ):
            display_status = "completed"
        if (
            step_name == "minutes"
            and status == "failed"
            and str(output.get("openclaw_status") or "") == "completed"
        ):
            display_status = "partial"
        return {
            "name": step_name,
            "status": display_status,
            "input_file": str(input_payload.get("transcript") or ""),
            "system_understanding": meeting_step_understanding(step_name, output),
            "ai_result": meeting_step_result(step_name, output),
            "confirmation": output.get("confirmation") if isinstance(output.get("confirmation"), dict) else {"required": False},
            "output_path": first_output_path(output),
            "output": compact_meeting_step_output(step_name, output),
            "task_id": str(task.get("task_id") or ""),
            "updated_at": str(task.get("updated_at") or ""),
        }
    def aggregate_meeting_jobs(self, tasks: list[dict[str, object]]) -> list[dict[str, object]]:
        groups: dict[str, dict[str, object]] = {}
        order: list[str] = []
        for task in tasks:
            if task.get("type") != "meeting":
                continue
            output = task.get("output") if isinstance(task.get("output"), dict) else {}
            input_payload = task.get("input") if isinstance(task.get("input"), dict) else {}
            transcript = self.normalize_meeting_transcript_ref(str(input_payload.get("transcript") or ""))
            if not transcript:
                continue
            meeting_id = str(input_payload.get("meeting_id") or output.get("meeting_id") or "")
            minutes_output = output.get("minutes") if isinstance(output.get("minutes"), dict) else {}
            title = str(
                input_payload.get("meeting_title")
                or output.get("title")
                or minutes_output.get("title")
                or task.get("title")
                or Path(transcript).stem
                or "Meeting"
            )
            if not title:
                title = str(task.get("title") or Path(transcript).stem or "Meeting")
            key = f"meeting:{meeting_id}" if meeting_id else f"transcript:{transcript}"
            if key not in groups:
                groups[key] = {
                    "job_id": str(task.get("task_id")),
                    "status": "completed",
                    "title": title,
                    "transcript": transcript,
                    "meeting_id": meeting_id,
                    "steps": {},
                    "updated_at": str(task.get("updated_at") or ""),
                }
                order.append(key)
            group = groups[key]
            if meeting_id and not str(group.get("meeting_id") or ""):
                group["meeting_id"] = meeting_id
            if transcript and (
                not str(group.get("transcript") or "")
                or Path(str(group.get("transcript") or "")).is_absolute()
            ):
                group["transcript"] = transcript
            step_name = str(input_payload.get("step") or "minutes")
            status = str(task.get("status") or "completed")
            step = self.meeting_step_from_task(task, step_name, status, output, input_payload)
            step_status = str(step.get("status") or status)
            steps = group["steps"] if isinstance(group.get("steps"), dict) else {}
            existing = steps.get(step_name)
            if not isinstance(existing, dict) or str(step.get("updated_at")) >= str(existing.get("updated_at") or ""):
                steps[step_name] = step
            group["steps"] = steps
            group["updated_at"] = max(str(group.get("updated_at") or ""), str(task.get("updated_at") or ""))
            if step_status == "partial" and str(group.get("status") or "") == "completed":
                group["status"] = "partial"
            elif status in {"failed", "blocked"} and step_status != "partial":
                group["status"] = status
            if not str(group.get("title") or "").strip() or str(group.get("title")).startswith("会议工作流："):
                group["title"] = title

        jobs: list[dict[str, object]] = []
        for key in order:
            group = groups[key]
            steps_by_name = group.get("steps") if isinstance(group.get("steps"), dict) else {}
            ordered_steps = []
            for name in ("realtime_capture", "import_transcript", "minutes", "decisions", "action_items", "followup", "reminders", "projection_confirmation"):
                if isinstance(steps_by_name, dict) and isinstance(steps_by_name.get(name), dict):
                    ordered_steps.append(steps_by_name[name])
            jobs.append(
                {
                    "job_id": str(group.get("job_id")),
                    "status": str(group.get("status") or "completed"),
                    "title": str(group.get("title") or "Meeting"),
                    "meeting_id": str(group.get("meeting_id") or ""),
                    "transcript": str(group.get("transcript") or ""),
                    "steps": ordered_steps,
                    "updated_at": str(group.get("updated_at") or ""),
                }
            )
        return jobs
