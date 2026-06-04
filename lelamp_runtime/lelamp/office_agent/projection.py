from __future__ import annotations

import base64
import json
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree
from typing import Any

from .audit import AuditLogger
from .utils import dedupe_path, safe_filename


def redact_projection_text(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    patterns = [
        (r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]"),
        (r"\bsk-[A-Za-z0-9][A-Za-z0-9._-]{8,}\b", "sk-[redacted]"),
        (r"(?i)(api[_-]?key|access[_-]?token|token|signature|password|secret)(\s*[:=]\s*)([^\s,;]+)", r"\1\2[redacted]"),
        (r"(?i)(authorization)(\s*[:=]\s*)([^\n\r]+)", r"\1\2[redacted]"),
        (r"://[^/\s:@]+:[^/\s:@]+@", "://[redacted]@"),
    ]
    redacted = text
    for pattern, replacement in patterns:
        redacted = re.sub(pattern, replacement, redacted)
    return redacted


def _text_from_pptx_xml(data: bytes) -> str:
    root = ElementTree.fromstring(data)
    parts: list[str] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "t" and node.text:
            text = node.text.strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


class ProjectionService:
    def __init__(self, output_dir: Path, audit: AuditLogger):
        self.output_dir = output_dir
        self.audit = audit
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render_markdown(self, title: str, body: str, mode: str = "meeting") -> dict[str, str]:
        safe_title = redact_projection_text(title)
        path = dedupe_path(self.output_dir / safe_filename(safe_title, default="projection", suffix=".md"))
        content = "\n".join(
            [
                f"# {safe_title}",
                "",
                f"Mode: {mode}",
                "",
                redact_projection_text(body),
                "",
            ]
        )
        path.write_text(content, encoding="utf-8")
        self.audit.record(
            "projection.render_markdown",
            target=str(path),
            details={"title": safe_title, "mode": mode, "chars": len(body)},
        )
        return {"path": str(path), "mode": mode}

    def render_confirmation(self, title: str, decisions: list[str], action_items: list[str]) -> dict[str, str]:
        lines = ["## Decisions"]
        lines.extend(f"- {item}" for item in decisions)
        lines.extend(["", "## Action Items"])
        lines.extend(f"- {item}" for item in action_items)
        return self.render_markdown(title, "\n".join(lines), mode="confirmation")

    def render_status_card(
        self,
        title: str,
        status: str,
        details: list[str] | None = None,
        *,
        accent: str = "blue",
    ) -> dict[str, str]:
        lines = [
            f"Status: {status}",
            f"Accent: {accent}",
            "",
            "## Details",
            *([f"- {item}" for item in details or []] or ["- No details."]),
        ]
        return self.render_markdown(title, "\n".join(lines), mode="status_card")

    def render_countdown(
        self,
        title: str,
        seconds: int,
        *,
        message: str = "",
    ) -> dict[str, str]:
        seconds = max(0, seconds)
        ends_at = datetime.now().astimezone() + timedelta(seconds=seconds)
        minutes, remainder = divmod(seconds, 60)
        lines = [
            f"Countdown: {minutes:02d}:{remainder:02d}",
            f"Ends at: {ends_at.strftime('%H:%M:%S')}",
            "",
            message or "请在倒计时结束前完成当前步骤。",
        ]
        return self.render_markdown(title, "\n".join(lines), mode="countdown")

    def render_action_card(
        self,
        title: str,
        actions: list[str],
        *,
        decisions: list[str] | None = None,
    ) -> dict[str, str]:
        lines = [
            "## Decisions",
            *([f"- {item}" for item in decisions or []] or ["- None"]),
            "",
            "## Action Items",
            *([f"- [ ] {item}" for item in actions] or ["- [ ] No action items."]),
        ]
        return self.render_markdown(title, "\n".join(lines), mode="action_card")

    def extract_pptx_slides(self, path: Path, *, max_slides: int = 80, max_chars_per_slide: int = 6000) -> list[dict[str, object]]:
        slides: list[dict[str, object]] = []
        with zipfile.ZipFile(path) as archive:
            names = sorted(
                (name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")),
                key=lambda value: int(re.search(r"slide(\d+)\.xml$", value).group(1)) if re.search(r"slide(\d+)\.xml$", value) else 0,
            )
            for index, name in enumerate(names[: max(1, max_slides)], start=1):
                text = _text_from_pptx_xml(archive.read(name)).strip()
                truncated = len(text) > max_chars_per_slide
                if truncated:
                    text = text[:max_chars_per_slide].rstrip() + "\n[TRUNCATED]"
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                title = lines[0] if lines else f"Slide {index}"
                body = "\n".join(lines[1:] if len(lines) > 1 else lines)
                slides.append(
                    {
                        "index": index,
                        "title": redact_projection_text(title)[:160],
                        "text": redact_projection_text(body),
                        "chars": len(text),
                        "truncated": truncated,
                    }
                )
        return slides

    def render_pptx_slide(
        self,
        deck_title: str,
        slide: dict[str, object],
        *,
        slide_count: int,
        source_name: str,
    ) -> dict[str, str]:
        slide_index = int(slide.get("index") or 1)
        slide_title = str(slide.get("title") or f"Slide {slide_index}")
        body = str(slide.get("text") or "").strip()
        lines = [
            f"Deck: {redact_projection_text(deck_title)}",
            f"Source: {redact_projection_text(source_name)}",
            f"Slide: {slide_index}/{slide_count}",
            "",
            f"## {redact_projection_text(slide_title)}",
            "",
        ]
        if body:
            lines.extend(f"- {line}" for line in body.splitlines() if line.strip())
        else:
            lines.append("- 此页没有可提取文本；如需展示视觉版式，请使用“捕获并总结当前页”。")
        path = dedupe_path(self.output_dir / safe_filename(f"{deck_title}_slide_{slide_index:03d}", default="ppt_slide", suffix=".md"))
        content = "\n".join(
            [
                f"# {redact_projection_text(deck_title)}",
                "",
                "Mode: ppt_slide",
                "",
                *lines,
                "",
            ]
        )
        path.write_text(content, encoding="utf-8")
        self.audit.record(
            "projection.render_pptx_slide",
            target=str(path),
            details={"title": redact_projection_text(deck_title), "slide_index": slide_index, "slide_count": slide_count, "source": redact_projection_text(source_name)},
        )
        return {"path": str(path), "mode": "ppt_slide"}

    def create_projection_calibration_plan(self, surface: str, ambient_light: str) -> dict[str, object]:
        plan = {
            "surface": surface,
            "ambient_light": ambient_light,
            "steps": [
                "detect projection rectangle corners",
                "estimate keystone transform",
                "adjust focus motor or request manual focus",
                "measure ambient brightness",
                "select brightness and contrast profile",
            ],
            "recommended_backend": "OpenCV + projector SDK + optional ToF sensor",
        }
        path = dedupe_path(self.output_dir / "projection_calibration_plan.json")
        path.write_text(__import__("json").dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        self.audit.record("projection.calibration_plan", target=str(path), details=plan)
        return {**plan, "path": str(path)}

    def render_calibration_pattern(self, title: str = "投影校准测试图") -> dict[str, object]:
        path = dedupe_path(self.output_dir / safe_filename(title, default="projection_calibration", suffix=".md"))
        content = "\n".join(
            [
                f"# {redact_projection_text(title)}",
                "",
                "Mode: calibration",
                "",
                "Target: external_monitor",
                "Pattern: border_grid_focus_brightness",
                "",
                "## Instructions",
                "- 将此页面全屏显示在外接显示器或投影面。",
                "- 用摄像头拍摄完整画面，四个角和边框都要入镜。",
                "- 上传照片后系统会估算梯形、清晰度、亮度和遮挡风险。",
                "",
                "## Markers",
                "- corner_border",
                "- center_focus_text",
                "- brightness_grayscale",
                "- grid_alignment",
            ]
        )
        path.write_text(content, encoding="utf-8")
        result = {
            "status": "completed",
            "path": str(path),
            "mode": "calibration",
            "target": "external_monitor",
            "pattern": "border_grid_focus_brightness",
        }
        self.audit.record("projection.calibration_pattern", target=str(path), details=result)
        return result

    def analyze_calibration_capture(
        self,
        image_data_url: str,
        *,
        title: str = "projection_calibration",
        max_bytes: int = 20 * 1024 * 1024,
    ) -> dict[str, object]:
        mime_type, data = _decode_image_data_url(image_data_url)
        if len(data) > max_bytes:
            payload = {"status": "blocked", "reason": f"Image exceeds limit of {max_bytes} bytes.", "bytes": len(data)}
            self.audit.record("projection.calibration_capture", status="blocked", target=title, details=payload)
            return payload
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime_type]
        image_path = dedupe_path(self.output_dir / safe_filename(title, default="projection_calibration", suffix=f"_capture{extension}"))
        image_path.write_bytes(data)
        analysis = self._analyze_calibration_image(image_path)
        analysis_path = dedupe_path(self.output_dir / safe_filename(title, default="projection_calibration", suffix="_analysis.json"))
        analysis_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        report_path = dedupe_path(self.output_dir / safe_filename(title, default="projection_calibration", suffix="_report.md"))
        report_path.write_text(_calibration_report_markdown(analysis), encoding="utf-8")
        result = {
            "status": analysis["status"],
            "capture_path": str(image_path),
            "analysis_path": str(analysis_path),
            "report_path": str(report_path),
            "mime_type": mime_type,
            **analysis,
        }
        self.audit.record(
            "projection.calibration_analyze",
            status="ok" if analysis["status"] == "completed" else "warning",
            target=str(image_path),
            details={"analysis_path": str(analysis_path), "status": analysis["status"]},
        )
        return result

    def _analyze_calibration_image(self, image_path: Path) -> dict[str, object]:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception as exc:
            return {
                "status": "backend_missing",
                "message": "opencv-python is required for projection calibration image analysis.",
                "error": str(exc)[:500],
                "recommendations": ["安装 opencv-python-headless 后重新上传校准照片。"],
            }
        frame = cv2.imread(str(image_path))
        if frame is None:
            return {
                "status": "error",
                "message": "OpenCV could not read calibration capture.",
                "recommendations": ["请重新拍摄 PNG/JPEG/WebP 校准照片。"],
            }
        return _analyze_projection_frame(frame, cv2, np)


def _decode_image_data_url(image_data_url: str) -> tuple[str, bytes]:
    match = re.match(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$", image_data_url)
    if not match:
        raise ValueError("Expected a PNG, JPEG, or WebP data URL.")
    mime_type, raw_base64 = match.groups()
    try:
        data = base64.b64decode("".join(raw_base64.split()), validate=True)
    except Exception as exc:
        raise ValueError("Image data URL is not valid base64.") from exc
    return mime_type, data


def _analyze_projection_frame(frame: Any, cv2: Any, np: Any) -> dict[str, object]:
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    contrast = float(gray.std())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    edges = cv2.Canny(gray, 50, 160)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_area = height * width
    best_quad = None
    best_area = 0.0
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:16]:
        area = cv2.contourArea(contour)
        if area < image_area * 0.12:
            continue
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
        if len(approx) == 4 and area > best_area:
            best_quad = approx.reshape(4, 2).astype("float32")
            best_area = float(area)

    rectangle: dict[str, object]
    keystone: dict[str, object]
    if best_quad is None:
        rectangle = {"detected": False, "confidence": 0.0}
        keystone = {
            "status": "needs_manual_check",
            "horizontal_skew_pct": None,
            "vertical_skew_pct": None,
            "area_coverage_pct": None,
        }
    else:
        ordered = _order_points(best_quad, np)
        tl, tr, br, bl = ordered
        top = float(np.linalg.norm(tr - tl))
        bottom = float(np.linalg.norm(br - bl))
        left = float(np.linalg.norm(bl - tl))
        right = float(np.linalg.norm(br - tr))
        horizontal_skew = abs(top - bottom) / max(top, bottom, 1.0)
        vertical_skew = abs(left - right) / max(left, right, 1.0)
        coverage = best_area / max(float(image_area), 1.0)
        rectangle = {
            "detected": True,
            "confidence": round(min(1.0, coverage * 1.4), 4),
            "points": [[round(float(x), 2), round(float(y), 2)] for x, y in ordered.tolist()],
            "area_coverage_pct": round(coverage * 100, 2),
        }
        keystone = {
            "status": "ok" if horizontal_skew < 0.05 and vertical_skew < 0.05 else "needs_adjustment",
            "horizontal_skew_pct": round(horizontal_skew * 100, 2),
            "vertical_skew_pct": round(vertical_skew * 100, 2),
            "area_coverage_pct": round(coverage * 100, 2),
        }

    brightness_status = "ok"
    if brightness < 85:
        brightness_status = "too_dark"
    elif brightness > 210:
        brightness_status = "too_bright"
    focus_status = "ok" if sharpness >= 120 else "needs_focus"
    contrast_status = "ok" if contrast >= 38 else "low_contrast"
    obstruction_status = "possible_obstruction" if float((edges > 0).mean()) < 0.015 else "ok"
    recommendations = _calibration_recommendations(
        brightness_status=brightness_status,
        focus_status=focus_status,
        contrast_status=contrast_status,
        obstruction_status=obstruction_status,
        keystone=keystone,
        rectangle=rectangle,
    )
    status = "completed" if all(
        item == "ok"
        for item in [brightness_status, focus_status, contrast_status, obstruction_status, str(keystone.get("status"))]
    ) else "needs_adjustment"
    return {
        "status": status,
        "image_size": {"width": width, "height": height},
        "rectangle": rectangle,
        "keystone": keystone,
        "brightness": {"value": round(brightness, 3), "status": brightness_status},
        "contrast": {"value": round(contrast, 3), "status": contrast_status},
        "focus": {"laplacian_variance": round(sharpness, 3), "status": focus_status},
        "obstruction": {"edge_density": round(float((edges > 0).mean()), 5), "status": obstruction_status},
        "recommendations": recommendations,
        "hardware_control": "display_substitute_manual_adjustment",
    }


def _order_points(points: Any, np: Any) -> Any:
    rect = np.zeros((4, 2), dtype="float32")
    sums = points.sum(axis=1)
    rect[0] = points[np.argmin(sums)]
    rect[2] = points[np.argmax(sums)]
    diffs = np.diff(points, axis=1)
    rect[1] = points[np.argmin(diffs)]
    rect[3] = points[np.argmax(diffs)]
    return rect


def _calibration_recommendations(
    *,
    brightness_status: str,
    focus_status: str,
    contrast_status: str,
    obstruction_status: str,
    keystone: dict[str, object],
    rectangle: dict[str, object],
) -> list[str]:
    recommendations: list[str] = []
    if not rectangle.get("detected"):
        recommendations.append("未检测到完整投影/显示矩形；请让四个角和边框完整入镜后重新拍摄。")
    if keystone.get("status") == "needs_adjustment":
        recommendations.append("检测到梯形偏差；请调整显示器/投影面角度，或在真实投影硬件接入后启用梯形校正。")
    if brightness_status == "too_dark":
        recommendations.append("画面偏暗；请提高显示器亮度、降低环境光干扰，或在真实投影硬件上提高亮度档位。")
    if brightness_status == "too_bright":
        recommendations.append("画面过曝；请降低显示器亮度或避免摄像头直对强反光区域。")
    if contrast_status == "low_contrast":
        recommendations.append("对比度偏低；请提高显示器对比度或减少幕面反光。")
    if focus_status == "needs_focus":
        recommendations.append("清晰度偏低；请重新对焦或缩短拍摄距离，真实投影接入后应驱动对焦马达。")
    if obstruction_status == "possible_obstruction":
        recommendations.append("边缘信息过少，可能存在遮挡、画面过空或拍摄过近；请检查是否有人或物体挡住画面。")
    if not recommendations:
        recommendations.append("当前校准照片的亮度、清晰度、梯形和遮挡指标均在可接受范围。")
    return recommendations


def _calibration_report_markdown(analysis: dict[str, object]) -> str:
    brightness = analysis.get("brightness") if isinstance(analysis.get("brightness"), dict) else {}
    focus = analysis.get("focus") if isinstance(analysis.get("focus"), dict) else {}
    keystone = analysis.get("keystone") if isinstance(analysis.get("keystone"), dict) else {}
    recommendations = analysis.get("recommendations") if isinstance(analysis.get("recommendations"), list) else []
    lines = [
        "# 投影/外接显示器校准分析",
        "",
        f"状态: {analysis.get('status')}",
        f"亮度: {brightness.get('status')} ({brightness.get('value')})",
        f"清晰度: {focus.get('status')} ({focus.get('laplacian_variance')})",
        f"梯形: {keystone.get('status')} (水平 {keystone.get('horizontal_skew_pct')}%, 垂直 {keystone.get('vertical_skew_pct')}%)",
        "",
        "## 调整建议",
        *[f"- {item}" for item in recommendations],
        "",
    ]
    return "\n".join(lines)
