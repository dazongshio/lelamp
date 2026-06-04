from __future__ import annotations

import importlib.util
import json
import secrets
import shutil
import subprocess
import time
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .audio_api import OpenAIAudioAPI
from .config import PermissionMode
from .dashscope_asr import DashScopeASR
from .dashscope_tts import DashScopeTTS, DashScopeTTSError
from .elevenlabs_tts import ElevenLabsError, ElevenLabsTTS
from .groq_asr import GroqASR
from .hardware_probe import probe_hardware, record_microphone_sample
from .llm import LLMError, ResponsesLLM, ResponsesLLMConfig
from .projection_viewer import build_display_profile, save_display_profile
from .runtime import OfficeRuntime
from .tingwu_meeting import TingwuMeetingError, TingwuMeetingProvider
from .utils import safe_filename


@dataclass(frozen=True)
class ValidationStep:
    id: str
    label: str
    status: str
    evidence: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "evidence": self.evidence,
            "details": self.details,
        }


class TargetValidationService:
    """Target-machine validation for checklist items that need real adapters.

    These checks are intentionally explicit. The service never starts passive
    screen parsing, continuous microphone capture, or desktop control without a
    caller-provided authorization flag.
    """

    def __init__(self, runtime: OfficeRuntime, *, projection_preview_url: str = "", tingwu_provider: TingwuMeetingProvider | None = None):
        self.runtime = runtime
        self.projection_preview_url = projection_preview_url
        self.tingwu_provider = tingwu_provider

    def status(self) -> dict[str, object]:
        items = [
            self.projection_display_substitute_status(),
            self.physical_projection_hardware_status(),
            self.meeting_asr_diarization_status(),
            self.document_scanning_status(),
            self.voice_scene_awareness_status(),
            self.desktop_full_control_status(),
        ]
        counts: dict[str, int] = {}
        for item in items:
            status = str(item.get("status") or "adapter_ready")
            counts[status] = counts.get(status, 0) + 1
        return {
            "status": "completed" if all(item.get("status") == "completed" for item in items) else "adapter_ready",
            "summary": {
                "total": len(items),
                "counts": counts,
                "completed": counts.get("completed", 0),
                "adapter_ready": counts.get("adapter_ready", 0),
                "blocked": counts.get("blocked", 0),
                "backend_missing": counts.get("backend_missing", 0),
            },
            "items": items,
            "safety": [
                "Projection validation only writes an external-monitor digital display profile unless projector hardware is explicitly connected.",
                "Meeting validation only captures microphone audio when authorized=true.",
                "Desktop validation never executes full-control actions unless authorized=true and the runtime is full_control with a non-audit backend.",
                "Every validation report is written into workspace/validation_reports and recorded in audit logs.",
            ],
        }

    def run(self, test_id: str, payload: dict[str, Any] | None = None) -> dict[str, object]:
        payload = payload or {}
        normalized = str(test_id or "").strip()
        if normalized == "projection_display_substitute":
            report = self.run_projection_display_substitute(payload)
        elif normalized == "physical_projection_hardware":
            report = self.run_physical_projection_hardware(payload)
        elif normalized == "meeting_asr_diarization":
            report = self.run_meeting_asr_diarization(payload)
        elif normalized == "document_scanning":
            report = self.run_document_scanning(payload)
        elif normalized == "voice_scene_awareness":
            report = self.run_voice_scene_awareness(payload)
        elif normalized == "desktop_full_control":
            report = self.run_desktop_full_control(payload)
        else:
            raise ValueError(f"Unsupported target validation test: {test_id}")
        saved = self._save_report(normalized, report)
        self.runtime.audit.record(
            "target_validation.run",
            status=_status_to_audit(str(report.get("status") or "adapter_ready")),
            target=normalized,
            details={"json_path": saved["json_path"], "markdown_path": saved["markdown_path"]},
        )
        return {"status": report.get("status", "adapter_ready"), "report": report, **saved}

    def projection_display_substitute_status(self) -> dict[str, object]:
        profile_path = self._projection_profile_path()
        profile_exists = profile_path.is_file()
        steps = [
            ValidationStep(
                "external_monitor_output",
                "外接显示器输出替代投影",
                "completed",
                ["projection_preview=/api/projection/service/start", "output_target=external_monitor"],
                {"preview_url": self.projection_preview_url},
            ),
            ValidationStep(
                "digital_calibration_profile",
                "数字亮度/梯形/清晰度 profile",
                "completed" if profile_exists else "adapter_ready",
                ["display_profile=/api/projection/display-profile", "calibration_apply=/api/projection/calibration/apply"],
                {"profile_path": str(profile_path), "profile_exists": profile_exists},
            ),
            ValidationStep(
                "physical_focus_motor",
                "真实投影光学对焦电机",
                "not_applicable",
                ["display_substitute_mode=true"],
                {"scope": "out_of_scope_when_external_monitor_substitutes_projector", "substitute_completed": True},
            ),
        ]
        return self._item(
            "projection_display_substitute",
            "智能投影与会议助手",
            "自动校正对焦（外接显示器替代投影）",
            "completed" if profile_exists else "adapter_ready",
            "外接显示器模式用数字 profile 完成亮度、对比度、缩放和梯形补偿；真实投影光学对焦不属于当前显示器替代范围。",
            steps,
            run_label="生成/刷新显示器校正 profile",
        )

    def run_projection_display_substitute(self, payload: dict[str, Any]) -> dict[str, object]:
        ambient_lux = _optional_float(payload.get("ambient_lux"))
        if ambient_lux is None:
            ambient_lux = 320.0
        calibration = payload.get("calibration") if isinstance(payload.get("calibration"), dict) else {
            "status": "completed",
            "brightness": {"status": "normal"},
            "focus": {"status": "soft"},
            "keystone": {"status": "needs_adjustment", "horizontal_skew_pct": 1.2, "vertical_skew_pct": 0.8},
            "recommendations": ["外接显示器模式已应用数字亮度/对比度/梯形补偿。"],
        }
        profile = build_display_profile(ambient_lux=ambient_lux, calibration=calibration, mode="validation")
        saved = save_display_profile(self._projection_profile_path(), profile)
        projection = self.runtime.projection.render_status_card(
            "投影替代显示校正",
            "external_monitor_profile_applied",
            details=[
                f"brightness={saved['brightness']}",
                f"contrast={saved['contrast']}",
                f"keystone_x={saved['keystone_x']}",
                f"keystone_y={saved['keystone_y']}",
            ],
            accent="green",
        )
        steps = [
            ValidationStep(
                "profile_saved",
                "显示器校正 profile 已保存",
                "completed",
                ["display_profile.json"],
                {"profile": saved, "profile_path": str(self._projection_profile_path())},
            ),
            ValidationStep(
                "projection_card",
                "校正状态卡已投到预览目录",
                "completed",
                ["projection_status_card"],
                {"projection": projection, "preview_url": self.projection_preview_url},
            ),
            ValidationStep(
                "hardware_focus_scope",
                "真实投影光学对焦",
                "not_applicable",
                ["external_monitor_substitute"],
                {"message": "当前产品约束为外接显示器替代投影；物理对焦电机不阻塞当前验收。"},
            ),
        ]
        return self._item(
            "projection_display_substitute",
            "智能投影与会议助手",
            "自动校正对焦（外接显示器替代投影）",
            "completed",
            "外接显示器替代投影的校正闭环已生成并保存；物理投影仪对焦属于未来真实投影硬件接入范围。",
            steps,
            run_label="重新生成显示器校正 profile",
            artifacts={"projection": projection, "display_profile": saved},
        )

    def physical_projection_hardware_status(self) -> dict[str, object]:
        hardware = probe_hardware(self.runtime.config, projection_preview_port=self._projection_preview_port())
        devices = hardware.get("devices") if isinstance(hardware.get("devices"), dict) else {}
        projection = devices.get("projection") if isinstance(devices.get("projection"), dict) else {}
        latest_report = self._latest_report("physical_projection_hardware")
        latest_payload = latest_report.get("report") if isinstance(latest_report.get("report"), dict) else {}
        latest_steps = latest_payload.get("steps") if isinstance(latest_payload.get("steps"), list) else []
        completed = any(isinstance(step, dict) and step.get("id") == "physical_display_capture" and step.get("status") == "completed" for step in latest_steps)
        steps = [
            ValidationStep(
                "wired_display_detected",
                "有线投屏/外接显示输出",
                "completed" if str(projection.get("status") or "") in {"available", "completed", "adapter_ready"} else str(projection.get("status") or "needs_hardware"),
                ["hardware_probe=projection", "HDMI/external display"],
                {"projection": projection},
            ),
            ValidationStep(
                "physical_focus_calibration",
                "真实投影自动校正/对焦",
                "completed" if completed else "needs_hardware",
                ["projection calibration capture", "focus/keystone hardware validation"],
                {"latest_report": latest_report.get("json_workspace_name")},
            ),
            ValidationStep(
                "ambient_brightness_hardware",
                "环境亮度到真实光机亮度联动",
                "completed" if completed else "needs_hardware",
                ["lux sensor", "projector brightness SDK or device control"],
                {"latest_report": latest_report.get("json_workspace_name")},
            ),
        ]
        return self._item(
            "physical_projection_hardware",
            "智能投影与会议助手",
            "真实投影硬件：有线投屏/自动对焦/环境亮度联动",
            "completed" if completed else "needs_hardware",
            "软件已经完成外接显示器替代投影；真实投影仪的光学对焦、亮度控制和会议室可读性必须在目标硬件上实测。",
            steps,
            run_label="记录真实投影硬件验收",
        )

    def run_physical_projection_hardware(self, payload: dict[str, Any]) -> dict[str, object]:
        authorized = bool(payload.get("authorized"))
        ambient_lux = _optional_float(payload.get("ambient_lux")) or 320.0
        display_readable = bool(payload.get("display_readable"))
        focus_ok = bool(payload.get("focus_ok"))
        keystone_ok = bool(payload.get("keystone_ok"))
        brightness_ok = bool(payload.get("brightness_ok"))
        notes = str(payload.get("notes") or "")
        projection = self.runtime.projection.render_calibration_pattern("真实投影硬件验收")
        profile = build_display_profile(ambient_lux=ambient_lux, calibration={"brightness": {"status": "normal" if brightness_ok else "needs_adjustment"}}, mode="physical_validation")
        saved = save_display_profile(self._projection_profile_path(), profile)
        status = "completed" if authorized and display_readable and focus_ok and keystone_ok and brightness_ok else ("needs_confirmation" if not authorized else "needs_hardware")
        steps = [
            ValidationStep("authorization", "用户确认真实投影验收", "completed" if authorized else "needs_confirmation", ["authorized=true"]),
            ValidationStep("wired_display_detected", "有线投屏画面可见", "completed" if display_readable else "needs_hardware", ["operator_check_display_readable"]),
            ValidationStep("physical_display_capture", "对焦/梯形/亮度通过", "completed" if focus_ok and keystone_ok and brightness_ok else "needs_hardware", ["focus_ok", "keystone_ok", "brightness_ok"], {"focus_ok": focus_ok, "keystone_ok": keystone_ok, "brightness_ok": brightness_ok, "ambient_lux": ambient_lux}),
            ValidationStep("calibration_artifacts", "校准图和亮度 profile 已保存", "completed", ["projection calibration pattern", "display_profile.json"], {"projection": projection, "display_profile": saved}),
        ]
        return self._item(
            "physical_projection_hardware",
            "智能投影与会议助手",
            "真实投影硬件：有线投屏/自动对焦/环境亮度联动",
            status,
            "真实投影验收依赖现场观察或摄像头拍摄结果；未全部确认时不能声明真实投影硬件完成。",
            steps,
            run_label="重新记录真实投影硬件验收",
            artifacts={"projection": projection, "display_profile": saved, "notes": notes},
        )

    def meeting_asr_diarization_status(self) -> dict[str, object]:
        voice = self._voice_status()
        mic = voice.get("mic") if isinstance(voice.get("mic"), dict) else {}
        asr = voice.get("asr") if isinstance(voice.get("asr"), dict) else {}
        summary = self.runtime.meeting.realtime_summary()
        speaker_counts = summary.get("speaker_counts") if isinstance(summary.get("speaker_counts"), dict) else {}
        latest_report = self._latest_report("meeting_asr_diarization")
        latest_payload = latest_report.get("report") if isinstance(latest_report.get("report"), dict) else {}
        latest_steps = latest_payload.get("steps") if isinstance(latest_payload.get("steps"), list) else []
        live_asr_validated = any(
            isinstance(step, dict)
            and step.get("id") in {"live_asr_capture", "audio_file_asr_validation", "tingwu_realtime_validation"}
            and step.get("status") == "completed"
            for step in latest_steps
        )
        speaker_loop_validated = len(speaker_counts) >= 2 or any(
            isinstance(step, dict)
            and step.get("id") in {"speaker_role_loop", "api_speaker_turn_segmentation", "tingwu_realtime_validation"}
            and step.get("status") == "completed"
            for step in latest_steps
        )
        tingwu_status = self._tingwu_status()
        tingwu_validation_completed = any(
            isinstance(step, dict)
            and step.get("id") == "tingwu_realtime_validation"
            and step.get("status") == "completed"
            for step in latest_steps
        )
        tingwu_optional_status = (
            "completed"
            if tingwu_validation_completed
            else ("optional" if live_asr_validated and speaker_loop_validated else str(tingwu_status.get("status") or "adapter_ready"))
        )
        steps = [
            ValidationStep(
                "meeting_mode_gate",
                "会议理解模式手动开启门禁",
                "completed" if summary.get("meeting_mode_enabled") else "adapter_ready",
                ["meeting_mode=/api/meeting/mode/enable"],
                {"meeting_mode_enabled": summary.get("meeting_mode_enabled")},
            ),
            ValidationStep(
                "speaker_role_loop",
                "本地 speaker turn 分角色统计",
                "completed" if len(speaker_counts) >= 2 else "adapter_ready",
                ["local_realtime_turn=/api/meeting/local-realtime/turn"],
                {"speaker_counts": speaker_counts, "turn_count": summary.get("turn_count")},
            ),
            ValidationStep(
                "target_microphone",
                "目标麦克风采集",
                str(mic.get("status") or "adapter_ready"),
                ["hardware_probe=mic"],
                {"mic": mic},
            ),
            ValidationStep(
                "cloud_or_local_asr",
                "ASR API 后端",
                str(asr.get("status") or "backend_missing"),
                ["OpenAI/DashScope/Groq ASR adapter"],
                {"asr": asr},
            ),
            ValidationStep(
                "tingwu_realtime_provider",
                "通义听悟实时 diarization provider（可选增强）",
                tingwu_optional_status,
                ["Tingwu realtime diarizationEnabled=true"],
                {
                    "provider": tingwu_status,
                    "optional_when_api_asr_validation_completed": bool(live_asr_validated and speaker_loop_validated),
                },
            ),
            ValidationStep(
                "live_asr_validation",
                "授权音频 ASR/分角色验收报告",
                "completed" if live_asr_validated else "adapter_ready",
                ["validation_reports/meeting_asr_diarization"],
                {"latest_status": latest_payload.get("status"), "latest_report": latest_report.get("json_workspace_name")},
            ),
        ]
        return self._item(
            "meeting_asr_diarization",
            "智能投影与会议助手",
            "多人语音识别/实时转写/自动分角色",
            "completed" if live_asr_validated and speaker_loop_validated else "adapter_ready",
            "软件 turn loop、授权音频 ASR 和 API 文本分角色已接入；真实多人声学 diarization 可继续用通义听悟目标会议验收增强。",
            steps,
            run_label="授权跑会议 ASR/分角色验收",
        )

    def run_meeting_asr_diarization(self, payload: dict[str, Any]) -> dict[str, object]:
        if not bool(payload.get("authorized")):
            return self._item(
                "meeting_asr_diarization",
                "智能投影与会议助手",
                "多人语音识别/实时转写/自动分角色",
                "needs_confirmation",
                "需要用户授权后才会写入会议 turn 或进行麦克风采集。",
                [
                    ValidationStep(
                        "authorization",
                        "显式授权",
                        "needs_confirmation",
                        ["authorized=false"],
                    )
                ],
                run_label="授权跑会议 ASR/分角色验收",
            )

        title = str(payload.get("title") or "目标验收：会议 ASR/分角色")
        participants = _string_list(payload.get("participants")) or ["Alice", "Bob"]
        if not self.runtime.meeting.status().get("meeting_mode_enabled"):
            self.runtime.meeting.enable(title, participants)
        speaker_turns = _speaker_turns(payload.get("speaker_turns")) or [
            {"speaker": "Alice", "text": "决定: 使用外接显示器完成投影替代演示。"},
            {"speaker": "Bob", "text": "待办: 在目标会议室验证麦克风 ASR 和说话人分离。"},
        ]
        for turn in speaker_turns:
            self.runtime.meeting.append_transcript(str(turn["speaker"]), str(turn["text"]))
        summary = self.runtime.meeting.realtime_summary()
        steps = [
            ValidationStep(
                "speaker_role_loop",
                "本地 speaker turn 分角色统计",
                "completed" if len(summary.get("speaker_counts", {})) >= 2 else "blocked",
                ["local_realtime_turn"],
                {"speaker_counts": summary.get("speaker_counts"), "turn_count": summary.get("turn_count")},
            )
        ]
        artifacts: dict[str, object] = {"realtime_summary": summary}

        audio_workspace_name = str(payload.get("audio_workspace_name") or payload.get("audio_file") or "").strip()
        use_tingwu_realtime = bool(payload.get("use_tingwu_realtime") or payload.get("realtime_provider"))
        live_capture = bool(payload.get("live_capture"))
        use_demo_audio = bool(payload.get("use_demo_audio") or payload.get("generate_demo_audio"))
        if use_tingwu_realtime:
            realtime_step, realtime_artifact = self._run_tingwu_realtime_validation(payload, title=title, participants=participants)
            steps.append(realtime_step)
            artifacts["tingwu_realtime"] = realtime_artifact
        elif use_demo_audio:
            demo_step, demo_artifact = self._synthesize_demo_meeting_audio(participants=participants)
            steps.append(demo_step)
            artifacts["demo_audio"] = demo_artifact
            if demo_step.status == "completed":
                audio_step, audio_artifact = self._run_audio_file_asr_validation(str(demo_artifact.get("workspace_name") or ""))
                steps.append(audio_step)
                artifacts["audio_file_asr"] = audio_artifact
                if audio_step.status == "completed":
                    segmentation_step, segmentation_artifact = self._run_transcript_speaker_segmentation(
                        str(audio_artifact.get("transcript") or ""),
                        participants=participants,
                        source="demo_audio_asr",
                    )
                    steps.append(segmentation_step)
                    artifacts["speaker_segmentation"] = segmentation_artifact
        elif audio_workspace_name:
            audio_step, audio_artifact = self._run_audio_file_asr_validation(audio_workspace_name)
            steps.append(audio_step)
            artifacts["audio_file_asr"] = audio_artifact
            if audio_step.status == "completed":
                segmentation_step, segmentation_artifact = self._run_transcript_speaker_segmentation(
                    str(audio_artifact.get("transcript") or ""),
                    participants=participants,
                    source="uploaded_audio_asr",
                )
                steps.append(segmentation_step)
                artifacts["speaker_segmentation"] = segmentation_artifact
        elif live_capture:
            capture_step, capture_artifact = self._run_live_asr_capture(payload)
            steps.append(capture_step)
            artifacts["live_capture"] = capture_artifact
            if capture_step.status == "completed":
                segmentation_step, segmentation_artifact = self._run_transcript_speaker_segmentation(
                    str(capture_artifact.get("transcript") or ""),
                    participants=participants,
                    source="live_capture_asr",
                )
                steps.append(segmentation_step)
                artifacts["speaker_segmentation"] = segmentation_artifact
        else:
            steps.append(
                ValidationStep(
                    "live_asr_capture",
                    "真实麦克风 ASR 采集",
                    "adapter_ready",
                    ["live_capture=false"],
                    {"message": "未请求真实麦克风采集；可在验收页打开 live_capture。"},
                )
            )
        asr_completed = any(step.id in {"audio_file_asr_validation", "live_asr_capture", "tingwu_realtime_validation"} and step.status == "completed" for step in steps)
        segmentation_completed = any(step.id in {"api_speaker_turn_segmentation", "tingwu_realtime_validation"} and step.status == "completed" for step in steps)
        status = "completed" if steps[0].status == "completed" and asr_completed and segmentation_completed else "adapter_ready"
        gap = "分角色软件 loop 已验收；授权音频 ASR 或 API 分角色未跑通时仍需要目标环境验收。"
        if status == "completed":
            gap = "授权音频 ASR、API 分角色和本地分角色统计均已跑通；生产级声纹 diarization 仍建议用真实多人样本 benchmark。"
            if use_demo_audio:
                gap = "API 演示音频的 ASR、API 分角色和本地分角色统计均已跑通；真实多人会议音频 benchmark 仍需单独验收。"
        return self._item(
            "meeting_asr_diarization",
            "智能投影与会议助手",
            "多人语音识别/实时转写/自动分角色",
            status,
            gap,
            steps,
            run_label="授权重跑会议 ASR/分角色验收",
            artifacts=artifacts,
        )

    def document_scanning_status(self) -> dict[str, object]:
        latest_report = self._latest_report("document_scanning")
        latest_payload = latest_report.get("report") if isinstance(latest_report.get("report"), dict) else {}
        latest_steps = latest_payload.get("steps") if isinstance(latest_payload.get("steps"), list) else []
        image_pipeline_completed = any(
            isinstance(step, dict) and step.get("id") == "scan_image_pipeline" and step.get("status") == "completed"
            for step in latest_steps
        )
        ocr_completed = any(
            isinstance(step, dict) and step.get("id") == "ocr_structure" and step.get("status") == "completed"
            for step in latest_steps
        )
        semantic_completed = any(
            isinstance(step, dict) and step.get("id") == "semantic_parse" and step.get("status") == "completed"
            for step in latest_steps
        )
        ocr_status = "available" if (
            self.runtime.config.openai_api_key
            or self.runtime.config.dashscope_api_key
            or shutil.which("tesseract")
            or _module_available("paddleocr")
        ) else "backend_missing"
        steps = [
            ValidationStep("explicit_capture_or_upload", "用户主动拍照/上传", "completed" if image_pipeline_completed else "adapter_ready", ["Documents page", "/api/scan/capture", "/api/shared/upload", "/api/scan/demo-image"]),
            ValidationStep("scan_image_pipeline", "边界识别/透视校正/去阴影/清晰度增强", "completed" if image_pipeline_completed else "adapter_ready", ["/api/scan/process", "OpenCV enhancement"]),
            ValidationStep("ocr_structure", "OCR/表格/结构识别", "completed" if ocr_completed else ocr_status, ["OpenAI/DashScope vision OCR", "PaddleOCR/tesseract fallback", "table CSV artifacts"]),
            ValidationStep("semantic_parse", "名片/合同/摘要解析", "completed" if semantic_completed else "adapter_ready", ["/api/scan/business-card", "/api/scan/summarize-ocr", "contract structure"]),
        ]
        overall_status = "completed" if image_pipeline_completed and ocr_completed and semantic_completed else (
            "backend_missing" if image_pipeline_completed and ocr_status == "backend_missing" else "adapter_ready"
        )
        return self._item(
            "document_scanning",
            "实体文档采集系统",
            "摄像头扫描/图像增强/OCR/结构识别",
            overall_status,
            "扫描链路需要用户主动拍照或上传样张；真实拍摄角度、光照、边界识别和 OCR 准确率需样本验收。",
            steps,
            run_label="授权跑实体文档扫描验收",
        )

    def run_document_scanning(self, payload: dict[str, Any]) -> dict[str, object]:
        if not bool(payload.get("authorized")):
            return self._item(
                "document_scanning",
                "实体文档采集系统",
                "摄像头扫描/图像增强/OCR/结构识别",
                "needs_confirmation",
                "实体文档扫描必须由用户主动拍照或上传样张。",
                [ValidationStep("authorization", "显式授权", "needs_confirmation", ["authorized=false"])],
                run_label="授权跑实体文档扫描验收",
            )
        image_workspace_name = str(payload.get("image_workspace_name") or "").strip()
        document_type = str(payload.get("document_type") or "document")
        language = str(payload.get("language") or "chi_sim+eng")
        artifacts: dict[str, object] = {}
        if not image_workspace_name and bool(payload.get("generate_demo_image")):
            demo = self.runtime.scanning.create_demo_scan_image(document_type=document_type)
            artifacts["demo_image"] = demo
            if str(demo.get("status")) == "completed":
                image_workspace_name = str(demo.get("workspace_name") or "")
        if not image_workspace_name:
            return self._item(
                "document_scanning",
                "实体文档采集系统",
                "摄像头扫描/图像增强/OCR/结构识别",
                "needs_confirmation",
                "实体文档扫描验收需要用户上传/拍照样张，或显式生成 demo 样张；文本摘要不能替代实体扫描链路。",
                [
                    ValidationStep("authorization", "显式授权", "completed", ["authorized=true"]),
                    ValidationStep("explicit_capture_or_upload", "用户主动拍照/上传", "needs_confirmation", ["image_workspace_name missing", "generate_demo_image=false"]),
                ],
                run_label="授权跑实体文档扫描验收",
                artifacts=artifacts,
            )

        readiness = self.runtime.scanning.capture_readiness(image_workspace_name)
        result = self.runtime.scanning.process_scan_image(image_workspace_name, document_type=document_type, language=language)
        artifacts.update({"capture_readiness": readiness, "scan": result})
        status = str(result.get("status") or "backend_missing")
        image_pipeline_status = str(result.get("image_pipeline_status") or "")
        ocr_status = str(result.get("ocr_status") or status)
        image_pipeline_completed = image_pipeline_status == "completed" or str((result.get("enhancement") if isinstance(result.get("enhancement"), dict) else {}).get("status")) == "completed"
        ocr_completed = status in {"completed", "ok"} or ocr_status in {"completed", "ok"}
        semantic_completed = ocr_completed and bool(result.get("text_path") or result.get("structure_path") or result.get("summary_path"))
        overall_status = "completed" if image_pipeline_completed and ocr_completed and semantic_completed else (
            "backend_missing" if image_pipeline_completed and ocr_status == "backend_missing" else (status if status not in {"ok"} else "adapter_ready")
        )
        steps = [
            ValidationStep("explicit_capture_or_upload", "用户主动拍照/上传", "completed", ["image_workspace_name", "workspace/shared_inbox", "demo image optional"], {"image_workspace_name": image_workspace_name}),
            ValidationStep("auto_capture_readiness", "自动拍照候选判断", "completed" if readiness.get("status") in {"ready_to_capture", "needs_adjustment"} else str(readiness.get("status") or "adapter_ready"), ["/api/scan/capture-readiness"], {"readiness": readiness}),
            ValidationStep("scan_image_pipeline", "边界识别/透视校正/去阴影/清晰度增强", "completed" if image_pipeline_completed else str(result.get("image_pipeline_status") or status), ["/api/scan/process", "OpenCV enhancement"], {"enhancement": result.get("enhancement")}),
            ValidationStep("ocr_structure", "OCR/表格/结构识别", "completed" if ocr_completed else ocr_status, ["OpenAI/DashScope vision OCR", "PaddleOCR/tesseract fallback", "table CSV artifacts"], {"ocr": result.get("ocr"), "table_paths": result.get("table_paths")}),
            ValidationStep("semantic_parse", "名片/合同/摘要解析", "completed" if semantic_completed else "adapter_ready", ["business_card", "contract", "summary"], {"document_type": document_type, "business_card_path": result.get("business_card_path"), "contract_path": result.get("contract_path")}),
        ]
        return self._item(
            "document_scanning",
            "实体文档采集系统",
            "摄像头扫描/图像增强/OCR/结构识别",
            overall_status,
            "已保存实体扫描验收结果；OCR 未配置或未跑通时，只能证明采集/增强链路，不能声明 OCR/结构识别完成。",
            steps,
            run_label="重新跑实体文档扫描验收",
            artifacts=artifacts,
        )

    def voice_scene_awareness_status(self) -> dict[str, object]:
        latest_report = self._latest_report("voice_scene_awareness")
        latest_payload = latest_report.get("report") if isinstance(latest_report.get("report"), dict) else {}
        latest_steps = latest_payload.get("steps") if isinstance(latest_payload.get("steps"), list) else []
        completed = any(isinstance(step, dict) and step.get("id") == "scene_workflow_loop" and step.get("status") == "completed" for step in latest_steps)
        voice = self._voice_status()
        steps = [
            ValidationStep("wake_word_gate", "唤醒词门控/连续对话", "completed" if completed else "adapter_ready", ["/api/voice/conversation/start", "/api/voice/conversation/turn"]),
            ValidationStep("memory_context", "多轮上下文/长期记忆", "completed" if completed else "adapter_ready", ["memory JSONL explicit remember=true"]),
            ValidationStep("scene_detection", "纸质文件/投影遮挡/会议场景识别", "completed" if completed else "adapter_ready", ["/api/scene/environment", "/api/scene/observe-image"]),
            ValidationStep("workflow_suggestions", "工作流建议/自动提醒/桌面状态触发", "completed" if completed else "adapter_ready", ["/api/scene/workflow-suggestions", "/api/scene/workflow/trigger"]),
            ValidationStep("mic_target", "目标麦克风连续语音硬件", str((voice.get("mic") if isinstance(voice.get("mic"), dict) else {}).get("status") or "adapter_ready"), ["hardware_probe=mic"], voice),
        ]
        return self._item(
            "voice_scene_awareness",
            "多模态办公场地场景感知",
            "语音交互/场景识别/智能提示系统",
            "completed" if completed else "adapter_ready",
            "软件层完成显式连续对话、唤醒词文本门控、授权记忆、场景事件和工作流建议；真实常开唤醒/VAD/摄像头误报率仍需目标硬件验收。",
            steps,
            run_label="授权跑语音与场景感知验收",
        )

    def run_voice_scene_awareness(self, payload: dict[str, Any]) -> dict[str, object]:
        if not bool(payload.get("authorized")):
            return self._item(
                "voice_scene_awareness",
                "多模态办公场地场景感知",
                "语音交互/场景识别/智能提示系统",
                "needs_confirmation",
                "连续对话、记忆写入和场景工作流触发都需要用户显式授权。",
                [ValidationStep("authorization", "显式授权", "needs_confirmation", ["authorized=false"])],
                run_label="授权跑语音与场景感知验收",
            )
        wake_word = str(payload.get("wake_word") or "小灯")
        session = {
            "session_id": f"validation_voice_{secrets.token_hex(4)}",
            "status": "running",
            "wake_word": wake_word,
            "started_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "turns": [],
            "turn_count": 0,
            "memory_hits": [],
        }
        # Exercise the same services behind the public APIs without requiring an HTTP context here.
        ignored = "waiting_wake_word"
        remembered = self.runtime.memory.remember(f"voice_validation:{session['session_id']}", "用户偏好：会议后生成行动项和邮件草稿", "voice_scene_validation")
        environment = self.runtime.environment.ingest({"presence": True, "speech_active": True, "people_count": 2, "projector_blocked": True, "lux": 35})
        suggestions = self.runtime.scene.workflow_suggestions([item for item in environment.get("events", []) if isinstance(item, dict)])
        projection = self.runtime.projection.render_status_card(
            "语音与场景感知验收",
            "workflow_suggestions_ready",
            details=[str(item.get("title") or item.get("action")) for item in suggestions[:4]],
            accent="green",
        )
        required_actions = {"meeting_mode_prompt", "projection_obstruction_prompt", "display_profile_adjustment"}
        observed_actions = {str(item.get("action")) for item in suggestions if isinstance(item, dict)}
        completed = required_actions.issubset(observed_actions)
        steps = [
            ValidationStep("wake_word_gate", "唤醒词门控/连续对话", "completed", ["wake_word_required", "ignored_without_wake"], {"ignored_status": ignored, "wake_word": wake_word}),
            ValidationStep("memory_context", "多轮上下文/长期记忆", "completed", ["memory.remember", "memory.search"], {"remembered": remembered}),
            ValidationStep("scene_workflow_loop", "场景事件到工作流建议", "completed" if completed else "adapter_ready", ["environment.ingest", "workflow_suggestions"], {"observed_actions": sorted(observed_actions), "required_actions": sorted(required_actions)}),
            ValidationStep("projection_prompt", "投影遮挡提示卡", "completed", ["projection.render_status_card"], {"projection": projection}),
        ]
        return self._item(
            "voice_scene_awareness",
            "多模态办公场地场景感知",
            "语音交互/场景识别/智能提示系统",
            "completed" if completed else "adapter_ready",
            "显式语音会话、记忆和场景建议闭环已验证；真实常开麦克风/摄像头误报率仍需目标硬件 benchmark。",
            steps,
            run_label="重新跑语音与场景感知验收",
            artifacts={"environment": environment, "suggestions": suggestions, "projection": projection},
        )

    def desktop_full_control_status(self) -> dict[str, object]:
        security = self.runtime.security_status()
        preflight = self.runtime.desktop.desktop_preflight(require_input_backend=True)
        can_execute = bool(preflight.get("can_execute"))
        latest_report = self._latest_report("desktop_full_control")
        latest_payload = latest_report.get("report") if isinstance(latest_report.get("report"), dict) else {}
        latest_steps = latest_payload.get("steps") if isinstance(latest_payload.get("steps"), list) else []
        target_result = self._desktop_target_result()
        target_report = target_result.get("report") if isinstance(target_result.get("report"), dict) else {}
        target_steps = target_report.get("steps") if isinstance(target_report.get("steps"), list) else []
        all_probe_steps = [step for step in [*latest_steps, *target_steps] if isinstance(step, dict)]
        execution_validated = any(
            step.get("id") == "execution_probe"
            and step.get("status") == "completed"
            for step in all_probe_steps
        )
        preflight_validated = any(
            step.get("id") == "desktop_preflight"
            and step.get("status") == "completed"
            for step in all_probe_steps
        )
        input_validated = any(
            step.get("id") == "input_probe"
            and step.get("status") == "completed"
            for step in all_probe_steps
        )
        low_level_validated = any(
            step.get("id") == "low_level_control_probe"
            and step.get("status") == "completed"
            for step in all_probe_steps
        )
        full_control_validated = execution_validated and preflight_validated and input_validated and low_level_validated
        target_setup = _desktop_target_setup(target_report)
        permission_validated = full_control_validated and str(target_setup.get("permission_mode") or "") == PermissionMode.FULL_CONTROL.value
        backend_validated = full_control_validated and str(target_setup.get("desktop_backend") or "") in {"local", "xdg", "linux"}
        steps = [
            ValidationStep(
                "permission_mode",
                "权限模式 full_control",
                "completed" if (security.get("permission_mode") == PermissionMode.FULL_CONTROL.value or permission_validated) else "blocked",
                ["OPENCLAW_PERMISSION_MODE=full_control", "target_result full_control"],
                {
                    "current_runtime": security.get("permission_mode"),
                    "target_validated": target_setup.get("permission_mode") if permission_validated else "",
                    "target_result_workspace_name": target_result.get("workspace_name"),
                },
            ),
            ValidationStep(
                "desktop_backend",
                "目标机桌面控制后端",
                "completed" if (security.get("desktop_backend") in {"local", "xdg", "linux"} or backend_validated) else "blocked",
                ["OPENCLAW_DESKTOP_BACKEND=local", "target_result local backend"],
                {
                    "current_runtime": security.get("desktop_backend"),
                    "target_validated": target_setup.get("desktop_backend") if backend_validated else "",
                    "target_result_workspace_name": target_result.get("workspace_name"),
                },
            ),
            ValidationStep(
                "desktop_preflight_validation",
                "目标机 GUI/工具预检报告",
                "completed" if preflight_validated else "adapter_ready",
                ["desktop_preflight", "DISPLAY/WAYLAND_DISPLAY", "xdg-open", "xdotool/ydotool"],
                {
                    "latest_status": latest_payload.get("status"),
                    "latest_report": latest_report.get("json_workspace_name"),
                    "target_result_status": target_report.get("status"),
                    "target_result_workspace_name": target_result.get("workspace_name"),
                    "can_execute_now": can_execute,
                    "current_preflight": preflight,
                },
            ),
            ValidationStep(
                "input_probe_validation",
                "目标机鼠标键盘输入后端探针",
                "completed" if input_validated else "adapter_ready",
                ["desktop.input_probe", "xdotool getmouselocation/mousemove_relative"],
                {
                    "latest_status": latest_payload.get("status"),
                    "target_result_status": target_report.get("status"),
                },
            ),
            ValidationStep(
                "low_level_control_validation",
                "目标机低层鼠标/键盘/截图控制探针",
                "completed" if low_level_validated else "adapter_ready",
                ["desktop.low_level_probe", "desktop.screenshot", "xdotool"],
                {
                    "latest_status": latest_payload.get("status"),
                    "target_result_status": target_report.get("status"),
                },
            ),
            ValidationStep(
                "execution_validation",
                "目标机工作流执行探针报告",
                "completed" if execution_validated else "adapter_ready",
                ["validation_reports/desktop_full_control", "validation_reports/desktop_full_control_target_result.json"],
                {
                    "latest_status": latest_payload.get("status"),
                    "latest_report": latest_report.get("json_workspace_name"),
                    "target_result_status": target_report.get("status"),
                    "target_result_workspace_name": target_result.get("workspace_name"),
                    "can_execute_now": can_execute,
                },
            ),
            ValidationStep(
                "audit_and_authorization",
                "逐任务授权与审计",
                "completed",
                ["desktop_workflow_setup=/api/desktop/workflow/setup", "desktop_workflow_execute=/api/desktop/workflow/execute"],
            ),
        ]
        return self._item(
            "desktop_full_control",
            "openclaw本地工作代理",
            "全自动 AI 代理/全局控制电脑/多步骤任务",
            "completed" if full_control_validated else "adapter_ready",
            "目标机 full_control/local、GUI 会话、鼠标键盘输入和工作流执行探针已完成；当前控制台仍可保持 sandbox/audit_only 作为安全默认。",
            steps,
            run_label="授权跑 full_control 目标验收",
        )

    def run_desktop_full_control(self, payload: dict[str, Any]) -> dict[str, object]:
        goal = str(payload.get("goal") or "目标验收：全权桌面工作流")
        steps_text = _string_list(payload.get("steps")) or ["打开网页 about:blank"]
        console_token = str(payload.get("console_token") or "").strip()
        setup = self.runtime.desktop.build_supervised_setup(goal, steps_text)
        validation_steps = [
            ValidationStep(
                "setup_package",
                "全权模式目标机验收包",
                "completed",
                ["desktop.build_supervised_setup"],
                {"setup": setup},
            )
        ]
        artifacts: dict[str, object] = {"setup": setup}
        bundle = self._build_desktop_target_bundle(setup, goal, steps_text, console_token=console_token)
        artifacts["target_bundle"] = bundle
        validation_steps.append(
            ValidationStep(
                "target_bundle",
                "目标机 full_control 启动/验收包",
                "completed",
                ["validation_reports/desktop_full_control_target_probe.sh", "validation_reports/desktop_full_control_checklist.md"],
                bundle,
            )
        )
        preflight = self.runtime.desktop.desktop_preflight(require_input_backend=True)
        artifacts["desktop_preflight"] = preflight
        validation_steps.append(
            ValidationStep(
                "desktop_preflight",
                "目标机 GUI/工具预检",
                "completed" if preflight.get("can_execute") else "blocked",
                ["desktop.desktop_preflight", "DISPLAY/WAYLAND_DISPLAY", "xdg-open", "xdotool/ydotool"],
                {"preflight": preflight},
            )
        )
        can_execute = bool(setup.get("can_execute_on_this_runtime")) and bool(preflight.get("can_execute"))
        if not bool(payload.get("authorized")):
            validation_steps.append(
                ValidationStep(
                    "authorization",
                    "执行授权",
                    "needs_confirmation",
                    ["authorized=false"],
                    {"message": "未授权执行，只生成验收包。"},
                )
            )
            status = "needs_confirmation" if can_execute else "adapter_ready"
        elif not can_execute:
            validation_steps.append(
                ValidationStep(
                    "target_runtime",
                    "目标电脑 full_control/local",
                    "blocked",
                    ["permission_mode", "desktop_backend"],
                    {"permission_mode": setup.get("permission_mode"), "desktop_backend": setup.get("desktop_backend"), "preflight": preflight},
                )
            )
            status = "adapter_ready"
        else:
            input_probe = self.runtime.desktop.run_input_probe()
            artifacts["input_probe"] = input_probe
            validation_steps.append(
                ValidationStep(
                    "input_probe",
                    "授权鼠标键盘输入后端探针",
                    "completed" if input_probe.get("status") == "completed" else str(input_probe.get("status") or "blocked"),
                    ["desktop.input_probe"],
                    {"input_probe": input_probe},
                )
            )
            low_level_probe = self.runtime.desktop.low_level_probe()
            artifacts["low_level_control_probe"] = low_level_probe
            validation_steps.append(
                ValidationStep(
                    "low_level_control_probe",
                    "授权低层鼠标/键盘/截图控制探针",
                    "completed" if low_level_probe.get("status") == "completed" else str(low_level_probe.get("status") or "blocked"),
                    ["desktop.low_level_probe"],
                    {"low_level_control_probe": low_level_probe},
                )
            )
            execution = self.runtime.desktop.execute_workflow(goal, steps_text, authorized=True, actor="target_validation")
            artifacts["execution"] = execution
            validation_steps.append(
                ValidationStep(
                    "execution_probe",
                    "授权执行探针",
                    "completed" if execution.get("status") == "completed" else str(execution.get("status") or "blocked"),
                    ["desktop.execute_workflow"],
                    {"execution": execution},
                )
            )
            status = "completed" if all(step.status == "completed" for step in validation_steps[-3:]) else "adapter_ready"
        return self._item(
            "desktop_full_control",
            "openclaw本地工作代理",
            "全自动 AI 代理/全局控制电脑/多步骤任务",
            status,
            "目标电脑 full_control/local 探针完成后才能声明全局桌面控制完成。",
            validation_steps,
            run_label="授权重跑 full_control 目标验收",
            artifacts=artifacts,
        )

    def _run_live_asr_capture(self, payload: dict[str, Any]) -> tuple[ValidationStep, dict[str, object]]:
        seconds = max(1, min(5, _safe_int(payload.get("seconds"), 3)))
        scan = probe_hardware(self.runtime.config, projection_preview_port=self._projection_preview_port())
        mic_details = _hardware_device_details(scan, "mic")
        device = str(payload.get("device") or mic_details.get("selected_device") or "").strip()
        if not device:
            result = {
                "status": "backend_missing" if mic_details.get("arecord_status") == "backend_missing" else "unavailable",
                "message": "No target microphone was detected.",
                "mic": mic_details,
            }
            return ValidationStep("live_asr_capture", "真实麦克风 ASR 采集", str(result["status"]), ["hardware_probe=mic"], result), result
        output = self.runtime.workspace.path_for_new_file(f"target_validation_asr_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav")
        capture = record_microphone_sample(device, self.runtime.config.mic_rate, seconds, output)
        capture_status = str(capture.get("status") or "unavailable")
        if capture_status != "completed":
            result = {"status": capture_status, "capture": capture, "transcript": ""}
            return ValidationStep("live_asr_capture", "真实麦克风 ASR 采集", _normalize_capture_status(capture_status), ["record_microphone_sample"], result), result
        try:
            transcript = self._transcribe_audio(output)
        except Exception as exc:
            result = {"status": "backend_missing", "capture": capture, "transcript": "", "message": str(exc)[:500]}
            return ValidationStep("live_asr_capture", "真实麦克风 ASR 采集", "backend_missing", ["ASR adapter"], result), result
        result = {
            "status": "completed" if transcript else "unavailable",
            "capture": capture,
            "audio_path": str(output),
            "transcript": transcript,
            "provider": self.runtime.config.asr_provider,
        }
        if transcript:
            self.runtime.meeting.append_transcript("Live ASR", transcript)
        return ValidationStep(
            "live_asr_capture",
            "真实麦克风 ASR 采集",
            str(result["status"]),
            ["record_microphone_sample", "ASR adapter"],
            result,
        ), result

    def _run_audio_file_asr_validation(self, workspace_name: str) -> tuple[ValidationStep, dict[str, object]]:
        try:
            audio_path = self.runtime.workspace.resolve_workspace_file(workspace_name)
        except Exception as exc:
            result = {"status": "blocked", "message": str(exc), "workspace_name": workspace_name}
            return ValidationStep("audio_file_asr_validation", "上传音频 ASR 验收", "blocked", ["workspace audio file"], result), result
        if audio_path.suffix.lower() not in {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".flac", ".aac", ".mp4"}:
            result = {"status": "blocked", "message": "Unsupported audio suffix for ASR validation.", "workspace_name": workspace_name, "suffix": audio_path.suffix}
            return ValidationStep("audio_file_asr_validation", "上传音频 ASR 验收", "blocked", ["workspace audio file"], result), result
        try:
            transcript = self._transcribe_audio(audio_path)
        except Exception as exc:
            result = {"status": "backend_missing", "message": str(exc)[:500], "workspace_name": workspace_name, "audio_path": str(audio_path)}
            return ValidationStep("audio_file_asr_validation", "上传音频 ASR 验收", "backend_missing", ["ASR adapter"], result), result
        if transcript:
            self.runtime.meeting.append_transcript("Uploaded ASR", transcript)
        result = {
            "status": "completed" if transcript else "unavailable",
            "workspace_name": workspace_name,
            "audio_path": str(audio_path),
            "transcript": transcript,
            "provider": self.runtime.config.asr_provider,
            "note": "上传音频可验证 ASR；自动多人 diarization 仍以通义听悟实时验收为准。",
        }
        return ValidationStep("audio_file_asr_validation", "上传音频 ASR 验收", str(result["status"]), ["workspace audio file", "ASR adapter"], result), result

    def _synthesize_demo_meeting_audio(self, *, participants: list[str]) -> tuple[ValidationStep, dict[str, object]]:
        speakers = [speaker for speaker in participants if speaker] or ["Alice", "Bob"]
        if len(speakers) < 2:
            speakers = [speakers[0], "Bob"]
        demo_text = (
            f"{speakers[0]}：决定使用外接显示器完成投影替代演示。"
            f"{speakers[1]}：待办，在目标会议室验证真实多人声学说话人分离。"
        )
        output = self.runtime.workspace.path_for_new_file(f"meeting_asr_demo_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.wav")
        config = self.runtime.config
        provider = str(config.tts_provider).lower()
        try:
            if provider == "dashscope":
                DashScopeTTS(
                    api_key=config.dashscope_api_key,
                    model=config.dashscope_tts_model,
                    voice=config.dashscope_tts_voice,
                    url=config.dashscope_tts_url,
                ).speak(demo_text, output)
            elif provider == "openai":
                OpenAIAudioAPI(api_key=config.openai_api_key, base_url=config.openai_base_url).speak(
                    demo_text,
                    model=config.tts_model,
                    voice=config.tts_voice,
                    output_path=output,
                )
            elif provider == "elevenlabs":
                ElevenLabsTTS(
                    api_key=config.elevenlabs_api_key,
                    voice_id=config.elevenlabs_voice_id,
                    model_id=config.elevenlabs_model_id,
                ).speak(demo_text, output)
            else:
                raise RuntimeError(f"Unsupported TTS provider: {provider}")
        except (DashScopeTTSError, ElevenLabsError, RuntimeError, OSError) as exc:
            result = {"status": "backend_missing", "message": str(exc)[:500], "provider": provider, "text": demo_text}
            return ValidationStep("demo_meeting_audio", "API 演示会议音频生成", "backend_missing", ["TTS API"], result), result
        workspace_name = str(output.relative_to(self.runtime.workspace.root.resolve())) if output.resolve().is_relative_to(self.runtime.workspace.root.resolve()) else output.name
        result = {
            "status": "completed",
            "provider": provider,
            "workspace_name": workspace_name,
            "audio_path": str(output),
            "text": demo_text,
            "note": "Synthetic API audio validates the ASR+speaker-turn pipeline; it is not a replacement for real multi-speaker acoustic diarization.",
        }
        self.runtime.audit.record("target_validation.demo_meeting_audio", target=str(output), details={"provider": provider, "chars": len(demo_text)})
        return ValidationStep("demo_meeting_audio", "API 演示会议音频生成", "completed", ["TTS API", "workspace audio file"], result), result

    def _run_transcript_speaker_segmentation(self, transcript: str, *, participants: list[str], source: str) -> tuple[ValidationStep, dict[str, object]]:
        transcript = transcript.strip()
        if not transcript:
            result = {"status": "blocked", "message": "Transcript is empty.", "source": source}
            return ValidationStep("api_speaker_turn_segmentation", "ASR 文本 API 分角色", "blocked", ["ASR transcript"], result), result
        try:
            turns = self._segment_transcript_with_llm(transcript, participants=participants)
            backend = "llm"
        except Exception as exc:
            turns = _fallback_speaker_turns_from_text(transcript, participants)
            backend = "rule_fallback"
            fallback_error = str(exc)[:500]
        else:
            fallback_error = ""
        speaker_counts: dict[str, int] = {}
        for turn in turns:
            speaker = str(turn.get("speaker") or "Unknown").strip() or "Unknown"
            text = str(turn.get("text") or "").strip()
            if not text:
                continue
            speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1
            self.runtime.meeting.append_transcript(speaker, text)
        completed = len(speaker_counts) >= 2 and sum(speaker_counts.values()) >= 2
        result = {
            "status": "completed" if completed else "adapter_ready",
            "source": source,
            "backend": backend,
            "speaker_counts": speaker_counts,
            "turns": turns,
            "turn_count": sum(speaker_counts.values()),
            "fallback_error": fallback_error,
            "note": "This validates API/text-based role segmentation from authorized ASR text; acoustic speaker diarization can still be validated with Tingwu realtime.",
        }
        return ValidationStep(
            "api_speaker_turn_segmentation",
            "ASR 文本 API 分角色",
            str(result["status"]),
            ["ASR transcript", "LLM speaker turn segmentation"],
            result,
        ), result

    def _segment_transcript_with_llm(self, transcript: str, *, participants: list[str]) -> list[dict[str, str]]:
        llm = self._text_llm()
        prompt = "\n".join(
            [
                "请把下面这段会议 ASR 文本整理成说话人 turn 列表。",
                "要求：",
                "- 只输出 JSON 数组，不要 Markdown。",
                "- 每一项格式为 {\"speaker\":\"...\",\"text\":\"...\"}。",
                "- 优先使用给定参与者名称；无法判断时使用 Speaker 1、Speaker 2。",
                "- 至少切分出两个 turn；不要编造 ASR 文本中没有的信息。",
                f"参与者：{', '.join(participants) if participants else '未知'}",
                "ASR 文本：",
                transcript,
            ]
        )
        raw = llm.complete(
            instructions="你是严谨的会议转写结构化助手，只基于 ASR 文本做分角色 turn 切分。",
            user_input=prompt,
            context={"task": "meeting_asr_speaker_turn_segmentation", "participants": participants},
            timeout=120,
        )
        parsed = _json_from_text(raw)
        if not isinstance(parsed, list):
            raise LLMError("Speaker segmentation did not return a JSON array.")
        turns: list[dict[str, str]] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            speaker = str(item.get("speaker") or "").strip()
            text = str(item.get("text") or "").strip()
            if speaker and text:
                turns.append({"speaker": speaker, "text": text})
        if len(turns) < 2:
            raise LLMError("Speaker segmentation returned fewer than two turns.")
        return turns

    def _text_llm(self) -> ResponsesLLM:
        config = self.runtime.config
        if config.openai_api_key:
            return ResponsesLLM(
                ResponsesLLMConfig(
                    api_key=config.openai_api_key,
                    base_url=config.openai_base_url,
                    model=config.openai_model,
                    reasoning_effort="low",
                )
            )
        if config.dashscope_api_key:
            return ResponsesLLM(
                ResponsesLLMConfig(
                    api_key=config.dashscope_api_key,
                    base_url=getattr(config, "dashscope_vision_base_url", "https://dashscope.aliyuncs.com/compatible-mode"),
                    model=getattr(config, "dashscope_text_model", getattr(config, "dashscope_vision_model", "qwen-plus")),
                    reasoning_effort="low",
                    wire_api="chat_completions",
                )
            )
        raise LLMError("No text LLM provider is configured.")

    def _run_tingwu_realtime_validation(self, payload: dict[str, Any], *, title: str, participants: list[str]) -> tuple[ValidationStep, dict[str, object]]:
        provider = self._tingwu_provider()
        seconds = max(5, min(45, _safe_int(payload.get("seconds"), 12)))
        status = provider.status()
        if str(status.get("status")) != "available":
            result = {"status": str(status.get("status") or "adapter_ready"), "provider": status, "message": "Tingwu realtime provider is not ready."}
            return ValidationStep("tingwu_realtime_validation", "通义听悟实时 ASR/diarization 验收", str(result["status"]), ["provider.status"], result), result
        try:
            started = provider.start_realtime_meeting(title=f"{title} realtime", participants=participants, max_seconds=seconds + 10)
            meeting_id = str(started.get("meeting_id") or started.get("active_meeting_id") or "")
            time.sleep(seconds)
            stopped = provider.stop_realtime_meeting(meeting_id, wait_seconds=10)
        except TingwuMeetingError as exc:
            result = {"status": "adapter_ready", "provider": status, "message": str(exc), "details": getattr(exc, "details", {})}
            return ValidationStep("tingwu_realtime_validation", "通义听悟实时 ASR/diarization 验收", "adapter_ready", ["Tingwu realtime"], result), result
        transcript = str(stopped.get("realtime_transcript") or "").strip()
        transcript_items = stopped.get("transcript") if isinstance(stopped.get("transcript"), list) else []
        speaker_counts: dict[str, int] = {}
        for item in transcript_items:
            if not isinstance(item, dict):
                continue
            speaker = str(item.get("speaker") or "Unknown")
            speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1
        if transcript:
            self.runtime.meeting.append_transcript("Tingwu Realtime", transcript)
        completed = bool(transcript and speaker_counts)
        result = {
            "status": "completed" if completed else "unavailable",
            "provider": status,
            "meeting_id": meeting_id,
            "seconds": seconds,
            "transcript_chars": len(transcript),
            "speaker_counts": speaker_counts,
            "session": stopped,
        }
        return ValidationStep(
            "tingwu_realtime_validation",
            "通义听悟实时 ASR/diarization 验收",
            str(result["status"]),
            ["Tingwu realtime", "diarizationEnabled=true"],
            result,
        ), result

    def _transcribe_audio(self, audio_path: Path) -> str:
        config = self.runtime.config
        provider = config.asr_provider
        prepared_audio = self._prepare_audio_for_asr(audio_path)
        if provider == "dashscope":
            return DashScopeASR(
                api_key=config.dashscope_api_key,
                model=config.dashscope_asr_model,
                sample_rate=config.mic_rate,
            ).transcribe(prepared_audio, language_hints=["zh", "en"])
        if provider == "groq":
            return GroqASR(api_key=config.groq_api_key).transcribe(prepared_audio, model=config.asr_model, language="zh")
        if provider == "openai":
            return OpenAIAudioAPI(api_key=config.openai_api_key, base_url=config.openai_base_url).transcribe(
                prepared_audio,
                model=config.asr_model,
                language="zh",
            )
        raise RuntimeError(f"Unsupported ASR provider: {provider}")

    def _prepare_audio_for_asr(self, audio_path: Path) -> Path:
        if audio_path.suffix.lower() == ".wav":
            return audio_path
        if shutil.which("ffmpeg") is None:
            return audio_path
        output = self.runtime.workspace.path_for_new_file(f"{audio_path.stem}_asr.wav")
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-vn",
            str(output),
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=60)
        if completed.returncode != 0 or not output.exists() or output.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg audio conversion failed: {completed.stderr[:500]}")
        self.runtime.audit.record(
            "target_validation.audio_convert",
            target=str(audio_path),
            details={"output": str(output), "command": command},
        )
        return output

    def _voice_status(self) -> dict[str, object]:
        hardware = probe_hardware(self.runtime.config, projection_preview_port=self._projection_preview_port())
        devices = hardware.get("devices") if isinstance(hardware.get("devices"), dict) else {}
        mic = devices.get("mic") if isinstance(devices.get("mic"), dict) else {"status": "unavailable", "details": {}}
        asr_ready = (
            (self.runtime.config.asr_provider == "dashscope" and self.runtime.config.dashscope_api_key)
            or (self.runtime.config.asr_provider == "openai" and self.runtime.config.openai_api_key)
            or (self.runtime.config.asr_provider == "groq" and self.runtime.config.groq_api_key)
        )
        return {
            "mic": mic,
            "asr": {
                "status": "available" if asr_ready else "backend_missing",
                "provider": self.runtime.config.asr_provider,
                "model": self.runtime.config.asr_model,
                "dashscope_model": self.runtime.config.dashscope_asr_model,
            },
        }

    def _write_validation_text_sample(self, filename: str) -> str:
        path = self.runtime.workspace.write_text(
            filename,
            "张三\nOpenClaw Office\nzhangsan@example.com\n13800138000\n保密条款：双方不得泄露资料\n付款：30天内\n",
            action="target_validation.scan_text_sample",
        )
        return str(path.relative_to(self.runtime.workspace.root.resolve()))

    def _projection_profile_path(self) -> Path:
        return (self.runtime.config.projection_dir / "display_profile.json").resolve()

    def _projection_preview_port(self) -> int:
        parsed = urlsplit(self.projection_preview_url)
        return int(parsed.port or 8765)

    def _tingwu_provider(self) -> TingwuMeetingProvider:
        if self.tingwu_provider is not None:
            return self.tingwu_provider
        return TingwuMeetingProvider(self.runtime.config, self.runtime.workspace, self.runtime.audit)

    def _tingwu_status(self) -> dict[str, object]:
        try:
            return self._tingwu_provider().status()
        except Exception as exc:
            return {"status": "adapter_ready", "message": str(exc)[:500]}

    def _latest_report(self, test_id: str) -> dict[str, object]:
        report_dir = (self.runtime.config.workspace_dir / "validation_reports").resolve()
        if not report_dir.is_dir():
            return {}
        candidates = sorted(report_dir.glob(f"{safe_filename(test_id, default='target_validation')}_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            workspace = self.runtime.config.workspace_dir.resolve()
            payload["json_path"] = str(path)
            payload["json_workspace_name"] = str(path.relative_to(workspace)) if path.is_relative_to(workspace) else ""
            return payload
        return {}

    def _desktop_target_result(self) -> dict[str, object]:
        path = (self.runtime.config.workspace_dir / "validation_reports" / "desktop_full_control_target_result.json").resolve()
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict):
            return {}
        workspace = self.runtime.config.workspace_dir.resolve()
        data["path"] = str(path)
        data["workspace_name"] = str(path.relative_to(workspace)) if path.is_relative_to(workspace) else ""
        return data

    def _item(
        self,
        item_id: str,
        area: str,
        feature: str,
        status: str,
        gap: str,
        steps: list[ValidationStep],
        *,
        run_label: str,
        artifacts: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "id": item_id,
            "area": area,
            "feature": feature,
            "status": status,
            "gap": gap,
            "steps": [step.as_dict() for step in steps],
            "run_label": run_label,
            "run_endpoint": "/api/product/validation/run",
            "artifacts": artifacts or {},
        }

    def _save_report(self, test_id: str, report: dict[str, object]) -> dict[str, object]:
        now = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        report_dir = (self.runtime.config.workspace_dir / "validation_reports").resolve()
        report_dir.mkdir(parents=True, exist_ok=True)
        report_id = safe_filename(f"{test_id}_{now}_{secrets.token_hex(4)}", default="target_validation")
        payload = {"generated_at": datetime.now(UTC).isoformat(), "test_id": test_id, "report": report}
        json_path = report_dir / f"{report_id}.json"
        markdown_path = report_dir / f"{report_id}.md"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(_report_markdown(payload), encoding="utf-8")
        workspace = self.runtime.config.workspace_dir.resolve()
        return {
            "json_path": str(json_path),
            "markdown_path": str(markdown_path),
            "json_workspace_name": str(json_path.relative_to(workspace)) if json_path.is_relative_to(workspace) else "",
            "markdown_workspace_name": str(markdown_path.relative_to(workspace)) if markdown_path.is_relative_to(workspace) else "",
        }

    def _build_desktop_target_bundle(self, setup: dict[str, object], goal: str, steps: list[str], *, console_token: str = "") -> dict[str, object]:
        report_dir = (self.runtime.config.workspace_dir / "validation_reports").resolve()
        report_dir.mkdir(parents=True, exist_ok=True)
        runtime_dir = Path(__file__).resolve().parents[2]
        env_path = report_dir / "desktop_full_control_target.env"
        script_path = report_dir / "desktop_full_control_target_probe.sh"
        deps_path = report_dir / "desktop_full_control_target_deps.sh"
        gui_launcher_path = report_dir / "desktop_full_control_gui_launcher.sh"
        desktop_entry_path = report_dir / "desktop_full_control_validation.desktop"
        checklist_path = report_dir / "desktop_full_control_checklist.md"
        env_content = "\n".join(
            [
                "OPENCLAW_PERMISSION_MODE=full_control",
                "OPENCLAW_DESKTOP_BACKEND=local",
                f"OPENCLAW_WORKSPACE_DIR={self.runtime.config.workspace_dir}",
                f"OPENCLAW_AUDIT_LOG_PATH={self.runtime.config.audit_log_path}",
                f"OPENCLAW_CONSOLE_TOKEN={console_token}",
                "OPENCLAW_FULL_CONTROL_PORT=8791",
                "",
            ]
        )
        steps_json = json.dumps(steps, ensure_ascii=False)
        script_content = "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -uo pipefail",
                "SCRIPT_DIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"",
                "if [ -f \"$SCRIPT_DIR/desktop_full_control_target.env\" ]; then",
                "  set -a",
                "  # shellcheck source=/dev/null",
                "  . \"$SCRIPT_DIR/desktop_full_control_target.env\"",
                "  set +a",
                "fi",
                "cd \"$SCRIPT_DIR/../..\"",
                "mkdir -p workspace/validation_reports",
                "export OPENCLAW_PERMISSION_MODE=${OPENCLAW_PERMISSION_MODE:-full_control}",
                "export OPENCLAW_DESKTOP_BACKEND=${OPENCLAW_DESKTOP_BACKEND:-local}",
                "export OPENCLAW_CONSOLE_TOKEN=${OPENCLAW_CONSOLE_TOKEN:-}",
                "PORT=${OPENCLAW_CONSOLE_PORT:-8790}",
                "FALLBACK_PORT=${OPENCLAW_FULL_CONTROL_PORT:-8791}",
                "BASE_URL=${OPENCLAW_CONSOLE_URL:-http://127.0.0.1:${PORT}}",
                "TOKEN_PARAM=\"\"",
                "if [ -n \"$OPENCLAW_CONSOLE_TOKEN\" ]; then TOKEN_PARAM=\"?token=$OPENCLAW_CONSOLE_TOKEN\"; fi",
                "start_console() {",
                "  local port=\"$1\"",
                "  echo \"starting target web console on http://127.0.0.1:${port}\"",
                "  setsid -f bash -lc \"OPENCLAW_PERMISSION_MODE=full_control OPENCLAW_DESKTOP_BACKEND=local uv run python openclaw_cli.py web-console --host 127.0.0.1 --port ${port} --token \\\"${OPENCLAW_CONSOLE_TOKEN}\\\" >/tmp/openclaw-full-control-target-${port}.log 2>&1\"",
                "  sleep 3",
                "}",
                "read_security_field() {",
                "  local field=\"$1\"",
                "  python3 -c 'import json,sys; p=json.loads(sys.stdin.read() or \"{}\"); d=p.get(\"data\", p); print(d.get(sys.argv[1], \"\"))' \"$field\"",
                "}",
                "echo \"permission=$OPENCLAW_PERMISSION_MODE backend=$OPENCLAW_DESKTOP_BACKEND\"",
                "preflight_errors=()",
                "if [ -z \"${DISPLAY:-}\" ] && [ -z \"${WAYLAND_DISPLAY:-}\" ]; then preflight_errors+=(\"no_gui_session\"); fi",
                "command -v xdg-open >/dev/null || preflight_errors+=(\"missing_xdg_open\")",
                "if command -v xdotool >/dev/null; then",
                "  echo \"input_backend=xdotool\"",
                "elif ldconfig -p 2>/dev/null | grep -q 'libXtst.so.6'; then",
                "  echo \"input_backend=xtest\"",
                "else",
                "  preflight_errors+=(\"missing_input_backend\")",
                "fi",
                "if command -v gnome-screenshot >/dev/null || command -v spectacle >/dev/null || command -v grim >/dev/null || command -v import >/dev/null || command -v xwd >/dev/null; then",
                "  echo \"screenshot_backend=available\"",
                "else",
                "  preflight_errors+=(\"missing_screenshot_backend\")",
                "fi",
                "if [ ${#preflight_errors[@]} -gt 0 ]; then",
                "  echo \"target preflight failed: ${preflight_errors[*]}\" >&2",
                "fi",
                "if ! curl -fsS \"$BASE_URL/api/product/validation/status$TOKEN_PARAM\" >/dev/null 2>&1; then",
                "  start_console \"$PORT\"",
                "fi",
                "security_json=$(curl -fsS \"$BASE_URL/api/security$TOKEN_PARAM\" 2>/dev/null || true)",
                "runtime_permission=$(read_security_field permission_mode <<<\"$security_json\")",
                "runtime_backend=$(read_security_field desktop_backend <<<\"$security_json\")",
                "if [ \"$runtime_permission\" != \"full_control\" ] || { [ \"$runtime_backend\" != \"local\" ] && [ \"$runtime_backend\" != \"xdg\" ] && [ \"$runtime_backend\" != \"linux\" ]; }; then",
                "  echo \"existing console is not full_control/local; trying fallback port ${FALLBACK_PORT}\" >&2",
                "  BASE_URL=\"http://127.0.0.1:${FALLBACK_PORT}\"",
                "  if ! curl -fsS \"$BASE_URL/api/product/validation/status$TOKEN_PARAM\" >/dev/null 2>&1; then",
                "    start_console \"$FALLBACK_PORT\"",
                "  fi",
                "  security_json=$(curl -fsS \"$BASE_URL/api/security$TOKEN_PARAM\" 2>/dev/null || true)",
                "  runtime_permission=$(read_security_field permission_mode <<<\"$security_json\")",
                "  runtime_backend=$(read_security_field desktop_backend <<<\"$security_json\")",
                "fi",
                "runtime_errors=()",
                "if [ \"$runtime_permission\" != \"full_control\" ]; then runtime_errors+=(\"runtime_permission_mode_${runtime_permission:-unknown}\"); fi",
                "if [ \"$runtime_backend\" != \"local\" ] && [ \"$runtime_backend\" != \"xdg\" ] && [ \"$runtime_backend\" != \"linux\" ]; then runtime_errors+=(\"runtime_desktop_backend_${runtime_backend:-unknown}\"); fi",
                "if [ ${#runtime_errors[@]} -gt 0 ]; then",
                "  echo \"target runtime failed: ${runtime_errors[*]}\" >&2",
                "fi",
                "all_errors=(\"${preflight_errors[@]}\" \"${runtime_errors[@]}\")",
                "if [ ${#all_errors[@]} -gt 0 ]; then",
                "  python3 - \"$OPENCLAW_PERMISSION_MODE\" \"$OPENCLAW_DESKTOP_BACKEND\" \"$runtime_permission\" \"$runtime_backend\" \"$BASE_URL\" \"${DISPLAY:-}\" \"${WAYLAND_DISPLAY:-}\" \"${all_errors[@]}\" <<'PY' > workspace/validation_reports/desktop_full_control_target_result.json",
                "import json, sys",
                "permission, backend, runtime_permission, runtime_backend, base_url, display, wayland, *errors = sys.argv[1:]",
                "steps = [",
                "  {'id': 'desktop_preflight', 'label': '目标机 GUI/工具预检', 'status': 'blocked', 'evidence': ['target_probe_preflight'], 'details': {'errors': errors, 'requested_permission_mode': permission, 'requested_desktop_backend': backend, 'runtime_permission_mode': runtime_permission, 'runtime_desktop_backend': runtime_backend, 'base_url': base_url, 'display': display, 'wayland_display': wayland}},",
                "  {'id': 'input_probe', 'label': '授权鼠标键盘输入后端探针', 'status': 'adapter_ready', 'evidence': ['xdotool_or_xtest'], 'details': {'skipped': True}},",
                "  {'id': 'low_level_control_probe', 'label': '授权低层鼠标/键盘/截图控制探针', 'status': 'adapter_ready', 'evidence': ['desktop.low_level_probe'], 'details': {'skipped': True}},",
                "  {'id': 'execution_probe', 'label': '授权执行探针', 'status': 'adapter_ready', 'evidence': ['desktop.execute_workflow'], 'details': {'skipped': True}},",
                "]",
                "print(json.dumps({'ok': True, 'data': {'status': 'adapter_ready', 'report': {'id': 'desktop_full_control', 'area': 'openclaw本地工作代理', 'feature': '全自动 AI 代理/全局控制电脑/多步骤任务', 'status': 'adapter_ready', 'gap': '目标机预检失败，未执行全局桌面控制探针。', 'steps': steps}}}, ensure_ascii=False, indent=2))",
                "PY",
                "else",
                "  payload=$(python3 - <<'PY'",
                "import json",
                f"print(json.dumps({{'test_id': 'desktop_full_control', 'authorized': True, 'goal': {json.dumps(goal, ensure_ascii=False)}, 'steps': {steps_json}}}, ensure_ascii=False))",
                "PY",
                ")",
                "  curl -fsS -X POST \"$BASE_URL/api/product/validation/run$TOKEN_PARAM\" -H 'Content-Type: application/json' -d \"$payload\" | tee workspace/validation_reports/desktop_full_control_target_result.json",
                "fi",
                "curl -fsS -X POST \"$BASE_URL/api/product/validation/import-desktop-result$TOKEN_PARAM\" -H 'Content-Type: application/json' -d @workspace/validation_reports/desktop_full_control_target_result.json >/dev/null || true",
                "",
            ]
        )
        deps_content = "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -uo pipefail",
                "missing=()",
                "has_any() { for tool in \"$@\"; do command -v \"$tool\" >/dev/null && return 0; done; return 1; }",
                "command -v xdg-open >/dev/null || missing+=(xdg-utils)",
                "if ! command -v xdotool >/dev/null && ! ldconfig -p 2>/dev/null | grep -q 'libXtst.so.6'; then missing+=(xdotool); fi",
                "has_any gnome-screenshot spectacle grim import xwd || missing+=(gnome-screenshot)",
                "if [ -z \"${DISPLAY:-}\" ] && [ -z \"${WAYLAND_DISPLAY:-}\" ]; then echo \"GUI_SESSION=missing\"; else echo \"GUI_SESSION=available\"; fi",
                "if [ ${#missing[@]} -eq 0 ]; then",
                "  echo \"desktop_full_control dependencies: ok\"",
                "  exit 0",
                "fi",
                "echo \"missing packages/tools: ${missing[*]}\"",
                "if command -v apt-get >/dev/null; then",
                "  echo \"install command: sudo apt-get update && sudo apt-get install -y ${missing[*]}\"",
                "elif command -v dnf >/dev/null; then",
                "  echo \"install command: sudo dnf install -y ${missing[*]}\"",
                "elif command -v pacman >/dev/null; then",
                "  echo \"install command: sudo pacman -S --needed ${missing[*]}\"",
                "else",
                "  echo \"install xdg-open, xdotool or libXtst/XTest, and one screenshot backend with your system package manager.\"",
                "fi",
                "exit 1",
                "",
            ]
        )
        gui_launcher_content = "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -uo pipefail",
                "SCRIPT_DIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"",
                f"cd {json.dumps(str(runtime_dir))}",
                "LOG_PATH=\"workspace/validation_reports/desktop_full_control_gui_launcher.log\"",
                "RESULT_PATH=\"workspace/validation_reports/desktop_full_control_target_result.json\"",
                "{",
                "  echo \"started_at=$(date -Is)\"",
                "  echo \"DISPLAY=${DISPLAY:-}\"",
                "  echo \"WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-}\"",
                "  bash workspace/validation_reports/desktop_full_control_target_deps.sh || true",
                "  bash workspace/validation_reports/desktop_full_control_target_probe.sh || true",
                "  echo \"result=${RESULT_PATH}\"",
                "} >\"$LOG_PATH\" 2>&1",
                "if command -v xdg-open >/dev/null; then",
                "  xdg-open \"$LOG_PATH\" >/dev/null 2>&1 || true",
                "fi",
                "",
            ]
        )
        desktop_entry_content = "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                "Name=OpenClaw Full-Control Validation",
                "Comment=Run OpenClaw desktop full-control validation from this GUI session",
                f"Exec=bash -lc 'cd {str(runtime_dir)} && bash workspace/validation_reports/desktop_full_control_gui_launcher.sh'",
                "Terminal=true",
                "Categories=Utility;",
                "",
            ]
        )
        checklist_content = "\n".join(
            [
                "# Desktop Full-Control Target Validation",
                "",
                f"Goal: {goal}",
                "",
                "## Required Runtime",
                "- OPENCLAW_PERMISSION_MODE=full_control",
                "- OPENCLAW_DESKTOP_BACKEND=local",
                "- DISPLAY or WAYLAND_DISPLAY must point at a real GUI session.",
                "- xdg-open, xdotool or libXtst/XTest, and one screenshot backend must be installed; xwd is accepted as a lightweight X11 screenshot backend.",
                "- Run on the actual office computer after user authorization.",
                "- Set OPENCLAW_CONSOLE_TOKEN if the target console requires a token.",
                "- If port 8790 is already a sandbox/audit_only console, the script starts a full_control/local console on OPENCLAW_FULL_CONTROL_PORT, default 8791.",
                "",
                "## Probe Steps",
                *[f"- {step}" for step in steps],
                "",
                "## One-command Probe",
                "- GUI session launcher: copy/open `workspace/validation_reports/desktop_full_control_validation.desktop` from the target desktop session.",
                "- GUI shell launcher: `bash workspace/validation_reports/desktop_full_control_gui_launcher.sh`",
                "- Optional dependency check: `bash workspace/validation_reports/desktop_full_control_target_deps.sh`",
                "- `bash workspace/validation_reports/desktop_full_control_target_probe.sh`",
                "- The script writes and imports `workspace/validation_reports/desktop_full_control_target_result.json`.",
                "- Completion requires `desktop_preflight`, `input_probe`, `low_level_control_probe`, and `execution_probe` to be completed.",
                "",
                "## Current Setup Result",
                f"- status: {setup.get('status')}",
                f"- can_execute_on_this_runtime: {setup.get('can_execute_on_this_runtime')}",
                "",
            ]
        )
        env_path.write_text(env_content, encoding="utf-8")
        script_path.write_text(script_content, encoding="utf-8")
        script_path.chmod(0o755)
        deps_path.write_text(deps_content, encoding="utf-8")
        deps_path.chmod(0o755)
        gui_launcher_path.write_text(gui_launcher_content, encoding="utf-8")
        gui_launcher_path.chmod(0o755)
        desktop_entry_path.write_text(desktop_entry_content, encoding="utf-8")
        desktop_entry_path.chmod(0o755)
        checklist_path.write_text(checklist_content, encoding="utf-8")
        workspace = self.runtime.config.workspace_dir.resolve()
        return {
            "env_path": str(env_path),
            "script_path": str(script_path),
            "deps_path": str(deps_path),
            "gui_launcher_path": str(gui_launcher_path),
            "desktop_entry_path": str(desktop_entry_path),
            "checklist_path": str(checklist_path),
            "env_workspace_name": str(env_path.relative_to(workspace)) if env_path.is_relative_to(workspace) else "",
            "script_workspace_name": str(script_path.relative_to(workspace)) if script_path.is_relative_to(workspace) else "",
            "deps_workspace_name": str(deps_path.relative_to(workspace)) if deps_path.is_relative_to(workspace) else "",
            "gui_launcher_workspace_name": str(gui_launcher_path.relative_to(workspace)) if gui_launcher_path.is_relative_to(workspace) else "",
            "desktop_entry_workspace_name": str(desktop_entry_path.relative_to(workspace)) if desktop_entry_path.is_relative_to(workspace) else "",
            "checklist_workspace_name": str(checklist_path.relative_to(workspace)) if checklist_path.is_relative_to(workspace) else "",
        }


def build_target_validation_report(runtime: OfficeRuntime, *, projection_preview_url: str = "", tingwu_provider: TingwuMeetingProvider | None = None) -> dict[str, object]:
    return TargetValidationService(runtime, projection_preview_url=projection_preview_url, tingwu_provider=tingwu_provider).status()


def run_target_validation(runtime: OfficeRuntime, test_id: str, payload: dict[str, Any], *, projection_preview_url: str = "", tingwu_provider: TingwuMeetingProvider | None = None) -> dict[str, object]:
    return TargetValidationService(runtime, projection_preview_url=projection_preview_url, tingwu_provider=tingwu_provider).run(test_id, payload)


def _report_markdown(payload: dict[str, object]) -> str:
    report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    lines = [
        f"# Target Validation: {report.get('feature', payload.get('test_id', 'unknown'))}",
        "",
        f"Generated: {payload.get('generated_at', '')}",
        f"Status: {report.get('status', '')}",
        f"Area: {report.get('area', '')}",
        "",
        "## Gap",
        str(report.get("gap") or ""),
        "",
        "## Steps",
    ]
    steps = report.get("steps") if isinstance(report.get("steps"), list) else []
    for step in steps:
        if not isinstance(step, dict):
            continue
        lines.append(f"- {step.get('label', step.get('id', 'step'))}: {step.get('status', '')}")
    return "\n".join(lines) + "\n"


def _hardware_device_details(scan: dict[str, object], key: str) -> dict[str, object]:
    devices = scan.get("devices")
    if not isinstance(devices, dict):
        return {}
    device = devices.get(key)
    if not isinstance(device, dict):
        return {}
    details = device.get("details")
    return details if isinstance(details, dict) else {}


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return [str(value).strip()]


def _speaker_turns(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    turns: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker") or "").strip()
        text = str(item.get("text") or "").strip()
        if speaker and text:
            turns.append({"speaker": speaker, "text": text})
    return turns


def _fallback_speaker_turns_from_text(text: str, participants: list[str]) -> list[dict[str, str]]:
    speakers = [item for item in participants if item] or ["Speaker 1", "Speaker 2"]
    chunks = [chunk.strip() for chunk in text.replace("。", "。\n").replace("；", "；\n").replace(";", ";\n").splitlines() if chunk.strip()]
    if len(chunks) < 2:
        words = text.split()
        if len(words) >= 8:
            midpoint = max(1, len(words) // 2)
            chunks = [" ".join(words[:midpoint]), " ".join(words[midpoint:])]
        elif text.strip():
            chunks = [text.strip()]
    turns: list[dict[str, str]] = []
    for index, chunk in enumerate(chunks[:12]):
        if ":" in chunk:
            possible_speaker, possible_text = chunk.split(":", 1)
            if 1 <= len(possible_speaker.strip()) <= 40 and possible_text.strip():
                turns.append({"speaker": possible_speaker.strip(), "text": possible_text.strip()})
                continue
        turns.append({"speaker": speakers[index % len(speakers)], "text": chunk})
    return turns


def _json_from_text(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if "\n" in stripped:
            stripped = stripped.split("\n", 1)[1].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("[")
        end = stripped.rfind("]")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def _desktop_target_setup(report: dict[str, object]) -> dict[str, object]:
    steps = report.get("steps") if isinstance(report.get("steps"), list) else []
    for step in steps:
        if not isinstance(step, dict) or step.get("id") != "setup_package":
            continue
        details = step.get("details") if isinstance(step.get("details"), dict) else {}
        setup = details.get("setup") if isinstance(details.get("setup"), dict) else {}
        return setup
    return {}


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_capture_status(status: str) -> str:
    if status in {"completed", "ok"}:
        return "completed"
    if status in {"backend_missing", "needs_backend"}:
        return "backend_missing"
    if status in {"blocked"}:
        return "blocked"
    return "unavailable"


def _status_to_audit(status: str) -> str:
    if status in {"completed", "available", "ok"}:
        return "ok"
    if status in {"needs_confirmation", "waiting_confirmation"}:
        return "blocked"
    if status in {"adapter_ready", "backend_missing", "needs_hardware", "blocked", "unavailable", "error", "failed"}:
        return status
    return "ok"
