from __future__ import annotations

import hashlib
import html
import ipaddress
import importlib.util
import json
import math
import os
import re
import secrets
import shlex
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from lelamp.motor_control import LELAMP_MOTOR_ORDER

from ..audio_api import AudioAPIError, OpenAIAudioAPI
from ..config import tingwu_credential_next_actions
from ..dashscope_tts import DashScopeTTS, DashScopeTTSError
from ..documents import DOCUMENT_WORKFLOW_SUFFIXES
from ..elevenlabs_tts import ElevenLabsError, ElevenLabsTTS
from ..hardware_probe import play_audio_file, probe_hardware
from ..tingwu_meeting import PLACEHOLDER_CAPTURE_DEVICES, sanitize_event_payload

LELAMP_CONTROL_MOTORS = LELAMP_MOTOR_ORDER
OFFICIAL_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
OFFICIAL_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

__all__ = ['normalize_result_status', 'collect_outputs', 'summarize_manual_result', 'manual_result_details', 'summarize_dict', 'first_output_path', 'extract_email_subject', 'compact_meeting_step_output', 'meeting_step_understanding', 'meeting_step_result', 'first_nonempty_line', 'audit_event_dto']

def format_weather_answer(*args, **kwargs):
    from .. import web_helpers
    return web_helpers.format_weather_answer(*args, **kwargs)

def list_string(*args, **kwargs):
    from .. import web_helpers
    return web_helpers.list_string(*args, **kwargs)

def normalize_result_status(result: Any) -> str:
    if isinstance(result, dict):
        status = str(result.get("status") or "")
        if status:
            return status
        if result.get("permission") and isinstance(result.get("permission"), dict) and not result["permission"].get("allowed", True):
            return "blocked"
    return "completed"


def collect_outputs(result: Any) -> list[dict[str, str]]:
    outputs: list[dict[str, str]] = []
    if not isinstance(result, dict):
        return outputs
    seen: set[str] = set()

    def add_output(path_value: str, *, step: str = "") -> None:
        if not path_value or path_value in seen:
            return
        seen.add(path_value)
        item = {"path": path_value, "type": Path(path_value).suffix.lstrip(".") or "file"}
        if step:
            item["step"] = step
        outputs.append(item)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("path") and isinstance(item, str):
                    add_output(item)
                elif key.endswith("paths") and isinstance(item, dict):
                    for nested_key, nested_item in item.items():
                        if isinstance(nested_item, str):
                            add_output(nested_item, step=str(nested_key))
                        else:
                            walk(nested_item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(result)
    return outputs[:25]


def summarize_manual_result(result: dict[str, object], *, blocked: bool = False) -> str:
    if blocked:
        return "该请求命中高风险默认阻止策略，未执行。"
    route = result.get("route") if isinstance(result.get("route"), dict) else {}
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    payload = result.get("result")
    if isinstance(payload, dict):
        answer = payload.get("answer")
        if answer:
            return str(answer)
        tool_name = str(tool.get("name") or "")
        if tool_name == "get_weather":
            return format_weather_answer(payload)
        if tool_name in {"render_projection_markdown", "render_lamp_countdown"}:
            path = payload.get("path") or payload.get("card_path") or payload.get("countdown_path")
            return f"投影卡片已生成：{path}" if path else "投影卡片已生成，可在 Projection 页面查看。"
        if tool_name in {"analyze_workspace_document", "summarize_workspace_document", "create_report_outline", "extract_key_data_table"}:
            return summarize_dict(payload)
        if tool_name == "search_local_content":
            count = payload.get("count")
            matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
            names = [str(item.get("workspace_name") or item.get("name") or "") for item in matches if isinstance(item, dict)]
            suffix = f"：{', '.join(name for name in names[:3] if name)}" if names else ""
            return f"在允许的 workspace/shared_inbox 中找到 {count or len(matches)} 条结果{suffix}。"
        if tool_name == "plan_office_task":
            steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
            if steps:
                first = steps[0] if isinstance(steps[0], dict) else {}
                action = str(first.get("action") or "澄清目标")
                return f"我已经把请求拆成 {len(steps)} 个可审查步骤。下一步：{action}。"
            return "我需要更明确的目标或文件，才能继续执行办公任务。"
        if payload.get("status") in {"needs_llm", "needs_search", "backend_missing", "unavailable"}:
            return summarize_dict(payload)
        summary = summarize_dict(payload)
        if summary and not summary.startswith("{"):
            return summary
    return f"已调用 {tool.get('name', route.get('skill', 'OpenClaw'))}，但没有可直接展示的结果。详情已记录审计。"


def manual_result_details(result: dict[str, object], *, blocked: bool = False) -> dict[str, object]:
    route = result.get("route") if isinstance(result.get("route"), dict) else {}
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    payload = result.get("result")
    return {
        "blocked": blocked,
        "intent": route.get("intent") or "unknown",
        "route_summary": route.get("summary") or "",
        "tool": tool.get("name") or route.get("skill") or "unknown",
        "tool_args": tool.get("args") if isinstance(tool.get("args"), dict) else {},
        "tool_result": payload if isinstance(payload, dict) else {"value": payload},
        "event_log": result.get("event_log") or "",
    }


def summarize_dict(payload: object) -> str:
    if not isinstance(payload, dict):
        return str(payload)[:240]
    for key in ("summary", "message", "path", "summary_path", "analysis_path", "table_path"):
        if payload.get(key):
            return str(payload[key])[:240]
    return json.dumps(payload, ensure_ascii=False)[:240]


def first_output_path(payload: object) -> str:
    outputs = collect_outputs(payload)
    return outputs[0]["path"] if outputs else ""


def extract_email_subject(draft: str) -> str:
    for line in draft.splitlines()[:30]:
        clean = line.strip().lstrip("-").strip()
        if clean.lower().startswith("subject:"):
            return clean.split(":", 1)[1].strip()
        if clean.startswith("主题："):
            return clean.split("：", 1)[1].strip()
    return ""


def compact_meeting_step_output(step_name: str, output: dict[str, object]) -> dict[str, object]:
    allowed_common = {
        "status",
        "provider",
        "provider_status",
        "openclaw_status",
        "meeting_id",
        "provider_task_id",
        "transcript_path",
        "transcript",
        "audio_path",
        "minutes_path",
        "tingwu_minutes_path",
        "manifest_path",
        "path",
        "output_dir",
        "provider_error",
        "openclaw_error",
        "error",
        "message",
        "content_status",
        "summary",
        "decisions",
        "action_items",
        "items",
        "turn_count",
        "final_count",
        "audio_seconds",
        "sample_rate",
        "audio_format",
        "websocket_audio_frames",
        "audio_rms",
        "audio_peak",
        "tingwu_http_operations",
        "diagnostics",
        "quality_notes",
        "fallback_reason",
        "parse_error",
    }
    if step_name == "followup":
        allowed_common.update({"required_output_paths", "email_draft_path", "followup_status"})
    compact = {key: value for key, value in output.items() if key in allowed_common}
    for key in ("minutes", "followup", "session"):
        value = output.get(key)
        if isinstance(value, dict):
            compact[key] = compact_meeting_step_output(key, value)
    tingwu_minutes = output.get("tingwu_minutes")
    if isinstance(tingwu_minutes, dict):
        compact["tingwu_minutes"] = {
            key: value
            for key, value in tingwu_minutes.items()
            if key in {"summary", "summary_source", "structured_summary", "decisions", "action_items"}
        }
    ai_minutes = output.get("ai_minutes")
    if isinstance(ai_minutes, dict):
        compact["ai_minutes"] = {
            key: value
            for key, value in ai_minutes.items()
            if key in {"summary", "decisions", "action_items", "source_data_id", "minutes_task_id"}
        }
    monitor = output.get("monitor")
    if isinstance(monitor, dict):
        compact["monitor"] = {
            key: value
            for key, value in monitor.items()
            if key in {"final_count", "audio_seconds", "websocket_audio_frames", "last_status_poll"}
        }
    http_operations = output.get("tingwu_http_operations")
    if isinstance(http_operations, list):
        compact["tingwu_http_operations"] = [
            {
                key: item.get(key)
                for key in ("timestamp", "action", "endpoint", "model", "request_task", "request_type", "request_data_id", "response_data_id", "response_status")
                if isinstance(item, dict) and key in item
            }
            for item in http_operations[-12:]
            if isinstance(item, dict)
        ]
    outputs = collect_outputs(output)
    if outputs:
        compact["outputs"] = outputs[:12]
    return sanitize_event_payload(compact)


def meeting_step_understanding(step_name: str, output: dict[str, object]) -> str:
    if step_name == "realtime_capture":
        return f"通义听悟实时采集中，已记录 {output.get('final_count', 0)} 条最终转写，音频约 {output.get('audio_seconds', 0)} 秒"
    if step_name == "import_transcript":
        return f"已解析 {output.get('parsed_count', 0)} 条发言，会议模式：{output.get('meeting_mode_enabled', False)}"
    if step_name == "minutes":
        return f"已汇总 {output.get('turn_count', 0)} 条发言，识别决策 {len(list_string(output.get('decisions')))} 条、行动项 {len(list_string(output.get('action_items')))} 条"
    if step_name == "decisions":
        return f"从会议内容提取 {len(list_string(output.get('decisions') or output.get('items')))} 条决策"
    if step_name == "action_items":
        return f"从会议内容提取 {len(list_string(output.get('action_items') or output.get('items')))} 条行动项"
    if step_name == "followup":
        return "生成会议纪要、transcript 导出、follow-up 邮件草稿和可选投影确认"
    if step_name == "reminders":
        return f"基于行动项创建 {output.get('count', 0)} 条本地 reminder 草稿"
    if step_name == "projection_confirmation":
        return "生成显示器/投影预览用确认卡，不控制办公电脑"
    return summarize_dict(output)


def meeting_step_result(step_name: str, output: dict[str, object]) -> str:
    if step_name == "realtime_capture":
        return str(output.get("transcript_path") or output.get("realtime_transcript") or summarize_dict(output))
    if step_name == "minutes":
        return str(output.get("path") or summarize_dict(output))
    if step_name == "decisions":
        items = list_string(output.get("decisions") or output.get("items"))
        return "；".join(items[:3]) or "未识别到明确决策"
    if step_name == "action_items":
        items = list_string(output.get("action_items") or output.get("items"))
        return "；".join(items[:3]) or "未识别到行动项"
    if step_name == "followup":
        paths = collect_outputs(output)
        return "、".join(str(item.get("type") or item.get("path")) for item in paths[:4]) or summarize_dict(output)
    if step_name == "reminders":
        return str(output.get("message") or f"本地 reminder 草稿 {output.get('count', 0)} 条")
    if step_name == "projection_confirmation":
        projection = output.get("projection") if isinstance(output.get("projection"), dict) else {}
        return str(projection.get("path") or output.get("path") or summarize_dict(output))
    return summarize_dict(output)


def first_nonempty_line(lines: list[str]) -> str:
    for line in lines:
        clean = line.strip("- #")
        if clean and not clean.lower().startswith("mode:"):
            return clean[:120]
    return ""


def audit_event_dto(event: dict[str, object]) -> dict[str, object]:
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    return {
        "timestamp": str(event.get("timestamp") or ""),
        "actor": str(event.get("actor") or event.get("user") or "openclaw"),
        "action": str(event.get("action") or ""),
        "status": str(event.get("status") or "ok"),
        "target": str(event.get("target") or ""),
        "details": details,
        "request_id": str(event.get("request_id") or event.get("id") or ""),
        "source_ip": str(event.get("source_ip") or ""),
        "permission_mode": str(event.get("permission_mode") or ""),
        "desktop_backend": str(event.get("desktop_backend") or ""),
    }

