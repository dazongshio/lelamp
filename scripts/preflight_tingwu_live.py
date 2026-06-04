#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.parse
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "lelamp_runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

if sys.version_info < (3, 12):
    raise SystemExit("preflight_tingwu_live requires Python >= 3.12. Run it with lelamp_runtime/.venv/bin/python.")

from lelamp.office_agent.audit import AuditLogger  # noqa: E402
from lelamp.office_agent.config import OfficeAgentConfig, is_placeholder_tingwu_credential, tingwu_credential_kind  # noqa: E402
from lelamp.office_agent.workspace import Workspace  # noqa: E402

FAKE_MIC_DEVICES = {"fake-mic", "mock", "mock-mic"}
PLACEHOLDER_MIC_DEVICES = {"auto", "default", "pulse", "sysdefault"}
OFFICIAL_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
OFFICIAL_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


def assert_ok(name: str, condition: bool, details: object = "") -> None:
    if not condition:
        raise AssertionError(f"{name} failed: {details}")
    print(f"ok - {name}")


def write_evidence(path_value: str, payload: dict[str, object]) -> None:
    if not path_value:
        return
    path = Path(path_value).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"evidence_json={path}")


def preflight_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    workspace_root = (
        Path(args.workspace).expanduser().resolve()
        if args.workspace
        else Path(tempfile.mkdtemp(prefix="lelamp-tingwu-preflight-")) / "workspace"
    )
    audit_path = Path(args.audit_log).expanduser().resolve() if args.audit_log else workspace_root.parent / "audit.jsonl"
    return workspace_root, audit_path


def failure_payload(
    args: argparse.Namespace,
    error: object,
    *,
    workspace_root: Path,
    audit_path: Path,
    checks: dict[str, object],
    status: dict[str, object] | None = None,
    capture_probe: dict[str, object] | None = None,
    endpoint_probe: dict[str, object] | None = None,
) -> dict[str, object]:
    env_config = OfficeAgentConfig.from_env()
    capture_seconds = max(1, args.capture_seconds or env_config.tingwu_preflight_capture_seconds)
    return {
        "status": "failed",
        "error": str(error),
        "workspace_dir": str(workspace_root),
        "audit_log_path": str(audit_path),
        "configured_mic_device": env_config.mic_device,
        "mic_device": env_config.mic_device,
        "selected_mic_device": str((status or {}).get("selected_mic_device") or ""),
        "sample_rate": env_config.tingwu_sample_rate,
        "capture_seconds": capture_seconds,
        "audio_format": env_config.tingwu_audio_format,
        "http_url": (status or {}).get("http_url", ""),
        "ws_url": (status or {}).get("ws_url", ""),
        "endpoint_probe": endpoint_probe or {},
        "provider_status": status or {},
        "capture_probe": capture_probe or {},
        "checks": checks,
    }


def real_microphone_selected(selected: str, probe: dict[str, object]) -> bool:
    selected_normalized = str(selected or "").strip().lower()
    configured_normalized = str(probe.get("configured_device") or "").strip().lower()
    message = str(probe.get("message") or "").lower()
    return (
        bool(selected_normalized)
        and selected_normalized not in PLACEHOLDER_MIC_DEVICES
        and selected_normalized not in FAKE_MIC_DEVICES
        and configured_normalized not in FAKE_MIC_DEVICES
        and str(probe.get("status") or "") != "mock"
        and "fake microphone" not in message
        and "tingwu_mock=1" not in message
    )


def capture_probe_matches_selected(selected: str, capture_probe: dict[str, object]) -> bool:
    selected_normalized = str(selected or "").strip().lower()
    capture_selected = str(capture_probe.get("selected_device") or "").strip().lower()
    return (
        bool(selected_normalized)
        and selected_normalized not in PLACEHOLDER_MIC_DEVICES
        and capture_selected == selected_normalized
        and capture_selected not in FAKE_MIC_DEVICES
    )


def endpoint_origin(url: object) -> str:
    parsed = urllib.parse.urlsplit(str(url or ""))
    if not parsed.scheme or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme.lower()}://{host}"


def endpoint_details(url: object) -> dict[str, object]:
    parsed = urllib.parse.urlsplit(str(url or ""))
    return {
        "url": str(url or ""),
        "scheme": parsed.scheme.lower(),
        "host": (parsed.hostname or "").lower(),
        "path": parsed.path,
        "origin": endpoint_origin(url),
    }


def endpoint_matches(url: object, expected: str) -> bool:
    parsed = urllib.parse.urlsplit(str(url or ""))
    expected_parsed = urllib.parse.urlsplit(expected)
    return (
        parsed.scheme.lower() == expected_parsed.scheme
        and (parsed.hostname or "").lower() == (expected_parsed.hostname or "").lower()
        and (parsed.path.rstrip("/") or "/") == (expected_parsed.path.rstrip("/") or "/")
        and (parsed.port or 443) == (expected_parsed.port or 443)
    )


def tingwu_endpoint_probe(status: dict[str, object]) -> dict[str, object]:
    http_url = str(status.get("http_url") or "")
    ws_url = str(status.get("ws_url") or "")
    return {
        "http_url": http_url,
        "ws_url": ws_url,
        "http": endpoint_details(http_url),
        "ws": endpoint_details(ws_url),
        "official_dashscope": endpoint_matches(http_url, OFFICIAL_TINGWU_HTTP_URL)
        and endpoint_matches(ws_url, OFFICIAL_TINGWU_WS_URL),
    }


def missing_credentials_error(api_key: str | None, app_id: str | None) -> str:
    missing: list[str] = []
    if is_placeholder_tingwu_credential(api_key):
        missing.append("TINGWU_API_KEY or DASHSCOPE_API_KEY")
    if is_placeholder_tingwu_credential(app_id):
        missing.append("TINGWU_APP_ID or TINGWU_MEETING_APP_ID")
    return "Missing live Tingwu credentials: " + ", ".join(missing) if missing else ""


def print_check(name: str, condition: bool, details: object = "") -> None:
    if condition:
        print(f"ok - {name}")
    else:
        print(f"not ok - {name}: {details}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check local prerequisites for live Tongyi Tingwu verification without creating a cloud meeting task."
    )
    parser.add_argument("--workspace", default="")
    parser.add_argument("--audit-log", default="")
    parser.add_argument("--capture-seconds", type=int, default=0, help="Seconds of ALSA PCM audio to sample; defaults to TINGWU_PREFLIGHT_CAPTURE_SECONDS or 3.")
    parser.add_argument("--skip-capture", action="store_true", help="Skip the ALSA capture signal test.")
    parser.add_argument("--evidence-json", default="", help="Write a machine-readable preflight evidence report.")
    args = parser.parse_args()

    workspace_root, audit_path = preflight_paths(args)
    api_key = os.getenv("TINGWU_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    app_id = os.getenv("TINGWU_APP_ID") or os.getenv("TINGWU_MEETING_APP_ID")
    credential_diagnostics = {
        "api_key_kind": tingwu_credential_kind(api_key),
        "app_id_kind": tingwu_credential_kind(app_id, role="app_id"),
    }
    credential_error = missing_credentials_error(api_key, app_id)
    env_config = OfficeAgentConfig.from_env()
    capture_seconds = max(1, args.capture_seconds or env_config.tingwu_preflight_capture_seconds)
    config = replace(
        env_config,
        workspace_dir=workspace_root,
        audit_log_path=audit_path,
        allowed_roots=(workspace_root,),
        tingwu_api_key=str(api_key or ""),
        tingwu_app_id=str(app_id or ""),
        tingwu_preflight_capture_seconds=capture_seconds,
        tingwu_mock=False,
    ).normalized()
    checks: dict[str, object] = {
        "tingwu_api_key_configured": not is_placeholder_tingwu_credential(api_key),
        "tingwu_app_id_configured": not is_placeholder_tingwu_credential(app_id),
        "dashscope_tingwu_import": False,
        "provider_available": False,
        "official_tingwu_endpoint": False,
        "microphone_selected": False,
        "real_microphone_device": False,
        "microphone_capture_device_matches": False,
        "microphone_capture_open": False,
        "microphone_capture_signal": False,
        "workspace_writable": False,
        "audit_writable": False,
    }
    status: dict[str, object] = {}
    endpoint_probe: dict[str, object] = {}
    capture_probe: dict[str, object] = {}
    selected = ""
    errors: list[str] = []

    def make_payload(status_value: str, error: str = "") -> dict[str, object]:
        return {
            "status": status_value,
            "error": error,
            "workspace_dir": str(workspace_root),
            "audit_log_path": str(audit_path),
            "configured_mic_device": config.mic_device,
            "mic_device": config.mic_device,
            "selected_mic_device": selected,
            "sample_rate": config.tingwu_sample_rate,
            "capture_seconds": config.tingwu_preflight_capture_seconds,
            "audio_format": config.tingwu_audio_format,
            "http_url": status.get("http_url", config.tingwu_http_url),
            "ws_url": status.get("ws_url", config.tingwu_ws_url),
            "endpoint_probe": endpoint_probe,
            "provider_status": status,
            "credential_diagnostics": credential_diagnostics,
            "capture_probe": capture_probe,
            "checks": checks,
        }

    print_check("Tingwu API key configured", not is_placeholder_tingwu_credential(api_key), "Set TINGWU_API_KEY or DASHSCOPE_API_KEY.")
    print_check("Tingwu app id configured", not is_placeholder_tingwu_credential(app_id), "Set TINGWU_APP_ID or TINGWU_MEETING_APP_ID.")
    if credential_error:
        errors.append(credential_error)

    try:
        import dashscope  # noqa: F401
        from dashscope.multimodal.tingwu.tingwu_realtime import TingWuRealtime  # noqa: F401
        from lelamp.office_agent.tingwu_meeting import TingwuMeetingProvider, preflight_arecord_capture  # noqa: E402
    except Exception as exc:
        errors.append(f"dashscope TingWuRealtime import failed: {exc}")
        print_check("dashscope TingWuRealtime import", False, exc)
        TingwuMeetingProvider = None  # type: ignore[assignment]
        preflight_arecord_capture = None  # type: ignore[assignment]
    else:
        checks["dashscope_tingwu_import"] = True
        assert_ok("dashscope TingWuRealtime import", True)

    audit = AuditLogger(config.audit_log_path)
    workspace = Workspace(config.workspace_dir, config.allowed_roots, audit)
    provider = TingwuMeetingProvider(config, workspace, audit) if TingwuMeetingProvider is not None else None

    if provider is not None:
        try:
            status = provider.status()
            endpoint_probe = tingwu_endpoint_probe(status)
            mic_probe = status.get("mic_probe") if isinstance(status.get("mic_probe"), dict) else {}
            selected = str(status.get("selected_mic_device") or mic_probe.get("selected_device") or config.mic_device)
            checks["provider_available"] = status.get("status") == "available"
            checks["official_tingwu_endpoint"] = endpoint_probe.get("official_dashscope") is True
            checks["microphone_selected"] = bool(selected) and str(status.get("mic_status")) == "available"
            checks["real_microphone_device"] = real_microphone_selected(selected, mic_probe)
            print_check("provider is locally available", bool(checks["provider_available"]), status)
            print_check("provider status redacts URLs", "?" not in str(status.get("http_url") or "") and "@" not in str(status.get("http_url") or ""), status)
            print_check("official Tingwu endpoints configured", bool(checks["official_tingwu_endpoint"]), endpoint_probe)
            print_check("microphone selected", bool(checks["microphone_selected"]), status)
            print_check("real microphone selected", bool(checks["real_microphone_device"]), status)
        except Exception as exc:
            errors.append(f"provider status probe failed: {exc}")
            print_check("provider status probe", False, exc)

    capture_probe = {"status": "skipped", "reason": "skip_capture" if args.skip_capture else "microphone_not_ready"}
    if not args.skip_capture and preflight_arecord_capture is not None and bool(checks["real_microphone_device"]):
        try:
            print(f"Sampling microphone for {config.tingwu_preflight_capture_seconds}s. Speak clearly near the selected microphone now.")
            capture_probe = preflight_arecord_capture(
                selected,
                config.tingwu_sample_rate,
                duration_seconds=config.tingwu_preflight_capture_seconds,
            )
            checks["microphone_capture_device_matches"] = capture_probe_matches_selected(selected, capture_probe)
            checks["microphone_capture_open"] = capture_probe.get("status") == "available"
            checks["microphone_capture_signal"] = (
                bool(checks["microphone_capture_open"])
                and int(capture_probe.get("audio_bytes") or 0) > 0
                and int(capture_probe.get("audio_rms") or 0) > 0
                and int(capture_probe.get("audio_peak") or 0) > 0
            )
            assert_ok(
                "microphone capture uses selected device",
                bool(checks["microphone_capture_device_matches"]),
                {"selected_mic_device": selected, "capture_probe": capture_probe},
            )
            assert_ok("microphone capture opens", bool(checks["microphone_capture_open"]), capture_probe)
            assert_ok("microphone capture has signal", bool(checks["microphone_capture_signal"]), capture_probe)
        except AssertionError as exc:
            errors.append(str(exc))
            print_check("microphone capture preflight", False, exc)

    try:
        workspace.root.mkdir(parents=True, exist_ok=True)
        config.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        test_path = workspace.root / ".tingwu_preflight_write_test"
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink()
        audit.record("tingwu.preflight", target="tongyi_tingwu", details={"status": "ok", "configured_mic_device": config.mic_device, "selected_mic_device": selected})
        checks["workspace_writable"] = workspace.root.is_dir()
        checks["audit_writable"] = config.audit_log_path.is_file()
        assert_ok("workspace and audit writable", bool(checks["workspace_writable"] and checks["audit_writable"]), {"workspace": str(workspace.root), "audit": str(config.audit_log_path)})
    except Exception as exc:
        errors.append(f"workspace/audit write failed: {exc}")
        print_check("workspace and audit writable", False, exc)

    failed_checks = [key for key, value in checks.items() if value is not True]
    if failed_checks:
        errors.append("Preflight failed checks: " + ", ".join(failed_checks))
    status_value = "failed" if errors else "ok"
    payload = make_payload(status_value, "; ".join(dict.fromkeys(errors)))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    write_evidence(args.evidence_json, payload)
    if errors:
        raise SystemExit(payload["error"])
    print("preflight_tingwu_live complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
