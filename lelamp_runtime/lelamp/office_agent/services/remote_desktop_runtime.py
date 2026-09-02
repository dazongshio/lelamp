from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..desktop_companion import DesktopCompanionService
from ..routes._base import ApiError


def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)


def default_ssh_key_path(*args, **kwargs): return _helper("default_ssh_key_path")(*args, **kwargs)
def is_private_ssh_host(*args, **kwargs): return _helper("is_private_ssh_host")(*args, **kwargs)
def now_iso(*args, **kwargs): return _helper("now_iso")(*args, **kwargs)
def require_string(*args, **kwargs): return _helper("require_string")(*args, **kwargs)
def safe_int(*args, **kwargs): return _helper("safe_int")(*args, **kwargs)


class RemoteDesktopRuntimeMixin:
    def remote_ssh_target_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        host = require_string(payload, "host")
        if not is_private_ssh_host(host):
            raise ApiError("invalid_remote_host", "Only private LAN, loopback, and link-local SSH hosts are allowed.", status=400, details={"host": host})
        user = require_string(payload, "user")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", user):
            raise ApiError("invalid_remote_user", "SSH user contains unsupported characters.", status=400)
        port = safe_int(payload.get("port"), 22)
        if port < 1 or port > 65535:
            raise ApiError("invalid_remote_port", "SSH port must be between 1 and 65535.", status=400)
        timeout_seconds = max(2, min(60, safe_int(payload.get("timeout_seconds"), 12)))
        key_value = str(payload.get("key_path") or "").strip()
        key_path = Path(key_value).expanduser().resolve() if key_value else default_ssh_key_path()
        if key_path is None or not key_path.is_file():
            raise ApiError("missing_ssh_key", "SSH key file not found. Create or select an SSH key first.", status=400, details={"key_path": str(key_path or key_value)})
        home = Path.home().resolve()
        allowed_key_roots = (home / ".ssh", self.runtime.config.workspace_dir.resolve())
        if not any(key_path.is_relative_to(root.resolve()) for root in allowed_key_roots):
            raise ApiError("invalid_ssh_key_path", "SSH key path must be under ~/.ssh or workspace.", status=403)
        return {"host": host, "user": user, "port": port, "key_path": key_path, "timeout_seconds": timeout_seconds}

    def safe_remote_ssh_target(self, target: dict[str, Any]) -> dict[str, object]:
        key_path = Path(str(target.get("key_path") or ""))
        return {
            "host": str(target.get("host") or ""),
            "user": str(target.get("user") or ""),
            "port": int(target.get("port") or 22),
            "key_path": str(key_path),
            "key_name": key_path.name,
        }

    def run_remote_ssh_command(self, target: dict[str, Any], remote_argv: list[str], *, timeout_seconds: int) -> dict[str, object]:
        ssh_binary = shutil.which("ssh")
        if not ssh_binary:
            raise ApiError("ssh_backend_missing", "OpenSSH client is not installed.", status=500)
        destination = f"{target['user']}@{target['host']}"
        remote_command = (
            'export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.npm-global/bin:$PATH"; exec '
            + " ".join(shlex.quote(part) for part in remote_argv)
        )
        command = [
            ssh_binary,
            "-i",
            str(target["key_path"]),
            "-p",
            str(target["port"]),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "--",
            destination,
            remote_command,
        ]
        started = time.time()
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds, check=False)
            exit_code = int(completed.returncode)
            stdout = completed.stdout[-12000:]
            stderr = completed.stderr[-12000:]
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = str(exc.stdout or "")[-12000:]
            stderr = (str(exc.stderr or "") + f"\nCommand timed out after {timeout_seconds}s.").strip()[-12000:]
        return {
            "backend": "openssh",
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_seconds": round(time.time() - started, 3),
        }

    def run_remote_ssh_script(self, target: dict[str, Any], script: str, *, timeout_seconds: int) -> dict[str, object]:
        ssh_binary = shutil.which("ssh")
        if not ssh_binary:
            raise ApiError("ssh_backend_missing", "OpenSSH client is not installed.", status=500)
        destination = f"{target['user']}@{target['host']}"
        command = [
            ssh_binary,
            "-i",
            str(target["key_path"]),
            "-p",
            str(target["port"]),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "--",
            destination,
            "sh",
            "-s",
        ]
        started = time.time()
        try:
            completed = subprocess.run(command, input=script, capture_output=True, text=True, timeout=timeout_seconds, check=False)
            exit_code = int(completed.returncode)
            stdout = completed.stdout[-20000:]
            stderr = completed.stderr[-20000:]
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = str(exc.stdout or "")[-20000:]
            stderr = (str(exc.stderr or "") + f"\nCommand timed out after {timeout_seconds}s.").strip()[-20000:]
        return {
            "backend": "openssh",
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_seconds": round(time.time() - started, 3),
        }

    def _desktop_companion_loop(self, interval_seconds: int) -> None:
        while not self._desktop_companion_stop.is_set():
            try:
                self.run_desktop_companion_once(limit=5, actor="desktop_companion_daemon")
            except Exception as exc:
                self._desktop_companion_last_run = {"status": "error", "error": str(exc)[:1000], "timestamp": now_iso()}
                self.audit.record("desktop_companion.daemon", status="error", target="desktop_companion", details=self._desktop_companion_last_run)
            self._desktop_companion_stop.wait(interval_seconds)

    def run_desktop_companion_once(self, *, limit: int, actor: str) -> dict[str, object]:
        companion = DesktopCompanionService(
            workspace=self.runtime.workspace,
            audit=self.runtime.audit,
            backend=self.runtime.config.desktop_backend,
            permission_mode=self.runtime.config.permission_mode,
        )
        approved_listing = companion.list_approved_tasks(limit=limit, scan_limit=max(50, limit * 10))
        approved = approved_listing["tasks"]
        executed: list[dict[str, object]] = []
        for task in approved[:limit]:
            task_id = str(task.get("id") or "")
            if not task_id:
                continue
            result = companion.execute_task(task_id, actor=actor)
            if str(result.get("status")) in {"planned", "attempted"}:
                updated_task = self.runtime.desktop_tasks.update_status(
                    task_id,
                    "done",
                    actor=actor,
                    reason=f"desktop companion {result.get('status')}",
                )
                result = {**result, "final_status": "done", "task": updated_task}
            executed.append(result)
        payload = {
            "status": "completed",
            "processed": len(executed),
            "approved_count": len(approved),
            "scanned_count": approved_listing.get("scanned_count"),
            "backend": self.runtime.config.desktop_backend,
            "executed": executed,
            "timestamp": now_iso(),
        }
        self._desktop_companion_last_run = {
            "status": payload["status"],
            "processed": payload["processed"],
            "approved_count": payload["approved_count"],
            "backend": payload["backend"],
            "timestamp": payload["timestamp"],
        }
        self.audit.record("desktop_companion.run", target="desktop_companion", details=self._desktop_companion_last_run)
        return payload

