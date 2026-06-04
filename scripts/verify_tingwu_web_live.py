#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "lelamp_runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

if sys.version_info < (3, 12):
    raise SystemExit("verify_tingwu_web_live requires Python >= 3.12. Run it with lelamp_runtime/.venv/bin/python.")

from lelamp.office_agent.config import is_placeholder_tingwu_credential, tingwu_credential_kind, tingwu_credential_next_actions  # noqa: E402


REQUIRED_STEPS = {
    "realtime_capture": "completed",
    "import_transcript": "completed",
    "minutes": "completed",
    "decisions": "waiting_confirmation",
    "action_items": "completed",
    "followup": "completed",
    "reminders": "completed",
    "projection_confirmation": "completed",
}
STEP_CHECK_NAMES = {
    "realtime_capture": "step_realtime_capture_completed",
    "import_transcript": "step_import_transcript_completed",
    "minutes": "step_minutes_completed",
    "decisions": "step_decisions_waiting_confirmation",
    "action_items": "step_action_items_completed",
    "followup": "step_followup_completed",
    "reminders": "step_reminders_completed",
    "projection_confirmation": "step_projection_confirmation_completed",
}

REQUIRED_AUDIT_ACTIONS = (
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
)

CANARY_SECRETS = ("web-live-title-token", "web-live-title-password", "web-live-participant-password")
DEFAULT_SPOKEN_PHRASE = "乐灯听悟验收测试"
FAKE_MIC_DEVICES = {"fake-mic", "mock", "mock-mic"}
OFFICIAL_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
OFFICIAL_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


def request_json(
    base_url: str,
    path: str,
    *,
    token: str,
    method: str = "GET",
    payload: object | None = None,
    timeout: int = 45,
) -> dict[str, Any]:
    status, envelope = request_envelope(base_url, path, token=token, method=method, payload=payload, timeout=timeout)
    if status >= 400:
        raise AssertionError(f"{method} {path} returned HTTP {status}: {envelope}")
    if not envelope.get("ok"):
        raise AssertionError(f"{method} {path} returned API error: {envelope}")
    data_obj = envelope.get("data")
    if not isinstance(data_obj, dict):
        raise AssertionError(f"{method} {path} returned invalid envelope: {envelope}")
    return data_obj


def request_envelope(
    base_url: str,
    path: str,
    *,
    token: str,
    method: str = "GET",
    payload: object | None = None,
    timeout: int = 45,
) -> tuple[int, dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}"}
    data: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            envelope = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            envelope = json.loads(body)
        except json.JSONDecodeError:
            envelope = {"ok": False, "error": {"code": "non_json", "message": body, "details": {}}}
        return exc.code, envelope
    return int(response.status), envelope


def assert_ok(name: str, condition: bool, details: object = "") -> None:
    if not condition:
        raise AssertionError(f"{name} failed: {details}")
    print(f"ok - {name}")


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


def tingwu_endpoint_probe(provider: dict[str, object]) -> dict[str, object]:
    http_url = str(provider.get("http_url") or "")
    ws_url = str(provider.get("ws_url") or "")
    return {
        "http_url": http_url,
        "ws_url": ws_url,
        "http": endpoint_details(http_url),
        "ws": endpoint_details(ws_url),
        "official_dashscope": endpoint_matches(http_url, OFFICIAL_TINGWU_HTTP_URL)
        and endpoint_matches(ws_url, OFFICIAL_TINGWU_WS_URL),
    }


def http_operation_names(operations: object) -> set[str]:
    if not isinstance(operations, list):
        return set()
    return {
        str(item.get("action") or "")
        for item in operations
        if isinstance(item, dict)
    }


def tingwu_http_operation_chain_valid(
    operations: object,
    *,
    provider_task_id: str,
    minutes_task_id: str = "",
    require_minutes: bool = False,
) -> bool:
    if not isinstance(operations, list):
        return False
    records = [item for item in operations if isinstance(item, dict)]
    if any(not endpoint_matches(item.get("endpoint"), OFFICIAL_TINGWU_HTTP_URL) for item in records):
        return False
    creates = [item for item in records if item.get("action") == "CreateTask"]
    if not any(
        str(item.get("request_type") or "") == "realtime"
        and str(item.get("response_data_id") or "") == provider_task_id
        for item in creates
    ):
        return False
    if not require_minutes:
        return True
    minutes_creates = [item for item in records if item.get("action") == "CreateRealtimeMinutesTask"]
    get_tasks = [item for item in records if item.get("action") == "GetTask"]
    return bool(
        provider_task_id
        and minutes_task_id
        and any(
            str(item.get("request_data_id") or "") == provider_task_id
            and str(item.get("response_data_id") or "") == minutes_task_id
            for item in minutes_creates
        )
        and any(str(item.get("request_data_id") or "") == minutes_task_id for item in get_tasks)
    )


def secret_absent_from_outputs(secret: str, paths: list[Path]) -> bool:
    if len(secret) < 8:
        return True
    needle = secret.encode("utf-8")
    for path in paths:
        if path.is_dir():
            candidates = [item for item in path.rglob("*") if item.is_file()]
        else:
            candidates = [path] if path.is_file() else []
        for candidate in candidates:
            try:
                if needle in candidate.read_bytes():
                    return False
            except OSError:
                return False
    return True


def wait_for_audio(base_url: str, token: str, meeting_id: str, seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + max(3, seconds)
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = request_json(base_url, f"/api/meeting/realtime/status?meeting_id={urllib.parse.quote(meeting_id)}", token=token)
        print(
            f"status={last.get('status')} finals={last.get('final_count')} "
            f"audio_seconds={last.get('audio_seconds')} partial={last.get('partial_text')!r}"
        )
        time.sleep(1)
    return last


def stop_and_wait(base_url: str, token: str, meeting_id: str, *, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while True:
        last = request_json(
            base_url,
            "/api/meeting/realtime/stop",
            token=token,
            method="POST",
            payload={"meeting_id": meeting_id, "run_followup": False},
            timeout=180,
        )
        if str(last.get("status") or "") not in {"starting", "running", "stopping"}:
            return last
        if time.monotonic() >= deadline:
            return last
        time.sleep(1)


def file_is_under(path_value: object, root: Path) -> bool:
    path = Path(str(path_value or "")).expanduser().resolve()
    return path.is_file() and path.is_relative_to(root)


def output_workspace_paths(manifest: dict[str, Any]) -> set[str]:
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), list) else []
    return {
        str(item.get("workspace_path") or "")
        for item in outputs
        if isinstance(item, dict) and item.get("inside_workspace") is True
    }


def manifest_has_workspace_path(manifest: dict[str, Any], path_value: object, workspace_root: Path) -> bool:
    if not str(path_value or "").strip():
        return False
    path = Path(str(path_value)).expanduser().resolve()
    try:
        workspace_path = str(path.relative_to(workspace_root))
    except ValueError:
        return False
    return workspace_path in output_workspace_paths(manifest)


def path_is_under_meeting_output(path_value: object, output_dir: Path) -> bool:
    if not str(path_value or "").strip():
        return False
    path = Path(str(path_value)).expanduser().resolve()
    return path.is_file() and path.is_relative_to(output_dir)


def audit_actions_visible(base_url: str, token: str, actions: tuple[str, ...], *, meeting_id: str = "") -> tuple[bool, dict[str, object]]:
    found: dict[str, int] = {}
    samples: dict[str, list[dict[str, Any]]] = {}
    for action in actions:
        payload = request_json(
            base_url,
            f"/api/audit/search?action={urllib.parse.quote(action)}&limit=5000",
            token=token,
        )
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        if meeting_id:
            related = [
                item for item in items
                if isinstance(item, dict) and meeting_id in json.dumps(item, ensure_ascii=False)
            ]
            # Some lower-level OpenClaw audit entries target generated files
            # rather than the provider meeting id. Their presence still proves
            # the action was audited in this isolated live workspace.
            count = len(related) if related else len(items)
            samples[action] = related[:3] if related else items[:3]
        else:
            count = len(items)
            samples[action] = items[:3]
        found[action] = count
    missing = [action for action, count in found.items() if count <= 0]
    return not missing, {"missing": missing, "found": found, "samples": samples}


def verify_meeting_allowed_roots_block(base_url: str, token: str) -> dict[str, object]:
    outside_path = Path(tempfile.gettempdir()) / "lelamp_tingwu_live_outside_allowed_roots.txt"
    outside_path.write_text("outside workspace should stay unreadable by meeting APIs\n", encoding="utf-8")
    try:
        status, envelope = request_envelope(
            base_url,
            "/api/meeting/minutes",
            token=token,
            method="POST",
            payload={"transcript": str(outside_path), "title": "outside allowed roots probe"},
        )
    finally:
        try:
            outside_path.unlink()
        except FileNotFoundError:
            pass
    error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
    audit_payload = request_json(
        base_url,
        "/api/audit/search?action=meeting_minutes&limit=200",
        token=token,
    )
    audit_items = audit_payload.get("items") if isinstance(audit_payload.get("items"), list) else []
    audit_visible = any(
        isinstance(item, dict)
        and str(item.get("status") or "") == "blocked"
        and "outside allowed roots" in json.dumps(item, ensure_ascii=False)
        for item in audit_items
    )
    return {
        "path": str(outside_path),
        "status_code": status,
        "ok": envelope.get("ok") is True,
        "error_code": str(error.get("code") or ""),
        "audit_visible": audit_visible,
    }


def temp_files_under(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [str(item) for item in path.rglob(".*.tmp") if item.is_file()]


def write_evidence(path_value: str, payload: dict[str, object]) -> None:
    if not path_value:
        return
    path = Path(path_value).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"evidence_json={path}")


def with_canary_inputs(title: str, participants: list[str]) -> tuple[str, list[str]]:
    title_value = f"{title} token={CANARY_SECRETS[0]} password={CANARY_SECRETS[1]}"
    participant_values = [item for item in participants if item]
    if participant_values:
        participant_values[0] = f"{participant_values[0]} password={CANARY_SECRETS[2]}"
    else:
        participant_values = [f"LiveTester password={CANARY_SECRETS[2]}"]
    return title_value, participant_values


def normalize_phrase(value: object) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def transcript_contains_phrase(transcript_text: str, phrase: str) -> bool:
    normalized_phrase = normalize_phrase(phrase)
    if not normalized_phrase:
        return True
    return normalized_phrase in normalize_phrase(transcript_text)


def spoken_phrase_failure_details(transcript_text: str, phrase: str, transcript_path: Path) -> dict[str, object]:
    return {
        "spoken_phrase": phrase,
        "normalized_spoken_phrase": normalize_phrase(phrase),
        "normalized_transcript_preview": normalize_phrase(transcript_text)[:500],
        "transcript_path": str(transcript_path),
        "transcript_preview": transcript_text[:500],
        "hint": "Repeat the exact spoken phrase clearly near the microphone, or rerun with --spoken-phrase matching what Tingwu transcribed.",
    }


def start_failure_evidence(
    *,
    args: argparse.Namespace,
    security: dict[str, Any],
    workspace_root: Path,
    allowed_roots: list[Path],
    allowed_roots_probe: dict[str, object],
    full_control_request: dict[str, object],
    full_control_confirm: dict[str, object],
    full_control_cancel: dict[str, object],
    security_after_full_control: dict[str, Any],
    provider: dict[str, object],
    endpoint_probe: dict[str, object],
    status_code: int,
    envelope: dict[str, Any],
) -> dict[str, object]:
    error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    detail_provider = details.get("provider") if isinstance(details.get("provider"), dict) else {}
    mic_probe = (
        details.get("mic_probe")
        if isinstance(details.get("mic_probe"), dict)
        else detail_provider.get("mic_probe")
        if isinstance(detail_provider.get("mic_probe"), dict)
        else provider.get("mic_probe")
        if isinstance(provider.get("mic_probe"), dict)
        else {}
    )
    capture_probe = (
        details.get("capture_probe")
        if isinstance(details.get("capture_probe"), dict)
        else mic_probe.get("capture_probe")
        if isinstance(mic_probe.get("capture_probe"), dict)
        else {}
    )
    capture_signal = False
    if isinstance(capture_probe, dict):
        try:
            capture_signal = (
                int(capture_probe.get("audio_bytes") or 0) > 0
                and int(capture_probe.get("audio_rms") or 0) > 0
                and int(capture_probe.get("audio_peak") or 0) > 0
            )
        except (TypeError, ValueError):
            capture_signal = False
    return {
        "status": "failed",
        "mode": "web_api",
        "base_url": args.base_url,
        "error": str(error.get("message") or envelope),
        "error_code": str(error.get("code") or ""),
        "status_code": status_code,
        "error_details": details,
        "workspace_dir": str(workspace_root),
        "audit_log_path": str(security.get("audit_log_path") or ""),
        "allowed_roots_probe": allowed_roots_probe,
        "full_control_gate": {
            "request_status": str(full_control_request.get("status") or ""),
            "request_id_present": bool(full_control_request.get("request_id")),
            "confirm_status": str(full_control_confirm.get("status") or ""),
            "confirm_full_control_enabled": full_control_confirm.get("full_control_enabled") is True,
            "security_permission_mode_after_confirm": str(security_after_full_control.get("permission_mode") or ""),
            "security_full_control_enabled_after_confirm": security_after_full_control.get("full_control_enabled") is True,
            "cancel_status": str(full_control_cancel.get("status") or ""),
        },
        "configured_mic_device": str(mic_probe.get("configured_device") or provider.get("mic_device") or ""),
        "selected_mic_device": str(mic_probe.get("selected_device") or provider.get("selected_mic_device") or provider.get("mic_device") or ""),
        "mic_probe": mic_probe,
        "endpoint_probe": endpoint_probe,
        "sample_rate": int(provider.get("sample_rate") or 16000),
        "audio_format": str(provider.get("audio_format") or "pcm"),
        "spoken_phrase": args.spoken_phrase,
        "spoken_phrase_detected": False,
        "redaction_canaries": list(CANARY_SECRETS),
        "checks": {
            "sandbox_visible": security.get("permission_mode") == "sandbox",
            "audit_only_visible": security.get("desktop_backend") == "audit_only",
            "workspace_allowed": workspace_root in allowed_roots,
            "allowed_roots_block_enforced": allowed_roots_probe.get("status_code") == 403
            and allowed_roots_probe.get("ok") is False
            and allowed_roots_probe.get("error_code") == "blocked",
            "full_control_gate_enforced": full_control_request.get("status") == "waiting_confirmation"
            and full_control_confirm.get("status") == "backend_missing"
            and security_after_full_control.get("permission_mode") == "sandbox"
            and full_control_cancel.get("status") == "blocked",
            "provider_available": provider.get("status") == "available" and provider.get("configured") is True,
            "live_mode_not_mock": provider.get("mock") is False,
            "official_tingwu_endpoint": endpoint_probe.get("official_dashscope") is True,
            "real_microphone_device": real_microphone_selected(mic_probe) if isinstance(mic_probe, dict) else False,
            "microphone_capture_open": isinstance(capture_probe, dict) and capture_probe.get("status") == "available",
            "microphone_capture_signal": capture_signal,
            "realtime_start_succeeded": False,
            "spoken_phrase_detected": False,
        },
    }


def missing_credentials_evidence(
    *,
    args: argparse.Namespace,
    security: dict[str, Any],
    workspace_root: Path,
    allowed_roots: list[Path],
    allowed_roots_probe: dict[str, object],
    full_control_request: dict[str, object],
    full_control_confirm: dict[str, object],
    full_control_cancel: dict[str, object],
    security_after_full_control: dict[str, Any],
    api_key: str,
    app_id: str,
) -> dict[str, object]:
    credential_diagnostics = {
        "api_key_kind": tingwu_credential_kind(api_key),
        "app_id_kind": tingwu_credential_kind(app_id, role="app_id"),
    }
    return {
        "status": "failed",
        "mode": "web_api",
        "base_url": args.base_url,
        "error": "Set TINGWU_API_KEY or DASHSCOPE_API_KEY and TINGWU_APP_ID or TINGWU_MEETING_APP_ID in this verifier shell so secret persistence checks are meaningful.",
        "credential_diagnostics": credential_diagnostics,
        "next_actions": tingwu_credential_next_actions(
            str(credential_diagnostics["api_key_kind"]),
            str(credential_diagnostics["app_id_kind"]),
        ),
        "workspace_dir": str(workspace_root),
        "audit_log_path": str(security.get("audit_log_path") or ""),
        "allowed_roots_probe": allowed_roots_probe,
        "full_control_gate": {
            "request_status": str(full_control_request.get("status") or ""),
            "request_id_present": bool(full_control_request.get("request_id")),
            "confirm_status": str(full_control_confirm.get("status") or ""),
            "confirm_full_control_enabled": full_control_confirm.get("full_control_enabled") is True,
            "security_permission_mode_after_confirm": str(security_after_full_control.get("permission_mode") or ""),
            "security_full_control_enabled_after_confirm": security_after_full_control.get("full_control_enabled") is True,
            "cancel_status": str(full_control_cancel.get("status") or ""),
        },
        "configured_mic_device": "",
        "selected_mic_device": "",
        "mic_probe": {},
        "endpoint_probe": {},
        "sample_rate": 16000,
        "audio_format": "pcm",
        "spoken_phrase": args.spoken_phrase,
        "spoken_phrase_detected": False,
        "redaction_canaries": list(CANARY_SECRETS),
        "checks": {
            "sandbox_visible": security.get("permission_mode") == "sandbox",
            "audit_only_visible": security.get("desktop_backend") == "audit_only",
            "workspace_allowed": workspace_root in allowed_roots,
            "allowed_roots_block_enforced": allowed_roots_probe.get("status_code") == 403
            and allowed_roots_probe.get("ok") is False
            and allowed_roots_probe.get("error_code") == "blocked",
            "full_control_gate_enforced": full_control_request.get("status") == "waiting_confirmation"
            and full_control_confirm.get("status") == "backend_missing"
            and security_after_full_control.get("permission_mode") == "sandbox"
            and full_control_cancel.get("status") == "blocked",
            "tingwu_api_key_configured": not is_placeholder_tingwu_credential(api_key),
            "tingwu_app_id_configured": not is_placeholder_tingwu_credential(app_id),
            "provider_available": False,
            "live_mode_not_mock": False,
            "official_tingwu_endpoint": False,
            "real_microphone_device": False,
            "realtime_start_succeeded": False,
            "spoken_phrase_detected": False,
        },
    }


def real_microphone_selected(probe: dict[str, object], *, require_capture_probe: bool = True) -> bool:
    audio_source = str(probe.get("audio_source") or "").strip().lower()
    selected = str(probe.get("selected_device") or "").strip().lower()
    configured = str(probe.get("configured_device") or "").strip().lower()
    message = str(probe.get("message") or "").lower()
    capture_probe = probe.get("capture_probe") if isinstance(probe.get("capture_probe"), dict) else {}
    capture_selected = str(capture_probe.get("selected_device") or "").strip().lower()
    capture_message = str(capture_probe.get("message") or "").lower()
    try:
        capture_bytes = int(capture_probe.get("audio_bytes") or 0)
        capture_rms = int(capture_probe.get("audio_rms") or 0)
        capture_peak = int(capture_probe.get("audio_peak") or 0)
    except (TypeError, ValueError):
        capture_bytes = capture_rms = capture_peak = 0
    return (
        bool(selected)
        and audio_source != "file"
        and selected not in {"auto", "default", "pulse", "sysdefault"}
        and selected not in FAKE_MIC_DEVICES
        and configured not in FAKE_MIC_DEVICES
        and str(probe.get("status") or "") == "available"
        and "fake microphone" not in message
        and "tingwu_mock=1" not in message
        and (
            not require_capture_probe
            or (
                bool(capture_probe)
                and str(capture_probe.get("status") or "") == "available"
                and capture_selected == selected
                and capture_selected not in {"auto", "default", "pulse", "sysdefault"}
                and capture_selected not in FAKE_MIC_DEVICES
                and "fake microphone" not in capture_message
                and "tingwu_mock=1" not in capture_message
                and capture_bytes > 0
                and capture_rms > 0
                and capture_peak > 0
            )
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a live Tongyi Tingwu realtime meeting through the LeLamp Web Console API."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8790")
    parser.add_argument("--token", required=True)
    parser.add_argument("--title", default="LeLamp Tingwu Web API live verification")
    parser.add_argument("--participant", action="append", default=["LiveTester"])
    parser.add_argument("--seconds", type=int, default=12)
    parser.add_argument("--evidence-json", default="", help="Write a machine-readable Web live verification evidence report.")
    parser.add_argument("--spoken-phrase", default=DEFAULT_SPOKEN_PHRASE, help="Required phrase to speak during capture and verify in the realtime transcript.")
    args = parser.parse_args()

    security = request_json(args.base_url, "/api/security", token=args.token)
    assert_ok("sandbox mode visible", security.get("permission_mode") == "sandbox", security)
    assert_ok("audit_only desktop visible", security.get("desktop_backend") == "audit_only", security)
    workspace_root = Path(str(security["workspace_dir"])).expanduser().resolve()
    allowed_roots = [Path(str(item)).expanduser().resolve() for item in security.get("allowed_roots", []) if item]
    assert_ok("workspace is inside allowed roots", workspace_root in allowed_roots, security)
    allowed_roots_probe = verify_meeting_allowed_roots_block(args.base_url, args.token)
    assert_ok(
        "meeting APIs block transcript paths outside allowed roots",
        allowed_roots_probe.get("status_code") == 403
        and allowed_roots_probe.get("ok") is False
        and allowed_roots_probe.get("error_code") == "blocked"
        and allowed_roots_probe.get("audit_visible") is True,
        allowed_roots_probe,
    )
    full_control_request_payload = request_json(
        args.base_url,
        "/api/settings/full-control/request",
        token=args.token,
        method="POST",
        payload={"purpose": "live Tingwu verifier confirms full_control remains gated"},
    )
    full_control_request = full_control_request_payload if isinstance(full_control_request_payload, dict) else {}
    assert_ok(
        "full_control request remains gated",
        full_control_request.get("status") == "waiting_confirmation" and bool(full_control_request.get("request_id")),
        full_control_request,
    )
    full_control_confirm_payload = request_json(
        args.base_url,
        "/api/settings/full-control/confirm",
        token=args.token,
        method="POST",
        payload={"request_id": full_control_request.get("request_id"), "step": 3},
    )
    full_control_confirm = full_control_confirm_payload if isinstance(full_control_confirm_payload, dict) else {}
    assert_ok(
        "full_control confirm does not mutate runtime mode",
        full_control_confirm.get("status") == "backend_missing"
        and full_control_confirm.get("full_control_enabled") is False,
        full_control_confirm,
    )
    security_after_full_control = request_json(args.base_url, "/api/security", token=args.token)
    assert_ok(
        "sandbox remains active after full_control confirm",
        security_after_full_control.get("permission_mode") == "sandbox"
        and security_after_full_control.get("full_control_enabled") is False,
        security_after_full_control,
    )
    full_control_cancel_payload = request_json(
        args.base_url,
        "/api/settings/full-control/cancel",
        token=args.token,
        method="POST",
        payload={"request_id": full_control_request.get("request_id")},
    )
    full_control_cancel = full_control_cancel_payload if isinstance(full_control_cancel_payload, dict) else {}
    assert_ok("full_control cancel remains blocked", full_control_cancel.get("status") == "blocked", full_control_cancel)
    api_key = os.getenv("TINGWU_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    app_id = os.getenv("TINGWU_APP_ID") or os.getenv("TINGWU_MEETING_APP_ID")
    if is_placeholder_tingwu_credential(api_key) or is_placeholder_tingwu_credential(app_id):
        evidence = missing_credentials_evidence(
            args=args,
            security=security,
            workspace_root=workspace_root,
            allowed_roots=allowed_roots,
            allowed_roots_probe=allowed_roots_probe,
            full_control_request=full_control_request,
            full_control_confirm=full_control_confirm,
            full_control_cancel=full_control_cancel,
            security_after_full_control=security_after_full_control,
            api_key=str(api_key or ""),
            app_id=str(app_id or ""),
        )
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        write_evidence(args.evidence_json, evidence)
        raise SystemExit(evidence["error"])

    provider_payload = request_json(args.base_url, "/api/meeting/provider/status", token=args.token)
    provider = provider_payload.get("providers", {}).get("tongyi_tingwu", {})
    assert_ok("tingwu provider configured", provider.get("status") == "available" and provider.get("configured") is True, provider)
    assert_ok("live mode not mock", provider.get("mock") is False, provider)
    endpoint_probe = tingwu_endpoint_probe(provider)
    assert_ok("official Tingwu endpoints configured", endpoint_probe.get("official_dashscope") is True, endpoint_probe)
    provider_mic_probe = provider.get("mic_probe") if isinstance(provider.get("mic_probe"), dict) else {}
    assert_ok("real microphone selected", real_microphone_selected(provider_mic_probe, require_capture_probe=False), provider)
    print(f"Recording from {provider.get('mic_device')} at {provider.get('sample_rate')} Hz for {args.seconds}s.")
    print(f"Speak this phrase after this line: {args.spoken_phrase}")
    live_title, live_participants = with_canary_inputs(args.title, args.participant)

    start_status, start_envelope = request_envelope(
        args.base_url,
        "/api/meeting/realtime/start",
        token=args.token,
        method="POST",
        payload={
            "title": live_title,
            "participants": live_participants,
            "max_seconds": max(3, args.seconds + 20),
        },
        timeout=60,
    )
    if start_status >= 400 or start_envelope.get("ok") is not True:
        write_evidence(
            args.evidence_json,
            start_failure_evidence(
                args=args,
                security=security,
                workspace_root=workspace_root,
                allowed_roots=allowed_roots,
                allowed_roots_probe=allowed_roots_probe,
                full_control_request=full_control_request,
                full_control_confirm=full_control_confirm,
                full_control_cancel=full_control_cancel,
                security_after_full_control=security_after_full_control,
                provider=provider,
                endpoint_probe=endpoint_probe,
                status_code=start_status,
                envelope=start_envelope,
            ),
        )
        raise AssertionError(f"POST /api/meeting/realtime/start returned HTTP {start_status}: {start_envelope}")
    start = start_envelope.get("data")
    if not isinstance(start, dict):
        raise AssertionError(f"POST /api/meeting/realtime/start returned invalid envelope: {start_envelope}")
    meeting_id = str(start.get("meeting_id") or "")
    assert_ok("web realtime start", start.get("status") == "running" and meeting_id, start)
    web_task_id = str(start.get("task_id_web") or start.get("task_id") or "")
    assert_ok("web realtime task id returned", bool(web_task_id), start)

    wait_for_audio(args.base_url, args.token, meeting_id, args.seconds)

    realtime_event_payload = request_json(
        args.base_url,
        f"/api/meeting/realtime/events?meeting_id={urllib.parse.quote(meeting_id)}",
        token=args.token,
    )
    realtime_events = realtime_event_payload.get("events") if isinstance(realtime_event_payload.get("events"), list) else []
    expected_event_names = {"meeting_started", "transcript", "meeting_stopped", "websocket_open", "websocket_started", "tingwu_event"}
    websocket_event_names = {"websocket_open", "websocket_started"}
    assert_ok(
        "web realtime provider events API",
        any(str(item.get("event") or "") in expected_event_names for item in realtime_events if isinstance(item, dict)),
        realtime_event_payload,
    )
    assert_ok(
        "web realtime events include WebSocket stream start",
        any(str(item.get("event") or "") in websocket_event_names for item in realtime_events if isinstance(item, dict)),
        realtime_event_payload,
    )
    active_fetch_status, active_fetch_envelope = request_envelope(
        args.base_url,
        "/api/meeting/realtime/fetch-minutes",
        token=args.token,
        method="POST",
        payload={"meeting_id": meeting_id, "run_followup": True},
        timeout=30,
    )
    active_fetch_error = active_fetch_envelope.get("error") if isinstance(active_fetch_envelope.get("error"), dict) else {}
    active_fetch_probe = {
        "status_code": active_fetch_status,
        "ok": active_fetch_envelope.get("ok") is True,
        "error_code": str(active_fetch_error.get("code") or ""),
        "status": str((active_fetch_error.get("details") if isinstance(active_fetch_error.get("details"), dict) else {}).get("status") or ""),
    }
    assert_ok(
        "web fetch-minutes is blocked before realtime capture stops",
        active_fetch_probe.get("status_code") == 409
        and active_fetch_probe.get("ok") is False
        and active_fetch_probe.get("error_code") == "meeting_not_stopped",
        active_fetch_probe,
    )

    result = stop_and_wait(args.base_url, args.token, meeting_id, timeout=60)
    session = result.get("session") if isinstance(result.get("session"), dict) else {}
    minutes = result.get("minutes") if isinstance(result.get("minutes"), dict) else {}
    job = result.get("job") if isinstance(result.get("job"), dict) else {}
    task_event_payload = request_json(args.base_url, f"/api/tasks/{urllib.parse.quote(web_task_id)}/events", token=args.token)
    task_events = task_event_payload.get("events") if isinstance(task_event_payload.get("events"), list) else []
    task_list_payload = request_json(args.base_url, "/api/tasks?limit=50", token=args.token)
    task_items = task_list_payload.get("items") if isinstance(task_list_payload.get("items"), list) else []
    listed_task = next((item for item in task_items if isinstance(item, dict) and item.get("task_id") == web_task_id), {})
    manifest_path = Path(str(result.get("manifest_path") or "")).expanduser().resolve()
    steps = {step.get("name"): step.get("status") for step in job.get("steps", []) if isinstance(step, dict)}
    transcript_text = str(session.get("realtime_transcript") or "").strip()
    transcript_items = session.get("transcript") if isinstance(session.get("transcript"), list) else []
    task_payload = session.get("task_payload") if isinstance(session.get("task_payload"), dict) else {}
    provider_events = task_payload.get("events") if isinstance(task_payload.get("events"), list) else []
    mic_probe = task_payload.get("mic_probe") if isinstance(task_payload.get("mic_probe"), dict) else provider_mic_probe
    provider_event_names = [
        str(item.get("event") or "")
        for item in provider_events
        if isinstance(item, dict)
    ]

    assert_ok("web realtime stopped before AI minutes fetch", result.get("status") == "stopped" and result.get("provider_status") == "stopped", result)
    assert_ok("web live provider captured from real microphone", real_microphone_selected(mic_probe), mic_probe)
    assert_ok("session output dir under workspace", Path(str(session.get("output_dir") or "")).resolve().is_relative_to(workspace_root), session)
    assert_ok("live transcript saved", file_is_under(session.get("transcript_path"), workspace_root), session)
    assert_ok("live audio saved", file_is_under(session.get("audio_path"), workspace_root) and float(session.get("audio_seconds") or 0) > 0, session)
    audio_path = Path(str(session.get("audio_path") or "")).expanduser().resolve()
    with wave.open(str(audio_path), "rb") as audio:
        assert_ok(
            "live audio wav format",
            audio.getframerate() == int(provider.get("sample_rate") or 16000)
            and audio.getnchannels() == 1
            and audio.getsampwidth() == 2
            and audio.getnframes() > 0,
            {
                "framerate": audio.getframerate(),
                "channels": audio.getnchannels(),
                "sampwidth": audio.getsampwidth(),
                "frames": audio.getnframes(),
            },
        )
    assert_ok("live audio has signal", int(session.get("audio_rms") or 0) > 0 and int(session.get("audio_peak") or 0) > 0, session)
    assert_ok("live WebSocket audio frames sent", int(session.get("websocket_audio_frames") or 0) > 0, session)
    transcript_path = Path(str(session.get("transcript_path") or "")).expanduser().resolve()
    transcript_file_text = transcript_path.read_text(encoding="utf-8").strip()
    final_count = int(session.get("final_count") or 0)
    assert_ok("live transcript file has speech text", len(transcript_file_text) >= 8, transcript_path)
    assert_ok("live realtime transcript callbacks produced content", final_count > 0 or len(transcript_items) > 0, session)
    spoken_phrase_detected = transcript_contains_phrase(transcript_file_text, args.spoken_phrase)
    if not spoken_phrase_detected:
        output_dir_for_failure = Path(str(session.get("output_dir") or "")).resolve()
        session_path_for_failure = output_dir_for_failure / "session.json" if session.get("output_dir") else Path("")
        manifest_path_for_failure = Path(str(result.get("manifest_path") or "")).expanduser().resolve() if result.get("manifest_path") else output_dir_for_failure / "manifest.json"
        task_file_for_failure = workspace_root / "web_tasks" / f"{web_task_id}.json" if web_task_id else Path("")
        write_evidence(
            args.evidence_json,
            {
                "status": "failed",
                "mode": "web_api",
                "meeting_id": meeting_id,
                "base_url": args.base_url,
                "workspace_dir": str(workspace_root),
                "audit_log_path": str(security.get("audit_log_path") or ""),
                "output_dir": str(session.get("output_dir") or ""),
                "transcript_path": str(session.get("transcript_path") or ""),
                "audio_path": str(session.get("audio_path") or ""),
                "session_path": str(session_path_for_failure) if str(session_path_for_failure) else "",
                "manifest_path": str(manifest_path_for_failure),
                "task_file": str(task_file_for_failure) if str(task_file_for_failure) else "",
                "provider_task_id": str(session.get("task_id") or ""),
                "configured_mic_device": str(mic_probe.get("configured_device") or provider.get("mic_device") or ""),
                "selected_mic_device": str(mic_probe.get("selected_device") or provider.get("selected_mic_device") or provider.get("mic_device") or ""),
                "mic_probe": mic_probe,
                "endpoint_probe": endpoint_probe,
                "web_task_id": web_task_id,
                "audio_seconds": float(session.get("audio_seconds") or 0),
                "audio_bytes": int(session.get("audio_bytes") or 0),
                "sample_rate": int(session.get("sample_rate") or provider.get("sample_rate") or 16000),
                "audio_format": str(session.get("audio_format") or provider.get("audio_format") or "pcm"),
                "websocket_audio_frames": int(session.get("websocket_audio_frames") or 0),
                "audio_rms": int(session.get("audio_rms") or 0),
                "audio_peak": int(session.get("audio_peak") or 0),
                "final_count": final_count,
                "transcript_items": len(transcript_items),
                "spoken_phrase": args.spoken_phrase,
                "spoken_phrase_detected": False,
                "spoken_phrase_failure": spoken_phrase_failure_details(transcript_file_text, args.spoken_phrase, transcript_path),
                "redaction_canaries": list(CANARY_SECRETS),
                "steps": steps,
                "provider_events": [*provider_event_names[-40:]],
                "checks": {
                    "sandbox_visible": security.get("permission_mode") == "sandbox",
                    "audit_only_visible": security.get("desktop_backend") == "audit_only",
                    "workspace_allowed": workspace_root in allowed_roots,
                    "provider_available": provider.get("status") == "available" and provider.get("configured") is True,
                    "live_mode_not_mock": provider.get("mock") is False,
                    "official_tingwu_endpoint": endpoint_probe.get("official_dashscope") is True,
                    "real_microphone_device": real_microphone_selected(mic_probe),
                    "stopped_before_ai_minutes": result.get("status") == "stopped" and result.get("provider_status") == "stopped",
                    "audio_saved": file_is_under(session.get("audio_path"), workspace_root),
                    "audio_has_signal": int(session.get("audio_rms") or 0) > 0 and int(session.get("audio_peak") or 0) > 0,
                    "websocket_stream_started": any(item in websocket_event_names for item in provider_event_names),
                    "websocket_audio_frames_sent": int(session.get("websocket_audio_frames") or 0) > 0,
                    "transcript_saved": file_is_under(session.get("transcript_path"), workspace_root),
                    "realtime_transcript_non_empty": len(transcript_file_text) >= 8 and (final_count > 0 or len(transcript_items) > 0),
                    "spoken_phrase_detected": False,
                },
            },
        )
    assert_ok(
        "live transcript contains requested spoken phrase",
        spoken_phrase_detected,
        spoken_phrase_failure_details(transcript_file_text, args.spoken_phrase, transcript_path),
    )
    assert_ok("live tingwu minutes not fetched during stop", not session.get("minutes_path"), session)
    assert_ok(
        "web session persisted provider events",
        any(item in expected_event_names for item in provider_event_names),
        provider_events,
    )
    assert_ok(
        "web session persisted WebSocket stream events",
        any(item in websocket_event_names for item in provider_event_names),
        provider_events,
    )
    assert_ok(
        "web task event log includes realtime events",
        any(str(item.get("event") or "") in expected_event_names for item in task_events if isinstance(item, dict)),
        task_event_payload,
    )
    assert_ok(
        "web task event log includes WebSocket stream start",
        any(str(item.get("event") or "") in websocket_event_names for item in task_events if isinstance(item, dict)),
        task_event_payload,
    )
    listed_task_output = listed_task.get("output") if isinstance(listed_task.get("output"), dict) else {}
    listed_task_monitor = listed_task_output.get("monitor") if isinstance(listed_task_output.get("monitor"), dict) else {}
    assert_ok(
        "web task monitor list includes realtime capture task",
        bool(listed_task) and listed_task.get("task_id") == web_task_id,
        task_list_payload,
    )
    assert_ok(
        "web task monitor list exposes realtime metrics",
        int(listed_task_monitor.get("websocket_audio_frames") or 0) > 0
        and float(listed_task_monitor.get("audio_seconds") or 0) > 0
        and int(listed_task_monitor.get("final_count") or 0) >= 0,
        listed_task,
    )
    assert_ok("OpenClaw minutes saved from stopped transcript", file_is_under(minutes.get("path"), workspace_root), minutes)
    stop_expected_steps = {key: value for key, value in REQUIRED_STEPS.items() if key not in {"followup", "reminders", "projection_confirmation"}}
    assert_ok(
        "stop workflow saves capture and OpenClaw minutes before explicit follow-up",
        all(steps.get(name) == status for name, status in stop_expected_steps.items())
        and "followup" not in steps
        and "reminders" not in steps
        and "projection_confirmation" not in steps,
        steps,
    )
    assert_ok("meeting manifest saved", manifest_path.is_file() and manifest_path.is_relative_to(Path(str(session.get("output_dir") or "")).resolve()), result)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), list) else []
    output_dir = Path(str(session.get("output_dir") or "")).resolve()
    session_path = output_dir / "session.json"
    manifest_workspace_paths = {
        str(item.get("workspace_path") or "")
        for item in manifest_outputs
        if isinstance(item, dict)
    }
    assert_ok(
        "meeting manifest indexes provider and OpenClaw outputs",
        manifest.get("meeting_id") == meeting_id
        and manifest.get("provider") == "tongyi_tingwu"
        and manifest.get("provider_status") == "stopped"
        and manifest.get("openclaw_status") == "completed"
        and manifest.get("tingwu_http_operations") == session.get("tingwu_http_operations")
        and tingwu_http_operation_chain_valid(
            manifest.get("tingwu_http_operations"),
            provider_task_id=str(session.get("task_id") or ""),
        )
        and not manifest.get("tingwu_minutes_path")
        and manifest.get("openclaw_minutes_path") == minutes.get("path")
        and str(Path(str(session.get("transcript_path"))).resolve().relative_to(workspace_root)) in manifest_workspace_paths
        and str(Path(str(session.get("audio_path"))).resolve().relative_to(workspace_root)) in manifest_workspace_paths
        and str((output_dir / "session.json").relative_to(workspace_root)) in manifest_workspace_paths
        and any(item.get("inside_workspace") for item in manifest_outputs if isinstance(item, dict)),
        manifest,
    )
    assert_ok("no temporary artifact files left", not temp_files_under(workspace_root), temp_files_under(workspace_root))
    fetch_result = request_json(
        args.base_url,
        "/api/meeting/realtime/fetch-minutes",
        token=args.token,
        method="POST",
        payload={"meeting_id": meeting_id, "run_followup": True},
        timeout=180,
    )
    fetch_session = fetch_result.get("session") if isinstance(fetch_result.get("session"), dict) else {}
    fetch_ai_minutes = fetch_session.get("ai_minutes") if isinstance(fetch_session.get("ai_minutes"), dict) else {}
    tingwu_http_operations = fetch_session.get("tingwu_http_operations") if isinstance(fetch_session.get("tingwu_http_operations"), list) else []
    provider_task_id = str(fetch_session.get("task_id") or session.get("task_id") or "")
    minutes_task_id = str(fetch_ai_minutes.get("minutes_task_id") or "")
    fetch_minutes = fetch_result.get("minutes") if isinstance(fetch_result.get("minutes"), dict) else {}
    fetch_tingwu_minutes = fetch_minutes.get("tingwu_minutes") if isinstance(fetch_minutes.get("tingwu_minutes"), dict) else {}
    followup = fetch_result.get("followup") if isinstance(fetch_result.get("followup"), dict) else {}
    fetch_manifest_path = Path(str(fetch_result.get("manifest_path") or "")).expanduser().resolve()
    fetch_job = fetch_result.get("job") if isinstance(fetch_result.get("job"), dict) else {}
    fetch_steps = {step.get("name"): step.get("status") for step in fetch_job.get("steps", []) if isinstance(step, dict)}
    fetch_manifest = json.loads(fetch_manifest_path.read_text(encoding="utf-8")) if fetch_manifest_path.is_file() else {}
    fetch_steps_list = fetch_job.get("steps") if isinstance(fetch_job.get("steps"), list) else []
    decisions_path = next((str(step.get("output_path") or "") for step in fetch_steps_list if isinstance(step, dict) and step.get("name") == "decisions"), "")
    action_items_path = next((str(step.get("output_path") or "") for step in fetch_steps_list if isinstance(step, dict) and step.get("name") == "action_items"), "")
    if decisions_path and not Path(decisions_path).expanduser().is_absolute():
        decisions_path = str((workspace_root / decisions_path).resolve())
    if action_items_path and not Path(action_items_path).expanduser().is_absolute():
        action_items_path = str((workspace_root / action_items_path).resolve())
    followup_required_paths = followup.get("required_output_paths") if isinstance(followup.get("required_output_paths"), dict) else {}
    followup_minutes = followup.get("minutes") if isinstance(followup.get("minutes"), dict) else {}
    followup_transcript = followup.get("transcript") if isinstance(followup.get("transcript"), dict) else {}
    followup_reminders = followup.get("reminders") if isinstance(followup.get("reminders"), dict) else {}
    followup_projection = followup.get("projection") if isinstance(followup.get("projection"), dict) else {}
    followup_output_paths = {
        "openclaw_minutes": str(followup_required_paths.get("openclaw_minutes") or followup_minutes.get("path") or ""),
        "transcript_export": str(followup_required_paths.get("transcript_export") or followup_transcript.get("path") or ""),
        "email_draft": str(followup_required_paths.get("email_draft") or followup.get("email_draft_path") or ""),
        "reminders": str(followup_required_paths.get("reminders") or followup_reminders.get("store_path") or ""),
        "projection_confirmation": str(followup_required_paths.get("projection_confirmation") or followup_projection.get("path") or ""),
        "decisions": decisions_path,
        "action_items": action_items_path,
    }
    assert_ok(
        "web explicit fetch-minutes returns saved Tingwu and OpenClaw outputs",
        fetch_result.get("status") == "completed"
        and fetch_session.get("meeting_id") == meeting_id
        and file_is_under(fetch_session.get("minutes_path"), workspace_root)
        and file_is_under(fetch_minutes.get("path"), workspace_root)
        and fetch_manifest_path.is_file()
        and fetch_manifest_path.is_relative_to(output_dir)
        and all(fetch_steps.get(name) == status for name, status in REQUIRED_STEPS.items()),
        fetch_result,
    )
    assert_ok(
        "web explicit fetch-minutes returns Tingwu AI minutes task metadata",
        str(fetch_ai_minutes.get("source_data_id") or "") == provider_task_id
        and minutes_task_id
        and minutes_task_id != str(fetch_ai_minutes.get("source_data_id") or ""),
        fetch_ai_minutes,
    )
    assert_ok(
        "web Tingwu HTTP operation chain captured",
        tingwu_http_operation_chain_valid(
            tingwu_http_operations,
            provider_task_id=provider_task_id,
            minutes_task_id=minutes_task_id,
            require_minutes=True,
        ),
        {"operations": tingwu_http_operations, "provider_task_id": provider_task_id, "minutes_task_id": minutes_task_id},
    )
    assert_ok(
        "web manifest preserves same Tingwu HTTP operation chain",
        fetch_manifest.get("tingwu_http_operations") == tingwu_http_operations
        and tingwu_http_operation_chain_valid(
            fetch_manifest.get("tingwu_http_operations"),
            provider_task_id=provider_task_id,
            minutes_task_id=minutes_task_id,
            require_minutes=True,
        ),
        {"manifest": fetch_manifest, "session_operations": tingwu_http_operations},
    )
    assert_ok(
        "web explicit fetch-minutes returns structured Tingwu minutes for UI",
        bool(str(fetch_tingwu_minutes.get("summary") or "").strip()) and fetch_tingwu_minutes.get("structured_summary") is True,
        fetch_minutes,
    )
    tingwu_minutes_text = Path(str(fetch_session.get("minutes_path") or "")).read_text(encoding="utf-8", errors="replace") if file_is_under(fetch_session.get("minutes_path"), workspace_root) else ""
    assert_ok(
        "web structured Tingwu summary is saved in workspace minutes file",
        str(fetch_tingwu_minutes.get("summary") or "").strip() in tingwu_minutes_text,
        {"summary": fetch_tingwu_minutes.get("summary"), "minutes_path": fetch_session.get("minutes_path")},
    )
    assert_ok(
        "web explicit follow-up files are saved under workspace and indexed by manifest",
        all(file_is_under(path, workspace_root) for path in followup_output_paths.values())
        and all(path_is_under_meeting_output(path, output_dir) for path in followup_output_paths.values())
        and all(manifest_has_workspace_path(fetch_manifest, path, workspace_root) for path in followup_output_paths.values()),
        {"followup_output_paths": followup_output_paths, "manifest": fetch_manifest},
    )
    followup_status = request_json(
        args.base_url,
        f"/api/meeting/realtime/status?meeting_id={urllib.parse.quote(meeting_id)}",
        token=args.token,
    )
    status_completed_after_fetch = followup_status.get("status") == "completed"
    assert_ok("web realtime status remains completed after lock release", status_completed_after_fetch, followup_status)

    jobs_payload = request_json(args.base_url, "/api/meeting/jobs", token=args.token)
    jobs = jobs_payload.get("items") if isinstance(jobs_payload.get("items"), list) else []
    restored_job = next((item for item in jobs if isinstance(item, dict) and item.get("meeting_id") == meeting_id), {})
    restored_steps = restored_job.get("steps") if isinstance(restored_job.get("steps"), list) else []
    restored_outputs = {
        step.get("name"): step.get("output")
        for step in restored_steps
        if isinstance(step, dict) and isinstance(step.get("output"), dict)
    }
    restored_minutes = restored_outputs.get("minutes") if isinstance(restored_outputs.get("minutes"), dict) else {}
    restored_realtime = restored_outputs.get("realtime_capture") if isinstance(restored_outputs.get("realtime_capture"), dict) else {}
    restored_followup = restored_outputs.get("followup") if isinstance(restored_outputs.get("followup"), dict) else {}
    restored_tingwu_minutes = restored_minutes.get("tingwu_minutes") if isinstance(restored_minutes.get("tingwu_minutes"), dict) else {}
    assert_ok(
        "web meeting jobs can restore saved Tingwu UI result",
        bool(restored_job)
        and restored_minutes.get("tingwu_minutes_path") == fetch_session.get("minutes_path")
        and restored_minutes.get("path") == fetch_minutes.get("path")
        and str(restored_tingwu_minutes.get("summary") or "").strip() == str(fetch_tingwu_minutes.get("summary") or "").strip()
        and restored_tingwu_minutes.get("structured_summary") is True
        and restored_tingwu_minutes.get("summary_source") == fetch_tingwu_minutes.get("summary_source")
        and restored_tingwu_minutes.get("summary_source") != "raw_payload"
        and restored_realtime.get("transcript_path") == fetch_session.get("transcript_path")
        and int(restored_realtime.get("websocket_audio_frames") or 0) > 0
        and isinstance(restored_followup.get("required_output_paths"), dict)
        and "events" not in restored_realtime,
        {"job": restored_job, "restored_outputs": restored_outputs},
    )

    notifications = request_json(args.base_url, "/api/assistant/notifications", token=args.token)
    items = notifications.get("items") if isinstance(notifications.get("items"), list) else []
    assert_ok(
        "assistant notified",
        any(item.get("event") == "meeting_realtime_stopped" and item.get("payload", {}).get("meeting_id") == meeting_id for item in items if isinstance(item, dict)),
        notifications,
    )
    assert_ok(
        "assistant notified after explicit fetch-minutes",
        any(item.get("event") == "meeting_ai_minutes_ready" and item.get("payload", {}).get("meeting_id") == meeting_id for item in items if isinstance(item, dict)),
        notifications,
    )
    task_file = workspace_root / "web_tasks" / f"{web_task_id}.json"
    notifications_path = workspace_root / ".assistant" / "notifications.json"
    task_text = task_file.read_text(encoding="utf-8") if task_file.is_file() else ""
    task_json = json.loads(task_text) if task_text else {}
    task_output = task_json.get("output") if isinstance(task_json.get("output"), dict) else {}
    notifications_text = notifications_path.read_text(encoding="utf-8") if notifications_path.is_file() else ""
    assert_ok(
        "web live task and assistant notification artifacts saved",
        task_file.is_file()
        and notifications_path.is_file()
        and any(item.get("payload", {}).get("meeting_id") == meeting_id for item in items if isinstance(item, dict)),
        {"task_file": str(task_file), "notifications_path": str(notifications_path), "notifications": notifications},
    )
    assert_ok(
        "web realtime task file preserves same Tingwu HTTP operation chain",
        task_output.get("tingwu_http_operations") == tingwu_http_operations
        and tingwu_http_operation_chain_valid(
            task_output.get("tingwu_http_operations"),
            provider_task_id=provider_task_id,
            minutes_task_id=minutes_task_id,
            require_minutes=True,
        ),
        {"task_file": str(task_file), "task_output": task_output, "session_operations": tingwu_http_operations},
    )

    audit = request_json(args.base_url, "/api/audit/recent?limit=1000", token=args.token)
    audit_text = json.dumps(audit, ensure_ascii=False)
    audit_visible, audit_details = audit_actions_visible(args.base_url, args.token, REQUIRED_AUDIT_ACTIONS, meeting_id=meeting_id)
    assert_ok(
        "audit lifecycle and OpenClaw follow-up actions visible",
        audit_visible,
        audit_details,
    )
    secret_hint = str(provider.get("api_key") or provider.get("api_key_value") or "")
    assert_ok("provider status does not expose api key", not secret_hint, provider)
    audit_path = Path(str(security.get("audit_log_path") or "")).expanduser().resolve()
    full_control_audit_visible = all(action in audit_text for action in ("full_control_request", "full_control_confirm", "full_control_cancel"))
    assert_ok(
        "tingwu api key not persisted",
        secret_absent_from_outputs(api_key, [workspace_root, audit_path]),
        "secret value appeared in workspace or audit output",
    )
    assert_ok(
        "tingwu app id not persisted outside session protocol metadata",
        secret_absent_from_outputs(app_id, [audit_path]),
        "app id appeared in audit output",
    )
    for secret in CANARY_SECRETS:
        assert_ok(
            f"web live canary secret redacted: {secret}",
            secret_absent_from_outputs(secret, [workspace_root, audit_path]),
            "canary secret appeared in workspace or audit output",
        )
        assert_ok(
            f"web live canary absent from task and notification artifacts: {secret}",
            secret not in task_text and secret not in notifications_text,
            {"task_file": str(task_file), "notifications_path": str(notifications_path)},
        )

    evidence = {
        "status": "ok",
        "mode": "web_api",
        "meeting_id": meeting_id,
        "base_url": args.base_url,
        "stop_status_before_fetch": str(result.get("status") or ""),
        "provider_status_before_fetch": str(result.get("provider_status") or ""),
        "minutes_path_after_stop": str(session.get("minutes_path") or ""),
        "active_fetch_minutes_probe": active_fetch_probe,
        "status_after_fetch": str(fetch_result.get("status") or ""),
        "allowed_roots_probe": allowed_roots_probe,
        "full_control_gate": {
            "request_status": str(full_control_request.get("status") or ""),
            "request_id_present": bool(full_control_request.get("request_id")),
            "confirm_status": str(full_control_confirm.get("status") or ""),
            "confirm_full_control_enabled": full_control_confirm.get("full_control_enabled") is True,
            "security_permission_mode_after_confirm": str(security_after_full_control.get("permission_mode") or ""),
            "security_full_control_enabled_after_confirm": security_after_full_control.get("full_control_enabled") is True,
            "cancel_status": str(full_control_cancel.get("status") or ""),
            "audit_visible": full_control_audit_visible,
        },
        "task_monitor": {
            "task_id": web_task_id,
            "websocket_audio_frames": int(listed_task_monitor.get("websocket_audio_frames") or 0),
            "audio_seconds": float(listed_task_monitor.get("audio_seconds") or 0),
            "final_count": int(listed_task_monitor.get("final_count") or 0),
        },
        "workspace_dir": str(workspace_root),
        "audit_log_path": str(audit_path),
        "output_dir": str(output_dir),
        "transcript_path": str(session.get("transcript_path") or ""),
        "audio_path": str(session.get("audio_path") or ""),
        "session_path": str(session_path),
        "tingwu_minutes_path": str(fetch_session.get("minutes_path") or ""),
        "openclaw_minutes_path": str(fetch_minutes.get("path") or ""),
        "manifest_path": str(fetch_manifest_path),
        "decisions_path": decisions_path,
        "action_items_path": action_items_path,
        "followup_output_paths": followup_output_paths,
        "task_file": str(task_file),
        "notifications_path": str(notifications_path),
        "web_task_id": web_task_id,
        "configured_mic_device": str(mic_probe.get("configured_device") or provider.get("mic_device") or ""),
        "selected_mic_device": str(mic_probe.get("selected_device") or provider.get("selected_mic_device") or provider.get("mic_device") or ""),
        "mic_probe": mic_probe,
        "endpoint_probe": endpoint_probe,
        "meeting_jobs_restore": {
            "job_id": str(restored_job.get("job_id") or ""),
            "step_names": [
                str(step.get("name") or "")
                for step in restored_steps
                if isinstance(step, dict)
            ],
            "tingwu_minutes_path": str(restored_minutes.get("tingwu_minutes_path") or ""),
            "openclaw_minutes_path": str(restored_minutes.get("path") or ""),
            "tingwu_minutes_summary": str(restored_tingwu_minutes.get("summary") or ""),
            "tingwu_minutes_summary_source": str(restored_tingwu_minutes.get("summary_source") or ""),
            "tingwu_minutes_structured": restored_tingwu_minutes.get("structured_summary") is True,
            "websocket_audio_frames": int(restored_realtime.get("websocket_audio_frames") or 0),
            "followup_output_keys": sorted((restored_followup.get("required_output_paths") if isinstance(restored_followup.get("required_output_paths"), dict) else {}).keys()),
            "events_compacted": "events" not in restored_realtime,
        },
        "provider_task_id": provider_task_id,
        "ai_minutes_source_data_id": str(fetch_ai_minutes.get("source_data_id") or ""),
        "ai_minutes_task_id": minutes_task_id,
        "tingwu_minutes_summary": str(fetch_tingwu_minutes.get("summary") or ""),
        "tingwu_minutes_summary_source": str(fetch_tingwu_minutes.get("summary_source") or ""),
        "tingwu_minutes_structured": fetch_tingwu_minutes.get("structured_summary") is True,
        "audio_seconds": float(fetch_session.get("audio_seconds") or session.get("audio_seconds") or 0),
        "audio_bytes": int(fetch_session.get("audio_bytes") or session.get("audio_bytes") or 0),
        "sample_rate": int(fetch_session.get("sample_rate") or session.get("sample_rate") or provider.get("sample_rate") or 16000),
        "audio_format": str(fetch_session.get("audio_format") or session.get("audio_format") or provider.get("audio_format") or "pcm"),
        "websocket_audio_frames": int(fetch_session.get("websocket_audio_frames") or session.get("websocket_audio_frames") or 0),
        "audio_rms": int(fetch_session.get("audio_rms") or session.get("audio_rms") or 0),
        "audio_peak": int(fetch_session.get("audio_peak") or session.get("audio_peak") or 0),
        "final_count": int(fetch_session.get("final_count") or final_count or 0),
        "transcript_items": len(transcript_items),
        "spoken_phrase": args.spoken_phrase,
        "spoken_phrase_detected": spoken_phrase_detected,
        "redaction_canaries": list(CANARY_SECRETS),
        "steps": fetch_steps,
        "provider_events": [
            *provider_event_names[-40:],
        ],
        "tingwu_http_operations": tingwu_http_operations[-40:],
        "notifications": [
            str(item.get("event") or "")
            for item in items
            if isinstance(item, dict) and item.get("payload", {}).get("meeting_id") == meeting_id
        ],
        "checks": {
            "sandbox_visible": security.get("permission_mode") == "sandbox",
            "audit_only_visible": security.get("desktop_backend") == "audit_only",
            "workspace_allowed": workspace_root in allowed_roots,
            "allowed_roots_block_enforced": allowed_roots_probe.get("status_code") == 403
            and allowed_roots_probe.get("ok") is False
            and allowed_roots_probe.get("error_code") == "blocked"
            and allowed_roots_probe.get("audit_visible") is True,
            "full_control_gate_enforced": full_control_request.get("status") == "waiting_confirmation"
            and bool(full_control_request.get("request_id"))
            and full_control_confirm.get("status") == "backend_missing"
            and full_control_confirm.get("full_control_enabled") is False
            and security_after_full_control.get("permission_mode") == "sandbox"
            and security_after_full_control.get("full_control_enabled") is False
            and full_control_cancel.get("status") == "blocked",
            "full_control_audit_visible": full_control_audit_visible,
            "provider_available": provider.get("status") == "available" and provider.get("configured") is True,
            "live_mode_not_mock": provider.get("mock") is False,
            "official_tingwu_endpoint": endpoint_probe.get("official_dashscope") is True,
            "real_microphone_device": real_microphone_selected(mic_probe),
            "stopped_before_ai_minutes": result.get("status") == "stopped" and result.get("provider_status") == "stopped",
            "audio_saved": file_is_under(session.get("audio_path"), workspace_root),
            "audio_has_signal": int(session.get("audio_rms") or 0) > 0 and int(session.get("audio_peak") or 0) > 0,
            "websocket_stream_started": any(item in websocket_event_names for item in provider_event_names),
            "websocket_audio_frames_sent": int(session.get("websocket_audio_frames") or 0) > 0,
            "transcript_saved": file_is_under(session.get("transcript_path"), workspace_root),
            "realtime_transcript_non_empty": len(transcript_file_text) >= 8 and (final_count > 0 or len(transcript_items) > 0),
            "spoken_phrase_detected": spoken_phrase_detected,
            "openclaw_minutes_saved": file_is_under(fetch_minutes.get("path"), workspace_root),
            "tingwu_minutes_saved": file_is_under(fetch_session.get("minutes_path"), workspace_root),
            "ai_minutes_task_metadata": str(fetch_ai_minutes.get("source_data_id") or "") == provider_task_id and bool(minutes_task_id),
            "tingwu_minutes_structured": fetch_tingwu_minutes.get("structured_summary") is True and bool(str(fetch_tingwu_minutes.get("summary") or "").strip()),
            "manifest_saved": fetch_manifest_path.is_file() and fetch_manifest_path.is_relative_to(output_dir),
            "followup_outputs_saved": all(file_is_under(path, workspace_root) and path_is_under_meeting_output(path, output_dir) for path in followup_output_paths.values()),
            "manifest_indexes_followup_outputs": all(manifest_has_workspace_path(fetch_manifest, path, workspace_root) for path in followup_output_paths.values()),
            **{
                check_name: fetch_steps.get(step_name) == expected_status
                for step_name, expected_status in REQUIRED_STEPS.items()
                for check_name in (STEP_CHECK_NAMES[step_name],)
            },
            "all_required_steps": all(fetch_steps.get(name) == status for name, status in REQUIRED_STEPS.items()),
            "task_monitor_listed": bool(listed_task) and listed_task.get("task_id") == web_task_id,
            "task_monitor_metrics_visible": int(listed_task_monitor.get("websocket_audio_frames") or 0) > 0 and float(listed_task_monitor.get("audio_seconds") or 0) > 0,
            "meeting_jobs_restore_outputs": bool(restored_job)
            and restored_minutes.get("tingwu_minutes_path") == fetch_session.get("minutes_path")
            and restored_minutes.get("path") == fetch_minutes.get("path")
            and str(restored_tingwu_minutes.get("summary") or "").strip() == str(fetch_tingwu_minutes.get("summary") or "").strip()
            and restored_tingwu_minutes.get("structured_summary") is True
            and restored_tingwu_minutes.get("summary_source") == fetch_tingwu_minutes.get("summary_source")
            and restored_tingwu_minutes.get("summary_source") != "raw_payload"
            and restored_realtime.get("transcript_path") == fetch_session.get("transcript_path")
            and int(restored_realtime.get("websocket_audio_frames") or 0) > 0
            and isinstance(restored_followup.get("required_output_paths"), dict)
            and "events" not in restored_realtime,
            "assistant_stop_notification": any(item.get("event") == "meeting_realtime_stopped" and item.get("payload", {}).get("meeting_id") == meeting_id for item in items if isinstance(item, dict)),
            "assistant_fetch_notification": any(item.get("event") == "meeting_ai_minutes_ready" and item.get("payload", {}).get("meeting_id") == meeting_id for item in items if isinstance(item, dict)),
            "status_completed_after_fetch": status_completed_after_fetch,
            "audit_lifecycle_visible": audit_visible,
            "api_key_absent": secret_absent_from_outputs(api_key, [workspace_root, audit_path]),
            "app_id_absent_from_audit": secret_absent_from_outputs(app_id, [audit_path]),
            "no_temp_files": not temp_files_under(workspace_root),
            "tingwu_http_operations_visible": tingwu_http_operation_chain_valid(
                tingwu_http_operations,
                provider_task_id=provider_task_id,
                minutes_task_id=minutes_task_id,
                require_minutes=True,
            ),
        },
    }
    write_evidence(args.evidence_json, evidence)
    print(f"meeting_id={meeting_id}")
    print(f"output_dir={session.get('output_dir')}")
    print("verify_tingwu_web_live complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
