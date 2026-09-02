from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime
from pathlib import Path
from typing import Any

from ._base import ApiError, NOT_HANDLED, RequestContext, exact_payload


def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)


def clamp_number(*a, **kw): return _helper("clamp_number")(*a, **kw)
def compact_scene_snapshot(*a, **kw): return _helper("compact_scene_snapshot")(*a, **kw)
def dedupe_scene_events(*a, **kw): return _helper("dedupe_scene_events")(*a, **kw)
def infer_ambient_lux_from_scene_event(*a, **kw): return _helper("infer_ambient_lux_from_scene_event")(*a, **kw)
def list_string(*a, **kw): return _helper("list_string")(*a, **kw)
def normalize_task_status(*a, **kw): return _helper("normalize_task_status")(*a, **kw)
def optional_float(*a, **kw): return _helper("optional_float")(*a, **kw)
def payload_bool(*a, **kw): return _helper("payload_bool")(*a, **kw)
def require_string(*a, **kw): return _helper("require_string")(*a, **kw)
def safe_int(*a, **kw): return _helper("safe_int")(*a, **kw)
def scan_view_plan_from_payload(*a, **kw): return _helper("scan_view_plan_from_payload")(*a, **kw)
def status_to_audit(*a, **kw): return _helper("status_to_audit")(*a, **kw)


class SceneRoutesMixin:
    def api_scene_image(self, workspace_name: str, ctx: RequestContext) -> Path:
        safe = self.ensure_allowed_path(workspace_name, ctx, action="scene_image")
        if safe.path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            raise ApiError("unsupported_scene_image", "Only image files can be displayed here.", status=400)
        self.record_audit("scene_image", "ok", safe.workspace_name, {"size_bytes": safe.path.stat().st_size}, ctx)
        return safe.path

    def api_scene_recent(self, limit: int, ctx: RequestContext) -> dict[str, object]:
        events = self.runtime.scene.get_recent_events(limit=max(1, min(100, limit)))
        payload = {"status": "ok", "events": events, "total": len(events)}
        self.record_audit("scene_recent", "ok", "scene", {"count": len(events)}, ctx)
        return payload

    def api_scene_workflow_suggestions(self, limit: int, ctx: RequestContext) -> dict[str, object]:
        limit = max(1, min(100, limit))
        events = self.runtime.scene.get_recent_events(limit=limit)
        suggestions = self.runtime.scene.workflow_suggestions(events, limit=limit)
        payload = {
            "status": "completed",
            "version": _helper("SCENE_WORKFLOW_VERSION"),
            "source": "recent_scene_events",
            "events": events,
            "suggestions": suggestions,
            "total": len(suggestions),
            "safety": [
                "场景建议只基于用户显式提交的图像或环境读数。",
                "触发工作流需要用户点击确认。",
                "不会被动解析投影内容，不会自动控制电脑。",
            ],
        }
        self.record_audit("scene_workflow_suggestions", "ok", "scene", {"events": len(events), "suggestions": len(suggestions)}, ctx)
        return payload

    def api_scene_workflow_suggestions_from_payload(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        events = payload.get("events")
        normalized_events: list[dict[str, object]]
        if isinstance(events, list):
            normalized_events = [item for item in events if isinstance(item, dict)]
        else:
            normalized_events = self.runtime.scene.get_recent_events(limit=max(1, min(100, safe_int(payload.get("limit"), 20))))
        suggestions = self.runtime.scene.workflow_suggestions(normalized_events)
        result = {
            "status": "completed",
            "version": _helper("SCENE_WORKFLOW_VERSION"),
            "source": "provided_events" if isinstance(events, list) else "recent_scene_events",
            "events": normalized_events,
            "suggestions": suggestions,
            "total": len(suggestions),
            "safety": [
                "场景建议只基于用户显式提交的图像或环境读数。",
                "触发工作流需要用户点击确认。",
                "不会被动解析投影内容，不会自动控制电脑。",
            ],
        }
        self.record_audit("scene_workflow_suggestions", "ok", "scene", {"events": len(normalized_events), "suggestions": len(suggestions)}, ctx)
        return result

    def api_scene_workflow_trigger(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        action = require_string(payload, "action")
        authorized = bool(payload.get("authorized"))
        if not authorized:
            result = {
                "status": "needs_confirmation",
                "action": action,
                "message": "需要用户在页面上明确确认后才触发场景工作流。",
                "safety": "no_passive_camera_or_projection_parsing",
            }
            self.record_audit("scene_workflow_trigger", "blocked", action, result, ctx)
            return result

        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        description = str(event.get("description") or payload.get("description") or "")
        title = str(payload.get("title") or "")
        if action == "scan_document":
            goal = title or "扫描并整理桌面纸质文件"
            document_type = str(payload.get("document_type") or "document")
            scan_url = f"/documents?scan=1&type={urllib.parse.quote(document_type)}&source=scene"
            steps = [
                f"请用户在 Documents 页面主动拍照或上传纸质文件图片：{scan_url}",
                "运行边界识别、透视校正、图像增强和 OCR。",
                "把 OCR 文本整理成摘要、表格或合同要点，等待用户确认。",
            ]
            desktop_task = self.runtime.desktop_tasks.request_task(
                goal,
                steps,
                source="scene_workflow",
                requires_full_control=False,
            )
            reminder = self.runtime.daily.create_reminder("场景检测到纸质文件：请在 Documents 页面完成扫描/OCR。")
            result = {
                "status": "completed",
                "action": action,
                "message": "已创建扫描工作流任务和本地提醒；不会自动拍照。",
                "desktop_task": desktop_task,
                "reminder": reminder,
                "next_url": scan_url,
                "scan_request": {
                    "document_type": document_type,
                    "source": "scene_workflow",
                    "requires_user_capture": True,
                    "recommended_endpoint": "/api/scan/capture",
                },
            }
            task = self.create_task("场景触发：扫描工作流", "scene", "completed", {"action": action, "event": event}, result)
            self.record_audit("scene_workflow_trigger", "ok", action, {"task_id": task["task_id"], "desktop_task_id": desktop_task.get("id")}, ctx)
            return {"task_id": task["task_id"], **result}

        if action == "projection_obstruction_prompt":
            details = ["检测到投影路径可能被遮挡。", "请移开遮挡物或调整外接显示器/投影位置。"]
            if description:
                details.append(f"触发事件：{description}")
            projection = self.runtime.projection.render_status_card(
                "投影遮挡提示",
                "needs_adjustment",
                details=details,
                accent="amber",
            )
            result = {
                "status": "completed",
                "action": action,
                "message": "已生成投影/显示器提示卡。",
                "projection": projection,
                "preview_url": self._projection_preview_url,
            }
            task = self.create_task("场景触发：投影遮挡提示", "scene", "completed", {"action": action, "event": event}, result)
            self.record_audit("scene_workflow_trigger", "ok", action, {"task_id": task["task_id"], "projection_path": projection.get("path")}, ctx)
            return {"task_id": task["task_id"], **result}

        if action == "meeting_mode_prompt":
            meeting_title = title or f"场景建议会议 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            participants = list_string(payload.get("participants")) or ["Unknown"]
            meeting = self.runtime.meeting.enable(meeting_title, participants)
            projection = self.runtime.projection.render_status_card(
                "会议模式已开启",
                "meeting_mode_enabled",
                details=[
                    "会议理解已由用户显式触发。",
                    "后续实时转写仍需要在 Meeting 页面启动 ASR/实时会议。",
                ],
                accent="green",
            )
            result = {
                "status": "completed",
                "action": action,
                "message": "已开启会议模式，并生成确认提示卡。",
                "meeting": meeting,
                "projection": projection,
                "preview_url": self._projection_preview_url,
            }
            task = self.create_task("场景触发：开启会议模式", "scene", "completed", {"action": action, "event": event}, result)
            self.record_audit("scene_workflow_trigger", "ok", action, {"task_id": task["task_id"], "meeting_mode_enabled": meeting.get("meeting_mode_enabled")}, ctx)
            return {"task_id": task["task_id"], **result}

        if action == "display_profile_adjustment":
            ambient_lux = optional_float(payload.get("ambient_lux"))
            if ambient_lux is None:
                ambient_lux = infer_ambient_lux_from_scene_event(event)
            profile_payload: dict[str, Any] = {"mode": "ambient", "ambient_lux": ambient_lux}
            profile = self.api_projection_display_profile_update(profile_payload, ctx)
            result = {
                "status": "completed",
                "action": action,
                "message": "已根据场景事件更新外接显示器数字亮度/对比度 profile。",
                "display_profile": profile,
            }
            task = self.create_task("场景触发：显示亮度 Profile", "scene", "completed", {"action": action, "event": event}, result)
            self.record_audit("scene_workflow_trigger", "ok", action, {"task_id": task["task_id"], "ambient_lux": ambient_lux}, ctx)
            return {"task_id": task["task_id"], **result}

        if action == "desk_idle_reminder":
            reminder = self.runtime.daily.create_reminder(title or "桌面空闲：稍后检查 workspace 和待办。")
            result = {
                "status": "completed",
                "action": action,
                "message": "已创建本地 reminder 草稿。",
                "reminder": reminder,
            }
            task = self.create_task("场景触发：桌面提醒", "scene", "completed", {"action": action, "event": event}, result)
            self.record_audit("scene_workflow_trigger", "ok", action, {"task_id": task["task_id"], "reminder_count": reminder.get("count")}, ctx)
            return {"task_id": task["task_id"], **result}

        raise ApiError("unsupported_scene_workflow", f"Unsupported scene workflow action: {action}", status=400)

    def api_scene_observe_image(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        image_data_url = require_string(payload, "image_data_url")
        title = str(payload.get("title") or "desk_scene")
        image_path = self.write_scene_observation_image(image_data_url, title)
        workspace_name = str(image_path.relative_to(self.runtime.workspace.root))
        analysis = self.runtime.camera_observer.analyze_frame(workspace_name)
        status = "completed" if analysis.get("status") == "ok" else normalize_task_status(str(analysis.get("status") or "backend_missing"))
        result = {
            "status": status,
            "source": "explicit_user_image_capture",
            "image_path": str(image_path),
            "workspace_name": workspace_name,
            "analysis": analysis,
            "events": analysis.get("events", []),
        }
        result["suggestions"] = self.runtime.scene.workflow_suggestions(
            [item for item in result["events"] if isinstance(item, dict)]
        )
        task = self.create_task("场景图像观察", "hardware", status, {"workspace_name": workspace_name}, result)
        self.record_audit(
            "scene_observe_image",
            status_to_audit(status),
            workspace_name,
            {"task_id": task["task_id"], "events": len(result["events"]), "suggestions": len(result["suggestions"])},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_scene_device_observe(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = str(payload.get("title") or "desk_scene_observation")
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
                "message": f"设备相机拍照超过 {timeout_seconds} 秒未返回。",
                "camera_index": camera_index,
                "timeout_seconds": timeout_seconds,
                "rotation_degrees": rotation_degrees,
            }
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if str(capture.get("status") or "") != "captured":
            fallback_capture = self.capture_from_camera_preview_snapshot(title, camera_index=camera_index, rotation_degrees=rotation_degrees)
            if str(fallback_capture.get("status") or "") != "captured":
                result = {
                    "status": "unavailable",
                    "source": "device_camera_capture",
                    "image_path": "",
                    "workspace_name": "",
                    "analysis": {},
                    "events": [],
                    "suggestions": [],
                    "capture": capture,
                    "fallback_capture": fallback_capture,
                    "rotation_degrees": rotation_degrees,
                    "message": "设备相机没有拍到图片，请检查摄像头连接或改用上传图片。",
                }
                self.record_audit("scene_device_observe", "blocked", f"camera:{camera_index}", result, ctx)
                return result
            capture = fallback_capture

        capture_path = Path(str(capture.get("path") or "")).expanduser().resolve()
        workspace_root = self.runtime.workspace.root.resolve()
        try:
            workspace_name = str(capture_path.relative_to(workspace_root))
        except ValueError:
            self.record_audit(
                "scene_device_observe",
                "blocked",
                str(capture_path),
                {"reason": "camera capture outside workspace", "camera_index": camera_index},
                ctx,
            )
            raise ApiError("blocked", "Camera capture is outside workspace.", status=403)

        analysis = self.runtime.camera_observer.analyze_frame(workspace_name)
        status = "completed" if analysis.get("status") == "ok" else normalize_task_status(str(analysis.get("status") or "backend_missing"))
        result = {
            "status": status,
            "source": "device_camera_capture",
            "image_path": str(capture_path),
            "workspace_name": workspace_name,
            "analysis": analysis,
            "events": analysis.get("events", []),
            "capture": capture,
            "camera_index": camera_index,
            "rotation_degrees": rotation_degrees,
            "title": title,
        }
        result["suggestions"] = self.runtime.scene.workflow_suggestions(
            [item for item in result["events"] if isinstance(item, dict)]
        )
        task = self.create_task("场景设备相机观察", "hardware", status, {"workspace_name": workspace_name, "camera_index": camera_index}, result)
        self.record_audit(
            "scene_device_observe",
            status_to_audit(status),
            workspace_name,
            {"task_id": task["task_id"], "events": len(result["events"]), "suggestions": len(result["suggestions"]), "camera_index": camera_index},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_scene_sensor_snapshot(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        """Collect an explicit one-shot sensor snapshot and turn it into scene events."""
        include_camera = bool(payload.get("include_camera", True))
        include_mic = bool(payload.get("include_mic", True))
        include_hardware = bool(payload.get("include_hardware", True))
        seconds = max(1, min(3, safe_int(payload.get("mic_seconds"), 1)))
        camera_title = str(payload.get("title") or "scene_sensor_snapshot")

        hardware = self.api_hardware_status(ctx=None) if include_hardware else {}
        devices = hardware.get("devices") if isinstance(hardware.get("devices"), dict) else {}
        sensors = hardware.get("sensors") if isinstance(hardware.get("sensors"), dict) else {}
        projection = devices.get("projection") if isinstance(devices.get("projection"), dict) else {}
        projection_details = projection.get("details") if isinstance(projection.get("details"), dict) else {}

        camera: dict[str, object] = {"status": "skipped"}
        camera_events: list[dict[str, object]] = []
        image_analysis: dict[str, object] = {}
        image_metrics: dict[str, object] = {}
        if include_camera:
            camera = self.capture_scene_camera_snapshot(camera_title, payload, ctx)
            image_analysis = camera.get("analysis") if isinstance(camera.get("analysis"), dict) else {}
            image_metrics = image_analysis.get("metrics") if isinstance(image_analysis.get("metrics"), dict) else {}
            camera_events = [item for item in camera.get("events", []) if isinstance(item, dict)]

        mic: dict[str, object] = {"status": "skipped"}
        if include_mic:
            mic = self.capture_scene_microphone_activity(seconds=seconds)

        brightness = optional_float(payload.get("lux"))
        lux_source = "manual" if brightness is not None else ""
        if brightness is None and image_metrics:
            metric_brightness = optional_float(image_metrics.get("brightness"))
            if metric_brightness is not None:
                brightness = round(metric_brightness / 255 * 1000, 1)
                lux_source = "camera_brightness_estimate"

        mic_status = str(mic.get("status") or "")
        mic_rms = safe_int(mic.get("rms"), 0)
        mic_peak = safe_int(mic.get("peak"), 0)
        speech_active = bool(payload.get("speech_active")) or (mic_status == "completed" and (mic_rms >= 120 or mic_peak >= 900))
        people_count = safe_int(payload.get("people_count"), 0)
        presence = bool(payload.get("presence")) or bool(people_count > 0) or bool(camera_events and str(camera_events[0].get("event_type") or "") != "desk_idle")
        projection_status = str(projection.get("status") or "")
        projector_blocked = bool(payload.get("projector_blocked"))
        if projection_status == "available" and image_metrics:
            rectangles = safe_int(image_metrics.get("large_rectangles"), 0)
            brightness_value = optional_float(image_metrics.get("brightness"))
            if rectangles == 0 and brightness_value is not None and brightness_value < 55:
                projector_blocked = True

        reading = {
            "presence": presence,
            "motion": bool(payload.get("motion")) if "motion" in payload else None,
            "lux": brightness,
            "sound_level": mic_rms if mic_status == "completed" else optional_float(payload.get("sound_level")),
            "speech_active": speech_active,
            "people_count": people_count or None,
            "projector_blocked": projector_blocked,
            "calendar_event_now": bool(payload.get("calendar_event_now")),
        }
        environment = self.runtime.environment.ingest(reading)
        all_events = dedupe_scene_events(
            [item for item in camera_events if isinstance(item, dict)]
            + [item for item in environment.get("events", []) if isinstance(item, dict)]
        )
        suggestions = self.runtime.scene.workflow_suggestions(all_events)
        status = "completed" if any(str(item.get("status") or "") not in {"unavailable", "failed", "error"} for item in (camera, mic, hardware)) else "unavailable"
        result = {
            "status": status,
            "source": "explicit_sensor_snapshot",
            "reading": reading,
            "reading_sources": {
                "lux": lux_source or "unavailable",
                "speech_active": "microphone_rms" if mic_status == "completed" else "manual_or_unavailable",
                "projection": "hardware_probe" if projection else "unavailable",
                "camera": str(camera.get("source") or camera.get("status") or "unavailable"),
            },
            "camera": camera,
            "microphone": mic,
            "hardware": {
                "status": "completed" if include_hardware else "skipped",
                "devices": devices,
                "sensors": sensors,
                "projection": projection,
                "projection_details": projection_details,
            },
            "environment": environment,
            "events": all_events,
            "event_count": len(all_events),
            "suggestions": suggestions,
            "safety": [
                "本次快照由用户主动触发，不启动后台常驻场景解析。",
                "相机只采集单帧；麦克风只采集短样本用于活动强度，不做转写。",
                "投影内容默认不解析，只读取连接/遮挡/亮度相关信号。",
            ],
        }
        task = self.create_task("场景传感器快照", "hardware", status, {"include_camera": include_camera, "include_mic": include_mic, "include_hardware": include_hardware}, result)
        self.record_audit(
            "scene_sensor_snapshot",
            status_to_audit(status),
            "scene",
            {"task_id": task["task_id"], "events": len(all_events), "suggestions": len(suggestions)},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_scene_ambient_capture(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        """Explicitly capture both cameras and server microphone audio, then transcribe it."""
        seconds = max(1, min(10, safe_int(payload.get("mic_seconds"), 4)))
        include_cameras = payload_bool(payload.get("include_cameras"), default=True)
        include_mic = payload_bool(payload.get("include_mic"), default=True)
        camera_indices = payload.get("camera_indices")
        if not isinstance(camera_indices, list):
            camera_indices = [0, 1]
        indices: list[int] = []
        for item in camera_indices:
            numeric = safe_int(item, -1)
            if numeric >= 0 and numeric not in indices:
                indices.append(numeric)
            if len(indices) >= 2:
                break
        if not indices:
            indices = [0, 1]

        cameras: list[dict[str, object]] = []
        if include_cameras:
            for index in indices:
                rotation_degrees = self.scene_camera_rotation_degrees(index, payload)
                camera = self.capture_scene_camera_snapshot(
                    f"ambient_camera_{index}",
                    {
                        **payload,
                        "camera_index": index,
                        "rotation_degrees": rotation_degrees,
                    },
                    ctx,
                )
                workspace_name = str(camera.get("workspace_name") or "")
                cameras.append(
                    {
                        "camera_index": index,
                        "rotation_degrees": camera.get("rotation_degrees", rotation_degrees),
                        "cam0_rotate_180": bool(camera.get("cam0_rotate_180", rotation_degrees == 180 if index == 0 else False)),
                        "status": camera.get("status", "unavailable"),
                        "source": camera.get("source", ""),
                        "workspace_name": workspace_name,
                        "image_url": self.console_scene_image_url(workspace_name) if workspace_name else "",
                        "path": camera.get("image_path") or camera.get("path") or "",
                        "events": camera.get("events", []),
                        "message": camera.get("message", ""),
                        "analysis": camera.get("analysis", {}),
                    }
                )

        audio = self.capture_scene_stereo_transcript(seconds=seconds) if include_mic else {"status": "skipped", "transcripts": []}
        camera_ok = any(str(item.get("status") or "") in {"captured", "completed", "ok"} for item in cameras)
        audio_ok = str(audio.get("status") or "") == "completed"
        if not include_cameras and not include_mic:
            status = "skipped"
        else:
            status = "completed" if camera_ok or audio_ok else "unavailable"
        result = {
            "status": status,
            "source": "explicit_ambient_capture",
            "include_cameras": include_cameras,
            "include_mic": include_mic,
            "camera_count": len(cameras),
            "cameras": cameras,
            "microphone": audio,
            "transcripts": audio.get("transcripts", []),
            "safety": [
                "仅在用户点击后采集，不后台常开监听。",
                "双摄检查和语音转文字可独立触发，未请求的输入不会采集。",
            ],
        }
        task_title = "双摄像头检查" if include_cameras and not include_mic else "左右声道转文字" if include_mic and not include_cameras else "双摄像头与左右声道转文字"
        task = self.create_task(task_title, "scene", status, {"seconds": seconds, "camera_indices": indices, "include_cameras": include_cameras, "include_mic": include_mic}, result)
        self.record_audit(
            "scene_ambient_capture",
            status_to_audit(status),
            "scene_ambient_capture",
            {"task_id": task["task_id"], "camera_count": len(cameras), "transcript_count": len(result["transcripts"]), "include_cameras": include_cameras, "include_mic": include_mic},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_scene_oriented_scan(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        authorized = bool(payload.get("authorized"))
        preflight = self.lelamp_motion_preflight(read_pose=True)
        if not authorized:
            result = {
                "status": "needs_confirmation",
                "source": "explicit_lelamp_oriented_scan",
                "message": "需要用户在页面点击授权后，才允许 LeLamp 小幅转动观察。",
                "preflight": preflight,
                "views": [],
                "events": [],
                "suggestions": [],
                "safety": [
                    "不会后台自动转动。",
                    "不会连续解析投影内容。",
                    "授权后只做一次小范围扫描。",
                ],
            }
            self.record_audit("scene_oriented_scan", "blocked", "lelamp", {"reason": "missing_authorization"}, ctx)
            return result
        if not self.runtime.config.enable_hardware:
            result = {
                "status": "adapter_ready",
                "source": "explicit_lelamp_oriented_scan",
                "message": "已检测到 LeLamp 串口，但当前进程未启用 OPENCLAW_ENABLE_HARDWARE=1；为安全起见不会写入电机目标位置。",
                "preflight": preflight,
                "views": [],
                "events": [],
                "suggestions": [],
            }
            self.record_audit("scene_oriented_scan", "blocked", "lelamp", {"reason": "hardware_disabled"}, ctx)
            return result
        if not preflight.get("pose_readable"):
            result = {
                "status": "needs_hardware",
                "source": "explicit_lelamp_oriented_scan",
                "message": "LeLamp 姿态不可读，未执行转动。请先确认电机供电、串口和校准状态。",
                "preflight": preflight,
                "views": [],
                "events": [],
                "suggestions": [],
            }
            self.record_audit("scene_oriented_scan", "blocked", "lelamp", {"reason": "pose_unreadable"}, ctx)
            return result

        mode = str(payload.get("mode") or "yaw").strip().lower()
        if mode not in {"yaw", "multi_axis", "pan_tilt"}:
            mode = "yaw"
        yaw_delta = clamp_number(optional_float(payload.get("yaw_delta")), default=6.0, low=1.0, high=12.0)
        pitch_delta = clamp_number(optional_float(payload.get("pitch_delta")), default=6.0, low=1.0, high=8.0)
        view_limit = max(1, min(9, safe_int(payload.get("view_limit"), 5 if mode != "yaw" else 3)))
        max_step = clamp_number(optional_float(payload.get("max_step")), default=3.0, low=1.0, high=4.0)
        hold_seconds = clamp_number(optional_float(payload.get("hold_seconds")), default=0.45, low=0.1, high=1.5)
        camera_index = self.resolve_camera_index(payload.get("camera_index"))
        include_mic = bool(payload.get("include_mic", False))
        tilt_motor = str(payload.get("tilt_motor") or "base_pitch").strip()
        if tilt_motor not in {"wrist_pitch", "base_pitch"}:
            tilt_motor = "base_pitch"
        views_plan = scan_view_plan_from_payload(
            payload.get("views"),
            payload.get("offsets"),
            yaw_delta=yaw_delta,
            pitch_delta=pitch_delta,
            mode=mode,
            view_limit=view_limit,
        )
        title = str(payload.get("title") or "lelamp_oriented_scan")

        started = time.monotonic()
        views: list[dict[str, object]] = []
        all_events: list[dict[str, object]] = []
        return_status = "not_started"
        return_error = ""
        port = str(preflight.get("port") or self.runtime.config.hardware_port)
        bus = None
        try:
            from lelamp.person_tracker import read_current_pose

            bus = self.connect_lelamp_motor_bus(port=port, max_step=max_step)
            start_pose = read_current_pose(bus)
            scan_motors = ["base_yaw"] if mode == "yaw" else ["base_yaw", tilt_motor]
            scan_center_pose = dict(start_pose)
            center_lift_offset = 0.0
            if mode != "yaw" and tilt_motor == "wrist_pitch":
                current_tilt = float(start_pose[tilt_motor])
                scan_center_pose[tilt_motor] = clamp_number(
                    max(current_tilt, -45.0),
                    default=current_tilt,
                    low=-55.0,
                    high=35.0,
                )
                center_lift_offset = round(float(scan_center_pose[tilt_motor]) - current_tilt, 3)
            for index, view_plan in enumerate(views_plan):
                yaw_offset = float(view_plan["yaw_offset"])
                pitch_offset = float(view_plan["pitch_offset"])
                target_pose = dict(scan_center_pose)
                target_pose["base_yaw"] = clamp_number(
                    float(start_pose["base_yaw"]) + yaw_offset,
                    default=float(start_pose["base_yaw"]),
                    low=-85.0,
                    high=85.0,
                )
                target_pose[tilt_motor] = clamp_number(
                    float(scan_center_pose[tilt_motor]) + pitch_offset,
                    default=float(scan_center_pose[tilt_motor]),
                    low=-55.0 if tilt_motor == "wrist_pitch" else -100.0,
                    high=35.0 if tilt_motor == "wrist_pitch" else 100.0,
                )
                actual_pose, movement_trace = self.move_lelamp_pose_in_steps(
                    bus,
                    target_pose,
                    motors=scan_motors,
                    max_step=max_step,
                    step_seconds=min(0.25, hold_seconds),
                )
                time.sleep(hold_seconds)
                actual_pose = read_current_pose(bus)
                actual_yaw_offset = float(actual_pose["base_yaw"]) - float(start_pose["base_yaw"])
                actual_pitch_offset = float(actual_pose[tilt_motor]) - float(scan_center_pose[tilt_motor])
                view_payload = {
                    "title": f"{title}_{index}_yaw{yaw_offset:+.1f}_pitch{pitch_offset:+.1f}",
                    "camera_index": camera_index,
                    "include_camera": True,
                    "include_mic": include_mic,
                    "include_hardware": True,
                    "mic_seconds": 1,
                    "cam0_rotate_180": payload.get("cam0_rotate_180", True),
                    "lux": payload.get("lux"),
                    "people_count": payload.get("people_count"),
                    "presence": payload.get("presence"),
                    "projector_blocked": payload.get("projector_blocked"),
                    "calendar_event_now": payload.get("calendar_event_now"),
                }
                snapshot = self.api_scene_sensor_snapshot(view_payload, ctx)
                view_events = [item for item in snapshot.get("events", []) if isinstance(item, dict)]
                all_events.extend(view_events)
                views.append(
                    {
                        "index": index,
                        "label": view_plan["label"],
                        "requested_yaw_offset": yaw_offset,
                        "requested_pitch_offset": pitch_offset,
                        "actual_yaw_offset": round(actual_yaw_offset, 3),
                        "actual_pitch_offset": round(actual_pitch_offset, 3),
                        "actual_pitch_from_start": round(float(actual_pose[tilt_motor]) - float(start_pose[tilt_motor]), 3),
                        "target_pose": target_pose,
                        "actual_pose": actual_pose,
                        "movement_trace": movement_trace,
                        "snapshot": compact_scene_snapshot(snapshot),
                        "events": view_events,
                    }
                )

            try:
                self.move_lelamp_pose_in_steps(
                    bus,
                    start_pose,
                    motors=scan_motors,
                    max_step=max_step,
                    step_seconds=min(0.25, hold_seconds),
                )
                time.sleep(hold_seconds)
                return_status = "completed"
            except Exception as exc:
                return_status = "failed"
                return_error = str(exc)[:1000]
        except Exception as exc:
            result = {
                "status": "failed",
                "source": "explicit_lelamp_oriented_scan",
                "message": f"LeLamp 转动观察失败：{str(exc)[:300]}",
                "preflight": preflight,
                "views": views,
                "events": dedupe_scene_events(all_events),
                "suggestions": self.runtime.scene.workflow_suggestions(dedupe_scene_events(all_events)),
                "error": str(exc)[:1000],
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }
            self.record_audit("scene_oriented_scan", "error", "lelamp", {"error": str(exc)[:500], "views": len(views)}, ctx)
            return result
        finally:
            if bus is not None:
                try:
                    bus.disconnect(disable_torque=False)
                except Exception:
                    pass

        deduped_events = dedupe_scene_events(all_events)
        suggestions = self.runtime.scene.workflow_suggestions(deduped_events)
        status = "completed" if views else "unavailable"
        result = {
            "status": status,
            "source": "explicit_lelamp_oriented_scan",
            "message": f"已完成 {len(views)} 个视角的{'左右/抬低' if mode != 'yaw' else '左右'}观察，并尝试回到起始姿态。",
            "preflight": preflight,
            "scan": {
                "mode": mode,
                "motors": ["base_yaw", tilt_motor] if mode != "yaw" else ["base_yaw"],
                "tilt_motor": tilt_motor,
                "tilt_label": "相机微调俯仰轴" if tilt_motor == "wrist_pitch" else "第一抬升舵机 base_pitch",
                "axis_summary": (
                    "左右使用 base_yaw，抬头/低头使用第一抬升舵机 base_pitch。"
                    if mode != "yaw" and tilt_motor == "base_pitch"
                    else "左右使用 base_yaw。"
                ),
                "scan_center_pose": scan_center_pose,
                "center_lift_offset": center_lift_offset,
                "views_plan": views_plan,
                "offsets": [item["yaw_offset"] for item in views_plan],
                "pitch_offsets": [item["pitch_offset"] for item in views_plan],
                "yaw_delta": yaw_delta,
                "pitch_delta": pitch_delta,
                "max_step": max_step,
                "hold_seconds": hold_seconds,
                "camera_index": camera_index,
                "include_mic": include_mic,
                "return_status": return_status,
                "return_error": return_error,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            },
            "views": views,
            "events": deduped_events,
            "event_count": len(deduped_events),
            "suggestions": suggestions,
            "safety": [
                "本次扫描由用户主动授权触发。",
                "多轴扫描只调整 base_yaw 和第一抬升舵机 base_pitch，并限制 yaw/pitch 最大偏移。",
                "扫描结束后尝试回到起始姿态，且不断开扭矩。",
            ],
        }
        task = self.create_task("LeLamp 转动观察环境", "hardware", status, {"mode": mode, "views": len(views), "views_plan": views_plan}, result)
        self.record_audit(
            "scene_oriented_scan",
            status_to_audit(status),
            "lelamp",
            {"task_id": task["task_id"], "views": len(views), "events": len(deduped_events), "return_status": return_status},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_scene_tracking_run(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        authorized = bool(payload.get("authorized"))
        preflight = self.lelamp_motion_preflight(read_pose=True)
        if not authorized:
            result = {
                "status": "needs_confirmation",
                "source": "person_tracker",
                "message": "需要用户显式授权后，才允许 LeLamp 目标追踪试运行。",
                "preflight": preflight,
                "frames": [],
                "target_count": 0,
                "move_count": 0,
            }
            self.record_audit("scene_tracking_run", "blocked", "person_tracker", {"reason": "missing_authorization"}, ctx)
            return result

        if not self.runtime.config.enable_hardware and bool(payload.get("move", False)):
            result = {
                "status": "adapter_ready",
                "source": "person_tracker",
                "message": "当前进程未启用 OPENCLAW_ENABLE_HARDWARE=1；只允许检测，不允许追踪移动。",
                "preflight": preflight,
                "frames": [],
                "target_count": 0,
                "move_count": 0,
            }
            self.record_audit("scene_tracking_run", "blocked", "person_tracker", {"reason": "hardware_disabled"}, ctx)
            return result

        camera_index = self.resolve_camera_index(payload.get("camera_index"))
        backend = str(payload.get("backend") or "yolo").strip().lower()
        if backend not in {"auto", "face", "hog", "yolo"}:
            backend = "yolo"
        frames = max(1, min(60, safe_int(payload.get("frames"), 12)))
        move = bool(payload.get("move", True))
        max_step = clamp_number(optional_float(payload.get("max_step")), default=1.5, low=0.5, high=3.0)
        yaw_gain = clamp_number(optional_float(payload.get("yaw_gain")), default=4.0, low=1.0, high=8.0)
        pitch_gain = clamp_number(optional_float(payload.get("pitch_gain")), default=3.0, low=1.0, high=8.0)
        deadband = clamp_number(optional_float(payload.get("deadband")), default=0.1, low=0.03, high=0.3)
        min_hits = max(1, min(5, safe_int(payload.get("min_hits"), 2)))
        width = max(320, min(3840, safe_int(payload.get("width"), 1280)))
        height = max(240, min(2160, safe_int(payload.get("height"), 720)))
        yolo_model = str(payload.get("yolo_model") or (_helper("runtime_root")() / "yolo11n.pt"))

        was_streaming = self.camera_stream_running()
        stream_camera_index = self._camera_stream_camera_index if self._camera_stream_camera_index is not None else camera_index
        started = time.monotonic()
        if was_streaming:
            self.stop_camera_stream_service(ctx=ctx)

        command = [
            sys.executable,
            "-m",
            "lelamp.person_tracker",
            "track",
            "--camera-index",
            str(camera_index),
            "--backend",
            backend,
            "--frames",
            str(frames),
            "--sleep",
            str(clamp_number(optional_float(payload.get("sleep")), default=0.12, low=0.02, high=0.5)),
            "--width",
            str(width),
            "--height",
            str(height),
            "--motion-mode",
            "head",
            "--max-step",
            str(max_step),
            "--yaw-gain",
            str(yaw_gain),
            "--pitch-gain",
            str(pitch_gain),
            "--deadband",
            str(deadband),
            "--min-hits",
            str(min_hits),
            "--yaw-min",
            "-85",
            "--yaw-max",
            "85",
            "--pitch-min",
            "-60",
            "--pitch-max",
            "35",
            "--port",
            str(preflight.get("port") or self.runtime.config.hardware_port),
            "--id",
            str(self.runtime.config.lamp_id),
        ]
        if backend == "yolo":
            command.extend(["--yolo-model", yolo_model])
        if move:
            command.append("--move")

        stdout = ""
        stderr = ""
        return_code = -1
        restored_stream: dict[str, object] | None = None
        try:
            completed = subprocess.run(
                command,
                cwd=str(_helper("runtime_root")()),
                check=False,
                capture_output=True,
                text=True,
                timeout=max(15, frames * 2),
            )
            stdout = completed.stdout
            stderr = completed.stderr
            return_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or "tracking command timed out"
            return_code = 124
        finally:
            if was_streaming:
                restored_stream = self.start_camera_stream_service(
                    camera_index=stream_camera_index,
                    width=width,
                    height=height,
                    backend="auto",
                    ctx=ctx,
                )

        frame_payloads: list[dict[str, object]] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                frame_payloads.append(parsed)

        target_count = sum(1 for item in frame_payloads if str(item.get("status") or "") == "target_found")
        move_count = sum(1 for item in frame_payloads if isinstance(item.get("sent_action"), dict))
        status = "completed" if return_code == 0 else "failed"
        if return_code == 0 and target_count == 0:
            status = "no_target"
        result = {
            "status": status,
            "source": "person_tracker",
            "message": "追踪试运行完成，未检测到目标。" if target_count == 0 else f"追踪试运行完成，检测到 {target_count} 帧目标，发出 {move_count} 次移动。",
            "preflight": preflight,
            "request": {
                "camera_index": camera_index,
                "backend": backend,
                "frames": frames,
                "move": move,
                "motion_mode": "head",
                "motors": ["base_yaw", "wrist_pitch"],
                "max_step": max_step,
                "yaw_gain": yaw_gain,
                "pitch_gain": pitch_gain,
                "deadband": deadband,
                "min_hits": min_hits,
                "stream_was_running": was_streaming,
            },
            "frames": frame_payloads,
            "target_count": target_count,
            "move_count": move_count,
            "return_code": return_code,
            "stderr_tail": stderr[-2000:],
            "restored_stream": restored_stream,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
        }
        task = self.create_task("LeLamp 目标追踪试运行", "hardware", status, result["request"], result)
        self.record_audit(
            "scene_tracking_run",
            status_to_audit(status),
            "person_tracker",
            {"task_id": task["task_id"], "target_count": target_count, "move_count": move_count, "return_code": return_code},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_scene_environment(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        reading = {
            "presence": payload.get("presence"),
            "motion": payload.get("motion"),
            "lux": payload.get("lux"),
            "sound_level": payload.get("sound_level"),
            "speech_active": payload.get("speech_active"),
            "people_count": payload.get("people_count"),
            "projector_blocked": payload.get("projector_blocked"),
            "calendar_event_now": payload.get("calendar_event_now"),
        }
        result = self.runtime.environment.ingest(reading)
        result["suggestions"] = self.runtime.scene.workflow_suggestions(
            [item for item in result.get("events", []) if isinstance(item, dict)]
        )
        status = "completed"
        task = self.create_task("环境场景读数", "hardware", status, {"reading": reading}, result)
        self.record_audit(
            "scene_environment",
            "ok",
            "environment",
            {"task_id": task["task_id"], "event_count": result.get("event_count"), "suggestions": len(result["suggestions"])},
            ctx,
        )
        return {"status": status, "task_id": task["task_id"], **result}

    def api_scene_report(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        event_type = require_string(payload, "event_type")
        description = require_string(payload, "description")
        confidence = float(payload.get("confidence") or 1.0)
        event = self.runtime.scene.report_event(event_type, description, confidence)
        suggestions = self.runtime.scene.workflow_suggestions([event])
        self.record_audit("scene_report", "ok", event_type, {"event": event, "suggestions": len(suggestions)}, ctx)
        return {"status": "completed", "event": event, "suggestions": suggestions}


GET = {"/api/scene/recent": "api_scene_recent", "/api/scene/workflow-suggestions": "api_scene_workflow_suggestions"}
POST = {
    "/api/scene/observe-image": "api_scene_observe_image", "/api/scene/device-observe": "api_scene_device_observe",
    "/api/scene/sensor-snapshot": "api_scene_sensor_snapshot", "/api/scene/ambient-capture": "api_scene_ambient_capture",
    "/api/scene/oriented-scan": "api_scene_oriented_scan", "/api/scene/tracking-run": "api_scene_tracking_run",
    "/api/scene/environment": "api_scene_environment", "/api/scene/report": "api_scene_report",
    "/api/scene/workflow/trigger": "api_scene_workflow_trigger",
}

def dispatch_get(server: Any, path: str, params: dict[str, list[str]], ctx: Any) -> Any:
    if path == "/api/scene/recent": return server.api_scene_recent(_helper("safe_int")(params.get("limit", ["20"])[0], 20), ctx)
    if path == "/api/scene/workflow-suggestions": return server.api_scene_workflow_suggestions(_helper("safe_int")(params.get("limit", ["20"])[0], 20), ctx)
    return NOT_HANDLED

def dispatch_post(server: Any, path: str, payload: dict[str, Any], ctx: Any) -> Any:
    if path == "/api/scene/workflow-suggestions": return server.api_scene_workflow_suggestions_from_payload(payload, ctx)
    return exact_payload(server, path, payload, ctx, POST)
