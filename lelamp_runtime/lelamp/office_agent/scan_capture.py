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



class ScanCaptureMixin:
    def __init__(self, workspace: Workspace, audit: AuditLogger, config: OfficeAgentConfig | None = None):
        self.workspace = workspace
        self.audit = audit
        self.config = config
    def register_scan_image(self, image_filename: str, document_type: str = "document") -> dict[str, object]:
        path = self.workspace.resolve_workspace_file(image_filename)
        sidecar = {
            "image": str(path),
            "document_type": document_type,
            "status": "registered",
            "next_backend": "OpenAI/DashScope vision OCR or local OCR adapter",
            "pipeline": [
                "edge_detection",
                "perspective_correction",
                "shadow_removal",
                "ocr",
                "structure_extraction",
                "semantic_analysis",
            ],
        }
        metadata_path = self.workspace.write_json(
            safe_filename(Path(image_filename).stem, suffix="_scan_metadata.json"),
            sidecar,
            action="scan.register",
        )
        payload = {**sidecar, "metadata_path": str(metadata_path)}
        self.audit.record("scan.image_registered", target=image_filename, details=payload)
        return payload
    def capture_scan_image(
        self,
        image_data_url: str,
        *,
        title: str = "document_scan",
        document_type: str = "document",
        language: str = "chi_sim+eng",
        max_bytes: int = 20 * 1024 * 1024,
    ) -> dict[str, object]:
        mime_type, data = self._decode_image_data_url(image_data_url)
        if len(data) > max_bytes:
            payload = {
                "status": "blocked",
                "reason": f"Scan image exceeds limit of {max_bytes} bytes.",
                "bytes": len(data),
            }
            self.audit.record("scan.capture", status="blocked", target=title, details=payload)
            return payload
        image_path = self.workspace.path_for_new_file(
            safe_filename(title, default="document_scan", suffix=f"_capture{IMAGE_MIME_EXTENSIONS[mime_type]}")
        )
        image_path.write_bytes(data)
        workspace_name = str(image_path.relative_to(self.workspace.root))
        self.audit.record(
            "scan.capture",
            target=workspace_name,
            details={"bytes": len(data), "mime_type": mime_type, "document_type": document_type},
        )
        result = self.process_scan_image(workspace_name, document_type=document_type, language=language)
        result["source_image_path"] = str(image_path)
        result["source_workspace_name"] = workspace_name
        return result
    def create_demo_scan_image(
        self,
        *,
        title: str = "validation_scan_demo",
        document_type: str = "document",
    ) -> dict[str, object]:
        try:
            from PIL import Image, ImageDraw, ImageFont  # type: ignore
        except Exception as exc:
            payload = {
                "status": "backend_missing",
                "reason": "Pillow is required to generate a demo scan image.",
                "error": str(exc)[:500],
            }
            self.audit.record("scan.demo_image", status="blocked", target=title, details=payload)
            return payload

        page_width, page_height = 900, 640
        paper = Image.new("RGB", (page_width, page_height), (252, 252, 246))
        draw = ImageDraw.Draw(paper)
        font = ImageFont.load_default()
        title_font = ImageFont.load_default()
        draw.rectangle((0, 0, page_width - 1, page_height - 1), outline=(215, 215, 205), width=3)
        y = 42
        lines = [
            "OpenClaw Demo Scan",
            "Decision: validate document capture pipeline",
            "Owner: Alice    Due: 2026-06-08",
            "Amount: $12,500",
            "Risk: confidential termination liability",
            "",
            "Item,Owner,Status",
            "Projection profile,Alice,Done",
            "Document OCR,Bob,Needs backend",
            "Meeting minutes,Carol,Ready",
        ]
        for index, line in enumerate(lines):
            fill = (30, 50, 70) if index == 0 else (45, 55, 65)
            draw.text((62, y), line, fill=fill, font=title_font if index == 0 else font)
            y += 46 if index == 0 else 38
        draw.line((58, 305, 830, 305), fill=(160, 170, 170), width=2)
        draw.line((58, 350, 830, 350), fill=(210, 215, 215), width=1)
        draw.line((58, 390, 830, 390), fill=(210, 215, 215), width=1)
        draw.line((58, 430, 830, 430), fill=(210, 215, 215), width=1)
        draw.line((350, 305, 350, 468), fill=(210, 215, 215), width=1)
        draw.line((610, 305, 610, 468), fill=(210, 215, 215), width=1)

        canvas = Image.new("RGB", (1280, 900), (49, 67, 82))
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore

            source = np.array(paper)
            destination = np.array(
                [[210, 125], [1105, 175], [1035, 795], [165, 735]],
                dtype="float32",
            )
            source_points = np.array(
                [[0, 0], [page_width - 1, 0], [page_width - 1, page_height - 1], [0, page_height - 1]],
                dtype="float32",
            )
            matrix = cv2.getPerspectiveTransform(source_points, destination)
            warped = cv2.warpPerspective(source, matrix, (1280, 900))
            mask = cv2.warpPerspective(np.full((page_height, page_width), 255, dtype=np.uint8), matrix, (1280, 900))
            background = np.array(canvas)
            background[mask > 0] = warped[mask > 0]
            canvas = Image.fromarray(background)
        except Exception:
            canvas.paste(paper.rotate(2, expand=True, fillcolor=(49, 67, 82)), (180, 115))

        image_path = self.workspace.path_for_new_file(
            safe_filename(title, default="validation_scan_demo", suffix="_demo_scan.png")
        )
        image_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(image_path, format="PNG")
        workspace_name = str(image_path.relative_to(self.workspace.root))
        payload = {
            "status": "completed",
            "workspace_name": workspace_name,
            "image_path": str(image_path),
            "document_type": document_type,
            "synthetic": True,
            "purpose": "target_validation_document_scanning",
            "expected_pipeline": ["boundary_detection", "perspective_correction", "enhancement", "ocr_if_backend_available"],
        }
        self.audit.record("scan.demo_image", target=workspace_name, details=payload)
        return payload
    def process_scan_image(
        self,
        image_filename: str,
        *,
        document_type: str = "document",
        language: str = "chi_sim+eng",
    ) -> dict[str, object]:
        image_path = self.workspace.resolve_workspace_file(image_filename)
        if image_path.suffix.lower() not in IMAGE_SUFFIXES:
            payload = {
                "image": image_filename,
                "document_type": document_type,
                "status": "blocked",
                "reason": "Scan processing input must be an imported workspace image.",
                "supported_suffixes": sorted(IMAGE_SUFFIXES),
            }
            self.audit.record("scan.process", status="blocked", target=image_filename, details=payload)
            return payload

        registered = self.register_scan_image(image_filename, document_type)
        enhancement = self.enhance_scan_image(image_filename)
        ocr_input_selection = _select_ocr_input(enhancement, image_filename)
        ocr_input = str(ocr_input_selection.get("workspace_name") or image_filename)
        ocr_result = self.run_ocr(ocr_input, language)
        status = str(ocr_result.get("status") or "backend_missing")
        image_pipeline_completed = str(registered.get("status")) == "registered" and str(enhancement.get("status")) == "completed"
        pipeline_status = "completed" if status in {"ok", "completed"} else status
        payload = {
            "status": pipeline_status,
            "image_pipeline_status": "completed" if image_pipeline_completed else str(enhancement.get("status") or "adapter_ready"),
            "ocr_status": status,
            "image": image_filename,
            "ocr_input": ocr_input,
            "ocr_input_selection": ocr_input_selection,
            "document_type": document_type,
            "registered": registered,
            "enhancement": enhancement,
            "ocr": ocr_result,
            "summary": ocr_result.get("summary") or ocr_result.get("message") or "",
            "text_path": ocr_result.get("text_path") or "",
            "summary_path": ocr_result.get("summary_path") or "",
            "structure_path": ocr_result.get("structure_path") or "",
            "table_paths": ocr_result.get("table_paths") or [],
            "business_card_path": ocr_result.get("business_card_path") or "",
            "contract_path": ocr_result.get("contract_path") or "",
            "tables": ocr_result.get("tables") or [],
            "entities": ocr_result.get("entities") or {},
            "risks": ocr_result.get("risks") or [],
            "business_card": ocr_result.get("business_card") or {},
            "contract": ocr_result.get("contract") or {},
            "quality_notes": ocr_result.get("quality_notes") or [],
        }
        self.audit.record("scan.process", target=image_filename, details={"status": pipeline_status, "ocr_input": ocr_input})
        return payload
    def capture_readiness(self, image_filename: str) -> dict[str, object]:
        enhancement = self.enhance_scan_image(image_filename)
        metrics = enhancement.get("metrics") if isinstance(enhancement.get("metrics"), dict) else {}
        boundary = enhancement.get("boundary") if isinstance(enhancement.get("boundary"), dict) else {}
        brightness = _float_metric(metrics.get("brightness"))
        contrast = _float_metric(metrics.get("contrast"))
        sharpness = _float_metric(metrics.get("sharpness_laplacian"))
        edge_density = _float_metric(metrics.get("edge_density"))
        boundary_detected = bool(enhancement.get("boundary_detected"))
        document_region_detected = bool(boundary_detected or enhancement.get("document_region_detected"))
        checks = {
            "boundary_detected": boundary_detected,
            "document_region_detected": document_region_detected,
            "boundary_confidence": _float_metric(boundary.get("confidence")),
            "brightness_ok": 60 <= brightness <= 215,
            "contrast_ok": contrast >= 18,
            "sharpness_ok": sharpness >= 45,
            "edge_density_ok": 0.01 <= edge_density <= 0.32,
        }
        recommendations: list[str] = []
        if not checks["document_region_detected"]:
            recommendations.append("未稳定识别到文件纸面区域，请让文件完整入镜并尽量露出四角。")
        elif not checks["boundary_detected"]:
            recommendations.append("未稳定识别到文档边界，请让纸张完整入镜并露出四角。")
        if not checks["brightness_ok"]:
            recommendations.append("画面亮度不理想，请调整台灯或避免强反光。")
        if not checks["contrast_ok"]:
            recommendations.append("文字/纸张对比度偏低，请换深色背景或改善光照。")
        if not checks["sharpness_ok"]:
            recommendations.append("画面可能模糊，请保持设备稳定或靠近文档。")
        if not checks["edge_density_ok"]:
            recommendations.append("边缘信息异常，可能过曝、欠曝或画面内容太少。")
        stable_score = sum(1 for ok in checks.values() if ok is True) / max(1, len(checks))
        ready = document_region_detected and checks["brightness_ok"] and checks["contrast_ok"] and checks["sharpness_ok"]
        result = {
            "status": "ready_to_capture" if ready else "needs_adjustment",
            "ready": ready,
            "auto_capture_candidate": ready,
            "stable_score": round(stable_score, 4),
            "image": image_filename,
            "checks": checks,
            "metrics": metrics,
            "enhancement": enhancement,
            "recommendations": recommendations or ["画面质量满足自动拍照候选条件。"],
        }
        path = self.workspace.write_json(
            safe_filename(Path(image_filename).stem, suffix="_capture_readiness.json"),
            result,
            action="scan.capture_readiness",
        )
        result["readiness_path"] = str(path)
        self.audit.record("scan.capture_readiness", target=image_filename, details={"status": result["status"], "ready": ready, "stable_score": result["stable_score"]})
        return result

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
