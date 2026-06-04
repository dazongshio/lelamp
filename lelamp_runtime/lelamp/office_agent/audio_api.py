from __future__ import annotations

import json
import mimetypes
import secrets
import urllib.error
import urllib.request
from pathlib import Path


class AudioAPIError(RuntimeError):
    pass


class OpenAIAudioAPI:
    def __init__(self, *, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def transcribe(self, audio_path: Path, *, model: str, language: str | None = None) -> str:
        fields = {"model": model}
        if language:
            fields["language"] = language
        body, content_type = self._multipart_body(fields, {"file": audio_path})
        request = urllib.request.Request(
            f"{self.base_url}/v1/audio/transcriptions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": content_type,
            },
            method="POST",
        )
        payload = self._request_json(request)
        text = payload.get("text")
        if not isinstance(text, str):
            raise AudioAPIError(f"Transcription returned no text: {json.dumps(payload)[:500]}")
        return text.strip()

    def speak(self, text: str, *, model: str, voice: str, output_path: Path) -> Path:
        payload = {
            "model": model,
            "voice": voice,
            "input": text,
            "response_format": "wav",
        }
        request = urllib.request.Request(
            f"{self.base_url}/v1/audio/speech",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                output_path.write_bytes(response.read())
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise AudioAPIError(f"Speech API HTTP {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise AudioAPIError(f"Speech API request failed: {exc}") from exc
        return output_path

    def _request_json(self, request: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise AudioAPIError(f"Audio API HTTP {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise AudioAPIError(f"Audio API request failed: {exc}") from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise AudioAPIError(f"Audio API returned non-JSON body: {body[:500]}") from exc

    def _multipart_body(
        self,
        fields: dict[str, str],
        files: dict[str, Path],
    ) -> tuple[bytes, str]:
        boundary = f"----openclaw-{secrets.token_hex(16)}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        for name, path in files.items():
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    (
                        f'Content-Disposition: form-data; name="{name}"; '
                        f'filename="{path.name}"\r\n'
                    ).encode(),
                    f"Content-Type: {content_type}\r\n\r\n".encode(),
                    path.read_bytes(),
                    b"\r\n",
                ]
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
