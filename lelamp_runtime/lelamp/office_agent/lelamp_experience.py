from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditLogger
from .projection import ProjectionService
from .scene import SceneService


@dataclass(frozen=True)
class LampStateCue:
    state: str
    rgb: tuple[int, int, int]
    recording: str
    description: str

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "rgb": list(self.rgb),
            "recording": self.recording,
            "description": self.description,
        }


LELAMP_STATE_CUES: dict[str, LampStateCue] = {
    "idle": LampStateCue("idle", (10, 10, 10), "idle", "低亮待机，保持存在感但不打扰。"),
    "wake": LampStateCue("wake", (255, 230, 160), "wake_up", "唤醒确认，灯头抬起看向用户。"),
    "listening": LampStateCue("listening", (36, 126, 255), "scanning", "正在聆听或观察桌面。"),
    "thinking": LampStateCue("thinking", (130, 82, 255), "curious", "正在分析、检索或规划。"),
    "speaking": LampStateCue("speaking", (42, 210, 92), "nod", "正在回答或确认已完成。"),
    "reminder": LampStateCue("reminder", (255, 210, 48), "wake_up", "提醒用户注意待办或倒计时。"),
    "blocked": LampStateCue("blocked", (255, 146, 36), "headshake", "需要用户授权、移动遮挡或补充信息。"),
    "success": LampStateCue("success", (42, 210, 92), "nod", "任务完成。"),
    "error": LampStateCue("error", (255, 48, 42), "shock", "出现错误，需要人工处理。"),
    "meeting": LampStateCue("meeting", (255, 255, 255), "wake_up", "会议模式已启动。"),
    "projecting": LampStateCue("projecting", (80, 180, 255), "idle", "正在投影展示。"),
}


class LeLampExperienceService:
    """LeLamp-specific affordances combining pose, scene events, and projection cards."""

    def __init__(self, *, audit: AuditLogger, scene: SceneService, projection: ProjectionService):
        self.audit = audit
        self.scene = scene
        self.projection = projection

    def capability_map(self) -> dict[str, object]:
        payload = {
            "capabilities": [
                {
                    "name": "expressive_state",
                    "status": "implemented",
                    "description": "Map assistant states to LeLamp RGB and motion recordings.",
                    "states": sorted(LELAMP_STATE_CUES),
                },
                {
                    "name": "desk_scene_events",
                    "status": "adapter_ready",
                    "description": "Turn camera/sensor observations into paper, whiteboard, projection, and meeting events.",
                },
                {
                    "name": "projection_interaction",
                    "status": "implemented",
                    "description": "Render confirmation, countdown, status, and action cards to the projection output directory.",
                },
                {
                    "name": "office_environment_sensing",
                    "status": "adapter_ready",
                    "description": "Infer person nearby, ambient too dark, projection blocked, and meeting-likely-started events from sensors.",
                },
            ]
        }
        self.audit.record("lelamp.capabilities", details={"count": len(payload["capabilities"])})
        return payload

    def state_cue(self, state: str) -> dict[str, object]:
        cue = LELAMP_STATE_CUES.get(state, LELAMP_STATE_CUES["thinking"])
        payload = cue.as_dict()
        self.audit.record("lelamp.state_cue", target=state, details=payload)
        return payload

    def report_affordance_event(
        self,
        event_type: str,
        description: str,
        *,
        confidence: float = 1.0,
        render_card: bool = True,
    ) -> dict[str, object]:
        scene_event = self.scene.report_event(event_type, description, confidence)
        state = state_for_event(event_type, description)
        cue = self.state_cue(state)
        projection = None
        if render_card:
            projection = self.projection.render_status_card(
                title=f"LeLamp Event - {event_type}",
                status=str(scene_event["suggestion"]),
                details=[
                    f"Event: {event_type}",
                    f"Description: {description}",
                    f"Confidence: {scene_event['confidence']}",
                ],
                accent=accent_for_state(state),
            )
        payload = {
            "event": scene_event,
            "recommended_state": state,
            "cue": cue,
            "projection": projection,
        }
        self.audit.record("lelamp.affordance_event", target=event_type, details=payload)
        return payload

    def render_countdown(self, title: str, seconds: int, message: str = "") -> dict[str, object]:
        projection = self.projection.render_countdown(title, seconds, message=message)
        cue = self.state_cue("reminder")
        payload = {"projection": projection, "cue": cue}
        self.audit.record("lelamp.countdown", target=title, details=payload)
        return payload

    def render_action_confirmation(
        self,
        title: str,
        actions: list[str],
        decisions: list[str] | None = None,
    ) -> dict[str, object]:
        projection = self.projection.render_action_card(title, actions, decisions=decisions)
        cue = self.state_cue("projecting")
        payload = {"projection": projection, "cue": cue}
        self.audit.record("lelamp.action_confirmation", target=title, details=payload)
        return payload


def state_for_event(event_type: str, description: str) -> str:
    normalized = f"{event_type} {description}".lower()
    if any(marker in normalized for marker in ["blocked", "遮挡", "too_dark", "太暗", "dark"]):
        return "blocked"
    if any(marker in normalized for marker in ["paper", "document", "whiteboard", "名片", "纸", "白板"]):
        return "listening"
    if any(marker in normalized for marker in ["meeting", "会议", "presentation", "演示"]):
        return "meeting"
    if any(marker in normalized for marker in ["success", "完成"]):
        return "success"
    return "thinking"


def accent_for_state(state: str) -> str:
    return {
        "blocked": "orange",
        "error": "red",
        "success": "green",
        "meeting": "white",
        "projecting": "blue",
        "reminder": "yellow",
    }.get(state, "blue")
