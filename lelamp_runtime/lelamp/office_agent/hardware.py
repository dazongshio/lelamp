from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

from .audit import AuditLogger


class LampHardware(AbstractContextManager["LampHardware"]):
    """Optional adapter around LeLamp motor and RGB services.

    Imports are intentionally lazy so the office agent can run on non-Raspberry
    Pi development machines without rpi_ws281x installed.
    """

    def __init__(self, *, enabled: bool, port: str, lamp_id: str, audit: AuditLogger):
        self.enabled = enabled
        self.port = port
        self.lamp_id = lamp_id
        self.audit = audit
        self.animation_service: Any | None = None
        self.rgb_service: Any | None = None

    def __enter__(self) -> "LampHardware":
        if not self.enabled:
            self.audit.record("hardware.start", status="skipped", details={"reason": "disabled"})
            return self

        try:
            from lelamp.service.motors.animation_service import AnimationService
            from lelamp.service.rgb.rgb_service import RGBService

            self.animation_service = AnimationService(
                port=self.port,
                lamp_id=self.lamp_id,
                fps=30,
                duration=2.0,
                idle_recording="idle",
            )
            self.rgb_service = RGBService(
                led_count=40,
                led_pin=12,
                led_freq_hz=800000,
                led_dma=10,
                led_brightness=255,
                led_invert=False,
                led_channel=0,
            )
            self.animation_service.start()
            self.rgb_service.start()
            self.audit.record("hardware.start", details={"port": self.port, "lamp_id": self.lamp_id})
        except Exception as exc:
            self.animation_service = None
            self.rgb_service = None
            self.audit.record("hardware.start", status="error", details={"error": str(exc)})
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.animation_service is not None:
            self.animation_service.stop()
        if self.rgb_service is not None:
            self.rgb_service.stop()
        if self.enabled:
            self.audit.record("hardware.stop")

    def play(self, recording_name: str) -> str:
        if self.animation_service is None:
            self.audit.record(
                "hardware.play",
                status="skipped",
                target=recording_name,
                details={"reason": "hardware unavailable"},
            )
            return "Hardware is disabled or unavailable."
        self.animation_service.dispatch("play", recording_name)
        self.audit.record("hardware.play", target=recording_name)
        return f"Playing lamp recording: {recording_name}"

    def set_rgb(self, red: int, green: int, blue: int) -> str:
        if self.rgb_service is None:
            self.audit.record(
                "hardware.rgb",
                status="skipped",
                details={"reason": "hardware unavailable", "rgb": [red, green, blue]},
            )
            return "Hardware is disabled or unavailable."
        self.rgb_service.dispatch("solid", (red, green, blue))
        self.audit.record("hardware.rgb", details={"rgb": [red, green, blue]})
        return f"Set lamp RGB to ({red}, {green}, {blue})."
