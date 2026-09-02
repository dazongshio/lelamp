#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = ROOT / "lelamp_runtime" / ".venv" / "bin" / "python"
SECRET = "isolated-document-test-secret"


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_for_port(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket() as client:
            client.settimeout(0.2)
            if client.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError("隔离协作服务未能启动。")


def collaboration_token(document_id: str) -> str:
    payload = {
        "document_id": document_id,
        "actor_id": "integration-user",
        "display_name": "集成测试用户",
        "client_id": "integration01",
        "role": "owner",
        "exp": int(time.time()) + 300,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = hmac.new(SECRET.encode(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def main() -> None:
    run(
        [
            str(VENV_PYTHON),
            "-m",
            "unittest",
            "lelamp.test.test_document_workspace",
            "lelamp.test.test_meeting_document_integration",
            "lelamp.test.test_document_ai",
            "lelamp.test.test_document_sharing",
            "lelamp.test.test_document_backup",
            "-v",
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "lelamp_runtime")},
    )
    run(["npm", "run", "test:documents:markdown"])
    run(["npm", "run", "test:documents:frontend"])
    run(["npm", "run", "test:documents:editor"])
    with tempfile.TemporaryDirectory(prefix="lelamp-document-suite-") as temporary:
        port = available_port()
        env = {
            **os.environ,
            "LELAMP_COLLAB_HOST": "127.0.0.1",
            "LELAMP_COLLAB_PORT": str(port),
            "LELAMP_WEB_TOKEN": SECRET,
            "OPENCLAW_WORKSPACE": temporary,
        }
        service = subprocess.Popen(
            ["node", "scripts/collaboration-server.mjs"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            wait_for_port(port)
            document_id = uuid.uuid4().hex
            token = collaboration_token(document_id)
            run(
                [
                    "node",
                    "scripts/test_document_collaboration.mjs",
                    f"ws://127.0.0.1:{port}",
                    document_id,
                    token,
                ]
            )
            time.sleep(1)
            service.terminate()
            service.wait(timeout=5)
            service = subprocess.Popen(
                ["node", "scripts/collaboration-server.mjs"],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            wait_for_port(port)
            run(
                [
                    "node",
                    "scripts/check_document_collaboration_state.mjs",
                    f"ws://127.0.0.1:{port}",
                    document_id,
                    token,
                    "客户端甲",
                    "客户端乙",
                    "离线修改",
                    "在线修改",
                ]
            )
        finally:
            service.terminate()
            try:
                service.wait(timeout=5)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait(timeout=5)
    print(json.dumps({"status": "passed", "suite": "LeLamp 协作文档"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
