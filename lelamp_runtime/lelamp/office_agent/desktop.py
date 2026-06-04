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


class DesktopService:
    EXECUTION_BACKENDS = {"local", "xdg", "linux"}
    APP_ALIASES: dict[str, tuple[str, ...]] = {
        "浏览器": ("xdg-open",),
        "网页": ("xdg-open",),
        "chrome": ("google-chrome", "chromium", "chromium-browser"),
        "谷歌浏览器": ("google-chrome", "chromium", "chromium-browser"),
        "firefox": ("firefox",),
        "火狐": ("firefox",),
        "终端": ("gnome-terminal", "konsole", "xfce4-terminal", "xterm"),
        "命令行": ("gnome-terminal", "konsole", "xfce4-terminal", "xterm"),
        "文件管理器": ("nautilus", "dolphin", "thunar", "pcmanfm"),
        "文件夹": ("nautilus", "dolphin", "thunar", "pcmanfm"),
        "编辑器": ("code", "gedit", "kate", "mousepad"),
        "vscode": ("code",),
        "记事本": ("gedit", "kate", "mousepad"),
        "计算器": ("gnome-calculator", "kcalc", "galculator"),
        "播放器": ("vlc", "mpv"),
        "音乐": ("spotify", "vlc"),
        "wps": ("wps", "libreoffice"),
        "文档": ("libreoffice", "wps"),
        "表格": ("libreoffice", "wps"),
    }

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

    def build_workflow(self, goal: str, steps: list[str]) -> dict[str, object]:
        payload = {
            "goal": goal,
            "steps": [
                {
                    "index": index + 1,
                    "description": step,
                    "permission": "sandbox" if index == 0 else self.permission_mode.value,
                }
                for index, step in enumerate(steps)
            ],
            "backend": self.backend,
        }
        self.audit.record("desktop.workflow_plan", target=goal, details=payload)
        return payload

    def build_supervised_setup(self, goal: str, steps: list[str]) -> dict[str, object]:
        workflow = self.build_workflow(goal, steps)
        preflight = self.desktop_preflight(require_input_backend=True)
        can_execute = bool(preflight.get("can_execute"))
        checks = [
            *list(preflight.get("checks", [])),
            {
                "name": "explicit_authorization",
                "status": "required",
                "expected": "per_workflow_authorized=true",
                "actual": "not_evaluated_in_setup",
            },
            {
                "name": "allowed_roots",
                "status": "passed" if self._search_roots() else "blocked",
                "expected": "workspace/shared_inbox/allowed_roots",
                "actual": [str(path) for path in self._search_roots()],
            },
        ]
        payload = {
            "status": "ready" if can_execute else "needs_target_setup",
            "goal": goal,
            "workflow": workflow,
            "can_execute_on_this_runtime": can_execute,
            "permission_mode": self.permission_mode.value,
            "desktop_backend": self.backend,
            "preflight": preflight,
            "checks": checks,
            "target_setup": {
                "environment": {
                    "OPENCLAW_PERMISSION_MODE": "full_control",
                    "OPENCLAW_DESKTOP_BACKEND": "local",
                },
                "restart_required": self.permission_mode != PermissionMode.FULL_CONTROL or self.backend == "audit_only",
                "command": "OPENCLAW_PERMISSION_MODE=full_control OPENCLAW_DESKTOP_BACKEND=local uv run python openclaw_cli.py web-console --host 127.0.0.1 --port 8790",
            },
            "safety": [
                "No full-control desktop action runs during setup generation.",
                "Execution still requires explicit workflow authorization.",
                "File actions remain restricted to configured allowed roots.",
                "Audit records are written for setup, plan, and execution attempts.",
            ],
        }
        self.audit.record("desktop.supervised_setup", target=goal, details={"status": payload["status"], "can_execute": can_execute})
        return payload

    def execute_workflow(
        self,
        goal: str,
        steps: list[str],
        *,
        authorized: bool = False,
        actor: str = "user",
        max_steps: int = 10,
    ) -> dict[str, object]:
        workflow = self.build_workflow(goal, steps[:max_steps])
        if not authorized:
            payload = {
                "status": "needs_confirmation",
                "message": "Explicit authorization is required before desktop workflow execution.",
                "authorized": False,
                "workflow": workflow,
                "steps": [],
            }
            self.audit.record("desktop.workflow_execute", status="blocked", target=goal, details=payload)
            return payload
        permission = self.request_operation(goal, require_confirmation=True)
        if not permission.get("allowed"):
            payload = {
                "status": "blocked",
                "message": "Desktop workflow execution requires full_control permission mode.",
                "authorized": True,
                "permission": permission,
                "workflow": workflow,
                "steps": [],
            }
            self.audit.record("desktop.workflow_execute", status="blocked", target=goal, details=payload)
            return payload
        preflight = self.desktop_preflight(require_input_backend=False)
        if not preflight.get("can_execute"):
            payload = {
                "status": "blocked",
                "message": "Desktop workflow execution requires full_control, a local backend, a GUI session, and xdg-open.",
                "authorized": True,
                "permission": permission,
                "preflight": preflight,
                "workflow": workflow,
                "steps": [],
            }
            self.audit.record("desktop.workflow_execute", status="blocked", target=goal, details=payload)
            return payload
        results: list[dict[str, object]] = []
        for step in steps[:max_steps]:
            result = self.handle_command(step)
            results.append({"description": step, "result": result})
        result_statuses = {str(item.get("result", {}).get("status") if isinstance(item.get("result"), dict) else "") for item in results}
        success_statuses = {"opened", "launched", "sent", "completed"}
        status = "completed" if results and result_statuses <= success_statuses else "planned"
        if result_statuses & {"blocked", "unavailable", "not_found"}:
            status = "partial"
        payload = {
            "status": status,
            "authorized": True,
            "actor": actor,
            "backend": self.backend,
            "permission_mode": self.permission_mode.value,
            "preflight": preflight,
            "workflow": workflow,
            "steps": results,
            "step_count": len(results),
        }
        self.audit.record("desktop.workflow_execute", target=goal, details=payload)
        return payload

    def handle_command(self, text: str) -> dict[str, object]:
        normalized = text.lower()
        if self._has(normalized, "暂停", "继续播放", "上一首", "下一首", "播放", "停止播放", "pause", "next"):
            return self.media_control(text)
        if "音量" in normalized or "volume" in normalized:
            return self.set_volume(text)
        if self._has(normalized, "找文件", "查找文件", "搜索文件", "find file"):
            query = _extract_file_query(text) or text
            return self.find_files(query)
        if self._has(normalized, "打开文件", "打开这个文件", "open file"):
            query = _extract_file_query(text) or text
            return self.open_file(query)
        if self._has(normalized, "截图", "屏幕截图", "screenshot"):
            return self.capture_screenshot()
        if self._has(normalized, "移动鼠标", "mouse move"):
            point = _extract_xy(text)
            return self.mouse_move(point[0], point[1])
        if self._has(normalized, "点击", "click"):
            return self.mouse_click(_extract_button(text))
        if self._has(normalized, "热键", "快捷键", "hotkey"):
            return self.send_hotkey(_extract_hotkey(text) or text)
        if self._has(normalized, "输入文字", "键入", "type text"):
            return self.type_text(_extract_text_to_type(text) or text)
        url = _extract_url(text)
        if url or self._has(normalized, "打开网页", "打开网站", "访问", "网址", "open website"):
            return self.open_url(url or _extract_web_query(text) or text)
        return self.open_app(_extract_app_name(text) or text)

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


def normalize_url_or_search(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return "about:blank"
    if re.match(r"^https?://", cleaned, re.I):
        return cleaned
    if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", cleaned, re.I):
        return f"https://{cleaned}"
    query = urllib.parse.urlencode({"q": cleaned})
    return f"https://www.google.com/search?{query}"


def discover_x11_display() -> str:
    candidates = []
    if os.getenv("DISPLAY"):
        candidates.append(str(os.getenv("DISPLAY")))
    x11_dir = Path("/tmp/.X11-unix")
    if x11_dir.is_dir():
        for path in sorted(x11_dir.glob("X*")):
            suffix = path.name.removeprefix("X")
            if suffix.isdigit():
                candidates.append(f":{suffix}")
    seen: set[str] = set()
    for display in candidates:
        if display in seen:
            continue
        seen.add(display)
        if xtest_available(display):
            return display
    return ""


def xtest_available(display: str) -> bool:
    try:
        x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
        ctypes.cdll.LoadLibrary("libXtst.so.6")
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        handle = x11.XOpenDisplay(display.encode())
        if not handle:
            return False
        x11.XCloseDisplay(handle)
        return True
    except Exception:
        return False


def xtest_mouse_move(display: str, x: int, y: int, *, relative: bool = False) -> dict[str, object]:
    return _with_xtest_display(display, lambda x11, xtst, handle: _xtest_mouse_move(x11, xtst, handle, x, y, relative=relative))


def xtest_mouse_click(display: str, button: int) -> dict[str, object]:
    def run(x11: ctypes.CDLL, xtst: ctypes.CDLL, handle: int) -> dict[str, object]:
        xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeButtonEvent.restype = ctypes.c_int
        down = xtst.XTestFakeButtonEvent(handle, int(button), 1, 0)
        up = xtst.XTestFakeButtonEvent(handle, int(button), 0, 0)
        x11.XFlush(handle)
        return {"status": "completed" if down and up else "blocked", "button_down": int(down), "button_up": int(up)}

    return _with_xtest_display(display, run)


def xtest_hotkey(display: str, hotkey: str) -> dict[str, object]:
    keys = [part for part in hotkey.split("+") if part]
    if not keys:
        return {"status": "blocked", "message": "empty hotkey", "display": display}

    def run(x11: ctypes.CDLL, xtst: ctypes.CDLL, handle: int) -> dict[str, object]:
        keycodes = [_keysym_to_keycode(x11, handle, key) for key in keys]
        if not all(keycodes):
            return {"status": "blocked", "keycodes": keycodes, "message": "one or more keys could not be resolved"}
        xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeKeyEvent.restype = ctypes.c_int
        downs = [int(xtst.XTestFakeKeyEvent(handle, code, 1, 0)) for code in keycodes]
        ups = [int(xtst.XTestFakeKeyEvent(handle, code, 0, 0)) for code in reversed(keycodes)]
        x11.XFlush(handle)
        completed = all(downs) and all(ups)
        return {"status": "completed" if completed else "blocked", "keys": keys, "keycodes": keycodes, "downs": downs, "ups": ups}

    return _with_xtest_display(display, run)


def xtest_key_tap(display: str, key: str) -> dict[str, object]:
    return xtest_hotkey(display, key)


def _xtest_mouse_move(x11: ctypes.CDLL, xtst: ctypes.CDLL, handle: int, x: int, y: int, *, relative: bool) -> dict[str, object]:
    if relative:
        xtst.XTestFakeRelativeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeRelativeMotionEvent.restype = ctypes.c_int
        moved = xtst.XTestFakeRelativeMotionEvent(handle, int(x), int(y), 0)
    else:
        xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeMotionEvent.restype = ctypes.c_int
        moved = xtst.XTestFakeMotionEvent(handle, -1, int(x), int(y), 0)
    x11.XFlush(handle)
    return {"status": "completed" if moved else "blocked", "moved": int(moved), "relative": relative}


def _with_xtest_display(display: str, callback: object) -> dict[str, object]:
    try:
        x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
        xtst = ctypes.cdll.LoadLibrary("libXtst.so.6")
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        handle = x11.XOpenDisplay(display.encode())
        if not handle:
            return {"status": "blocked", "message": f"Cannot open display {display}."}
        try:
            result = callback(x11, xtst, handle)  # type: ignore[misc]
        finally:
            x11.XCloseDisplay(handle)
        return {"display": display, **result}
    except Exception as exc:
        return {"status": "blocked", "message": str(exc)[:500], "display": display}


def _keysym_to_keycode(x11: ctypes.CDLL, handle: int, key: str) -> int:
    aliases = {
        "ctrl": "Control_L",
        "control": "Control_L",
        "alt": "Alt_L",
        "shift": "Shift_L",
        "super": "Super_L",
        "return": "Return",
        "enter": "Return",
        "escape": "Escape",
        "esc": "Escape",
        "tab": "Tab",
        "space": "space",
    }
    name = aliases.get(key.lower(), key)
    x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
    x11.XStringToKeysym.restype = ctypes.c_ulong
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_ubyte
    keysym = x11.XStringToKeysym(name.encode())
    if not keysym and len(name) == 1:
        keysym = x11.XStringToKeysym(name.lower().encode())
    if not keysym:
        return 0
    return int(x11.XKeysymToKeycode(handle, keysym))


def _preflight_xtest_display(preflight: dict[str, object]) -> str:
    backends = preflight.get("input_backends") if isinstance(preflight.get("input_backends"), list) else []
    for backend in backends:
        if isinstance(backend, dict) and backend.get("name") == "xtest":
            return str(backend.get("display") or "")
    return ""


def normalize_hotkey(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"^(?:发送|按下|执行)?\s*(?:热键|快捷键|hotkey)[:：]?\s*", "", cleaned, flags=re.I)
    if not cleaned:
        return ""
    parts = [part.strip() for part in re.split(r"[+\s]+", cleaned) if part.strip()]
    aliases = {
        "ctrl": "ctrl",
        "control": "ctrl",
        "cmd": "super",
        "command": "super",
        "win": "super",
        "windows": "super",
        "alt": "alt",
        "shift": "shift",
        "enter": "Return",
        "return": "Return",
        "esc": "Escape",
        "escape": "Escape",
        "space": "space",
        "tab": "Tab",
    }
    normalized = [aliases.get(part.lower(), part) for part in parts]
    if not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in normalized):
        return ""
    return "+".join(normalized)


def _command_result(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return {
        "status": "completed" if completed.returncode == 0 else "blocked",
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip()[:500],
        "stderr": completed.stderr.strip()[:500],
    }


def _desktop_session() -> dict[str, object]:
    display = os.getenv("DISPLAY", "")
    wayland_display = os.getenv("WAYLAND_DISPLAY", "")
    session_type = os.getenv("XDG_SESSION_TYPE", "")
    desktop = os.getenv("XDG_CURRENT_DESKTOP", "")
    session_desktop = os.getenv("XDG_SESSION_DESKTOP", "")
    return {
        "has_gui_session": bool(display or wayland_display),
        "display": display,
        "wayland_display": wayland_display,
        "session_type": session_type,
        "desktop": desktop,
        "session_desktop": session_desktop,
    }


def _extract_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s，。]+", text, re.I)
    if match:
        return match.group(0)
    match = re.search(r"([\w.-]+\.[a-z]{2,}(?:/[^\s，。]*)?)", text, re.I)
    if match:
        return match.group(1)
    return None


def _extract_xy(text: str) -> tuple[int, int]:
    numbers = [int(item) for item in re.findall(r"-?\d+", text)[:2]]
    if len(numbers) >= 2:
        return max(0, numbers[0]), max(0, numbers[1])
    return 0, 0


def _extract_button(text: str) -> int:
    if any(marker in text for marker in ("右键", "right")):
        return 3
    if any(marker in text for marker in ("中键", "middle")):
        return 2
    match = re.search(r"\b([1-5])\b", text)
    return int(match.group(1)) if match else 1


def _extract_hotkey(text: str) -> str | None:
    match = re.search(r"(?:热键|快捷键|hotkey)[:：]?\s*([A-Za-z0-9_+.\-\s]{1,80})", text, re.I)
    return match.group(1).strip() if match else None


def _extract_text_to_type(text: str) -> str | None:
    match = re.search(r"(?:输入文字|键入|type text)[:：]?\s*(.{1,500})", text, re.I)
    return match.group(1).strip() if match else None


def _extract_app_name(text: str) -> str | None:
    match = re.search(r"(?:打开|启动|运行)\s*([\w\u4e00-\u9fff.+-]{1,30})", text)
    if match:
        return match.group(1).strip()
    return None


def _extract_file_query(text: str) -> str | None:
    cleaned = re.sub(r"(帮我|请|找文件|查找文件|搜索文件|打开文件|打开这个文件|文件)", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。")
    return cleaned or None


def _extract_web_query(text: str) -> str | None:
    cleaned = re.sub(r"(帮我|请|打开网页|打开网站|访问|网址|网页|网站)", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。")
    return cleaned or None


def _normalize_query(query: str) -> str:
    cleaned = _extract_file_query(query) or query
    cleaned = re.sub(r"\*+", "", cleaned).lower().strip()
    return cleaned
