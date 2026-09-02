from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .audit import AuditLogger
from .workspace import Workspace
from .utils import safe_filename

_SPEAKER_RE = re.compile(r"^[\w .#:\-\u4e00-\u9fff]{1,40}$", re.UNICODE)


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
        clean_speaker = sanitize_speaker(speaker)
        clean_text = sanitize_turn_text(text)
        if not clean_text:
            self.audit.record(
                "meeting.transcript_append",
                status="blocked",
                details={"reason": "non_text_or_empty_turn", "speaker": clean_speaker, "chars": len(str(text or ""))},
            )
            raise ValueError("Meeting turn is empty or not valid text.")

        item = {
            "timestamp": datetime.now(UTC).isoformat(),
            "speaker": clean_speaker,
            "text": clean_text,
        }
        self._session.transcript.append(item)
        self.audit.record(
            "meeting.transcript_append",
            details={"speaker": clean_speaker, "chars": len(clean_text)},
        )
        return item

    def realtime_summary(self) -> dict[str, object]:
        transcript = clean_transcript_items(self._session.transcript if self._session else [])
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
        transcript = clean_transcript_items(self._session.transcript)
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
            f"开始时间：{format_transcript_time(self._session.started_at)}",
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
            *[f"- [{format_transcript_time(item['timestamp'])}] {item['speaker']}: {item['text']}" for item in transcript],
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
        transcript = clean_transcript_items(self._session.transcript)
        path = self.workspace.write_json(
            filename,
            {
                "title": self._session.title,
                "participants": self._session.participants,
                "started_at": self._session.started_at,
                "transcript": transcript,
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
        if is_binary_like_text(text):
            self.audit.record(
                "meeting.transcript_parse",
                status="blocked",
                target=filename,
                details={"reason": "binary_like_transcript", "title": title},
            )
            raise ValueError("所选文件不是可读的会议转写文本。会议导入只支持 txt/md/json 转写内容。")
        self.enable(title, participants)

        parsed_count = 0
        try:
            data = json.loads(text)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        speaker = sanitize_speaker(str(item.get("speaker", "Unknown")))
                        value = str(item.get("text", ""))
                        if sanitize_turn_text(value):
                            self.append_transcript(speaker, value)
                            parsed_count += 1
            elif isinstance(data, dict) and isinstance(data.get("transcript"), list):
                for item in data["transcript"]:
                    if isinstance(item, dict):
                        speaker = sanitize_speaker(str(item.get("speaker", "Unknown")))
                        value = str(item.get("text", ""))
                        if sanitize_turn_text(value):
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
                if sanitize_turn_text(value):
                    self.append_transcript(speaker.strip(), value.strip())
                    parsed_count += 1

        self.audit.record(
            "meeting.transcript_parse",
            target=filename,
            details={"parsed_count": parsed_count},
        )
        return {"parsed_count": parsed_count, **self.status()}


def sanitize_speaker(value: str) -> str:
    text = str(value or "").replace("\x00", "").strip()
    text = re.sub(r"\s+", " ", text)
    if not text or "\ufffd" in text or not _SPEAKER_RE.match(text):
        return "Unknown"
    return text[:40]


def sanitize_turn_text(value: str) -> str:
    text = str(value or "").replace("\x00", "").strip()
    text = re.sub(r"[\r\t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text or is_binary_like_text(text):
        return ""
    return text[:4000]


def clean_transcript_items(items: list[dict[str, str]]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in items:
        text = sanitize_turn_text(str(item.get("text") or ""))
        if not text:
            continue
        cleaned.append(
            {
                "timestamp": str(item.get("timestamp") or ""),
                "speaker": sanitize_speaker(str(item.get("speaker") or "Unknown")),
                "text": text,
            }
        )
    return cleaned


def format_transcript_time(value: str) -> str:
    """Keep meeting turn timestamps compact while preserving two-digit seconds."""
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"(?:T|\s)(\d{2}:\d{2}:\d{2})", text)
    if match:
        return match.group(1)
    clock = re.fullmatch(r"(\d{2}:\d{2}:\d{2})(?:\.\d+)?", text)
    return clock.group(1) if clock else text[:8]


def is_binary_like_text(text: str) -> bool:
    value = str(text or "")
    if not value:
        return False
    sample = value[:4096]
    if sample.startswith("PK\x03\x04") or "PK\x03\x04" in sample[:512]:
        return True
    replacement_count = sample.count("\ufffd")
    control_count = sum(1 for char in sample if ord(char) < 32 and char not in "\n\r\t")
    if replacement_count >= 3:
        return True
    return control_count / max(1, len(sample)) > 0.05
