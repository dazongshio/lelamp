from __future__ import annotations

import shutil
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from ..remote_control import execute_saved_remote_voice_command, load_saved_remote_target, remote_open_codex_script, save_remote_target
from ..shared_space import find_lan_ip
from ._base import ApiError, NOT_HANDLED, RequestContext, exact_payload

def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)
def default_ssh_key_path(*a,**kw): return _helper("default_ssh_key_path")(*a,**kw)
def list_string(*a,**kw): return _helper("list_string")(*a,**kw)
def normalize_task_status(*a,**kw): return _helper("normalize_task_status")(*a,**kw)
def parse_safe_ssh_command(*a,**kw): return _helper("parse_safe_ssh_command")(*a,**kw)
def remote_codex_bootstrap_script(*a,**kw): return _helper("remote_codex_bootstrap_script")(*a,**kw)
def require_string(*a,**kw): return _helper("require_string")(*a,**kw)
def safe_int(*a,**kw): return _helper("safe_int")(*a,**kw)
def status_to_audit(*a,**kw): return _helper("status_to_audit")(*a,**kw)

class DesktopRoutesMixin:
    def api_desktop_automation_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        status = self.runtime.browser_automation.status(check_launch=True)
        if ctx:
            self.record_audit(
                "browser_automation.status",
                status_to_audit(str(status.get("status"))),
                "browser_automation",
                {
                    "package_installed": status.get("package_installed"),
                    "headless_default": status.get("headless_default"),
                },
                ctx,
            )
        return status

    def api_desktop_companion_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        with self._desktop_companion_lock:
            running = self._desktop_companion_thread is not None and self._desktop_companion_thread.is_alive()
            payload = {
                "status": "running" if running else "stopped",
                "backend": self.runtime.config.desktop_backend,
                "permission_mode": self.runtime.config.permission_mode.value,
                "queue_dir": str(self.runtime.desktop_tasks.queue_dir),
                "started_at": self._desktop_companion_started_at,
                "last_run": self._desktop_companion_last_run,
                "safety": [
                    "Only approved desktop tasks are processed.",
                    "Default audit_only backend plans without changing the desktop.",
                    "The service can be stopped from the web console.",
                ],
            }
        if ctx:
            self.record_audit("desktop_companion.status", "ok", "desktop_companion", {"running": running}, ctx)
        return payload

    def api_desktop_task_execute_browser(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        task_id = require_string(payload, "task_id")
        authorized = bool(payload.get("authorized"))
        actor = str(payload.get("actor") or ctx.actor or "web_console")
        headless = payload.get("headless")
        allowed_hosts = list_string(payload.get("allowed_hosts"))
        task = self.runtime.desktop_tasks.get_task(task_id)
        result = self.runtime.browser_automation.execute_task(
            task,
            actor=actor,
            authorized=authorized,
            headless=bool(headless) if headless is not None else None,
            allowed_hosts=allowed_hosts,
        )
        self.runtime.desktop_tasks.record_execution(
            task_id,
            backend="playwright_browser",
            execution_status=str(result.get("status") or "unknown"),
            actor=actor,
            note=str(result.get("message") or ""),
            step_count=safe_int(result.get("step_count"), 0),
        )
        self.record_audit(
            "desktop_task.execute_browser",
            status_to_audit(str(result.get("status"))),
            task_id,
            {
                "authorized": authorized,
                "status": result.get("status"),
                "report_workspace_name": result.get("report_workspace_name"),
            },
            ctx,
        )
        return result

    def api_desktop_workflow_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        preflight = self.runtime.desktop.desktop_preflight(require_input_backend=True)
        payload = {
            "status": "available",
            "permission_mode": self.runtime.config.permission_mode.value,
            "desktop_backend": self.runtime.config.desktop_backend,
            "can_execute": bool(preflight.get("can_execute")),
            "preflight": preflight,
            "allowed_roots": [str(path) for path in self.runtime.config.allowed_roots],
            "supported_actions": [
                "open_url",
                "open_app",
                "open_file",
                "find_files",
                "media_control",
                "set_volume",
                "mouse_move",
                "mouse_click",
                "type_text",
                "hotkey",
                "screenshot",
                "low_level_probe",
            ],
            "setup_endpoint": "/api/desktop/workflow/setup",
            "safety": [
                "Plan endpoint never controls the desktop.",
                "Setup endpoint generates target-machine full_control validation steps without controlling the desktop.",
                "Execute endpoint requires explicit authorization.",
                "Execution is blocked unless OPENCLAW_PERMISSION_MODE=full_control.",
                "Low-level mouse, keyboard, and screenshot actions require full_control, a GUI session, and xdotool or XTest where applicable.",
                "File actions remain limited to workspace/shared_inbox/allowed_roots.",
            ],
        }
        if ctx:
            self.record_audit("desktop_workflow.status", "ok", "desktop_workflow", {"can_execute": payload["can_execute"]}, ctx)
        return payload

    def api_desktop_workflow_plan(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        goal = require_string(payload, "goal")
        steps = list_string(payload.get("steps")) or [goal]
        workflow = self.runtime.desktop.build_workflow(goal, steps)
        task = self.create_task("桌面工作流计划", "assistant", "completed", {"goal": goal, "steps": steps}, workflow)
        result = {"status": "completed", "task_id": task["task_id"], **workflow, "safety": self.api_desktop_workflow_status(ctx=None)["safety"]}
        self.record_audit("desktop_workflow.plan", "ok", goal, {"task_id": task["task_id"], "steps": len(steps)}, ctx)
        return result

    def api_desktop_workflow_setup(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        goal = require_string(payload, "goal")
        steps = list_string(payload.get("steps")) or [goal]
        setup = self.runtime.desktop.build_supervised_setup(goal, steps)
        task = self.create_task("桌面全权工作流验收包", "assistant", "completed", {"goal": goal, "steps": steps}, setup)
        result = {"status": "completed", "task_id": task["task_id"], **setup}
        self.record_audit("desktop_workflow.setup", "ok", goal, {"task_id": task["task_id"], "setup_status": setup.get("status")}, ctx)
        return result

    def api_desktop_workflow_execute(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        goal = require_string(payload, "goal")
        steps = list_string(payload.get("steps")) or [goal]
        authorized = bool(payload.get("authorized"))
        actor = str(payload.get("actor") or ctx.actor or "web_console")
        result = self.runtime.desktop.execute_workflow(goal, steps, authorized=authorized, actor=actor)
        status = str(result.get("status") or "unknown")
        task = self.create_task(
            "桌面工作流执行",
            "assistant",
            normalize_task_status(status),
            {"goal": goal, "steps": steps, "authorized": authorized},
            result,
        )
        self.record_audit(
            "desktop_workflow.execute",
            status_to_audit(status),
            goal,
            {"task_id": task["task_id"], "status": status, "authorized": authorized, "steps": len(steps)},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_desktop_control_action(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        action = require_string(payload, "action")
        authorized = bool(payload.get("authorized"))
        if not authorized:
            result = {"status": "needs_confirmation", "message": "Explicit authorization is required before low-level desktop control.", "action": action}
        elif action == "mouse_move":
            result = self.runtime.desktop.mouse_move(safe_int(payload.get("x"), 0), safe_int(payload.get("y"), 0))
        elif action == "mouse_click":
            result = self.runtime.desktop.mouse_click(safe_int(payload.get("button"), 1))
        elif action == "type_text":
            result = self.runtime.desktop.type_text(str(payload.get("text") or ""))
        elif action == "hotkey":
            result = self.runtime.desktop.send_hotkey(str(payload.get("hotkey") or ""))
        elif action == "screenshot":
            result = self.runtime.desktop.capture_screenshot()
        elif action == "low_level_probe":
            result = self.runtime.desktop.low_level_probe()
        else:
            raise ApiError("unsupported_desktop_action", f"Unsupported desktop control action: {action}", status=400)
        status = str(result.get("status") or "unknown")
        task = self.create_task(
            "低层桌面控制动作",
            "desktop",
            normalize_task_status(status),
            {"action": action, "authorized": authorized},
            result,
        )
        self.record_audit(
            "desktop_control.action",
            status_to_audit(status),
            action,
            {"task_id": task["task_id"], "authorized": authorized, "status": status},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_remote_ssh_status(self, ctx: RequestContext) -> dict[str, object]:
        saved_target = load_saved_remote_target(self.runtime.config.workspace_dir)
        lan_ip = find_lan_ip()
        console_url = f"http://{lan_ip}:8790/?token={urllib.parse.quote(self.token)}" if lan_ip else ""
        payload = {
            "status": "available" if shutil.which("ssh") else "backend_missing",
            "backend": "openssh",
            "ssh_binary": shutil.which("ssh") or "",
            "default_port": 22,
            "default_key_path": str(default_ssh_key_path() or ""),
            "known_hosts_path": str(Path.home() / ".ssh" / "known_hosts"),
            "console_lan_ip": lan_ip or "",
            "console_lan_url": console_url,
            "saved_target": saved_target,
            "safety": [
                "Web console should be opened with the LAN URL, not 127.0.0.1, from other devices.",
                "Only private LAN, link-local, localhost, and Tailscale/CGNAT shared-address SSH targets are accepted.",
                "Password login is not stored by the console; use SSH keys.",
                "Arbitrary run endpoint requires explicit authorization.",
                "Dedicated connect/Codex buttons perform only their named SSH/Codex workflow.",
                "Commands are executed without shell interpolation; shell metacharacters are blocked.",
                "All attempts are written to the audit log.",
            ],
            "examples": [
                {"label": "查看系统", "command": "uname -a"},
                {"label": "列目录", "command": "ls -la"},
                {"label": "查看时间", "command": "date"},
            ],
        }
        self.record_audit("remote_ssh.status", status_to_audit(str(payload["status"])), "remote_ssh", {"ssh_binary": payload["ssh_binary"]}, ctx)
        return payload

    def api_remote_ssh_test(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        target = self.remote_ssh_target_from_payload(payload)
        result = self.run_remote_ssh_command(target, ["true"], timeout_seconds=min(target["timeout_seconds"], 8))
        status = "completed" if result["exit_code"] == 0 else "failed"
        if status == "completed":
            save_remote_target(self.runtime.config.workspace_dir, target, source="remote_ssh_test")
        response = {
            **result,
            "status": status,
            "message": "SSH 连接可用。" if status == "completed" else "SSH 连接失败，请检查目标地址、用户名、端口和 key。",
            "target": self.safe_remote_ssh_target(target),
        }
        self.record_audit("remote_ssh.test", status_to_audit(status), str(response["target"].get("host")), response, ctx)
        return response

    def api_remote_ssh_run(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        authorized = bool(payload.get("authorized"))
        target = self.remote_ssh_target_from_payload(payload)
        command = require_string(payload, "command")
        if not authorized:
            response = {
                "status": "needs_confirmation",
                "message": "执行远程 SSH 命令需要显式授权。",
                "target": self.safe_remote_ssh_target(target),
                "command": command,
            }
            self.record_audit("remote_ssh.run", "blocked", str(response["target"].get("host")), response, ctx)
            return response
        argv = parse_safe_ssh_command(command)
        result = self.run_remote_ssh_command(target, argv, timeout_seconds=target["timeout_seconds"])
        status = "completed" if result["exit_code"] == 0 else "failed"
        task = self.create_task(
            "远程 SSH 命令",
            "desktop",
            normalize_task_status(status),
            {"target": self.safe_remote_ssh_target(target), "command": command, "authorized": authorized},
            result,
        )
        response = {
            **result,
            "task_id": task["task_id"],
            "status": status,
            "message": "远程命令执行完成。" if status == "completed" else "远程命令执行失败。",
            "target": self.safe_remote_ssh_target(target),
            "command": command,
        }
        self.record_audit(
            "remote_ssh.run",
            status_to_audit(status),
            str(response["target"].get("host")),
            {"task_id": task["task_id"], "status": status, "command": command, "exit_code": result["exit_code"]},
            ctx,
        )
        return response

    def api_remote_ssh_bootstrap_codex(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        authorized = bool(payload.get("authorized"))
        target = self.remote_ssh_target_from_payload(payload)
        if not authorized:
            response = {
                "status": "needs_confirmation",
                "message": "安装 Codex 需要显式授权。",
                "target": self.safe_remote_ssh_target(target),
            }
            self.record_audit("remote_ssh.bootstrap_codex", "blocked", str(response["target"].get("host")), response, ctx)
            return response
        script = remote_codex_bootstrap_script()
        result = self.run_remote_ssh_script(target, script, timeout_seconds=max(90, min(300, target["timeout_seconds"] * 12)))
        status = "completed" if result["exit_code"] == 0 else "failed"
        installed = "CODEX_STATUS=installed" in str(result.get("stdout") or "")
        task = self.create_task(
            "远程 Codex 引导安装",
            "desktop",
            normalize_task_status(status),
            {"target": self.safe_remote_ssh_target(target), "authorized": authorized},
            {**result, "installed": installed},
        )
        response = {
            **result,
            "task_id": task["task_id"],
            "status": status,
            "installed": installed,
            "message": "远程 Codex 已可用。" if status == "completed" else "远程 Codex 安装失败，请查看输出。",
            "target": self.safe_remote_ssh_target(target),
        }
        if status == "completed":
            save_remote_target(self.runtime.config.workspace_dir, target, source="bootstrap_codex")
        self.record_audit(
            "remote_ssh.bootstrap_codex",
            status_to_audit(status),
            str(response["target"].get("host")),
            {"task_id": task["task_id"], "status": status, "installed": installed, "exit_code": result["exit_code"]},
            ctx,
        )
        return response

    def api_remote_ssh_open_codex(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        authorized = bool(payload.get("authorized"))
        target = self.remote_ssh_target_from_payload(payload)
        if not authorized:
            response = {
                "status": "needs_confirmation",
                "message": "打开 Codex 需要显式授权。",
                "target": self.safe_remote_ssh_target(target),
            }
            self.record_audit("remote_ssh.open_codex", "blocked", str(response["target"].get("host")), response, ctx)
            return response
        result = self.run_remote_ssh_script(target, remote_open_codex_script(), timeout_seconds=max(60, min(240, target["timeout_seconds"] * 8)))
        status = "completed" if result["exit_code"] == 0 else "failed"
        response = {
            **result,
            "status": status,
            "message": "已在远程电脑上准备 Codex。" if status == "completed" else "远程电脑打开 Codex 失败。",
            "target": self.safe_remote_ssh_target(target),
        }
        if status == "completed":
            save_remote_target(self.runtime.config.workspace_dir, target, source="open_codex")
        self.record_audit(
            "remote_ssh.open_codex",
            status_to_audit(status),
            str(response["target"].get("host")),
            {"status": status, "exit_code": result["exit_code"]},
            ctx,
        )
        return response

    def api_remote_voice_command(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        text = str(payload.get("text") or payload.get("message") or "").strip()
        if not text:
            raise ApiError("missing_message", "Missing remote voice command text.", status=400)
        if any(str(payload.get(key) or "").strip() for key in ("host", "user", "key_path")):
            target = self.remote_ssh_target_from_payload(payload)
            save_remote_target(self.runtime.config.workspace_dir, target, source="remote_voice_form")
        result = execute_saved_remote_voice_command(self.runtime, text)
        self.record_audit("remote_voice.web_command", status_to_audit(str(result.get("status") or "ignored")), text, result, ctx)
        return result

    def api_desktop_companion_start(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        interval_seconds = max(1, min(60, safe_int(payload.get("interval_seconds"), 5)))
        with self._desktop_companion_lock:
            if self._desktop_companion_thread is not None and self._desktop_companion_thread.is_alive():
                return {**self.api_desktop_companion_status(ctx=None), "message": "Desktop companion service is already running."}
            self._desktop_companion_stop.clear()
            self._desktop_companion_started_at = time.time()
            thread = threading.Thread(
                target=self._desktop_companion_loop,
                args=(interval_seconds,),
                name="openclaw-desktop-companion",
                daemon=True,
            )
            self._desktop_companion_thread = thread
            thread.start()
        result = {**self.api_desktop_companion_status(ctx=None), "message": "Desktop companion service started.", "interval_seconds": interval_seconds}
        self.record_audit("desktop_companion.start", "ok", "desktop_companion", result, ctx)
        return result

    def api_desktop_companion_stop(self, ctx: RequestContext) -> dict[str, object]:
        with self._desktop_companion_lock:
            thread = self._desktop_companion_thread
            self._desktop_companion_stop.set()
        if thread is not None:
            thread.join(timeout=3.0)
        with self._desktop_companion_lock:
            self._desktop_companion_thread = None
            self._desktop_companion_started_at = None
        result = {**self.api_desktop_companion_status(ctx=None), "message": "Desktop companion service stopped."}
        self.record_audit("desktop_companion.stop", "ok", "desktop_companion", result, ctx)
        return result

    def api_desktop_companion_run_once(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        limit = max(1, min(20, safe_int(payload.get("limit"), 5)))
        result = self.run_desktop_companion_once(limit=limit, actor="web_console_companion")
        self.record_audit("desktop_companion.run_once", status_to_audit(str(result.get("status"))), "desktop_companion", result, ctx)
        return result

GET={
    "/api/desktop/automation/status":"api_desktop_automation_status", "/api/desktop/workflow/status":"api_desktop_workflow_status",
    "/api/desktop/companion/status":"api_desktop_companion_status", "/api/remote/ssh/status":"api_remote_ssh_status",
}
POST={
    "/api/desktop/task/execute-browser":"api_desktop_task_execute_browser", "/api/desktop/workflow/plan":"api_desktop_workflow_plan",
    "/api/desktop/workflow/setup":"api_desktop_workflow_setup", "/api/desktop/workflow/execute":"api_desktop_workflow_execute",
    "/api/desktop/control/action":"api_desktop_control_action", "/api/desktop/companion/start":"api_desktop_companion_start",
    "/api/desktop/companion/run-once":"api_desktop_companion_run_once", "/api/remote/ssh/test":"api_remote_ssh_test",
    "/api/remote/ssh/run":"api_remote_ssh_run", "/api/remote/ssh/bootstrap-codex":"api_remote_ssh_bootstrap_codex",
    "/api/remote/ssh/open-codex":"api_remote_ssh_open_codex", "/api/remote/voice-command":"api_remote_voice_command",
}
def dispatch_get(server:Any,path:str,params:dict[str,list[str]],ctx:Any)->Any:
    if path=="/api/desktop/tasks": return server.runtime.desktop_tasks.list_tasks(limit=safe_int(params.get("limit",["50"])[0],50))
    method=GET.get(path);return NOT_HANDLED if method is None else getattr(server,method)(ctx)
def dispatch_post(server:Any,path:str,payload:dict[str,Any],ctx:Any)->Any:
    if path=="/api/desktop/companion/stop": return server.api_desktop_companion_stop(ctx)
    if path=="/api/desktop/task/request":
        goal=require_string(payload,"goal")
        return server.runtime.desktop_tasks.request_task(goal,list_string(payload.get("steps")) or [goal],source="web_console",requires_full_control=bool(payload.get("requires_full_control",True)))
    if path=="/api/desktop/task/status":
        return server.runtime.desktop_tasks.update_status(require_string(payload,"task_id"),require_string(payload,"status"),actor=str(payload.get("actor") or "web_console"),reason=str(payload.get("reason") or ""))
    return exact_payload(server,path,payload,ctx,POST)
