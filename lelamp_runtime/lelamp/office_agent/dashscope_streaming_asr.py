from __future__ import annotations

import audioop
import shutil
import subprocess
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from .dashscope_asr import DashScopeASRError
from .hardware_probe import resolve_capture_device


class DashScopeStreamingASR:
    def __init__(self, *, api_key: str, model: str, sample_rate: int):
        self.api_key = api_key
        self.model = model
        self.sample_rate = sample_rate

    def listen_once(
        self,
        *,
        device: str,
        max_seconds: int,
        language_hints: list[str] | None = None,
        silence_ms: int = 700,
        frame_ms: int = 100,
        speech_threshold: int = 1200,
        save_path: Path | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise DashScopeASRError("DASHSCOPE_API_KEY is not configured.")
        if shutil.which("arecord") is None:
            raise DashScopeASRError("arecord not found")
        try:
            selected_device = resolve_capture_device(device)
        except RuntimeError as exc:
            raise DashScopeASRError(str(exc)) from exc
        try:
            import dashscope
            from dashscope.audio.asr import Recognition, RecognitionCallback
        except Exception as exc:
            raise DashScopeASRError("dashscope package is not installed.") from exc

        dashscope.api_key = self.api_key
        events: Queue[dict[str, Any]] = Queue()
        final_sentences: list[str] = []
        partial_sentence = ""
        errors: list[str] = []

        class Callback(RecognitionCallback):
            def on_event(self, result):
                sentence = result.get_sentence()
                text = _sentence_text(sentence)
                if text:
                    events.put({"text": text, "sentence": sentence, "final": _sentence_final(sentence)})

            def on_error(self, result):
                errors.append(str(result))

        recognition = Recognition(
            model=self.model,
            callback=Callback(),
            format="pcm",
            sample_rate=self.sample_rate,
            language_hints=language_hints or ["zh", "en"],
        )
        recognition.start()

        channels = 1
        sample_width = 2
        frame_bytes = int(self.sample_rate * frame_ms / 1000) * sample_width * channels
        max_frames = max(1, int(max_seconds * 1000 / frame_ms))
        silence_frames_needed = max(1, int(silence_ms / frame_ms))
        command = [
            "arecord",
            "-q",
            "-D",
            selected_device,
            "-f",
            "S16_LE",
            "-c",
            str(channels),
            "-r",
            str(self.sample_rate),
            "-t",
            "raw",
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdout is not None
        started_at = time.perf_counter()
        speech_started = False
        silence_frames = 0
        chunks: list[bytes] = []
        stop_reason = "max_seconds"
        peak = 0
        frame_rms_max = 0
        first_partial_at: float | None = None
        last_event_at: float | None = None

        try:
            for _ in range(max_frames):
                frame = process.stdout.read(frame_bytes)
                if not frame:
                    stop_reason = "input_closed"
                    break
                chunks.append(frame)
                rms = audioop.rms(frame, sample_width)
                peak = max(peak, audioop.max(frame, sample_width))
                frame_rms_max = max(frame_rms_max, rms)
                if rms >= speech_threshold:
                    speech_started = True
                    silence_frames = 0
                elif speech_started:
                    silence_frames += 1
                recognition.send_audio_frame(frame)

                while True:
                    try:
                        event = events.get_nowait()
                    except Empty:
                        break
                    last_event_at = time.perf_counter()
                    if first_partial_at is None:
                        first_partial_at = last_event_at
                    if event["final"]:
                        final_sentences.append(str(event["text"]))
                    else:
                        partial_sentence = str(event["text"])

                if speech_started and silence_frames >= silence_frames_needed:
                    stop_reason = "silence"
                    break
        finally:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
            recognition.stop()

        deadline = time.perf_counter() + 1.5
        while time.perf_counter() < deadline:
            try:
                event = events.get(timeout=0.1)
            except Empty:
                continue
            last_event_at = time.perf_counter()
            if first_partial_at is None:
                first_partial_at = last_event_at
            if event["final"]:
                final_sentences.append(str(event["text"]))
            else:
                partial_sentence = str(event["text"])

        if errors:
            raise DashScopeASRError(errors[-1])

        audio = b"".join(chunks)
        if save_path is not None:
            import wave

            with wave.open(str(save_path), "wb") as stream:
                stream.setnchannels(channels)
                stream.setsampwidth(sample_width)
                stream.setframerate(self.sample_rate)
                stream.writeframes(audio)

        text = " ".join(item for item in final_sentences if item).strip() or partial_sentence.strip()
        now = time.perf_counter()
        return {
            "text": text,
            "speech_started": speech_started,
            "stop_reason": stop_reason,
            "audio_seconds": round(len(audio) / (self.sample_rate * sample_width * channels), 2),
            "wall_seconds": round(now - started_at, 2),
            "first_partial_seconds": round(first_partial_at - started_at, 2) if first_partial_at else None,
            "last_event_seconds": round(last_event_at - started_at, 2) if last_event_at else None,
            "rms": audioop.rms(audio, sample_width) if audio else 0,
            "peak": peak,
            "frame_rms_max": frame_rms_max,
            "configured_device": device,
            "selected_device": selected_device,
        }


def _sentence_text(sentence: Any) -> str:
    if isinstance(sentence, dict):
        return str(sentence.get("text", "")).strip()
    if isinstance(sentence, list):
        return " ".join(
            str(item.get("text", "")).strip() for item in sentence if isinstance(item, dict)
        ).strip()
    return str(sentence or "").strip()


def _sentence_final(sentence: Any) -> bool:
    if isinstance(sentence, dict):
        return sentence.get("end_time") is not None
    if isinstance(sentence, list):
        return any(isinstance(item, dict) and item.get("end_time") is not None for item in sentence)
    return False
