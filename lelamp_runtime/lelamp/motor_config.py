from __future__ import annotations

from collections import OrderedDict

MOTOR_MODEL = "sts3215"
DEFAULT_LED_COUNT = 40

MOTOR_IDS = OrderedDict(
    [
        ("base_yaw", 1),
        ("base_pitch", 2),
        ("elbow_pitch", 3),
        ("wrist_roll", 4),
        ("wrist_pitch", 5),
    ]
)

MOTOR_ORDER = tuple(MOTOR_IDS.keys())
EXPECTED_RECORDING_COLUMNS = ("timestamp", *(f"{name}.pos" for name in MOTOR_ORDER))

DEFAULT_RECORDINGS = (
    "curious",
    "excited",
    "happy_wiggle",
    "headshake",
    "idle",
    "nod",
    "sad",
    "scanning",
    "shock",
    "shy",
    "wake_up",
)

CALIBRATION_SAFETY_NOTE = (
    "Calibration safety: do not fully rotate the base/head yaw joints. "
    "Move yaw joints about 90 degrees clockwise and 90 degrees counterclockwise "
    "from center; rotate pitch joints only within the physical safe range."
)
