from __future__ import annotations

import mimetypes
import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path

from ..routes._base import ApiError, RequestContext


def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)


def SafePath(*args, **kwargs): return _helper("SafePath")(*args, **kwargs)
def format_bytes(*args, **kwargs): return _helper("format_bytes")(*args, **kwargs)
def redact_target(*args, **kwargs): return _helper("redact_target")(*args, **kwargs)


class LocalSystemRuntimeMixin:
    def _audio_backend(self) -> str:
        if shutil.which("wpctl"):
            return "wpctl"
        if shutil.which("pactl"):
            return "pactl"
        raise ApiError("audio_control_unavailable", "系统未安装可用的音量控制工具。", status=503)

    def _run_audio_command(self, command: list[str]) -> str:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "音频命令执行失败").strip()
            raise ApiError("audio_control_failed", "无法调整系统音量。", status=503, details={"error": detail[:500]})
        return completed.stdout.strip()

    def _read_system_audio(self) -> dict[str, object]:
        backend = self._audio_backend()
        if backend == "wpctl":
            output = self._run_audio_command(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
            match = re.search(r"Volume:\s*([0-9.]+)", output)
            if not match:
                raise ApiError("audio_status_unavailable", "无法读取系统音量。", status=503)
            volume = int(round(float(match.group(1)) * 100))
            muted = "[MUTED]" in output.upper()
        else:
            volume_output = self._run_audio_command(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            mute_output = self._run_audio_command(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
            match = re.search(r"(\d+)%", volume_output)
            if not match:
                raise ApiError("audio_status_unavailable", "无法读取系统音量。", status=503)
            volume = int(match.group(1))
            muted = "yes" in mute_output.lower()
        return {"status": "completed", "volume": max(0, min(100, volume)), "muted": muted, "backend": backend}

    def shared_file_dto(self, item: dict[str, object]) -> dict[str, object]:
        workspace_name = str(item.get("workspace_name") or item.get("relative_path") or item.get("name") or "")
        size = int(item.get("size_bytes") or item.get("size") or 0)
        return {
            "name": str(item.get("name") or Path(workspace_name).name),
            "relative_path": workspace_name,
            "workspace_name": workspace_name,
            "size": size,
            "size_bytes": size,
            "size_label": format_bytes(size),
            "sha256": str(item.get("sha256") or ""),
            "mime_type": mimetypes.guess_type(str(item.get("name") or workspace_name))[0] or "application/octet-stream",
            "uploaded_at": str(item.get("uploaded_at") or ""),
            "status": "ready",
            "allowed_actions": ["analyze", "summarize", "report_outline", "key_data_table", "search", "generate_minutes", "followup_package"],
        }

    def ensure_allowed_path(self, input_path: str, ctx: RequestContext, *, action: str = "file_read") -> SafePath:
        value = urllib.parse.unquote(str(input_path or "")).strip().replace("\\", "/")
        if not value:
            raise ApiError("missing_file_path", "Missing file_path.", status=400)
        try:
            workspace_path = self.runtime.workspace.resolve_workspace_file(value)
            return SafePath(workspace_path, str(workspace_path.relative_to(self.runtime.workspace.root)))
        except Exception:
            pass
        candidates: list[Path] = []
        if Path(value).is_absolute():
            candidates.append(Path(value).expanduser().resolve())
        else:
            candidates.append((self.runtime.config.workspace_dir / value).resolve())
            candidates.append((self.runtime.config.workspace_dir.parent / value).resolve())
            if not value.startswith("shared_inbox/"):
                candidates.append((self.shared_space.inbox_dir / value).resolve())
        projection_root = self.runtime.config.projection_dir.resolve()
        roots = tuple(dict.fromkeys([*(path.resolve() for path in self.runtime.config.allowed_roots), projection_root]))
        for candidate in candidates:
            if candidate.is_file() and any(candidate.is_relative_to(root) for root in roots):
                workspace_root = self.runtime.config.workspace_dir.resolve()
                if candidate.is_relative_to(workspace_root):
                    workspace_name = str(candidate.relative_to(workspace_root))
                elif candidate.is_relative_to(projection_root):
                    workspace_name = str(Path(projection_root.name) / candidate.relative_to(projection_root))
                else:
                    workspace_name = str(candidate)
                return SafePath(candidate, workspace_name)
        target = redact_target(value)
        self.record_audit(action, "blocked", target, {"reason": "outside allowed roots or file missing"}, ctx)
        raise ApiError("blocked", "File access blocked by workspace/shared_inbox/allowed roots policy.", status=403, details={"target": target})

    def workspace_relative_path(self, path_value: str) -> str:
        if not path_value:
            return ""
        path = Path(path_value).expanduser().resolve()
        workspace = self.runtime.config.workspace_dir.resolve()
        if path.is_relative_to(workspace):
            return str(path.relative_to(workspace))
        return ""

    def normalize_meeting_transcript_ref(self, transcript: str) -> str:
        value = str(transcript or "").strip()
        if not value:
            return ""
        path = Path(value).expanduser()
        if path.is_absolute():
            return self.workspace_relative_path(value) or value
        return value.removeprefix("./")

