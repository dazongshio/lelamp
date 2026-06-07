from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal

from .audit import AuditLogger
from .config import OfficeAgentConfig


MEETING_VOICE_COMMANDS_FILE = Path(__file__).with_name("meeting_voice_commands.json")

MeetingVoiceAction = Literal[
    "meeting_status",
    "meeting_provider_status",
    "start_realtime_meeting",
    "stop_realtime_meeting",
    "fetch_realtime_minutes",
    "enable_meeting_mode",
    "disable_meeting_mode",
    "local_realtime_status",
    "export_transcript",
    "generate_minutes",
    "extract_decisions",
    "extract_action_items",
    "create_reminders",
    "create_followup_package",
    "render_projection_confirmation",
    "export_package",
]

MEETING_VOICE_ACTIONS: set[str] = {
    "meeting_status",
    "meeting_provider_status",
    "start_realtime_meeting",
    "stop_realtime_meeting",
    "fetch_realtime_minutes",
    "enable_meeting_mode",
    "disable_meeting_mode",
    "local_realtime_status",
    "export_transcript",
    "generate_minutes",
    "extract_decisions",
    "extract_action_items",
    "create_reminders",
    "create_followup_package",
    "render_projection_confirmation",
    "export_package",
}


@dataclass(frozen=True)
class MeetingVoiceCommand:
    action: MeetingVoiceAction
    label: str
    reply: str
    title: str = ""
    participants: tuple[str, ...] = ()
    max_seconds: int = 7200

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "label": self.label,
            "reply": self.reply,
            "title": self.title,
            "participants": list(self.participants),
            "max_seconds": self.max_seconds,
        }


class MeetingVoiceSkill:
    """Deterministic meeting commands that run locally before any LLM call."""

    def __init__(
        self,
        *,
        config: OfficeAgentConfig,
        audit: AuditLogger,
        executor: Callable[[MeetingVoiceCommand, str], dict[str, object]] | None = None,
    ):
        self.config = config
        self.audit = audit
        self.executor = executor

    def set_executor(self, executor: Callable[[MeetingVoiceCommand, str], dict[str, object]] | None) -> None:
        self.executor = executor

    def handle_text(
        self,
        text: str,
        *,
        executor: Callable[[MeetingVoiceCommand, str], dict[str, object]] | None = None,
    ) -> dict[str, object]:
        command = parse_meeting_voice_command(text)
        if command is None:
            return {"handled": False, "text": text}
        command_executor = executor or self.executor
        if command_executor is None:
            result = {
                "status": "backend_missing",
                "reply": "会议控制服务没有接入。",
                "command": command.as_dict(),
            }
        else:
            result = command_executor(command, text)
        self.audit.record(
            "meeting.voice_command",
            target=text,
            status=str(result.get("status", "unknown")),
            details={k: v for k, v in result.items() if k != "text"},
        )
        return {"handled": True, "text": text, **result}


def execute_runtime_meeting_voice_command(runtime: Any, command: MeetingVoiceCommand, text: str = "") -> dict[str, object]:
    """Best-effort executor for CLI/daemon use where the Web Console Tingwu provider is not attached."""
    try:
        return _execute_runtime_meeting_voice_command(runtime, command)
    except Exception as exc:
        return {
            "status": "blocked",
            "reply": _meeting_voice_error_reply(command, str(exc)),
            "command": command.as_dict(),
            "meeting_result": {"error": str(exc)[:1000]},
            "ai_assistant_kept_online": True,
            "qwen_omni_called": False,
        }


def _execute_runtime_meeting_voice_command(runtime: Any, command: MeetingVoiceCommand) -> dict[str, object]:
    action = command.action
    if action == "meeting_status":
        meeting_status = runtime.meeting.status()
        local_status = runtime.meeting.realtime_summary()
        reply = _runtime_meeting_status_reply(meeting_status, local_status)
        return _runtime_meeting_voice_result(command, reply, {"meeting_mode": meeting_status, "local_realtime": local_status})
    if action == "meeting_provider_status":
        return _runtime_meeting_voice_result(
            command,
            "听悟实时会议服务需要通过 Web Console 检查；当前本地会议控制可用。",
            {"provider": {"status": "web_console_required"}},
            status="backend_missing",
        )
    if action in {"start_realtime_meeting", "stop_realtime_meeting", "fetch_realtime_minutes"}:
        return _runtime_meeting_voice_result(
            command,
            "听悟实时会议的开始、停止和拉取纪要需要通过 Web Console 执行；AI 助手仍保持在线。",
            {"realtime": {"status": "web_console_required"}},
            status="backend_missing",
        )
    if action == "enable_meeting_mode":
        title = command.title or default_meeting_title("LeLamp 本地会议")
        result = runtime.meeting.enable(title, list(command.participants) or ["Unknown"])
        return _runtime_meeting_voice_result(command, command.reply, {"meeting_mode": result})
    if action == "disable_meeting_mode":
        result = runtime.meeting.disable()
        return _runtime_meeting_voice_result(command, command.reply, {"meeting_mode": result})
    if action == "local_realtime_status":
        local_status = runtime.meeting.realtime_summary()
        return _runtime_meeting_voice_result(command, _runtime_local_realtime_reply(local_status), {"local_realtime": local_status})
    if action == "export_transcript":
        result = runtime.meeting.export_transcript()
        return _runtime_meeting_voice_result(command, f"已导出会议转写：{result.get('path') or ''}", {"export": result})
    if action == "generate_minutes":
        result = runtime.meeting.generate_minutes()
        return _runtime_meeting_voice_result(command, f"已生成会议纪要：{result.get('path') or ''}", {"minutes": result})
    if action == "extract_decisions":
        minutes = runtime.meeting.generate_minutes()
        decisions = [str(item) for item in minutes.get("decisions", [])]
        return _runtime_meeting_voice_result(command, f"已提取会议决策：{len(decisions)} 条。", {"decisions": decisions, "source_minutes": minutes})
    if action == "extract_action_items":
        minutes = runtime.meeting.generate_minutes()
        action_items = [str(item) for item in minutes.get("action_items", [])]
        return _runtime_meeting_voice_result(command, f"已提取会议待办：{len(action_items)} 条。", {"action_items": action_items, "source_minutes": minutes})
    if action == "create_reminders":
        minutes = runtime.meeting.generate_minutes()
        action_items = [str(item) for item in minutes.get("action_items", [])]
        reminders = runtime.daily.create_reminders_from_action_items(action_items) if action_items else {"count": 0, "reminders": []}
        return _runtime_meeting_voice_result(command, f"已生成会议提醒草稿：{reminders.get('count') or 0} 条。", {"reminders": reminders, "source_minutes": minutes})
    if action == "create_followup_package":
        result = runtime.p0.generate_meeting_followup_package(
            recipient="待填写收件人",
            create_reminders=True,
            render_projection=True,
        )
        return _runtime_meeting_voice_result(command, "已生成会议跟进包。", {"followup": result}, status=str(result.get("status") or "completed"))
    if action == "render_projection_confirmation":
        minutes = runtime.meeting.generate_minutes()
        result = runtime.projection.render_confirmation(
            str(minutes.get("title") or command.title or "会议确认"),
            [str(item) for item in minutes.get("decisions", [])],
            [str(item) for item in minutes.get("action_items", [])],
        )
        return _runtime_meeting_voice_result(command, "已生成会议投影确认页。", {"projection_confirmation": result, "source_minutes": minutes})
    if action == "export_package":
        return _runtime_meeting_voice_result(
            command,
            "会议材料打包导出需要通过 Web Console 执行。",
            {"export_package": {"status": "web_console_required"}},
            status="backend_missing",
        )
    return _runtime_meeting_voice_result(command, "这个会议命令暂时不支持。", {}, status="unsupported")


def _runtime_meeting_voice_result(
    command: MeetingVoiceCommand,
    reply: str,
    result: dict[str, object],
    *,
    status: str = "completed",
) -> dict[str, object]:
    return {
        "status": status,
        "reply": reply,
        "command": command.as_dict(),
        "meeting_result": result,
        "ai_assistant_kept_online": True,
        "qwen_omni_called": False,
    }


def _meeting_voice_error_reply(command: MeetingVoiceCommand, message: str) -> str:
    if command.action in {"start_realtime_meeting", "stop_realtime_meeting", "fetch_realtime_minutes"}:
        return f"听悟实时会议命令没有完成：{message}。AI 助手仍保持在线。"
    return f"会议命令没有完成：{message}"


def _runtime_meeting_status_reply(meeting_status: dict[str, object], local_status: dict[str, object]) -> str:
    enabled = "开启" if bool(meeting_status.get("meeting_mode_enabled")) else "关闭"
    turns = int(local_status.get("turn_count") or 0)
    return f"本地会议模式{enabled}，已有 {turns} 条转写。AI 助手仍保持在线。"


def _runtime_local_realtime_reply(local_status: dict[str, object]) -> str:
    enabled = "开启" if bool(local_status.get("meeting_mode_enabled")) else "关闭"
    turns = int(local_status.get("turn_count") or 0)
    speakers = local_status.get("speaker_counts") if isinstance(local_status.get("speaker_counts"), dict) else {}
    return f"本地会议模式{enabled}，已有 {turns} 条转写，识别到 {len(speakers)} 个说话人标签。"


def parse_meeting_voice_command(text: str) -> MeetingVoiceCommand | None:
    normalized = _normalize(text)
    if not normalized:
        return None
    command_config = load_meeting_voice_command_config()
    negation = command_config.get("negation") if isinstance(command_config.get("negation"), dict) else {}
    allowed_negations = _markers_from_config(
        negation.get("allowed_markers"),
        ("停止会议", "结束会议", "关闭会议模式", "停止听写", "结束听写", "停止转写", "结束转写"),
    )
    if _is_negated(normalized, command_config) and not _contains_any(normalized, allowed_negations):
        return None

    defaults = command_config.get("defaults") if isinstance(command_config.get("defaults"), dict) else {}
    title = _extract_title(text) or str(defaults.get("title") or "LeLamp 实时会议")
    participants = _extract_participants(text) or _string_list(defaults.get("participants")) or ["Unknown"]
    max_seconds = _extract_max_seconds(text, int(defaults.get("max_seconds") or 7200))

    for item in _list_config_items(command_config.get("commands")):
        markers = _markers_from_config(item.get("markers"))
        if not _contains_any(normalized, markers):
            continue
        action = str(item.get("action") or "")
        if action not in MEETING_VOICE_ACTIONS:
            continue
        return MeetingVoiceCommand(
            action,  # type: ignore[arg-type]
            str(item.get("label") or action),
            str(item.get("reply") or "已执行会议命令。"),
            title=title,
            participants=tuple(participants),
            max_seconds=max_seconds,
        )
    return None


def load_meeting_voice_command_config() -> dict[str, object]:
    path = _meeting_voice_command_config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        if path != MEETING_VOICE_COMMANDS_FILE:
            try:
                payload = json.loads(MEETING_VOICE_COMMANDS_FILE.read_text(encoding="utf-8"))
            except Exception:
                return {}
        else:
            return {}
    return payload if isinstance(payload, dict) else {}


def _meeting_voice_command_config_path() -> Path:
    configured = os.getenv("LELAMP_MEETING_VOICE_COMMANDS_FILE", "").strip()
    return Path(configured).expanduser() if configured else MEETING_VOICE_COMMANDS_FILE


def _normalize(text: str) -> str:
    return (
        text.lower()
        .replace(" ", "")
        .replace("，", "")
        .replace("。", "")
        .replace(",", "")
        .replace(".", "")
        .replace("！", "")
        .replace("!", "")
        .replace("：", ":")
    )


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _is_negated(text: str, command_config: dict[str, object] | None = None) -> bool:
    command_config = command_config or load_meeting_voice_command_config()
    negation = command_config.get("negation") if isinstance(command_config.get("negation"), dict) else {}
    markers = _markers_from_config(negation.get("markers"), ("不要", "别", "先不", "不用", "不要执行"))
    return _contains_any(text, markers)


def _list_config_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _markers_from_config(value: object, fallback: tuple[str, ...] = ()) -> tuple[str, ...]:
    if not isinstance(value, list):
        return tuple(_normalize(item) for item in fallback)
    markers = tuple(_normalize(str(item)) for item in value if str(item).strip())
    return markers or tuple(_normalize(item) for item in fallback)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _extract_title(text: str) -> str:
    for pattern in (
        r"(?:会议标题|会议名称|标题|名称)[:：]\s*([^\n，。；;]{1,80})",
        r"[“\"]([^”\"]{1,80})[”\"]",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _extract_participants(text: str) -> list[str]:
    match = re.search(r"(?:参会人|参与人|成员|与会人)[:：]\s*([^\n。；;]{1,160})", text)
    if not match:
        return []
    raw = match.group(1)
    values = [item.strip() for item in re.split(r"[,，、/ ]+", raw) if item.strip()]
    return values[:20]


def _extract_max_seconds(text: str, fallback: int) -> int:
    match = re.search(r"(\d{1,3})\s*(分钟|分|小时|个小时)", text)
    if not match:
        return max(30, min(8 * 60 * 60, fallback))
    amount = int(match.group(1))
    unit = match.group(2)
    seconds = amount * 3600 if "小时" in unit else amount * 60
    return max(30, min(8 * 60 * 60, seconds))


def default_meeting_title(prefix: str = "LeLamp 实时会议") -> str:
    return f"{prefix} {datetime.now().strftime('%Y-%m-%d %H:%M')}"
