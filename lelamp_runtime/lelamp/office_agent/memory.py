from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .audit import AuditLogger


class MemoryService:
    def __init__(self, path: Path, audit: AuditLogger):
        self.path = path
        self.audit = audit
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def remember(self, key: str, value: str, category: str = "general") -> dict[str, str]:
        item = {
            "id": str(uuid4()),
            "timestamp": datetime.now(UTC).isoformat(),
            "category": category,
            "key": key,
            "value": value,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True))
            stream.write("\n")
        self.audit.record("memory.remember", target=key, details={"category": category})
        return item

    def search(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        query_lower = query.lower()
        matches: list[dict[str, str]] = []
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                haystack = f"{item.get('category', '')} {item.get('key', '')} {item.get('value', '')}".lower()
                if query_lower in haystack:
                    matches.append(item)
        self.audit.record("memory.search", target=query, details={"count": len(matches[:limit])})
        return matches[-limit:]

    def list_recent(self, limit: int = 10) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        items: list[dict[str, str]] = []
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        self.audit.record("memory.recent", details={"limit": limit})
        return items[-limit:]
