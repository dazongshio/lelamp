from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

from ..routes._base import ApiError, NOT_HANDLED, RequestContext


def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)


def require_string(*args, **kwargs): return _helper("require_string")(*args, **kwargs)
def safe_int(*args, **kwargs): return _helper("safe_int")(*args, **kwargs)
def status_to_audit(*args, **kwargs): return _helper("status_to_audit")(*args, **kwargs)


class ScanRuntimeMixin:
    def dispatch_scan_post(self, path: str, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object] | object:
        if path == "/api/scan/register":
            filename = require_string(payload, "filename")
            document_type = str(payload.get("document_type") or "document")
            return self.runtime.scanning.register_scan_image(filename, document_type)
        if path == "/api/scan/ocr":
            filename = require_string(payload, "filename")
            language = str(payload.get("language") or "ch")
            return self.runtime.scanning.run_ocr(filename, language)
        if path == "/api/scan/process":
            filename = require_string(payload, "filename")
            language = str(payload.get("language") or "chi_sim+eng")
            document_type = str(payload.get("document_type") or "document")
            return self.runtime.scanning.process_scan_image(filename, document_type=document_type, language=language)
        if path == "/api/scan/enhance":
            filename = require_string(payload, "filename")
            return self.runtime.scanning.enhance_scan_image(filename)
        if path == "/api/scan/capture-readiness":
            filename = require_string(payload, "filename")
            return self.runtime.scanning.capture_readiness(filename)
        if path == "/api/scan/demo-image":
            title = str(payload.get("title") or "validation_scan_demo")
            document_type = str(payload.get("document_type") or "document")
            return self.runtime.scanning.create_demo_scan_image(title=title, document_type=document_type)
        if path == "/api/scan/capture":
            image_data_url = require_string(payload, "image_data_url")
            title = str(payload.get("title") or "document_scan")
            language = str(payload.get("language") or "chi_sim+eng")
            document_type = str(payload.get("document_type") or "document")
            try:
                return self.runtime.scanning.capture_scan_image(
                    image_data_url,
                    title=title,
                    document_type=document_type,
                    language=language,
                    max_bytes=self.max_upload_bytes,
                )
            except ValueError as exc:
                raise ApiError("invalid_image", str(exc), status=400) from exc
        if path == "/api/scan/device-capture":
            title = str(payload.get("title") or "document_scan")
            language = str(payload.get("language") or "chi_sim+eng")
            document_type = str(payload.get("document_type") or "document")
            camera_index = self.resolve_camera_index(payload.get("camera_index"))
            rotation_degrees = self.scene_camera_rotation_degrees(camera_index, payload)
            timeout_seconds = max(3, min(20, safe_int(payload.get("timeout_seconds"), 12)))
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(self.runtime.camera_observer.capture_frame, camera_index=camera_index, rotation_degrees=rotation_degrees)
            try:
                capture = future.result(timeout=timeout_seconds)
            except FutureTimeoutError:
                future.cancel()
                capture = {
                    "status": "unavailable",
                    "message": f"设备摄像头拍照超过 {timeout_seconds} 秒未返回。",
                    "camera_index": camera_index,
                    "timeout_seconds": timeout_seconds,
                    "rotation_degrees": rotation_degrees,
                }
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            if str(capture.get("status") or "") != "captured":
                fallback_capture = self.capture_from_camera_preview_snapshot(title, camera_index=camera_index, rotation_degrees=rotation_degrees)
                if str(fallback_capture.get("status") or "") != "captured":
                    return {
                        "status": "unavailable",
                        "message": "设备摄像头没有拍到图片，请检查摄像头连接或改用浏览器摄像头/上传图片。",
                        "capture": capture,
                        "fallback_capture": fallback_capture,
                        "rotation_degrees": rotation_degrees,
                    }
                capture = fallback_capture
            capture_path = str(capture.get("path") or "")
            result = self.runtime.scanning.process_scan_image(
                capture_path,
                document_type=document_type,
                language=language,
            )
            result["capture"] = capture
            result["source_image_path"] = capture_path
            result["rotation_degrees"] = rotation_degrees
            try:
                result["source_workspace_name"] = str(Path(capture_path).resolve().relative_to(self.runtime.config.workspace_dir.resolve()))
            except ValueError:
                result["source_workspace_name"] = capture_path
            self.record_audit(
                "scan.device_capture",
                status_to_audit(str(result.get("status") or "completed")),
                str(result.get("source_workspace_name") or capture_path),
                {"camera_index": camera_index, "document_type": document_type},
                ctx,
            )
            return result
        if path == "/api/scan/summarize-ocr":
            filename = require_string(payload, "filename")
            return self.runtime.scanning.summarize_ocr_text(filename)
        if path == "/api/scan/business-card":
            filename = require_string(payload, "filename")
            return self.runtime.scanning.analyze_business_card_text(filename)
        return NOT_HANDLED
