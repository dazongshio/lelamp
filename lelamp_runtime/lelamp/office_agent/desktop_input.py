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



class DesktopInputMixin:
    def __init__(
        self,
        audit: AuditLogger,
        *,
        permission_mode: PermissionMode,
        backend: str,
        allowed_roots: tuple[Path, ...] = (),
        workspace_dir: Path | None = None,
    ):
        self.audit = audit
        self.permission_mode = permission_mode
        self.backend = backend
        self.allowed_roots = tuple(path.resolve() for path in allowed_roots)
        self.workspace_dir = workspace_dir.resolve() if workspace_dir else None
    def request_operation(
        self,
        task_description: str,
        *,
        require_confirmation: bool = True,
    ) -> dict[str, object]:
        if self.permission_mode != PermissionMode.FULL_CONTROL:
            payload = {
                "allowed": False,
                "reason": "full_control mode disabled",
                "required_mode": PermissionMode.FULL_CONTROL.value,
                "current_mode": self.permission_mode.value,
            }
            self.audit.record("desktop.request", status="blocked", target=task_description, details=payload)
            return payload

        payload = {
            "allowed": True,
            "backend": self.backend,
            "task_description": task_description,
            "require_confirmation": require_confirmation,
            "status": "pending_backend" if self.backend == "audit_only" else "pending_execution",
        }
        self.audit.record("desktop.request", target=task_description, details=payload)
        return payload
    def desktop_preflight(self, *, require_input_backend: bool = False) -> dict[str, object]:
        session = _desktop_session()
        xdg_open = shutil.which("xdg-open")
        input_backends = [
            {"name": name, "path": path}
            for name in ("xdotool",)
            if (path := shutil.which(name))
        ]
        xtest_display = discover_x11_display()
        if xtest_display:
            input_backends.append({"name": "xtest", "path": "libXtst.so.6", "display": xtest_display})
        fallback_input_backends = [
            {"name": name, "path": path}
            for name in ("ydotool",)
            if (path := shutil.which(name))
        ]
        screenshot_backends = [
            {"name": name, "path": path}
            for name in ("gnome-screenshot", "spectacle", "grim", "import", "xwd", "gdbus")
            if (path := shutil.which(name))
        ]
        permission_ok = self.permission_mode == PermissionMode.FULL_CONTROL
        backend_ok = self.backend in self.EXECUTION_BACKENDS
        gui_ok = bool(session["has_gui_session"]) or bool(xtest_display)
        opener_ok = xdg_open is not None
        input_ok = bool(input_backends)
        can_execute = permission_ok and backend_ok and gui_ok and opener_ok and (input_ok if require_input_backend else True)
        checks = [
            {
                "name": "permission_mode",
                "status": "passed" if permission_ok else "blocked",
                "expected": PermissionMode.FULL_CONTROL.value,
                "actual": self.permission_mode.value,
            },
            {
                "name": "desktop_backend",
                "status": "passed" if backend_ok else "blocked",
                "expected": sorted(self.EXECUTION_BACKENDS),
                "actual": self.backend,
            },
            {
                "name": "gui_session",
                "status": "passed" if gui_ok else "blocked",
                "expected": "DISPLAY or WAYLAND_DISPLAY",
                "actual": session,
            },
            {
                "name": "url_file_launcher",
                "status": "passed" if opener_ok else "blocked",
                "expected": "xdg-open",
                "actual": xdg_open or "",
            },
        ]
        if require_input_backend:
            checks.append(
                {
                    "name": "input_backend",
                    "status": "passed" if input_ok else "blocked",
                    "expected": "xdotool or ydotool",
                    "actual": input_backends,
                }
            )
        payload = {
            "status": "ready" if can_execute else "blocked",
            "can_execute": can_execute,
            "permission_mode": self.permission_mode.value,
            "desktop_backend": self.backend,
            "session": session,
            "xdg_open": xdg_open or "",
            "input_backends": input_backends,
            "fallback_input_backends": fallback_input_backends,
            "screenshot_backends": screenshot_backends,
            "require_input_backend": require_input_backend,
            "checks": checks,
        }
        self.audit.record("desktop.preflight", status="ok" if can_execute else "blocked", target="desktop", details=payload)
        return payload
    def run_input_probe(self) -> dict[str, object]:
        preflight = self.desktop_preflight(require_input_backend=True)
        if not preflight.get("can_execute"):
            payload = {
                "status": "blocked",
                "message": "Desktop input probe requires full_control, a local backend, a GUI session, and an input backend.",
                "preflight": preflight,
            }
            self.audit.record("desktop.input_probe", status="blocked", target="desktop", details=payload)
            return payload

        xdotool = shutil.which("xdotool")
        if xdotool and os.getenv("DISPLAY"):
            commands = [
                [xdotool, "getmouselocation"],
                [xdotool, "mousemove_relative", "--", "0", "0"],
            ]
            results: list[dict[str, object]] = []
            for command in commands:
                completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
                results.append(
                    {
                        "command": command,
                        "returncode": completed.returncode,
                        "stdout": completed.stdout.strip()[:300],
                        "stderr": completed.stderr.strip()[:300],
                    }
                )
                if completed.returncode != 0:
                    payload = {"status": "blocked", "tool": "xdotool", "preflight": preflight, "results": results}
                    self.audit.record("desktop.input_probe", status="blocked", target="xdotool", details=payload)
                    return payload
            payload = {"status": "completed", "tool": "xdotool", "preflight": preflight, "results": results}
            self.audit.record("desktop.input_probe", target="xdotool", details=payload)
            return payload

        xtest_display = _preflight_xtest_display(preflight)
        if xtest_display:
            mouse_result = xtest_mouse_move(xtest_display, 0, 0, relative=True)
            key_result = xtest_key_tap(xtest_display, "Shift_L")
            completed = mouse_result.get("status") == "completed" and key_result.get("status") == "completed"
            payload = {
                "status": "completed" if completed else "blocked",
                "tool": "xtest",
                "display": xtest_display,
                "preflight": preflight,
                "results": [mouse_result, key_result],
            }
            self.audit.record("desktop.input_probe", status="ok" if payload["status"] == "completed" else "blocked", target="xtest", details=payload)
            return payload

        ydotool = shutil.which("ydotool")
        payload = {
            "status": "adapter_ready",
            "tool": "ydotool" if ydotool else "",
            "preflight": preflight,
            "message": "ydotool is available but this runtime does not run a non-invasive input-control probe; validate with xdotool on X11 or add a target-specific ydotool probe.",
        }
        self.audit.record("desktop.input_probe", status="blocked", target="desktop", details=payload)
        return payload
    def mouse_move(self, x: int, y: int) -> dict[str, object]:
        payload = {"x": x, "y": y, "backend": self.backend}
        preflight = self.desktop_preflight(require_input_backend=True)
        if not self._should_execute() or not preflight.get("can_execute"):
            payload.update({"status": "blocked" if self._should_execute() else "planned", "reason": "desktop input execution is not available", "preflight": preflight})
            self.audit.record("desktop.mouse_move", status="blocked", target=f"{x},{y}", details=payload)
            return payload
        xdotool = shutil.which("xdotool")
        if not xdotool:
            xtest_display = _preflight_xtest_display(preflight)
            if xtest_display:
                result = xtest_mouse_move(xtest_display, x, y)
                payload.update(result)
                payload["tool"] = "xtest"
                payload["display"] = xtest_display
                self.audit.record("desktop.mouse_move", status="ok" if payload["status"] == "completed" else "blocked", target=f"{x},{y}", details=payload)
                return payload
            payload.update({"status": "unavailable", "install_hint": "Install xdotool or make XTest available for mouse control.", "preflight": preflight})
            self.audit.record("desktop.mouse_move", status="blocked", target=f"{x},{y}", details=payload)
            return payload
        completed = subprocess.run([xdotool, "mousemove", str(x), str(y)], check=False, capture_output=True, text=True, timeout=5)
        payload.update(_command_result(completed))
        self.audit.record("desktop.mouse_move", status="ok" if payload["status"] == "completed" else "blocked", target=f"{x},{y}", details=payload)
        return payload
    def mouse_click(self, button: int = 1) -> dict[str, object]:
        button = max(1, min(5, int(button)))
        payload = {"button": button, "backend": self.backend}
        preflight = self.desktop_preflight(require_input_backend=True)
        if not self._should_execute() or not preflight.get("can_execute"):
            payload.update({"status": "blocked" if self._should_execute() else "planned", "reason": "desktop input execution is not available", "preflight": preflight})
            self.audit.record("desktop.mouse_click", status="blocked", target=str(button), details=payload)
            return payload
        xdotool = shutil.which("xdotool")
        if not xdotool:
            xtest_display = _preflight_xtest_display(preflight)
            if xtest_display:
                result = xtest_mouse_click(xtest_display, button)
                payload.update(result)
                payload["tool"] = "xtest"
                payload["display"] = xtest_display
                self.audit.record("desktop.mouse_click", status="ok" if payload["status"] == "completed" else "blocked", target=str(button), details=payload)
                return payload
            payload.update({"status": "unavailable", "install_hint": "Install xdotool or make XTest available for mouse control.", "preflight": preflight})
            self.audit.record("desktop.mouse_click", status="blocked", target=str(button), details=payload)
            return payload
        completed = subprocess.run([xdotool, "click", str(button)], check=False, capture_output=True, text=True, timeout=5)
        payload.update(_command_result(completed))
        self.audit.record("desktop.mouse_click", status="ok" if payload["status"] == "completed" else "blocked", target=str(button), details=payload)
        return payload
    def type_text(self, text: str) -> dict[str, object]:
        safe_text = text[:500]
        payload = {"text_chars": len(safe_text), "backend": self.backend}
        preflight = self.desktop_preflight(require_input_backend=True)
        if not self._should_execute() or not preflight.get("can_execute"):
            payload.update({"status": "blocked" if self._should_execute() else "planned", "reason": "desktop input execution is not available", "preflight": preflight})
            self.audit.record("desktop.type_text", status="blocked", target="text", details=payload)
            return payload
        xdotool = shutil.which("xdotool")
        if not xdotool:
            payload.update({"status": "unavailable", "install_hint": "Install xdotool for keyboard control.", "preflight": preflight})
            self.audit.record("desktop.type_text", status="blocked", target="text", details=payload)
            return payload
        completed = subprocess.run([xdotool, "type", "--delay", "1", safe_text], check=False, capture_output=True, text=True, timeout=10)
        payload.update(_command_result(completed))
        self.audit.record("desktop.type_text", status="ok" if payload["status"] == "completed" else "blocked", target="text", details=payload)
        return payload
    def send_hotkey(self, hotkey: str) -> dict[str, object]:
        normalized = normalize_hotkey(hotkey)
        payload = {"hotkey": normalized, "backend": self.backend}
        if not normalized:
            payload.update({"status": "blocked", "reason": "invalid hotkey"})
            self.audit.record("desktop.hotkey", status="blocked", target=hotkey, details=payload)
            return payload
        preflight = self.desktop_preflight(require_input_backend=True)
        if not self._should_execute() or not preflight.get("can_execute"):
            payload.update({"status": "blocked" if self._should_execute() else "planned", "reason": "desktop input execution is not available", "preflight": preflight})
            self.audit.record("desktop.hotkey", status="blocked", target=normalized, details=payload)
            return payload
        xdotool = shutil.which("xdotool")
        if not xdotool:
            xtest_display = _preflight_xtest_display(preflight)
            if xtest_display:
                result = xtest_hotkey(xtest_display, normalized)
                payload.update(result)
                payload["tool"] = "xtest"
                payload["display"] = xtest_display
                self.audit.record("desktop.hotkey", status="ok" if payload["status"] == "completed" else "blocked", target=normalized, details=payload)
                return payload
            payload.update({"status": "unavailable", "install_hint": "Install xdotool or make XTest available for keyboard control.", "preflight": preflight})
            self.audit.record("desktop.hotkey", status="blocked", target=normalized, details=payload)
            return payload
        completed = subprocess.run([xdotool, "key", normalized], check=False, capture_output=True, text=True, timeout=5)
        payload.update(_command_result(completed))
        self.audit.record("desktop.hotkey", status="ok" if payload["status"] == "completed" else "blocked", target=normalized, details=payload)
        return payload
    def capture_screenshot(self, output_path: Path | None = None) -> dict[str, object]:
        output = (output_path or self._new_desktop_artifact("desktop_screenshot.png")).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {"path": str(output), "backend": self.backend}
        preflight = self.desktop_preflight(require_input_backend=False)
        if not self._should_execute() or not preflight.get("can_execute"):
            payload.update({"status": "blocked" if self._should_execute() else "planned", "reason": "desktop screenshot execution is not available", "preflight": preflight})
            self.audit.record("desktop.screenshot", status="blocked", target=str(output), details=payload)
            return payload
        command, command_output = self._screenshot_command(output)
        if not command:
            payload.update({"status": "unavailable", "install_hint": "Install gnome-screenshot, spectacle, grim, ImageMagick import, or xwd.", "preflight": preflight})
            self.audit.record("desktop.screenshot", status="blocked", target=str(output), details=payload)
            return payload
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=15)
        payload.update(_command_result(completed))
        payload["command"] = command
        payload["path"] = str(command_output)
        if command_output.exists():
            payload["size_bytes"] = command_output.stat().st_size
        if payload["status"] == "completed" and (not command_output.exists() or command_output.stat().st_size == 0):
            payload["status"] = "blocked"
            payload["stderr"] = "screenshot output missing or empty"
        self.audit.record("desktop.screenshot", status="ok" if payload["status"] == "completed" else "blocked", target=str(command_output), details=payload)
        return payload
    def low_level_probe(self) -> dict[str, object]:
        preflight = self.desktop_preflight(require_input_backend=True)
        input_probe = self.run_input_probe()
        screenshot = self.capture_screenshot() if input_probe.get("status") == "completed" else {
            "status": "adapter_ready",
            "message": "Screenshot probe skipped until input probe succeeds.",
        }
        status = "completed" if input_probe.get("status") == "completed" else "adapter_ready"
        payload = {
            "status": status,
            "preflight": preflight,
            "input_probe": input_probe,
            "screenshot_probe": screenshot,
            "screenshot_required": False,
        }
        self.audit.record("desktop.low_level_probe", status="ok" if status == "completed" else "blocked", target="desktop", details=payload)
        return payload

from .desktop_support import (
    _command_result, _desktop_session, _extract_app_name, _extract_button,
    _extract_file_query, _extract_hotkey, _extract_text_to_type, _extract_url,
    _extract_web_query, _extract_xy, _normalize_query, _preflight_xtest_display,
    _with_xtest_display, _xtest_mouse_move, discover_x11_display, normalize_hotkey,
    normalize_url_or_search, xtest_available, xtest_hotkey, xtest_key_tap,
    xtest_mouse_click, xtest_mouse_move,
)
