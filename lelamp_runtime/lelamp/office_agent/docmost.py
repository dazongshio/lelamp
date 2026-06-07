from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .config import OfficeAgentConfig
from .utils import clamp_text


class DocmostError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "docmost_error",
        status: int = 500,
        details: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or {}


@dataclass(frozen=True)
class DocmostPage:
    page_id: str
    title: str
    space_id: str
    space_slug: str
    url: str
    raw: dict[str, object]


class DocmostService:
    """Small Docmost API client for syncing local LeLamp artifacts into a Wiki."""

    def __init__(self, config: OfficeAgentConfig, audit: AuditLogger):
        self.config = config
        self.audit = audit

    def configured(self) -> bool:
        return bool(self.config.docmost_url and self.config.docmost_api_key)

    def status(self) -> dict[str, object]:
        base = {
            "provider": "docmost",
            "url": self.config.docmost_url,
            "default_space": self.config.docmost_default_space,
            "configured": self.config.docmost_api_key != "",
        }
        if not self.config.docmost_url:
            return {**base, "status": "needs_config", "message": "DOCMOST_URL is not configured."}
        if not self.config.docmost_api_key:
            return {**base, "status": "needs_config", "message": "DOCMOST_API_KEY is not configured."}
        try:
            workspace = self.request("/api/workspace/info", {})
            spaces = self.list_spaces()
            resolved = self.resolve_space(self.config.docmost_default_space, spaces=spaces)
        except DocmostError as exc:
            self.audit.record("docmost.status", status="blocked", target=self.config.docmost_url, details=exc.details | {"code": exc.code})
            return {**base, "status": "blocked", "message": str(exc), "details": exc.details}
        payload = {
            **base,
            "status": "ok",
            "workspace": workspace,
            "spaces": spaces,
            "resolved_space": resolved,
        }
        self.audit.record("docmost.status", status="ok", target=self.config.docmost_url, details={"space": resolved.get("name") or resolved.get("slug")})
        return payload

    def list_spaces(self) -> list[dict[str, object]]:
        payload = self.request("/api/spaces", {})
        if isinstance(payload.get("items"), list):
            return [item for item in payload["items"] if isinstance(item, dict)]
        if isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("items"), list):
            return [item for item in payload["data"]["items"] if isinstance(item, dict)]
        if isinstance(payload.get("data"), list):
            return [item for item in payload["data"] if isinstance(item, dict)]
        if isinstance(payload.get("spaces"), list):
            return [item for item in payload["spaces"] if isinstance(item, dict)]
        return []

    def resolve_space(self, name_or_slug_or_id: str | None = None, *, spaces: list[dict[str, object]] | None = None) -> dict[str, object]:
        target = (name_or_slug_or_id or self.config.docmost_default_space or "General").strip()
        spaces = spaces if spaces is not None else self.list_spaces()
        if not spaces:
            raise DocmostError("Docmost has no accessible spaces.", code="docmost_space_missing", status=404)

        def candidates(space: dict[str, object]) -> set[str]:
            return {
                str(space.get("id") or "").strip().lower(),
                str(space.get("name") or "").strip().lower(),
                str(space.get("slug") or "").strip().lower(),
            }

        normalized = target.lower()
        for space in spaces:
            if normalized in candidates(space):
                return space
        for fallback in ("general", "默认", "default"):
            for space in spaces:
                if fallback in candidates(space):
                    return space
        raise DocmostError(
            f"Docmost space not found: {target}",
            code="docmost_space_not_found",
            status=404,
            details={"requested_space": target, "available_spaces": [_space_summary(space) for space in spaces]},
        )

    def create_page(
        self,
        *,
        title: str,
        markdown: str,
        space: str | None = None,
        parent_page_id: str | None = None,
    ) -> DocmostPage:
        if not self.configured():
            raise DocmostError("Docmost is not configured.", code="docmost_needs_config", status=400)
        resolved_space = self.resolve_space(space)
        space_id = str(resolved_space.get("id") or "").strip()
        if not space_id:
            raise DocmostError("Docmost space does not contain an id.", code="docmost_space_invalid", status=500)
        payload: dict[str, object] = {
            "spaceId": space_id,
            "title": title.strip()[:180] or "LeLamp 文档",
            "content": markdown,
            "format": "markdown",
        }
        if parent_page_id:
            payload["parentPageId"] = parent_page_id
        response = self.request("/api/pages/create", payload)
        if isinstance(response.get("page"), dict):
            page = response["page"]
        elif isinstance(response.get("data"), dict):
            page = response["data"]
        else:
            page = response
        page_id = str(page.get("id") or page.get("pageId") or "").strip()
        if not page_id:
            raise DocmostError("Docmost did not return a page id.", code="docmost_page_invalid", status=500, details={"response_keys": sorted(response)})
        space_slug = str(resolved_space.get("slug") or resolved_space.get("name") or space_id).strip()
        page_url = self.page_url(page, space_slug=space_slug, page_id=page_id)
        result = DocmostPage(
            page_id=page_id,
            title=str(page.get("title") or title),
            space_id=space_id,
            space_slug=space_slug,
            url=page_url,
            raw=page,
        )
        self.audit.record("docmost.page.create", status="ok", target=title, details={"page_id": page_id, "space": space_slug, "url": page_url})
        return result

    def page_url(self, page: dict[str, object], *, space_slug: str, page_id: str) -> str:
        page_slug = str(page.get("slug") or page.get("slugId") or page.get("pageSlugId") or "").strip()
        if page_slug:
            return f"{self.config.docmost_url}/s/{space_slug}/p/{page_slug}"
        return f"{self.config.docmost_url}/s/{space_slug}/p/{page_id}"

    def request(self, endpoint: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        if not self.config.docmost_url:
            raise DocmostError("DOCMOST_URL is not configured.", code="docmost_needs_config", status=400)
        if not self.config.docmost_api_key:
            raise DocmostError("DOCMOST_API_KEY is not configured.", code="docmost_needs_config", status=400)
        url = f"{self.config.docmost_url}{endpoint}"
        data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.docmost_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            details = {"http_status": exc.code, "endpoint": endpoint, "body": clamp_text(body, 800)}
            raise DocmostError(f"Docmost API returned HTTP {exc.code}.", code="docmost_http_error", status=exc.code, details=details) from exc
        except urllib.error.URLError as exc:
            raise DocmostError(
                f"Cannot connect to Docmost: {exc.reason}",
                code="docmost_network_error",
                status=502,
                details={"endpoint": endpoint, "reason": str(exc.reason)},
            ) from exc
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            raise DocmostError("Docmost returned a non-JSON response.", code="docmost_bad_response", status=502, details={"body": clamp_text(raw, 800)}) from exc
        if isinstance(parsed, dict):
            return parsed
        raise DocmostError("Docmost returned an unexpected response.", code="docmost_bad_response", status=502, details={"type": type(parsed).__name__})


def build_docmost_markdown(
    *,
    title: str,
    source_path: str,
    content: str,
    source_kind: str,
    local_preview_url: str = "",
    local_download_url: str = "",
    max_chars: int = 160_000,
) -> str:
    lines = [
        f"# {title}",
        "",
        "## 来源",
        f"- LeLamp 文件：`{source_path}`",
        f"- 类型：{source_kind}",
    ]
    if local_preview_url:
        lines.append(f"- 本地预览：{local_preview_url}")
    if local_download_url:
        lines.append(f"- 本地下载：{local_download_url}")
    lines.extend(["", "## 内容", ""])
    text = content.strip()
    if not text:
        text = "该文件没有可抽取的文本内容，请通过本地预览或下载查看原文件。"
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[LeLamp: 内容过长，已截断后同步到 Wiki。]"
    if _looks_like_markdown(text):
        lines.append(text)
    else:
        lines.extend(["```text", text, "```"])
    return "\n".join(lines).strip() + "\n"


def _looks_like_markdown(text: str) -> bool:
    stripped = text.lstrip()
    return bool(re.search(r"(?m)^#{1,6}\s+\S|^- \S|^\d+\. \S|^\|.*\|", stripped[:4000]))


def _space_summary(space: dict[str, object]) -> dict[str, str]:
    return {
        "id": str(space.get("id") or ""),
        "name": str(space.get("name") or ""),
        "slug": str(space.get("slug") or ""),
    }
