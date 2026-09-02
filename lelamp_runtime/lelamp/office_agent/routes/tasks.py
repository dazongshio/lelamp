from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any

from ._base import ApiError, NOT_HANDLED, RequestContext

def _helper(name:str):
    from .. import web_console
    return getattr(web_console,name)
def atomic_write_json(*a,**kw): return _helper("atomic_write_json")(*a,**kw)
def audit_event_dto(*a,**kw): return _helper("audit_event_dto")(*a,**kw)
def csv_escape(*a,**kw): return _helper("csv_escape")(*a,**kw)
def now_iso(*a,**kw): return _helper("now_iso")(*a,**kw)
def read_recent_audit(*a,**kw): return _helper("read_recent_audit")(*a,**kw)
def redact_target(*a,**kw): return _helper("redact_target")(*a,**kw)
def require_string(*a,**kw): return _helper("require_string")(*a,**kw)
def safe_int(*a,**kw): return _helper("safe_int")(*a,**kw)
def sanitize_id(*a,**kw): return _helper("sanitize_id")(*a,**kw)

class TaskAuditRoutesMixin:
    def api_recent_audit_from_params(self, params: dict[str, list[str]], ctx: RequestContext) -> dict[str, object]:
        limit = safe_int(params.get("limit", [params.get("page_size", ["50"])[0]])[0], 50)
        page = max(1, safe_int(params.get("page", ["1"])[0], 1))
        page_size = max(1, min(200, safe_int(params.get("page_size", [str(limit)])[0], limit)))
        status = params.get("status", [""])[0]
        action = params.get("action", [""])[0]
        query = params.get("q", [""])[0]
        return self.api_recent_audit(limit=max(limit, page * page_size), status=status, action=action, query=query, page=page, page_size=page_size, ctx=ctx)

    def api_recent_audit(
        self,
        *,
        limit: int = 50,
        status: str = "",
        action: str = "",
        query: str = "",
        page: int = 1,
        page_size: int | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        read_limit = max(1, min(limit, 1000))
        if query or action:
            read_limit = max(read_limit, 5000)
        events = read_recent_audit(self.runtime.config.audit_log_path, limit=read_limit)
        if status:
            events = [event for event in events if event.get("status") == status]
        if action:
            events = [event for event in events if action in str(event.get("action", ""))]
        if query:
            lowered = query.lower()
            events = [event for event in events if lowered in json.dumps(event, ensure_ascii=False).lower()]
        total = len(events)
        page_size = page_size or total or 1
        start = (max(1, page) - 1) * page_size
        items = [audit_event_dto(event) for event in events[start : start + page_size]]
        if ctx:
            self.record_audit("audit_recent", "ok", str(self.runtime.config.audit_log_path), {"total": total, "page": page}, ctx)
        return {"items": items, "events": items, "total": total, "page": page, "page_size": page_size, "path": str(self.runtime.config.audit_log_path)}

    def api_audit_export(self, query: str, ctx: RequestContext) -> tuple[str, bytes]:
        payload = self.api_recent_audit_from_params(urllib.parse.parse_qs(query), ctx)
        rows = payload["items"]
        header = ["timestamp", "actor", "action", "status", "target", "details", "request_id"]
        lines = [",".join(header)]
        for row in rows:
            values = [
                str(row.get("timestamp", "")),
                str(row.get("actor", "")),
                str(row.get("action", "")),
                str(row.get("status", "")),
                str(row.get("target", "")),
                json.dumps(row.get("details", {}), ensure_ascii=False),
                str(row.get("request_id", "")),
            ]
            lines.append(",".join(csv_escape(value) for value in values))
        self.record_audit("audit_export", "ok", "audit.csv", {"count": len(rows)}, ctx)
        return ("audit.csv", ("\n".join(lines) + "\n").encode("utf-8-sig"))

    def api_audit_export_signed(self, query: str, ctx: RequestContext) -> Path:
        parsed_query = urllib.parse.parse_qs(query)
        payload = self.api_recent_audit_from_params(parsed_query, ctx)
        rows = payload["items"]
        result = self.runtime.enterprise.export_signed_audit(rows, query={key: values for key, values in parsed_query.items() if key != "token"})
        status = str(result.get("status") or "")
        if status != "completed":
            raise ApiError(status or "needs_config", str(result.get("message") or "Signed audit export unavailable."), status=409, details=result)
        return Path(str(result["path"]))

    def api_verify_signed_audit(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        path_value = require_string(payload, "path")
        path = Path(path_value).expanduser().resolve()
        workspace = self.runtime.config.workspace_dir.resolve()
        if not path.is_file() or not path.is_relative_to(workspace):
            self.record_audit("enterprise.audit_export_verify", "blocked", redact_target(path_value), {"reason": "outside workspace"}, ctx)
            raise ApiError("blocked", "Signed audit export must be inside workspace.", status=403)
        return self.runtime.enterprise.verify_signed_audit_export(path)

    def api_tasks_recent(self, *, limit: int, ctx: RequestContext | None = None) -> dict[str, object]:
        tasks = self.load_tasks(limit=limit)
        if ctx:
            self.record_audit("tasks.recent", "ok", "web_tasks", {"count": len(tasks)}, ctx)
        return {"items": tasks, "tasks": tasks, "total": len(tasks)}

    def api_task_get(self, task_id: str, ctx: RequestContext) -> dict[str, object]:
        with self._task_lock:
            path = self.task_dir() / f"{sanitize_id(task_id)}.json"
            if not path.is_file() or not path.resolve().is_relative_to(self.task_dir()):
                raise ApiError("not_found", "Task not found.", status=404)
            task = json.loads(path.read_text(encoding="utf-8"))
        self.record_audit("tasks.get", "ok", task_id, {}, ctx)
        return task

    def api_task_events(self, task_id: str, ctx: RequestContext) -> dict[str, object]:
        task = self.api_task_get(task_id, ctx)
        output = task.get("output") if isinstance(task.get("output"), dict) else {}
        events = output.get("events") if isinstance(output.get("events"), list) else []
        return {"task_id": task_id, "status": task.get("status"), "events": events, "total": len(events)}

    def api_task_cancel(self, task_id: str, ctx: RequestContext) -> dict[str, object]:
        task = self.api_task_get(task_id, ctx)
        task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
        if task.get("type") == "meeting" and str(task_input.get("step") or "") == "realtime_capture" and task.get("status") in {"starting", "running", "stopping"}:
            self.record_audit("tasks.cancel", "blocked", task_id, {"reason": "realtime_capture_requires_meeting_stop", "meeting_id": task_input.get("meeting_id")}, ctx)
            raise ApiError(
                "realtime_capture_requires_stop",
                "Realtime capture tasks must be stopped through /api/meeting/realtime/stop so provider capture, workspace outputs, and task monitor stay consistent.",
                status=409,
                details={"task_id": task_id, "meeting_id": task_input.get("meeting_id"), "stop_endpoint": "/api/meeting/realtime/stop"},
            )
        if task.get("status") in {"completed", "blocked", "failed"}:
            raise ApiError("conflict", "Task is already finished.", status=409)
        task["status"] = "blocked"
        task["updated_at"] = now_iso()
        task["error"] = {"code": "cancelled", "message": "Cancelled by web user."}
        atomic_write_json(self.task_dir() / f"{sanitize_id(task_id)}.json", task)
        self.record_audit("tasks.cancel", "blocked", task_id, {}, ctx)
        return task

def dispatch_get(server:Any,path:str,params:dict[str,list[str]],ctx:Any)->Any:
    if path in {"/api/audit/recent","/api/audit/search"}: return server.api_recent_audit_from_params(params,ctx)
    if path in {"/api/tasks","/api/tasks/recent"}: return server.api_tasks_recent(limit=safe_int(params.get("limit",["20"])[0],20),ctx=ctx)
    if path.startswith("/api/tasks/") and path.endswith("/events"):
        return server.api_task_events(path.removeprefix("/api/tasks/").removesuffix("/events").strip("/"),ctx)
    if path.startswith("/api/tasks/"): return server.api_task_get(path.removeprefix("/api/tasks/").strip("/"),ctx)
    return NOT_HANDLED
def dispatch_post(server:Any,path:str,payload:dict[str,Any],ctx:Any)->Any:
    if path=="/api/security/verify-signed-audit": return server.api_verify_signed_audit(payload,ctx)
    if path.startswith("/api/tasks/") and path.endswith("/cancel"):
        return server.api_task_cancel(path.removeprefix("/api/tasks/").removesuffix("/cancel").strip("/"),ctx)
    return NOT_HANDLED
