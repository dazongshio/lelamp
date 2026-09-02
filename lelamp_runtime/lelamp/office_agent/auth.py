from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import urllib.parse
from typing import Any


class ConsoleAuth:
    """Token, scoped document-session and same-origin policy for the console."""

    def __init__(self, token: str):
        self.token = token

    def authorized(self, parsed: urllib.parse.ParseResult, headers: Any, method: str = "GET") -> bool:
        if not self.token:
            return True
        params = urllib.parse.parse_qs(parsed.query)
        auth = str(headers.get("Authorization") or "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        provided = bearer or str(headers.get("X-OpenClaw-Console-Token") or "").strip() or params.get("token", [""])[0].strip()
        try:
            if hmac.compare_digest(provided, self.token):
                return True
        except TypeError:
            pass
        session_token = str(headers.get("X-LeLamp-Document-Session") or "").strip() or params.get("document_session", [""])[0].strip()
        session = self.document_session_payload(session_token)
        if not session:
            return False
        if parsed.path.startswith("/api/docs/"):
            requested_id = parsed.path.removeprefix("/api/docs/").split("/", 1)[0]
            return requested_id == str(session.get("document_id") or "")
        return method == "GET" and parsed.path in {"/documents", "/api/docs", "/api/docs/search", "/api/docs/stats"}

    def document_session_payload(self, token: str) -> dict[str, object] | None:
        try:
            encoded, signature = token.split(".", 1)
            expected = hmac.new(self.token.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return None
            padding = "=" * ((4 - len(encoded) % 4) % 4)
            payload = json.loads(base64.urlsafe_b64decode(f"{encoded}{padding}").decode("utf-8"))
            if not isinstance(payload, dict) or int(payload.get("exp") or 0) < int(time.time()):
                return None
            if not re.fullmatch(r"[a-f0-9]{32}", str(payload.get("document_id") or "")):
                return None
            if not str(payload.get("actor_id") or "").strip():
                return None
            return payload
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeError):
            return None

    @staticmethod
    def trusted_write_origin(headers: Any, host: str) -> bool:
        origin = str(headers.get("Origin") or "").strip()
        if not origin:
            return True
        if origin == "null":
            return False
        try:
            parsed = urllib.parse.urlparse(origin)
        except ValueError:
            return False
        return parsed.scheme in {"http", "https"} and parsed.netloc.casefold() == str(host or "").casefold()
