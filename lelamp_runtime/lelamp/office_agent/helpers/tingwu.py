from __future__ import annotations

import hashlib
import html
import ipaddress
import importlib.util
import json
import math
import os
import re
import secrets
import shlex
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from lelamp.motor_control import LELAMP_MOTOR_ORDER

from ..audio_api import AudioAPIError, OpenAIAudioAPI
from ..config import tingwu_credential_next_actions
from ..dashscope_tts import DashScopeTTS, DashScopeTTSError
from ..documents import DOCUMENT_WORKFLOW_SUFFIXES
from ..elevenlabs_tts import ElevenLabsError, ElevenLabsTTS
from ..hardware_probe import play_audio_file, probe_hardware
from ..tingwu_meeting import PLACEHOLDER_CAPTURE_DEVICES, sanitize_event_payload

LELAMP_CONTROL_MOTORS = LELAMP_MOTOR_ORDER
OFFICIAL_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
OFFICIAL_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

__all__ = ['endpoint_matches', 'is_real_tingwu_microphone', 'capture_probe_matches_selected_microphone', 'tingwu_live_acceptance_commands', 'tingwu_provider_preflight_next_actions', 'tingwu_provider_acceptance_checklist', 'tingwu_capture_status', 'tingwu_realtime_task_summary']

def runtime_root(*args, **kwargs):
    from .. import web_helpers
    return web_helpers.runtime_root(*args, **kwargs)

def endpoint_matches(url: object, expected: str) -> bool:
    parsed = urllib.parse.urlsplit(str(url or ""))
    expected_parsed = urllib.parse.urlsplit(expected)
    return (
        parsed.scheme.lower() == expected_parsed.scheme
        and (parsed.hostname or "").lower() == (expected_parsed.hostname or "").lower()
        and (parsed.path.rstrip("/") or "/") == (expected_parsed.path.rstrip("/") or "/")
        and (parsed.port or 443) == (expected_parsed.port or 443)
    )


def is_real_tingwu_microphone(selected: str, probe: dict[str, object]) -> bool:
    selected_normalized = str(selected or "").strip().lower()
    configured_normalized = str(probe.get("configured_device") or "").strip().lower()
    message = str(probe.get("message") or "").lower()
    fake_devices = {"fake-mic", "mock", "mock-mic"}
    return (
        bool(selected_normalized)
        and selected_normalized not in PLACEHOLDER_CAPTURE_DEVICES
        and selected_normalized not in fake_devices
        and configured_normalized not in fake_devices
        and str(probe.get("status") or "") != "mock"
        and "fake microphone" not in message
        and "tingwu_mock=1" not in message
    )


def capture_probe_matches_selected_microphone(selected: str, capture_probe: dict[str, object]) -> bool:
    selected_normalized = str(selected or "").strip().lower()
    capture_selected = str(capture_probe.get("selected_device") or "").strip().lower()
    fake_devices = {"fake-mic", "mock", "mock-mic"}
    return (
        bool(selected_normalized)
        and selected_normalized not in PLACEHOLDER_CAPTURE_DEVICES
        and capture_selected == selected_normalized
        and capture_selected not in fake_devices
    )


def tingwu_live_acceptance_commands() -> dict[str, object]:
    runtime_root = Path(__file__).resolve().parents[3]
    repo_root = runtime_root.parent
    venv_python = runtime_root / ".venv" / "bin" / "python"
    python = str(venv_python if venv_python.is_file() else Path(sys.executable).resolve())
    preflight_command = [
        python,
        str(repo_root / "scripts" / "preflight_tingwu_live.py"),
        "--capture-seconds",
        "3",
    ]
    acceptance_command = [
        python,
        str(repo_root / "scripts" / "verify_tingwu_live_suite.py"),
        "--env-file",
        ".env.tingwu.local",
        "--seconds",
        "12",
        "--preflight-capture-seconds",
        "3",
        "--spoken-phrase",
        "乐灯听悟验收测试",
        "--evidence-dir",
        "/tmp/lelamp-tingwu-evidence",
    ]
    audit_command = [
        python,
        str(repo_root / "scripts" / "audit_tingwu_live_evidence.py"),
        "/tmp/lelamp-tingwu-evidence/summary.json",
        "--check-files",
    ]
    credential_links = [
        {
            "label": "百炼中国站控制台",
            "url": "https://bailian.console.aliyun.com/",
        },
        {
            "label": "API Key 获取说明",
            "url": "https://help.aliyun.com/zh/model-studio/get-api-key/",
        },
        {
            "label": "APP ID 获取说明",
            "url": "https://help.aliyun.com/zh/model-studio/obtain-api-key-app-id-and-workspace-id/",
        },
        {
            "label": "通义听悟实时记录接入",
            "url": "https://help.aliyun.com/zh/tingwu/interface-and-implementation",
        },
    ]
    return {
        "cwd": str(runtime_root),
        "preflight_command": preflight_command,
        "acceptance_command": acceptance_command,
        "audit_command": audit_command,
        "credential_links": credential_links,
        "credentials_env": {
            "ENV_FILE": ".env.tingwu.local",
        },
        "microphone_env": {"OPENCLAW_MIC_DEVICE": "auto"},
    }


def tingwu_provider_preflight_next_actions(
    checks: dict[str, object],
    *,
    credential_diagnostics: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    commands = tingwu_live_acceptance_commands()
    runtime_root = str(commands["cwd"])
    acceptance_command = list(commands["acceptance_command"])  # type: ignore[arg-type]
    audit_command = list(commands["audit_command"])  # type: ignore[arg-type]
    credential_diagnostics = credential_diagnostics or {}
    credential_guidance = tingwu_credential_next_actions(
        str(credential_diagnostics.get("api_key_kind") or ""),
        str(credential_diagnostics.get("app_id_kind") or ""),
    )
    if checks.get("tingwu_api_key_configured") is not True or checks.get("tingwu_app_id_configured") is not True:
        return [
            {
                "id": "configure_tingwu_credentials",
                "status": "required",
                "message": "；".join(credential_guidance) or "复制 .env.tingwu.example 为 .env.tingwu.local，填入新 Key 和百炼 Model Studio 应用 App ID 后运行验收。",
                "credential_diagnostics": credential_diagnostics,
                "env": commands["credentials_env"],
                "links": commands["credential_links"],
                "cwd": runtime_root,
                "command": acceptance_command,
                "audit_command": audit_command,
            }
        ]
    if checks.get("official_tingwu_endpoint") is not True:
        return [
            {
                "id": "restore_official_tingwu_endpoint",
                "status": "required",
                "message": "恢复官方 DashScope HTTP/WS 端点后再验收。",
                "env": {
                    "TINGWU_HTTP_URL": OFFICIAL_TINGWU_HTTP_URL,
                    "TINGWU_WS_URL": OFFICIAL_TINGWU_WS_URL,
                },
                "cwd": runtime_root,
            }
        ]
    if checks.get("real_microphone_device") is not True or checks.get("microphone_available") is not True:
        return [
            {
                "id": "select_real_alsa_microphone",
                "status": "required",
                "message": "选择真实 USB/ALSA 麦克风，避免 default/pulse/mock/fake 设备。",
                "env": commands["microphone_env"],
                "cwd": runtime_root,
            }
        ]
    if checks.get("microphone_capture_device_matches") is not True:
        return [
            {
                "id": "match_selected_capture_device",
                "status": "required",
                "message": "确认预检采集设备和选中的 ALSA 设备一致。",
            }
        ]
    if checks.get("microphone_capture_open") is not True or checks.get("microphone_capture_signal") is not True:
        return [
            {
                "id": "capture_non_silent_pcm",
                "status": "required",
                "message": "靠近麦克风说话，确认 arecord 能打开设备并采到非静音 PCM。",
                "cwd": runtime_root,
                "command": commands["preflight_command"],
            }
        ]
    return [
        {
            "id": "start_live_meeting_acceptance",
            "status": "ready",
            "message": "开始实时会议，口播“乐灯听悟验收测试”，停止后拉取 AI 纪要。",
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        }
    ]


def tingwu_provider_acceptance_checklist(checks: dict[str, object]) -> list[dict[str, object]]:
    commands = tingwu_live_acceptance_commands()
    runtime_root = str(commands["cwd"])
    preflight_command = list(commands["preflight_command"])  # type: ignore[arg-type]
    acceptance_command = list(commands["acceptance_command"])  # type: ignore[arg-type]
    audit_command = list(commands["audit_command"])  # type: ignore[arg-type]
    credentials_env = commands["credentials_env"]
    microphone_env = commands["microphone_env"]
    credentials_ok = (
        checks.get("tingwu_api_key_configured") is True
        and checks.get("tingwu_app_id_configured") is True
        and checks.get("provider_configured") is True
    )
    endpoint_ok = checks.get("official_tingwu_endpoint") is True
    microphone_ok = (
        checks.get("microphone_available") is True
        and checks.get("real_microphone_device") is True
        and checks.get("microphone_capture_device_matches") is True
        and checks.get("microphone_capture_open") is True
        and checks.get("microphone_capture_signal") is True
    )
    preflight_ok = credentials_ok and endpoint_ok and microphone_ok

    def status(done: bool, *, ready_after_preflight: bool = False) -> str:
        if done:
            return "completed"
        if ready_after_preflight and preflight_ok:
            return "ready"
        return "blocked" if not preflight_ok else "pending"

    return [
        {
            "id": "import_transcript",
            "title": "导入 transcript",
            "status": "ready",
            "how_to_test": "从 shared_inbox/workspace/allowed roots 选择会议转写文件并点击导入；确认 import_transcript 步骤完成，越界路径被拒绝并写入审计。",
            "evidence": ["step_import_transcript_completed", "meeting_import_transcript", "allowed_roots_blocked"],
        },
        {
            "id": "credentials",
            "title": "配置通义听悟凭证",
            "status": "completed" if credentials_ok else "blocked",
            "how_to_test": "复制 .env.tingwu.example 为 .env.tingwu.local，填入新 Key 和 App ID；或确认等价环境变量已在运行 Web Console 的 shell 中配置。",
            "evidence": ["tingwu_api_key_configured", "tingwu_app_id_configured", "provider_configured"],
            "env": credentials_env,
            "links": commands["credential_links"],
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
        {
            "id": "local_audio_preflight",
            "title": "本地麦克风预检",
            "status": "completed" if endpoint_ok and microphone_ok else "blocked",
            "how_to_test": "点击本地预检，确认官方 DashScope 端点、真实 ALSA/USB 麦克风、设备一致、采集打开和非静音信号全部通过。",
            "env": microphone_env,
            "cwd": runtime_root,
            "command": preflight_command,
            "evidence": [
                "official_tingwu_endpoint",
                "real_microphone_device",
                "microphone_capture_device_matches",
                "microphone_capture_open",
                "microphone_capture_signal",
            ],
        },
        {
            "id": "live_realtime_create_task",
            "title": "开始实时会议并创建听悟任务",
            "status": status(False, ready_after_preflight=True),
            "how_to_test": "点击开始实时会议，现场口播“乐灯听悟验收测试”，确认通义听悟 CreateTask 和实时 meeting id 出现在诊断信息里。",
            "evidence": ["provider_task_id", "tingwu_http_operations", "meeting_id"],
            "env": credentials_env,
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
        {
            "id": "websocket_pcm_streaming",
            "title": "WebSocket PCM 音频推流",
            "status": status(False, ready_after_preflight=True),
            "how_to_test": "会议运行中查看实时任务监控，确认 websocket_audio_frames、audio_seconds、audio_rms、audio_peak 持续增长。",
            "evidence": ["websocket_audio_frames", "audio_seconds", "audio_rms", "audio_peak"],
            "env": credentials_env,
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
        {
            "id": "realtime_transcript",
            "title": "实时 transcript 回传",
            "status": status(False, ready_after_preflight=True),
            "how_to_test": "查看实时转写区域，确认听到的口播短语出现在 transcript，并保存到 workspace。",
            "evidence": ["transcript_path", "realtime_transcript", "spoken_phrase_detected"],
            "env": credentials_env,
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
        {
            "id": "stop_then_fetch_minutes",
            "title": "停止会议后拉取 AI 纪要",
            "status": status(False, ready_after_preflight=True),
            "how_to_test": "先点击停止实时会议，再点击拉取 AI 纪要；会议未停止时 fetch-minutes 应被阻断。",
            "evidence": ["stop_status_before_fetch", "tingwu_minutes_path", "ai_minutes_task_id"],
            "env": credentials_env,
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
        {
            "id": "openclaw_followup_outputs",
            "title": "OpenClaw 后处理产物",
            "status": status(False, ready_after_preflight=True),
            "how_to_test": "确认 decisions、action items、follow-up email draft、reminders、projection confirmation 都写入 workspace/meetings/{meeting_id}。",
            "evidence": ["openclaw_minutes_path", "followup_output_paths", "manifest_path"],
            "env": credentials_env,
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
        {
            "id": "ui_task_assistant_audit",
            "title": "UI、任务监控、助手通知和审计",
            "status": status(False, ready_after_preflight=True),
            "how_to_test": "刷新页面后确认 Meeting UI 可恢复会议输出；任务监控保留实时指标；AssistantPanel 出现会议通知；Audit 页面可搜索关键动作。",
            "evidence": ["task_monitor", "assistant_notifications", "audit_log"],
            "env": credentials_env,
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
    ]


def tingwu_capture_status(session: dict[str, object]) -> str:
    status = str(session.get("status") or "")
    if status in {"starting", "running", "stopping"}:
        return status
    realtime_transcript = str(session.get("realtime_transcript") or "").strip()
    transcript_items = session.get("transcript") if isinstance(session.get("transcript"), list) else []
    try:
        audio_seconds = float(session.get("audio_seconds") or 0)
    except (TypeError, ValueError):
        audio_seconds = 0.0
    if status == "failed" and not (realtime_transcript or transcript_items or audio_seconds > 0):
        return "failed"
    transcript_path = Path(str(session.get("transcript_path") or ""))
    if transcript_path.is_file() or realtime_transcript or transcript_items or audio_seconds > 0:
        return "completed"
    return "failed" if status == "failed" else status or "completed"


def tingwu_realtime_task_summary(session: dict[str, object]) -> dict[str, object]:
    keys = (
        "provider",
        "status",
        "meeting_id",
        "title",
        "participants",
        "task_id",
        "data_id",
        "websocket_task_id",
        "created_at",
        "started_at",
        "stopped_at",
        "output_dir",
        "transcript_path",
        "audio_path",
        "minutes_path",
        "audio_bytes",
        "audio_seconds",
        "sample_rate",
        "audio_format",
        "websocket_audio_frames",
        "audio_rms",
        "audio_peak",
        "realtime_transcript",
        "partial_text",
        "final_count",
        "tingwu_http_operations",
        "error",
    )
    summary = {key: session.get(key) for key in keys if key in session}
    transcript = session.get("transcript")
    if isinstance(transcript, list):
        summary["transcript"] = transcript[-40:]
    task_payload = session.get("task_payload") if isinstance(session.get("task_payload"), dict) else {}
    events = task_payload.get("events") if isinstance(task_payload.get("events"), list) else []
    if events:
        summary["events"] = [item for item in events if isinstance(item, dict)][-200:]
    return sanitize_event_payload(summary)

