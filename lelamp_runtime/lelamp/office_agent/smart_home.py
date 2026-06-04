from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .audit import AuditLogger


@dataclass(frozen=True)
class SmartHomeConfig:
    provider: str = "none"
    home_assistant_url: str = ""
    home_assistant_token: str = ""
    webhook_url: str = ""
    entity_map: dict[str, str] | None = None


class SmartHomeService:
    """Configurable smart-home bridge.

    Xiaomi/Mi Home device control is intentionally not hard-coded here because
    public account/device APIs vary by region and auth method. Home Assistant
    and webhook bridges give the desktop assistant a stable API boundary.
    """

    def __init__(self, audit: AuditLogger, config: SmartHomeConfig):
        self.audit = audit
        self.config = config
        self.entity_map = config.entity_map or {}

    def status(self) -> dict[str, object]:
        home_assistant_configured = bool(self.config.home_assistant_url and self.config.home_assistant_token)
        webhook_configured = bool(self.config.webhook_url)
        payload = {
            "status": "available" if (home_assistant_configured or webhook_configured) else "needs_config",
            "provider": self.config.provider,
            "configured": home_assistant_configured or webhook_configured,
            "home_assistant_configured": home_assistant_configured,
            "webhook_configured": webhook_configured,
            "known_entities": sorted(self.entity_map),
            "capabilities": ["turn_on", "turn_off", "set_temperature", "brightness_pct", "media_pause", "return_to_base"],
        }
        self.audit.record("smart_home.status", details=payload)
        return payload

    def control(self, command: str, *, entity_name: str | None = None) -> dict[str, object]:
        parsed = parse_smart_home_command(command, self.entity_map, entity_name=entity_name)
        if self.config.webhook_url:
            return self._post_webhook(command, parsed)
        if self.config.home_assistant_url and self.config.home_assistant_token:
            return self._call_home_assistant(command, parsed)

        payload = {
            "status": "needs_config",
            "command": command,
            "parsed": parsed,
            "configure": {
                "home_assistant": [
                    "OPENCLAW_SMART_HOME_PROVIDER=home_assistant",
                    "OPENCLAW_HOME_ASSISTANT_URL=http://homeassistant.local:8123",
                    "OPENCLAW_HOME_ASSISTANT_TOKEN=<long-lived-token>",
                    "OPENCLAW_SMART_HOME_ENTITIES={\"客厅灯\":\"light.living_room\"}",
                ],
                "webhook": [
                    "OPENCLAW_SMART_HOME_WEBHOOK_URL=https://your-bridge.example/control",
                ],
            },
        }
        self.audit.record("smart_home.control", status="blocked", target=command, details=payload)
        return payload

    def _post_webhook(self, command: str, parsed: dict[str, object]) -> dict[str, object]:
        body = json.dumps({"command": command, "parsed": parsed}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.config.webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                response_body = response.read().decode("utf-8", "replace")
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            payload = {"status": "error", "provider": "webhook", "error": str(exc), "parsed": parsed}
            self.audit.record("smart_home.control", status="error", target=command, details=payload)
            return payload
        payload = {
            "status": "sent",
            "provider": "webhook",
            "http_status": getattr(response, "status", None),
            "response": response_body[:1000],
            "parsed": parsed,
        }
        self.audit.record("smart_home.control", target=command, details=payload)
        return payload

    def _call_home_assistant(self, command: str, parsed: dict[str, object]) -> dict[str, object]:
        entity_id = str(parsed.get("entity_id") or "")
        service = str(parsed.get("service") or "")
        domain = str(parsed.get("domain") or "")
        if not entity_id or not service or not domain:
            payload = {
                "status": "blocked",
                "provider": "home_assistant",
                "reason": "entity or service could not be resolved",
                "parsed": parsed,
            }
            self.audit.record("smart_home.control", status="blocked", target=command, details=payload)
            return payload

        url = f"{self.config.home_assistant_url.rstrip('/')}/api/services/{domain}/{service}"
        data: dict[str, Any] = {"entity_id": entity_id}
        if parsed.get("temperature") is not None:
            data["temperature"] = parsed["temperature"]
        if parsed.get("brightness_pct") is not None:
            data["brightness_pct"] = parsed["brightness_pct"]
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.config.home_assistant_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                response_body = response.read().decode("utf-8", "replace")
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            payload = {
                "status": "error",
                "provider": "home_assistant",
                "error": str(exc),
                "parsed": parsed,
            }
            self.audit.record("smart_home.control", status="error", target=command, details=payload)
            return payload

        payload = {
            "status": "sent",
            "provider": "home_assistant",
            "http_status": getattr(response, "status", None),
            "service": f"{domain}.{service}",
            "data": data,
            "response": response_body[:1000],
        }
        self.audit.record("smart_home.control", target=command, details=payload)
        return payload


def parse_entity_map(raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if key and value}


def parse_smart_home_command(
    command: str,
    entity_map: dict[str, str],
    *,
    entity_name: str | None = None,
) -> dict[str, object]:
    text = command.strip().lower()
    resolved_name = entity_name or _find_entity_name(command, entity_map)
    entity_id = entity_map.get(resolved_name or "", "")
    domain = entity_id.split(".", 1)[0] if "." in entity_id else _infer_domain(command)
    action = _infer_action(text)
    service = _service_for(domain, action)
    temperature = _extract_temperature(command)
    brightness_pct = _extract_brightness(command)
    if domain == "climate" and temperature is not None and action in {"turn_on", "set"}:
        service = "set_temperature"
    if domain == "light" and brightness_pct is not None and action in {"turn_on", "set"}:
        service = "turn_on"
    return {
        "entity_name": resolved_name,
        "entity_id": entity_id,
        "domain": domain,
        "action": action,
        "service": service,
        "temperature": temperature,
        "brightness_pct": brightness_pct,
    }


def _find_entity_name(command: str, entity_map: dict[str, str]) -> str | None:
    for name in sorted(entity_map, key=len, reverse=True):
        if name and name in command:
            return name
    match = re.search(r"(?:打开|开启|关闭|关掉|启动|暂停|停止|调高|调低)\s*([\w\u4e00-\u9fff -]{1,24})", command)
    if match:
        return match.group(1).strip()
    return None


def _infer_domain(command: str) -> str:
    if any(marker in command for marker in ["灯", "台灯", "吸顶灯"]):
        return "light"
    if any(marker in command for marker in ["空调", "恒温器", "温度"]):
        return "climate"
    if any(marker in command for marker in ["扫地", "机器人", "吸尘"]):
        return "vacuum"
    if any(marker in command for marker in ["窗帘", "卷帘"]):
        return "cover"
    if any(marker in command for marker in ["电视", "音箱", "播放器"]):
        return "media_player"
    if any(marker in command for marker in ["风扇", "净化器"]):
        return "fan"
    return "switch"


def _infer_action(text: str) -> str:
    if any(marker in text for marker in ["关闭", "关掉", "关上", "turn off"]):
        return "turn_off"
    if any(marker in text for marker in ["暂停", "pause"]):
        return "pause"
    if any(marker in text for marker in ["停止", "stop"]):
        return "stop"
    if any(marker in text for marker in ["回充", "回去充电", "dock"]):
        return "return_to_base"
    if any(marker in text for marker in ["打开", "开启", "启动", "turn on", "start"]):
        return "turn_on"
    if any(marker in text for marker in ["调到", "设置", "调成", "set"]):
        return "set"
    return "turn_on"


def _service_for(domain: str, action: str) -> str:
    if domain == "vacuum":
        return {"turn_on": "start", "turn_off": "stop", "stop": "stop", "return_to_base": "return_to_base"}.get(
            action,
            "start",
        )
    if domain == "cover":
        return {"turn_on": "open_cover", "turn_off": "close_cover", "stop": "stop_cover"}.get(
            action,
            "open_cover",
        )
    if domain == "media_player":
        return {"turn_on": "turn_on", "turn_off": "turn_off", "pause": "media_pause", "stop": "media_stop"}.get(
            action,
            "turn_on",
        )
    return {"turn_on": "turn_on", "turn_off": "turn_off", "set": "turn_on"}.get(action, "turn_on")


def _extract_temperature(command: str) -> int | None:
    match = re.search(r"(-?\d{1,2})\s*(?:度|℃|摄氏度)", command)
    if not match:
        return None
    value = int(match.group(1))
    if value < 10 or value > 35:
        return None
    return value


def _extract_brightness(command: str) -> int | None:
    match = re.search(r"(\d{1,3})\s*%", command)
    if not match:
        match = re.search(r"(?:亮度|调到|设置到)\s*(\d{1,3})", command)
    if not match:
        return None
    value = max(1, min(100, int(match.group(1))))
    return value
