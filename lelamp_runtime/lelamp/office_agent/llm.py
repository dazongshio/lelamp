from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class LLMError(RuntimeError):
    pass


class LLMHTTPError(LLMError):
    def __init__(self, message: str, *, status_code: int, body: str):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class ResponsesLLMConfig:
    api_key: str
    base_url: str
    model: str
    reasoning_effort: str = "xhigh"
    wire_api: str = "responses"


class ResponsesLLM:
    """Small Responses API client using only the Python standard library."""

    def __init__(self, config: ResponsesLLMConfig):
        self.config = config

    def complete(
        self,
        *,
        instructions: str,
        user_input: str,
        context: dict[str, Any] | None = None,
        timeout: float = 120.0,
    ) -> str:
        if not self.config.api_key:
            raise LLMError("OPENAI_API_KEY is not configured.")

        payload: dict[str, Any] = {
            "model": self.config.model,
            "instructions": instructions,
            "input": self._build_input(user_input, context or {}),
            "reasoning": {"effort": self.config.reasoning_effort},
            "store": False,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            _join_api_path(self.config.base_url, "/v1/responses"),
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        body = self._request_text(request, timeout=timeout, api_name="Responses API")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Responses API returned non-JSON body: {body[:500]}") from exc

        text = self._extract_output_text(parsed)
        if not text:
            raise LLMError(f"Responses API returned no output text: {json.dumps(parsed)[:1000]}")
        return text

    def complete_multimodal(
        self,
        *,
        instructions: str,
        text: str,
        image_data_url: str,
        context: dict[str, Any] | None = None,
        timeout: float = 120.0,
    ) -> str:
        if not self.config.api_key:
            raise LLMError("OPENAI_API_KEY is not configured.")
        if self.config.wire_api == "chat_completions":
            return self._complete_multimodal_chat_completions(
                instructions=instructions,
                text=text,
                image_data_url=image_data_url,
                context=context or {},
                timeout=timeout,
                previous_error="configured_wire_api=chat_completions",
            )

        content: list[dict[str, Any]] = [{"type": "input_text", "text": self._build_input(text, context or {})}]
        content.append({"type": "input_image", "image_url": image_data_url})
        payload: dict[str, Any] = {
            "model": self.config.model,
            "instructions": instructions,
            "input": [{"role": "user", "content": content}],
            "reasoning": {"effort": self.config.reasoning_effort},
            "store": False,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            _join_api_path(self.config.base_url, "/v1/responses"),
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            body = self._request_text(request, timeout=timeout, api_name="Responses API")
            parsed = json.loads(body)
            output_text = self._extract_output_text(parsed)
            if not output_text:
                raise LLMError(f"Responses API returned no output text: {json.dumps(parsed)[:1000]}")
            return output_text
        except json.JSONDecodeError as exc:
            raise LLMError(f"Responses API returned non-JSON body: {body[:500]}") from exc
        except LLMHTTPError as exc:
            if exc.status_code not in {400, 404, 429, 500, 502, 503, 504}:
                raise
            return self._complete_multimodal_chat_completions(
                instructions=instructions,
                text=text,
                image_data_url=image_data_url,
                context=context or {},
                timeout=timeout,
                previous_error=str(exc),
            )

    def _build_input(self, user_input: str, context: dict[str, Any]) -> str:
        if not context:
            return user_input
        return "\n\n".join(
            [
                user_input,
                "Local OpenClaw tool/context state:",
                json.dumps(context, ensure_ascii=False, indent=2),
            ]
        )

    def _extract_output_text(self, payload: dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"]

        parts: list[str] = []
        for item in payload.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                if isinstance(content.get("text"), str):
                    parts.append(content["text"])
        return "\n".join(parts).strip()

    def _complete_multimodal_chat_completions(
        self,
        *,
        instructions: str,
        text: str,
        image_data_url: str,
        context: dict[str, Any],
        timeout: float,
        previous_error: str,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": instructions},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self._build_input(text, {**context, "responses_api_fallback_reason": previous_error[:500]})},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            _join_api_path(self.config.base_url, "/v1/chat/completions"),
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        body = self._request_text(request, timeout=timeout, api_name="Chat Completions API")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Chat Completions API returned non-JSON body: {body[:500]}") from exc
        text_parts: list[str] = []
        for choice in parsed.get("choices", []):
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        text_parts.append(item["text"])
        output_text = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
        if not output_text:
            raise LLMError(f"Chat Completions API returned no output text: {json.dumps(parsed)[:1000]}")
        return output_text

    def _request_text(self, request: urllib.request.Request, *, timeout: float, api_name: str, retries: int = 2) -> str:
        last_error: LLMError | None = None
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                last_error = LLMHTTPError(f"{api_name} HTTP {exc.code}: {error_body}", status_code=exc.code, body=error_body)
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= retries:
                    raise last_error from exc
            except urllib.error.URLError as exc:
                last_error = LLMError(f"{api_name} request failed: {exc}")
                if attempt >= retries:
                    raise last_error from exc
            time.sleep(min(0.75 * (attempt + 1), 2.0))
        raise last_error or LLMError(f"{api_name} request failed.")


def _join_api_path(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    normalized_path = "/" + path.lstrip("/")
    if base.endswith("/v1") and normalized_path.startswith("/v1/"):
        return f"{base}{normalized_path[3:]}"
    return f"{base}{normalized_path}"
