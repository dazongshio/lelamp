from __future__ import annotations

import ast
import json
import os
import re
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from lelamp.office_agent import web_console
from lelamp.office_agent.routes import ROUTE_GROUPS
from lelamp.office_agent.routes._base import NOT_HANDLED, RequestContext


RUNTIME_ROOT = Path(__file__).resolve().parents[2]
OFFICE_AGENT_ROOT = RUNTIME_ROOT / "lelamp" / "office_agent"
CONTRACT_TOKEN = "lelamp-api-contract-test"


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class CallableStub:
    def __init__(self, result: Any | None = None):
        self.result = {"status": "contract_stub"} if result is None else result

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        return self.result

    def __getattr__(self, _name: str) -> "CallableStub":
        return self


class RouteServerStub:
    def __init__(self):
        self.runtime = SimpleNamespace(
            desktop_tasks=CallableStub(),
            skills=SimpleNamespace(list_skills=lambda: []),
            p0=SimpleNamespace(status=lambda: {"status": "ok"}),
            readiness_report=lambda: {"status": "ok"},
        )

    def dispatch_scan_post(self, *_args: Any, **_kwargs: Any) -> object:
        return NOT_HANDLED

    def __getattr__(self, _name: str) -> CallableStub:
        return CallableStub()


class HelperAndRouteContractTest(unittest.TestCase):
    def test_every_dynamic_helper_resolves(self):
        references: dict[str, list[str]] = {}
        for directory in (OFFICE_AGENT_ROOT / "routes", OFFICE_AGENT_ROOT / "services"):
            for path in directory.glob("*.py"):
                for name in re.findall(r"_helper\([\"']([^\"']+)", path.read_text(encoding="utf-8")):
                    references.setdefault(name, []).append(str(path.relative_to(RUNTIME_ROOT)))
        missing = {name: paths for name, paths in references.items() if not hasattr(web_console, name)}
        self.assertEqual(missing, {}, f"Unresolved web_console compatibility helpers: {missing}")

    def test_all_declared_and_custom_routes_dispatch(self):
        server = RouteServerStub()
        ctx = RequestContext(request_id="contract", actor="contract", source_ip="127.0.0.1")
        payload = {
            "action": "contract_probe",
            "goal": "contract probe",
            "host": "127.0.0.1",
            "message": "contract probe",
            "state": "idle",
            "status": "done",
            "task_id": "contract-task",
            "test_id": "readiness",
            "text": "contract probe",
            "user": "contract",
        }
        failures: list[str] = []
        for route_group in ROUTE_GROUPS:
            if hasattr(route_group, "build_product_checklist"):
                route_group.build_product_checklist = lambda _runtime: {"items": [], "summary": {}}
            source_path = Path(route_group.__file__ or "")
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            get_paths = set(getattr(route_group, "GET", {}))
            post_paths = set(getattr(route_group, "POST", {}))
            for node in tree.body:
                if not isinstance(node, ast.FunctionDef) or node.name not in {"dispatch_get", "dispatch_post"}:
                    continue
                literals = {
                    item.value
                    for item in ast.walk(node)
                    if isinstance(item, ast.Constant)
                    and isinstance(item.value, str)
                    and item.value.startswith("/api/")
                }
                (get_paths if node.name == "dispatch_get" else post_paths).update(literals)
            for path in sorted(get_paths):
                dispatch_path = f"{path}contract" if path.endswith("/") else path
                try:
                    result = route_group.dispatch_get(server, dispatch_path, {"limit": ["5"], "meeting_id": ["contract"]}, ctx)
                    if result is NOT_HANDLED:
                        failures.append(f"GET {path} was not handled by {route_group.__name__}")
                except Exception as exc:  # Report every route instead of stopping at the first contract failure.
                    failures.append(f"GET {path} raised {type(exc).__name__}: {exc}")
            for path in sorted(post_paths):
                dispatch_path = f"{path}contract/cancel" if path == "/api/tasks/" else path
                try:
                    result = route_group.dispatch_post(server, dispatch_path, payload, ctx)
                    if result is NOT_HANDLED:
                        failures.append(f"POST {path} was not handled by {route_group.__name__}")
                except Exception as exc:
                    failures.append(f"POST {path} raised {type(exc).__name__}: {exc}")
        self.assertEqual(failures, [], "\n".join(failures))


class LiveWebApiContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web_port = available_port()
        cls.preview_port = available_port()
        cls.temporary = tempfile.TemporaryDirectory(prefix="lelamp-api-contract-")
        temporary_path = Path(cls.temporary.name)
        env = {
            **os.environ,
            "OPENCLAW_ENABLE_HARDWARE": "0",
            "OPENCLAW_WORKSPACE": str(temporary_path / "workspace"),
            "LELAMP_STARTUP_HOME": "0",
            "LELAMP_PROJECTION_KIOSK_PROFILE": str(temporary_path / "chromium-profile"),
            "LELAMP_WEB_TOKEN": CONTRACT_TOKEN,
        }
        cls.process = subprocess.Popen(
            [
                str(RUNTIME_ROOT / ".venv" / "bin" / "python"),
                "-u",
                "openclaw_cli.py",
                "web-console",
                "--host",
                "127.0.0.1",
                "--port",
                str(cls.web_port),
                "--token",
                CONTRACT_TOKEN,
                "--projection-preview-port",
                str(cls.preview_port),
            ],
            cwd=RUNTIME_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                output = cls.process.stdout.read() if cls.process.stdout else ""
                raise RuntimeError(f"Contract Web API exited during startup:\n{output}")
            try:
                status, _ = cls.request("GET", "/api/health", token=CONTRACT_TOKEN)
                if status == 200:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError("Contract Web API did not become healthy within 20 seconds.")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait(timeout=5)
        cls.temporary.cleanup()

    @classmethod
    def request(cls, method: str, path: str, *, token: str | None = None, payload: dict[str, Any] | None = None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{cls.web_port}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_service_starts_and_health_is_successful(self):
        status, response = self.request("GET", "/api/health", token=CONTRACT_TOKEN)
        self.assertEqual(status, 200)
        self.assertTrue(response["ok"])

    def test_key_status_endpoints_never_return_500(self):
        endpoints = (
            "/api/health",
            "/api/services/status",
            "/api/readiness",
            "/api/docs/stats",
            "/api/meeting/status",
            "/api/hardware/status",
            "/api/assistant/providers/status",
            "/api/voice/status",
            "/api/desktop/automation/status",
            "/api/remote/ssh/status",
        )
        failures = []
        for path in endpoints:
            status, response = self.request("GET", path, token=CONTRACT_TOKEN)
            if status >= 500:
                failures.append((path, status, response))
        self.assertEqual(failures, [])

    def test_unauthorized_and_path_traversal_are_rejected(self):
        status, _ = self.request("GET", "/api/health")
        self.assertEqual(status, 401)
        traversal = urllib.parse.quote("../../etc/passwd", safe="")
        status, response = self.request("GET", f"/api/shared/preview?file={traversal}", token=CONTRACT_TOKEN)
        self.assertIn(status, {403, 404})
        self.assertFalse(response["ok"])

    def test_dangerous_desktop_action_requires_explicit_authorization(self):
        status, response = self.request(
            "POST",
            "/api/desktop/control/action",
            token=CONTRACT_TOKEN,
            payload={"action": "mouse_click", "authorized": False},
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["data"]["status"], "needs_confirmation")


if __name__ == "__main__":
    unittest.main()
