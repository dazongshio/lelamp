from __future__ import annotations

import base64
import csv
import io
import json
import mimetypes
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .config import OfficeAgentConfig
from .llm import LLMError, ResponsesLLM, ResponsesLLMConfig
from .workspace import Workspace, WorkspaceError
from .utils import safe_filename


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
IMAGE_MIME_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
RISK_MARKERS = ["liability", "termination", "confidential", "违约", "终止", "保密", "赔偿", "付款"]
TESSERACT_LANGUAGE_MAP = {
    "ch": "chi_sim",
    "zh": "chi_sim",
    "zh_cn": "chi_sim",
    "chi_sim": "chi_sim",
    "en": "eng",
    "eng": "eng",
    "chi_sim+eng": "chi_sim+eng",
}



class ScanOcrMixin:
    def run_ocr(self, image_filename: str, language: str = "ch") -> dict[str, object]:
        image_path = self.workspace.resolve_workspace_file(image_filename)
        if image_path.suffix.lower() not in IMAGE_SUFFIXES:
            payload = {
                "image": image_filename,
                "status": "blocked",
                "reason": "OCR input must be an imported workspace image.",
                "supported_suffixes": sorted(IMAGE_SUFFIXES),
            }
            self.audit.record("scan.ocr", status="blocked", target=image_filename, details=payload)
            return payload

        api_result = self._run_vision_ocr(image_path, language)
        if api_result is not None and str(api_result.get("status")) in {"completed", "backend_missing"}:
            return api_result

        local_result = self._run_local_ocr(image_path, language)
        if local_result is not None:
            return local_result

        payload = {
            "image": image_filename,
            "language": language,
            "status": "backend_missing",
            "recommended_backend": "OpenAI or DashScope vision OCR",
            "fallback_backend": "PaddleOCR PP-OCRv5 or tesseract",
            "install_hint": "Configure OPENAI_API_KEY or DASHSCOPE_API_KEY for API OCR, or install PaddleOCR/tesseract for local fallback.",
        }
        path = self.workspace.write_json(
            safe_filename(Path(image_filename).stem, suffix="_ocr_request.json"),
            payload,
            action="scan.ocr_request",
        )
        payload["request_path"] = str(path)
        self.audit.record("scan.ocr", status="blocked", target=image_filename, details=payload)
        return payload
    def run_ocr_placeholder(self, image_filename: str, language: str = "ch") -> dict[str, object]:
        return self.run_ocr(image_filename, language)
    def summarize_ocr_text(self, filename: str) -> dict[str, object]:
        text = self.workspace.read_text(filename, max_chars=40000)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        emails = sorted(set(_find_emails(text)))
        phones = sorted(set(_find_phone_candidates(lines)))
        risk_markers = [marker for marker in RISK_MARKERS if marker.lower() in text.lower()]
        payload = {
            "source": filename,
            "status": "ok",
            "line_count": len(lines),
            "preview_lines": lines[:12],
            "emails": emails[:20],
            "phones": phones[:20],
            "risk_markers": risk_markers,
            "structure": {
                "has_table_like_lines": any("," in line or "\t" in line or "|" in line for line in lines),
                "numbered_lines": [line for line in lines if line[:2].strip(".、").isdigit()][:10],
            },
        }
        path = self.workspace.write_json(
            safe_filename(Path(filename).stem, suffix="_ocr_summary.json"),
            payload,
            action="scan.ocr_summary",
        )
        payload["path"] = str(path)
        self.audit.record("scan.ocr_summary", target=filename, details={"lines": len(lines), "path": str(path)})
        return payload
    def analyze_business_card_text(self, filename: str) -> dict[str, object]:
        text = self.workspace.read_text(filename, max_chars=20000)
        structure = _heuristic_structure_from_text(text, "business_card")
        payload = {
            "source": filename,
            "name_candidate": structure["business_card"].get("name", ""),
            "organization_candidate": structure["business_card"].get("organization", ""),
            "emails": structure["business_card"].get("emails", []),
            "phones": structure["business_card"].get("phones", []),
            "title_candidate": structure["business_card"].get("title", ""),
        }
        path = self.workspace.write_json(
            safe_filename(Path(filename).stem, suffix="_business_card.json"),
            payload,
            action="scan.business_card_parse",
        )
        payload["path"] = str(path)
        self.audit.record("scan.business_card_analyze", target=filename, details=payload)
        return payload
    def _run_paddleocr(self, image_path: Path, language: str) -> dict[str, object] | None:
        try:
            from paddleocr import PaddleOCR  # type: ignore
        except Exception:
            return None

        lang = "ch" if language in {"ch", "zh", "zh_cn", "chi_sim", "chi_sim+eng"} else language
        try:
            ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
            result = ocr.ocr(str(image_path), cls=True)
        except Exception as exc:
            payload = {
                "status": "error",
                "backend": "paddleocr",
                "image_path": str(image_path),
                "error": str(exc)[:1000],
            }
            self.audit.record("scan.ocr", status="error", target=str(image_path), details=payload)
            return payload

        lines: list[str] = []
        confidences: list[float] = []
        for page in result or []:
            for item in page or []:
                if len(item) < 2:
                    continue
                text_info = item[1]
                if isinstance(text_info, (list, tuple)) and text_info:
                    lines.append(str(text_info[0]))
                    if len(text_info) > 1:
                        try:
                            confidences.append(float(text_info[1]))
                        except (TypeError, ValueError):
                            pass
        return self._write_ocr_result(
            image_path,
            backend="paddleocr",
            language=language,
            text="\n".join(line for line in lines if line.strip()),
            confidences=confidences,
        )
    def _run_local_ocr(self, image_path: Path, language: str) -> dict[str, object] | None:
        paddle_result = self._run_paddleocr(image_path, language)
        if paddle_result is not None:
            return paddle_result
        return self._run_tesseract(image_path, language)
    def _run_vision_ocr(self, image_path: Path, language: str) -> dict[str, object] | None:
        if self.config is None:
            return None
        providers: list[tuple[str, ResponsesLLMConfig]] = []
        if self.config.openai_api_key:
            providers.append(
                (
                    "openai_vision",
                    ResponsesLLMConfig(
                        api_key=self.config.openai_api_key,
                        base_url=self.config.openai_base_url,
                        model=self.config.openai_vision_model,
                        reasoning_effort="low",
                    ),
                )
            )
        if self.config.dashscope_api_key:
            providers.append(
                (
                    "dashscope_qwen_vl",
                    ResponsesLLMConfig(
                        api_key=self.config.dashscope_api_key,
                        base_url=self.config.dashscope_vision_base_url,
                        model=self.config.dashscope_vision_model,
                        reasoning_effort="low",
                        wire_api=self.config.dashscope_vision_wire_api or "chat_completions",
                    ),
                )
            )
        if not providers:
            return None

        prompt = "\n".join(
            [
                "对这张用户主动拍摄或上传的实体文档图片执行 OCR、版面结构识别和办公语义解析。",
                f"OCR 语言提示：{language}。",
                "只基于图片内容，不要补充图片中没有的信息。",
                "输出严格 JSON，不要 Markdown 代码块。",
                "JSON 字段：ocr_text, summary, key_points, tables, entities, risks, business_card, contract, quality_notes, uncertain_items。",
                "tables 中每个表格应包含 title, headers, rows。",
                "看不清、被裁剪或无法判断的信息写入 uncertain_items。",
            ]
        )
        errors: list[str] = []
        image_data_url = _image_path_to_data_url(image_path)
        for backend, config in providers:
            try:
                raw = ResponsesLLM(config).complete_multimodal(
                    instructions="你是严谨的实体文档 OCR 与结构识别助手。只处理用户主动提供的图片。",
                    text=prompt,
                    image_data_url=image_data_url,
                    context={
                        "task": "physical_document_scan_ocr",
                        "image": str(image_path.relative_to(self.workspace.root)),
                        "backend": backend,
                    },
                    timeout=120,
                )
            except LLMError as exc:
                errors.append(f"{backend}: {str(exc)[:500]}")
                self.audit.record(
                    "scan.ocr",
                    status="blocked",
                    target=str(image_path),
                    details={"backend": backend, "error": str(exc)[:500]},
                )
                continue

            parsed = _parse_json_object(raw)
            text = str(parsed.get("ocr_text") or parsed.get("text") or raw).strip()
            if not text:
                errors.append(f"{backend}: Vision OCR returned no usable text.")
                self.audit.record(
                    "scan.ocr",
                    status="blocked",
                    target=str(image_path),
                    details={"backend": backend, "error": "empty_text"},
                )
                continue
            heuristics = _heuristic_structure_from_text(text, "document")
            structure = _merge_structure(heuristics, parsed)
            return self._persist_structured_result(
                image_path,
                backend=backend,
                language=language,
                text=text,
                structure=structure,
                status="completed",
                provider="ResponsesLLM",
                model=config.model,
            )

        payload = {
            "status": "backend_missing",
            "backend": "vision_ocr",
            "provider": "ResponsesLLM",
            "image": str(image_path.relative_to(self.workspace.root)),
            "language": language,
            "message": "; ".join(errors) or "No vision OCR provider is configured.",
        }
        self.audit.record("scan.ocr", status="blocked", target=str(image_path), details=payload)
        return payload
    def _run_tesseract(self, image_path: Path, language: str) -> dict[str, object] | None:
        if shutil.which("tesseract") is None:
            return None
        tesseract_language = TESSERACT_LANGUAGE_MAP.get(language.lower(), language)
        command = ["tesseract", str(image_path), "stdout", "-l", tesseract_language]
        try:
            completed = subprocess.run(command, check=False, timeout=45, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            payload = {"status": "timeout", "backend": "tesseract", "image_path": str(image_path), "command": command}
            self.audit.record("scan.ocr", status="error", target=str(image_path), details=payload)
            return payload
        if completed.returncode != 0:
            payload = {
                "status": "error",
                "backend": "tesseract",
                "image_path": str(image_path),
                "stderr": completed.stderr.strip()[:1000],
                "command": command,
            }
            self.audit.record("scan.ocr", status="error", target=str(image_path), details=payload)
            return payload
        return self._write_ocr_result(
            image_path,
            backend="tesseract",
            language=tesseract_language,
            text=completed.stdout.strip(),
            confidences=[],
        )

from .scan_image_utils import (
    _center_document_crop, _decode_textin_image, _document_text_metrics,
    _draw_corner_preview, _empty_structure_value, _enhance_color_scan,
    _enhance_for_ocr, _extract_table_like_blocks, _fallback_document_region,
    _find_emails, _find_phone_candidates, _float_metric, _four_point_transform,
    _gray_world_white_balance, _heuristic_structure_from_text, _image_metrics,
    _image_path_to_data_url, _lift_paper_background, _merge_structure,
    _order_points, _order_textin_points, _parse_json_object, _perspective_correct,
    _scan_summary_markdown, _select_ocr_input, _textin_boundary_from_result,
    _textin_image_items, _trim_border,
)
