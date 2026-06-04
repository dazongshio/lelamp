from __future__ import annotations

import base64
import csv
import io
import json
import mimetypes
import re
import shutil
import subprocess
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
        ocr_input = str(enhancement.get("enhanced_workspace_name") or image_filename)
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
        checks = {
            "boundary_detected": boundary_detected,
            "boundary_confidence": _float_metric(boundary.get("confidence")),
            "brightness_ok": 60 <= brightness <= 215,
            "contrast_ok": contrast >= 18,
            "sharpness_ok": sharpness >= 45,
            "edge_density_ok": 0.01 <= edge_density <= 0.32,
        }
        recommendations: list[str] = []
        if not checks["boundary_detected"]:
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
        ready = boundary_detected and checks["brightness_ok"] and checks["contrast_ok"] and checks["sharpness_ok"]
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
        corrected, boundary = _perspective_correct(frame, cv2, np)
        enhanced = _enhance_for_ocr(corrected, cv2)
        enhanced_path = self.workspace.path_for_new_file(safe_filename(Path(image_filename).stem, suffix="_enhanced.png"))
        if not cv2.imwrite(str(enhanced_path), enhanced):
            payload = {"status": "error", "image": image_filename, "reason": "OpenCV failed to write enhanced image."}
            self.audit.record("scan.enhance", status="error", target=image_filename, details=payload)
            return payload

        enhanced_workspace_name = str(enhanced_path.relative_to(self.workspace.root))
        payload = {
            "status": "completed",
            "image": image_filename,
            "enhanced_image_path": str(enhanced_path),
            "enhanced_workspace_name": enhanced_workspace_name,
            "original_size": {"width": original_width, "height": original_height},
            "enhanced_size": {"width": int(enhanced.shape[1]), "height": int(enhanced.shape[0])},
            "boundary_detected": bool(boundary.get("detected")),
            "boundary": boundary,
            "metrics": _image_metrics(frame, corrected, boundary, cv2),
            "pipeline": ["edge_detection", "perspective_correction", "shadow_removal", "contrast_boost", "sharpening"],
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
            details={"enhanced": enhanced_workspace_name, "boundary_detected": payload["boundary_detected"]},
        )
        return payload

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
    ratio = 900.0 / max(height, width) if max(height, width) > 900 else 1.0
    resized = cv2.resize(frame, (int(width * ratio), int(height * ratio))) if ratio != 1.0 else frame
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 60, 180)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_area = resized.shape[0] * resized.shape[1]
    best_quad = None
    best_area = 0.0
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
        area = cv2.contourArea(contour)
        if area < image_area * 0.08:
            continue
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
        if len(approx) == 4 and area > best_area:
            best_quad = approx.reshape(4, 2).astype("float32")
            best_area = float(area)
    if best_quad is None:
        return original, {
            "detected": False,
            "confidence": 0.0,
            "reason": "No large quadrilateral document boundary was detected.",
        }

    quad = best_quad / ratio
    warped = _four_point_transform(original, quad, cv2, np)
    confidence = min(1.0, best_area / max(1.0, float(image_area)))
    return warped, {
        "detected": True,
        "confidence": round(confidence, 4),
        "points": [[round(float(x), 2), round(float(y), 2)] for x, y in quad.tolist()],
    }


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


def _enhance_for_ocr(frame: Any, cv2: Any) -> Any:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    kernel_size = max(15, (min(gray.shape[:2]) // 24) | 1)
    background = cv2.medianBlur(gray, kernel_size)
    shadow_removed = cv2.divide(gray, background, scale=255)
    denoised = cv2.fastNlMeansDenoising(shadow_removed, None, 8, 7, 21)
    blurred = cv2.GaussianBlur(denoised, (0, 0), 1.0)
    sharpened = cv2.addWeighted(denoised, 1.45, blurred, -0.45, 0)
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
        "original_pixels": int(original.shape[0] * original.shape[1]),
        "corrected_pixels": int(corrected.shape[0] * corrected.shape[1]),
    }
