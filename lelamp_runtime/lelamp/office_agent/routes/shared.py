from __future__ import annotations

from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from ..documents import DocumentExtractionError
from ..shared_space import TEXT_PREVIEW_SUFFIXES
from ._base import ApiError, NOT_HANDLED, RequestContext, exact_payload

def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)
def atomic_write_text_file(*a,**kw): return _helper("atomic_write_text_file")(*a,**kw)
def is_text_workflow_path(*a,**kw): return _helper("is_text_workflow_path")(*a,**kw)
def normalize_task_status(*a,**kw): return _helper("normalize_task_status")(*a,**kw)
def redact_target(*a,**kw): return _helper("redact_target")(*a,**kw)
def require_file_path(*a,**kw): return _helper("require_file_path")(*a,**kw)
def require_string(*a,**kw): return _helper("require_string")(*a,**kw)
def safe_int(*a,**kw): return _helper("safe_int")(*a,**kw)
def shared_file_matches_type(*a,**kw): return _helper("shared_file_matches_type")(*a,**kw)
def status_to_audit(*a,**kw): return _helper("status_to_audit")(*a,**kw)

class SharedRoutesMixin:
    def api_shared_files(self, params: dict[str, list[str]] | None = None, ctx: RequestContext | None = None) -> dict[str, object]:
        params = params or {}
        query = (params.get("q", [""])[0] or "").lower()
        status_filter = params.get("status", [""])[0]
        type_filter = params.get("type", [""])[0].strip().lower()
        page = max(1, safe_int(params.get("page", ["1"])[0], 1))
        page_size = min(100, max(1, safe_int(params.get("page_size", ["20"])[0], 20)))
        files = [self.shared_file_dto(item.as_dict()) for item in self.shared_space.list_files()]
        if query:
            files = [item for item in files if query in f"{item['name']} {item['relative_path']} {item['mime_type']}".lower()]
        if status_filter:
            files = [item for item in files if item.get("status") == status_filter]
        if type_filter:
            files = [item for item in files if shared_file_matches_type(item, type_filter)]
        total = len(files)
        start = (page - 1) * page_size
        if ctx:
            self.record_audit("shared_space.list", "ok", "shared_inbox", {"total": total, "page": page, "page_size": page_size}, ctx)
        return {
            "shared_inbox": str(self.shared_space.inbox_dir),
            "items": files[start : start + page_size],
            "files": files[start : start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def api_shared_preview(self, workspace_name: str, ctx: RequestContext) -> dict[str, object]:
        if not workspace_name:
            raise ApiError("missing_file", "Missing required query parameter: file", status=400)
        try:
            preview = self.shared_space.read_preview(workspace_name)
        except ValueError as exc:
            self.record_audit("file_read", "blocked", redact_target(workspace_name), {"reason": str(exc)}, ctx)
            raise ApiError("blocked", "File access blocked by workspace/shared_inbox policy.", status=403) from exc
        if preview.get("status") == "binary":
            safe = self.ensure_allowed_path(workspace_name, ctx, action="shared_preview_extract")
            if is_text_workflow_path(safe.path):
                try:
                    extracted = self.runtime.documents.extract_document_text(safe.workspace_name, max_chars=12000)
                    source = extracted.get("source") if isinstance(extracted.get("source"), dict) else {}
                    preview = {
                        **preview,
                        "status": "ok",
                        "download_only": False,
                        "text": str(extracted.get("text") or ""),
                        "truncated": bool(source.get("truncated")),
                        "document_text_backend": source.get("backend"),
                    }
                except DocumentExtractionError as exc:
                    preview = {
                        **preview,
                        "status": exc.status,
                        "text": str(exc),
                        "document_text_backend": exc.backend,
                    }
        self.record_audit("file_read", "ok", str(preview.get("workspace_name") or workspace_name), {"preview_status": preview.get("status")}, ctx)
        return preview

    def api_workspace_files(self, params: dict[str, list[str]] | None = None, ctx: RequestContext | None = None) -> dict[str, object]:
        params = params or {}
        query = (params.get("q", [""])[0] or "").lower()
        type_filter = params.get("type", [""])[0].strip().lower()
        page = max(1, safe_int(params.get("page", ["1"])[0], 1))
        page_size = min(300, max(1, safe_int(params.get("page_size", ["100"])[0], 100)))
        files = [self.shared_file_dto({"workspace_name": str(item.path.relative_to(self.runtime.workspace.root)), "name": item.name, "size_bytes": item.size_bytes, "sha256": item.sha256}) for item in self.runtime.workspace.list_files()]
        if query:
            files = [item for item in files if query in f"{item['name']} {item['relative_path']} {item['mime_type']}".lower()]
        if type_filter:
            files = [item for item in files if shared_file_matches_type(item, type_filter)]
        total = len(files)
        start = (page - 1) * page_size
        if ctx:
            self.record_audit("workspace.list", "ok", "workspace", {"total": total, "page": page, "page_size": page_size}, ctx)
        return {
            "workspace": str(self.runtime.workspace.root),
            "shared_inbox": str(self.shared_space.inbox_dir),
            "items": files[start : start + page_size],
            "files": files[start : start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def api_workspace_preview(self, workspace_name: str, ctx: RequestContext) -> dict[str, object]:
        if not workspace_name:
            raise ApiError("missing_file", "Missing required query parameter: file", status=400)
        safe = self.ensure_allowed_path(workspace_name, ctx, action="workspace_preview")
        stat = safe.path.stat()
        payload: dict[str, object] = {
            "status": "binary",
            "workspace_name": safe.workspace_name,
            "name": safe.path.name,
            "size_bytes": stat.st_size,
            "download_only": True,
        }
        if safe.path.suffix.lower() in TEXT_PREVIEW_SUFFIXES:
            raw = safe.path.read_bytes()[:200_000]
            payload.update(
                {
                    "status": "ok",
                    "text": raw.decode("utf-8", "replace"),
                    "truncated": stat.st_size > 200_000,
                }
            )
        elif is_text_workflow_path(safe.path):
            try:
                extracted = self.runtime.documents.extract_document_text(safe.workspace_name, max_chars=12000)
                source = extracted.get("source") if isinstance(extracted.get("source"), dict) else {}
                payload.update(
                    {
                        "status": "ok",
                        "download_only": False,
                        "text": str(extracted.get("text") or ""),
                        "truncated": bool(source.get("truncated")),
                        "document_text_backend": source.get("backend"),
                    }
                )
            except DocumentExtractionError as exc:
                payload.update({"status": exc.status, "text": str(exc), "document_text_backend": exc.backend})
        self.record_audit("workspace.preview", status_to_audit(str(payload.get("status"))), safe.workspace_name, {"size_bytes": stat.st_size}, ctx)
        return payload

    def api_workspace_markdown_save(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        workspace_name = require_string(payload, "file_path")
        content = str(payload.get("content") or "")
        if len(content.encode("utf-8")) > 500_000:
            raise ApiError("content_too_large", "Markdown 内容不能超过 500 KB。", status=413)
        safe = self.ensure_allowed_path(workspace_name, ctx, action="workspace.markdown_save")
        if safe.path.suffix.lower() not in {".md", ".markdown"}:
            self.record_audit(
                "workspace.markdown_save",
                "blocked",
                safe.workspace_name,
                {"reason": "unsupported_suffix"},
                ctx,
            )
            raise ApiError("unsupported_file_type", "只允许编辑 Markdown 文件。", status=400)
        atomic_write_text_file(safe.path, content)
        result = {
            "status": "saved",
            "workspace_name": safe.workspace_name,
            "name": safe.path.name,
            "size_bytes": safe.path.stat().st_size,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.record_audit(
            "workspace.markdown_save",
            "ok",
            safe.workspace_name,
            {"size_bytes": result["size_bytes"], "chars": len(content)},
            ctx,
        )
        return result

    def api_shared_download(self, workspace_name: str, ctx: RequestContext) -> Path:
        if not workspace_name:
            raise ApiError("missing_file", "Missing required query parameter: file", status=400)
        try:
            path = self.shared_space.resolve_shared_file(workspace_name)
        except ValueError as exc:
            self.record_audit("shared_space.download", "blocked", redact_target(workspace_name), {"reason": str(exc)}, ctx)
            raise ApiError("blocked", "File access blocked by shared_inbox policy.", status=403) from exc
        self.record_audit(
            "shared_space.download",
            "ok",
            target=str(path.relative_to(self.runtime.config.workspace_dir)),
            details={"size_bytes": path.stat().st_size},
            ctx=ctx,
        )
        return path

    def api_workspace_file(self, workspace_name: str, ctx: RequestContext, *, action: str = "workspace.file") -> Path:
        if not workspace_name:
            raise ApiError("missing_file", "Missing required query parameter: file", status=400)
        safe = self.ensure_allowed_path(workspace_name, ctx, action=action)
        self.record_audit(action, "ok", safe.workspace_name, {"size_bytes": safe.path.stat().st_size}, ctx)
        return safe.path

    def api_shared_upload(self, content_type: str, body: bytes, ctx: RequestContext) -> dict[str, object]:
        if "multipart/form-data" not in content_type:
            raise ApiError("bad_upload", "Expected multipart/form-data upload.", status=400)
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        )
        if not message.is_multipart():
            raise ApiError("bad_upload", "Malformed upload body.", status=400)
        uploaded = []
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename and payload:
                try:
                    uploaded.append(self.shared_file_dto(self.shared_space.put_bytes(filename, payload, source="web_console_upload").as_dict()))
                except ValueError as exc:
                    self.record_audit("upload", "blocked", redact_target(filename), {"reason": str(exc)}, ctx)
                    raise ApiError("blocked", "Upload blocked by shared_inbox policy.", status=403) from exc
        if not uploaded:
            raise ApiError("bad_upload", "No file was uploaded.", status=400)
        self.record_audit("upload", "ok", "shared_inbox", {"count": len(uploaded), "files": [item["relative_path"] for item in uploaded]}, ctx)
        return {"status": "ok", "files": uploaded}

    def api_shared_note(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = require_string(payload, "title")
        text = str(payload.get("content") or payload.get("text") or "")
        if not text.strip():
            raise ApiError("missing_content", "Missing note content.", status=400)
        item = self.shared_file_dto(self.shared_space.put_note(title, text, source="web_console_note").as_dict())
        self.record_audit("note_create", "ok", str(item["relative_path"]), {"chars": len(text)}, ctx)
        return {"status": "ok", "file": item}

    def api_shared_file_action(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        file_path = require_file_path(payload)
        action = require_string(payload, "action")
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        safe = self.ensure_allowed_path(file_path, ctx, action="file_action")
        result: dict[str, object]
        task_type = "document"
        if action == "analyze":
            result = self.api_document_analyze({"file_path": safe.workspace_name}, ctx)
        elif action == "summarize":
            result = self.api_document_summarize({"file_path": safe.workspace_name, "style": params.get("style", "brief")}, ctx)
        elif action == "report_outline":
            result = self.api_document_report_outline({"file_path": safe.workspace_name, "topic": params.get("topic") or Path(safe.workspace_name).stem}, ctx)
        elif action == "key_data_table":
            result = self.api_document_table_extract({"file_path": safe.workspace_name}, ctx)
        elif action == "search":
            query = str(params.get("q") or Path(safe.workspace_name).stem)
            result = self.runtime.file_search.search(query, limit=10)
            task_type = "assistant"
        elif action == "generate_minutes":
            result = self.api_meeting_minutes({"transcript": safe.workspace_name, "title": Path(safe.workspace_name).stem}, ctx)
            task_type = "meeting"
        elif action == "followup_package":
            result = self.api_meeting_followup({"transcript": safe.workspace_name, "title": Path(safe.workspace_name).stem, "render_projection": True}, ctx)
            task_type = "meeting"
        else:
            self.record_audit("file_action", "blocked", safe.workspace_name, {"action": action, "reason": "unknown action"}, ctx)
            raise ApiError("unknown_action", f"Unsupported file action: {action}", status=400)
        task = self.create_task(
            title=f"{action}: {safe.workspace_name}",
            task_type=task_type,
            status=normalize_task_status(str(result.get("status") or "completed")),
            input_payload={"file_path": safe.workspace_name, "action": action},
            output=result,
        )
        self.record_audit("file_action", "ok", safe.workspace_name, {"action": action, "task_id": task["task_id"]}, ctx)
        return {"status": task["status"], "task_id": task["task_id"], "result": result}

def dispatch_get(server:Any,path:str,params:dict[str,list[str]],ctx:Any)->Any:
    if path=="/api/shared/files": return server.api_shared_files(params,ctx)
    if path=="/api/shared/preview": return server.api_shared_preview(params.get("file",[""])[0],ctx)
    if path=="/api/workspace/files": return server.api_workspace_files(params,ctx)
    if path=="/api/workspace/preview": return server.api_workspace_preview(params.get("file",[""])[0],ctx)
    return NOT_HANDLED
POST={"/api/shared/note":"api_shared_note", "/api/workspace/markdown":"api_workspace_markdown_save", "/api/shared/file-action":"api_shared_file_action"}
def dispatch_post(server:Any,path:str,payload:dict[str,Any],ctx:Any)->Any:
    return exact_payload(server,path,payload,ctx,POST)
