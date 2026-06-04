from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .audit import AuditLogger
from .workspace import Workspace


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime


class LocalDailyService:
    """Local reminders and calendar events for the P0 assistant loop."""

    def __init__(self, workspace: Workspace, audit: AuditLogger):
        self.workspace = workspace
        self.audit = audit
        self.state_dir = workspace.root / ".assistant"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.reminders_path = self.state_dir / "reminders.json"
        self.events_path = self.state_dir / "calendar_events.json"

    def create_reminder(self, text: str, *, remind_at: datetime | None = None) -> dict[str, object]:
        reminders = self._load_list(self.reminders_path)
        parsed_remind_at = remind_at or parse_datetime_from_text(text)
        reminder = {
            "id": str(uuid4()),
            "text": clean_reminder_text(text),
            "remind_at": parsed_remind_at.isoformat() if parsed_remind_at else None,
            "created_at": datetime.now(UTC).isoformat(),
            "status": "pending",
            "source": "local_daily",
        }
        reminders.append(reminder)
        self._write_list(self.reminders_path, reminders)
        self.audit.record("daily.reminder_create", target=reminder["text"], details=reminder)
        return {"reminder": reminder, "store_path": str(self.reminders_path), "count": len(reminders)}

    def list_reminders(self, *, include_done: bool = False) -> dict[str, object]:
        reminders = self._load_list(self.reminders_path)
        visible = reminders if include_done else [item for item in reminders if item.get("status") != "done"]
        visible.sort(key=lambda item: str(item.get("remind_at") or ""))
        payload = {
            "count": len(visible),
            "store_path": str(self.reminders_path),
            "reminders": visible,
        }
        self.audit.record("daily.reminder_list", details={"count": len(visible)})
        return payload

    def create_event(
        self,
        title: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        participants: list[str] | None = None,
        source: str = "manual",
    ) -> dict[str, object]:
        parsed_start = start or parse_datetime_from_text(title) or datetime.now().astimezone()
        parsed_end = end or (parsed_start + timedelta(hours=1))
        events = self._load_list(self.events_path)
        conflicts = self._find_conflicts(parsed_start, parsed_end, events)
        event = {
            "id": str(uuid4()),
            "title": clean_event_title(title),
            "start": parsed_start.isoformat(),
            "end": parsed_end.isoformat(),
            "participants": participants or [],
            "source": source,
            "created_at": datetime.now(UTC).isoformat(),
        }
        events.append(event)
        events.sort(key=lambda item: str(item.get("start") or ""))
        self._write_list(self.events_path, events)
        payload = {
            "event": event,
            "conflicts": conflicts,
            "store_path": str(self.events_path),
        }
        self.audit.record("daily.event_create", target=event["title"], details=payload)
        return payload

    def agenda(self, date: str = "today") -> dict[str, object]:
        target_date = resolve_date(date)
        events = []
        for item in self._load_list(self.events_path):
            try:
                start = datetime.fromisoformat(str(item.get("start"))).astimezone()
            except (TypeError, ValueError):
                continue
            if start.date() == target_date.date():
                events.append(
                    {
                        "time": start.strftime("%H:%M"),
                        "title": item.get("title", ""),
                        "source": item.get("source", "local_event"),
                        "id": item.get("id"),
                        "end": item.get("end"),
                        "participants": item.get("participants", []),
                    }
                )

        reminders = []
        for item in self._load_list(self.reminders_path):
            raw_time = item.get("remind_at")
            if not raw_time:
                continue
            try:
                remind_at = datetime.fromisoformat(str(raw_time)).astimezone()
            except ValueError:
                continue
            if remind_at.date() == target_date.date() and item.get("status") != "done":
                reminders.append(
                    {
                        "time": remind_at.strftime("%H:%M"),
                        "title": item.get("text", ""),
                        "source": "local_reminder",
                        "id": item.get("id"),
                    }
                )

        payload = {
            "date": target_date.date().isoformat(),
            "events": sorted(events, key=lambda item: item["time"]),
            "reminders": sorted(reminders, key=lambda item: item["time"]),
            "store_paths": {
                "events": str(self.events_path),
                "reminders": str(self.reminders_path),
            },
        }
        self.audit.record("daily.agenda", target=payload["date"], details=payload)
        return payload

    def create_reminders_from_action_items(self, action_items: list[str]) -> dict[str, object]:
        created = []
        for item in action_items:
            text = re.sub(r"^[^:：]{1,40}[:：]\s*", "", item).strip() or item
            created.append(self.create_reminder(f"跟进会议待办：{text}")["reminder"])
        if not created:
            self._write_list(self.reminders_path, self._load_list(self.reminders_path))
        payload = {"count": len(created), "reminders": created, "store_path": str(self.reminders_path)}
        self.audit.record("daily.reminders_from_actions", details={"count": len(created)})
        return payload

    def _find_conflicts(self, start: datetime, end: datetime, events: list[dict[str, object]]) -> list[dict[str, object]]:
        conflicts: list[dict[str, object]] = []
        target = TimeWindow(start=start, end=end)
        for item in events:
            try:
                existing = TimeWindow(
                    start=datetime.fromisoformat(str(item.get("start"))).astimezone(),
                    end=datetime.fromisoformat(str(item.get("end"))).astimezone(),
                )
            except (TypeError, ValueError):
                continue
            if target.start < existing.end and existing.start < target.end:
                conflicts.append(
                    {
                        "id": item.get("id"),
                        "title": item.get("title"),
                        "start": item.get("start"),
                        "end": item.get("end"),
                    }
                )
        return conflicts

    def _load_list(self, path: Path) -> list[dict[str, object]]:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return payload if isinstance(payload, list) else []

    def _write_list(self, path: Path, payload: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_date(value: str) -> datetime:
    now = datetime.now().astimezone()
    normalized = value.lower()
    if normalized in {"tomorrow", "明天"}:
        return now + timedelta(days=1)
    if normalized in {"day_after_tomorrow", "后天"}:
        return now + timedelta(days=2)
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if match:
        return now.replace(
            year=int(match.group(1)),
            month=int(match.group(2)),
            day=int(match.group(3)),
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    return now


def parse_datetime_from_text(text: str) -> datetime | None:
    now = datetime.now().astimezone()
    base = now
    if "后天" in text:
        base = now + timedelta(days=2)
    elif "明天" in text:
        base = now + timedelta(days=1)
    elif "今天" in text:
        base = now

    relative = re.search(r"(\d+)\s*(分钟|小时|天)后", text)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        if unit == "分钟":
            return now + timedelta(minutes=amount)
        if unit == "小时":
            return now + timedelta(hours=amount)
        return now + timedelta(days=amount)

    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*(\d{1,2})?[:：点]?(\d{1,2})?", text)
    if match:
        hour = int(match.group(4) or 9)
        minute = int(match.group(5) or 0)
        return now.replace(
            year=int(match.group(1)),
            month=int(match.group(2)),
            day=int(match.group(3)),
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )

    time_match = re.search(r"(\d{1,2})(?:[:：点](\d{1,2})?)?", text)
    if not time_match:
        return None
    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if "明天" not in text and "后天" not in text and candidate < now:
        candidate += timedelta(days=1)
    return candidate


def clean_reminder_text(text: str) -> str:
    cleaned = re.sub(r"(帮我|请|设置|创建|添加|一个|提醒|闹钟|定时|到时候|记得)", "", text).strip()
    cleaned = re.sub(r"(今天|明天|后天)?\s*\d{1,2}[:：点]\d{0,2}", "", cleaned).strip()
    cleaned = re.sub(r"(\d+)\s*(分钟|小时|天)后", "", cleaned).strip()
    cleaned = re.sub(r"^(我|我需要|我要)", "", cleaned).strip()
    return cleaned or text


def clean_event_title(text: str) -> str:
    cleaned = re.sub(r"(帮我|请|安排|创建|添加|日程|日历|会议|开会)", " ", text).strip()
    cleaned = re.sub(r"(今天|明天|后天)?\s*\d{1,2}[:：点]\d{0,2}", " ", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or text
