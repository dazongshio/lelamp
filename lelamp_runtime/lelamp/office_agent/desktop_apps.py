from __future__ import annotations

import base64
import ctypes
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

from .audit import AuditLogger
from .config import PermissionMode



class DesktopAppsMixin:
    def open_app(self, app_name: str) -> dict[str, object]:
        candidates = self._app_candidates(app_name)
        command = self._first_available(candidates)
        payload = {
            "app_name": app_name,
            "candidates": candidates,
            "backend": self.backend,
            "command": command,
        }
        if command is None:
            payload.update(
                {
                    "status": "unavailable",
                    "install_hint": "Install the target desktop app or add its command name to the request.",
                }
            )
            self.audit.record("desktop.open_app", status="blocked", target=app_name, details=payload)
            return payload
        if not self._should_execute():
            payload.update({"status": "planned", "reason": "desktop backend is audit_only"})
            self.audit.record("desktop.open_app", target=app_name, details=payload)
            return payload
        preflight = self.desktop_preflight(require_input_backend=False)
        if not preflight.get("can_execute"):
            payload.update({"status": "blocked", "reason": "desktop preflight failed", "preflight": preflight})
            self.audit.record("desktop.open_app", status="blocked", target=app_name, details=payload)
            return payload
        args = [command]
        if command == "xdg-open":
            args.append("about:blank")
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        payload["status"] = "launched"
        self.audit.record("desktop.open_app", target=app_name, details=payload)
        return payload
    def open_url(self, url_or_query: str) -> dict[str, object]:
        url = normalize_url_or_search(url_or_query)
        payload = {"url": url, "backend": self.backend}
        if not self._should_execute():
            payload.update({"status": "planned", "reason": "desktop backend is audit_only"})
            self.audit.record("desktop.open_url", target=url, details=payload)
            return payload
        preflight = self.desktop_preflight(require_input_backend=False)
        if not preflight.get("can_execute"):
            payload.update({"status": "blocked", "reason": "desktop preflight failed", "preflight": preflight})
            self.audit.record("desktop.open_url", status="blocked", target=url, details=payload)
            return payload
        if shutil.which("xdg-open") is None:
            payload.update({"status": "unavailable", "install_hint": "Install xdg-utils for xdg-open."})
            self.audit.record("desktop.open_url", status="blocked", target=url, details=payload)
            return payload
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        payload["status"] = "opened"
        self.audit.record("desktop.open_url", target=url, details=payload)
        return payload
    def find_files(self, query: str, *, limit: int = 20) -> dict[str, object]:
        roots = self._search_roots()
        normalized_query = _normalize_query(query)
        matches: list[dict[str, object]] = []
        for root in roots:
            if not root.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    name
                    for name in dirnames
                    if not name.startswith(".") and name not in {"node_modules", "__pycache__", ".git"}
                ]
                for filename in filenames:
                    if filename.startswith("."):
                        continue
                    if normalized_query and normalized_query not in filename.lower():
                        continue
                    path = (Path(dirpath) / filename).resolve()
                    try:
                        size_bytes = path.stat().st_size
                    except OSError:
                        continue
                    matches.append(
                        {
                            "name": filename,
                            "path": str(path),
                            "size_bytes": size_bytes,
                            "root": str(root),
                        }
                    )
                    if len(matches) >= limit:
                        break
                if len(matches) >= limit:
                    break
            if len(matches) >= limit:
                break
        payload = {"status": "completed", "query": query, "roots": [str(root) for root in roots], "count": len(matches), "matches": matches}
        self.audit.record("desktop.find_files", target=query, details={"count": len(matches)})
        return payload
    def open_file(self, path_or_query: str) -> dict[str, object]:
        resolved = self._resolve_allowed_file(path_or_query)
        if resolved is None:
            matches = self.find_files(path_or_query, limit=1)["matches"]
            if matches:
                resolved = Path(str(matches[0]["path"])).resolve()
        payload = {"request": path_or_query, "backend": self.backend, "path": str(resolved) if resolved else None}
        if resolved is None:
            payload.update({"status": "not_found"})
            self.audit.record("desktop.open_file", status="blocked", target=path_or_query, details=payload)
            return payload
        if not self._is_allowed(resolved):
            payload.update({"status": "blocked", "reason": "file is outside allowed roots"})
            self.audit.record("desktop.open_file", status="blocked", target=str(resolved), details=payload)
            return payload
        if not self._should_execute():
            payload.update({"status": "planned", "reason": "desktop backend is audit_only"})
            self.audit.record("desktop.open_file", target=str(resolved), details=payload)
            return payload
        preflight = self.desktop_preflight(require_input_backend=False)
        if not preflight.get("can_execute"):
            payload.update({"status": "blocked", "reason": "desktop preflight failed", "preflight": preflight})
            self.audit.record("desktop.open_file", status="blocked", target=str(resolved), details=payload)
            return payload
        if shutil.which("xdg-open") is None:
            payload.update({"status": "unavailable", "install_hint": "Install xdg-utils for xdg-open."})
            self.audit.record("desktop.open_file", status="blocked", target=str(resolved), details=payload)
            return payload
        subprocess.Popen(["xdg-open", str(resolved)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        payload["status"] = "opened"
        self.audit.record("desktop.open_file", target=str(resolved), details=payload)
        return payload
    def media_control(self, command_text: str) -> dict[str, object]:
        normalized = command_text.lower()
        action = "play-pause"
        if self._has(normalized, "下一首", "next"):
            action = "next"
        elif self._has(normalized, "上一首", "previous", "prev"):
            action = "previous"
        elif self._has(normalized, "暂停", "pause"):
            action = "pause"
        elif self._has(normalized, "继续", "播放", "play"):
            action = "play"
        elif self._has(normalized, "停止", "stop"):
            action = "stop"

        payload = {"action": action, "backend": self.backend}
        if not self._should_execute():
            payload.update({"status": "planned", "reason": "desktop backend is audit_only"})
            self.audit.record("desktop.media_control", target=command_text, details=payload)
            return payload
        if shutil.which("playerctl") is None:
            payload.update({"status": "unavailable", "install_hint": "Install playerctl to control desktop media."})
            self.audit.record("desktop.media_control", status="blocked", target=command_text, details=payload)
            return payload
        subprocess.run(["playerctl", action], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        payload["status"] = "sent"
        self.audit.record("desktop.media_control", target=command_text, details=payload)
        return payload
    def set_volume(self, command_text: str) -> dict[str, object]:
        match = re.search(r"(\d{1,3})\s*%?", command_text)
        level = max(0, min(100, int(match.group(1)))) if match else None
        if "调高" in command_text or "大一点" in command_text:
            value = "+5%"
        elif "调低" in command_text or "小一点" in command_text:
            value = "-5%"
        elif level is not None:
            value = f"{level}%"
        else:
            value = "+5%"
        payload = {"value": value, "backend": self.backend}
        if not self._should_execute():
            payload.update({"status": "planned", "reason": "desktop backend is audit_only"})
            self.audit.record("desktop.set_volume", target=command_text, details=payload)
            return payload
        if shutil.which("pactl"):
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", value],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            payload["status"] = "sent"
            payload["backend_command"] = "pactl"
        elif shutil.which("amixer"):
            subprocess.run(
                ["amixer", "set", "Master", value],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            payload["status"] = "sent"
            payload["backend_command"] = "amixer"
        else:
            payload.update({"status": "unavailable", "install_hint": "Install PulseAudio pactl or ALSA amixer."})
            self.audit.record("desktop.set_volume", status="blocked", target=command_text, details=payload)
            return payload
        self.audit.record("desktop.set_volume", target=command_text, details=payload)
        return payload
    def _should_execute(self) -> bool:
        return self.permission_mode == PermissionMode.FULL_CONTROL and self.backend in self.EXECUTION_BACKENDS
    def _new_desktop_artifact(self, filename: str) -> Path:
        base = self.workspace_dir or Path.cwd()
        directory = (base / "desktop_artifacts").resolve()
        stem = Path(filename).stem or "desktop_artifact"
        suffix = Path(filename).suffix or ".png"
        return directory / f"{stem}_{int(time.time())}{suffix}"
    def _screenshot_command(self, output: Path) -> tuple[list[str], Path]:
        if command := shutil.which("gdbus"):
            bus = os.getenv("DBUS_SESSION_BUS_ADDRESS")
            if bus:
                return [
                    "env",
                    f"DBUS_SESSION_BUS_ADDRESS={bus}",
                    command,
                    "call",
                    "--session",
                    "--dest",
                    "org.gnome.Shell.Screenshot",
                    "--object-path",
                    "/org/gnome/Shell/Screenshot",
                    "--method",
                    "org.gnome.Shell.Screenshot.Screenshot",
                    "true",
                    "false",
                    str(output),
                ], output
        if command := shutil.which("gnome-screenshot"):
            return [command, "-f", str(output)], output
        if command := shutil.which("spectacle"):
            return [command, "-b", "-n", "-o", str(output)], output
        if command := shutil.which("grim"):
            return [command, str(output)], output
        if command := shutil.which("import"):
            return [command, "-window", "root", str(output)], output
        if command := shutil.which("xwd"):
            xwd_output = output.with_suffix(".xwd")
            env_display = os.getenv("DISPLAY") or discover_x11_display()
            if env_display:
                return ["env", f"DISPLAY={env_display}", command, "-root", "-silent", "-out", str(xwd_output)], xwd_output
            return [command, "-root", "-silent", "-out", str(xwd_output)], xwd_output
        return [], output
    def _app_candidates(self, app_name: str) -> list[str]:
        normalized = app_name.lower().strip()
        for label, commands in self.APP_ALIASES.items():
            if label.lower() in normalized:
                return list(commands)
        if re.fullmatch(r"[\w.+-]+", normalized):
            return [normalized]
        return []
    def _first_available(self, candidates: list[str]) -> str | None:
        for command in candidates:
            if shutil.which(command):
                return command
        return None
    def _search_roots(self) -> tuple[Path, ...]:
        roots = list(self.allowed_roots)
        if self.workspace_dir is not None:
            roots.insert(0, self.workspace_dir)
        return tuple(dict.fromkeys(path.resolve() for path in roots))
    def _resolve_allowed_file(self, path_or_query: str) -> Path | None:
        candidate = Path(path_or_query).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        for root in self._search_roots():
            possible = (root / path_or_query).resolve()
            if possible.is_file():
                return possible
        return None
    def _is_allowed(self, path: Path) -> bool:
        roots = self._search_roots()
        return any(path.is_relative_to(root) for root in roots)
    def _has(self, text: str, *markers: str) -> bool:
        return any(marker in text for marker in markers)

from .desktop_support import (
    _command_result, _desktop_session, _extract_app_name, _extract_button,
    _extract_file_query, _extract_hotkey, _extract_text_to_type, _extract_url,
    _extract_web_query, _extract_xy, _normalize_query, _preflight_xtest_display,
    _with_xtest_display, _xtest_mouse_move, discover_x11_display, normalize_hotkey,
    normalize_url_or_search, xtest_available, xtest_hotkey, xtest_key_tap,
    xtest_mouse_click, xtest_mouse_move,
)
