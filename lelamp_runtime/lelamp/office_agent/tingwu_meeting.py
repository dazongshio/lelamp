from __future__ import annotations

import audioop
import fcntl
import ipaddress
import math
import os
import re
import secrets
import json
import shutil
import socket
import subprocess
import threading
import time
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Any
from urllib import error, parse, request

from dashscope.multimodal.tingwu.tingwu_realtime import TingWuRealtime, TingWuRealtimeCallback

from .audit import AuditLogger
from .config import OfficeAgentConfig, tingwu_credential_kind
from .utils import safe_filename
from .workspace import Workspace


class TingwuMeetingError(RuntimeError):
    def __init__(self, message: str, *, details: dict[str, object] | None = None):
        super().__init__(message)
        self.details = sanitize_event_payload(details or {})


RECOVERABLE_ACTIVE_STATUSES = {"starting", "running", "stopping", "finalizing"}
ACTIVE_MEETING_STATUSES = {"starting", "running", "stopping", "finalizing"}
MAX_TINGWU_API_BYTES = 1024 * 1024
MAX_TINGWU_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_TINGWU_EVENT_TEXT_CHARS = 2000
MAX_TINGWU_EVENT_LIST_ITEMS = 20
MAX_TINGWU_PROVIDER_EVENTS = 200
MAX_TINGWU_AI_EVENTS = 50
MAX_TINGWU_RAW_EVENTS = 80
MAX_TINGWU_AGENT_EVENTS = 80
MAX_TINGWU_HTTP_OPERATIONS = 50
MAX_TINGWU_ARTIFACT_REDIRECTS = 3
TINGWU_WORKSPACE_LOCK_NAME = ".tingwu_realtime.lock"
PLACEHOLDER_CAPTURE_DEVICES = {"default", "pulse", "sysdefault"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def synthetic_pcm_frame(sample_rate: int, *, seconds: float = 1.0, frequency: float = 440.0, amplitude: int = 1200) -> bytes:
    samples = max(1, int(sample_rate * seconds))
    amplitude = max(1, min(32767, int(amplitude)))
    period = max(1.0, float(sample_rate) / max(1.0, float(frequency)))
    frame = bytearray(samples * 2)
    for index in range(samples):
        sample = int(math.sin((index / period) * math.tau) * amplitude)
        frame[index * 2 : index * 2 + 2] = sample.to_bytes(2, "little", signed=True)
    return bytes(frame)


def pcm_signal_metrics(raw: bytes, *, sample_width: int = 2) -> dict[str, int]:
    if not raw:
        return {"audio_bytes": 0, "audio_rms": 0, "audio_peak": 0}
    usable = len(raw) - (len(raw) % sample_width)
    if usable <= 0:
        return {"audio_bytes": len(raw), "audio_rms": 0, "audio_peak": 0}
    frame = raw[:usable]
    return {
        "audio_bytes": len(raw),
        "audio_rms": int(audioop.rms(frame, sample_width)),
        "audio_peak": int(audioop.max(frame, sample_width)),
    }


def amplify_pcm16(frame: bytes, gain: float) -> bytes:
    gain = float(gain or 1.0)
    if not frame or gain == 1.0:
        return frame
    usable = len(frame) - (len(frame) % 2)
    if usable <= 0:
        return frame
    output = bytearray(len(frame))
    for index in range(0, usable, 2):
        sample = int.from_bytes(frame[index : index + 2], "little", signed=True)
        amplified = max(-32768, min(32767, int(sample * gain)))
        output[index : index + 2] = amplified.to_bytes(2, "little", signed=True)
    if usable < len(frame):
        output[usable:] = frame[usable:]
    return bytes(output)


def redact_provider_url(url: str) -> str:
    value = str(url or "")
    if not value:
        return ""
    parsed = parse.urlsplit(value)
    if not parsed.netloc:
        return value[:120]
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def redact_url_origin(url: str) -> str:
    value = str(url or "")
    parsed = parse.urlsplit(value)
    if not parsed.netloc:
        return "[invalid-url]"
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return parse.urlunsplit((parsed.scheme, host, "", "", ""))


def validate_tingwu_artifact_url(url: str, *, trusted_base_url: str = "") -> str:
    value = str(url or "").strip()
    parsed = parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TingwuMeetingError(f"Blocked unsupported Tingwu artifact URL: {redact_url_origin(value)}")
    trusted = parse.urlsplit(str(trusted_base_url or ""))
    if not same_url_origin(parsed, trusted) and not public_hostname(str(parsed.hostname or "")):
        raise TingwuMeetingError(f"Blocked non-public Tingwu artifact URL: {redact_url_origin(value)}")
    return value


def same_url_origin(left: parse.SplitResult, right: parse.SplitResult) -> bool:
    return bool(
        left.scheme
        and right.scheme
        and left.scheme == right.scheme
        and (left.hostname or "").lower() == (right.hostname or "").lower()
        and effective_port(left) == effective_port(right)
    )


def effective_port(parsed: parse.SplitResult) -> int | None:
    if parsed.port is not None:
        return parsed.port
    if parsed.scheme == "https":
        return 443
    if parsed.scheme == "http":
        return 80
    return None


def public_hostname(hostname: str) -> bool:
    host = hostname.strip().lower().rstrip(".")
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        if host in {"localhost"} or "." not in host or host.endswith(".local"):
            return False
        ips = resolve_hostname_ips(host)
        return bool(ips) and all(public_ip_address(ip) for ip in ips)
    return public_ip_address(ip)


def resolve_hostname_ips(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    host = hostname.strip().lower().strip("[]").rstrip(".")
    if not host:
        return []
    try:
        results = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return []
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for result in results:
        sockaddr = result[4]
        if not sockaddr:
            continue
        raw_ip = str(sockaddr[0])
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        key = str(ip)
        if key not in seen:
            addresses.append(ip)
            seen.add(key)
    return addresses


def public_ip_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def redact_sensitive_text(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    patterns = [
        (r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]"),
        (r"\bsk-[A-Za-z0-9][A-Za-z0-9._-]{8,}\b", "sk-[redacted]"),
        (r"(?i)(api[_-]?key|access[_-]?token|token|signature|password|secret)(\s*[:=]\s*)([^\s,;]+)", r"\1\2[redacted]"),
        (r"(?i)(apiKey|accessToken|refreshToken|clientSecret|dashscopeToken|authorizationHeader|signatureValue|passwordValue|secretValue)(\s*[:=]\s*)([^\s,;]+)", r"\1\2[redacted]"),
        (r"(?i)(authorization)(\s*[:=]\s*)([^\n\r]+)", r"\1\2[redacted]"),
        (r"://[^/\s:@]+:[^/\s:@]+@", "://[redacted]@"),
        (r"(?i)/latest/meta-data/[^\s\"')<>]*", "/latest/meta-data/[redacted]"),
        (r"(?i)/metadata/instance[^\s\"')<>]*", "/metadata/instance[redacted]"),
    ]
    redacted = text
    for pattern, replacement in patterns:
        redacted = re.sub(pattern, replacement, redacted)
    return redacted


def normalize_payload_key(key: object) -> str:
    text = str(key or "").strip()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    return text.strip("_").lower()


def is_sensitive_payload_key(key: object) -> bool:
    normalized = normalize_payload_key(key)
    if not normalized:
        return False
    exact = {
        "authorization",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "token",
        "signature",
        "secret",
        "password",
    }
    if normalized in exact:
        return True
    segments = [part for part in normalized.split("_") if part]
    if any(part in {"authorization", "apikey", "token", "signature", "secret", "password"} for part in segments):
        return True
    joined_pairs = {"_".join(pair) for pair in zip(segments, segments[1:], strict=False)}
    return bool({"api_key", "access_token", "refresh_token"} & joined_pairs)


def sanitize_event_payload(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if is_sensitive_payload_key(key_text):
                clean[key_text] = "[redacted]"
            else:
                clean[key_text] = sanitize_event_payload(item)
        return clean
    if isinstance(value, list):
        return [sanitize_event_payload(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def compact_event_payload(value: Any, *, depth: int = 0) -> Any:
    value = sanitize_event_payload(value)
    if depth > 6:
        return "[truncated]"
    if isinstance(value, dict):
        return {str(key): compact_event_payload(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        compacted = [compact_event_payload(item, depth=depth + 1) for item in value[:MAX_TINGWU_EVENT_LIST_ITEMS]]
        if len(value) > MAX_TINGWU_EVENT_LIST_ITEMS:
            compacted.append({"truncated_items": len(value) - MAX_TINGWU_EVENT_LIST_ITEMS})
        return compacted
    if isinstance(value, str) and len(value) > MAX_TINGWU_EVENT_TEXT_CHARS:
        return f"{value[:MAX_TINGWU_EVENT_TEXT_CHARS]}...[truncated {len(value) - MAX_TINGWU_EVENT_TEXT_CHARS} chars]"
    return value


def read_limited_response_body(
    response: Any,
    *,
    max_bytes: int = MAX_TINGWU_ARTIFACT_BYTES,
    label: str = "Tingwu response",
) -> str:
    length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
    if length:
        try:
            declared = int(length)
        except ValueError:
            declared = 0
        if declared > max_bytes:
            raise TingwuMeetingError(f"{label} is too large: {declared} bytes exceeds {max_bytes} bytes.")
    body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise TingwuMeetingError(f"{label} is too large: exceeds {max_bytes} bytes.")
    return body.decode("utf-8", errors="replace")


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(8)}.tmp")
    encoded = content.encode(encoding)
    try:
        with temp_path.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.replace(path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def fsync_parent_dir(path: Path) -> None:
    try:
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@dataclass
class TingwuTranscriptItem:
    timestamp: str
    text: str
    speaker: str = "Unknown"
    final: bool = False


@dataclass
class TingwuMeetingSession:
    meeting_id: str
    title: str
    participants: list[str]
    task_id: str
    status: str
    created_at: str
    data_id: str = ""
    websocket_task_id: str = ""
    started_at: str | None = None
    stopped_at: str | None = None
    transcript: list[TingwuTranscriptItem] = field(default_factory=list)
    partial_text: str = ""
    audio_bytes: int = 0
    audio_seconds: float = 0.0
    sample_rate: int = 16000
    audio_format: str = "pcm"
    websocket_audio_frames: int = 0
    audio_rms: int = 0
    audio_peak: int = 0
    output_dir: str = ""
    transcript_path: str = ""
    audio_path: str = ""
    minutes_path: str = ""
    task_payload: dict[str, Any] = field(default_factory=dict)
    tingwu_http_operations: list[dict[str, Any]] = field(default_factory=list)
    ai_minutes: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "meeting_id": self.meeting_id,
            "title": self.title,
            "participants": self.participants,
            "task_id": self.task_id,
            "status": self.status,
            "created_at": self.created_at,
            "data_id": self.data_id,
            "websocket_task_id": self.websocket_task_id,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "transcript": [item.__dict__ for item in self.transcript],
            "partial_text": self.partial_text,
            "audio_bytes": self.audio_bytes,
            "audio_seconds": self.audio_seconds,
            "sample_rate": self.sample_rate,
            "audio_format": self.audio_format,
            "websocket_audio_frames": self.websocket_audio_frames,
            "audio_rms": self.audio_rms,
            "audio_peak": self.audio_peak,
            "output_dir": self.output_dir,
            "transcript_path": self.transcript_path,
            "audio_path": self.audio_path,
            "minutes_path": self.minutes_path,
            "task_payload": self.task_payload,
            "tingwu_http_operations": self.tingwu_http_operations,
            "ai_minutes": self.ai_minutes,
            "error": self.error,
        }


class ArecordPCMStreamer:
    def __init__(self, *, device: str, sample_rate: int, frame_ms: int = 100):
        self.device = device
        self.sample_rate = sample_rate
        self.frame_ms = max(20, frame_ms)
        self.channels = 1
        self.sample_width = 2
        self._process: subprocess.Popen[bytes] | None = None
        self._stderr: bytes = b""

    @property
    def frame_bytes(self) -> int:
        return int(self.sample_rate * self.frame_ms / 1000) * self.sample_width * self.channels

    def start(self) -> None:
        if shutil.which("arecord") is None:
            raise TingwuMeetingError("arecord not found. Install ALSA utilities on the Pi.")
        command = [
            "arecord",
            "-q",
            "-D",
            self.device,
            "-f",
            "S16_LE",
            "-c",
            str(self.channels),
            "-r",
            str(self.sample_rate),
            "-t",
            "raw",
        ]
        self._process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def frames(self):
        if self._process is None or self._process.stdout is None:
            raise TingwuMeetingError("arecord has not started.")
        while self._process.poll() is None:
            frame = self._process.stdout.read(self.frame_bytes)
            if not frame:
                break
            yield frame
        self._stderr = self._process.stderr.read() if self._process.stderr else b""
        if self._process.returncode not in {None, 0, -15} and self._stderr:
            raise TingwuMeetingError(self._stderr.decode("utf-8", errors="replace")[:1000])

    def stop(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)


class WavPCMStreamer:
    def __init__(self, *, path: str | Path, sample_rate: int, frame_ms: int = 100, speed: float = 1.0):
        self.path = Path(path).expanduser()
        self.sample_rate = sample_rate
        self.frame_ms = max(20, frame_ms)
        self.speed = max(0.1, float(speed or 1.0))
        self.sample_width = 2
        self.channels = 1
        self._stream: wave.Wave_read | None = None

    @property
    def frame_bytes(self) -> int:
        return int(self.sample_rate * self.frame_ms / 1000) * self.sample_width * self.channels

    def start(self) -> None:
        self._stream = wave.open(str(self.path), "rb")
        if (
            self._stream.getframerate() != self.sample_rate
            or self._stream.getnchannels() != self.channels
            or self._stream.getsampwidth() != self.sample_width
        ):
            self._stream.close()
            self._stream = None
            raise TingwuMeetingError(
                "TINGWU_AUDIO_FILE must be mono 16-bit PCM WAV at "
                f"{self.sample_rate} Hz."
            )

    def frames(self):
        if self._stream is None:
            raise TingwuMeetingError("TINGWU_AUDIO_FILE stream has not started.")
        frames_per_chunk = max(1, int(self.sample_rate * self.frame_ms / 1000))
        while True:
            frame = self._stream.readframes(frames_per_chunk)
            if not frame:
                break
            yield frame
            time.sleep((self.frame_ms / 1000) / self.speed)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None


class StreamingWavWriter:
    def __init__(self, path: str | Path, *, sample_rate: int):
        self.path = Path(path)
        self.sample_rate = sample_rate
        self.bytes_written = 0
        self.peak = 0
        self.rms_sum = 0
        self.rms_frames = 0
        self._stream: wave.Wave_write | None = None

    def __enter__(self) -> "StreamingWavWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = wave.open(str(self.path), "wb")
        self._stream.setnchannels(1)
        self._stream.setsampwidth(2)
        self._stream.setframerate(self.sample_rate)
        return self

    def write(self, frame: bytes) -> None:
        if self._stream is None:
            raise TingwuMeetingError("audio writer has not started.")
        self._stream.writeframes(frame)
        self.bytes_written += len(frame)
        try:
            self.peak = max(self.peak, audioop.max(frame, 2) if frame else 0)
            self.rms_sum += audioop.rms(frame, 2) if frame else 0
            self.rms_frames += 1
        except Exception:
            pass

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    @property
    def rms(self) -> int:
        return int(self.rms_sum / self.rms_frames) if self.rms_frames else 0


def probe_arecord_device(device: str) -> dict[str, object]:
    """Read-only ALSA capture probe used before creating a cloud meeting task."""
    if shutil.which("arecord") is None:
        return {
            "status": "backend_missing",
            "configured_device": device,
            "message": "arecord not found. Install ALSA utilities on the Pi.",
            "candidates": [],
        }
    try:
        result = subprocess.run(["arecord", "-l"], capture_output=True, text=True, check=False, timeout=3)
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "configured_device": device,
            "message": "arecord -l timed out.",
            "candidates": [],
        }
    candidates = parse_arecord_capture_devices(result.stdout)
    if result.returncode != 0:
        return {
            "status": "unavailable",
            "configured_device": device,
            "message": (result.stderr or result.stdout or "arecord -l failed").strip()[:1000],
            "candidates": candidates,
        }
    normalized = device.strip()
    if normalized in {"", "auto"}:
        selected = preferred_capture_device(candidates)
        return {
            "status": "available" if selected else "unavailable",
            "configured_device": device,
            "selected_device": selected or "",
            "configured_device_valid": bool(selected),
            "auto_selected": bool(selected),
            "message": "ready" if selected else "No ALSA capture device was listed by arecord -l.",
            "candidates": candidates,
        }
    if normalized in PLACEHOLDER_CAPTURE_DEVICES:
        return {
            "status": "unavailable",
            "configured_device": device,
            "selected_device": normalized,
            "configured_device_valid": False,
            "candidates": candidates,
            "message": (
                "Tingwu live capture selected an unresolved ALSA placeholder; "
                "use a concrete ALSA capture device such as plughw:1,0. "
                "Use OPENCLAW_MIC_DEVICE=auto to resolve one from arecord -l."
            ),
        }
    valid = any(
        normalized in {str(item.get("hw")), str(item.get("plughw")), str(item.get("plughw_by_id"))}
        for item in candidates
    )
    return {
        "status": "available" if valid else "unavailable",
        "configured_device": device,
        "selected_device": normalized if valid else "",
        "configured_device_valid": valid,
        "message": "ready" if valid else "Configured microphone was not listed by arecord -l.",
        "candidates": candidates,
    }


def preflight_arecord_capture(device: str, sample_rate: int, *, duration_seconds: int = 1) -> dict[str, object]:
    """Capture a brief PCM sample before creating a cloud task."""
    if device == "fake-mic":
        duration = max(1, int(duration_seconds))
        frame = synthetic_pcm_frame(sample_rate, seconds=duration, amplitude=600)
        return {
            "status": "available",
            "selected_device": device,
            "sample_rate": sample_rate,
            "duration_seconds": duration,
            "message": "fake microphone capture preflight",
            **pcm_signal_metrics(frame),
        }
    if shutil.which("arecord") is None:
        return {
            "status": "backend_missing",
            "selected_device": device,
            "sample_rate": sample_rate,
            "duration_seconds": max(1, int(duration_seconds)),
            "message": "arecord not found. Install ALSA utilities on the Pi.",
            "audio_bytes": 0,
            "audio_rms": 0,
            "audio_peak": 0,
        }
    duration = max(1, int(duration_seconds))
    command = [
        "arecord",
        "-q",
        "-D",
        device,
        "-f",
        "S16_LE",
        "-c",
        "1",
        "-r",
        str(sample_rate),
        "-t",
        "raw",
        "-d",
        str(duration),
        "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=False, check=False, timeout=duration + 3)
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "selected_device": device,
            "sample_rate": sample_rate,
            "duration_seconds": duration,
            "message": "arecord capture preflight timed out.",
            "audio_bytes": 0,
            "audio_rms": 0,
            "audio_peak": 0,
        }
    metrics = pcm_signal_metrics(result.stdout or b"")
    stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
    stdout_text = (result.stdout or b"")[:200].decode("utf-8", errors="replace").strip()
    message = (stderr or stdout_text or "ready").strip()[:1000]
    return {
        "status": "available" if result.returncode == 0 else "unavailable",
        "selected_device": device,
        "sample_rate": sample_rate,
        "duration_seconds": duration,
        "message": "ready" if result.returncode == 0 else message,
        **metrics,
    }


def preflight_wav_capture(path: str | Path, sample_rate: int) -> dict[str, object]:
    audio_path = Path(path).expanduser().resolve()
    try:
        with wave.open(str(audio_path), "rb") as stream:
            channels = stream.getnchannels()
            width = stream.getsampwidth()
            rate = stream.getframerate()
            frames = stream.getnframes()
            raw = stream.readframes(frames)
    except Exception as exc:
        return {
            "status": "unavailable",
            "selected_device": str(audio_path),
            "sample_rate": sample_rate,
            "duration_seconds": 0,
            "message": f"TINGWU_AUDIO_FILE could not be opened: {exc}",
            "audio_bytes": 0,
            "audio_rms": 0,
            "audio_peak": 0,
        }
    duration = round(len(raw) / max(1, rate * max(1, channels) * max(1, width)), 2)
    metrics = pcm_signal_metrics(raw if channels == 1 and width == 2 else b"")
    status = "available" if channels == 1 and width == 2 and rate == sample_rate and metrics["audio_bytes"] > 0 else "unavailable"
    return {
        "status": status,
        "selected_device": str(audio_path),
        "sample_rate": rate,
        "duration_seconds": duration,
        "message": "ready" if status == "available" else "TINGWU_AUDIO_FILE must be mono 16-bit PCM WAV at the configured sample rate.",
        "audio_bytes": metrics["audio_bytes"],
        "audio_rms": metrics["audio_rms"],
        "audio_peak": metrics["audio_peak"],
    }


def preferred_capture_device(candidates: list[dict[str, object]]) -> str:
    for item in candidates:
        haystack = " ".join(str(item.get(key, "")).lower() for key in ("card_id", "card_name", "device_name"))
        if any(marker in haystack for marker in ("usb", "mic", "microphone", "respeaker", "seeed", "array")):
            return str(item.get("plughw") or item.get("hw") or "")
    if candidates:
        return str(candidates[0].get("plughw") or candidates[0].get("hw") or "")
    return ""


def parse_arecord_capture_devices(value: str) -> list[dict[str, object]]:
    devices: list[dict[str, object]] = []
    for line in value.splitlines():
        clean = line.strip()
        if not clean.startswith("card ") or ", device " not in clean:
            continue
        try:
            left, right = clean.split(", device ", 1)
            card_index = int(left.split()[1].rstrip(":"))
            card_id = left.split(":", 1)[1].split("[", 1)[0].strip()
            card_name = left.split("[", 1)[1].split("]", 1)[0]
            device_index = int(right.split(":", 1)[0])
            device_name = right.split("[", 1)[1].split("]", 1)[0] if "[" in right else right.split(":", 1)[-1].strip()
        except (IndexError, ValueError):
            continue
        devices.append(
            {
                "card_index": card_index,
                "card_id": card_id,
                "card_name": card_name,
                "device_index": device_index,
                "device_name": device_name,
                "hw": f"hw:{card_index},{device_index}",
                "plughw": f"plughw:{card_index},{device_index}",
                "plughw_by_id": f"plughw:CARD={card_id},DEV={device_index}",
            }
        )
    return devices


class TingwuRealtimeCallback(TingWuRealtimeCallback):
    def __init__(self, provider: "TingwuMeetingProvider", meeting_id: str):
        super().__init__()
        self.provider = provider
        self.meeting_id = meeting_id
        self.opened = threading.Event()
        self.can_send_audio = threading.Event()
        self.stopped = threading.Event()
        self.errors: Queue[str] = Queue()

    def on_open(self) -> None:
        self.opened.set()
        self.provider._emit(self.meeting_id, "websocket_open", {})

    def on_started(self, task_id: str) -> None:
        session = self.provider._sessions[self.meeting_id]
        session.websocket_task_id = task_id
        session.task_payload["websocket_task_id"] = task_id
        self.provider._emit(self.meeting_id, "websocket_started", {"websocket_task_id": task_id})
        self.provider._persist_session(session)

    def on_speech_listen(self, result: dict):
        self.can_send_audio.set()
        self._dispatch(result)

    def on_recognize_result(self, result: dict):
        self._dispatch(result)

    def on_ai_result(self, result: dict):
        session = self.provider._sessions[self.meeting_id]
        ai_events = session.task_payload.setdefault("ai_result_events", [])
        if isinstance(ai_events, list):
            ai_events.append(compact_event_payload(result))
            del ai_events[:-MAX_TINGWU_AI_EVENTS]
        self.provider._record_agent_event(self.meeting_id, result)
        self._dispatch(result)

    def on_stopped(self) -> None:
        self.stopped.set()
        self.can_send_audio.clear()
        self.provider._emit(self.meeting_id, "websocket_stopped", {})

    def on_error(self, error_code: str, error_msg: str) -> None:
        message = f"{error_code}: {error_msg}" if error_code else error_msg
        message = redact_sensitive_text(message)
        self.errors.put(message)
        self.provider._emit(self.meeting_id, "websocket_error", {"error": message})

    def on_close(self, close_status_code, close_msg):
        self.provider._emit(
            self.meeting_id,
            "websocket_close",
            {"code": close_status_code, "message": close_msg},
        )

    def _dispatch(self, result: dict) -> None:
        try:
            self.provider._handle_realtime_event(self.meeting_id, result)
        except Exception as exc:
            message = redact_sensitive_text(str(exc))[:1000]
            self.errors.put(message)
            self.provider._emit(self.meeting_id, "websocket_event_error", {"error": message})


class TingwuMeetingProvider:
    """Tongyi Tingwu realtime meeting provider with a local mock fallback.

    The production path creates a Tingwu task, streams Pi microphone PCM over
    WebSocket, collects realtime transcript events, and saves meeting artifacts
    under workspace/meetings/{meeting_id}. If TINGWU_MOCK=1 is set, the same
    lifecycle runs without network or microphone hardware for smoke tests.
    """

    def __init__(self, config: OfficeAgentConfig, workspace: Workspace, audit: AuditLogger):
        self.config = config
        self.workspace = workspace
        self.audit = audit
        self._sessions: dict[str, TingwuMeetingSession] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._event_queues: dict[str, Queue[dict[str, Any]]] = {}
        self._pending_http_operations: dict[str, list[dict[str, Any]]] = {}
        self._http_operation_meeting_id = ""
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._workspace_lock_guard = threading.Lock()
        self._workspace_lock_fd: int | None = None
        self._workspace_lock_meeting_id = ""
        self._workspace_sessions_loaded = False

    def status(self) -> dict[str, object]:
        self._load_workspace_active_sessions(force=True)
        configured = bool(self.config.tingwu_api_key and self.config.tingwu_app_id)
        credential_diagnostics = {
            "api_key_kind": self.config.tingwu_api_key_kind or tingwu_credential_kind(self.config.tingwu_api_key),
            "app_id_kind": self.config.tingwu_app_id_kind or tingwu_credential_kind(self.config.tingwu_app_id, role="app_id"),
        }
        mic_probe = self.microphone_probe()
        mic_status = str(mic_probe.get("status") or "")
        if self.config.tingwu_mock:
            provider_status = "available"
            message = "ready (mock)"
        elif not configured:
            provider_status = "needs_config"
            if "aliyun_access_key_id" in credential_diagnostics.values():
                message = "DASHSCOPE_API_KEY must be a Bailian/DashScope API Key, not an Aliyun RAM AccessKey ID."
            elif "legacy_tingwu_appkey" in credential_diagnostics.values():
                message = "TINGWU_APP_ID must be the Bailian Model Studio app App ID, not a legacy Tingwu OpenAPI AppKey."
            else:
                message = "Set TINGWU_API_KEY/DASHSCOPE_API_KEY and TINGWU_APP_ID/TINGWU_MEETING_APP_ID."
        elif mic_status != "available":
            provider_status = "unavailable"
            message = f"Microphone is not ready: {mic_probe.get('message') or mic_status}"
        else:
            provider_status = "available"
            message = "ready"
        return {
            "provider": "tongyi_tingwu",
            "status": provider_status,
            "configured": configured,
            "mock": self.config.tingwu_mock,
            "api_key_configured": bool(self.config.tingwu_api_key),
            "app_id_configured": bool(self.config.tingwu_app_id),
            "credential_diagnostics": credential_diagnostics,
            "http_url": redact_provider_url(self.config.tingwu_http_url),
            "ws_url": redact_provider_url(self.config.tingwu_ws_url),
            "configured_mic_device": self.config.mic_device,
            "mic_device": self.config.mic_device,
            "selected_mic_device": mic_probe.get("selected_device") or self.config.mic_device,
            "mic_status": mic_status,
            "mic_probe": mic_probe,
            "audio_source": "file" if self.config.tingwu_audio_file.strip() else "microphone",
            "sample_rate": self.config.tingwu_sample_rate,
            "audio_format": self.config.tingwu_audio_format,
            "pcm_gain": self.config.tingwu_pcm_gain,
            "audio_file_speed": self.config.tingwu_audio_file_speed if self.config.tingwu_audio_file.strip() else 1.0,
            "transcription_model": self.config.tingwu_transcription_model,
            "analysis_model": self.config.tingwu_analysis_model,
            "language_hints": self.language_hints(),
            "translation_enabled": self.config.tingwu_translation_enabled,
            "translation_target_lang": self.translation_target_langs(),
            "phrase_id_configured": bool(self.config.tingwu_phrase_id.strip()),
            "hot_words_configured": bool(self.hot_words()),
            "audio_channel_mode": self.config.tingwu_audio_channel_mode,
            "capabilities": self.capabilities_status(),
            "active_meeting_id": self.active_meeting_id(),
            "active_count": len([item for item in self._sessions.values() if item.status in ACTIVE_MEETING_STATUSES]),
            "message": message,
        }

    def microphone_probe(self) -> dict[str, object]:
        if self.config.tingwu_audio_file.strip():
            capture_probe = preflight_wav_capture(self.config.tingwu_audio_file, self.config.tingwu_sample_rate)
            status = "available" if capture_probe.get("status") == "available" else "unavailable"
            return {
                "status": status,
                "configured_device": "TINGWU_AUDIO_FILE",
                "selected_device": str(capture_probe.get("selected_device") or self.config.tingwu_audio_file),
                "configured_device_valid": status == "available",
                "message": "ready (audio file)" if status == "available" else capture_probe.get("message", "audio file unavailable"),
                "audio_source": "file",
                "candidates": [],
                "capture_probe": capture_probe,
            }
        if self.config.tingwu_mock:
            return {
                "status": "mock",
                "configured_device": self.config.mic_device,
                "message": "TINGWU_MOCK=1 skips microphone hardware.",
                "candidates": [],
            }
        if self.config.mic_device == "fake-mic":
            return {
                "status": "available",
                "configured_device": self.config.mic_device,
                "selected_device": self.config.mic_device,
                "configured_device_valid": True,
                "message": "fake microphone for protocol tests",
                "candidates": [],
            }
        return probe_arecord_device(self.config.mic_device)

    def validate_microphone_ready(self) -> dict[str, object]:
        probe = self.microphone_probe()
        if str(probe.get("status")) != "available":
            raise TingwuMeetingError(
                f"Microphone is not ready: {probe.get('message') or probe.get('status')}",
                details={"mic_probe": probe},
            )
        selected = str(probe.get("selected_device") or self.config.mic_device).strip()
        if selected.lower() in PLACEHOLDER_CAPTURE_DEVICES:
            raise TingwuMeetingError(
                "Microphone is not ready: selected device is an unresolved ALSA placeholder. "
                "Use OPENCLAW_MIC_DEVICE=auto or a concrete device such as plughw:1,0.",
                details={"mic_probe": probe},
            )
        if self.config.tingwu_audio_file.strip():
            capture_probe = preflight_wav_capture(self.config.tingwu_audio_file, self.config.tingwu_sample_rate)
            probe["audio_source"] = "file"
        else:
            capture_probe = preflight_arecord_capture(
                selected,
                self.config.tingwu_sample_rate,
                duration_seconds=self.config.tingwu_preflight_capture_seconds,
            )
        probe["capture_probe"] = capture_probe
        if str(capture_probe.get("status")) != "available":
            raise TingwuMeetingError(
                f"Microphone capture preflight failed: {capture_probe.get('message') or capture_probe.get('status')}",
                details={"mic_probe": probe, "capture_probe": capture_probe},
            )
        audio_bytes = int(capture_probe.get("audio_bytes") or 0)
        audio_rms = int(capture_probe.get("audio_rms") or 0)
        audio_peak = int(capture_probe.get("audio_peak") or 0)
        if audio_bytes <= 0 or audio_rms <= 0 or audio_peak <= 0:
            raise TingwuMeetingError(
                "Microphone capture preflight failed: selected device produced no audio signal. "
                "Speak near the microphone or choose the correct OPENCLAW_MIC_DEVICE.",
                details={"mic_probe": probe, "capture_probe": capture_probe},
            )
        return probe

    def selected_mic_device(self, session: TingwuMeetingSession) -> str:
        probe = session.task_payload.get("mic_probe") if isinstance(session.task_payload.get("mic_probe"), dict) else {}
        selected = str(probe.get("selected_device") or "").strip()
        return selected or self.config.mic_device

    def language_hints(self) -> list[str]:
        return [item.strip() for item in self.config.tingwu_language_hints.split(",") if item.strip()]

    def translation_target_langs(self) -> list[str]:
        return [item.strip() for item in self.config.tingwu_translation_target_lang.split(",") if item.strip()]

    def hot_words(self) -> list[str]:
        return [item.strip() for item in re.split(r"[,，\n]", self.config.tingwu_hot_words) if item.strip()]

    def capabilities_status(self) -> dict[str, bool]:
        custom_prompt_enabled = bool(self.config.tingwu_custom_prompt_enabled and self.config.tingwu_custom_prompt.strip())
        return {
            "realtime_transcription": True,
            "speaker_diarization": True,
            "translation": bool(self.config.tingwu_translation_enabled and self.translation_target_langs()),
            "phrase_hot_words": bool(self.config.tingwu_phrase_id.strip()),
            "local_hot_words_note": bool(self.hot_words()),
            "key_information": self.config.tingwu_key_information_enabled,
            "actions": self.config.tingwu_actions_enabled,
            "full_summary": self.config.tingwu_full_summary_enabled,
            "conversational_summary": self.config.tingwu_conversational_enabled,
            "questions_answering": self.config.tingwu_questions_answering_enabled,
            "mind_map": self.config.tingwu_mind_map_enabled,
            "ppt_extraction": self.config.tingwu_ppt_extraction_enabled,
            "auto_chapters": self.config.tingwu_auto_chapters_enabled,
            "text_polish": self.config.tingwu_text_polish_enabled,
            "custom_prompt": custom_prompt_enabled,
            "meeting_agent_events": True,
        }

    def task_analysis_parameters(self) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            "model": self.config.tingwu_analysis_model,
            "keyInformationEnabled": self.config.tingwu_key_information_enabled,
            "actionsEnabled": self.config.tingwu_actions_enabled,
            "fullSummaryEnabled": self.config.tingwu_full_summary_enabled,
            "fullSummaryFormat": "markdown",
            "conversationalEnabled": self.config.tingwu_conversational_enabled,
            "questionsAnsweringEnabled": self.config.tingwu_questions_answering_enabled,
            "mindMapEnabled": self.config.tingwu_mind_map_enabled,
            "pptExtractionEnabled": self.config.tingwu_ppt_extraction_enabled,
            "autoChaptersEnabled": self.config.tingwu_auto_chapters_enabled,
            "textPolishEnabled": self.config.tingwu_text_polish_enabled,
            "customPromptEnabled": bool(self.config.tingwu_custom_prompt_enabled and self.config.tingwu_custom_prompt.strip()),
        }
        if self.config.tingwu_mind_map_format.strip():
            parameters["mindMapFormat"] = self.config.tingwu_mind_map_format.strip()
        if self.config.tingwu_auto_chapter_granularity.strip():
            parameters["autoChapterGranularity"] = self.config.tingwu_auto_chapter_granularity.strip()
        if self.config.tingwu_auto_chapter_title_length_level.strip():
            parameters["autoChapterTitleLengthLevel"] = self.config.tingwu_auto_chapter_title_length_level.strip()
        if parameters["customPromptEnabled"]:
            parameters["customPromptModel"] = self.config.tingwu_custom_prompt_model.strip() or "tingwu-turbo"
            parameters["customPromptTransType"] = self.config.tingwu_custom_prompt_trans_type.strip() or "chat"
            parameters["customPromptContent"] = self.config.tingwu_custom_prompt.strip()
        return parameters

    def active_meeting_id(self) -> str | None:
        with self._lock:
            for meeting_id, session in self._sessions.items():
                if session.status in ACTIVE_MEETING_STATUSES:
                    return meeting_id
        return None

    def start_realtime_meeting(self, *, title: str, participants: list[str], max_seconds: int = 7200) -> dict[str, object]:
        with self._start_lock:
            self._load_workspace_active_sessions(force=True)
            if self.active_meeting_id():
                raise TingwuMeetingError("Another realtime meeting is already running.")
            if self._workspace_lock_fd is not None:
                raise TingwuMeetingError("Another realtime meeting is already running.")
            if not self._acquire_workspace_meeting_lock(title=title):
                self._load_workspace_active_sessions(force=True)
                active = self.active_meeting_id()
                detail = f": {active}" if active else ""
                raise TingwuMeetingError(f"Another realtime meeting is already running{detail}.")
            try:
                return self._start_realtime_meeting_locked(title=title, participants=participants, max_seconds=max_seconds)
            except Exception:
                self._release_workspace_meeting_lock()
                raise

    def _start_realtime_meeting_locked(self, *, title: str, participants: list[str], max_seconds: int = 7200) -> dict[str, object]:
        self._load_workspace_active_sessions(force=True)
        if not title.strip():
            raise TingwuMeetingError("Meeting title is required.")
        if not self.config.tingwu_api_key and not self.config.tingwu_mock:
            raise TingwuMeetingError("TINGWU_API_KEY or DASHSCOPE_API_KEY is not configured.")
        if not self.config.tingwu_app_id and not self.config.tingwu_mock:
            raise TingwuMeetingError("TINGWU_APP_ID or TINGWU_MEETING_APP_ID is not configured.")
        if self.active_meeting_id():
            raise TingwuMeetingError("Another realtime meeting is already running.")
        mic_probe = self.validate_microphone_ready() if not self.config.tingwu_mock else self.microphone_probe()

        stored_title = redact_sensitive_text(title)[:240] or "Tingwu Meeting"
        stored_participants = [redact_sensitive_text(item)[:120] for item in participants]
        meeting_id = f"tingwu_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(8)}"
        output_dir = (self.workspace.root / "meetings" / meeting_id).resolve()
        if not output_dir.is_relative_to(self.workspace.root.resolve()):
            raise TingwuMeetingError("Invalid meeting output directory.")
        output_dir.mkdir(parents=True, exist_ok=True)

        previous_http_meeting_id = self._http_operation_meeting_id
        self._http_operation_meeting_id = meeting_id
        try:
            task = sanitize_event_payload(self.create_task(title=title, participants=participants))
        finally:
            self._http_operation_meeting_id = previous_http_meeting_id
        task["mic_probe"] = mic_probe
        task["pcm_gain"] = self.config.tingwu_pcm_gain
        if self.config.tingwu_audio_file.strip():
            task["audio_file_speed"] = self.config.tingwu_audio_file_speed
        now = utc_now()
        data_id = self._extract_data_id(task)
        session = TingwuMeetingSession(
            meeting_id=meeting_id,
            title=stored_title,
            participants=stored_participants,
            task_id=data_id or meeting_id,
            status="running",
            created_at=now,
            data_id=data_id,
            sample_rate=self.config.tingwu_sample_rate,
            audio_format=self.config.tingwu_audio_format,
            output_dir=str(output_dir),
            transcript_path=str(output_dir / "transcript.md"),
            audio_path=str(output_dir / "audio.wav"),
            task_payload=task,
            tingwu_http_operations=self._drain_http_operations(meeting_id),
        )
        self._assign_workspace_meeting_lock(meeting_id)
        with self._lock:
            self._sessions[meeting_id] = session
            self._stop_events[meeting_id] = threading.Event()
            self._event_queues[meeting_id] = Queue()

        thread = threading.Thread(
            target=self._run_session,
            args=(meeting_id, max_seconds),
            name=f"tingwu-meeting-{meeting_id}",
            daemon=True,
        )
        with self._lock:
            self._threads[meeting_id] = thread
        thread.start()
        self.audit.record(
            "tingwu.meeting_start",
            target=meeting_id,
            details={
                "title": stored_title,
                "task_id": session.task_id,
                "participants": stored_participants,
                "mock": self.config.tingwu_mock,
                "mic_probe": mic_probe,
                "pcm_gain": self.config.tingwu_pcm_gain,
                "audio_file_speed": self.config.tingwu_audio_file_speed if self.config.tingwu_audio_file.strip() else 1.0,
            },
        )
        self._persist_session(session)
        return self.session_status(meeting_id)

    def stop_realtime_meeting(self, meeting_id: str | None = None, *, wait_seconds: float = 8) -> dict[str, object]:
        meeting_id = meeting_id or self.active_meeting_id()
        if not meeting_id:
            raise TingwuMeetingError("No realtime meeting is running.")
        session = self._sessions.get(meeting_id) or self._load_session(meeting_id)
        if session is None:
            raise TingwuMeetingError(f"Meeting not found: {meeting_id}")
        if session.status in {"stopped", "completed", "failed", "finalizing"}:
            return self.session_status(meeting_id)
        event = self._stop_events.get(meeting_id)
        if event is None:
            self._recover_interrupted_session(session)
            if session.status == "stopped":
                self._release_workspace_meeting_lock()
            return self.session_status(meeting_id)
        session.status = "stopping"
        event.set()
        thread = self._threads.get(meeting_id)
        if thread is not None:
            thread.join(timeout=wait_seconds)
        if thread is not None and thread.is_alive():
            if session.status in {"starting", "running", "stopping"}:
                session.status = "stopping"
                self._persist_session(session)
            return self.session_status(meeting_id)
        if session.status in {"running", "stopping", "starting"}:
            session.status = "stopped"
            session.stopped_at = session.stopped_at or utc_now()
            self._write_transcript(session)
            self._persist_session(session)
        if session.status in {"stopped", "failed", "completed"}:
            self._release_workspace_meeting_lock()
        return self.session_status(meeting_id)

    def session_status(self, meeting_id: str | None = None) -> dict[str, object]:
        if meeting_id is None:
            self._load_workspace_active_sessions(force=True)
        meeting_id = meeting_id or self.active_meeting_id()
        if not meeting_id:
            return {"status": "idle", "provider": "tongyi_tingwu", "active_meeting_id": None}
        session = self._sessions.get(meeting_id)
        if session is None:
            loaded = self._load_session(meeting_id)
            if loaded is None:
                raise TingwuMeetingError(f"Meeting not found: {meeting_id}")
            session = loaded
        payload = session.as_dict()
        if payload.get("status") == "stopped" and self._thread_alive(meeting_id):
            payload["status"] = "stopping"
        return {
            **payload,
            "provider": "tongyi_tingwu",
            "realtime_transcript": self.transcript_text(session),
            "final_count": len([item for item in session.transcript if item.final]),
        }

    def drain_events(self, meeting_id: str, limit: int = 100) -> list[dict[str, object]]:
        queue = self._event_queues.get(meeting_id)
        if queue is None:
            return []
        events: list[dict[str, object]] = []
        for _ in range(max(1, limit)):
            try:
                events.append(queue.get_nowait())
            except Empty:
                break
        return events

    def create_task(self, *, title: str, participants: list[str]) -> dict[str, Any]:
        if self.config.tingwu_mock:
            mock_task_id = f"mock_{secrets.token_hex(8)}"
            result = {
                "status": "mock",
                "task_id": mock_task_id,
                "data_id": mock_task_id,
                "websocket_url": "mock://tingwu/realtime",
                "title": title,
                "participants": participants,
            }
            self._record_tingwu_http_operation(
                self._http_operation_meeting_id,
                action="CreateTask",
                url=self.config.tingwu_http_url,
                request_payload={
                    "model": "tingwu-meeting",
                    "input": {
                        "task": "createTask",
                        "type": "realtime",
                        "format": self.config.tingwu_audio_format,
                        "sampleRate": self.config.tingwu_sample_rate,
                    },
                },
                response=result,
            )
            return result
        transcription_parameters = remove_none(
            {
                "model": self.config.tingwu_transcription_model,
                "languageHints": self.language_hints() or None,
                "diarizationEnabled": True,
                "diarizationSpeakerCount": 0,
                "translationEnabled": bool(self.config.tingwu_translation_enabled and self.translation_target_langs()),
                "translationTargetLang": self.translation_target_langs() or None,
                "phraseId": self.config.tingwu_phrase_id.strip() or None,
            }
        )
        payload = {
            "model": "tingwu-meeting",
            "input": {
                "task": "createTask",
                "appId": self.config.tingwu_app_id,
                "type": "realtime",
                "format": self.config.tingwu_audio_format,
                "sampleRate": self.config.tingwu_sample_rate,
            },
            "parameters": {
                "transcription": transcription_parameters,
                "audio": {"audioChannelMode": self.config.tingwu_audio_channel_mode.strip()},
                "analysis": self.task_analysis_parameters(),
            },
        }
        result = self._post_json(self.config.tingwu_http_url, payload, action="CreateTask")
        data_id = self._extract_data_id(result)
        if not data_id:
            safe_result = json.dumps(sanitize_event_payload(result), ensure_ascii=False)
            raise TingwuMeetingError(f"CreateTask did not return dataId: {safe_result[:1000]}")
        return result

    def get_task(self, task_id: str) -> dict[str, Any]:
        if self.config.tingwu_mock:
            result = {
                "status": "completed",
                "task_id": task_id,
                "output": {
                    "dataId": task_id,
                    "summarizationPathData": {
                        "Summarization": {
                            "ParagraphSummary": "这是通义听悟 mock 会议纪要。",
                        },
                    },
                    "meetingAssistancePathData": {
                        "MeetingAssistance": {
                            "KeySentences": [{"sentence": "确认 LeLamp 会议模块接入通义听悟。"}],
                            "Actions": [{"task": "继续验证实时转写、停止会议和会后纪要保存。"}],
                        },
                    },
                },
            }
            self._record_tingwu_http_operation(
                self._http_operation_meeting_id,
                action="GetTask",
                url=self.config.tingwu_http_url,
                request_payload={"model": "tingwu-meeting", "input": {"task": "getTask", "dataId": task_id}},
                response=result,
            )
            return result
        payload = {
            "model": "tingwu-meeting",
            "input": {"task": "getTask", "dataId": task_id},
        }
        return self._post_json(self.config.tingwu_http_url, payload, action="GetTask")

    def create_minutes_task(self, task_id: str) -> dict[str, Any]:
        if self.config.tingwu_mock:
            mock_minutes_id = f"mock_minutes_{secrets.token_hex(8)}"
            result = {
                "status": "completed",
                "task_id": mock_minutes_id,
                "data_id": mock_minutes_id,
                "source_data_id": task_id,
            }
            self._record_tingwu_http_operation(
                self._http_operation_meeting_id,
                action="CreateRealtimeMinutesTask",
                url=self.config.tingwu_http_url,
                request_payload={"model": "tingwu-meeting", "input": {"task": "createTask", "type": "realtime", "dataId": task_id}},
                response=result,
            )
            return result
        payload = {
            "model": "tingwu-meeting",
            "input": {
                "task": "createTask",
                "appId": self.config.tingwu_app_id,
                "type": "realtime",
                "dataId": task_id,
            },
            "parameters": {
                "analysis": self.task_analysis_parameters(),
            },
        }
        return self._post_json(self.config.tingwu_http_url, payload, action="CreateRealtimeMinutesTask")

    def finalize_meeting(self, meeting_id: str, *, retry_failed_minutes: bool = False) -> dict[str, object]:
        session = self._sessions.get(meeting_id)
        if session is None:
            session = self._load_session(meeting_id)
            if session is None:
                raise TingwuMeetingError(f"Meeting not found: {meeting_id}")
        if session.status in {"starting", "running", "stopping"}:
            if self._thread_alive(session.meeting_id):
                raise TingwuMeetingError("Realtime meeting is still running. Stop capture before fetching Tingwu AI minutes.")
            self._recover_interrupted_session(session)
            if session.status in {"starting", "running", "stopping"}:
                raise TingwuMeetingError("Realtime meeting is still running or owned by another process.")
        should_fetch_minutes = session.status not in {"completed", "failed"} or (
            retry_failed_minutes
            and session.status == "failed"
            and (not session.ai_minutes or not self._minutes_completed(session.ai_minutes))
        )
        if should_fetch_minutes:
            if not self._acquire_workspace_meeting_lock(title=session.title, meeting_id=session.meeting_id):
                raise TingwuMeetingError("Another realtime meeting is already running.")
            try:
                session.status = "finalizing"
                self._persist_session(session)
                previous_http_meeting_id = self._http_operation_meeting_id
                self._http_operation_meeting_id = session.meeting_id
                try:
                    session.ai_minutes = sanitize_event_payload(self.fetch_ai_minutes(session.task_id))
                finally:
                    self._http_operation_meeting_id = previous_http_meeting_id
                session.tingwu_http_operations.extend(self._drain_http_operations(session.meeting_id))
                del session.tingwu_http_operations[:-MAX_TINGWU_HTTP_OPERATIONS]
                session.minutes_path = str(self._write_minutes(session))
                if not self._minutes_completed(session.ai_minutes):
                    safe_minutes = json.dumps(sanitize_event_payload(session.ai_minutes), ensure_ascii=False)
                    raise TingwuMeetingError(f"Tingwu AI minutes did not complete: {safe_minutes[:1000]}")
                session.status = "completed"
                session.error = ""
                session.stopped_at = session.stopped_at or utc_now()
            except Exception as exc:
                session.status = "failed"
                session.error = redact_sensitive_text(str(exc))
                session.tingwu_http_operations.extend(self._drain_http_operations(session.meeting_id))
                del session.tingwu_http_operations[:-MAX_TINGWU_HTTP_OPERATIONS]
                if not session.minutes_path and session.ai_minutes:
                    session.minutes_path = str(self._write_minutes(session))
        self._persist_session(session)
        self.audit.record(
            "tingwu.meeting_finalize",
            status="ok" if session.status == "completed" else "error",
            target=meeting_id,
            details={"task_id": session.task_id, "status": session.status, "minutes_path": session.minutes_path, "error": session.error},
        )
        if session.status in {"completed", "failed"}:
            self._release_workspace_meeting_lock()
        return self.session_status(meeting_id)

    def _minutes_completed(self, payload: dict[str, Any]) -> bool:
        status = str(payload.get("status") or "").lower()
        if status in {"completed", "succeeded", "success", "finish", "finished"}:
            return True
        if status in {"timeout", "failed", "error", "canceled", "cancelled"}:
            return False
        return self._task_status(payload) == "completed"

    def fetch_ai_minutes(self, task_id: str, *, timeout_seconds: int = 60, interval_seconds: float = 2.0) -> dict[str, Any]:
        if self.config.tingwu_mock:
            create_result = self.create_minutes_task(task_id)
            minutes_task_id = self._extract_data_id(create_result) or task_id
            result = self.get_task(minutes_task_id)
            return {
                **self._hydrate_minutes_payload(result),
                "status": "completed",
                "source_data_id": task_id,
                "minutes_task_id": minutes_task_id,
                "create_task": create_result,
            }
        create_result = self.create_minutes_task(task_id)
        minutes_task_id = self._extract_data_id(create_result) or task_id
        deadline = time.monotonic() + max(1, timeout_seconds)
        last: dict[str, Any] = create_result
        while time.monotonic() < deadline:
            last = self.get_task(minutes_task_id)
            status = self._task_status(last)
            if status in {"completed", "succeeded", "success", "finish", "finished"}:
                return {
                    **self._hydrate_minutes_payload(last),
                    "source_data_id": task_id,
                    "minutes_task_id": minutes_task_id,
                    "create_task": create_result,
                }
            if status in {"failed", "error", "canceled", "cancelled"}:
                raise TingwuMeetingError(json.dumps(sanitize_event_payload(last), ensure_ascii=False)[:1000])
            time.sleep(interval_seconds)
        return {
            "status": "timeout",
            "source_data_id": task_id,
            "minutes_task_id": minutes_task_id,
            "create_task": create_result,
            "last": self._hydrate_minutes_payload(last),
        }

    def transcript_text(self, session: TingwuMeetingSession) -> str:
        lines = []
        for item in session.transcript:
            text = item.text.strip()
            if text:
                lines.append(f"{item.speaker}: {text}")
        if session.partial_text.strip():
            lines.append(f"Unknown: {session.partial_text.strip()}")
        return "\n".join(lines)

    def _run_session(self, meeting_id: str, max_seconds: int) -> None:
        session = self._sessions[meeting_id]
        self._assign_workspace_meeting_lock(meeting_id)
        session.status = "running"
        session.started_at = utc_now()
        self._emit(meeting_id, "meeting_started", {"task_id": session.task_id})
        self._persist_session(session)
        try:
            if self.config.tingwu_mock:
                self._run_mock_session(meeting_id, max_seconds)
            else:
                self._run_realtime_stream(meeting_id, max_seconds)
        except Exception as exc:
            session.status = "failed"
            session.error = redact_sensitive_text(str(exc))
            self._emit(meeting_id, "meeting_error", {"error": session.error})
            self.audit.record("tingwu.meeting_stream", status="error", target=meeting_id, details={"error": session.error[:1000]})
        finally:
            session.stopped_at = session.stopped_at or utc_now()
            final_status = session.status
            if final_status in {"starting", "running", "stopping"}:
                final_status = "stopped"
            self._write_transcript(session)
            if final_status in {"failed", "completed", "stopped"}:
                self._release_workspace_meeting_lock()
            session.status = final_status
            self._persist_session(session)
            self._emit(meeting_id, "meeting_stopped", {"status": session.status, "transcript_path": session.transcript_path})

    def _run_mock_session(self, meeting_id: str, max_seconds: int) -> None:
        session = self._sessions[meeting_id]
        stop_event = self._stop_events[meeting_id]
        session.websocket_task_id = session.websocket_task_id or f"mock_ws_{secrets.token_hex(4)}"
        self._emit(meeting_id, "websocket_open", {})
        self._emit(meeting_id, "websocket_started", {"websocket_task_id": session.websocket_task_id})
        samples = [
            "决定: 使用通义听悟作为第一版会议引擎。",
            "待办: 验证树莓派麦克风采集和实时转写。",
            "待办: 会后生成纪要、行动项和投影确认卡。",
        ]
        deadline = time.monotonic() + min(max(1, max_seconds), 3)
        index = 0
        session.sample_rate = int(session.sample_rate or self.config.tingwu_sample_rate)
        session.audio_format = session.audio_format or self.config.tingwu_audio_format
        frame = synthetic_pcm_frame(session.sample_rate)
        with StreamingWavWriter(session.audio_path, sample_rate=session.sample_rate) as audio_writer:
            while time.monotonic() < deadline and (index < len(samples) or not stop_event.is_set()):
                text = samples[index % len(samples)]
                self._append_transcript(meeting_id, text, speaker="Mock", final=True)
                audio_writer.write(frame)
                session.websocket_audio_frames += 1
                session.audio_bytes += len(frame)
                session.audio_seconds = round(session.audio_bytes / max(1, session.sample_rate * 2), 2)
                index += 1
                time.sleep(0.35)
            self._record_audio_saved(session, bytes_written=audio_writer.bytes_written, rms=audio_writer.rms, peak=audio_writer.peak)

    def _run_realtime_stream(self, meeting_id: str, max_seconds: int) -> None:
        session = self._sessions[meeting_id]
        stop_event = self._stop_events[meeting_id]
        data_id = session.data_id or self._extract_data_id(session.task_payload)
        if not data_id:
            raise TingwuMeetingError("Missing Tingwu dataId from CreateTask.")
        session.data_id = data_id
        session.task_id = data_id
        session.sample_rate = int(session.sample_rate or self.config.tingwu_sample_rate)
        session.audio_format = session.audio_format or self.config.tingwu_audio_format
        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                self._run_realtime_stream_once(meeting_id, max_seconds, attempt=attempt)
                return
            except Exception as exc:
                retryable = (
                    attempt < attempts
                    and not stop_event.is_set()
                    and session.websocket_audio_frames <= 0
                    and session.audio_bytes <= 0
                    and self._is_retryable_websocket_start_error(exc)
                )
                self._emit(
                    meeting_id,
                    "websocket_stream_attempt_failed",
                    {"attempt": attempt, "retryable": retryable, "error": redact_sensitive_text(str(exc))[:1000]},
                )
                if not retryable:
                    raise
                time.sleep(1.0)

    def _run_realtime_stream_once(self, meeting_id: str, max_seconds: int, *, attempt: int) -> None:
        session = self._sessions[meeting_id]
        stop_event = self._stop_events[meeting_id]
        data_id = session.data_id or session.task_id or self._extract_data_id(session.task_payload)
        if not data_id:
            raise TingwuMeetingError("Missing Tingwu dataId from CreateTask.")
        if self.config.tingwu_audio_file.strip():
            streamer = WavPCMStreamer(
                path=self.config.tingwu_audio_file,
                sample_rate=session.sample_rate,
                frame_ms=100,
                speed=self.config.tingwu_audio_file_speed,
            )
        else:
            streamer = ArecordPCMStreamer(
                device=self.selected_mic_device(session),
                sample_rate=session.sample_rate,
                frame_ms=100,
            )
        callback = TingwuRealtimeCallback(self, meeting_id)
        client = TingWuRealtime(
            model="tingwu-meeting-realtime",
            audio_format=session.audio_format,
            sample_rate=session.sample_rate,
            app_id=self.config.tingwu_app_id,
            base_address=self.config.tingwu_ws_url,
            api_key=self.config.tingwu_api_key,
            callback=callback,
            data_id=data_id,
        )
        client.request.task_id = secrets.token_hex(8)
        try:
            self._emit(meeting_id, "websocket_stream_attempt", {"attempt": attempt})
            self._start_tingwu_client(client, callback)
            session.websocket_task_id = getattr(client.request, "task_id", "") or session.websocket_task_id
            self._persist_session(session)
            if not callback.can_send_audio.wait(timeout=15):
                self._raise_callback_error(callback, default="Timed out waiting for Tingwu speech-listen event.")
            streamer.start()
            deadline = time.monotonic() + max(1, max_seconds)
            with StreamingWavWriter(session.audio_path, sample_rate=session.sample_rate) as audio_writer:
                for frame in streamer.frames():
                    if stop_event.is_set() or time.monotonic() >= deadline:
                        break
                    self._raise_callback_error(callback)
                    if not callback.can_send_audio.is_set():
                        time.sleep(0.1)
                        continue
                    frame = amplify_pcm16(frame, self.config.tingwu_pcm_gain)
                    audio_writer.write(frame)
                    session.websocket_audio_frames += 1
                    session.audio_bytes += len(frame)
                    session.audio_seconds = round(session.audio_bytes / max(1, session.sample_rate * 2), 2)
                    client.send_audio_frame(frame)
                self._record_audio_saved(session, bytes_written=audio_writer.bytes_written, rms=audio_writer.rms, peak=audio_writer.peak)
                if audio_writer.bytes_written <= 0:
                    raise TingwuMeetingError(
                        "No microphone audio frames were captured. Check ALSA device, microphone permissions, and input level."
                    )
        finally:
            streamer.stop()
            try:
                self._stop_tingwu_client(client, session)
            except Exception as exc:
                self._emit(meeting_id, "finish_task_error", {"error": redact_sensitive_text(str(exc))[:1000]})
            callback.stopped.wait(timeout=8)
            client.close()

    def _start_tingwu_client(self, client: TingWuRealtime, callback: TingwuRealtimeCallback) -> None:
        connect = getattr(client, "_connect", None)
        send_start = getattr(client, "_send_start_request", None)
        api_key = getattr(client, "api_key", None)
        if callable(connect) and callable(send_start) and api_key:
            connect(api_key)
            if not callback.opened.wait(timeout=20):
                self._raise_callback_error(callback, default="Timed out waiting for Tingwu websocket open event.")
            send_start()
            return
        client.start()

    def _is_retryable_websocket_start_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "socket is already closed",
                "websocket is not connected",
                "timed out waiting for tingwu speech-listen",
                "connection is already closed",
            )
        )

    def _handle_realtime_event(self, meeting_id: str, event: dict[str, Any]) -> None:
        output = event.get("payload", {}).get("output", {}) if isinstance(event.get("payload"), dict) else {}
        event_type = str(output.get("action") or event.get("type") or event.get("event") or "")
        if event_type == "task-failed":
            message = f"{output.get('errorCode') or ''}: {output.get('errorMessage') or ''}".strip(": ")
            raise TingwuMeetingError(redact_sensitive_text(message or json.dumps(event, ensure_ascii=False))[:1000])
        text = extract_transcript_text(event)
        speaker = extract_speaker(event)
        self._record_realtime_raw_event(meeting_id, event_type, event, text=text, speaker=speaker)
        if text:
            self._append_transcript(
                meeting_id,
                text,
                speaker=speaker,
                final=is_final_transcript(event),
            )
        self._emit(meeting_id, "tingwu_event", {"type": event_type, "text": text, "speaker": speaker, "final": is_final_transcript(event)})

    def _stop_tingwu_client(self, client: TingWuRealtime, session: TingwuMeetingSession) -> None:
        try:
            client.stop()
        except Exception as exc:
            self._emit(session.meeting_id, "sdk_stop_error", {"error": redact_sensitive_text(str(exc))[:1000]})
            try:
                self._send_tingwu_finish_task(client, session)
            except Exception as fallback_exc:
                self._emit(session.meeting_id, "finish_task_error", {"error": redact_sensitive_text(str(fallback_exc))[:1000]})
                raise

    def _send_tingwu_finish_task(self, client: TingWuRealtime, session: TingwuMeetingSession) -> None:
        task_id = session.websocket_task_id or getattr(client.request, "task_id", "") or secrets.token_hex(8)
        message = {
            "header": {
                "action": "finish-task",
                "task_id": task_id,
                "request_id": task_id,
                "streaming": "duplex",
            },
            "payload": {
                "model": "tingwu-meeting-realtime",
                "task_group": "aigc",
                "task": "multimodal-generation",
                "function": "generation",
                "input": {
                    "appId": self.config.tingwu_app_id,
                    "dataId": session.data_id or session.task_id,
                    "directive": "stop",
                },
            },
        }
        client._send_text_frame(json.dumps(message, ensure_ascii=False))  # noqa: SLF001

    def _raise_callback_error(self, callback: TingwuRealtimeCallback, *, default: str = "") -> None:
        try:
            message = callback.errors.get_nowait()
        except Empty:
            if default:
                raise TingwuMeetingError(default)
            return
        raise TingwuMeetingError(message)

    def _append_transcript(self, meeting_id: str, text: str, *, speaker: str = "Unknown", final: bool = False) -> None:
        session = self._sessions[meeting_id]
        if final:
            session.partial_text = ""
            item = TingwuTranscriptItem(timestamp=utc_now(), speaker=speaker or "Unknown", text=text, final=True)
            session.transcript.append(item)
            payload = item.__dict__
        else:
            session.partial_text = text
            payload = {"timestamp": utc_now(), "speaker": speaker, "text": text, "final": False}
        self._emit(meeting_id, "transcript", payload)
        self._write_transcript(session)
        self._persist_session(session)

    def _record_realtime_raw_event(self, meeting_id: str, event_type: str, event: dict[str, Any], *, text: str, speaker: str) -> None:
        session = self._sessions.get(meeting_id)
        if session is None:
            return
        raw_events = session.task_payload.setdefault("raw_realtime_events", [])
        if not isinstance(raw_events, list):
            raw_events = []
            session.task_payload["raw_realtime_events"] = raw_events
        raw_events.append(
            {
                "timestamp": utc_now(),
                "type": event_type,
                "speaker": speaker,
                "text": text,
                "final": is_final_transcript(event),
                "event": compact_event_payload(event),
            }
        )
        del raw_events[:-MAX_TINGWU_RAW_EVENTS]

        agent_event = extract_agent_event(event)
        if agent_event:
            self._append_agent_event(session, agent_event)

    def _record_agent_event(self, meeting_id: str, event: dict[str, Any]) -> None:
        session = self._sessions.get(meeting_id)
        if session is None:
            return
        agent_event = extract_agent_event(event) or {
            "timestamp": utc_now(),
            "type": "agent_result",
            "event": compact_event_payload(event),
        }
        self._append_agent_event(session, agent_event)

    def _append_agent_event(self, session: TingwuMeetingSession, event: dict[str, Any]) -> None:
        agent_events = session.task_payload.setdefault("agent_events", [])
        if not isinstance(agent_events, list):
            agent_events = []
            session.task_payload["agent_events"] = agent_events
        agent_events.append(compact_event_payload(event))
        del agent_events[:-MAX_TINGWU_AGENT_EVENTS]

    def _emit(self, meeting_id: str, event: str, payload: dict[str, object] | None = None) -> None:
        queue = self._event_queues.get(meeting_id)
        clean_payload = compact_event_payload(payload or {})
        item = {"event": event, "timestamp": utc_now(), **clean_payload}
        session = self._sessions.get(meeting_id)
        if session is not None:
            events = session.task_payload.setdefault("events", [])
            if isinstance(events, list):
                events.append(item)
                del events[:-MAX_TINGWU_PROVIDER_EVENTS]
        if queue is not None:
            queue.put(item)

    def _record_audio_saved(self, session: TingwuMeetingSession, *, bytes_written: int, rms: int, peak: int) -> Path:
        path = self._session_artifact_path(session, "audio_path", "audio.wav")
        session.audio_rms = int(rms)
        session.audio_peak = int(peak)
        self.audit.record(
            "tingwu.audio_save",
            target=str(path),
            details={
                "bytes": bytes_written,
                "seconds": session.audio_seconds,
                "sample_rate": session.sample_rate,
                "audio_format": session.audio_format,
                "rms": rms,
                "peak": peak,
            },
        )
        return path

    def _write_transcript(self, session: TingwuMeetingSession) -> Path:
        path = self._session_artifact_path(session, "transcript_path", "transcript.md")
        lines = [f"# {session.title} Transcript", ""]
        for item in session.transcript:
            lines.append(f"{item.speaker}: {item.text}")
        if session.partial_text:
            lines.append(f"Unknown: {session.partial_text}")
        atomic_write_text(path, "\n".join(lines) + "\n")
        return path

    def _write_minutes(self, session: TingwuMeetingSession) -> Path:
        path = self._session_artifact_path(session, "minutes_path", "tingwu_ai_minutes.md")
        minutes = normalize_minutes_payload(session.ai_minutes)
        feature_sections = tingwu_feature_sections(session.ai_minutes)
        lines = [
            f"# {session.title} AI Minutes",
            "",
            "Provider: tongyi_tingwu",
            f"Meeting ID: {session.meeting_id}",
            f"Task ID: {session.task_id}",
            "",
            "## Summary",
            minutes["summary"] or "通义听悟未返回摘要，使用 transcript 进入 OpenClaw 后处理。",
            "",
            "## Decisions",
            *([f"- {item}" for item in minutes["decisions"]] or ["- 暂无明确决策，需要人工补充。"]),
            "",
            "## Action Items",
            *([f"- {item}" for item in minutes["action_items"]] or ["- 暂无明确待办，需要人工补充。"]),
            "",
            *feature_sections,
            "",
            "## Raw Tingwu Result",
            "```json",
            json.dumps(session.ai_minutes, ensure_ascii=False, indent=2)[:20000],
            "```",
        ]
        atomic_write_text(path, "\n".join(lines) + "\n")
        return path

    def _persist_session(self, session: TingwuMeetingSession) -> None:
        output_dir = self._session_output_dir(session)
        self._session_artifact_path(session, "transcript_path", "transcript.md")
        self._session_artifact_path(session, "audio_path", "audio.wav")
        self._session_artifact_path(session, "minutes_path", "tingwu_ai_minutes.md", allow_empty=True)
        path = output_dir / "session.json"
        atomic_write_text(path, json.dumps(session.as_dict(), ensure_ascii=False, indent=2))

    def _session_output_dir(self, session: TingwuMeetingSession) -> Path:
        workspace = self.workspace.root.resolve()
        default = workspace / "meetings" / safe_filename(session.meeting_id, default="meeting")
        value = str(session.output_dir or "").strip()
        candidate = Path(value).expanduser() if value else default
        if not candidate.is_absolute():
            candidate = default
        candidate = candidate.resolve()
        if not candidate.is_relative_to(default.resolve()):
            candidate = default.resolve()
        session.output_dir = str(candidate)
        return candidate

    def _session_artifact_path(
        self,
        session: TingwuMeetingSession,
        attr: str,
        filename: str,
        *,
        allow_empty: bool = False,
    ) -> Path:
        output_dir = self._session_output_dir(session)
        value = str(getattr(session, attr) or "").strip()
        if allow_empty and not value:
            return output_dir / filename
        candidate = Path(value).expanduser() if value else output_dir / filename
        if not candidate.is_absolute():
            candidate = output_dir / candidate
        candidate = candidate.resolve()
        if not candidate.is_relative_to(output_dir):
            candidate = output_dir / filename
        setattr(session, attr, str(candidate))
        return candidate

    def _thread_alive(self, meeting_id: str) -> bool:
        thread = self._threads.get(meeting_id)
        return thread is not None and thread.is_alive()

    def _recover_interrupted_session(self, session: TingwuMeetingSession) -> None:
        if session.status not in RECOVERABLE_ACTIVE_STATUSES or self._thread_alive(session.meeting_id):
            return
        if self._workspace_meeting_lock_held_elsewhere():
            self._emit(session.meeting_id, "meeting_active_elsewhere", {"status": session.status})
            return
        previous_status = session.status
        session.status = "stopped"
        session.stopped_at = session.stopped_at or utc_now()
        note = f"Recovered from persisted {previous_status} state after provider restart; local capture stream is no longer active."
        if note not in session.error:
            session.error = f"{session.error}\n{note}".strip()
        self._write_transcript(session)
        self._persist_session(session)
        self._emit(session.meeting_id, "meeting_recovered", {"previous_status": previous_status, "status": session.status})
        self.audit.record(
            "tingwu.meeting_recovered",
            target=session.meeting_id,
            details={"previous_status": previous_status, "status": session.status, "transcript_path": session.transcript_path},
        )

    def _load_workspace_active_sessions(self, *, force: bool = False) -> None:
        if self._workspace_sessions_loaded and not force:
            return
        self._workspace_sessions_loaded = True
        meetings_dir = self.workspace.root / "meetings"
        if not meetings_dir.is_dir():
            return
        for path in sorted(meetings_dir.glob("*/session.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            meeting_id = str(data.get("meeting_id") or path.parent.name)
            status = str(data.get("status") or "")
            should_refresh = force and meeting_id in self._sessions and not self._thread_alive(meeting_id)
            if status in RECOVERABLE_ACTIVE_STATUSES and (meeting_id not in self._sessions or should_refresh):
                self._load_session(meeting_id)
            elif should_refresh and status not in RECOVERABLE_ACTIVE_STATUSES:
                self._load_session(meeting_id)

    def _load_session(self, meeting_id: str) -> TingwuMeetingSession | None:
        path = self.workspace.root / "meetings" / safe_filename(meeting_id, default="meeting") / "session.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.audit.record(
                "tingwu.session_load",
                status="error",
                target=str(path),
                details={"meeting_id": meeting_id, "error": redact_sensitive_text(str(exc))[:500]},
            )
            return None
        transcript = [
            TingwuTranscriptItem(
                timestamp=str(item.get("timestamp") or ""),
                speaker=str(item.get("speaker") or "Unknown"),
                text=str(item.get("text") or ""),
                final=bool(item.get("final")),
            )
            for item in data.get("transcript", [])
            if isinstance(item, dict)
        ]
        task_payload = data.get("task_payload") if isinstance(data.get("task_payload"), dict) else {}
        ai_minutes = data.get("ai_minutes") if isinstance(data.get("ai_minutes"), dict) else {}
        tingwu_http_operations = data.get("tingwu_http_operations") if isinstance(data.get("tingwu_http_operations"), list) else []
        session = TingwuMeetingSession(
            meeting_id=str(data.get("meeting_id") or meeting_id),
            title=str(data.get("title") or "Meeting"),
            participants=[str(item) for item in data.get("participants", [])],
            task_id=str(data.get("task_id") or ""),
            status=str(data.get("status") or "completed"),
            created_at=str(data.get("created_at") or ""),
            data_id=str(data.get("data_id") or data.get("task_id") or ""),
            websocket_task_id=str(data.get("websocket_task_id") or ""),
            started_at=data.get("started_at"),
            stopped_at=data.get("stopped_at"),
            transcript=transcript,
            partial_text=str(data.get("partial_text") or ""),
            audio_bytes=int(data.get("audio_bytes") or 0),
            audio_seconds=float(data.get("audio_seconds") or 0.0),
            sample_rate=int(data.get("sample_rate") or self.config.tingwu_sample_rate),
            audio_format=str(data.get("audio_format") or self.config.tingwu_audio_format),
            websocket_audio_frames=int(data.get("websocket_audio_frames") or 0),
            audio_rms=int(data.get("audio_rms") or 0),
            audio_peak=int(data.get("audio_peak") or 0),
            output_dir=str(data.get("output_dir") or path.parent),
            transcript_path=str(data.get("transcript_path") or path.parent / "transcript.md"),
            audio_path=str(data.get("audio_path") or path.parent / "audio.wav"),
            minutes_path=str(data.get("minutes_path") or ""),
            task_payload=sanitize_event_payload(task_payload),
            tingwu_http_operations=sanitize_event_payload(tingwu_http_operations)[-MAX_TINGWU_HTTP_OPERATIONS:],
            ai_minutes=sanitize_event_payload(ai_minutes),
            error=redact_sensitive_text(str(data.get("error") or "")),
        )
        self._session_output_dir(session)
        self._session_artifact_path(session, "transcript_path", "transcript.md")
        self._session_artifact_path(session, "audio_path", "audio.wav")
        self._session_artifact_path(session, "minutes_path", "tingwu_ai_minutes.md", allow_empty=True)
        self._sessions[session.meeting_id] = session
        self._event_queues.setdefault(session.meeting_id, Queue())
        self._recover_interrupted_session(session)
        self._persist_session(session)
        return session

    def _workspace_meeting_lock_path(self) -> Path:
        workspace = self.workspace.root.resolve()
        path = (workspace / "meetings" / TINGWU_WORKSPACE_LOCK_NAME).resolve()
        if not path.parent.is_relative_to(workspace):
            raise TingwuMeetingError("Invalid Tingwu workspace lock path.")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _write_workspace_lock_payload(self, fd: int, payload: dict[str, object]) -> None:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        os.fsync(fd)
        fsync_parent_dir(self._workspace_meeting_lock_path())

    def _acquire_workspace_meeting_lock(self, *, title: str = "", meeting_id: str = "") -> bool:
        with self._workspace_lock_guard:
            if self._workspace_lock_fd is not None:
                if meeting_id and self._workspace_lock_meeting_id != meeting_id:
                    return False
                if meeting_id and not self._workspace_lock_meeting_id:
                    self._workspace_lock_meeting_id = meeting_id
                return True
            path = self._workspace_meeting_lock_path()
            fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                return False
            except Exception:
                os.close(fd)
                raise
            payload = {
                "pid": os.getpid(),
                "locked_at": utc_now(),
                "title": redact_sensitive_text(title)[:200],
                "meeting_id": meeting_id,
            }
            self._write_workspace_lock_payload(fd, payload)
            self._workspace_lock_fd = fd
            self._workspace_lock_meeting_id = meeting_id
            return True

    def _assign_workspace_meeting_lock(self, meeting_id: str) -> None:
        if not meeting_id:
            return
        with self._workspace_lock_guard:
            if self._workspace_lock_fd is not None and not self._workspace_lock_meeting_id:
                self._workspace_lock_meeting_id = meeting_id
                try:
                    os.lseek(self._workspace_lock_fd, 0, os.SEEK_SET)
                    raw = os.read(self._workspace_lock_fd, 64 * 1024)
                    payload = json.loads(raw.decode("utf-8") or "{}") if raw else {}
                    if not isinstance(payload, dict):
                        payload = {}
                except Exception:
                    payload = {}
                payload["pid"] = os.getpid()
                payload["meeting_id"] = meeting_id
                payload.setdefault("locked_at", utc_now())
                self._write_workspace_lock_payload(self._workspace_lock_fd, payload)

    def _release_workspace_meeting_lock(self) -> None:
        with self._workspace_lock_guard:
            fd = self._workspace_lock_fd
            if fd is None:
                return
            try:
                self._write_workspace_lock_payload(fd, {"pid": os.getpid(), "released_at": utc_now()})
            except Exception:
                pass
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
                self._workspace_lock_fd = None
                self._workspace_lock_meeting_id = ""

    def _workspace_meeting_lock_held_elsewhere(self) -> bool:
        if self._workspace_lock_fd is not None:
            return False
        path = self._workspace_meeting_lock_path()
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    def _post_json(self, url: str, payload: dict[str, Any], *, action: str) -> dict[str, Any]:
        data = json.dumps(remove_none(payload), ensure_ascii=False).encode("utf-8")
        req = request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.tingwu_api_key}",
                "Content-Type": "application/json",
                "user-agent": "openclaw/0.1 tingwu-meeting",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                body = read_limited_response_body(response, max_bytes=MAX_TINGWU_API_BYTES, label=f"{action} response")
        except error.HTTPError as exc:
            detail = redact_sensitive_text(read_limited_response_body(exc, max_bytes=64 * 1024, label=f"{action} error response"))[:2000]
            raise TingwuMeetingError(f"{action} failed: HTTP {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise TingwuMeetingError(redact_sensitive_text(f"{action} failed: {exc}")) from exc
        except TingwuMeetingError:
            raise
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise TingwuMeetingError(f"{action} returned non-JSON response: {redact_sensitive_text(body)[:500]}") from exc
        if not isinstance(parsed, dict):
            raise TingwuMeetingError(f"{action} returned invalid payload.")
        logged_response = sanitize_event_payload(parsed)
        self._record_tingwu_http_operation(
            self._http_operation_meeting_id,
            action=action,
            url=url,
            request_payload=payload,
            response=logged_response,
        )
        return parsed if action == "GetTask" else logged_response

    def _record_tingwu_http_operation(
        self,
        meeting_id: str,
        *,
        action: str,
        url: str,
        request_payload: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        if not meeting_id:
            return
        input_payload = request_payload.get("input") if isinstance(request_payload.get("input"), dict) else {}
        response_status = self._task_status(response)
        logged_response = sanitize_event_payload(response)
        if action != "GetTask":
            response = logged_response
        operation = sanitize_event_payload(
            {
                "timestamp": utc_now(),
                "action": action,
                "endpoint": redact_provider_url(url),
                "model": request_payload.get("model"),
                "request_task": input_payload.get("task"),
                "request_type": input_payload.get("type"),
                "request_data_id": input_payload.get("dataId"),
                "response_data_id": self._extract_data_id(logged_response),
                "response_status": response_status,
            }
        )
        session = self._sessions.get(meeting_id)
        if session is not None:
            session.tingwu_http_operations.append(operation)
            del session.tingwu_http_operations[:-MAX_TINGWU_HTTP_OPERATIONS]
            return
        pending = self._pending_http_operations.setdefault(meeting_id, [])
        pending.append(operation)
        del pending[:-MAX_TINGWU_HTTP_OPERATIONS]

    def _drain_http_operations(self, meeting_id: str) -> list[dict[str, Any]]:
        pending = self._pending_http_operations.pop(meeting_id, [])
        return list(pending[-MAX_TINGWU_HTTP_OPERATIONS:])

    def _extract_data_id(self, payload: dict[str, Any]) -> str:
        candidates: list[Any] = [
            payload.get("data_id"),
            payload.get("dataId"),
            payload.get("task_id"),
            payload.get("TaskId"),
        ]
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        candidates.extend([output.get("dataId"), output.get("data_id"), output.get("task_id"), output.get("TaskId")])
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    def _task_status(self, payload: dict[str, Any]) -> str:
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        raw = first_present(
            output,
            ("status", "task_status", "taskStatus", "TaskStatus"),
            default=first_present(payload, ("status", "task_status", "taskStatus", "Status", "TaskStatus")),
        )
        if isinstance(raw, int):
            return {0: "completed", 1: "running", 2: "failed", 3: "transcribing"}.get(raw, str(raw))
        status = str(raw or "").lower()
        if status in {"0", "completed", "succeeded", "success", "finish", "finished"}:
            return "completed"
        if status in {"2", "failed", "error", "canceled", "cancelled"}:
            return "failed"
        return status or "running"

    def _hydrate_minutes_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        hydrated = dict(payload)
        hydrated_output = dict(output)
        artifact_keys = {
            "transcriptionPath",
            "translationsPath",
            "summarizationPath",
            "meetingAssistancePath",
            "autoChaptersPath",
            "mindMapPath",
            "pptExtractionPath",
            "textPolishPath",
            "customPromptPath",
            "keyInformationPath",
            "questionsAnsweringPath",
            "conversationalPath",
        }
        artifact_keys.update(
            str(key)
            for key, value in output.items()
            if str(key).endswith("Path") and isinstance(value, str) and value and value.lower() != "null"
        )
        for key in sorted(artifact_keys):
            url = output.get(key)
            if isinstance(url, str) and url and url.lower() != "null":
                hydrated_output[key] = redact_url_origin(url)
                try:
                    hydrated_output[f"{key}Data"] = sanitize_event_payload(self._fetch_json_url(url))
                except Exception as exc:
                    hydrated_output[f"{key}Error"] = redact_sensitive_text(str(exc))[:1000]
        hydrated["output"] = hydrated_output
        return hydrated

    def _fetch_json_url(self, url: str) -> Any:
        url = validate_tingwu_artifact_url(url, trusted_base_url=self.config.tingwu_http_url)
        try:
            with self._open_validated_artifact_url(url) as response:
                body = read_limited_response_body(response, label="Tingwu artifact response")
        except error.HTTPError as exc:
            detail = redact_sensitive_text(read_limited_response_body(exc, max_bytes=64 * 1024, label="Tingwu artifact error response"))[:1000]
            raise TingwuMeetingError(f"Tingwu artifact fetch failed: HTTP {exc.code} {detail}") from exc
        except error.URLError as exc:
            reason = redact_sensitive_text(str(getattr(exc, "reason", exc)))[:1000]
            raise TingwuMeetingError(f"Tingwu artifact fetch failed: {reason}") from exc
        except TingwuMeetingError:
            raise
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body

    def _open_validated_artifact_url(self, url: str) -> Any:
        current = url
        for _ in range(MAX_TINGWU_ARTIFACT_REDIRECTS + 1):
            validate_tingwu_artifact_url(current, trusted_base_url=self.config.tingwu_http_url)
            req = request.Request(current, headers={"user-agent": "openclaw/0.1 tingwu-meeting"}, method="GET")
            try:
                opener = request.build_opener(NoRedirectHandler)
                return opener.open(req, timeout=30)
            except error.HTTPError as exc:
                if exc.code not in {301, 302, 303, 307, 308}:
                    raise
                location = exc.headers.get("Location", "")
                if not location:
                    raise TingwuMeetingError(f"Tingwu artifact redirect missing Location: {redact_url_origin(current)}") from exc
                current = parse.urljoin(current, location)
        raise TingwuMeetingError(f"Tingwu artifact redirect limit exceeded: {MAX_TINGWU_ARTIFACT_REDIRECTS}")

    def _parse_ws_message(self, message: object) -> dict[str, Any]:
        if isinstance(message, (bytes, bytearray)):
            return {"type": "binary", "bytes": len(message)}
        try:
            parsed = json.loads(str(message))
        except json.JSONDecodeError:
            return {"type": "message", "message": str(message)}
        return parsed if isinstance(parsed, dict) else {"type": "message", "message": parsed}


def remove_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: remove_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [remove_none(item) for item in value]
    return value


def first_present(payload: dict[str, Any], keys: tuple[str, ...], *, default: Any = None) -> Any:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return default


class NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def extract_transcript_text(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    transcription = output.get("transcription") if isinstance(output.get("transcription"), dict) else {}
    translations = output.get("translations") if isinstance(output.get("translations"), dict) else {}
    ai_result = output.get("aiResult") if isinstance(output.get("aiResult"), dict) else {}
    candidates: list[Any] = [
        event.get("text"),
        event.get("transcript"),
        event.get("sentence"),
        event.get("result"),
        transcription.get("text"),
        transcription.get("sentence"),
        transcription.get("result"),
        transcription.get("words"),
        transcription.get("stashResult"),
        translations.get("text"),
        translations.get("sentence"),
        translations.get("words"),
        translations.get("translations"),
        ai_result.get("correction"),
        output.get("text"),
        output.get("sentence"),
        output.get("result"),
    ]
    legacy_output = event.get("output") if isinstance(event.get("output"), dict) else {}
    candidates.extend([legacy_output.get("text"), legacy_output.get("transcript"), legacy_output.get("sentence"), legacy_output.get("result")])
    for candidate in candidates:
        text = _text_from_candidate(candidate)
        if text:
            return text
    return ""


def _text_from_candidate(candidate: Any) -> str:
    if isinstance(candidate, str):
        return candidate.strip()
    if isinstance(candidate, dict):
        for key in ("text", "sentence", "transcript", "content", "paragraph", "paragraphText", "formalParagraphText", "summary", "result", "word", "value"):
            text = _text_from_candidate(candidate.get(key))
            if text:
                return text
        words = candidate.get("words")
        if isinstance(words, list):
            text = "".join(_text_from_candidate(item) for item in words).strip()
            if text:
                return text
        sentences = candidate.get("sentences")
        if isinstance(sentences, list):
            text = " ".join(_text_from_candidate(item) for item in sentences).strip()
            if text:
                return text
        translations = candidate.get("translations")
        if isinstance(translations, dict):
            text = " ".join(_text_from_candidate(item) for item in translations.values()).strip()
            if text:
                return text
        stash = candidate.get("stashResult")
        if isinstance(stash, dict):
            text = _text_from_candidate(stash)
            if text:
                return text
        nested_text = " ".join(_text_from_candidate(item) for item in candidate.values()).strip()
        if nested_text:
            return nested_text
    if isinstance(candidate, list):
        return " ".join(_text_from_candidate(item) for item in candidate).strip()
    return ""


def is_final_transcript(event: dict[str, Any]) -> bool:
    marker = str(event.get("type") or event.get("event") or event.get("status") or "").lower()
    if any(token in marker for token in ("sentence_end", "completed", "final")):
        return True
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    action = str(output.get("action") or "").lower()
    transcription = output.get("transcription") if isinstance(output.get("transcription"), dict) else {}
    translations = output.get("translations") if isinstance(output.get("translations"), dict) else {}
    legacy_output = event.get("output") if isinstance(event.get("output"), dict) else {}
    return bool(
        truthy_marker(event.get("final"))
        or truthy_marker(event.get("is_final"))
        or truthy_marker(legacy_output.get("final"))
        or truthy_marker(legacy_output.get("is_final"))
        or truthy_marker(transcription.get("sentenceEnd"))
        or truthy_marker(translations.get("sentenceEnd"))
        or action in {"ai-result", "speech-end"}
    )


def extract_speaker(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    transcription = output.get("transcription") if isinstance(output.get("transcription"), dict) else {}
    legacy_output = event.get("output") if isinstance(event.get("output"), dict) else {}
    candidates = [
        event.get("speaker"),
        event.get("speaker_id"),
        event.get("speakerId"),
        event.get("speakerID"),
        event.get("speakerName"),
        event.get("speakerLabel"),
        event.get("role"),
        event.get("role_id"),
        event.get("roleId"),
        event.get("channel"),
        event.get("channel_id"),
        event.get("channelId"),
        output.get("speaker"),
        output.get("speaker_id"),
        output.get("speakerId"),
        output.get("speakerID"),
        output.get("speakerName"),
        output.get("speakerLabel"),
        output.get("role"),
        output.get("role_id"),
        output.get("roleId"),
        output.get("channel"),
        output.get("channel_id"),
        output.get("channelId"),
        transcription.get("speaker"),
        transcription.get("speaker_id"),
        transcription.get("speakerId"),
        transcription.get("speakerID"),
        transcription.get("speakerName"),
        transcription.get("speakerLabel"),
        transcription.get("role"),
        transcription.get("role_id"),
        transcription.get("roleId"),
        transcription.get("channel"),
        transcription.get("channel_id"),
        transcription.get("channelId"),
        legacy_output.get("speaker"),
        legacy_output.get("speaker_id"),
        legacy_output.get("speakerId"),
        legacy_output.get("speakerName"),
    ]
    for candidate in candidates:
        speaker = normalize_speaker(candidate)
        if speaker:
            return speaker
    nested = find_nested_speaker(event)
    if nested:
        return nested
    return "Unknown"


def normalize_speaker(candidate: Any) -> str:
    if candidate in {None, ""}:
        return ""
    if isinstance(candidate, bool):
        return ""
    if isinstance(candidate, (int, float)):
        return f"Speaker {int(candidate)}"
    if isinstance(candidate, str):
        value = candidate.strip()
        if not value or value.lower() in {"unknown", "none", "null", "undefined"}:
            return ""
        if re.fullmatch(r"\d+(?:\.0+)?", value):
            return f"Speaker {int(float(value))}"
        return value
    if isinstance(candidate, dict):
        for key in (
            "speaker",
            "speaker_id",
            "speakerId",
            "speakerID",
            "speakerName",
            "speakerLabel",
            "role",
            "role_id",
            "roleId",
            "channel",
            "channel_id",
            "channelId",
        ):
            speaker = normalize_speaker(candidate.get(key))
            if speaker:
                return speaker
    return ""


def find_nested_speaker(value: Any, *, depth: int = 0) -> str:
    if depth > 6:
        return ""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized_key in {
                "speaker",
                "speakerid",
                "speakername",
                "speakerlabel",
                "role",
                "roleid",
                "channel",
                "channelid",
            }:
                speaker = normalize_speaker(item)
                if speaker:
                    return speaker
        for item in value.values():
            speaker = find_nested_speaker(item, depth=depth + 1)
            if speaker:
                return speaker
    elif isinstance(value, list):
        for item in value:
            speaker = find_nested_speaker(item, depth=depth + 1)
            if speaker:
                return speaker
    return ""


def extract_agent_event(event: dict[str, Any]) -> dict[str, Any] | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    action = str(output.get("action") or event.get("action") or event.get("type") or event.get("event") or "")
    agent_result = output.get("agent_result") if isinstance(output.get("agent_result"), dict) else {}
    commands = output.get("commands") if isinstance(output.get("commands"), list) else []
    if action != "agent_result" and not agent_result and not commands:
        return None
    command_items = [item for item in commands if isinstance(item, dict)]
    meeting_commands = [item for item in command_items if str(item.get("name") or "") == "meeting_state_change"]
    meeting_data_ids = [
        str((item.get("arguments") if isinstance(item.get("arguments"), dict) else {}).get("dataId") or "").strip()
        for item in meeting_commands
    ]
    return remove_none(
        {
            "timestamp": utc_now(),
            "type": action or "agent_result",
            "agent_id": str(agent_result.get("agentId") or output.get("agentId") or ""),
            "text": _text_from_candidate(agent_result) or _text_from_candidate(output.get("text")),
            "data_id": next((item for item in meeting_data_ids if item), ""),
            "meeting_state_commands": meeting_commands,
            "event": compact_event_payload(event),
        }
    )


def tingwu_feature_sections(payload: dict[str, Any]) -> list[str]:
    sections: list[str] = []
    section_specs = [
        ("Full Summary", ("FullSummary", "fullSummary", "ParagraphSummary", "paragraphSummary", "summary", "Summary")),
        ("Speaker Summary", ("ConversationalSummary", "conversationalSummary", "SpeakerSummary", "speakerSummary")),
        ("Key Information", ("KeyInformation", "keyInformation", "KeyInformations", "keyInformations", "KeySentences", "keySentences")),
        ("Questions And Answers", ("QuestionsAnswering", "questionsAnswering", "Questions", "questions", "QA", "qa")),
        ("Auto Chapters", ("AutoChapters", "autoChapters", "Chapters", "chapters")),
        ("Mind Map", ("MindMap", "mindMap")),
        ("PPT Extraction", ("PptExtraction", "pptExtraction", "PPTExtraction", "PPT")),
        ("Text Polish", ("TextPolish", "textPolish")),
        ("Custom Prompt", ("CustomPrompt", "customPrompt")),
        ("Translations", ("Translations", "translationsPathData", "translations")),
        ("Transcription", ("Transcription", "transcriptionPathData", "transcription")),
    ]
    for title, keys in section_specs:
        value = first_feature_value(payload, keys)
        text = feature_markdown(value)
        if text:
            sections.extend([f"## {title}", text, ""])
    while sections and sections[-1] == "":
        sections.pop()
    return sections


def first_feature_value(payload: Any, keys: tuple[str, ...]) -> Any:
    for item in minutes_candidate_objects(payload):
        value = first_present_case_insensitive(item, keys)
        if value is not None and value != "":
            return value
    return None


def feature_markdown(value: Any, *, depth: int = 0) -> str:
    if value is None or value == "":
        return ""
    if depth > 4:
        return json.dumps(value, ensure_ascii=False)[:4000]
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        items = [feature_markdown(item, depth=depth + 1).strip() for item in value]
        items = [item for item in items if item]
        if not items:
            return ""
        return "\n".join(f"- {item}" if "\n" not in item else f"- {item.replace(chr(10), chr(10) + '  ')}" for item in items[:30])
    if isinstance(value, dict):
        text = _text_from_candidate(value)
        if text and len(text) > 8:
            return text
        parts: list[str] = []
        for key, item in value.items():
            rendered = feature_markdown(item, depth=depth + 1).strip()
            if rendered:
                parts.append(f"- {key}: {rendered}" if "\n" not in rendered else f"- {key}:\n  {rendered.replace(chr(10), chr(10) + '  ')}")
        return "\n".join(parts[:30])
    return str(value)


def truthy_marker(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "final", "completed", "complete", "sentence_end"}
    return bool(value)


def normalize_minutes_payload(payload: dict[str, Any]) -> dict[str, Any]:
    text_blob = json.dumps(payload, ensure_ascii=False)
    candidates = minutes_candidate_objects(payload)
    summary = ""
    summary_source = ""
    decisions: list[str] = []
    action_items: list[str] = []
    for item in candidates:
        if not summary:
            summary_value, summary_key = first_present_case_insensitive_with_key(
                item,
                (
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
                    "summaryMindMap",
                    "SummaryMindMap",
                ),
            )
            if summary_value is None and is_summary_container(item):
                summary_value, summary_key = first_present_case_insensitive_with_key(item, ("summary", "Summary"))
            summary = _text_from_candidate(summary_value)
            if summary:
                summary_source = summary_key
        decisions.extend(
            _list_from_candidate(
                first_present_case_insensitive(
                    item,
                    (
                        "decisions",
                        "decision",
                        "Decision",
                        "key_sentence",
                        "keySentences",
                        "KeySentences",
                        "keyInformation",
                        "KeyInformation",
                        "keyInformations",
                        "KeyInformations",
                        "key_information",
                        "conclusions",
                        "conclusion",
                        "Conclusion",
                        "meetingAssistance",
                        "MeetingAssistance",
                    ),
                )
            )
        )
        action_items.extend(
            _list_from_candidate(
                first_present_case_insensitive(
                    item,
                    (
                        "action_items",
                        "ActionItems",
                        "actionItems",
                        "actionItem",
                        "ActionItem",
                        "todo",
                        "Todo",
                        "todos",
                        "Todos",
                        "todoList",
                        "TodoList",
                        "tasks",
                        "Tasks",
                        "taskList",
                        "TaskList",
                        "actions",
                        "Actions",
                    ),
                )
            )
        )
    if not summary:
        summary = text_blob[:800]
        summary_source = "raw_payload"
    return {
        "summary": summary.strip(),
        "summary_source": summary_source,
        "structured_summary": summary_source != "raw_payload",
        "decisions": dedupe_strings(decisions),
        "action_items": dedupe_strings(action_items),
    }


def minutes_candidate_objects(payload: Any) -> list[dict[str, Any]]:
    roots: list[Any] = [payload]
    if isinstance(payload, dict):
        output = payload.get("output")
        if isinstance(output, dict):
            roots.append(output)
            for key in (
                "summarizationPathData",
                "meetingAssistancePathData",
                "autoChaptersPathData",
                "transcriptionPathData",
                "translationsPathData",
                "mindMapPathData",
                "pptExtractionPathData",
                "textPolishPathData",
                "customPromptPathData",
                "keyInformationPathData",
                "questionsAnsweringPathData",
                "conversationalPathData",
                "Summarization",
                "MeetingAssistance",
            ):
                value = first_present_case_insensitive(output, (key,))
                if value is not None:
                    roots.append(value)
    result: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            marker = id(value)
            if marker in seen:
                return
            seen.add(marker)
            result.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for root in roots:
        visit(root)
    return result


def is_summary_container(item: dict[str, Any]) -> bool:
    key_text = " ".join(str(key) for key in item.keys()).lower()
    return any(marker in key_text for marker in ("summary", "summarization", "abstract"))


def first_present_case_insensitive(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    value, _ = first_present_case_insensitive_with_key(item, keys)
    return value


def first_present_case_insensitive_with_key(item: dict[str, Any], keys: tuple[str, ...]) -> tuple[Any, str]:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key], key
    lowered = {str(key).lower(): (key, value) for key, value in item.items()}
    for key in keys:
        found = lowered.get(key.lower())
        if found is not None and found[1] is not None:
            return found[1], str(found[0])
    return None, ""


def _list_from_candidate(candidate: Any) -> list[str]:
    if candidate is None:
        return []
    if isinstance(candidate, str):
        parts = re.split(r"[\r\n]+|(?<=[。；;])\s*|[•●]", candidate)
        return [line.strip("- \t。；;").strip() for line in parts if line.strip("- \t。；;").strip()]
    if isinstance(candidate, list):
        values: list[str] = []
        for item in candidate:
            values.extend(_list_from_candidate(item))
        return values
    if isinstance(candidate, dict):
        for key in (
            "text",
            "Text",
            "content",
            "Content",
            "summary",
            "Summary",
            "title",
            "Title",
            "result",
            "Result",
            "description",
            "Description",
            "name",
            "Name",
            "sentence",
            "Sentence",
            "task",
            "Task",
            "action",
            "Action",
            "todo",
            "Todo",
        ):
            text = _text_from_candidate(first_present_case_insensitive(candidate, (key,)))
            if text:
                return [text]
        for key in ("items", "Items", "list", "List", "children", "Children", "sentences", "Sentences", "KeySentences", "Actions"):
            values = _list_from_candidate(first_present_case_insensitive(candidate, (key,)))
            if values:
                return values
        return [json.dumps(candidate, ensure_ascii=False)]
    return [str(candidate)]


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = value.strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result
