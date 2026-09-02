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



class DesktopWorkflowMixin:
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

from .desktop_support import (
    _command_result, _desktop_session, _extract_app_name, _extract_button,
    _extract_file_query, _extract_hotkey, _extract_text_to_type, _extract_url,
    _extract_web_query, _extract_xy, _normalize_query, _preflight_xtest_display,
    _with_xtest_display, _xtest_mouse_move, discover_x11_display, normalize_hotkey,
    normalize_url_or_search, xtest_available, xtest_hotkey, xtest_key_tap,
    xtest_mouse_click, xtest_mouse_move,
)
