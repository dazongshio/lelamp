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

__all__ = ['atomic_write_json', 'runtime_root', 'quote_env_value', 'update_local_env_value', 'pid_alive', 'process_cmdline', 'atomic_write_text_file', 'atomic_write_bytes', 'parse_json_body', 'require_string', 'list_string', 'safe_int', 'safe_float', 'payload_bool', 'optional_float', 'clamp_number', 'now_iso', 'sanitize_id', 'format_bytes', 'redact_target', 'redact_provider_url', 'csv_escape', 'parse_datetime', 'read_recent_audit', 'render_error_page']

def atomic_write_json(path: Path, payload: object) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    atomic_write_bytes(path, data.encode("utf-8"))


def runtime_root() -> Path:
    return Path(__file__).resolve().parents[3]


def quote_env_value(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value):
        return value
    return json.dumps(value, ensure_ascii=False)


def update_local_env_value(env_path: Path, key: str, value: str) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    output: list[str] = []
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
    updated = False
    for line in lines:
        if pattern.match(line):
            if not updated:
                output.append(f"{key}={quote_env_value(value)}")
                updated = True
            continue
        output.append(line)
    if not updated:
        if output and output[-1].strip():
            output.append("")
        output.append(f"{key}={quote_env_value(value)}")
    atomic_write_text_file(env_path, "\n".join(output).rstrip() + "\n")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_cmdline(pid: int) -> list[str]:
    path = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def atomic_write_text_file(path: Path, content: str) -> None:
    atomic_write_bytes(path, content.encode("utf-8"))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def parse_json_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Expected JSON body.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Expected JSON object.")
    return payload


def require_string(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"Missing required field: {key}")
    return value


def list_string(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return [str(value)]


def safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def payload_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp_number(value: float | None, *, default: float, low: float, high: float) -> float:
    if value is None:
        value = default
    return max(low, min(high, float(value)))


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sanitize_id(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})[:80]


def format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def redact_target(target: str) -> str:
    value = str(target)
    sensitive = [".ssh", "id_rsa", "passwd", "cookie", "Cookies", "keychain", "token"]
    if any(marker in value for marker in sensitive):
        return Path(value).name if Path(value).name else "[redacted]"
    return value[:200]


def redact_provider_url(url: str) -> str:
    value = str(url or "")
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if not parsed.netloc:
        return value[:120]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def csv_escape(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def read_recent_audit(path: Path, *, limit: int) -> list[dict[str, object]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    events: list[dict[str, object]] = []
    for line in lines[-limit:]:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def render_error_page(title: str, message: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{html.escape(title)}</title></head>
<body><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body>
</html>"""

