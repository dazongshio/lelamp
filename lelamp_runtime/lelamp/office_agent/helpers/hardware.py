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

__all__ = ['round_motor_map', 'split_wave_channels', 'workspace_name_for_path', 'scan_offsets_from_payload', 'scan_view_plan_from_payload', 'serial_device_candidates', 'hardware_device_details', 'read_system_sensors']

def clamp_number(*args, **kwargs):
    from .. import web_helpers
    return web_helpers.clamp_number(*args, **kwargs)

def optional_float(*args, **kwargs):
    from .. import web_helpers
    return web_helpers.optional_float(*args, **kwargs)

def round_motor_map(values: Any, keys: Iterable[str] | None = None) -> dict[str, float]:
    if not isinstance(values, dict):
        return {}
    source_keys = list(keys) if keys is not None else list(values.keys())
    rounded: dict[str, float] = {}
    for key in source_keys:
        if key not in values:
            continue
        numeric = optional_float(values.get(key))
        if numeric is not None and math.isfinite(numeric):
            rounded[str(key)] = round(float(numeric), 4)
    return rounded


def split_wave_channels(path: Path) -> list[dict[str, object]]:
    import audioop

    with wave.open(str(path), "rb") as source:
        channel_count = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        frame_count = source.getnframes()
        frames = source.readframes(frame_count)
    duration = frame_count / sample_rate if sample_rate else 0
    if channel_count <= 1:
        return [
            {
                "channel": "mono",
                "label": "单声道",
                "path": path,
                "rms": audioop.rms(frames, sample_width) if frames else 0,
                "peak": audioop.max(frames, sample_width) if frames else 0,
                "duration_seconds": round(duration, 2),
            }
        ]

    channels: list[dict[str, object]] = []
    labels = ["左声道", "右声道"]
    for index in range(min(channel_count, 2)):
        channel_frames = audioop.tomono(
            frames,
            sample_width,
            1.0 if index == 0 else 0.0,
            0.0 if index == 0 else 1.0,
        )
        channel_path = path.with_name(f"{path.stem}_{'left' if index == 0 else 'right'}{path.suffix}")
        with wave.open(str(channel_path), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(sample_width)
            target.setframerate(sample_rate)
            target.writeframes(channel_frames)
        channels.append(
            {
                "channel": "left" if index == 0 else "right",
                "label": labels[index],
                "path": channel_path,
                "rms": audioop.rms(channel_frames, sample_width) if channel_frames else 0,
                "peak": audioop.max(channel_frames, sample_width) if channel_frames else 0,
                "duration_seconds": round(duration, 2),
            }
        )
    return channels


def workspace_name_for_path(workspace_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace_root.resolve()))
    except ValueError:
        return ""


def scan_offsets_from_payload(value: Any, yaw_delta: float) -> list[float]:
    offsets: list[float] = []
    if isinstance(value, list):
        for item in value:
            numeric = optional_float(item)
            if numeric is not None:
                offsets.append(clamp_number(numeric, default=0.0, low=-8.0, high=8.0))
            if len(offsets) >= 3:
                break
    if not offsets:
        offsets = [-yaw_delta, 0.0, yaw_delta]
    if 0.0 not in offsets:
        offsets.insert(min(1, len(offsets)), 0.0)
    return offsets[:3]


def scan_view_plan_from_payload(
    views_value: Any,
    offsets_value: Any,
    *,
    yaw_delta: float,
    pitch_delta: float,
    mode: str,
    view_limit: int,
) -> list[dict[str, object]]:
    planned: list[dict[str, object]] = []
    if isinstance(views_value, list):
        for index, item in enumerate(views_value):
            if not isinstance(item, dict):
                continue
            yaw = clamp_number(optional_float(item.get("yaw_offset")), default=0.0, low=-12.0, high=12.0)
            pitch = clamp_number(optional_float(item.get("pitch_offset")), default=0.0, low=-8.0, high=8.0)
            planned.append({"label": str(item.get("label") or f"view_{index + 1}"), "yaw_offset": yaw, "pitch_offset": pitch})
            if len(planned) >= view_limit:
                break
    if not planned and mode == "yaw":
        planned = [
            {"label": "left", "yaw_offset": offset, "pitch_offset": 0.0}
            for offset in scan_offsets_from_payload(offsets_value, yaw_delta)
        ]
    if not planned:
        base_plan = [
            ("center", 0.0, 0.0),
            ("left", -yaw_delta, 0.0),
            ("right", yaw_delta, 0.0),
            ("up", 0.0, pitch_delta),
            ("down", 0.0, -pitch_delta),
            ("left_up", -yaw_delta, pitch_delta),
            ("right_up", yaw_delta, pitch_delta),
            ("left_down", -yaw_delta, -pitch_delta),
            ("right_down", yaw_delta, -pitch_delta),
        ]
        planned = [{"label": label, "yaw_offset": yaw, "pitch_offset": pitch} for label, yaw, pitch in base_plan]
    deduped: list[dict[str, object]] = []
    seen: set[tuple[float, float]] = set()
    for item in planned:
        yaw = round(float(item["yaw_offset"]), 3)
        pitch = round(float(item["pitch_offset"]), 3)
        key = (yaw, pitch)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"label": item["label"], "yaw_offset": yaw, "pitch_offset": pitch})
        if len(deduped) >= view_limit:
            break
    return deduped or [{"label": "center", "yaw_offset": 0.0, "pitch_offset": 0.0}]


def serial_device_candidates() -> list[str]:
    candidates: list[str] = []
    for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*"):
        candidates.extend(str(path) for path in sorted(Path("/dev").glob(Path(pattern).name)))
    return candidates


def hardware_device_details(scan: dict[str, object], key: str) -> dict[str, object]:
    devices = scan.get("devices")
    if not isinstance(devices, dict):
        return {}
    device = devices.get(key)
    if not isinstance(device, dict):
        return {}
    details = device.get("details")
    return details if isinstance(details, dict) else {}


def read_system_sensors(workspace_dir: Path) -> dict[str, object]:
    sensors: dict[str, object] = {
        "cpu_temp": None,
        "cpu_usage": None,
        "memory_usage": None,
        "disk_usage": None,
    }
    temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        if temp_path.exists():
            sensors["cpu_temp"] = round(int(temp_path.read_text().strip()) / 1000, 1)
    except (OSError, ValueError):
        sensors["cpu_temp"] = None
    try:
        stat = os.statvfs(workspace_dir)
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
        sensors["disk_usage"] = round((total - free) / total, 4) if total else None
    except OSError:
        sensors["disk_usage"] = None
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
        values: dict[str, int] = {}
        for line in meminfo.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                values[parts[0].rstrip(":")] = int(parts[1])
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        sensors["memory_usage"] = round((total - available) / total, 4) if total else None
    except (OSError, ValueError):
        sensors["memory_usage"] = None
    return sensors

