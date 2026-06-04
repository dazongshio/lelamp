#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "lelamp_runtime"
PYTHON = RUNTIME_ROOT / ".venv" / "bin" / "python"
DEFAULT_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
DEFAULT_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
PLACEHOLDER_SECRET_VALUES = {"replace_with_new_rotated_key", "replace_with_bailian_app_id"}


def assert_ok(name: str, condition: bool, details: object = "") -> None:
    if not condition:
        raise AssertionError(f"{name} failed: {details}")
    print(f"ok - {name}")


def run(name: str, command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print(f"== {name}")
    subprocess.run(command, cwd=cwd, env=env, check=True)
    print(f"ok - {name}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_http(base_url: str, token: str, *, timeout: float = 12.0) -> None:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    request = urllib.request.Request(f"{base_url}/api/security", headers={"Authorization": f"Bearer {token}"})
    last_error: object = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=1.5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"Web console did not become ready at {base_url}: {last_error}")


def terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def stop_process_group(process: subprocess.Popen[bytes]) -> None:
    if hasattr(os, "killpg") and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    else:
        terminate(process)


def assert_tingwu_env_template_safe() -> None:
    template = RUNTIME_ROOT / ".env.tingwu.example"
    gitignore = REPO_ROOT / ".gitignore"
    readme = RUNTIME_ROOT / "README.md"
    assert_ok("tingwu env template exists", template.is_file(), template)
    text = template.read_text(encoding="utf-8")
    readme_text = readme.read_text(encoding="utf-8")
    assert_ok(
        "tingwu env template keeps only placeholders",
        all(value in text for value in PLACEHOLDER_SECRET_VALUES)
        and "sk-" not in text
        and "export DASHSCOPE_API_KEY=" in text
        and "export TINGWU_APP_ID=" in text,
    )
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "lelamp_runtime/.env.tingwu.local"],
        cwd=REPO_ROOT,
        check=False,
    )
    assert_ok("tingwu local env file is gitignored", ignored.returncode == 0, gitignore)
    assert_ok(
        "tingwu README uses ignored env file for live credentials",
        "cp .env.tingwu.example .env.tingwu.local" in readme_text
        and "source .env.tingwu.local" in readme_text
        and "--env-file .env.tingwu.local" in readme_text
        and "TINGWU_API_KEY=... TINGWU_APP_ID=... OPENCLAW_MIC_DEVICE=auto" not in readme_text
        and "rotate it in" in readme_text,
    )
    configure_script = REPO_ROOT / "scripts" / "configure_tingwu_env.py"
    assert_ok("tingwu env configurator exists", configure_script.is_file(), configure_script)


def assert_tingwu_env_configurator_safe(temp_root: Path) -> None:
    env_file = temp_root / ".env.tingwu.local"
    wrong = subprocess.run(
        [
            str(PYTHON),
            str(REPO_ROOT / "scripts" / "configure_tingwu_env.py"),
            "--env-file",
            str(env_file),
            "--api-key",
            "LTAI_example_wrong_access_key_id",
            "--app-id",
            "legacy_openapi_project_key_without_tw_prefix",
            "--yes",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert_ok(
        "tingwu env configurator rejects wrong credential kinds without writing secrets",
        wrong.returncode == 1
        and not env_file.exists()
        and "aliyun_access_key_id" in wrong.stdout
        and "unexpected_app_id_shape" in wrong.stdout
        and "LTAI_example_wrong_access_key_id" not in wrong.stdout
        and "legacy_openapi_project_key_without_tw_prefix" not in wrong.stdout,
        wrong.stdout + wrong.stderr,
    )
    ok = subprocess.run(
        [
            str(PYTHON),
            str(REPO_ROOT / "scripts" / "configure_tingwu_env.py"),
            "--env-file",
            str(env_file),
            "--api-key",
            "dashscope-valid-like-key",
            "--app-id",
            "tw_valid_like_app",
            "--mic-device",
            "plughw:2,0",
            "--yes",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    text = env_file.read_text(encoding="utf-8") if env_file.is_file() else ""
    mode = env_file.stat().st_mode & 0o777 if env_file.is_file() else 0
    assert_ok(
        "tingwu env configurator writes ignored local env file",
        ok.returncode == 0
        and "dashscope-valid-like-key" not in ok.stdout
        and "tw_valid_like_app" not in ok.stdout
        and "export DASHSCOPE_API_KEY='dashscope-valid-like-key'" in text
        and "export TINGWU_APP_ID='tw_valid_like_app'" in text
        and "export OPENCLAW_MIC_DEVICE='plughw:2,0'" in text
        and mode & 0o077 == 0,
        {"stdout": ok.stdout, "stderr": ok.stderr, "mode": oct(mode), "text": text},
    )


def run_web_console_smoke(
    *,
    temp_root: Path,
    workspace: Path,
    audit_log: Path,
    token: str,
    env: dict[str, str],
    label: str,
    smoke_args: list[str],
) -> None:
    port = free_port()
    log_path = temp_root / f"{label}.log"
    with log_path.open("wb") as log_stream:
        process = subprocess.Popen(
            [str(PYTHON), "openclaw_cli.py", "web-console", "--host", "127.0.0.1", "--port", str(port), "--token", token],
            cwd=RUNTIME_ROOT,
            env={**env, "OPENCLAW_WORKSPACE_DIR": str(workspace), "OPENCLAW_AUDIT_LOG_PATH": str(audit_log)},
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
        try:
            base_url = f"http://127.0.0.1:{port}"
            wait_for_http(base_url, token)
            run(
                label,
                [sys.executable, "../scripts/smoke_web_api.py", "--base-url", base_url, "--token", token, *smoke_args],
                cwd=RUNTIME_ROOT,
            )
        except Exception:
            print(f"web console log: {log_path}", file=sys.stderr)
            raise
        finally:
            stop_process_group(process)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local non-live Tingwu meeting verification suite.")
    parser.add_argument("--skip-build", action="store_true", help="Skip npm production build.")
    parser.add_argument("--keep-workspace", action="store_true", help="Keep temporary smoke workspace for inspection.")
    args = parser.parse_args()

    assert_ok("runtime python exists", PYTHON.is_file(), PYTHON)

    py_compile_targets = [
        "lelamp/office_agent/tingwu_meeting.py",
        "lelamp/office_agent/hardware_probe.py",
        "lelamp/office_agent/dashscope_streaming_asr.py",
        "openclaw_voice.py",
        "openclaw_realtime_voice.py",
        "../scripts/smoke_audio_device_resolution.py",
        "lelamp/office_agent/web_console.py",
        "../scripts/preflight_tingwu_live.py",
        "../scripts/audit_tingwu_live_evidence.py",
        "../scripts/smoke_frontend_meeting_static.py",
        "../scripts/smoke_tingwu_evidence_audit.py",
        "../scripts/smoke_tingwu_provider.py",
        "../scripts/smoke_web_api.py",
        "../scripts/verify_tingwu_live.py",
        "../scripts/verify_tingwu_web_live.py",
        "../scripts/verify_tingwu_live_suite.py",
        "../scripts/configure_tingwu_env.py",
    ]
    run("python compile", [str(PYTHON), "-m", "py_compile", *py_compile_targets], cwd=RUNTIME_ROOT)
    assert_tingwu_env_template_safe()
    with tempfile.TemporaryDirectory(prefix="lelamp-tingwu-env-config-") as env_config_temp:
        assert_tingwu_env_configurator_safe(Path(env_config_temp))
    run("audio device resolution smoke", [str(PYTHON), "../scripts/smoke_audio_device_resolution.py"], cwd=RUNTIME_ROOT)
    run("frontend meeting static smoke", [sys.executable, "scripts/smoke_frontend_meeting_static.py"], cwd=REPO_ROOT)
    run("tingwu evidence audit smoke", [sys.executable, "scripts/smoke_tingwu_evidence_audit.py"], cwd=REPO_ROOT)
    if not args.skip_build:
        run("frontend build", ["npm", "run", "build"], cwd=REPO_ROOT)
    run("tingwu provider smoke", [str(PYTHON), "../scripts/smoke_tingwu_provider.py"], cwd=RUNTIME_ROOT)

    temp_root = Path(tempfile.mkdtemp(prefix="lelamp-tingwu-local-suite-"))
    workspace = temp_root / "workspace"
    audit_log = temp_root / "audit.jsonl"
    token = "test-console"
    env = {
        **os.environ,
        "OPENCLAW_DISABLE_CLOUD": "1",
        "OPENCLAW_CLOUD_AI_ENABLED": "0",
        "OPENAI_API_KEY": "",
        "GROQ_API_KEY": "",
        "ELEVENLABS_API_KEY": "",
        "DASHSCOPE_API_KEY": "",
        "TINGWU_MOCK": "1",
        "TINGWU_HTTP_URL": DEFAULT_TINGWU_HTTP_URL,
        "TINGWU_WS_URL": DEFAULT_TINGWU_WS_URL,
        "LELAMP_WEB_TOKEN": token,
    }
    try:
        run_web_console_smoke(
            temp_root=temp_root,
            workspace=workspace,
            audit_log=audit_log,
            token=token,
            env=env,
            label="web api mock smoke",
            smoke_args=["--expect-default-tingwu-endpoints"],
        )
        run_web_console_smoke(
            temp_root=temp_root,
            workspace=workspace,
            audit_log=audit_log,
            token=token,
            env=env,
            label="web api restart recovery smoke",
            smoke_args=["--restart-recovery"],
        )
    finally:
        if not args.keep_workspace:
            shutil.rmtree(temp_root, ignore_errors=True)

    print("smoke_tingwu_local_suite complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
