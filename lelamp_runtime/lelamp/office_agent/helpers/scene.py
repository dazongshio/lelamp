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

__all__ = ['compact_scene_snapshot', 'infer_ambient_lux_from_scene_event', 'dedupe_scene_events', 'dedupe_events']

def compact_scene_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    camera = snapshot.get("camera") if isinstance(snapshot.get("camera"), dict) else {}
    microphone = snapshot.get("microphone") if isinstance(snapshot.get("microphone"), dict) else {}
    reading = snapshot.get("reading") if isinstance(snapshot.get("reading"), dict) else {}
    hardware = snapshot.get("hardware") if isinstance(snapshot.get("hardware"), dict) else {}
    projection = hardware.get("projection") if isinstance(hardware.get("projection"), dict) else {}
    return {
        "task_id": snapshot.get("task_id"),
        "status": snapshot.get("status"),
        "camera": {
            "status": camera.get("status"),
            "camera_index": camera.get("camera_index"),
            "workspace_name": camera.get("workspace_name"),
            "image_path": camera.get("image_path"),
            "source": camera.get("source"),
        },
        "microphone": {
            "status": microphone.get("status"),
            "rms": microphone.get("rms"),
            "peak": microphone.get("peak"),
            "activity_detected": microphone.get("activity_detected"),
        },
        "projection": {
            "status": projection.get("status"),
        },
        "reading": {
            "presence": reading.get("presence"),
            "speech_active": reading.get("speech_active"),
            "lux": reading.get("lux"),
            "projector_blocked": reading.get("projector_blocked"),
        },
        "event_count": snapshot.get("event_count"),
    }


def infer_ambient_lux_from_scene_event(event: dict[str, object]) -> float:
    text = f"{event.get('event_type') or ''} {event.get('description') or ''}"
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:lux|照度)", text, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    event_type = str(event.get("event_type") or "")
    if event_type == "ambient_too_dark":
        return 45.0
    if event_type in {"ambient_too_bright", "projection_too_bright"}:
        return 1100.0
    return 300.0


def dedupe_scene_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        key = (str(event.get("event_type") or ""), str(event.get("description") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def dedupe_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        marker = (str(event.get("event") or event.get("type") or ""), str(event.get("timestamp") or ""))
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(event)
    return merged

