from __future__ import annotations

import re
from pathlib import Path


def safe_filename(value: str, *, default: str = "artifact", suffix: str = "") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ \-\u4e00-\u9fff]+", "", value).strip().replace(" ", "_")
    cleaned = cleaned.strip("._-")
    if not cleaned:
        cleaned = default
    if suffix and not cleaned.endswith(suffix):
        cleaned = f"{cleaned}{suffix}"
    return cleaned


def dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def clamp_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[TRUNCATED]"
