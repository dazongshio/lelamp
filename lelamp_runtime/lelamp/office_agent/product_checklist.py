from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from .runtime import OfficeRuntime


@dataclass(frozen=True)
class ChecklistFeature:
    area: str
    feature: str
    status: str
    evidence: list[str]
    gap: str = ""
    next_step: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "area": self.area,
            "feature": self.feature,
            "status": self.status,
            "evidence": self.evidence,
            "gap": self.gap,
            "next_step": self.next_step,
        }


def build_product_checklist(runtime: OfficeRuntime) -> dict[str, object]:
    readiness = runtime.readiness_report()
    readiness_by_name = {str(item["capability"]): item for item in readiness["items"]}
    security = runtime.security_status()
    projection_cards = len(list(runtime.config.projection_dir.glob("*.md"))) if runtime.config.projection_dir.exists() else 0
    document_extractors = runtime.documents.extraction_status()
    browser_status = runtime.browser_automation.status(check_launch=False)
    voice_status = _voice_status_from_readiness(readiness_by_name)
    smart_home = runtime.smart_home.status()
    mobile = runtime.mobile_bridge.status()
    enterprise = runtime.enterprise.status()
    local_platform = enterprise.get("local_platform") if isinstance(enterprise.get("local_platform"), dict) else {}
    meeting_enabled = bool(security.get("meeting_mode_enabled"))
    vision_ready = bool(runtime.config.openai_api_key or runtime.config.dashscope_api_key)
    ocr_ready = bool(runtime.config.openai_api_key or runtime.config.dashscope_api_key)
    validation_status = _target_validation_statuses(runtime)
    meeting_asr_validated = validation_status.get("meeting_asr_diarization") == "completed"
    physical_projection_validated = validation_status.get("physical_projection_hardware") == "completed"
    document_scanning_validated = validation_status.get("document_scanning") == "completed"
    validation_steps = _target_validation_completed_steps(runtime)
    document_steps = validation_steps.get("document_scanning", set())
    document_scan_image_validated = "scan_image_pipeline" in document_steps
    document_ocr_validated = {"ocr_structure", "semantic_parse"}.issubset(document_steps)
    voice_scene_validated = validation_status.get("voice_scene_awareness") == "completed"
    desktop_full_control_validated = validation_status.get("desktop_full_control") == "completed"

    items: list[ChecklistFeature] = [
        ChecklistFeature(
            "智能投影与会议助手",
            "有线投屏/外接显示器代替投影",
            "implemented",
            ["projection_preview=/api/projection/service/start", "output_target=external_monitor", "physical_projector=display_substitute"],
        ),
        ChecklistFeature(
            "智能投影与会议助手",
            "自动校正对焦",
            "implemented" if physical_projection_validated else "needs_hardware",
            [
                "calibration_pattern=/api/projection/calibration/pattern",
                "calibration_capture_analysis=/api/projection/calibration/analyze",
                "calibration_apply=/api/projection/calibration/apply",
                "external-monitor digital keystone/brightness profile",
                "target_validation=/api/product/validation/run projection_display_substitute",
                "target_validation=/api/product/validation/run physical_projection_hardware",
            ],
            "外接显示器替代投影的数字亮度/对比度/缩放/梯形 profile 已完成；真实投影光学对焦、电机或投影仪 SDK 仍需目标硬件验收。" if not physical_projection_validated else "真实投影硬件验收已回传。",
            "接入真实投影仪 focus/keystone/brightness 控制后运行 physical_projection_hardware 验收。" if not physical_projection_validated else "",
        ),
        ChecklistFeature(
            "智能投影与会议助手",
            "环境亮度自适应调节",
            "implemented" if physical_projection_validated else "needs_hardware",
            [
                "environment_reading=/api/scene/environment",
                "display_profile=/api/projection/display-profile",
                "external-monitor preview applies digital brightness/contrast profile",
                f"target_validation_status={validation_status.get('physical_projection_hardware', 'needs_hardware')}",
            ],
            "显示器替代投影时的数字自适应已完成；真实光机亮度控制仍需 lux 传感器和投影仪 SDK/硬件接口。" if not physical_projection_validated else "真实光机亮度联动已验收。",
            "在目标硬件上接入 lux 传感器和投影亮度控制。",
        ),
        ChecklistFeature(
            "智能投影与会议助手",
            "一键进入会议模式",
            "implemented",
            ["meeting_mode_enable=/api/meeting/mode/enable", f"meeting_mode_enabled={meeting_enabled}"],
        ),
        ChecklistFeature(
            "智能投影与会议助手",
            "多人语音识别/实时转写/自动分角色",
            "implemented" if meeting_asr_validated else "adapter_ready",
            [
                "Tingwu/DashScope/OpenAI ASR adapters",
                "realtime_meeting=/api/meeting/realtime/start",
                "local_realtime_turn=/api/meeting/local-realtime/turn",
                "speaker role stats",
                "uploaded/browser audio ASR validation",
                "api_speaker_turn_segmentation validation",
                "target_validation=/api/product/validation/run meeting_asr_diarization",
                f"target_validation_status={validation_status.get('meeting_asr_diarization', 'adapter_ready')}",
                f"voice_status={voice_status}",
            ],
            "软件层支持实时 turn 追加、speaker 分角色统计、授权音频 ASR 和 API 文本分角色；当前已跑通 API 演示音频验收，真实多人声学 diarization 准确率仍需目标会议环境 benchmark。" if meeting_asr_validated else "软件层支持实时 turn 追加、speaker 分角色统计、授权音频 ASR 和 API 文本分角色；真实多人声学 diarization 准确率还需目标会议环境 benchmark。",
            "上传/录制授权会议音频跑 ASR+API 分角色验收；如需声纹级 diarization，配置 Tingwu app id 后跑实时验收。",
        ),
        ChecklistFeature(
            "智能投影与会议助手",
            "自动生成会议纪要",
            "implemented",
            ["meeting_minutes=/api/meeting/minutes", "local_rules_minutes", "P0OfficeService.generate_meeting_followup_package"],
            "无云模型时使用本地规则生成可审查纪要；配置模型后可增强语言质量。",
        ),
        ChecklistFeature(
            "智能投影与会议助手",
            "语音指令：总结这一页 PPT",
            "implemented",
            [
                "browser_capture_ppt_page_summary=/api/projection/summarize-ppt-page",
                "pptx_text_page_summary=/api/projection/summarize-ppt-page file_path+slide_index",
                "explicit user screen-share capture",
                "pptx_file_projection=/api/projection/pptx/session",
                f"openai_vision={'configured' if runtime.config.openai_api_key else 'missing'}",
                f"dashscope_qwen_vl={'configured' if runtime.config.dashscope_api_key else 'missing'}",
            ],
            "上传 PPTX 的当前页文本总结不依赖云模型；屏幕截图视觉总结需要 OpenAI/DashScope 视觉模型，且默认必须用户显式选择屏幕共享。" if not vision_ready else "屏幕截图视觉总结和 PPTX 文本总结都可用；默认仍需用户显式选择屏幕共享。",
            "如需无人值守视觉总结，安装截图/OCR 后端或接入 companion capture。",
        ),
        ChecklistFeature(
            "智能投影与会议助手",
            "实时生成内容并投影展示",
            "implemented",
            [f"projection_cards={projection_cards}", "projection_card=/api/projection/card", "markdown_file=/api/projection/markdown-file", "pptx_file_projection=/api/projection/pptx/session", "preview_url=http://127.0.0.1:8765/"],
        ),
        ChecklistFeature(
            "智能投影与会议助手",
            "会后纪要/演示总结保存、导出、邮件发送",
            "implemented",
            ["workspace outputs", "meeting_export_package=/api/meeting/export-package", "meeting_send_email=/api/meeting/send-email"],
            "邮件发送需要 SMTP 配置和显式授权；默认只生成草稿。",
        ),
        ChecklistFeature(
            "智能投影与会议助手",
            "默认不解析投影内容，需手动开启会议理解模式",
            "implemented",
            ["meeting_mode gate", "explicit summarize-ppt-page capture", "audit log records enable/capture"],
        ),
        ChecklistFeature(
            "openclaw本地工作代理",
            "文件工作区沙箱：只访问拖入/上传文件",
            "implemented",
            [f"shared_inbox={security.get('shared_inbox_dir')}", f"allowed_roots={security.get('allowed_roots')}"],
        ),
        ChecklistFeature(
            "openclaw本地工作代理",
            "文档总结/合同解析/多文件对比",
            "implemented",
            [
                "document_analyze=/api/document/analyze",
                "document_summarize=/api/document/summarize",
                "compare_text_files",
                f"pdf_text={document_extractors.get('pdf')}:{document_extractors.get('pdf_backend')}",
                f"docx_text={document_extractors.get('docx')}:{document_extractors.get('docx_backend')}",
            ],
        ),
        ChecklistFeature(
            "openclaw本地工作代理",
            "把文档整理成汇报提纲",
            "implemented",
            ["report_outline=/api/document/report-outline", "local_rules fallback", "ResponsesLLM optional"],
            "无云模型时使用本地规则生成可审查提纲；配置模型后可增强改写和归纳。",
        ),
        ChecklistFeature(
            "openclaw本地工作代理",
            "提取关键数据做表格",
            "implemented",
            ["table_extract=/api/document/table-extract", "local_rules CSV", "writes CSV"],
            "本地规则会抽取键值、决策、待办、数值和风险词；复杂表格语义抽取可用模型增强。",
        ),
        ChecklistFeature(
            "openclaw本地工作代理",
            "根据会议纪要生成邮件",
            "implemented",
            ["meeting_followup=/api/meeting/followup", "local_rules email draft", "send requires explicit authorization"],
            "无云模型时使用本地规则生成邮件草稿；邮件发送仍需 SMTP 配置和显式授权。",
        ),
        ChecklistFeature(
            "openclaw本地工作代理",
            "全自动 AI 代理/全局控制电脑/多步骤任务",
            "implemented" if desktop_full_control_validated else "adapter_ready",
            [
                "desktop_workflow_plan=/api/desktop/workflow/plan",
                "desktop_workflow_setup=/api/desktop/workflow/setup",
                "desktop_workflow_execute=/api/desktop/workflow/execute",
                "desktop_control_action=/api/desktop/control/action",
                "target_validation=/api/product/validation/run desktop_full_control",
                f"target_validation_status={validation_status.get('desktop_full_control', 'adapter_ready')}",
                f"permission_mode={security.get('permission_mode')}",
                f"desktop_backend={security.get('desktop_backend')}",
                f"browser_automation={browser_status.get('status')}",
            ],
            "目标机 full_control/local、GUI 会话、输入探针、低层控制探针和工作流执行探针已回传完成；当前控制台保留 sandbox/audit_only 作为安全默认。" if desktop_full_control_validated else "已提供计划、低层鼠标/键盘/截图控制 API、目标机验收包和门禁执行接口；当前运行时是 sandbox + audit_only，真实全局鼠标键盘控制必须在目标电脑启用 full_control/local 后端并逐任务授权。",
            "" if desktop_full_control_validated else "在办公电脑上以 full_control/local 启动，并完成 desktop_preflight、input_probe、low_level_control_probe、execution_probe 四项回传验收。",
        ),
        ChecklistFeature(
            "实体文档采集系统",
            "摄像头扫描：自动识别边界/自动拍照",
            "implemented" if document_scan_image_validated else "adapter_ready",
            ["browser_camera_capture=/api/scan/capture", "capture_readiness=/api/scan/capture-readiness", "opencv document boundary detection", "target_validation=/api/product/validation/run document_scanning"],
            "浏览器拍照/上传、自动拍照候选判断和 OpenCV 边界识别已接入；真实摄像头角度和样张准确率需目标验收。" if not document_scan_image_validated else "实体文档图像采集、自动拍照候选和边界/增强样张验收已完成。",
        ),
        ChecklistFeature(
            "实体文档采集系统",
            "图像增强：去阴影/视觉校正/清晰度增强",
            "implemented" if document_scan_image_validated else "adapter_ready",
            ["scan_processing=/api/scan/process", "edge/perspective/shadow/contrast enhancement", f"target_validation_status={validation_status.get('document_scanning', 'adapter_ready')}"],
        ),
        ChecklistFeature(
            "实体文档采集系统",
            "OCR 与表格识别",
            "implemented" if document_ocr_validated else ("adapter_ready" if ocr_ready else "backend_missing"),
            ["OpenAI/DashScope vision OCR configured" if ocr_ready else "OCR backend missing", "local_rules table CSV extraction for OCR text", "table CSV extraction for scanned structure"],
            "OpenAI/DashScope 视觉 OCR 或本地 PaddleOCR/tesseract 任一可用即可做真实图片 OCR；OCR 后的表格/结构抽取已有本地规则。",
        ),
        ChecklistFeature(
            "实体文档采集系统",
            "名片识别/合同解析/文件内容总结",
            "implemented" if document_ocr_validated else "adapter_ready",
            ["business_card=/api/scan/business-card", "contract scan structure", "summarize_ocr=/api/scan/summarize-ocr", "target_validation=document_scanning"],
            "真实名片/合同样本集还未通过 OCR/结构识别验收。" if not document_ocr_validated else "",
        ),
        ChecklistFeature(
            "多模态办公场地场景感知",
            "唤醒词/连续对话/多轮上下文/长期记忆",
            "implemented" if voice_scene_validated else "adapter_ready",
            [
                "voice_status=/api/voice/status",
                "conversation_start=/api/voice/conversation/start",
                "conversation_turn=/api/voice/conversation/turn",
                "conversation_stop=/api/voice/conversation/stop",
                "assistant_message=/api/assistant/message",
                "memory JSONL authorized writes",
                "target_validation=/api/product/validation/run voice_scene_awareness",
            ],
            "软件层完成显式连续对话、文本唤醒词门控、多轮上下文和授权记忆写入；真实连续麦克风唤醒、barge-in、多说话人语音链路仍需目标硬件验收。",
            "在目标麦克风/扬声器上验证本地唤醒、VAD、barge-in 和多说话人链路。",
        ),
        ChecklistFeature(
            "多模态办公场地场景感知",
            "检测纸质文件并提升扫描",
            "implemented" if voice_scene_validated else "adapter_ready",
            ["scene_observe_image=/api/scene/observe-image", "workflow_suggestions=/api/scene/workflow-suggestions", "trigger scan_document=/api/scene/workflow/trigger", "next_url=/documents?scan=1", "target_validation=voice_scene_awareness"],
            "软件层可从显式图像/事件生成扫描任务、提醒并跳转到 Documents 扫描入口；目标摄像头视角和纸张检测覆盖率还需实机验收。",
            "在目标硬件上验证纸张检测覆盖率和自动拍照候选阈值。",
        ),
        ChecklistFeature(
            "多模态办公场地场景感知",
            "检测投影遮盖并提示",
            "implemented" if voice_scene_validated else "adapter_ready",
            ["environment_reading projector_blocked", "trigger projection_obstruction_prompt=/api/scene/workflow/trigger", "projection status card", "target_validation=voice_scene_awareness"],
            "软件层可根据读数/事件生成外接显示器提示；真实投影画面摄像头联动还需硬件验证。",
            "接入目标摄像头/投影面检测，验证遮挡误报率。",
        ),
        ChecklistFeature(
            "多模态办公场地场景感知",
            "工作流建议/自动提醒/桌面状态触发任务",
            "implemented" if voice_scene_validated else "adapter_ready",
            ["workflow_suggestions=/api/scene/workflow-suggestions", "workflow_trigger=/api/scene/workflow/trigger", "daily reminders", "desktop task queue", "target_validation=voice_scene_awareness"],
            "当前是用户点击后的显式触发，不做无人确认的自动动作。",
            "上线前继续收敛企业场景规则、优先级和误触发确认策略。",
        ),
        ChecklistFeature(
            "全流程安全架构",
            "文件访问分级：文件级/系统级",
            "implemented",
            [f"allowed_roots={security.get('allowed_roots')}", "workspace/shared_inbox enforcement"],
        ),
        ChecklistFeature(
            "全流程安全架构",
            "权限分层：沙箱模式/全权模式",
            "implemented",
            [f"permission_mode={security.get('permission_mode')}", "full_control confirmation endpoints"],
        ),
        ChecklistFeature(
            "全流程安全架构",
            "操作记录日志供审查",
            "implemented",
            [f"audit_log_path={security.get('audit_log_path')}", "audit recent/search/export APIs"],
        ),
        ChecklistFeature(
            "全流程安全架构",
            "企业可选配：本地算力与数据平台",
            "implemented",
            [
                "enterprise_policy=/api/security/enterprise-policy",
                "signed_audit_export=/api/audit/export-signed",
                "local_platform_status=/api/enterprise/local-platform/status",
                "local_platform_build=/api/enterprise/local-platform/build",
                f"local_platform={local_platform.get('status', 'not_built')}",
            ],
            "已提供本地算力/数据平台交付包模板；SIEM/MDM 凭据、真实离线模型权重和集中管理平台仍需企业部署接入。",
            "在企业环境填入 SIEM/MDM 配置并导入本地模型权重。",
        ),
    ]

    grouped: dict[str, list[dict[str, object]]] = {}
    for item in items:
        grouped.setdefault(item.area, []).append(item.as_dict())
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    unfinished_statuses = {"adapter_ready", "backend_missing", "needs_backend", "needs_config", "needs_hardware", "blocked", "unavailable", "failed", "error"}
    unfinished = [
        item.as_dict()
        for item in items
        if item.status in unfinished_statuses
    ]
    deployment_notes = [
        item.as_dict()
        for item in items
        if item.status == "implemented" and (item.gap or item.next_step)
    ]
    return {
        "summary": {
            "total": len(items),
            "counts": counts,
            "software_mvp_ready": bool(readiness["summary"].get("ready_for_software_mvp_demo")),
            "remaining_count": len(unfinished),
            "deployment_note_count": len(deployment_notes),
        },
        "areas": grouped,
        "items": [item.as_dict() for item in items],
        "remaining": unfinished,
        "deployment_notes": deployment_notes,
        "readiness_summary": readiness["summary"],
    }


def _voice_status_from_readiness(readiness_by_name: dict[str, dict[str, object]]) -> str:
    item = readiness_by_name.get("本地语音/小爱前台") or {}
    return str(item.get("status") or "adapter_ready")


def _target_validation_statuses(runtime: OfficeRuntime) -> dict[str, str]:
    report_dir = runtime.config.workspace_dir / "validation_reports"
    if not report_dir.is_dir():
        return {}
    statuses: dict[str, str] = {}
    for path in sorted(report_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        test_id = str(payload.get("test_id") or "")
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        status = str(report.get("status") or "")
        if test_id == "desktop_full_control" and status == "completed" and not _desktop_report_has_full_control_evidence(report):
            status = "adapter_ready"
        if test_id == "document_scanning" and status == "completed" and not _document_scanning_report_has_full_pipeline_evidence(report):
            status = "adapter_ready"
        if test_id and status and test_id not in statuses:
            statuses[test_id] = status
    target_result = report_dir / "desktop_full_control_target_result.json"
    if target_result.is_file():
        try:
            payload = json.loads(target_result.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
        report = data.get("report") if isinstance(data, dict) and isinstance(data.get("report"), dict) else {}
        if report.get("status") == "completed" and _desktop_report_has_full_control_evidence(report):
            statuses["desktop_full_control"] = "completed"
    return statuses


def _desktop_report_has_full_control_evidence(report: dict[str, object]) -> bool:
    steps = report.get("steps") if isinstance(report.get("steps"), list) else []
    completed = {
        str(step.get("id"))
        for step in steps
        if isinstance(step, dict) and step.get("status") == "completed"
    }
    return {"desktop_preflight", "input_probe", "low_level_control_probe", "execution_probe"}.issubset(completed)


def _document_scanning_report_has_full_pipeline_evidence(report: dict[str, object]) -> bool:
    steps = report.get("steps") if isinstance(report.get("steps"), list) else []
    completed = {
        str(step.get("id"))
        for step in steps
        if isinstance(step, dict) and step.get("status") == "completed"
    }
    return {"scan_image_pipeline", "ocr_structure", "semantic_parse"}.issubset(completed)


def _target_validation_completed_steps(runtime: OfficeRuntime) -> dict[str, set[str]]:
    report_dir = runtime.config.workspace_dir / "validation_reports"
    if not report_dir.is_dir():
        return {}
    steps_by_test: dict[str, set[str]] = {}
    for path in sorted(report_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        test_id = str(payload.get("test_id") or "")
        if not test_id or test_id in steps_by_test:
            continue
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        steps = report.get("steps") if isinstance(report.get("steps"), list) else []
        steps_by_test[test_id] = {
            str(step.get("id"))
            for step in steps
            if isinstance(step, dict) and step.get("status") == "completed"
        }
    return steps_by_test
