from __future__ import annotations

from .meeting_session import MeetingSessionRoutesMixin
from .meeting_content import MeetingContentRoutesMixin
from .meeting_workflow import MeetingWorkflowRoutesMixin
from typing import Any

from ._base import NOT_HANDLED, exact_payload


class MeetingRoutesMixin(MeetingSessionRoutesMixin, MeetingContentRoutesMixin, MeetingWorkflowRoutesMixin):
    """Compatibility aggregate for the domain-specific mixins."""


GET = {
    "/api/meeting/status": "api_meeting_status",
    "/api/meeting/local-realtime/status": "api_meeting_local_realtime_status",
    "/api/meeting/provider/status": "api_meeting_provider_status",
    "/api/meeting/jobs": "api_meeting_jobs",
}

POST = {
    "/api/meeting/voice-command": "api_meeting_voice_command",
    "/api/meeting/local-realtime/turn": "api_meeting_local_realtime_turn",
    "/api/meeting/local-realtime/export": "api_meeting_local_realtime_export",
    "/api/meeting/mode/enable": "api_meeting_mode_enable",
    "/api/meeting/mode/disable": "api_meeting_mode_disable",
    "/api/meeting/import-transcript": "api_meeting_import_transcript",
    "/api/meeting/import-text": "api_meeting_import_text",
    "/api/meeting/provider/preflight": "api_meeting_provider_preflight",
    "/api/meeting/realtime/start": "api_meeting_realtime_start",
    "/api/meeting/realtime/stop": "api_meeting_realtime_stop",
    "/api/meeting/realtime/highlight": "api_meeting_realtime_highlight",
    "/api/meeting/realtime/share-clip": "api_meeting_realtime_share_clip",
    "/api/meeting/realtime/fetch-minutes": "api_meeting_realtime_fetch_minutes",
    "/api/meeting/realtime/ask": "api_meeting_realtime_ask",
    "/api/meeting/realtime/transcript-update": "api_meeting_realtime_transcript_update",
    "/api/meeting/realtime/transcript": "api_meeting_realtime_transcript_update",
    "/api/meeting/minutes": "api_meeting_minutes",
    "/api/meeting/followup": "api_meeting_followup",
    "/api/meeting/export-package": "api_meeting_export_package",
    "/api/meeting/send-email": "api_meeting_send_email",
    "/api/meeting/reminders": "api_meeting_reminders",
    "/api/meeting/projection-confirmation": "api_meeting_projection_confirmation",
    "/api/meeting/confirm-step": "api_meeting_confirm_step",
}


def dispatch_get(server: Any, path: str, params: dict[str, list[str]], ctx: Any) -> Any:
    if path in GET:
        return getattr(server, GET[path])(ctx)
    meeting_id = params.get("meeting_id", [""])[0]
    if path == "/api/meeting/realtime/status": return server.api_meeting_realtime_status(meeting_id, ctx)
    if path == "/api/meeting/realtime/events": return server.api_meeting_realtime_events(meeting_id, ctx)
    if path == "/api/meeting/realtime/audio": return server.api_meeting_realtime_audio(meeting_id, ctx)
    if path == "/api/meeting/realtime/export": return server.api_meeting_realtime_export(meeting_id, params.get("format", ["markdown"])[0], ctx)
    if path == "/api/meeting/realtime/insights": return server.api_meeting_realtime_insights(meeting_id, ctx)
    if path == "/api/meeting/shared-clip": return server.api_meeting_shared_clip(params.get("token", [""])[0], ctx)
    if path.startswith("/api/meeting/jobs/"): return server.api_meeting_job(path.rsplit("/", 1)[-1], ctx)
    return NOT_HANDLED


def dispatch_post(server: Any, path: str, payload: dict[str, Any], ctx: Any) -> Any:
    if path in {"/api/meeting/decisions", "/api/meeting/action-items"}:
        return server.api_meeting_extract_step(path.rsplit("/", 1)[-1], payload, ctx)
    if path.startswith("/api/meeting/extract/"):
        return server.api_meeting_extract_step(path.rsplit("/", 1)[-1], payload, ctx)
    return exact_payload(server, path, payload, ctx, POST)
