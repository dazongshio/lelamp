from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from ._base import NOT_HANDLED, RequestContext, exact_payload

def _helper(name:str):
    from .. import web_console
    return getattr(web_console,name)
def clamp_number(*a,**kw): return _helper("clamp_number")(*a,**kw)
def optional_float(*a,**kw): return _helper("optional_float")(*a,**kw)
def probe_hardware(*a,**kw): return _helper("probe_hardware")(*a,**kw)
def safe_int(*a,**kw): return _helper("safe_int")(*a,**kw)
def server_tts_status(*a,**kw): return _helper("server_tts_status")(*a,**kw)
def status_to_audit(*a,**kw): return _helper("status_to_audit")(*a,**kw)

class SecurityRoutesMixin:
    def api_health(self) -> dict[str, object]:
        return {
            "status": "ok",
            "server": "web_console",
            "uptime_seconds": int(time.time() - self.started_at),
            "token_required": bool(self.token),
        }

    def api_services_status(self) -> dict[str, object]:
        projection_count = len(list(self.runtime.config.projection_dir.glob("*.md"))) if self.runtime.config.projection_dir.exists() else 0
        browser_status = self.runtime.browser_automation.status(check_launch=False)
        hardware = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
        devices = hardware.get("devices") if isinstance(hardware.get("devices"), dict) else {}
        projection = devices.get("projection") if isinstance(devices.get("projection"), dict) else {}
        return {
            "services": [
                {"name": "web server", "status": "online", "uptime": f"{int(time.time() - self.started_at)}s", "details": {"type": "ThreadingHTTPServer"}},
                {"name": "OpenClaw core", "status": "online", "details": {"permission_mode": self.runtime.config.permission_mode.value}},
                {"name": "LeLamp core", "status": "adapter_ready" if not self.runtime.config.enable_hardware else "online", "details": {"hardware_enabled": self.runtime.config.enable_hardware}},
                {"name": "File Watcher", "status": "backend_missing", "details": {"note": "No live watcher service is connected; shared files are listed on request."}},
                {"name": "Audit Logger", "status": "online", "details": {"path": str(self.runtime.config.audit_log_path)}},
                {"name": "Assistant Engine", "status": "online", "details": {"router": "OfficeIntentRouter"}},
                {"name": "Camera Preview", "status": self.api_camera_stream_status()["status"], "details": self.api_camera_stream_status()},
                {
                    "name": "Server Speaker Playback",
                    "status": "adapter_ready",
                    "details": {
                        "mode": "server_tts_to_alsa",
                        "playback_mode": "server_side_only",
                        "note": "Assistant speech is synthesized and played only on the Raspberry Pi/server-connected ALSA speaker.",
                    },
                },
                {
                    "name": "Server TTS",
                    "status": server_tts_status(self.runtime.config),
                    "details": {
                        "provider": self.runtime.config.tts_provider,
                        "model": self.runtime.config.tts_model,
                        "voice": self.runtime.config.tts_voice,
                        "speaker_output": "server_side_only",
                    },
                },
                {"name": "Realtime Voice Assistant", "status": self.voice_assistant_process_status()["status"], "details": self.voice_assistant_process_status()},
                {
                    "name": "Projection Service",
                    "status": "adapter_ready",
                    "details": {
                        "projection_cards": projection_count,
                        "physical_projector": projection.get("details", {}).get("projector_connected", False) if isinstance(projection.get("details"), dict) else False,
                        "projector_output": projection.get("details", {}).get("projector_output", "") if isinstance(projection.get("details"), dict) else "",
                    },
                },
                {"name": "Projection Hardware", "status": projection.get("status", "adapter_ready"), "details": projection},
                {"name": "Browser Automation", "status": browser_status["status"], "details": browser_status},
                {"name": "Hardware Monitor", "status": "adapter_ready" if not self.runtime.config.enable_hardware else "online", "details": {"polling": "on_request"}},
            ]
        }

    def api_security(self, ctx: RequestContext | None = None) -> dict[str, object]:
        security = self.runtime.security_status()
        security["console_token_required"] = bool(self.token)
        security["token_required"] = bool(self.token)
        security["full_control_enabled"] = self.runtime.config.permission_mode.value == "full_control"
        security["projection_preview_url"] = self._projection_preview_url
        security["cloud_ai_enabled"] = self.runtime.config.cloud_ai_enabled
        security["enterprise_policy_status"] = self.runtime.enterprise.status()
        if ctx:
            self.record_audit("security.status", "ok", "web_console", {}, ctx)
        return security

    def api_enterprise_policy_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        status = self.runtime.enterprise.status()
        if ctx:
            self.record_audit("enterprise_policy.status", status_to_audit(str(status.get("status"))), "enterprise_policy", {"cloud_ai_enabled": status.get("cloud_ai_enabled")}, ctx)
        return status

    def api_enterprise_local_platform_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        status = self.runtime.enterprise.local_platform_status()
        if ctx:
            self.record_audit("enterprise_local_platform.status", status_to_audit(str(status.get("status"))), "enterprise_platform", {"status": status.get("status")}, ctx)
        return status

    def api_enterprise_local_platform_build(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        include_samples = bool(payload.get("include_samples", True))
        result = self.runtime.enterprise.build_local_platform_bundle(include_samples=include_samples)
        task = self.create_task("企业本地算力与数据平台包", "enterprise", "completed", {"include_samples": include_samples}, result)
        self.record_audit("enterprise_local_platform.build", "ok", str(result.get("bundle_path")), {"task_id": task["task_id"]}, ctx)
        return {"task_id": task["task_id"], **result}

    def api_full_control_request(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        purpose = str(payload.get("purpose") or "").strip()
        if len(purpose) < 10:
            raise ApiError("invalid_purpose", "Purpose must be at least 10 characters.", status=400)
        result = {"status": "waiting_confirmation", "step": 1, "request_id": uuid4().hex, "message": "full_control request recorded; backend mode change is not automatic."}
        self.record_audit("full_control_request", "ok", "full_control", {"purpose": purpose, "request_id": result["request_id"]}, ctx)
        return result

    def api_full_control_confirm(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        step = safe_int(payload.get("step"), 1)
        result = {
            "status": "backend_missing" if step >= 3 else "waiting_confirmation",
            "step": step,
            "message": "Runtime permission mode cannot be changed by this UI process; set OPENCLAW_PERMISSION_MODE=full_control and restart after admin approval.",
            "full_control_enabled": self.runtime.config.permission_mode.value == "full_control",
        }
        self.record_audit("full_control_confirm", status_to_audit(str(result["status"])), "full_control", result, ctx)
        return result

    def api_full_control_cancel(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        result = {"status": "blocked", "message": "full_control request cancelled."}
        self.record_audit("full_control_cancel", "blocked", "full_control", result, ctx)
        return result

    def api_audio_settings_get(self, ctx: RequestContext) -> dict[str, object]:
        result = self._read_system_audio()
        self.record_audit("audio_settings_read", "ok", "default_audio_sink", result, ctx)
        return result

    def api_audio_settings_update(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        volume_value = optional_float(payload.get("volume"))
        muted_value = payload.get("muted")
        if volume_value is None and not isinstance(muted_value, bool):
            raise ApiError("invalid_audio_settings", "请提供音量或静音状态。", status=400)

        backend = self._audio_backend()
        if volume_value is not None:
            volume = int(round(clamp_number(volume_value, default=60.0, low=0.0, high=100.0)))
            command = (
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{volume}%"]
                if backend == "wpctl"
                else ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{volume}%"]
            )
            self._run_audio_command(command)
        if isinstance(muted_value, bool):
            command = (
                ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if muted_value else "0"]
                if backend == "wpctl"
                else ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if muted_value else "0"]
            )
            self._run_audio_command(command)

        result = self._read_system_audio()
        self.record_audit("audio_settings_update", "ok", "default_audio_sink", result, ctx)
        return result

GET={"/api/security":"api_security", "/api/security/enterprise-policy":"api_enterprise_policy_status", "/api/enterprise/local-platform/status":"api_enterprise_local_platform_status", "/api/settings/audio":"api_audio_settings_get"}
POST={"/api/enterprise/local-platform/build":"api_enterprise_local_platform_build", "/api/settings/full-control/request":"api_full_control_request", "/api/settings/full-control/confirm":"api_full_control_confirm", "/api/settings/full-control/cancel":"api_full_control_cancel", "/api/settings/audio":"api_audio_settings_update"}
def dispatch_get(server:Any,path:str,params:dict[str,list[str]],ctx:Any)->Any:
    if path in {"/api/health","/api/services/status"}: return server.api_health() if path.endswith("health") else server.api_services_status()
    method=GET.get(path); return NOT_HANDLED if method is None else getattr(server,method)(ctx)
def dispatch_post(server:Any,path:str,payload:dict[str,Any],ctx:Any)->Any:
    return exact_payload(server,path,payload,ctx,POST)
