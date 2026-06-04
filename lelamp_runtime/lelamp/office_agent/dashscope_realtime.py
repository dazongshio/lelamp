from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websocket


class DashScopeRealtimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class DashScopeRealtimeConfig:
    api_key: str
    model: str = "qwen3-omni-flash-realtime"
    url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    voice: str = "Cherry"
    instructions: str = "你是 LeLamp 桌面数字人助手。请用简体中文回答，保持简短、自然、适合朗读。"
    input_audio_format: str = "pcm"
    output_audio_format: str = "pcm"
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    turn_detection_type: str = "server_vad"
    vad_threshold: float = 0.5
    silence_duration_ms: int = 600
    transcription_model: str = "gummy-realtime-v1"


class DashScopeRealtimeClient:
    """Small OpenAI-Realtime-compatible DashScope WebSocket client."""

    def __init__(
        self,
        config: DashScopeRealtimeConfig,
        *,
        on_audio_delta: Callable[[bytes], None] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.config = config
        self.on_audio_delta = on_audio_delta
        self.on_event = on_event
        self.events: Queue[dict[str, Any]] = Queue()
        self._opened = threading.Event()
        self._closed = threading.Event()
        self._errors: list[str] = []
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._session_ready = threading.Event()
        self._first_audio_at: float | None = None
        self._speech_started_at: float | None = None

    @property
    def first_audio_seconds(self) -> float | None:
        if self._first_audio_at is None or self._speech_started_at is None:
            return None
        return self._first_audio_at - self._speech_started_at

    def connect(self, timeout: float = 15) -> None:
        if not self.config.api_key:
            raise DashScopeRealtimeError("DASHSCOPE_API_KEY is not configured.")
        if self._ws is not None and self._opened.is_set() and not self._closed.is_set():
            return
        self._opened.clear()
        self._closed.clear()
        self._session_ready.clear()
        self._errors.clear()
        self._first_audio_at = None
        self._speech_started_at = None

        self._ws = websocket.WebSocketApp(
            self._url_with_model(),
            header={
                "Authorization": f"Bearer {self.config.api_key}",
                "OpenAI-Beta": "realtime=v1",
                "user-agent": "openclaw/0.1 dashscope-realtime",
            },
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(
            target=lambda: self._ws.run_forever(ping_interval=20, ping_timeout=10),
            name="dashscope-realtime-ws",
            daemon=True,
        )
        self._thread.start()
        if not self._opened.wait(timeout):
            self.close()
            suffix = f": {self._errors[-1]}" if self._errors else ""
            raise DashScopeRealtimeError(f"DashScope Realtime websocket did not open{suffix}")
        self.update_session()
        self._session_ready.wait(3)

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
        self._closed.set()

    def append_audio(self, pcm: bytes) -> None:
        if not pcm:
            return
        self.send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }
        )

    def commit_audio(self) -> None:
        self.send({"type": "input_audio_buffer.commit"})

    def clear_audio(self) -> None:
        self.send({"type": "input_audio_buffer.clear"})

    def cancel_response(self) -> None:
        self.send({"type": "response.cancel"})

    def create_response(self) -> None:
        self.send({"type": "response.create"})

    def append_text(self, text: str) -> None:
        if not text.strip():
            return
        self.send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )

    def ask_text(self, text: str, *, timeout: float = 25) -> dict[str, Any]:
        self.connect(timeout=min(timeout, 15))
        self.append_text(text)
        self.create_response()
        started = time.monotonic()
        chunks: list[str] = []
        last_event: dict[str, Any] = {}
        while time.monotonic() - started < timeout:
            event = self.next_event(timeout=1)
            if event is None:
                continue
            last_event = event
            event_type = str(event.get("type", ""))
            delta = event.get("delta")
            if event_type in {"response.text.delta", "response.output_text.delta", "response.audio_transcript.delta"} and isinstance(delta, str):
                chunks.append(delta)
            elif event_type == "response.done":
                text_value = "".join(chunks).strip() or extract_realtime_response_text(event)
                if text_value:
                    return {"status": "completed", "text": text_value, "event_type": event_type}
                break
            elif event_type == "error":
                raise DashScopeRealtimeError(json.dumps(event.get("error", event), ensure_ascii=False))
        text_value = "".join(chunks).strip() or extract_realtime_response_text(last_event)
        if text_value:
            return {"status": "completed", "text": text_value, "event_type": str(last_event.get("type", ""))}
        raise DashScopeRealtimeError("Qwen-Omni realtime text response timed out or returned no text.")

    def update_session(self) -> None:
        self.send(
            {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": self.config.instructions,
                    "voice": self.config.voice,
                    "input_audio_format": self.config.input_audio_format,
                    "output_audio_format": self.config.output_audio_format,
                    "input_audio_transcription": {
                        "model": self.config.transcription_model,
                    },
                    "turn_detection": {
                        "type": self.config.turn_detection_type,
                        "threshold": self.config.vad_threshold,
                        "silence_duration_ms": self.config.silence_duration_ms,
                    },
                },
            }
        )

    def send(self, payload: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            raise DashScopeRealtimeError("DashScope Realtime websocket is not connected.")
        payload.setdefault("event_id", f"evt_{uuid.uuid4().hex}")
        with self._send_lock:
            ws.send(json.dumps(payload, ensure_ascii=False))

    def next_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            return self.events.get(timeout=timeout)
        except Empty:
            return None

    def _url_with_model(self) -> str:
        parts = urlsplit(self.config.url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.setdefault("model", self.config.model)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def _on_open(self, ws) -> None:
        self._opened.set()

    def _on_message(self, ws, message) -> None:
        if isinstance(message, (bytes, bytearray)):
            event: dict[str, Any] = {"type": "binary.audio", "audio": bytes(message)}
        else:
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                event = {"type": "message", "message": str(message)}
        event_type = str(event.get("type", ""))
        now = time.perf_counter()
        if event_type == "session.updated":
            self._session_ready.set()
        elif event_type == "input_audio_buffer.speech_started":
            self._speech_started_at = now
            self._first_audio_at = None
        elif event_type == "response.audio.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                audio = base64.b64decode(delta)
                event["audio"] = audio
                if self._first_audio_at is None:
                    self._first_audio_at = now
                if self.on_audio_delta is not None:
                    self.on_audio_delta(audio)
        elif event_type == "error":
            self._errors.append(json.dumps(event.get("error", event), ensure_ascii=False))

        self.events.put(event)
        if self.on_event is not None:
            self.on_event(event)

    def _on_error(self, ws, error) -> None:
        self._errors.append(str(error))
        self._closed.set()
        event = {"type": "error", "error": {"message": str(error)}}
        self.events.put(event)
        if self.on_event is not None:
            self.on_event(event)

    def _on_close(self, ws, code, reason) -> None:
        self._closed.set()
        event = {"type": "closed", "code": code, "reason": reason}
        self.events.put(event)
        if self.on_event is not None:
            self.on_event(event)


def extract_realtime_response_text(event: dict[str, Any]) -> str:
    texts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"text", "transcript"} and isinstance(item, str):
                    texts.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(event.get("response", event))
    return "".join(texts).strip()
