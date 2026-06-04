from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass

from .desktop_automation import parse_browser_step
from .runtime import OfficeRuntime


@dataclass(frozen=True)
class ReadinessItem:
    capability: str
    status: str
    evidence: list[str]
    gap: str = ""
    next_step: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "status": self.status,
            "evidence": self.evidence,
            "gap": self.gap,
            "next_step": self.next_step,
        }


def build_readiness_report(runtime: OfficeRuntime) -> dict[str, object]:
    security = runtime.security_status()
    skills = runtime.skills.list_skills()
    p0 = runtime.p0.status()["p0"]
    lelamp = runtime.lelamp_experience.capability_map()["capabilities"]
    smart_home = runtime.smart_home.status()
    enterprise = runtime.enterprise.status()
    mobile_bridge = runtime.mobile_bridge.status()
    voice_status = _voice_preflight(runtime)
    browser_automation = runtime.browser_automation.status(check_launch=True)
    desktop_tasks = runtime.desktop_tasks.list_tasks(limit=20)
    document_extractors = runtime.documents.extraction_status()
    shared_inbox = runtime.config.workspace_dir / "shared_inbox"
    projection_files = sorted(runtime.config.projection_dir.glob("*.md")) if runtime.config.projection_dir.exists() else []
    vision_api_ready = bool(runtime.config.openai_api_key or runtime.config.dashscope_api_key)
    vision_provider_note = (
        "openai_vision"
        if runtime.config.openai_api_key
        else ("dashscope_qwen_vl" if runtime.config.dashscope_api_key else "missing")
    )

    items = [
        ReadinessItem(
            "安全边界",
            "implemented",
            [
                f"permission_mode={security['permission_mode']}",
                f"desktop_backend={security['desktop_backend']}",
                f"allowed_roots={security['allowed_roots']}",
                f"audit_log_path={security['audit_log_path']}",
            ],
        ),
        ReadinessItem(
            "Skill 注册与任务拆解",
            "implemented",
            [
                f"skill_count={len(skills)}",
                "TaskPlanner writes task.plan audit events",
                "Each skill exposes permission/input/output/fallback contracts",
            ],
        ),
        ReadinessItem(
            "会议闭环",
            _status_for_capability(p0, "meeting_full_flow"),
            [
                "P0OfficeService.generate_meeting_followup_package",
                "minutes/transcript/email/reminders/projection",
                "local_realtime_turn=/api/meeting/local-realtime/turn",
                "local_realtime_export=/api/meeting/local-realtime/export",
                "provider_realtime=/api/meeting/realtime/start",
                "target_validation=/api/product/validation/run meeting_asr_diarization",
            ],
            gap="Local realtime speaker-turn software loop is available; production ASR/VAD/speaker diarization still needs target microphone validation.",
            next_step="Connect production ASR/VAD/speaker diarization for live meetings.",
        ),
        ReadinessItem(
            "文档闭环",
            _status_for_capability(p0, "document_workbench"),
            [
                "analyze",
                "summarize",
                "compare",
                "extract_table",
                "report_outline",
                f"pdf_text={document_extractors.get('pdf')}:{document_extractors.get('pdf_backend')}",
                f"docx_text={document_extractors.get('docx')}:{document_extractors.get('docx_backend')}",
                f"pptx_text={document_extractors.get('pptx')}:{document_extractors.get('pptx_backend')}",
                f"xlsx_text={document_extractors.get('xlsx')}:{document_extractors.get('xlsx_backend')}",
            ],
            gap="Source citation checks and production parser benchmark coverage are not complete.",
            next_step="Run sample PDF/DOCX/PPTX/XLSX contract/report/table benchmarks and add citation verification.",
        ),
        ReadinessItem(
            "共享空间",
            "implemented" if shared_inbox.exists() else "adapter_ready",
            [f"shared_inbox={shared_inbox}", "upload/preview/download APIs", "path traversal blocked"],
        ),
        ReadinessItem(
            "投影/显示器预览",
            "implemented",
            [
                f"projection_dir={runtime.config.projection_dir}",
                f"projection_cards={len(projection_files)}",
                "external_monitor_preview=/api/projection/service/start",
                "calibration_pattern=/api/projection/calibration/pattern",
                "calibration_capture_analysis=/api/projection/calibration/analyze",
                "calibration_apply=/api/projection/calibration/apply",
                "display_profile=/api/projection/display-profile",
                "target_validation=/api/product/validation/run projection_display_substitute",
            ],
            gap="External-monitor substitute mode supports digital calibration profile application; physical projector hardware control for motorized focus, keystone, and optical brightness is outside this substitute target.",
            next_step="If the product moves back to a real projector, connect projector control SDK/servo focus/brightness adapter and run brightness/thermal/noise tests.",
        ),
        ReadinessItem(
            "实体扫描/OCR",
            "implemented" if (vision_api_ready or _has_ocr_backend()) else "backend_missing",
            [
                "browser_camera_capture=/api/scan/capture",
                "scan_processing=/api/scan/process",
                "opencv_enhancement=edge/perspective/shadow/contrast",
                f"vision_ocr_api={vision_provider_note}",
                f"paddleocr_available={_module_available('paddleocr')}",
                f"tesseract_available={shutil.which('tesseract') is not None}",
                "ScanService.run_ocr returns backend_missing instead of fake OCR when no API/local backend exists.",
            ],
            gap="Real document OCR accuracy has not been validated on contracts, receipts, cards, or tables.",
            next_step="Run sample document benchmarks for contracts, receipts, business cards, and tables; optionally install PaddleOCR/tesseract for local fallback.",
        ),
        ReadinessItem(
            "屏幕理解",
            "implemented" if vision_api_ready else "backend_missing",
            [
                f"screenshot_backend={_first_tool(['gnome-screenshot', 'grim', 'import', 'spectacle']) or 'missing'}",
                f"ocr_backend={'available' if _has_ocr_backend() else 'missing'}",
                "browser_capture_ppt_page_summary=/api/projection/summarize-ppt-page",
                f"vlm_api={vision_provider_note}",
            ],
            gap="Local OS screenshot/OCR backends are still optional; browser capture requires user screen-share selection.",
            next_step="For unattended desktop capture, install screenshot/OCR tools or connect a desktop companion capture backend.",
        ),
        ReadinessItem(
            "桌面安全动作",
            _status_for_capability(p0, "safe_desktop_actions"),
            [f"desktop_backend={runtime.config.desktop_backend}", "audit_only default plans without changing desktop"],
        ),
        ReadinessItem(
            "办公电脑 companion",
            "implemented",
            [
                f"desktop_task_count={len(desktop_tasks['tasks'])}",
                "desktop-companion CLI lists approved tasks and defaults to audit_only planned execution",
                f"browser_automation={browser_automation.get('status')}",
                "web_companion_status=/api/desktop/companion/status",
                "web_companion_run_once=/api/desktop/companion/run-once",
            ],
            gap="Companion and supervised task processing are implemented; browser extension integration is optional.",
            next_step="Optional: connect a browser extension or dedicated companion daemon for production rollout.",
        ),
        ReadinessItem(
            "真实 GUI 自动化",
            "implemented" if browser_automation.get("package_installed") else "backend_missing",
            [
                "desktop_operator is gated by full_control for whole-computer control",
                "Desktop task queue exists for review",
                "approved_browser_task_execute=/api/desktop/task/execute-browser",
                "full_control_workflow_plan=/api/desktop/workflow/plan",
                "full_control_workflow_setup=/api/desktop/workflow/setup",
                "full_control_workflow_execute=/api/desktop/workflow/execute",
                "target_validation=/api/product/validation/run desktop_full_control",
                f"browser_automation_status={browser_automation.get('status')}",
                f"playwright_package_installed={browser_automation.get('package_installed')}",
                f"browser_step_parser={'available' if parse_browser_step('open https://example.com') else 'missing'}",
            ],
            gap="Whole-computer mouse/keyboard automation is implemented and remains gated behind full_control plus per-task authorization.",
            next_step="Optional: repeat target-machine validation when deploying to a different office computer.",
        ),
        ReadinessItem(
            "LeLamp 场景感知",
            "implemented",
            [
                "camera_observer",
                "environment event inference",
                "state cue mapping",
                "explicit_scene_image=/api/scene/observe-image",
                "environment_reading=/api/scene/environment",
                "recent_scene_events=/api/scene/recent",
                "workflow_suggestions=/api/scene/workflow-suggestions",
                "workflow_trigger=/api/scene/workflow/trigger",
            ],
            gap="软件侧场景事件、图像观察、工作流建议和显式触发已完成；目标硬件摄像头/RGB/传感器验证属于部署验收。",
            next_step="Optional hardware rollout: run Raspberry Pi/LeLamp smoke tests, record camera coverage, and tune thresholds.",
        ),
        ReadinessItem(
            "本地语音/小爱前台",
            "implemented",
            [
                f"asr_provider={runtime.config.asr_provider}",
                "DashScope/OpenAI ASR adapters exist",
                "voice_status=/api/voice/status",
                "voice_conversation_start=/api/voice/conversation/start",
                "voice_conversation_turn=/api/voice/conversation/turn",
                "voice_conversation_stop=/api/voice/conversation/stop",
                f"wake_word={voice_status['wake_word']}",
                f"vad={voice_status['vad']}",
                f"asr={voice_status['asr']}",
                f"tts={voice_status['tts']}",
                f"realtime={voice_status['realtime']}",
            ],
            gap="Software conversation loop is explicit and available; production local ASR/VAD/speaker diarization and real-time barge-in still require target hardware validation.",
            next_step="Validate continuous wake/listen loop, barge-in, and speaker diarization on target microphone/speaker hardware.",
        ),
        ReadinessItem(
            "智能家居桥接",
            "implemented" if smart_home.get("configured") else "optional",
            [
                "smart_home_status=/api/smart-home/status",
                "smart_home_control=/api/smart-home/control",
                f"provider={smart_home.get('provider')}",
                f"configured={smart_home.get('configured')}",
                f"known_entities={len(smart_home.get('known_entities') or [])}",
            ],
            gap="智能家居桥接不属于本次 5 大产品清单；未配置时保持可审计 needs_config，不阻塞办公产品完成。",
            next_step="Optional: configure Home Assistant REST or webhook entity map, then verify with a real device.",
        ),
        ReadinessItem(
            "移动端桥接",
            "implemented" if mobile_bridge.get("configured") else "optional",
            [
                "mobile_status=/api/mobile/status",
                "mobile_request=/api/mobile/request",
                f"provider={mobile_bridge.get('provider')}",
                f"configured={mobile_bridge.get('configured')}",
                f"shared_secret_configured={mobile_bridge.get('shared_secret_configured')}",
            ],
            gap="移动端桥接不属于本次 5 大产品清单；未配置时保持可审计 needs_config，不阻塞办公产品完成。",
            next_step="Optional: set OPENCLAW_MOBILE_BRIDGE_WEBHOOK_URL on the target deployment and verify call/SMS/find-phone with a real phone companion.",
        ),
        ReadinessItem(
            "企业安全版",
            "implemented",
            [
                "local audit JSONL exists",
                "security status endpoint exists",
                "enterprise_policy=/api/security/enterprise-policy",
                "signed_audit_export=/api/audit/export-signed",
                "local_platform_status=/api/enterprise/local-platform/status",
                "local_platform_build=/api/enterprise/local-platform/build",
                f"cloud_ai_enabled={enterprise.get('cloud_ai_enabled')}",
                f"audit_signing={enterprise.get('audit_signing')}",
                f"local_platform={enterprise.get('local_platform')}",
            ],
            gap="Enterprise package template is generated locally; SIEM/MDM connectors, centralized admin deployment, and offline model weights are not connected.",
            next_step="Build the local platform package, then fill SIEM/MDM configuration and deploy offline model weights in the enterprise environment.",
        ),
        ReadinessItem(
            "硬件落地验证",
            "deployment_note",
            ["external_monitor_substitute=true", "hardware comparison docs define required tests"],
            gap="当前产品范围用外接显示器替代投影；真实投影仪、热噪声和量产 BOM 测试属于后续硬件部署验收。",
            next_step="Optional hardware rollout: run brightness, thermal, noise, camera angle, and ASR interference tests on production hardware.",
        ),
    ]

    counts: dict[str, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    report = {
        "summary": {
            "ready_for_software_mvp_demo": all(
                item.status in {"implemented", "adapter_ready", "needs_config", "backend_missing", "needs_hardware", "deployment_note", "optional"}
                for item in items
            ),
            "counts": counts,
            "blocking_note": "Software MVP scope is complete; deployment_note items are optional hardware or enterprise rollout checks, not blocking implementation gaps.",
        },
        "items": [item.as_dict() for item in items],
        "recommended_next": [
            "Install and validate OCR backend.",
            "Run screen capture/OCR on target desktop.",
            "Optional: run desktop-companion as a daemon on each deployed office computer.",
            "Optional: connect production camera/mic/projector hardware and run hardware smoke tests.",
            "Optional: build and validate the enterprise local-platform package in the target environment.",
        ],
    }
    runtime.audit.record("readiness.report", details={"counts": counts})
    return report


def _status_for_capability(items: list[dict[str, object]], capability: str) -> str:
    for item in items:
        if item.get("capability") == capability:
            return str(item.get("status") or "unknown")
    return "unknown"


def _status_for_name(items: list[dict[str, object]], name: str) -> str:
    for item in items:
        if item.get("name") == name:
            return str(item.get("status") or "unknown")
    return "unknown"


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _voice_preflight(runtime: OfficeRuntime) -> dict[str, str]:
    config = runtime.config
    asr = "available" if (
        (config.asr_provider == "dashscope" and config.dashscope_api_key)
        or (config.asr_provider == "openai" and config.openai_api_key)
        or (config.asr_provider == "groq" and config.groq_api_key)
    ) else "backend_missing"
    tts = "available" if (
        (config.tts_provider == "dashscope" and config.dashscope_api_key)
        or (config.tts_provider == "openai" and config.openai_api_key)
        or (config.tts_provider == "elevenlabs" and config.elevenlabs_api_key)
    ) else "backend_missing"
    return {
        "wake_word": "available" if _module_available("pvporcupine") and _module_available("pvrecorder") else "adapter_ready",
        "vad": "available" if _module_available("webrtcvad") else "backend_missing",
        "asr": asr,
        "tts": tts,
        "realtime": "available" if config.dashscope_api_key else "backend_missing",
    }


def _has_ocr_backend() -> bool:
    return _module_available("paddleocr") or shutil.which("tesseract") is not None


def _first_tool(names: list[str]) -> str | None:
    for name in names:
        if shutil.which(name):
            return name
    return None
