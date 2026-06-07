from __future__ import annotations

import csv
import json
from contextlib import AbstractContextManager
from pathlib import Path
import time
from typing import Any

from lelamp.motor_control import ordered_motor_names, write_goal_position_ordered

from .audit import AuditLogger


class LampHardware(AbstractContextManager["LampHardware"]):
    """Optional adapter around LeLamp motor and RGB services.

    Imports are intentionally lazy so the office agent can run on non-Raspberry
    Pi development machines without rpi_ws281x installed.
    """

    def __init__(self, *, enabled: bool, port: str, lamp_id: str, audit: AuditLogger, rgb_enabled: bool = False):
        self.enabled = enabled
        self.rgb_enabled = rgb_enabled
        self.port = port
        self.lamp_id = lamp_id
        self.audit = audit
        self.animation_service: Any | None = None
        self.rgb_service: Any | None = None
        self._animation_error: str | None = None
        self._rgb_error: str | None = None

    def __enter__(self) -> "LampHardware":
        if not self.enabled:
            self.audit.record("hardware.start", status="skipped", details={"reason": "disabled"})
            return self

        self.audit.record("hardware.start", status="ready", details={"port": self.port, "lamp_id": self.lamp_id})
        return self

    def ensure_motion(self) -> bool:
        if not self.enabled:
            return False
        if self.animation_service is not None:
            return True
        try:
            from lelamp.service.motors.animation_service import AnimationService

            self.animation_service = AnimationService(
                port=self.port,
                lamp_id=self.lamp_id,
                fps=30,
                duration=2.0,
                idle_recording="idle",
            )
            self.animation_service.start()
            self.audit.record("hardware.motion.start", details={"port": self.port, "lamp_id": self.lamp_id})
            return True
        except Exception as exc:
            self.animation_service = None
            self._animation_error = str(exc)
            self.audit.record("hardware.motion.start", status="error", details={"error": str(exc)})
            return False

    def ensure_rgb(self) -> bool:
        if not self.enabled:
            return False
        if not self.rgb_enabled:
            self._rgb_error = "OPENCLAW_ENABLE_RGB is disabled"
            self.audit.record("hardware.rgb.start", status="skipped", details={"reason": self._rgb_error})
            return False
        if self.rgb_service is not None:
            return True
        try:
            from lelamp.service.rgb.rgb_service import RGBService

            self.rgb_service = RGBService(
                led_count=40,
                led_pin=12,
                led_freq_hz=800000,
                led_dma=10,
                led_brightness=255,
                led_invert=False,
                led_channel=0,
            )
            self.rgb_service.start()
            self.audit.record("hardware.rgb.start")
            return True
        except Exception as exc:
            self.rgb_service = None
            self._rgb_error = str(exc)
            self.audit.record("hardware.rgb.start", status="error", details={"error": str(exc)})
            return False

    def release_motion(self) -> None:
        if self.animation_service is None:
            return
        try:
            self.animation_service.stop()
        finally:
            self.animation_service = None
        self.audit.record("hardware.motion.release")

    def interrupt_motion(self) -> None:
        if self.animation_service is None:
            return
        if hasattr(self.animation_service, "interrupt"):
            self.animation_service.interrupt()
            self.audit.record("hardware.motion.interrupt")

    def hold_recording_final_pose(
        self,
        recording_name: str,
        *,
        max_step: float = 2.0,
        step_seconds: float = 0.04,
        tolerance: float = 0.7,
    ) -> str:
        result = self.hold_recording_final_pose_result(
            recording_name,
            max_step=max_step,
            step_seconds=step_seconds,
            tolerance=tolerance,
        )
        if bool(result.get("reached")):
            return f"Holding lamp pose from recording: {recording_name}"
        status = str(result.get("status") or "")
        if status in {"disabled", "error"}:
            return "Hardware is disabled or unavailable."
        if status == "missing_recording_pose":
            return "Recording pose is unavailable."
        return "Recording pose was not reached."

    def hold_recording_final_pose_result(
        self,
        recording_name: str,
        *,
        max_step: float = 2.0,
        step_seconds: float = 0.04,
        tolerance: float = 0.7,
        stable_seconds: float = 0.35,
        max_iterations: int = 160,
    ) -> dict[str, object]:
        if not self.enabled:
            return {
                "status": "disabled",
                "reached": False,
                "recording": recording_name,
                "reason": "Hardware is disabled or unavailable.",
            }
        self.release_motion()
        target = self._recording_final_pose(recording_name)
        if not target:
            payload = {
                "status": "missing_recording_pose",
                "reached": False,
                "recording": recording_name,
                "reason": "recording pose unavailable",
            }
            self.audit.record("hardware.motion.hold", status="skipped", target=recording_name, details=payload)
            return payload
        return self.hold_pose_result(
            target,
            pose_name=f"recording:{recording_name}",
            max_step=max_step,
            step_seconds=step_seconds,
            tolerance=tolerance,
            stable_seconds=stable_seconds,
            max_iterations=max_iterations,
        )

    def hold_pose_file_result(
        self,
        path: Path,
        *,
        pose_name: str | None = None,
        max_step: float = 2.0,
        step_seconds: float = 0.04,
        tolerance: float = 2.0,
        stable_seconds: float = 0.45,
        max_iterations: int = 160,
    ) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            motors = payload.get("motors", payload) if isinstance(payload, dict) else {}
            target = {str(key): float(value) for key, value in motors.items()}
        except Exception as exc:
            result = {
                "status": "missing_pose_file",
                "reached": False,
                "pose_file": str(path),
                "pose_name": pose_name or path.stem,
                "error": str(exc)[:500],
            }
            self.audit.record("hardware.motion.hold", status="skipped", target=str(path), details=result)
            return result
        return self.hold_pose_result(
            target,
            pose_name=pose_name or path.stem,
            pose_file=str(path),
            max_step=max_step,
            step_seconds=step_seconds,
            tolerance=tolerance,
            stable_seconds=stable_seconds,
            max_iterations=max_iterations,
        )

    def hold_pose_result(
        self,
        target: dict[str, float],
        *,
        pose_name: str,
        pose_file: str = "",
        max_step: float = 2.0,
        step_seconds: float = 0.04,
        tolerance: float = 2.0,
        stable_seconds: float = 0.45,
        max_iterations: int = 160,
    ) -> dict[str, object]:
        if not self.enabled:
            return {
                "status": "disabled",
                "reached": False,
                "pose_name": pose_name,
                "pose_file": pose_file,
                "reason": "Hardware is disabled or unavailable.",
            }
        try:
            from lelamp.person_tracker import TRACKING_MOTORS, connect_motor_bus, read_current_pose
            import argparse

            bus = self._connect_motor_bus_with_retry(connect_motor_bus, argparse.Namespace(port=self.port, id=self.lamp_id), attempts=4, sleep_seconds=0.35)
            try:
                current = read_current_pose(bus)
                motors = ordered_motor_names(target)
                bus.enable_torque(list(motors), num_retry=5)
                self._configure_pose_motion_profile(bus, motors)
                trace_count = 0
                verification_rounds: list[dict[str, object]] = []
                command_wait = max(step_seconds, min(0.18, max(0.04, max_step * 0.012)))
                max_delta = max((abs(float(target[motor]) - float(current.get(motor, target[motor]))) for motor in motors), default=0.0)
                max_rounds = max(1, min(max_iterations, max(12, int(max_delta / max(0.1, max_step)) + 16)))
                command_pose = {motor: float(current.get(motor, target[motor])) for motor in motors}
                command_lead_limit = max(max_step * 5.0, max_step + tolerance)
                for round_index in range(max_rounds):
                    if all(abs(float(target[motor]) - float(current.get(motor, target[motor]))) <= tolerance for motor in motors):
                        break
                    command = {}
                    for motor in motors:
                        current_value = float(command_pose[motor])
                        actual_value = float(current.get(motor, target[motor]))
                        target_value = float(target[motor])
                        delta = target_value - current_value
                        command[motor] = target_value if abs(delta) <= max_step else current_value + (max_step if delta > 0 else -max_step)
                        lead = command[motor] - actual_value
                        if abs(lead) > command_lead_limit:
                            command[motor] = actual_value + (command_lead_limit if lead > 0 else -command_lead_limit)
                    write_goal_position_ordered(bus, command, delay_seconds=step_seconds)
                    command_pose.update(command)
                    trace_count += 1
                    time.sleep(command_wait)
                    current = read_current_pose(bus)
                    verification_rounds.append(
                        {
                            "round": round_index + 1,
                            "commanded": {motor: round(float(command[motor]), 3) for motor in motors},
                            "actual": {motor: round(float(current.get(motor, target[motor])), 3) for motor in motors},
                            "errors": {
                                motor: round(abs(float(target[motor]) - float(current.get(motor, target[motor]))), 3)
                                for motor in motors
                            },
                        }
                    )
                if stable_seconds > 0:
                    time.sleep(stable_seconds)
                    current = read_current_pose(bus)
            finally:
                bus.disconnect(disable_torque=False)
            max_error = self._max_pose_error(current, target, motors)
            reached = max_error <= tolerance
            errors = {
                motor: round(abs(float(target[motor]) - float(current.get(motor, target[motor]))), 3)
                for motor in motors
            }
            payload = {
                "status": "reached" if reached else "not_reached",
                "reached": reached,
                "pose_name": pose_name,
                "pose_file": pose_file,
                "steps": trace_count,
                "target": {motor: round(float(target[motor]), 3) for motor in motors},
                "actual": {motor: round(float(current.get(motor, target[motor])), 3) for motor in motors},
                "errors": errors,
                "worst_motor": max(errors, key=errors.get) if errors else "",
                "max_error": round(max_error, 3),
                "tolerance": tolerance,
                "stable_seconds": stable_seconds,
                "verification_rounds": verification_rounds,
            }
            self.audit.record("hardware.motion.hold", status="ready" if reached else "blocked", target=pose_name, details=payload)
            return payload
        except Exception as exc:
            self._animation_error = str(exc)
            payload = {
                "status": "error",
                "reached": False,
                "pose_name": pose_name,
                "pose_file": pose_file,
                "error": str(exc),
            }
            self.audit.record("hardware.motion.hold", status="error", target=pose_name, details=payload)
            return payload

    def relax_motors_result(self) -> dict[str, object]:
        if not self.enabled:
            return {
                "status": "disabled",
                "reason": "Hardware is disabled or unavailable.",
            }
        self.release_motion()
        try:
            from lelamp.person_tracker import TRACKING_MOTORS, connect_motor_bus, read_current_pose
            import argparse

            bus = self._connect_motor_bus_with_retry(
                connect_motor_bus,
                argparse.Namespace(port=self.port, id=self.lamp_id),
                attempts=4,
                sleep_seconds=0.35,
            )
            try:
                current = read_current_pose(bus)
                bus.disable_torque(list(TRACKING_MOTORS), num_retry=5)
            finally:
                bus.disconnect(disable_torque=False)
            payload = {
                "status": "torque_disabled",
                "motors": list(TRACKING_MOTORS),
                "current": {
                    motor: round(float(current[motor]), 3)
                    for motor in TRACKING_MOTORS
                    if motor in current
                },
            }
            self.audit.record("hardware.motion.relax", status="ready", target=self.lamp_id, details=payload)
            return payload
        except Exception as exc:
            payload = {
                "status": "error",
                "error": str(exc),
            }
            self.audit.record("hardware.motion.relax", status="error", target=self.lamp_id, details=payload)
            return payload

    def _connect_motor_bus_with_retry(self, connect_motor_bus: Any, args: Any, *, attempts: int, sleep_seconds: float) -> Any:
        last_exc: Exception | None = None
        for attempt in range(max(1, attempts)):
            try:
                return connect_motor_bus(args, max_step=None)
            except Exception as exc:
                last_exc = exc
                self.audit.record(
                    "hardware.motion.connect_retry",
                    status="error",
                    details={"attempt": attempt + 1, "attempts": attempts, "error": str(exc)[:500]},
                )
                time.sleep(sleep_seconds)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Motor bus connection failed.")

    def _configure_pose_motion_profile(self, bus: Any, motors: list[str]) -> None:
        settings = (
            ("Torque_Limit", 1000),
            ("Goal_Time", 500),
            ("Goal_Velocity", 1200),
            ("Acceleration", 20),
        )
        for motor in motors:
            for data_name, value in settings:
                try:
                    bus.write(data_name, motor, value, normalize=False, num_retry=2)
                except Exception as exc:
                    self.audit.record(
                        "hardware.motion.profile",
                        status="skipped",
                        target=motor,
                        details={"data_name": data_name, "value": value, "error": str(exc)[:300]},
                    )

    def _max_pose_error(self, current: dict[str, float], target: dict[str, float], motors: list[str]) -> float:
        return max((abs(float(target[motor]) - float(current.get(motor, target[motor]))) for motor in motors), default=0.0)

    def _recording_final_pose(self, recording_name: str) -> dict[str, float]:
        path = Path(__file__).resolve().parents[1] / "recordings" / f"{recording_name}.csv"
        try:
            with path.open("r", encoding="utf-8", newline="") as stream:
                last_row: dict[str, str] | None = None
                for row in csv.DictReader(stream):
                    last_row = row
        except OSError:
            return {}
        if not last_row:
            return {}
        pose: dict[str, float] = {}
        for key, value in last_row.items():
            if key and key.endswith(".pos"):
                pose[key.removesuffix(".pos")] = float(value)
        return pose

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.animation_service is not None:
            self.animation_service.stop()
        if self.rgb_service is not None:
            self.rgb_service.stop()
        if self.enabled:
            self.audit.record("hardware.stop")

    def play(self, recording_name: str) -> str:
        if self.animation_service is None:
            self.ensure_motion()
        if self.animation_service is None:
            self.audit.record(
                "hardware.play",
                status="skipped",
                target=recording_name,
                details={"reason": "hardware unavailable", "error": self._animation_error},
            )
            return "Hardware is disabled or unavailable."
        self.interrupt_motion()
        self.animation_service.dispatch("play", recording_name)
        self.audit.record("hardware.play", target=recording_name)
        return f"Playing lamp recording: {recording_name}"

    def set_rgb(self, red: int, green: int, blue: int) -> str:
        if self.rgb_service is None:
            self.ensure_rgb()
        if self.rgb_service is None:
            self.audit.record(
                "hardware.rgb",
                status="skipped",
                details={"reason": "hardware unavailable", "rgb": [red, green, blue], "error": self._rgb_error},
            )
            return "Hardware is disabled or unavailable."
        self.rgb_service.dispatch("solid", (red, green, blue))
        self.audit.record("hardware.rgb", details={"rgb": [red, green, blue]})
        return f"Set lamp RGB to ({red}, {green}, {blue})."
