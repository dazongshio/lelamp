from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .scene import SceneService
from .workspace import Workspace, WorkspaceError


class CameraObserverService:
    """Single-frame desk observation with optional OpenCV analysis."""

    def __init__(self, *, workspace: Workspace, audit: AuditLogger, scene: SceneService):
        self.workspace = workspace
        self.audit = audit
        self.scene = scene

    def capture_frame(self, *, camera_index: int = 0, rotation_degrees: int = 0) -> dict[str, object]:
        rotation_degrees = normalize_rotation_degrees(rotation_degrees)
        out_path = self.workspace.path_for_new_file(
            f"desk_observation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        )
        commands = [
            ["rpicam-still", "--camera", str(camera_index), "-n", "-o", str(out_path), "-t", "1000"],
            ["libcamera-still", "--camera", str(camera_index), "-n", "-o", str(out_path), "-t", "1000"],
            ["fswebcam", "-q", "-d", f"/dev/video{camera_index}", str(out_path)],
        ]
        attempted: list[str] = []
        for command in commands:
            if shutil.which(command[0]) is None:
                continue
            attempted.append(command[0])
            try:
                subprocess.run(command, check=True, timeout=15, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                attempted.append(f"{command[0]}:{type(exc).__name__}")
                continue
            if out_path.exists() and out_path.stat().st_size > 0:
                rotation = self.rotate_image_file(out_path, rotation_degrees=rotation_degrees)
                payload = {
                    "status": "captured",
                    "path": str(out_path),
                    "bytes": out_path.stat().st_size,
                    "command": command[0],
                    "rotation_degrees": rotation_degrees,
                    "rotation": rotation,
                }
                self.audit.record("camera.capture", target=str(out_path), details=payload)
                return payload

        cv2_result = self._capture_with_cv2(out_path, camera_index, rotation_degrees=rotation_degrees)
        if cv2_result["status"] == "captured":
            return cv2_result

        payload = {
            "status": "unavailable",
            "path": str(out_path),
            "attempted": attempted,
            "rotation_degrees": rotation_degrees,
            "install_hint": "Install rpicam/libcamera, fswebcam, or opencv-python with camera access.",
        }
        self.audit.record("camera.capture", status="blocked", target=str(out_path), details=payload)
        return payload

    def analyze_frame(self, image_filename: str) -> dict[str, object]:
        try:
            image_path = self._resolve_image(image_filename)
        except WorkspaceError as exc:
            return {"status": "blocked", "reason": str(exc), "image": image_filename}

        try:
            import cv2
        except Exception:
            payload = {
                "status": "needs_backend",
                "image_path": str(image_path),
                "install_hint": "Install opencv-python to enable document/whiteboard/brightness heuristics.",
            }
            self.audit.record("camera.analyze", status="blocked", target=str(image_path), details=payload)
            return payload

        frame = cv2.imread(str(image_path))
        if frame is None:
            payload = {"status": "error", "image_path": str(image_path), "reason": "cv2 could not read image"}
            self.audit.record("camera.analyze", status="error", target=str(image_path), details=payload)
            return payload

        metrics = image_metrics_from_cv2(frame)
        events = scene_events_from_metrics(metrics)
        reported = [
            self.scene.report_event(event["event_type"], event["description"], float(event["confidence"]))
            for event in events
        ]
        payload = {
            "status": "ok",
            "image_path": str(image_path),
            "metrics": metrics,
            "events": reported,
        }
        self.audit.record("camera.analyze", target=str(image_path), details={"events": len(reported)})
        return payload

    def observe_once(self, *, camera_index: int = 0, rotation_degrees: int = 0) -> dict[str, object]:
        capture = self.capture_frame(camera_index=camera_index, rotation_degrees=rotation_degrees)
        if capture.get("status") != "captured":
            return {"status": "unavailable", "capture": capture, "events": []}
        analysis = self.analyze_frame(str(capture["path"]))
        return {
            "status": "ok" if analysis.get("status") == "ok" else "partial",
            "capture": capture,
            "analysis": analysis,
            "events": analysis.get("events", []),
        }

    def _capture_with_cv2(self, out_path: Path, camera_index: int, *, rotation_degrees: int = 0) -> dict[str, object]:
        try:
            import cv2
        except Exception:
            return {"status": "needs_backend"}
        camera = cv2.VideoCapture(camera_index)
        try:
            ok, frame = camera.read()
        finally:
            camera.release()
        if not ok:
            return {"status": "unavailable"}
        rotation_degrees = normalize_rotation_degrees(rotation_degrees)
        if rotation_degrees == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        cv2.imwrite(str(out_path), frame)
        payload = {
            "status": "captured",
            "path": str(out_path),
            "bytes": out_path.stat().st_size if out_path.exists() else 0,
            "command": "cv2.VideoCapture",
            "rotation_degrees": rotation_degrees,
            "rotation": {"status": "applied" if rotation_degrees else "none", "degrees": rotation_degrees},
        }
        self.audit.record("camera.capture", target=str(out_path), details=payload)
        return payload

    def rotate_image_file(self, path: Path, *, rotation_degrees: int = 0) -> dict[str, object]:
        rotation_degrees = normalize_rotation_degrees(rotation_degrees)
        if rotation_degrees == 0:
            return {"status": "none", "degrees": 0}
        try:
            import cv2
        except Exception as exc:
            return {"status": "needs_backend", "degrees": rotation_degrees, "message": type(exc).__name__}

        frame = cv2.imread(str(path))
        if frame is None:
            return {"status": "failed", "degrees": rotation_degrees, "message": "cv2 could not read image"}
        if rotation_degrees == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        if not cv2.imwrite(str(path), frame):
            return {"status": "failed", "degrees": rotation_degrees, "message": "cv2 could not write image"}
        return {"status": "applied", "degrees": rotation_degrees}

    def _resolve_image(self, image_filename: str) -> Path:
        candidate = Path(image_filename).expanduser()
        if candidate.is_absolute() and candidate.is_file() and candidate.resolve().is_relative_to(self.workspace.root):
            return candidate.resolve()
        return self.workspace.resolve_workspace_file(image_filename)


def normalize_rotation_degrees(value: int) -> int:
    try:
        degrees = int(value)
    except (TypeError, ValueError):
        return 0
    return 180 if degrees % 360 == 180 else 0


def image_metrics_from_cv2(frame: Any) -> dict[str, object]:
    import cv2

    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    edges = cv2.Canny(gray, 80, 180)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    large_rectangles = 0
    largest_area_ratio = 0.0
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        if perimeter <= 0:
            continue
        approx = cv2.approxPolyDP(contour, 0.03 * perimeter, True)
        area = cv2.contourArea(contour)
        area_ratio = float(area / max(1, width * height))
        if len(approx) == 4 and area_ratio > 0.08:
            large_rectangles += 1
            largest_area_ratio = max(largest_area_ratio, area_ratio)

    return {
        "width": width,
        "height": height,
        "brightness": round(brightness, 2),
        "large_rectangles": large_rectangles,
        "largest_area_ratio": round(largest_area_ratio, 4),
    }


def scene_events_from_metrics(metrics: dict[str, object]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    brightness = float(metrics.get("brightness") or 0)
    large_rectangles = int(metrics.get("large_rectangles") or 0)
    largest_area_ratio = float(metrics.get("largest_area_ratio") or 0)

    if brightness < 45:
        events.append(
            {
                "event_type": "ambient_too_dark",
                "description": f"桌面画面亮度偏低，平均亮度 {brightness:.1f}",
                "confidence": 0.76,
            }
        )
    elif brightness > 225:
        events.append(
            {
                "event_type": "projection_too_bright",
                "description": f"画面亮度过高，可能影响投影或 OCR，平均亮度 {brightness:.1f}",
                "confidence": 0.66,
            }
        )

    if large_rectangles >= 1 and largest_area_ratio >= 0.12:
        events.append(
            {
                "event_type": "paper_or_screen_detected",
                "description": f"检测到大矩形区域，可能是纸质文档、屏幕或白板，占比 {largest_area_ratio:.2f}",
                "confidence": min(0.92, 0.62 + largest_area_ratio),
            }
        )

    if not events:
        events.append(
            {
                "event_type": "desk_observed",
                "description": "已完成桌面单帧观察，未发现明确纸张、白板或光照异常。",
                "confidence": 0.5,
            }
        )
    return events
