from __future__ import annotations

import threading
from typing import Any

from .config import OfficeAgentConfig
from .hardware import LampHardware
from .hardware_probe import probe_hardware
from .lelamp_experience import LELAMP_STATE_CUES
from .runtime import OfficeRuntime, build_runtime


SAFE_RECORDINGS = frozenset(cue.recording for cue in LELAMP_STATE_CUES.values())


class MHSCommandError(ValueError):
    """A device request that was rejected before touching hardware."""


class LeLampMHSAdapter:
    """MHS-ready, model-neutral façade around LeLamp's physical capabilities.

    The public MHS SDK is not required: this layer exposes machine-readable
    states, procedures and safety constraints and is transported over MCP.
    """

    def __init__(self, runtime: OfficeRuntime | None = None):
        self.runtime = runtime or build_runtime()
        self.config: OfficeAgentConfig = self.runtime.config
        self._hardware = LampHardware(
            enabled=self.config.enable_hardware,
            port=self.config.hardware_port,
            lamp_id=self.config.lamp_id,
            audit=self.runtime.audit,
            rgb_enabled=self.config.enable_rgb,
        )
        self._lock = threading.RLock()

    def describe(self) -> dict[str, Any]:
        return {
            "schema": "mhs-ready/0.1",
            "official_mhs_compliance": False,
            "device": {
                "id": self.config.lamp_id,
                "type": "desktop_robotic_lamp",
                "manufacturer": "LeLamp",
                "gateway": "local-python-mcp",
            },
            "states": ["hardware_status", "assistant_state", "sensor_status"],
            "procedures": [
                "read_status",
                "set_expression",
                "play_safe_motion",
                "observe_camera_once",
                "emergency_stop",
            ],
            "safety": {
                "hardware_writes_default": "disabled",
                "hardware_enable_env": "OPENCLAW_ENABLE_HARDWARE=1",
                "rgb_enable_env": "OPENCLAW_ENABLE_RGB=1",
                "write_confirmation_required": True,
                "motion_allowlist": sorted(SAFE_RECORDINGS),
                "raw_motor_register_access": False,
                "raw_serial_access": False,
                "camera_requires_confirmation": True,
                "audit_log": str(self.config.audit_log_path),
            },
        }

    def read_status(self) -> dict[str, Any]:
        payload = probe_hardware(self.config, projection_preview_port=8765)
        self.runtime.audit.record("mhs.read_status", details={"hardware_enabled": self.config.enable_hardware})
        return payload

    def set_expression(self, state: str, *, confirmed: bool = False) -> dict[str, Any]:
        self._require_confirmation(confirmed)
        if state not in LELAMP_STATE_CUES:
            raise MHSCommandError(f"Unsupported state: {state}")
        cue = LELAMP_STATE_CUES[state]
        with self._lock:
            motion = self._hardware.play(cue.recording)
            rgb = self._hardware.set_rgb(*cue.rgb)
        result = {"status": "executed" if self.config.enable_hardware else "dry_run", "state": state, "motion": motion, "rgb": rgb}
        self.runtime.audit.record("mhs.set_expression", target=state, details=result)
        return result

    def play_safe_motion(self, recording: str, *, confirmed: bool = False) -> dict[str, Any]:
        self._require_confirmation(confirmed)
        if recording not in SAFE_RECORDINGS:
            raise MHSCommandError(f"Motion is not allowlisted: {recording}")
        with self._lock:
            message = self._hardware.play(recording)
        result = {"status": "executed" if self.config.enable_hardware else "dry_run", "recording": recording, "message": message}
        self.runtime.audit.record("mhs.play_safe_motion", target=recording, details=result)
        return result

    def observe_camera_once(self, *, camera_index: int = 0, rotation_degrees: int = 0, confirmed: bool = False) -> dict[str, Any]:
        self._require_confirmation(confirmed)
        if camera_index not in (0, 1):
            raise MHSCommandError("camera_index must be 0 or 1")
        if rotation_degrees not in (0, 90, 180, 270):
            raise MHSCommandError("rotation_degrees must be 0, 90, 180 or 270")
        result = self.runtime.camera_observer.observe_once(camera_index=camera_index, rotation_degrees=rotation_degrees)
        self.runtime.audit.record("mhs.observe_camera", target=str(camera_index), details={"rotation_degrees": rotation_degrees})
        return result

    def emergency_stop(self) -> dict[str, Any]:
        with self._lock:
            self._hardware.interrupt_motion()
            result = self._hardware.relax_motors_result()
        self.runtime.audit.record("mhs.emergency_stop", status="ready", details=result)
        return result

    @staticmethod
    def _require_confirmation(confirmed: bool) -> None:
        if confirmed is not True:
            raise MHSCommandError("This physical or privacy-sensitive action requires confirmed=true")

