#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.parse
import wave
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "lelamp_runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

if sys.version_info < (3, 12):
    raise SystemExit("verify_tingwu_live requires Python >= 3.12. Run it with lelamp_runtime/.venv/bin/python.")

from lelamp.office_agent.audit import AuditLogger  # noqa: E402
from lelamp.office_agent.config import OfficeAgentConfig, is_placeholder_tingwu_credential, tingwu_credential_kind, tingwu_credential_next_actions  # noqa: E402
from lelamp.office_agent.tingwu_meeting import TINGWU_WORKSPACE_LOCK_NAME, TingwuMeetingError, TingwuMeetingProvider, normalize_minutes_payload  # noqa: E402
from lelamp.office_agent.workspace import Workspace  # noqa: E402

CANARY_SECRETS = ("live-title-token", "live-title-password", "live-participant-password")
DEFAULT_SPOKEN_PHRASE = "乐灯听悟验收测试"
FAKE_MIC_DEVICES = {"fake-mic", "mock", "mock-mic"}
OFFICIAL_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
OFFICIAL_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


def assert_ok(name: str, condition: bool, details: object = "") -> None:
    if not condition:
        raise AssertionError(f"{name} failed: {details}")
    print(f"ok - {name}")


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


def wait_for_stopped(provider: TingwuMeetingProvider, meeting_id: str, current: dict[str, object], *, timeout: float = 30.0) -> dict[str, object]:
    if str(current.get("status") or "") not in {"starting", "running", "stopping"}:
        return current
    deadline = time.monotonic() + timeout
    last = current
    while time.monotonic() < deadline:
        last = provider.session_status(meeting_id)
        if str(last.get("status") or "") in {"stopped", "failed", "completed"}:
            return last
        time.sleep(0.5)
    return last


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
    config: OfficeAgentConfig,
    status: dict[str, object],
    endpoint_probe: dict[str, object],
    error: object,
) -> dict[str, object]:
    details = getattr(error, "details", {})
    if not isinstance(details, dict):
        details = {}
    detail_mic_probe = details.get("mic_probe") if isinstance(details.get("mic_probe"), dict) else {}
    detail_capture_probe = details.get("capture_probe") if isinstance(details.get("capture_probe"), dict) else {}
    status_mic_probe = status.get("mic_probe") if isinstance(status.get("mic_probe"), dict) else {}
    mic_probe = detail_mic_probe or status_mic_probe
    capture_probe = detail_capture_probe or (mic_probe.get("capture_probe") if isinstance(mic_probe.get("capture_probe"), dict) else {})
    selected = str(mic_probe.get("selected_device") or status.get("selected_mic_device") or config.mic_device)
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
        "mode": "direct_provider",
        "error": str(error),
        "error_details": details,
        "workspace_dir": str(config.workspace_dir),
        "audit_log_path": str(config.audit_log_path),
        "configured_mic_device": config.mic_device,
        "selected_mic_device": selected,
        "mic_probe": mic_probe,
        "endpoint_probe": endpoint_probe,
        "sample_rate": config.tingwu_sample_rate,
        "audio_format": config.tingwu_audio_format,
        "spoken_phrase": args.spoken_phrase,
        "spoken_phrase_detected": False,
        "redaction_canaries": list(CANARY_SECRETS),
        "checks": {
            "provider_configured": status.get("configured") is True,
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
    config: OfficeAgentConfig,
    api_key: str,
    app_id: str,
) -> dict[str, object]:
    endpoint_probe = tingwu_endpoint_probe({
        "http_url": config.tingwu_http_url,
        "ws_url": config.tingwu_ws_url,
    })
    credential_diagnostics = {
        "api_key_kind": tingwu_credential_kind(api_key),
        "app_id_kind": tingwu_credential_kind(app_id, role="app_id"),
    }
    return {
        "status": "failed",
        "mode": "direct_provider",
        "error": "Set TINGWU_API_KEY or DASHSCOPE_API_KEY, and TINGWU_APP_ID or TINGWU_MEETING_APP_ID before running live verification.",
        "credential_diagnostics": credential_diagnostics,
        "next_actions": tingwu_credential_next_actions(
            str(credential_diagnostics["api_key_kind"]),
            str(credential_diagnostics["app_id_kind"]),
        ),
        "workspace_dir": str(config.workspace_dir),
        "audit_log_path": str(config.audit_log_path),
        "configured_mic_device": config.mic_device,
        "selected_mic_device": "",
        "mic_probe": {},
        "endpoint_probe": endpoint_probe,
        "sample_rate": config.tingwu_sample_rate,
        "audio_format": config.tingwu_audio_format,
        "spoken_phrase": args.spoken_phrase,
        "spoken_phrase_detected": False,
        "redaction_canaries": list(CANARY_SECRETS),
        "checks": {
            "provider_configured": False,
            "tingwu_api_key_configured": not is_placeholder_tingwu_credential(api_key),
            "tingwu_app_id_configured": not is_placeholder_tingwu_credential(app_id),
            "official_tingwu_endpoint": endpoint_probe.get("official_dashscope") is True,
            "real_microphone_device": False,
            "realtime_start_succeeded": False,
            "spoken_phrase_detected": False,
        },
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a live Tongyi Tingwu realtime meeting verification using the configured microphone."
    )
    parser.add_argument("--title", default="LeLamp Tingwu live verification")
    parser.add_argument("--participant", action="append", default=["LiveTester"])
    parser.add_argument("--seconds", type=int, default=12)
    parser.add_argument("--workspace", default="")
    parser.add_argument("--audit-log", default="")
    parser.add_argument("--evidence-json", default="", help="Write a machine-readable live verification evidence report.")
    parser.add_argument("--spoken-phrase", default=DEFAULT_SPOKEN_PHRASE, help="Required phrase to speak during capture and verify in the realtime transcript.")
    args = parser.parse_args()

    api_key = os.getenv("TINGWU_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    app_id = os.getenv("TINGWU_APP_ID") or os.getenv("TINGWU_MEETING_APP_ID")
    workspace_root = Path(args.workspace).expanduser().resolve() if args.workspace else Path(tempfile.mkdtemp(prefix="lelamp-tingwu-live-")) / "workspace"
    audit_path = Path(args.audit_log).expanduser().resolve() if args.audit_log else workspace_root.parent / "audit.jsonl"
    config = replace(
        OfficeAgentConfig.from_env(),
        workspace_dir=workspace_root,
        audit_log_path=audit_path,
        allowed_roots=(workspace_root,),
        tingwu_api_key=api_key,
        tingwu_app_id=app_id,
        tingwu_mock=False,
    ).normalized()
    if is_placeholder_tingwu_credential(api_key) or is_placeholder_tingwu_credential(app_id):
        evidence = missing_credentials_evidence(args=args, config=config, api_key=str(api_key or ""), app_id=str(app_id or ""))
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        write_evidence(args.evidence_json, evidence)
        raise SystemExit(evidence["error"])

    audit = AuditLogger(config.audit_log_path)
    workspace = Workspace(config.workspace_dir, config.allowed_roots, audit)
    provider = TingwuMeetingProvider(config, workspace, audit)

    status = provider.status()
    assert_ok("provider configured", status["status"] == "available", status)
    endpoint_probe = tingwu_endpoint_probe(status)
    assert_ok("official Tingwu endpoints configured", endpoint_probe.get("official_dashscope") is True, endpoint_probe)
    status_mic_probe = status.get("mic_probe") if isinstance(status.get("mic_probe"), dict) else {}
    assert_ok("real microphone selected", real_microphone_selected(status_mic_probe, require_capture_probe=False), status)
    print(f"Recording from {config.mic_device} at {config.tingwu_sample_rate} Hz for {args.seconds}s.")
    print(f"Speak this phrase after this line: {args.spoken_phrase}")
    live_title, live_participants = with_canary_inputs(args.title, args.participant)

    try:
        session = provider.start_realtime_meeting(
            title=live_title,
            participants=live_participants,
            max_seconds=max(3, args.seconds),
        )
    except TingwuMeetingError as exc:
        write_evidence(
            args.evidence_json,
            start_failure_evidence(
                args=args,
                config=config,
                status=status,
                endpoint_probe=endpoint_probe,
                error=exc,
            ),
        )
        raise
    meeting_id = str(session["meeting_id"])
    deadline = time.monotonic() + max(3, args.seconds)
    while time.monotonic() < deadline:
        status = provider.session_status(meeting_id)
        print(
            f"status={status.get('status')} finals={status.get('final_count')} "
            f"audio_seconds={status.get('audio_seconds')} partial={status.get('partial_text')!r}"
        )
        time.sleep(1)

    result = provider.stop_realtime_meeting(meeting_id, wait_seconds=20)
    result = wait_for_stopped(provider, meeting_id, result, timeout=30)
    task_payload = result.get("task_payload") if isinstance(result.get("task_payload"), dict) else {}
    provider_events = task_payload.get("events") if isinstance(task_payload.get("events"), list) else []
    mic_probe = task_payload.get("mic_probe") if isinstance(task_payload.get("mic_probe"), dict) else status_mic_probe
    provider_event_names = [
        str(item.get("event") or "")
        for item in provider_events
        if isinstance(item, dict)
    ]
    transcript_path = Path(str(result["transcript_path"]))
    audio_path = Path(str(result["audio_path"]))
    output_dir = Path(str(result["output_dir"]))
    session_path = output_dir / "session.json"
    transcript_text = str(result.get("realtime_transcript") or "").strip()
    transcript_items = result.get("transcript") if isinstance(result.get("transcript"), list) else []
    assert_ok("live session stopped before AI minutes fetch", result["status"] == "stopped", result)
    assert_ok("live provider captured from real microphone", real_microphone_selected(mic_probe), mic_probe)
    assert_ok("live audio saved", audio_path.is_file() and audio_path.stat().st_size > 44 and float(result.get("audio_seconds") or 0) > 0, result)
    with wave.open(str(audio_path), "rb") as audio:
        assert_ok(
            "live audio wav format",
            audio.getframerate() == config.tingwu_sample_rate
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
    assert_ok("live audio has signal", int(result.get("audio_rms") or 0) > 0 and int(result.get("audio_peak") or 0) > 0, result)
    assert_ok("live WebSocket audio frames sent", int(result.get("websocket_audio_frames") or 0) > 0, result)
    assert_ok("live transcript saved", transcript_path.is_file() and transcript_path.stat().st_size > 0, transcript_path)
    transcript_file_text = transcript_path.read_text(encoding="utf-8").strip()
    final_count = int(result.get("final_count") or 0)
    assert_ok("live transcript file has speech text", len(transcript_file_text) >= 8, transcript_path)
    assert_ok("live realtime transcript callbacks produced content", final_count > 0 or len(transcript_items) > 0, result)
    spoken_phrase_detected = transcript_contains_phrase(transcript_file_text, args.spoken_phrase)
    if not spoken_phrase_detected:
        lock_path = config.workspace_dir / "meetings" / TINGWU_WORKSPACE_LOCK_NAME
        lock_released = provider._workspace_lock_fd is None and not provider._workspace_meeting_lock_held_elsewhere()
        write_evidence(
            args.evidence_json,
            {
                "status": "failed",
                "mode": "direct_provider",
                "meeting_id": meeting_id,
                "workspace_dir": str(config.workspace_dir),
                "audit_log_path": str(config.audit_log_path),
                "output_dir": str(output_dir),
                "transcript_path": str(transcript_path),
                "audio_path": str(audio_path),
                "session_path": str(session_path),
                "configured_mic_device": config.mic_device,
                "provider_task_id": str(result.get("task_id") or ""),
                "selected_mic_device": str(mic_probe.get("selected_device") or status.get("selected_mic_device") or config.mic_device),
                "mic_probe": mic_probe,
                "endpoint_probe": endpoint_probe,
                "workspace_lock_path": str(lock_path),
                "workspace_lock_released": lock_released,
                "audio_seconds": float(result.get("audio_seconds") or 0),
                "audio_bytes": int(result.get("audio_bytes") or 0),
                "sample_rate": int(result.get("sample_rate") or config.tingwu_sample_rate),
                "audio_format": str(result.get("audio_format") or config.tingwu_audio_format),
                "websocket_audio_frames": int(result.get("websocket_audio_frames") or 0),
                "audio_rms": int(result.get("audio_rms") or 0),
                "audio_peak": int(result.get("audio_peak") or 0),
                "final_count": final_count,
                "transcript_items": len(transcript_items),
                "spoken_phrase": args.spoken_phrase,
                "spoken_phrase_detected": False,
                "spoken_phrase_failure": spoken_phrase_failure_details(transcript_file_text, args.spoken_phrase, transcript_path),
                "redaction_canaries": list(CANARY_SECRETS),
                "provider_events": [*provider_event_names[-40:]],
                "checks": {
                    "provider_configured": True,
                    "official_tingwu_endpoint": endpoint_probe.get("official_dashscope") is True,
                    "real_microphone_device": real_microphone_selected(mic_probe),
                    "stopped_before_ai_minutes": result.get("status") == "stopped",
                    "audio_saved": audio_path.is_file() and audio_path.stat().st_size > 44,
                    "audio_has_signal": int(result.get("audio_rms") or 0) > 0 and int(result.get("audio_peak") or 0) > 0,
                    "websocket_stream_started": any(item in {"websocket_open", "websocket_started"} for item in provider_event_names),
                    "websocket_audio_frames_sent": int(result.get("websocket_audio_frames") or 0) > 0,
                    "transcript_saved": transcript_path.is_file() and transcript_path.stat().st_size > 0,
                    "realtime_transcript_non_empty": len(transcript_file_text) >= 8 and (final_count > 0 or len(transcript_items) > 0),
                    "spoken_phrase_detected": False,
                    "workspace_lock_released": lock_released,
                },
            },
        )
    assert_ok(
        "live transcript contains requested spoken phrase",
        spoken_phrase_detected,
        spoken_phrase_failure_details(transcript_file_text, args.spoken_phrase, transcript_path),
    )
    assert_ok("live minutes not fetched during stop", not result.get("minutes_path"), result)
    assert_ok(
        "live provider events persisted",
        any(item in {"meeting_started", "transcript", "meeting_stopped", "websocket_open", "websocket_started", "tingwu_event"} for item in provider_event_names),
        provider_events,
    )
    assert_ok(
        "live WebSocket stream started",
        any(item in {"websocket_open", "websocket_started"} for item in provider_event_names),
        provider_events,
    )
    assert_ok("live session metadata saved", session_path.is_file(), session_path)
    persisted = json.loads(session_path.read_text(encoding="utf-8"))
    persisted_payload = persisted.get("task_payload") if isinstance(persisted.get("task_payload"), dict) else {}
    persisted_events = persisted_payload.get("events") if isinstance(persisted_payload.get("events"), list) else []
    assert_ok(
        "persisted session matches live result",
        persisted.get("meeting_id") == meeting_id
        and persisted.get("status") == "stopped"
        and persisted.get("transcript_path") == str(transcript_path)
        and persisted.get("audio_path") == str(audio_path)
        and not persisted.get("minutes_path"),
        persisted,
    )
    assert_ok(
        "persisted session includes provider events",
        any(
            str(item.get("event") or "") in {"meeting_started", "transcript", "meeting_stopped", "websocket_open", "websocket_started", "tingwu_event"}
            for item in persisted_events
            if isinstance(item, dict)
        ),
        persisted,
    )
    finalized = provider.finalize_meeting(meeting_id)
    minutes_path = Path(str(finalized["minutes_path"]))
    minutes_text = minutes_path.read_text(encoding="utf-8", errors="replace") if minutes_path.is_file() else ""
    persisted = json.loads(session_path.read_text(encoding="utf-8"))
    tingwu_http_operations = persisted.get("tingwu_http_operations") if isinstance(persisted.get("tingwu_http_operations"), list) else []
    ai_minutes = finalized.get("ai_minutes") if isinstance(finalized.get("ai_minutes"), dict) else {}
    provider_task_id = str(finalized.get("task_id") or result.get("task_id") or "")
    minutes_task_id = str(ai_minutes.get("minutes_task_id") or "")
    assert_ok(
        "live Tingwu HTTP operation chain captured",
        tingwu_http_operation_chain_valid(
            tingwu_http_operations,
            provider_task_id=provider_task_id,
            minutes_task_id=minutes_task_id,
            require_minutes=True,
        ),
        {"operations": tingwu_http_operations, "provider_task_id": provider_task_id, "minutes_task_id": minutes_task_id},
    )
    structured_minutes = normalize_minutes_payload(ai_minutes) if ai_minutes else {"summary": "", "decisions": [], "action_items": []}
    tingwu_minutes_summary = str(structured_minutes.get("summary") or "").strip()
    tingwu_minutes_summary_source = str(structured_minutes.get("summary_source") or "")
    tingwu_minutes_structured = structured_minutes.get("structured_summary") is True
    assert_ok("live session completed after explicit AI minutes fetch", finalized["status"] == "completed", finalized)
    assert_ok("live minutes saved", minutes_path.is_file() and minutes_path.stat().st_size > 0, minutes_path)
    assert_ok(
        "live AI minutes include structured summary",
        bool(tingwu_minutes_summary) and tingwu_minutes_structured,
        {"ai_minutes": ai_minutes, "structured_minutes": structured_minutes},
    )
    assert_ok(
        "live structured Tingwu summary is saved in minutes file",
        tingwu_minutes_summary in minutes_text,
        {"summary": tingwu_minutes_summary, "minutes_path": str(minutes_path)},
    )
    assert_ok(
        "live AI minutes task metadata captured",
        str(ai_minutes.get("source_data_id") or "") == provider_task_id
        and str(ai_minutes.get("minutes_task_id") or "").strip()
        and str(ai_minutes.get("minutes_task_id") or "") != str(ai_minutes.get("source_data_id") or ""),
        ai_minutes,
    )
    assert_ok(
        "persisted session completed after explicit finalize",
        persisted.get("meeting_id") == meeting_id
        and persisted.get("status") == "completed"
        and persisted.get("minutes_path") == str(minutes_path),
        persisted,
    )
    assert_ok(
        "workspace boundary",
        output_dir.is_relative_to(config.workspace_dir)
        and transcript_path.resolve().is_relative_to(config.workspace_dir)
        and audio_path.resolve().is_relative_to(config.workspace_dir)
        and minutes_path.resolve().is_relative_to(config.workspace_dir)
        and session_path.resolve().is_relative_to(config.workspace_dir),
        result,
    )
    audit_text = config.audit_log_path.read_text(encoding="utf-8") if config.audit_log_path.is_file() else ""
    assert_ok(
        "audit lifecycle visible",
        all(action in audit_text for action in ("tingwu.meeting_start", "tingwu.audio_save", "tingwu.meeting_finalize")),
        config.audit_log_path,
    )
    assert_ok(
        "tingwu api key not persisted",
        secret_absent_from_outputs(api_key, [config.workspace_dir, config.audit_log_path]),
        "secret value appeared in workspace or audit output",
    )
    assert_ok(
        "tingwu app id not persisted outside session protocol metadata",
        secret_absent_from_outputs(app_id, [config.audit_log_path]),
        "app id appeared in audit output",
    )
    for secret in CANARY_SECRETS:
        assert_ok(
            f"live canary secret redacted: {secret}",
            secret_absent_from_outputs(secret, [config.workspace_dir, config.audit_log_path]),
            "canary secret appeared in workspace or audit output",
        )
    assert_ok("no temporary artifact files left", not temp_files_under(config.workspace_dir), temp_files_under(config.workspace_dir))
    lock_path = config.workspace_dir / "meetings" / TINGWU_WORKSPACE_LOCK_NAME
    lock_released = provider._workspace_lock_fd is None and not provider._workspace_meeting_lock_held_elsewhere()
    assert_ok(
        "realtime workspace lock released after live stop",
        lock_released,
        lock_path,
    )
    evidence = {
        "status": "ok",
        "mode": "direct_provider",
        "meeting_id": meeting_id,
        "stop_status_before_fetch": str(result.get("status") or ""),
        "minutes_path_after_stop": str(result.get("minutes_path") or ""),
        "status_after_fetch": str(finalized.get("status") or ""),
        "workspace_dir": str(config.workspace_dir),
        "audit_log_path": str(config.audit_log_path),
        "output_dir": str(output_dir),
        "transcript_path": str(transcript_path),
        "audio_path": str(audio_path),
        "minutes_path": str(minutes_path),
        "session_path": str(session_path),
        "configured_mic_device": config.mic_device,
        "selected_mic_device": str(mic_probe.get("selected_device") or status.get("selected_mic_device") or config.mic_device),
        "mic_probe": mic_probe,
        "endpoint_probe": endpoint_probe,
        "provider_task_id": provider_task_id,
        "ai_minutes_source_data_id": str(ai_minutes.get("source_data_id") or ""),
        "ai_minutes_task_id": minutes_task_id,
        "tingwu_minutes_summary": tingwu_minutes_summary,
        "tingwu_minutes_summary_source": tingwu_minutes_summary_source,
        "tingwu_minutes_structured": tingwu_minutes_structured,
        "audio_seconds": float(finalized.get("audio_seconds") or result.get("audio_seconds") or 0),
        "audio_bytes": int(finalized.get("audio_bytes") or result.get("audio_bytes") or 0),
        "sample_rate": int(finalized.get("sample_rate") or result.get("sample_rate") or config.tingwu_sample_rate),
        "audio_format": str(finalized.get("audio_format") or result.get("audio_format") or config.tingwu_audio_format),
        "websocket_audio_frames": int(finalized.get("websocket_audio_frames") or result.get("websocket_audio_frames") or 0),
        "audio_rms": int(finalized.get("audio_rms") or result.get("audio_rms") or 0),
        "audio_peak": int(finalized.get("audio_peak") or result.get("audio_peak") or 0),
        "final_count": int(finalized.get("final_count") or final_count or 0),
        "transcript_items": len(transcript_items),
        "spoken_phrase": args.spoken_phrase,
        "spoken_phrase_detected": spoken_phrase_detected,
        "redaction_canaries": list(CANARY_SECRETS),
        "provider_events": [
            *provider_event_names[-40:],
        ],
        "tingwu_http_operations": tingwu_http_operations[-40:],
        "checks": {
            "provider_configured": True,
            "official_tingwu_endpoint": endpoint_probe.get("official_dashscope") is True,
            "real_microphone_device": real_microphone_selected(mic_probe),
            "stopped_before_ai_minutes": result.get("status") == "stopped",
            "audio_saved": audio_path.is_file() and audio_path.stat().st_size > 44,
            "audio_has_signal": int(result.get("audio_rms") or 0) > 0 and int(result.get("audio_peak") or 0) > 0,
            "websocket_stream_started": any(item in {"websocket_open", "websocket_started"} for item in provider_event_names),
            "websocket_audio_frames_sent": int(result.get("websocket_audio_frames") or 0) > 0,
            "transcript_saved": transcript_path.is_file() and transcript_path.stat().st_size > 0,
            "realtime_transcript_non_empty": len(transcript_file_text) >= 8 and (final_count > 0 or len(transcript_items) > 0),
            "spoken_phrase_detected": spoken_phrase_detected,
            "ai_minutes_saved": minutes_path.is_file() and minutes_path.stat().st_size > 0,
            "tingwu_minutes_structured": tingwu_minutes_structured and bool(tingwu_minutes_summary) and tingwu_minutes_summary in minutes_text,
            "ai_minutes_task_metadata": str(ai_minutes.get("source_data_id") or "") == provider_task_id and bool(minutes_task_id),
            "workspace_boundary": output_dir.is_relative_to(config.workspace_dir),
            "audit_lifecycle_visible": all(action in audit_text for action in ("tingwu.meeting_start", "tingwu.audio_save", "tingwu.meeting_finalize")),
            "api_key_absent": secret_absent_from_outputs(api_key, [config.workspace_dir, config.audit_log_path]),
            "app_id_absent_from_audit": secret_absent_from_outputs(app_id, [config.audit_log_path]),
            "no_temp_files": not temp_files_under(config.workspace_dir),
            "workspace_lock_released": lock_released,
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
    print(f"output_dir={result['output_dir']}")
    print("verify_tingwu_live complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
