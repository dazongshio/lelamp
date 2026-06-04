from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditLogger
from .hardware import LampHardware


@dataclass(frozen=True)
class FeedbackCue:
    rgb: tuple[int, int, int] | None = None
    recording: str | None = None


DEFAULT_FEEDBACK_CUES: dict[str, FeedbackCue] = {
    "idle": FeedbackCue(rgb=(10, 10, 10), recording="idle"),
    "idle_listen": FeedbackCue(rgb=(8, 12, 18)),
    "wake": FeedbackCue(rgb=(255, 230, 160), recording="wake_up"),
    "listening": FeedbackCue(rgb=(36, 126, 255), recording="scanning"),
    "asr": FeedbackCue(rgb=(36, 126, 255)),
    "thinking": FeedbackCue(rgb=(130, 82, 255), recording="curious"),
    "speaking": FeedbackCue(rgb=(42, 210, 92), recording="nod"),
    "follow_up": FeedbackCue(rgb=(58, 168, 255), recording="curious"),
    "reminder": FeedbackCue(rgb=(255, 210, 48), recording="wake_up"),
    "success": FeedbackCue(rgb=(42, 210, 92), recording="nod"),
    "blocked": FeedbackCue(rgb=(255, 146, 36), recording="headshake"),
    "error": FeedbackCue(rgb=(255, 48, 42), recording="shock"),
    "meeting": FeedbackCue(rgb=(255, 255, 255), recording="wake_up"),
    "projecting": FeedbackCue(rgb=(80, 180, 255), recording="idle"),
}


class AssistantFeedback:
    """Maps assistant states to optional LeLamp RGB and motion cues."""

    def __init__(
        self,
        *,
        hardware: LampHardware,
        audit: AuditLogger,
        enabled: bool,
        cues: dict[str, FeedbackCue] | None = None,
    ):
        self.hardware = hardware
        self.audit = audit
        self.enabled = enabled
        self.cues = cues or DEFAULT_FEEDBACK_CUES
        self._last_state: str | None = None
        if not enabled:
            self.audit.record(
                "assistant.feedback",
                status="skipped",
                details={"reason": "OPENCLAW_ENABLE_HARDWARE is disabled"},
            )

    def apply(self, state: str) -> None:
        if not self.enabled:
            return
        if state == self._last_state:
            return
        self._last_state = state

        cue = self.cues.get(state)
        if cue is None:
            return

        if cue.rgb is not None:
            self.hardware.set_rgb(*cue.rgb)
        if cue.recording is not None:
            self.hardware.play(cue.recording)
        self.audit.record(
            "assistant.feedback",
            target=state,
            details={
                "rgb": list(cue.rgb) if cue.rgb is not None else None,
                "recording": cue.recording,
            },
        )
