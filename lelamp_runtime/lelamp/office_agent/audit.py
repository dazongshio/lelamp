from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def _normalize_key(key: object) -> str:
    text = str(key or "").strip()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    return text.strip("_").lower()


def _sensitive_key(key: object) -> bool:
    normalized = _normalize_key(key)
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


def _redact_text(value: object) -> str:
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


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            clean[key_text] = "[redacted]" if _sensitive_key(key_text) else _sanitize(item)
        return clean
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


class AuditLogger:
    """Append-only JSONL audit log for agent actions."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        action: str,
        *,
        status: str = "ok",
        target: str | None = None,
        details: dict[str, Any] | None = None,
        actor: str | None = None,
        request_id: str | None = None,
        source_ip: str | None = None,
        permission_mode: str | None = None,
        desktop_backend: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "id": str(uuid4()),
            "timestamp": datetime.now(UTC).isoformat(),
            "action": _redact_text(action),
            "status": _redact_text(status),
            "target": _redact_text(target) if target is not None else None,
            "details": _sanitize(details or {}),
        }
        if actor is not None:
            event["actor"] = _redact_text(actor)
        if request_id is not None:
            event["request_id"] = _redact_text(request_id)
        if source_ip is not None:
            event["source_ip"] = source_ip
        if permission_mode is not None:
            event["permission_mode"] = _redact_text(permission_mode)
        if desktop_backend is not None:
            event["desktop_backend"] = _redact_text(desktop_backend)

        with self._lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
                stream.write("\n")

        return event
