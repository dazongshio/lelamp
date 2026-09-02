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


from .tingwu_session import TingwuSessionMixin
from .tingwu_realtime import TingwuRealtimeMixin
from .tingwu_client import TingwuClientMixin

class TingwuMeetingProvider(TingwuSessionMixin, TingwuRealtimeMixin, TingwuClientMixin):
    """Tingwu provider assembled from domain-specific session, realtime, and client mixins."""




from .tingwu_minutes import (
    NoRedirectHandler, _list_from_candidate, _text_from_candidate, dedupe_strings,
    extract_agent_event, extract_speaker, extract_transcript_text, feature_markdown,
    find_nested_speaker, first_feature_value, first_present,
    first_present_case_insensitive, first_present_case_insensitive_with_key,
    is_final_transcript, is_summary_container, minutes_candidate_objects,
    normalize_minutes_payload, normalize_speaker, remove_none, tingwu_feature_sections,
    truthy_marker,
)
