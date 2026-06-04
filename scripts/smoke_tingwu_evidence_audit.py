#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from verify_tingwu_live_suite import (  # noqa: E402
    DEFAULT_SPOKEN_PHRASE,
    PYTHON,
    RUNTIME_ROOT,
    free_port,
    goal_requirement_report,
    load_env_file,
    merge_stage_result,
    start_web_console,
    stop_process,
    wait_for_console,
)

OFFICIAL_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
OFFICIAL_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


def workspace_path(path: Path, workspace: Path) -> str:
    return str(path.resolve().relative_to(workspace.resolve()))


def assert_ok(name: str, condition: bool, details: object = "") -> None:
    if not condition:
        raise AssertionError(f"{name} failed: {details}")
    print(f"ok - {name}")


def assert_env_file_parser_is_safe(root: Path) -> None:
    valid = root / "valid_env.tingwu.local"
    valid.write_text(
        "\n".join(
            [
                "export DASHSCOPE_API_KEY='placeholder-key'",
                "TINGWU_APP_ID=placeholder-app",
                "OPENCLAW_MIC_DEVICE=auto",
            ]
        ),
        encoding="utf-8",
    )
    loaded = load_env_file(str(valid))
    assert_ok(
        "live suite env-file parser accepts known quoted values",
        loaded == {
            "DASHSCOPE_API_KEY": "placeholder-key",
            "TINGWU_APP_ID": "placeholder-app",
            "OPENCLAW_MIC_DEVICE": "auto",
        },
        loaded,
    )
    unsupported = root / "unsupported_env.tingwu.local"
    unsupported.write_text("UNSAFE_COMMAND=rm -rf /tmp/nope\n", encoding="utf-8")
    try:
        load_env_file(str(unsupported))
    except SystemExit as exc:
        assert_ok("live suite env-file parser rejects unsupported keys", "UNSAFE_COMMAND" in str(exc), exc)
    else:
        raise AssertionError("live suite env-file parser accepted unsupported key")
    shellish = root / "shellish_env.tingwu.local"
    shellish.write_text("DASHSCOPE_API_KEY=$(echo should_not_execute)\n", encoding="utf-8")
    loaded_shellish = load_env_file(str(shellish))
    assert_ok(
        "live suite env-file parser does not execute shell expansion",
        loaded_shellish.get("DASHSCOPE_API_KEY") == "$(echo should_not_execute)",
        loaded_shellish,
    )
    cwd_relative_dir = REPO_ROOT / ".tmp_tingwu_env_parser_smoke"
    cwd_relative = cwd_relative_dir / "cwd_relative.env"
    try:
        cwd_relative_dir.mkdir(parents=True, exist_ok=True)
        cwd_relative.write_text(
            "\n".join(
                [
                    "export DASHSCOPE_API_KEY='placeholder-key'",
                    "TINGWU_APP_ID=placeholder-app",
                ]
            ),
            encoding="utf-8",
        )
        loaded_relative = load_env_file(str(cwd_relative.relative_to(REPO_ROOT)))
        assert_ok(
            "live suite env-file parser accepts repo-relative paths",
            loaded_relative.get("DASHSCOPE_API_KEY") == "placeholder-key"
            and loaded_relative.get("TINGWU_APP_ID") == "placeholder-app",
            loaded_relative,
        )
    finally:
        try:
            cwd_relative.unlink()
            cwd_relative_dir.rmdir()
        except OSError:
            pass
    try:
        load_env_file("lelamp_runtime/does-not-exist.env")
    except SystemExit as exc:
        message = str(exc)
        assert_ok(
            "live suite env-file parser reports repo-relative missing path",
            str(REPO_ROOT / "lelamp_runtime" / "does-not-exist.env") in message
            and "lelamp_runtime/lelamp_runtime" not in message,
            message,
        )
    else:
        raise AssertionError("live suite env-file parser accepted missing repo-relative path")


def write_file(path: Path, content: str = "ok") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_audit_log(path: Path, actions: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"action": action, "status": "ok", "target": "smoke"}, ensure_ascii=False, sort_keys=True)
        for action in actions
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_wav(path: Path, *, frames: int = 192000, sample_rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x01\x00" * frames)
    return path


def run_audit(path: Path, *, check_files: bool = True) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "scripts/audit_tingwu_live_evidence.py", str(path)]
    if check_files:
        command.append("--check-files")
    return subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True)


def run_live_suite_missing_credentials(
    evidence_dir: Path,
    *,
    extra_args: list[str] | None = None,
    env_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"TINGWU_API_KEY", "DASHSCOPE_API_KEY", "TINGWU_APP_ID", "TINGWU_MEETING_APP_ID"}
    }
    command = [
        sys.executable,
        "scripts/verify_tingwu_live_suite.py",
        "--seconds",
        "1",
        "--evidence-dir",
        str(evidence_dir),
    ]
    if env_file is not None:
        command.extend(["--env-file", str(env_file)])
    command.extend(extra_args or [])
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


def assert_env_file_placeholders_do_not_hit_cloud(root: Path) -> None:
    env_file = root / "placeholder_env.tingwu.local"
    env_file.write_text(
        "\n".join(
            [
                "export DASHSCOPE_API_KEY='replace_with_new_rotated_key'",
                "export TINGWU_APP_ID='replace_with_bailian_app_id'",
                "export OPENCLAW_MIC_DEVICE=auto",
            ]
        ),
        encoding="utf-8",
    )
    evidence_dir = root / "placeholder_evidence"
    result = run_live_suite_missing_credentials(
        evidence_dir,
        extra_args=["--skip-preflight", "--seconds", "1", "--preflight-capture-seconds", "1"],
        env_file=env_file,
    )
    summary_path = evidence_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    direct_run = (
        summary.get("stage_runs", {}).get("direct_provider", {})
        if isinstance(summary.get("stage_runs"), dict)
        else {}
    )
    assert_ok(
        "live suite treats template env-file values as missing credentials",
        result.returncode == 1
        and str(result.stderr).startswith("Missing live Tingwu credentials")
        and summary.get("acceptance_complete") is False
        and isinstance(direct_run, dict)
        and str(direct_run.get("error") or "").startswith("Missing live Tingwu credentials")
        and "InvalidApiKey" not in result.stdout
        and "InvalidApiKey" not in result.stderr,
        {"returncode": result.returncode, "stdout": result.stdout[-1000:], "stderr": result.stderr[-1000:], "summary": summary},
    )

    wrong_kind_env_file = root / "wrong_kind_env.tingwu.local"
    wrong_kind_env_file.write_text(
        "\n".join(
            [
                "export DASHSCOPE_API_KEY='LTAI_example_wrong_access_key_id'",
                "export TINGWU_APP_ID='AppKey example_wrong_tingwu_openapi_project_key'",
                "export OPENCLAW_MIC_DEVICE=auto",
            ]
        ),
        encoding="utf-8",
    )
    wrong_kind_evidence_dir = root / "wrong_kind_evidence"
    wrong_kind_result = run_live_suite_missing_credentials(
        wrong_kind_evidence_dir,
        extra_args=["--skip-preflight", "--seconds", "1", "--preflight-capture-seconds", "1"],
        env_file=wrong_kind_env_file,
    )
    wrong_kind_summary_path = wrong_kind_evidence_dir / "summary.json"
    wrong_kind_summary = json.loads(wrong_kind_summary_path.read_text(encoding="utf-8")) if wrong_kind_summary_path.is_file() else {}
    wrong_kind_readiness = wrong_kind_summary.get("goal_readiness") if isinstance(wrong_kind_summary.get("goal_readiness"), dict) else {}
    wrong_kind_configure_action = next(
        (
            item
            for item in wrong_kind_summary.get("next_actions", [])
            if isinstance(item, dict) and item.get("id") == "configure_tingwu_credentials"
        ),
        {},
    ) if isinstance(wrong_kind_summary.get("next_actions"), list) else {}
    assert_ok(
        "live suite treats AccessKey/AppKey-shaped env-file values as missing credentials",
        wrong_kind_result.returncode == 1
        and str(wrong_kind_result.stderr).startswith("Missing live Tingwu credentials")
        and wrong_kind_summary.get("acceptance_complete") is False
        and wrong_kind_readiness.get("credential_diagnostics", {}).get("api_key_kind") == "aliyun_access_key_id"
        and wrong_kind_readiness.get("credential_diagnostics", {}).get("app_id_kind") == "legacy_tingwu_appkey"
        and isinstance(wrong_kind_configure_action, dict)
        and "fresh Bailian/DashScope API Key" in str(wrong_kind_configure_action.get("message") or "")
        and "legacy Tingwu OpenAPI AppKey" in str(wrong_kind_configure_action.get("message") or "")
        and "example_wrong_tingwu_openapi_project_key" not in json.dumps(wrong_kind_summary, ensure_ascii=False)
        and "InvalidApiKey" not in wrong_kind_result.stdout
        and "InvalidApiKey" not in wrong_kind_result.stderr,
        {
            "returncode": wrong_kind_result.returncode,
            "stdout": wrong_kind_result.stdout[-1000:],
            "stderr": wrong_kind_result.stderr[-1000:],
            "summary": wrong_kind_summary,
        },
    )

    checker = subprocess.run(
        [
            sys.executable,
            "scripts/check_tingwu_env_file.py",
            "--env-file",
            str(wrong_kind_env_file),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    checker_payload = json.loads(checker.stdout) if checker.stdout.strip().startswith("{") else {}
    assert_ok(
        "tingwu env-file checker reports wrong credential kinds without leaking values",
        checker.returncode == 1
        and checker_payload.get("status") == "failed"
        and checker_payload.get("checks", {}).get("api_key_kind") == "aliyun_access_key_id"
        and checker_payload.get("checks", {}).get("app_id_kind") == "legacy_tingwu_appkey"
        and "LTAI_example_wrong_access_key_id" not in checker.stdout
        and "example_wrong_tingwu_openapi_project_key" not in checker.stdout,
        {"returncode": checker.returncode, "stdout": checker.stdout, "stderr": checker.stderr},
    )

    bare_appkey_env_file = root / "bare_appkey_env.tingwu.local"
    bare_appkey_env_file.write_text(
        "\n".join(
            [
                "export DASHSCOPE_API_KEY='valid-dashscope-like-key'",
                "export TINGWU_APP_ID='legacy_openapi_project_key_without_tw_prefix'",
                "export OPENCLAW_MIC_DEVICE=auto",
            ]
        ),
        encoding="utf-8",
    )
    bare_appkey_evidence_dir = root / "bare_appkey_evidence"
    bare_appkey_result = run_live_suite_missing_credentials(
        bare_appkey_evidence_dir,
        extra_args=["--skip-preflight", "--seconds", "1", "--preflight-capture-seconds", "1"],
        env_file=bare_appkey_env_file,
    )
    bare_appkey_checker = subprocess.run(
        [
            sys.executable,
            "scripts/check_tingwu_env_file.py",
            "--env-file",
            str(bare_appkey_env_file),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    bare_appkey_checker_payload = json.loads(bare_appkey_checker.stdout) if bare_appkey_checker.stdout.strip().startswith("{") else {}
    bare_appkey_summary_path = bare_appkey_evidence_dir / "summary.json"
    bare_appkey_summary = json.loads(bare_appkey_summary_path.read_text(encoding="utf-8")) if bare_appkey_summary_path.is_file() else {}
    bare_appkey_readiness = bare_appkey_summary.get("goal_readiness") if isinstance(bare_appkey_summary.get("goal_readiness"), dict) else {}
    bare_appkey_configure_action = next(
        (
            item
            for item in bare_appkey_summary.get("next_actions", [])
            if isinstance(item, dict) and item.get("id") == "configure_tingwu_credentials"
        ),
        {},
    ) if isinstance(bare_appkey_summary.get("next_actions"), list) else {}
    assert_ok(
        "live suite rejects non-tw App ID shape before cloud calls",
        bare_appkey_result.returncode == 1
        and str(bare_appkey_result.stderr).startswith("Missing live Tingwu credentials")
        and "app_id_kind=unexpected_app_id_shape" in bare_appkey_result.stderr
        and bare_appkey_readiness.get("credential_diagnostics", {}).get("api_key_kind") == "configured"
        and bare_appkey_readiness.get("credential_diagnostics", {}).get("app_id_kind") == "unexpected_app_id_shape"
        and "usually starts with tw_" in str(bare_appkey_configure_action.get("message") or "")
        and "InvalidApiKey" not in bare_appkey_result.stdout
        and "InvalidApiKey" not in bare_appkey_result.stderr
        and "legacy_openapi_project_key_without_tw_prefix" not in json.dumps(bare_appkey_summary, ensure_ascii=False)
        and bare_appkey_checker.returncode == 1
        and bare_appkey_checker_payload.get("checks", {}).get("api_key_kind") == "configured"
        and bare_appkey_checker_payload.get("checks", {}).get("app_id_kind") == "unexpected_app_id_shape"
        and "legacy_openapi_project_key_without_tw_prefix" not in bare_appkey_checker.stdout,
        {
            "suite": {
                "returncode": bare_appkey_result.returncode,
                "stdout": bare_appkey_result.stdout[-1000:],
                "stderr": bare_appkey_result.stderr[-1000:],
            },
            "checker": {
                "returncode": bare_appkey_checker.returncode,
                "stdout": bare_appkey_checker.stdout,
                "stderr": bare_appkey_checker.stderr,
            },
        },
    )


def run_preflight_missing_credentials(evidence_json: Path, workspace: Path, audit_log: Path) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"TINGWU_API_KEY", "DASHSCOPE_API_KEY", "TINGWU_APP_ID", "TINGWU_MEETING_APP_ID"}
    }
    command = [
        str(REPO_ROOT / "lelamp_runtime" / ".venv" / "bin" / "python"),
        "../scripts/preflight_tingwu_live.py",
        "--workspace",
        str(workspace),
        "--audit-log",
        str(audit_log),
        "--evidence-json",
        str(evidence_json),
        "--capture-seconds",
        "1",
    ]
    return subprocess.run(
        command,
        cwd=REPO_ROOT / "lelamp_runtime",
        env=env,
        text=True,
        capture_output=True,
    )


def run_direct_live_missing_credentials(evidence_json: Path, workspace: Path, audit_log: Path) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"TINGWU_API_KEY", "DASHSCOPE_API_KEY", "TINGWU_APP_ID", "TINGWU_MEETING_APP_ID"}
    }
    command = [
        str(REPO_ROOT / "lelamp_runtime" / ".venv" / "bin" / "python"),
        "../scripts/verify_tingwu_live.py",
        "--seconds",
        "1",
        "--workspace",
        str(workspace),
        "--audit-log",
        str(audit_log),
        "--evidence-json",
        str(evidence_json),
        "--spoken-phrase",
        DEFAULT_SPOKEN_PHRASE,
    ]
    return subprocess.run(
        command,
        cwd=REPO_ROOT / "lelamp_runtime",
        env=env,
        text=True,
        capture_output=True,
    )


def run_web_live_missing_credentials(
    evidence_json: Path,
    workspace: Path,
    audit_log: Path,
    log_path: Path,
    *,
    token: str = "missing-credentials-web",
) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"TINGWU_API_KEY", "DASHSCOPE_API_KEY", "TINGWU_APP_ID", "TINGWU_MEETING_APP_ID"}
    }
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    web_env = {
        **env,
        "LELAMP_WEB_TOKEN": token,
        "OPENCLAW_WORKSPACE_DIR": str(workspace),
        "OPENCLAW_AUDIT_LOG_PATH": str(audit_log),
        "OPENCLAW_ALLOWED_ROOTS": str(workspace),
    }
    process = start_web_console(
        command=[str(PYTHON), "openclaw_cli.py", "web-console", "--host", "127.0.0.1", "--port", str(port), "--token", token],
        cwd=RUNTIME_ROOT,
        env=web_env,
        log_path=log_path,
    )
    try:
        wait_for_console(base_url, token)
        return subprocess.run(
            [
                str(PYTHON),
                "../scripts/verify_tingwu_web_live.py",
                "--base-url",
                base_url,
                "--token",
                token,
                "--seconds",
                "1",
                "--evidence-json",
                str(evidence_json),
                "--spoken-phrase",
                DEFAULT_SPOKEN_PHRASE,
            ],
            cwd=RUNTIME_ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
    finally:
        stop_process(process)


def official_endpoint_probe() -> dict[str, object]:
    return {
        "http_url": OFFICIAL_TINGWU_HTTP_URL,
        "ws_url": OFFICIAL_TINGWU_WS_URL,
        "http": {
            "url": OFFICIAL_TINGWU_HTTP_URL,
            "scheme": "https",
            "host": "dashscope.aliyuncs.com",
            "path": "/api/v1/services/aigc/multimodal-generation/generation",
            "origin": "https://dashscope.aliyuncs.com",
        },
        "ws": {
            "url": OFFICIAL_TINGWU_WS_URL,
            "scheme": "wss",
            "host": "dashscope.aliyuncs.com",
            "path": "/api-ws/v1/inference",
            "origin": "wss://dashscope.aliyuncs.com",
        },
        "official_dashscope": True,
    }


def assert_live_verifiers_preserve_spoken_phrase_failure_evidence() -> None:
    direct_verifier = (REPO_ROOT / "scripts" / "verify_tingwu_live.py").read_text(encoding="utf-8")
    web_verifier = (REPO_ROOT / "scripts" / "verify_tingwu_web_live.py").read_text(encoding="utf-8")
    required_tokens = (
        "if not spoken_phrase_detected:",
        "write_evidence(",
        "\"spoken_phrase_failure\"",
        "spoken_phrase_failure_details(",
        "\"status\": \"failed\"",
        "require_capture_probe: bool = True",
        "require_capture_probe=False",
        "capture_probe = probe.get(\"capture_probe\")",
        "str(capture_probe.get(\"status\") or \"\") == \"available\"",
        "capture_bytes > 0",
        "capture_rms > 0",
        "capture_peak > 0",
        "\"redaction_canaries\": list(CANARY_SECRETS)",
    )
    missing = {
        "direct": [token for token in required_tokens if token not in direct_verifier],
        "web": [token for token in required_tokens if token not in web_verifier],
    }
    direct_required = ("\"workspace_lock_path\"", "\"workspace_lock_released\"")
    web_required = ("\"manifest_path\"", "\"task_file\"")
    missing["direct"].extend(token for token in direct_required if token not in direct_verifier)
    missing["web"].extend(token for token in web_required if token not in web_verifier)
    canary_counts = {
        "direct": direct_verifier.count("\"redaction_canaries\": list(CANARY_SECRETS)"),
        "web": web_verifier.count("\"redaction_canaries\": list(CANARY_SECRETS)"),
    }
    if canary_counts["direct"] < 2:
        missing["direct"].append("redaction canaries in both failure and success evidence")
    if canary_counts["web"] < 2:
        missing["web"].append("redaction canaries in both failure and success evidence")
    if missing["direct"] or missing["web"]:
        raise AssertionError(f"live verifiers must preserve spoken phrase failure evidence: {missing}")
    print("ok - live verifiers preserve spoken phrase failure evidence")


def assert_live_verifiers_preserve_start_failure_evidence() -> None:
    direct_verifier = (REPO_ROOT / "scripts" / "verify_tingwu_live.py").read_text(encoding="utf-8")
    web_verifier = (REPO_ROOT / "scripts" / "verify_tingwu_web_live.py").read_text(encoding="utf-8")
    required_tokens = (
        "start_failure_evidence(",
        "\"error_details\"",
        "\"microphone_capture_open\"",
        "\"microphone_capture_signal\"",
        "\"realtime_start_succeeded\": False",
        "\"spoken_phrase_detected\": False",
        "\"redaction_canaries\": list(CANARY_SECRETS)",
        "write_evidence(",
        "\"mic_probe\"",
        "\"endpoint_probe\"",
    )
    missing = {
        "direct": [token for token in required_tokens if token not in direct_verifier],
        "web": [token for token in required_tokens if token not in web_verifier],
    }
    direct_required = (
        "except TingwuMeetingError as exc:",
        "provider.start_realtime_meeting(",
        "details = getattr(error, \"details\", {})",
    )
    web_required = (
        "start_status, start_envelope = request_envelope(",
        "status_code=start_status",
        "\"error_code\"",
    )
    missing["direct"].extend(token for token in direct_required if token not in direct_verifier)
    missing["web"].extend(token for token in web_required if token not in web_verifier)
    if missing["direct"] or missing["web"]:
        raise AssertionError(f"live verifiers must preserve realtime start failure evidence: {missing}")
    print("ok - live verifiers preserve realtime start failure evidence")


def assert_live_verifiers_preserve_missing_credentials_evidence() -> None:
    direct_verifier = (REPO_ROOT / "scripts" / "verify_tingwu_live.py").read_text(encoding="utf-8")
    web_verifier = (REPO_ROOT / "scripts" / "verify_tingwu_web_live.py").read_text(encoding="utf-8")
    required_tokens = (
        "missing_credentials_evidence(",
        "\"tingwu_api_key_configured\"",
        "\"tingwu_app_id_configured\"",
        "\"realtime_start_succeeded\": False",
        "\"redaction_canaries\": list(CANARY_SECRETS)",
        "write_evidence(args.evidence_json, evidence)",
    )
    missing = {
        "direct": [token for token in required_tokens if token not in direct_verifier],
        "web": [token for token in required_tokens if token not in web_verifier],
    }
    web_required = (
        "\"allowed_roots_probe\"",
        "\"full_control_gate\"",
        "\"sandbox_visible\"",
        "\"audit_only_visible\"",
    )
    missing["web"].extend(token for token in web_required if token not in web_verifier)
    if missing["direct"] or missing["web"]:
        raise AssertionError(f"live verifiers must preserve missing-credentials evidence: {missing}")
    print("ok - live verifiers preserve missing-credentials evidence")


def full_summary(root: Path) -> dict[str, object]:
    direct_spoken_phrase = "乐灯听悟验收测试"
    web_spoken_phrase = "乐灯听悟验收测试"
    direct_canaries = ["direct-live-title-token", "direct-live-title-password", "direct-live-participant-password"]
    web_canaries = ["web-live-title-token", "web-live-title-password", "web-live-participant-password"]
    preflight_ws = root / "preflight_ws"
    direct_ws = root / "direct_ws"
    web_ws = root / "web_ws"
    for directory in (preflight_ws, direct_ws, web_ws):
        directory.mkdir(parents=True, exist_ok=True)
    runtime_root = REPO_ROOT / "lelamp_runtime"
    write_file(root / "web-console-live.log", "web console log")
    write_file(root / "web-console-live-restart.log", "web console restart log")

    direct_checks = {
        "provider_configured",
        "official_tingwu_endpoint",
        "real_microphone_device",
        "stopped_before_ai_minutes",
        "audio_saved",
        "audio_has_signal",
        "websocket_stream_started",
        "websocket_audio_frames_sent",
        "transcript_saved",
        "realtime_transcript_non_empty",
        "spoken_phrase_detected",
        "ai_minutes_saved",
        "tingwu_minutes_structured",
        "ai_minutes_task_metadata",
        "workspace_boundary",
        "audit_lifecycle_visible",
        "api_key_absent",
        "app_id_absent_from_audit",
        "no_temp_files",
        "workspace_lock_released",
        "tingwu_http_operations_visible",
    }
    web_checks = {
        "sandbox_visible",
        "audit_only_visible",
        "workspace_allowed",
        "allowed_roots_block_enforced",
        "full_control_gate_enforced",
        "full_control_audit_visible",
        "provider_available",
        "live_mode_not_mock",
        "official_tingwu_endpoint",
        "real_microphone_device",
        "stopped_before_ai_minutes",
        "audio_saved",
        "audio_has_signal",
        "websocket_stream_started",
        "websocket_audio_frames_sent",
        "transcript_saved",
        "realtime_transcript_non_empty",
        "spoken_phrase_detected",
        "openclaw_minutes_saved",
        "tingwu_minutes_saved",
        "ai_minutes_task_metadata",
        "tingwu_minutes_structured",
        "manifest_saved",
        "followup_outputs_saved",
        "manifest_indexes_followup_outputs",
        "step_realtime_capture_completed",
        "step_import_transcript_completed",
        "step_minutes_completed",
        "step_decisions_waiting_confirmation",
        "step_action_items_completed",
        "step_followup_completed",
        "step_reminders_completed",
        "step_projection_confirmation_completed",
        "all_required_steps",
        "task_monitor_listed",
        "task_monitor_metrics_visible",
        "meeting_jobs_restore_outputs",
        "web_restart_recovery_outputs",
        "assistant_stop_notification",
        "assistant_fetch_notification",
        "status_completed_after_fetch",
        "audit_lifecycle_visible",
        "api_key_absent",
        "app_id_absent_from_audit",
        "no_temp_files",
        "tingwu_http_operations_visible",
    }
    direct_audit_actions = ["tingwu.meeting_start", "tingwu.audio_save", "tingwu.meeting_finalize"]
    web_audit_actions = [
        "tingwu.meeting_start",
        "tingwu.audio_save",
        "tingwu.meeting_finalize",
        "meeting_realtime_stop",
        "meeting_realtime_fetch_minutes",
        "meeting.transcript_parse",
        "meeting.minutes_generated",
        "meeting.decisions_extract",
        "meeting.action_items_extract",
        "p0.meeting_followup_email_write",
        "daily.reminders_from_actions",
        "meeting.reminders_snapshot",
        "projection.render_markdown",
        "meeting_projection_workspace_copy",
        "p0.meeting_followup_package",
        "meeting_manifest",
    ]
    direct_output_dir = direct_ws / "meetings" / "direct-meeting"
    direct_transcript = direct_output_dir / "transcript.md"
    direct_audio = direct_output_dir / "audio.wav"
    direct_minutes = direct_output_dir / "tingwu_ai_minutes.md"
    direct_session = direct_output_dir / "session.json"
    web_output_dir = web_ws / "meetings" / "web-meeting"
    web_transcript = web_output_dir / "transcript.md"
    web_audio = web_output_dir / "audio.wav"
    web_tingwu_minutes = web_output_dir / "tingwu_ai_minutes.md"
    web_openclaw_minutes = web_output_dir / "openclaw_minutes.md"
    web_decisions = web_output_dir / "decisions.json"
    web_actions = web_output_dir / "action_items.json"
    web_transcript_export = web_output_dir / "followup_transcript.json"
    web_email_draft = web_output_dir / "followup_email.md"
    web_reminders_store = web_output_dir / "reminders.json"
    web_projection_confirmation = web_output_dir / "projection_confirmation.md"
    web_manifest = web_output_dir / "manifest.json"
    web_session = web_output_dir / "session.json"
    web_task = web_ws / "web_tasks" / "task.json"
    web_notifications = web_ws / ".assistant" / "notifications.json"
    direct_http_operations = [
        {
            "timestamp": "2026-05-27T00:00:00+00:00",
            "action": "CreateTask",
            "endpoint": OFFICIAL_TINGWU_HTTP_URL,
            "model": "tingwu-meeting",
            "request_task": "createTask",
            "request_type": "realtime",
            "request_data_id": None,
            "response_data_id": "direct-provider-task",
            "response_status": "",
        },
        {
            "timestamp": "2026-05-27T00:00:01+00:00",
            "action": "CreateRealtimeMinutesTask",
            "endpoint": OFFICIAL_TINGWU_HTTP_URL,
            "model": "tingwu-meeting",
            "request_task": "createTask",
            "request_type": "realtime",
            "request_data_id": "direct-provider-task",
            "response_data_id": "direct-minutes-task",
            "response_status": "",
        },
        {
            "timestamp": "2026-05-27T00:00:02+00:00",
            "action": "GetTask",
            "endpoint": OFFICIAL_TINGWU_HTTP_URL,
            "model": "tingwu-meeting",
            "request_task": "getTask",
            "request_type": None,
            "request_data_id": "direct-minutes-task",
            "response_data_id": "direct-minutes-task",
            "response_status": "completed",
        },
    ]
    web_http_operations = [
        {**item, "response_data_id": str(item.get("response_data_id") or "").replace("direct", "web"), "request_data_id": (str(item.get("request_data_id")).replace("direct", "web") if item.get("request_data_id") else item.get("request_data_id"))}
        for item in direct_http_operations
    ]

    web_followup_paths = {
        "openclaw_minutes": str(write_file(web_openclaw_minutes, "OpenClaw minutes.")),
        "transcript_export": str(write_json(web_transcript_export, {"transcript": ["Web live transcript text."]})),
        "email_draft": str(write_file(web_email_draft, "Follow-up email draft.")),
        "reminders": str(write_json(web_reminders_store, [{"text": "Follow up."}])),
        "projection_confirmation": str(write_file(web_projection_confirmation, "# Projection confirmation\n")),
        "decisions": str(write_json(web_decisions, {"items": ["Decision"]})),
        "action_items": str(write_json(web_actions, {"items": ["Action"]})),
    }

    write_json(web_session, {
        "meeting_id": "web-meeting",
        "status": "completed",
        "task_id": "web-provider-task",
        "output_dir": str(web_output_dir),
        "transcript_path": str(web_transcript),
        "audio_path": str(web_audio),
        "minutes_path": str(web_tingwu_minutes),
        "audio_bytes": 384000,
        "audio_seconds": 12,
        "sample_rate": 16000,
        "audio_format": "pcm",
        "websocket_audio_frames": 120,
        "audio_rms": 100,
        "audio_peak": 1000,
        "transcript": [
            {
                "timestamp": "2026-05-27T00:00:00+00:00",
                "speaker": "Speaker 1",
                "text": f"Web live transcript text. {web_spoken_phrase}.",
                "final": True,
            }
        ],
        "tingwu_http_operations": web_http_operations,
        "ai_minutes": {"source_data_id": "web-provider-task", "minutes_task_id": "web-minutes-task"},
    })

    summary = {
        "status": "ok",
        "acceptance_complete": True,
        "stage_status": {"preflight": "ok", "direct_provider": "ok", "web_api": "ok"},
        "acceptance_blockers": [],
        "evidence_dir": str(root),
        "seconds": 12,
        "preflight_capture_seconds": 3,
        "stage_runs": {
            "preflight": {
                "status": "ok",
                "stage": "live preflight",
                "command": [
                    sys.executable,
                    "../scripts/preflight_tingwu_live.py",
                    "--workspace",
                    str(preflight_ws),
                    "--audit-log",
                    str(root / "preflight_audit.jsonl"),
                    "--evidence-json",
                    str(root / "preflight.json"),
                    "--capture-seconds",
                    "3",
                ],
                "cwd": str(runtime_root),
                "evidence_json": str(root / "preflight.json"),
                "workspace": str(preflight_ws),
                "audit_log": str(root / "preflight_audit.jsonl"),
                "capture_seconds": 3,
            },
            "direct_provider": {
                "status": "ok",
                "stage": "direct live provider verification",
                "command": [
                    sys.executable,
                    "../scripts/verify_tingwu_live.py",
                    "--seconds",
                    "12",
                    "--workspace",
                    str(direct_ws),
                    "--audit-log",
                    str(root / "direct_audit.jsonl"),
                    "--evidence-json",
                    str(root / "direct_provider.json"),
                    "--spoken-phrase",
                    direct_spoken_phrase,
                ],
                "cwd": str(runtime_root),
                "evidence_json": str(root / "direct_provider.json"),
                "workspace": str(direct_ws),
                "audit_log": str(root / "direct_audit.jsonl"),
            },
            "web_api": {
                "status": "ok",
                "stage": "web live verification",
                "command": [
                    sys.executable,
                    "../scripts/verify_tingwu_web_live.py",
                    "--base-url",
                    "http://127.0.0.1:8790",
                    "--token",
                    "test-console",
                    "--seconds",
                    "12",
                    "--evidence-json",
                    str(root / "web_api.json"),
                    "--spoken-phrase",
                    web_spoken_phrase,
                ],
                "cwd": str(runtime_root),
                "evidence_json": str(root / "web_api.json"),
                "workspace": str(web_ws),
                "audit_log": str(root / "web_audit.jsonl"),
                "console_log": str(root / "web-console-live.log"),
                "console_command": [sys.executable, "openclaw_cli.py", "web-console", "--host", "127.0.0.1", "--port", "8790"],
                "console_pid": 1234,
                "console_returncode": 0,
                "restart_base_url": "http://127.0.0.1:8791",
                "restart_console_log": str(root / "web-console-live-restart.log"),
                "restart_console_command": [sys.executable, "openclaw_cli.py", "web-console", "--host", "127.0.0.1", "--port", "8791"],
                "restart_console_pid": 1235,
                "restart_console_returncode": 0,
                "base_url": "http://127.0.0.1:8790",
            },
        },
        "preflight": {
            "status": "ok",
            "credential_diagnostics": {"api_key_kind": "configured", "app_id_kind": "configured"},
            "configured_mic_device": "auto",
            "selected_mic_device": "plughw:2,0",
            "http_url": OFFICIAL_TINGWU_HTTP_URL,
            "ws_url": OFFICIAL_TINGWU_WS_URL,
            "endpoint_probe": official_endpoint_probe(),
            "sample_rate": 16000,
            "capture_seconds": 3,
            "capture_probe": {
                "status": "available",
                "selected_device": "plughw:2,0",
                "sample_rate": 16000,
                "duration_seconds": 3,
                "audio_bytes": 96000,
                "audio_rms": 80,
                "audio_peak": 900,
                "message": "ready",
            },
            "workspace_dir": str(preflight_ws),
            "audit_log_path": str(write_audit_log(root / "preflight_audit.jsonl", ["tingwu.preflight"])),
            "checks": {
                "tingwu_api_key_configured": True,
                "tingwu_app_id_configured": True,
                "dashscope_tingwu_import": True,
                "provider_available": True,
                "official_tingwu_endpoint": True,
                "microphone_selected": True,
                "real_microphone_device": True,
                "microphone_capture_device_matches": True,
                "microphone_capture_open": True,
                "microphone_capture_signal": True,
                "workspace_writable": True,
                "audit_writable": True,
            },
        },
        "direct_provider": {
            "status": "ok",
            "credential_diagnostics": {"api_key_kind": "configured", "app_id_kind": "configured"},
            "meeting_id": "direct-meeting",
            "workspace_dir": str(direct_ws),
            "audit_log_path": str(write_audit_log(root / "direct_audit.jsonl", direct_audit_actions)),
            "output_dir": str(direct_output_dir),
            "transcript_path": str(write_file(direct_transcript, f"Live transcript text. {direct_spoken_phrase}.")),
            "audio_path": str(write_wav(direct_audio)),
            "minutes_path": str(write_file(direct_minutes, "Direct Tingwu structured summary.\nTingwu AI minutes.")),
            "stop_status_before_fetch": "stopped",
            "minutes_path_after_stop": "",
            "status_after_fetch": "completed",
            "session_path": str(write_json(direct_session, {
                "meeting_id": "direct-meeting",
                "status": "completed",
                "task_id": "direct-provider-task",
                "output_dir": str(direct_output_dir),
                "transcript_path": str(direct_transcript),
                "audio_path": str(direct_audio),
                "minutes_path": str(direct_minutes),
                "audio_bytes": 384000,
                "audio_seconds": 12,
                "sample_rate": 16000,
                "audio_format": "pcm",
                "websocket_audio_frames": 120,
                "audio_rms": 100,
                "audio_peak": 1000,
                "transcript": [
                    {
                        "timestamp": "2026-05-27T00:00:00+00:00",
                        "speaker": "Speaker 1",
                        "text": f"Live transcript text. {direct_spoken_phrase}.",
                        "final": True,
                    }
                ],
                "tingwu_http_operations": direct_http_operations,
                "ai_minutes": {"source_data_id": "direct-provider-task", "minutes_task_id": "direct-minutes-task"},
            })),
            "configured_mic_device": "auto",
            "selected_mic_device": "plughw:2,0",
            "mic_probe": {
                "status": "available",
                "configured_device": "auto",
                "selected_device": "plughw:2,0",
                "message": "ready",
                "capture_probe": {
                    "status": "available",
                    "selected_device": "plughw:2,0",
                    "sample_rate": 16000,
                    "duration_seconds": 3,
                    "audio_bytes": 96000,
                    "audio_rms": 80,
                    "audio_peak": 900,
                    "message": "ready",
                },
            },
            "endpoint_probe": official_endpoint_probe(),
            "provider_task_id": "direct-provider-task",
            "ai_minutes_source_data_id": "direct-provider-task",
            "ai_minutes_task_id": "direct-minutes-task",
            "tingwu_minutes_summary": "Direct Tingwu structured summary.",
            "tingwu_minutes_summary_source": "ParagraphSummary",
            "tingwu_minutes_structured": True,
            "audio_seconds": 12,
            "audio_bytes": 384000,
            "sample_rate": 16000,
            "audio_format": "pcm",
            "websocket_audio_frames": 120,
            "audio_rms": 100,
            "audio_peak": 1000,
            "final_count": 1,
            "transcript_items": 1,
            "spoken_phrase": direct_spoken_phrase,
            "spoken_phrase_detected": True,
            "redaction_canaries": direct_canaries,
            "provider_events": ["meeting_started", "websocket_started", "meeting_stopped"],
            "tingwu_http_operations": direct_http_operations,
            "checks": {key: True for key in direct_checks},
        },
        "web_api": {
            "status": "ok",
            "credential_diagnostics": {"api_key_kind": "configured", "app_id_kind": "configured"},
            "meeting_id": "web-meeting",
            "workspace_dir": str(web_ws),
            "audit_log_path": str(write_audit_log(root / "web_audit.jsonl", web_audit_actions)),
            "output_dir": str(web_output_dir),
            "transcript_path": str(write_file(web_transcript, f"Web live transcript text. {web_spoken_phrase}.")),
            "audio_path": str(write_wav(web_audio)),
            "session_path": str(web_session),
            "tingwu_minutes_path": str(write_file(web_tingwu_minutes, "Web Tingwu structured summary.\nWeb Tingwu AI minutes.")),
            "openclaw_minutes_path": web_followup_paths["openclaw_minutes"],
            "stop_status_before_fetch": "stopped",
            "provider_status_before_fetch": "stopped",
            "minutes_path_after_stop": "",
            "active_fetch_minutes_probe": {
                "status_code": 409,
                "ok": False,
                "error_code": "meeting_not_stopped",
                "status": "running",
            },
            "status_after_fetch": "completed",
            "allowed_roots_probe": {
                "path": str(root / "outside_allowed_roots_transcript.md"),
                "status_code": 403,
                "ok": False,
                "error_code": "blocked",
                "audit_visible": True,
            },
            "manifest_path": str(write_json(web_manifest, {
                "meeting_id": "web-meeting",
                "provider": "tongyi_tingwu",
                "transcript_path": str(web_transcript),
                "audio": {"path": str(web_audio), "seconds": 12, "bytes": 384000, "sample_rate": 16000, "format": "pcm", "rms": 100, "peak": 1000},
                "tingwu_minutes_path": str(web_tingwu_minutes),
                "openclaw_minutes_path": web_followup_paths["openclaw_minutes"],
                "tingwu_http_operations": web_http_operations,
                "outputs": [
                    {"path": str(web_transcript), "workspace_path": workspace_path(web_transcript, web_ws), "type": "markdown", "exists": True, "inside_workspace": True},
                    {"path": str(web_audio), "workspace_path": workspace_path(web_audio, web_ws), "type": "wav", "exists": True, "inside_workspace": True},
                    {"path": str(web_tingwu_minutes), "workspace_path": workspace_path(web_tingwu_minutes, web_ws), "type": "markdown", "exists": True, "inside_workspace": True},
                    {"path": web_followup_paths["openclaw_minutes"], "workspace_path": workspace_path(Path(web_followup_paths["openclaw_minutes"]), web_ws), "type": "markdown", "exists": True, "inside_workspace": True},
                    {"path": web_followup_paths["transcript_export"], "workspace_path": workspace_path(Path(web_followup_paths["transcript_export"]), web_ws), "type": "json", "exists": True, "inside_workspace": True},
                    {"path": web_followup_paths["email_draft"], "workspace_path": workspace_path(Path(web_followup_paths["email_draft"]), web_ws), "type": "markdown", "exists": True, "inside_workspace": True},
                    {"path": web_followup_paths["reminders"], "workspace_path": workspace_path(Path(web_followup_paths["reminders"]), web_ws), "type": "json", "exists": True, "inside_workspace": True},
                    {"path": web_followup_paths["projection_confirmation"], "workspace_path": workspace_path(Path(web_followup_paths["projection_confirmation"]), web_ws), "type": "markdown", "exists": True, "inside_workspace": True},
                    {"path": web_followup_paths["decisions"], "workspace_path": workspace_path(Path(web_followup_paths["decisions"]), web_ws), "type": "json", "exists": True, "inside_workspace": True},
                    {"path": web_followup_paths["action_items"], "workspace_path": workspace_path(Path(web_followup_paths["action_items"]), web_ws), "type": "json", "exists": True, "inside_workspace": True},
                    {"path": str(web_session), "workspace_path": workspace_path(web_session, web_ws), "type": "json", "exists": True, "inside_workspace": True},
                ],
            })),
            "decisions_path": web_followup_paths["decisions"],
            "action_items_path": web_followup_paths["action_items"],
            "followup_output_paths": web_followup_paths,
            "task_file": str(write_json(web_task, {
                "task_id": "web-task",
                "status": "completed",
                "output": {
                    "meeting_id": "web-meeting",
                    "websocket_audio_frames": 120,
                    "audio_seconds": 12,
                    "final_count": 1,
                    "tingwu_http_operations": web_http_operations,
                    "monitor": {
                        "websocket_audio_frames": 120,
                        "audio_seconds": 12,
                        "final_count": 1,
                    },
                    "events": [
                        {"event": "meeting_started"},
                        {"event": "websocket_started"},
                        {"event": "meeting_stopped"},
                    ],
                },
            })),
            "notifications_path": str(write_json(web_notifications, {
                "items": [
                    {"event": "meeting_realtime_stopped", "payload": {"meeting_id": "web-meeting"}},
                    {"event": "meeting_ai_minutes_ready", "payload": {"meeting_id": "web-meeting"}},
                ]
            })),
            "web_task_id": "web-task",
            "configured_mic_device": "auto",
            "selected_mic_device": "plughw:2,0",
            "mic_probe": {
                "status": "available",
                "configured_device": "auto",
                "selected_device": "plughw:2,0",
                "message": "ready",
                "capture_probe": {
                    "status": "available",
                    "selected_device": "plughw:2,0",
                    "sample_rate": 16000,
                    "duration_seconds": 3,
                    "audio_bytes": 96000,
                    "audio_rms": 80,
                    "audio_peak": 900,
                    "message": "ready",
                },
            },
            "endpoint_probe": official_endpoint_probe(),
            "full_control_gate": {
                "request_status": "waiting_confirmation",
                "request_id_present": True,
                "confirm_status": "backend_missing",
                "confirm_full_control_enabled": False,
                "security_permission_mode_after_confirm": "sandbox",
                "security_full_control_enabled_after_confirm": False,
                "cancel_status": "blocked",
                "audit_visible": True,
            },
            "task_monitor": {
                "task_id": "web-task",
                "websocket_audio_frames": 120,
                "audio_seconds": 12,
                "final_count": 1,
            },
            "meeting_jobs_restore": {
                "job_id": "web-job",
                "step_names": [
                    "realtime_capture",
                    "import_transcript",
                    "minutes",
                    "decisions",
                    "action_items",
                    "followup",
                    "reminders",
                    "projection_confirmation",
                ],
                "tingwu_minutes_path": str(web_tingwu_minutes),
                "openclaw_minutes_path": web_followup_paths["openclaw_minutes"],
                "tingwu_minutes_summary": "Web Tingwu structured summary.",
                "tingwu_minutes_summary_source": "ParagraphSummary",
                "tingwu_minutes_structured": True,
                "websocket_audio_frames": 120,
                "followup_output_keys": sorted(web_followup_paths),
                "events_compacted": True,
            },
            "web_restart_recovery": {
                "job_id": "web-job",
                "step_names": [
                    "realtime_capture",
                    "import_transcript",
                    "minutes",
                    "decisions",
                    "action_items",
                    "followup",
                    "reminders",
                    "projection_confirmation",
                ],
                "tingwu_minutes_path": str(web_tingwu_minutes),
                "openclaw_minutes_path": web_followup_paths["openclaw_minutes"],
                "tingwu_minutes_summary": "Web Tingwu structured summary.",
                "tingwu_minutes_summary_source": "ParagraphSummary",
                "tingwu_minutes_structured": True,
                "websocket_audio_frames": 120,
                "followup_output_keys": sorted(web_followup_paths),
                "events_compacted": True,
                "task_monitor_frames": 120,
                "task_monitor_audio_seconds": 12,
                "assistant_notifications": ["meeting_realtime_stopped", "meeting_ai_minutes_ready"],
            },
            "provider_task_id": "web-provider-task",
            "ai_minutes_source_data_id": "web-provider-task",
            "ai_minutes_task_id": "web-minutes-task",
            "tingwu_minutes_summary": "Web Tingwu structured summary.",
            "tingwu_minutes_summary_source": "ParagraphSummary",
            "tingwu_minutes_structured": True,
            "audio_seconds": 12,
            "audio_bytes": 384000,
            "sample_rate": 16000,
            "audio_format": "pcm",
            "websocket_audio_frames": 120,
            "audio_rms": 100,
            "audio_peak": 1000,
            "final_count": 1,
            "transcript_items": 1,
            "spoken_phrase": web_spoken_phrase,
            "spoken_phrase_detected": True,
            "redaction_canaries": web_canaries,
            "provider_events": ["meeting_started", "websocket_started", "meeting_stopped"],
            "tingwu_http_operations": web_http_operations,
            "notifications": ["meeting_realtime_stopped", "meeting_ai_minutes_ready"],
            "steps": {
                "realtime_capture": "completed",
                "import_transcript": "completed",
                "minutes": "completed",
                "decisions": "waiting_confirmation",
                "action_items": "completed",
                "followup": "completed",
                "reminders": "completed",
                "projection_confirmation": "completed",
            },
            "checks": {key: True for key in web_checks},
        },
    }
    summary.update(
        goal_requirement_report(
            stage_status=summary["stage_status"],  # type: ignore[arg-type]
            stage_evidence={
                "preflight": summary["preflight"],
                "direct_provider": summary["direct_provider"],
                "web_api": summary["web_api"],
            },
            blockers=[],
        )
    )
    write_json(root / "preflight.json", summary["preflight"])
    write_json(root / "direct_provider.json", summary["direct_provider"])
    write_json(root / "web_api.json", summary["web_api"])
    return summary


def main() -> int:
    assert_live_verifiers_preserve_spoken_phrase_failure_evidence()
    assert_live_verifiers_preserve_start_failure_evidence()
    assert_live_verifiers_preserve_missing_credentials_evidence()
    with tempfile.TemporaryDirectory(prefix="lelamp-tingwu-evidence-audit-") as temp:
        root = Path(temp)
        assert_env_file_parser_is_safe(root)
        assert_env_file_placeholders_do_not_hit_cloud(root)
        summary_path = root / "summary.json"
        payload = full_summary(root)
        summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        goal_requirements = payload.get("goal_requirements") if isinstance(payload.get("goal_requirements"), list) else []
        assert_ok(
            "complete evidence includes proven goal requirement report",
            payload.get("goal_completion_ready") is True
            and len(goal_requirements) >= 10
            and all(isinstance(item, dict) and item.get("status") == "proven" for item in goal_requirements),
            payload.get("goal_requirements"),
        )

        result = run_audit(summary_path)
        assert_ok("complete evidence passes audit", result.returncode == 0, result.stdout + result.stderr)

        leaked_credential_diagnostic = full_summary(root / "leaked_credential_diagnostic_case")
        leaked_credential_diagnostic["goal_readiness"]["credential_diagnostics"] = {
            "api_key_kind": "sk_should_not_be_kind",
            "app_id_kind": "configured",
        }
        leaked_credential_diagnostic_path = root / "leaked_credential_diagnostic_summary.json"
        leaked_credential_diagnostic_path.write_text(json.dumps(leaked_credential_diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(leaked_credential_diagnostic_path)
        assert_ok("unsafe credential diagnostic value fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_goal_requirements = full_summary(root / "missing_goal_requirements_case")
        missing_goal_requirements.pop("goal_requirements", None)
        missing_goal_requirements_path = root / "missing_goal_requirements_summary.json"
        missing_goal_requirements_path.write_text(json.dumps(missing_goal_requirements, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_goal_requirements_path)
        assert_ok("missing goal requirement report fails audit", result.returncode == 1, result.stdout + result.stderr)

        partial_goal_requirement = full_summary(root / "partial_goal_requirement_case")
        partial_goal_requirement["goal_completion_ready"] = False
        partial_goal_requirement["goal_requirements"][0]["status"] = "partial"
        partial_goal_requirement["goal_requirements"][0]["missing_checks"] = ["web_api.all_required_steps"]
        partial_goal_requirement_path = root / "partial_goal_requirement_summary.json"
        partial_goal_requirement_path.write_text(json.dumps(partial_goal_requirement, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(partial_goal_requirement_path)
        assert_ok("partial goal requirement report fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_goal_readiness = full_summary(root / "mismatched_goal_readiness_case")
        mismatched_goal_readiness["goal_readiness"]["microphone_ready"] = False
        mismatched_goal_readiness_path = root / "mismatched_goal_readiness_summary.json"
        mismatched_goal_readiness_path.write_text(json.dumps(mismatched_goal_readiness, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_goal_readiness_path)
        assert_ok("mismatched goal readiness fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_stage_evidence = full_summary(root / "mismatched_stage_evidence_case")
        stage_evidence_path = Path(str(mismatched_stage_evidence["stage_runs"]["direct_provider"]["evidence_json"]))
        stage_evidence = json.loads(stage_evidence_path.read_text(encoding="utf-8"))
        stage_evidence["meeting_id"] = "wrong-meeting"
        write_json(stage_evidence_path, stage_evidence)
        mismatched_stage_evidence_path = root / "mismatched_stage_evidence_summary.json"
        mismatched_stage_evidence_path.write_text(json.dumps(mismatched_stage_evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_stage_evidence_path)
        assert_ok("mismatched stage evidence JSON fails file audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_stage_audio_metrics = full_summary(root / "mismatched_stage_audio_metrics_case")
        stage_evidence_path = Path(str(mismatched_stage_audio_metrics["stage_runs"]["web_api"]["evidence_json"]))
        stage_evidence = json.loads(stage_evidence_path.read_text(encoding="utf-8"))
        stage_evidence["websocket_audio_frames"] = 119
        write_json(stage_evidence_path, stage_evidence)
        mismatched_stage_audio_metrics_path = root / "mismatched_stage_audio_metrics_summary.json"
        mismatched_stage_audio_metrics_path.write_text(json.dumps(mismatched_stage_audio_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_stage_audio_metrics_path)
        assert_ok("mismatched stage audio metrics fail file audit", result.returncode == 1, result.stdout + result.stderr)

        corrupt_audio = full_summary(root / "corrupt_audio_case")
        Path(str(corrupt_audio["direct_provider"]["audio_path"])).write_text("not a wav", encoding="utf-8")
        corrupt_audio_path = root / "corrupt_audio.json"
        corrupt_audio_path.write_text(json.dumps(corrupt_audio, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(corrupt_audio_path)
        assert_ok("corrupt audio artifact fails file audit", result.returncode == 1, result.stdout + result.stderr)

        wrong_audio_format = full_summary(root / "wrong_audio_format_case")
        write_wav(Path(str(wrong_audio_format["web_api"]["audio_path"])), sample_rate=8000)
        wrong_audio_format_path = root / "wrong_audio_format_summary.json"
        wrong_audio_format_path.write_text(json.dumps(wrong_audio_format, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(wrong_audio_format_path)
        assert_ok("wrong audio WAV format fails file audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_audio_seconds = full_summary(root / "mismatched_audio_seconds_case")
        mismatched_audio_seconds["web_api"]["audio_seconds"] = 1
        mismatched_audio_seconds_path = root / "mismatched_audio_seconds_summary.json"
        mismatched_audio_seconds_path.write_text(json.dumps(mismatched_audio_seconds, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_audio_seconds_path)
        assert_ok("mismatched audio duration evidence fails file audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_audio_bytes = full_summary(root / "mismatched_audio_bytes_case")
        mismatched_audio_bytes["direct_provider"]["audio_bytes"] = 3200
        mismatched_audio_bytes_path = root / "mismatched_audio_bytes_summary.json"
        mismatched_audio_bytes_path.write_text(json.dumps(mismatched_audio_bytes, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_audio_bytes_path)
        assert_ok("mismatched audio byte evidence fails file audit", result.returncode == 1, result.stdout + result.stderr)

        empty_transcript = full_summary(root / "empty_transcript_case")
        Path(str(empty_transcript["web_api"]["transcript_path"])).write_text("", encoding="utf-8")
        empty_transcript_path = root / "empty_transcript_summary.json"
        empty_transcript_path.write_text(json.dumps(empty_transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(empty_transcript_path)
        assert_ok("empty transcript artifact fails file audit", result.returncode == 1, result.stdout + result.stderr)

        missing_spoken_phrase = full_summary(root / "missing_spoken_phrase_case")
        Path(str(missing_spoken_phrase["web_api"]["transcript_path"])).write_text("Web live transcript text without the required phrase.", encoding="utf-8")
        missing_spoken_phrase_path = root / "missing_spoken_phrase_summary.json"
        missing_spoken_phrase_path.write_text(json.dumps(missing_spoken_phrase, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_spoken_phrase_path)
        assert_ok("missing spoken phrase artifact fails file audit", result.returncode == 1, result.stdout + result.stderr)

        false_spoken_phrase = full_summary(root / "false_spoken_phrase_case")
        false_spoken_phrase["direct_provider"]["spoken_phrase_detected"] = False
        false_spoken_phrase_path = root / "false_spoken_phrase_summary.json"
        false_spoken_phrase_path.write_text(json.dumps(false_spoken_phrase, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(false_spoken_phrase_path)
        assert_ok("false spoken phrase evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_transcript_metric = full_summary(root / "missing_transcript_metric_case")
        missing_transcript_metric["direct_provider"]["final_count"] = 0
        missing_transcript_metric["direct_provider"]["transcript_items"] = 0
        missing_transcript_metric_path = root / "missing_transcript_metric_summary.json"
        missing_transcript_metric_path.write_text(json.dumps(missing_transcript_metric, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_transcript_metric_path)
        assert_ok("missing transcript callback metrics fail file audit", result.returncode == 1, result.stdout + result.stderr)

        tiny_transcript = full_summary(root / "tiny_transcript_case")
        Path(str(tiny_transcript["web_api"]["transcript_path"])).write_text("ok", encoding="utf-8")
        tiny_transcript_path = root / "tiny_transcript_summary.json"
        tiny_transcript_path.write_text(json.dumps(tiny_transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(tiny_transcript_path)
        assert_ok("too-short transcript artifact fails file audit", result.returncode == 1, result.stdout + result.stderr)

        invalid_manifest = full_summary(root / "invalid_manifest_case")
        Path(str(invalid_manifest["web_api"]["manifest_path"])).write_text("{not-json", encoding="utf-8")
        invalid_manifest_path = root / "invalid_manifest_summary.json"
        invalid_manifest_path.write_text(json.dumps(invalid_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(invalid_manifest_path)
        assert_ok("invalid JSON artifact fails file audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_session = full_summary(root / "mismatched_session_case")
        session_path = Path(str(mismatched_session["direct_provider"]["session_path"]))
        session_payload = json.loads(session_path.read_text(encoding="utf-8"))
        session_payload["meeting_id"] = "wrong-meeting"
        write_json(session_path, session_payload)
        mismatched_session_path = root / "mismatched_session_summary.json"
        mismatched_session_path.write_text(json.dumps(mismatched_session, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_session_path)
        assert_ok("mismatched session JSON fails file audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_session_audio_metrics = full_summary(root / "mismatched_session_audio_metrics_case")
        session_path = Path(str(mismatched_session_audio_metrics["web_api"]["session_path"]))
        session_payload = json.loads(session_path.read_text(encoding="utf-8"))
        session_payload["audio_seconds"] = 11
        write_json(session_path, session_payload)
        mismatched_session_audio_metrics_path = root / "mismatched_session_audio_metrics_summary.json"
        mismatched_session_audio_metrics_path.write_text(json.dumps(mismatched_session_audio_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_session_audio_metrics_path)
        assert_ok("mismatched session audio metrics fail file audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_session_http_operations = full_summary(root / "mismatched_session_http_operations_case")
        session_path = Path(str(mismatched_session_http_operations["web_api"]["session_path"]))
        session_payload = json.loads(session_path.read_text(encoding="utf-8"))
        session_payload["tingwu_http_operations"] = []
        write_json(session_path, session_payload)
        mismatched_session_http_operations_path = root / "mismatched_session_http_operations_summary.json"
        mismatched_session_http_operations_path.write_text(json.dumps(mismatched_session_http_operations, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_session_http_operations_path)
        assert_ok("mismatched session Tingwu HTTP operations fail file audit", result.returncode == 1, result.stdout + result.stderr)

        missing_ai_minutes_metadata = full_summary(root / "missing_ai_minutes_metadata_case")
        missing_ai_minutes_metadata["direct_provider"].pop("ai_minutes_task_id", None)
        missing_ai_minutes_metadata_path = root / "missing_ai_minutes_metadata_summary.json"
        missing_ai_minutes_metadata_path.write_text(json.dumps(missing_ai_minutes_metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_ai_minutes_metadata_path)
        assert_ok("missing AI minutes task metadata fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_tingwu_http_operations = full_summary(root / "missing_tingwu_http_operations_case")
        missing_tingwu_http_operations["direct_provider"].pop("tingwu_http_operations", None)
        missing_tingwu_http_operations_path = root / "missing_tingwu_http_operations_summary.json"
        missing_tingwu_http_operations_path.write_text(json.dumps(missing_tingwu_http_operations, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_tingwu_http_operations_path)
        assert_ok("missing Tingwu HTTP operations fail audit", result.returncode == 1, result.stdout + result.stderr)

        private_tingwu_http_endpoint = full_summary(root / "private_tingwu_http_endpoint_case")
        private_tingwu_http_endpoint["web_api"]["tingwu_http_operations"][0]["endpoint"] = "http://127.0.0.1:8000/fake"
        private_tingwu_http_endpoint_path = root / "private_tingwu_http_endpoint_summary.json"
        private_tingwu_http_endpoint_path.write_text(json.dumps(private_tingwu_http_endpoint, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(private_tingwu_http_endpoint_path)
        assert_ok("private Tingwu HTTP operation endpoint fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_tingwu_minutes_create = full_summary(root / "mismatched_tingwu_minutes_create_case")
        for item in mismatched_tingwu_minutes_create["web_api"]["tingwu_http_operations"]:
            if item.get("action") == "CreateRealtimeMinutesTask":
                item["request_data_id"] = "other-provider-task"
        mismatched_tingwu_minutes_create_path = root / "mismatched_tingwu_minutes_create_summary.json"
        mismatched_tingwu_minutes_create_path.write_text(json.dumps(mismatched_tingwu_minutes_create, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_tingwu_minutes_create_path)
        assert_ok("mismatched Tingwu minutes CreateTask source fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_tingwu_get_task = full_summary(root / "mismatched_tingwu_get_task_case")
        for item in mismatched_tingwu_get_task["web_api"]["tingwu_http_operations"]:
            if item.get("action") == "GetTask":
                item["request_data_id"] = "other-minutes-task"
        mismatched_tingwu_get_task_path = root / "mismatched_tingwu_get_task_summary.json"
        mismatched_tingwu_get_task_path.write_text(json.dumps(mismatched_tingwu_get_task, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_tingwu_get_task_path)
        assert_ok("mismatched Tingwu GetTask dataId fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_tingwu_structured_minutes = full_summary(root / "missing_tingwu_structured_minutes_case")
        missing_tingwu_structured_minutes["direct_provider"].pop("tingwu_minutes_summary", None)
        missing_tingwu_structured_minutes_path = root / "missing_tingwu_structured_minutes_summary.json"
        missing_tingwu_structured_minutes_path.write_text(json.dumps(missing_tingwu_structured_minutes, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_tingwu_structured_minutes_path)
        assert_ok("missing direct structured Tingwu minutes summary fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_web_tingwu_structured_minutes = full_summary(root / "missing_web_tingwu_structured_minutes_case")
        missing_web_tingwu_structured_minutes["web_api"].pop("tingwu_minutes_summary", None)
        missing_web_tingwu_structured_minutes_path = root / "missing_web_tingwu_structured_minutes_summary.json"
        missing_web_tingwu_structured_minutes_path.write_text(json.dumps(missing_web_tingwu_structured_minutes, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_web_tingwu_structured_minutes_path)
        assert_ok("missing web structured Tingwu minutes summary fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_tingwu_structured_minutes = full_summary(root / "mismatched_tingwu_structured_minutes_case")
        Path(str(mismatched_tingwu_structured_minutes["direct_provider"]["minutes_path"])).write_text("Different saved Tingwu minutes.", encoding="utf-8")
        mismatched_tingwu_structured_minutes_path = root / "mismatched_tingwu_structured_minutes_summary.json"
        mismatched_tingwu_structured_minutes_path.write_text(json.dumps(mismatched_tingwu_structured_minutes, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_tingwu_structured_minutes_path)
        assert_ok("mismatched direct structured Tingwu minutes summary fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_web_tingwu_structured_minutes = full_summary(root / "mismatched_web_tingwu_structured_minutes_case")
        Path(str(mismatched_web_tingwu_structured_minutes["web_api"]["tingwu_minutes_path"])).write_text("Different saved Tingwu minutes.", encoding="utf-8")
        mismatched_web_tingwu_structured_minutes_path = root / "mismatched_web_tingwu_structured_minutes_summary.json"
        mismatched_web_tingwu_structured_minutes_path.write_text(json.dumps(mismatched_web_tingwu_structured_minutes, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_web_tingwu_structured_minutes_path)
        assert_ok("mismatched web structured Tingwu minutes summary fails audit", result.returncode == 1, result.stdout + result.stderr)

        raw_payload_tingwu_summary = full_summary(root / "raw_payload_tingwu_summary_case")
        raw_payload_tingwu_summary["direct_provider"]["tingwu_minutes_summary_source"] = "raw_payload"
        raw_payload_tingwu_summary["direct_provider"]["tingwu_minutes_structured"] = False
        raw_payload_tingwu_summary_path = root / "raw_payload_tingwu_summary.json"
        raw_payload_tingwu_summary_path.write_text(json.dumps(raw_payload_tingwu_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(raw_payload_tingwu_summary_path)
        assert_ok("raw-payload Tingwu summary fallback fails audit", result.returncode == 1, result.stdout + result.stderr)

        non_summary_source_tingwu_summary = full_summary(root / "non_summary_source_tingwu_summary_case")
        non_summary_source_tingwu_summary["web_api"]["tingwu_minutes_summary_source"] = "KeySentences"
        non_summary_source_tingwu_summary_path = root / "non_summary_source_tingwu_summary.json"
        non_summary_source_tingwu_summary_path.write_text(json.dumps(non_summary_source_tingwu_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(non_summary_source_tingwu_summary_path)
        assert_ok("non-summary Tingwu source fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_meeting_jobs_restore = full_summary(root / "missing_meeting_jobs_restore_case")
        missing_meeting_jobs_restore["web_api"].pop("meeting_jobs_restore", None)
        missing_meeting_jobs_restore_path = root / "missing_meeting_jobs_restore_summary.json"
        missing_meeting_jobs_restore_path.write_text(json.dumps(missing_meeting_jobs_restore, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_meeting_jobs_restore_path)
        assert_ok("missing Meeting UI restore evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_meeting_jobs_restore = full_summary(root / "mismatched_meeting_jobs_restore_case")
        mismatched_meeting_jobs_restore["web_api"]["meeting_jobs_restore"]["tingwu_minutes_summary"] = "Different summary"
        mismatched_meeting_jobs_restore_path = root / "mismatched_meeting_jobs_restore_summary.json"
        mismatched_meeting_jobs_restore_path.write_text(json.dumps(mismatched_meeting_jobs_restore, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_meeting_jobs_restore_path)
        assert_ok("mismatched Meeting UI restore evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        unstructured_meeting_jobs_restore = full_summary(root / "unstructured_meeting_jobs_restore_case")
        unstructured_meeting_jobs_restore["web_api"]["meeting_jobs_restore"]["tingwu_minutes_structured"] = False
        unstructured_meeting_jobs_restore_path = root / "unstructured_meeting_jobs_restore_summary.json"
        unstructured_meeting_jobs_restore_path.write_text(json.dumps(unstructured_meeting_jobs_restore, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(unstructured_meeting_jobs_restore_path)
        assert_ok("unstructured Meeting UI restore evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_web_restart_recovery = full_summary(root / "missing_web_restart_recovery_case")
        missing_web_restart_recovery["web_api"].pop("web_restart_recovery", None)
        missing_web_restart_recovery_path = root / "missing_web_restart_recovery_summary.json"
        missing_web_restart_recovery_path.write_text(json.dumps(missing_web_restart_recovery, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_web_restart_recovery_path)
        assert_ok("missing Web restart recovery evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_web_restart_recovery = full_summary(root / "mismatched_web_restart_recovery_case")
        mismatched_web_restart_recovery["web_api"]["web_restart_recovery"]["task_monitor_frames"] = 0
        mismatched_web_restart_recovery_path = root / "mismatched_web_restart_recovery_summary.json"
        mismatched_web_restart_recovery_path.write_text(json.dumps(mismatched_web_restart_recovery, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_web_restart_recovery_path)
        assert_ok("mismatched Web restart recovery evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        reused_ai_minutes_task_id = full_summary(root / "reused_ai_minutes_task_id_case")
        reused_ai_minutes_task_id["web_api"]["ai_minutes_task_id"] = reused_ai_minutes_task_id["web_api"]["ai_minutes_source_data_id"]
        reused_stage_path = Path(str(reused_ai_minutes_task_id["stage_runs"]["web_api"]["evidence_json"]))
        write_json(reused_stage_path, reused_ai_minutes_task_id["web_api"])
        reused_ai_minutes_task_id_path = root / "reused_ai_minutes_task_id_summary.json"
        reused_ai_minutes_task_id_path.write_text(json.dumps(reused_ai_minutes_task_id, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(reused_ai_minutes_task_id_path)
        assert_ok("reused AI minutes task id fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_manifest = full_summary(root / "mismatched_manifest_case")
        manifest_path = Path(str(mismatched_manifest["web_api"]["manifest_path"]))
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_payload["outputs"] = []
        write_json(manifest_path, manifest_payload)
        mismatched_manifest_path = root / "mismatched_manifest_summary.json"
        mismatched_manifest_path.write_text(json.dumps(mismatched_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_manifest_path)
        assert_ok("mismatched manifest outputs fail file audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_manifest_audio_metrics = full_summary(root / "mismatched_manifest_audio_metrics_case")
        manifest_path = Path(str(mismatched_manifest_audio_metrics["web_api"]["manifest_path"]))
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_payload["audio"]["bytes"] = 3200
        write_json(manifest_path, manifest_payload)
        mismatched_manifest_audio_metrics_path = root / "mismatched_manifest_audio_metrics_summary.json"
        mismatched_manifest_audio_metrics_path.write_text(json.dumps(mismatched_manifest_audio_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_manifest_audio_metrics_path)
        assert_ok("mismatched manifest audio metrics fail file audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_manifest_tingwu_http_operations = full_summary(root / "mismatched_manifest_tingwu_http_operations_case")
        manifest_path = Path(str(mismatched_manifest_tingwu_http_operations["web_api"]["manifest_path"]))
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_payload["tingwu_http_operations"] = []
        write_json(manifest_path, manifest_payload)
        mismatched_manifest_tingwu_http_operations_path = root / "mismatched_manifest_tingwu_http_operations_summary.json"
        mismatched_manifest_tingwu_http_operations_path.write_text(json.dumps(mismatched_manifest_tingwu_http_operations, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_manifest_tingwu_http_operations_path)
        assert_ok("mismatched manifest Tingwu HTTP operations fail file audit", result.returncode == 1, result.stdout + result.stderr)

        missing_followup_output = full_summary(root / "missing_followup_output_case")
        Path(str(missing_followup_output["web_api"]["followup_output_paths"]["projection_confirmation"])).unlink()
        missing_followup_output_path = root / "missing_followup_output_summary.json"
        missing_followup_output_path.write_text(json.dumps(missing_followup_output, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_followup_output_path)
        assert_ok("missing follow-up output artifact fails file audit", result.returncode == 1, result.stdout + result.stderr)

        missing_manifest_followup_output = full_summary(root / "missing_manifest_followup_output_case")
        manifest_path = Path(str(missing_manifest_followup_output["web_api"]["manifest_path"]))
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        projection_workspace_path = workspace_path(
            Path(str(missing_manifest_followup_output["web_api"]["followup_output_paths"]["projection_confirmation"])),
            Path(str(missing_manifest_followup_output["web_api"]["workspace_dir"])),
        )
        manifest_payload["outputs"] = [
            item for item in manifest_payload.get("outputs", [])
            if not isinstance(item, dict) or item.get("workspace_path") != projection_workspace_path
        ]
        write_json(manifest_path, manifest_payload)
        missing_manifest_followup_output_path = root / "missing_manifest_followup_output_summary.json"
        missing_manifest_followup_output_path.write_text(json.dumps(missing_manifest_followup_output, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_manifest_followup_output_path)
        assert_ok("manifest missing follow-up output index fails file audit", result.returncode == 1, result.stdout + result.stderr)

        missing_task_monitor = full_summary(root / "missing_task_monitor_case")
        task_path = Path(str(missing_task_monitor["web_api"]["task_file"]))
        task_payload = json.loads(task_path.read_text(encoding="utf-8"))
        task_output = task_payload.get("output") if isinstance(task_payload.get("output"), dict) else {}
        task_output["monitor"] = {"websocket_audio_frames": 0, "audio_seconds": 0, "final_count": 1}
        task_payload["output"] = task_output
        write_json(task_path, task_payload)
        missing_task_monitor_path = root / "missing_task_monitor_summary.json"
        missing_task_monitor_path.write_text(json.dumps(missing_task_monitor, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_task_monitor_path)
        assert_ok("missing task monitor metrics fail file audit", result.returncode == 1, result.stdout + result.stderr)

        missing_task_monitor_evidence = full_summary(root / "missing_task_monitor_evidence_case")
        missing_task_monitor_evidence["web_api"].pop("task_monitor", None)
        missing_task_monitor_evidence_path = root / "missing_task_monitor_evidence_summary.json"
        missing_task_monitor_evidence_path.write_text(json.dumps(missing_task_monitor_evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_task_monitor_evidence_path)
        assert_ok("missing task monitor evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_task_monitor_evidence = full_summary(root / "mismatched_task_monitor_evidence_case")
        mismatched_task_monitor_evidence["web_api"]["task_monitor"]["websocket_audio_frames"] = 119
        mismatched_task_monitor_evidence_path = root / "mismatched_task_monitor_evidence_summary.json"
        mismatched_task_monitor_evidence_path.write_text(json.dumps(mismatched_task_monitor_evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_task_monitor_evidence_path)
        assert_ok("mismatched task monitor evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_task_monitor = full_summary(root / "mismatched_task_monitor_case")
        task_path = Path(str(mismatched_task_monitor["web_api"]["task_file"]))
        task_payload = json.loads(task_path.read_text(encoding="utf-8"))
        task_output = task_payload.get("output") if isinstance(task_payload.get("output"), dict) else {}
        task_monitor = task_output.get("monitor") if isinstance(task_output.get("monitor"), dict) else {}
        task_monitor["websocket_audio_frames"] = 119
        task_output["monitor"] = task_monitor
        task_payload["output"] = task_output
        write_json(task_path, task_payload)
        mismatched_task_monitor_path = root / "mismatched_task_monitor_summary.json"
        mismatched_task_monitor_path.write_text(json.dumps(mismatched_task_monitor, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_task_monitor_path)
        assert_ok("mismatched task monitor metrics fail file audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_task_tingwu_http_operations = full_summary(root / "mismatched_task_tingwu_http_operations_case")
        task_path = Path(str(mismatched_task_tingwu_http_operations["web_api"]["task_file"]))
        task_payload = json.loads(task_path.read_text(encoding="utf-8"))
        task_output = task_payload.get("output") if isinstance(task_payload.get("output"), dict) else {}
        task_output["tingwu_http_operations"] = []
        task_payload["output"] = task_output
        write_json(task_path, task_payload)
        mismatched_task_tingwu_http_operations_path = root / "mismatched_task_tingwu_http_operations_summary.json"
        mismatched_task_tingwu_http_operations_path.write_text(json.dumps(mismatched_task_tingwu_http_operations, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_task_tingwu_http_operations_path)
        assert_ok("mismatched task Tingwu HTTP operations fail file audit", result.returncode == 1, result.stdout + result.stderr)

        missing_audit_action = full_summary(root / "missing_audit_action_case")
        write_audit_log(Path(str(missing_audit_action["web_api"]["audit_log_path"])), ["tingwu.meeting_start", "tingwu.audio_save"])
        missing_audit_action_path = root / "missing_audit_action_summary.json"
        missing_audit_action_path.write_text(json.dumps(missing_audit_action, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_audit_action_path)
        assert_ok("missing required audit action fails file audit", result.returncode == 1, result.stdout + result.stderr)

        invalid_audit_jsonl = full_summary(root / "invalid_audit_jsonl_case")
        Path(str(invalid_audit_jsonl["direct_provider"]["audit_log_path"])).write_text("{not-json\n", encoding="utf-8")
        invalid_audit_jsonl_path = root / "invalid_audit_jsonl_summary.json"
        invalid_audit_jsonl_path.write_text(json.dumps(invalid_audit_jsonl, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(invalid_audit_jsonl_path)
        assert_ok("invalid audit JSONL fails file audit", result.returncode == 1, result.stdout + result.stderr)

        leaked_redaction_canary = full_summary(root / "leaked_redaction_canary_case")
        Path(str(leaked_redaction_canary["direct_provider"]["transcript_path"])).write_text(
            f"Live transcript text. {leaked_redaction_canary['direct_provider']['spoken_phrase']}. direct-live-title-token",
            encoding="utf-8",
        )
        leaked_redaction_canary_path = root / "leaked_redaction_canary_summary.json"
        leaked_redaction_canary_path.write_text(json.dumps(leaked_redaction_canary, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(leaked_redaction_canary_path)
        assert_ok("leaked redaction canary fails file audit", result.returncode == 1, result.stdout + result.stderr)

        leaked_summary_canary = full_summary(root / "leaked_summary_canary_case")
        leaked_summary_canary["diagnostic_leak"] = "web-live-title-token"
        leaked_summary_canary_path = root / "leaked_summary_canary_summary.json"
        leaked_summary_canary_path.write_text(json.dumps(leaked_summary_canary, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(leaked_summary_canary_path)
        assert_ok("leaked summary redaction canary fails file audit", result.returncode == 1, result.stdout + result.stderr)

        leaked_stage_evidence_canary = full_summary(root / "leaked_stage_evidence_canary_case")
        stage_evidence_path = Path(str(leaked_stage_evidence_canary["stage_runs"]["web_api"]["evidence_json"]))
        stage_evidence = json.loads(stage_evidence_path.read_text(encoding="utf-8"))
        stage_evidence["diagnostic_leak"] = "web-live-title-token"
        write_json(stage_evidence_path, stage_evidence)
        leaked_stage_evidence_canary_path = root / "leaked_stage_evidence_canary_summary.json"
        leaked_stage_evidence_canary_path.write_text(json.dumps(leaked_stage_evidence_canary, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(leaked_stage_evidence_canary_path)
        assert_ok("leaked stage evidence redaction canary fails file audit", result.returncode == 1, result.stdout + result.stderr)

        leaked_console_log_canary = full_summary(root / "leaked_console_log_canary_case")
        Path(str(leaked_console_log_canary["stage_runs"]["web_api"]["console_log"])).write_text("web-live-title-token\n", encoding="utf-8")
        leaked_console_log_canary_path = root / "leaked_console_log_canary_summary.json"
        leaked_console_log_canary_path.write_text(json.dumps(leaked_console_log_canary, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(leaked_console_log_canary_path)
        assert_ok("leaked console log redaction canary fails file audit", result.returncode == 1, result.stdout + result.stderr)

        leaked_preflight_workspace_canary = full_summary(root / "leaked_preflight_workspace_canary_case")
        preflight_workspace = Path(str(leaked_preflight_workspace_canary["stage_runs"]["preflight"]["workspace"]))
        write_file(preflight_workspace / "preflight-leak.txt", "web-live-title-token\n")
        leaked_preflight_workspace_canary_path = root / "leaked_preflight_workspace_canary_summary.json"
        leaked_preflight_workspace_canary_path.write_text(json.dumps(leaked_preflight_workspace_canary, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(leaked_preflight_workspace_canary_path)
        assert_ok("leaked preflight workspace redaction canary fails file audit", result.returncode == 1, result.stdout + result.stderr)

        leaked_preflight_audit_canary = full_summary(root / "leaked_preflight_audit_canary_case")
        Path(str(leaked_preflight_audit_canary["stage_runs"]["preflight"]["audit_log"])).write_text("web-live-title-token\n", encoding="utf-8")
        leaked_preflight_audit_canary_path = root / "leaked_preflight_audit_canary_summary.json"
        leaked_preflight_audit_canary_path.write_text(json.dumps(leaked_preflight_audit_canary, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(leaked_preflight_audit_canary_path)
        assert_ok("leaked preflight audit redaction canary fails file audit", result.returncode == 1, result.stdout + result.stderr)

        missing_redaction_canaries = full_summary(root / "missing_redaction_canaries_case")
        missing_redaction_canaries["web_api"].pop("redaction_canaries", None)
        missing_redaction_canaries_stage = Path(str(missing_redaction_canaries["stage_runs"]["web_api"]["evidence_json"]))
        write_json(missing_redaction_canaries_stage, missing_redaction_canaries["web_api"])
        missing_redaction_canaries_path = root / "missing_redaction_canaries_summary.json"
        missing_redaction_canaries_path.write_text(json.dumps(missing_redaction_canaries, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_redaction_canaries_path)
        assert_ok("missing redaction canary declaration fails file audit", result.returncode == 1, result.stdout + result.stderr)

        missing_full_control_gate = full_summary(root / "missing_full_control_gate_case")
        missing_full_control_gate["web_api"].pop("full_control_gate", None)
        missing_full_control_gate_path = root / "missing_full_control_gate_summary.json"
        missing_full_control_gate_path.write_text(json.dumps(missing_full_control_gate, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_full_control_gate_path)
        assert_ok("missing full_control gate evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        enabled_full_control_gate = full_summary(root / "enabled_full_control_gate_case")
        enabled_full_control_gate["web_api"]["full_control_gate"]["confirm_full_control_enabled"] = True
        enabled_full_control_gate["web_api"]["full_control_gate"]["security_full_control_enabled_after_confirm"] = True
        enabled_full_control_gate_path = root / "enabled_full_control_gate_summary.json"
        enabled_full_control_gate_path.write_text(json.dumps(enabled_full_control_gate, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(enabled_full_control_gate_path)
        assert_ok("enabled full_control gate fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_real_mic = full_summary(root / "missing_real_mic_case")
        missing_real_mic["direct_provider"].pop("mic_probe", None)
        missing_real_mic["direct_provider"]["checks"].pop("real_microphone_device", None)
        missing_real_mic_path = root / "missing_real_mic_summary.json"
        missing_real_mic_path.write_text(json.dumps(missing_real_mic, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_real_mic_path)
        assert_ok("missing real microphone evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        fake_real_mic = full_summary(root / "fake_real_mic_case")
        fake_real_mic["web_api"]["configured_mic_device"] = "fake-mic"
        fake_real_mic["web_api"]["selected_mic_device"] = "fake-mic"
        fake_real_mic["web_api"]["mic_probe"] = {
            "status": "available",
            "configured_device": "fake-mic",
            "selected_device": "fake-mic",
            "message": "fake microphone capture preflight",
        }
        fake_real_mic["web_api"]["checks"]["real_microphone_device"] = False
        fake_real_mic_path = root / "fake_real_mic_summary.json"
        fake_real_mic_path.write_text(json.dumps(fake_real_mic, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(fake_real_mic_path)
        assert_ok("fake microphone evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        unresolved_selected_mic = full_summary(root / "unresolved_selected_mic_case")
        unresolved_selected_mic["direct_provider"]["selected_mic_device"] = "auto"
        unresolved_selected_mic["direct_provider"]["mic_probe"]["selected_device"] = "auto"
        unresolved_selected_mic["direct_provider"]["mic_probe"]["capture_probe"]["selected_device"] = "auto"
        unresolved_selected_mic_path = root / "unresolved_selected_mic_summary.json"
        unresolved_selected_mic_path.write_text(json.dumps(unresolved_selected_mic, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(unresolved_selected_mic_path)
        assert_ok("unresolved auto microphone evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        unavailable_real_mic_probe = full_summary(root / "unavailable_real_mic_probe_case")
        unavailable_real_mic_probe["direct_provider"]["mic_probe"]["status"] = "unavailable"
        unavailable_real_mic_probe["direct_provider"]["mic_probe"]["message"] = "Configured microphone was not listed by arecord -l."
        unavailable_real_mic_probe["direct_provider"]["checks"]["real_microphone_device"] = False
        unavailable_real_mic_probe_path = root / "unavailable_real_mic_probe_summary.json"
        unavailable_real_mic_probe_path.write_text(json.dumps(unavailable_real_mic_probe, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(unavailable_real_mic_probe_path)
        assert_ok("unavailable microphone probe fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_live_capture_probe = full_summary(root / "missing_live_capture_probe_case")
        missing_live_capture_probe["web_api"]["mic_probe"].pop("capture_probe", None)
        missing_live_capture_probe_path = root / "missing_live_capture_probe_summary.json"
        missing_live_capture_probe_path.write_text(json.dumps(missing_live_capture_probe, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_live_capture_probe_path)
        assert_ok("missing live microphone capture probe fails audit", result.returncode == 1, result.stdout + result.stderr)

        silent_live_capture_probe = full_summary(root / "silent_live_capture_probe_case")
        silent_live_capture_probe["direct_provider"]["mic_probe"]["capture_probe"]["audio_rms"] = 0
        silent_live_capture_probe["direct_provider"]["mic_probe"]["capture_probe"]["audio_peak"] = 0
        silent_live_capture_probe_path = root / "silent_live_capture_probe_summary.json"
        silent_live_capture_probe_path.write_text(json.dumps(silent_live_capture_probe, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(silent_live_capture_probe_path)
        assert_ok("silent live microphone capture probe fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_preflight_endpoint = full_summary(root / "missing_preflight_endpoint_case")
        missing_preflight_endpoint["preflight"].pop("endpoint_probe", None)
        missing_preflight_endpoint["preflight"]["checks"].pop("official_tingwu_endpoint", None)
        missing_preflight_endpoint_stage = Path(str(missing_preflight_endpoint["stage_runs"]["preflight"]["evidence_json"]))
        missing_preflight_endpoint_stage_payload = json.loads(missing_preflight_endpoint_stage.read_text(encoding="utf-8"))
        missing_preflight_endpoint_stage_payload.pop("endpoint_probe", None)
        missing_preflight_endpoint_stage_payload["checks"].pop("official_tingwu_endpoint", None)
        write_json(missing_preflight_endpoint_stage, missing_preflight_endpoint_stage_payload)
        missing_preflight_endpoint_path = root / "missing_preflight_endpoint_summary.json"
        missing_preflight_endpoint_path.write_text(json.dumps(missing_preflight_endpoint, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_preflight_endpoint_path)
        assert_ok("missing preflight endpoint evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        private_direct_endpoint = full_summary(root / "private_direct_endpoint_case")
        private_direct_endpoint["direct_provider"]["endpoint_probe"] = {
            "http_url": "http://127.0.0.1:9000/api/v1/services/aigc/multimodal-generation/generation",
            "ws_url": OFFICIAL_TINGWU_WS_URL,
            "http": {
                "url": "http://127.0.0.1:9000/api/v1/services/aigc/multimodal-generation/generation",
                "scheme": "http",
                "host": "127.0.0.1",
                "path": "/api/v1/services/aigc/multimodal-generation/generation",
                "origin": "http://127.0.0.1:9000",
            },
            "ws": official_endpoint_probe()["ws"],
            "official_dashscope": False,
        }
        private_direct_endpoint["direct_provider"]["checks"]["official_tingwu_endpoint"] = False
        private_direct_endpoint_stage = Path(str(private_direct_endpoint["stage_runs"]["direct_provider"]["evidence_json"]))
        write_json(private_direct_endpoint_stage, private_direct_endpoint["direct_provider"])
        private_direct_endpoint_path = root / "private_direct_endpoint_summary.json"
        private_direct_endpoint_path.write_text(json.dumps(private_direct_endpoint, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(private_direct_endpoint_path)
        assert_ok("private direct endpoint evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        fake_web_ws_endpoint = full_summary(root / "fake_web_ws_endpoint_case")
        fake_web_ws_endpoint["web_api"]["endpoint_probe"] = {
            "http_url": OFFICIAL_TINGWU_HTTP_URL,
            "ws_url": "ws://fake-tingwu.local/realtime",
            "http": official_endpoint_probe()["http"],
            "ws": {
                "url": "ws://fake-tingwu.local/realtime",
                "scheme": "ws",
                "host": "fake-tingwu.local",
                "path": "/realtime",
                "origin": "ws://fake-tingwu.local",
            },
            "official_dashscope": False,
        }
        fake_web_ws_endpoint["web_api"]["checks"]["official_tingwu_endpoint"] = False
        fake_web_ws_endpoint_stage = Path(str(fake_web_ws_endpoint["stage_runs"]["web_api"]["evidence_json"]))
        write_json(fake_web_ws_endpoint_stage, fake_web_ws_endpoint["web_api"])
        fake_web_ws_endpoint_path = root / "fake_web_ws_endpoint_summary.json"
        fake_web_ws_endpoint_path.write_text(json.dumps(fake_web_ws_endpoint, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(fake_web_ws_endpoint_path)
        assert_ok("fake web WebSocket endpoint evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_allowed_roots_probe = full_summary(root / "missing_allowed_roots_probe_case")
        missing_allowed_roots_probe["web_api"].pop("allowed_roots_probe", None)
        missing_allowed_roots_probe_path = root / "missing_allowed_roots_probe_summary.json"
        missing_allowed_roots_probe_path.write_text(json.dumps(missing_allowed_roots_probe, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_allowed_roots_probe_path)
        assert_ok("missing allowed-roots probe evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        allowed_roots_probe_not_blocked = full_summary(root / "allowed_roots_probe_not_blocked_case")
        allowed_roots_probe_not_blocked["web_api"]["allowed_roots_probe"]["status_code"] = 200
        allowed_roots_probe_not_blocked["web_api"]["allowed_roots_probe"]["ok"] = True
        allowed_roots_probe_not_blocked["web_api"]["checks"]["allowed_roots_block_enforced"] = False
        allowed_roots_probe_not_blocked_path = root / "allowed_roots_probe_not_blocked_summary.json"
        allowed_roots_probe_not_blocked_path.write_text(json.dumps(allowed_roots_probe_not_blocked, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(allowed_roots_probe_not_blocked_path)
        assert_ok("unblocked allowed-roots probe fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_stop_sequence = full_summary(root / "missing_stop_sequence_case")
        missing_stop_sequence["direct_provider"].pop("stop_status_before_fetch", None)
        missing_stop_sequence_path = root / "missing_stop_sequence_summary.json"
        missing_stop_sequence_path.write_text(json.dumps(missing_stop_sequence, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_stop_sequence_path)
        assert_ok("missing stop-before-fetch sequence evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        minutes_fetched_before_stop = full_summary(root / "minutes_fetched_before_stop_case")
        minutes_fetched_before_stop["web_api"]["minutes_path_after_stop"] = minutes_fetched_before_stop["web_api"]["tingwu_minutes_path"]
        minutes_fetched_before_stop_path = root / "minutes_fetched_before_stop_summary.json"
        minutes_fetched_before_stop_path.write_text(json.dumps(minutes_fetched_before_stop, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(minutes_fetched_before_stop_path)
        assert_ok("minutes path present before fetch fails audit", result.returncode == 1, result.stdout + result.stderr)

        incomplete_fetch_sequence = full_summary(root / "incomplete_fetch_sequence_case")
        incomplete_fetch_sequence["direct_provider"]["status_after_fetch"] = "stopped"
        incomplete_fetch_sequence_path = root / "incomplete_fetch_sequence_summary.json"
        incomplete_fetch_sequence_path.write_text(json.dumps(incomplete_fetch_sequence, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(incomplete_fetch_sequence_path)
        assert_ok("incomplete fetch sequence fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_active_fetch_probe = full_summary(root / "missing_active_fetch_probe_case")
        missing_active_fetch_probe["web_api"].pop("active_fetch_minutes_probe", None)
        missing_active_fetch_probe_path = root / "missing_active_fetch_probe_summary.json"
        missing_active_fetch_probe_path.write_text(json.dumps(missing_active_fetch_probe, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_active_fetch_probe_path)
        assert_ok("missing active fetch-minutes block probe fails audit", result.returncode == 1, result.stdout + result.stderr)

        unblocked_active_fetch_probe = full_summary(root / "unblocked_active_fetch_probe_case")
        unblocked_active_fetch_probe["web_api"]["active_fetch_minutes_probe"]["status_code"] = 200
        unblocked_active_fetch_probe["web_api"]["active_fetch_minutes_probe"]["ok"] = True
        unblocked_active_fetch_probe_path = root / "unblocked_active_fetch_probe_summary.json"
        unblocked_active_fetch_probe_path.write_text(json.dumps(unblocked_active_fetch_probe, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(unblocked_active_fetch_probe_path)
        assert_ok("unblocked active fetch-minutes probe fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_start_event = json.loads(summary_path.read_text(encoding="utf-8"))
        missing_start_event["direct_provider"]["provider_events"] = ["websocket_started", "meeting_stopped"]
        missing_start_event_path = root / "missing_start_event.json"
        missing_start_event_path.write_text(json.dumps(missing_start_event, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_start_event_path)
        assert_ok("missing provider start event fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_stop_event = json.loads(summary_path.read_text(encoding="utf-8"))
        missing_stop_event["web_api"]["provider_events"] = ["meeting_started", "websocket_started"]
        missing_stop_event_path = root / "missing_stop_event.json"
        missing_stop_event_path.write_text(json.dumps(missing_stop_event, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_stop_event_path)
        assert_ok("missing provider stop event fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_websocket_event = json.loads(summary_path.read_text(encoding="utf-8"))
        missing_websocket_event["direct_provider"]["provider_events"] = ["meeting_started", "meeting_stopped"]
        missing_websocket_event_path = root / "missing_websocket_event.json"
        missing_websocket_event_path.write_text(json.dumps(missing_websocket_event, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_websocket_event_path)
        assert_ok("missing WebSocket stream event fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_stage_runs = json.loads(summary_path.read_text(encoding="utf-8"))
        missing_stage_runs.pop("stage_runs", None)
        missing_stage_runs_path = root / "missing_stage_runs.json"
        missing_stage_runs_path.write_text(json.dumps(missing_stage_runs, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_stage_runs_path)
        assert_ok("missing stage_runs fails audit", result.returncode == 1, result.stdout + result.stderr)

        incomplete_stage_runs = json.loads(summary_path.read_text(encoding="utf-8"))
        incomplete_stage_runs["stage_runs"]["web_api"].pop("console_log", None)
        incomplete_stage_runs_path = root / "incomplete_stage_runs.json"
        incomplete_stage_runs_path.write_text(json.dumps(incomplete_stage_runs, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(incomplete_stage_runs_path)
        assert_ok("incomplete web stage diagnostics fail audit", result.returncode == 1, result.stdout + result.stderr)

        incomplete_preflight_stage = json.loads(summary_path.read_text(encoding="utf-8"))
        incomplete_preflight_stage["stage_runs"]["preflight"].pop("capture_seconds", None)
        incomplete_preflight_stage_path = root / "incomplete_preflight_stage.json"
        incomplete_preflight_stage_path.write_text(json.dumps(incomplete_preflight_stage, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(incomplete_preflight_stage_path)
        assert_ok("incomplete preflight stage diagnostics fail audit", result.returncode == 1, result.stdout + result.stderr)

        wrong_preflight_command = json.loads(summary_path.read_text(encoding="utf-8"))
        wrong_preflight_command["stage_runs"]["preflight"]["command"] = [sys.executable, "../scripts/not_preflight.py"]
        wrong_preflight_command_path = root / "wrong_preflight_command.json"
        wrong_preflight_command_path.write_text(json.dumps(wrong_preflight_command, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(wrong_preflight_command_path)
        assert_ok("wrong preflight command fails audit", result.returncode == 1, result.stdout + result.stderr)

        wrong_direct_command = json.loads(summary_path.read_text(encoding="utf-8"))
        wrong_direct_command["stage_runs"]["direct_provider"]["command"] = [sys.executable, "../scripts/preflight_tingwu_live.py"]
        wrong_direct_command_path = root / "wrong_direct_command.json"
        wrong_direct_command_path.write_text(json.dumps(wrong_direct_command, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(wrong_direct_command_path)
        assert_ok("wrong direct verifier command fails audit", result.returncode == 1, result.stdout + result.stderr)

        wrong_web_command = json.loads(summary_path.read_text(encoding="utf-8"))
        wrong_web_command["stage_runs"]["web_api"]["command"] = [sys.executable, "../scripts/verify_tingwu_live.py"]
        wrong_web_command_path = root / "wrong_web_command.json"
        wrong_web_command_path.write_text(json.dumps(wrong_web_command, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(wrong_web_command_path)
        assert_ok("wrong web verifier command fails audit", result.returncode == 1, result.stdout + result.stderr)

        wrong_console_command = json.loads(summary_path.read_text(encoding="utf-8"))
        wrong_console_command["stage_runs"]["web_api"]["console_command"] = [sys.executable, "openclaw_cli.py", "project-screen"]
        wrong_console_command_path = root / "wrong_console_command.json"
        wrong_console_command_path.write_text(json.dumps(wrong_console_command, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(wrong_console_command_path)
        assert_ok("wrong web console command fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_console_command_port = json.loads(summary_path.read_text(encoding="utf-8"))
        console_command = list(mismatched_console_command_port["stage_runs"]["web_api"]["console_command"])
        console_command[console_command.index("--port") + 1] = "8799"
        mismatched_console_command_port["stage_runs"]["web_api"]["console_command"] = console_command
        mismatched_console_command_port_path = root / "mismatched_console_command_port.json"
        mismatched_console_command_port_path.write_text(json.dumps(mismatched_console_command_port, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_console_command_port_path)
        assert_ok("mismatched web console command port fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_restart_console_command_port = json.loads(summary_path.read_text(encoding="utf-8"))
        restart_console_command = list(mismatched_restart_console_command_port["stage_runs"]["web_api"]["restart_console_command"])
        restart_console_command[restart_console_command.index("--port") + 1] = "8799"
        mismatched_restart_console_command_port["stage_runs"]["web_api"]["restart_console_command"] = restart_console_command
        mismatched_restart_console_command_port_path = root / "mismatched_restart_console_command_port.json"
        mismatched_restart_console_command_port_path.write_text(json.dumps(mismatched_restart_console_command_port, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_restart_console_command_port_path)
        assert_ok("mismatched restart web console command port fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_command_workspace = json.loads(summary_path.read_text(encoding="utf-8"))
        command = list(mismatched_command_workspace["stage_runs"]["direct_provider"]["command"])
        command[command.index("--workspace") + 1] = str(root / "other_direct_workspace")
        mismatched_command_workspace["stage_runs"]["direct_provider"]["command"] = command
        mismatched_command_workspace_path = root / "mismatched_command_workspace.json"
        mismatched_command_workspace_path.write_text(json.dumps(mismatched_command_workspace, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_command_workspace_path)
        assert_ok("mismatched stage command workspace fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_command_evidence = json.loads(summary_path.read_text(encoding="utf-8"))
        command = list(mismatched_command_evidence["stage_runs"]["web_api"]["command"])
        command[command.index("--evidence-json") + 1] = str(root / "other_web_evidence.json")
        mismatched_command_evidence["stage_runs"]["web_api"]["command"] = command
        mismatched_command_evidence_path = root / "mismatched_command_evidence.json"
        mismatched_command_evidence_path.write_text(json.dumps(mismatched_command_evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_command_evidence_path)
        assert_ok("mismatched stage command evidence path fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_command_capture_seconds = json.loads(summary_path.read_text(encoding="utf-8"))
        command = list(mismatched_command_capture_seconds["stage_runs"]["preflight"]["command"])
        command[command.index("--capture-seconds") + 1] = "1"
        mismatched_command_capture_seconds["stage_runs"]["preflight"]["command"] = command
        mismatched_command_capture_seconds_path = root / "mismatched_command_capture_seconds.json"
        mismatched_command_capture_seconds_path.write_text(json.dumps(mismatched_command_capture_seconds, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_command_capture_seconds_path)
        assert_ok("mismatched preflight command capture seconds fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_command_base_url = json.loads(summary_path.read_text(encoding="utf-8"))
        command = list(mismatched_command_base_url["stage_runs"]["web_api"]["command"])
        command[command.index("--base-url") + 1] = "http://127.0.0.1:9999"
        mismatched_command_base_url["stage_runs"]["web_api"]["command"] = command
        mismatched_command_base_url_path = root / "mismatched_command_base_url.json"
        mismatched_command_base_url_path.write_text(json.dumps(mismatched_command_base_url, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_command_base_url_path)
        assert_ok("mismatched web command base URL fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_command_seconds = json.loads(summary_path.read_text(encoding="utf-8"))
        command = list(mismatched_command_seconds["stage_runs"]["direct_provider"]["command"])
        command[command.index("--seconds") + 1] = "6"
        mismatched_command_seconds["stage_runs"]["direct_provider"]["command"] = command
        mismatched_command_seconds_path = root / "mismatched_command_seconds.json"
        mismatched_command_seconds_path.write_text(json.dumps(mismatched_command_seconds, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_command_seconds_path)
        assert_ok("mismatched direct command seconds fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_command_spoken_phrase = json.loads(summary_path.read_text(encoding="utf-8"))
        command = list(mismatched_command_spoken_phrase["stage_runs"]["web_api"]["command"])
        command[command.index("--spoken-phrase") + 1] = "不同验收短语"
        mismatched_command_spoken_phrase["stage_runs"]["web_api"]["command"] = command
        mismatched_command_spoken_phrase_path = root / "mismatched_command_spoken_phrase.json"
        mismatched_command_spoken_phrase_path.write_text(json.dumps(mismatched_command_spoken_phrase, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_command_spoken_phrase_path)
        assert_ok("mismatched web command spoken phrase fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_preflight_evidence = full_summary(root / "mismatched_preflight_evidence_case")
        preflight_evidence_path = Path(str(mismatched_preflight_evidence["stage_runs"]["preflight"]["evidence_json"]))
        preflight_evidence_payload = json.loads(preflight_evidence_path.read_text(encoding="utf-8"))
        preflight_evidence_payload["capture_seconds"] = 1
        preflight_evidence_path.write_text(json.dumps(preflight_evidence_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        mismatched_preflight_evidence_path = root / "mismatched_preflight_evidence.json"
        mismatched_preflight_evidence_path.write_text(json.dumps(mismatched_preflight_evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_preflight_evidence_path)
        assert_ok("mismatched preflight stage evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        skipped_preflight_capture = json.loads(summary_path.read_text(encoding="utf-8"))
        skipped_preflight_capture["preflight"]["capture_probe"] = {"status": "skipped", "reason": "skip_capture"}
        skipped_preflight_capture_path = root / "skipped_preflight_capture.json"
        skipped_preflight_capture_path.write_text(json.dumps(skipped_preflight_capture, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(skipped_preflight_capture_path)
        assert_ok("skipped preflight capture fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_preflight_capture = json.loads(summary_path.read_text(encoding="utf-8"))
        mismatched_preflight_capture["preflight"]["capture_probe"]["selected_device"] = "plughw:9,9"
        mismatched_preflight_capture_path = root / "mismatched_preflight_capture.json"
        mismatched_preflight_capture_path.write_text(json.dumps(mismatched_preflight_capture, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_preflight_capture_path)
        assert_ok("mismatched preflight capture device fails audit", result.returncode == 1, result.stdout + result.stderr)

        mismatched_preflight_capture_seconds = json.loads(summary_path.read_text(encoding="utf-8"))
        mismatched_preflight_capture_seconds["preflight"]["capture_probe"]["duration_seconds"] = 1
        mismatched_preflight_capture_seconds_path = root / "mismatched_preflight_capture_seconds.json"
        mismatched_preflight_capture_seconds_path.write_text(json.dumps(mismatched_preflight_capture_seconds, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(mismatched_preflight_capture_seconds_path)
        assert_ok("mismatched preflight capture duration fails audit", result.returncode == 1, result.stdout + result.stderr)

        fake_preflight_capture = json.loads(summary_path.read_text(encoding="utf-8"))
        fake_preflight_capture["preflight"]["selected_mic_device"] = "fake-mic"
        fake_preflight_capture["preflight"]["capture_probe"]["selected_device"] = "fake-mic"
        fake_preflight_capture["preflight"]["capture_probe"]["message"] = "fake microphone capture preflight"
        fake_preflight_capture["preflight"]["checks"]["real_microphone_device"] = False
        fake_preflight_capture_path = root / "fake_preflight_capture.json"
        fake_preflight_capture_path.write_text(json.dumps(fake_preflight_capture, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(fake_preflight_capture_path)
        assert_ok("fake preflight capture fails audit", result.returncode == 1, result.stdout + result.stderr)

        unresolved_preflight_capture = json.loads(summary_path.read_text(encoding="utf-8"))
        unresolved_preflight_capture["preflight"]["selected_mic_device"] = "auto"
        unresolved_preflight_capture["preflight"]["capture_probe"]["selected_device"] = "auto"
        unresolved_preflight_capture_path = root / "unresolved_preflight_capture.json"
        unresolved_preflight_capture_path.write_text(json.dumps(unresolved_preflight_capture, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(unresolved_preflight_capture_path)
        assert_ok("unresolved preflight auto microphone fails audit", result.returncode == 1, result.stdout + result.stderr)

        silent_preflight_capture = json.loads(summary_path.read_text(encoding="utf-8"))
        silent_preflight_capture["preflight"]["capture_probe"]["audio_rms"] = 0
        silent_preflight_capture["preflight"]["capture_probe"]["audio_peak"] = 0
        silent_preflight_capture_path = root / "silent_preflight_capture.json"
        silent_preflight_capture_path.write_text(json.dumps(silent_preflight_capture, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(silent_preflight_capture_path)
        assert_ok("silent preflight capture fails audit", result.returncode == 1, result.stdout + result.stderr)

        empty_preflight_capture = json.loads(summary_path.read_text(encoding="utf-8"))
        empty_preflight_capture["preflight"]["capture_probe"]["audio_bytes"] = 0
        empty_preflight_capture_path = root / "empty_preflight_capture.json"
        empty_preflight_capture_path.write_text(json.dumps(empty_preflight_capture, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(empty_preflight_capture_path)
        assert_ok("empty preflight capture fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_preflight_signal_check = json.loads(summary_path.read_text(encoding="utf-8"))
        missing_preflight_signal_check["preflight"]["checks"].pop("microphone_capture_signal", None)
        missing_preflight_signal_check_path = root / "missing_preflight_signal_check.json"
        missing_preflight_signal_check_path.write_text(json.dumps(missing_preflight_signal_check, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_preflight_signal_check_path)
        assert_ok("missing preflight signal check fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_preflight_real_mic_check = json.loads(summary_path.read_text(encoding="utf-8"))
        missing_preflight_real_mic_check["preflight"]["checks"].pop("real_microphone_device", None)
        missing_preflight_real_mic_check_path = root / "missing_preflight_real_mic_check.json"
        missing_preflight_real_mic_check_path.write_text(json.dumps(missing_preflight_real_mic_check, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_preflight_real_mic_check_path)
        assert_ok("missing preflight real microphone check fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_preflight_capture_device_match_check = json.loads(summary_path.read_text(encoding="utf-8"))
        missing_preflight_capture_device_match_check["preflight"]["checks"].pop("microphone_capture_device_matches", None)
        missing_preflight_capture_device_match_check_path = root / "missing_preflight_capture_device_match_check.json"
        missing_preflight_capture_device_match_check_path.write_text(json.dumps(missing_preflight_capture_device_match_check, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_preflight_capture_device_match_check_path)
        assert_ok("missing preflight capture device match check fails audit", result.returncode == 1, result.stdout + result.stderr)

        partial = dict(payload)
        partial["status"] = "partial"
        partial["acceptance_complete"] = False
        partial_path = root / "partial.json"
        partial_path.write_text(json.dumps(partial, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(partial_path)
        assert_ok("partial evidence fails audit", result.returncode == 1, result.stdout + result.stderr)

        missing_artifact = json.loads(summary_path.read_text(encoding="utf-8"))
        manifest_path = Path(str(missing_artifact["web_api"]["manifest_path"]))
        manifest_path.unlink()
        missing_path = root / "missing_artifact.json"
        missing_path.write_text(json.dumps(missing_artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_path)
        assert_ok("missing artifact fails file audit", result.returncode == 1, result.stdout + result.stderr)

        missing_notification_artifact_event = full_summary(root / "missing_notification_artifact_event_case")
        write_json(Path(str(missing_notification_artifact_event["web_api"]["notifications_path"])), {
            "items": [
                {"event": "meeting_ai_minutes_ready", "payload": {"meeting_id": "web-meeting"}},
            ]
        })
        missing_notification_artifact_event_path = root / "missing_notification_artifact_event_summary.json"
        missing_notification_artifact_event_path.write_text(json.dumps(missing_notification_artifact_event, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(missing_notification_artifact_event_path)
        assert_ok("missing assistant notification artifact event fails file audit", result.returncode == 1, result.stdout + result.stderr)

        outside = full_summary(root / "outside_case")
        outside_path = root / "outside_summary.json"
        outside["web_api"]["openclaw_minutes_path"] = str(write_file(root / "outside_minutes.md"))
        outside_path.write_text(json.dumps(outside, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(outside_path)
        assert_ok("outside workspace artifact fails file audit", result.returncode == 1, result.stdout + result.stderr)

        followup_outside_meeting_dir = full_summary(root / "followup_outside_meeting_dir_case")
        outside_workspace_followup = Path(str(followup_outside_meeting_dir["web_api"]["workspace_dir"])) / "outside_meeting_followup.md"
        followup_outside_meeting_dir["web_api"]["followup_output_paths"]["email_draft"] = str(write_file(outside_workspace_followup, "outside meeting dir"))
        followup_outside_meeting_dir_path = root / "followup_outside_meeting_dir_summary.json"
        followup_outside_meeting_dir_path.write_text(json.dumps(followup_outside_meeting_dir, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_audit(followup_outside_meeting_dir_path)
        assert_ok("follow-up output outside meeting directory fails file audit", result.returncode == 1, result.stdout + result.stderr)

        direct_preflight_missing_evidence = root / "direct_preflight_missing_credentials.json"
        result = run_preflight_missing_credentials(
            direct_preflight_missing_evidence,
            root / "direct_preflight_missing_workspace",
            root / "direct_preflight_missing_audit.jsonl",
        )
        assert_ok(
            "direct preflight writes evidence when credentials are missing",
            result.returncode == 1 and direct_preflight_missing_evidence.is_file(),
            result.stdout + result.stderr,
        )
        direct_preflight_missing = json.loads(direct_preflight_missing_evidence.read_text(encoding="utf-8"))
        checks = direct_preflight_missing.get("checks") if isinstance(direct_preflight_missing.get("checks"), dict) else {}
        assert_ok(
            "direct preflight missing-credentials evidence is structured",
            direct_preflight_missing.get("status") == "failed"
            and str(direct_preflight_missing.get("error") or "").startswith("Missing live Tingwu credentials")
            and checks.get("tingwu_api_key_configured") is False
            and checks.get("tingwu_app_id_configured") is False
            and "workspace_dir" in direct_preflight_missing
            and "audit_log_path" in direct_preflight_missing
            and "capture_seconds" in direct_preflight_missing,
            direct_preflight_missing,
        )
        assert_ok(
            "direct preflight missing-credentials evidence preserves local readiness probes",
            isinstance(direct_preflight_missing.get("provider_status"), dict)
            and isinstance(direct_preflight_missing.get("endpoint_probe"), dict)
            and isinstance(direct_preflight_missing.get("capture_probe"), dict)
            and "dashscope_tingwu_import" in checks
            and "official_tingwu_endpoint" in checks
            and "microphone_selected" in checks
            and "real_microphone_device" in checks
            and "microphone_capture_signal" in checks
            and "workspace_writable" in checks
            and "audit_writable" in checks,
            direct_preflight_missing,
        )

        direct_live_missing_evidence = root / "direct_live_missing_credentials.json"
        result = run_direct_live_missing_credentials(
            direct_live_missing_evidence,
            root / "direct_live_missing_workspace",
            root / "direct_live_missing_audit.jsonl",
        )
        assert_ok(
            "direct live verifier writes evidence when credentials are missing",
            result.returncode == 1 and direct_live_missing_evidence.is_file(),
            result.stdout + result.stderr,
        )
        direct_live_missing = json.loads(direct_live_missing_evidence.read_text(encoding="utf-8"))
        checks = direct_live_missing.get("checks") if isinstance(direct_live_missing.get("checks"), dict) else {}
        assert_ok(
            "direct live missing-credentials evidence is structured",
            direct_live_missing.get("status") == "failed"
            and direct_live_missing.get("mode") == "direct_provider"
            and checks.get("tingwu_api_key_configured") is False
            and checks.get("tingwu_app_id_configured") is False
            and checks.get("realtime_start_succeeded") is False
            and "workspace_dir" in direct_live_missing
            and "audit_log_path" in direct_live_missing
            and "endpoint_probe" in direct_live_missing
            and isinstance(direct_live_missing.get("redaction_canaries"), list),
            direct_live_missing,
        )

        web_live_missing_evidence = root / "web_live_missing_credentials.json"
        result = run_web_live_missing_credentials(
            web_live_missing_evidence,
            root / "web_live_missing_workspace",
            root / "web_live_missing_audit.jsonl",
            root / "web_live_missing_console.log",
        )
        assert_ok(
            "web live verifier writes evidence when credentials are missing",
            result.returncode == 1 and web_live_missing_evidence.is_file(),
            result.stdout + result.stderr,
        )
        web_live_missing = json.loads(web_live_missing_evidence.read_text(encoding="utf-8"))
        checks = web_live_missing.get("checks") if isinstance(web_live_missing.get("checks"), dict) else {}
        full_control_gate = web_live_missing.get("full_control_gate") if isinstance(web_live_missing.get("full_control_gate"), dict) else {}
        allowed_roots_probe = web_live_missing.get("allowed_roots_probe") if isinstance(web_live_missing.get("allowed_roots_probe"), dict) else {}
        assert_ok(
            "web live missing-credentials evidence is structured",
            web_live_missing.get("status") == "failed"
            and web_live_missing.get("mode") == "web_api"
            and checks.get("tingwu_api_key_configured") is False
            and checks.get("tingwu_app_id_configured") is False
            and checks.get("sandbox_visible") is True
            and checks.get("audit_only_visible") is True
            and checks.get("allowed_roots_block_enforced") is True
            and checks.get("full_control_gate_enforced") is True
            and checks.get("realtime_start_succeeded") is False
            and allowed_roots_probe.get("status_code") == 403
            and full_control_gate.get("confirm_status") == "backend_missing"
            and isinstance(web_live_missing.get("redaction_canaries"), list),
            web_live_missing,
        )

        missing_credentials_dir = root / "missing_credentials_evidence"
        result = run_live_suite_missing_credentials(missing_credentials_dir)
        missing_summary_path = missing_credentials_dir / "summary.json"
        assert_ok(
            "live suite writes partial summary when credentials are missing",
            result.returncode == 1 and missing_summary_path.is_file(),
            result.stdout + result.stderr,
        )
        missing_summary = json.loads(missing_summary_path.read_text(encoding="utf-8"))
        assert_ok(
            "missing-credentials summary marks all required live stages failed",
            missing_summary.get("status") == "partial"
            and missing_summary.get("acceptance_complete") is False
            and missing_summary.get("stage_status") == {"preflight": "failed", "direct_provider": "failed", "web_api": "failed"},
            missing_summary,
        )
        missing_stage_runs = missing_summary.get("stage_runs")
        missing_blockers = missing_summary.get("acceptance_blockers")
        missing_next_actions = missing_summary.get("next_actions")
        configure_action = next(
            (item for item in missing_next_actions if isinstance(item, dict) and item.get("id") == "configure_tingwu_credentials"),
            None,
        ) if isinstance(missing_next_actions, list) else None
        full_acceptance_action = next(
            (item for item in missing_next_actions if isinstance(item, dict) and item.get("id") == "run_full_live_acceptance"),
            None,
        ) if isinstance(missing_next_actions, list) else None
        assert_ok(
            "missing-credentials summary includes per-stage diagnostics",
            isinstance(missing_stage_runs, dict)
            and all(isinstance(missing_stage_runs.get(name), dict) for name in ("preflight", "direct_provider", "web_api"))
            and all(str(missing_stage_runs[name].get("error", "")).startswith("Missing live Tingwu credentials") for name in ("preflight", "direct_provider", "web_api")),
            missing_stage_runs,
        )
        missing_evidence_files = missing_summary.get("evidence_files")
        assert_ok(
            "missing-credentials summary writes per-stage evidence files",
            isinstance(missing_evidence_files, dict)
            and all(Path(str(missing_evidence_files.get(name) or "")).is_file() for name in ("preflight", "direct_provider", "web_api")),
            missing_evidence_files,
        )
        assert_ok(
            "missing-credentials stage diagnostics include rerunnable commands",
            isinstance(missing_stage_runs, dict)
            and all(isinstance(missing_stage_runs[name].get("command"), list) and missing_stage_runs[name].get("cwd") for name in ("preflight", "direct_provider", "web_api"))
            and "capture_seconds" in missing_stage_runs["preflight"]
            and "console_command" in missing_stage_runs["web_api"],
            missing_stage_runs,
        )
        assert_ok(
            "missing-credentials summary exposes acceptance blockers and spoken phrase",
            missing_summary.get("required_spoken_phrase") == DEFAULT_SPOKEN_PHRASE
            and isinstance(missing_blockers, list)
            and {item.get("stage") for item in missing_blockers if isinstance(item, dict)} == {"preflight", "direct_provider", "web_api"}
            and all(str(item.get("error", "")).startswith("Missing live Tingwu credentials") for item in missing_blockers if isinstance(item, dict))
            and all(isinstance(item.get("command"), list) and item.get("evidence_json") and item.get("cwd") for item in missing_blockers if isinstance(item, dict)),
            missing_summary,
        )
        assert_ok(
            "missing-credentials summary includes next acceptance actions",
            isinstance(missing_next_actions, list)
            and isinstance(configure_action, dict)
            and configure_action.get("status") == "required"
            and isinstance(configure_action.get("env"), dict)
            and configure_action["env"].get("ENV_FILE") == ".env.tingwu.local"
            and isinstance(configure_action.get("links"), list)
            and any(
                isinstance(link, dict)
                and link.get("url") == "https://bailian.console.aliyun.com/"
                for link in configure_action["links"]
            )
            and any(
                isinstance(link, dict)
                and "app-id" in str(link.get("url") or "").lower()
                for link in configure_action["links"]
            )
            and isinstance(configure_action.get("cwd"), str)
            and Path(str(configure_action.get("cwd"))).is_absolute()
            and isinstance(configure_action.get("command"), list)
            and isinstance(configure_action.get("audit_command"), list)
            and "--env-file" in configure_action["command"]
            and ".env.tingwu.local" in configure_action["command"]
            and isinstance(full_acceptance_action, dict)
            and isinstance(full_acceptance_action.get("env"), dict)
            and full_acceptance_action["env"].get("ENV_FILE") == ".env.tingwu.local"
            and isinstance(full_acceptance_action.get("links"), list)
            and any(
                isinstance(link, dict)
                and link.get("url") == "https://bailian.console.aliyun.com/"
                for link in full_acceptance_action["links"]
            )
            and isinstance(full_acceptance_action.get("cwd"), str)
            and Path(str(full_acceptance_action.get("cwd"))).is_absolute()
            and isinstance(full_acceptance_action.get("command"), list)
            and isinstance(full_acceptance_action.get("audit_command"), list)
            and "--env-file" in full_acceptance_action["command"]
            and ".env.tingwu.local" in full_acceptance_action["command"]
            and Path(str(full_acceptance_action["command"][1])).is_absolute()
            and Path(str(full_acceptance_action["audit_command"][1])).is_absolute()
            and str(full_acceptance_action["command"][1]).endswith("scripts/verify_tingwu_live_suite.py")
            and str(full_acceptance_action["audit_command"][1]).endswith("scripts/audit_tingwu_live_evidence.py")
            and "<" not in json.dumps(missing_next_actions, ensure_ascii=False)
            and ">" not in json.dumps(missing_next_actions, ensure_ascii=False),
            missing_next_actions,
        )
        missing_goal_requirements = missing_summary.get("goal_requirements") if isinstance(missing_summary.get("goal_requirements"), list) else []
        missing_goal_readiness = missing_summary.get("goal_readiness") if isinstance(missing_summary.get("goal_readiness"), dict) else {}
        assert_ok(
            "missing-credentials summary exposes goal coverage and local readiness",
            missing_summary.get("goal_completion_ready") is False
            and len(missing_goal_requirements) >= 10
            and any(isinstance(item, dict) and item.get("status") == "partial" for item in missing_goal_requirements)
            and missing_goal_readiness.get("credentials_configured") is False
            and "dashscope_tingwu_import" in missing_goal_readiness
            and "official_tingwu_endpoint" in missing_goal_readiness
            and "microphone_ready" in missing_goal_readiness
            and "microphone_capture_device_matches" in missing_goal_readiness
            and "workspace_writable" in missing_goal_readiness
            and "audit_writable" in missing_goal_readiness
            and "selected_mic_device" in missing_goal_readiness,
            {"goal_requirements": missing_goal_requirements, "goal_readiness": missing_goal_readiness},
        )
        result = run_audit(missing_summary_path, check_files=False)
        assert_ok("missing-credentials summary fails acceptance audit", result.returncode == 1, result.stdout + result.stderr)

        missing_env_file_dir = root / "missing_env_file_evidence"
        missing_env_file = root / "does_not_exist.tingwu.local"
        result = run_live_suite_missing_credentials(missing_env_file_dir, env_file=missing_env_file)
        missing_env_file_summary_path = missing_env_file_dir / "summary.json"
        assert_ok(
            "live suite writes partial summary when env file is missing",
            result.returncode == 1 and missing_env_file_summary_path.is_file(),
            result.stdout + result.stderr,
        )
        missing_env_file_summary = json.loads(missing_env_file_summary_path.read_text(encoding="utf-8"))
        missing_env_stage_runs = missing_env_file_summary.get("stage_runs")
        assert_ok(
            "missing-env-file summary preserves rerunnable diagnostics",
            missing_env_file_summary.get("status") == "partial"
            and missing_env_file_summary.get("acceptance_complete") is False
            and missing_env_file_summary.get("stage_status") == {"preflight": "failed", "direct_provider": "failed", "web_api": "failed"}
            and isinstance(missing_env_stage_runs, dict)
            and all(str(missing_env_stage_runs[name].get("error") or "").startswith("Tingwu env file not found") for name in ("preflight", "direct_provider", "web_api"))
            and all(missing_env_stage_runs[name].get("failure_status") == "env_file_error" for name in ("preflight", "direct_provider", "web_api")),
            missing_env_file_summary,
        )
        result = run_audit(missing_env_file_summary_path, check_files=False)
        assert_ok("missing-env-file summary fails acceptance audit", result.returncode == 1, result.stdout + result.stderr)

        skipped_live_stage_dir = root / "missing_credentials_skipped_stage_evidence"
        result = run_live_suite_missing_credentials(skipped_live_stage_dir, extra_args=["--skip-preflight"])
        skipped_summary_path = skipped_live_stage_dir / "summary.json"
        assert_ok(
            "live suite skip stage writes partial summary",
            result.returncode == 1 and skipped_summary_path.is_file(),
            result.stdout + result.stderr,
        )
        skipped_summary = json.loads(skipped_summary_path.read_text(encoding="utf-8"))
        assert_ok(
            "live suite skip stage cannot complete acceptance",
            skipped_summary.get("status") == "partial"
            and skipped_summary.get("acceptance_complete") is False
            and skipped_summary.get("stage_status") == {"preflight": "skipped", "direct_provider": "failed", "web_api": "failed"},
            skipped_summary,
        )
        result = run_audit(skipped_summary_path, check_files=False)
        assert_ok("skipped live stage summary fails acceptance audit", result.returncode == 1, result.stdout + result.stderr)

        merged = merge_stage_result(
            {"status": "failed", "returncode": 7, "command": ["verify"], "evidence_json": "/tmp/evidence.json"},
            {"status": "ok", "checks": {"would_pass": True}},
        )
        assert_ok("nonzero stage result overrides ok-looking evidence", merged.get("status") == "failed", merged)
        stage_error = merged.get("stage_error")
        assert_ok(
            "nonzero stage preserves command and evidence path",
            isinstance(stage_error, dict) and stage_error.get("returncode") == 7 and stage_error.get("command") == ["verify"] and stage_error.get("evidence_json") == "/tmp/evidence.json",
            stage_error,
        )

    print("smoke_tingwu_evidence_audit complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
