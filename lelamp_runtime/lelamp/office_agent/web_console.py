from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

from lelamp.motor_control import LELAMP_MOTOR_ORDER, ordered_motor_names

from .document_workspace import DocumentWorkspace
from .auth import ConsoleAuth
from .hardware import LampHardware
from .hardware_probe import play_speaker_tone, probe_hardware, record_microphone_sample
from .lelamp_voice_skill import parse_lamp_voice_command
from .runtime import OfficeRuntime
from .routes import ROUTE_GROUPS
from .routes._base import ApiError, NOT_HANDLED, RequestContext
from .routes.documents import DocumentsRoutesMixin
from .routes.meeting import MeetingRoutesMixin
from .routes.hardware import HardwareRoutesMixin
from .routes.assistant import AssistantRoutesMixin
from .routes.projection import ProjectionRoutesMixin
from .routes.scene import SceneRoutesMixin
from .routes.desktop import DesktopRoutesMixin
from .routes.shared import SharedRoutesMixin
from .routes.tasks import TaskAuditRoutesMixin
from .routes.security import SecurityRoutesMixin
from .routes.system import SystemRoutesMixin
from .services import (
    AssistantRuntimeMixin,
    ConsoleTestRuntimeMixin,
    HardwareRuntimeMixin,
    LocalSystemRuntimeMixin,
    MediaRuntimeMixin,
    MeetingPipelineMixin,
    ProcessManager,
    ProjectionCatalogMixin,
    RemoteDesktopRuntimeMixin,
    ScanRuntimeMixin,
    SceneCaptureMixin,
    StartupRuntimeMixin,
    TaskStoreMixin,
)
from .shared_space import SharedSpaceService, find_lan_ip
from .scene import SCENE_WORKFLOW_VERSION
from .tingwu_meeting import (
    TingwuMeetingProvider,
)
from .web_helpers import (
    atomic_write_json,
    runtime_root,
    quote_env_value,
    update_local_env_value,
    pid_alive,
    process_cmdline,
    normalize_voice_assistant_text,
    parse_system_audio_voice_command,
    parse_voice_assistant_control_command,
    atomic_write_text_file,
    atomic_write_bytes,
    document_collaborator_color,
    humanize_result_title,
    scan_result_markdown,
    desktop_full_control_evidence,
    desktop_full_control_remediation,
    parse_json_body,
    endpoint_matches,
    is_real_tingwu_microphone,
    capture_probe_matches_selected_microphone,
    tingwu_live_acceptance_commands,
    tingwu_provider_preflight_next_actions,
    tingwu_provider_acceptance_checklist,
    require_string,
    list_string,
    shared_file_matches_type,
    require_file_path,
    default_ssh_key_path,
    is_private_ssh_host,
    parse_safe_ssh_command,
    remote_codex_bootstrap_script,
    wiki_title_from_content,
    wiki_excerpt_from_content,
    strip_markdown_inline,
    is_text_workflow_path,
    document_adapter_status_from_runtime,
    safe_int,
    safe_float,
    payload_bool,
    optional_float,
    clamp_number,
    round_motor_map,
    split_wave_channels,
    workspace_name_for_path,
    scan_offsets_from_payload,
    scan_view_plan_from_payload,
    serial_device_candidates,
    compact_scene_snapshot,
    infer_ambient_lux_from_scene_event,
    now_iso,
    sanitize_id,
    format_bytes,
    redact_target,
    redact_provider_url,
    assistant_route_is_chat,
    assistant_high_risk_policy,
    local_chat_reply,
    assistant_ack_for_route,
    normalize_task_status,
    normalize_hardware_test_status,
    dedupe_scene_events,
    hardware_device_details,
    _module_available,
    server_tts_status,
    synthesize_and_play_on_server,
    status_to_audit,
    tingwu_capture_status,
    tingwu_realtime_task_summary,
    normalize_result_status,
    collect_outputs,
    summarize_manual_result,
    manual_result_details,
    format_weather_answer,
    document_result_payload,
    summarize_dict,
    first_output_path,
    extract_email_subject,
    compact_meeting_step_output,
    meeting_step_understanding,
    meeting_step_result,
    dedupe_events,
    first_nonempty_line,
    audit_event_dto,
    csv_escape,
    parse_datetime,
    read_system_sensors,
    read_recent_audit,
    render_error_page,
)
from .utils import safe_filename


CONSOLE_SAFE_TEST_IDS = (
    "security",
    "skills",
    "readiness",
    "p0_status",
    "shared_note",
    "workspace_blocked_read",
    "document_analysis",
    "meeting_followup",
    "projection_status",
    "projection_countdown",
    "projection_action",
    "projection_calibration",
    "scan_register",
    "ocr_text_summary",
    "desktop_audit_only",
    "desktop_full_control_gate",
    "desktop_task_queue",
    "browser_automation_status",
    "desktop_companion",
    "lelamp_state",
    "environment_event",
    "hardware_status",
    "smart_home_status",
    "smart_home_control_guard",
    "xiaoai_utility",
    "xiaoai_features",
    "intent_router",
    "local_file_search",
    "daily_reminder",
    "mobile_bridge",
    "voice_stack_status",
    "audit_recent",
)

TEST_PNG_BYTES = bytes.fromhex(
    "89504E470D0A1A0A0000000D4948445200000001000000010804000000B51C0C02"
    "0000000B4944415478DA63FCFF1F0003030200EFBFA7DB0000000049454E44AE426082"
)

WEB_CONSOLE_DIST = Path(__file__).resolve().parents[3] / "dist"
MAX_TASK_EVENTS = 200
LELAMP_CONTROL_MOTORS = LELAMP_MOTOR_ORDER
OFFICIAL_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
OFFICIAL_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
QWEN_OMNI_VOICE_DOC_URL = "https://help.aliyun.com/zh/model-studio/omni-voice-list"




@dataclass(frozen=True)
class SafePath:
    path: Path
    workspace_name: str


class WebConsoleServer(
    DocumentsRoutesMixin,
    MeetingRoutesMixin,
    HardwareRoutesMixin,
    AssistantRoutesMixin,
    ProjectionRoutesMixin,
    SceneRoutesMixin,
    DesktopRoutesMixin,
    SharedRoutesMixin,
    TaskAuditRoutesMixin,
    SecurityRoutesMixin,
    SystemRoutesMixin,
    TaskStoreMixin,
    MeetingPipelineMixin,
    MediaRuntimeMixin,
    HardwareRuntimeMixin,
    SceneCaptureMixin,
    AssistantRuntimeMixin,
    RemoteDesktopRuntimeMixin,
    LocalSystemRuntimeMixin,
    ConsoleTestRuntimeMixin,
    StartupRuntimeMixin,
    ScanRuntimeMixin,
    ProjectionCatalogMixin,
):
    """Browser control console for the Raspberry Pi OpenClaw runtime."""

    def __init__(
        self,
        runtime: OfficeRuntime,
        *,
        token: str | None = None,
        max_upload_bytes: int = 50 * 1024 * 1024,
        projection_preview_port: int = 8765,
    ):
        self.runtime = runtime
        self.audit = runtime.audit
        self.shared_space = SharedSpaceService(runtime.workspace, runtime.audit)
        self.token = token or os.getenv("LELAMP_WEB_TOKEN", "").strip() or secrets.token_urlsafe(18)
        self.auth = ConsoleAuth(self.token)
        self.max_upload_bytes = max(1, max_upload_bytes)
        self.projection_preview_port = projection_preview_port
        self.started_at = time.time()
        self.processes = ProcessManager()
        self._task_lock = threading.Lock()
        self._desktop_companion_lock = threading.RLock()
        self._desktop_companion_stop = threading.Event()
        self._desktop_companion_thread: threading.Thread | None = None
        self._desktop_companion_started_at: float | None = None
        self._desktop_companion_last_run: dict[str, object] | None = None
        self._voice_conversation_lock = threading.RLock()
        self._voice_conversations: dict[str, dict[str, object]] = {}
        self._voice_assistant_lock = threading.RLock()
        self._voice_assistant_process: subprocess.Popen[bytes] | None = None
        self._voice_assistant_started_at: float | None = None
        self._lelamp_voice_lock = threading.RLock()
        self._lelamp_voice_hardware: LampHardware | None = None
        self._projection_preview_lock = threading.RLock()
        self._projection_preview_httpd: ThreadingHTTPServer | None = None
        self._projection_preview_thread: threading.Thread | None = None
        self._projection_preview_started_at: float | None = None
        self._projection_preview_url = f"http://{find_lan_ip() or '127.0.0.1'}:{self.projection_preview_port}/"
        self._projection_kiosk_process: subprocess.Popen[bytes] | None = None
        self._projection_kiosk_started_at: float | None = None
        self._projection_kiosk_log_path = self.runtime.config.workspace_dir / ".projection_kiosk.log"
        self.camera_stream_port = safe_int(os.getenv("LELAMP_CAMERA_STREAM_PORT", "8788"), 8788)
        self._camera_stream_lock = threading.RLock()
        self._camera_stream_process: subprocess.Popen[bytes] | None = None
        self._camera_stream_started_at: float | None = None
        self._camera_stream_camera_index: int | None = None
        self._camera_stream_url = os.getenv("LELAMP_CAMERA_STREAM_URL", f"http://127.0.0.1:{self.camera_stream_port}").rstrip("/") + "/"
        self.tingwu = TingwuMeetingProvider(runtime.config, runtime.workspace, runtime.audit)
        self.documents_workspace = DocumentWorkspace(runtime.config.workspace_dir)
        self._document_result_sync_lock = threading.Lock()
        self._document_result_sync_completed = False
        self._assistant_notification_lock = threading.Lock()
        self._assistant_notifications = self.load_assistant_notifications()

    def make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/health":
                    self._send_api(server.api_health())
                    return
                if parsed.path.startswith("/assets/") and server._send_dist_asset(self, parsed.path):
                    return
                if parsed.path == "/api/meeting/shared-clip":
                    try:
                        params = urllib.parse.parse_qs(parsed.query)
                        self._send_media(server.api_meeting_shared_clip(params.get("share", [""])[0], server._request_context(self)))
                    except ApiError as exc:
                        self._send_api_error(exc)
                    return
                # The React shell must load before a browser can collect and store
                # the console token. Data, downloads, and mutations remain behind
                # the authorization check below.
                if server._is_frontend_route(parsed.path):
                    self._send_html(
                        server._render_react_console()
                        or render_error_page("前端未构建", "请先在项目目录运行 npm run build。")
                    )
                    return
                ctx = server._request_context(self)
                if not server._authorized(parsed, self.headers, self.command):
                    server.record_audit(
                        "web_console.request",
                        "blocked",
                        parsed.path,
                        {"reason": "unauthorized"},
                        ctx,
                    )
                    if parsed.path.startswith("/api/"):
                        self._send_api_error(ApiError("unauthorized", "Token is missing or invalid.", status=401))
                    else:
                        self._send_html(render_error_page("Unauthorized", "Use the console URL printed by the Pi."), status=401)
                    return

                try:
                    if parsed.path == "/api/shared/download":
                        params = urllib.parse.parse_qs(parsed.query)
                        self._send_download(server.api_shared_download(params.get("file", [""])[0], ctx))
                        return
                    if parsed.path == "/api/shared/file":
                        params = urllib.parse.parse_qs(parsed.query)
                        self._send_file(server.api_shared_download(params.get("file", [""])[0], ctx))
                        return
                    if parsed.path == "/api/workspace/download":
                        params = urllib.parse.parse_qs(parsed.query)
                        self._send_download(server.api_workspace_file(params.get("file", [""])[0], ctx, action="workspace.download"))
                        return
                    if parsed.path == "/api/workspace/file":
                        params = urllib.parse.parse_qs(parsed.query)
                        self._send_file(server.api_workspace_file(params.get("file", [""])[0], ctx, action="workspace.file"))
                        return
                    if parsed.path == "/api/scene/image":
                        params = urllib.parse.parse_qs(parsed.query)
                        self._send_file(server.api_scene_image(params.get("file", [""])[0], ctx))
                        return
                    if parsed.path == "/api/meeting/realtime/audio":
                        params = urllib.parse.parse_qs(parsed.query)
                        self._send_media(server.api_meeting_realtime_audio(params.get("meeting_id", [""])[0], ctx))
                        return
                    if parsed.path == "/api/meeting/realtime/export":
                        params = urllib.parse.parse_qs(parsed.query)
                        self._send_download(server.api_meeting_realtime_export(
                            params.get("meeting_id", [""])[0], params.get("format", ["txt"])[0], ctx
                        ))
                        return
                    if parsed.path == "/api/audit/export.csv":
                        self._send_csv(server.api_audit_export(parsed.query, ctx))
                        return
                    if parsed.path == "/api/audit/export-signed":
                        self._send_download(server.api_audit_export_signed(parsed.query, ctx))
                        return
                    self._send_api(server.handle_get(parsed.path, parsed.query, ctx))
                except ApiError as exc:
                    self._send_api_error(exc)
                except Exception as exc:  # Keep the console usable and auditable on backend failures.
                    server.record_audit("web_console.error", "error", parsed.path, {"error": str(exc)[:1000]}, ctx)
                    self._send_api_error(ApiError("server_error", "Backend error.", status=500, details={"error": str(exc)[:1000]}))
                return

            def do_POST(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                ctx = server._request_context(self)
                if not server._authorized(parsed, self.headers, self.command):
                    server.record_audit(
                        "web_console.request",
                        "blocked",
                        parsed.path,
                        {"reason": "unauthorized"},
                        ctx,
                    )
                    self._send_api_error(ApiError("unauthorized", "Token is missing or invalid.", status=401))
                    return
                if not server._trusted_write_origin(self.headers, ctx):
                    server.record_audit(
                        "web_console.request",
                        "blocked",
                        parsed.path,
                        {"reason": "cross_origin_write"},
                        ctx,
                    )
                    self._send_api_error(ApiError("cross_origin_write_blocked", "已阻止跨站写入请求。", status=403))
                    return
                try:
                    result = server.handle_post(parsed.path, self.headers.get("Content-Type", ""), self._read_body(), ctx)
                except ApiError as exc:
                    self._send_api_error(exc)
                    return
                except ValueError as exc:
                    self._send_api_error(ApiError("bad_request", str(exc), status=400))
                    return
                except Exception as exc:  # Keep UI from receiving HTML stack traces.
                    server.record_audit("web_console.error", "error", parsed.path, {"error": str(exc)[:1000]}, ctx)
                    self._send_api_error(ApiError("server_error", "Backend error.", status=500, details={"error": str(exc)[:1000]}))
                    return
                self._send_api(result)

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def _read_body(self) -> bytes:
                length_header = self.headers.get("Content-Length", "0")
                try:
                    content_length = int(length_header)
                except ValueError:
                    content_length = 0
                if content_length <= 0:
                    return b""
                if content_length > server.max_upload_bytes:
                    raise ValueError("Request body is too large.")
                return self.rfile.read(content_length)

            def _send_html(self, content: str, *, status: int = 200) -> None:
                encoded = content.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _send_json(self, payload: object, *, status: int = 200) -> None:
                encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _send_api(self, payload: object, *, status: int = 200) -> None:
                self._send_json({"ok": True, "data": payload}, status=status)

            def _send_api_error(self, error: ApiError) -> None:
                self._send_json(
                    {
                        "ok": False,
                        "error": {
                            "code": error.code,
                            "message": error.message,
                            "details": error.details,
                        },
                    },
                    status=error.status,
                )

            def _send_download(self, path: Path) -> None:
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header(
                    "Content-Disposition",
                    f"attachment; filename*=UTF-8''{urllib.parse.quote(path.name)}",
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_file(self, path: Path) -> None:
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_media(self, path: Path) -> None:
                size = path.stat().st_size
                start, end = 0, max(0, size - 1)
                range_header = str(self.headers.get("Range") or "")
                status = 200
                if range_header.startswith("bytes="):
                    match = re.fullmatch(r"(\d*)-(\d*)", range_header[6:].strip())
                    if match:
                        if match.group(1):
                            start = min(int(match.group(1)), size)
                        if match.group(2):
                            end = min(int(match.group(2)), max(0, size - 1))
                        if start > end or start >= size:
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{size}")
                            self.end_headers()
                            return
                        status = 206
                length = max(0, end - start + 1)
                self.send_response(status)
                self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "audio/wav")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("Content-Length", str(length))
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                with path.open("rb") as stream:
                    stream.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = stream.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)

            def _send_csv(self, payload: tuple[str, bytes]) -> None:
                filename, data = payload
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}",
                )
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler

    def serve(self, *, host: str, port: int) -> None:
        httpd = ThreadingHTTPServer((host, port), self.make_handler())
        bound_host, bound_port = httpd.server_address[:2]
        lan_ip = find_lan_ip()
        url_host = lan_ip if bound_host in {"0.0.0.0", ""} and lan_ip else bound_host
        # Never put the console credential in terminal output, service logs, or
        # audit records. The browser keeps an explicitly supplied token in local
        # storage and sends it through the Authorization header.
        local_url = f"http://{url_host}:{bound_port}/"
        public_url = self.public_console_url(bound_port)
        self.audit.record(
            "web_console.start",
            target=public_url or local_url,
            details={
                "workspace_dir": str(self.runtime.config.workspace_dir),
                "shared_inbox_dir": str(self.shared_space.inbox_dir),
                "max_upload_bytes": self.max_upload_bytes,
                "local_url": local_url,
                "public_url": public_url,
            },
        )
        projection_preview = self.start_projection_preview_service()
        self.audit.record(
            "projection_start",
            status=status_to_audit(str(projection_preview.get("status") or "adapter_ready")),
            target="projection_preview_service",
            details=projection_preview,
        )
        startup_home = self.startup_home_pose()
        print(f"OpenClaw web console: {local_url}")
        if public_url:
            print(f"Fixed console URL: {public_url}")
        print(f"Projection preview: {projection_preview.get('preview_url', self._projection_preview_url)}")
        print(f"LeLamp startup home: {startup_home.get('status')} - {startup_home.get('message')}")
        if bound_host == "0.0.0.0":
            print(f"LAN URL: http://{lan_ip or '<raspberry-pi-ip>'}:{bound_port}/")
        print(f"Shared inbox: {self.shared_space.inbox_dir}")
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_lelamp_voice_hardware()
            self.stop_projection_preview_service()
            httpd.server_close()
            self.audit.record("web_console.stop", target=local_url)

    def public_console_url(self, bound_port: int) -> str:
        configured = os.getenv("LELAMP_PUBLIC_URL", "").strip()
        if not configured:
            return ""
        parsed = urllib.parse.urlparse(configured if "://" in configured else f"http://{configured}")
        scheme = parsed.scheme or "http"
        netloc = parsed.netloc or parsed.path
        path = parsed.path if parsed.netloc else ""
        if ":" not in netloc:
            netloc = f"{netloc}:{bound_port}"
        # Strip any configured token parameter before logging or auditing the
        # public address. Credentials belong in request headers, not URLs.
        query = urllib.parse.urlencode(
            [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query) if key.lower() != "token"]
        )
        return urllib.parse.urlunparse((scheme, netloc, path or "/", "", query, ""))


    def _is_frontend_route(self, path: str) -> bool:
        return path in {
            "/",
            "/index.html",
            "/dashboard",
            "/shared",
            "/assistant",
            "/meeting",
            "/documents",
            "/scan",
            "/results",
            "/pot",
            "/wiki",
            "/checklist",
            "/projection",
            "/desktop",
            "/remote",
            "/validation",
            "/scene",
            "/mobile",
            "/smart-home",
            "/voice",
            "/motors",
            "/hardware",
            "/audit",
            "/settings",
        }

    def _render_react_console(self, *, include_token: bool = False) -> str | None:
        index_path = WEB_CONSOLE_DIST / "index.html"
        if not index_path.exists():
            return None
        return index_path.read_text(encoding="utf-8")

    def _send_dist_asset(self, handler: BaseHTTPRequestHandler, request_path: str) -> bool:
        if not WEB_CONSOLE_DIST.exists():
            return False
        relative = request_path.lstrip("/")
        asset_path = (WEB_CONSOLE_DIST / relative).resolve()
        try:
            asset_path.relative_to(WEB_CONSOLE_DIST.resolve())
        except ValueError:
            return False
        if not asset_path.is_file():
            return False
        data = asset_path.read_bytes()
        content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return True

    def _request_context(self, handler: BaseHTTPRequestHandler) -> RequestContext:
        source_ip = handler.client_address[0] if handler.client_address else ""
        forwarded_host = str(handler.headers.get("X-Forwarded-Host") or "").split(",", 1)[0].strip()
        host = forwarded_host or str(handler.headers.get("Host") or "").strip()
        forwarded_proto = str(handler.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
        session = self._document_session_payload(
            str(handler.headers.get("X-LeLamp-Document-Session") or "")
            or urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query).get("document_session", [""])[0]
        )
        return RequestContext(
            request_id=uuid4().hex,
            actor=str(session.get("actor_id") or "lelamp-web") if session else "lelamp-web",
            source_ip=source_ip,
            actor_name=str(session.get("display_name") or "协作者") if session else "本机用户",
            host=host,
            forwarded_proto=forwarded_proto,
        )

    def _authorized(self, parsed: urllib.parse.ParseResult, headers, method: str = "GET") -> bool:
        return self._auth_service().authorized(parsed, headers, method)

    def _auth_service(self) -> ConsoleAuth:
        auth = getattr(self, "auth", None)
        if auth is None or auth.token != self.token:
            auth = ConsoleAuth(self.token)
            self.auth = auth
        return auth

    def _document_session_payload(self, token: str) -> dict[str, object] | None:
        return self._auth_service().document_session_payload(token)

    def _trusted_write_origin(self, headers, ctx: RequestContext) -> bool:
        return self._auth_service().trusted_write_origin(headers, ctx.host)

    def record_audit(
        self,
        action: str,
        status: str,
        target: str | None,
        details: dict[str, Any] | None,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        return self.audit.record(
            action,
            status=status,
            target=target,
            details=details or {},
            actor=ctx.actor,
            request_id=ctx.request_id,
            source_ip=ctx.source_ip,
            permission_mode=self.runtime.config.permission_mode.value,
            desktop_backend=self.runtime.config.desktop_backend,
        )

    def handle_get(self, path: str, query: str, ctx: RequestContext) -> dict[str, object]:
        params = urllib.parse.parse_qs(query)
        for route_group in ROUTE_GROUPS:
            result = route_group.dispatch_get(self, path, params, ctx)
            if result is not NOT_HANDLED:
                return result
        raise ApiError("not_found", f"Unknown API path: {path}", status=404)

    def handle_post(self, path: str, content_type: str, body: bytes, ctx: RequestContext) -> dict[str, object]:
        if path == "/api/shared/upload":
            return self.api_shared_upload(content_type, body, ctx)
        if path == "/api/meeting/import-media":
            return self.api_meeting_import_media(content_type, body, ctx)
        payload = parse_json_body(body)
        for route_group in ROUTE_GROUPS:
            result = route_group.dispatch_post(self, path, payload, ctx)
            if result is not NOT_HANDLED:
                return result
        raise ApiError("not_found", f"Unknown API path: {path}", status=404)
