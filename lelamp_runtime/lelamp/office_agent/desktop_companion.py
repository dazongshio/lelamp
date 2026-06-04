from __future__ import annotations

from pathlib import Path

from .audit import AuditLogger
from .config import PermissionMode
from .desktop import DesktopService
from .desktop_tasks import DesktopTaskQueue
from .workspace import Workspace


class DesktopCompanionService:
    """Office-computer side executor for approved OpenClaw desktop tasks.

    This service is intentionally conservative. It can read the shared
    workspace and approved task queue, but the default backend is audit_only,
    so it creates execution plans instead of controlling the computer.
    """

    def __init__(
        self,
        *,
        workspace: Workspace,
        audit: AuditLogger,
        backend: str = "audit_only",
        permission_mode: PermissionMode = PermissionMode.SANDBOX,
    ):
        self.workspace = workspace
        self.audit = audit
        self.backend = backend
        self.permission_mode = permission_mode
        self.tasks = DesktopTaskQueue(workspace, audit)
        self.desktop = DesktopService(
            audit,
            permission_mode=permission_mode,
            backend=backend,
            allowed_roots=(workspace.root,),
            workspace_dir=workspace.root,
        )

    def status(self) -> dict[str, object]:
        payload = {
            "status": "ready",
            "workspace_dir": str(self.workspace.root),
            "queue_dir": str(self.tasks.queue_dir),
            "backend": self.backend,
            "permission_mode": self.permission_mode.value,
            "execution_default": "audit_only" if self.backend == "audit_only" else "local_requested",
            "safety": [
                "Only reads files inside the shared workspace.",
                "Only processes desktop_tasks that were approved.",
                "audit_only does not open apps, URLs, files, or control media.",
                "Local execution still uses DesktopService allowed-root checks.",
            ],
        }
        self.audit.record("desktop_companion.status", details=payload)
        return payload

    def list_approved_tasks(self, *, limit: int = 50, scan_limit: int | None = None) -> dict[str, object]:
        scan_size = max(limit, scan_limit or (limit * 5), 50)
        listed = self.tasks.list_tasks(limit=scan_size)
        approved = [task for task in listed["tasks"] if task.get("status") == "approved"][:limit]
        payload = {
            "queue_dir": listed["queue_dir"],
            "count": len(approved),
            "scanned_count": len(listed["tasks"]),
            "tasks": approved,
        }
        self.audit.record("desktop_companion.list_approved", details={"count": len(approved), "scanned_count": len(listed["tasks"])})
        return payload

    def execute_task(self, task_id: str, *, actor: str = "desktop_companion") -> dict[str, object]:
        task = self.tasks.get_task(task_id)
        if task.get("status") != "approved":
            payload = {"status": "blocked", "reason": "task is not approved", "task": task}
            self.audit.record("desktop_companion.execute", status="blocked", target=task_id, details=payload)
            return payload

        execution_steps = []
        for step in task.get("steps", []):
            if not isinstance(step, dict):
                continue
            description = str(step.get("description") or "")
            if not description.strip():
                continue
            execution_steps.append(self._execute_step(description))

        result_status = "planned" if self.backend == "audit_only" else "attempted"
        updated = self.tasks.record_execution(
            str(task["id"]),
            backend=self.backend,
            execution_status=result_status,
            actor=actor,
            note=f"companion {result_status}",
            step_count=len(execution_steps),
        )
        payload = {
            "status": result_status,
            "task_id": task["id"],
            "goal": task.get("goal"),
            "backend": self.backend,
            "steps": execution_steps,
            "task": updated,
        }
        self.audit.record(
            "desktop_companion.execute",
            target=str(task["id"]),
            details={"status": result_status, "backend": self.backend, "steps": len(execution_steps)},
        )
        return payload

    def _execute_step(self, description: str) -> dict[str, object]:
        if self.backend == "audit_only":
            payload = {
                "status": "planned",
                "description": description,
                "reason": "desktop companion backend is audit_only",
            }
            self.audit.record("desktop_companion.step", target=description, details=payload)
            return payload
        return self.desktop.handle_command(description)


def build_desktop_companion(
    *,
    workspace_dir: Path,
    audit_log_path: Path,
    backend: str = "audit_only",
    permission_mode: PermissionMode = PermissionMode.SANDBOX,
) -> DesktopCompanionService:
    audit = AuditLogger(audit_log_path)
    workspace = Workspace(workspace_dir, (workspace_dir,), audit)
    return DesktopCompanionService(
        workspace=workspace,
        audit=audit,
        backend=backend,
        permission_mode=permission_mode,
    )
