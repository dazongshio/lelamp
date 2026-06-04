from __future__ import annotations

import json
import urllib.error
import urllib.request
import zipfile
from tempfile import TemporaryDirectory
from pathlib import Path

from .audit import AuditLogger
from .config import OfficeAgentConfig
from .config import PermissionMode
from .camera_observer import scene_events_from_metrics
from .daily import LocalDailyService
from .desktop import DesktopService
from .desktop_automation import BrowserAutomationService, parse_browser_step
from .desktop_companion import DesktopCompanionService
from .desktop_tasks import DesktopTaskQueue
from .documents import DocumentService
from .environment import EnvironmentSensingService
from .file_search import LocalFileSearchService
from .intent_router import OfficeIntentRouter
from .lelamp_experience import LeLampExperienceService
from .llm import ResponsesLLM, ResponsesLLMConfig
from .meeting import MeetingService
from .memory import MemoryService
from .mobile_bridge import MobileBridgeConfig, MobileBridgeService
from .p0 import P0OfficeService
from .projection import ProjectionService
from .projection_viewer import build_display_profile
from .scanning import ScanService
from .scene import SceneService
from .screen import ScreenContextService, build_screen_summary
from .shared_space import SharedSpaceService
from .smart_home import SmartHomeConfig, SmartHomeService
from .task_planner import TaskPlanner
from .target_validation import TargetValidationService, ValidationStep
from .workspace import Workspace
from .runtime import build_runtime
from .web_console import RequestContext, WebConsoleServer
from openclaw_cli import _ensure_workspace_file, run_tool
from openclaw_cli import _ensure_workspace_relative_file
from .xiaoai import XiaoAiService


def _write_minimal_docx(path: Path, text: str) -> None:
    paragraphs = "".join(
        f"<w:p><w:r><w:t>{_xml_escape(line)}</w:t></w:r></w:p>"
        for line in text.splitlines()
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}</w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>')
        archive.writestr("word/document.xml", document_xml)


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _FakeHTTPResponse:
    def __init__(self, body: str):
        self.body = body.encode("utf-8")

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _assert_multimodal_llm_fallback() -> None:
    calls: list[str] = []
    original_urlopen = urllib.request.urlopen

    def fake_urlopen(request: urllib.request.Request, timeout: float = 120):
        url = request.full_url
        calls.append(url)
        if url.endswith("/v1/responses"):
            raise urllib.error.HTTPError(
                url,
                503,
                "Service temporarily unavailable",
                hdrs={},
                fp=__import__("io").BytesIO(b'{"error":{"message":"temporary"}}'),
            )
        if url.endswith("/v1/chat/completions"):
            return _FakeHTTPResponse(json.dumps({"choices": [{"message": {"content": "fallback summary ok"}}]}))
        raise AssertionError(f"unexpected URL: {url}")

    urllib.request.urlopen = fake_urlopen
    try:
        result = ResponsesLLM(
            ResponsesLLMConfig(api_key="test", base_url="https://api.example.test", model="vision-model", reasoning_effort="low")
        ).complete_multimodal(
            instructions="summarize",
            text="describe image",
            image_data_url="data:image/png;base64,AAAA",
            timeout=1,
        )
    finally:
        urllib.request.urlopen = original_urlopen
    assert result == "fallback summary ok"
    assert any(url.endswith("/v1/responses") for url in calls)
    assert any(url.endswith("/v1/chat/completions") for url in calls)

    calls.clear()

    def fake_chat_urlopen(request: urllib.request.Request, timeout: float = 120):
        calls.append(request.full_url)
        if request.full_url != "https://dashscope.example/compatible-mode/v1/chat/completions":
            raise AssertionError(f"unexpected URL: {request.full_url}")
        return _FakeHTTPResponse(json.dumps({"choices": [{"message": {"content": "direct chat summary"}}]}))

    urllib.request.urlopen = fake_chat_urlopen
    try:
        direct = ResponsesLLM(
            ResponsesLLMConfig(
                api_key="test",
                base_url="https://dashscope.example/compatible-mode",
                model="qwen-vl-plus",
                reasoning_effort="low",
                wire_api="chat_completions",
            )
        ).complete_multimodal(
            instructions="summarize",
            text="describe image",
            image_data_url="data:image/png;base64,AAAA",
            timeout=1,
        )
    finally:
        urllib.request.urlopen = original_urlopen
    assert direct == "direct chat summary"
    assert calls == ["https://dashscope.example/compatible-mode/v1/chat/completions"]


def main() -> None:
    _assert_multimodal_llm_fallback()
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        audit = AuditLogger(root / "logs" / "audit.jsonl")
        workspace = Workspace(root / "workspace", (root / "workspace",), audit)
        untrusted = root / "outside" / "secret.txt"
        untrusted.parent.mkdir()
        untrusted.write_text("do not import from outside allowed roots", encoding="utf-8")
        try:
            workspace.import_file(untrusted)
        except Exception as exc:
            assert "outside allowed roots" in str(exc)
        else:
            raise AssertionError("workspace.import_file accepted an untrusted path")

        source = workspace.write_text(
            "contract.txt",
            "Title: Demo Contract\n决定: use sandbox mode\n待办: review liability and termination clauses\n",
        )
        assert source.exists()

        documents = DocumentService(workspace, audit)
        analysis = documents.analyze_text_file("contract.txt")
        assert "liability" in analysis["risk_markers"]
        docx_path = workspace.root / "contract.docx"
        _write_minimal_docx(
            docx_path,
            "Title: Demo DOCX Contract\n决定: use adapter mode\n待办: review termination and liability clauses",
        )
        docx_analysis = documents.analyze_text_file("contract.docx")
        assert docx_analysis["source"]["backend"] == "ooxml_docx"
        assert "termination" in docx_analysis["risk_markers"]

        meeting = MeetingService(workspace, audit)
        meeting.enable("Demo Meeting", ["Alice", "Bob"])
        meeting.append_transcript("Alice", "决定: use sandbox mode")
        meeting.append_transcript("Bob", "待办: review projection brightness")
        minutes = meeting.generate_minutes()
        assert Path(minutes["path"]).exists()

        daily = LocalDailyService(workspace, audit)
        reminder = daily.create_reminder("明天9点提醒我开会")
        assert reminder["reminder"]["remind_at"]
        first_event = daily.create_event("明天9点 项目同步")
        second_event = daily.create_event("明天9点半 冲突测试")
        assert second_event["conflicts"]
        assert daily.agenda("tomorrow")["events"]

        file_search = LocalFileSearchService(workspace, audit, (workspace.root,))
        search_result = file_search.search("liability termination")
        assert search_result["count"] >= 1
        assert "liability" in search_result["matches"][0]["snippets"][0].lower()

        shared_space = SharedSpaceService(workspace, audit)
        shared_note = shared_space.put_note("office_computer_note", "决定: use shared inbox\n待办: analyze file")
        assert shared_note.workspace_name == "shared_inbox/office_computer_note.md"
        assert shared_space.list_files()
        shared_analysis = documents.analyze_text_file(shared_note.workspace_name)
        assert shared_analysis["key_value_pairs"]["决定"] == "use shared inbox"

        scanning = ScanService(workspace, audit)
        card = workspace.write_text("card_ocr.txt", "Ada Lovelace\nOpenClaw\nada@example.com\n1234567890")
        assert card.exists()
        parsed = scanning.analyze_business_card_text("card_ocr.txt")
        assert parsed["emails"]
        ocr_summary = scanning.summarize_ocr_text("card_ocr.txt")
        assert ocr_summary["line_count"] >= 4
        scan_input = workspace.write_text("scan_input.png", "not a real image")
        assert scan_input.exists()
        scan_metadata = scanning.register_scan_image("scan_input.png")
        assert scan_metadata["status"] == "registered"
        ocr_result = scanning.run_ocr("scan_input.png")
        assert ocr_result["status"] in {"backend_missing", "error", "timeout", "ok"}
        non_image_result = scanning.run_ocr("card_ocr.txt")
        assert non_image_result["status"] == "blocked"
        capture_readiness = scanning.capture_readiness("scan_input.png")
        assert capture_readiness["status"] in {"needs_adjustment", "ready_to_capture", "error", "backend_missing"}

        projection = ProjectionService(root / "projection", audit)
        projection_output = projection.render_markdown("Status", "All systems checked.")
        assert Path(projection_output["path"]).exists()
        countdown = projection.render_countdown("Timer", 90)
        assert Path(countdown["path"]).exists()
        confirmation = projection.render_confirmation("Confirm", ["ship P0"], ["Alice review audit"])
        assert Path(confirmation["path"]).exists()
        status_card = projection.render_status_card("Security", "sandbox")
        assert Path(status_card["path"]).exists()
        action_card_direct = projection.render_action_card("Actions", ["review notes"], decisions=["ship P0"])
        assert Path(action_card_direct["path"]).exists()
        calibration_profile = build_display_profile(
            calibration={
                "brightness": {"status": "too_dark"},
                "focus": {"status": "needs_focus"},
                "keystone": {"status": "needs_adjustment", "horizontal_skew_pct": 4.0, "vertical_skew_pct": 3.0},
            },
            mode="calibration",
        )
        assert calibration_profile.brightness >= 1.28
        assert calibration_profile.keystone_x != 0

        scene = SceneService(audit)
        event = scene.report_event("paper_detected", "纸质合同在桌面上", 0.9)
        assert "扫描" in event["suggestion"]
        lelamp = LeLampExperienceService(audit=audit, scene=scene, projection=projection)
        assert lelamp.state_cue("listening")["recording"] == "scanning"
        action_card = lelamp.render_action_confirmation("Confirm", ["review notes"], decisions=["ship P0"])
        assert Path(action_card["projection"]["path"]).exists()

        environment = EnvironmentSensingService(audit=audit, scene=scene)
        env_result = environment.ingest({"presence": True, "lux": 30, "speech_active": True, "people_count": 2})
        assert env_result["event_count"] >= 2
        camera_events = scene_events_from_metrics({"brightness": 30, "large_rectangles": 1, "largest_area_ratio": 0.2})
        assert any(item["event_type"] == "ambient_too_dark" for item in camera_events)

        memory = MemoryService(root / "memory" / "memory.jsonl", audit)
        memory.remember("meeting_template", "Use decisions and action items", "preference")
        assert memory.search("meeting_template")

        desktop = DesktopService(audit, permission_mode=PermissionMode.SANDBOX, backend="audit_only")
        desktop_result = desktop.request_operation("send email")
        assert desktop_result["allowed"] is False
        workflow_needs_confirmation = desktop.execute_workflow("smoke workflow", ["打开网页 https://example.com"], authorized=False)
        assert workflow_needs_confirmation["status"] == "needs_confirmation"
        workflow_setup = desktop.build_supervised_setup("smoke workflow", ["打开网页 https://example.com"])
        assert workflow_setup["status"] == "needs_target_setup"
        assert workflow_setup["target_setup"]["restart_required"] is True
        workflow_blocked = desktop.execute_workflow("smoke workflow", ["打开网页 https://example.com"], authorized=True)
        assert workflow_blocked["status"] == "blocked"
        assert desktop.open_app("计算器")["status"] in {"planned", "unavailable"}
        assert desktop.mouse_click()["status"] in {"planned", "blocked"}
        assert desktop.send_hotkey("ctrl+l")["status"] in {"planned", "blocked"}
        assert desktop.capture_screenshot()["status"] in {"planned", "blocked"}
        assert desktop.low_level_probe()["status"] == "adapter_ready"
        desktop_tasks = DesktopTaskQueue(workspace, audit)
        desktop_task = desktop_tasks.request_task(
            "在办公电脑上人工确认共享文件",
            ["打开 shared_inbox", "查看文件", "用户确认后再执行"],
            source="smoke_test",
        )
        assert desktop_task["status"] == "requested"
        assert Path(str(desktop_task["path"])).exists()
        assert desktop_tasks.list_tasks()["tasks"]
        approved_task = desktop_tasks.update_status(str(desktop_task["id"]), "approved", actor="tester")
        assert approved_task["status"] == "approved"
        companion = DesktopCompanionService(workspace=workspace, audit=audit)
        assert companion.status()["execution_default"] == "audit_only"
        assert companion.list_approved_tasks()["count"] >= 1
        companion_result = companion.execute_task(str(desktop_task["id"]))
        assert companion_result["status"] == "planned"
        unapproved = desktop_tasks.request_task("未批准任务", ["打开浏览器"], source="smoke_test")
        blocked_companion = companion.execute_task(str(unapproved["id"]))
        assert blocked_companion["status"] == "blocked"
        browser_automation = BrowserAutomationService(workspace, audit, OfficeAgentConfig(workspace_dir=workspace.root).normalized())
        assert browser_automation.status(check_launch=False)["status"] in {"adapter_ready", "backend_missing"}
        assert parse_browser_step("open https://example.com").action == "goto"
        browser_task = desktop_tasks.request_task(
            "打开测试网页并截图",
            ["open https://example.com", "extract text", "screenshot"],
            source="smoke_test",
            requires_full_control=False,
        )
        unauthorized_browser = browser_automation.execute_task(browser_task, authorized=False)
        assert unauthorized_browser["status"] == "needs_confirmation"
        desktop_tasks.update_status(str(browser_task["id"]), "approved", actor="tester")
        approved_browser_task = desktop_tasks.get_task(str(browser_task["id"]))
        maybe_browser = browser_automation.execute_task(approved_browser_task, authorized=True)
        assert maybe_browser["status"] in {"completed", "backend_missing", "failed"}
        assert Path(str(maybe_browser["report_path"])).exists()

        xiaoai = XiaoAiService(audit)
        calculation = xiaoai.answer_utility("计算 36*18")
        assert calculation["value"] == 648
        conversion = xiaoai.answer_utility("1米等于多少厘米")
        assert conversion["converted_value"] == 100

        smart_home = SmartHomeService(
            audit,
            SmartHomeConfig(entity_map={"客厅灯": "light.living_room"}),
        )
        smart_result = smart_home.control("打开客厅灯")
        assert smart_result["status"] == "needs_config"
        assert smart_result["parsed"]["entity_id"] == "light.living_room"
        mobile = MobileBridgeService(audit, MobileBridgeConfig())
        assert mobile.status()["status"] == "needs_config"
        assert mobile.request("找手机")["status"] == "needs_config"
        assert mobile.request("打电话给 12345678")["status"] == "needs_confirmation"

        screen_summary = build_screen_summary("Project plan\nhttps://example.com\nowner@example.com")
        assert "owner@example.com" in screen_summary

        p0 = P0OfficeService(
            workspace=workspace,
            audit=audit,
            meeting=meeting,
            projection=projection,
            daily=daily,
            file_search=file_search,
            screen=ScreenContextService(workspace, audit),
        )
        package = p0.generate_meeting_followup_package()
        assert package["status"] == "completed"
        assert package["email"]["status"] == "completed"
        assert package["email"]["provider"] == "local_rules"
        assert Path(package["email_draft_path"]).exists()

        report_outline = documents.create_report_outline(["contract.txt"], "Demo Report")
        assert report_outline["status"] == "completed"
        assert Path(str(report_outline["outline_path"])).exists()
        key_data_table = documents.extract_table_from_text("contract.txt")
        assert key_data_table["status"] == "completed"
        assert Path(str(key_data_table["table_path"])).exists()

        planner = TaskPlanner(audit)
        plan = planner.plan("把会议内容整理成邮件草稿", ["contract.txt"])
        assert plan["steps"]
        assert plan["safety"]["default_permission"] == "sandbox"
        assert all("inputs" in step and "outputs" in step for step in plan["steps"])
        router = OfficeIntentRouter(audit)
        risky_route = router.route("请全权操作电脑发送邮件")
        assert risky_route.skill == "desktop_operator"
        assert risky_route.requires_confirmation is True

        runtime = build_runtime(
            OfficeAgentConfig(
                workspace_dir=root / "runtime_workspace",
                audit_log_path=root / "runtime_logs" / "audit.jsonl",
                projection_dir=root / "runtime_projection",
                memory_path=root / "runtime_memory" / "memory.jsonl",
                allowed_roots=(),
            ).normalized()
        )
        security = runtime.security_status()
        assert security["permission_mode"] == "sandbox"
        assert security["desktop_backend"] == "audit_only"
        assert str(runtime.config.workspace_dir) in security["allowed_roots"]
        readiness = runtime.readiness_report()
        assert readiness["summary"]["counts"]
        assert any(item["capability"] == "安全边界" for item in readiness["items"])
        assert any(item["status"] in {"backend_missing", "needs_hardware"} for item in readiness["items"])
        allowed_transcript = root / "allowed_transcript.txt"
        allowed_transcript.write_text("Alice: 决定: ship P0\nBob: 待办: review audit\n", encoding="utf-8")
        runtime.workspace.allowed_roots = (runtime.workspace.root, root)
        imported_transcript_name = _ensure_workspace_file(runtime, str(allowed_transcript))
        assert imported_transcript_name == "allowed_transcript.txt"
        runtime_shared = SharedSpaceService(runtime.workspace, runtime.audit)
        runtime_shared_note = runtime_shared.put_note("runtime_shared_note", "Ada\nOpenClaw\nada@example.com\n1234567890")
        assert _ensure_workspace_relative_file(runtime, runtime_shared_note.workspace_name) == runtime_shared_note.workspace_name
        outside_runtime_transcript = root.parent / f"{root.name}_blocked_transcript.txt"
        outside_runtime_transcript.write_text("Alice: 决定: blocked\n", encoding="utf-8")
        blocked_args = type(
            "Args",
            (),
            {
                "transcript": str(outside_runtime_transcript),
                "title": "Blocked",
                "participant": [],
                "recipient": "team@example.com",
                "no_reminders": False,
                "no_projection": False,
            },
        )()
        try:
            blocked_followup = run_tool(runtime, "followup", blocked_args)
            assert blocked_followup["status"] == "blocked"
        finally:
            outside_runtime_transcript.unlink(missing_ok=True)
        skills = runtime.skills.list_skills()
        assert any(skill["name"] == "desktop_operator" and skill["mode"] == "full_control" for skill in skills)
        assert all("input_contract" in skill and "output_contract" in skill for skill in skills)
        full_control_result = runtime.desktop.request_operation("click through app")
        assert full_control_result["allowed"] is False
        runtime_task = runtime.desktop_tasks.request_task("review desktop plan", ["manual review"], source="smoke_test")
        assert runtime_task["workspace_name"].startswith("desktop_tasks/")
        console = WebConsoleServer(runtime, token="test")
        scene_ctx = RequestContext(request_id="scene-smoke", actor="smoke", source_ip="127.0.0.1")
        desktop_setup = console.api_desktop_workflow_setup({"goal": "smoke desktop setup", "steps": ["打开网页 https://example.com"]}, scene_ctx)
        assert desktop_setup["status"] == "needs_target_setup"
        assert desktop_setup["task_id"]
        scene_env = console.api_scene_environment(
            {"projector_blocked": True, "speech_active": True, "people_count": 2, "calendar_event_now": True, "lux": 35},
            scene_ctx,
        )
        assert scene_env["suggestions"]
        scene_suggestions = console.api_scene_workflow_suggestions(10, scene_ctx)
        actions = {str(item["action"]) for item in scene_suggestions["suggestions"]}
        assert {"projection_obstruction_prompt", "meeting_mode_prompt", "display_profile_adjustment"} <= actions
        blocked_scene_trigger = console.api_scene_workflow_trigger({"action": "projection_obstruction_prompt"}, scene_ctx)
        assert blocked_scene_trigger["status"] == "needs_confirmation"
        projection_scene_trigger = console.api_scene_workflow_trigger(
            {"action": "projection_obstruction_prompt", "authorized": True, "event": {"event_type": "projection_blocked", "description": "投影路径可能被遮挡。"}},
            scene_ctx,
        )
        assert projection_scene_trigger["status"] == "completed"
        assert Path(str(projection_scene_trigger["projection"]["path"])).exists()
        scan_scene_trigger = console.api_scene_workflow_trigger(
            {"action": "scan_document", "authorized": True, "event": {"event_type": "paper_detected", "description": "纸质文件在桌面上。"}},
            scene_ctx,
        )
        assert scan_scene_trigger["status"] == "completed"
        assert scan_scene_trigger["desktop_task"]["status"] == "requested"
        assert scan_scene_trigger["next_url"].startswith("/documents?scan=1")
        assert scan_scene_trigger["scan_request"]["recommended_endpoint"] == "/api/scan/capture"
        enterprise_status = console.api_enterprise_local_platform_status(scene_ctx)
        assert enterprise_status["status"] in {"not_built", "available"}
        enterprise_bundle = console.api_enterprise_local_platform_build({"include_samples": True}, scene_ctx)
        assert enterprise_bundle["status"] == "completed"
        assert Path(str(enterprise_bundle["bundle_path"])).exists()
        assert Path(str(enterprise_bundle["manifest_path"])).exists()
        assert console.api_enterprise_local_platform_status(scene_ctx)["status"] == "available"
        voice_status = console.api_voice_status(scene_ctx)
        assert voice_status["conversation"]["status"] == "available"
        voice_blocked = console.api_voice_conversation_start({"authorized": False}, scene_ctx)
        assert voice_blocked["status"] == "needs_confirmation"
        voice_session = console.api_voice_conversation_start({"authorized": True, "wake_word": "小灯"}, scene_ctx)
        assert voice_session["status"] == "completed"
        voice_session_id = str(voice_session["session_id"])
        ignored_turn = console.api_voice_conversation_turn({"session_id": voice_session_id, "text": "帮我看一下状态"}, scene_ctx)
        assert ignored_turn["status"] == "waiting_wake_word"
        completed_turn = console.api_voice_conversation_turn({"session_id": voice_session_id, "text": "小灯 你是谁", "remember": True}, scene_ctx)
        assert completed_turn["status"] == "completed"
        assert completed_turn["session"]["turn_count"] == 1
        assert completed_turn["turn"]["remembered"]
        stopped_voice = console.api_voice_conversation_stop({"session_id": voice_session_id}, scene_ctx)
        assert stopped_voice["session"]["status"] == "stopped"
        meeting_mode = console.api_meeting_mode_enable({"title": "Local realtime smoke", "participants": ["Alice", "Bob"]}, scene_ctx)
        assert meeting_mode["meeting_mode_enabled"] is True
        local_turn = console.api_meeting_local_realtime_turn({"speaker": "Alice", "text": "决定: ship local realtime"}, scene_ctx)
        assert local_turn["status"] == "completed"
        assert local_turn["speaker_counts"]["Alice"] == 1
        local_turn_2 = console.api_meeting_local_realtime_turn({"speaker": "Bob", "text": "待办: review transcript"}, scene_ctx)
        assert local_turn_2["turn_count"] == 2
        local_export = console.api_meeting_local_realtime_export({}, scene_ctx)
        assert local_export["status"] == "completed"
        assert Path(str(local_export["transcript_path"])).exists()
        validation_status = console.api_product_validation_status(scene_ctx)
        assert validation_status["summary"]["total"] >= 6
        validation_ids = {str(item["id"]) for item in validation_status["items"]}
        assert {
            "projection_display_substitute",
            "physical_projection_hardware",
            "meeting_asr_diarization",
            "document_scanning",
            "voice_scene_awareness",
            "desktop_full_control",
        } <= validation_ids
        projection_validation = console.api_product_validation_run(
            {"test_id": "projection_display_substitute", "ambient_lux": 260},
            scene_ctx,
        )
        assert projection_validation["status"] == "completed"
        assert Path(str(projection_validation["json_path"])).exists()
        physical_projection_validation = console.api_product_validation_run(
            {
                "test_id": "physical_projection_hardware",
                "authorized": True,
                "display_readable": False,
                "focus_ok": False,
                "keystone_ok": False,
                "brightness_ok": False,
            },
            scene_ctx,
        )
        assert physical_projection_validation["report"]["status"] == "needs_hardware"
        meeting_validation = console.api_product_validation_run(
            {"test_id": "meeting_asr_diarization", "authorized": True, "live_capture": False},
            scene_ctx,
        )
        assert meeting_validation["report"]["status"] in {"adapter_ready", "completed"}
        document_validation = console.api_product_validation_run(
            {"test_id": "document_scanning", "authorized": True, "generate_demo_image": True},
            scene_ctx,
        )
        assert document_validation["report"]["status"] in {"backend_missing", "adapter_ready", "completed"}
        document_steps = {str(step["id"]): step for step in document_validation["report"]["steps"]}
        assert document_steps["scan_image_pipeline"]["status"] == "completed"
        assert "ocr_structure" in document_steps
        voice_scene_validation = console.api_product_validation_run(
            {"test_id": "voice_scene_awareness", "authorized": True},
            scene_ctx,
        )
        assert voice_scene_validation["report"]["status"] == "completed"
        validation_service = TargetValidationService(runtime, projection_preview_url="http://127.0.0.1:8765/")
        class _FakeSegmentationLLM:
            def complete(self, **_: object) -> str:
                return '[{"speaker":"Alice","text":"决定: ship ASR validation"},{"speaker":"Bob","text":"待办: review benchmark"}]'

        validation_service._text_llm = lambda: _FakeSegmentationLLM()  # type: ignore[method-assign]
        segmentation_step, segmentation_artifact = validation_service._run_transcript_speaker_segmentation(
            "Alice 决定 ship ASR validation. Bob review benchmark.",
            participants=["Alice", "Bob"],
            source="smoke_asr",
        )
        assert segmentation_step.status == "completed"
        assert segmentation_artifact["speaker_counts"] == {"Alice": 1, "Bob": 1}
        validation_service._synthesize_demo_meeting_audio = lambda participants: (  # type: ignore[method-assign]
            ValidationStep("demo_meeting_audio", "API 演示会议音频生成", "completed", [], {"workspace_name": "demo.wav"}),
            {"status": "completed", "workspace_name": "demo.wav"},
        )
        validation_service._run_audio_file_asr_validation = lambda workspace_name: (  # type: ignore[method-assign]
            ValidationStep("audio_file_asr_validation", "上传音频 ASR 验收", "completed", [], {"transcript": "Alice: ok\nBob: ok"}),
            {"status": "completed", "transcript": "Alice: ok\nBob: ok"},
        )
        demo_meeting_validation = validation_service.run_meeting_asr_diarization(
            {"authorized": True, "use_demo_audio": True, "participants": ["Alice", "Bob"]}
        )
        assert demo_meeting_validation["status"] == "completed"
        synthetic_completed = validation_service._item(
            "meeting_asr_diarization",
            "智能投影与会议助手",
            "多人语音识别/实时转写/自动分角色",
            "completed",
            "smoke",
            [
                ValidationStep("speaker_role_loop", "本地 speaker turn 分角色统计", "completed", [], {"speaker_counts": {"Alice": 1, "Bob": 1}}),
                ValidationStep("audio_file_asr_validation", "上传音频 ASR 验收", "completed", [], {"transcript": "Alice: ok\nBob: ok"}),
                ValidationStep("api_speaker_turn_segmentation", "ASR 文本 API 分角色", "completed", [], {"speaker_counts": {"Alice": 1, "Bob": 1}}),
            ],
            run_label="smoke",
        )
        assert synthetic_completed["status"] == "completed"
        desktop_validation = console.api_product_validation_run(
            {"test_id": "desktop_full_control", "authorized": False, "steps": ["打开网页 about:blank"]},
            scene_ctx,
        )
        assert desktop_validation["report"]["status"] in {"adapter_ready", "needs_confirmation"}
        target_result = runtime.workspace.root / "validation_reports" / "desktop_full_control_target_result.json"
        target_result.parent.mkdir(parents=True, exist_ok=True)
        target_result.write_text(
            json.dumps(
                {
                    "ok": True,
                    "data": {
                        "status": "completed",
                        "report": {
                            "status": "completed",
                            "steps": [
                                {
                                    "id": "desktop_preflight",
                                    "status": "completed",
                                    "label": "目标机 GUI/工具预检",
                                    "evidence": ["desktop.desktop_preflight"],
                                    "details": {},
                                },
                                {
                                    "id": "input_probe",
                                    "status": "completed",
                                    "label": "授权鼠标键盘输入后端探针",
                                    "evidence": ["desktop.input_probe"],
                                    "details": {},
                                },
                                {
                                    "id": "low_level_control_probe",
                                    "status": "completed",
                                    "label": "授权低层鼠标/键盘/截图控制探针",
                                    "evidence": ["desktop.low_level_probe"],
                                    "details": {},
                                },
                                {
                                    "id": "execution_probe",
                                    "status": "completed",
                                    "label": "授权执行探针",
                                    "evidence": ["desktop.execute_workflow"],
                                    "details": {},
                                }
                            ],
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        assert validation_service.desktop_full_control_status()["status"] == "completed"
        ppt_summary = console.api_projection_summarize_ppt_page(
            {"image_data_url": "data:image/png;base64," + "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=", "title": "PPT smoke"},
            RequestContext(request_id="smoke", actor="smoke", source_ip="127.0.0.1"),
        )
        assert ppt_summary["status"] == "backend_missing"
        pptx_path = runtime.workspace.root / "summary_smoke.pptx"
        with zipfile.ZipFile(pptx_path, "w") as archive:
            archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>')
            archive.writestr(
                "ppt/slides/slide1.xml",
                '<?xml version="1.0" encoding="UTF-8"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>总结页标题</a:t></a:r></a:p><a:p><a:r><a:t>关键行动项</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
            )
        pptx_summary = console.api_projection_summarize_ppt_page(
            {"file_path": "summary_smoke.pptx", "slide_index": 1, "title": "PPTX text smoke", "render_projection": True},
            RequestContext(request_id="smoke", actor="smoke", source_ip="127.0.0.1"),
        )
        assert pptx_summary["status"] == "completed"
        assert pptx_summary["provider"] == "local_pptx_text"
        assert Path(str(pptx_summary["summary_path"])).exists()
        assert Path(str(pptx_summary["projection_path"])).exists()

        audit_lines = audit.path.read_text(encoding="utf-8").splitlines()
        assert audit_lines
        events = [json.loads(line) for line in audit_lines]
        assert any(event["status"] == "blocked" and event["action"] == "workspace.import" for event in events)
        for event in events:
            assert {"action", "status", "target", "details", "timestamp"}.issubset(event)

        print("office_agent smoke test passed")


if __name__ == "__main__":
    main()
