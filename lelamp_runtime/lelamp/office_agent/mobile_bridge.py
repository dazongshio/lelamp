from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .audit import AuditLogger


@dataclass(frozen=True)
class MobileBridgeConfig:
    webhook_url: str = ""
    shared_secret: str = ""
    device_id: str = "primary_phone"


class MobileBridgeService:
    """Phone-side companion bridge boundary.

    The desktop runtime cannot directly place calls, send SMS, or ring a phone.
    It can only send an authorized request to a user-configured phone companion
    endpoint. Without that endpoint, all requests remain auditable plans.
    """

    def __init__(self, audit: AuditLogger, config: MobileBridgeConfig):
        self.audit = audit
        self.config = config

    def status(self) -> dict[str, object]:
        payload = {
            "status": "available" if self.config.webhook_url else "needs_config",
            "provider": "webhook" if self.config.webhook_url else "none",
            "configured": bool(self.config.webhook_url),
            "device_id": self.config.device_id,
            "shared_secret_configured": bool(self.config.shared_secret),
            "capabilities": ["call", "sms", "find_phone"],
            "safety": [
                "Requires a phone-side companion app or trusted webhook.",
                "Calls and SMS require explicit authorization.",
                "Payloads are audited; message body is redacted in audit details.",
            ],
        }
        self.audit.record("mobile_bridge.status", details={**payload, "shared_secret_configured": bool(self.config.shared_secret)})
        return payload

    def request(self, text: str, *, authorized: bool = False) -> dict[str, object]:
        parsed = parse_mobile_request(text)
        action = str(parsed.get("action") or "unknown")
        if action in {"call", "sms"} and not authorized:
            payload = {
                "status": "needs_confirmation",
                "request": text,
                "parsed": parsed,
                "message": "Phone call and SMS actions require explicit user authorization.",
            }
            self.audit.record("mobile_bridge.request", status="blocked", target=action, details=_audit_details(payload))
            return payload
        if action == "unknown":
            payload = {
                "status": "blocked",
                "request": text,
                "parsed": parsed,
                "message": "Could not resolve a supported mobile bridge action.",
            }
            self.audit.record("mobile_bridge.request", status="blocked", target=action, details=_audit_details(payload))
            return payload
        if not self.config.webhook_url:
            payload = {
                "status": "needs_config",
                "request": text,
                "parsed": parsed,
                "configure": [
                    "Install a phone companion app or expose a trusted webhook.",
                    "Set OPENCLAW_MOBILE_BRIDGE_WEBHOOK_URL=https://phone-bridge.example/action",
                    "Optionally set OPENCLAW_MOBILE_BRIDGE_SHARED_SECRET for HMAC request signing.",
                ],
                "safety": "The desktop runtime never places calls or sends SMS without a configured phone bridge.",
            }
            self.audit.record("mobile_bridge.request", status="blocked", target=action, details=_audit_details(payload))
            return payload

        return self._post_webhook(text, parsed, authorized=authorized)

    def _post_webhook(self, text: str, parsed: dict[str, object], *, authorized: bool) -> dict[str, object]:
        timestamp = str(int(time.time()))
        body = json.dumps(
            {
                "device_id": self.config.device_id,
                "request": text,
                "parsed": parsed,
                "authorized": authorized,
                "timestamp": timestamp,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-OpenClaw-Device-ID": self.config.device_id,
            "X-OpenClaw-Timestamp": timestamp,
        }
        if self.config.shared_secret:
            signature = hmac.new(self.config.shared_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-OpenClaw-Signature"] = f"sha256={signature}"
        request = urllib.request.Request(self.config.webhook_url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                response_body = response.read().decode("utf-8", "replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            payload = {
                "status": "error",
                "provider": "webhook",
                "parsed": parsed,
                "error": str(exc)[:1000],
            }
            self.audit.record("mobile_bridge.request", status="error", target=str(parsed.get("action") or ""), details=_audit_details(payload))
            return payload
        payload = {
            "status": "sent",
            "provider": "webhook",
            "http_status": getattr(response, "status", None),
            "parsed": parsed,
            "response": response_body[:1000],
            "signed": bool(self.config.shared_secret),
        }
        self.audit.record("mobile_bridge.request", target=str(parsed.get("action") or ""), details=_audit_details(payload))
        return payload


def parse_mobile_request(text: str) -> dict[str, object]:
    normalized = text.lower().strip()
    if any(marker in normalized for marker in ["找手机", "手机在哪", "find phone", "ring phone"]):
        return {"action": "find_phone", "target": "primary_phone"}
    if any(marker in normalized for marker in ["短信", "发消息", "send sms", "text "]):
        recipient = _extract_recipient(text)
        message = _extract_sms_message(text)
        return {
            "action": "sms",
            "recipient": recipient,
            "message": message,
            "message_chars": len(message),
        }
    if any(marker in normalized for marker in ["打电话", "电话", "打给", "call "]):
        return {
            "action": "call",
            "recipient": _extract_recipient(text),
        }
    return {"action": "unknown"}


def _extract_recipient(text: str) -> str:
    phone = re.search(r"(?:\+?\d[\d -]{6,}\d)", text)
    if phone:
        return re.sub(r"\s+", "", phone.group(0))
    match = re.search(r"(?:给|打给|电话给|发短信给|短信给|call)\s*([\w\u4e00-\u9fff +.-]{1,40})", text, re.I)
    if match:
        return match.group(1).strip(" ，。:：")
    return ""


def _extract_sms_message(text: str) -> str:
    patterns = [
        r"(?:内容是|内容为|说|发送)\s*[“\"']?(.+?)[”\"']?$",
        r"(?:sms|text)\s+.+?\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip(" ，。\"'")
    return ""


def _audit_details(payload: dict[str, object]) -> dict[str, object]:
    safe = dict(payload)
    parsed = safe.get("parsed")
    if isinstance(parsed, dict) and "message" in parsed:
        redacted = dict(parsed)
        redacted["message"] = f"[redacted:{len(str(parsed.get('message') or ''))} chars]"
        safe["parsed"] = redacted
    if "request" in safe:
        safe["request"] = str(safe["request"])[:200]
    return safe
