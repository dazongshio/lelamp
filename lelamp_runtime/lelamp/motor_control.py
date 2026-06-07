from __future__ import annotations

import time
from typing import Any, Mapping


LELAMP_MOTOR_ORDER = ("base_yaw", "base_pitch", "elbow_pitch", "wrist_roll", "wrist_pitch")


def ordered_motor_names(values: Mapping[str, Any]) -> list[str]:
    return [motor for motor in LELAMP_MOTOR_ORDER if motor in values]


def write_goal_position_ordered(
    bus: Any,
    goal_position: Mapping[str, float],
    *,
    delay_seconds: float = 0.0,
    num_retry: int = 2,
) -> list[str]:
    """Write Goal_Position one motor at a time in LeLamp's physical 1-5 order."""
    written: list[str] = []
    for motor in ordered_motor_names(goal_position):
        bus.write("Goal_Position", motor, float(goal_position[motor]), num_retry=num_retry)
        written.append(motor)
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return written
