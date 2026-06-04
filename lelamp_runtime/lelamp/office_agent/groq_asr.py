from __future__ import annotations

from pathlib import Path

from .audio_api import AudioAPIError, OpenAIAudioAPI


class GroqASR:
    def __init__(self, *, api_key: str):
        self.client = OpenAIAudioAPI(api_key=api_key, base_url="https://api.groq.com/openai")

    def transcribe(self, audio_path: Path, *, model: str, language: str | None = None) -> str:
        try:
            return self.client.transcribe(audio_path, model=model, language=language)
        except AudioAPIError:
            raise
