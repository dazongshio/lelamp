from __future__ import annotations

from .meeting_commands import MeetingCommandsMixin
from .meeting_registration import MeetingRegistrationMixin
from .meeting_asr import MeetingAsrMixin


class MeetingPipelineMixin(MeetingCommandsMixin, MeetingRegistrationMixin, MeetingAsrMixin):
    """Compatibility aggregate for the domain-specific mixins."""

