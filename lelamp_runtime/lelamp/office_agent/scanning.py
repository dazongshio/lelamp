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


class ScanService:
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

    def enhance_scan_image(self, image_filename: str) -> dict[str, object]:
        image_path = self.workspace.resolve_workspace_file(image_filename)
        if image_path.suffix.lower() not in IMAGE_SUFFIXES:
            payload = {"status": "blocked", "image": image_filename, "reason": "Enhancement input must be an image."}
            self.audit.record("scan.enhance", status="blocked", target=image_filename, details=payload)
            return payload
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception as exc:
            payload = {
                "status": "backend_missing",
                "image": image_filename,
                "reason": "opencv-python is not available.",
                "install_hint": "Install opencv-python-headless to enable edge detection, correction, and enhancement.",
                "error": str(exc)[:500],
            }
            self.audit.record("scan.enhance", status="blocked", target=image_filename, details=payload)
            return payload

        frame = cv2.imread(str(image_path))
        if frame is None:
            payload = {"status": "error", "image": image_filename, "reason": "OpenCV could not read image."}
            self.audit.record("scan.enhance", status="error", target=image_filename, details=payload)
            return payload

        original_height, original_width = frame.shape[:2]
        textin_enhancement = self._run_textin_crop_enhance(image_path)
        if isinstance(textin_enhancement, dict) and str(textin_enhancement.get("status")) == "completed":
            textin_workspace_name = str(textin_enhancement.get("workspace_name") or "")
            try:
                textin_image_path = self.workspace.resolve_workspace_file(textin_workspace_name)
            except Exception:
                textin_image_path = None
            textin_frame = cv2.imread(str(textin_image_path)) if textin_image_path is not None else None
            if textin_frame is not None:
                boundary = _textin_boundary_from_result(textin_enhancement, textin_frame)
                corner_preview = _draw_corner_preview(textin_frame, boundary, cv2, np)
                color_scan = _enhance_color_scan(textin_frame, cv2, trim_border=False)
                ocr_scan = _enhance_for_ocr(textin_frame, cv2)
                return self._write_enhancement_payload(
                    image_filename=image_filename,
                    original_frame=frame,
                    corrected=textin_frame,
                    color_scan=color_scan,
                    ocr_scan=ocr_scan,
                    corner_preview=corner_preview,
                    boundary=boundary,
                    pipeline_backend="textin_crop_enhance",
                    external_enhancement=textin_enhancement,
                )

        corrected, boundary = _perspective_correct(frame, cv2, np)
        corner_preview = _draw_corner_preview(frame, boundary, cv2, np)
        color_scan = _enhance_color_scan(corrected, cv2)
        ocr_scan = _enhance_for_ocr(corrected, cv2)
        return self._write_enhancement_payload(
            image_filename=image_filename,
            original_frame=frame,
            corrected=corrected,
            color_scan=color_scan,
            ocr_scan=ocr_scan,
            corner_preview=corner_preview,
            boundary=boundary,
            pipeline_backend="local_opencv",
            external_enhancement=textin_enhancement if isinstance(textin_enhancement, dict) else None,
        )

    def _write_enhancement_payload(
        self,
        *,
        image_filename: str,
        original_frame: Any,
        corrected: Any,
        color_scan: Any,
        ocr_scan: Any,
        corner_preview: Any,
        boundary: dict[str, object],
        pipeline_backend: str,
        external_enhancement: dict[str, object] | None = None,
    ) -> dict[str, object]:
        import cv2  # type: ignore

        original_height, original_width = original_frame.shape[:2]
        corner_preview_path = self.workspace.path_for_new_file(safe_filename(Path(image_filename).stem, suffix="_scan_corners.png"))
        if not cv2.imwrite(str(corner_preview_path), corner_preview):
            payload = {"status": "error", "image": image_filename, "reason": "OpenCV failed to write corner preview image."}
            self.audit.record("scan.enhance", status="error", target=image_filename, details=payload)
            return payload
        enhanced_path = self.workspace.path_for_new_file(safe_filename(Path(image_filename).stem, suffix="_scan_color.png"))
        if not cv2.imwrite(str(enhanced_path), color_scan):
            payload = {"status": "error", "image": image_filename, "reason": "OpenCV failed to write enhanced image."}
            self.audit.record("scan.enhance", status="error", target=image_filename, details=payload)
            return payload
        ocr_path = self.workspace.path_for_new_file(safe_filename(Path(image_filename).stem, suffix="_enhanced_ocr.png"))
        if not cv2.imwrite(str(ocr_path), ocr_scan):
            payload = {"status": "error", "image": image_filename, "reason": "OpenCV failed to write OCR image."}
            self.audit.record("scan.enhance", status="error", target=image_filename, details=payload)
            return payload

        corner_preview_workspace_name = str(corner_preview_path.relative_to(self.workspace.root))
        enhanced_workspace_name = str(enhanced_path.relative_to(self.workspace.root))
        ocr_workspace_name = str(ocr_path.relative_to(self.workspace.root))
        document_region_detected = bool(boundary.get("detected") or boundary.get("document_region_detected"))
        correction_status = "detected" if bool(boundary.get("detected")) else (
            "fallback_document_region" if document_region_detected else "fallback_crop"
        )
        payload = {
            "status": "completed",
            "image": image_filename,
            "corner_preview_image_path": str(corner_preview_path),
            "corner_preview_workspace_name": corner_preview_workspace_name,
            "enhanced_image_path": str(enhanced_path),
            "enhanced_workspace_name": enhanced_workspace_name,
            "color_image_path": str(enhanced_path),
            "color_workspace_name": enhanced_workspace_name,
            "ocr_image_path": str(ocr_path),
            "ocr_workspace_name": ocr_workspace_name,
            "original_size": {"width": original_width, "height": original_height},
            "corrected_size": {"width": int(corrected.shape[1]), "height": int(corrected.shape[0])},
            "enhanced_size": {"width": int(color_scan.shape[1]), "height": int(color_scan.shape[0])},
            "ocr_size": {"width": int(ocr_scan.shape[1]), "height": int(ocr_scan.shape[0])},
            "boundary_detected": bool(boundary.get("detected")),
            "document_region_detected": document_region_detected,
            "boundary": boundary,
            "auto_corner_correction": {
                "enabled": True,
                "status": correction_status,
                "confidence": boundary.get("confidence"),
                "point_order": boundary.get("point_order") or [],
                "points": boundary.get("points") or [],
                "preview_workspace_name": corner_preview_workspace_name,
            },
            "metrics": _image_metrics(original_frame, color_scan, boundary, cv2),
            "backend": pipeline_backend,
            "external_enhancement": external_enhancement or {},
            "pipeline": [
                pipeline_backend,
                "document_boundary_detection",
                "auto_four_corner_detection",
                "four_point_perspective_correction",
                "color_shadow_removal",
                "white_balance",
                "mild_contrast_boost",
                "mild_sharpening",
                "ocr_shadow_normalization_without_binarization",
            ],
        }
        path = self.workspace.write_json(
            safe_filename(Path(image_filename).stem, suffix="_enhancement.json"),
            payload,
            action="scan.enhancement_result",
        )
        payload["enhancement_path"] = str(path)
        self.audit.record(
            "scan.enhance",
            target=image_filename,
            details={
                "enhanced": enhanced_workspace_name,
                "corner_preview": corner_preview_workspace_name,
                "boundary_detected": payload["boundary_detected"],
                "boundary_confidence": boundary.get("confidence"),
            },
        )
        return payload

    def _run_textin_crop_enhance(self, image_path: Path) -> dict[str, object] | None:
        config = self.config
        if config is None:
            return None
        if not config.textin_app_id or not config.textin_secret_code:
            return None
        params = urllib.parse.urlencode(
            {
                "enhance_mode": str(config.textin_crop_enhance_mode),
                "crop_image": "1" if config.textin_crop_enabled else "0",
                "dewarp_image": "1" if config.textin_dewarp_enabled else "0",
                "correct_direction": "1" if config.textin_correct_direction_enabled else "0",
            }
        )
        request = urllib.request.Request(
            f"{config.textin_crop_enhance_url}?{params}",
            data=image_path.read_bytes(),
            headers={
                "x-ti-app-id": config.textin_app_id,
                "x-ti-secret-code": config.textin_secret_code,
                "Content-Type": "application/octet-stream",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=config.textin_timeout_seconds) as response:
                http_status = response.status
                raw = response.read()
        except Exception as exc:
            payload = {
                "status": "error",
                "backend": "textin_crop_enhance",
                "reason": "request_failed",
                "error": str(exc)[:500],
            }
            self.audit.record("scan.textin_crop_enhance", status="error", target=str(image_path), details=payload)
            return payload
        try:
            response_payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            payload = {
                "status": "error",
                "backend": "textin_crop_enhance",
                "reason": "invalid_json_response",
                "http_status": http_status,
                "error": str(exc)[:500],
            }
            self.audit.record("scan.textin_crop_enhance", status="error", target=str(image_path), details=payload)
            return payload

        code = response_payload.get("code")
        if code != 200:
            payload = {
                "status": "error",
                "backend": "textin_crop_enhance",
                "http_status": http_status,
                "code": code,
                "message": str(response_payload.get("message") or response_payload.get("msg") or "")[:500],
            }
            self.audit.record("scan.textin_crop_enhance", status="error", target=str(image_path), details=payload)
            return payload

        image_items = _textin_image_items(response_payload)
        if not image_items:
            payload = {
                "status": "error",
                "backend": "textin_crop_enhance",
                "http_status": http_status,
                "code": code,
                "reason": "no_processed_image",
            }
            self.audit.record("scan.textin_crop_enhance", status="error", target=str(image_path), details=payload)
            return payload

        item = image_items[0]
        image_data = _decode_textin_image(str(item.get("image") or ""))
        if image_data is None:
            payload = {
                "status": "error",
                "backend": "textin_crop_enhance",
                "http_status": http_status,
                "code": code,
                "reason": "processed_image_decode_failed",
            }
            self.audit.record("scan.textin_crop_enhance", status="error", target=str(image_path), details=payload)
            return payload

        suffix = ".jpg" if image_data.startswith(b"\xff\xd8\xff") else ".png"
        image_output_path = self.workspace.path_for_new_file(safe_filename(image_path.stem, suffix=f"_textin_scan{suffix}"))
        image_output_path.write_bytes(image_data)
        metadata = {
            "status": "completed",
            "backend": "textin_crop_enhance",
            "http_status": http_status,
            "code": code,
            "message": response_payload.get("message") or response_payload.get("msg") or "",
            "version": response_payload.get("version") or "",
            "duration": response_payload.get("duration"),
            "workspace_name": str(image_output_path.relative_to(self.workspace.root)),
            "image_path": str(image_output_path),
            "image_bytes": len(image_data),
            "origin_width": (response_payload.get("result") or {}).get("origin_width") if isinstance(response_payload.get("result"), dict) else None,
            "origin_height": (response_payload.get("result") or {}).get("origin_height") if isinstance(response_payload.get("result"), dict) else None,
            "cropped_width": item.get("cropped_width"),
            "cropped_height": item.get("cropped_height"),
            "position": item.get("position") if isinstance(item.get("position"), list) else [],
            "angle": item.get("angle"),
            "x_request_id": response_payload.get("x_request_id") or "",
        }
        metadata_path = self.workspace.write_json(
            safe_filename(image_path.stem, suffix="_textin_scan_enhancement.json"),
            metadata,
            action="scan.textin_crop_enhance_metadata",
        )
        metadata["metadata_path"] = str(metadata_path)
        self.audit.record(
            "scan.textin_crop_enhance",
            target=str(image_path),
            details={k: v for k, v in metadata.items() if k not in {"image_path"}},
        )
        return metadata

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

    def _write_ocr_result(
        self,
        image_path: Path,
        *,
        backend: str,
        language: str,
        text: str,
        confidences: list[float],
    ) -> dict[str, object]:
        structure = _heuristic_structure_from_text(text, "document")
        return self._persist_structured_result(
            image_path,
            backend=backend,
            language=language,
            text=text,
            structure=structure,
            status="ok",
            confidence_avg=round(sum(confidences) / len(confidences), 4) if confidences else None,
        )

    def _persist_structured_result(
        self,
        image_path: Path,
        *,
        backend: str,
        language: str,
        text: str,
        structure: dict[str, Any],
        status: str,
        provider: str = "",
        model: str = "",
        confidence_avg: float | None = None,
    ) -> dict[str, object]:
        if not image_path.is_relative_to(self.workspace.root):
            raise WorkspaceError("OCR result source must stay inside workspace.")

        text_path = self.workspace.write_text(
            safe_filename(image_path.stem, suffix="_ocr.txt"),
            text,
            action="scan.ocr_text_write",
        )
        table_paths = self._write_table_artifacts(image_path, structure.get("tables"))
        business_card = structure.get("business_card") if isinstance(structure.get("business_card"), dict) else {}
        contract = structure.get("contract") if isinstance(structure.get("contract"), dict) else {}
        business_card_path = ""
        contract_path = ""
        if business_card:
            business_card_path = str(
                self.workspace.write_json(
                    safe_filename(image_path.stem, suffix="_business_card.json"),
                    business_card,
                    action="scan.business_card_result",
                )
            )
        if contract:
            contract_path = str(
                self.workspace.write_json(
                    safe_filename(image_path.stem, suffix="_contract.json"),
                    contract,
                    action="scan.contract_result",
                )
            )

        summary = {
            "status": status,
            "backend": backend,
            "provider": provider,
            "model": model,
            "image": str(image_path.relative_to(self.workspace.root)),
            "language": language,
            "text_path": str(text_path),
            "chars": len(text),
            "line_count": len([line for line in text.splitlines() if line.strip()]),
            "confidence_avg": confidence_avg,
            "preview": text[:1000],
            "summary": str(structure.get("summary") or ""),
            "key_points": structure.get("key_points") if isinstance(structure.get("key_points"), list) else [],
            "tables": structure.get("tables") if isinstance(structure.get("tables"), list) else [],
            "table_paths": table_paths,
            "entities": structure.get("entities") if isinstance(structure.get("entities"), dict) else {},
            "risks": structure.get("risks") if isinstance(structure.get("risks"), list) else [],
            "business_card": business_card,
            "business_card_path": business_card_path,
            "contract": contract,
            "contract_path": contract_path,
            "quality_notes": structure.get("quality_notes") if isinstance(structure.get("quality_notes"), list) else [],
            "uncertain_items": structure.get("uncertain_items") if isinstance(structure.get("uncertain_items"), list) else [],
        }
        structure_path = self.workspace.write_json(
            safe_filename(image_path.stem, suffix="_scan_structure.json"),
            summary,
            action="scan.structure_result",
        )
        summary_markdown = _scan_summary_markdown(summary)
        summary_path = self.workspace.write_text(
            safe_filename(image_path.stem, suffix="_scan_summary.md"),
            summary_markdown,
            action="scan.summary_write",
        )
        result_path = self.workspace.write_json(
            safe_filename(image_path.stem, suffix="_ocr_result.json"),
            summary,
            action="scan.ocr_result",
        )
        summary["structure_path"] = str(structure_path)
        summary["summary_path"] = str(summary_path)
        summary["result_path"] = str(result_path)
        self.audit.record(
            "scan.ocr",
            target=str(image_path),
            details={"backend": backend, "chars": len(text), "result_path": str(result_path)},
        )
        return summary

    def _write_table_artifacts(self, image_path: Path, tables: object) -> list[str]:
        if not isinstance(tables, list):
            return []
        paths: list[str] = []
        for index, table in enumerate(tables[:5], start=1):
            if not isinstance(table, dict):
                continue
            headers = table.get("headers") if isinstance(table.get("headers"), list) else []
            rows = table.get("rows") if isinstance(table.get("rows"), list) else []
            if not headers and not rows:
                continue
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            if headers:
                writer.writerow([str(item) for item in headers])
            for row in rows:
                if isinstance(row, list):
                    writer.writerow([str(item) for item in row])
            table_path = self.workspace.write_text(
                safe_filename(image_path.stem, suffix=f"_table_{index}.csv"),
                buffer.getvalue(),
                action="scan.table_result",
            )
            paths.append(str(table_path))
        return paths

    def _decode_image_data_url(self, image_data_url: str) -> tuple[str, bytes]:
        match = re.match(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$", image_data_url)
        if not match:
            raise ValueError("Expected a PNG, JPEG, or WebP data URL.")
        mime_type, raw_base64 = match.groups()
        try:
            data = base64.b64decode("".join(raw_base64.split()), validate=True)
        except Exception as exc:
            raise ValueError("Image data URL is not valid base64.") from exc
        return mime_type, data


def _find_emails(text: str) -> list[str]:
    return re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)


def _float_metric(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _find_phone_candidates(lines: list[str]) -> list[str]:
    phones: list[str] = []
    for line in lines:
        if sum(ch.isdigit() for ch in line) >= 7:
            phones.append(line)
    return phones


def _image_path_to_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    if mime_type not in IMAGE_MIME_EXTENSIONS:
        mime_type = "image/png"
    return f"data:{mime_type};base64,{base64.b64encode(image_path.read_bytes()).decode('ascii')}"


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _textin_image_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    image_list = result.get("image_list") if isinstance(result.get("image_list"), list) else []
    return [item for item in image_list if isinstance(item, dict)]


def _decode_textin_image(value: str) -> bytes | None:
    text = value.strip()
    if text.startswith("data:image") and "," in text:
        text = text.split(",", 1)[1]
    if not text:
        return None
    try:
        return base64.b64decode(text, validate=True)
    except Exception:
        return None


def _textin_boundary_from_result(textin_result: dict[str, object], processed_frame: Any) -> dict[str, object]:
    height, width = processed_frame.shape[:2]
    position = textin_result.get("position") if isinstance(textin_result.get("position"), list) else []
    points: list[list[float]] = []
    if len(position) >= 8:
        try:
            raw_points = [
                [float(position[0]), float(position[1])],
                [float(position[2]), float(position[3])],
                [float(position[4]), float(position[5])],
                [float(position[6]), float(position[7])],
            ]
            points = _order_textin_points(raw_points)
        except (TypeError, ValueError):
            points = []
    duration = textin_result.get("duration")
    confidence = 0.9 if points else 0.82
    preview_points = [
        [0.0, 0.0],
        [float(max(0, width - 1)), 0.0],
        [float(max(0, width - 1)), float(max(0, height - 1))],
        [0.0, float(max(0, height - 1))],
    ]
    return {
        "detected": True,
        "document_region_detected": True,
        "confidence": confidence,
        "method": "textin_crop_enhance",
        "backend": "textin_crop_enhance",
        "reason": "TextIn crop_enhance_image detected and enhanced the document.",
        "point_order": ["top_left", "top_right", "bottom_right", "bottom_left"],
        "points": [[round(x, 2), round(y, 2)] for x, y in preview_points],
        "raw_points": [[round(x, 2), round(y, 2)] for x, y in points],
        "source_points": [[round(x, 2), round(y, 2)] for x, y in points],
        "angle": textin_result.get("angle"),
        "cropped_size": {
            "width": textin_result.get("cropped_width"),
            "height": textin_result.get("cropped_height"),
        },
        "duration_ms": duration,
    }


def _order_textin_points(points: list[list[float]]) -> list[list[float]]:
    ordered = sorted(points, key=lambda item: (item[1], item[0]))
    top = sorted(ordered[:2], key=lambda item: item[0])
    bottom = sorted(ordered[2:], key=lambda item: item[0])
    return [top[0], top[1], bottom[1], bottom[0]]


def _heuristic_structure_from_text(text: str, document_type: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    emails = sorted(set(_find_emails(text)))
    phones = sorted(set(_find_phone_candidates(lines)))
    risks = sorted({marker for marker in RISK_MARKERS if marker.lower() in text.lower()})
    tables = _extract_table_like_blocks(lines)
    dates = re.findall(r"\b\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?\b", text)
    amounts = re.findall(r"(?:人民币|¥|￥|\$)?\s?\d[\d,]*(?:\.\d+)?(?:元|万元|美元)?", text)
    business_card = {
        "name": lines[0] if lines else "",
        "organization": lines[1] if len(lines) > 1 else "",
        "title": lines[2] if len(lines) > 2 else "",
        "emails": emails,
        "phones": phones,
    }
    contract = {
        "parties": lines[:2],
        "dates": dates[:10],
        "amounts": amounts[:10],
        "obligations": [line for line in lines if any(keyword in line.lower() for keyword in ["应", "shall", "must", "负责", "deliver"])][:10],
        "risk_terms": risks[:10],
    }
    summary = "；".join(lines[:3]) if lines else "未识别到清晰文本。"
    quality_notes = []
    if not lines:
        quality_notes.append("OCR 未提取到文本，图片可能过暗、过模糊或裁切不完整。")
    if len(text) < 40:
        quality_notes.append("文本较少，可能需要重新拍照或更近距离扫描。")
    structure = {
        "document_type": document_type,
        "summary": summary,
        "key_points": lines[:8],
        "tables": tables,
        "entities": {"emails": emails, "phones": phones, "dates": dates[:10], "amounts": amounts[:10]},
        "risks": risks,
        "business_card": business_card if document_type in {"business_card", "card"} or emails or phones else {},
        "contract": contract if document_type in {"contract", "agreement"} or risks or amounts else {},
        "quality_notes": quality_notes,
        "uncertain_items": [] if lines else ["图片中的正文内容无法清晰识别"],
    }
    return structure


def _extract_table_like_blocks(lines: list[str]) -> list[dict[str, object]]:
    table_lines = [line for line in lines if any(sep in line for sep in ["|", ",", "\t"])]
    if len(table_lines) < 2:
        return []
    delimiter = "|" if any("|" in line for line in table_lines) else ("\t" if any("\t" in line for line in table_lines) else ",")
    parsed_rows = [[cell.strip() for cell in line.strip("|").split(delimiter)] for line in table_lines[:12]]
    parsed_rows = [row for row in parsed_rows if len(row) > 1]
    if len(parsed_rows) < 2:
        return []
    headers = parsed_rows[0]
    rows = parsed_rows[1:]
    return [{"title": "detected_table_1", "headers": headers, "rows": rows}]


def _merge_structure(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if _empty_structure_value(value):
            continue
        if isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = value
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged_dict = dict(merged[key])
            for child_key, child_value in value.items():
                if not _empty_structure_value(child_value):
                    merged_dict[child_key] = child_value
            merged[key] = merged_dict
            continue
        merged[key] = value
    return merged


def _empty_structure_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _scan_summary_markdown(payload: dict[str, object]) -> str:
    lines = ["# 实体文档扫描结果", ""]
    summary = str(payload.get("summary") or "").strip()
    if summary:
        lines.extend(["## 摘要", summary, ""])
    key_points = payload.get("key_points") if isinstance(payload.get("key_points"), list) else []
    if key_points:
        lines.append("## 关键要点")
        lines.extend(f"- {item}" for item in key_points[:20])
        lines.append("")
    risks = payload.get("risks") if isinstance(payload.get("risks"), list) else []
    if risks:
        lines.append("## 风险/待确认")
        lines.extend(f"- {item}" for item in risks[:20])
        lines.append("")
    uncertain = payload.get("uncertain_items") if isinstance(payload.get("uncertain_items"), list) else []
    if uncertain:
        lines.append("## 看不清或无法判断")
        lines.extend(f"- {item}" for item in uncertain[:20])
        lines.append("")
    preview = str(payload.get("preview") or "").strip()
    if preview:
        lines.extend(["## OCR 文本预览", "", "```text", preview[:4000], "```"])
    return "\n".join(lines).strip() + "\n"


def _perspective_correct(frame: Any, cv2: Any, np: Any) -> tuple[Any, dict[str, object]]:
    original = frame.copy()
    height, width = frame.shape[:2]
    ratio = 1200.0 / max(height, width) if max(height, width) > 1200 else 1.0
    resized = cv2.resize(frame, (int(width * ratio), int(height * ratio))) if ratio != 1.0 else frame
    image_area = resized.shape[0] * resized.shape[1]
    best_quad = None
    best_score = 0.0
    best_area_ratio = 0.0
    best_rectangularity = 0.0
    best_method = ""

    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    lab_l = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB)[:, :, 0]
    variants = [
        ("gray", gray),
        ("lab_l", lab_l),
        ("clahe", cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)),
    ]
    for method, source in variants:
        for blur_size, canny_low, canny_high in (
            (3, 20, 80),
            (3, 35, 120),
            (5, 20, 80),
            (5, 45, 150),
            (7, 55, 180),
        ):
            blurred = cv2.GaussianBlur(source, (blur_size, blur_size), 0)
            edges = cv2.Canny(blurred, canny_low, canny_high)
            edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)
            edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)), iterations=1)
            contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:60]:
                area = cv2.contourArea(contour)
                if area < image_area * 0.035:
                    continue
                perimeter = cv2.arcLength(contour, True)
                for epsilon in (0.01, 0.018, 0.025, 0.035, 0.05, 0.075):
                    approx = cv2.approxPolyDP(contour, epsilon * perimeter, True)
                    if len(approx) != 4 or not cv2.isContourConvex(approx):
                        continue
                    quad = approx.reshape(4, 2).astype("float32")
                    rect = _order_points(quad, np)
                    rect_area = abs(float(cv2.contourArea(rect)))
                    if rect_area < image_area * 0.035:
                        continue
                    x, y, w_box, h_box = cv2.boundingRect(rect.astype("int32"))
                    touches = sum(
                        [
                            x <= 3,
                            y <= 3,
                            x + w_box >= resized.shape[1] - 3,
                            y + h_box >= resized.shape[0] - 3,
                        ]
                    )
                    if touches > 1:
                        continue
                    aspect = max(w_box / max(1, h_box), h_box / max(1, w_box))
                    if aspect > 2.85:
                        continue
                    rectangularity = min(1.0, float(area) / max(1.0, rect_area))
                    area_ratio = rect_area / max(1.0, float(image_area))
                    if area_ratio > 0.90:
                        continue
                    aspect_score = 1.0 - min(abs(aspect - 1.38), 1.38) / 1.38
                    if area_ratio <= 0.64:
                        area_preference = 1.0 - min(abs(area_ratio - 0.32), 0.32) / 0.32
                    else:
                        area_preference = max(0.0, 1.0 - (area_ratio - 0.64) / 0.26)
                    score = (
                        area_preference * 0.34
                        + rectangularity * 0.32
                        + aspect_score * 0.16
                        + min(area_ratio, 0.55) * 0.18
                        - touches * 0.12
                    )
                    if score > best_score:
                        best_quad = quad
                        best_score = score
                        best_area_ratio = area_ratio
                        best_rectangularity = rectangularity
                        best_method = f"{method}:blur{blur_size}:canny{canny_low}-{canny_high}"
    if best_quad is None:
        fallback_region = _fallback_document_region(original, resized, ratio, cv2, np)
        if fallback_region is not None:
            return fallback_region
        fallback = _center_document_crop(original)
        x = int(fallback["x"])
        y = int(fallback["y"])
        crop_width = int(fallback["width"])
        crop_height = int(fallback["height"])
        cropped = original[y : y + crop_height, x : x + crop_width]
        return cropped, {
            "detected": False,
            "confidence": 0.35,
            "reason": "No large quadrilateral document boundary was detected.",
            "fallback_crop": fallback,
        }

    quad = best_quad / ratio
    ordered_quad = _order_points(quad, np)
    warped = _four_point_transform(original, ordered_quad, cv2, np)
    confidence = min(1.0, max(0.45, best_score))
    return warped, {
        "detected": True,
        "confidence": round(confidence, 4),
        "area_ratio": round(float(best_area_ratio), 4),
        "rectangularity": round(float(best_rectangularity), 4),
        "method": best_method,
        "point_order": ["top_left", "top_right", "bottom_right", "bottom_left"],
        "points": [[round(float(x), 2), round(float(y), 2)] for x, y in ordered_quad.tolist()],
        "raw_points": [[round(float(x), 2), round(float(y), 2)] for x, y in quad.tolist()],
    }


def _fallback_document_region(original: Any, resized: Any, ratio: float, cv2: Any, np: Any) -> tuple[Any, dict[str, object]] | None:
    image_height, image_width = resized.shape[:2]
    image_area = float(image_height * image_width)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    l_channel = lab[:, :, 0]
    saturation = hsv[:, :, 1]
    best: dict[str, object] | None = None

    for l_min, s_max in ((170, 80), (180, 100), (190, 100), (160, 70)):
        mask = ((l_channel >= l_min) & (saturation <= s_max)).astype("uint8") * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (17, 17)), iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)), iterations=1)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
            area = float(cv2.contourArea(contour))
            area_ratio = area / max(1.0, image_area)
            if not 0.055 <= area_ratio <= 0.68:
                continue
            x, y, w_box, h_box = cv2.boundingRect(contour)
            touches = sum(
                [
                    x <= 3,
                    y <= 3,
                    x + w_box >= image_width - 3,
                    y + h_box >= image_height - 3,
                ]
            )
            if touches > 1:
                continue
            bbox_ratio = (w_box * h_box) / max(1.0, image_area)
            if bbox_ratio > 0.82:
                continue
            perimeter = cv2.arcLength(contour, True)
            quad = None
            for epsilon in (0.015, 0.02, 0.025, 0.035, 0.05, 0.075, 0.1):
                approx = cv2.approxPolyDP(contour, epsilon * perimeter, True)
                if len(approx) == 4 and cv2.isContourConvex(approx):
                    quad = approx.reshape(4, 2).astype("float32")
                    break
            if quad is None:
                continue
            ordered = _order_points(quad, np)
            quad_area = abs(float(cv2.contourArea(ordered)))
            quad_area_ratio = quad_area / max(1.0, image_area)
            if not 0.055 <= quad_area_ratio <= 0.72:
                continue
            width_a = np.linalg.norm(ordered[2] - ordered[3])
            width_b = np.linalg.norm(ordered[1] - ordered[0])
            height_a = np.linalg.norm(ordered[1] - ordered[2])
            height_b = np.linalg.norm(ordered[0] - ordered[3])
            max_width = max(width_a, width_b, 1.0)
            max_height = max(height_a, height_b, 1.0)
            aspect = max(max_width / max_height, max_height / max_width)
            if aspect > 2.65:
                continue
            original_quad = ordered / max(ratio, 1e-6)
            warped = _four_point_transform(original, original_quad, cv2, np)
            text_metrics = _document_text_metrics(warped, cv2)
            components = int(text_metrics.get("components") or 0)
            text_area_ratio = float(text_metrics.get("text_area_ratio") or 0.0)
            edge_density = float(text_metrics.get("edge_density") or 0.0)
            if components < 450 or text_area_ratio < 0.045 or edge_density < 0.08:
                continue
            area_score = 1.0 - min(abs(quad_area_ratio - 0.36), 0.36) / 0.36
            text_score = min(1.0, components / 1800.0) * 0.34
            text_score += min(1.0, text_area_ratio / 0.075) * 0.36
            text_score += min(1.0, edge_density / 0.13) * 0.20
            text_score += min(1.0, float(text_metrics.get("text_rows") or 0.0) / 0.75) * 0.10
            touch_penalty = touches * 0.08
            score = area_score * 0.22 + text_score * 0.66 + min(quad_area_ratio, 0.55) * 0.12 - touch_penalty
            if score < 0.62:
                continue
            if best is None or score > float(best["score"]):
                best = {
                    "score": score,
                    "warped": warped,
                    "quad": original_quad,
                    "area_ratio": quad_area_ratio,
                    "method": f"paper_mask:l{l_min}:s{s_max}",
                    "text_metrics": text_metrics,
                }

    if best is None:
        return None

    quad = _order_points(best["quad"], np)
    x, y, w_box, h_box = cv2.boundingRect(quad.astype("int32"))
    fallback_crop = {
        "x": max(0, int(x)),
        "y": max(0, int(y)),
        "width": int(w_box),
        "height": int(h_box),
        "points": [[round(float(px), 2), round(float(py), 2)] for px, py in quad.tolist()],
    }
    confidence = min(0.78, max(0.52, float(best["score"])))
    return best["warped"], {
        "detected": False,
        "document_region_detected": True,
        "confidence": round(confidence, 4),
        "reason": "No stable four-corner boundary was detected; used a paper-region fallback crop.",
        "method": best["method"],
        "area_ratio": round(float(best["area_ratio"]), 4),
        "point_order": ["top_left", "top_right", "bottom_right", "bottom_left"],
        "points": fallback_crop["points"],
        "fallback_crop": fallback_crop,
        "text_metrics": best["text_metrics"],
    }


def _document_text_metrics(frame: Any, cv2: Any) -> dict[str, object]:
    height, width = frame.shape[:2]
    scale = 900.0 / max(height, width) if max(height, width) > 900 else 1.0
    resized = cv2.resize(frame, (int(width * scale), int(height * scale))) if scale != 1.0 else frame
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    roi_height, roi_width = gray.shape[:2]
    margin = max(4, int(min(roi_height, roi_width) * 0.04))
    roi = gray[margin : max(margin + 1, roi_height - margin), margin : max(margin + 1, roi_width - margin)]
    if roi.size == 0:
        return {"components": 0, "text_area_ratio": 0.0, "edge_density": 0.0, "dark_density": 0.0, "text_rows": 0.0}

    kernel_size = max(31, (min(roi.shape[:2]) // 10) | 1)
    background = cv2.medianBlur(roi, kernel_size)
    normalized = cv2.divide(roi, background, scale=255)
    dark_mask = (((normalized < 210) & (roi < 210)).astype("uint8")) * 255
    dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)), iterations=1)
    component_count, _, stats, _ = cv2.connectedComponentsWithStats(dark_mask, 8)
    components = 0
    text_area = 0
    max_component_area = max(600, int(roi.size * 0.02))
    for index in range(1, component_count):
        _, _, component_width, component_height, area = stats[index]
        if area < 4 or area > max_component_area:
            continue
        if component_height < 2 or component_width < 1:
            continue
        aspect = max(component_width / max(1, component_height), component_height / max(1, component_width))
        if aspect > 35:
            continue
        components += 1
        text_area += int(area)

    edges = cv2.Canny(roi, 80, 180)
    row_density = (dark_mask > 0).mean(axis=1)
    text_rows = float((row_density > 0.01).sum() / max(1, len(row_density)))
    return {
        "components": components,
        "text_area_ratio": round(float(text_area / max(1, roi.size)), 4),
        "edge_density": round(float((edges > 0).mean()), 4),
        "dark_density": round(float((dark_mask > 0).mean()), 4),
        "text_rows": round(text_rows, 4),
    }


def _center_document_crop(image: Any) -> dict[str, object]:
    height, width = image.shape[:2]
    margin_x = int(width * 0.035)
    margin_y = int(height * 0.035)
    crop_width = max(1, width - margin_x * 2)
    crop_height = max(1, height - margin_y * 2)
    return {
        "x": margin_x,
        "y": margin_y,
        "width": crop_width,
        "height": crop_height,
        "points": [
            [margin_x, margin_y],
            [margin_x + crop_width, margin_y],
            [margin_x + crop_width, margin_y + crop_height],
            [margin_x, margin_y + crop_height],
        ],
    }


def _draw_corner_preview(frame: Any, boundary: dict[str, object], cv2: Any, np: Any) -> Any:
    preview = frame.copy()
    detected = bool(boundary.get("detected"))
    points_value = boundary.get("points")
    if not isinstance(points_value, list) or len(points_value) != 4:
        fallback = boundary.get("fallback_crop") if isinstance(boundary.get("fallback_crop"), dict) else {}
        points_value = fallback.get("points") if isinstance(fallback.get("points"), list) else []
    if not isinstance(points_value, list) or len(points_value) != 4:
        return preview

    points = np.array(points_value, dtype="float32").reshape(4, 2)
    points_i = points.astype("int32")
    height, width = preview.shape[:2]
    line_width = max(2, int(round(min(height, width) / 220)))
    radius = max(5, int(round(min(height, width) / 110)))
    color = (32, 210, 100) if detected else (0, 180, 255)
    fill = (255, 255, 255)
    labels = ["TL", "TR", "BR", "BL"]

    overlay = preview.copy()
    cv2.polylines(overlay, [points_i], isClosed=True, color=color, thickness=line_width * 2, lineType=cv2.LINE_AA)
    preview = cv2.addWeighted(overlay, 0.72, preview, 0.28, 0)
    for index, (x_value, y_value) in enumerate(points_i.tolist()):
        x = int(x_value)
        y = int(y_value)
        cv2.circle(preview, (x, y), radius, color, -1, lineType=cv2.LINE_AA)
        cv2.circle(preview, (x, y), radius + line_width, fill, line_width, lineType=cv2.LINE_AA)
        label = labels[index]
        label_x = min(max(4, x + radius + 6), max(4, width - 48))
        label_y = min(max(20, y - radius - 6), max(20, height - 8))
        cv2.putText(
            preview,
            label,
            (label_x, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            max(0.55, min(height, width) / 1100.0),
            color,
            max(1, line_width),
            cv2.LINE_AA,
        )

    status_text = "auto corners" if detected else "fallback crop"
    confidence = boundary.get("confidence")
    if isinstance(confidence, (float, int)):
        status_text = f"{status_text} {float(confidence):.2f}"
    cv2.rectangle(preview, (8, 8), (min(width - 8, 260), 42), (0, 0, 0), -1)
    cv2.putText(
        preview,
        status_text,
        (18, 32),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        color,
        2,
        cv2.LINE_AA,
    )
    return preview


def _four_point_transform(image: Any, points: Any, cv2: Any, np: Any) -> Any:
    rect = _order_points(points, np)
    tl, tr, br, bl = rect
    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_width = max(1, int(max(width_a, width_b)))
    max_height = max(1, int(max(height_a, height_b)))
    destination = np.array(
        [[0, 0], [max_width - 1, 0], [max_width - 1, max_height - 1], [0, max_height - 1]],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(rect, destination)
    return cv2.warpPerspective(image, matrix, (max_width, max_height))


def _order_points(points: Any, np: Any) -> Any:
    rect = np.zeros((4, 2), dtype="float32")
    sums = points.sum(axis=1)
    rect[0] = points[np.argmin(sums)]
    rect[2] = points[np.argmax(sums)]
    diffs = np.diff(points, axis=1)
    rect[1] = points[np.argmin(diffs)]
    rect[3] = points[np.argmax(diffs)]
    return rect


def _enhance_color_scan(frame: Any, cv2: Any, *, trim_border: bool = True) -> Any:
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    kernel_size = max(51, (min(frame.shape[:2]) // 12) | 1)
    background = cv2.medianBlur(l_channel, kernel_size)
    normalized_l = cv2.divide(l_channel, background, scale=230)
    normalized_l = cv2.normalize(normalized_l, None, 42, 248, cv2.NORM_MINMAX)
    clahe = cv2.createCLAHE(clipLimit=0.9, tileGridSize=(8, 8))
    enhanced_l = clahe.apply(normalized_l)
    neutral = 128
    neutral_a = a_channel.copy()
    neutral_b = b_channel.copy()
    neutral_a[:] = neutral
    neutral_b[:] = neutral
    a_soft = cv2.addWeighted(a_channel, 0.72, neutral_a, 0.28, 0)
    b_soft = cv2.addWeighted(b_channel, 0.72, neutral_b, 0.28, 0)
    balanced = cv2.merge((enhanced_l, a_soft, b_soft))
    color = cv2.cvtColor(balanced, cv2.COLOR_LAB2BGR)
    color = _gray_world_white_balance(color, cv2)
    blurred = cv2.GaussianBlur(color, (0, 0), 1.0)
    sharpened = cv2.addWeighted(color, 1.08, blurred, -0.08, 0)
    sharpened = _lift_paper_background(sharpened, cv2)
    return _trim_border(sharpened, cv2) if trim_border else sharpened


def _lift_paper_background(frame: Any, cv2: Any) -> Any:
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    paper_mask = cv2.inRange(l_channel, 184, 255)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    paper_mask = cv2.morphologyEx(paper_mask, cv2.MORPH_OPEN, kernel, iterations=1)
    lifted_l = l_channel.copy()
    paper_pixels = lifted_l[paper_mask > 0]
    lifted_l[paper_mask > 0] = (paper_pixels.astype("float32") * 0.88 + 255.0 * 0.12).clip(0, 255).astype("uint8")
    cleaned = cv2.merge((lifted_l, a_channel, b_channel))
    return cv2.cvtColor(cleaned, cv2.COLOR_LAB2BGR)


def _gray_world_white_balance(frame: Any, cv2: Any) -> Any:
    channels = cv2.split(frame.astype("float32"))
    means = [max(1.0, float(channel.mean())) for channel in channels]
    target = sum(means) / len(means)
    balanced = [channel * (target / mean) for channel, mean in zip(channels, means)]
    return cv2.merge(balanced).clip(0, 255).astype("uint8")


def _trim_border(frame: Any, cv2: Any) -> Any:
    height, width = frame.shape[:2]
    trim_x = int(width * 0.012)
    trim_y = int(height * 0.012)
    if trim_x <= 0 or trim_y <= 0 or width - 2 * trim_x < 120 or height - 2 * trim_y < 120:
        return frame
    return frame[trim_y : height - trim_y, trim_x : width - trim_x]


def _enhance_for_ocr(frame: Any, cv2: Any) -> Any:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    kernel_size = max(51, (min(gray.shape[:2]) // 12) | 1)
    background = cv2.medianBlur(gray, kernel_size)
    shadow_removed = cv2.divide(gray, background, scale=238)
    shadow_removed = cv2.normalize(shadow_removed, None, 35, 248, cv2.NORM_MINMAX)
    denoised = cv2.fastNlMeansDenoising(shadow_removed, None, 5, 7, 21)
    clahe = cv2.createCLAHE(clipLimit=1.0, tileGridSize=(8, 8))
    contrast = clahe.apply(denoised)
    blurred = cv2.GaussianBlur(denoised, (0, 0), 1.0)
    sharpened = cv2.addWeighted(contrast, 1.18, blurred, -0.18, 0)
    return cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)


def _image_metrics(original: Any, corrected: Any, boundary: dict[str, object], cv2: Any) -> dict[str, object]:
    gray = cv2.cvtColor(corrected, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 80, 180)
    return {
        "brightness": round(float(gray.mean()), 3),
        "contrast": round(float(gray.std()), 3),
        "sharpness_laplacian": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 3),
        "edge_density": round(float((edges > 0).mean()), 5),
        "perspective_corrected": bool(boundary.get("detected")),
        "document_region_detected": bool(boundary.get("detected") or boundary.get("document_region_detected")),
        "original_pixels": int(original.shape[0] * original.shape[1]),
        "corrected_pixels": int(corrected.shape[0] * corrected.shape[1]),
    }


def _select_ocr_input(enhancement: dict[str, object], original_workspace_name: str) -> dict[str, object]:
    boundary_detected = bool(enhancement.get("boundary_detected"))
    document_region_detected = bool(boundary_detected or enhancement.get("document_region_detected"))
    if document_region_detected:
        workspace_name = str(
            enhancement.get("color_workspace_name")
            or enhancement.get("ocr_workspace_name")
            or enhancement.get("enhanced_workspace_name")
            or original_workspace_name
        )
        return {
            "workspace_name": workspace_name,
            "mode": "enhanced_document" if boundary_detected else "fallback_document_region",
            "reason": "document_boundary_detected" if boundary_detected else "paper_region_fallback_detected",
        }
    return {
        "workspace_name": original_workspace_name,
        "mode": "original_image",
        "reason": "document_boundary_not_detected",
    }
