#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import wave
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "lelamp_runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

from lelamp.office_agent.audit import AuditLogger  # noqa: E402
from lelamp.office_agent.config import OfficeAgentConfig, is_placeholder_tingwu_credential  # noqa: E402
from lelamp.office_agent.tingwu_meeting import TingwuMeetingProvider, normalize_minutes_payload  # noqa: E402
from lelamp.office_agent.workspace import Workspace  # noqa: E402


DEFAULT_SPOKEN_PHRASE = "乐灯听悟验收测试"


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


def normalize_phrase(value: object) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def transcript_contains_phrase(transcript_text: str, phrase: str) -> bool:
    normalized_phrase = normalize_phrase(phrase)
    if not normalized_phrase:
        return True
    return normalized_phrase in normalize_phrase(transcript_text)


def wait_for_stopped(provider: TingwuMeetingProvider, meeting_id: str, status: dict[str, object], *, timeout: float) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    current = status
    while time.monotonic() < deadline and str(current.get("status") or "") in {"starting", "running", "stopping"}:
        time.sleep(0.5)
        current = provider.session_status(meeting_id)
    return current


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as stream:
        raw_bytes = path.stat().st_size - 44
        by_header = stream.getnframes() / max(1, stream.getframerate())
        by_size = raw_bytes / max(1, stream.getframerate() * stream.getnchannels() * stream.getsampwidth())
        if by_header > 3600 and by_size > 0:
            return round(by_size, 2)
        return round(by_header, 2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Tongyi Tingwu realtime meeting using a local mono PCM WAV file instead of a microphone.")
    parser.add_argument("--audio-file", required=True, help="Mono 16-bit PCM WAV at TINGWU_SAMPLE_RATE, usually 16000 Hz.")
    parser.add_argument("--title", default="LeLamp Tingwu audio-file verification")
    parser.add_argument("--spoken-phrase", default=DEFAULT_SPOKEN_PHRASE)
    parser.add_argument("--workspace", default="")
    parser.add_argument("--audit-log", default="")
    parser.add_argument("--evidence-json", default="")
    args = parser.parse_args()

    audio_file = Path(args.audio_file).expanduser().resolve()
    api_key = os.getenv("TINGWU_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    app_id = os.getenv("TINGWU_APP_ID") or os.getenv("TINGWU_MEETING_APP_ID")
    workspace_root = Path(args.workspace).expanduser().resolve() if args.workspace else Path(tempfile.mkdtemp(prefix="lelamp-tingwu-audio-file-")) / "workspace"
    audit_path = Path(args.audit_log).expanduser().resolve() if args.audit_log else workspace_root.parent / "audit.jsonl"
    config = replace(
        OfficeAgentConfig.from_env(),
        workspace_dir=workspace_root,
        audit_log_path=audit_path,
        allowed_roots=(workspace_root,),
        tingwu_api_key=api_key,
        tingwu_app_id=app_id,
        tingwu_audio_file=str(audio_file),
        tingwu_mock=False,
    ).normalized()
    if is_placeholder_tingwu_credential(api_key) or is_placeholder_tingwu_credential(app_id):
        raise SystemExit("Set TINGWU_API_KEY or DASHSCOPE_API_KEY, and TINGWU_APP_ID or TINGWU_MEETING_APP_ID before running audio-file verification.")

    audit = AuditLogger(config.audit_log_path)
    workspace = Workspace(config.workspace_dir, config.allowed_roots, audit)
    provider = TingwuMeetingProvider(config, workspace, audit)
    status = provider.status()
    assert_ok("provider configured", status.get("status") == "available", status)
    assert_ok("audio file source configured", status.get("audio_source") == "file", status)
    assert_ok("audio file has signal", int(status.get("mic_probe", {}).get("capture_probe", {}).get("audio_rms", 0)) > 0, status)

    duration = wav_seconds(audio_file)
    session = provider.start_realtime_meeting(
        title=args.title,
        participants=["AudioFileTester"],
        max_seconds=max(3, int(duration) + 3),
    )
    meeting_id = str(session["meeting_id"])
    deadline = time.monotonic() + max(6, duration + 8)
    while time.monotonic() < deadline:
        current = provider.session_status(meeting_id)
        print(
            f"status={current.get('status')} finals={current.get('final_count')} "
            f"audio_seconds={current.get('audio_seconds')} partial={current.get('partial_text')!r}"
        )
        if str(current.get("status") or "") in {"stopped", "failed", "completed"}:
            break
        time.sleep(1)

    stopped = provider.stop_realtime_meeting(meeting_id, wait_seconds=20)
    stopped = wait_for_stopped(provider, meeting_id, stopped, timeout=30)
    transcript_path = Path(str(stopped.get("transcript_path") or "")).resolve()
    transcript_text = transcript_path.read_text(encoding="utf-8").strip() if transcript_path.is_file() else ""
    final_count = int(stopped.get("final_count") or 0)
    assert_ok("audio-file session stopped before AI minutes fetch", stopped.get("status") == "stopped", stopped)
    assert_ok("audio-file websocket frames sent", int(stopped.get("websocket_audio_frames") or 0) > 0, stopped)
    assert_ok("audio-file realtime transcript produced content", final_count > 0 and len(transcript_text) >= 8, stopped)
    assert_ok("audio-file transcript contains requested phrase", transcript_contains_phrase(transcript_text, args.spoken_phrase), transcript_text[:500])
    assert_ok("audio-file stop did not fetch minutes", not stopped.get("minutes_path"), stopped)

    finalized = provider.finalize_meeting(meeting_id)
    minutes_path = Path(str(finalized.get("minutes_path") or "")).resolve()
    minutes_payload = finalized.get("ai_minutes") if isinstance(finalized.get("ai_minutes"), dict) else {}
    minutes = normalize_minutes_payload(minutes_payload)
    assert_ok("audio-file session completed after explicit AI minutes fetch", finalized.get("status") == "completed", finalized)
    assert_ok("audio-file Tingwu minutes saved", minutes_path.is_file() and minutes_path.stat().st_size > 0, minutes_path)
    assert_ok("audio-file Tingwu minutes structured", minutes.get("structured_summary") is True and bool(str(minutes.get("summary") or "").strip()), minutes)

    output_dir = Path(str(finalized.get("output_dir") or stopped.get("output_dir") or "")).resolve()
    session_path = output_dir / "session.json"
    task_payload = finalized.get("task_payload") if isinstance(finalized.get("task_payload"), dict) else {}
    provider_events = task_payload.get("events") if isinstance(task_payload.get("events"), list) else []
    evidence = {
        "status": "ok",
        "mode": "audio_file",
        "meeting_id": meeting_id,
        "workspace_dir": str(config.workspace_dir),
        "audit_log_path": str(config.audit_log_path),
        "source_audio_path": str(audio_file),
        "output_dir": str(output_dir),
        "transcript_path": str(transcript_path),
        "audio_path": str(finalized.get("audio_path") or stopped.get("audio_path") or ""),
        "session_path": str(session_path),
        "minutes_path": str(minutes_path),
        "provider_task_id": str(finalized.get("task_id") or ""),
        "ai_minutes_source_data_id": str(minutes_payload.get("source_data_id") or ""),
        "ai_minutes_task_id": str(minutes_payload.get("minutes_task_id") or ""),
        "tingwu_minutes_summary": str(minutes.get("summary") or ""),
        "tingwu_minutes_summary_source": str(minutes.get("summary_source") or ""),
        "tingwu_minutes_structured": minutes.get("structured_summary") is True,
        "audio_seconds": float(finalized.get("audio_seconds") or stopped.get("audio_seconds") or 0),
        "audio_bytes": int(finalized.get("audio_bytes") or stopped.get("audio_bytes") or 0),
        "sample_rate": int(finalized.get("sample_rate") or stopped.get("sample_rate") or config.tingwu_sample_rate),
        "audio_format": str(finalized.get("audio_format") or stopped.get("audio_format") or config.tingwu_audio_format),
        "websocket_audio_frames": int(finalized.get("websocket_audio_frames") or stopped.get("websocket_audio_frames") or 0),
        "audio_rms": int(finalized.get("audio_rms") or stopped.get("audio_rms") or 0),
        "audio_peak": int(finalized.get("audio_peak") or stopped.get("audio_peak") or 0),
        "final_count": int(finalized.get("final_count") or final_count or 0),
        "spoken_phrase": args.spoken_phrase,
        "spoken_phrase_detected": True,
        "provider_events": [str(item.get("event") or "") for item in provider_events if isinstance(item, dict)][-40:],
        "tingwu_http_operations": (finalized.get("tingwu_http_operations") if isinstance(finalized.get("tingwu_http_operations"), list) else [])[-40:],
        "checks": {
            "provider_configured": True,
            "audio_file_source": True,
            "stopped_before_ai_minutes": stopped.get("status") == "stopped",
            "audio_saved": Path(str(finalized.get("audio_path") or stopped.get("audio_path") or "")).is_file(),
            "audio_has_signal": int(finalized.get("audio_rms") or stopped.get("audio_rms") or 0) > 0,
            "websocket_audio_frames_sent": int(finalized.get("websocket_audio_frames") or stopped.get("websocket_audio_frames") or 0) > 0,
            "realtime_transcript_non_empty": len(transcript_text) >= 8 and final_count > 0,
            "spoken_phrase_detected": True,
            "ai_minutes_saved": minutes_path.is_file() and minutes_path.stat().st_size > 0,
            "tingwu_minutes_structured": minutes.get("structured_summary") is True and bool(str(minutes.get("summary") or "").strip()),
        },
    }
    write_evidence(args.evidence_json, evidence)
    print(f"meeting_id={meeting_id}")
    print(f"output_dir={output_dir}")
    print("verify_tingwu_audio_file complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
