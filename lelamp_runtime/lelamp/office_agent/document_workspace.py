from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


DOCUMENT_ROLES = {"owner", "editor", "commenter", "viewer"}
DOCUMENT_STATUSES = {"active", "archived", "trashed"}
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


class DocumentWorkspaceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 400, details: dict[str, object] | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or {}


@dataclass(frozen=True)
class DocumentActor:
    actor_id: str
    display_name: str
    role: str = "viewer"


class DocumentWorkspace:
    """Durable local document store with stable IDs and revision history.

    Markdown remains the portable representation. Metadata, permissions, comments,
    revisions and attachments live beside it under a private workspace directory.
    Existing workspace files are imported by copy and are never modified.
    """

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.root = (self.workspace_root / ".documents").resolve()
        self.documents_dir = self.root / "items"
        self.attachments_dir = self.root / "attachments"
        self.index_path = self.root / "index.json"
        self._lock = threading.RLock()
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.attachments_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._write_json(self.index_path, {"version": 1, "documents": []})

    def list_documents(
        self,
        *,
        actor: DocumentActor,
        status: str = "active",
        query: str = "",
        source_type: str = "",
        space_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]:
        normalized_query = query.strip().casefold()
        with self._lock:
            items = []
            for document_id in self._document_ids():
                metadata = self._metadata(document_id)
                if status and metadata.get("status") != status:
                    continue
                if source_type and metadata.get("source_type") != source_type:
                    continue
                if space_id and metadata.get("space_id") != space_id:
                    continue
                if not self._can(metadata, actor, "view"):
                    continue
                if normalized_query:
                    haystack = " ".join(
                        [
                            str(metadata.get("title") or ""),
                            str(metadata.get("owner_name") or ""),
                            self._content_path(document_id).read_text(encoding="utf-8", errors="replace")[:200_000],
                        ]
                    ).casefold()
                    if normalized_query not in haystack:
                        continue
                items.append(self._summary(metadata, actor))
            items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            return items[: max(1, min(500, int(limit)))]

    def create_document(
        self,
        *,
        actor: DocumentActor,
        title: str,
        content: str = "",
        source_type: str = "manual",
        source_path: str = "",
        space_id: str = "personal",
        template: str = "",
        external_id: str = "",
    ) -> dict[str, object]:
        clean_title = self._clean_title(title or "无标题文档")
        if not content and template:
            content = self._template_content(template, clean_title)
        if not content:
            content = f"# {clean_title}\n\n"
        document_id = uuid4().hex
        now = self._now()
        metadata: dict[str, object] = {
            "id": document_id,
            "engine": "native",
            "engine_document_id": document_id,
            "title": clean_title,
            "space_id": space_id or "personal",
            "owner_id": actor.actor_id,
            "owner_name": actor.display_name,
            "status": "active",
            "source_type": source_type or "manual",
            "source_path": source_path,
            "external_id": external_id.strip()[:200],
            "created_at": now,
            "updated_at": now,
            "content_version": 1,
            "permissions": [
                {
                    "principal_type": "user",
                    "principal_id": actor.actor_id,
                    "display_name": actor.display_name,
                    "role": "owner",
                }
            ],
            "favorite_by": [],
        }
        with self._lock:
            item_dir = self._item_dir(document_id)
            item_dir.mkdir(parents=True, exist_ok=False)
            (item_dir / "revisions").mkdir()
            self._write_text(self._content_path(document_id), content)
            self._write_json(self._metadata_path(document_id), metadata)
            self._write_json(self._comments_path(document_id), {"comments": []})
            self._create_revision(document_id, metadata, content, actor, "创建文档")
            index = self._index()
            index["documents"] = [*list(index.get("documents") or []), document_id]
            self._write_json(self.index_path, index)
            return self.get_document(document_id, actor=actor, include_content=True)

    def import_markdown(
        self,
        *,
        actor: DocumentActor,
        source_path: Path,
        title: str = "",
        source_type: str = "imported",
        external_id: str = "",
        update_existing: bool = False,
    ) -> dict[str, object]:
        source = source_path.resolve()
        if not source.is_file() or source.suffix.lower() not in {".md", ".markdown"}:
            raise DocumentWorkspaceError("invalid_markdown_source", "请选择有效的 Markdown 文件。")
        if not source.is_relative_to(self.workspace_root):
            raise DocumentWorkspaceError("source_outside_workspace", "只能导入工作区内的 Markdown 文件。", status=403)
        content = source.read_text(encoding="utf-8", errors="replace")
        inferred_title = self._markdown_title(content) or source.stem
        relative_source = str(source.relative_to(self.workspace_root))
        if update_existing:
            with self._lock:
                for document_id in self._document_ids():
                    metadata = self._metadata(document_id)
                    same_external_id = bool(external_id) and str(metadata.get("external_id") or "") == external_id
                    same_source = not external_id and str(metadata.get("source_path") or "") == relative_source
                    if str(metadata.get("source_type") or "") == source_type and (same_external_id or same_source):
                        document = self.update_document(
                            document_id,
                            actor=actor,
                            title=title or inferred_title,
                            content=content,
                            status="active",
                            summary="同步来源文档",
                        )
                        if metadata.get("source_path") != relative_source:
                            refreshed = self._metadata(document_id)
                            refreshed["source_path"] = relative_source
                            self._write_json(self._metadata_path(document_id), refreshed)
                            document = self.get_document(document_id, actor=actor)
                        return document
        return self.create_document(
            actor=actor,
            title=title or inferred_title,
            content=content,
            source_type=source_type,
            source_path=relative_source,
            external_id=external_id,
        )

    def get_document(self, document_id: str, *, actor: DocumentActor, include_content: bool = True) -> dict[str, object]:
        with self._lock:
            metadata = self._metadata(document_id)
            self._require(metadata, actor, "view")
            payload = self._summary(metadata, actor)
            payload["permissions"] = list(metadata.get("permissions") or [])
            payload["favorite"] = actor.actor_id in list(metadata.get("favorite_by") or [])
            payload["attachment_count"] = len(self.list_attachments(document_id, actor=actor))
            payload["comment_count"] = len(self.list_comments(document_id, actor=actor, include_resolved=True))
            if include_content:
                payload["content"] = self._content_path(document_id).read_text(encoding="utf-8", errors="replace")
            return payload

    def find_document_by_external_id(
        self,
        external_id: str,
        *,
        actor: DocumentActor,
        source_type: str = "",
    ) -> dict[str, object] | None:
        normalized = external_id.strip()
        if not normalized:
            return None
        with self._lock:
            for document_id in self._document_ids():
                metadata = self._metadata(document_id)
                if str(metadata.get("external_id") or "") != normalized:
                    continue
                if source_type and str(metadata.get("source_type") or "") != source_type:
                    continue
                if self._can(metadata, actor, "view"):
                    return self.get_document(document_id, actor=actor)
        return None

    def update_document(
        self,
        document_id: str,
        *,
        actor: DocumentActor,
        title: str | None = None,
        content: str | None = None,
        base_version: int | None = None,
        status: str | None = None,
        space_id: str | None = None,
        summary: str = "更新文档",
    ) -> dict[str, object]:
        with self._lock:
            metadata = self._metadata(document_id)
            self._require(metadata, actor, "edit")
            if base_version is not None and int(metadata.get("content_version") or 0) != int(base_version):
                raise DocumentWorkspaceError(
                    "document_version_conflict",
                    "文档已在其他位置更新，请同步后重试。",
                    status=409,
                    details={
                        "expected_version": base_version,
                        "current_version": metadata.get("content_version"),
                        "document": self.get_document(document_id, actor=actor, include_content=True),
                    },
                )
            changed = False
            current_content = self._content_path(document_id).read_text(encoding="utf-8", errors="replace")
            next_content = current_content
            if title is not None:
                clean_title = self._clean_title(title)
                if clean_title != metadata.get("title"):
                    metadata["title"] = clean_title
                    changed = True
            if content is not None and content != current_content:
                next_content = content
                self._write_text(self._content_path(document_id), content)
                metadata["content_version"] = int(metadata.get("content_version") or 0) + 1
                changed = True
            if status is not None:
                if status not in DOCUMENT_STATUSES:
                    raise DocumentWorkspaceError("invalid_document_status", "文档状态无效。")
                if status != metadata.get("status"):
                    metadata["status"] = status
                    changed = True
            if space_id is not None and space_id.strip() and space_id != metadata.get("space_id"):
                metadata["space_id"] = space_id.strip()
                changed = True
            if changed:
                metadata["updated_at"] = self._now()
                self._write_json(self._metadata_path(document_id), metadata)
                self._create_revision(document_id, metadata, next_content, actor, summary)
            return self.get_document(document_id, actor=actor, include_content=True)

    def trash_document(self, document_id: str, *, actor: DocumentActor) -> dict[str, object]:
        return self.update_document(document_id, actor=actor, status="trashed", summary="移入回收站")

    def restore_document(self, document_id: str, *, actor: DocumentActor) -> dict[str, object]:
        return self.update_document(document_id, actor=actor, status="active", summary="从回收站恢复")

    def purge_document(self, document_id: str, *, actor: DocumentActor) -> dict[str, object]:
        with self._lock:
            metadata = self._metadata(document_id)
            if self._role(metadata, actor) != "owner":
                raise DocumentWorkspaceError("document_purge_forbidden", "只有文档所有者可以永久删除文档。", status=403)
            if metadata.get("status") != "trashed":
                raise DocumentWorkspaceError("document_not_trashed", "请先将文档移入回收站。", status=409)
            shutil.rmtree(self._item_dir(document_id))
            attachment_dir = self.attachments_dir / document_id
            if attachment_dir.is_dir():
                shutil.rmtree(attachment_dir)
            collaboration_path = self.root / "collaboration" / f"{document_id}.bin"
            if collaboration_path.is_file():
                collaboration_path.unlink()
            index = self._index()
            index["documents"] = [value for value in list(index.get("documents") or []) if str(value) != document_id]
            self._write_json(self.index_path, index)
            return {"id": document_id, "status": "purged"}

    def set_favorite(self, document_id: str, *, actor: DocumentActor, favorite: bool) -> dict[str, object]:
        with self._lock:
            metadata = self._metadata(document_id)
            self._require(metadata, actor, "view")
            favorites = set(str(value) for value in list(metadata.get("favorite_by") or []))
            favorites.add(actor.actor_id) if favorite else favorites.discard(actor.actor_id)
            metadata["favorite_by"] = sorted(favorites)
            self._write_json(self._metadata_path(document_id), metadata)
            return self.get_document(document_id, actor=actor)

    def list_revisions(self, document_id: str, *, actor: DocumentActor) -> list[dict[str, object]]:
        metadata = self._metadata(document_id)
        self._require(metadata, actor, "view")
        revision_dir = self._item_dir(document_id) / "revisions"
        revisions = []
        for path in revision_dir.glob("*.json"):
            payload = self._read_json(path)
            payload.pop("content", None)
            revisions.append(payload)
        revisions.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return revisions

    def get_revision(self, document_id: str, revision_id: str, *, actor: DocumentActor) -> dict[str, object]:
        metadata = self._metadata(document_id)
        self._require(metadata, actor, "view")
        path = self._revision_path(document_id, revision_id)
        if not path.is_file():
            raise DocumentWorkspaceError("revision_not_found", "找不到指定历史版本。", status=404)
        return self._read_json(path)

    def restore_revision(self, document_id: str, revision_id: str, *, actor: DocumentActor) -> dict[str, object]:
        with self._lock:
            metadata = self._metadata(document_id)
            self._require(metadata, actor, "edit")
            path = self._revision_path(document_id, revision_id)
            if not path.is_file():
                raise DocumentWorkspaceError("revision_not_found", "找不到指定历史版本。", status=404)
            revision = self._read_json(path)
            return self.update_document(
                document_id,
                actor=actor,
                title=str(revision.get("title") or metadata.get("title") or "无标题文档"),
                content=str(revision.get("content") or ""),
                summary=f"恢复版本 {revision_id[:8]}",
            )

    def list_comments(self, document_id: str, *, actor: DocumentActor, include_resolved: bool = False) -> list[dict[str, object]]:
        metadata = self._metadata(document_id)
        self._require(metadata, actor, "view")
        comments = list(self._read_json(self._comments_path(document_id)).get("comments") or [])
        if not include_resolved:
            comments = [item for item in comments if not item.get("resolved")]
        return comments

    def add_comment(
        self,
        document_id: str,
        *,
        actor: DocumentActor,
        body: str,
        anchor_text: str = "",
        parent_id: str = "",
    ) -> dict[str, object]:
        clean_body = body.strip()
        if not clean_body:
            raise DocumentWorkspaceError("empty_comment", "评论内容不能为空。")
        with self._lock:
            metadata = self._metadata(document_id)
            self._require(metadata, actor, "comment")
            store = self._read_json(self._comments_path(document_id))
            comments = list(store.get("comments") or [])
            if parent_id and not any(str(item.get("id")) == parent_id for item in comments):
                raise DocumentWorkspaceError("parent_comment_not_found", "回复的评论不存在。", status=404)
            comment = {
                "id": uuid4().hex,
                "document_id": document_id,
                "parent_id": parent_id,
                "body": clean_body,
                "anchor_text": anchor_text[:500],
                "author_id": actor.actor_id,
                "author_name": actor.display_name,
                "created_at": self._now(),
                "updated_at": self._now(),
                "resolved": False,
            }
            comments.append(comment)
            self._write_json(self._comments_path(document_id), {"comments": comments})
            return comment

    def update_comment(
        self,
        document_id: str,
        comment_id: str,
        *,
        actor: DocumentActor,
        body: str | None = None,
        resolved: bool | None = None,
    ) -> dict[str, object]:
        with self._lock:
            metadata = self._metadata(document_id)
            self._require(metadata, actor, "comment")
            store = self._read_json(self._comments_path(document_id))
            comments = list(store.get("comments") or [])
            target = next((item for item in comments if str(item.get("id")) == comment_id), None)
            if target is None:
                raise DocumentWorkspaceError("comment_not_found", "评论不存在。", status=404)
            if body is not None:
                if actor.actor_id != target.get("author_id") and self._role(metadata, actor) != "owner":
                    raise DocumentWorkspaceError("comment_edit_forbidden", "只能修改自己的评论。", status=403)
                target["body"] = body.strip()
            if resolved is not None:
                target["resolved"] = bool(resolved)
                target["resolved_by"] = actor.actor_id if resolved else ""
            target["updated_at"] = self._now()
            self._write_json(self._comments_path(document_id), {"comments": comments})
            return target

    def get_permissions(self, document_id: str, *, actor: DocumentActor) -> list[dict[str, object]]:
        metadata = self._metadata(document_id)
        self._require(metadata, actor, "view")
        return list(metadata.get("permissions") or [])

    def set_permissions(
        self,
        document_id: str,
        *,
        actor: DocumentActor,
        permissions: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        with self._lock:
            metadata = self._metadata(document_id)
            if self._role(metadata, actor) != "owner":
                raise DocumentWorkspaceError("permission_update_forbidden", "只有文档所有者可以修改权限。", status=403)
            cleaned = []
            owner_present = False
            for item in permissions:
                principal_id = str(item.get("principal_id") or "").strip()
                role = str(item.get("role") or "").strip()
                if not principal_id or role not in DOCUMENT_ROLES:
                    continue
                if principal_id == metadata.get("owner_id"):
                    role = "owner"
                    owner_present = True
                cleaned.append(
                    {
                        "principal_type": str(item.get("principal_type") or "user"),
                        "principal_id": principal_id,
                        "display_name": str(item.get("display_name") or principal_id),
                        "role": role,
                    }
                )
            if not owner_present:
                cleaned.insert(
                    0,
                    {
                        "principal_type": "user",
                        "principal_id": metadata["owner_id"],
                        "display_name": metadata["owner_name"],
                        "role": "owner",
                    },
                )
            metadata["permissions"] = cleaned
            metadata["updated_at"] = self._now()
            self._write_json(self._metadata_path(document_id), metadata)
            return cleaned

    def add_attachment(
        self,
        document_id: str,
        *,
        actor: DocumentActor,
        filename: str,
        content_base64: str,
        mime_type: str = "",
    ) -> dict[str, object]:
        metadata = self._metadata(document_id)
        self._require(metadata, actor, "edit")
        try:
            raw = base64.b64decode(content_base64, validate=True)
        except Exception as exc:
            raise DocumentWorkspaceError("invalid_attachment", "附件内容无效。") from exc
        if len(raw) > MAX_ATTACHMENT_BYTES:
            raise DocumentWorkspaceError("attachment_too_large", "附件不能超过 20 MB。", status=413)
        attachment_id = uuid4().hex
        clean_filename = self._clean_filename(filename)
        suffix = Path(clean_filename).suffix.lower()[:12]
        storage_dir = self.attachments_dir / document_id
        storage_dir.mkdir(parents=True, exist_ok=True)
        storage_path = storage_dir / f"{attachment_id}{suffix}"
        self._write_bytes(storage_path, raw)
        item = {
            "id": attachment_id,
            "document_id": document_id,
            "filename": clean_filename,
            "mime_type": mime_type or mimetypes.guess_type(clean_filename)[0] or "application/octet-stream",
            "size": len(raw),
            "checksum": hashlib.sha256(raw).hexdigest(),
            "storage_name": storage_path.name,
            "created_by": actor.actor_id,
            "created_by_name": actor.display_name,
            "created_at": self._now(),
        }
        manifest_path = self._attachments_manifest_path(document_id)
        manifest = self._read_json(manifest_path) if manifest_path.exists() else {"attachments": []}
        manifest["attachments"] = [*list(manifest.get("attachments") or []), item]
        self._write_json(manifest_path, manifest)
        return item

    def list_attachments(self, document_id: str, *, actor: DocumentActor) -> list[dict[str, object]]:
        metadata = self._metadata(document_id)
        self._require(metadata, actor, "view")
        path = self._attachments_manifest_path(document_id)
        return list(self._read_json(path).get("attachments") or []) if path.exists() else []

    def attachment_path(self, document_id: str, attachment_id: str, *, actor: DocumentActor) -> tuple[Path, dict[str, object]]:
        item = next((value for value in self.list_attachments(document_id, actor=actor) if value.get("id") == attachment_id), None)
        if item is None:
            raise DocumentWorkspaceError("attachment_not_found", "附件不存在。", status=404)
        path = (self.attachments_dir / document_id / str(item["storage_name"])).resolve()
        if not path.is_relative_to(self.attachments_dir) or not path.is_file():
            raise DocumentWorkspaceError("attachment_not_found", "附件文件不存在。", status=404)
        return path, item

    def export_markdown(self, document_id: str, *, actor: DocumentActor) -> tuple[str, bytes]:
        document = self.get_document(document_id, actor=actor, include_content=True)
        filename = f"{self._clean_filename(str(document['title']))}.md"
        return filename, str(document.get("content") or "").encode("utf-8")

    def stats(self, *, actor: DocumentActor) -> dict[str, object]:
        return {
            "active": len(self.list_documents(actor=actor, status="active")),
            "trashed": len(self.list_documents(actor=actor, status="trashed")),
            "meeting": len(self.list_documents(actor=actor, status="active", source_type="meeting")),
            "engine": "native",
        }

    def _summary(self, metadata: dict[str, object], actor: DocumentActor) -> dict[str, object]:
        content_path = self._content_path(str(metadata["id"]))
        content = content_path.read_text(encoding="utf-8", errors="replace") if content_path.exists() else ""
        excerpt = re.sub(r"[#>*_`~\[\]()|-]+", " ", content)
        excerpt = re.sub(r"\s+", " ", excerpt).strip()[:180]
        return {
            key: metadata.get(key)
            for key in (
                "id",
                "engine",
                "engine_document_id",
                "title",
                "space_id",
                "owner_id",
                "owner_name",
                "status",
                "source_type",
                "source_path",
                "external_id",
                "created_at",
                "updated_at",
                "content_version",
            )
        } | {
            "excerpt": excerpt,
            "role": self._role(metadata, actor),
            "can_edit": self._can(metadata, actor, "edit"),
            "can_comment": self._can(metadata, actor, "comment"),
        }

    def _create_revision(
        self,
        document_id: str,
        metadata: dict[str, object],
        content: str,
        actor: DocumentActor,
        summary: str,
    ) -> None:
        revision_id = f"{int(time.time() * 1000)}-{uuid4().hex[:10]}"
        payload = {
            "id": revision_id,
            "document_id": document_id,
            "title": metadata.get("title"),
            "content": content,
            "content_version": metadata.get("content_version"),
            "actor_id": actor.actor_id,
            "actor_name": actor.display_name,
            "summary": summary,
            "created_at": self._now(),
            "restorable": True,
        }
        self._write_json(self._revision_path(document_id, revision_id), payload)

    def _document_ids(self) -> list[str]:
        return [str(value) for value in list(self._index().get("documents") or []) if self._metadata_path(str(value)).is_file()]

    def _index(self) -> dict[str, object]:
        return self._read_json(self.index_path)

    def _metadata(self, document_id: str) -> dict[str, object]:
        if not re.fullmatch(r"[a-f0-9]{32}", document_id):
            raise DocumentWorkspaceError("invalid_document_id", "文档 ID 无效。", status=400)
        path = self._metadata_path(document_id)
        if not path.is_file():
            raise DocumentWorkspaceError("document_not_found", "文档不存在。", status=404)
        return self._read_json(path)

    def _role(self, metadata: dict[str, object], actor: DocumentActor) -> str:
        if actor.actor_id == metadata.get("owner_id"):
            return "owner"
        for item in list(metadata.get("permissions") or []):
            if str(item.get("principal_id")) == actor.actor_id:
                return str(item.get("role") or "viewer")
            if str(item.get("principal_type")) == "space" and str(item.get("principal_id")) == str(metadata.get("space_id")):
                return str(item.get("role") or "viewer")
        # A caller-provided role is descriptive only. Access must come from the
        # persisted owner/permission entries; otherwise forged actors could
        # grant themselves document access.
        return "none"

    def _can(self, metadata: dict[str, object], actor: DocumentActor, action: str) -> bool:
        role = self._role(metadata, actor)
        matrix = {
            "view": {"owner", "editor", "commenter", "viewer"},
            "comment": {"owner", "editor", "commenter"},
            "edit": {"owner", "editor"},
        }
        return role in matrix[action]

    def _require(self, metadata: dict[str, object], actor: DocumentActor, action: str) -> None:
        if not self._can(metadata, actor, action):
            raise DocumentWorkspaceError("document_permission_denied", "没有执行此操作的文档权限。", status=403)

    def _item_dir(self, document_id: str) -> Path:
        return self.documents_dir / document_id

    def _metadata_path(self, document_id: str) -> Path:
        return self._item_dir(document_id) / "metadata.json"

    def _content_path(self, document_id: str) -> Path:
        return self._item_dir(document_id) / "content.md"

    def _comments_path(self, document_id: str) -> Path:
        return self._item_dir(document_id) / "comments.json"

    def _attachments_manifest_path(self, document_id: str) -> Path:
        return self._item_dir(document_id) / "attachments.json"

    def _revision_path(self, document_id: str, revision_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9-]+", revision_id):
            raise DocumentWorkspaceError("invalid_revision_id", "历史版本 ID 无效。")
        return self._item_dir(document_id) / "revisions" / f"{revision_id}.json"

    def _template_content(self, template: str, title: str) -> str:
        templates = {
            "meeting": f"# {title}\n\n## 会议摘要\n\n## 关键决定\n\n## 行动项\n\n## 完整转写\n\n",
            "project": f"# {title}\n\n## 背景\n\n## 目标\n\n## 计划\n\n## 风险\n\n## 下一步\n\n",
            "weekly": f"# {title}\n\n## 本周进展\n\n## 数据与结果\n\n## 问题与风险\n\n## 下周计划\n\n",
        }
        return templates.get(template, f"# {title}\n\n")

    def _clean_title(self, title: str) -> str:
        value = re.sub(r"[\x00-\x1f\x7f]+", "", title).strip()
        return (value or "无标题文档")[:160]

    def _clean_filename(self, filename: str) -> str:
        value = Path(str(filename).replace("\\", "/")).name
        value = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", value).strip(" .")
        return (value or "附件")[:180]

    def _markdown_title(self, content: str) -> str:
        match = re.search(r"(?m)^#\s+(.+?)\s*$", content)
        return self._clean_title(match.group(1)) if match else ""

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DocumentWorkspaceError("document_store_corrupt", "文档数据无法读取。", status=500, details={"path": str(path)}) from exc
        return payload if isinstance(payload, dict) else {}

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        self._write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(path)

    def _write_bytes(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temp.write_bytes(content)
        temp.replace(path)
