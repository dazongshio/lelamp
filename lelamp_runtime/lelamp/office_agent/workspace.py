from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

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
            for path in sorted(self.root.iterdir())
            if path.is_file() and not path.name.startswith(".")
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
        safe = safe_filename(filename, default="artifact")
        path = (self.root / safe).resolve()
        if not path.is_relative_to(self.root):
            self.audit.record(
                "workspace.new_file",
                status="blocked",
                target=str(path),
                details={"reason": "outside workspace"},
            )
            raise WorkspaceError("Invalid workspace file name.")
        return dedupe_path(path)

    def resolve_workspace_file(self, filename: str) -> Path:
        candidate = (self.root / filename).resolve()
        if not candidate.is_file() or not candidate.is_relative_to(self.root):
            self.audit.record(
                "workspace.resolve",
                status="blocked",
                target=str(candidate),
                details={"reason": "not in workspace"},
            )
            raise WorkspaceError(f"Workspace file not found: {filename}")
        return candidate

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

    def _is_allowed_root(self, path: Path) -> bool:
        return any(path.is_relative_to(root) for root in self.allowed_roots)

    def _dedupe_destination(self, path: Path) -> Path:
        return dedupe_path(path)
