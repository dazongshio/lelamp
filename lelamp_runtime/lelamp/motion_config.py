from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .motor_control import LELAMP_MOTOR_ORDER


DEFAULT_MOTION_CONFIG_VERSION = 1


def runtime_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_motion_config_path() -> Path:
    configured = os.getenv("LELAMP_MOTION_CONFIG", "").strip()
    if configured:
        return Path(configured).expanduser()
    workspace = Path(os.getenv("OPENCLAW_WORKSPACE_DIR", runtime_root() / "workspace")).expanduser()
    return workspace / "lelamp_motion_config.json"


def empty_motion_config() -> dict[str, Any]:
    return {
        "version": DEFAULT_MOTION_CONFIG_VERSION,
        "updated_at": time.time(),
        "poses": {},
        "actions": {},
    }


def load_motion_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or default_motion_config_path()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return empty_motion_config()
    if not isinstance(payload, dict):
        return empty_motion_config()
    payload.setdefault("version", DEFAULT_MOTION_CONFIG_VERSION)
    payload.setdefault("poses", {})
    payload.setdefault("actions", {})
    return payload


def save_motion_config(config: dict[str, Any], path: Path | None = None) -> Path:
    config_path = path or default_motion_config_path()
    payload = dict(config)
    payload["version"] = int(payload.get("version") or DEFAULT_MOTION_CONFIG_VERSION)
    payload["updated_at"] = time.time()
    payload.setdefault("poses", {})
    payload.setdefault("actions", {})
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    tmp = config_path.with_name(f".{config_path.name}.tmp")
    tmp.write_bytes(data)
    tmp.replace(config_path)
    return config_path


def normalize_pose(values: Any) -> dict[str, float]:
    if not isinstance(values, dict):
        return {}
    pose: dict[str, float] = {}
    for motor in LELAMP_MOTOR_ORDER:
        if motor not in values:
            continue
        try:
            pose[motor] = round(float(values[motor]), 4)
        except (TypeError, ValueError):
            continue
    return pose


def complete_pose(values: Any) -> dict[str, float]:
    pose = normalize_pose(values)
    return pose if all(motor in pose for motor in LELAMP_MOTOR_ORDER) else {}


def get_named_pose(config: dict[str, Any], name: str) -> dict[str, float]:
    poses = config.get("poses")
    if not isinstance(poses, dict):
        return {}
    entry = poses.get(name)
    if isinstance(entry, dict) and isinstance(entry.get("motors"), dict):
        return complete_pose(entry.get("motors"))
    return complete_pose(entry)


def set_named_pose(config: dict[str, Any], name: str, motors: dict[str, float], *, label: str = "") -> dict[str, Any]:
    poses = config.setdefault("poses", {})
    if not isinstance(poses, dict):
        poses = {}
        config["poses"] = poses
    poses[name] = {
        "label": label or name,
        "motors": complete_pose(motors),
    }
    return config


def get_action_recording(config: dict[str, Any], name: str) -> str:
    actions = config.get("actions")
    if not isinstance(actions, dict):
        return ""
    entry = actions.get(name)
    if not isinstance(entry, dict):
        return ""
    recording = entry.get("recording")
    return str(recording).strip() if recording is not None else ""


def get_action_mode(config: dict[str, Any], name: str) -> str:
    actions = config.get("actions")
    if not isinstance(actions, dict):
        return "absolute"
    entry = actions.get(name)
    if not isinstance(entry, dict):
        return "absolute"
    mode = str(entry.get("mode") or "").strip().lower()
    if mode in {"mixed", "configured_absolute", "absolute_with_current_hold"}:
        return "mixed"
    if mode in {"relative", "delta", "delta_from_current"}:
        return "relative"
    return "absolute"


def get_action_keyframes(config: dict[str, Any], name: str) -> list[dict[str, float]]:
    actions = config.get("actions")
    if not isinstance(actions, dict):
        return []
    entry = actions.get(name)
    if not isinstance(entry, dict):
        return []
    frames = entry.get("keyframes")
    if not isinstance(frames, list):
        return []
    result: list[dict[str, float]] = []
    for frame in frames:
        motors = frame.get("motors") if isinstance(frame, dict) else frame
        pose = normalize_pose(motors)
        if pose:
            repeat = 1
            if isinstance(frame, dict):
                try:
                    repeat = int(frame.get("frames") or 1)
                except (TypeError, ValueError):
                    repeat = 1
            repeat = max(1, min(300, repeat))
            action = {f"{motor}.pos": value for motor, value in pose.items()}
            result.extend(dict(action) for _ in range(repeat))
    return result
