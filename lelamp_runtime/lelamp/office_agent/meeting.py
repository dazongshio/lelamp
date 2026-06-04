from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .audit import AuditLogger
from .workspace import Workspace
from .utils import safe_filename


@dataclass
class MeetingSession:
    title: str
    participants: list[str]
    started_at: str
    transcript: list[dict[str, str]] = field(default_factory=list)
    active: bool = True


class MeetingService:
    def __init__(self, workspace: Workspace, audit: AuditLogger, *, enabled: bool = False):
        self.workspace = workspace
        self.audit = audit
        self._session: MeetingSession | None = None
        self._meeting_mode_enabled = enabled

    def enable(self, title: str, participants: list[str]) -> dict[str, object]:
        self._meeting_mode_enabled = True
        self._session = MeetingSession(
            title=title,
            participants=participants,
            started_at=datetime.now(UTC).isoformat(),
        )
        self.audit.record(
            "meeting.enable",
            details={"title": title, "participants": participants},
        )
        return self.status()

    def disable(self) -> dict[str, object]:
        if self._session is not None:
            self._session.active = False
        self._meeting_mode_enabled = False
        self.audit.record("meeting.disable")
        return self.status()

    def append_transcript(self, speaker: str, text: str) -> dict[str, object]:
        if not self._meeting_mode_enabled or self._session is None:
            self.audit.record(
                "meeting.transcript_append",
                status="blocked",
                details={"reason": "meeting mode disabled"},
            )
            raise PermissionError("Meeting mode is disabled. Enable it before recording transcripts.")

        item = {
            "timestamp": datetime.now(UTC).isoformat(),
            "speaker": speaker,
            "text": text,
        }
        self._session.transcript.append(item)
        self.audit.record(
            "meeting.transcript_append",
            details={"speaker": speaker, "chars": len(text)},
        )
        return item

    def realtime_summary(self) -> dict[str, object]:
        transcript = self._session.transcript if self._session else []
        speaker_counts: dict[str, int] = {}
        char_counts: dict[str, int] = {}
        for item in transcript:
            speaker = item.get("speaker", "Unknown")
            speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1
            char_counts[speaker] = char_counts.get(speaker, 0) + len(item.get("text", ""))
        payload = {
            "meeting_mode_enabled": self._meeting_mode_enabled,
            "active_title": self._session.title if self._session else None,
            "participants": self._session.participants if self._session else [],
            "turn_count": len(transcript),
            "speaker_counts": speaker_counts,
            "char_counts": char_counts,
            "transcript": transcript[-80:],
        }
        self.audit.record("meeting.realtime_summary", details={"turn_count": len(transcript), "speakers": len(speaker_counts)})
        return payload

    def generate_minutes(self) -> dict[str, object]:
        if self._session is None:
            raise ValueError("No meeting session is active.")

        title = self._session.title
        transcript = self._session.transcript
        speaker_counts: dict[str, int] = {}
        action_items: list[str] = []
        decisions: list[str] = []

        for item in transcript:
            speaker_counts[item["speaker"]] = speaker_counts.get(item["speaker"], 0) + 1
            lower_text = item["text"].lower()
            if any(marker in lower_text for marker in ["todo", "action", "follow up", "负责", "待办"]):
                action_items.append(f"{item['speaker']}: {item['text']}")
            if any(marker in lower_text for marker in ["decide", "decision", "agreed", "决定", "确认"]):
                decisions.append(f"{item['speaker']}: {item['text']}")

        lines = [
            f"# {title}",
            "",
            f"Started: {self._session.started_at}",
            "",
            "## Participants",
            *[f"- {participant}" for participant in self._session.participants],
            "",
            "## Speaker Turns",
            *[f"- {speaker}: {count}" for speaker, count in sorted(speaker_counts.items())],
            "",
            "## Decisions",
            *([f"- {item}" for item in decisions] or ["- 暂无明确决策，需要人工补充。"]),
            "",
            "## Action Items",
            *([f"- {item}" for item in action_items] or ["- 暂无明确待办，需要人工补充。"]),
            "",
            "## Transcript",
            *[f"- [{item['timestamp']}] {item['speaker']}: {item['text']}" for item in transcript],
            "",
        ]
        filename = safe_filename(title, default="meeting", suffix="_minutes.md")
        path = self.workspace.write_text(
            filename,
            "\n".join(lines),
            action="meeting.minutes_generate",
        )
        payload = {
            "path": str(path),
            "title": title,
            "turn_count": len(transcript),
            "speaker_counts": speaker_counts,
            "decisions": decisions,
            "action_items": action_items,
        }
        self.audit.record("meeting.minutes_generated", target=str(path), details=payload)
        return payload

    def export_transcript(self) -> dict[str, str]:
        if self._session is None:
            raise ValueError("No meeting session is active.")
        filename = safe_filename(self._session.title, default="meeting", suffix="_transcript.json")
        path = self.workspace.write_json(
            filename,
            {
                "title": self._session.title,
                "participants": self._session.participants,
                "started_at": self._session.started_at,
                "transcript": self._session.transcript,
            },
            action="meeting.transcript_export",
        )
        return {"path": str(path)}

    def status(self) -> dict[str, object]:
        return {
            "meeting_mode_enabled": self._meeting_mode_enabled,
            "active_title": self._session.title if self._session else None,
            "participants": self._session.participants if self._session else [],
            "turn_count": len(self._session.transcript) if self._session else 0,
        }

    def parse_transcript_file(self, filename: str, title: str, participants: list[str]) -> dict[str, object]:
        text = self.workspace.read_text(filename, max_chars=100000)
        self.enable(title, participants)

        parsed_count = 0
        try:
            data = json.loads(text)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        speaker = str(item.get("speaker", "Unknown"))
                        value = str(item.get("text", ""))
                        if value:
                            self.append_transcript(speaker, value)
                            parsed_count += 1
            elif isinstance(data, dict) and isinstance(data.get("transcript"), list):
                for item in data["transcript"]:
                    if isinstance(item, dict):
                        speaker = str(item.get("speaker", "Unknown"))
                        value = str(item.get("text", ""))
                        if value:
                            self.append_transcript(speaker, value)
                            parsed_count += 1
        except json.JSONDecodeError:
            for line in text.splitlines():
                clean = line.strip()
                if not clean:
                    continue
                if clean.startswith("#"):
                    continue
                if ":" in clean:
                    speaker, value = clean.split(":", 1)
                else:
                    speaker, value = "Unknown", clean
                self.append_transcript(speaker.strip(), value.strip())
                parsed_count += 1

        self.audit.record(
            "meeting.transcript_parse",
            target=filename,
            details={"parsed_count": parsed_count},
        )
        return {"parsed_count": parsed_count, **self.status()}
