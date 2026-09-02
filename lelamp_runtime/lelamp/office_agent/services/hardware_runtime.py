from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Any

from lelamp.motion_config import get_named_pose, load_motion_config
from lelamp.motor_control import LELAMP_MOTOR_ORDER, ordered_motor_names, write_goal_position_ordered

from ..hardware import LampHardware
from ..utils import safe_filename


def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)


def round_motor_map(*args, **kwargs): return _helper("round_motor_map")(*args, **kwargs)
def safe_int(*args, **kwargs): return _helper("safe_int")(*args, **kwargs)
def serial_device_candidates(*args, **kwargs): return _helper("serial_device_candidates")(*args, **kwargs)
LELAMP_CONTROL_MOTORS = LELAMP_MOTOR_ORDER


class HardwareRuntimeMixin:
    def lelamp_motion_preflight(self, *, read_pose: bool) -> dict[str, object]:
        port = str(self.runtime.config.hardware_port or "/dev/ttyACM0")
        candidates = serial_device_candidates()
        port_exists = Path(port).exists()
        serial_detected = port_exists or bool(candidates)
        result: dict[str, object] = {
            "status": "available" if serial_detected else "needs_hardware",
            "hardware_enabled": self.runtime.config.enable_hardware,
            "port": port,
            "lamp_id": self.runtime.config.lamp_id,
            "serial_detected": serial_detected,
            "serial_candidates": candidates,
            "configured_port_exists": port_exists,
            "pose_readable": False,
            "pose": {},
            "safety": [
                "姿态预检只读电机当前位置，不写入目标位置。",
                "转动观察必须由页面按钮显式授权触发。",
                "全方位扫描只在显式授权后小幅调整 base_yaw 和 base_pitch，并尝试回到起始姿态。",
            ],
        }
        if not serial_detected:
            result["message"] = "未检测到 LeLamp 串口；请确认电源和 USB 控制线已连接。"
            return result
        if not self.runtime.config.enable_hardware:
            result["status"] = "adapter_ready"
            result["message"] = "已检测到串口，但 OPENCLAW_ENABLE_HARDWARE 未启用；只允许只读预检，不允许网页驱动电机。"
        if read_pose:
            pose_result = self.read_lelamp_pose(port=port)
            result["pose_status"] = pose_result.get("status")
            result["pose_readable"] = bool(pose_result.get("pose_readable"))
            result["pose"] = pose_result.get("pose", {})
            result["pose_error"] = pose_result.get("error", "")
            result["read_duration_ms"] = pose_result.get("duration_ms")
            if pose_result.get("pose_readable") and self.runtime.config.enable_hardware:
                result["status"] = "available"
                result["message"] = "LeLamp 串口和电机姿态读取正常。"
            elif pose_result.get("pose_readable"):
                result.setdefault("message", "LeLamp 姿态读取正常；启用 OPENCLAW_ENABLE_HARDWARE=1 后才允许转动。")
            else:
                result["status"] = "unavailable"
                result["message"] = "检测到串口，但无法读取电机姿态。请检查电机供电、舵机总线和校准。"
        return result

    def read_lelamp_pose(self, *, port: str) -> dict[str, object]:
        started = time.monotonic()
        try:
            from lelamp.person_tracker import read_current_pose

            bus = self.connect_lelamp_motor_bus(port=port, max_step=None)
            try:
                pose = read_current_pose(bus)
            finally:
                bus.disconnect(disable_torque=False)
            return {
                "status": "completed",
                "pose_readable": True,
                "pose": round_motor_map(pose, LELAMP_CONTROL_MOTORS),
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }
        except Exception as exc:
            return {
                "status": "failed",
                "pose_readable": False,
                "pose": {},
                "error": str(exc)[:1000],
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }

    def read_saved_lelamp_pose(self, pose_name: str) -> dict[str, float]:
        config_pose_names = {
            "lelamp_center": "default",
            "lelamp_scan": "scan",
            "lelamp_projection": "projection",
        }
        configured = get_named_pose(load_motion_config(self.motion_config_path()), config_pose_names.get(pose_name, pose_name))
        if configured:
            return round_motor_map(configured, LELAMP_CONTROL_MOTORS)
        pose_path = self.runtime.config.workspace_dir / ".poses" / f"{safe_filename(pose_name)}.json"
        try:
            payload = json.loads(pose_path.read_text(encoding="utf-8"))
            motors = payload.get("motors", payload) if isinstance(payload, dict) else {}
            return round_motor_map(motors, LELAMP_CONTROL_MOTORS)
        except Exception:
            return {}

    def motion_config_path(self) -> Path:
        configured = os.getenv("LELAMP_MOTION_CONFIG", "").strip()
        if configured:
            return Path(configured).expanduser()
        return self.runtime.config.workspace_dir / "lelamp_motion_config.json"

    def connect_lelamp_motor_bus(self, *, port: str, max_step: float | None):
        from lelamp.person_tracker import connect_motor_bus

        args = argparse.Namespace(port=port, id=self.runtime.config.lamp_id)
        return connect_motor_bus(args, max_step=max_step)

    def move_lelamp_pose_in_steps(
        self,
        bus: Any,
        target_pose: dict[str, float],
        *,
        motors: list[str],
        max_step: float,
        step_seconds: float,
        tolerance: float = 0.7,
        max_iterations: int | None = None,
    ) -> tuple[dict[str, float], list[dict[str, object]]]:
        from lelamp.person_tracker import read_current_pose

        current_pose = read_current_pose(bus)
        motors = ordered_motor_names({motor: target_pose[motor] for motor in motors if motor in target_pose})
        if not motors:
            return current_pose, []
        try:
            bus.enable_torque(list(motors), num_retry=5)
        except Exception:
            pass
        max_delta = max((abs(float(target_pose[motor]) - float(current_pose[motor])) for motor in motors), default=0.0)
        iteration_limit = max(1, safe_int(max_iterations, 0)) if max_iterations is not None else max(1, min(240, math.ceil(max_delta / max(0.1, max_step)) + 12))
        effective_step_seconds = max(step_seconds, min(0.35, max(0.08, max_step * 0.03)))
        command_pose = {motor: float(current_pose[motor]) for motor in motors}
        command_lead_limit = max(max_step * 5.0, max_step + tolerance)
        trace: list[dict[str, object]] = []

        for step_index in range(iteration_limit):
            if all(abs(float(target_pose[motor]) - float(current_pose[motor])) <= tolerance for motor in motors):
                break
            next_pose: dict[str, float] = {}
            for motor in motors:
                current_value = float(command_pose[motor])
                actual_value = float(current_pose[motor])
                target_value = float(target_pose[motor])
                delta = target_value - current_value
                if abs(delta) <= max_step:
                    next_pose[motor] = target_value
                else:
                    next_pose[motor] = current_value + (max_step if delta > 0 else -max_step)
                lead = next_pose[motor] - actual_value
                if abs(lead) > command_lead_limit:
                    next_pose[motor] = actual_value + (command_lead_limit if lead > 0 else -command_lead_limit)

            write_goal_position_ordered(bus, next_pose)
            command_pose.update(next_pose)
            time.sleep(effective_step_seconds)
            current_pose = read_current_pose(bus)
            trace.append(
                {
                    "step": step_index + 1,
                    "target": {motor: round(float(target_pose[motor]), 3) for motor in motors},
                    "commanded": {motor: round(float(next_pose[motor]), 3) for motor in motors},
                    "actual": {motor: round(float(current_pose[motor]), 3) for motor in motors},
                }
            )

        for verification_round in range(2):
            pending = [
                motor
                for motor in motors
                if abs(float(target_pose[motor]) - float(current_pose[motor])) > tolerance
            ]
            if not pending:
                break
            for motor in pending:
                current_value = float(current_pose[motor])
                target_value = float(target_pose[motor])
                delta = target_value - current_value
                command_value = target_value if abs(delta) <= max_step else current_value + (max_step if delta > 0 else -max_step)
                write_goal_position_ordered(bus, {motor: command_value})
                time.sleep(effective_step_seconds)
                current_pose = read_current_pose(bus)
                trace.append(
                    {
                        "step": len(trace) + 1,
                        "verification_round": verification_round + 1,
                        "motor": motor,
                        "target": {motor: round(target_value, 3)},
                        "commanded": {motor: round(command_value, 3)},
                        "actual": {name: round(float(current_pose[name]), 3) for name in motors},
                    }
                )

        return current_pose, trace

    def lelamp_voice_hardware(self) -> LampHardware:
        if self._lelamp_voice_hardware is None:
            hardware = LampHardware(
                enabled=self.runtime.config.enable_hardware,
                port=self.runtime.config.hardware_port,
                lamp_id=self.runtime.config.lamp_id,
                audit=self.runtime.audit,
                rgb_enabled=self.runtime.config.enable_rgb,
            )
            hardware.__enter__()
            self._lelamp_voice_hardware = hardware
        return self._lelamp_voice_hardware

    def stop_lelamp_voice_hardware(self) -> None:
        with self._lelamp_voice_lock:
            hardware = self._lelamp_voice_hardware
            self._lelamp_voice_hardware = None
            self.runtime.lelamp_voice.set_hardware(None)
            if hardware is not None:
                hardware.__exit__(None, None, None)
