from __future__ import annotations

from .audit import AuditLogger

SCENE_WORKFLOW_VERSION = "2026-05-31"


class SceneService:
    def __init__(self, audit: AuditLogger):
        self.audit = audit
        self.events: list[dict[str, str]] = []

    def report_event(self, event_type: str, description: str, confidence: float = 1.0) -> dict[str, object]:
        suggestion = self._suggest(event_type, description)
        event = {
            "event_type": event_type,
            "description": description,
            "confidence": max(0.0, min(1.0, confidence)),
            "suggestion": suggestion,
        }
        self.events.append({key: str(value) for key, value in event.items()})
        self.audit.record("scene.event", target=event_type, details=event)
        return event

    def get_recent_events(self, limit: int = 10) -> list[dict[str, str]]:
        self.audit.record("scene.recent", details={"limit": limit})
        return self.events[-limit:]

    def workflow_suggestions(self, events: list[dict[str, object]] | None = None, limit: int = 20) -> list[dict[str, object]]:
        source_events = events if events is not None else self.get_recent_events(limit)
        suggestions = workflow_suggestions_from_events(source_events)
        self.audit.record("scene.workflow_suggestions", details={"events": len(source_events), "suggestions": len(suggestions)})
        return suggestions

    def _suggest(self, event_type: str, description: str) -> str:
        normalized = f"{event_type} {description}".lower()
        if event_type in {"desk_observed"}:
            return "暂无自动动作建议，仅记录桌面观察结果。"
        if event_type in {"person_nearby"}:
            return "建议保持低打扰待机；如用户发声或靠近停留，可准备进入聆听状态。"
        if event_type in {"ambient_too_dark"}:
            return "建议提高环境亮度或提示用户调整灯光，以改善阅读、扫描和投影。"
        if event_type in {"ambient_too_bright", "projection_too_bright"}:
            return "建议降低环境光或调整投影亮度/对比度。"
        if event_type in {"meeting_likely_started", "group_conversation"}:
            return "建议询问是否开启会议模式并准备转写。"
        if any(marker in normalized for marker in ["blocked", "遮挡"]):
            return "建议提醒用户调整投影遮挡。"
        if any(marker in normalized for marker in ["paper", "document", "合同", "文件", "纸", "名片"]):
            return "建议启动扫描或导入文档工作区。"
        if any(marker in normalized for marker in ["project", "presentation", "ppt", "演示", "投影"]):
            return "建议进入会议模式并准备投影确认页。"
        if any(marker in normalized for marker in ["whiteboard", "白板"]):
            return "建议拍照归档白板内容并生成待办。"
        return "暂无自动动作建议，仅记录场景事件。"


def workflow_suggestions_from_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    suggestions: dict[str, dict[str, object]] = {}

    def add(
        action: str,
        title: str,
        description: str,
        *,
        trigger: str,
        confidence: float,
        category: str,
        safe_default: str,
        requires_confirmation: bool = True,
        metadata: dict[str, object] | None = None,
    ) -> None:
        existing = suggestions.get(action)
        payload = {
            "action": action,
            "title": title,
            "description": description,
            "trigger": trigger,
            "confidence": round(max(0.0, min(1.0, confidence)), 4),
            "category": category,
            "safe_default": safe_default,
            "requires_confirmation": requires_confirmation,
            "metadata": metadata or {},
        }
        if existing is None or float(payload["confidence"]) > float(existing.get("confidence") or 0):
            suggestions[action] = payload

    for event in events:
        event_type = str(event.get("event_type") or "")
        description = str(event.get("description") or "")
        normalized = f"{event_type} {description}".lower()
        try:
            confidence = float(event.get("confidence") or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5

        explicit_scan_event = event_type in {"paper_detected", "paper_or_screen_detected", "document_detected", "whiteboard_detected"}
        scan_keyword_match = event_type != "desk_observed" and any(
            marker in normalized for marker in ["paper", "document", "合同", "文件", "纸", "名片", "白板"]
        )
        if explicit_scan_event or scan_keyword_match:
            add(
                "scan_document",
                "生成扫描工作流任务",
                "把桌面纸质文件或白板转成待确认的扫描/OCR 工作流任务；不会自动拍照或读取文件。",
                trigger=event_type,
                confidence=confidence,
                category="scan",
                safe_default="create_desktop_task",
                metadata={"event": event},
            )

        if event_type == "projection_blocked" or any(marker in normalized for marker in ["blocked", "遮挡"]):
            add(
                "projection_obstruction_prompt",
                "投影遮挡提示",
                "生成显示器/投影提示卡，提醒用户调整遮挡；不解析投影内容。",
                trigger=event_type,
                confidence=confidence,
                category="projection",
                safe_default="render_projection_status_card",
                metadata={"event": event},
            )

        if event_type in {"meeting_likely_started", "group_conversation"}:
            add(
                "meeting_mode_prompt",
                "建议开启会议模式",
                "把多人/语音/日程信号转换成会议模式确认任务；用户点击后才开启会议理解。",
                trigger=event_type,
                confidence=confidence,
                category="meeting",
                safe_default="enable_meeting_mode_after_click",
                metadata={"event": event},
            )

        if event_type in {"ambient_too_dark", "ambient_too_bright", "projection_too_bright"}:
            add(
                "display_profile_adjustment",
                "调整显示亮度 Profile",
                "根据环境光事件为外接显示器预览生成亮度/对比度 profile。",
                trigger=event_type,
                confidence=confidence,
                category="projection",
                safe_default="digital_display_profile",
                metadata={"event": event},
            )

        if event_type == "desk_idle":
            add(
                "desk_idle_reminder",
                "创建桌面状态提醒",
                "把桌面空闲状态记录成本地 reminder 草稿，便于稍后检查工作区。",
                trigger=event_type,
                confidence=confidence,
                category="reminder",
                safe_default="local_reminder_draft",
                metadata={"event": event},
            )

    return sorted(suggestions.values(), key=lambda item: (-float(item["confidence"]), str(item["action"])))
