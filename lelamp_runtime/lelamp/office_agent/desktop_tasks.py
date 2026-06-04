from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .audit import AuditLogger
from .workspace import Workspace


VALID_TASK_STATUSES = {"requested", "approved", "rejected", "done", "blocked"}


class DesktopTaskQueue:
    """Auditable shared task queue for office-computer cooperation.

    The Pi writes task requests into the workspace. A human or future
    office-computer companion agent can inspect and approve them before any
    full-control desktop action is attempted.
    """

    def __init__(self, workspace: Workspace, audit: AuditLogger, *, dirname: str = "desktop_tasks"):
        self.workspace = workspace
        self.audit = audit
        self.queue_dir = (workspace.root / dirname).resolve()
        if not self.queue_dir.is_relative_to(workspace.root.resolve()):
            raise ValueError("Desktop task queue must stay inside the workspace.")
        self.queue_dir.mkdir(parents=True, exist_ok=True)

    def request_task(
        self,
        goal: str,
        steps: list[str],
        *,
        source: str = "openclaw",
        requires_full_control: bool = True,
    ) -> dict[str, object]:
        task_id = uuid4().hex
        payload = {
            "id": task_id,
            "status": "requested",
            "goal": goal,
            "steps": [
                {
                    "index": index + 1,
                    "description": step,
                    "requires_confirmation": True,
                }
                for index, step in enumerate(steps)
                if step.strip()
            ],
            "source": source,
            "requires_full_control": requires_full_control,
            "created_at": _now(),
            "updated_at": _now(),
            "approval": {
                "required": True,
                "approved_by": None,
                "approved_at": None,
                "rejected_by": None,
                "rejected_at": None,
                "reason": None,
            },
            "execution": {
                "backend": "not_connected",
                "status": "manual_review_required",
                "note": "No office-computer GUI automation backend is connected. This queue is for review/approval only.",
            },
        }
        path = self._task_path(task_id)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.audit.record(
            "desktop_task.request",
            target=task_id,
            details={"goal": goal, "path": str(path), "steps": len(payload["steps"])},
        )
        return {**payload, "path": str(path), "workspace_name": str(path.relative_to(self.workspace.root))}

    def list_tasks(self, *, limit: int = 50) -> dict[str, object]:
        tasks = [self._load_task(path) for path in sorted(self.queue_dir.glob("*.json"), reverse=True)]
        tasks = [task for task in tasks if task is not None][: max(1, limit)]
        self.audit.record("desktop_task.list", details={"count": len(tasks)})
        return {
            "queue_dir": str(self.queue_dir),
            "tasks": tasks,
        }

    def get_task(self, task_id: str) -> dict[str, object]:
        path = self._resolve_task_path(task_id)
        task = self._load_task(path)
        if task is None:
            raise ValueError("Desktop task could not be read.")
        self.audit.record("desktop_task.get", target=task_id)
        return task

    def update_status(
        self,
        task_id: str,
        status: str,
        *,
        actor: str = "user",
        reason: str = "",
    ) -> dict[str, object]:
        if status not in VALID_TASK_STATUSES:
            raise ValueError(f"Invalid desktop task status: {status}")
        path = self._resolve_task_path(task_id)
        task = json.loads(path.read_text(encoding="utf-8"))
        old_status = str(task.get("status") or "")
        task["status"] = status
        task["updated_at"] = _now()
        approval = task.setdefault("approval", {})
        if status == "approved":
            approval["approved_by"] = actor
            approval["approved_at"] = _now()
            approval["reason"] = reason or approval.get("reason")
        elif status == "rejected":
            approval["rejected_by"] = actor
            approval["rejected_at"] = _now()
            approval["reason"] = reason or approval.get("reason")
        elif reason:
            approval["reason"] = reason
        task.setdefault("history", []).append(
            {
                "timestamp": _now(),
                "actor": actor,
                "from": old_status,
                "to": status,
                "reason": reason,
            }
        )
        path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        self.audit.record(
            "desktop_task.status",
            target=task_id,
            details={"from": old_status, "to": status, "actor": actor, "reason": reason},
        )
        return {**task, "path": str(path), "workspace_name": str(path.relative_to(self.workspace.root))}

    def record_execution(
        self,
        task_id: str,
        *,
        backend: str,
        execution_status: str,
        actor: str,
        note: str,
        step_count: int,
    ) -> dict[str, object]:
        path = self._resolve_task_path(task_id)
        task = json.loads(path.read_text(encoding="utf-8"))
        task["updated_at"] = _now()
        task["execution"] = {
            "backend": backend,
            "status": execution_status,
            "note": note,
            "step_count": step_count,
            "updated_by": actor,
            "updated_at": _now(),
        }
        task.setdefault("history", []).append(
            {
                "timestamp": _now(),
                "actor": actor,
                "from": task.get("status"),
                "to": task.get("status"),
                "reason": note,
            }
        )
        path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        self.audit.record(
            "desktop_task.execution",
            target=task_id,
            details={"backend": backend, "status": execution_status, "actor": actor, "step_count": step_count},
        )
        return {**task, "path": str(path), "workspace_name": str(path.relative_to(self.workspace.root))}

    def _task_path(self, task_id: str) -> Path:
        return (self.queue_dir / f"{task_id}.json").resolve()

    def _resolve_task_path(self, task_id: str) -> Path:
        clean = "".join(ch for ch in task_id if ch.isalnum() or ch in {"-", "_"})
        path = self._task_path(clean)
        if not path.is_file() or not path.is_relative_to(self.queue_dir):
            self.audit.record(
                "desktop_task.resolve",
                status="blocked",
                target=task_id,
                details={"reason": "task not found or outside queue"},
            )
            raise ValueError("Desktop task not found.")
        return path

    def _load_task(self, path: Path) -> dict[str, object] | None:
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(task, dict):
            return None
        task["path"] = str(path)
        task["workspace_name"] = str(path.relative_to(self.workspace.root))
        return task


def _now() -> str:
    return datetime.now(UTC).isoformat()
