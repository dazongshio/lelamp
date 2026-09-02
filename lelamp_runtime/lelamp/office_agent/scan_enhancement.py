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



class ScanEnhancementMixin:
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
