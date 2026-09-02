from __future__ import annotations
import json
import math
import time
import urllib.parse
from datetime import datetime
from typing import Any
from ._base import ApiError, NOT_HANDLED, RequestContext, exact_payload

def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)

def atomic_write_json(*a,**kw): return _helper("atomic_write_json")(*a,**kw)
def clamp_number(*a,**kw): return _helper("clamp_number")(*a,**kw)
def hardware_device_details(*a,**kw): return _helper("hardware_device_details")(*a,**kw)
def normalize_hardware_test_status(*a,**kw): return _helper("normalize_hardware_test_status")(*a,**kw)
def optional_float(*a,**kw): return _helper("optional_float")(*a,**kw)
def ordered_motor_names(*a,**kw): return _helper("ordered_motor_names")(*a,**kw)
def parse_lamp_voice_command(*a,**kw): return _helper("parse_lamp_voice_command")(*a,**kw)
def parse_system_audio_voice_command(*a,**kw): return _helper("parse_system_audio_voice_command")(*a,**kw)
def parse_voice_assistant_control_command(*a,**kw): return _helper("parse_voice_assistant_control_command")(*a,**kw)
def play_speaker_tone(*a,**kw): return _helper("play_speaker_tone")(*a,**kw)
def probe_hardware(*a,**kw): return _helper("probe_hardware")(*a,**kw)
def read_recent_audit(*a,**kw): return _helper("read_recent_audit")(*a,**kw)
def record_microphone_sample(*a,**kw): return _helper("record_microphone_sample")(*a,**kw)
def require_string(*a,**kw): return _helper("require_string")(*a,**kw)
def round_motor_map(*a,**kw): return _helper("round_motor_map")(*a,**kw)
def safe_filename(*a,**kw): return _helper("safe_filename")(*a,**kw)
def safe_int(*a,**kw): return _helper("safe_int")(*a,**kw)
def status_to_audit(*a,**kw): return _helper("status_to_audit")(*a,**kw)
def summarize_dict(*a,**kw): return _helper("summarize_dict")(*a,**kw)

class HardwareRoutesMixin:
    def api_camera_stream_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        running = self.camera_stream_running()
        status_payload: dict[str, object] = {}
        if running:
            try:
                with urllib.request.urlopen(f"{self._camera_stream_url}status.json", timeout=1.5) as response:
                    status_payload = json.loads(response.read().decode("utf-8", errors="replace"))
            except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                status_payload = {}
        browser_preview_url = self.camera_stream_browser_url(ctx)
        stream_status = str(status_payload.get("status") or "")
        frame_index = safe_int(status_payload.get("frame_index"), 0)
        stream_error = str(status_payload.get("error") or "")
        if not running:
            public_status = "stopped"
            message = "Camera preview is stopped."
        elif stream_status == "error":
            public_status = "error"
            message = stream_error or "Camera preview service is running, but the camera did not open."
        elif stream_status == "camera_read_failed":
            public_status = "error"
            message = "Camera preview service is running, but no camera frames are available."
        elif frame_index <= 0 and stream_status in {"", "starting"}:
            public_status = "starting"
            message = "Camera preview is starting; waiting for the first frame."
        else:
            public_status = "online"
            message = "Camera preview is available in the browser."
        payload = {
            "status": public_status,
            "preview_url": self._camera_stream_url,
            "snapshot_url": f"{self._camera_stream_url}snapshot.jpg",
            "stream_url": f"{self._camera_stream_url}stream.mjpg",
            "browser_preview_url": browser_preview_url,
            "browser_snapshot_url": urllib.parse.urljoin(browser_preview_url, "snapshot.jpg"),
            "browser_stream_url": urllib.parse.urljoin(browser_preview_url, "stream.mjpg"),
            "camera_index": safe_int(status_payload.get("camera_index"), self._camera_stream_camera_index or 0),
            "started_at": self._camera_stream_started_at,
            "always_on": running,
            "details": status_payload,
            "message": message,
        }
        if ctx:
            self.record_audit("camera_stream.status", "ok", "camera_stream", {"running": running}, ctx)
        return payload

    def api_camera_stream_start(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        result = self.start_camera_stream_service(
            camera_index=payload.get("camera_index"),
            width=safe_int(payload.get("width"), 1280),
            height=safe_int(payload.get("height"), 720),
            backend=str(payload.get("backend") or "auto"),
            ctx=ctx,
        )
        self.record_audit("camera_stream.start", status_to_audit(str(result.get("status"))), "camera_stream", result, ctx)
        return result

    def api_camera_stream_stop(self, ctx: RequestContext) -> dict[str, object]:
        result = self.stop_camera_stream_service(ctx=ctx)
        self.record_audit("camera_stream.stop", status_to_audit(str(result.get("status"))), "camera_stream", result, ctx)
        return result

    def api_hardware_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        scanned = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
        devices = scanned["devices"] if isinstance(scanned.get("devices"), dict) else {}
        events = [event for event in read_recent_audit(self.runtime.config.audit_log_path, limit=40) if str(event.get("action", "")).startswith(("lelamp", "hardware", "projection"))]
        payload = {
            "hardware_enabled": self.runtime.config.enable_hardware,
            "devices": devices,
            "sensors": scanned.get("sensors", {}),
            "events": events[-10:],
            "camera": {
                "status": str(devices.get("camera", {}).get("status", "unavailable")) if isinstance(devices.get("camera"), dict) else "unavailable",
                "note": summarize_dict(devices.get("camera", {}).get("details", {})) if isinstance(devices.get("camera"), dict) else "",
            },
            "screen_context": {"status": "adapter_ready", "note": "Screen capture remains explicit only."},
            "lelamp": self.runtime.lelamp_experience.capability_map(),
            "smart_home": self.runtime.smart_home.status(),
            "scan": scanned.get("scan", {}),
            "probes": scanned.get("probes", {}),
        }
        if ctx:
            self.record_audit("hardware_status", "ok", "hardware", {"hardware_enabled": payload["hardware_enabled"], "summary": payload["scan"]}, ctx)
        return payload

    def api_hardware_scan(self, ctx: RequestContext) -> dict[str, object]:
        payload = self.api_hardware_status(ctx=None)
        self.record_audit("hardware_scan", "ok", "hardware", {"summary": payload.get("scan", {})}, ctx)
        return payload

    def api_lelamp_motion_status(self, ctx: RequestContext) -> dict[str, object]:
        result = self.lelamp_motion_preflight(read_pose=True)
        self.record_audit(
            "lelamp_motion_status",
            status_to_audit(str(result.get("status") or "unavailable")),
            str(result.get("port") or self.runtime.config.hardware_port),
            {
                "hardware_enabled": result.get("hardware_enabled"),
                "serial_detected": result.get("serial_detected"),
                "pose_readable": result.get("pose_readable"),
            },
            ctx,
        )
        return result

    def api_lelamp_motor_control_read(self, ctx: RequestContext) -> dict[str, object]:
        port = str(self.runtime.config.hardware_port or "/dev/ttyACM0")
        pose = self.read_lelamp_pose(port=port)
        saved_poses = {
            "default": self.read_saved_lelamp_pose("lelamp_center"),
            "scan": self.read_saved_lelamp_pose("lelamp_scan"),
            "projection": self.read_saved_lelamp_pose("lelamp_projection"),
        }
        result = {
            "status": "completed" if pose.get("pose_readable") else "failed",
            "hardware_enabled": self.runtime.config.enable_hardware,
            "port": port,
            "lamp_id": self.runtime.config.lamp_id,
            "motors": list(_helper("LELAMP_CONTROL_MOTORS")),
            "pose": round_motor_map(pose.get("pose", {}), _helper("LELAMP_CONTROL_MOTORS")),
            "saved_poses": saved_poses,
            "pose_readable": bool(pose.get("pose_readable")),
            "error": pose.get("error", ""),
            "duration_ms": pose.get("duration_ms"),
        }
        self.record_audit("lelamp_motor_read", status_to_audit(str(result["status"])), port, {"pose_readable": result["pose_readable"]}, ctx)
        return result

    def api_lelamp_motor_control_move(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        if not self.runtime.config.enable_hardware:
            raise ApiError("hardware_disabled", "OPENCLAW_ENABLE_HARDWARE is not enabled.", status=409)
        port = str(self.runtime.config.hardware_port or "/dev/ttyACM0")
        mode = str(payload.get("mode") or "target")
        max_delta = clamp_number(optional_float(payload.get("max_delta")), default=35.0, low=0.1, high=90.0)
        hold_seconds = clamp_number(optional_float(payload.get("hold_seconds")), default=0.35, low=0.0, high=3.0)
        requested = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        deltas = payload.get("deltas") if isinstance(payload.get("deltas"), dict) else {}
        motor = str(payload.get("motor") or "").strip()
        delta_value = optional_float(payload.get("delta"))

        try:
            from lelamp.person_tracker import read_current_pose

            bus = self.connect_lelamp_motor_bus(port=port, max_step=None)
            try:
                current = read_current_pose(bus)
                target: dict[str, float] = {}
                if mode == "delta":
                    for name, value in deltas.items():
                        if str(name) in _helper("LELAMP_CONTROL_MOTORS"):
                            delta = clamp_number(optional_float(value), default=0.0, low=-max_delta, high=max_delta)
                            target[str(name)] = float(current[str(name)]) + delta
                    if motor in _helper("LELAMP_CONTROL_MOTORS") and delta_value is not None:
                        target[motor] = float(current[motor]) + clamp_number(delta_value, default=0.0, low=-max_delta, high=max_delta)
                else:
                    for name, value in requested.items():
                        if str(name) in _helper("LELAMP_CONTROL_MOTORS"):
                            numeric = optional_float(value)
                            if numeric is not None:
                                target[str(name)] = float(numeric)
                if not target:
                    raise ApiError("empty_motor_target", "No valid motor target was provided.", status=400)
                ordered_target = {motor: float(target[motor]) for motor in ordered_motor_names(target)}
                move_step = clamp_number(optional_float(payload.get("max_step")), default=6.0, low=0.5, high=12.0)
                move_iterations = safe_int(payload.get("max_iterations"), 0)
                actual, movement_trace = self.move_lelamp_pose_in_steps(
                    bus,
                    ordered_target,
                    motors=ordered_motor_names(ordered_target),
                    max_step=move_step,
                    step_seconds=0.12,
                    tolerance=0.5,
                    max_iterations=max(1, min(240, move_iterations)) if move_iterations > 0 else None,
                )
                time.sleep(hold_seconds)
                actual = read_current_pose(bus)
            finally:
                bus.disconnect(disable_torque=False)
        except Exception as exc:
            result = {
                "status": "failed",
                "port": port,
                "lamp_id": self.runtime.config.lamp_id,
                "error": str(exc)[:1000],
            }
            self.record_audit("lelamp_motor_move", "error", port, result, ctx)
            return result

        errors = {name: round(abs(float(ordered_target[name]) - float(actual[name])), 4) for name in ordered_target}
        max_error = max(errors.values()) if errors else 0
        result = {
            "status": "completed" if max_error <= 0.5 else "timeout",
            "port": port,
            "lamp_id": self.runtime.config.lamp_id,
            "mode": mode,
            "motors": list(_helper("LELAMP_CONTROL_MOTORS")),
            "before": round_motor_map(current, _helper("LELAMP_CONTROL_MOTORS")),
            "target": round_motor_map(ordered_target),
            "actual": round_motor_map(actual, _helper("LELAMP_CONTROL_MOTORS")),
            "errors": errors,
            "max_error": round(max_error, 4),
            "write_order": ordered_motor_names(ordered_target),
            "movement_trace": movement_trace,
        }
        self.record_audit("lelamp_motor_move", status_to_audit(str(result["status"])), port, {"mode": mode, "target": ordered_target, "write_order": result["write_order"], "max_error": result["max_error"]}, ctx)
        return result

    def api_lelamp_motor_control_save_pose(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        pose_key = str(payload.get("pose") or "").strip().lower()
        pose_names = {
            "default": "lelamp_center",
            "scan": "lelamp_scan",
            "projection": "lelamp_projection",
        }
        pose_name = pose_names.get(pose_key)
        if not pose_name:
            raise ApiError("invalid_pose", "Pose must be default, scan, or projection.", status=400)
        motors_payload = payload.get("motors")
        if not isinstance(motors_payload, dict):
            raise ApiError("missing_motors", "Missing motors payload.", status=400)

        motors: dict[str, float] = {}
        missing: list[str] = []
        invalid: list[str] = []
        for motor in _helper("LELAMP_CONTROL_MOTORS"):
            if motor not in motors_payload:
                missing.append(motor)
                continue
            numeric = optional_float(motors_payload.get(motor))
            if numeric is None or not math.isfinite(numeric):
                invalid.append(motor)
                continue
            motors[motor] = round(float(numeric), 4)
        if missing or invalid:
            raise ApiError(
                "invalid_pose_motors",
                "Pose must include numeric values for all five motors.",
                status=400,
                details={"missing": missing, "invalid": invalid},
            )

        port = str(self.runtime.config.hardware_port or "/dev/ttyACM0")
        pose_path = self.runtime.config.workspace_dir / ".poses" / f"{safe_filename(pose_name)}.json"
        pose_payload = {
            "name": pose_name,
            "id": self.runtime.config.lamp_id,
            "port": port,
            "created_at": time.time(),
            "motors": motors,
        }
        atomic_write_json(pose_path, pose_payload)
        motion_config = load_motion_config(self.motion_config_path())
        pose_labels = {
            "default": "默认位置",
            "scan": "扫描位置",
            "projection": "投影位置",
        }
        set_named_pose(motion_config, pose_key, motors, label=pose_labels.get(pose_key, pose_key))
        motion_config_path = save_motion_config(motion_config, self.motion_config_path())
        saved_poses = {
            "default": self.read_saved_lelamp_pose("lelamp_center"),
            "scan": self.read_saved_lelamp_pose("lelamp_scan"),
            "projection": self.read_saved_lelamp_pose("lelamp_projection"),
        }
        result = {
            "status": "completed",
            "port": port,
            "lamp_id": self.runtime.config.lamp_id,
            "pose": motors,
            "saved_poses": saved_poses,
            "pose_file": str(pose_path),
            "motion_config_file": str(motion_config_path),
            "motors": list(_helper("LELAMP_CONTROL_MOTORS")),
        }
        self.record_audit("lelamp_motor_save_pose", "ok", pose_key, {"pose_name": pose_name, "motors": motors}, ctx)
        return result

    def api_lelamp_state(self, state: str, ctx: RequestContext) -> dict[str, object]:
        result = self.runtime.lelamp_experience.state_cue(state)
        scan = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
        rgb = scan.get("devices", {}).get("rgb", {}) if isinstance(scan.get("devices"), dict) else {}
        rgb_status_value = str(rgb.get("status", "adapter_ready")) if isinstance(rgb, dict) else "adapter_ready"
        status = "adapter_ready" if not self.runtime.config.enable_hardware else ("ok" if rgb_status_value == "available" else rgb_status_value)
        payload = {
            "status": status,
            "state": state,
            "cue": result,
            "hardware_enabled": self.runtime.config.enable_hardware,
            "rgb_probe_status": rgb_status_value,
        }
        self.record_audit("lelamp_state", status, state, payload, ctx)
        return payload

    def api_lelamp_voice_command(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        text = str(payload.get("text") or payload.get("message") or "").strip()
        if not text:
            raise ApiError("missing_text", "Missing LeLamp command text.", status=400)

        audio_action = parse_system_audio_voice_command(text)
        if audio_action is not None:
            current = self._read_system_audio()
            update: dict[str, object] = {}
            if "volume" in audio_action:
                update["volume"] = audio_action["volume"]
                update["muted"] = False
            elif "delta" in audio_action:
                update["volume"] = max(0, min(100, int(current["volume"]) + int(audio_action["delta"])))
                update["muted"] = False
            if "muted" in audio_action:
                update["muted"] = audio_action["muted"]
            audio_result = self.api_audio_settings_update(update, ctx)
            reply = "已静音。" if audio_result["muted"] else f"音量已调整到 {audio_result['volume']}%。"
            return {
                "handled": True,
                "text": text,
                "status": "executed",
                "reply": reply,
                "command": {"action": "set_system_volume", "label": "音量控制", "reply": reply},
                "audio": audio_result,
            }

        voice_control_action = parse_voice_assistant_control_command(text)
        if voice_control_action is not None:
            if voice_control_action == "start":
                voice_result = self.api_voice_assistant_start({}, ctx)
                reply = str(voice_result.get("message") or "已开启语音助手。")
            elif voice_control_action == "stop":
                voice_result = self.api_voice_assistant_stop(ctx)
                reply = str(voice_result.get("message") or "已关闭语音助手。")
            else:
                voice_result = self.api_voice_assistant_status(ctx)
                reply = str(voice_result.get("message") or "语音助手状态已更新。")
            result = {
                "handled": True,
                "text": text,
                "status": voice_result.get("status", "completed"),
                "reply": reply,
                "command": {
                    "action": f"voice_assistant_{voice_control_action}",
                    "label": "voice_assistant",
                    "reply": reply,
                },
                "voice_assistant": voice_result,
            }
            self.record_audit(
                "lelamp.voice_text_command",
                status_to_audit(str(result.get("status"))),
                text,
                {"handled": True, "status": result.get("status"), "command": result.get("command"), "qwen_omni_called": False},
                ctx,
            )
            return result

        command = parse_lamp_voice_command(text)
        audit_status = "blocked"
        with self._lelamp_voice_lock:
            if command is not None and command.action in {"set_rgb", "play", "default_state", "scan_pdf", "project", "relax_motors"} and self.runtime.config.enable_hardware:
                self.runtime.lelamp_voice.set_hardware(self.lelamp_voice_hardware())
            elif self._lelamp_voice_hardware is not None:
                self.runtime.lelamp_voice.set_hardware(self._lelamp_voice_hardware)
            if command is not None and command.action == "scan_pdf":
                self.runtime.lelamp_voice.set_scan_services(
                    camera_observer=self.runtime.camera_observer,
                    scanning=self.runtime.scanning,
                    workspace=self.runtime.workspace,
                    projection=self.runtime.projection,
                    preview_snapshot=self.capture_from_camera_preview_snapshot,
                )
            elif command is not None and command.action == "project":
                self.runtime.lelamp_voice.set_scan_services(
                    workspace=self.runtime.workspace,
                    projection=self.runtime.projection,
                )

            result = self.runtime.lelamp_voice.handle_text(text)

        if not bool(result.get("handled")):
            result = {
                **result,
                "status": "not_handled",
                "reply": "未识别为台灯控制命令。",
            }
        audit_status = status_to_audit(str(result.get("status") or audit_status))
        self.record_audit(
            "lelamp.voice_text_command",
            audit_status,
            text,
            {
                "handled": result.get("handled"),
                "status": result.get("status"),
                "command": result.get("command"),
            },
            ctx,
        )
        return result

    def api_hardware_test(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        test = require_string(payload, "test")
        if test == "scan":
            return self.api_hardware_scan(ctx)
        if test == "camera":
            camera_index = self.resolve_camera_index(payload.get("camera_index"))
            result = self.runtime.camera_observer.capture_frame(camera_index=camera_index)
            status = normalize_hardware_test_status(str(result.get("status") or "unavailable"))
            self.record_audit("hardware_test.camera", status_to_audit(status), f"camera:{camera_index}", result, ctx)
            return {"status": status, "test": test, "result": result}
        if test == "mic":
            seconds = safe_int(payload.get("seconds"), 2)
            rate = safe_int(payload.get("rate"), self.runtime.config.mic_rate)
            scan = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
            mic_details = hardware_device_details(scan, "mic")
            requested_device = str(payload.get("device") or "").strip()
            selected_device = str(mic_details.get("selected_device") or "").strip()
            device = requested_device or selected_device
            if not device:
                status = "backend_missing" if mic_details.get("arecord_status") == "backend_missing" else "unavailable"
                result = {
                    "status": status,
                    "message": "No ALSA capture device was detected.",
                    "configured_device": self.runtime.config.mic_device,
                    "selected_device": "",
                    "candidates": mic_details.get("candidates", []),
                }
                self.record_audit("hardware_test.mic", status, "mic", result, ctx)
                return {"status": status, "test": test, "result": result}
            output = self.runtime.workspace.path_for_new_file(f"hardware_mic_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav")
            result = record_microphone_sample(device, rate, seconds, output)
            result.setdefault("configured_device", self.runtime.config.mic_device)
            result.setdefault("selected_device", device)
            result["configured_device_valid"] = bool(mic_details.get("configured_device_valid"))
            result["candidates"] = mic_details.get("candidates", [])
            status = normalize_hardware_test_status(str(result.get("status") or "unavailable"))
            self.record_audit("hardware_test.mic", status_to_audit(status), device, result, ctx)
            return {"status": status, "test": test, "result": result}
        if test == "speaker":
            scan = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
            speaker_details = hardware_device_details(scan, "speaker")
            requested_device = str(payload.get("device") or "").strip()
            selected_device = str(speaker_details.get("selected_device") or "").strip()
            device = requested_device or selected_device
            if not device:
                status = "backend_missing" if speaker_details.get("aplay_status") == "backend_missing" else "unavailable"
                result = {
                    "status": status,
                    "message": "No ALSA playback device was detected.",
                    "configured_device": self.runtime.config.speaker_device,
                    "selected_device": "",
                    "candidates": speaker_details.get("candidates", []),
                }
                self.record_audit("hardware_test.speaker", status, "speaker", result, ctx)
                return {"status": status, "test": test, "result": result}
            output = self.runtime.workspace.path_for_new_file(f"hardware_speaker_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav")
            result = play_speaker_tone(device, output)
            result["configured_device"] = self.runtime.config.speaker_device
            result["selected_device"] = device
            result["configured_device_valid"] = bool(speaker_details.get("configured_device_valid"))
            result["candidates"] = speaker_details.get("candidates", [])
            status = normalize_hardware_test_status(str(result.get("status") or "unavailable"))
            self.record_audit("hardware_test.speaker", status_to_audit(status), device, result, ctx)
            return {"status": status, "test": test, "result": result}
        if test == "projection":
            result = self.runtime.projection.render_status_card(
                "Hardware Projection Test",
                "display_test",
                details=["Generated from /api/hardware/test", "Physical projector detection is reported by /api/hardware/scan"],
                accent="blue",
            )
            self.record_audit("hardware_test.projection", "ok", str(result.get("path")), result, ctx)
            return {"status": "completed", "test": test, "result": result}
        if test == "rgb":
            state = str(payload.get("state") or "success")
            result = self.api_lelamp_state(state, ctx)
            return {"status": result["status"], "test": test, "result": result}
        self.record_audit("hardware_test", "blocked", test, {"reason": "unknown test"}, ctx)
        raise ApiError("unknown_hardware_test", f"Unsupported hardware test: {test}", status=400)

GET={"/api/hardware/status":"api_hardware_status", "/api/hardware/scan":"api_hardware_scan", "/api/lelamp/motion/status":"api_lelamp_motion_status", "/api/camera-stream/status":"api_camera_stream_status"}
POST={"/api/hardware/test":"api_hardware_test", "/api/lelamp/motor-control/move":"api_lelamp_motor_control_move", "/api/lelamp/motor-control/save-pose":"api_lelamp_motor_control_save_pose", "/api/lelamp/voice-command":"api_lelamp_voice_command", "/api/camera-stream/start":"api_camera_stream_start"}

def dispatch_get(server:Any,path:str,params:dict[str,list[str]],ctx:Any)->Any:
    method=GET.get(path); return NOT_HANDLED if method is None else getattr(server,method)(ctx)
def dispatch_post(server:Any,path:str,payload:dict[str,Any],ctx:Any)->Any:
    if path=="/api/lelamp/state": return server.api_lelamp_state(str(payload.get("state") or ""),ctx)
    if path=="/api/lelamp/motor-control/read": return server.api_lelamp_motor_control_read(ctx)
    if path=="/api/camera-stream/stop": return server.api_camera_stream_stop(ctx)
    return exact_payload(server,path,payload,ctx,POST)
