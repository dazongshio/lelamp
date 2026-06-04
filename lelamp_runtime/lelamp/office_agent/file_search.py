from __future__ import annotations

import os
import re
from pathlib import Path

from .audit import AuditLogger
from .utils import clamp_text, safe_filename
from .workspace import Workspace


TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".xml",
    ".log",
}


class LocalFileSearchService:
    """Allowed-root file search with filename and lightweight content scoring."""

    def __init__(self, workspace: Workspace, audit: AuditLogger, allowed_roots: tuple[Path, ...]):
        self.workspace = workspace
        self.audit = audit
        self.allowed_roots = tuple(dict.fromkeys([workspace.root.resolve(), *[path.resolve() for path in allowed_roots]]))

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        include_content: bool = True,
        write_report: bool = True,
    ) -> dict[str, object]:
        terms = tokenize_query(query)
        matches: list[dict[str, object]] = []
        for root in self.allowed_roots:
            if not root.exists():
                continue
            for path in self._walk_files(root):
                scored = self._score_path(path, terms, include_content=include_content)
                if scored is None:
                    continue
                matches.append(scored)

        matches.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
        matches = matches[:limit]
        payload: dict[str, object] = {
            "query": query,
            "terms": terms,
            "roots": [str(path) for path in self.allowed_roots],
            "count": len(matches),
            "matches": matches,
        }
        if write_report:
            report_path = self.workspace.write_json(
                safe_filename(f"file_search_{query}", default="file_search", suffix=".json"),
                payload,
                action="file_search.report_write",
            )
            payload["report_path"] = str(report_path)
        self.audit.record("file_search.search", target=query, details={"count": len(matches)})
        return payload

    def _walk_files(self, root: Path):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name
                for name in dirnames
                if not name.startswith(".") and name not in {"node_modules", "__pycache__", ".git", ".venv"}
            ]
            for filename in filenames:
                if filename.startswith("."):
                    continue
                yield (Path(dirpath) / filename).resolve()

    def _score_path(self, path: Path, terms: list[str], *, include_content: bool) -> dict[str, object] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        name_lower = path.name.lower()
        score = 0
        reasons: list[str] = []
        snippets: list[str] = []

        if not terms:
            score += 1
        for term in terms:
            if term in name_lower:
                score += 12
                reasons.append(f"filename:{term}")

        if include_content and path.suffix.lower() in TEXT_SUFFIXES and stat.st_size <= 2_000_000:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            lower_text = text.lower()
            for term in terms:
                count = lower_text.count(term)
                if count:
                    score += min(20, count * 4)
                    reasons.append(f"content:{term}x{count}")
                    snippet = snippet_around(text, term)
                    if snippet and snippet not in snippets:
                        snippets.append(snippet)

        if score <= 0:
            return None
        return {
            "path": str(path),
            "name": path.name,
            "size_bytes": stat.st_size,
            "score": score,
            "reasons": reasons[:8],
            "snippets": snippets[:3],
        }


def tokenize_query(query: str) -> list[str]:
    cleaned = re.sub(r"(帮我|请|搜索|查找|找一下|文件|内容|本地|语义)", " ", query.lower())
    terms = [term for term in re.split(r"[\s,，。:：;；]+", cleaned) if term]
    if terms:
        return terms[:8]
    return [query.lower().strip()] if query.strip() else []


def snippet_around(text: str, term: str, *, radius: int = 80) -> str:
    index = text.lower().find(term.lower())
    if index < 0:
        return ""
    start = max(0, index - radius)
    end = min(len(text), index + len(term) + radius)
    snippet = text[start:end].replace("\n", " ")
    return clamp_text(snippet.strip(), 220)
