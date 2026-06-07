from __future__ import annotations

from pathlib import Path

from .audit import AuditLogger
from .config import OfficeAgentConfig
from .daily import LocalDailyService
from .file_search import LocalFileSearchService
from .llm import LLMError, ResponsesLLM, ResponsesLLMConfig
from .meeting import MeetingService
from .projection import ProjectionService
from .screen import ScreenContextService
from .utils import safe_filename
from .workspace import Workspace


class P0OfficeService:
    """P0 office assistant workflows assembled from smaller services."""

    def __init__(
        self,
        *,
        workspace: Workspace,
        audit: AuditLogger,
        meeting: MeetingService,
        projection: ProjectionService,
        daily: LocalDailyService,
        file_search: LocalFileSearchService,
        screen: ScreenContextService,
        config: OfficeAgentConfig | None = None,
    ):
        self.workspace = workspace
        self.audit = audit
        self.meeting = meeting
        self.projection = projection
        self.daily = daily
        self.file_search = file_search
        self.screen = screen
        self.config = config

    def status(self) -> dict[str, object]:
        llm_status = "implemented" if self.config and self.config.openai_api_key else "adapter_ready"
        payload = {
            "p0": [
                {
                    "capability": "meeting_full_flow",
                    "status": "implemented",
                    "entrypoints": ["generate_meeting_followup_package", "minutes"],
                    "notes": "Meeting minutes/transcript/reminders/projection and local email drafts are available; OPENAI_API_KEY upgrades email drafting quality.",
                },
                {
                    "capability": "document_workbench",
                    "status": "implemented",
                    "entrypoints": ["analyze", "summarize", "compare", "extract_table", "report_outline"],
                    "notes": "Key data table and report outline have local rules fallback; OPENAI_API_KEY upgrades generation quality.",
                },
                {
                    "capability": "calendar_reminder",
                    "status": "implemented",
                    "entrypoints": ["create_reminder", "create_event", "agenda", "conflict_detection"],
                },
                {
                    "capability": "screen_understanding",
                    "status": "adapter_ready",
                    "entrypoints": ["capture_screen", "ocr_image", "summarize_current_screen"],
                    "notes": "Requires a screenshot tool and tesseract/PaddleOCR for OCR.",
                },
                {
                    "capability": "local_file_search",
                    "status": "implemented",
                    "entrypoints": ["search_local_content", "open_local_file"],
                },
                {
                    "capability": "email_draft",
                    "status": llm_status,
                    "entrypoints": ["draft_email_from_note", "meeting_followup_email"],
                    "notes": "Draft generation uses local rules without API keys and never sends email automatically.",
                },
                {
                    "capability": "safe_desktop_actions",
                    "status": "implemented",
                    "entrypoints": ["open_app", "open_url", "media_control", "set_volume"],
                },
                {
                    "capability": "audit_and_permissions",
                    "status": "implemented",
                    "entrypoints": ["get_security_status", "audit.jsonl"],
                },
            ]
        }
        self.audit.record("p0.status", details={"count": len(payload["p0"])})
        return payload

    def generate_meeting_followup_package(
        self,
        *,
        recipient: str = "待填写收件人",
        create_reminders: bool = True,
        render_projection: bool = True,
    ) -> dict[str, object]:
        minutes = self.meeting.generate_minutes()
        transcript = self.meeting.export_transcript()
        minutes_path = Path(str(minutes["path"]))
        source = minutes_path.read_text(encoding="utf-8", errors="replace")
        title = str(minutes.get("title") or "Meeting")
        email = self.generate_followup_email_with_api(
            title=title,
            recipient=recipient,
            decisions=[str(item) for item in minutes.get("decisions", [])],
            action_items=[str(item) for item in minutes.get("action_items", [])],
            minutes_text=source,
        )
        reminder_result = None
        action_items = [str(item) for item in minutes.get("action_items", [])]
        if create_reminders and action_items:
            reminder_result = self.daily.create_reminders_from_action_items(action_items)

        projection_result = None
        if render_projection:
            projection_result = self.projection.render_confirmation(
                f"{title} - 会后确认",
                [str(item) for item in minutes.get("decisions", [])],
                action_items,
            )

        payload = {
            "status": "completed" if email.get("status") == "completed" else "backend_missing",
            "minutes": minutes,
            "transcript": transcript,
            "email": email,
            "email_draft_path": str(email.get("email_draft_path") or ""),
            "reminders": reminder_result,
            "projection": projection_result,
        }
        self.audit.record("p0.meeting_followup_package", target=title, details=payload)
        return payload

    def generate_followup_email_with_api(
        self,
        *,
        title: str,
        recipient: str,
        decisions: list[str],
        action_items: list[str],
        minutes_text: str,
    ) -> dict[str, object]:
        if self.config is None or not self.config.openai_api_key:
            return self.generate_followup_email_locally(
                title=title,
                recipient=recipient,
                decisions=decisions,
                action_items=action_items,
                minutes_text=minutes_text,
            )

        llm = ResponsesLLM(
            ResponsesLLMConfig(
                api_key=self.config.openai_api_key,
                base_url=self.config.openai_base_url,
                model=self.config.openai_model,
                reasoning_effort="low",
            )
        )
        prompt = "\n\n".join(
            [
                f"请根据以下会议纪要生成一封中文会后跟进邮件草稿。会议标题：{title}",
                f"收件人：{recipient}",
                "要求：",
                "- 输出 Markdown。",
                "- 包含 To、Subject、正文草稿。",
                "- 正文要礼貌、简洁，突出决策、行动项、负责人和需要确认的问题。",
                "- 不自动发送；结尾提醒用户确认后再发送。",
                "- 不要编造纪要中没有的信息，缺失内容写“待确认”。",
                "已提取决策：",
                "\n".join(f"- {item}" for item in decisions) or "- 暂无明确决策",
                "已提取行动项：",
                "\n".join(f"- {item}" for item in action_items) or "- 暂无明确行动项",
                "会议纪要：",
                minutes_text,
            ]
        )
        try:
            draft = llm.complete(
                instructions="你是严谨的会议秘书。只基于会议纪要生成邮件草稿，不执行发送。",
                user_input=prompt,
                context={"task": "meeting_followup_email", "title": title},
                timeout=120,
            )
            if not draft.strip():
                raise LLMError("ResponsesLLM returned an empty email draft.")
        except LLMError as exc:
            payload = self.generate_followup_email_locally(
                title=title,
                recipient=recipient,
                decisions=decisions,
                action_items=action_items,
                minutes_text=minutes_text,
            )
            payload.update(
                {
                    "provider": "local_rules_fallback",
                    "fallback_after": "ResponsesLLM",
                    "fallback_error": str(exc),
                    "message": "云端邮件草稿生成失败，已改用本地规则生成；不会自动发送。",
                }
            )
            self.audit.record("p0.meeting_followup_email_write.fallback", target=title, details=payload)
            return payload

        email_path = self.workspace.write_text(
            safe_filename(title, default="meeting", suffix="_followup_email.md"),
            draft,
            action="p0.meeting_followup_email_write",
        )
        return {
            "status": "completed",
            "email_draft_path": str(email_path),
            "provider": "ResponsesLLM",
            "model": self.config.openai_model,
            "chars": len(draft),
        }

    def generate_followup_email_locally(
        self,
        *,
        title: str,
        recipient: str,
        decisions: list[str],
        action_items: list[str],
        minutes_text: str,
    ) -> dict[str, object]:
        body_lines = [
            f"To: {recipient}",
            f"Subject: {title} 会后跟进",
            "",
            "各位好，",
            "",
            f"以下是《{title}》的会后跟进草稿，请确认后再发送。",
            "",
            "## 已识别决策",
            *([f"- {item}" for item in decisions] or ["- 暂无明确决策，待人工补充。"]),
            "",
            "## 行动项",
            *([f"- {item}" for item in action_items] or ["- 暂无明确行动项，待人工补充。"]),
            "",
            "## 待确认",
            "- 请确认负责人、截止时间和对外表述。",
            "- 本草稿由本地规则生成，未自动发送。",
            "",
            "## 纪要摘录",
            minutes_text[:4000],
            "",
            "谢谢。",
            "",
        ]
        draft = "\n".join(body_lines)
        email_path = self.workspace.write_text(
            safe_filename(title, default="meeting", suffix="_followup_email.md"),
            draft,
            action="p0.meeting_followup_email_write.local",
        )
        payload = {
            "status": "completed",
            "email_draft_path": str(email_path),
            "provider": "local_rules",
            "model": "local_rules",
            "chars": len(draft),
            "message": "本地规则已生成邮件草稿；配置 OPENAI_API_KEY 后可启用更强的 API 改写。不会自动发送。",
        }
        self.audit.record("p0.meeting_followup_email_write", target=title, details=payload)
        return payload
