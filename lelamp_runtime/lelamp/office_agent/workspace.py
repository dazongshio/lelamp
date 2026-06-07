from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from .audit import AuditLogger
from .utils import dedupe_path, safe_filename


class WorkspaceError(ValueError):
    pass


@dataclass(frozen=True)
class WorkspaceFile:
    path: Path
    size_bytes: int
    sha256: str

    @property
    def name(self) -> str:
        return self.path.name


class Workspace:
    """File whitelist rooted in the local agent workspace."""

    def __init__(self, root: Path, allowed_roots: tuple[Path, ...], audit: AuditLogger):
        self.root = root
        self.allowed_roots = allowed_roots
        self.audit = audit
        self.root.mkdir(parents=True, exist_ok=True)

    def import_file(self, source: str | Path) -> WorkspaceFile:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            self.audit.record("workspace.import", status="blocked", target=str(source_path))
            raise WorkspaceError(f"File not found: {source_path}")

        if not self._is_allowed_root(source_path):
            self.audit.record(
                "workspace.import",
                status="blocked",
                target=str(source_path),
                details={"reason": "source outside allowed roots"},
            )
            raise WorkspaceError(
                "Source file is outside allowed roots. Set OPENCLAW_ALLOWED_ROOTS or "
                "copy the file into the workspace first."
            )

        destination = self._dedupe_destination(self.root / source_path.name)
        shutil.copy2(source_path, destination)
        imported = self.describe_file(destination)
        self.audit.record(
            "workspace.import",
            target=str(destination),
            details={
                "source": str(source_path),
                "sha256": imported.sha256,
                "size_bytes": imported.size_bytes,
            },
        )
        return imported

    def list_files(self) -> list[WorkspaceFile]:
        files = [
            self.describe_file(path)
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and self._is_visible_workspace_file(path)
        ]
        self.audit.record("workspace.list", details={"count": len(files)})
        return files

    def read_text(self, filename: str, *, max_chars: int = 12000) -> str:
        path = self.resolve_workspace_file(filename)
        text = path.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > max_chars
        self.audit.record(
            "workspace.read_text",
            target=str(path),
            details={"chars": min(len(text), max_chars), "truncated": truncated},
        )
        if truncated:
            return text[:max_chars] + "\n[TRUNCATED]"
        return text

    def write_text(self, filename: str, content: str, *, action: str = "workspace.write_text") -> Path:
        path = self.path_for_new_file(filename)
        path.write_text(content, encoding="utf-8")
        self.audit.record(
            action,
            target=str(path),
            details={"chars": len(content)},
        )
        return path

    def write_json(
        self,
        filename: str,
        payload: object,
        *,
        action: str = "workspace.write_json",
    ) -> Path:
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        return self.write_text(filename, content, action=action)

    def path_for_new_file(self, filename: str) -> Path:
        safe = self._safe_workspace_relative_path(filename)
        path = (self.root / safe).resolve()
        if not path.is_relative_to(self.root):
            self.audit.record(
                "workspace.new_file",
                status="blocked",
                target=str(path),
                details={"reason": "outside workspace"},
            )
            raise WorkspaceError("Invalid workspace file name.")
        path.parent.mkdir(parents=True, exist_ok=True)
        return dedupe_path(path)

    def resolve_workspace_file(self, filename: str) -> Path:
        normalized = filename.strip().replace("\\", "/")
        candidate = (self.root / normalized).resolve()
        if candidate.is_file() and candidate.is_relative_to(self.root):
            return candidate

        legacy_mapping = self._legacy_workspace_mapping()
        mapped = legacy_mapping.get(normalized) or legacy_mapping.get(Path(normalized).name)
        if mapped:
            mapped_path = (self.root / mapped).resolve()
            if mapped_path.is_file() and mapped_path.is_relative_to(self.root):
                return mapped_path
        for old_prefix, new_prefix in legacy_mapping.items():
            if normalized.startswith(f"{old_prefix}/"):
                suffix = normalized[len(old_prefix) + 1 :]
                mapped_path = (self.root / new_prefix / suffix).resolve()
                if mapped_path.is_file() and mapped_path.is_relative_to(self.root):
                    return mapped_path

        basename = Path(normalized).name
        if basename:
            matches = [
                path
                for path in self.root.rglob(basename)
                if path.is_file() and path.is_relative_to(self.root) and self._is_visible_workspace_file(path)
            ]
            if len(matches) == 1:
                return matches[0]

        self.audit.record(
            "workspace.resolve",
            status="blocked",
            target=str(candidate),
            details={"reason": "not in workspace"},
        )
        raise WorkspaceError(f"Workspace file not found: {filename}")

    def describe_file(self, path: Path) -> WorkspaceFile:
        resolved = path.resolve()
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return WorkspaceFile(
            path=resolved,
            size_bytes=resolved.stat().st_size,
            sha256=digest.hexdigest(),
        )

    def _is_visible_workspace_file(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return False
        hidden_parts = [part for part in relative.parts if part.startswith(".")]
        if hidden_parts:
            return False
        if relative.parts and relative.parts[0] in {
            "browser_automation",
            "desktop_tasks",
            "perception_runs",
            "web_tasks",
        }:
            return False
        return True

    def _safe_workspace_relative_path(self, filename: str) -> Path:
        source = Path(str(filename).strip().replace("\\", "/"))
        parts = [part for part in source.parts if part not in {"", ".", "/"}]
        if len(parts) > 1:
            safe_parts = [safe_filename(part, default="folder") for part in parts[:-1]]
            safe_name = safe_filename(parts[-1], default="artifact")
            return Path(*safe_parts, safe_name)

        safe_name = safe_filename(source.name or str(filename), default="artifact")
        task = _workspace_task_for_filename(safe_name)
        year, month, day = _workspace_date_for_filename(safe_name)
        return Path(task, year, month, day, safe_name)

    def _legacy_workspace_mapping(self) -> dict[str, str]:
        index_path = self.root / ".workspace_file_index.json"
        if not index_path.is_file():
            return {}
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        mapping = payload.get("files") if isinstance(payload, dict) else {}
        if not isinstance(mapping, dict):
            return {}
        return {str(key): str(value) for key, value in mapping.items() if key and value}

    def _is_allowed_root(self, path: Path) -> bool:
        return any(path.is_relative_to(root) for root in self.allowed_roots)

    def _dedupe_destination(self, path: Path) -> Path:
        return dedupe_path(path)


def _workspace_date_for_filename(filename: str) -> tuple[str, str, str]:
    compact = re.search(r"(20\d{2})(\d{2})(\d{2})", filename)
    if compact:
        return compact.group(1), compact.group(2), compact.group(3)
    dashed = re.search(r"(20\d{2})[-_](\d{2})[-_](\d{2})", filename)
    if dashed:
        return dashed.group(1), dashed.group(2), dashed.group(3)
    now = datetime.now()
    return f"{now.year:04d}", f"{now.month:02d}", f"{now.day:02d}"


def _workspace_task_for_filename(filename: str) -> str:
    name = filename.lower()
    if any(token in name for token in ("meeting", "transcript", "minutes", "followup", "action_items", "decisions", "reminders", "tingwu", "asr")):
        return "meetings"
    if any(token in name for token in ("lamp_head_scan", "desk_observation", "camscanner", "scan_", "_scan", "enhancement", "enhanced_ocr", "ocr", "business_card", "contract", "table_")):
        return "scans"
    if any(token in name for token in ("projection", "projector", "ppt")):
        return "projection"
    if any(token in name for token in ("scene", "camera_preview", "ambient", "mic_activity")):
        return "scene"
    if any(token in name for token in ("hardware", "speaker_test", "mic_test")):
        return "hardware"
    if any(token in name for token in ("target_validation", "validation")):
        return "validation"
    if name.endswith((".wav", ".mp3", ".m4a", ".flac")):
        return "audio"
    if name.endswith((".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md", ".json", ".zip")):
        return "documents"
    return "artifacts"
