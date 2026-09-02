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

__all__ = ['document_collaborator_color', 'humanize_result_title', 'scan_result_markdown', 'shared_file_matches_type', 'require_file_path', 'wiki_title_from_content', 'wiki_excerpt_from_content', 'strip_markdown_inline', 'is_text_workflow_path', 'document_adapter_status_from_runtime', 'document_result_payload']

def _module_available(*args, **kwargs):
    from .. import web_helpers
    return web_helpers._module_available(*args, **kwargs)

def collect_outputs(*args, **kwargs):
    from .. import web_helpers
    return web_helpers.collect_outputs(*args, **kwargs)

def list_string(*args, **kwargs):
    from .. import web_helpers
    return web_helpers.list_string(*args, **kwargs)

def summarize_dict(*args, **kwargs):
    from .. import web_helpers
    return web_helpers.summarize_dict(*args, **kwargs)

def document_collaborator_color(client_id: str) -> str:
    palette = ("#3370ff", "#7b61ff", "#00a870", "#d46b08", "#c94075", "#007d8a")
    digest = hashlib.sha256(client_id.encode("utf-8")).digest()
    return palette[digest[0] % len(palette)]


def humanize_result_title(stem: str, source_type: str) -> str:
    value = re.sub(
        r"(_scan_summary|_scan_color|_enhanced_ocr|_ocr(?:-\d+)?|_pdf_metadata)$",
        "",
        stem,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"[_-]+", " ", value).strip()
    value = re.sub(r"^[a-f0-9]{24,}\s*", "", value, flags=re.IGNORECASE).strip()
    timestamp = re.fullmatch(r"lamp head scan (\d{4})(\d{2})(\d{2}) (\d{2})(\d{2})(\d{2})", value, flags=re.IGNORECASE)
    if timestamp:
        year, month, day, hour, minute, _second = timestamp.groups()
        value = f"{year}-{month}-{day} {hour}:{minute} 扫描文件"
    else:
        value = re.sub(r"^scan color(?:\s+(\d+))?$", lambda match: f"文字识别{f' {match.group(1)}' if match.group(1) else ''}", value, flags=re.IGNORECASE)
    prefix = "扫描结果" if source_type == "scan" else "会议记录"
    return f"{prefix} · {value or stem}"[:160]


def scan_result_markdown(source: Path, relative: str, title: str) -> str:
    if source.suffix.lower() == ".txt":
        text = source.read_text(encoding="utf-8", errors="replace")[:200_000].strip()
        return f"# {title}\n\n## 识别文字\n\n{text or '未识别到文字。'}\n\n---\n\n原始结果：`{relative}`\n"
    return (
        f"# {title}\n\n"
        "扫描文件已保存在设备工作区，可在“处理结果”中预览或下载。\n\n"
        f"- 原始文件：`{relative}`\n"
        f"- 文件类型：{source.suffix.lower().lstrip('.').upper() or '未知'}\n"
    )


def shared_file_matches_type(item: dict[str, object], type_filter: str) -> bool:
    mime = str(item.get("mime_type") or "").lower()
    name = str(item.get("relative_path") or item.get("name") or "").lower()
    suffix = Path(name).suffix
    if type_filter == "audio":
        return mime.startswith("audio/") or suffix in {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".flac", ".aac", ".mp4"}
    if type_filter in {"image", "scan"}:
        return mime.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
    if type_filter in {"document", "text"}:
        return mime.startswith("text/") or suffix in DOCUMENT_WORKFLOW_SUFFIXES
    return type_filter in f"{mime} {name}"


def require_file_path(payload: dict[str, Any]) -> str:
    value = str(payload.get("file_path") or payload.get("filename") or payload.get("transcript") or "").strip()
    if not value:
        raise ApiError("missing_file_path", "Missing file_path.", status=400)
    return value


def wiki_title_from_content(content: str, path: Path) -> str:
    for line in content.splitlines():
        text = line.strip()
        if not text:
            continue
        heading = re.match(r"^#{1,3}\s+(.+)$", text)
        if heading:
            return strip_markdown_inline(heading.group(1))[:120] or path.stem
        return strip_markdown_inline(text)[:120] or path.stem
    return path.stem.replace("_", " ")


def wiki_excerpt_from_content(content: str, *, max_chars: int = 180) -> str:
    parts: list[str] = []
    for line in content.splitlines():
        text = line.strip()
        if not text or re.match(r"^#{1,6}\s+", text):
            continue
        parts.append(strip_markdown_inline(text))
        if sum(len(part) for part in parts) >= max_chars:
            break
    excerpt = " ".join(part for part in parts if part).strip()
    return excerpt[:max_chars]


def strip_markdown_inline(value: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", value)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.strip()


def is_text_workflow_path(path: Path) -> bool:
    return path.suffix.lower() in DOCUMENT_WORKFLOW_SUFFIXES


def document_adapter_status_from_runtime(runtime: OfficeRuntime) -> dict[str, str]:
    llm_status = "available" if runtime.config.openai_api_key else "local_rules"
    local_ocr_available = bool(shutil.which("tesseract") or _module_available("paddleocr"))
    api_ocr_available = bool(runtime.config.openai_api_key or runtime.config.dashscope_api_key)
    vision_ocr_status = "available" if (api_ocr_available or local_ocr_available) else "backend_missing"
    extraction = runtime.documents.extraction_status()
    pdf_status = str(extraction.get("pdf") or "backend_missing")
    docx_status = str(extraction.get("docx") or "backend_missing")
    pptx_status = str(extraction.get("pptx") or "backend_missing")
    xlsx_status = str(extraction.get("xlsx") or "backend_missing")
    analyzer_status = "available" if any(status == "available" for status in (pdf_status, docx_status, pptx_status, xlsx_status)) else "backend_missing"
    return {
        "document_analyzer": analyzer_status,
        "risk_scanner": analyzer_status,
        "report_outline": llm_status,
        "table_extractor": llm_status,
        "meeting_email_draft": llm_status,
        "scan_capture": "available",
        "scan_enhancement": "available",
        "ocr": vision_ocr_status,
        "vision_ocr": "available" if api_ocr_available else "backend_missing",
        "local_ocr": "available" if local_ocr_available else "backend_missing",
        "pdf_text": pdf_status,
        "docx_text": docx_status,
        "pptx_text": pptx_status,
        "xlsx_text": xlsx_status,
    }


def document_result_payload(task: dict[str, object], result: dict[str, object], status: str) -> dict[str, object]:
    outputs = collect_outputs(result)
    metadata = {key: value for key, value in result.items() if key not in {"points", "summary_path", "analysis_path", "table_path", "outline_path"}}
    llm_status = str(result.get("status") or status)
    table_status = "available" if llm_status == "completed" else llm_status
    return {
        "task_id": task["task_id"],
        "status": status,
        "summary": summarize_dict(result),
        "metadata": metadata,
        "risks": [{"marker": item, "level": "medium"} for item in list_string(result.get("risk_markers"))],
        "outputs": outputs,
        "adapter_status": {
            "document_analyzer": "available",
            "report_outline": "available" if llm_status == "completed" else llm_status,
            "table_extractor": table_status,
            "ocr": "unavailable",
        },
        **result,
    }

