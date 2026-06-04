from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path


class ElevenLabsError(RuntimeError):
    pass


class ElevenLabsTTS:
    def __init__(self, *, api_key: str, voice_id: str, model_id: str):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id

    def speak(self, text: str, output_path: Path) -> Path:
        if not self.api_key:
            raise ElevenLabsError("ELEVENLABS_API_KEY is not configured.")
        payload = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": False,
            },
        }
        request = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}?output_format=pcm_16000",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "xi-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "audio/wav",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw_audio = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ElevenLabsError(f"ElevenLabs HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ElevenLabsError(f"ElevenLabs request failed: {exc}") from exc

        self._write_pcm_as_wav(raw_audio, output_path)
        return output_path

    def list_voices(self) -> list[dict[str, str]]:
        if not self.api_key:
            raise ElevenLabsError("ELEVENLABS_API_KEY is not configured.")
        request = urllib.request.Request(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": self.api_key},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ElevenLabsError(f"ElevenLabs HTTP {exc.code}: {body}") from exc
        voices = []
        for item in payload.get("voices", []):
            voices.append(
                {
                    "voice_id": str(item.get("voice_id", "")),
                    "name": str(item.get("name", "")),
                    "category": str(item.get("category", "")),
                }
            )
        return voices

    def _write_pcm_as_wav(self, pcm_audio: bytes, output_path: Path) -> None:
        import wave

        with wave.open(str(output_path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(pcm_audio)
