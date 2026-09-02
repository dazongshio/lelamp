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


QWEN_OMNI_35_VOICES: tuple[dict[str, str], ...] = (
    {"voice": "Tina", "label": "Tina", "description": "Warm, sweet, cozy, and crisp for problem solving."},
    {"voice": "Cindy", "label": "Cindy", "description": "Sweet-talking young woman from Taiwan."},
    {"voice": "Liora Mira", "label": "Liora Mira", "description": "Gentle and warm everyday voice."},
    {"voice": "Sunnybobi", "label": "Sunnybobi", "description": "Cheerful neighbor-girl style."},
    {"voice": "Raymond", "label": "Raymond", "description": "Clear-voiced homebody."},
    {"voice": "Ethan", "label": "Ethan", "description": "Bright, warm Mandarin male voice."},
    {"voice": "Theo Calm", "label": "Theo Calm", "description": "Quiet, understanding, healing tone."},
    {"voice": "Serena", "label": "Serena", "description": "Gentle young woman."},
    {"voice": "Harvey", "label": "Harvey", "description": "Deep, mellow, mature voice."},
    {"voice": "Maia", "label": "Maia", "description": "Intellectual and gentle."},
    {"voice": "Evan", "label": "Evan", "description": "Youthful college-student style."},
    {"voice": "Qiao", "label": "Qiao", "description": "Cute with personality."},
    {"voice": "Momo", "label": "Momo", "description": "Playful and mischievous."},
    {"voice": "Wil", "label": "Wil", "description": "Young Shenzhen male with Hong Kong/Taiwan accent."},
    {"voice": "Angel", "label": "Angel", "description": "Sweet, slightly Taiwanese-accented."},
    {"voice": "Li Cassian", "label": "Li Cassian", "description": "Restrained, thoughtful voice."},
    {"voice": "Mia", "label": "Mia", "description": "Slow-living lifestyle artist."},
    {"voice": "Joyner", "label": "Joyner", "description": "Funny, exaggerated, down-to-earth."},
    {"voice": "Gold", "label": "Gold", "description": "West Coast Black rapper style."},
    {"voice": "Katerina", "label": "Katerina", "description": "Mature, commanding, rhythmic."},
    {"voice": "Ryan", "label": "Ryan", "description": "High-energy dramatic delivery."},
    {"voice": "Jennifer", "label": "Jennifer", "description": "Cinematic American female voice."},
    {"voice": "Aiden", "label": "Aiden", "description": "American young man."},
    {"voice": "Mione", "label": "Mione", "description": "Mature British female voice."},
    {"voice": "Sunny", "label": "Sichuan - Sunny", "description": "Sweet Sichuan female voice."},
    {"voice": "Dylan", "label": "Beijing - Dylan", "description": "Young Beijing hutong voice."},
    {"voice": "Eric", "label": "Sichuan - Eric", "description": "Lively Chengdu male voice."},
    {"voice": "Peter", "label": "Tianjin - Peter", "description": "Tianjin crosstalk foil style."},
    {"voice": "Joseph Chen", "label": "Joseph Chen", "description": "Warm overseas Chinese male voice."},
    {"voice": "Marcus", "label": "Shaanxi - Marcus", "description": "Deep, sincere Shaanxi male voice."},
    {"voice": "Li", "label": "Nanjing - Li", "description": "Nanjing male voice."},
    {"voice": "Rocky", "label": "Cantonese - Rocky", "description": "Witty Cantonese chat companion."},
    {"voice": "Sohee", "label": "Sohee", "description": "Warm, expressive Korean female voice."},
    {"voice": "Lenn", "label": "Lenn", "description": "Rational, rebellious German youth."},
    {"voice": "Ono Anna", "label": "Ono Anna", "description": "Clever, playful Japanese voice."},
    {"voice": "Sonrisa", "label": "Sonrisa", "description": "Warm Latin American female voice."},
    {"voice": "Bodega", "label": "Bodega", "description": "Warm, enthusiastic Spanish male voice."},
    {"voice": "Emilien", "label": "Emilien", "description": "Romantic French big brother."},
    {"voice": "Andre", "label": "Andre", "description": "Magnetic, steady male voice."},
    {"voice": "Radio Gol", "label": "Radio Gol", "description": "Passionate football commentator."},
    {"voice": "Alek", "label": "Alek", "description": "Cold Russian spirit with warmth underneath."},
    {"voice": "Rizky", "label": "Rizky", "description": "Young Indonesian male voice."},
    {"voice": "Roya", "label": "Roya", "description": "Sporty, free-spirited female voice."},
    {"voice": "Arda", "label": "Arda", "description": "Clean, crisp, gently warm Turkish voice."},
    {"voice": "Hana", "label": "Hana", "description": "Mature Vietnamese female voice."},
    {"voice": "Dolce", "label": "Dolce", "description": "Laid-back Italian male voice."},
    {"voice": "Jakub", "label": "Jakub", "description": "Charismatic Polish young man."},
    {"voice": "Griet", "label": "Griet", "description": "Mature artistic Dutch female voice."},
    {"voice": "Eliška", "label": "Eliška", "description": "Warm Central European female voice."},
    {"voice": "Marina", "label": "Marina", "description": "Multicultural-city female voice."},
    {"voice": "Siiri", "label": "Siiri", "description": "Reserved, gentle Finnish voice."},
    {"voice": "Ingrid", "label": "Ingrid", "description": "Rural Norwegian female voice."},
    {"voice": "Sigga", "label": "Sigga", "description": "Intellectual Icelandic female voice."},
    {"voice": "Bea", "label": "Bea", "description": "Sweet Filipino female voice."},
    {"voice": "Chloe", "label": "Chloe", "description": "Malaysian office worker voice."},
)


QWEN_OMNI_FLASH_202512_VOICES: tuple[dict[str, str], ...] = (
    {"voice": "Cherry", "label": "Cherry", "description": "Sunny, positive, friendly, natural young woman."},
    {"voice": "Serena", "label": "Serena", "description": "Gentle young woman."},
    {"voice": "Ethan", "label": "Ethan", "description": "Warm, energetic Mandarin male voice."},
    {"voice": "Chelsie", "label": "Chelsie", "description": "Virtual girlfriend style."},
    {"voice": "Momo", "label": "Momo", "description": "Playful and mischievous."},
    {"voice": "Vivian", "label": "Vivian", "description": "Confident, cute, slightly feisty."},
    {"voice": "Moon", "label": "Moon", "description": "Cool and effortless."},
    {"voice": "Maia", "label": "Maia", "description": "Intellectual and gentle."},
    {"voice": "Kai", "label": "Kai", "description": "Soothing voice."},
    {"voice": "Nofish", "label": "Nofish", "description": "Designer voice, weak retroflex pronunciation."},
    {"voice": "Bella", "label": "Bella", "description": "Little girl voice."},
    {"voice": "Jennifer", "label": "Jennifer", "description": "Cinematic American English female voice."},
    {"voice": "Ryan", "label": "Ryan", "description": "Rhythmic, dramatic male voice."},
    {"voice": "Katerina", "label": "Katerina", "description": "Mature, rhythmic female voice."},
    {"voice": "Aiden", "label": "Aiden", "description": "American English young man."},
    {"voice": "Eldric Sage", "label": "Eldric Sage", "description": "Calm, wise elder."},
    {"voice": "Mia", "label": "Mia", "description": "Gentle female voice."},
    {"voice": "Mochi", "label": "Mochi", "description": "Quick-witted young adult."},
    {"voice": "Bellona", "label": "Bellona", "description": "Powerful character voice."},
    {"voice": "Vincent", "label": "Vincent", "description": "Raspy, smoky heroic voice."},
    {"voice": "Bunny", "label": "Bunny", "description": "Very cute little-girl voice."},
    {"voice": "Neil", "label": "Neil", "description": "Professional news-anchor style."},
    {"voice": "Elias", "label": "Elias", "description": "Academic storytelling voice."},
    {"voice": "Arthur", "label": "Arthur", "description": "Earthy village storyteller."},
    {"voice": "Nini", "label": "Nini", "description": "Soft, clingy young female voice."},
    {"voice": "Ebona", "label": "Ebona", "description": "Whispery suspense voice."},
    {"voice": "Seren", "label": "Seren", "description": "Gentle sleep-aid voice."},
    {"voice": "Pip", "label": "Pip", "description": "Playful mischievous boy."},
    {"voice": "Stella", "label": "Stella", "description": "Sweet dramatic teenage-girl voice."},
    {"voice": "Bodega", "label": "Bodega", "description": "Passionate Spanish male voice."},
    {"voice": "Sonrisa", "label": "Sonrisa", "description": "Outgoing Latin American female voice."},
    {"voice": "Alek", "label": "Alek", "description": "Russian male voice."},
    {"voice": "Dolce", "label": "Dolce", "description": "Laid-back Italian male voice."},
    {"voice": "Sohee", "label": "Sohee", "description": "Warm Korean female voice."},
    {"voice": "Ono Anna", "label": "Ono Anna", "description": "Spirited Japanese childhood friend."},
    {"voice": "Lenn", "label": "Lenn", "description": "Rebellious German youth."},
    {"voice": "Emilien", "label": "Emilien", "description": "Romantic French male voice."},
    {"voice": "Andre", "label": "Andre", "description": "Magnetic, steady male voice."},
    {"voice": "Radio Gol", "label": "Radio Gol", "description": "Football commentator."},
    {"voice": "Jada", "label": "Shanghai - Jada", "description": "Fast-paced Shanghai auntie."},
    {"voice": "Dylan", "label": "Beijing - Dylan", "description": "Young Beijing hutong voice."},
    {"voice": "Li", "label": "Nanjing - Li", "description": "Patient yoga teacher."},
    {"voice": "Marcus", "label": "Shaanxi - Marcus", "description": "Deep Shaanxi male voice."},
    {"voice": "Roy", "label": "Southern Min - Roy", "description": "Humorous Taiwanese male voice."},
    {"voice": "Peter", "label": "Tianjin - Peter", "description": "Tianjin crosstalk foil style."},
    {"voice": "Sunny", "label": "Sichuan - Sunny", "description": "Sweet Sichuan female voice."},
    {"voice": "Eric", "label": "Sichuan - Eric", "description": "Chengdu male voice."},
    {"voice": "Rocky", "label": "Cantonese - Rocky", "description": "Witty Cantonese chat companion."},
    {"voice": "Kiki", "label": "Cantonese - Kiki", "description": "Sweet Hong Kong female voice."},
)


QWEN_OMNI_FLASH_202509_VOICES: tuple[dict[str, str], ...] = tuple(
    voice
    for voice in QWEN_OMNI_FLASH_202512_VOICES
    if voice["voice"]
    in {
        "Cherry",
        "Ethan",
        "Nofish",
        "Jennifer",
        "Ryan",
        "Katerina",
        "Elias",
        "Jada",
        "Dylan",
        "Sunny",
        "Li",
        "Marcus",
        "Roy",
        "Peter",
        "Rocky",
        "Kiki",
        "Eric",
    }
)


QWEN_OMNI_TURBO_VOICES: tuple[dict[str, str], ...] = tuple(
    voice
    for voice in QWEN_OMNI_FLASH_202512_VOICES
    if voice["voice"] in {"Cherry", "Serena", "Ethan", "Chelsie"}
)


def qwen_omni_realtime_default_voice(model: str) -> str:
    normalized = model.lower()
    if "3.5" in normalized:
        return "Tina"
    if "turbo" in normalized:
        return "Chelsie"
    return "Cherry"


def qwen_omni_realtime_voices(model: str) -> list[dict[str, str]]:
    normalized = model.lower()
    if "3.5" in normalized:
        voices = QWEN_OMNI_35_VOICES
    elif "turbo" in normalized:
        voices = QWEN_OMNI_TURBO_VOICES
    elif "2025-12-01" in normalized:
        voices = QWEN_OMNI_FLASH_202512_VOICES
    else:
        voices = QWEN_OMNI_FLASH_202509_VOICES
    return [dict(item) for item in voices]


def qwen_omni_voice_supported(model: str, voice: str) -> bool:
    return any(item["voice"] == voice for item in qwen_omni_realtime_voices(model))


@dataclass(frozen=True)
class DashScopeRealtimeConfig:
    api_key: str
    model: str = "qwen3.5-omni-plus-realtime"
    url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    voice: str = "Tina"
    instructions: str = "你是 LeLamp 桌面数字人助手。请用简体中文回答，保持简短、自然、适合朗读。"
    input_audio_format: str = "pcm"
    output_audio_format: str = "pcm"
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    turn_detection_type: str = "server_vad"
    vad_threshold: float = 0.5
    silence_duration_ms: int = 600
    transcription_model: str = "gummy-realtime-v1"
    enable_search: bool = False
    enable_search_sources: bool = True


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
        session: dict[str, Any] = {
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
        }
        if self.config.enable_search:
            session["enable_search"] = True
            if self.config.enable_search_sources:
                session["search_options"] = {"enable_source": True}
        self.send(
            {
                "type": "session.update",
                "session": session,
            },
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
