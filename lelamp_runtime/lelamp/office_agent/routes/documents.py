from __future__ import annotations
from pathlib import Path
import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.parse
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from ..docmost import DocmostError, build_docmost_markdown
from ..document_workspace import DocumentActor, DocumentWorkspaceError
from ..documents import DocumentExtractionError
from ..llm import LLMError, ResponsesLLM, ResponsesLLMConfig
from ..shared_space import TEXT_PREVIEW_SUFFIXES, find_lan_ip
from ..utils import dedupe_path, safe_filename
from ._base import ApiError, NOT_HANDLED, RequestContext, exact_payload


def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)


def atomic_write_text_file(*args, **kwargs): return _helper("atomic_write_text_file")(*args, **kwargs)
def document_collaborator_color(*args, **kwargs): return _helper("document_collaborator_color")(*args, **kwargs)
def format_bytes(*args, **kwargs): return _helper("format_bytes")(*args, **kwargs)
def humanize_result_title(*args, **kwargs): return _helper("humanize_result_title")(*args, **kwargs)
def is_text_workflow_path(*args, **kwargs): return _helper("is_text_workflow_path")(*args, **kwargs)
def require_file_path(*args, **kwargs): return _helper("require_file_path")(*args, **kwargs)
def safe_int(*args, **kwargs): return _helper("safe_int")(*args, **kwargs)
def scan_result_markdown(*args, **kwargs): return _helper("scan_result_markdown")(*args, **kwargs)
def status_to_audit(*args, **kwargs): return _helper("status_to_audit")(*args, **kwargs)
def wiki_excerpt_from_content(*args, **kwargs): return _helper("wiki_excerpt_from_content")(*args, **kwargs)
def wiki_title_from_content(*args, **kwargs): return _helper("wiki_title_from_content")(*args, **kwargs)


class DocumentsRoutesMixin:
    def api_document_analyze(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(_helper("require_file_path")(payload), ctx, action="document_analyze")
        try:
            result = self.runtime.documents.analyze_text_file(safe.workspace_name)
            status = "completed"
        except (UnicodeDecodeError, DocumentExtractionError) as exc:
            if isinstance(exc, DocumentExtractionError):
                result = exc.as_payload(filename=safe.workspace_name)
                result["adapter_status"] = _helper("document_adapter_status_from_runtime")(self.runtime)
                if exc.backend == "unsupported":
                    result["status"] = "unsupported"
                    result["message"] = "当前文件类型不支持文本分析，请打开或下载原文件查看。"
            else:
                result = {"status": "backend_missing", "summary": "Workspace file could not be decoded as text.", "error": str(exc)}
            status = str(result.get("status") or "backend_missing")
        task = self.create_task("文档分析", "document", status, {"file_path": safe.workspace_name}, result)
        self.record_audit("document_analyze", _helper("status_to_audit")(status), safe.workspace_name, {"task_id": task["task_id"]}, ctx)
        return _helper("document_result_payload")(task, result, status)

    def api_document_summarize(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(_helper("require_file_path")(payload), ctx, action="document_summarize")
        style = str(payload.get("style") or "brief")
        try:
            result = self.runtime.documents.summarize_text_file(safe.workspace_name, style)
            status = "completed"
        except (UnicodeDecodeError, DocumentExtractionError) as exc:
            if isinstance(exc, DocumentExtractionError):
                result = exc.as_payload(filename=safe.workspace_name)
                result["adapter_status"] = _helper("document_adapter_status_from_runtime")(self.runtime)
            else:
                result = {"status": "backend_missing", "summary": "Workspace file could not be decoded as text.", "error": str(exc)}
            status = str(result.get("status") or "backend_missing")
        task = self.create_task("文档摘要", "document", status, {"file_path": safe.workspace_name, "style": style}, result)
        self.record_audit("document_summarize", _helper("status_to_audit")(status), safe.workspace_name, {"task_id": task["task_id"]}, ctx)
        return _helper("document_result_payload")(task, result, status)

    def api_document_risks(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        result = self.api_document_analyze(payload, ctx)
        analysis = result.get("metadata", {})
        risks = [{"marker": item, "level": "medium"} for item in _helper("list_string")(analysis.get("risk_markers") if isinstance(analysis, dict) else [])]
        result["risks"] = risks
        self.record_audit("document_risk_scan", "ok", _helper("require_file_path")(payload), {"risks": len(risks)}, ctx)
        return result

    def api_document_table_extract(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(_helper("require_file_path")(payload), ctx, action="document_table_extract")
        try:
            result = self.runtime.documents.extract_table_from_text(safe.workspace_name)
        except DocumentExtractionError as exc:
            result = {**exc.as_payload(filename=safe.workspace_name), "table_path": "", "rows": 0, "adapter_status": _helper("document_adapter_status_from_runtime")(self.runtime)}
        status = "completed" if int(result.get("rows") or 0) > 0 else "backend_missing"
        task = self.create_task("表格提取", "document", status, {"file_path": safe.workspace_name}, result)
        self.record_audit("document_table_extract", _helper("status_to_audit")(status), safe.workspace_name, {"task_id": task["task_id"], "rows": result.get("rows")}, ctx)
        return _helper("document_result_payload")(task, result, status)

    def api_document_report_outline(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(_helper("require_file_path")(payload), ctx, action="document_report_outline")
        topic = str(payload.get("topic") or Path(safe.workspace_name).stem)
        try:
            result = self.runtime.documents.create_report_outline([safe.workspace_name], topic)
        except DocumentExtractionError as exc:
            result = {**exc.as_payload(filename=safe.workspace_name), "outline_path": "", "adapter_status": _helper("document_adapter_status_from_runtime")(self.runtime)}
        status = str(result.get("status") or ("completed" if result.get("outline_path") else "backend_missing"))
        task = self.create_task("汇报提纲", "document", _helper("normalize_task_status")(status), {"file_path": safe.workspace_name, "topic": topic}, result)
        self.record_audit("document_report_outline", _helper("status_to_audit")(status), safe.workspace_name, {"task_id": task["task_id"], "outline_path": result.get("outline_path")}, ctx)
        return _helper("document_result_payload")(task, result, status)

    def api_document_adapters_status(self, ctx: RequestContext) -> dict[str, object]:
        adapters = _helper("document_adapter_status_from_runtime")(self.runtime)
        self.record_audit("document_adapters.status", "ok", "document_adapters", adapters, ctx)
        return {"adapters": adapters}

    def document_actor(self, ctx: RequestContext) -> DocumentActor:
        actor_id = str(ctx.actor or "lelamp-web").strip() or "lelamp-web"
        return DocumentActor(actor_id=actor_id, display_name=str(ctx.actor_name or "本机用户"), role="viewer")

    def document_workspace_call(self, callback):
        try:
            return callback()
        except DocumentWorkspaceError as exc:
            raise ApiError(exc.code, str(exc), status=exc.status, details=exc.details) from exc

    def api_docs_list(self, params: dict[str, list[str]], ctx: RequestContext) -> dict[str, object]:
        self.sync_existing_result_documents(ctx)
        actor = self.document_actor(ctx)
        query = params.get("q", [""])[0]
        status = params.get("status", ["active"])[0]
        source_type = params.get("source_type", [""])[0]
        space_id = params.get("space_id", [""])[0]
        limit = safe_int(params.get("limit", ["200"])[0], 200)
        documents = self.document_workspace_call(
            lambda: self.documents_workspace.list_documents(
                actor=actor,
                status=status,
                query=query,
                source_type=source_type,
                space_id=space_id,
                limit=limit,
            )
        )
        result = {"status": "completed", "documents": documents, "count": len(documents)}
        self.record_audit("documents.list", "ok", status or "all", {"count": len(documents), "query": query[:100]}, ctx)
        return result

    def sync_existing_result_documents(self, ctx: RequestContext) -> dict[str, object]:
        if getattr(self, "_document_result_sync_completed", False):
            return {"status": "completed", "imported_count": 0, "cached": True}
        lock = getattr(self, "_document_result_sync_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._document_result_sync_lock = lock
        with lock:
            if getattr(self, "_document_result_sync_completed", False):
                return {"status": "completed", "imported_count": 0, "cached": True}
            workspace = self.runtime.config.workspace_dir.resolve()
            actor = self.document_actor(ctx)
            imported = 0
            errors: list[dict[str, str]] = []
            meeting_root = workspace / "meetings" / "会议记录"
            scan_root = workspace / "scans"
            candidates: list[tuple[Path, str]] = []
            if meeting_root.is_dir():
                candidates.extend((path, "meeting") for path in meeting_root.glob("*.md") if path.is_file())
            if scan_root.is_dir():
                for path in scan_root.rglob("*"):
                    if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".pdf"}:
                        continue
                    if not re.search(r"(scan_summary|_ocr(?:-|\.|_)|lamp_head_scan|扫描)", path.name, re.IGNORECASE):
                        continue
                    candidates.append((path, "scan"))
            for source, source_type in sorted(candidates, key=lambda item: str(item[0])):
                try:
                    relative = str(source.resolve().relative_to(workspace))
                    external_id = f"legacy:{relative}"
                    if source.suffix.lower() in {".md", ".markdown"}:
                        self.documents_workspace.import_markdown(
                            actor=actor,
                            source_path=source,
                            source_type=source_type,
                            external_id=external_id,
                            update_existing=True,
                        )
                    else:
                        title = humanize_result_title(source.stem, source_type)
                        content = scan_result_markdown(source, relative, title)
                        existing = self.documents_workspace.find_document_by_external_id(
                            external_id,
                            actor=actor,
                            source_type=source_type,
                        )
                        if existing is None:
                            self.documents_workspace.create_document(
                                actor=actor,
                                title=title,
                                content=content,
                                source_type=source_type,
                                source_path=relative,
                                external_id=external_id,
                            )
                        else:
                            current = self.documents_workspace.get_document(str(existing["id"]), actor=actor)
                            if str(current.get("content") or "") != content or str(current.get("title") or "") != title:
                                self.documents_workspace.update_document(
                                    str(existing["id"]),
                                    actor=actor,
                                    title=title,
                                    content=content,
                                    summary="同步扫描结果",
                                )
                    imported += 1
                except (DocumentWorkspaceError, OSError, UnicodeError) as exc:
                    errors.append({"path": str(source), "message": str(exc)[:300]})
            self._document_result_sync_completed = True
            result = {
                "status": "completed" if not errors else "partial",
                "imported_count": imported,
                "error_count": len(errors),
                "errors": errors[:20],
            }
            self.record_audit("documents.sync_existing_results", status_to_audit(result["status"]), "documents", result, ctx)
            return result

    def api_docs_stats(self, ctx: RequestContext) -> dict[str, object]:
        result = self.document_workspace_call(lambda: self.documents_workspace.stats(actor=self.document_actor(ctx)))
        self.record_audit("documents.stats", "ok", "documents", result, ctx)
        return {"status": "completed", **result}

    def api_docs_create(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        actor = self.document_actor(ctx)
        source_path = str(payload.get("source_path") or "").strip()
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        request_external_id = f"request:{idempotency_key}" if idempotency_key else str(payload.get("external_id") or "")
        if request_external_id:
            existing = self.documents_workspace.find_document_by_external_id(request_external_id, actor=actor)
            if existing is not None:
                return {"status": "completed", "document": existing, "idempotent_replay": True}
        if source_path:
            safe = self.ensure_allowed_path(source_path, ctx, action="documents.import")
            document = self.document_workspace_call(
                lambda: self.documents_workspace.import_markdown(
                    actor=actor,
                    source_path=safe.path,
                    title=str(payload.get("title") or ""),
                    source_type=str(payload.get("source_type") or "imported"),
                    external_id=request_external_id,
                    update_existing=bool(payload.get("update_existing", False)),
                )
            )
            action = "documents.import"
        else:
            document = self.document_workspace_call(
                lambda: self.documents_workspace.create_document(
                    actor=actor,
                    title=str(payload.get("title") or "无标题文档"),
                    content=str(payload.get("content") or ""),
                    source_type=str(payload.get("source_type") or "manual"),
                    space_id=str(payload.get("space_id") or "personal"),
                    template=str(payload.get("template") or ""),
                    external_id=request_external_id,
                )
            )
            action = "documents.create"
        self.record_audit(action, "ok", str(document.get("id")), {"title": document.get("title"), "source_path": source_path}, ctx)
        return {"status": "completed", "document": document}

    def api_docs_migrate(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        workspace = self.runtime.config.workspace_dir.resolve()
        raw_paths = payload.get("paths")
        if isinstance(raw_paths, list):
            candidates = []
            for raw_path in raw_paths[:500]:
                safe = self.ensure_allowed_path(str(raw_path), ctx, action="documents.migrate")
                candidates.append(safe.path)
        else:
            candidates = [
                path
                for path in workspace.rglob("*")
                if path.is_file()
                and path.suffix.lower() in {".md", ".markdown"}
                and ".documents" not in path.parts
                and "node_modules" not in path.parts
            ][:500]
        actor = self.document_actor(ctx)
        imported: list[dict[str, object]] = []
        errors: list[dict[str, str]] = []
        for source in candidates:
            try:
                relative = str(source.resolve().relative_to(workspace))
                imported.append(
                    self.documents_workspace.import_markdown(
                        actor=actor,
                        source_path=source,
                        source_type="imported",
                        external_id=f"legacy:{relative}",
                        update_existing=True,
                    )
                )
            except (DocumentWorkspaceError, OSError, UnicodeError) as exc:
                errors.append({"path": str(source), "message": str(exc)[:300]})
        result = {
            "status": "completed" if not errors else "partial",
            "imported": imported,
            "imported_count": len(imported),
            "error_count": len(errors),
            "errors": errors,
        }
        self.record_audit(
            "documents.migrate",
            "ok" if not errors else "partial",
            "workspace_markdown",
            {"imported_count": len(imported), "error_count": len(errors)},
            ctx,
        )
        return result

    def api_docs_get_route(self, path: str, params: dict[str, list[str]], ctx: RequestContext) -> dict[str, object]:
        parts = [urllib.parse.unquote(part) for part in path.removeprefix("/api/docs/").split("/") if part]
        if not parts:
            raise ApiError("document_not_found", "文档不存在。", status=404)
        document_id = parts[0]
        actor = self.document_actor(ctx)
        resource = parts[1] if len(parts) > 1 else ""
        child_id = parts[2] if len(parts) > 2 else ""
        if not resource:
            document = self.document_workspace_call(lambda: self.documents_workspace.get_document(document_id, actor=actor))
            return {"status": "completed", "document": document}
        if resource == "content":
            document = self.document_workspace_call(lambda: self.documents_workspace.get_document(document_id, actor=actor))
            return {
                "status": "completed",
                "document_id": document_id,
                "title": document.get("title"),
                "content": document.get("content"),
                "content_version": document.get("content_version"),
                "can_edit": document.get("can_edit"),
            }
        if resource == "comments":
            include_resolved = params.get("include_resolved", ["false"])[0].lower() == "true"
            comments = self.document_workspace_call(
                lambda: self.documents_workspace.list_comments(document_id, actor=actor, include_resolved=include_resolved)
            )
            return {"status": "completed", "comments": comments}
        if resource == "history":
            if child_id:
                revision = self.document_workspace_call(
                    lambda: self.documents_workspace.get_revision(document_id, child_id, actor=actor)
                )
                return {"status": "completed", "revision": revision}
            revisions = self.document_workspace_call(lambda: self.documents_workspace.list_revisions(document_id, actor=actor))
            return {"status": "completed", "revisions": revisions}
        if resource == "permissions":
            permissions = self.document_workspace_call(lambda: self.documents_workspace.get_permissions(document_id, actor=actor))
            return {"status": "completed", "permissions": permissions}
        if resource == "attachments":
            if child_id:
                path, attachment = self.document_workspace_call(
                    lambda: self.documents_workspace.attachment_path(document_id, child_id, actor=actor)
                )
                return {
                    "status": "completed",
                    "attachment": attachment,
                    "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                }
            attachments = self.document_workspace_call(lambda: self.documents_workspace.list_attachments(document_id, actor=actor))
            return {"status": "completed", "attachments": attachments}
        if resource == "collaboration-token":
            document = self.document_workspace_call(lambda: self.documents_workspace.get_document(document_id, actor=actor, include_content=False))
            requested_client_id = str(params.get("client_id", [""])[0]).strip()
            client_id = requested_client_id if re.fullmatch(r"[a-zA-Z0-9_-]{8,64}", requested_client_id) else uuid4().hex[:12]
            expires_at = int(time.time()) + 300
            token_payload = {
                "document_id": document_id,
                "actor_id": actor.actor_id,
                "display_name": actor.display_name,
                "client_id": client_id,
                "role": document.get("role"),
                "exp": expires_at,
            }
            encoded = base64.urlsafe_b64encode(
                json.dumps(token_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).decode("ascii").rstrip("=")
            signature = hmac.new(self.token.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
            forwarded_host = ctx.host.split(":", 1)[0] if ctx.host else (find_lan_ip() or "127.0.0.1")
            scheme = "wss" if ctx.forwarded_proto == "https" else "ws"
            return {
                "status": "completed",
                "document_id": document_id,
                "token": f"{encoded}.{signature}",
                "expires_at": expires_at,
                "url": os.getenv("LELAMP_COLLAB_URL", f"{scheme}://{forwarded_host}:8791"),
                "user": {
                    "id": f"{actor.actor_id}:{client_id}",
                    "name": f"{actor.display_name} · {client_id[-2:].upper()}",
                    "color": document_collaborator_color(client_id),
                },
            }
        if resource == "export":
            filename, data = self.document_workspace_call(lambda: self.documents_workspace.export_markdown(document_id, actor=actor))
            return {
                "status": "completed",
                "filename": filename,
                "format": "markdown",
                "content_base64": base64.b64encode(data).decode("ascii"),
            }
        raise ApiError("unknown_document_resource", "未知的文档资源。", status=404)

    def api_docs_post_route(self, path: str, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        parts = [urllib.parse.unquote(part) for part in path.removeprefix("/api/docs/").split("/") if part]
        if not parts:
            raise ApiError("document_not_found", "文档不存在。", status=404)
        document_id = parts[0]
        actor = self.document_actor(ctx)
        resource = parts[1] if len(parts) > 1 else "update"
        child_id = parts[2] if len(parts) > 2 else ""
        if resource in {"update", "content"}:
            document = self.document_workspace_call(
                lambda: self.documents_workspace.update_document(
                    document_id,
                    actor=actor,
                    title=str(payload["title"]) if "title" in payload else None,
                    content=str(payload["content"]) if "content" in payload else None,
                    base_version=safe_int(payload.get("base_version"), 0) if payload.get("base_version") is not None else None,
                    space_id=str(payload["space_id"]) if "space_id" in payload else None,
                    summary=str(payload.get("summary") or "更新文档"),
                )
            )
            self.record_audit(
                "documents.update",
                "ok",
                document_id,
                {"content_version": document.get("content_version"), "title_changed": "title" in payload, "content_changed": "content" in payload},
                ctx,
            )
            return {"status": "completed", "document": document}
        if resource == "trash":
            document = self.document_workspace_call(lambda: self.documents_workspace.trash_document(document_id, actor=actor))
            self.record_audit("documents.trash", "ok", document_id, {}, ctx)
            return {"status": "completed", "document": document}
        if resource == "restore":
            document = self.document_workspace_call(lambda: self.documents_workspace.restore_document(document_id, actor=actor))
            self.record_audit("documents.restore", "ok", document_id, {}, ctx)
            return {"status": "completed", "document": document}
        if resource == "purge":
            result = self.document_workspace_call(lambda: self.documents_workspace.purge_document(document_id, actor=actor))
            self.record_audit("documents.purge", "ok", document_id, {}, ctx)
            return {"status": "completed", "document": result}
        if resource == "favorite":
            document = self.document_workspace_call(
                lambda: self.documents_workspace.set_favorite(document_id, actor=actor, favorite=bool(payload.get("favorite", True)))
            )
            return {"status": "completed", "document": document}
        if resource == "comments" and not child_id:
            comment = self.document_workspace_call(
                lambda: self.documents_workspace.add_comment(
                    document_id,
                    actor=actor,
                    body=str(payload.get("body") or ""),
                    anchor_text=str(payload.get("anchor_text") or ""),
                    parent_id=str(payload.get("parent_id") or ""),
                )
            )
            self.record_audit("documents.comment.create", "ok", document_id, {"comment_id": comment.get("id")}, ctx)
            return {"status": "completed", "comment": comment}
        if resource == "comments" and child_id:
            comment = self.document_workspace_call(
                lambda: self.documents_workspace.update_comment(
                    document_id,
                    child_id,
                    actor=actor,
                    body=str(payload["body"]) if "body" in payload else None,
                    resolved=bool(payload["resolved"]) if "resolved" in payload else None,
                )
            )
            self.record_audit("documents.comment.update", "ok", document_id, {"comment_id": child_id, "resolved": comment.get("resolved")}, ctx)
            return {"status": "completed", "comment": comment}
        if resource == "permissions":
            raw_permissions = payload.get("permissions")
            if not isinstance(raw_permissions, list):
                raise ApiError("invalid_permissions", "权限列表无效。", status=400)
            permissions = self.document_workspace_call(
                lambda: self.documents_workspace.set_permissions(
                    document_id,
                    actor=actor,
                    permissions=[item for item in raw_permissions if isinstance(item, dict)],
                )
            )
            self.record_audit("documents.permissions.update", "ok", document_id, {"count": len(permissions)}, ctx)
            return {"status": "completed", "permissions": permissions}
        if resource == "share-token":
            document = self.document_workspace_call(
                lambda: self.documents_workspace.get_document(document_id, actor=actor, include_content=False)
            )
            if document.get("role") != "owner":
                raise ApiError("share_token_forbidden", "只有文档所有者可以生成分享链接。", status=403)
            principal_id = str(payload.get("principal_id") or "").strip()
            permission = next(
                (
                    item
                    for item in list(document.get("permissions") or [])
                    if str(item.get("principal_id") or "") == principal_id
                    and str(item.get("role") or "") in {"editor", "commenter", "viewer"}
                ),
                None,
            )
            if permission is None:
                raise ApiError("share_principal_not_found", "请先添加协作者并设置权限。", status=400)
            expires_at = int(time.time()) + max(300, min(30 * 86400, safe_int(payload.get("expires_in"), 7 * 86400)))
            session_payload = {
                "document_id": document_id,
                "actor_id": principal_id,
                "display_name": str(permission.get("display_name") or principal_id),
                "exp": expires_at,
            }
            encoded = base64.urlsafe_b64encode(
                json.dumps(session_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).decode("ascii").rstrip("=")
            signature = hmac.new(self.token.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
            session_token = f"{encoded}.{signature}"
            scheme = "https" if ctx.forwarded_proto == "https" else "http"
            base_url = os.getenv("LELAMP_PUBLIC_URL", "").rstrip("/")
            if not base_url:
                base_url = f"{scheme}://{ctx.host or (find_lan_ip() or '127.0.0.1') + ':8790'}"
            share_url = (
                f"{base_url}/documents?document={urllib.parse.quote(document_id)}"
                f"&document_session={urllib.parse.quote(session_token)}"
            )
            self.record_audit(
                "documents.share_token.create",
                "ok",
                document_id,
                {"principal_id": principal_id, "role": permission.get("role"), "expires_at": expires_at},
                ctx,
            )
            return {"status": "completed", "share_url": share_url, "expires_at": expires_at}
        if resource == "history" and child_id:
            document = self.document_workspace_call(
                lambda: self.documents_workspace.restore_revision(document_id, child_id, actor=actor)
            )
            self.record_audit("documents.history.restore", "ok", document_id, {"revision_id": child_id}, ctx)
            return {"status": "completed", "document": document}
        if resource == "attachments":
            attachment = self.document_workspace_call(
                lambda: self.documents_workspace.add_attachment(
                    document_id,
                    actor=actor,
                    filename=str(payload.get("filename") or "附件"),
                    content_base64=str(payload.get("content_base64") or ""),
                    mime_type=str(payload.get("mime_type") or ""),
                )
            )
            self.record_audit(
                "documents.attachment.create",
                "ok",
                document_id,
                {"attachment_id": attachment.get("id"), "filename": attachment.get("filename"), "size": attachment.get("size")},
                ctx,
            )
            return {"status": "completed", "attachment": attachment}
        if resource == "ai" and child_id != "apply":
            document = self.document_workspace_call(
                lambda: self.documents_workspace.get_document(document_id, actor=actor, include_content=True)
            )
            operation = str(payload.get("operation") or "summarize").strip()
            selected_text = str(payload.get("selected_text") or "").strip()
            source_text = selected_text or str(document.get("content") or "")
            suggestion = self.generate_document_ai_suggestion(operation, source_text)
            self.record_audit(
                "documents.ai.read",
                "ok",
                document_id,
                {
                    "operation": operation,
                    "scope": "selection" if selected_text else "document",
                    "source_chars": len(source_text),
                    "result_chars": len(suggestion),
                },
                ctx,
            )
            return {
                "status": "completed",
                "document_id": document_id,
                "operation": operation,
                "suggestion": suggestion,
                "base_version": document.get("content_version"),
            }
        if resource == "ai" and child_id == "apply":
            before = self.document_workspace_call(
                lambda: self.documents_workspace.get_document(document_id, actor=actor, include_content=False)
            )
            operation = str(payload.get("operation") or "ai_edit").strip()
            document = self.document_workspace_call(
                lambda: self.documents_workspace.update_document(
                    document_id,
                    actor=actor,
                    content=str(payload.get("content") or ""),
                    base_version=safe_int(payload.get("base_version"), 0),
                    summary=f"AI 建议：{operation[:40]}",
                )
            )
            self.record_audit(
                "documents.ai.write",
                "ok",
                document_id,
                {
                    "operation": operation,
                    "before_version": before.get("content_version"),
                    "after_version": document.get("content_version"),
                    "mode": str(payload.get("mode") or "replace"),
                },
                ctx,
            )
            return {"status": "completed", "document": document}
        raise ApiError("unknown_document_action", "未知的文档操作。", status=404)

    def generate_document_ai_suggestion(self, operation: str, source_text: str) -> str:
        prompts = {
            "summarize": "用简洁中文总结内容，保留关键事实、决定和数字。",
            "rewrite": "用清晰、自然、专业的中文改写内容，不改变原意。",
            "shorten": "将内容压缩到约一半长度，保留关键信息。",
            "expand": "扩写内容，补充必要的解释和结构，但不要编造事实。",
            "actions": "提取关键决定和行动项，使用 Markdown 列表；行动项尽量包含负责人和时间。",
            "title_toc": "根据内容生成一个具体中文标题和 Markdown 目录，只输出建议内容。",
            "translate": "将内容翻译成自然、准确的中文；已经是中文的部分保持含义并优化表达。",
        }
        instruction = prompts.get(operation)
        if not instruction:
            raise ApiError("invalid_ai_operation", "不支持这个 AI 文档操作。", status=400)
        source = source_text.strip()
        if not source:
            raise ApiError("empty_ai_source", "请先选择内容或填写文档正文。", status=400)
        config = self.runtime.config
        try:
            if config.openai_api_key:
                llm = ResponsesLLM(
                    ResponsesLLMConfig(
                        api_key=config.openai_api_key,
                        base_url=config.openai_base_url,
                        model=config.openai_model,
                        reasoning_effort="low",
                    )
                )
            elif config.dashscope_api_key:
                llm = ResponsesLLM(
                    ResponsesLLMConfig(
                        api_key=config.dashscope_api_key,
                        base_url=getattr(config, "dashscope_vision_base_url", "https://dashscope.aliyuncs.com/compatible-mode"),
                        model=getattr(config, "dashscope_text_model", getattr(config, "dashscope_vision_model", "qwen-plus")),
                        reasoning_effort="low",
                        wire_api="chat_completions",
                    )
                )
            else:
                raise ApiError("ai_not_configured", "尚未配置文档 AI 服务。", status=409)
            return llm.complete(
                instructions=f"{instruction}\n只返回可供用户预览和插入的正文，不要解释处理过程。",
                user_input=source[:100_000],
                timeout=45,
            ).strip()
        except ApiError:
            raise
        except (LLMError, OSError, ValueError) as exc:
            raise ApiError("ai_generation_failed", "AI 建议生成失败，请稍后重试。", status=502) from exc

    def api_docmost_status(self, ctx: RequestContext) -> dict[str, object]:
        status = self.runtime.docmost.status()
        self.record_audit(
            "docmost.status",
            status_to_audit(str(status.get("status") or "unknown")),
            "docmost",
            {
                "configured": status.get("configured"),
                "url": status.get("url"),
                "default_space": status.get("default_space"),
            },
            ctx,
        )
        return status

    def wiki_root(self) -> Path:
        root = (self.runtime.config.workspace_dir / "wiki").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def resolve_wiki_path(self, value: str) -> Path:
        raw = urllib.parse.unquote(str(value or "")).strip().replace("\\", "/")
        if not raw:
            raise ApiError("missing_wiki_path", "Missing wiki page path.", status=400)
        root = self.wiki_root()
        workspace_root = self.runtime.config.workspace_dir.resolve()
        candidate = Path(raw).expanduser().resolve() if Path(raw).is_absolute() else (workspace_root / raw).resolve()
        if not candidate.is_relative_to(root):
            candidate = (root / raw).resolve()
        if not candidate.is_relative_to(root):
            raise ApiError("invalid_wiki_path", "Wiki pages must stay under workspace/wiki.", status=403)
        if candidate.suffix.lower() not in {".md", ".markdown"}:
            raise ApiError("invalid_wiki_suffix", "Wiki pages must be Markdown files.", status=400)
        return candidate

    def new_wiki_path(self, title: str) -> Path:
        cleaned = safe_filename(title or "wiki-page", default="wiki-page", suffix=".md")
        return dedupe_path(self.wiki_root() / cleaned)

    def wiki_page_item(self, path: Path, content: str | None = None) -> dict[str, object]:
        stat = path.stat()
        if content is None:
            content = path.read_text(encoding="utf-8", errors="replace")
        workspace_name = str(path.resolve().relative_to(self.runtime.config.workspace_dir.resolve()))
        title = wiki_title_from_content(content, path)
        excerpt = wiki_excerpt_from_content(content)
        return {
            "path": workspace_name,
            "title": title,
            "excerpt": excerpt,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            "size_bytes": stat.st_size,
            "size_label": format_bytes(stat.st_size),
        }

    def api_wiki_pages(self, ctx: RequestContext) -> dict[str, object]:
        root = self.wiki_root()
        paths = sorted(
            {path for suffix in ("*.md", "*.markdown") for path in root.rglob(suffix) if path.is_file()},
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        pages = [self.wiki_page_item(path) for path in paths]
        self.record_audit("wiki.pages", "ok", "workspace/wiki", {"count": len(pages)}, ctx)
        return {
            "status": "ok",
            "root": str(root),
            "workspace_root": str(self.runtime.config.workspace_dir.resolve()),
            "pages": pages,
        }

    def api_wiki_page(self, path_value: str, ctx: RequestContext) -> dict[str, object]:
        path = self.resolve_wiki_path(path_value)
        if not path.is_file():
            raise ApiError("wiki_page_not_found", "Wiki page not found.", status=404, details={"path": str(path_value)})
        content = path.read_text(encoding="utf-8", errors="replace")
        item = self.wiki_page_item(path, content)
        self.record_audit("wiki.page", "ok", str(item["path"]), {"size_bytes": item["size_bytes"]}, ctx)
        return {"status": "ok", "page": item, "content": content}

    def api_wiki_save_page(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = str(payload.get("title") or "").strip()
        content = str(payload.get("content") if payload.get("content") is not None else "")
        if not title:
            title = wiki_title_from_content(content, Path("wiki-page.md"))
        path_value = str(payload.get("path") or "").strip()
        path = self.resolve_wiki_path(path_value) if path_value else self.new_wiki_path(title)
        existed = path.is_file()
        atomic_write_text_file(path, content)
        item = self.wiki_page_item(path, content)
        self.record_audit(
            "wiki.save_page",
            "ok",
            str(item["path"]),
            {"created": not existed, "title": title, "size_bytes": item["size_bytes"]},
            ctx,
        )
        return {
            "status": "completed",
            "created": not existed,
            "page": item,
            "content": content,
            "message": "Wiki 页面已保存。",
        }

    def api_docmost_sync_file(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(require_file_path(payload), ctx, action="docmost_sync_file")
        title = str(payload.get("title") or safe.path.stem).strip() or safe.path.stem
        space = str(payload.get("space") or "").strip() or None
        try:
            content, source_kind, extraction = self.docmost_file_content(safe)
            markdown = build_docmost_markdown(
                title=title,
                source_path=safe.workspace_name,
                source_kind=source_kind,
                content=content,
            )
            page = self.runtime.docmost.create_page(title=title, markdown=markdown, space=space)
        except DocmostError as exc:
            self.record_audit("docmost.sync_file", "blocked", safe.workspace_name, {"code": exc.code, **exc.details}, ctx)
            raise ApiError(exc.code, str(exc), status=exc.status, details=exc.details) from exc
        result = {
            "status": "completed",
            "provider": "docmost",
            "source_file": safe.workspace_name,
            "source_kind": source_kind,
            "extraction": extraction,
            "docmost_url": self.runtime.config.docmost_url,
            "docmost_page_id": page.page_id,
            "docmost_page_url": page.url,
            "docmost_page_title": page.title,
            "docmost_space_id": page.space_id,
            "docmost_space_slug": page.space_slug,
        }
        task = self.create_task(
            "同步到 Wiki",
            "document",
            "completed",
            {"file_path": safe.workspace_name, "title": title, "space": space or self.runtime.config.docmost_default_space},
            result,
        )
        result["task_id"] = task["task_id"]
        self.record_audit(
            "docmost.sync_file",
            "ok",
            safe.workspace_name,
            {"task_id": task["task_id"], "page_id": page.page_id, "space": page.space_slug},
            ctx,
        )
        return result

    def docmost_file_content(self, safe: SafePath) -> tuple[str, str, dict[str, object]]:
        suffix = safe.path.suffix.lower()
        size = safe.path.stat().st_size
        if suffix in TEXT_PREVIEW_SUFFIXES:
            raw = safe.path.read_bytes()[:1_000_000]
            text = raw.decode("utf-8", "replace")
            return text, suffix.lstrip(".") or "text", {
                "status": "ok",
                "backend": "text_file",
                "size_bytes": size,
                "truncated": size > len(raw),
            }
        if is_text_workflow_path(safe.path):
            workspace_root = self.runtime.config.workspace_dir.resolve()
            if safe.path.resolve().is_relative_to(workspace_root):
                try:
                    extracted = self.runtime.documents.extract_document_text(safe.workspace_name, max_chars=160_000)
                    source = extracted.get("source") if isinstance(extracted.get("source"), dict) else {}
                    return str(extracted.get("text") or ""), suffix.lstrip(".") or "document", {
                        "status": "ok",
                        "backend": str(source.get("backend") or "document_text"),
                        "size_bytes": size,
                        "truncated": bool(source.get("truncated")),
                    }
                except DocumentExtractionError as exc:
                    return "", suffix.lstrip(".") or "document", {
                        "status": exc.status,
                        "backend": exc.backend,
                        "size_bytes": size,
                        "message": str(exc),
                    }
        return "", suffix.lstrip(".") or "file", {
            "status": "unsupported",
            "backend": "metadata_only",
            "size_bytes": size,
            "message": "该文件类型没有可同步的文本内容。",
        }

GET = {"/api/document/adapters/status":"api_document_adapters_status", "/api/docmost/status":"api_docmost_status", "/api/wiki/pages":"api_wiki_pages"}
POST = {"/api/document/analyze":"api_document_analyze", "/api/document/summarize":"api_document_summarize", "/api/document/risks":"api_document_risks", "/api/document/table-extract":"api_document_table_extract", "/api/document/report-outline":"api_document_report_outline", "/api/docmost/sync-file":"api_docmost_sync_file", "/api/wiki/page":"api_wiki_save_page"}

def dispatch_get(server: Any, path: str, params: dict[str, list[str]], ctx: Any) -> Any:
    if path in {"/api/docs", "/api/docs/search"}: return server.api_docs_list(params, ctx)
    if path == "/api/docs/stats": return server.api_docs_stats(ctx)
    if path.startswith("/api/docs/"): return server.api_docs_get_route(path, params, ctx)
    if path == "/api/wiki/page": return server.api_wiki_page(params.get("path", [""])[0], ctx)
    method=GET.get(path); return NOT_HANDLED if method is None else getattr(server, method)(ctx)

def dispatch_post(server: Any, path: str, payload: dict[str, Any], ctx: Any) -> Any:
    if path == "/api/docs": return server.api_docs_create(payload, ctx)
    if path == "/api/docs/migrate": return server.api_docs_migrate(payload, ctx)
    if path.startswith("/api/docs/"): return server.api_docs_post_route(path, payload, ctx)
    scan_result = server.dispatch_scan_post(path, payload, ctx)
    if scan_result is not NOT_HANDLED: return scan_result
    return exact_payload(server, path, payload, ctx, POST)
