from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import websocket


class DashScopeTTSError(RuntimeError):
    pass


class DashScopeTTS:
    def __init__(self, *, api_key: str, model: str, voice: str, url: str = ""):
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.url = url or "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

    def speak(self, text: str, output_path: Path) -> Path:
        result = self.speak_with_stats(text, output_path)
        return Path(str(result["path"]))

    def speak_with_stats(self, text: str, output_path: Path) -> dict[str, object]:
        if not self.api_key:
            raise DashScopeTTSError("DASHSCOPE_API_KEY is not configured.")
        task_id = uuid.uuid4().hex
        audio_chunks: list[bytes] = []
        errors: list[str] = []
        opened = threading.Event()
        finished = threading.Event()
        started_at = time.perf_counter()
        opened_at: float | None = None
        first_audio_at: float | None = None

        def on_open(ws):
            nonlocal opened_at
            opened_at = time.perf_counter()
            opened.set()
            ws.send(json.dumps(self._start_payload(task_id), ensure_ascii=False))
            ws.send(json.dumps(self._continue_payload(task_id, text), ensure_ascii=False))
            ws.send(json.dumps(self._finish_payload(task_id), ensure_ascii=False))

        def on_message(ws, message):
            nonlocal first_audio_at
            if isinstance(message, (bytes, bytearray)):
                if first_audio_at is None:
                    first_audio_at = time.perf_counter()
                audio_chunks.append(bytes(message))
                return
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                return
            header = payload.get("header", {})
            event = header.get("event")
            if event == "task-failed":
                errors.append(json.dumps(payload, ensure_ascii=False))
                finished.set()
                ws.close()
            elif event == "task-finished":
                finished.set()
                ws.close()

        def on_error(ws, error):
            errors.append(str(error))
            finished.set()

        def on_close(ws, code, reason):
            finished.set()

        ws = websocket.WebSocketApp(
            self.url,
            header={
                "Authorization": f"Bearer {self.api_key}",
                "user-agent": "openclaw/0.1 dashscope-tts-direct",
            },
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        thread = threading.Thread(target=lambda: ws.run_forever(ping_interval=0), daemon=True)
        thread.start()

        if not opened.wait(15):
            ws.close()
            raise DashScopeTTSError(
                "DashScope TTS websocket did not open within 15s"
                + (f": {errors[-1]}" if errors else "")
            )
        if not finished.wait(60):
            ws.close()
            raise DashScopeTTSError("DashScope TTS timed out waiting for audio.")
        if errors:
            raise DashScopeTTSError(f"DashScope TTS websocket error: {errors[-1]}")
        if not audio_chunks:
            raise DashScopeTTSError("DashScope TTS returned no audio chunks.")

        output_path.write_bytes(b"".join(audio_chunks))
        # The server returns WAV because the requested format is wav.
        completed_at = time.perf_counter()
        audio_bytes = sum(len(chunk) for chunk in audio_chunks)
        return {
            "path": str(output_path),
            "bytes": audio_bytes,
            "chunks": len(audio_chunks),
            "open_seconds": round(opened_at - started_at, 3) if opened_at else None,
            "first_audio_seconds": round(first_audio_at - started_at, 3) if first_audio_at else None,
            "total_seconds": round(completed_at - started_at, 3),
        }

    def _start_payload(self, task_id: str) -> dict:
        return {
            "header": {
                "action": "run-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {
                "model": self.model,
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "input": {},
                "parameters": {
                    "voice": self.voice,
                    "volume": 50,
                    "text_type": "PlainText",
                    "sample_rate": 16000,
                    "rate": 1.0,
                    "format": "wav",
                    "pitch": 1.0,
                    "seed": 0,
                    "type": 0,
                    "enable_ssml": True,
                },
            },
        }

    def _continue_payload(self, task_id: str, text: str) -> dict:
        return {
            "header": {
                "action": "continue-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {
                "model": self.model,
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "input": {"text": text},
            },
        }

    def _finish_payload(self, task_id: str) -> dict:
        return {
            "header": {
                "action": "finish-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {"input": {}},
        }


class ReusableDashScopeTTS:
    """DashScope TTS client that keeps one WebSocket open across utterances."""

    def __init__(self, *, api_key: str, model: str, voice: str, url: str = ""):
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.url = url or "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
        self._base = DashScopeTTS(api_key=api_key, model=model, voice=voice, url=url)
        self._lock = threading.Lock()
        self._opened = threading.Event()
        self._closed = threading.Event()
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._current: dict[str, Any] | None = None

    def start(self, timeout: float = 15) -> dict[str, object]:
        if not self.api_key:
            raise DashScopeTTSError("DASHSCOPE_API_KEY is not configured.")
        if self._ws is not None and self._opened.is_set() and not self._closed.is_set():
            return {"open_seconds": 0.0, "reused": True}

        self._opened.clear()
        self._closed.clear()
        started_at = time.perf_counter()

        def on_open(ws):
            self._opened.set()

        def on_message(ws, message):
            current = self._current
            if current is None:
                return
            if isinstance(message, (bytes, bytearray)):
                if current["first_audio_at"] is None:
                    current["first_audio_at"] = time.perf_counter()
                current["chunks"].append(bytes(message))
                return
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                return
            header = payload.get("header", {})
            event = header.get("event")
            if event == "task-failed":
                current["errors"].append(json.dumps(payload, ensure_ascii=False))
                current["finished"].set()
            elif event == "task-finished":
                current["finished"].set()

        def on_error(ws, error):
            current = self._current
            if current is not None:
                current["errors"].append(str(error))
                current["finished"].set()
            self._closed.set()

        def on_close(ws, code, reason):
            self._closed.set()

        self._ws = websocket.WebSocketApp(
            self.url,
            header={
                "Authorization": f"Bearer {self.api_key}",
                "user-agent": "openclaw/0.1 dashscope-tts-reusable",
            },
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        self._thread = threading.Thread(
            target=lambda: self._ws.run_forever(ping_interval=20, ping_timeout=10),
            daemon=True,
        )
        self._thread.start()
        if not self._opened.wait(timeout):
            self.close()
            raise DashScopeTTSError("DashScope reusable TTS websocket did not open within timeout.")
        return {"open_seconds": round(time.perf_counter() - started_at, 3), "reused": False}

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
        self._closed.set()

    def speak_with_stats(self, text: str, output_path: Path) -> dict[str, object]:
        with self._lock:
            start_stats = self.start()
            if self._ws is None:
                raise DashScopeTTSError("DashScope reusable TTS websocket is not available.")
            task_id = uuid.uuid4().hex
            started_at = time.perf_counter()
            current = {
                "chunks": [],
                "errors": [],
                "finished": threading.Event(),
                "first_audio_at": None,
            }
            self._current = current
            self._ws.send(json.dumps(self._base._start_payload(task_id), ensure_ascii=False))
            self._ws.send(json.dumps(self._base._continue_payload(task_id, text), ensure_ascii=False))
            self._ws.send(json.dumps(self._base._finish_payload(task_id), ensure_ascii=False))
            if not current["finished"].wait(60):
                raise DashScopeTTSError("DashScope reusable TTS timed out waiting for audio.")
            if current["errors"]:
                self.close()
                raise DashScopeTTSError(f"DashScope reusable TTS websocket error: {current['errors'][-1]}")
            chunks = current["chunks"]
            if not chunks:
                raise DashScopeTTSError("DashScope reusable TTS returned no audio chunks.")
            output_path.write_bytes(b"".join(chunks))
            completed_at = time.perf_counter()
            first_audio_at = current["first_audio_at"]
            return {
                "path": str(output_path),
                "bytes": sum(len(chunk) for chunk in chunks),
                "chunks": len(chunks),
                "connection_open_seconds": start_stats["open_seconds"],
                "connection_reused": start_stats["reused"],
                "first_audio_seconds": round(first_audio_at - started_at, 3) if first_audio_at else None,
                "total_seconds": round(completed_at - started_at, 3),
            }
