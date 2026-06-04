from __future__ import annotations

import json

from livekit.agents import Agent, function_tool

from .audit import AuditLogger
from .camera_observer import CameraObserverService
from .config import OfficeAgentConfig
from .daily import LocalDailyService
from .desktop import DesktopService
from .desktop_tasks import DesktopTaskQueue
from .documents import DocumentService
from .environment import EnvironmentSensingService
from .file_search import LocalFileSearchService
from .hardware import LampHardware
from .lelamp_experience import LeLampExperienceService
from .meeting import MeetingService
from .memory import MemoryService
from .p0 import P0OfficeService
from .projection import ProjectionService
from .scanning import ScanService
from .scene import SceneService
from .screen import ScreenContextService
from .smart_home import SmartHomeService
from .skills import SkillRegistry
from .task_planner import TaskPlanner
from .workspace import Workspace, WorkspaceError
from .prompts import OFFICE_AGENT_INSTRUCTIONS
from .xiaoai import XiaoAiService


class OpenClawOfficeAgent(Agent):
    def __init__(
        self,
        *,
        config: OfficeAgentConfig,
        workspace: Workspace,
        skills: SkillRegistry,
        hardware: LampHardware,
        audit: AuditLogger,
        meeting: MeetingService,
        documents: DocumentService,
        scanning: ScanService,
        projection: ProjectionService,
        scene: SceneService,
        memory: MemoryService,
        desktop: DesktopService,
        desktop_tasks: DesktopTaskQueue,
        daily: LocalDailyService,
        file_search: LocalFileSearchService,
        screen: ScreenContextService,
        camera_observer: CameraObserverService,
        environment: EnvironmentSensingService,
        lelamp_experience: LeLampExperienceService,
        smart_home: SmartHomeService,
        xiaoai: XiaoAiService,
        p0: P0OfficeService,
        planner: TaskPlanner,
    ) -> None:
        super().__init__(instructions=OFFICE_AGENT_INSTRUCTIONS)
        self.config = config
        self.workspace = workspace
        self.skills = skills
        self.hardware = hardware
        self.audit = audit
        self.meeting = meeting
        self.documents = documents
        self.scanning = scanning
        self.projection = projection
        self.scene = scene
        self.memory = memory
        self.desktop = desktop
        self.desktop_tasks = desktop_tasks
        self.daily = daily
        self.file_search = file_search
        self.screen = screen
        self.camera_observer = camera_observer
        self.environment = environment
        self.lelamp_experience = lelamp_experience
        self.smart_home = smart_home
        self.xiaoai = xiaoai
        self.p0 = p0
        self.planner = planner

    @function_tool
    async def get_security_status(self) -> str:
        """
        Report the active safety boundary, workspace path, allowed roots, and audit log path.
        Use this when users ask what the agent can access or whether full-control mode is active.
        """
        status = {
            "permission_mode": self.config.permission_mode.value,
            "workspace_dir": str(self.config.workspace_dir),
            "allowed_roots": [str(path) for path in self.config.allowed_roots],
            "audit_log_path": str(self.config.audit_log_path),
            "hardware_enabled": self.config.enable_hardware,
            "meeting_mode_enabled": self.meeting.status()["meeting_mode_enabled"],
            "projection_dir": str(self.config.projection_dir),
            "memory_path": str(self.config.memory_path),
            "desktop_backend": self.config.desktop_backend,
            "smart_home_provider": self.config.smart_home_provider,
            "smart_home_status": self.smart_home.status(),
        }
        self.audit.record("security.status")
        return json.dumps(status, ensure_ascii=False, indent=2)

    @function_tool
    async def list_office_skills(self) -> str:
        """
        List available OpenClaw office skills, their permission tier, and implementation status.
        Use this to explain product capabilities or current prototype boundaries.
        """
        return json.dumps(self.skills.list_skills(), ensure_ascii=False, indent=2)

    @function_tool
    async def get_p0_status(self) -> str:
        """
        List P0 office assistant capabilities and whether each one is implemented.
        Use this when the user asks for P0 progress or core office-assistant status.
        """
        return json.dumps(self.p0.status(), ensure_ascii=False, indent=2)

    @function_tool
    async def list_lelamp_capabilities(self) -> str:
        """
        List LeLamp-specific capabilities: expressive state, desk scene events,
        projection interaction, and office environment sensing.
        """
        return json.dumps(self.lelamp_experience.capability_map(), ensure_ascii=False, indent=2)

    @function_tool
    async def get_lamp_state_cue(self, state: str) -> str:
        """
        Return the RGB and movement recording for a LeLamp assistant state.

        Args:
            state: idle, wake, listening, thinking, speaking, reminder, blocked, success, error, meeting, or projecting.
        """
        return json.dumps(self.lelamp_experience.state_cue(state), ensure_ascii=False, indent=2)

    @function_tool
    async def observe_desk_once(self, camera_index: int = 0) -> str:
        """
        Capture one camera frame and infer desk scene events such as paper/screen/whiteboard or ambient light issues.

        Args:
            camera_index: Local camera index for OpenCV/fswebcam fallback.
        """
        return json.dumps(self.camera_observer.observe_once(camera_index=camera_index), ensure_ascii=False, indent=2)

    @function_tool
    async def report_environment_reading(self, reading_json: str) -> str:
        """
        Ingest office environment sensor readings and infer scene events.

        Args:
            reading_json: JSON object with optional presence, motion, lux, sound_level, speech_active,
                people_count, projector_blocked, and calendar_event_now.
        """
        try:
            reading = json.loads(reading_json)
        except json.JSONDecodeError as exc:
            return f"Environment reading failed: invalid JSON: {exc}"
        if not isinstance(reading, dict):
            return "Environment reading failed: JSON must be an object."
        return json.dumps(self.environment.ingest(reading), ensure_ascii=False, indent=2)

    @function_tool
    async def render_lamp_countdown(self, title: str, seconds: int, message: str = "") -> str:
        """
        Render a projection countdown card and return the recommended LeLamp reminder cue.

        Args:
            title: Countdown title.
            seconds: Countdown duration in seconds.
            message: Optional message shown under the countdown.
        """
        return json.dumps(
            self.lelamp_experience.render_countdown(title, seconds, message),
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def render_lamp_action_confirmation(
        self,
        title: str,
        actions: list[str],
        decisions: list[str] | None = None,
    ) -> str:
        """
        Render a projection action-confirmation card and return the recommended LeLamp projecting cue.

        Args:
            title: Card title.
            actions: Action items to confirm.
            decisions: Optional decisions to show above action items.
        """
        return json.dumps(
            self.lelamp_experience.render_action_confirmation(title, actions, decisions),
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def list_xiaoai_features(self) -> str:
        """
        List XiaoAi-compatible assistant capabilities and their current implementation status.
        Use this when users ask what XiaoAi features OpenClaw supports.
        """
        return json.dumps(self.xiaoai.feature_matrix(), ensure_ascii=False, indent=2)

    @function_tool
    async def answer_xiaoai_utility(self, text: str) -> str:
        """
        Answer deterministic XiaoAi-style utility requests such as time, calculation,
        unit conversion, jokes, poems, or identify when LLM/search is needed.

        Args:
            text: User's utility request.
        """
        try:
            return json.dumps(self.xiaoai.answer_utility(text), ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"Utility request failed: {exc}"

    @function_tool
    async def get_smart_home_status(self) -> str:
        """
        Report configured smart-home provider, webhook/Home Assistant status, and known entities.
        """
        return json.dumps(self.smart_home.status(), ensure_ascii=False, indent=2)

    @function_tool
    async def control_smart_home_device(self, command: str, entity_name: str | None = None) -> str:
        """
        Control a smart-home device through the configured Home Assistant or webhook bridge.

        Args:
            command: Natural-language command such as 打开客厅灯 or 空调调到26度.
            entity_name: Optional configured entity display name.
        """
        return json.dumps(
            self.smart_home.control(command, entity_name=entity_name),
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def create_local_reminder(self, text: str) -> str:
        """
        Create a local reminder from natural language.

        Args:
            text: Reminder text with optional time, such as 明天9点提醒我开会.
        """
        return json.dumps(self.daily.create_reminder(text), ensure_ascii=False, indent=2)

    @function_tool
    async def list_local_reminders(self, include_done: bool = False) -> str:
        """
        List local reminders.

        Args:
            include_done: Include completed reminders.
        """
        return json.dumps(self.daily.list_reminders(include_done=include_done), ensure_ascii=False, indent=2)

    @function_tool
    async def create_local_calendar_event(self, title: str, participants: list[str] | None = None) -> str:
        """
        Create a local calendar event with conflict detection.

        Args:
            title: Event title and optional date/time in natural language.
            participants: Optional participant names or roles.
        """
        return json.dumps(
            self.daily.create_event(title, participants=participants or []),
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def get_local_agenda(self, date: str = "today") -> str:
        """
        Return local agenda and reminders for a date.

        Args:
            date: today, tomorrow, day_after_tomorrow, or YYYY-MM-DD.
        """
        return json.dumps(self.daily.agenda(date), ensure_ascii=False, indent=2)

    @function_tool
    async def search_local_content(self, query: str, limit: int = 20) -> str:
        """
        Search allowed local roots by filename and lightweight text content scoring.

        Args:
            query: Search keywords.
            limit: Maximum matches.
        """
        return json.dumps(self.file_search.search(query, limit=limit), ensure_ascii=False, indent=2)

    @function_tool
    async def capture_screen_context(self) -> str:
        """
        Capture the current screen into the workspace.
        """
        return json.dumps(self.screen.capture_screen(), ensure_ascii=False, indent=2)

    @function_tool
    async def summarize_current_screen(self, language: str = "chi_sim+eng") -> str:
        """
        Capture the current screen, run OCR when available, and write a screen-context summary.

        Args:
            language: Tesseract language list, such as chi_sim+eng or eng.
        """
        return json.dumps(
            self.screen.summarize_current_screen(language=language),
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def import_workspace_file(self, source_path: str) -> str:
        """
        Import a user-approved file into the sandbox workspace.

        Args:
            source_path: Absolute or user-relative path to a file under an allowed root.
        """
        try:
            imported = self.workspace.import_file(source_path)
        except WorkspaceError as exc:
            return f"Import blocked: {exc}"

        return json.dumps(
            {
                "name": imported.name,
                "path": str(imported.path),
                "size_bytes": imported.size_bytes,
                "sha256": imported.sha256,
            },
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def list_workspace_files(self) -> str:
        """
        List files currently available inside the sandbox workspace.
        Use before reading or summarizing user documents.
        """
        files = self.workspace.list_files()
        return json.dumps(
            [
                {
                    "name": item.name,
                    "path": str(item.path),
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in files
            ],
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def read_workspace_text_file(self, filename: str, max_chars: int = 12000) -> str:
        """
        Read a text file from the sandbox workspace for summarization or extraction.
        This is intentionally text-only for the first prototype.

        Args:
            filename: File name already present in the workspace.
            max_chars: Maximum characters to return.
        """
        try:
            return self.workspace.read_text(filename, max_chars=max_chars)
        except (WorkspaceError, UnicodeDecodeError) as exc:
            return f"Read blocked: {exc}"

    @function_tool
    async def analyze_workspace_document(self, filename: str) -> str:
        """
        Analyze a workspace text document for headings, key-value pairs, rough metrics, and risk markers.

        Args:
            filename: Workspace text file to analyze.
        """
        try:
            return json.dumps(self.documents.analyze_text_file(filename), ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"Document analysis failed: {exc}"

    @function_tool
    async def summarize_workspace_document(self, filename: str, style: str = "brief") -> str:
        """
        Summarize a workspace text document into a markdown file.

        Args:
            filename: Workspace text file to summarize.
            style: brief, detailed, or outline.
        """
        try:
            return json.dumps(
                self.documents.summarize_text_file(filename, style=style),
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return f"Document summarization failed: {exc}"

    @function_tool
    async def compare_workspace_documents(self, left_filename: str, right_filename: str) -> str:
        """
        Compare two workspace text documents and write a JSON diff report.

        Args:
            left_filename: First workspace text file.
            right_filename: Second workspace text file.
        """
        try:
            return json.dumps(
                self.documents.compare_text_files(left_filename, right_filename),
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return f"Document comparison failed: {exc}"

    @function_tool
    async def extract_table_from_workspace_text(self, filename: str) -> str:
        """
        Extract key document data into CSV through the configured LLM API.

        Args:
            filename: Workspace text file to analyze.
        """
        try:
            return json.dumps(self.documents.extract_table_from_text(filename), ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"Table extraction failed: {exc}"

    @function_tool
    async def create_report_outline(self, filenames: list[str], topic: str) -> str:
        """
        Create a report outline from multiple workspace text documents through the configured LLM API.

        Args:
            filenames: Workspace source files.
            topic: Report topic/title.
        """
        try:
            return json.dumps(
                self.documents.create_report_outline(filenames, topic),
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return f"Report outline creation failed: {exc}"

    @function_tool
    async def create_meeting_note_template(self, title: str, participants: list[str]) -> str:
        """
        Create a structured meeting note template in the sandbox workspace.

        Args:
            title: Meeting title.
            participants: Participant names or roles.
        """
        safe_title = "".join(ch for ch in title if ch.isalnum() or ch in {" ", "-", "_"}).strip()
        if not safe_title:
            safe_title = "meeting"
        filename = f"{safe_title.replace(' ', '_')}.md"
        path = (self.config.workspace_dir / filename).resolve()
        if not path.is_relative_to(self.config.workspace_dir):
            return "Create blocked: invalid file name."

        content = "\n".join(
            [
                f"# {title}",
                "",
                "## Participants",
                *[f"- {person}" for person in participants],
                "",
                "## Summary",
                "",
                "## Decisions",
                "",
                "## Action Items",
                "",
                "## Risks / Open Questions",
                "",
            ]
        )
        path.write_text(content, encoding="utf-8")
        self.audit.record(
            "workspace.create_meeting_note",
            target=str(path),
            details={"title": title, "participants": participants},
        )
        return f"Created meeting note template: {path}"

    @function_tool
    async def enable_meeting_mode(self, title: str, participants: list[str]) -> str:
        """
        Explicitly enable meeting understanding mode.

        Args:
            title: Meeting title.
            participants: Participant names or roles.
        """
        return json.dumps(self.meeting.enable(title, participants), ensure_ascii=False, indent=2)

    @function_tool
    async def disable_meeting_mode(self) -> str:
        """
        Disable meeting understanding mode and stop accepting transcript updates.
        """
        return json.dumps(self.meeting.disable(), ensure_ascii=False, indent=2)

    @function_tool
    async def get_meeting_status(self) -> str:
        """
        Return the current meeting mode status and transcript turn count.
        """
        self.audit.record("meeting.status")
        return json.dumps(self.meeting.status(), ensure_ascii=False, indent=2)

    @function_tool
    async def append_meeting_transcript(self, speaker: str, text: str) -> str:
        """
        Append a speaker turn to the active meeting transcript.

        Args:
            speaker: Speaker name or role.
            text: Spoken content after ASR.
        """
        try:
            return json.dumps(self.meeting.append_transcript(speaker, text), ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"Transcript append blocked: {exc}"

    @function_tool
    async def import_transcript_file_as_meeting(
        self,
        filename: str,
        title: str,
        participants: list[str],
    ) -> str:
        """
        Parse a workspace transcript file into the meeting session.
        Accepts JSON transcript arrays or plain speaker: text lines.

        Args:
            filename: Workspace transcript text or JSON file.
            title: Meeting title.
            participants: Participant names or roles.
        """
        try:
            return json.dumps(
                self.meeting.parse_transcript_file(filename, title, participants),
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return f"Transcript import failed: {exc}"

    @function_tool
    async def generate_meeting_minutes(self) -> str:
        """
        Generate meeting minutes and action items from the active transcript.
        """
        try:
            return json.dumps(self.meeting.generate_minutes(), ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"Minutes generation failed: {exc}"

    @function_tool
    async def generate_meeting_followup_package(
        self,
        recipient: str = "待填写收件人",
        create_reminders: bool = True,
        render_projection: bool = True,
    ) -> str:
        """
        Generate a P0 meeting follow-up package: minutes, transcript export,
        email draft, optional reminders, and optional projection confirmation.

        Args:
            recipient: Intended recipient for the email draft.
            create_reminders: Create local reminders from action items.
            render_projection: Render decisions/action items for projection confirmation.
        """
        try:
            return json.dumps(
                self.p0.generate_meeting_followup_package(
                    recipient=recipient,
                    create_reminders=create_reminders,
                    render_projection=render_projection,
                ),
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return f"Meeting follow-up package failed: {exc}"

    @function_tool
    async def export_meeting_transcript(self) -> str:
        """
        Export the active meeting transcript to a workspace JSON file.
        """
        try:
            return json.dumps(self.meeting.export_transcript(), ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"Transcript export failed: {exc}"

    @function_tool
    async def draft_email_from_note(self, filename: str, recipient: str, intent: str) -> str:
        """
        Draft an email from a workspace note without sending it.

        Args:
            filename: Workspace text file to base the email on.
            recipient: Intended recipient or recipient group.
            intent: Email purpose, such as follow-up, decision confirmation, or task assignment.
        """
        try:
            source = self.workspace.read_text(filename, max_chars=60000)
        except WorkspaceError as exc:
            return f"Draft blocked: {exc}"

        result = self.p0.generate_followup_email_with_api(
            title=intent,
            recipient=recipient,
            decisions=[],
            action_items=[],
            minutes_text=source,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    @function_tool
    async def register_scanned_image(self, image_filename: str, document_type: str = "document") -> str:
        """
        Register an imported workspace image as a scanned physical document.

        Args:
            image_filename: Image file already imported into the workspace.
            document_type: document, contract, business_card, receipt, or whiteboard.
        """
        try:
            return json.dumps(
                self.scanning.register_scan_image(image_filename, document_type),
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return f"Scan registration failed: {exc}"

    @function_tool
    async def request_ocr_for_scan(self, image_filename: str, language: str = "ch") -> str:
        """
        Run OCR for a scanned image when a local backend is available.
        Returns backend_missing instead of claiming recognition if no OCR backend is installed.

        Args:
            image_filename: Image file already imported into the workspace.
            language: OCR language code, such as ch or en.
        """
        try:
            return json.dumps(
                self.scanning.run_ocr(image_filename, language),
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return f"OCR request failed: {exc}"

    @function_tool
    async def summarize_ocr_text(self, filename: str) -> str:
        """
        Summarize OCR text from a scanned paper document.

        Args:
            filename: Workspace text file containing OCR output.
        """
        try:
            return json.dumps(
                self.scanning.summarize_ocr_text(filename),
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return f"OCR text summarization failed: {exc}"

    @function_tool
    async def parse_business_card_text(self, filename: str) -> str:
        """
        Parse OCR text from a business card into structured contact candidates.

        Args:
            filename: Workspace text file containing OCR results.
        """
        try:
            return json.dumps(
                self.scanning.analyze_business_card_text(filename),
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return f"Business card parsing failed: {exc}"

    @function_tool
    async def render_projection_markdown(self, title: str, body: str, mode: str = "meeting") -> str:
        """
        Render markdown content to the projection output directory.

        Args:
            title: Projection page title.
            body: Markdown body to display.
            mode: meeting, document, confirmation, warning, or status.
        """
        return json.dumps(
            self.projection.render_markdown(title, body, mode),
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def render_projection_confirmation(
        self,
        title: str,
        decisions: list[str],
        action_items: list[str],
    ) -> str:
        """
        Render a meeting confirmation page with decisions and action items.

        Args:
            title: Confirmation page title.
            decisions: Decisions to confirm.
            action_items: Action items to confirm.
        """
        return json.dumps(
            self.projection.render_confirmation(title, decisions, action_items),
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def create_projection_calibration_plan(self, surface: str, ambient_light: str) -> str:
        """
        Create a projector calibration plan for keystone, focus, and brightness.

        Args:
            surface: Wall, screen, desktop, whiteboard, or other projection surface.
            ambient_light: Dark, normal office, bright office, or daylight.
        """
        return json.dumps(
            self.projection.create_projection_calibration_plan(surface, ambient_light),
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def report_scene_event(self, event_type: str, description: str, confidence: float = 1.0) -> str:
        """
        Record a multimodal scene event and get a workflow suggestion.

        Args:
            event_type: paper_detected, projection_blocked, presentation_started, whiteboard_seen, etc.
            description: Short event description.
            confidence: Detection confidence between 0 and 1.
        """
        return json.dumps(
            self.scene.report_event(event_type, description, confidence),
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def get_recent_scene_events(self, limit: int = 10) -> str:
        """
        Return recent scene events.

        Args:
            limit: Number of recent events.
        """
        return json.dumps(self.scene.get_recent_events(limit), ensure_ascii=False, indent=2)

    @function_tool
    async def remember_user_preference(self, key: str, value: str, category: str = "preference") -> str:
        """
        Store a user-approved long-term memory item.

        Args:
            key: Memory key.
            value: Memory value.
            category: Memory category.
        """
        return json.dumps(self.memory.remember(key, value, category), ensure_ascii=False, indent=2)

    @function_tool
    async def search_memory(self, query: str, limit: int = 10) -> str:
        """
        Search local long-term memory.

        Args:
            query: Search string.
            limit: Maximum results.
        """
        return json.dumps(self.memory.search(query, limit), ensure_ascii=False, indent=2)

    @function_tool
    async def list_recent_memory(self, limit: int = 10) -> str:
        """
        List recent long-term memory entries.

        Args:
            limit: Maximum results.
        """
        return json.dumps(self.memory.list_recent(limit), ensure_ascii=False, indent=2)

    @function_tool
    async def plan_office_task(self, request: str, context_files: list[str] | None = None) -> str:
        """
        Decompose an office request into OpenClaw skill steps.

        Args:
            request: User's natural-language office task.
            context_files: Relevant workspace files, if any.
        """
        return json.dumps(
            self.planner.plan(request, context_files or []),
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def request_desktop_operation(self, task_description: str) -> str:
        """
        Request a full-control desktop operation. This prototype only verifies permission mode
        and records the request; a GUI automation backend must be integrated later.

        Args:
            task_description: Concrete desktop operation requested by the user.
        """
        planned = self.planner.plan(task_description)
        task = self.desktop_tasks.request_task(
            task_description,
            [str(step.get("action") or step) for step in planned.get("steps", [])],
            source="agent_tool",
        )
        return json.dumps(
            {
                "permission": self.desktop.request_operation(task_description),
                "task": task,
            },
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def open_desktop_app(self, app_name: str) -> str:
        """
        Open a known desktop application through a deterministic local command.
        Execution requires OPENCLAW_DESKTOP_BACKEND=local; otherwise this records a plan.

        Args:
            app_name: Application display name or command alias.
        """
        return json.dumps(self.desktop.open_app(app_name), ensure_ascii=False, indent=2)

    @function_tool
    async def open_desktop_url(self, url_or_query: str) -> str:
        """
        Open a URL or search query in the system browser.
        Execution requires OPENCLAW_DESKTOP_BACKEND=local; otherwise this records a plan.

        Args:
            url_or_query: URL, domain, or search query.
        """
        return json.dumps(self.desktop.open_url(url_or_query), ensure_ascii=False, indent=2)

    @function_tool
    async def find_local_file(self, query: str, limit: int = 20) -> str:
        """
        Search allowed local roots for matching file names.

        Args:
            query: File name fragment to search.
            limit: Maximum results.
        """
        return json.dumps(self.desktop.find_files(query, limit=limit), ensure_ascii=False, indent=2)

    @function_tool
    async def open_local_file(self, path_or_query: str) -> str:
        """
        Open a file under allowed roots by path or name search.
        Execution requires OPENCLAW_DESKTOP_BACKEND=local; otherwise this records a plan.

        Args:
            path_or_query: Allowed path or file-name fragment.
        """
        return json.dumps(self.desktop.open_file(path_or_query), ensure_ascii=False, indent=2)

    @function_tool
    async def control_desktop_media(self, command: str) -> str:
        """
        Control desktop media playback through playerctl when available.

        Args:
            command: Media command, such as 暂停音乐, 下一首, or 继续播放.
        """
        return json.dumps(self.desktop.media_control(command), ensure_ascii=False, indent=2)

    @function_tool
    async def set_system_volume(self, command: str) -> str:
        """
        Adjust system volume through pactl or amixer when available.

        Args:
            command: Volume command, such as 音量调到30 or 声音大一点.
        """
        return json.dumps(self.desktop.set_volume(command), ensure_ascii=False, indent=2)

    @function_tool
    async def build_desktop_workflow(self, goal: str, steps: list[str]) -> str:
        """
        Build an auditable desktop workflow plan without executing it.

        Args:
            goal: Workflow goal.
            steps: Ordered workflow steps.
        """
        return json.dumps(
            self.desktop.build_workflow(goal, steps),
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def request_desktop_task_for_review(self, goal: str, steps: list[str]) -> str:
        """
        Create a shared desktop task request for human review by the office computer.

        Args:
            goal: Intended desktop task.
            steps: Proposed manual or future-agent execution steps.
        """
        return json.dumps(
            self.desktop_tasks.request_task(goal, steps, source="agent_tool"),
            ensure_ascii=False,
            indent=2,
        )

    @function_tool
    async def list_desktop_tasks(self, limit: int = 50) -> str:
        """
        List shared desktop task requests.

        Args:
            limit: Maximum task count.
        """
        return json.dumps(self.desktop_tasks.list_tasks(limit=limit), ensure_ascii=False, indent=2)

    @function_tool
    async def express_status(self, state: str) -> str:
        """
        Express an office workflow state through LeLamp movement and light if hardware is enabled.

        Args:
            state: One of thinking, success, warning, scanning, meeting, blocked.
        """
        cue = self.lelamp_experience.state_cue(state)
        recording = str(cue["recording"])
        rgb_values = cue["rgb"]
        rgb = tuple(int(value) for value in rgb_values)  # type: ignore[arg-type]
        movement_result = self.hardware.play(recording)
        rgb_result = self.hardware.set_rgb(*rgb)
        return json.dumps(
            {
                "cue": cue,
                "movement_result": movement_result,
                "rgb_result": rgb_result,
            },
            ensure_ascii=False,
            indent=2,
        )
