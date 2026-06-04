from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .audit import AuditLogger
from .scene import SceneService


@dataclass(frozen=True)
class EnvironmentReading:
    presence: bool | None = None
    motion: bool | None = None
    lux: float | None = None
    sound_level: float | None = None
    speech_active: bool | None = None
    people_count: int | None = None
    projector_blocked: bool | None = None
    calendar_event_now: bool | None = None


class EnvironmentSensingService:
    """Infer office events from privacy-friendly sensor readings."""

    def __init__(self, *, audit: AuditLogger, scene: SceneService):
        self.audit = audit
        self.scene = scene

    def ingest(self, reading: EnvironmentReading | dict[str, Any]) -> dict[str, object]:
        if isinstance(reading, dict):
            normalized = reading_from_dict(reading)
        else:
            normalized = reading
        events = environment_events(normalized)
        reported = [
            self.scene.report_event(event["event_type"], event["description"], float(event["confidence"]))
            for event in events
        ]
        payload = {
            "reading": normalized.__dict__,
            "events": reported,
            "event_count": len(reported),
        }
        self.audit.record("environment.ingest", details={"event_count": len(reported), "reading": normalized.__dict__})
        return payload


def reading_from_dict(payload: dict[str, Any]) -> EnvironmentReading:
    return EnvironmentReading(
        presence=_optional_bool(payload.get("presence")),
        motion=_optional_bool(payload.get("motion")),
        lux=_optional_float(payload.get("lux")),
        sound_level=_optional_float(payload.get("sound_level")),
        speech_active=_optional_bool(payload.get("speech_active")),
        people_count=_optional_int(payload.get("people_count")),
        projector_blocked=_optional_bool(payload.get("projector_blocked")),
        calendar_event_now=_optional_bool(payload.get("calendar_event_now")),
    )


def environment_events(reading: EnvironmentReading) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    if reading.presence is True or (reading.people_count is not None and reading.people_count > 0):
        events.append(
            {
                "event_type": "person_nearby",
                "description": "检测到有人靠近 LeLamp 工作区。",
                "confidence": 0.82,
            }
        )
    if reading.lux is not None and reading.lux < 80:
        events.append(
            {
                "event_type": "ambient_too_dark",
                "description": f"环境照度偏低：{reading.lux:g} lux，可能影响阅读、扫描或投影。",
                "confidence": 0.78,
            }
        )
    if reading.lux is not None and reading.lux > 900:
        events.append(
            {
                "event_type": "ambient_too_bright",
                "description": f"环境照度偏高：{reading.lux:g} lux，可能影响投影可读性。",
                "confidence": 0.7,
            }
        )
    if reading.projector_blocked is True:
        events.append(
            {
                "event_type": "projection_blocked",
                "description": "投影路径可能被遮挡。",
                "confidence": 0.86,
            }
        )
    if reading.calendar_event_now and (reading.speech_active or (reading.people_count or 0) >= 2):
        events.append(
            {
                "event_type": "meeting_likely_started",
                "description": "当前有日程且检测到多人/语音活动，可能已经开始会议。",
                "confidence": 0.84,
            }
        )
    elif reading.speech_active and (reading.people_count or 0) >= 2:
        events.append(
            {
                "event_type": "group_conversation",
                "description": "检测到多人语音活动，可询问是否开启会议模式。",
                "confidence": 0.72,
            }
        )
    if not events:
        events.append(
            {
                "event_type": "desk_idle",
                "description": "暂无明确环境事件。",
                "confidence": 0.45,
            }
        )
    return events


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on", "是", "有"}
    return bool(value)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
