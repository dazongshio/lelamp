#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import wave
from pathlib import Path
from typing import Any


OFFICIAL_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
OFFICIAL_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
SAFE_TINGWU_CREDENTIAL_KINDS = {
    "missing",
    "placeholder",
    "aliyun_access_key_id",
    "legacy_tingwu_appkey",
    "unexpected_app_id_shape",
    "configured",
}

REQUIRED_DIRECT_CHECKS = {
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

REQUIRED_WEB_CHECKS = {
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

REQUIRED_WEB_STEPS = {
    "realtime_capture": "completed",
    "import_transcript": "completed",
    "minutes": "completed",
    "decisions": "waiting_confirmation",
    "action_items": "completed",
    "followup": "completed",
    "reminders": "completed",
    "projection_confirmation": "completed",
}

REQUIRED_PROVIDER_EVENTS = {"meeting_started", "meeting_stopped"}
REQUIRED_WEBSOCKET_EVENTS = {"websocket_open", "websocket_started"}
REQUIRED_MEETING_NOTIFICATIONS = {"meeting_realtime_stopped", "meeting_ai_minutes_ready"}
REQUIRED_DIRECT_AUDIT_ACTIONS = {"tingwu.meeting_start", "tingwu.audio_save", "tingwu.meeting_finalize"}
REQUIRED_WEB_AUDIT_ACTIONS = {
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
}
ALLOWED_TINGWU_SUMMARY_SOURCES = {
    "fullSummary",
    "FullSummary",
    "full_summary",
    "summaries",
    "Summaries",
    "paragraphSummary",
    "ParagraphSummary",
    "questionsAnswering",
    "questionsAnsweringSummary",
    "QuestionsAnswering",
    "QuestionsAnsweringSummary",
    "conversationalSummary",
    "ConversationalSummary",
    "abstract",
    "Abstract",
    "summary",
    "Summary",
    "summaryMindMap",
    "SummaryMindMap",
}

DIRECT_ARTIFACT_KEYS = ("audit_log_path", "transcript_path", "audio_path", "minutes_path", "session_path")
WEB_ARTIFACT_KEYS = ("audit_log_path", "transcript_path", "audio_path", "session_path", "tingwu_minutes_path", "openclaw_minutes_path", "manifest_path", "task_file", "notifications_path")
REQUIRED_FOLLOWUP_OUTPUT_KEYS = (
    "openclaw_minutes",
    "transcript_export",
    "email_draft",
    "reminders",
    "projection_confirmation",
    "decisions",
    "action_items",
)
PREFLIGHT_ARTIFACT_KEYS = ("audit_log_path",)
STAGE_NAMES = ("preflight", "direct_provider", "web_api")
STAGE_RUN_REQUIRED_KEYS = {
    "preflight": ("status", "stage", "command", "cwd", "evidence_json", "workspace", "audit_log", "capture_seconds"),
    "direct_provider": ("status", "stage", "command", "cwd", "evidence_json", "workspace", "audit_log"),
    "web_api": (
        "status",
        "stage",
        "command",
        "cwd",
        "evidence_json",
        "workspace",
        "audit_log",
        "base_url",
        "console_log",
        "console_command",
        "console_pid",
        "console_returncode",
        "restart_base_url",
        "restart_console_log",
        "restart_console_command",
        "restart_console_pid",
        "restart_console_returncode",
    ),
}
GOAL_REQUIREMENT_CHECKS: dict[str, tuple[str, ...]] = {
    "import_transcript": ("web_api.step_import_transcript_completed",),
    "pi_usb_microphone_capture": (
        "preflight.real_microphone_device",
        "preflight.microphone_capture_signal",
        "direct_provider.real_microphone_device",
        "direct_provider.audio_saved",
        "direct_provider.audio_has_signal",
        "web_api.real_microphone_device",
        "web_api.audio_saved",
        "web_api.audio_has_signal",
    ),
    "tingwu_realtime_create_task": (
        "direct_provider.provider_configured",
        "direct_provider.official_tingwu_endpoint",
        "direct_provider.tingwu_http_operations_visible",
        "web_api.provider_available",
        "web_api.live_mode_not_mock",
        "web_api.official_tingwu_endpoint",
        "web_api.tingwu_http_operations_visible",
    ),
    "websocket_pcm_streaming": (
        "direct_provider.websocket_stream_started",
        "direct_provider.websocket_audio_frames_sent",
        "web_api.websocket_stream_started",
        "web_api.websocket_audio_frames_sent",
        "web_api.task_monitor_metrics_visible",
    ),
    "realtime_transcript": (
        "direct_provider.transcript_saved",
        "direct_provider.realtime_transcript_non_empty",
        "direct_provider.spoken_phrase_detected",
        "web_api.transcript_saved",
        "web_api.realtime_transcript_non_empty",
        "web_api.spoken_phrase_detected",
    ),
    "stop_before_ai_minutes": (
        "direct_provider.stopped_before_ai_minutes",
        "web_api.stopped_before_ai_minutes",
        "web_api.status_completed_after_fetch",
    ),
    "tingwu_ai_minutes": (
        "direct_provider.ai_minutes_saved",
        "direct_provider.ai_minutes_task_metadata",
        "direct_provider.tingwu_minutes_structured",
        "web_api.tingwu_minutes_saved",
        "web_api.ai_minutes_task_metadata",
        "web_api.tingwu_minutes_structured",
    ),
    "openclaw_postprocessing": (
        "web_api.openclaw_minutes_saved",
        "web_api.followup_outputs_saved",
        "web_api.manifest_indexes_followup_outputs",
        "web_api.step_minutes_completed",
        "web_api.step_decisions_waiting_confirmation",
        "web_api.step_action_items_completed",
        "web_api.step_followup_completed",
        "web_api.step_reminders_completed",
        "web_api.step_projection_confirmation_completed",
        "web_api.all_required_steps",
    ),
    "workspace_outputs": (
        "direct_provider.workspace_boundary",
        "direct_provider.no_temp_files",
        "web_api.manifest_saved",
        "web_api.followup_outputs_saved",
        "web_api.manifest_indexes_followup_outputs",
        "web_api.no_temp_files",
    ),
    "meeting_ui_task_monitor": (
        "web_api.task_monitor_listed",
        "web_api.task_monitor_metrics_visible",
        "web_api.meeting_jobs_restore_outputs",
        "web_api.web_restart_recovery_outputs",
    ),
    "assistant_panel_notifications": (
        "web_api.assistant_stop_notification",
        "web_api.assistant_fetch_notification",
    ),
    "audit_and_safety_boundaries": (
        "direct_provider.audit_lifecycle_visible",
        "direct_provider.api_key_absent",
        "direct_provider.app_id_absent_from_audit",
        "web_api.sandbox_visible",
        "web_api.audit_only_visible",
        "web_api.workspace_allowed",
        "web_api.allowed_roots_block_enforced",
        "web_api.full_control_gate_enforced",
        "web_api.full_control_audit_visible",
        "web_api.audit_lifecycle_visible",
        "web_api.api_key_absent",
        "web_api.app_id_absent_from_audit",
    ),
}


def fail(message: str, details: object = "") -> None:
    if details:
        raise AssertionError(f"{message}: {details}")
    raise AssertionError(message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AssertionError(f"Evidence file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Evidence file is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise AssertionError(f"Evidence file must contain a JSON object: {path}")
    return payload


def normalize_phrase(value: object) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def check_true_map(name: str, payload: dict[str, Any], required: set[str]) -> None:
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        fail(f"{name} evidence is missing checks")
    missing = sorted(required - set(checks))
    if missing:
        fail(f"{name} evidence is missing required checks", missing)
    failed = sorted(key for key in required if checks.get(key) is not True)
    if failed:
        fail(f"{name} required checks failed", {key: checks.get(key) for key in failed})


def check_positive_number(name: str, payload: dict[str, Any], key: str) -> None:
    try:
        value = float(payload.get(key) or 0)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"{name}.{key} is not numeric: {payload.get(key)!r}") from exc
    if value <= 0:
        fail(f"{name}.{key} must be > 0", value)


def check_nonempty_string(name: str, payload: dict[str, Any], key: str) -> None:
    if not str(payload.get(key) or "").strip():
        fail(f"{name}.{key} must be present")


def resolved_path(value: object) -> Path:
    return Path(str(value or "")).expanduser().resolve()


def check_artifacts(name: str, payload: dict[str, Any], keys: tuple[str, ...], *, workspace_key: str = "workspace_dir") -> None:
    workspace_value = str(payload.get(workspace_key) or "").strip()
    workspace = resolved_path(workspace_value) if workspace_value else None
    if workspace is not None and not workspace.is_dir():
        fail(f"{name}.{workspace_key} must exist when --check-files is used", str(workspace))
    missing: dict[str, str] = {}
    outside_workspace: dict[str, str] = {}
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if not value:
            missing[key] = ""
            continue
        path = resolved_path(value)
        if not path.is_file():
            missing[key] = str(path)
            continue
        if workspace is not None and key not in {"audit_log_path"} and not path.is_relative_to(workspace):
            outside_workspace[key] = str(path)
    if missing:
        fail(f"{name} artifact files are missing", missing)
    if outside_workspace:
        fail(f"{name} artifact files must stay under workspace", outside_workspace)


def check_followup_artifacts(name: str, payload: dict[str, Any]) -> None:
    if name != "web_api":
        return
    paths = payload.get("followup_output_paths")
    if not isinstance(paths, dict):
        fail("web_api.followup_output_paths must be present")
    workspace = resolved_path(payload.get("workspace_dir"))
    output_dir = resolved_path(payload.get("output_dir"))
    missing_keys = [key for key in REQUIRED_FOLLOWUP_OUTPUT_KEYS if not str(paths.get(key) or "").strip()]
    if missing_keys:
        fail("web_api.followup_output_paths missing required outputs", missing_keys)
    missing_files: dict[str, str] = {}
    outside_workspace: dict[str, str] = {}
    outside_meeting_dir: dict[str, str] = {}
    for key in REQUIRED_FOLLOWUP_OUTPUT_KEYS:
        path = resolved_path(paths.get(key))
        if not path.is_file():
            missing_files[key] = str(path)
        elif not path.is_relative_to(workspace):
            outside_workspace[key] = str(path)
        elif not path.is_relative_to(output_dir):
            outside_meeting_dir[key] = str(path)
    if missing_files:
        fail("web_api follow-up output files are missing", missing_files)
    if outside_workspace:
        fail("web_api follow-up output files must stay under workspace", outside_workspace)
    if outside_meeting_dir:
        fail("web_api follow-up output files must stay under workspace/meetings/{meeting_id}", outside_meeting_dir)


def check_nonempty_text_file(name: str, payload: dict[str, Any], key: str) -> None:
    path = resolved_path(payload.get(key))
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError(f"{name}.{key} must be UTF-8 text: {path}") from exc
    if not text.strip():
        fail(f"{name}.{key} must be non-empty text", str(path))


def check_transcript_evidence(name: str, payload: dict[str, Any]) -> None:
    path = resolved_path(payload.get("transcript_path"))
    try:
        transcript_text = path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as exc:
        raise AssertionError(f"{name}.transcript_path must be UTF-8 text: {path}") from exc
    if len(transcript_text) < 8:
        fail(f"{name}.transcript_path must contain transcript text, not just an empty shell", str(path))
    try:
        final_count = int(payload.get("final_count") or 0)
        transcript_items = int(payload.get("transcript_items") or 0)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"{name}.final_count/transcript_items must be numeric: {payload}") from exc
    if final_count <= 0 and transcript_items <= 0:
        fail(
            f"{name} must prove realtime transcript callbacks produced content",
            {"final_count": payload.get("final_count"), "transcript_items": payload.get("transcript_items")},
        )
    spoken_phrase = str(payload.get("spoken_phrase") or "").strip()
    if not spoken_phrase:
        fail(f"{name}.spoken_phrase must be present to prove a real microphone utterance was requested")
    if payload.get("spoken_phrase_detected") is not True:
        fail(f"{name}.spoken_phrase_detected must be true", {"spoken_phrase": spoken_phrase})
    normalized_transcript = normalize_phrase(transcript_text)
    normalized_phrase = normalize_phrase(spoken_phrase)
    if normalized_phrase and normalized_phrase not in normalized_transcript:
        fail(
            f"{name}.transcript_path must contain the requested spoken phrase",
            {
                "spoken_phrase": spoken_phrase,
                "normalized_spoken_phrase": normalized_phrase,
                "normalized_transcript_preview": normalized_transcript[:500],
                "transcript_path": str(path),
                "hint": "Repeat the exact spoken phrase clearly near the microphone, or rerun live verification with --spoken-phrase matching what Tingwu transcribed.",
            },
        )


def check_json_file(name: str, payload: dict[str, Any], key: str) -> None:
    path = resolved_path(payload.get(key))
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{name}.{key} must be valid JSON: {path}") from exc
    if not isinstance(loaded, (dict, list)):
        fail(f"{name}.{key} must contain a JSON object or array", str(path))


def load_artifact_json(name: str, payload: dict[str, Any], key: str) -> Any:
    path = resolved_path(payload.get(key))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{name}.{key} must be valid JSON: {path}") from exc


def same_path(left: object, right: object) -> bool:
    return resolved_path(left) == resolved_path(right)


def workspace_relative(path_value: object, workspace_value: object) -> str:
    path = resolved_path(path_value)
    workspace = resolved_path(workspace_value)
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return ""


def artifact_int_metric(value: object, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"{label} must be numeric: {value!r}") from exc


def artifact_float_metric(value: object, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"{label} must be numeric: {value!r}") from exc


def compare_int_metric(mismatches: dict[str, object], label: str, artifact_value: object, evidence_value: object) -> None:
    artifact = artifact_int_metric(artifact_value, f"{label}.artifact")
    evidence = artifact_int_metric(evidence_value, f"{label}.evidence")
    if artifact != evidence:
        mismatches[label] = {"artifact": artifact, "evidence": evidence}


def compare_float_metric(
    mismatches: dict[str, object],
    label: str,
    artifact_value: object,
    evidence_value: object,
    *,
    tolerance: float = 0.05,
) -> None:
    artifact = artifact_float_metric(artifact_value, f"{label}.artifact")
    evidence = artifact_float_metric(evidence_value, f"{label}.evidence")
    if abs(artifact - evidence) > tolerance:
        mismatches[label] = {"artifact": artifact, "evidence": evidence}


def check_session_json_consistency(name: str, payload: dict[str, Any]) -> None:
    if not str(payload.get("session_path") or "").strip():
        return
    session = load_artifact_json(name, payload, "session_path")
    if not isinstance(session, dict):
        fail(f"{name}.session_path must contain a JSON object")
    mismatches: dict[str, object] = {}
    if str(session.get("meeting_id") or "") != str(payload.get("meeting_id") or ""):
        mismatches["meeting_id"] = session.get("meeting_id")
    for key in ("output_dir", "transcript_path", "audio_path", "minutes_path"):
        if str(payload.get(key) or "").strip() and not same_path(session.get(key), payload.get(key)):
            mismatches[key] = session.get(key)
    if str(payload.get("sample_rate") or "").strip():
        try:
            session_sample_rate = int(session.get("sample_rate") or 0)
            payload_sample_rate = int(payload.get("sample_rate") or 0)
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"{name}.session_path sample_rate must be numeric") from exc
        if session_sample_rate != payload_sample_rate:
            mismatches["sample_rate"] = session.get("sample_rate")
    if str(payload.get("audio_format") or "").strip() and str(session.get("audio_format") or "") != str(payload.get("audio_format") or ""):
        mismatches["audio_format"] = session.get("audio_format")
    for key in ("audio_bytes", "websocket_audio_frames", "audio_rms", "audio_peak"):
        if key in payload:
            compare_int_metric(mismatches, key, session.get(key), payload.get(key))
    if "audio_seconds" in payload:
        compare_float_metric(mismatches, "audio_seconds", session.get("audio_seconds"), payload.get("audio_seconds"))
    transcript = session.get("transcript")
    if not isinstance(transcript, list):
        mismatches["transcript"] = transcript
        transcript = []
    if "final_count" in payload:
        final_count = sum(1 for item in transcript if isinstance(item, dict) and item.get("final") is True)
        compare_int_metric(mismatches, "transcript.final_count", final_count, payload.get("final_count"))
    if "transcript_items" in payload:
        transcript_items = sum(1 for item in transcript if isinstance(item, dict))
        compare_int_metric(mismatches, "transcript_items", transcript_items, payload.get("transcript_items"))
    ai_minutes = session.get("ai_minutes") if isinstance(session.get("ai_minutes"), dict) else {}
    if str(payload.get("ai_minutes_source_data_id") or "").strip() and str(ai_minutes.get("source_data_id") or "") != str(payload.get("ai_minutes_source_data_id") or ""):
        mismatches["ai_minutes.source_data_id"] = ai_minutes.get("source_data_id")
    if str(payload.get("ai_minutes_task_id") or "").strip() and str(ai_minutes.get("minutes_task_id") or "") != str(payload.get("ai_minutes_task_id") or ""):
        mismatches["ai_minutes.minutes_task_id"] = ai_minutes.get("minutes_task_id")
    if "tingwu_http_operations" in payload:
        session_operations = session.get("tingwu_http_operations")
        if session_operations != payload.get("tingwu_http_operations"):
            mismatches["tingwu_http_operations"] = "session operations differ from evidence"
    if str(session.get("status") or "") not in {"stopped", "completed"}:
        mismatches["status"] = session.get("status")
    if mismatches:
        fail(f"{name}.session_path does not match evidence", mismatches)


def check_manifest_json_consistency(name: str, payload: dict[str, Any]) -> None:
    if not str(payload.get("manifest_path") or "").strip():
        return
    manifest = load_artifact_json(name, payload, "manifest_path")
    if not isinstance(manifest, dict):
        fail(f"{name}.manifest_path must contain a JSON object")
    mismatches: dict[str, object] = {}
    if str(manifest.get("meeting_id") or "") != str(payload.get("meeting_id") or ""):
        mismatches["meeting_id"] = manifest.get("meeting_id")
    if manifest.get("provider") != "tongyi_tingwu":
        mismatches["provider"] = manifest.get("provider")
    for manifest_key, payload_key in (
        ("transcript_path", "transcript_path"),
        ("tingwu_minutes_path", "tingwu_minutes_path"),
        ("openclaw_minutes_path", "openclaw_minutes_path"),
    ):
        if str(payload.get(payload_key) or "").strip() and not same_path(manifest.get(manifest_key), payload.get(payload_key)):
            mismatches[manifest_key] = manifest.get(manifest_key)
    audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
    if str(payload.get("audio_path") or "").strip() and not same_path(audio.get("path"), payload.get("audio_path")):
        mismatches["audio.path"] = audio.get("path")
    if str(payload.get("sample_rate") or "").strip():
        try:
            manifest_sample_rate = int(audio.get("sample_rate") or 0)
            payload_sample_rate = int(payload.get("sample_rate") or 0)
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"{name}.manifest_path audio sample_rate must be numeric") from exc
        if manifest_sample_rate != payload_sample_rate:
            mismatches["audio.sample_rate"] = audio.get("sample_rate")
    if str(payload.get("audio_format") or "").strip() and str(audio.get("format") or "") != str(payload.get("audio_format") or ""):
        mismatches["audio.format"] = audio.get("format")
    if "audio_seconds" in payload:
        compare_float_metric(mismatches, "audio.seconds", audio.get("seconds"), payload.get("audio_seconds"))
    if "tingwu_http_operations" in payload:
        manifest_operations = manifest.get("tingwu_http_operations")
        if manifest_operations != payload.get("tingwu_http_operations"):
            mismatches["tingwu_http_operations"] = "manifest operations differ from evidence"
    for manifest_key, payload_key in (
        ("bytes", "audio_bytes"),
        ("rms", "audio_rms"),
        ("peak", "audio_peak"),
    ):
        if payload_key in payload:
            compare_int_metric(mismatches, f"audio.{manifest_key}", audio.get(manifest_key), payload.get(payload_key))
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list):
        mismatches["outputs"] = outputs
    else:
        workspace_paths = {
            str(item.get("workspace_path") or "")
            for item in outputs
            if isinstance(item, dict) and item.get("inside_workspace") is True
        }
        expected_workspace_paths = {
            workspace_relative(payload.get("transcript_path"), payload.get("workspace_dir")),
            workspace_relative(payload.get("audio_path"), payload.get("workspace_dir")),
            workspace_relative(resolved_path(payload.get("output_dir")) / "session.json", payload.get("workspace_dir")),
        }
        if str(payload.get("tingwu_minutes_path") or "").strip():
            expected_workspace_paths.add(workspace_relative(payload.get("tingwu_minutes_path"), payload.get("workspace_dir")))
        if str(payload.get("openclaw_minutes_path") or "").strip():
            expected_workspace_paths.add(workspace_relative(payload.get("openclaw_minutes_path"), payload.get("workspace_dir")))
        followup_paths = payload.get("followup_output_paths")
        if isinstance(followup_paths, dict):
            for key in REQUIRED_FOLLOWUP_OUTPUT_KEYS:
                expected_workspace_paths.add(workspace_relative(followup_paths.get(key), payload.get("workspace_dir")))
        missing_outputs = sorted(path for path in expected_workspace_paths if path and path not in workspace_paths)
        if missing_outputs:
            mismatches["outputs_missing"] = missing_outputs
    if mismatches:
        fail(f"{name}.manifest_path does not match evidence", mismatches)


def check_task_json_consistency(name: str, payload: dict[str, Any]) -> None:
    if name != "web_api" or not str(payload.get("task_file") or "").strip():
        return
    task = load_artifact_json(name, payload, "task_file")
    if not isinstance(task, dict):
        fail(f"{name}.task_file must contain a JSON object")
    output = task.get("output") if isinstance(task.get("output"), dict) else {}
    monitor = output.get("monitor") if isinstance(output.get("monitor"), dict) else {}
    events = output.get("events") if isinstance(output.get("events"), list) else []
    mismatches: dict[str, object] = {}
    if str(task.get("task_id") or "") != str(payload.get("web_task_id") or ""):
        mismatches["task_id"] = task.get("task_id")
    if str(output.get("meeting_id") or "") != str(payload.get("meeting_id") or ""):
        mismatches["output.meeting_id"] = output.get("meeting_id")
    try:
        monitor_frames = int(monitor.get("websocket_audio_frames") or 0)
        monitor_audio_seconds = float(monitor.get("audio_seconds") or 0)
        monitor_final_count = int(monitor.get("final_count") or 0)
        evidence_frames = int(payload.get("websocket_audio_frames") or 0)
        evidence_audio_seconds = float(payload.get("audio_seconds") or 0)
        evidence_final_count = int(payload.get("final_count") or 0)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"{name}.task_file monitor metrics must be numeric: {monitor}") from exc
    if monitor_frames <= 0:
        mismatches["monitor.websocket_audio_frames"] = monitor.get("websocket_audio_frames")
    if monitor_audio_seconds <= 0:
        mismatches["monitor.audio_seconds"] = monitor.get("audio_seconds")
    if evidence_frames > 0 and monitor_frames != evidence_frames:
        mismatches["monitor.websocket_audio_frames_mismatch"] = {"monitor": monitor_frames, "evidence": evidence_frames}
    if evidence_audio_seconds > 0 and abs(monitor_audio_seconds - evidence_audio_seconds) > 0.05:
        mismatches["monitor.audio_seconds_mismatch"] = {"monitor": monitor_audio_seconds, "evidence": evidence_audio_seconds}
    if monitor_final_count != evidence_final_count:
        mismatches["monitor.final_count_mismatch"] = {"monitor": monitor_final_count, "evidence": evidence_final_count}
    if "tingwu_http_operations" in payload:
        task_operations = output.get("tingwu_http_operations")
        if task_operations != payload.get("tingwu_http_operations"):
            mismatches["tingwu_http_operations"] = "task output operations differ from evidence"
    event_names = {str(item.get("event") or "") for item in events if isinstance(item, dict)}
    missing_lifecycle = sorted(REQUIRED_PROVIDER_EVENTS - event_names)
    if missing_lifecycle:
        mismatches["events_missing"] = missing_lifecycle
    if not event_names & REQUIRED_WEBSOCKET_EVENTS:
        mismatches["events_websocket"] = sorted(event_names)
    if mismatches:
        fail(f"{name}.task_file does not prove realtime task monitor state", mismatches)


def check_task_monitor_evidence(payload: dict[str, Any]) -> None:
    monitor = payload.get("task_monitor")
    if not isinstance(monitor, dict):
        fail("web_api.task_monitor must be present")
    mismatches: dict[str, object] = {}
    if str(monitor.get("task_id") or "") != str(payload.get("web_task_id") or ""):
        mismatches["task_id"] = monitor.get("task_id")
    try:
        monitor_frames = int(monitor.get("websocket_audio_frames") or 0)
        monitor_audio_seconds = float(monitor.get("audio_seconds") or 0)
        monitor_final_count = int(monitor.get("final_count") or 0)
        evidence_frames = int(payload.get("websocket_audio_frames") or 0)
        evidence_audio_seconds = float(payload.get("audio_seconds") or 0)
        evidence_final_count = int(payload.get("final_count") or 0)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"web_api.task_monitor metrics must be numeric: {monitor}") from exc
    if monitor_frames <= 0:
        mismatches["websocket_audio_frames"] = monitor.get("websocket_audio_frames")
    if monitor_audio_seconds <= 0:
        mismatches["audio_seconds"] = monitor.get("audio_seconds")
    if monitor_frames != evidence_frames:
        mismatches["websocket_audio_frames_mismatch"] = {"monitor": monitor_frames, "evidence": evidence_frames}
    if abs(monitor_audio_seconds - evidence_audio_seconds) > 0.05:
        mismatches["audio_seconds_mismatch"] = {"monitor": monitor_audio_seconds, "evidence": evidence_audio_seconds}
    if monitor_final_count != evidence_final_count:
        mismatches["final_count_mismatch"] = {"monitor": monitor_final_count, "evidence": evidence_final_count}
    if mismatches:
        fail("web_api.task_monitor does not match live capture evidence", mismatches)


def check_notifications_json_consistency(name: str, payload: dict[str, Any]) -> None:
    if name != "web_api" or not str(payload.get("notifications_path") or "").strip():
        return
    notification_payload = load_artifact_json(name, payload, "notifications_path")
    if isinstance(notification_payload, dict):
        items = notification_payload.get("items")
    else:
        items = notification_payload
    if not isinstance(items, list):
        fail(f"{name}.notifications_path must contain a JSON object with items or a notification list")
    meeting_id = str(payload.get("meeting_id") or "")
    matching_events = {
        str(item.get("event") or "")
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("payload"), dict)
        and str(item["payload"].get("meeting_id") or "") == meeting_id
    }
    evidence_events = {str(item) for item in payload.get("notifications") or []}
    missing_file_events = sorted(REQUIRED_MEETING_NOTIFICATIONS - matching_events)
    missing_evidence_events = sorted(REQUIRED_MEETING_NOTIFICATIONS - evidence_events)
    mismatches: dict[str, object] = {}
    if missing_file_events:
        mismatches["notifications_path_missing"] = missing_file_events
    if missing_evidence_events:
        mismatches["evidence_notifications_missing"] = missing_evidence_events
    if mismatches:
        mismatches["meeting_id"] = meeting_id
        mismatches["notifications_path_events"] = sorted(matching_events)
        mismatches["evidence_notifications"] = sorted(evidence_events)
        fail(f"{name}.notifications_path does not prove assistant meeting notifications persisted", mismatches)


def check_ai_minutes_metadata(name: str, payload: dict[str, Any]) -> None:
    source_data_id = str(payload.get("ai_minutes_source_data_id") or "").strip()
    minutes_task_id = str(payload.get("ai_minutes_task_id") or "").strip()
    provider_task_id = str(payload.get("provider_task_id") or "").strip()
    if not source_data_id or not minutes_task_id:
        fail(f"{name} must include Tingwu AI minutes task metadata", {"source_data_id": source_data_id, "minutes_task_id": minutes_task_id})
    if provider_task_id and source_data_id != provider_task_id:
        fail(f"{name}.ai_minutes_source_data_id must match provider_task_id", {"source_data_id": source_data_id, "provider_task_id": provider_task_id})
    if minutes_task_id == source_data_id:
        fail(f"{name}.ai_minutes_task_id must be the analysis task dataId, not the realtime source dataId", {"source_data_id": source_data_id, "minutes_task_id": minutes_task_id})


def check_tingwu_http_operations(name: str, payload: dict[str, Any]) -> None:
    operations = payload.get("tingwu_http_operations")
    if not isinstance(operations, list) or not operations:
        fail(f"{name}.tingwu_http_operations must be present")
    required_actions = {"CreateTask", "CreateRealtimeMinutesTask", "GetTask"}
    by_action: dict[str, list[dict[str, Any]]] = {}
    for item in operations:
        if not isinstance(item, dict):
            continue
        by_action.setdefault(str(item.get("action") or ""), []).append(item)
    missing = sorted(required_actions - set(by_action))
    mismatches: dict[str, object] = {}
    if missing:
        mismatches["missing_actions"] = missing
    provider_task_id = str(payload.get("provider_task_id") or "")
    minutes_task_id = str(payload.get("ai_minutes_task_id") or "")
    realtime_creates = by_action.get("CreateTask", [])
    minutes_creates = by_action.get("CreateRealtimeMinutesTask", [])
    get_tasks = by_action.get("GetTask", [])
    if not any(str(item.get("request_type") or "") == "realtime" and str(item.get("response_data_id") or "") == provider_task_id for item in realtime_creates):
        mismatches["CreateTask"] = "must create realtime provider task and return provider_task_id"
    if not any(str(item.get("request_data_id") or "") == provider_task_id and str(item.get("response_data_id") or "") == minutes_task_id for item in minutes_creates):
        mismatches["CreateRealtimeMinutesTask"] = "must create AI minutes task from provider_task_id and return ai_minutes_task_id"
    if not any(str(item.get("request_data_id") or "") == minutes_task_id for item in get_tasks):
        mismatches["GetTask"] = "must fetch AI minutes task status by ai_minutes_task_id"
    bad_endpoints = [
        item
        for item in operations
        if isinstance(item, dict) and not endpoint_matches(item.get("endpoint"), OFFICIAL_TINGWU_HTTP_URL)
    ]
    if bad_endpoints:
        mismatches["endpoint"] = bad_endpoints[:3]
    if mismatches:
        fail(f"{name}.tingwu_http_operations do not prove the Tingwu CreateTask/GetTask HTTP chain", mismatches)


def check_stop_fetch_sequence(name: str, payload: dict[str, Any]) -> None:
    if str(payload.get("stop_status_before_fetch") or "") != "stopped":
        fail(f"{name}.stop_status_before_fetch must prove realtime capture stopped before AI minutes fetch", payload.get("stop_status_before_fetch"))
    if str(payload.get("minutes_path_after_stop") or "").strip():
        fail(f"{name}.minutes_path_after_stop must be empty before explicit AI minutes fetch", payload.get("minutes_path_after_stop"))
    if str(payload.get("status_after_fetch") or "") != "completed":
        fail(f"{name}.status_after_fetch must prove explicit AI minutes fetch completed", payload.get("status_after_fetch"))
    if name == "web_api" and str(payload.get("provider_status_before_fetch") or "") != "stopped":
        fail("web_api.provider_status_before_fetch must prove provider was stopped before AI minutes fetch", payload.get("provider_status_before_fetch"))
    if name == "web_api":
        probe = payload.get("active_fetch_minutes_probe")
        if not isinstance(probe, dict):
            fail("web_api.active_fetch_minutes_probe must be present")
        mismatches = {}
        if probe.get("status_code") != 409:
            mismatches["status_code"] = probe.get("status_code")
        if probe.get("ok") is not False:
            mismatches["ok"] = probe.get("ok")
        if str(probe.get("error_code") or "") != "meeting_not_stopped":
            mismatches["error_code"] = probe.get("error_code")
        if mismatches:
            fail("web_api.active_fetch_minutes_probe must prove fetch-minutes is blocked before stop", mismatches)


def check_full_control_gate(payload: dict[str, Any]) -> None:
    gate = payload.get("full_control_gate")
    if not isinstance(gate, dict):
        fail("web_api.full_control_gate must be present")
    expected = {
        "request_status": "waiting_confirmation",
        "request_id_present": True,
        "confirm_status": "backend_missing",
        "confirm_full_control_enabled": False,
        "security_permission_mode_after_confirm": "sandbox",
        "security_full_control_enabled_after_confirm": False,
        "cancel_status": "blocked",
        "audit_visible": True,
    }
    mismatches = {
        key: {"expected": expected_value, "actual": gate.get(key)}
        for key, expected_value in expected.items()
        if gate.get(key) != expected_value
    }
    if mismatches:
        fail("web_api.full_control_gate does not prove full_control remained gated and audited", mismatches)


def check_allowed_roots_probe(payload: dict[str, Any]) -> None:
    probe = payload.get("allowed_roots_probe")
    if not isinstance(probe, dict):
        fail("web_api.allowed_roots_probe must be present")
    expected = {
        "status_code": 403,
        "ok": False,
        "error_code": "blocked",
        "audit_visible": True,
    }
    mismatches = {
        key: {"expected": expected_value, "actual": probe.get(key)}
        for key, expected_value in expected.items()
        if probe.get(key) != expected_value
    }
    path_value = str(probe.get("path") or "")
    if path_value and str(payload.get("workspace_dir") or ""):
        try:
            if resolved_path(path_value).is_relative_to(resolved_path(payload.get("workspace_dir"))):
                mismatches["path"] = "probe path is inside workspace"
        except OSError:
            pass
    if mismatches:
        fail("web_api.allowed_roots_probe does not prove meeting APIs reject paths outside allowed roots", mismatches)


def endpoint_origin(url: object) -> str:
    parsed = urllib.parse.urlsplit(str(url or ""))
    if not parsed.scheme or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme.lower()}://{host}"


def endpoint_matches(url: object, expected: str) -> bool:
    parsed = urllib.parse.urlsplit(str(url or ""))
    expected_parsed = urllib.parse.urlsplit(expected)
    return (
        parsed.scheme.lower() == expected_parsed.scheme
        and (parsed.hostname or "").lower() == (expected_parsed.hostname or "").lower()
        and (parsed.path.rstrip("/") or "/") == (expected_parsed.path.rstrip("/") or "/")
        and (parsed.port or 443) == (expected_parsed.port or 443)
    )


def check_tingwu_endpoint(name: str, payload: dict[str, Any]) -> None:
    probe = payload.get("endpoint_probe")
    if not isinstance(probe, dict):
        fail(f"{name}.endpoint_probe must be present to prove live traffic used official Tingwu endpoints")
    http_url = str(probe.get("http_url") or payload.get("http_url") or "").strip()
    ws_url = str(probe.get("ws_url") or payload.get("ws_url") or "").strip()
    mismatches: dict[str, object] = {}
    if not endpoint_matches(http_url, OFFICIAL_TINGWU_HTTP_URL):
        mismatches["http_url"] = {
            "actual": http_url,
            "actual_origin": endpoint_origin(http_url),
            "expected": OFFICIAL_TINGWU_HTTP_URL,
        }
    if not endpoint_matches(ws_url, OFFICIAL_TINGWU_WS_URL):
        mismatches["ws_url"] = {
            "actual": ws_url,
            "actual_origin": endpoint_origin(ws_url),
            "expected": OFFICIAL_TINGWU_WS_URL,
        }
    if probe.get("official_dashscope") is not True:
        mismatches["official_dashscope"] = probe.get("official_dashscope")
    if mismatches:
        fail(f"{name}.endpoint_probe must prove official DashScope/Tongyi Tingwu endpoints, not local/mock/private endpoints", mismatches)


def check_real_microphone(name: str, payload: dict[str, Any]) -> None:
    selected = str(payload.get("selected_mic_device") or "").strip()
    configured = str(payload.get("configured_mic_device") or "").strip()
    probe = payload.get("mic_probe") if isinstance(payload.get("mic_probe"), dict) else {}
    selected_probe = str(probe.get("selected_device") or "").strip()
    configured_probe = str(probe.get("configured_device") or "").strip()
    message = str(probe.get("message") or "")
    capture_probe = probe.get("capture_probe") if isinstance(probe.get("capture_probe"), dict) else {}
    fake_names = {"fake-mic", "mock", "mock-mic"}
    placeholder_names = {"auto", "default", "pulse", "sysdefault"}
    mismatches: dict[str, object] = {}
    if not configured:
        mismatches["configured_mic_device"] = configured
    if configured_probe and configured and configured_probe != configured:
        mismatches["configured_device_mismatch"] = {"configured_mic_device": configured, "probe_configured_device": configured_probe}
    if not selected:
        mismatches["selected_mic_device"] = selected
    if str(probe.get("status") or "") != "available":
        mismatches["probe_status"] = probe.get("status")
    if selected.lower() in placeholder_names or selected_probe.lower() in placeholder_names:
        mismatches["unresolved_selected_device"] = {"selected_mic_device": selected, "probe_selected_device": selected_probe}
    if selected.lower() in fake_names or selected_probe.lower() in fake_names or configured_probe.lower() in fake_names:
        mismatches["fake_device"] = {"selected_mic_device": selected, "probe": probe}
    if str(probe.get("status") or "") == "mock" or "fake microphone" in message.lower() or "tingwu_mock=1" in message.lower():
        mismatches["mock_probe"] = probe
    if selected_probe and selected_probe != selected:
        mismatches["selected_device_mismatch"] = {"selected_mic_device": selected, "probe_selected_device": selected_probe}
    if not capture_probe:
        mismatches["capture_probe"] = "missing"
    else:
        capture_selected = str(capture_probe.get("selected_device") or "").strip()
        capture_message = str(capture_probe.get("message") or "").lower()
        if capture_probe.get("status") != "available":
            mismatches["capture_probe.status"] = capture_probe.get("status")
        if capture_selected != selected:
            mismatches["capture_probe.selected_device"] = {"selected_mic_device": selected, "capture_selected_device": capture_selected}
        if capture_selected.lower() in placeholder_names:
            mismatches["capture_probe.unresolved_selected_device"] = capture_probe
        if selected.lower() in fake_names or capture_selected.lower() in fake_names:
            mismatches["capture_probe.fake_device"] = {"selected_mic_device": selected, "capture_probe": capture_probe}
        if "fake microphone" in capture_message or "tingwu_mock=1" in capture_message:
            mismatches["capture_probe.mock_message"] = capture_probe
        try:
            capture_rate = int(capture_probe.get("sample_rate") or 0)
            evidence_rate = int(payload.get("sample_rate") or 0)
            capture_bytes = int(capture_probe.get("audio_bytes") or 0)
            capture_rms = int(capture_probe.get("audio_rms") or 0)
            capture_peak = int(capture_probe.get("audio_peak") or 0)
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"{name}.mic_probe.capture_probe metrics must be numeric: {capture_probe}") from exc
        if evidence_rate <= 0 or capture_rate != evidence_rate:
            mismatches["capture_probe.sample_rate"] = {"capture": capture_rate, "evidence": evidence_rate}
        if capture_bytes <= 0 or capture_rms <= 0 or capture_peak <= 0:
            mismatches["capture_probe.signal"] = {
                "audio_bytes": capture_probe.get("audio_bytes"),
                "audio_rms": capture_probe.get("audio_rms"),
                "audio_peak": capture_probe.get("audio_peak"),
            }
    if mismatches:
        fail(f"{name} must prove a real ALSA microphone was used, not fake-mic/mock capture", mismatches)


def check_tingwu_minutes_structure(name: str, payload: dict[str, Any]) -> None:
    summary = str(payload.get("tingwu_minutes_summary") or "").strip()
    if not summary:
        fail(f"{name}.tingwu_minutes_summary must contain structured Tingwu summary text")
    if payload.get("tingwu_minutes_structured") is not True:
        fail(f"{name}.tingwu_minutes_structured must be true")
    summary_source = str(payload.get("tingwu_minutes_summary_source") or "").strip()
    if summary_source not in ALLOWED_TINGWU_SUMMARY_SOURCES:
        fail(f"{name}.tingwu_minutes_summary_source must identify a structured Tingwu summary field", summary_source)
    path_key = "tingwu_minutes_path" if str(payload.get("tingwu_minutes_path") or "").strip() else "minutes_path"
    if not str(payload.get(path_key) or "").strip():
        fail(f"{name} must include a Tingwu minutes file path for structured summary verification")
    try:
        minutes_text = resolved_path(payload.get(path_key)).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError(f"{name}.{path_key} must be UTF-8 text: {payload.get(path_key)}") from exc
    if summary not in minutes_text:
        fail(f"{name}.tingwu_minutes_summary must match saved Tingwu minutes file content")


def check_restore_payload(name: str, payload: dict[str, Any], key: str, label: str) -> None:
    restore = payload.get(key)
    if not isinstance(restore, dict):
        fail(f"web_api.{key} must be present to prove {label}")
    step_names = restore.get("step_names") if isinstance(restore.get("step_names"), list) else []
    missing_steps = sorted(set(REQUIRED_WEB_STEPS) - {str(item) for item in step_names})
    mismatches: dict[str, object] = {}
    if missing_steps:
        mismatches["missing_steps"] = missing_steps
    if str(restore.get("tingwu_minutes_path") or "") != str(payload.get("tingwu_minutes_path") or ""):
        mismatches["tingwu_minutes_path"] = restore.get("tingwu_minutes_path")
    if str(restore.get("openclaw_minutes_path") or "") != str(payload.get("openclaw_minutes_path") or ""):
        mismatches["openclaw_minutes_path"] = restore.get("openclaw_minutes_path")
    if str(restore.get("tingwu_minutes_summary") or "").strip() != str(payload.get("tingwu_minutes_summary") or "").strip():
        mismatches["tingwu_minutes_summary"] = restore.get("tingwu_minutes_summary")
    if str(restore.get("tingwu_minutes_summary_source") or "").strip() != str(payload.get("tingwu_minutes_summary_source") or "").strip():
        mismatches["tingwu_minutes_summary_source"] = restore.get("tingwu_minutes_summary_source")
    if restore.get("tingwu_minutes_structured") is not True:
        mismatches["tingwu_minutes_structured"] = restore.get("tingwu_minutes_structured")
    try:
        frames = int(restore.get("websocket_audio_frames") or 0)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"web_api.{key}.websocket_audio_frames must be numeric: {restore}") from exc
    if frames <= 0:
        mismatches["websocket_audio_frames"] = restore.get("websocket_audio_frames")
    followup_keys = restore.get("followup_output_keys") if isinstance(restore.get("followup_output_keys"), list) else []
    missing_followup_keys = sorted(set(REQUIRED_FOLLOWUP_OUTPUT_KEYS) - {str(item) for item in followup_keys})
    if missing_followup_keys:
        mismatches["missing_followup_output_keys"] = missing_followup_keys
    if restore.get("events_compacted") is not True:
        mismatches["events_compacted"] = restore.get("events_compacted")
    if key == "web_restart_recovery":
        try:
            task_frames = int(restore.get("task_monitor_frames") or 0)
            task_audio_seconds = float(restore.get("task_monitor_audio_seconds") or 0)
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"web_api.{key} task monitor metrics must be numeric: {restore}") from exc
        if task_frames <= 0:
            mismatches["task_monitor_frames"] = restore.get("task_monitor_frames")
        if task_audio_seconds <= 0:
            mismatches["task_monitor_audio_seconds"] = restore.get("task_monitor_audio_seconds")
        notifications = restore.get("assistant_notifications") if isinstance(restore.get("assistant_notifications"), list) else []
        if "meeting_ai_minutes_ready" not in {str(item) for item in notifications}:
            mismatches["assistant_notifications"] = notifications
    if mismatches:
        fail(f"web_api.{key} does not prove {label}", mismatches)


def check_meeting_jobs_restore(name: str, payload: dict[str, Any]) -> None:
    if name != "web_api":
        return
    check_restore_payload(name, payload, "meeting_jobs_restore", "persisted Meeting UI recovery")
    check_restore_payload(name, payload, "web_restart_recovery", "Web Console restart recovery")


def read_audit_actions(name: str, payload: dict[str, Any]) -> set[str]:
    path = resolved_path(payload.get("audit_log_path"))
    actions: set[str] = set()
    invalid_lines: list[int] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines.append(index)
            continue
        if isinstance(item, dict):
            action = str(item.get("action") or "").strip()
            if action:
                actions.add(action)
    if invalid_lines:
        fail(f"{name}.audit_log_path must contain valid JSONL", {"path": str(path), "invalid_lines": invalid_lines[:10]})
    return actions


def check_audit_actions(name: str, payload: dict[str, Any], required: set[str]) -> None:
    actions = read_audit_actions(name, payload)
    missing = sorted(required - actions)
    if missing:
        fail(f"{name}.audit_log_path missing required audit actions", {"missing": missing, "actions": sorted(actions)})


def append_secret_scan_roots_from_stage_runs(roots: list[Path], payload: dict[str, Any]) -> None:
    stage_runs = payload.get("stage_runs")
    if not isinstance(stage_runs, dict):
        return
    for run in stage_runs.values():
        if not isinstance(run, dict):
            continue
        for key in (
            "evidence_json",
            "workspace",
            "audit_log",
            "console_log",
            "restart_console_log",
        ):
            value = str(run.get(key) or "").strip()
            if value:
                roots.append(resolved_path(value))


def secret_scan_roots(payload: dict[str, Any]) -> list[Path]:
    roots: list[Path] = []
    summary_path = str(payload.get("_summary_path") or "").strip()
    if summary_path:
        roots.append(resolved_path(summary_path))
    append_secret_scan_roots_from_stage_runs(roots, payload)
    for key in ("workspace_dir", "audit_log_path", "session_path", "manifest_path", "task_file", "notifications_path"):
        value = str(payload.get(key) or "").strip()
        if value:
            roots.append(resolved_path(value))
    followup_paths = payload.get("followup_output_paths")
    if isinstance(followup_paths, dict):
        for value in followup_paths.values():
            if str(value or "").strip():
                roots.append(resolved_path(value))
    return roots


def secret_scan_values(payload: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("TINGWU_API_KEY", "DASHSCOPE_API_KEY", "TINGWU_APP_ID", "TINGWU_MEETING_APP_ID"):
        value = os.getenv(key)
        if value:
            values.append(value)
    canaries = payload.get("redaction_canaries")
    if isinstance(canaries, list):
        values.extend(str(item) for item in canaries)
    return sorted({item for item in values if len(item) >= 8})


def check_redaction_canaries(name: str, payload: dict[str, Any]) -> None:
    if name not in {"direct_provider", "web_api"}:
        return
    canaries = payload.get("redaction_canaries")
    if not isinstance(canaries, list):
        fail(f"{name}.redaction_canaries must be present so live verifier canary redaction is auditable")
    values = [str(item) for item in canaries if str(item or "").strip()]
    if len(values) < 3 or any(len(item) < 8 for item in values):
        fail(f"{name}.redaction_canaries must include the title and participant canaries used by the live verifier", canaries)


def check_secret_absence(name: str, payload: dict[str, Any]) -> None:
    secrets = secret_scan_values(payload)
    if not secrets:
        return
    roots = secret_scan_roots(payload)
    leaks: dict[str, list[str]] = {}
    for root in roots:
        candidates = [root] if root.is_file() else [item for item in root.rglob("*") if item.is_file()] if root.is_dir() else []
        for candidate in candidates:
            try:
                data = secret_scan_bytes(candidate)
            except OSError as exc:
                leaks.setdefault("[read-error]", []).append(f"{candidate}: {exc}")
                continue
            for secret in secrets:
                if secret.encode("utf-8") in data:
                    leaks.setdefault(secret, []).append(str(candidate))
    if leaks:
        redacted = {f"secret_{index}": sorted(paths) for index, paths in enumerate(leaks.values(), start=1)}
        fail(f"{name} artifacts contain live credentials or redaction canaries", redacted)


def secret_scan_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() != ".json":
        return data
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data
    return json.dumps(remove_redaction_canary_fields(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")


def remove_redaction_canary_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): remove_redaction_canary_fields(item)
            for key, item in value.items()
            if str(key) != "redaction_canaries"
        }
    if isinstance(value, list):
        return [remove_redaction_canary_fields(item) for item in value]
    return value


def check_summary_secret_absence(summary: dict[str, Any]) -> None:
    combined: dict[str, Any] = {
        "_summary_path": summary.get("_summary_path"),
        "stage_runs": summary.get("stage_runs"),
        "redaction_canaries": [],
    }
    canaries: list[str] = []
    for stage_name in STAGE_NAMES:
        stage = summary.get(stage_name)
        if isinstance(stage, dict) and isinstance(stage.get("redaction_canaries"), list):
            canaries.extend(str(item) for item in stage["redaction_canaries"])
    combined["redaction_canaries"] = canaries
    check_secret_absence("summary", combined)


def check_wav_file(name: str, payload: dict[str, Any], key: str) -> None:
    path = resolved_path(payload.get(key))
    try:
        with wave.open(str(path), "rb") as audio:
            details = {
                "channels": audio.getnchannels(),
                "sampwidth": audio.getsampwidth(),
                "framerate": audio.getframerate(),
                "frames": audio.getnframes(),
            }
    except (wave.Error, EOFError, OSError) as exc:
        raise AssertionError(f"{name}.{key} must be a readable WAV file: {path}") from exc
    if details["channels"] <= 0 or details["sampwidth"] <= 0 or details["framerate"] <= 0 or details["frames"] <= 0:
        fail(f"{name}.{key} must contain audio frames", details)
    try:
        expected_sample_rate = int(payload.get("sample_rate") or 16000)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"{name}.sample_rate must be numeric: {payload.get('sample_rate')!r}") from exc
    if expected_sample_rate <= 0:
        fail(f"{name}.sample_rate must be > 0", payload.get("sample_rate"))
    if details["channels"] != 1 or details["sampwidth"] != 2 or details["framerate"] != expected_sample_rate:
        fail(f"{name}.{key} must be mono 16-bit PCM WAV at the configured sample rate", {"expected_sample_rate": expected_sample_rate, **details})
    expected_audio_bytes = int(payload.get("audio_bytes") or 0)
    wav_audio_bytes = details["frames"] * details["channels"] * details["sampwidth"]
    if expected_audio_bytes != wav_audio_bytes:
        fail(
            f"{name}.{key} audio byte count must match evidence",
            {"evidence_audio_bytes": expected_audio_bytes, "wav_audio_bytes": wav_audio_bytes, **details},
        )
    expected_audio_seconds = float(payload.get("audio_seconds") or 0)
    wav_audio_seconds = details["frames"] / details["framerate"]
    if abs(expected_audio_seconds - wav_audio_seconds) > 0.05:
        fail(
            f"{name}.{key} audio duration must match evidence",
            {
                "evidence_audio_seconds": expected_audio_seconds,
                "wav_audio_seconds": round(wav_audio_seconds, 4),
                **details,
            },
        )


def check_artifact_content(name: str, payload: dict[str, Any]) -> None:
    check_redaction_canaries(name, payload)
    check_wav_file(name, payload, "audio_path")
    check_transcript_evidence(name, payload)
    required_audit_actions = REQUIRED_WEB_AUDIT_ACTIONS if name == "web_api" else REQUIRED_DIRECT_AUDIT_ACTIONS
    check_audit_actions(name, payload, required_audit_actions)
    for key in ("transcript_path", "minutes_path", "tingwu_minutes_path", "openclaw_minutes_path"):
        if str(payload.get(key) or "").strip():
            check_nonempty_text_file(name, payload, key)
    for key in ("session_path", "manifest_path", "task_file", "notifications_path"):
        if str(payload.get(key) or "").strip():
            check_json_file(name, payload, key)
    check_session_json_consistency(name, payload)
    check_manifest_json_consistency(name, payload)
    check_task_json_consistency(name, payload)
    check_notifications_json_consistency(name, payload)
    check_secret_absence(name, payload)


def check_audio_protocol(name: str, payload: dict[str, Any]) -> None:
    audio_format = str(payload.get("audio_format") or "").strip().lower()
    if audio_format != "pcm":
        fail(f"{name}.audio_format must prove PCM realtime audio streaming", payload.get("audio_format"))


def check_preflight_probe(payload: dict[str, Any]) -> None:
    capture_probe = payload.get("capture_probe")
    if not isinstance(capture_probe, dict):
        fail("preflight.capture_probe must be present")
    if capture_probe.get("status") != "available":
        fail("preflight.capture_probe must prove the selected microphone can be opened", capture_probe)
    selected = str(payload.get("selected_mic_device") or "").strip()
    configured = str(payload.get("configured_mic_device") or payload.get("mic_device") or "").strip()
    selected_probe = str(capture_probe.get("selected_device") or "").strip()
    placeholder_names = {"auto", "default", "pulse", "sysdefault"}
    if not configured:
        fail("preflight.configured_mic_device must identify the configured microphone value", payload)
    if not selected or selected_probe != selected:
        fail("preflight.capture_probe selected device must match preflight.selected_mic_device", {"selected_mic_device": selected, "capture_probe": capture_probe})
    if selected.lower() in placeholder_names or selected_probe.lower() in placeholder_names:
        fail("preflight selected microphone must be a resolved capture device, not an auto/default placeholder", {"configured_mic_device": configured, "selected_mic_device": selected, "capture_probe": capture_probe})
    fake_names = {"fake-mic", "mock", "mock-mic"}
    if selected.lower() in fake_names or selected_probe.lower() in fake_names:
        fail("preflight must use a real ALSA microphone, not fake-mic/mock capture", {"selected_mic_device": selected, "capture_probe": capture_probe})
    capture_message = str(capture_probe.get("message") or "").lower()
    if str(capture_probe.get("status") or "") == "mock" or "fake microphone" in capture_message or "tingwu_mock=1" in capture_message:
        fail("preflight.capture_probe must not be mock/fake microphone evidence", capture_probe)
    try:
        sample_rate = int(payload.get("sample_rate") or 0)
        capture_rate = int(capture_probe.get("sample_rate") or 0)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"preflight sample rates must be numeric: {payload}") from exc
    if sample_rate <= 0 or capture_rate != sample_rate:
        fail("preflight.capture_probe sample rate must match preflight.sample_rate", {"sample_rate": sample_rate, "capture_probe": capture_probe})
    try:
        capture_seconds = int(payload.get("capture_seconds") or 0)
        probe_seconds = int(capture_probe.get("duration_seconds") or 0)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"preflight capture duration fields must be numeric: {payload}") from exc
    if capture_seconds <= 0 or probe_seconds != capture_seconds:
        fail(
            "preflight.capture_probe duration must match preflight.capture_seconds",
            {"capture_seconds": capture_seconds, "capture_probe": capture_probe},
        )
    try:
        audio_bytes = int(capture_probe.get("audio_bytes") or 0)
        audio_rms = int(capture_probe.get("audio_rms") or 0)
        audio_peak = int(capture_probe.get("audio_peak") or 0)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"preflight capture signal metrics must be numeric: {capture_probe}") from exc
    if audio_bytes <= 0:
        fail("preflight.capture_probe must include captured PCM audio bytes", capture_probe)
    if audio_rms <= 0 or audio_peak <= 0:
        fail(
            "preflight.capture_probe must prove the selected microphone produced non-silent audio",
            {"audio_bytes": audio_bytes, "audio_rms": audio_rms, "audio_peak": audio_peak, "capture_probe": capture_probe},
        )


def check_provider_events(name: str, payload: dict[str, Any]) -> None:
    events = payload.get("provider_events")
    if not isinstance(events, list):
        fail(f"{name}.provider_events must be a list")
    event_names = {str(item) for item in events}
    missing_lifecycle = sorted(REQUIRED_PROVIDER_EVENTS - event_names)
    if missing_lifecycle:
        fail(f"{name}.provider_events missing lifecycle events", {"missing": missing_lifecycle, "events": sorted(event_names)})
    if not event_names & REQUIRED_WEBSOCKET_EVENTS:
        fail(f"{name}.provider_events missing WebSocket stream events", sorted(event_names))


def check_command_list(name: str, payload: dict[str, Any], key: str) -> None:
    value = payload.get(key)
    if not isinstance(value, list) or not value or any(not str(item).strip() for item in value):
        fail(f"{name}.{key} must be a non-empty command list", value)


def command_contains_script(command: object, script_name: str) -> bool:
    if not isinstance(command, list):
        return False
    normalized = [Path(str(item)).name for item in command]
    return script_name in normalized or any(str(item).endswith(f"/{script_name}") for item in command)


def command_contains_tokens(command: object, tokens: tuple[str, ...]) -> bool:
    if not isinstance(command, list):
        return False
    values = {str(item) for item in command}
    basenames = {Path(str(item)).name for item in command}
    return all(token in values or token in basenames for token in tokens)


def command_option_value(command: object, option: str) -> str:
    if not isinstance(command, list):
        return ""
    values = [str(item) for item in command]
    for index, value in enumerate(values):
        if value == option and index + 1 < len(values):
            return values[index + 1]
        if value.startswith(f"{option}="):
            return value.split("=", 1)[1]
    return ""


def web_console_command_url(command: object) -> str:
    if not isinstance(command, list):
        return ""
    host = command_option_value(command, "--host") or "127.0.0.1"
    port_value = command_option_value(command, "--port") or "8790"
    try:
        port = int(port_value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"web console command --port must be numeric: {command}") from exc
    return f"http://{host}:{port}"


def check_web_console_command_url(run: dict[str, Any], command_key: str, url_key: str) -> None:
    expected = str(run.get(url_key) or "").strip()
    if not expected:
        return
    actual = web_console_command_url(run.get(command_key))
    if actual != expected:
        fail(
            f"summary.stage_runs.web_api.{command_key} host/port must match {url_key}",
            {"command_url": actual, url_key: expected, "command": run.get(command_key)},
        )


def check_command_option_path(stage_name: str, run: dict[str, Any], option: str, run_key: str) -> None:
    expected = str(run.get(run_key) or "").strip()
    actual = command_option_value(run.get("command"), option)
    if not expected:
        return
    if not actual:
        fail(f"summary.stage_runs.{stage_name}.command must include {option}", run.get("command"))
    if resolved_path(actual) != resolved_path(expected):
        fail(
            f"summary.stage_runs.{stage_name}.command {option} must match {run_key}",
            {"command_value": actual, run_key: expected},
        )


def check_stage_command_identity(stage_name: str, run: dict[str, Any], summary: dict[str, Any]) -> None:
    expected_scripts = {
        "preflight": "preflight_tingwu_live.py",
        "direct_provider": "verify_tingwu_live.py",
        "web_api": "verify_tingwu_web_live.py",
    }
    expected = expected_scripts.get(stage_name)
    if expected and not command_contains_script(run.get("command"), expected):
        fail(f"summary.stage_runs.{stage_name}.command must invoke {expected}", run.get("command"))
    path_options = [("--evidence-json", "evidence_json")]
    if stage_name in {"preflight", "direct_provider"}:
        path_options.extend([("--workspace", "workspace"), ("--audit-log", "audit_log")])
    for option, run_key in path_options:
        check_command_option_path(stage_name, run, option, run_key)
    if stage_name == "preflight":
        capture_value = command_option_value(run.get("command"), "--capture-seconds")
        if not capture_value:
            fail("summary.stage_runs.preflight.command must include --capture-seconds", run.get("command"))
        try:
            command_capture_seconds = int(capture_value)
            run_capture_seconds = int(run.get("capture_seconds"))
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"summary.stage_runs.preflight command capture seconds must be numeric: {run}") from exc
        if command_capture_seconds != run_capture_seconds:
            fail(
                "summary.stage_runs.preflight.command --capture-seconds must match capture_seconds",
                {"command_value": command_capture_seconds, "capture_seconds": run_capture_seconds},
            )
    if stage_name in {"direct_provider", "web_api"}:
        seconds_value = command_option_value(run.get("command"), "--seconds")
        if not seconds_value:
            fail(f"summary.stage_runs.{stage_name}.command must include --seconds", run.get("command"))
        try:
            command_seconds = int(seconds_value)
            summary_seconds = int(summary.get("seconds"))
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"summary.stage_runs.{stage_name} command seconds must be numeric: {run}") from exc
        if command_seconds != summary_seconds:
            fail(
                f"summary.stage_runs.{stage_name}.command --seconds must match summary.seconds",
                {"command_value": command_seconds, "summary_seconds": summary_seconds},
            )
        spoken_phrase_value = command_option_value(run.get("command"), "--spoken-phrase")
        if not spoken_phrase_value:
            fail(f"summary.stage_runs.{stage_name}.command must include --spoken-phrase", run.get("command"))
        evidence = summary.get(stage_name) if isinstance(summary.get(stage_name), dict) else {}
        evidence_phrase = str(evidence.get("spoken_phrase") or "")
        if spoken_phrase_value != evidence_phrase:
            fail(
                f"summary.stage_runs.{stage_name}.command --spoken-phrase must match {stage_name}.spoken_phrase",
                {"command_value": spoken_phrase_value, "spoken_phrase": evidence_phrase},
            )
    if stage_name == "web_api":
        base_url_value = command_option_value(run.get("command"), "--base-url")
        if not base_url_value:
            fail("summary.stage_runs.web_api.command must include --base-url", run.get("command"))
        if base_url_value != str(run.get("base_url") or ""):
            fail(
                "summary.stage_runs.web_api.command --base-url must match base_url",
                {"command_value": base_url_value, "base_url": run.get("base_url")},
            )
    if stage_name == "web_api":
        for key in ("console_command", "restart_console_command"):
            if not command_contains_tokens(run.get(key), ("openclaw_cli.py", "web-console")):
                fail(f"summary.stage_runs.web_api.{key} must start the Web Console", run.get(key))
        check_web_console_command_url(run, "console_command", "base_url")
        check_web_console_command_url(run, "restart_console_command", "restart_base_url")


def check_stage_runs(summary: dict[str, Any], *, check_files: bool = False) -> None:
    stage_runs = summary.get("stage_runs")
    if not isinstance(stage_runs, dict):
        fail("summary.stage_runs is missing")
    for stage_name in STAGE_NAMES:
        run = stage_runs.get(stage_name)
        if not isinstance(run, dict):
            fail(f"summary.stage_runs.{stage_name} must be present")
        missing = [key for key in STAGE_RUN_REQUIRED_KEYS[stage_name] if key not in run]
        if missing:
            fail(f"summary.stage_runs.{stage_name} is missing required fields", missing)
        if run.get("status") != "ok":
            fail(f"summary.stage_runs.{stage_name}.status is not ok", run)
        check_command_list(f"stage_runs.{stage_name}", run, "command")
        check_stage_command_identity(stage_name, run, summary)
        for key in ("stage", "cwd", "evidence_json", "workspace", "audit_log"):
            check_nonempty_string(f"stage_runs.{stage_name}", run, key)
        if stage_name == "preflight":
            try:
                capture_seconds = int(run.get("capture_seconds"))
            except (TypeError, ValueError) as exc:
                raise AssertionError(f"summary.stage_runs.preflight.capture_seconds must be numeric: {run}") from exc
            if capture_seconds <= 0:
                fail("summary.stage_runs.preflight.capture_seconds must be > 0", run.get("capture_seconds"))
        if stage_name == "web_api":
            check_command_list("stage_runs.web_api", run, "console_command")
            check_command_list("stage_runs.web_api", run, "restart_console_command")
            for key in ("base_url", "console_log", "restart_base_url", "restart_console_log"):
                check_nonempty_string("stage_runs.web_api", run, key)
            try:
                console_pid = int(run.get("console_pid"))
                int(run.get("console_returncode"))
                restart_console_pid = int(run.get("restart_console_pid"))
                int(run.get("restart_console_returncode"))
            except (TypeError, ValueError) as exc:
                raise AssertionError(f"summary.stage_runs.web_api console pid/returncode fields must be numeric: {run}") from exc
            if console_pid <= 0:
                fail("summary.stage_runs.web_api.console_pid must be > 0", run.get("console_pid"))
            if restart_console_pid <= 0:
                fail("summary.stage_runs.web_api.restart_console_pid must be > 0", run.get("restart_console_pid"))
        if check_files:
            path_expectations = {
                "cwd": "dir",
                "workspace": "dir",
                "audit_log": "file",
                "evidence_json": "file",
            }
            if stage_name == "web_api":
                path_expectations["console_log"] = "file"
                path_expectations["restart_console_log"] = "file"
            missing_paths: dict[str, str] = {}
            for key, expected in path_expectations.items():
                path = resolved_path(run.get(key))
                exists = path.is_dir() if expected == "dir" else path.is_file()
                if not exists:
                    missing_paths[key] = str(path)
            if missing_paths:
                fail(f"summary.stage_runs.{stage_name} referenced paths are missing", missing_paths)


def check_stage_evidence_file(summary: dict[str, Any], stage_name: str, required_keys: tuple[str, ...]) -> None:
    stage_runs = summary.get("stage_runs") if isinstance(summary.get("stage_runs"), dict) else {}
    run = stage_runs.get(stage_name) if isinstance(stage_runs, dict) else None
    embedded = summary.get(stage_name)
    if not isinstance(run, dict) or not isinstance(embedded, dict):
        fail(f"summary.{stage_name} and stage_runs.{stage_name} must be present")
    path = resolved_path(run.get("evidence_json"))
    file_payload = load_json(path)
    mismatches: dict[str, object] = {}
    for key in required_keys:
        if file_payload.get(key) != embedded.get(key):
            mismatches[key] = {"summary": embedded.get(key), "file": file_payload.get(key)}
    summary_checks = embedded.get("checks")
    file_checks = file_payload.get("checks")
    if isinstance(summary_checks, dict) and isinstance(file_checks, dict) and summary_checks != file_checks:
        mismatches["checks"] = "stage evidence checks differ from summary checks"
    if mismatches:
        fail(f"{stage_name} stage evidence JSON does not match summary", mismatches)


def check_stage_evidence_files(summary: dict[str, Any]) -> None:
    check_stage_evidence_file(
        summary,
        "preflight",
        (
            "status",
            "configured_mic_device",
            "selected_mic_device",
            "workspace_dir",
            "audit_log_path",
            "sample_rate",
            "capture_seconds",
            "capture_probe",
            "endpoint_probe",
        ),
    )
    check_stage_evidence_file(
        summary,
        "direct_provider",
        (
            "status",
            "meeting_id",
            "workspace_dir",
            "audit_log_path",
            "output_dir",
            "transcript_path",
            "audio_path",
            "minutes_path",
            "session_path",
            "configured_mic_device",
            "selected_mic_device",
            "mic_probe",
            "endpoint_probe",
            "sample_rate",
            "audio_format",
            "stop_status_before_fetch",
            "minutes_path_after_stop",
            "status_after_fetch",
            "provider_task_id",
            "ai_minutes_source_data_id",
            "ai_minutes_task_id",
            "tingwu_minutes_summary",
            "tingwu_minutes_summary_source",
            "tingwu_minutes_structured",
            "audio_seconds",
            "audio_bytes",
            "websocket_audio_frames",
            "audio_rms",
            "audio_peak",
            "final_count",
            "transcript_items",
            "spoken_phrase",
            "spoken_phrase_detected",
            "redaction_canaries",
            "provider_events",
            "tingwu_http_operations",
        ),
    )
    check_stage_evidence_file(
        summary,
        "web_api",
        (
            "status",
            "meeting_id",
            "workspace_dir",
            "audit_log_path",
            "output_dir",
            "transcript_path",
            "audio_path",
            "session_path",
            "tingwu_minutes_path",
            "openclaw_minutes_path",
            "manifest_path",
            "sample_rate",
            "audio_format",
            "stop_status_before_fetch",
            "provider_status_before_fetch",
            "minutes_path_after_stop",
            "active_fetch_minutes_probe",
            "status_after_fetch",
            "allowed_roots_probe",
            "task_monitor",
            "decisions_path",
            "action_items_path",
            "followup_output_paths",
            "task_file",
            "notifications_path",
            "web_task_id",
            "configured_mic_device",
            "selected_mic_device",
            "mic_probe",
            "endpoint_probe",
            "full_control_gate",
            "meeting_jobs_restore",
            "web_restart_recovery",
            "provider_task_id",
            "ai_minutes_source_data_id",
            "ai_minutes_task_id",
            "tingwu_minutes_summary",
            "tingwu_minutes_summary_source",
            "tingwu_minutes_structured",
            "audio_seconds",
            "audio_bytes",
            "websocket_audio_frames",
            "audio_rms",
            "audio_peak",
            "final_count",
            "transcript_items",
            "spoken_phrase",
            "spoken_phrase_detected",
            "redaction_canaries",
            "steps",
            "provider_events",
            "tingwu_http_operations",
            "notifications",
        ),
    )


def check_goal_coverage(summary: dict[str, Any]) -> None:
    if summary.get("goal_completion_ready") is not True:
        fail("summary.goal_completion_ready must be true for acceptance", summary.get("goal_completion_ready"))
    readiness = summary.get("goal_readiness")
    if not isinstance(readiness, dict):
        fail("summary.goal_readiness must be present")
    expected_readiness = {
        "credentials_configured": True,
        "dashscope_tingwu_import": True,
        "official_tingwu_endpoint": True,
        "microphone_ready": True,
        "microphone_capture_device_matches": True,
        "workspace_writable": True,
        "audit_writable": True,
    }
    readiness_mismatches = {
        key: {"expected": expected, "actual": readiness.get(key)}
        for key, expected in expected_readiness.items()
        if readiness.get(key) is not expected
    }
    if not str(readiness.get("selected_mic_device") or "").strip():
        readiness_mismatches["selected_mic_device"] = "missing"
    if readiness_mismatches:
        fail("summary.goal_readiness does not prove local live prerequisites", readiness_mismatches)
    check_credential_diagnostics("summary.goal_readiness", readiness.get("credential_diagnostics"))

    requirements = summary.get("goal_requirements")
    if not isinstance(requirements, list):
        fail("summary.goal_requirements must be present")
    by_id = {
        str(item.get("id") or ""): item
        for item in requirements
        if isinstance(item, dict)
    }
    missing_ids = sorted(set(GOAL_REQUIREMENT_CHECKS) - set(by_id))
    extra_ids = sorted(set(by_id) - set(GOAL_REQUIREMENT_CHECKS))
    mismatches: dict[str, object] = {}
    if missing_ids:
        mismatches["missing_ids"] = missing_ids
    if extra_ids:
        mismatches["extra_ids"] = extra_ids

    stage_payloads = {
        "preflight": summary.get("preflight") if isinstance(summary.get("preflight"), dict) else {},
        "direct_provider": summary.get("direct_provider") if isinstance(summary.get("direct_provider"), dict) else {},
        "web_api": summary.get("web_api") if isinstance(summary.get("web_api"), dict) else {},
    }
    for requirement_id, required_checks in GOAL_REQUIREMENT_CHECKS.items():
        item = by_id.get(requirement_id)
        if not isinstance(item, dict):
            continue
        required_list = list(required_checks)
        if item.get("status") != "proven":
            mismatches[f"{requirement_id}.status"] = item.get("status")
        if item.get("required_checks") != required_list:
            mismatches[f"{requirement_id}.required_checks"] = {
                "expected": required_list,
                "actual": item.get("required_checks"),
            }
        if item.get("missing_checks") != []:
            mismatches[f"{requirement_id}.missing_checks"] = item.get("missing_checks")
        satisfied = set(item.get("satisfied_checks") if isinstance(item.get("satisfied_checks"), list) else [])
        missing_satisfied = sorted(set(required_list) - satisfied)
        if missing_satisfied:
            mismatches[f"{requirement_id}.satisfied_checks"] = missing_satisfied
        failed_underlying: list[str] = []
        for check_ref in required_checks:
            stage_name, check_name = check_ref.split(".", 1)
            stage = stage_payloads.get(stage_name)
            checks = stage.get("checks") if isinstance(stage, dict) else {}
            if not isinstance(checks, dict) or checks.get(check_name) is not True:
                failed_underlying.append(check_ref)
        if failed_underlying:
            mismatches[f"{requirement_id}.underlying_checks"] = failed_underlying
    if mismatches:
        fail("summary.goal_requirements do not prove every objective requirement", mismatches)


def check_credential_diagnostics(name: str, diagnostics: object) -> None:
    if diagnostics is None:
        return
    if not isinstance(diagnostics, dict):
        fail(f"{name}.credential_diagnostics must be a non-secret object", diagnostics)
    unexpected: dict[str, object] = {}
    for key in ("api_key_kind", "app_id_kind"):
        value = diagnostics.get(key)
        if value is None:
            continue
        if value not in SAFE_TINGWU_CREDENTIAL_KINDS:
            unexpected[key] = value
    extra_keys = sorted(str(key) for key in diagnostics if key not in {"api_key_kind", "app_id_kind"})
    if extra_keys:
        unexpected["extra_keys"] = extra_keys
    if unexpected:
        fail(f"{name}.credential_diagnostics must contain only safe credential kind enums", unexpected)


def check_summary_credential_diagnostics(summary: dict[str, Any]) -> None:
    check_credential_diagnostics("summary.goal_readiness", (summary.get("goal_readiness") or {}).get("credential_diagnostics") if isinstance(summary.get("goal_readiness"), dict) else None)
    for index, action in enumerate(summary.get("next_actions") if isinstance(summary.get("next_actions"), list) else []):
        if isinstance(action, dict):
            check_credential_diagnostics(f"summary.next_actions[{index}]", action.get("credential_diagnostics"))
    for stage_name in ("preflight", "direct_provider", "web_api"):
        stage = summary.get(stage_name)
        if isinstance(stage, dict):
            check_credential_diagnostics(f"summary.{stage_name}", stage.get("credential_diagnostics"))


def audit_summary(summary: dict[str, Any], *, check_files: bool = False) -> dict[str, Any]:
    if summary.get("status") != "ok" or summary.get("acceptance_complete") is not True:
        fail(
            "live suite summary is not a complete acceptance pass",
            {"status": summary.get("status"), "acceptance_complete": summary.get("acceptance_complete"), "stage_status": summary.get("stage_status")},
        )
    stage_status = summary.get("stage_status")
    if not isinstance(stage_status, dict):
        fail("summary.stage_status is missing")
    for stage in ("preflight", "direct_provider", "web_api"):
        if stage_status.get(stage) != "ok":
            fail(f"summary stage {stage} is not ok", stage_status)
    check_stage_runs(summary, check_files=check_files)
    check_summary_credential_diagnostics(summary)
    if check_files:
        check_summary_secret_absence(summary)

    preflight = summary.get("preflight")
    direct = summary.get("direct_provider")
    web = summary.get("web_api")
    if not isinstance(preflight, dict) or not isinstance(direct, dict) or not isinstance(web, dict):
        fail("summary must include preflight, direct_provider, and web_api evidence objects")
    if check_files:
        check_stage_evidence_files(summary)
    check_goal_coverage(summary)

    check_true_map(
        "preflight",
        preflight,
        {
            "tingwu_api_key_configured",
            "tingwu_app_id_configured",
            "dashscope_tingwu_import",
            "provider_available",
            "official_tingwu_endpoint",
            "microphone_selected",
            "real_microphone_device",
            "microphone_capture_device_matches",
            "microphone_capture_open",
            "microphone_capture_signal",
            "workspace_writable",
            "audit_writable",
        },
    )
    check_true_map("direct_provider", direct, REQUIRED_DIRECT_CHECKS)
    check_true_map("web_api", web, REQUIRED_WEB_CHECKS)

    for key in ("configured_mic_device", "selected_mic_device", "workspace_dir", "audit_log_path"):
        check_nonempty_string("preflight", preflight, key)
    check_preflight_probe(preflight)
    check_tingwu_endpoint("preflight", preflight)
    if check_files:
        check_artifacts("preflight", preflight, PREFLIGHT_ARTIFACT_KEYS)

    for payload_name, payload in (("direct_provider", direct), ("web_api", web)):
        for key in ("meeting_id", "configured_mic_device", "workspace_dir", "audit_log_path", "output_dir", "transcript_path", "audio_path", "provider_task_id"):
            check_nonempty_string(payload_name, payload, key)
        check_positive_number(payload_name, payload, "sample_rate")
        for key in ("ai_minutes_source_data_id", "ai_minutes_task_id"):
            check_nonempty_string(payload_name, payload, key)
        check_ai_minutes_metadata(payload_name, payload)
        check_tingwu_http_operations(payload_name, payload)
        check_tingwu_endpoint(payload_name, payload)
        check_real_microphone(payload_name, payload)
        check_stop_fetch_sequence(payload_name, payload)
        check_tingwu_minutes_structure(payload_name, payload)
        check_meeting_jobs_restore(payload_name, payload)
        check_positive_number(payload_name, payload, "audio_seconds")
        check_positive_number(payload_name, payload, "audio_bytes")
        check_audio_protocol(payload_name, payload)
        check_positive_number(payload_name, payload, "websocket_audio_frames")
        check_positive_number(payload_name, payload, "audio_rms")
        check_positive_number(payload_name, payload, "audio_peak")
        check_provider_events(payload_name, payload)

    check_nonempty_string("direct_provider", direct, "minutes_path")
    if check_files:
        check_artifacts("direct_provider", direct, DIRECT_ARTIFACT_KEYS)
        check_artifact_content("direct_provider", direct)
    for key in ("session_path", "tingwu_minutes_path", "openclaw_minutes_path", "manifest_path", "task_file", "notifications_path", "web_task_id"):
        check_nonempty_string("web_api", web, key)
    if check_files:
        check_artifacts("web_api", web, WEB_ARTIFACT_KEYS)
        check_followup_artifacts("web_api", web)
        check_artifact_content("web_api", web)
    check_allowed_roots_probe(web)
    check_full_control_gate(web)
    check_task_monitor_evidence(web)

    steps = web.get("steps")
    if not isinstance(steps, dict):
        fail("web_api.steps must be present")
    mismatched_steps = {
        name: steps.get(name)
        for name, expected in REQUIRED_WEB_STEPS.items()
        if steps.get(name) != expected
    }
    if mismatched_steps:
        fail("web_api.steps do not prove the full OpenClaw follow-up chain", mismatched_steps)

    notifications = web.get("notifications")
    if not isinstance(notifications, list) or not {"meeting_realtime_stopped", "meeting_ai_minutes_ready"}.issubset({str(item) for item in notifications}):
        fail("web_api.notifications missing meeting completion events", notifications)

    return {
        "status": "ok",
        "meeting_ids": {
            "direct_provider": direct.get("meeting_id"),
            "web_api": web.get("meeting_id"),
        },
        "evidence_dir": summary.get("evidence_dir"),
        "checked_requirements": {
            "preflight_checks": len(preflight.get("checks", {})),
            "direct_provider_checks": len(REQUIRED_DIRECT_CHECKS),
            "web_api_checks": len(REQUIRED_WEB_CHECKS),
            "web_steps": len(REQUIRED_WEB_STEPS),
            "goal_requirements": len(GOAL_REQUIREMENT_CHECKS),
            "artifact_files": len(PREFLIGHT_ARTIFACT_KEYS) + len(DIRECT_ARTIFACT_KEYS) + len(WEB_ARTIFACT_KEYS) if check_files else 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Tingwu live-suite evidence and fail unless it proves full acceptance.")
    parser.add_argument("summary_json", help="Path to verify_tingwu_live_suite.py evidence summary.json")
    parser.add_argument("--check-files", action="store_true", help="Also verify evidence artifact paths exist and workspace artifacts stay under workspace_dir.")
    args = parser.parse_args()

    summary_path = Path(args.summary_json).expanduser().resolve()
    summary = load_json(summary_path)
    summary["_summary_path"] = str(summary_path)
    result = audit_summary(summary, check_files=args.check_files)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("audit_tingwu_live_evidence complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
