from __future__ import annotations

import os
import time

from lelamp.motion_config import get_named_pose, load_motion_config

from ..utils import safe_filename


def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)


def clamp_number(*args, **kwargs): return _helper("clamp_number")(*args, **kwargs)
def optional_float(*args, **kwargs): return _helper("optional_float")(*args, **kwargs)
def safe_int(*args, **kwargs): return _helper("safe_int")(*args, **kwargs)
def status_to_audit(*args, **kwargs): return _helper("status_to_audit")(*args, **kwargs)


class StartupRuntimeMixin:
    def startup_home_pose(self) -> dict[str, object]:
        if os.getenv("LELAMP_STARTUP_HOME", "1").lower() in {"0", "false", "no", "off"}:
            result = {
                "status": "disabled",
                "message": "LELAMP_STARTUP_HOME is disabled.",
            }
            self.audit.record("lelamp_startup_home", status="disabled", target="lelamp_center", details=result)
            return result
        if not self.runtime.config.enable_hardware:
            result = {
                "status": "adapter_ready",
                "message": "Hardware writes are disabled; startup home was skipped.",
            }
            self.audit.record("lelamp_startup_home", status="adapter_ready", target="lelamp_center", details=result)
            return result

        pose_name = os.getenv("LELAMP_STARTUP_HOME_POSE", "lelamp_center").strip() or "lelamp_center"
        pose_path = self.runtime.config.workspace_dir / ".poses" / f"{safe_filename(pose_name)}.json"
        configured_default_pose = get_named_pose(load_motion_config(self.motion_config_path()), "default")
        if not configured_default_pose and not pose_path.exists():
            result = {
                "status": "missing",
                "message": f"Startup home pose is missing in motion config and pose file: {pose_path}",
                "pose_path": str(pose_path),
                "motion_config_path": str(self.motion_config_path()),
            }
            self.audit.record("lelamp_startup_home", status="missing", target=str(pose_path), details=result)
            return result

        started = time.monotonic()
        bus = None
        try:
            from lelamp.person_tracker import TRACKING_MOTORS, load_pose, read_current_pose

            max_step = clamp_number(optional_float(os.getenv("LELAMP_STARTUP_HOME_MAX_STEP")), default=4.0, low=0.5, high=8.0)
            tolerance = clamp_number(optional_float(os.getenv("LELAMP_STARTUP_HOME_TOLERANCE")), default=0.5, low=0.3, high=5.0)
            step_seconds = clamp_number(optional_float(os.getenv("LELAMP_STARTUP_HOME_SLEEP")), default=0.12, low=0.02, high=0.4)
            configured_iterations = safe_int(os.getenv("LELAMP_STARTUP_HOME_STEPS"), 0)
            max_iterations = max(1, min(240, configured_iterations)) if configured_iterations > 0 else None
            port = str(self.runtime.config.hardware_port or "/dev/ttyACM0")
            target_pose = configured_default_pose or load_pose(pose_path)
            bus = self.connect_lelamp_motor_bus(port=port, max_step=None)
            initial_pose = read_current_pose(bus)
            actual_pose, movement_trace = self.move_lelamp_pose_in_steps(
                bus,
                target_pose,
                motors=list(TRACKING_MOTORS),
                max_step=max_step,
                step_seconds=step_seconds,
                tolerance=tolerance,
                max_iterations=max_iterations,
            )
            errors = {
                motor: round(abs(float(target_pose[motor]) - float(actual_pose[motor])), 4)
                for motor in TRACKING_MOTORS
            }
            reached = all(value <= tolerance for value in errors.values())
            status = "completed" if reached else "timeout"
            result = {
                "status": status,
                "message": "LeLamp moved to startup home pose." if reached else "LeLamp startup home stopped before every axis reached tolerance.",
                "pose_name": pose_name,
                "pose_path": str(pose_path),
                "port": port,
                "lamp_id": self.runtime.config.lamp_id,
                "motors": list(TRACKING_MOTORS),
                "primary_lift_motor": "base_pitch",
                "tolerance": tolerance,
                "max_step": max_step,
                "steps": len(movement_trace),
                "initial_pose": initial_pose,
                "target_pose": target_pose,
                "actual_pose": actual_pose,
                "errors": errors,
                "worst_motor": max(errors, key=errors.get) if errors else "",
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }
            self.audit.record(
                "lelamp_startup_home",
                status=status_to_audit(status),
                target=pose_name,
                details={key: value for key, value in result.items() if key != "movement_trace"},
            )
            return result
        except Exception as exc:
            result = {
                "status": "failed",
                "message": f"LeLamp startup home failed: {str(exc)[:300]}",
                "pose_name": pose_name,
                "pose_path": str(pose_path),
                "error": str(exc)[:1000],
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }
            self.audit.record("lelamp_startup_home", status="error", target=pose_name, details=result)
            return result
        finally:
            if bus is not None:
                try:
                    bus.disconnect(disable_torque=False)
                except Exception:
                    pass

