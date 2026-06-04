from __future__ import annotations

from http import HTTPStatus
import wave
from pathlib import Path


class DashScopeASRError(RuntimeError):
    pass


class DashScopeASR:
    def __init__(self, *, api_key: str, model: str, sample_rate: int):
        self.api_key = api_key
        self.model = model
        self.sample_rate = sample_rate

    def transcribe(self, audio_path: Path, *, language_hints: list[str] | None = None) -> str:
        if not self.api_key:
            raise DashScopeASRError("DASHSCOPE_API_KEY is not configured.")
        try:
            import dashscope
            from dashscope.audio.asr import Recognition
        except Exception as exc:
            raise DashScopeASRError("dashscope package is not installed.") from exc

        sample_rate = self.sample_rate
        try:
            with wave.open(str(audio_path), "rb") as stream:
                sample_rate = stream.getframerate() or sample_rate
        except Exception:
            pass
        dashscope.api_key = self.api_key
        recognition = Recognition(
            model=self.model,
            format="wav",
            sample_rate=sample_rate,
            language_hints=language_hints or ["zh", "en"],
            callback=None,
        )
        result = recognition.call(str(audio_path))
        if result.status_code != HTTPStatus.OK:
            raise DashScopeASRError(f"DashScope ASR error: {result.message}")
        sentence = result.get_sentence()
        if isinstance(sentence, dict):
            return str(sentence.get("text", "")).strip()
        if isinstance(sentence, list):
            return " ".join(str(item.get("text", "")) for item in sentence if isinstance(item, dict)).strip()
        return str(sentence).strip()
