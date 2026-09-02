from __future__ import annotations

from .task_lifecycle import TaskLifecycleMixin
from .meeting_outputs import MeetingOutputsMixin
from .meeting_jobs import MeetingJobsMixin


class TaskStoreMixin(TaskLifecycleMixin, MeetingOutputsMixin, MeetingJobsMixin):
    """Compatibility aggregate for the domain-specific mixins."""

