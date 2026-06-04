from __future__ import annotations

import argparse
import hmac
import html
import importlib.util
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import socket
import smtplib
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

from .desktop_companion import DesktopCompanionService
from .desktop_automation import parse_browser_step
from .documents import DOCUMENT_WORKFLOW_SUFFIXES, DocumentExtractionError
from .audio_api import AudioAPIError, OpenAIAudioAPI
from .dashscope_tts import DashScopeTTS, DashScopeTTSError
from .elevenlabs_tts import ElevenLabsError, ElevenLabsTTS
from .hardware_probe import play_audio_file, play_speaker_tone, probe_hardware, record_microphone_sample
from .llm import LLMError, ResponsesLLM, ResponsesLLMConfig
from .projection import redact_projection_text
from .projection_viewer import ProjectionPreviewServer, build_display_profile, latest_projection_file, markdown_to_html, save_display_profile
from .product_checklist import build_product_checklist
from .runtime import OfficeRuntime
from .scene import SCENE_WORKFLOW_VERSION
from .shared_space import SharedSpaceService, find_lan_ip
from .target_validation import build_target_validation_report, run_target_validation
from .config import tingwu_credential_next_actions
from .tingwu_meeting import (
    PLACEHOLDER_CAPTURE_DEVICES,
    TingwuMeetingError,
    TingwuMeetingProvider,
    normalize_minutes_payload,
    preflight_arecord_capture,
    redact_sensitive_text,
    sanitize_event_payload,
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
OFFICIAL_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
OFFICIAL_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


def atomic_write_json(path: Path, payload: object) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    atomic_write_bytes(path, data.encode("utf-8"))


def atomic_write_text_file(path: Path, content: str) -> None:
    atomic_write_bytes(path, content.encode("utf-8"))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


class ApiError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    actor: str
    source_ip: str
    host: str = ""
    forwarded_proto: str = ""


@dataclass(frozen=True)
class SafePath:
    path: Path
    workspace_name: str


class WebConsoleServer:
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
        self.max_upload_bytes = max(1, max_upload_bytes)
        self.projection_preview_port = projection_preview_port
        self.started_at = time.time()
        self._task_lock = threading.Lock()
        self._desktop_companion_lock = threading.RLock()
        self._desktop_companion_stop = threading.Event()
        self._desktop_companion_thread: threading.Thread | None = None
        self._desktop_companion_started_at: float | None = None
        self._desktop_companion_last_run: dict[str, object] | None = None
        self._voice_conversation_lock = threading.RLock()
        self._voice_conversations: dict[str, dict[str, object]] = {}
        self._projection_preview_lock = threading.RLock()
        self._projection_preview_httpd: ThreadingHTTPServer | None = None
        self._projection_preview_thread: threading.Thread | None = None
        self._projection_preview_started_at: float | None = None
        self._projection_preview_url = f"http://127.0.0.1:{self.projection_preview_port}/"
        self.camera_stream_port = safe_int(os.getenv("LELAMP_CAMERA_STREAM_PORT", "8788"), 8788)
        self._camera_stream_lock = threading.RLock()
        self._camera_stream_process: subprocess.Popen[bytes] | None = None
        self._camera_stream_started_at: float | None = None
        self._camera_stream_camera_index: int | None = None
        self._camera_stream_url = os.getenv("LELAMP_CAMERA_STREAM_URL", f"http://127.0.0.1:{self.camera_stream_port}").rstrip("/") + "/"
        self.tingwu = TingwuMeetingProvider(runtime.config, runtime.workspace, runtime.audit)
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
                ctx = server._request_context(self)
                if not server._authorized(parsed, self.headers):
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

                if server._is_frontend_route(parsed.path):
                    self._send_html(server._render_react_console() or render_console_page(server.token))
                    return
                try:
                    if parsed.path == "/api/shared/download":
                        params = urllib.parse.parse_qs(parsed.query)
                        self._send_download(server.api_shared_download(params.get("file", [""])[0], ctx))
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
                if not server._authorized(parsed, self.headers):
                    server.record_audit(
                        "web_console.request",
                        "blocked",
                        parsed.path,
                        {"reason": "unauthorized"},
                        ctx,
                    )
                    self._send_api_error(ApiError("unauthorized", "Token is missing or invalid.", status=401))
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
        url_host = "127.0.0.1" if bound_host in {"0.0.0.0", ""} else bound_host
        local_url = f"http://{url_host}:{bound_port}/?token={urllib.parse.quote(self.token)}"
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
        lan_ip = find_lan_ip()
        if bound_host == "0.0.0.0":
            print(f"LAN URL: http://{lan_ip or '<raspberry-pi-ip>'}:{bound_port}/?token={self.token}")
        print(f"Shared inbox: {self.shared_space.inbox_dir}")
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
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
        query = parsed.query
        if self.token and "token=" not in query:
            query = f"{query}&token={urllib.parse.quote(self.token)}" if query else f"token={urllib.parse.quote(self.token)}"
        return urllib.parse.urlunparse((scheme, netloc, path or "/", "", query, ""))

    def startup_home_pose(self) -> dict[str, object]:
        if os.getenv("LELAMP_STARTUP_HOME", "1").lower() in {"0", "false", "no", "off"}:
            result = {
                "status": "disabled",
                "message": "LELAMP_STARTUP_HOME is disabled.",
            }
            self.audit.record("lelamp_startup_home", status="disabled", target="lelamp_center", details=result)
            return result
        if not self.runtime.config.enable_hardware:
            result = {
                "status": "adapter_ready",
                "message": "Hardware writes are disabled; startup home was skipped.",
            }
            self.audit.record("lelamp_startup_home", status="adapter_ready", target="lelamp_center", details=result)
            return result

        pose_name = os.getenv("LELAMP_STARTUP_HOME_POSE", "lelamp_center").strip() or "lelamp_center"
        pose_path = self.runtime.config.workspace_dir / ".poses" / f"{safe_filename(pose_name)}.json"
        if not pose_path.exists():
            result = {
                "status": "missing",
                "message": f"Startup home pose is missing: {pose_path}",
                "pose_path": str(pose_path),
            }
            self.audit.record("lelamp_startup_home", status="missing", target=str(pose_path), details=result)
            return result

        started = time.monotonic()
        bus = None
        try:
            from lelamp.person_tracker import TRACKING_MOTORS, load_pose, read_current_pose

            max_step = clamp_number(optional_float(os.getenv("LELAMP_STARTUP_HOME_MAX_STEP")), default=4.0, low=0.5, high=8.0)
            tolerance = clamp_number(optional_float(os.getenv("LELAMP_STARTUP_HOME_TOLERANCE")), default=2.0, low=0.3, high=5.0)
            step_seconds = clamp_number(optional_float(os.getenv("LELAMP_STARTUP_HOME_SLEEP")), default=0.06, low=0.02, high=0.3)
            max_iterations = max(1, min(80, safe_int(os.getenv("LELAMP_STARTUP_HOME_STEPS"), 35)))
            port = str(self.runtime.config.hardware_port or "/dev/ttyACM0")
            target_pose = load_pose(pose_path)
            bus = self.connect_lelamp_motor_bus(port=port, max_step=max_step)
            initial_pose = read_current_pose(bus)
            actual_pose, movement_trace = self.move_lelamp_pose_in_steps(
                bus,
                target_pose,
                motors=list(TRACKING_MOTORS),
                max_step=max_step,
                step_seconds=step_seconds,
                tolerance=tolerance,
                max_iterations=max_iterations,
            )
            reached = all(abs(float(target_pose[motor]) - float(actual_pose[motor])) <= tolerance for motor in TRACKING_MOTORS)
            status = "completed" if reached else "timeout"
            result = {
                "status": status,
                "message": "LeLamp moved to startup home pose." if reached else "LeLamp startup home stopped before every axis reached tolerance.",
                "pose_name": pose_name,
                "pose_path": str(pose_path),
                "port": port,
                "lamp_id": self.runtime.config.lamp_id,
                "motors": list(TRACKING_MOTORS),
                "primary_lift_motor": "base_pitch",
                "tolerance": tolerance,
                "max_step": max_step,
                "steps": len(movement_trace),
                "initial_pose": initial_pose,
                "target_pose": target_pose,
                "actual_pose": actual_pose,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }
            self.audit.record(
                "lelamp_startup_home",
                status=status_to_audit(status),
                target=pose_name,
                details={key: value for key, value in result.items() if key != "movement_trace"},
            )
            return result
        except Exception as exc:
            result = {
                "status": "failed",
                "message": f"LeLamp startup home failed: {str(exc)[:300]}",
                "pose_name": pose_name,
                "pose_path": str(pose_path),
                "error": str(exc)[:1000],
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }
            self.audit.record("lelamp_startup_home", status="error", target=pose_name, details=result)
            return result
        finally:
            if bus is not None:
                try:
                    bus.disconnect(disable_torque=False)
                except Exception:
                    pass

    def _is_frontend_route(self, path: str) -> bool:
        return path in {
            "/",
            "/index.html",
            "/dashboard",
            "/shared",
            "/assistant",
            "/meeting",
            "/documents",
            "/checklist",
            "/projection",
            "/desktop",
            "/validation",
            "/scene",
            "/mobile",
            "/smart-home",
            "/voice",
            "/hardware",
            "/audit",
            "/settings",
        }

    def _render_react_console(self) -> str | None:
        index_path = WEB_CONSOLE_DIST / "index.html"
        if not index_path.exists():
            return None
        content = index_path.read_text(encoding="utf-8")
        token_script = (
            "<script>"
            f"window.__LELAMP_CONSOLE_TOKEN__ = {json.dumps(self.token)};"
            "</script>"
        )
        if "__LELAMP_CONSOLE_TOKEN__" in content:
            return content
        if "</head>" in content:
            return content.replace("</head>", f"  {token_script}\n  </head>", 1)
        return f"{token_script}\n{content}"

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
        handler.send_header("Cache-Control", "public, max-age=31536000, immutable")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return True

    def _request_context(self, handler: BaseHTTPRequestHandler) -> RequestContext:
        source_ip = handler.client_address[0] if handler.client_address else ""
        forwarded_host = str(handler.headers.get("X-Forwarded-Host") or "").split(",", 1)[0].strip()
        host = forwarded_host or str(handler.headers.get("Host") or "").strip()
        forwarded_proto = str(handler.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
        return RequestContext(
            request_id=uuid4().hex,
            actor="lelamp-web",
            source_ip=source_ip,
            host=host,
            forwarded_proto=forwarded_proto,
        )

    def _authorized(self, parsed: urllib.parse.ParseResult, headers) -> bool:
        if not self.token:
            return True
        params = urllib.parse.parse_qs(parsed.query)
        auth = str(headers.get("Authorization") or "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        provided = (
            bearer
            or str(headers.get("X-OpenClaw-Console-Token") or "").strip()
            or params.get("token", [""])[0].strip()
        )
        try:
            return hmac.compare_digest(provided, self.token)
        except TypeError:
            return False

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
        if path == "/api/security":
            return self.api_security(ctx)
        if path == "/api/security/enterprise-policy":
            return self.api_enterprise_policy_status(ctx)
        if path == "/api/enterprise/local-platform/status":
            return self.api_enterprise_local_platform_status(ctx)
        if path == "/api/skills":
            return {"skills": self.runtime.skills.list_skills()}
        if path == "/api/p0":
            return self.runtime.p0.status()
        if path == "/api/readiness":
            return self.runtime.readiness_report()
        if path == "/api/product/checklist":
            return build_product_checklist(self.runtime)
        if path == "/api/product/validation/status":
            return self.api_product_validation_status(ctx)
        if path == "/api/health":
            return self.api_health()
        if path == "/api/services/status":
            return self.api_services_status()
        if path == "/api/shared/files":
            return self.api_shared_files(params, ctx)
        if path == "/api/shared/preview":
            return self.api_shared_preview(params.get("file", [""])[0], ctx)
        if path == "/api/audit/recent":
            return self.api_recent_audit_from_params(params, ctx)
        if path == "/api/audit/search":
            return self.api_recent_audit_from_params(params, ctx)
        if path == "/api/projection/latest":
            return self.api_projection_latest(ctx)
        if path == "/api/projection/service/status":
            return self.api_projection_service_status()
        if path == "/api/projection/pptx/session":
            return self.api_projection_pptx_session_status(params, ctx)
        if path == "/api/projection/display-profile":
            return self.api_projection_display_profile(ctx)
        if path == "/api/hardware/status":
            return self.api_hardware_status(ctx)
        if path == "/api/hardware/scan":
            return self.api_hardware_scan(ctx)
        if path == "/api/lelamp/motion/status":
            return self.api_lelamp_motion_status(ctx)
        if path == "/api/camera-stream/status":
            return self.api_camera_stream_status(ctx)
        if path == "/api/scene/recent":
            limit = safe_int(params.get("limit", ["20"])[0], 20)
            return self.api_scene_recent(limit, ctx)
        if path == "/api/scene/workflow-suggestions":
            limit = safe_int(params.get("limit", ["20"])[0], 20)
            return self.api_scene_workflow_suggestions(limit, ctx)
        if path == "/api/assistant/providers/status":
            return self.api_assistant_providers_status(ctx)
        if path == "/api/assistant/notifications":
            since = params.get("since", [""])[0]
            return self.api_assistant_notifications(since, ctx)
        if path == "/api/meeting/provider/status":
            return self.api_meeting_provider_status(ctx)
        if path == "/api/meeting/realtime/status":
            meeting_id = params.get("meeting_id", [""])[0].strip() or None
            return self.api_meeting_realtime_status(meeting_id, ctx)
        if path == "/api/meeting/realtime/events":
            meeting_id = params.get("meeting_id", [""])[0].strip()
            if not meeting_id:
                raise ApiError("missing_meeting_id", "Missing meeting_id.", status=400)
            return self.api_meeting_realtime_events(meeting_id, ctx)
        if path == "/api/assistant/realtime/status":
            return self.api_assistant_realtime_status(ctx)
        if path == "/api/voice/status":
            return self.api_voice_status(ctx)
        if path == "/api/voice/conversation/status":
            return self.api_voice_conversation_status(params.get("session_id", [""])[0], ctx)
        if path == "/api/mobile/status":
            return self.api_mobile_status(ctx)
        if path == "/api/smart-home/status":
            return self.api_smart_home_status(ctx)
        if path == "/api/desktop/tasks":
            limit = safe_int(params.get("limit", ["50"])[0], 50)
            return self.runtime.desktop_tasks.list_tasks(limit=limit)
        if path == "/api/desktop/automation/status":
            return self.api_desktop_automation_status(ctx)
        if path == "/api/desktop/workflow/status":
            return self.api_desktop_workflow_status(ctx)
        if path == "/api/desktop/companion/status":
            return self.api_desktop_companion_status(ctx)
        if path in {"/api/tasks", "/api/tasks/recent"}:
            limit = safe_int(params.get("limit", ["20"])[0], 20)
            return self.api_tasks_recent(limit=limit, ctx=ctx)
        if path.startswith("/api/tasks/") and path.endswith("/events"):
            task_id = path.removeprefix("/api/tasks/").removesuffix("/events").strip("/")
            if not task_id:
                raise ApiError("missing_task_id", "Missing task id.", status=400)
            return self.api_task_events(task_id, ctx)
        if path.startswith("/api/tasks/"):
            task_id = path.removeprefix("/api/tasks/").strip("/")
            if not task_id:
                raise ApiError("missing_task_id", "Missing task id.", status=400)
            return self.api_task_get(task_id, ctx)
        if path == "/api/document/adapters/status":
            return self.api_document_adapters_status(ctx)
        if path == "/api/meeting/jobs":
            return self.api_meeting_jobs(ctx)
        if path == "/api/meeting/status":
            return self.api_meeting_status(ctx)
        if path == "/api/meeting/local-realtime/status":
            return self.api_meeting_local_realtime_status(ctx)
        if path.startswith("/api/meeting/jobs/"):
            job_id = path.removeprefix("/api/meeting/jobs/").strip("/")
            return self.api_meeting_job(job_id, ctx)
        raise ApiError("not_found", f"Unknown API path: {path}", status=404)

    def handle_post(self, path: str, content_type: str, body: bytes, ctx: RequestContext) -> dict[str, object]:
        if path == "/api/shared/upload":
            return self.api_shared_upload(content_type, body, ctx)
        payload = parse_json_body(body)
        if path == "/api/shared/note":
            return self.api_shared_note(payload, ctx)
        if path == "/api/shared/file-action":
            return self.api_shared_file_action(payload, ctx)
        if path == "/api/document/analyze":
            return self.api_document_analyze(payload, ctx)
        if path == "/api/document/summarize":
            return self.api_document_summarize(payload, ctx)
        if path == "/api/document/risks":
            return self.api_document_risks(payload, ctx)
        if path == "/api/document/table-extract":
            return self.api_document_table_extract(payload, ctx)
        if path == "/api/document/report-outline":
            return self.api_document_report_outline(payload, ctx)
        if path == "/api/scan/register":
            filename = require_string(payload, "filename")
            document_type = str(payload.get("document_type") or "document")
            return self.runtime.scanning.register_scan_image(filename, document_type)
        if path == "/api/scan/ocr":
            filename = require_string(payload, "filename")
            language = str(payload.get("language") or "ch")
            return self.runtime.scanning.run_ocr(filename, language)
        if path == "/api/scan/process":
            filename = require_string(payload, "filename")
            language = str(payload.get("language") or "chi_sim+eng")
            document_type = str(payload.get("document_type") or "document")
            return self.runtime.scanning.process_scan_image(filename, document_type=document_type, language=language)
        if path == "/api/scan/capture-readiness":
            filename = require_string(payload, "filename")
            return self.runtime.scanning.capture_readiness(filename)
        if path == "/api/scan/demo-image":
            title = str(payload.get("title") or "validation_scan_demo")
            document_type = str(payload.get("document_type") or "document")
            return self.runtime.scanning.create_demo_scan_image(title=title, document_type=document_type)
        if path == "/api/scan/capture":
            image_data_url = require_string(payload, "image_data_url")
            title = str(payload.get("title") or "document_scan")
            language = str(payload.get("language") or "chi_sim+eng")
            document_type = str(payload.get("document_type") or "document")
            try:
                return self.runtime.scanning.capture_scan_image(
                    image_data_url,
                    title=title,
                    document_type=document_type,
                    language=language,
                    max_bytes=self.max_upload_bytes,
                )
            except ValueError as exc:
                raise ApiError("invalid_image", str(exc), status=400) from exc
        if path == "/api/scan/device-capture":
            title = str(payload.get("title") or "document_scan")
            language = str(payload.get("language") or "chi_sim+eng")
            document_type = str(payload.get("document_type") or "document")
            camera_index = self.resolve_camera_index(payload.get("camera_index"))
            timeout_seconds = max(3, min(20, safe_int(payload.get("timeout_seconds"), 12)))
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(self.runtime.camera_observer.capture_frame, camera_index=camera_index)
            try:
                capture = future.result(timeout=timeout_seconds)
            except FutureTimeoutError:
                future.cancel()
                capture = {
                    "status": "unavailable",
                    "message": f"设备摄像头拍照超过 {timeout_seconds} 秒未返回。",
                    "camera_index": camera_index,
                    "timeout_seconds": timeout_seconds,
                }
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            if str(capture.get("status") or "") != "captured":
                fallback_capture = self.capture_from_camera_preview_snapshot(title, camera_index=camera_index)
                if str(fallback_capture.get("status") or "") != "captured":
                    return {
                        "status": "unavailable",
                        "message": "设备摄像头没有拍到图片，请检查摄像头连接或改用浏览器摄像头/上传图片。",
                        "capture": capture,
                        "fallback_capture": fallback_capture,
                    }
                capture = fallback_capture
            capture_path = str(capture.get("path") or "")
            result = self.runtime.scanning.process_scan_image(
                capture_path,
                document_type=document_type,
                language=language,
            )
            result["capture"] = capture
            result["source_image_path"] = capture_path
            try:
                result["source_workspace_name"] = str(Path(capture_path).resolve().relative_to(self.runtime.config.workspace_dir.resolve()))
            except ValueError:
                result["source_workspace_name"] = capture_path
            self.record_audit(
                "scan.device_capture",
                status_to_audit(str(result.get("status") or "completed")),
                str(result.get("source_workspace_name") or capture_path),
                {"camera_index": camera_index, "document_type": document_type},
                ctx,
            )
            return result
        if path == "/api/scan/summarize-ocr":
            filename = require_string(payload, "filename")
            return self.runtime.scanning.summarize_ocr_text(filename)
        if path == "/api/scan/business-card":
            filename = require_string(payload, "filename")
            return self.runtime.scanning.analyze_business_card_text(filename)
        if path == "/api/meeting/import-transcript":
            return self.api_meeting_import_transcript(payload, ctx)
        if path == "/api/meeting/import-text":
            return self.api_meeting_import_text(payload, ctx)
        if path == "/api/meeting/mode/enable":
            return self.api_meeting_mode_enable(payload, ctx)
        if path == "/api/meeting/mode/disable":
            return self.api_meeting_mode_disable(ctx)
        if path == "/api/meeting/minutes":
            return self.api_meeting_minutes(payload, ctx)
        if path == "/api/meeting/decisions":
            return self.api_meeting_extract_step("decisions", payload, ctx)
        if path == "/api/meeting/action-items":
            return self.api_meeting_extract_step("action_items", payload, ctx)
        if path == "/api/meeting/followup":
            return self.api_meeting_followup(payload, ctx)
        if path == "/api/meeting/export-package":
            return self.api_meeting_export_package(payload, ctx)
        if path == "/api/meeting/send-email":
            return self.api_meeting_send_email(payload, ctx)
        if path == "/api/meeting/reminders":
            return self.api_meeting_reminders(payload, ctx)
        if path == "/api/meeting/projection-confirmation":
            return self.api_meeting_projection_confirmation(payload, ctx)
        if path == "/api/meeting/confirm-step":
            return self.api_meeting_confirm_step(payload, ctx)
        if path == "/api/meeting/provider/preflight":
            return self.api_meeting_provider_preflight(payload, ctx)
        if path == "/api/meeting/realtime/start":
            return self.api_meeting_realtime_start(payload, ctx)
        if path == "/api/meeting/realtime/stop":
            return self.api_meeting_realtime_stop(payload, ctx)
        if path == "/api/meeting/realtime/fetch-minutes":
            return self.api_meeting_realtime_fetch_minutes(payload, ctx)
        if path == "/api/meeting/local-realtime/turn":
            return self.api_meeting_local_realtime_turn(payload, ctx)
        if path == "/api/meeting/local-realtime/export":
            return self.api_meeting_local_realtime_export(payload, ctx)
        if path == "/api/product/validation/run":
            return self.api_product_validation_run(payload, ctx)
        if path == "/api/product/validation/import-desktop-result":
            return self.api_product_validation_import_desktop_result(payload, ctx)
        if path == "/api/security/verify-signed-audit":
            return self.api_verify_signed_audit(payload, ctx)
        if path == "/api/enterprise/local-platform/build":
            return self.api_enterprise_local_platform_build(payload, ctx)
        if path == "/api/projection/card":
            return self.api_projection_card(payload, ctx)
        if path == "/api/projection/markdown-file":
            return self.api_projection_markdown_file(payload, ctx)
        if path == "/api/projection/pptx/session":
            return self.api_projection_pptx_session(payload, ctx)
        if path == "/api/projection/summarize-ppt-page":
            return self.api_projection_summarize_ppt_page(payload, ctx)
        if path == "/api/projection/calibration/pattern":
            return self.api_projection_calibration_pattern(payload, ctx)
        if path == "/api/projection/calibration/analyze":
            return self.api_projection_calibration_analyze(payload, ctx)
        if path == "/api/projection/calibration/apply":
            return self.api_projection_calibration_apply(payload, ctx)
        if path == "/api/projection/service/start":
            return self.api_projection_service_start(ctx)
        if path == "/api/projection/service/stop":
            return self.api_projection_service_stop(ctx)
        if path == "/api/camera-stream/start":
            return self.api_camera_stream_start(payload, ctx)
        if path == "/api/camera-stream/stop":
            return self.api_camera_stream_stop(ctx)
        if path == "/api/projection/display-profile":
            return self.api_projection_display_profile_update(payload, ctx)
        if path == "/api/lelamp/state":
            state = require_string(payload, "state")
            return self.api_lelamp_state(state, ctx)
        if path == "/api/scene/observe-image":
            return self.api_scene_observe_image(payload, ctx)
        if path == "/api/scene/device-observe":
            return self.api_scene_device_observe(payload, ctx)
        if path == "/api/scene/sensor-snapshot":
            return self.api_scene_sensor_snapshot(payload, ctx)
        if path == "/api/scene/oriented-scan":
            return self.api_scene_oriented_scan(payload, ctx)
        if path == "/api/scene/tracking-run":
            return self.api_scene_tracking_run(payload, ctx)
        if path == "/api/scene/environment":
            return self.api_scene_environment(payload, ctx)
        if path == "/api/scene/report":
            return self.api_scene_report(payload, ctx)
        if path == "/api/scene/workflow-suggestions":
            return self.api_scene_workflow_suggestions_from_payload(payload, ctx)
        if path == "/api/scene/workflow/trigger":
            return self.api_scene_workflow_trigger(payload, ctx)
        if path == "/api/hardware/test":
            return self.api_hardware_test(payload, ctx)
        if path == "/api/desktop/task/request":
            goal = require_string(payload, "goal")
            steps = list_string(payload.get("steps")) or [goal]
            requires_full_control = bool(payload.get("requires_full_control", True))
            return self.runtime.desktop_tasks.request_task(
                goal,
                steps,
                source="web_console",
                requires_full_control=requires_full_control,
            )
        if path == "/api/desktop/task/status":
            task_id = require_string(payload, "task_id")
            status = require_string(payload, "status")
            actor = str(payload.get("actor") or "web_console")
            reason = str(payload.get("reason") or "")
            return self.runtime.desktop_tasks.update_status(task_id, status, actor=actor, reason=reason)
        if path == "/api/desktop/task/execute-browser":
            return self.api_desktop_task_execute_browser(payload, ctx)
        if path == "/api/desktop/workflow/plan":
            return self.api_desktop_workflow_plan(payload, ctx)
        if path == "/api/desktop/workflow/setup":
            return self.api_desktop_workflow_setup(payload, ctx)
        if path == "/api/desktop/workflow/execute":
            return self.api_desktop_workflow_execute(payload, ctx)
        if path == "/api/desktop/control/action":
            return self.api_desktop_control_action(payload, ctx)
        if path == "/api/desktop/companion/start":
            return self.api_desktop_companion_start(payload, ctx)
        if path == "/api/desktop/companion/stop":
            return self.api_desktop_companion_stop(ctx)
        if path == "/api/desktop/companion/run-once":
            return self.api_desktop_companion_run_once(payload, ctx)
        if path == "/api/assistant/manual":
            return self.api_manual(payload, ctx)
        if path in {"/api/assistant/message", "/api/assistant/text"}:
            return self.api_assistant_message(payload, ctx)
        if path == "/api/assistant/pi-voice-once":
            return self.api_assistant_pi_voice_once(payload, ctx)
        if path == "/api/voice/capture-once":
            return self.api_voice_capture_once(payload, ctx)
        if path == "/api/voice/conversation/start":
            return self.api_voice_conversation_start(payload, ctx)
        if path == "/api/voice/conversation/turn":
            return self.api_voice_conversation_turn(payload, ctx)
        if path == "/api/voice/conversation/stop":
            return self.api_voice_conversation_stop(payload, ctx)
        if path == "/api/assistant/realtime/session":
            return self.api_assistant_realtime_session(payload, ctx)
        if path == "/api/assistant/speak":
            return self.api_assistant_speak(payload, ctx)
        if path == "/api/assistant/confirm":
            return self.api_assistant_confirm(payload, ctx)
        if path == "/api/assistant/reject":
            return self.api_assistant_reject(payload, ctx)
        if path == "/api/mobile/request":
            return self.api_mobile_request(payload, ctx)
        if path == "/api/smart-home/control":
            return self.api_smart_home_control(payload, ctx)
        if path == "/api/settings/full-control/request":
            return self.api_full_control_request(payload, ctx)
        if path == "/api/settings/full-control/confirm":
            return self.api_full_control_confirm(payload, ctx)
        if path == "/api/settings/full-control/cancel":
            return self.api_full_control_cancel(payload, ctx)
        if path.startswith("/api/tasks/") and path.endswith("/cancel"):
            task_id = path.removeprefix("/api/tasks/").removesuffix("/cancel").strip("/")
            return self.api_task_cancel(task_id, ctx)
        if path == "/api/test/run":
            return self.api_test_run(payload)
        raise ApiError("not_found", f"Unknown API path: {path}", status=404)

    def api_test_run(self, payload: dict[str, Any]) -> dict[str, object]:
        test_id = require_string(payload, "test_id")
        if test_id == "all":
            requested = list_string(payload.get("test_ids")) or list(CONSOLE_SAFE_TEST_IDS)
            results = [self.run_console_test(item) for item in requested]
            status = "ok" if all(item.get("status") != "error" for item in results) else "partial"
            return {"status": status, "count": len(results), "results": results}
        return self.run_console_test(test_id)

    def run_console_test(self, test_id: str) -> dict[str, object]:
        started_at = time.monotonic()
        try:
            result = self._run_console_test(test_id)
        except Exception as exc:  # The test center must show backend failures instead of breaking the UI.
            payload = {
                "test_id": test_id,
                "status": "error",
                "duration_ms": int((time.monotonic() - started_at) * 1000),
                "error": str(exc)[:1000],
            }
            self.audit.record("web_console.test", status="error", target=test_id, details=payload)
            return payload

        result_status = str(result.get("status") or "ok")
        payload = {
            "test_id": test_id,
            "status": result_status,
            "duration_ms": int((time.monotonic() - started_at) * 1000),
            "result": result,
        }
        audit_status = "error" if result_status == "error" else "ok"
        self.audit.record(
            "web_console.test",
            status=audit_status,
            target=test_id,
            details={"result_status": result_status, "duration_ms": payload["duration_ms"]},
        )
        return payload

    def _run_console_test(self, test_id: str) -> dict[str, object]:
        if test_id == "security":
            return self.api_security()
        if test_id == "skills":
            return {"status": "ok", "skills": self.runtime.skills.list_skills()}
        if test_id == "readiness":
            return self.runtime.readiness_report()
        if test_id == "p0_status":
            return {"status": "ok", **self.runtime.p0.status()}
        if test_id == "shared_note":
            item = self.shared_space.put_note(
                "console_test_shared_note",
                "OpenClaw 前端测试笔记\n决定: 使用 shared_inbox 作为办公电脑公共空间\n待办: 检查审计日志",
                source="web_console_test",
            )
            return {"status": "ok", "file": item.as_dict()}
        if test_id == "workspace_blocked_read":
            try:
                self.runtime.workspace.read_text("../outside-openclaw-test.txt")
            except ValueError as exc:
                return {"status": "ok", "expected_blocked": True, "blocked_reason": str(exc)}
            return {"status": "error", "expected_blocked": False, "reason": "Unauthorized read unexpectedly succeeded."}
        if test_id == "document_analysis":
            item = self.shared_space.put_note(
                "console_test_contract",
                "# 测试合同\n付款: 30天内\n保密: 双方不得泄露资料\n终止: 任一方违约可终止\nOpenClawTestSearchMarker",
                source="web_console_test",
            )
            analysis = self.runtime.documents.analyze_text_file(item.workspace_name)
            summary = self.runtime.documents.summarize_text_file(item.workspace_name, "outline")
            return {"status": "ok", "file": item.as_dict(), "analysis": analysis, "summary": summary}
        if test_id == "meeting_followup":
            item = self.shared_space.put_note(
                "console_test_meeting_transcript",
                "Alice: 决定: 先用显示器测试投影内容\nBob: 待办: 检查 sandbox 和 audit log\nAlice: 确认: 不自动发送邮件",
                source="web_console_test",
            )
            self.runtime.meeting.parse_transcript_file(item.workspace_name, "前端测试会议", ["Alice", "Bob"])
            package = self.runtime.p0.generate_meeting_followup_package(
                recipient="待填写收件人",
                create_reminders=True,
                render_projection=True,
            )
            return {"status": "ok", "transcript_file": item.as_dict(), "package": package}
        if test_id == "projection_status":
            card = self.runtime.projection.render_status_card(
                "前端测试状态卡",
                "ready",
                details=["sandbox 默认开启", "projection_out 已写入"],
                accent="blue",
            )
            return {"status": "ok", "card": card}
        if test_id == "projection_countdown":
            card = self.runtime.projection.render_countdown("前端测试倒计时", 90, message="显示器预览测试。")
            return {"status": "ok", "card": card}
        if test_id == "projection_action":
            card = self.runtime.projection.render_action_card(
                "前端测试行动卡",
                ["用户确认后再执行桌面动作", "导出会议跟进包"],
                decisions=["继续使用显示器进行投影验证"],
            )
            return {"status": "ok", "card": card}
        if test_id == "projection_calibration":
            plan = self.runtime.projection.create_projection_calibration_plan("display_preview", "office_lighting")
            return {"status": "adapter_ready", "plan": plan}
        if test_id == "scan_register":
            item = self.shared_space.put_bytes("console_test_scan.png", TEST_PNG_BYTES, source="web_console_test")
            registered = self.runtime.scanning.register_scan_image(item.workspace_name, "document")
            return {"status": "ok", "file": item.as_dict(), "registered": registered}
        if test_id == "scan_ocr":
            item = self.shared_space.put_bytes("console_test_ocr.png", TEST_PNG_BYTES, source="web_console_test")
            result = self.runtime.scanning.run_ocr(item.workspace_name, "chi_sim+eng")
            return result
        if test_id == "ocr_text_summary":
            item = self.shared_space.put_note(
                "console_test_ocr_text",
                "Ada Lovelace\nOpenClaw Office\nada@example.com\n13800138000\n保密: NDA required",
                source="web_console_test",
            )
            summary = self.runtime.scanning.summarize_ocr_text(item.workspace_name)
            card = self.runtime.scanning.analyze_business_card_text(item.workspace_name)
            return {"status": "ok", "file": item.as_dict(), "summary": summary, "business_card": card}
        if test_id == "screen_capture":
            return self.runtime.screen.capture_screen()
        if test_id == "screen_summary":
            return self.runtime.screen.summarize_current_screen()
        if test_id == "desktop_audit_only":
            if self.runtime.config.desktop_backend != "audit_only":
                return {
                    "status": "needs_confirmation",
                    "reason": "desktop backend is not audit_only; the test center will not launch desktop actions automatically.",
                    "desktop_backend": self.runtime.config.desktop_backend,
                }
            return self.runtime.desktop.open_url("https://example.com/openclaw-test")
        if test_id == "desktop_full_control_gate":
            permission = self.runtime.desktop.request_operation("前端测试：尝试全权桌面操作门禁")
            return {"status": "ok" if not permission.get("allowed") else "needs_confirmation", "permission": permission}
        if test_id == "desktop_task_queue":
            task = self.runtime.desktop_tasks.request_task(
                "前端测试：办公电脑查看 shared_inbox",
                ["打开共享空间", "查看测试文件", "等待用户确认后再执行"],
                source="web_console_test",
            )
            return {"status": "ok", "task": task}
        if test_id == "browser_automation_status":
            task = self.runtime.desktop_tasks.request_task(
                "前端测试：受控浏览器打开 example.com",
                ["open https://example.com", "extract text", "screenshot"],
                source="web_console_test",
                requires_full_control=False,
            )
            self.runtime.desktop_tasks.update_status(str(task["id"]), "approved", actor="web_console_test")
            status = self.runtime.browser_automation.status(check_launch=False)
            step_parse = [parse_browser_step(step["description"]).audit_dict() for step in task["steps"]]
            return {"status": status["status"], "backend": status, "task": task, "parsed_steps": step_parse}
        if test_id == "desktop_companion":
            companion = DesktopCompanionService(
                workspace=self.runtime.workspace,
                audit=self.runtime.audit,
                backend="audit_only",
                permission_mode=self.runtime.config.permission_mode,
            )
            return {
                "status": "ok",
                "companion": companion.status(),
                "approved_tasks": companion.list_approved_tasks(limit=5),
            }
        if test_id == "lelamp_state":
            return {"status": "ok", "cue": self.runtime.lelamp_experience.state_cue("thinking")}
        if test_id == "environment_event":
            result = self.runtime.environment.ingest(
                {
                    "presence": True,
                    "people_count": 2,
                    "lux": 48,
                    "speech_active": True,
                    "projector_blocked": False,
                    "calendar_event_now": True,
                }
            )
            return {"status": "ok", **result}
        if test_id == "camera_observe":
            return self.runtime.camera_observer.observe_once()
        if test_id == "hardware_status":
            return {"status": "ok", **self.api_hardware_status()}
        if test_id == "smart_home_status":
            return {"status": "ok", "smart_home": self.runtime.smart_home.status()}
        if test_id == "smart_home_control_guard":
            status = self.runtime.smart_home.status()
            if status.get("home_assistant_configured") or status.get("webhook_configured"):
                return {
                    "status": "needs_confirmation",
                    "reason": "Smart-home bridge is configured; the test center reports configuration instead of toggling a real device.",
                    "smart_home": status,
                }
            return self.runtime.smart_home.control("打开办公室测试灯")
        if test_id == "xiaoai_utility":
            return {"status": "ok", "answer": self.runtime.xiaoai.answer_utility("计算 36*18")}
        if test_id == "xiaoai_features":
            return {"status": "ok", "features": self.runtime.xiaoai.feature_matrix()}
        if test_id == "intent_router":
            return {"status": "ok", "route": self.runtime.intent_router.route("帮我生成会议 follow-up 并投影确认页").as_dict()}
        if test_id == "local_file_search":
            item = self.shared_space.put_note(
                "console_test_search_source",
                "OpenClawTestSearchMarker\n这是用于前端测试中心的本地文件搜索样本。",
                source="web_console_test",
            )
            result = self.runtime.file_search.search("OpenClawTestSearchMarker", limit=5)
            return {"status": "ok", "source": item.as_dict(), "search": result}
        if test_id == "daily_reminder":
            reminder = self.runtime.daily.create_reminder("10分钟后提醒我检查前端测试结果")
            agenda = self.runtime.daily.agenda("today")
            return {"status": "ok", "reminder": reminder, "agenda": agenda}
        if test_id == "mobile_bridge":
            result = self.runtime.mobile_bridge.request("找手机")
            return {"status": result.get("status"), "mobile_bridge": self.runtime.mobile_bridge.status(), "request": result}
        if test_id == "voice_stack_status":
            return self.build_voice_status()
        if test_id == "audit_recent":
            return {"status": "ok", **self.api_recent_audit(limit=20)}
        raise ValueError(f"Unknown console test: {test_id}")

    def api_health(self) -> dict[str, object]:
        return {
            "status": "ok",
            "server": "web_console",
            "uptime_seconds": int(time.time() - self.started_at),
            "token_required": bool(self.token),
        }

    def api_services_status(self) -> dict[str, object]:
        projection_count = len(list(self.runtime.config.projection_dir.glob("*.md"))) if self.runtime.config.projection_dir.exists() else 0
        browser_status = self.runtime.browser_automation.status(check_launch=False)
        return {
            "services": [
                {"name": "web server", "status": "online", "uptime": f"{int(time.time() - self.started_at)}s", "details": {"type": "ThreadingHTTPServer"}},
                {"name": "OpenClaw core", "status": "online", "details": {"permission_mode": self.runtime.config.permission_mode.value}},
                {"name": "LeLamp core", "status": "adapter_ready" if not self.runtime.config.enable_hardware else "online", "details": {"hardware_enabled": self.runtime.config.enable_hardware}},
                {"name": "File Watcher", "status": "backend_missing", "details": {"note": "No live watcher service is connected; shared files are listed on request."}},
                {"name": "Audit Logger", "status": "online", "details": {"path": str(self.runtime.config.audit_log_path)}},
                {"name": "Assistant Engine", "status": "online", "details": {"router": "OfficeIntentRouter"}},
                {"name": "Camera Preview", "status": self.api_camera_stream_status()["status"], "details": self.api_camera_stream_status()},
                {
                    "name": "Server Speaker Playback",
                    "status": "adapter_ready",
                    "details": {
                        "mode": "server_tts_to_alsa",
                        "playback_mode": "server_side_only",
                        "note": "Assistant speech is synthesized and played only on the Raspberry Pi/server-connected ALSA speaker.",
                    },
                },
                {
                    "name": "Server TTS",
                    "status": server_tts_status(self.runtime.config),
                    "details": {
                        "provider": self.runtime.config.tts_provider,
                        "model": self.runtime.config.tts_model,
                        "voice": self.runtime.config.tts_voice,
                        "speaker_output": "server_side_only",
                    },
                },
                {"name": "Projection Service", "status": "adapter_ready", "details": {"projection_cards": projection_count, "physical_projector": "not_required_for_display_test"}},
                {"name": "Browser Automation", "status": browser_status["status"], "details": browser_status},
                {"name": "Hardware Monitor", "status": "adapter_ready" if not self.runtime.config.enable_hardware else "online", "details": {"polling": "on_request"}},
            ]
        }

    def api_security(self, ctx: RequestContext | None = None) -> dict[str, object]:
        security = self.runtime.security_status()
        security["console_token_required"] = bool(self.token)
        security["token_required"] = bool(self.token)
        security["full_control_enabled"] = self.runtime.config.permission_mode.value == "full_control"
        security["projection_preview_url"] = f"http://127.0.0.1:{self.projection_preview_port}/"
        security["cloud_ai_enabled"] = self.runtime.config.cloud_ai_enabled
        security["enterprise_policy_status"] = self.runtime.enterprise.status()
        if ctx:
            self.record_audit("security.status", "ok", "web_console", {}, ctx)
        return security

    def api_enterprise_policy_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        status = self.runtime.enterprise.status()
        if ctx:
            self.record_audit("enterprise_policy.status", status_to_audit(str(status.get("status"))), "enterprise_policy", {"cloud_ai_enabled": status.get("cloud_ai_enabled")}, ctx)
        return status

    def api_enterprise_local_platform_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        status = self.runtime.enterprise.local_platform_status()
        if ctx:
            self.record_audit("enterprise_local_platform.status", status_to_audit(str(status.get("status"))), "enterprise_platform", {"status": status.get("status")}, ctx)
        return status

    def api_enterprise_local_platform_build(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        include_samples = bool(payload.get("include_samples", True))
        result = self.runtime.enterprise.build_local_platform_bundle(include_samples=include_samples)
        task = self.create_task("企业本地算力与数据平台包", "enterprise", "completed", {"include_samples": include_samples}, result)
        self.record_audit("enterprise_local_platform.build", "ok", str(result.get("bundle_path")), {"task_id": task["task_id"]}, ctx)
        return {"task_id": task["task_id"], **result}

    def api_desktop_automation_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        status = self.runtime.browser_automation.status(check_launch=True)
        if ctx:
            self.record_audit(
                "browser_automation.status",
                status_to_audit(str(status.get("status"))),
                "browser_automation",
                {
                    "package_installed": status.get("package_installed"),
                    "headless_default": status.get("headless_default"),
                },
                ctx,
            )
        return status

    def api_desktop_companion_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        with self._desktop_companion_lock:
            running = self._desktop_companion_thread is not None and self._desktop_companion_thread.is_alive()
            payload = {
                "status": "running" if running else "stopped",
                "backend": self.runtime.config.desktop_backend,
                "permission_mode": self.runtime.config.permission_mode.value,
                "queue_dir": str(self.runtime.desktop_tasks.queue_dir),
                "started_at": self._desktop_companion_started_at,
                "last_run": self._desktop_companion_last_run,
                "safety": [
                    "Only approved desktop tasks are processed.",
                    "Default audit_only backend plans without changing the desktop.",
                    "The service can be stopped from the web console.",
                ],
            }
        if ctx:
            self.record_audit("desktop_companion.status", "ok", "desktop_companion", {"running": running}, ctx)
        return payload

    def api_shared_files(self, params: dict[str, list[str]] | None = None, ctx: RequestContext | None = None) -> dict[str, object]:
        params = params or {}
        query = (params.get("q", [""])[0] or "").lower()
        status_filter = params.get("status", [""])[0]
        type_filter = params.get("type", [""])[0].strip().lower()
        page = max(1, safe_int(params.get("page", ["1"])[0], 1))
        page_size = min(100, max(1, safe_int(params.get("page_size", ["20"])[0], 20)))
        files = [self.shared_file_dto(item.as_dict()) for item in self.shared_space.list_files()]
        if query:
            files = [item for item in files if query in f"{item['name']} {item['relative_path']} {item['mime_type']}".lower()]
        if status_filter:
            files = [item for item in files if item.get("status") == status_filter]
        if type_filter:
            files = [item for item in files if shared_file_matches_type(item, type_filter)]
        total = len(files)
        start = (page - 1) * page_size
        if ctx:
            self.record_audit("shared_space.list", "ok", "shared_inbox", {"total": total, "page": page, "page_size": page_size}, ctx)
        return {
            "shared_inbox": str(self.shared_space.inbox_dir),
            "items": files[start : start + page_size],
            "files": files[start : start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def api_shared_preview(self, workspace_name: str, ctx: RequestContext) -> dict[str, object]:
        if not workspace_name:
            raise ApiError("missing_file", "Missing required query parameter: file", status=400)
        try:
            preview = self.shared_space.read_preview(workspace_name)
        except ValueError as exc:
            self.record_audit("file_read", "blocked", redact_target(workspace_name), {"reason": str(exc)}, ctx)
            raise ApiError("blocked", "File access blocked by workspace/shared_inbox policy.", status=403) from exc
        if preview.get("status") == "binary":
            safe = self.ensure_allowed_path(workspace_name, ctx, action="shared_preview_extract")
            if is_text_workflow_path(safe.path):
                try:
                    extracted = self.runtime.documents.extract_document_text(safe.workspace_name, max_chars=12000)
                    source = extracted.get("source") if isinstance(extracted.get("source"), dict) else {}
                    preview = {
                        **preview,
                        "status": "ok",
                        "download_only": False,
                        "text": str(extracted.get("text") or ""),
                        "truncated": bool(source.get("truncated")),
                        "document_text_backend": source.get("backend"),
                    }
                except DocumentExtractionError as exc:
                    preview = {
                        **preview,
                        "status": exc.status,
                        "text": str(exc),
                        "document_text_backend": exc.backend,
                    }
        self.record_audit("file_read", "ok", str(preview.get("workspace_name") or workspace_name), {"preview_status": preview.get("status")}, ctx)
        return preview

    def api_shared_download(self, workspace_name: str, ctx: RequestContext) -> Path:
        if not workspace_name:
            raise ApiError("missing_file", "Missing required query parameter: file", status=400)
        try:
            path = self.shared_space.resolve_shared_file(workspace_name)
        except ValueError as exc:
            self.record_audit("shared_space.download", "blocked", redact_target(workspace_name), {"reason": str(exc)}, ctx)
            raise ApiError("blocked", "File access blocked by shared_inbox policy.", status=403) from exc
        self.record_audit(
            "shared_space.download",
            "ok",
            target=str(path.relative_to(self.runtime.config.workspace_dir)),
            details={"size_bytes": path.stat().st_size},
            ctx=ctx,
        )
        return path

    def api_shared_upload(self, content_type: str, body: bytes, ctx: RequestContext) -> dict[str, object]:
        if "multipart/form-data" not in content_type:
            raise ApiError("bad_upload", "Expected multipart/form-data upload.", status=400)
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        )
        if not message.is_multipart():
            raise ApiError("bad_upload", "Malformed upload body.", status=400)
        uploaded = []
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename and payload:
                try:
                    uploaded.append(self.shared_file_dto(self.shared_space.put_bytes(filename, payload, source="web_console_upload").as_dict()))
                except ValueError as exc:
                    self.record_audit("upload", "blocked", redact_target(filename), {"reason": str(exc)}, ctx)
                    raise ApiError("blocked", "Upload blocked by shared_inbox policy.", status=403) from exc
        if not uploaded:
            raise ApiError("bad_upload", "No file was uploaded.", status=400)
        self.record_audit("upload", "ok", "shared_inbox", {"count": len(uploaded), "files": [item["relative_path"] for item in uploaded]}, ctx)
        return {"status": "ok", "files": uploaded}

    def api_shared_note(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = require_string(payload, "title")
        text = str(payload.get("content") or payload.get("text") or "")
        if not text.strip():
            raise ApiError("missing_content", "Missing note content.", status=400)
        item = self.shared_file_dto(self.shared_space.put_note(title, text, source="web_console_note").as_dict())
        self.record_audit("note_create", "ok", str(item["relative_path"]), {"chars": len(text)}, ctx)
        return {"status": "ok", "file": item}

    def api_shared_file_action(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        file_path = require_file_path(payload)
        action = require_string(payload, "action")
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        safe = self.ensure_allowed_path(file_path, ctx, action="file_action")
        result: dict[str, object]
        task_type = "document"
        if action == "analyze":
            result = self.api_document_analyze({"file_path": safe.workspace_name}, ctx)
        elif action == "summarize":
            result = self.api_document_summarize({"file_path": safe.workspace_name, "style": params.get("style", "brief")}, ctx)
        elif action == "report_outline":
            result = self.api_document_report_outline({"file_path": safe.workspace_name, "topic": params.get("topic") or Path(safe.workspace_name).stem}, ctx)
        elif action == "key_data_table":
            result = self.api_document_table_extract({"file_path": safe.workspace_name}, ctx)
        elif action == "search":
            query = str(params.get("q") or Path(safe.workspace_name).stem)
            result = self.runtime.file_search.search(query, limit=10)
            task_type = "assistant"
        elif action == "generate_minutes":
            result = self.api_meeting_minutes({"transcript": safe.workspace_name, "title": Path(safe.workspace_name).stem}, ctx)
            task_type = "meeting"
        elif action == "followup_package":
            result = self.api_meeting_followup({"transcript": safe.workspace_name, "title": Path(safe.workspace_name).stem, "render_projection": True}, ctx)
            task_type = "meeting"
        else:
            self.record_audit("file_action", "blocked", safe.workspace_name, {"action": action, "reason": "unknown action"}, ctx)
            raise ApiError("unknown_action", f"Unsupported file action: {action}", status=400)
        task = self.create_task(
            title=f"{action}: {safe.workspace_name}",
            task_type=task_type,
            status=normalize_task_status(str(result.get("status") or "completed")),
            input_payload={"file_path": safe.workspace_name, "action": action},
            output=result,
        )
        self.record_audit("file_action", "ok", safe.workspace_name, {"action": action, "task_id": task["task_id"]}, ctx)
        return {"status": task["status"], "task_id": task["task_id"], "result": result}

    def api_document_analyze(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(require_file_path(payload), ctx, action="document_analyze")
        try:
            result = self.runtime.documents.analyze_text_file(safe.workspace_name)
            status = "completed"
        except (UnicodeDecodeError, DocumentExtractionError) as exc:
            if isinstance(exc, DocumentExtractionError):
                result = exc.as_payload(filename=safe.workspace_name)
                result["adapter_status"] = document_adapter_status_from_runtime(self.runtime)
            else:
                result = {"status": "backend_missing", "summary": "Workspace file could not be decoded as text.", "error": str(exc)}
            status = str(result.get("status") or "backend_missing")
        task = self.create_task("文档分析", "document", status, {"file_path": safe.workspace_name}, result)
        self.record_audit("document_analyze", status_to_audit(status), safe.workspace_name, {"task_id": task["task_id"]}, ctx)
        return document_result_payload(task, result, status)

    def api_document_summarize(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(require_file_path(payload), ctx, action="document_summarize")
        style = str(payload.get("style") or "brief")
        try:
            result = self.runtime.documents.summarize_text_file(safe.workspace_name, style)
            status = "completed"
        except (UnicodeDecodeError, DocumentExtractionError) as exc:
            if isinstance(exc, DocumentExtractionError):
                result = exc.as_payload(filename=safe.workspace_name)
                result["adapter_status"] = document_adapter_status_from_runtime(self.runtime)
            else:
                result = {"status": "backend_missing", "summary": "Workspace file could not be decoded as text.", "error": str(exc)}
            status = str(result.get("status") or "backend_missing")
        task = self.create_task("文档摘要", "document", status, {"file_path": safe.workspace_name, "style": style}, result)
        self.record_audit("document_summarize", status_to_audit(status), safe.workspace_name, {"task_id": task["task_id"]}, ctx)
        return document_result_payload(task, result, status)

    def api_document_risks(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        result = self.api_document_analyze(payload, ctx)
        analysis = result.get("metadata", {})
        risks = [{"marker": item, "level": "medium"} for item in list_string(analysis.get("risk_markers") if isinstance(analysis, dict) else [])]
        result["risks"] = risks
        self.record_audit("document_risk_scan", "ok", require_file_path(payload), {"risks": len(risks)}, ctx)
        return result

    def api_document_table_extract(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(require_file_path(payload), ctx, action="document_table_extract")
        try:
            result = self.runtime.documents.extract_table_from_text(safe.workspace_name)
        except DocumentExtractionError as exc:
            result = {**exc.as_payload(filename=safe.workspace_name), "table_path": "", "rows": 0, "adapter_status": document_adapter_status_from_runtime(self.runtime)}
        status = "completed" if int(result.get("rows") or 0) > 0 else "backend_missing"
        task = self.create_task("表格提取", "document", status, {"file_path": safe.workspace_name}, result)
        self.record_audit("document_table_extract", status_to_audit(status), safe.workspace_name, {"task_id": task["task_id"], "rows": result.get("rows")}, ctx)
        return document_result_payload(task, result, status)

    def api_document_report_outline(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(require_file_path(payload), ctx, action="document_report_outline")
        topic = str(payload.get("topic") or Path(safe.workspace_name).stem)
        try:
            result = self.runtime.documents.create_report_outline([safe.workspace_name], topic)
        except DocumentExtractionError as exc:
            result = {**exc.as_payload(filename=safe.workspace_name), "outline_path": "", "adapter_status": document_adapter_status_from_runtime(self.runtime)}
        status = str(result.get("status") or ("completed" if result.get("outline_path") else "backend_missing"))
        task = self.create_task("汇报提纲", "document", normalize_task_status(status), {"file_path": safe.workspace_name, "topic": topic}, result)
        self.record_audit("document_report_outline", status_to_audit(status), safe.workspace_name, {"task_id": task["task_id"], "outline_path": result.get("outline_path")}, ctx)
        return document_result_payload(task, result, status)

    def api_document_adapters_status(self, ctx: RequestContext) -> dict[str, object]:
        adapters = document_adapter_status_from_runtime(self.runtime)
        self.record_audit("document_adapters.status", "ok", "document_adapters", adapters, ctx)
        return {"adapters": adapters}

    def api_meeting_status(self, ctx: RequestContext) -> dict[str, object]:
        status = self.runtime.meeting.status()
        self.record_audit("meeting_status", "ok", "meeting_mode", status, ctx)
        return {"status": "ok", **status}

    def api_meeting_local_realtime_status(self, ctx: RequestContext) -> dict[str, object]:
        summary = self.runtime.meeting.realtime_summary()
        self.record_audit("meeting_local_realtime.status", "ok", "active_meeting", {"turn_count": summary.get("turn_count")}, ctx)
        return {"status": "completed", **summary}

    def api_meeting_local_realtime_turn(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        speaker = str(payload.get("speaker") or "Unknown").strip() or "Unknown"
        text = require_string(payload, "text")
        try:
            item = self.runtime.meeting.append_transcript(speaker, text)
        except PermissionError as exc:
            self.record_audit("meeting_local_realtime.turn", "blocked", speaker, {"reason": str(exc)}, ctx)
            raise ApiError("meeting_mode_disabled", str(exc), status=409) from exc
        summary = self.runtime.meeting.realtime_summary()
        result = {
            "status": "completed",
            "turn": item,
            **summary,
            "source": str(payload.get("source") or "manual_realtime_turn"),
        }
        self.record_audit("meeting_local_realtime.turn", "ok", speaker, {"chars": len(text), "turn_count": summary.get("turn_count")}, ctx)
        return result

    def api_meeting_local_realtime_export(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        try:
            transcript = self.runtime.meeting.export_transcript()
        except ValueError as exc:
            raise ApiError("no_active_meeting", str(exc), status=409) from exc
        summary = self.runtime.meeting.realtime_summary()
        result = {
            "status": "completed",
            "transcript_path": transcript["path"],
            "workspace_name": self.workspace_relative_path(transcript["path"]),
            **summary,
        }
        task = self.create_task("本地实时转写导出", "meeting", "completed", {"source": payload.get("source") or "local_realtime"}, result)
        self.record_audit("meeting_local_realtime.export", "ok", str(transcript["path"]), {"task_id": task["task_id"], "turn_count": summary.get("turn_count")}, ctx)
        return {"task_id": task["task_id"], **result}

    def api_product_validation_status(self, ctx: RequestContext) -> dict[str, object]:
        result = build_target_validation_report(self.runtime, projection_preview_url=self._projection_preview_url, tingwu_provider=self.tingwu)
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        self.record_audit(
            "product_validation.status",
            status_to_audit(str(result.get("status") or "adapter_ready")),
            "target_validation",
            {"completed": summary.get("completed"), "adapter_ready": summary.get("adapter_ready")},
            ctx,
        )
        return result

    def api_product_validation_run(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        test_id = require_string(payload, "test_id")
        options = payload.get("options") if isinstance(payload.get("options"), dict) else payload
        result = run_target_validation(self.runtime, test_id, options, projection_preview_url=self._projection_preview_url, tingwu_provider=self.tingwu)
        report = result.get("report") if isinstance(result.get("report"), dict) else {}
        task = self.create_task(
            f"目标验收：{report.get('feature', test_id)}",
            "validation",
            normalize_task_status(str(result.get("status") or "adapter_ready")),
            {"test_id": test_id},
            result,
        )
        self.record_audit(
            "product_validation.run",
            status_to_audit(str(result.get("status") or "adapter_ready")),
            test_id,
            {"task_id": task["task_id"], "json_workspace_name": result.get("json_workspace_name")},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_product_validation_import_desktop_result(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        report = data.get("report") if isinstance(data.get("report"), dict) else {}
        if not report and isinstance(data.get("data"), dict):
            nested = data["data"]
            report = nested.get("report") if isinstance(nested.get("report"), dict) else {}
        if not isinstance(report, dict) or report.get("id") != "desktop_full_control":
            raise ApiError("invalid_validation_result", "Expected a desktop_full_control validation result.", status=400)
        evidence = desktop_full_control_evidence(report)
        missing_evidence = [key for key, value in evidence.items() if not value]
        remediation = desktop_full_control_remediation(report, missing_evidence)
        result_status = "completed" if all(evidence.values()) and report.get("status") == "completed" else "adapter_ready"
        saved_payload = payload if payload.get("ok") is not None else {"ok": True, "data": data}
        report_dir = (self.runtime.config.workspace_dir / "validation_reports").resolve()
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / "desktop_full_control_target_result.json"
        atomic_write_json(path, saved_payload)
        task = self.create_task(
            "导入 full_control 目标机验收结果",
            "validation",
            normalize_task_status(result_status),
            {"evidence": evidence, "missing_evidence": missing_evidence, "remediation": remediation},
            {
                "status": result_status,
                "workspace_name": self.workspace_relative_path(str(path)),
                "evidence": evidence,
                "missing_evidence": missing_evidence,
                "remediation": remediation,
            },
        )
        self.record_audit(
            "product_validation.import_desktop_result",
            status_to_audit(result_status),
            "desktop_full_control_target_result.json",
            {"task_id": task["task_id"], "evidence": evidence, "missing_evidence": missing_evidence, "remediation": remediation},
            ctx,
        )
        return {
            "status": result_status,
            "task_id": task["task_id"],
            "workspace_name": self.workspace_relative_path(str(path)),
            "path": str(path),
            "evidence": evidence,
            "missing_evidence": missing_evidence,
            "remediation": remediation,
        }

    def api_meeting_mode_enable(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = str(payload.get("title") or f"会议模式 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        participants = list_string(payload.get("participants")) or ["Unknown"]
        result = self.runtime.meeting.enable(title, participants)
        self.record_audit("meeting_mode_enable", "ok", title, result, ctx)
        return {"status": "completed", **result}

    def api_meeting_mode_disable(self, ctx: RequestContext) -> dict[str, object]:
        result = self.runtime.meeting.disable()
        self.record_audit("meeting_mode_disable", "ok", "meeting_mode", result, ctx)
        return {"status": "completed", **result}

    def api_meeting_import_transcript(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(require_file_path(payload), ctx, action="meeting_import_transcript")
        title = str(payload.get("title") or Path(safe.workspace_name).stem)
        participants = list_string(payload.get("participants")) or ["Unknown"]
        parsed = self.runtime.meeting.parse_transcript_file(safe.workspace_name, title, participants)
        job = self.create_meeting_job(title, safe.workspace_name, "import_transcript", "completed", parsed)
        self.record_audit("meeting_import_transcript", "ok", safe.workspace_name, {"job_id": job["job_id"], **parsed}, ctx)
        return job

    def api_meeting_import_text(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = str(payload.get("title") or f"meeting_text_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        text = require_string(payload, "text")
        participants = list_string(payload.get("participants")) or ["Unknown"]
        file_item = self.shared_space.put_note(title, text, source="meeting_text_import")
        shared = self.shared_file_dto(file_item.as_dict())
        job = self.api_meeting_import_transcript(
            {
                "file_path": shared["relative_path"],
                "title": title,
                "participants": participants,
            },
            ctx,
        )
        result = {
            "status": "completed",
            "source": "meeting_text_import",
            "file": shared,
            "job": job,
            "participants": participants,
        }
        self.record_audit(
            "meeting_import_text",
            "ok",
            str(shared["relative_path"]),
            {"job_id": job.get("job_id"), "chars": len(text), "participants": participants},
            ctx,
        )
        return result

    def load_meeting_transcript(self, payload: dict[str, Any], ctx: RequestContext, *, action: str) -> tuple[str, str]:
        transcript = payload.get("transcript") or payload.get("file_path")
        if transcript:
            safe = self.ensure_allowed_path(str(transcript), ctx, action=action)
            title = str(payload.get("title") or Path(safe.workspace_name).stem)
            self.runtime.meeting.parse_transcript_file(
                safe.workspace_name,
                title,
                list_string(payload.get("participants")) or ["Unknown"],
            )
            return safe.workspace_name, title
        status = self.runtime.meeting.status()
        title = str(payload.get("title") or status.get("active_title") or "Meeting")
        return "active_meeting", title

    def api_meeting_provider_status(self, ctx: RequestContext) -> dict[str, object]:
        status = self.tingwu.status()
        self.record_audit("meeting_provider.status", status_to_audit(str(status.get("status"))), "tongyi_tingwu", status, ctx)
        return {
            "status": status.get("status"),
            "primary_provider": "tongyi_tingwu",
            "providers": {"tongyi_tingwu": status},
        }

    def api_meeting_provider_preflight(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        provider = self.tingwu.status()
        credential_diagnostics = (
            provider.get("credential_diagnostics")
            if isinstance(provider.get("credential_diagnostics"), dict)
            else {}
        )
        checks: dict[str, object] = {
            "tingwu_api_key_configured": bool(provider.get("api_key_configured")),
            "tingwu_app_id_configured": bool(provider.get("app_id_configured")),
            "provider_configured": bool(provider.get("configured")),
            "official_tingwu_endpoint": endpoint_matches(provider.get("http_url"), OFFICIAL_TINGWU_HTTP_URL)
            and endpoint_matches(provider.get("ws_url"), OFFICIAL_TINGWU_WS_URL),
            "microphone_selected": bool(provider.get("selected_mic_device")),
            "microphone_available": str(provider.get("mic_status") or "") == "available",
            "real_microphone_device": False,
            "microphone_capture_device_matches": False,
            "microphone_capture_open": False,
            "microphone_capture_signal": False,
        }
        selected = str(provider.get("selected_mic_device") or provider.get("mic_device") or "").strip()
        mic_probe = provider.get("mic_probe") if isinstance(provider.get("mic_probe"), dict) else {}
        checks["real_microphone_device"] = is_real_tingwu_microphone(selected, mic_probe)
        capture_probe: dict[str, object] = {}
        capture_seconds = max(1, min(10, safe_int(payload.get("capture_seconds"), self.runtime.config.tingwu_preflight_capture_seconds)))
        if bool(payload.get("skip_capture", False)):
            capture_probe = {"status": "skipped", "selected_device": selected, "duration_seconds": 0}
        elif checks["real_microphone_device"]:
            capture_probe = preflight_arecord_capture(selected, self.runtime.config.tingwu_sample_rate, duration_seconds=capture_seconds)
            checks["microphone_capture_device_matches"] = capture_probe_matches_selected_microphone(selected, capture_probe)
            checks["microphone_capture_open"] = str(capture_probe.get("status") or "") == "available"
            checks["microphone_capture_signal"] = (
                bool(checks["microphone_capture_open"])
                and safe_int(capture_probe.get("audio_bytes"), 0) > 0
                and safe_int(capture_probe.get("audio_rms"), 0) > 0
                and safe_int(capture_probe.get("audio_peak"), 0) > 0
            )
        else:
            capture_probe = {
                "status": "blocked",
                "selected_device": selected,
                "message": "Select a real ALSA capture device before running microphone capture preflight.",
            }
        ready = all(bool(checks[key]) for key in (
            "tingwu_api_key_configured",
            "tingwu_app_id_configured",
            "provider_configured",
            "official_tingwu_endpoint",
            "microphone_selected",
            "microphone_available",
            "real_microphone_device",
            "microphone_capture_device_matches",
            "microphone_capture_open",
            "microphone_capture_signal",
        ))
        status = "available" if ready else "needs_config" if not bool(provider.get("configured")) else "unavailable"
        result = {
            "status": status,
            "provider": "tongyi_tingwu",
            "ready": ready,
            "checks": checks,
            "next_actions": tingwu_provider_preflight_next_actions(checks, credential_diagnostics=credential_diagnostics),
            "acceptance_checklist": tingwu_provider_acceptance_checklist(checks),
            "provider_status": provider,
            "capture_probe": capture_probe,
            "credential_diagnostics": credential_diagnostics,
            "capture_seconds": capture_seconds,
            "selected_mic_device": selected,
            "sample_rate": self.runtime.config.tingwu_sample_rate,
            "audio_format": self.runtime.config.tingwu_audio_format,
        }
        self.record_audit(
            "meeting_provider.preflight",
            status_to_audit(status),
            "tongyi_tingwu",
            sanitize_event_payload(result),
            ctx,
        )
        return result

    def api_meeting_realtime_start(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = str(payload.get("title") or f"LeLamp 实时会议 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        participants = list_string(payload.get("participants")) or ["Unknown"]
        max_seconds = max(30, min(8 * 60 * 60, safe_int(payload.get("max_seconds"), 2 * 60 * 60)))
        try:
            session = self.tingwu.start_realtime_meeting(title=title, participants=participants, max_seconds=max_seconds)
        except TingwuMeetingError as exc:
            details = self.tingwu_start_failure_details(exc)
            self.record_audit("meeting_realtime_start", status_to_audit(str(details["provider"].get("status"))), "tongyi_tingwu", details, ctx)
            self.push_assistant_notification(**self.build_tingwu_start_failure_notification(title, details))
            raise ApiError("meeting_provider_unavailable", str(exc), status=409, details=details) from exc
        stored_title = str(session.get("title") or redact_sensitive_text(title) or "Tingwu Meeting")
        task = self.upsert_meeting_step_task(
            stored_title,
            self.workspace_relative_path(str(session.get("transcript_path") or "")),
            "realtime_capture",
            "running",
            session,
            meeting_id=str(session.get("meeting_id") or ""),
            provider="tongyi_tingwu",
        )
        result = {
            **session,
            "status": "running",
            "task_id": task["task_id"],
            "task_id_web": task["task_id"],
            "provider_task_id": session.get("task_id"),
            "job": self.meeting_job_from_task(task),
        }
        self.record_audit(
            "meeting_realtime_start",
            "ok",
            str(session.get("meeting_id") or ""),
            {"task_id": task["task_id"], "provider_task_id": session.get("task_id"), "title": stored_title},
            ctx,
        )
        return result

    def api_meeting_realtime_status(self, meeting_id: str | None, ctx: RequestContext) -> dict[str, object]:
        try:
            status = self.tingwu.session_status(meeting_id)
        except TingwuMeetingError as exc:
            raise ApiError("not_found", str(exc), status=404) from exc
        if status.get("meeting_id"):
            self.sync_realtime_capture_task(status)
        self.record_audit(
            "meeting_realtime_status",
            status_to_audit(str(status.get("status"))),
            str(status.get("meeting_id") or "idle"),
            {"provider": "tongyi_tingwu", "final_count": status.get("final_count")},
            ctx,
        )
        return status

    def api_meeting_realtime_events(self, meeting_id: str, ctx: RequestContext) -> dict[str, object]:
        drained_events = self.tingwu.drain_events(meeting_id, limit=200)
        session_events: list[dict[str, object]] = []
        try:
            session = self.tingwu.session_status(meeting_id)
        except TingwuMeetingError:
            session = {}
        task_payload = session.get("task_payload") if isinstance(session.get("task_payload"), dict) else {}
        persisted_events = task_payload.get("events") if isinstance(task_payload.get("events"), list) else []
        session_events = [item for item in persisted_events if isinstance(item, dict)]
        events = dedupe_events([*session_events, *drained_events])[-200:]
        self.append_realtime_task_events(meeting_id, events)
        self.record_audit("meeting_realtime_events", "ok", meeting_id, {"count": len(events)}, ctx)
        return {"status": "ok", "meeting_id": meeting_id, "events": events, "total": len(events)}

    def api_meeting_realtime_stop(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        meeting_id = str(payload.get("meeting_id") or "").strip() or None
        try:
            session = self.tingwu.stop_realtime_meeting(meeting_id)
        except TingwuMeetingError as exc:
            raise ApiError("meeting_stop_failed", str(exc), status=409) from exc
        if str(session.get("status") or "") in {"starting", "running", "stopping", "finalizing"}:
            task = self.upsert_meeting_step_task(
                str(session.get("title") or "Tingwu Meeting"),
                self.workspace_relative_path(str(session.get("transcript_path") or "")),
                "realtime_capture",
                str(session.get("status") or "stopping"),
                session,
                meeting_id=str(session.get("meeting_id") or ""),
                provider="tongyi_tingwu",
            )
            result = {
                "status": session.get("status"),
                "task_id": task["task_id"],
                "task_id_web": task["task_id"],
                "provider_task_id": session.get("task_id"),
                "job": self.meeting_job_from_task(task),
                "session": session,
            }
            self.record_audit(
                "meeting_realtime_stop",
                "running",
                str(session.get("meeting_id") or ""),
                {
                    "task_id": task["task_id"],
                    "message": "Realtime stream is still stopping; final outputs are not registered until the stream thread exits.",
                },
                ctx,
            )
            return result
        result = self.register_tingwu_outputs(session, ctx, run_followup=bool(payload.get("run_followup", False)))
        self.record_audit(
            "meeting_realtime_stop",
            status_to_audit(str(result.get("status"))),
            str(session.get("meeting_id") or ""),
            {"job_id": result.get("job", {}).get("job_id") if isinstance(result.get("job"), dict) else None, "minutes_path": session.get("minutes_path")},
            ctx,
        )
        self.push_assistant_notification(**self.build_tingwu_assistant_notification("stop", session, result))
        return result

    def api_meeting_realtime_fetch_minutes(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        meeting_id = str(payload.get("meeting_id") or "").strip()
        if not meeting_id:
            raise ApiError("missing_meeting_id", "Missing meeting_id.", status=400)
        try:
            current = self.tingwu.session_status(meeting_id)
            if str(current.get("status") or "") in {"starting", "running", "stopping", "finalizing"}:
                self.record_audit(
                    "meeting_realtime_fetch_minutes",
                    "blocked",
                    meeting_id,
                    {"status": current.get("status"), "reason": "meeting_not_stopped"},
                    ctx,
                )
                raise ApiError(
                    "meeting_not_stopped",
                    "Realtime meeting is still running or stopping. Stop the meeting and wait for capture to finish before fetching AI minutes.",
                    status=409,
                    details={"meeting_id": meeting_id, "status": current.get("status")},
                )
            session = self.tingwu.finalize_meeting(meeting_id, retry_failed_minutes=True)
        except TingwuMeetingError as exc:
            raise ApiError("meeting_minutes_fetch_failed", str(exc), status=409) from exc
        result = self.register_tingwu_outputs(session, ctx, run_followup=bool(payload.get("run_followup", True)))
        self.record_audit("meeting_realtime_fetch_minutes", status_to_audit(str(result.get("status"))), meeting_id, {"minutes_path": session.get("minutes_path")}, ctx)
        self.push_assistant_notification(**self.build_tingwu_assistant_notification("fetch_minutes", session, result))
        return result

    def tingwu_start_failure_details(self, exc: TingwuMeetingError) -> dict[str, object]:
        error_details = exc.details if isinstance(getattr(exc, "details", None), dict) else {}
        provider = self.tingwu.status()
        mic_probe = error_details.get("mic_probe") if isinstance(error_details.get("mic_probe"), dict) else None
        capture_probe = error_details.get("capture_probe") if isinstance(error_details.get("capture_probe"), dict) else None
        if mic_probe:
            provider = {**provider, "mic_probe": mic_probe}
            provider["probe_status_before_capture"] = provider.get("status")
            provider["status"] = "unavailable"
            provider["mic_status"] = mic_probe.get("status") or provider.get("mic_status")
            provider["selected_mic_device"] = mic_probe.get("selected_device") or provider.get("selected_mic_device") or provider.get("mic_device")
            if capture_probe:
                provider["capture_probe"] = capture_probe
        details: dict[str, object] = {
            "error": str(exc),
            "provider": provider,
        }
        if mic_probe:
            details["mic_probe"] = mic_probe
        if capture_probe:
            details["capture_probe"] = capture_probe
        if error_details:
            details["diagnostics"] = error_details
        return sanitize_event_payload(details)

    def build_tingwu_start_failure_notification(self, title: str, details: dict[str, object]) -> dict[str, object]:
        provider = details.get("provider") if isinstance(details.get("provider"), dict) else {}
        provider_status = str(provider.get("status") or "unavailable")
        mic_status = str(provider.get("mic_status") or "")
        capture_probe = details.get("capture_probe") if isinstance(details.get("capture_probe"), dict) else {}
        if not capture_probe:
            provider_mic_probe = provider.get("mic_probe") if isinstance(provider.get("mic_probe"), dict) else {}
            capture_probe = provider_mic_probe.get("capture_probe") if isinstance(provider_mic_probe.get("capture_probe"), dict) else {}
        error = str(details.get("error") or "")
        if provider_status == "needs_config":
            reason = "缺少 TINGWU_API_KEY/DASHSCOPE_API_KEY 或 TINGWU_APP_ID/TINGWU_MEETING_APP_ID。"
        elif mic_status and mic_status != "available":
            reason = f"麦克风不可用：{provider.get('message') or mic_status}"
        else:
            reason = error or f"provider 状态为 {provider_status}。"
        return {
            "event": "meeting_realtime_start_failed",
            "text": f"实时会议「{title}」没有启动成功，{reason}",
            "status": "failed",
            "attachment": "",
            "payload": {"title": title, "provider": provider, "capture_probe": capture_probe, "error": error},
        }

    def build_tingwu_assistant_notification(
        self,
        action: str,
        session: dict[str, object],
        result: dict[str, object],
    ) -> dict[str, object]:
        title = str(session.get("title") or "Meeting")
        meeting_id = session.get("meeting_id")
        result_status = str(result.get("status") or session.get("status") or "failed")
        provider_status = str(result.get("provider_status") or session.get("status") or result_status)
        openclaw_status = str(result.get("openclaw_status") or "")
        provider_ok = provider_status == "completed"
        openclaw_ok = openclaw_status == "completed"
        action_is_fetch = action == "fetch_minutes"
        minutes = result.get("minutes") if isinstance(result.get("minutes"), dict) else {}
        content_status = str(result.get("content_status") or minutes.get("content_status") or "")

        if openclaw_ok and content_status == "no_speech_detected":
            event = "meeting_no_speech_detected"
            text = f"实时会议「{title}」已保存音频和诊断，但没有识别到可用发言；OpenClaw 已生成空会议诊断纪要。"
            status = "warning"
        elif not action_is_fetch and provider_status == "stopped" and openclaw_ok:
            event = "meeting_realtime_stopped"
            text = f"实时会议「{title}」已停止，转写和音频已保存，OpenClaw 后处理已生成；可以继续拉取通义听悟 AI 纪要。"
            status = "completed"
        elif provider_ok and result_status == "completed":
            event = "meeting_ai_minutes_ready" if action_is_fetch else "meeting_realtime_completed"
            text = (
                f"实时会议「{title}」的通义听悟 AI 纪要已拉取完成，OpenClaw 后处理已保存。"
                if action_is_fetch
                else f"实时会议「{title}」已停止，通义听悟纪要和 OpenClaw 后处理已保存。"
            )
            status = "completed"
        elif not provider_ok and openclaw_ok:
            event = "meeting_ai_minutes_provider_failed" if action_is_fetch else "meeting_realtime_provider_failed"
            text = (
                f"实时会议「{title}」的通义听悟 AI 纪要未完成；已保存转写和诊断结果，并用 OpenClaw 基于转写生成了后处理输出。"
                if action_is_fetch
                else f"实时会议「{title}」已停止，但通义听悟 AI 纪要未完成；已保存转写和诊断结果，并用 OpenClaw 基于转写生成了后处理输出。"
            )
            status = "warning"
        elif provider_ok and not openclaw_ok:
            event = "meeting_ai_minutes_openclaw_failed" if action_is_fetch else "meeting_realtime_openclaw_failed"
            text = (
                f"实时会议「{title}」的通义听悟 AI 纪要已拉取，但 OpenClaw 后处理未完成，请查看任务和审计日志。"
                if action_is_fetch
                else f"实时会议「{title}」已停止，通义听悟纪要已保存，但 OpenClaw 后处理未完成，请查看任务和审计日志。"
            )
            status = "failed"
        else:
            event = "meeting_ai_minutes_failed" if action_is_fetch else "meeting_realtime_failed"
            text = (
                f"实时会议「{title}」的 AI 纪要拉取和 OpenClaw 后处理均未完成，请查看任务和审计日志。"
                if action_is_fetch
                else f"实时会议「{title}」已停止，但通义听悟 AI 纪要和 OpenClaw 后处理均未完成，请查看任务和审计日志。"
            )
            status = "failed"

        transcript_path = str(session.get("transcript_path") or "")
        tingwu_minutes_path = str(session.get("minutes_path") or "")
        openclaw_minutes_path = str(minutes.get("path") or result.get("path") or "")
        provider_error = redact_sensitive_text(str(session.get("error") or result.get("provider_error") or minutes.get("provider_error") or ""))[:1000]
        openclaw_error = redact_sensitive_text(str(minutes.get("error") or result.get("error") or ""))[:1000]
        error = provider_error or openclaw_error
        return {
            "event": event,
            "text": text,
            "status": status,
            "attachment": "",
            "payload": {
                "meeting_id": meeting_id,
                "status": result_status,
                "provider_status": provider_status,
                "openclaw_status": openclaw_status,
                "content_status": content_status,
                "transcript_path": transcript_path,
                "tingwu_minutes_path": tingwu_minutes_path,
                "openclaw_minutes_path": openclaw_minutes_path,
                "manifest_path": str(result.get("manifest_path") or ""),
                "error": error,
                "provider_error": provider_error,
                "openclaw_error": openclaw_error,
            },
        }

    def register_tingwu_outputs(self, session: dict[str, object], ctx: RequestContext, *, run_followup: bool) -> dict[str, object]:
        title = str(session.get("title") or "Tingwu Meeting")
        meeting_id = str(session.get("meeting_id") or "")
        transcript_path = str(session.get("transcript_path") or "")
        transcript_workspace = self.workspace_relative_path(transcript_path)
        capture_status = tingwu_capture_status(session)
        provider_status = str(session.get("status") or "completed")
        ai_minutes = session.get("ai_minutes") if isinstance(session.get("ai_minutes"), dict) else {}
        tingwu_minutes = normalize_minutes_payload(ai_minutes) if ai_minutes else {"summary": "", "decisions": [], "action_items": []}
        transcript_file_has_content = self.tingwu_transcript_file_has_content(transcript_path)
        self.upsert_meeting_step_task(
            title,
            transcript_workspace,
            "realtime_capture",
            capture_status,
            session,
            meeting_id=meeting_id,
            provider="tongyi_tingwu",
        )
        fallback_transcript: dict[str, object] | None = None
        empty_transcript_import: dict[str, object] | None = None
        parsed: dict[str, object] | None = None
        parsed_count = 0
        parse_error = ""

        if transcript_workspace:
            try:
                parsed = self.runtime.meeting.parse_transcript_file(transcript_workspace, title, list_string(session.get("participants")) or ["Unknown"])
                parsed_count = safe_int(parsed.get("parsed_count"), 0)
            except Exception as exc:
                parsed = None
                parsed_count = 0
                parse_error = str(exc)[:1000]
                self.record_audit("meeting_realtime_import_transcript", "error", transcript_workspace, {"error": str(exc)[:1000]}, ctx)

        if parsed_count <= 0:
            fallback_transcript = self.create_tingwu_asr_fallback_transcript(session, ctx)
            if fallback_transcript.get("status") == "completed":
                transcript_path = str(fallback_transcript.get("path") or transcript_path)
                transcript_workspace = str(fallback_transcript.get("workspace_name") or self.workspace_relative_path(transcript_path))
                transcript_file_has_content = self.tingwu_transcript_file_has_content(transcript_path)
                session = {
                    **session,
                    "transcript_path": transcript_path,
                    "realtime_transcript": fallback_transcript.get("transcript_text") or "",
                    "transcript_fallback": fallback_transcript,
                }
                try:
                    parsed = self.runtime.meeting.parse_transcript_file(transcript_workspace, title, list_string(session.get("participants")) or ["Unknown"])
                    parsed_count = safe_int(parsed.get("parsed_count"), 0)
                except Exception as exc:
                    parsed = None
                    parsed_count = 0
                    parse_error = str(exc)[:1000]
                    fallback_transcript = {**fallback_transcript, "parse_error": str(exc)}

        if parsed_count > 0 and parsed is not None and transcript_workspace:
            self.upsert_meeting_step_task(
                title,
                transcript_workspace,
                "import_transcript",
                "completed",
                {
                    **parsed,
                    "source": "asr_fallback" if fallback_transcript else "tongyi_tingwu_realtime",
                    **({"fallback_transcript": fallback_transcript} if fallback_transcript else {}),
                },
                meeting_id=meeting_id,
                provider="tongyi_tingwu",
            )
        elif transcript_workspace and not transcript_file_has_content and Path(transcript_path).expanduser().is_file() and provider_status in {"completed", "stopped"}:
            empty_transcript_import = self.build_empty_tingwu_import_result(
                session=session,
                transcript_workspace=transcript_workspace,
                parsed_count=parsed_count,
                fallback_transcript=fallback_transcript,
                parse_error=parse_error,
            )
            self.upsert_meeting_step_task(
                title,
                transcript_workspace,
                "import_transcript",
                "completed",
                empty_transcript_import,
                meeting_id=meeting_id,
                provider="tongyi_tingwu",
            )
        else:
            import_error = "Tingwu transcript is outside workspace, missing, or could not be parsed."
            if transcript_workspace and not transcript_file_has_content and Path(transcript_path).expanduser().is_file():
                import_error = "Tingwu transcript produced no speaker turns; OpenClaw follow-up was blocked."
            self.upsert_meeting_step_task(
                title,
                transcript_path,
                "import_transcript",
                "failed",
                {
                    "status": "failed",
                    "error": import_error,
                    "transcript_path": transcript_path,
                    "parse_error": parse_error,
                    "fallback_transcript": fallback_transcript,
                },
                meeting_id=meeting_id,
                provider="tongyi_tingwu",
            )
        result_status = provider_status or "completed"
        minutes_step_status = result_status
        minutes_result: dict[str, object] = {
            "status": result_status,
            "provider_status": provider_status,
            "title": title,
            "provider": "tongyi_tingwu",
            "meeting_id": meeting_id,
            "provider_task_id": str(session.get("task_id") or ""),
            "transcript_path": transcript_path,
            "transcript": transcript_workspace,
            "tingwu_minutes_path": str(session.get("minutes_path") or ""),
            "tingwu_minutes": tingwu_minutes,
            "ai_minutes": ai_minutes,
            "realtime_transcript": str(session.get("realtime_transcript") or ""),
            "transcript_fallback": fallback_transcript,
            "output_dir": str(session.get("output_dir") or ""),
            "provider_error": str(session.get("error") or ""),
        }
        if parsed_count <= 0 and empty_transcript_import is None:
            minutes_result["status"] = "failed"
            minutes_step_status = "failed"
            minutes_result["openclaw_status"] = "failed"
            minutes_result["error"] = "Transcript import failed or produced no speaker turns; OpenClaw minutes were not generated."
        else:
            try:
                if parsed_count > 0:
                    generated = self.runtime.meeting.generate_minutes()
                    generated = self.materialize_tingwu_workspace_file(
                        generated,
                        output_dir=str(session.get("output_dir") or ""),
                        filename="openclaw_minutes.md",
                        meeting_id=meeting_id,
                        ctx=ctx,
                    )
                else:
                    generated = self.create_empty_tingwu_openclaw_minutes(
                        session=session,
                        transcript_workspace=transcript_workspace,
                        import_result=empty_transcript_import or {},
                        ctx=ctx,
                    )
                if provider_status == "completed":
                    result_status = "completed"
                    minutes_step_status = "completed"
                elif provider_status == "stopped":
                    result_status = "stopped"
                    minutes_step_status = "completed"
                else:
                    result_status = provider_status or "completed"
                    minutes_step_status = result_status
                minutes_result.update({
                    "status": result_status,
                    "openclaw_status": "completed",
                    "path": generated.get("path"),
                    "turn_count": generated.get("turn_count"),
                    "decisions": generated.get("decisions", []),
                    "action_items": generated.get("action_items", []),
                    "speaker_counts": generated.get("speaker_counts", {}),
                })
                if generated.get("content_status"):
                    minutes_result["content_status"] = generated.get("content_status")
                    minutes_result["message"] = str(generated.get("message") or "")
                if generated.get("diagnostics"):
                    minutes_result["diagnostics"] = generated.get("diagnostics")
                if generated.get("quality_notes"):
                    minutes_result["quality_notes"] = generated.get("quality_notes")
                if generated.get("transcript_fallback"):
                    minutes_result["transcript_fallback"] = generated.get("transcript_fallback")
                if provider_status == "stopped" and not generated.get("content_status"):
                    minutes_result["message"] = "Realtime capture stopped; Tongyi Tingwu AI minutes have not been fetched yet."
                elif provider_status != "completed":
                    minutes_result["error"] = str(session.get("error") or "Tongyi Tingwu provider did not complete; OpenClaw fallback outputs were generated from transcript.")
            except Exception as exc:
                minutes_result["status"] = "failed"
                minutes_step_status = "failed"
                minutes_result["openclaw_status"] = "failed"
                minutes_result["error"] = str(exc)
        minutes_task = self.upsert_meeting_step_task(
            title,
            transcript_workspace or transcript_path,
            "minutes",
            minutes_step_status,
            minutes_result,
            meeting_id=meeting_id,
            provider="tongyi_tingwu",
        )
        outputs: list[dict[str, object]] = []
        for key, output_type in (
            ("transcript_path", "markdown"),
            ("audio_path", "wav"),
            ("minutes_path", "markdown"),
        ):
            path_value = str(session.get(key) or "")
            if path_value:
                outputs.append({"path": path_value, "type": output_type, "source": "tongyi_tingwu"})
        output_dir = str(session.get("output_dir") or "")
        if output_dir:
            outputs.append({"path": str(Path(output_dir) / "session.json"), "type": "json", "source": "tongyi_tingwu"})
        outputs.extend(collect_outputs(minutes_result))
        followup_task: dict[str, object] | None = None
        followup: dict[str, object] | None = None
        if minutes_result.get("openclaw_status") == "completed":
            decisions = [str(item) for item in list_string(minutes_result.get("decisions"))]
            action_items = [str(item) for item in list_string(minutes_result.get("action_items"))]
            decisions_output = self.write_meeting_items_output(
                title,
                transcript_workspace,
                "decisions",
                decisions,
                minutes_result,
                output_dir=str(session.get("output_dir") or ""),
                meeting_id=meeting_id,
                ctx=ctx,
            )
            self.upsert_meeting_step_task(
                title,
                transcript_workspace,
                "decisions",
                "waiting_confirmation",
                decisions_output,
                meeting_id=meeting_id,
                provider="tongyi_tingwu",
            )
            outputs.extend(collect_outputs(decisions_output))
            actions_output = self.write_meeting_items_output(
                title,
                transcript_workspace,
                "action_items",
                action_items,
                minutes_result,
                output_dir=str(session.get("output_dir") or ""),
                meeting_id=meeting_id,
                ctx=ctx,
            )
            self.upsert_meeting_step_task(
                title,
                transcript_workspace,
                "action_items",
                "completed",
                actions_output,
                meeting_id=meeting_id,
                provider="tongyi_tingwu",
            )
            outputs.extend(collect_outputs(actions_output))

            if run_followup and transcript_workspace:
                try:
                    reminders: dict[str, object] | None = None
                    projection: dict[str, object] | None = None
                    if minutes_result.get("content_status") == "no_speech_detected":
                        followup = self.create_empty_tingwu_followup_outputs(
                            session=session,
                            minutes_result=minutes_result,
                            transcript_workspace=transcript_workspace,
                            ctx=ctx,
                        )
                        projection = followup.get("projection") if isinstance(followup.get("projection"), dict) else None
                        reminders = followup.get("reminders") if isinstance(followup.get("reminders"), dict) else None
                    else:
                        projection_dir_before = self.latest_projection_mtime()
                        package = self.runtime.p0.generate_meeting_followup_package(
                            recipient="待填写收件人",
                            create_reminders=True,
                            render_projection=True,
                        )
                        projection = package.get("projection") if isinstance(package.get("projection"), dict) else None
                        if projection is not None:
                            projection = self.materialize_tingwu_projection_output(
                                projection,
                                meeting_id=meeting_id,
                                projection_dir_before=projection_dir_before,
                                ctx=ctx,
                            )
                            package["projection"] = projection
                        followup = {
                            **package,
                            "status": "completed",
                            "source_status": str(package.get("status") or ""),
                            "step": "followup",
                            "meeting_id": meeting_id,
                        }
                        followup = self.materialize_tingwu_followup_outputs(followup, session=session, ctx=ctx)
                        reminders = followup.get("reminders") if isinstance(followup.get("reminders"), dict) else None
                        package_minutes = followup.get("minutes") if isinstance(followup.get("minutes"), dict) else {}
                        package_transcript = followup.get("transcript") if isinstance(followup.get("transcript"), dict) else {}
                        followup["required_output_paths"] = {
                            "openclaw_minutes": str(package_minutes.get("path") or ""),
                            "transcript_export": str(package_transcript.get("path") or ""),
                            "email_draft": str(followup.get("email_draft_path") or ""),
                            "reminders": str(reminders.get("store_path") if isinstance(reminders, dict) else ""),
                            "projection_confirmation": str(projection.get("path") if isinstance(projection, dict) else ""),
                        }
                    followup_task = self.upsert_meeting_step_task(
                        title,
                        transcript_workspace,
                        "followup",
                        "completed",
                        followup,
                        meeting_id=meeting_id,
                        provider="tongyi_tingwu",
                    )
                    outputs.extend(collect_outputs(followup))
                    if reminders is not None:
                        reminders_output = {
                            "status": "completed",
                            "step": "reminders",
                            "meeting_id": meeting_id,
                            "message": "已创建本地 reminder 草稿，不会同步外部日历或自动通知。",
                            **reminders,
                        }
                        self.upsert_meeting_step_task(
                            title,
                            transcript_workspace,
                            "reminders",
                            "completed",
                            reminders_output,
                            meeting_id=meeting_id,
                            provider="tongyi_tingwu",
                        )
                        outputs.extend(collect_outputs(reminders_output))
                    projection = followup.get("projection") if isinstance(followup.get("projection"), dict) else None
                    if projection is not None:
                        projection_output = {
                            "status": "completed",
                            "step": "projection_confirmation",
                            "meeting_id": meeting_id,
                            "projection": projection,
                            "path": projection.get("path"),
                            "decisions": decisions,
                            "action_items": action_items,
                        }
                        self.upsert_meeting_step_task(
                            title,
                            transcript_workspace,
                            "projection_confirmation",
                            "completed",
                            projection_output,
                            meeting_id=meeting_id,
                            provider="tongyi_tingwu",
                        )
                        outputs.extend(collect_outputs(projection_output))
                except Exception as exc:
                    followup = {"status": "failed", "step": "followup", "meeting_id": meeting_id, "error": str(exc)}
                    self.upsert_meeting_step_task(
                        title,
                        transcript_workspace,
                        "followup",
                        "failed",
                        followup,
                        meeting_id=meeting_id,
                        provider="tongyi_tingwu",
                    )
        self.record_audit(
            "meeting_realtime_import_transcript",
            status_to_audit(str(minutes_result.get("status"))),
            transcript_workspace or transcript_path,
            {"meeting_id": meeting_id, "task_id": minutes_task["task_id"], "outputs": len(outputs)},
            ctx,
        )
        job = self.find_aggregated_meeting_job(transcript_workspace or transcript_path, meeting_id=meeting_id) or self.meeting_job_from_task(minutes_task)
        manifest_path = self.write_tingwu_meeting_manifest(
            session=session,
            minutes=minutes_result,
            followup=followup,
            outputs=outputs,
            job=job,
            ctx=ctx,
        )
        if manifest_path:
            outputs.append({"path": manifest_path, "type": "json"})
            if followup_task is not None and isinstance(followup, dict):
                self.update_task(str(followup_task.get("task_id") or ""), output={**followup, "manifest_path": manifest_path})
        return {
            "status": minutes_result.get("status"),
            "provider_status": minutes_result.get("provider_status"),
            "openclaw_status": minutes_result.get("openclaw_status"),
            "content_status": minutes_result.get("content_status"),
            "task_id": minutes_task["task_id"],
            "job": job,
            "session": session,
            "minutes": minutes_result,
            "followup": followup,
            "manifest_path": manifest_path,
            "outputs": outputs,
        }

    def build_empty_tingwu_import_result(
        self,
        *,
        session: dict[str, object],
        transcript_workspace: str,
        parsed_count: int,
        fallback_transcript: dict[str, object] | None,
        parse_error: str = "",
    ) -> dict[str, object]:
        fallback_reason = ""
        if isinstance(fallback_transcript, dict):
            fallback_reason = str(fallback_transcript.get("reason") or fallback_transcript.get("status") or "")
        diagnostics = self.empty_tingwu_diagnostics(session=session, fallback_transcript=fallback_transcript)
        message = (
            "实时会议音频和转写文件已保存，但通义听悟实时转写没有返回最终发言；"
            "本地 ASR fallback 也没有识别到可用语音。OpenClaw 将生成空会议诊断纪要，不伪造发言。"
        )
        return {
            "status": "completed",
            "content_status": "no_speech_detected",
            "message": message,
            "transcript": transcript_workspace,
            "parsed_count": parsed_count,
            "meeting_mode_enabled": True,
            "fallback_transcript": fallback_transcript,
            "fallback_reason": fallback_reason,
            "parse_error": parse_error,
            "diagnostics": diagnostics,
            "quality_notes": [
                "没有可导入的 speaker turns。",
                "决策和行动项保持为空，需要重新录制或导入会议文本后再生成正式纪要。",
            ],
            "confirmation": {"required": False},
        }

    def tingwu_transcript_file_has_content(self, transcript_path: str) -> bool:
        value = str(transcript_path or "").strip()
        if not value:
            return False
        path = Path(value).expanduser()
        if not path.is_file():
            return False
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        for line in text.splitlines():
            clean = line.strip()
            if clean and not clean.startswith("#"):
                return True
        return False

    def create_empty_tingwu_openclaw_minutes(
        self,
        *,
        session: dict[str, object],
        transcript_workspace: str,
        import_result: dict[str, object],
        ctx: RequestContext,
    ) -> dict[str, object]:
        title = str(session.get("title") or "Tingwu Meeting")
        meeting_id = str(session.get("meeting_id") or "")
        diagnostics = import_result.get("diagnostics") if isinstance(import_result.get("diagnostics"), dict) else self.empty_tingwu_diagnostics(session=session, fallback_transcript=None)
        quality_notes = [str(item) for item in list_string(import_result.get("quality_notes"))] or [
            "通义听悟和本地 ASR 均未识别到可用发言。",
        ]
        lines = [
            f"# {title}",
            "",
            "## 状态",
            "- content_status: no_speech_detected",
            "- OpenClaw 后处理已完成：没有生成虚假发言、决策或行动项。",
            "",
            "## 诊断",
            f"- transcript: {transcript_workspace or '-'}",
            f"- audio_seconds: {diagnostics.get('audio_seconds', 0)}",
            f"- audio_bytes: {diagnostics.get('audio_bytes', 0)}",
            f"- websocket_audio_frames: {diagnostics.get('websocket_audio_frames', 0)}",
            f"- audio_rms: {diagnostics.get('audio_rms', 0)}",
            f"- audio_peak: {diagnostics.get('audio_peak', 0)}",
            f"- realtime_final_turns: {diagnostics.get('realtime_final_turns', 0)}",
            f"- asr_fallback_status: {diagnostics.get('asr_fallback_status', '-')}",
            f"- asr_fallback_reason: {diagnostics.get('asr_fallback_reason', '-')}",
            "",
            "## Decisions",
            "- 暂无明确决策，需要重新录制或导入会议文本后补充。",
            "",
            "## Action Items",
            "- 暂无明确待办，需要重新录制或导入会议文本后补充。",
            "",
            "## Quality Notes",
            *[f"- {item}" for item in quality_notes],
            "",
        ]
        path = self.write_meeting_output_text(
            str(session.get("output_dir") or ""),
            "openclaw_minutes.md",
            "\n".join(lines),
            action="meeting.minutes_generate",
            meeting_id=meeting_id,
            ctx=ctx,
        )
        payload = {
            "status": "completed",
            "provider": "openclaw",
            "content_status": "no_speech_detected",
            "path": str(path),
            "title": title,
            "turn_count": 0,
            "speaker_counts": {},
            "decisions": [],
            "action_items": [],
            "diagnostics": diagnostics,
            "quality_notes": quality_notes,
            "transcript_fallback": import_result.get("fallback_transcript"),
            "message": "No usable speaker turns were available; generated an auditable empty meeting report.",
        }
        self.record_audit("meeting.minutes_generated", "ok", str(path), payload, ctx)
        return payload

    def create_empty_tingwu_followup_outputs(
        self,
        *,
        session: dict[str, object],
        minutes_result: dict[str, object],
        transcript_workspace: str,
        ctx: RequestContext,
    ) -> dict[str, object]:
        title = str(session.get("title") or "Tingwu Meeting")
        meeting_id = str(session.get("meeting_id") or "")
        output_dir = str(session.get("output_dir") or "")
        diagnostics = minutes_result.get("diagnostics") if isinstance(minutes_result.get("diagnostics"), dict) else {}
        transcript_export_payload = {
            "title": title,
            "participants": list_string(session.get("participants")) or ["Unknown"],
            "started_at": session.get("started_at") or session.get("created_at") or "",
            "transcript": [],
            "content_status": "no_speech_detected",
            "source_transcript": transcript_workspace,
            "diagnostics": diagnostics,
        }
        transcript_export_path = self.write_meeting_output_json(
            output_dir,
            "followup_transcript.json",
            transcript_export_payload,
            action="meeting.transcript_export",
            meeting_id=meeting_id,
            ctx=ctx,
        )
        email_body = "\n".join(
            [
                f"# {title} Follow-up Email Draft",
                "",
                "To: 待填写收件人",
                f"Subject: {title} - 会议记录待补充",
                "",
                "本次实时会议已保存音频和诊断，但没有识别到可用发言。",
                "",
                "请重新录制会议、导入会议文本，或人工补充纪要后再发送正式会后邮件。",
                "",
                "## Diagnostics",
                f"- audio_seconds: {diagnostics.get('audio_seconds', 0)}",
                f"- websocket_audio_frames: {diagnostics.get('websocket_audio_frames', 0)}",
                f"- asr_fallback_status: {diagnostics.get('asr_fallback_status', '-')}",
                f"- asr_fallback_reason: {diagnostics.get('asr_fallback_reason', '-')}",
                "",
            ]
        )
        email_path = self.write_meeting_output_text(
            output_dir,
            "followup_email.md",
            email_body,
            action="p0.meeting_followup_email_write",
            meeting_id=meeting_id,
            ctx=ctx,
        )
        reminders = {
            "status": "completed",
            "count": 0,
            "items": [],
            "message": "没有识别到行动项，因此没有创建 reminder 草稿。",
        }
        reminder_path = self.write_meeting_output_json(
            output_dir,
            "reminders.json",
            reminders,
            action="meeting.reminders_snapshot",
            meeting_id=meeting_id,
            ctx=ctx,
        )
        reminders = {**reminders, "store_path": str(reminder_path)}
        projection = self.runtime.projection.render_status_card(
            f"{title} - 会议诊断",
            "no_speech_detected",
            [
                "音频和诊断已保存。",
                "没有识别到可用发言。",
                "请重新录制或导入会议文本后生成正式纪要。",
            ],
            accent="amber",
        )
        projection = self.materialize_tingwu_projection_output(
            projection,
            meeting_id=meeting_id,
            projection_dir_before=0,
            ctx=ctx,
        )
        followup = {
            "status": "completed",
            "source_status": "no_speech_detected",
            "step": "followup",
            "meeting_id": meeting_id,
            "content_status": "no_speech_detected",
            "minutes": minutes_result,
            "transcript": {"status": "completed", "path": str(transcript_export_path), "content_status": "no_speech_detected"},
            "email": {"status": "completed", "email_draft_path": str(email_path), "content_status": "no_speech_detected"},
            "email_draft_path": str(email_path),
            "reminders": reminders,
            "projection": projection,
            "required_output_paths": {
                "openclaw_minutes": str(minutes_result.get("path") or ""),
                "transcript_export": str(transcript_export_path),
                "email_draft": str(email_path),
                "reminders": str(reminder_path),
                "projection_confirmation": str(projection.get("path") if isinstance(projection, dict) else ""),
            },
            "message": "没有识别到可用发言；已生成诊断 follow-up 草稿，不自动发送。",
        }
        self.record_audit("p0.meeting_followup_package", "ok", title, followup, ctx)
        return followup

    def empty_tingwu_diagnostics(
        self,
        *,
        session: dict[str, object],
        fallback_transcript: dict[str, object] | None,
    ) -> dict[str, object]:
        transcript_items = session.get("transcript") if isinstance(session.get("transcript"), list) else []
        fallback = fallback_transcript if isinstance(fallback_transcript, dict) else {}
        return {
            "provider_status": str(session.get("status") or ""),
            "audio_seconds": float(session.get("audio_seconds") or 0),
            "audio_bytes": safe_int(session.get("audio_bytes"), 0),
            "websocket_audio_frames": safe_int(session.get("websocket_audio_frames"), 0),
            "audio_rms": safe_int(session.get("audio_rms"), 0),
            "audio_peak": safe_int(session.get("audio_peak"), 0),
            "realtime_final_turns": len([item for item in transcript_items if isinstance(item, dict) and item.get("final")]),
            "realtime_turns": len(transcript_items),
            "asr_fallback_status": str(fallback.get("status") or "not_run"),
            "asr_fallback_reason": str(fallback.get("reason") or ""),
            "asr_fallback_provider": str(fallback.get("provider") or self.runtime.config.asr_provider or ""),
        }

    def create_tingwu_asr_fallback_transcript(self, session: dict[str, object], ctx: RequestContext) -> dict[str, object]:
        meeting_id = str(session.get("meeting_id") or "")
        audio_path_value = str(session.get("audio_path") or "")
        audio_path = Path(audio_path_value).expanduser().resolve() if audio_path_value else None
        audio_seconds = float(session.get("audio_seconds") or 0)
        if audio_path is None or not audio_path.is_file():
            result = {"status": "failed", "reason": "audio_missing", "audio_path": audio_path_value}
            self.record_audit("meeting_realtime_asr_fallback", "failed", meeting_id, result, ctx)
            return result
        if audio_seconds <= 0 or int(session.get("audio_bytes") or 0) <= 0:
            result = {"status": "failed", "reason": "audio_empty", "audio_path": str(audio_path), "audio_seconds": audio_seconds}
            self.record_audit("meeting_realtime_asr_fallback", "failed", meeting_id, result, ctx)
            return result
        try:
            prepared_audio = self.prepare_audio_for_asr(audio_path, session, ctx)
            transcript_text = self.transcribe_meeting_audio(prepared_audio).strip()
        except Exception as exc:
            result = {"status": "failed", "reason": "asr_error", "audio_path": str(audio_path), "error": str(exc)[:1000]}
            self.record_audit("meeting_realtime_asr_fallback", "error", meeting_id, result, ctx)
            return result
        if not transcript_text or transcript_text.lower() in {"none", "null", "undefined"}:
            result = {"status": "failed", "reason": "asr_empty", "audio_path": str(audio_path), "provider": self.runtime.config.asr_provider}
            self.record_audit("meeting_realtime_asr_fallback", "unavailable", meeting_id, result, ctx)
            return result
        transcript_path = self.write_meeting_output_text(
            str(session.get("output_dir") or ""),
            "asr_fallback_transcript.md",
            self.format_asr_fallback_transcript(str(session.get("title") or "Tingwu Meeting"), transcript_text),
            action="meeting_realtime_asr_fallback",
            meeting_id=meeting_id,
            ctx=ctx,
        )
        result = {
            "status": "completed",
            "provider": self.runtime.config.asr_provider,
            "model": self.asr_model_label(),
            "audio_path": str(audio_path),
            "prepared_audio_path": str(prepared_audio),
            "path": str(transcript_path),
            "workspace_name": self.workspace_relative_path(str(transcript_path)),
            "transcript_chars": len(transcript_text),
            "transcript_text": transcript_text,
            "message": "Realtime provider returned no final speaker turns; generated transcript from saved meeting audio via configured ASR API.",
        }
        self.record_audit(
            "meeting_realtime_asr_fallback",
            "ok",
            meeting_id,
            {"provider": result["provider"], "model": result["model"], "chars": len(transcript_text), "path": result["workspace_name"]},
            ctx,
        )
        return result

    def prepare_audio_for_asr(self, audio_path: Path, session: dict[str, object], ctx: RequestContext) -> Path:
        if audio_path.suffix.lower() == ".wav":
            return audio_path
        if shutil.which("ffmpeg") is None:
            return audio_path
        meeting_dir = self.meeting_output_dir(str(session.get("output_dir") or ""), meeting_id=str(session.get("meeting_id") or ""), ctx=ctx)
        output = (meeting_dir / "asr_audio.wav") if meeting_dir is not None else self.runtime.workspace.path_for_new_file("meeting_asr_audio.wav")
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-vn",
            str(output),
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=60)
        if completed.returncode != 0 or not output.exists() or output.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg audio conversion failed: {completed.stderr[:500]}")
        return output

    def transcribe_meeting_audio(self, audio_path: Path) -> str:
        provider = str(self.runtime.config.asr_provider or "").strip().lower()
        if provider == "dashscope":
            from .dashscope_asr import DashScopeASR

            return DashScopeASR(
                api_key=self.runtime.config.dashscope_api_key,
                model=self.runtime.config.dashscope_asr_model,
                sample_rate=self.runtime.config.dashscope_asr_sample_rate or self.runtime.config.tingwu_sample_rate,
            ).transcribe(audio_path, language_hints=["zh", "en"])
        if provider == "groq":
            from .groq_asr import GroqASR

            return GroqASR(api_key=self.runtime.config.groq_api_key).transcribe(audio_path, model=self.runtime.config.asr_model, language="zh")
        if provider == "openai":
            return OpenAIAudioAPI(api_key=self.runtime.config.openai_api_key, base_url=self.runtime.config.openai_base_url).transcribe(
                audio_path,
                model=self.runtime.config.asr_model,
                language="zh",
            )
        raise RuntimeError(f"Unsupported ASR provider: {provider or 'missing'}")

    def asr_model_label(self) -> str:
        provider = str(self.runtime.config.asr_provider or "").strip().lower()
        if provider == "dashscope":
            return self.runtime.config.dashscope_asr_model
        return self.runtime.config.asr_model

    def format_asr_fallback_transcript(self, title: str, transcript_text: str) -> str:
        lines = [f"# {title} ASR Transcript", ""]
        for line in transcript_text.splitlines():
            clean = line.strip()
            if clean:
                lines.append(clean if ":" in clean else f"ASR: {clean}")
        if len(lines) == 2 and transcript_text.strip():
            lines.append(f"ASR: {transcript_text.strip()}")
        return "\n".join(lines) + "\n"

    def api_meeting_minutes(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        target, title = self.load_meeting_transcript(payload, ctx, action="meeting_minutes")
        result = self.runtime.meeting.generate_minutes()
        task = self.create_task("会议纪要", "meeting", "completed", {"transcript": target}, result)
        job = self.create_meeting_job(str(result.get("title") or title), str(target), "minutes", "completed", result)
        self.record_audit("meeting_minutes", "ok", target, {"task_id": task["task_id"], "job_id": job["job_id"]}, ctx)
        return {"status": "completed", "task_id": task["task_id"], "job": job, **result}

    def api_meeting_extract_step(self, step_name: str, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        target, title = self.load_meeting_transcript(payload, ctx, action=f"meeting_{step_name}")
        minutes = self.runtime.meeting.generate_minutes()
        items_key = "decisions" if step_name == "decisions" else "action_items"
        items = [str(item) for item in minutes.get(items_key, [])]
        label = "decisions" if step_name == "decisions" else "action_items"
        filename = safe_filename(title, default="meeting", suffix=f"_{label}.json")
        output_path = self.runtime.workspace.write_json(
            filename,
            {
                "title": title,
                "transcript": target,
                "step": step_name,
                "items": items,
                "source_minutes_path": minutes.get("path"),
                "generated_at": now_iso(),
                "confirmation_required": True,
            },
            action=f"meeting.{step_name}_extract",
        )
        status = "waiting_confirmation" if step_name == "decisions" else "completed"
        result = {
            "status": status,
            "step": step_name,
            items_key: items,
            "items": items,
            "path": str(output_path),
            "source_minutes_path": minutes.get("path"),
            "confirmation": {
                "required": True,
                "summary": "请用户确认后再作为正式会议结论使用。",
            },
            "message": "已从 transcript 生成可审查步骤输出，等待用户确认。" if status == "waiting_confirmation" else "已生成可审查步骤输出。",
        }
        task = self.create_task(
            f"会议工作流：{title}",
            "meeting",
            status,
            {"transcript": target, "step": step_name},
            result,
        )
        job = self.meeting_job_from_task(task)
        self.record_audit(f"meeting_{step_name}", status_to_audit(status), target, {"task_id": task["task_id"], "path": str(output_path), "count": len(items)}, ctx)
        return {"task_id": task["task_id"], "job": job, **result}

    def api_meeting_followup(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        target, title = self.load_meeting_transcript(payload, ctx, action="meeting_followup")
        result = self.runtime.p0.generate_meeting_followup_package(
            recipient=str(payload.get("recipient") or "待填写收件人"),
            create_reminders=bool(payload.get("create_reminders", True)),
            render_projection=bool(payload.get("render_projection", False)),
        )
        status = normalize_task_status(str(result.get("status") or "completed"))
        task = self.create_task("会议跟进包", "meeting", status, {"transcript": target, "step": "followup", "meeting_title": title}, result)
        job = self.create_meeting_job(title, target, "followup", status, result)
        self.record_audit("meeting_followup", status_to_audit(status), target, {"task_id": task["task_id"]}, ctx)
        return {"status": status, "task_id": task["task_id"], "job": job, **result}

    def api_meeting_export_package(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        if not bool(payload.get("authorized", False)):
            result = {
                "status": "needs_confirmation",
                "message": "Export requires explicit user authorization.",
                "confirmation": {"required": True, "scope": "meeting_followup_export"},
            }
            self.record_audit("meeting_export_package", "blocked", "meeting_export", result, ctx)
            return result
        target, title = self.load_meeting_transcript(payload, ctx, action="meeting_export_package")
        package = self.runtime.p0.generate_meeting_followup_package(
            recipient=str(payload.get("recipient") or "待填写收件人"),
            create_reminders=bool(payload.get("create_reminders", True)),
            render_projection=bool(payload.get("render_projection", True)),
        )
        paths = self.collect_existing_workspace_paths(package)
        if not paths:
            result = {"status": "backend_missing", "message": "No meeting outputs are available to export.", "source_status": package.get("status")}
            self.record_audit("meeting_export_package", "blocked", target, result, ctx)
            return result
        zip_path = self.runtime.workspace.path_for_new_file(safe_filename(title, default="meeting", suffix="_followup_export.zip"))
        manifest = {
            "title": title,
            "transcript": target,
            "created_at": now_iso(),
            "source_status": package.get("status"),
            "files": [self.export_archive_name(path) for path in paths],
            "note": "Export package created only after explicit user authorization.",
        }
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for path in paths:
                archive.write(path, arcname=self.export_archive_name(path))
        shared = self.shared_file_dto(
            self.shared_space.put_bytes(zip_path.name, zip_path.read_bytes(), source="meeting_export_package").as_dict()
        )
        result = {
            "status": "completed",
            "path": str(zip_path),
            "shared_file": shared,
            "download_url": f"/api/shared/download?file={urllib.parse.quote(str(shared['relative_path']))}",
            "file_count": len(paths),
            "manifest": manifest,
            "source_package": package,
        }
        task = self.create_task("会议跟进包导出", "meeting", "completed", {"transcript": target, "authorized": True}, result)
        result["task_id"] = task["task_id"]
        self.record_audit("meeting_export_package", "ok", str(zip_path), {"task_id": task["task_id"], "file_count": len(paths), "shared_file": shared.get("relative_path")}, ctx)
        return result

    def api_meeting_send_email(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        if not bool(payload.get("authorized", False)):
            result = {
                "status": "needs_confirmation",
                "message": "Email sending requires explicit user authorization.",
                "confirmation": {"required": True, "scope": "meeting_followup_email_send"},
            }
            self.record_audit("meeting_email_send", "blocked", "meeting_email", result, ctx)
            return result

        recipient = str(payload.get("recipient") or "").strip()
        if not recipient or recipient == "待填写收件人":
            raise ApiError("missing_recipient", "Recipient is required before sending email.", status=400)
        target, title = self.load_meeting_transcript(payload, ctx, action="meeting_email_send")
        package = self.runtime.p0.generate_meeting_followup_package(
            recipient=recipient,
            create_reminders=False,
            render_projection=False,
        )
        email_path = Path(str(package.get("email_draft_path") or ""))
        if not email_path.is_file() or not email_path.is_relative_to(self.runtime.workspace.root):
            result = {"status": "backend_missing", "message": "Email draft was not generated.", "source_package": package}
            self.record_audit("meeting_email_send", "blocked", target, result, ctx)
            return result
        smtp_result = self.send_email_draft(email_path=email_path, recipient=recipient, subject=f"{title} 会后跟进")
        status = str(smtp_result.get("status") or "backend_missing")
        result = {
            "status": status,
            "recipient": recipient,
            "email_draft_path": str(email_path),
            "provider": "smtp",
            "smtp": smtp_result,
            "source_package": package,
        }
        task = self.create_task("会议邮件发送", "meeting", normalize_task_status(status), {"transcript": target, "recipient": recipient, "authorized": True}, result)
        result["task_id"] = task["task_id"]
        self.record_audit("meeting_email_send", status_to_audit(status), target, {"task_id": task["task_id"], "recipient": recipient, "smtp_status": status}, ctx)
        return result

    def collect_existing_workspace_paths(self, payload: object) -> list[Path]:
        paths: list[Path] = []
        seen: set[Path] = set()
        workspace = self.runtime.workspace.root.resolve()
        projection_root = self.runtime.config.projection_dir.resolve()

        def add_path(value: str) -> None:
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = (workspace / candidate).resolve()
            else:
                candidate = candidate.resolve()
            if not candidate.is_file():
                return
            if not (candidate.is_relative_to(workspace) or candidate.is_relative_to(projection_root)):
                return
            if candidate in seen:
                return
            seen.add(candidate)
            paths.append(candidate)

        def visit(value: object, key: str = "") -> None:
            if isinstance(value, str):
                if key == "path" or key.endswith("_path") or key.endswith("Path"):
                    add_path(value)
                return
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    visit(child_value, str(child_key))
                return
            if isinstance(value, list):
                for item in value:
                    visit(item, key)

        visit(payload)
        return paths

    def send_email_draft(self, *, email_path: Path, recipient: str, subject: str) -> dict[str, object]:
        config = self.runtime.config
        if not config.smtp_host or not config.smtp_from:
            return {
                "status": "backend_missing",
                "message": "OPENCLAW_SMTP_HOST and OPENCLAW_SMTP_FROM/OPENCLAW_SMTP_USERNAME are required before sending email.",
                "configured": False,
                "host_configured": bool(config.smtp_host),
                "from_configured": bool(config.smtp_from),
            }
        draft = email_path.read_text(encoding="utf-8", errors="replace")
        parsed_subject = extract_email_subject(draft) or subject
        message = EmailMessage()
        message["From"] = config.smtp_from
        message["To"] = recipient
        message["Subject"] = parsed_subject
        message.set_content(draft)
        try:
            with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
                if config.smtp_tls:
                    smtp.starttls()
                if config.smtp_username:
                    smtp.login(config.smtp_username, config.smtp_password)
                smtp.send_message(message)
        except Exception as exc:
            return {
                "status": "failed",
                "message": str(exc)[:1000],
                "configured": True,
                "host": config.smtp_host,
                "port": config.smtp_port,
                "tls": config.smtp_tls,
            }
        return {
            "status": "completed",
            "configured": True,
            "host": config.smtp_host,
            "port": config.smtp_port,
            "tls": config.smtp_tls,
            "subject": parsed_subject,
        }

    def export_archive_name(self, path: Path) -> str:
        resolved = path.resolve()
        workspace = self.runtime.workspace.root.resolve()
        projection_root = self.runtime.config.projection_dir.resolve()
        if resolved.is_relative_to(workspace):
            return str(resolved.relative_to(workspace))
        if resolved.is_relative_to(projection_root):
            return f"projection/{resolved.relative_to(projection_root)}"
        return safe_filename(resolved.name, default="artifact")

    def api_meeting_reminders(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        target, title = self.load_meeting_transcript(payload, ctx, action="meeting_reminders")
        minutes = self.runtime.meeting.generate_minutes()
        action_items = [str(item) for item in minutes.get("action_items", [])]
        reminders = self.runtime.daily.create_reminders_from_action_items(action_items) if action_items else {"count": 0, "reminders": []}
        output_path = self.runtime.workspace.write_json(
            safe_filename(title, default="meeting", suffix="_reminders.json"),
            {
                "title": title,
                "transcript": target,
                "source_minutes_path": minutes.get("path"),
                "created_at": now_iso(),
                **reminders,
            },
            action="meeting.reminders_create",
        )
        result = {
            "status": "completed",
            "step": "reminders",
            "path": str(output_path),
            "source_minutes_path": minutes.get("path"),
            "reminders": reminders.get("reminders", []),
            "count": reminders.get("count", 0),
            "message": "已创建本地 reminder 草稿，不会同步外部日历或自动通知。",
        }
        task = self.create_task(
            f"会议工作流：{title}",
            "meeting",
            "completed",
            {"transcript": target, "step": "reminders"},
            result,
        )
        self.record_audit("meeting_reminders", "ok", target, {"task_id": task["task_id"], "path": str(output_path), "count": result["count"]}, ctx)
        return {"task_id": task["task_id"], "job": self.meeting_job_from_task(task), **result}

    def api_meeting_projection_confirmation(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        target = "active_meeting"
        title = str(payload.get("title") or "会议确认")
        decisions = list_string(payload.get("decisions"))
        action_items = list_string(payload.get("action_items"))
        if payload.get("transcript") or payload.get("file_path"):
            target, title = self.load_meeting_transcript(payload, ctx, action="meeting_projection_confirmation")
            minutes = self.runtime.meeting.generate_minutes()
            decisions = decisions or [str(item) for item in minutes.get("decisions", [])]
            action_items = action_items or [str(item) for item in minutes.get("action_items", [])]
        result = self.runtime.projection.render_confirmation(title, decisions, action_items)
        output = {"status": "completed", "step": "projection_confirmation", "projection": result, "path": result.get("path"), "decisions": decisions, "action_items": action_items}
        task = self.create_task(
            f"会议工作流：{title}",
            "meeting",
            "completed",
            {"transcript": target, "step": "projection_confirmation"},
            output,
        )
        self.record_audit("meeting_projection_confirmation", "ok", str(result.get("path")), {"title": title, "task_id": task["task_id"]}, ctx)
        return {"task_id": task["task_id"], "job": self.meeting_job_from_task(task), **output}

    def api_meeting_confirm_step(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        task_id = sanitize_id(require_string(payload, "task_id"))
        task = self.api_task_get(task_id, ctx)
        if task.get("type") != "meeting":
            self.record_audit("meeting_confirm_step", "blocked", task_id, {"reason": "not a meeting task"}, ctx)
            raise ApiError("blocked", "Only meeting workflow tasks can be confirmed here.", status=403)
        output = task.get("output") if isinstance(task.get("output"), dict) else {}
        input_payload = task.get("input") if isinstance(task.get("input"), dict) else {}
        output["confirmation"] = {
            **(output.get("confirmation") if isinstance(output.get("confirmation"), dict) else {}),
            "required": False,
            "confirmed": True,
            "confirmed_at": now_iso(),
            "confirmed_by": ctx.actor,
            "note": str(payload.get("note") or "用户已确认会议步骤。"),
        }
        task = self.update_task(task_id, status="completed", progress=1.0, output=output)
        step_name = str(input_payload.get("step") or "meeting_step")
        self.record_audit("meeting_confirm_step", "ok", task_id, {"step": step_name, "transcript": input_payload.get("transcript")}, ctx)
        return {"status": "completed", "task_id": task_id, "step": step_name, "job": self.meeting_job_from_task(task), "confirmation": output["confirmation"]}

    def api_meeting_jobs(self, ctx: RequestContext) -> dict[str, object]:
        jobs = self.aggregate_meeting_jobs(self.load_tasks(limit=100))
        self.record_audit("meeting_jobs.list", "ok", "meeting_jobs", {"count": len(jobs)}, ctx)
        return {"items": jobs, "total": len(jobs)}

    def api_meeting_job(self, job_id: str, ctx: RequestContext) -> dict[str, object]:
        for job in self.api_meeting_jobs(ctx)["items"]:
            if job.get("job_id") == job_id:
                return job
        raise ApiError("not_found", "Meeting job not found.", status=404)

    def api_projection_card(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = require_string(payload, "title")
        mode = str(payload.get("type") or payload.get("mode") or "status")
        if mode == "action":
            mode = "action_card"
        if mode == "countdown":
            result = self.runtime.projection.render_countdown(
                title,
                int(payload.get("duration_seconds") or payload.get("seconds") or 300),
                message=str(payload.get("message") or ""),
            )
        elif mode in {"confirmation", "action_card"}:
            result = self.runtime.projection.render_action_card(
                title,
                list_string(payload.get("actions") or payload.get("message")),
                decisions=list_string(payload.get("decisions")),
            )
        elif mode in {"status", "status_card"}:
            result = self.runtime.projection.render_status_card(
                title,
                str(payload.get("message") or payload.get("status") or "ready"),
                details=list_string(payload.get("details")),
                accent=str(payload.get("accent") or "blue"),
            )
        else:
            result = self.runtime.projection.render_markdown(title, str(payload.get("body") or payload.get("message") or ""), mode)
        task = self.create_task("投影卡片", "projection", "completed", {"title": title, "mode": mode}, result)
        self.record_audit("projection_card", "ok", str(result.get("path")), {"title": title, "mode": mode, "task_id": task["task_id"]}, ctx)
        return {"status": "completed", "task_id": task["task_id"], **result}

    def api_projection_markdown_file(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(require_file_path(payload), ctx, action="projection_markdown_file")
        suffix = safe.path.suffix.lower()
        if suffix not in {".md", ".markdown", ".txt"}:
            self.record_audit(
                "projection_markdown_file",
                "blocked",
                safe.workspace_name,
                {"reason": "unsupported_suffix", "suffix": suffix},
                ctx,
            )
            raise ApiError(
                "unsupported_projection_file",
                "Only Markdown or text files can be projected directly. For PPT, use the browser screen-capture PPT summary flow.",
                status=415,
                details={"suffix": suffix, "supported_suffixes": [".md", ".markdown", ".txt"]},
            )
        if safe.path.stat().st_size > 1_000_000:
            self.record_audit(
                "projection_markdown_file",
                "blocked",
                safe.workspace_name,
                {"reason": "file_too_large", "size_bytes": safe.path.stat().st_size},
                ctx,
            )
            raise ApiError("projection_file_too_large", "Projection Markdown/text file must be 1 MB or smaller.", status=413)
        try:
            body = safe.path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            self.record_audit("projection_markdown_file", "blocked", safe.workspace_name, {"reason": "not_utf8"}, ctx)
            raise ApiError("unsupported_projection_file", "Projection Markdown/text file must be UTF-8.", status=415) from exc
        title = str(payload.get("title") or safe.path.stem or "Workspace Markdown")
        result = self.runtime.projection.render_markdown(
            title,
            redact_projection_text(body),
            mode="markdown_file",
        )
        output_path = str(result.get("path") or "")
        response = {
            "status": "completed",
            "source_workspace_name": safe.workspace_name,
            "source_path": str(safe.path),
            "chars": len(body),
            **result,
            "projection_path": output_path,
            "preview_url": self._projection_preview_url,
        }
        task = self.create_task("投影 Markdown 文件", "projection", "completed", {"file_path": safe.workspace_name, "title": title}, response)
        response["task_id"] = task["task_id"]
        self.record_audit(
            "projection_markdown_file",
            "ok",
            safe.workspace_name,
            {"task_id": task["task_id"], "projection_path": output_path, "chars": len(body)},
            ctx,
        )
        return response

    def api_projection_pptx_session_status(self, params: dict[str, list[str]], ctx: RequestContext) -> dict[str, object]:
        file_path = str(params.get("file_path", [""])[0] or "").strip()
        if not file_path:
            raise ApiError("missing_file_path", "Missing file_path.", status=400)
        safe = self.ensure_allowed_path(file_path, ctx, action="projection_pptx_session_status")
        if safe.path.suffix.lower() != ".pptx":
            raise ApiError("unsupported_file_type", "PPT projection requires a .pptx file.", status=400, details={"suffix": safe.path.suffix.lower()})
        slides = self.runtime.projection.extract_pptx_slides(safe.path)
        output = self.build_pptx_session_payload(
            title=str(params.get("title", [""])[0] or safe.path.stem),
            safe=safe,
            slides=slides,
            slide_index=safe_int(params.get("slide_index", ["1"])[0], 1),
            projection=None,
            status="ready",
        )
        self.record_audit("projection_pptx_session_status", "ok", safe.workspace_name, {"slide_count": len(slides)}, ctx)
        return output

    def api_projection_pptx_session(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(require_file_path(payload), ctx, action="projection_pptx_session")
        if safe.path.suffix.lower() != ".pptx":
            self.record_audit("projection_pptx_session", "blocked", safe.workspace_name, {"reason": "unsupported_suffix", "suffix": safe.path.suffix.lower()}, ctx)
            raise ApiError("unsupported_file_type", "PPT projection requires a .pptx file.", status=400, details={"suffix": safe.path.suffix.lower()})
        slides = self.runtime.projection.extract_pptx_slides(safe.path)
        if not slides:
            self.record_audit("projection_pptx_session", "blocked", safe.workspace_name, {"reason": "no_extractable_slides"}, ctx)
            raise ApiError("empty_pptx", "No readable slides were found in this PPTX file.", status=400)
        title = str(payload.get("title") or safe.path.stem).strip() or safe.path.stem
        slide_index = safe_int(payload.get("slide_index"), 1)
        action = str(payload.get("action") or "show").strip().lower()
        if action == "next":
            slide_index += 1
        elif action == "previous":
            slide_index -= 1
        slide_index = max(1, min(len(slides), slide_index))
        projection = self.runtime.projection.render_pptx_slide(
            title,
            slides[slide_index - 1],
            slide_count=len(slides),
            source_name=safe.workspace_name,
        )
        output = self.build_pptx_session_payload(
            title=title,
            safe=safe,
            slides=slides,
            slide_index=slide_index,
            projection=projection,
            status="completed",
        )
        task = self.create_task(
            f"PPT 投影：{title}",
            "projection",
            "completed",
            {"file_path": safe.workspace_name, "slide_index": slide_index, "action": action},
            output,
        )
        output["task_id"] = task["task_id"]
        self.record_audit(
            "projection_pptx_session",
            "ok",
            str(projection.get("path")),
            {"task_id": task["task_id"], "source": safe.workspace_name, "slide_index": slide_index, "slide_count": len(slides)},
            ctx,
        )
        return output

    def build_pptx_session_payload(
        self,
        *,
        title: str,
        safe: SafePath,
        slides: list[dict[str, object]],
        slide_index: int,
        projection: dict[str, str] | None,
        status: str,
    ) -> dict[str, object]:
        slide_count = len(slides)
        if slide_count:
            slide_index = max(1, min(slide_count, slide_index))
            current_slide = slides[slide_index - 1]
        else:
            slide_index = 0
            current_slide = {}
        output: dict[str, object] = {
            "status": status,
            "title": redact_projection_text(title),
            "source_workspace_name": safe.workspace_name,
            "source_path": str(safe.path),
            "slide_index": slide_index,
            "slide_count": slide_count,
            "current_slide": current_slide,
            "slides": slides,
            "preview_url": self._projection_preview_url,
            "message": "PPTX text slides are ready for projection." if status == "ready" else "PPTX slide rendered to projection output.",
        }
        if projection:
            output.update({"projection": projection, "path": projection.get("path"), "projection_path": projection.get("path"), "mode": projection.get("mode")})
        return output

    def api_projection_calibration_pattern(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = str(payload.get("title") or "投影校准测试图")
        result = self.runtime.projection.render_calibration_pattern(title)
        task = self.create_task("投影校准测试图", "projection", "completed", {"title": title}, result)
        self.record_audit("projection_calibration_pattern", "ok", str(result.get("path")), {"task_id": task["task_id"]}, ctx)
        return {"task_id": task["task_id"], **result}

    def api_projection_calibration_analyze(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        image_data_url = require_string(payload, "image_data_url")
        title = str(payload.get("title") or "projection_calibration")
        try:
            result = self.runtime.projection.analyze_calibration_capture(
                image_data_url,
                title=title,
                max_bytes=self.max_upload_bytes,
            )
        except ValueError as exc:
            raise ApiError("invalid_image", str(exc), status=400) from exc
        status = normalize_task_status(str(result.get("status") or "completed"))
        task = self.create_task("投影校准分析", "projection", status, {"title": title}, result)
        self.record_audit("projection_calibration_analyze", status_to_audit(status), str(result.get("capture_path") or title), {"task_id": task["task_id"]}, ctx)
        return {"task_id": task["task_id"], **result}

    def api_projection_calibration_apply(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        calibration = payload.get("calibration") if isinstance(payload.get("calibration"), dict) else {}
        if not calibration:
            analysis_path_value = str(payload.get("analysis_path") or "").strip()
            if not analysis_path_value:
                raise ApiError("missing_calibration", "Provide calibration payload or analysis_path.", status=400)
            analysis_path = Path(analysis_path_value).expanduser().resolve()
            projection_dir = self.runtime.config.projection_dir.resolve()
            if not analysis_path.is_file() or not analysis_path.is_relative_to(projection_dir):
                raise ApiError("blocked", "Calibration analysis must stay inside projection output directory.", status=403)
            try:
                loaded = json.loads(analysis_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ApiError("invalid_calibration", "Calibration analysis file is not valid JSON.", status=400) from exc
            calibration = loaded if isinstance(loaded, dict) else {}
        profile = build_display_profile(
            ambient_lux=optional_float(payload.get("ambient_lux")),
            calibration=calibration,
            mode="calibration",
            brightness=optional_float(payload.get("brightness")),
            contrast=optional_float(payload.get("contrast")),
            scale=optional_float(payload.get("scale")),
            keystone_x=optional_float(payload.get("keystone_x")),
            keystone_y=optional_float(payload.get("keystone_y")),
        )
        saved = save_display_profile(self.projection_display_profile_path(), profile)
        recommendations = calibration.get("recommendations") if isinstance(calibration.get("recommendations"), list) else []
        result = {
            "status": "completed",
            "profile": saved,
            "path": str(self.projection_display_profile_path()),
            "preview_url": self._projection_preview_url,
            "calibration_status": calibration.get("status"),
            "recommendations": recommendations,
            "hardware_control": calibration.get("hardware_control", "display_substitute_digital_profile"),
            "message": "Calibration profile applied to the external-monitor preview. Physical focus/keystone motors still require projector SDK integration.",
        }
        task = self.create_task("投影校准自动应用", "projection", "completed", {"source": payload.get("analysis_path") or "payload"}, result)
        self.record_audit("projection_calibration_apply", "ok", "projection_display_profile", {"task_id": task["task_id"], "profile": saved}, ctx)
        return {"task_id": task["task_id"], **result}

    def projection_display_profile_path(self) -> Path:
        return (self.runtime.config.projection_dir / "display_profile.json").resolve()

    def api_projection_display_profile(self, ctx: RequestContext | None = None) -> dict[str, object]:
        profile = ProjectionPreviewServer(
            self.runtime.config.projection_dir,
            self.audit,
            display_profile_path=self.projection_display_profile_path(),
        ).load_display_profile()
        payload = {
            "status": "completed",
            "profile": profile.as_dict(),
            "path": str(self.projection_display_profile_path()),
            "preview_url": self._projection_preview_url,
            "display_test_mode": True,
            "physical_projector": "display_substitute",
            "message": "Digital display profile is applied by the external-monitor preview page.",
        }
        if ctx:
            self.record_audit("projection_display_profile.status", "ok", "projection_display_profile", {"mode": profile.mode}, ctx)
        return payload

    def api_projection_display_profile_update(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        mode = str(payload.get("mode") or "manual")
        ambient_lux = optional_float(payload.get("ambient_lux"))
        calibration_payload = payload.get("calibration") if isinstance(payload.get("calibration"), dict) else None
        profile = build_display_profile(
            ambient_lux=ambient_lux,
            calibration=calibration_payload,
            mode=mode,
            brightness=optional_float(payload.get("brightness")),
            contrast=optional_float(payload.get("contrast")),
            scale=optional_float(payload.get("scale")),
            keystone_x=optional_float(payload.get("keystone_x")),
            keystone_y=optional_float(payload.get("keystone_y")),
        )
        saved = save_display_profile(self.projection_display_profile_path(), profile)
        result = {
            "status": "completed",
            "profile": saved,
            "path": str(self.projection_display_profile_path()),
            "preview_url": self._projection_preview_url,
            "message": "Display profile updated. The external-monitor preview refreshes automatically.",
        }
        self.record_audit("projection_display_profile.update", "ok", "projection_display_profile", result, ctx)
        return result

    def api_projection_summarize_ppt_page(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        pptx_file_path = str(payload.get("file_path") or payload.get("pptx_file_path") or "").strip()
        if pptx_file_path:
            return self.api_projection_summarize_pptx_page(payload, ctx, pptx_file_path=pptx_file_path)

        has_openai_vision = bool(self.runtime.config.openai_api_key)
        has_dashscope_vision = bool(getattr(self.runtime.config, "dashscope_api_key", ""))
        if not has_openai_vision and not has_dashscope_vision:
            result = {
                "status": "backend_missing",
                "message": "OPENAI_API_KEY or DASHSCOPE_API_KEY is required for PPT page image understanding.",
                "provider": "ResponsesLLM",
            }
            task = self.create_task("总结这一页 PPT", "projection", "backend_missing", {"source": payload.get("source") or "browser_capture"}, result)
            self.record_audit("projection_ppt_page_summary", "backend_missing", "ppt_page", {"task_id": task["task_id"]}, ctx)
            return {"task_id": task["task_id"], **result}

        image_data_url = require_string(payload, "image_data_url")
        title = str(payload.get("title") or "PPT 当前页总结")
        render_projection = bool(payload.get("render_projection", True))
        screenshot_path = self.write_ppt_page_capture(image_data_url, title)
        prompt = "\n".join(
            [
                "请总结这张 PPT 当前页截图。",
                "输出中文 Markdown，必须包含：",
                "1. 一句话结论",
                "2. 页面核心要点",
                "3. 可直接口播的演讲提示",
                "4. 待确认/看不清的信息",
                "不要编造截图中没有的信息；看不清时写“待确认”。",
            ]
        )
        try:
            summary, provider, model = self._complete_ppt_page_summary(prompt, image_data_url, payload)
        except LLMError as exc:
            result = {
                "status": "backend_missing",
                "message": str(exc),
                "provider": "vision_llm",
                "screenshot_path": str(screenshot_path),
            }
            task = self.create_task("总结这一页 PPT", "projection", "backend_missing", {"source": payload.get("source") or "browser_capture"}, result)
            self.record_audit("projection_ppt_page_summary", "backend_missing", str(screenshot_path), {"task_id": task["task_id"], "error": str(exc)[:500]}, ctx)
            return {"task_id": task["task_id"], **result}

        summary_path = self.runtime.workspace.write_text(
            safe_filename(title, default="ppt_page", suffix="_summary.md"),
            summary,
            action="projection.ppt_page_summary_write",
        )
        projection = None
        if render_projection:
            projection = self.runtime.projection.render_markdown(title, summary, mode="ppt_page_summary")
        result = {
            "status": "completed",
            "summary": summary,
            "summary_path": str(summary_path),
            "screenshot_path": str(screenshot_path),
            "projection": projection,
            "projection_path": str(projection.get("path") if isinstance(projection, dict) else ""),
            "provider": provider,
            "model": model,
        }
        task = self.create_task("总结这一页 PPT", "projection", "completed", {"source": payload.get("source") or "browser_capture", "title": title}, result)
        self.record_audit("projection_ppt_page_summary", "ok", str(screenshot_path), {"task_id": task["task_id"], "summary_path": str(summary_path), "projection_path": result["projection_path"]}, ctx)
        return {"task_id": task["task_id"], **result}

    def api_projection_summarize_pptx_page(self, payload: dict[str, Any], ctx: RequestContext, *, pptx_file_path: str) -> dict[str, object]:
        safe = self.ensure_allowed_path(pptx_file_path, ctx, action="projection_pptx_page_summary")
        if safe.path.suffix.lower() != ".pptx":
            self.record_audit("projection_pptx_page_summary", "blocked", safe.workspace_name, {"reason": "unsupported_suffix", "suffix": safe.path.suffix.lower()}, ctx)
            raise ApiError("unsupported_file_type", "PPTX page summary requires a .pptx file.", status=400, details={"suffix": safe.path.suffix.lower()})
        slides = self.runtime.projection.extract_pptx_slides(safe.path)
        if not slides:
            self.record_audit("projection_pptx_page_summary", "blocked", safe.workspace_name, {"reason": "no_extractable_slides"}, ctx)
            raise ApiError("empty_pptx", "No readable slides were found in this PPTX file.", status=400)
        slide_index = max(1, min(len(slides), safe_int(payload.get("slide_index"), 1)))
        slide = slides[slide_index - 1]
        title = str(payload.get("title") or f"{safe.path.stem} 第 {slide_index} 页总结")
        slide_title = str(slide.get("title") or f"Slide {slide_index}")
        body = str(slide.get("text") or "").strip()
        bullets = [line.strip() for line in body.splitlines() if line.strip()]
        if not bullets and slide_title:
            bullets = [slide_title]
        summary_lines = [
            f"# {redact_projection_text(title)}",
            "",
            "Provider: local_pptx_text",
            f"Source: {redact_projection_text(safe.workspace_name)}",
            f"Slide: {slide_index}/{len(slides)}",
            "",
            "## 一句话结论",
            f"- {redact_projection_text(slide_title)}",
            "",
            "## 页面核心要点",
            *([f"- {redact_projection_text(item)}" for item in bullets[:8]] or ["- 此页没有可提取文本；请使用屏幕捕获总结视觉版式。"]),
            "",
            "## 可直接口播的演讲提示",
            f"- 这一页重点说明：{redact_projection_text(slide_title)}。",
            *[f"- 可以补充展开：{redact_projection_text(item)}" for item in bullets[1:4]],
            "",
            "## 待确认/看不清的信息",
            "- PPTX 文本模式无法读取图片、图表视觉细节和动画状态；需要时请使用屏幕捕获当前页。",
            "",
        ]
        summary = "\n".join(summary_lines)
        summary_path = self.runtime.workspace.write_text(
            safe_filename(title, default="pptx_page", suffix="_summary.md"),
            summary,
            action="projection.pptx_page_summary_write",
        )
        projection = None
        if bool(payload.get("render_projection", True)):
            projection = self.runtime.projection.render_markdown(title, summary, mode="pptx_page_summary")
        result = {
            "status": "completed",
            "summary": summary,
            "summary_path": str(summary_path),
            "screenshot_path": "",
            "source_workspace_name": safe.workspace_name,
            "source_path": str(safe.path),
            "slide_index": slide_index,
            "slide_count": len(slides),
            "current_slide": slide,
            "projection": projection,
            "projection_path": str(projection.get("path") if isinstance(projection, dict) else ""),
            "provider": "local_pptx_text",
            "model": "local_rules",
            "message": "已基于 PPTX 当前页可提取文本生成总结；视觉图表请使用屏幕捕获。",
        }
        task = self.create_task("总结 PPTX 当前页", "projection", "completed", {"file_path": safe.workspace_name, "slide_index": slide_index, "title": title}, result)
        self.record_audit(
            "projection_pptx_page_summary",
            "ok",
            safe.workspace_name,
            {"task_id": task["task_id"], "summary_path": str(summary_path), "projection_path": result["projection_path"], "slide_index": slide_index},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def _complete_ppt_page_summary(self, prompt: str, image_data_url: str, payload: dict[str, Any]) -> tuple[str, str, str]:
        instructions = "你是严谨的演示助手。只基于用户主动捕获的 PPT 当前页截图进行总结。"
        context = {"task": "summarize_current_ppt_page", "source": payload.get("source") or "browser_capture"}
        errors: list[str] = []
        if self.runtime.config.openai_api_key:
            model = self.runtime.config.openai_vision_model
            try:
                summary = ResponsesLLM(
                    ResponsesLLMConfig(
                        api_key=self.runtime.config.openai_api_key,
                        base_url=self.runtime.config.openai_base_url,
                        model=model,
                        reasoning_effort="low",
                    )
                ).complete_multimodal(
                    instructions=instructions,
                    text=prompt,
                    image_data_url=image_data_url,
                    context=context,
                    timeout=120,
                )
                return summary, "OpenAI-compatible vision", model
            except LLMError as exc:
                errors.append(f"openai_compatible={str(exc)[:500]}")
        if getattr(self.runtime.config, "dashscope_api_key", ""):
            model = str(getattr(self.runtime.config, "dashscope_vision_model", "qwen-vl-plus") or "qwen-vl-plus")
            try:
                summary = ResponsesLLM(
                    ResponsesLLMConfig(
                        api_key=self.runtime.config.dashscope_api_key,
                        base_url=str(getattr(self.runtime.config, "dashscope_vision_base_url", "https://dashscope.aliyuncs.com/compatible-mode")),
                        model=model,
                        reasoning_effort="low",
                        wire_api=str(getattr(self.runtime.config, "dashscope_vision_wire_api", "chat_completions") or "chat_completions"),
                    )
                ).complete_multimodal(
                    instructions=instructions,
                    text=prompt,
                    image_data_url=image_data_url,
                    context={**context, "fallback_after": errors[-1] if errors else ""},
                    timeout=120,
                )
                return summary, "DashScope Qwen-VL", model
            except LLMError as exc:
                errors.append(f"dashscope_qwen_vl={str(exc)[:500]}")
        raise LLMError("; ".join(errors) or "No vision API provider is configured.")

    def write_ppt_page_capture(self, image_data_url: str, title: str) -> Path:
        match = re.match(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$", image_data_url)
        if not match:
            raise ApiError("invalid_image", "Expected a PNG, JPEG, or WebP data URL.", status=400)
        mime_type, raw_base64 = match.groups()
        import base64

        try:
            data = base64.b64decode("".join(raw_base64.split()), validate=True)
        except Exception as exc:
            raise ApiError("invalid_image", "Image data URL is not valid base64.", status=400) from exc
        if len(data) > self.max_upload_bytes:
            raise ApiError("image_too_large", f"Image exceeds upload limit of {self.max_upload_bytes} bytes.", status=413)
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime_type]
        path = self.runtime.workspace.path_for_new_file(safe_filename(title, default="ppt_page", suffix=f"_capture{extension}"))
        atomic_write_bytes(path, data)
        self.audit.record("projection.ppt_page_capture_write", target=str(path), details={"bytes": len(data), "mime_type": mime_type})
        return path

    def api_projection_latest(self, ctx: RequestContext | None = None) -> dict[str, object]:
        latest = latest_projection_file(self.runtime.config.projection_dir)
        cards = self.projection_cards()
        if latest is None:
            return {"status": "empty", "path": None, "html": "", "cards": cards}
        text = latest.read_text(encoding="utf-8", errors="replace")
        payload = {
            "status": "ok",
            "name": latest.name,
            "path": str(latest),
            "mtime": latest.stat().st_mtime,
            "html": markdown_to_html(text),
            "cards": cards,
        }
        if ctx:
            self.record_audit("projection_latest", "ok", latest.name, {"mtime": payload["mtime"]}, ctx)
        return payload

    def api_projection_service_status(self) -> dict[str, object]:
        running = self.projection_preview_running()
        return {
            "status": "online" if running else "adapter_ready",
            "preview_url": self._projection_preview_url,
            "display_test_mode": True,
            "physical_projector": "display_substitute",
            "output_target": "external_monitor",
            "started_at": self._projection_preview_started_at,
            "message": "External monitor display mode is available. Open the preview URL on the connected monitor and press F for fullscreen.",
        }

    def api_projection_service_start(self, ctx: RequestContext) -> dict[str, object]:
        result = self.start_projection_preview_service()
        self.record_audit("projection_start", status_to_audit(str(result["status"])), "projection_preview_service", result, ctx)
        return result

    def api_projection_service_stop(self, ctx: RequestContext) -> dict[str, object]:
        result = self.stop_projection_preview_service()
        self.record_audit("projection_stop", status_to_audit(str(result["status"])), "projection_preview_service", result, ctx)
        return result

    def api_camera_stream_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        running = self.camera_stream_running()
        status_payload: dict[str, object] = {}
        if running:
            try:
                with urllib.request.urlopen(f"{self._camera_stream_url}status.json", timeout=1.5) as response:
                    status_payload = json.loads(response.read().decode("utf-8", errors="replace"))
            except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                status_payload = {}
        browser_preview_url = self.camera_stream_browser_url(ctx)
        payload = {
            "status": "online" if running else "stopped",
            "preview_url": self._camera_stream_url,
            "snapshot_url": f"{self._camera_stream_url}snapshot.jpg",
            "stream_url": f"{self._camera_stream_url}stream.mjpg",
            "browser_preview_url": browser_preview_url,
            "browser_snapshot_url": urllib.parse.urljoin(browser_preview_url, "snapshot.jpg"),
            "browser_stream_url": urllib.parse.urljoin(browser_preview_url, "stream.mjpg"),
            "camera_index": safe_int(status_payload.get("camera_index"), self._camera_stream_camera_index or 0),
            "started_at": self._camera_stream_started_at,
            "always_on": running,
            "details": status_payload,
            "message": "Camera preview is available in the browser." if running else "Camera preview is stopped.",
        }
        if ctx:
            self.record_audit("camera_stream.status", "ok", "camera_stream", {"running": running}, ctx)
        return payload

    def api_camera_stream_start(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        result = self.start_camera_stream_service(
            camera_index=payload.get("camera_index"),
            width=safe_int(payload.get("width"), 1280),
            height=safe_int(payload.get("height"), 720),
            backend=str(payload.get("backend") or "auto"),
            ctx=ctx,
        )
        self.record_audit("camera_stream.start", status_to_audit(str(result.get("status"))), "camera_stream", result, ctx)
        return result

    def api_camera_stream_stop(self, ctx: RequestContext) -> dict[str, object]:
        result = self.stop_camera_stream_service(ctx=ctx)
        self.record_audit("camera_stream.stop", status_to_audit(str(result.get("status"))), "camera_stream", result, ctx)
        return result

    def camera_stream_running(self) -> bool:
        with self._camera_stream_lock:
            process = self._camera_stream_process
            if process is not None and process.poll() is not None:
                self._camera_stream_process = None
                self._camera_stream_started_at = None
                self._camera_stream_camera_index = None
                process = None
        if process is not None:
            return self.camera_stream_healthcheck()
        if self.camera_stream_healthcheck():
            return True
        return False

    def camera_stream_healthcheck(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self._camera_stream_url}status.json", timeout=1.0) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError, TimeoutError):
            return False

    def start_camera_stream_service(
        self,
        *,
        camera_index: Any = None,
        width: int = 1280,
        height: int = 720,
        backend: str = "auto",
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        with self._camera_stream_lock:
            existing = self._camera_stream_process
            if existing is not None and existing.poll() is None:
                return {
                    **self.api_camera_stream_status(ctx),
                    "status": "starting" if not self.camera_stream_healthcheck() else "online",
                    "message": "Camera preview is already starting.",
                }
            if self.camera_stream_running():
                return {
                    **self.api_camera_stream_status(ctx),
                    "status": "online",
                    "message": "Camera preview is already running.",
                }

            backend = backend if backend in {"auto", "face", "hog", "yolo"} else "auto"
            resolved_camera_index = self.resolve_camera_index(camera_index)
            command = [
                sys.executable,
                "-u",
                "-m",
                "lelamp.camera_stream",
                "--host",
                "0.0.0.0",
                "--port",
                str(self.camera_stream_port),
                "--camera-index",
                str(resolved_camera_index),
                "--backend",
                backend,
                "--width",
                str(max(320, min(3840, width))),
                "--height",
                str(max(240, min(2160, height))),
            ]
            log_path = self.runtime.config.workspace_dir / ".camera_stream.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                log_file = log_path.open("ab")
                process = subprocess.Popen(
                    command,
                    cwd=str(Path(__file__).resolve().parents[2]),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as exc:
                return {
                    "status": "unavailable",
                    "preview_url": self._camera_stream_url,
                    "message": f"Camera preview could not start: {exc}",
                }
            self._camera_stream_process = process
            self._camera_stream_started_at = time.time()
            self._camera_stream_camera_index = resolved_camera_index

        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.camera_stream_healthcheck():
                break
            time.sleep(0.2)
        return {
            **self.api_camera_stream_status(ctx),
            "status": "online" if self.camera_stream_healthcheck() else "starting",
            "message": "Camera preview started; open it in the browser.",
        }

    def stop_camera_stream_service(self, ctx: RequestContext | None = None) -> dict[str, object]:
        with self._camera_stream_lock:
            process = self._camera_stream_process
            self._camera_stream_process = None
            self._camera_stream_started_at = None
            self._camera_stream_camera_index = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        else:
            self.stop_external_camera_stream_processes()
        return {
            "status": "stopped",
            "preview_url": self._camera_stream_url,
            "browser_preview_url": self.camera_stream_browser_url(ctx),
            "always_on": False,
            "message": "Camera preview stopped.",
        }

    def stop_external_camera_stream_processes(self) -> None:
        try:
            completed = subprocess.run(
                ["pgrep", "-f", r"lelamp\.camera_stream"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        current_pid = os.getpid()
        for raw_pid in completed.stdout.split():
            try:
                pid = int(raw_pid)
            except ValueError:
                continue
            if pid == current_pid:
                continue
            try:
                os.kill(pid, 15)
            except OSError:
                continue

    def camera_stream_browser_url(self, ctx: RequestContext | None = None) -> str:
        configured = os.getenv("LELAMP_CAMERA_BROWSER_URL", "").strip()
        if configured:
            return configured.rstrip("/") + "/"
        host = (ctx.host if ctx else "") or ""
        if host:
            web_host = host.rsplit("@", 1)[-1].split(":", 1)[0]
            if web_host:
                proto = (ctx.forwarded_proto if ctx else "") or "http"
                return f"{proto}://{web_host}:{self.camera_stream_port}/"
        return self._camera_stream_url

    def resolve_camera_index(self, value: Any = None, default: int = 0) -> int:
        if value not in (None, ""):
            return max(0, safe_int(value, default))
        configured = os.getenv("LELAMP_CAMERA_INDEX", "").strip() or os.getenv("LELAMP_CAMERA_STREAM_INDEX", "").strip()
        if configured:
            return max(0, safe_int(configured, default))
        detected = self.detect_available_camera_index()
        return detected if detected is not None else default

    def detect_available_camera_index(self) -> int | None:
        device_indices: list[int] = []
        for device in sorted(Path("/dev").glob("video*")):
            match = re.fullmatch(r"video(\d+)", device.name)
            if match:
                device_indices.append(safe_int(match.group(1), 0))
        candidates = sorted(set(device_indices + list(range(0, 6))))
        try:
            import cv2
        except Exception:
            return device_indices[0] if device_indices else None

        for index in candidates:
            camera = cv2.VideoCapture(index)
            try:
                if not camera.isOpened():
                    continue
                ok, frame = camera.read()
                if ok and frame is not None:
                    return index
            finally:
                camera.release()
        return device_indices[0] if device_indices else None

    def projection_preview_running(self) -> bool:
        with self._projection_preview_lock:
            if self._projection_preview_httpd is None or self._projection_preview_thread is None:
                return False
            if not self._projection_preview_thread.is_alive():
                self._projection_preview_httpd = None
                self._projection_preview_thread = None
                self._projection_preview_started_at = None
                return False
        try:
            with urllib.request.urlopen(f"{self._projection_preview_url}health", timeout=1.0) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError, TimeoutError):
            return False

    def start_projection_preview_service(self) -> dict[str, object]:
        with self._projection_preview_lock:
            if self._projection_preview_httpd is not None and self._projection_preview_thread is not None:
                if self._projection_preview_thread.is_alive():
                    return {
                        **self.api_projection_service_status(),
                        "status": "online",
                        "message": "External monitor display preview is already running.",
                    }
                self._projection_preview_httpd = None
                self._projection_preview_thread = None
                self._projection_preview_started_at = None

            self.runtime.config.projection_dir.mkdir(parents=True, exist_ok=True)
            preview = ProjectionPreviewServer(
                self.runtime.config.projection_dir,
                self.audit,
                refresh_seconds=2,
                display_profile_path=self.projection_display_profile_path(),
            )
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", self.projection_preview_port), preview.make_handler())
            except OSError as exc:
                return {
                    "status": "unavailable",
                    "preview_url": self._projection_preview_url,
                    "display_test_mode": True,
                    "physical_projector": "display_substitute",
                    "output_target": "external_monitor",
                    "message": f"Projection preview port {self.projection_preview_port} is unavailable: {exc}",
                }

            bound_host, bound_port = httpd.server_address[:2]
            self._projection_preview_url = f"http://{bound_host}:{bound_port}/"

            def serve_preview() -> None:
                try:
                    httpd.serve_forever(poll_interval=0.5)
                finally:
                    httpd.server_close()

            thread = threading.Thread(target=serve_preview, name="openclaw-projection-preview", daemon=True)
            self._projection_preview_httpd = httpd
            self._projection_preview_thread = thread
            self._projection_preview_started_at = time.time()
            thread.start()

        return {
            **self.api_projection_service_status(),
            "status": "online",
            "message": "External monitor display preview started. Open the preview URL on the connected monitor and press F for fullscreen.",
        }

    def stop_projection_preview_service(self) -> dict[str, object]:
        with self._projection_preview_lock:
            httpd = self._projection_preview_httpd
            thread = self._projection_preview_thread
            if httpd is None or thread is None:
                self._projection_preview_started_at = None
                return {
                    "status": "stopped",
                    "preview_url": self._projection_preview_url,
                    "display_test_mode": True,
                    "physical_projector": "display_substitute",
                    "output_target": "external_monitor",
                    "message": "External monitor display preview was not running.",
                }
            self._projection_preview_httpd = None
            self._projection_preview_thread = None
            self._projection_preview_started_at = None

        httpd.shutdown()
        thread.join(timeout=2.0)
        return {
            "status": "stopped",
            "preview_url": self._projection_preview_url,
            "display_test_mode": True,
            "physical_projector": "display_substitute",
            "output_target": "external_monitor",
            "message": "External monitor display preview stopped.",
        }

    def api_hardware_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        scanned = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
        devices = scanned["devices"] if isinstance(scanned.get("devices"), dict) else {}
        events = [event for event in read_recent_audit(self.runtime.config.audit_log_path, limit=40) if str(event.get("action", "")).startswith(("lelamp", "hardware", "projection"))]
        payload = {
            "hardware_enabled": self.runtime.config.enable_hardware,
            "devices": devices,
            "sensors": scanned.get("sensors", {}),
            "events": events[-10:],
            "camera": {
                "status": str(devices.get("camera", {}).get("status", "unavailable")) if isinstance(devices.get("camera"), dict) else "unavailable",
                "note": summarize_dict(devices.get("camera", {}).get("details", {})) if isinstance(devices.get("camera"), dict) else "",
            },
            "screen_context": {"status": "adapter_ready", "note": "Screen capture remains explicit only."},
            "lelamp": self.runtime.lelamp_experience.capability_map(),
            "smart_home": self.runtime.smart_home.status(),
            "scan": scanned.get("scan", {}),
            "probes": scanned.get("probes", {}),
        }
        if ctx:
            self.record_audit("hardware_status", "ok", "hardware", {"hardware_enabled": payload["hardware_enabled"], "summary": payload["scan"]}, ctx)
        return payload

    def api_hardware_scan(self, ctx: RequestContext) -> dict[str, object]:
        payload = self.api_hardware_status(ctx=None)
        self.record_audit("hardware_scan", "ok", "hardware", {"summary": payload.get("scan", {})}, ctx)
        return payload

    def api_lelamp_motion_status(self, ctx: RequestContext) -> dict[str, object]:
        result = self.lelamp_motion_preflight(read_pose=True)
        self.record_audit(
            "lelamp_motion_status",
            status_to_audit(str(result.get("status") or "unavailable")),
            str(result.get("port") or self.runtime.config.hardware_port),
            {
                "hardware_enabled": result.get("hardware_enabled"),
                "serial_detected": result.get("serial_detected"),
                "pose_readable": result.get("pose_readable"),
            },
            ctx,
        )
        return result

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
                "pose": pose,
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
        max_delta = max((abs(float(target_pose[motor]) - float(current_pose[motor])) for motor in motors), default=0.0)
        iteration_limit = max(1, safe_int(max_iterations, 0)) if max_iterations is not None else max(1, min(10, math.ceil(max_delta / max(0.1, max_step)) + 4))
        trace: list[dict[str, object]] = []

        for step_index in range(iteration_limit):
            if all(abs(float(target_pose[motor]) - float(current_pose[motor])) <= tolerance for motor in motors):
                break
            next_pose = dict(current_pose)
            for motor in motors:
                current_value = float(current_pose[motor])
                target_value = float(target_pose[motor])
                delta = target_value - current_value
                if abs(delta) <= max_step:
                    next_pose[motor] = target_value
                else:
                    next_pose[motor] = current_value + (max_step if delta > 0 else -max_step)

            bus.sync_write("Goal_Position", next_pose)
            time.sleep(step_seconds)
            current_pose = read_current_pose(bus)
            trace.append(
                {
                    "step": step_index + 1,
                    "target": {motor: round(float(target_pose[motor]), 3) for motor in motors},
                    "commanded": {motor: round(float(next_pose[motor]), 3) for motor in motors},
                    "actual": {motor: round(float(current_pose[motor]), 3) for motor in motors},
                }
            )

        return current_pose, trace

    def api_lelamp_state(self, state: str, ctx: RequestContext) -> dict[str, object]:
        result = self.runtime.lelamp_experience.state_cue(state)
        scan = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
        rgb = scan.get("devices", {}).get("rgb", {}) if isinstance(scan.get("devices"), dict) else {}
        rgb_status_value = str(rgb.get("status", "adapter_ready")) if isinstance(rgb, dict) else "adapter_ready"
        status = "adapter_ready" if not self.runtime.config.enable_hardware else ("ok" if rgb_status_value == "available" else rgb_status_value)
        payload = {
            "status": status,
            "state": state,
            "cue": result,
            "hardware_enabled": self.runtime.config.enable_hardware,
            "rgb_probe_status": rgb_status_value,
        }
        self.record_audit("lelamp_state", status, state, payload, ctx)
        return payload

    def api_scene_recent(self, limit: int, ctx: RequestContext) -> dict[str, object]:
        events = self.runtime.scene.get_recent_events(limit=max(1, min(100, limit)))
        payload = {"status": "ok", "events": events, "total": len(events)}
        self.record_audit("scene_recent", "ok", "scene", {"count": len(events)}, ctx)
        return payload

    def api_scene_workflow_suggestions(self, limit: int, ctx: RequestContext) -> dict[str, object]:
        limit = max(1, min(100, limit))
        events = self.runtime.scene.get_recent_events(limit=limit)
        suggestions = self.runtime.scene.workflow_suggestions(events, limit=limit)
        payload = {
            "status": "completed",
            "version": SCENE_WORKFLOW_VERSION,
            "source": "recent_scene_events",
            "events": events,
            "suggestions": suggestions,
            "total": len(suggestions),
            "safety": [
                "场景建议只基于用户显式提交的图像或环境读数。",
                "触发工作流需要用户点击确认。",
                "不会被动解析投影内容，不会自动控制电脑。",
            ],
        }
        self.record_audit("scene_workflow_suggestions", "ok", "scene", {"events": len(events), "suggestions": len(suggestions)}, ctx)
        return payload

    def api_scene_workflow_suggestions_from_payload(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        events = payload.get("events")
        normalized_events: list[dict[str, object]]
        if isinstance(events, list):
            normalized_events = [item for item in events if isinstance(item, dict)]
        else:
            normalized_events = self.runtime.scene.get_recent_events(limit=max(1, min(100, safe_int(payload.get("limit"), 20))))
        suggestions = self.runtime.scene.workflow_suggestions(normalized_events)
        result = {
            "status": "completed",
            "version": SCENE_WORKFLOW_VERSION,
            "source": "provided_events" if isinstance(events, list) else "recent_scene_events",
            "events": normalized_events,
            "suggestions": suggestions,
            "total": len(suggestions),
            "safety": [
                "场景建议只基于用户显式提交的图像或环境读数。",
                "触发工作流需要用户点击确认。",
                "不会被动解析投影内容，不会自动控制电脑。",
            ],
        }
        self.record_audit("scene_workflow_suggestions", "ok", "scene", {"events": len(normalized_events), "suggestions": len(suggestions)}, ctx)
        return result

    def api_scene_workflow_trigger(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        action = require_string(payload, "action")
        authorized = bool(payload.get("authorized"))
        if not authorized:
            result = {
                "status": "needs_confirmation",
                "action": action,
                "message": "需要用户在页面上明确确认后才触发场景工作流。",
                "safety": "no_passive_camera_or_projection_parsing",
            }
            self.record_audit("scene_workflow_trigger", "blocked", action, result, ctx)
            return result

        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        description = str(event.get("description") or payload.get("description") or "")
        title = str(payload.get("title") or "")
        if action == "scan_document":
            goal = title or "扫描并整理桌面纸质文件"
            document_type = str(payload.get("document_type") or "document")
            scan_url = f"/documents?scan=1&type={urllib.parse.quote(document_type)}&source=scene"
            steps = [
                f"请用户在 Documents 页面主动拍照或上传纸质文件图片：{scan_url}",
                "运行边界识别、透视校正、图像增强和 OCR。",
                "把 OCR 文本整理成摘要、表格或合同要点，等待用户确认。",
            ]
            desktop_task = self.runtime.desktop_tasks.request_task(
                goal,
                steps,
                source="scene_workflow",
                requires_full_control=False,
            )
            reminder = self.runtime.daily.create_reminder("场景检测到纸质文件：请在 Documents 页面完成扫描/OCR。")
            result = {
                "status": "completed",
                "action": action,
                "message": "已创建扫描工作流任务和本地提醒；不会自动拍照。",
                "desktop_task": desktop_task,
                "reminder": reminder,
                "next_url": scan_url,
                "scan_request": {
                    "document_type": document_type,
                    "source": "scene_workflow",
                    "requires_user_capture": True,
                    "recommended_endpoint": "/api/scan/capture",
                },
            }
            task = self.create_task("场景触发：扫描工作流", "scene", "completed", {"action": action, "event": event}, result)
            self.record_audit("scene_workflow_trigger", "ok", action, {"task_id": task["task_id"], "desktop_task_id": desktop_task.get("id")}, ctx)
            return {"task_id": task["task_id"], **result}

        if action == "projection_obstruction_prompt":
            details = ["检测到投影路径可能被遮挡。", "请移开遮挡物或调整外接显示器/投影位置。"]
            if description:
                details.append(f"触发事件：{description}")
            projection = self.runtime.projection.render_status_card(
                "投影遮挡提示",
                "needs_adjustment",
                details=details,
                accent="amber",
            )
            result = {
                "status": "completed",
                "action": action,
                "message": "已生成投影/显示器提示卡。",
                "projection": projection,
                "preview_url": self._projection_preview_url,
            }
            task = self.create_task("场景触发：投影遮挡提示", "scene", "completed", {"action": action, "event": event}, result)
            self.record_audit("scene_workflow_trigger", "ok", action, {"task_id": task["task_id"], "projection_path": projection.get("path")}, ctx)
            return {"task_id": task["task_id"], **result}

        if action == "meeting_mode_prompt":
            meeting_title = title or f"场景建议会议 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            participants = list_string(payload.get("participants")) or ["Unknown"]
            meeting = self.runtime.meeting.enable(meeting_title, participants)
            projection = self.runtime.projection.render_status_card(
                "会议模式已开启",
                "meeting_mode_enabled",
                details=[
                    "会议理解已由用户显式触发。",
                    "后续实时转写仍需要在 Meeting 页面启动 ASR/实时会议。",
                ],
                accent="green",
            )
            result = {
                "status": "completed",
                "action": action,
                "message": "已开启会议模式，并生成确认提示卡。",
                "meeting": meeting,
                "projection": projection,
                "preview_url": self._projection_preview_url,
            }
            task = self.create_task("场景触发：开启会议模式", "scene", "completed", {"action": action, "event": event}, result)
            self.record_audit("scene_workflow_trigger", "ok", action, {"task_id": task["task_id"], "meeting_mode_enabled": meeting.get("meeting_mode_enabled")}, ctx)
            return {"task_id": task["task_id"], **result}

        if action == "display_profile_adjustment":
            ambient_lux = optional_float(payload.get("ambient_lux"))
            if ambient_lux is None:
                ambient_lux = infer_ambient_lux_from_scene_event(event)
            profile_payload: dict[str, Any] = {"mode": "ambient", "ambient_lux": ambient_lux}
            profile = self.api_projection_display_profile_update(profile_payload, ctx)
            result = {
                "status": "completed",
                "action": action,
                "message": "已根据场景事件更新外接显示器数字亮度/对比度 profile。",
                "display_profile": profile,
            }
            task = self.create_task("场景触发：显示亮度 Profile", "scene", "completed", {"action": action, "event": event}, result)
            self.record_audit("scene_workflow_trigger", "ok", action, {"task_id": task["task_id"], "ambient_lux": ambient_lux}, ctx)
            return {"task_id": task["task_id"], **result}

        if action == "desk_idle_reminder":
            reminder = self.runtime.daily.create_reminder(title or "桌面空闲：稍后检查 workspace 和待办。")
            result = {
                "status": "completed",
                "action": action,
                "message": "已创建本地 reminder 草稿。",
                "reminder": reminder,
            }
            task = self.create_task("场景触发：桌面提醒", "scene", "completed", {"action": action, "event": event}, result)
            self.record_audit("scene_workflow_trigger", "ok", action, {"task_id": task["task_id"], "reminder_count": reminder.get("count")}, ctx)
            return {"task_id": task["task_id"], **result}

        raise ApiError("unsupported_scene_workflow", f"Unsupported scene workflow action: {action}", status=400)

    def api_scene_observe_image(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        image_data_url = require_string(payload, "image_data_url")
        title = str(payload.get("title") or "desk_scene")
        image_path = self.write_scene_observation_image(image_data_url, title)
        workspace_name = str(image_path.relative_to(self.runtime.workspace.root))
        analysis = self.runtime.camera_observer.analyze_frame(workspace_name)
        status = "completed" if analysis.get("status") == "ok" else normalize_task_status(str(analysis.get("status") or "backend_missing"))
        result = {
            "status": status,
            "source": "explicit_user_image_capture",
            "image_path": str(image_path),
            "workspace_name": workspace_name,
            "analysis": analysis,
            "events": analysis.get("events", []),
        }
        result["suggestions"] = self.runtime.scene.workflow_suggestions(
            [item for item in result["events"] if isinstance(item, dict)]
        )
        task = self.create_task("场景图像观察", "hardware", status, {"workspace_name": workspace_name}, result)
        self.record_audit(
            "scene_observe_image",
            status_to_audit(status),
            workspace_name,
            {"task_id": task["task_id"], "events": len(result["events"]), "suggestions": len(result["suggestions"])},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_scene_device_observe(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = str(payload.get("title") or "desk_scene_observation")
        camera_index = self.resolve_camera_index(payload.get("camera_index"))
        timeout_seconds = max(3, min(20, safe_int(payload.get("timeout_seconds"), 12)))
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.runtime.camera_observer.capture_frame, camera_index=camera_index)
        try:
            capture = future.result(timeout=timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            capture = {
                "status": "unavailable",
                "message": f"设备相机拍照超过 {timeout_seconds} 秒未返回。",
                "camera_index": camera_index,
                "timeout_seconds": timeout_seconds,
            }
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if str(capture.get("status") or "") != "captured":
            fallback_capture = self.capture_from_camera_preview_snapshot(title, camera_index=camera_index)
            if str(fallback_capture.get("status") or "") != "captured":
                result = {
                    "status": "unavailable",
                    "source": "device_camera_capture",
                    "image_path": "",
                    "workspace_name": "",
                    "analysis": {},
                    "events": [],
                    "suggestions": [],
                    "capture": capture,
                    "fallback_capture": fallback_capture,
                    "message": "设备相机没有拍到图片，请检查摄像头连接或改用上传图片。",
                }
                self.record_audit("scene_device_observe", "blocked", f"camera:{camera_index}", result, ctx)
                return result
            capture = fallback_capture

        capture_path = Path(str(capture.get("path") or "")).expanduser().resolve()
        workspace_root = self.runtime.workspace.root.resolve()
        try:
            workspace_name = str(capture_path.relative_to(workspace_root))
        except ValueError:
            self.record_audit(
                "scene_device_observe",
                "blocked",
                str(capture_path),
                {"reason": "camera capture outside workspace", "camera_index": camera_index},
                ctx,
            )
            raise ApiError("blocked", "Camera capture is outside workspace.", status=403)

        analysis = self.runtime.camera_observer.analyze_frame(workspace_name)
        status = "completed" if analysis.get("status") == "ok" else normalize_task_status(str(analysis.get("status") or "backend_missing"))
        result = {
            "status": status,
            "source": "device_camera_capture",
            "image_path": str(capture_path),
            "workspace_name": workspace_name,
            "analysis": analysis,
            "events": analysis.get("events", []),
            "capture": capture,
            "camera_index": camera_index,
            "title": title,
        }
        result["suggestions"] = self.runtime.scene.workflow_suggestions(
            [item for item in result["events"] if isinstance(item, dict)]
        )
        task = self.create_task("场景设备相机观察", "hardware", status, {"workspace_name": workspace_name, "camera_index": camera_index}, result)
        self.record_audit(
            "scene_device_observe",
            status_to_audit(status),
            workspace_name,
            {"task_id": task["task_id"], "events": len(result["events"]), "suggestions": len(result["suggestions"]), "camera_index": camera_index},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_scene_sensor_snapshot(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        """Collect an explicit one-shot sensor snapshot and turn it into scene events."""
        include_camera = bool(payload.get("include_camera", True))
        include_mic = bool(payload.get("include_mic", True))
        include_hardware = bool(payload.get("include_hardware", True))
        seconds = max(1, min(3, safe_int(payload.get("mic_seconds"), 1)))
        camera_title = str(payload.get("title") or "scene_sensor_snapshot")

        hardware = self.api_hardware_status(ctx=None) if include_hardware else {}
        devices = hardware.get("devices") if isinstance(hardware.get("devices"), dict) else {}
        sensors = hardware.get("sensors") if isinstance(hardware.get("sensors"), dict) else {}
        projection = devices.get("projection") if isinstance(devices.get("projection"), dict) else {}
        projection_details = projection.get("details") if isinstance(projection.get("details"), dict) else {}

        camera: dict[str, object] = {"status": "skipped"}
        camera_events: list[dict[str, object]] = []
        image_analysis: dict[str, object] = {}
        image_metrics: dict[str, object] = {}
        if include_camera:
            camera = self.capture_scene_camera_snapshot(camera_title, payload, ctx)
            image_analysis = camera.get("analysis") if isinstance(camera.get("analysis"), dict) else {}
            image_metrics = image_analysis.get("metrics") if isinstance(image_analysis.get("metrics"), dict) else {}
            camera_events = [item for item in camera.get("events", []) if isinstance(item, dict)]

        mic: dict[str, object] = {"status": "skipped"}
        if include_mic:
            mic = self.capture_scene_microphone_activity(seconds=seconds)

        brightness = optional_float(payload.get("lux"))
        lux_source = "manual" if brightness is not None else ""
        if brightness is None and image_metrics:
            metric_brightness = optional_float(image_metrics.get("brightness"))
            if metric_brightness is not None:
                brightness = round(metric_brightness / 255 * 1000, 1)
                lux_source = "camera_brightness_estimate"

        mic_status = str(mic.get("status") or "")
        mic_rms = safe_int(mic.get("rms"), 0)
        mic_peak = safe_int(mic.get("peak"), 0)
        speech_active = bool(payload.get("speech_active")) or (mic_status == "completed" and (mic_rms >= 120 or mic_peak >= 900))
        people_count = safe_int(payload.get("people_count"), 0)
        presence = bool(payload.get("presence")) or bool(people_count > 0) or bool(camera_events and str(camera_events[0].get("event_type") or "") != "desk_idle")
        projection_status = str(projection.get("status") or "")
        projector_blocked = bool(payload.get("projector_blocked"))
        if projection_status == "available" and image_metrics:
            rectangles = safe_int(image_metrics.get("large_rectangles"), 0)
            brightness_value = optional_float(image_metrics.get("brightness"))
            if rectangles == 0 and brightness_value is not None and brightness_value < 55:
                projector_blocked = True

        reading = {
            "presence": presence,
            "motion": bool(payload.get("motion")) if "motion" in payload else None,
            "lux": brightness,
            "sound_level": mic_rms if mic_status == "completed" else optional_float(payload.get("sound_level")),
            "speech_active": speech_active,
            "people_count": people_count or None,
            "projector_blocked": projector_blocked,
            "calendar_event_now": bool(payload.get("calendar_event_now")),
        }
        environment = self.runtime.environment.ingest(reading)
        all_events = dedupe_scene_events(
            [item for item in camera_events if isinstance(item, dict)]
            + [item for item in environment.get("events", []) if isinstance(item, dict)]
        )
        suggestions = self.runtime.scene.workflow_suggestions(all_events)
        status = "completed" if any(str(item.get("status") or "") not in {"unavailable", "failed", "error"} for item in (camera, mic, hardware)) else "unavailable"
        result = {
            "status": status,
            "source": "explicit_sensor_snapshot",
            "reading": reading,
            "reading_sources": {
                "lux": lux_source or "unavailable",
                "speech_active": "microphone_rms" if mic_status == "completed" else "manual_or_unavailable",
                "projection": "hardware_probe" if projection else "unavailable",
                "camera": str(camera.get("source") or camera.get("status") or "unavailable"),
            },
            "camera": camera,
            "microphone": mic,
            "hardware": {
                "status": "completed" if include_hardware else "skipped",
                "devices": devices,
                "sensors": sensors,
                "projection": projection,
                "projection_details": projection_details,
            },
            "environment": environment,
            "events": all_events,
            "event_count": len(all_events),
            "suggestions": suggestions,
            "safety": [
                "本次快照由用户主动触发，不启动后台常驻场景解析。",
                "相机只采集单帧；麦克风只采集短样本用于活动强度，不做转写。",
                "投影内容默认不解析，只读取连接/遮挡/亮度相关信号。",
            ],
        }
        task = self.create_task("场景传感器快照", "hardware", status, {"include_camera": include_camera, "include_mic": include_mic, "include_hardware": include_hardware}, result)
        self.record_audit(
            "scene_sensor_snapshot",
            status_to_audit(status),
            "scene",
            {"task_id": task["task_id"], "events": len(all_events), "suggestions": len(suggestions)},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_scene_oriented_scan(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        authorized = bool(payload.get("authorized"))
        preflight = self.lelamp_motion_preflight(read_pose=True)
        if not authorized:
            result = {
                "status": "needs_confirmation",
                "source": "explicit_lelamp_oriented_scan",
                "message": "需要用户在页面点击授权后，才允许 LeLamp 小幅转动观察。",
                "preflight": preflight,
                "views": [],
                "events": [],
                "suggestions": [],
                "safety": [
                    "不会后台自动转动。",
                    "不会连续解析投影内容。",
                    "授权后只做一次小范围扫描。",
                ],
            }
            self.record_audit("scene_oriented_scan", "blocked", "lelamp", {"reason": "missing_authorization"}, ctx)
            return result
        if not self.runtime.config.enable_hardware:
            result = {
                "status": "adapter_ready",
                "source": "explicit_lelamp_oriented_scan",
                "message": "已检测到 LeLamp 串口，但当前进程未启用 OPENCLAW_ENABLE_HARDWARE=1；为安全起见不会写入电机目标位置。",
                "preflight": preflight,
                "views": [],
                "events": [],
                "suggestions": [],
            }
            self.record_audit("scene_oriented_scan", "blocked", "lelamp", {"reason": "hardware_disabled"}, ctx)
            return result
        if not preflight.get("pose_readable"):
            result = {
                "status": "needs_hardware",
                "source": "explicit_lelamp_oriented_scan",
                "message": "LeLamp 姿态不可读，未执行转动。请先确认电机供电、串口和校准状态。",
                "preflight": preflight,
                "views": [],
                "events": [],
                "suggestions": [],
            }
            self.record_audit("scene_oriented_scan", "blocked", "lelamp", {"reason": "pose_unreadable"}, ctx)
            return result

        mode = str(payload.get("mode") or "yaw").strip().lower()
        if mode not in {"yaw", "multi_axis", "pan_tilt"}:
            mode = "yaw"
        yaw_delta = clamp_number(optional_float(payload.get("yaw_delta")), default=6.0, low=1.0, high=12.0)
        pitch_delta = clamp_number(optional_float(payload.get("pitch_delta")), default=6.0, low=1.0, high=8.0)
        view_limit = max(1, min(9, safe_int(payload.get("view_limit"), 5 if mode != "yaw" else 3)))
        max_step = clamp_number(optional_float(payload.get("max_step")), default=3.0, low=1.0, high=4.0)
        hold_seconds = clamp_number(optional_float(payload.get("hold_seconds")), default=0.45, low=0.1, high=1.5)
        camera_index = self.resolve_camera_index(payload.get("camera_index"))
        include_mic = bool(payload.get("include_mic", False))
        tilt_motor = str(payload.get("tilt_motor") or "base_pitch").strip()
        if tilt_motor not in {"wrist_pitch", "base_pitch"}:
            tilt_motor = "base_pitch"
        views_plan = scan_view_plan_from_payload(
            payload.get("views"),
            payload.get("offsets"),
            yaw_delta=yaw_delta,
            pitch_delta=pitch_delta,
            mode=mode,
            view_limit=view_limit,
        )
        title = str(payload.get("title") or "lelamp_oriented_scan")

        started = time.monotonic()
        views: list[dict[str, object]] = []
        all_events: list[dict[str, object]] = []
        return_status = "not_started"
        return_error = ""
        port = str(preflight.get("port") or self.runtime.config.hardware_port)
        bus = None
        try:
            from lelamp.person_tracker import read_current_pose

            bus = self.connect_lelamp_motor_bus(port=port, max_step=max_step)
            start_pose = read_current_pose(bus)
            scan_motors = ["base_yaw"] if mode == "yaw" else ["base_yaw", tilt_motor]
            scan_center_pose = dict(start_pose)
            center_lift_offset = 0.0
            if mode != "yaw" and tilt_motor == "wrist_pitch":
                current_tilt = float(start_pose[tilt_motor])
                scan_center_pose[tilt_motor] = clamp_number(
                    max(current_tilt, -45.0),
                    default=current_tilt,
                    low=-55.0,
                    high=35.0,
                )
                center_lift_offset = round(float(scan_center_pose[tilt_motor]) - current_tilt, 3)
            for index, view_plan in enumerate(views_plan):
                yaw_offset = float(view_plan["yaw_offset"])
                pitch_offset = float(view_plan["pitch_offset"])
                target_pose = dict(scan_center_pose)
                target_pose["base_yaw"] = clamp_number(
                    float(start_pose["base_yaw"]) + yaw_offset,
                    default=float(start_pose["base_yaw"]),
                    low=-85.0,
                    high=85.0,
                )
                target_pose[tilt_motor] = clamp_number(
                    float(scan_center_pose[tilt_motor]) + pitch_offset,
                    default=float(scan_center_pose[tilt_motor]),
                    low=-55.0 if tilt_motor == "wrist_pitch" else -100.0,
                    high=35.0 if tilt_motor == "wrist_pitch" else 100.0,
                )
                actual_pose, movement_trace = self.move_lelamp_pose_in_steps(
                    bus,
                    target_pose,
                    motors=scan_motors,
                    max_step=max_step,
                    step_seconds=min(0.25, hold_seconds),
                )
                time.sleep(hold_seconds)
                actual_pose = read_current_pose(bus)
                actual_yaw_offset = float(actual_pose["base_yaw"]) - float(start_pose["base_yaw"])
                actual_pitch_offset = float(actual_pose[tilt_motor]) - float(scan_center_pose[tilt_motor])
                view_payload = {
                    "title": f"{title}_{index}_yaw{yaw_offset:+.1f}_pitch{pitch_offset:+.1f}",
                    "camera_index": camera_index,
                    "include_camera": True,
                    "include_mic": include_mic,
                    "include_hardware": True,
                    "mic_seconds": 1,
                    "lux": payload.get("lux"),
                    "people_count": payload.get("people_count"),
                    "presence": payload.get("presence"),
                    "projector_blocked": payload.get("projector_blocked"),
                    "calendar_event_now": payload.get("calendar_event_now"),
                }
                snapshot = self.api_scene_sensor_snapshot(view_payload, ctx)
                view_events = [item for item in snapshot.get("events", []) if isinstance(item, dict)]
                all_events.extend(view_events)
                views.append(
                    {
                        "index": index,
                        "label": view_plan["label"],
                        "requested_yaw_offset": yaw_offset,
                        "requested_pitch_offset": pitch_offset,
                        "actual_yaw_offset": round(actual_yaw_offset, 3),
                        "actual_pitch_offset": round(actual_pitch_offset, 3),
                        "actual_pitch_from_start": round(float(actual_pose[tilt_motor]) - float(start_pose[tilt_motor]), 3),
                        "target_pose": target_pose,
                        "actual_pose": actual_pose,
                        "movement_trace": movement_trace,
                        "snapshot": compact_scene_snapshot(snapshot),
                        "events": view_events,
                    }
                )

            try:
                self.move_lelamp_pose_in_steps(
                    bus,
                    start_pose,
                    motors=scan_motors,
                    max_step=max_step,
                    step_seconds=min(0.25, hold_seconds),
                )
                time.sleep(hold_seconds)
                return_status = "completed"
            except Exception as exc:
                return_status = "failed"
                return_error = str(exc)[:1000]
        except Exception as exc:
            result = {
                "status": "failed",
                "source": "explicit_lelamp_oriented_scan",
                "message": f"LeLamp 转动观察失败：{str(exc)[:300]}",
                "preflight": preflight,
                "views": views,
                "events": dedupe_scene_events(all_events),
                "suggestions": self.runtime.scene.workflow_suggestions(dedupe_scene_events(all_events)),
                "error": str(exc)[:1000],
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }
            self.record_audit("scene_oriented_scan", "error", "lelamp", {"error": str(exc)[:500], "views": len(views)}, ctx)
            return result
        finally:
            if bus is not None:
                try:
                    bus.disconnect(disable_torque=False)
                except Exception:
                    pass

        deduped_events = dedupe_scene_events(all_events)
        suggestions = self.runtime.scene.workflow_suggestions(deduped_events)
        status = "completed" if views else "unavailable"
        result = {
            "status": status,
            "source": "explicit_lelamp_oriented_scan",
            "message": f"已完成 {len(views)} 个视角的{'左右/抬低' if mode != 'yaw' else '左右'}观察，并尝试回到起始姿态。",
            "preflight": preflight,
            "scan": {
                "mode": mode,
                "motors": ["base_yaw", tilt_motor] if mode != "yaw" else ["base_yaw"],
                "tilt_motor": tilt_motor,
                "tilt_label": "相机微调俯仰轴" if tilt_motor == "wrist_pitch" else "第一抬升舵机 base_pitch",
                "axis_summary": (
                    "左右使用 base_yaw，抬头/低头使用第一抬升舵机 base_pitch。"
                    if mode != "yaw" and tilt_motor == "base_pitch"
                    else "左右使用 base_yaw。"
                ),
                "scan_center_pose": scan_center_pose,
                "center_lift_offset": center_lift_offset,
                "views_plan": views_plan,
                "offsets": [item["yaw_offset"] for item in views_plan],
                "pitch_offsets": [item["pitch_offset"] for item in views_plan],
                "yaw_delta": yaw_delta,
                "pitch_delta": pitch_delta,
                "max_step": max_step,
                "hold_seconds": hold_seconds,
                "camera_index": camera_index,
                "include_mic": include_mic,
                "return_status": return_status,
                "return_error": return_error,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            },
            "views": views,
            "events": deduped_events,
            "event_count": len(deduped_events),
            "suggestions": suggestions,
            "safety": [
                "本次扫描由用户主动授权触发。",
                "多轴扫描只调整 base_yaw 和第一抬升舵机 base_pitch，并限制 yaw/pitch 最大偏移。",
                "扫描结束后尝试回到起始姿态，且不断开扭矩。",
            ],
        }
        task = self.create_task("LeLamp 转动观察环境", "hardware", status, {"mode": mode, "views": len(views), "views_plan": views_plan}, result)
        self.record_audit(
            "scene_oriented_scan",
            status_to_audit(status),
            "lelamp",
            {"task_id": task["task_id"], "views": len(views), "events": len(deduped_events), "return_status": return_status},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_scene_tracking_run(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        authorized = bool(payload.get("authorized"))
        preflight = self.lelamp_motion_preflight(read_pose=True)
        if not authorized:
            result = {
                "status": "needs_confirmation",
                "source": "person_tracker",
                "message": "需要用户显式授权后，才允许 LeLamp 目标追踪试运行。",
                "preflight": preflight,
                "frames": [],
                "target_count": 0,
                "move_count": 0,
            }
            self.record_audit("scene_tracking_run", "blocked", "person_tracker", {"reason": "missing_authorization"}, ctx)
            return result

        if not self.runtime.config.enable_hardware and bool(payload.get("move", False)):
            result = {
                "status": "adapter_ready",
                "source": "person_tracker",
                "message": "当前进程未启用 OPENCLAW_ENABLE_HARDWARE=1；只允许检测，不允许追踪移动。",
                "preflight": preflight,
                "frames": [],
                "target_count": 0,
                "move_count": 0,
            }
            self.record_audit("scene_tracking_run", "blocked", "person_tracker", {"reason": "hardware_disabled"}, ctx)
            return result

        camera_index = self.resolve_camera_index(payload.get("camera_index"))
        backend = str(payload.get("backend") or "yolo").strip().lower()
        if backend not in {"auto", "face", "hog", "yolo"}:
            backend = "yolo"
        frames = max(1, min(60, safe_int(payload.get("frames"), 12)))
        move = bool(payload.get("move", True))
        max_step = clamp_number(optional_float(payload.get("max_step")), default=1.5, low=0.5, high=3.0)
        yaw_gain = clamp_number(optional_float(payload.get("yaw_gain")), default=4.0, low=1.0, high=8.0)
        pitch_gain = clamp_number(optional_float(payload.get("pitch_gain")), default=3.0, low=1.0, high=8.0)
        deadband = clamp_number(optional_float(payload.get("deadband")), default=0.1, low=0.03, high=0.3)
        min_hits = max(1, min(5, safe_int(payload.get("min_hits"), 2)))
        width = max(320, min(3840, safe_int(payload.get("width"), 1280)))
        height = max(240, min(2160, safe_int(payload.get("height"), 720)))
        yolo_model = str(payload.get("yolo_model") or (Path(__file__).resolve().parents[2] / "yolo11n.pt"))

        was_streaming = self.camera_stream_running()
        stream_camera_index = self._camera_stream_camera_index if self._camera_stream_camera_index is not None else camera_index
        started = time.monotonic()
        if was_streaming:
            self.stop_camera_stream_service(ctx=ctx)

        command = [
            sys.executable,
            "-m",
            "lelamp.person_tracker",
            "track",
            "--camera-index",
            str(camera_index),
            "--backend",
            backend,
            "--frames",
            str(frames),
            "--sleep",
            str(clamp_number(optional_float(payload.get("sleep")), default=0.12, low=0.02, high=0.5)),
            "--width",
            str(width),
            "--height",
            str(height),
            "--motion-mode",
            "head",
            "--max-step",
            str(max_step),
            "--yaw-gain",
            str(yaw_gain),
            "--pitch-gain",
            str(pitch_gain),
            "--deadband",
            str(deadband),
            "--min-hits",
            str(min_hits),
            "--yaw-min",
            "-85",
            "--yaw-max",
            "85",
            "--pitch-min",
            "-60",
            "--pitch-max",
            "35",
            "--port",
            str(preflight.get("port") or self.runtime.config.hardware_port),
            "--id",
            str(self.runtime.config.lamp_id),
        ]
        if backend == "yolo":
            command.extend(["--yolo-model", yolo_model])
        if move:
            command.append("--move")

        stdout = ""
        stderr = ""
        return_code = -1
        restored_stream: dict[str, object] | None = None
        try:
            completed = subprocess.run(
                command,
                cwd=str(Path(__file__).resolve().parents[2]),
                check=False,
                capture_output=True,
                text=True,
                timeout=max(15, frames * 2),
            )
            stdout = completed.stdout
            stderr = completed.stderr
            return_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or "tracking command timed out"
            return_code = 124
        finally:
            if was_streaming:
                restored_stream = self.start_camera_stream_service(
                    camera_index=stream_camera_index,
                    width=width,
                    height=height,
                    backend="auto",
                    ctx=ctx,
                )

        frame_payloads: list[dict[str, object]] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                frame_payloads.append(parsed)

        target_count = sum(1 for item in frame_payloads if str(item.get("status") or "") == "target_found")
        move_count = sum(1 for item in frame_payloads if isinstance(item.get("sent_action"), dict))
        status = "completed" if return_code == 0 else "failed"
        if return_code == 0 and target_count == 0:
            status = "no_target"
        result = {
            "status": status,
            "source": "person_tracker",
            "message": "追踪试运行完成，未检测到目标。" if target_count == 0 else f"追踪试运行完成，检测到 {target_count} 帧目标，发出 {move_count} 次移动。",
            "preflight": preflight,
            "request": {
                "camera_index": camera_index,
                "backend": backend,
                "frames": frames,
                "move": move,
                "motion_mode": "head",
                "motors": ["base_yaw", "wrist_pitch"],
                "max_step": max_step,
                "yaw_gain": yaw_gain,
                "pitch_gain": pitch_gain,
                "deadband": deadband,
                "min_hits": min_hits,
                "stream_was_running": was_streaming,
            },
            "frames": frame_payloads,
            "target_count": target_count,
            "move_count": move_count,
            "return_code": return_code,
            "stderr_tail": stderr[-2000:],
            "restored_stream": restored_stream,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
        }
        task = self.create_task("LeLamp 目标追踪试运行", "hardware", status, result["request"], result)
        self.record_audit(
            "scene_tracking_run",
            status_to_audit(status),
            "person_tracker",
            {"task_id": task["task_id"], "target_count": target_count, "move_count": move_count, "return_code": return_code},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def capture_scene_camera_snapshot(self, title: str, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        camera_index = self.resolve_camera_index(payload.get("camera_index"))
        timeout_seconds = max(3, min(12, safe_int(payload.get("timeout_seconds"), 6)))
        capture = self.capture_from_camera_preview_snapshot(title, camera_index=camera_index)
        if str(capture.get("status") or "") != "captured":
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(self.runtime.camera_observer.capture_frame, camera_index=camera_index)
            try:
                capture = future.result(timeout=timeout_seconds)
            except FutureTimeoutError:
                future.cancel()
                capture = {
                    "status": "unavailable",
                    "message": f"设备相机拍照超过 {timeout_seconds} 秒未返回。",
                    "camera_index": camera_index,
                    "timeout_seconds": timeout_seconds,
                }
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        if str(capture.get("status") or "") != "captured":
            return {
                "status": "unavailable",
                "source": "device_camera_capture",
                "camera_index": camera_index,
                "capture": capture,
                "analysis": {},
                "events": [],
                "suggestions": [],
            }
        capture_path = Path(str(capture.get("path") or "")).expanduser().resolve()
        workspace_root = self.runtime.workspace.root.resolve()
        try:
            workspace_name = str(capture_path.relative_to(workspace_root))
        except ValueError:
            self.record_audit("scene_sensor_camera", "blocked", str(capture_path), {"camera_index": camera_index}, ctx)
            return {
                "status": "blocked",
                "source": "device_camera_capture",
                "camera_index": camera_index,
                "capture": capture,
                "analysis": {},
                "events": [],
                "suggestions": [],
                "message": "Camera capture is outside workspace.",
            }
        analysis = self.runtime.camera_observer.analyze_frame(workspace_name)
        events = [item for item in analysis.get("events", []) if isinstance(item, dict)]
        return {
            "status": "completed" if analysis.get("status") == "ok" else normalize_task_status(str(analysis.get("status") or "backend_missing")),
            "source": str(capture.get("source") or "device_camera_capture"),
            "camera_index": camera_index,
            "image_path": str(capture_path),
            "workspace_name": workspace_name,
            "capture": capture,
            "analysis": analysis,
            "events": events,
            "suggestions": self.runtime.scene.workflow_suggestions(events),
        }

    def capture_scene_microphone_activity(self, *, seconds: int) -> dict[str, object]:
        scan = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
        mic_details = hardware_device_details(scan, "mic")
        device = str(mic_details.get("selected_device") or "").strip()
        if not device:
            return {
                "status": "unavailable",
                "message": "No ALSA capture device was detected.",
                "configured_device": self.runtime.config.mic_device,
                "selected_device": "",
                "candidates": mic_details.get("candidates", []),
            }
        output = self.runtime.workspace.path_for_new_file(f"scene_mic_activity_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav")
        result = record_microphone_sample(device, self.runtime.config.mic_rate, seconds, output)
        result.setdefault("configured_device", self.runtime.config.mic_device)
        result.setdefault("selected_device", device)
        result["configured_device_valid"] = bool(mic_details.get("configured_device_valid"))
        result["candidates"] = mic_details.get("candidates", [])
        result["activity_detected"] = str(result.get("status") or "") == "completed" and (safe_int(result.get("rms"), 0) >= 120 or safe_int(result.get("peak"), 0) >= 900)
        result["purpose"] = "scene_activity_only_no_transcription"
        return result

    def capture_from_camera_preview_snapshot(self, title: str, *, camera_index: int) -> dict[str, object]:
        preview_url = os.getenv("LELAMP_CAMERA_STREAM_URL", "http://127.0.0.1:8788").rstrip("/")
        snapshot_url = f"{preview_url}/snapshot.jpg"
        try:
            request = urllib.request.Request(snapshot_url, headers={"Cache-Control": "no-store"})
            with urllib.request.urlopen(request, timeout=3) as response:
                content_type = str(response.headers.get("Content-Type") or "")
                data = response.read()
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            return {
                "status": "unavailable",
                "source": "camera_preview_snapshot",
                "snapshot_url": snapshot_url,
                "camera_index": camera_index,
                "message": f"相机预览快照不可用：{type(exc).__name__}",
            }
        if not data or "image/jpeg" not in content_type.lower():
            return {
                "status": "unavailable",
                "source": "camera_preview_snapshot",
                "snapshot_url": snapshot_url,
                "camera_index": camera_index,
                "content_type": content_type,
                "bytes": len(data),
                "message": "相机预览没有返回 JPEG 画面。",
            }
        path = self.runtime.workspace.path_for_new_file(safe_filename(title, default="camera_preview", suffix="_snapshot.jpg"))
        atomic_write_bytes(path, data)
        return {
            "status": "captured",
            "source": "camera_preview_snapshot",
            "path": str(path),
            "bytes": len(data),
            "snapshot_url": snapshot_url,
            "camera_index": camera_index,
            "command": "camera_stream.snapshot",
        }

    def api_scene_environment(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        reading = {
            "presence": payload.get("presence"),
            "motion": payload.get("motion"),
            "lux": payload.get("lux"),
            "sound_level": payload.get("sound_level"),
            "speech_active": payload.get("speech_active"),
            "people_count": payload.get("people_count"),
            "projector_blocked": payload.get("projector_blocked"),
            "calendar_event_now": payload.get("calendar_event_now"),
        }
        result = self.runtime.environment.ingest(reading)
        result["suggestions"] = self.runtime.scene.workflow_suggestions(
            [item for item in result.get("events", []) if isinstance(item, dict)]
        )
        status = "completed"
        task = self.create_task("环境场景读数", "hardware", status, {"reading": reading}, result)
        self.record_audit(
            "scene_environment",
            "ok",
            "environment",
            {"task_id": task["task_id"], "event_count": result.get("event_count"), "suggestions": len(result["suggestions"])},
            ctx,
        )
        return {"status": status, "task_id": task["task_id"], **result}

    def api_scene_report(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        event_type = require_string(payload, "event_type")
        description = require_string(payload, "description")
        confidence = float(payload.get("confidence") or 1.0)
        event = self.runtime.scene.report_event(event_type, description, confidence)
        suggestions = self.runtime.scene.workflow_suggestions([event])
        self.record_audit("scene_report", "ok", event_type, {"event": event, "suggestions": len(suggestions)}, ctx)
        return {"status": "completed", "event": event, "suggestions": suggestions}

    def write_scene_observation_image(self, image_data_url: str, title: str) -> Path:
        match = re.match(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$", image_data_url)
        if not match:
            raise ApiError("invalid_image", "Expected a PNG, JPEG, or WebP data URL.", status=400)
        mime_type, raw_base64 = match.groups()
        import base64

        try:
            data = base64.b64decode("".join(raw_base64.split()), validate=True)
        except Exception as exc:
            raise ApiError("invalid_image", "Image data URL is not valid base64.", status=400) from exc
        if len(data) > self.max_upload_bytes:
            raise ApiError("image_too_large", f"Image exceeds upload limit of {self.max_upload_bytes} bytes.", status=413)
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime_type]
        path = self.runtime.workspace.path_for_new_file(safe_filename(title, default="desk_scene", suffix=f"_capture{extension}"))
        atomic_write_bytes(path, data)
        self.audit.record("scene.image_capture_write", target=str(path), details={"bytes": len(data), "mime_type": mime_type})
        return path

    def api_hardware_test(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        test = require_string(payload, "test")
        if test == "scan":
            return self.api_hardware_scan(ctx)
        if test == "camera":
            camera_index = self.resolve_camera_index(payload.get("camera_index"))
            result = self.runtime.camera_observer.capture_frame(camera_index=camera_index)
            status = normalize_hardware_test_status(str(result.get("status") or "unavailable"))
            self.record_audit("hardware_test.camera", status_to_audit(status), f"camera:{camera_index}", result, ctx)
            return {"status": status, "test": test, "result": result}
        if test == "mic":
            seconds = safe_int(payload.get("seconds"), 2)
            rate = safe_int(payload.get("rate"), self.runtime.config.mic_rate)
            scan = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
            mic_details = hardware_device_details(scan, "mic")
            requested_device = str(payload.get("device") or "").strip()
            selected_device = str(mic_details.get("selected_device") or "").strip()
            device = requested_device or selected_device
            if not device:
                status = "backend_missing" if mic_details.get("arecord_status") == "backend_missing" else "unavailable"
                result = {
                    "status": status,
                    "message": "No ALSA capture device was detected.",
                    "configured_device": self.runtime.config.mic_device,
                    "selected_device": "",
                    "candidates": mic_details.get("candidates", []),
                }
                self.record_audit("hardware_test.mic", status, "mic", result, ctx)
                return {"status": status, "test": test, "result": result}
            output = self.runtime.workspace.path_for_new_file(f"hardware_mic_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav")
            result = record_microphone_sample(device, rate, seconds, output)
            result.setdefault("configured_device", self.runtime.config.mic_device)
            result.setdefault("selected_device", device)
            result["configured_device_valid"] = bool(mic_details.get("configured_device_valid"))
            result["candidates"] = mic_details.get("candidates", [])
            status = normalize_hardware_test_status(str(result.get("status") or "unavailable"))
            self.record_audit("hardware_test.mic", status_to_audit(status), device, result, ctx)
            return {"status": status, "test": test, "result": result}
        if test == "speaker":
            scan = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
            speaker_details = hardware_device_details(scan, "speaker")
            requested_device = str(payload.get("device") or "").strip()
            selected_device = str(speaker_details.get("selected_device") or "").strip()
            device = requested_device or selected_device
            if not device:
                status = "backend_missing" if speaker_details.get("aplay_status") == "backend_missing" else "unavailable"
                result = {
                    "status": status,
                    "message": "No ALSA playback device was detected.",
                    "configured_device": self.runtime.config.speaker_device,
                    "selected_device": "",
                    "candidates": speaker_details.get("candidates", []),
                }
                self.record_audit("hardware_test.speaker", status, "speaker", result, ctx)
                return {"status": status, "test": test, "result": result}
            output = self.runtime.workspace.path_for_new_file(f"hardware_speaker_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav")
            result = play_speaker_tone(device, output)
            result["configured_device"] = self.runtime.config.speaker_device
            result["selected_device"] = device
            result["configured_device_valid"] = bool(speaker_details.get("configured_device_valid"))
            result["candidates"] = speaker_details.get("candidates", [])
            status = normalize_hardware_test_status(str(result.get("status") or "unavailable"))
            self.record_audit("hardware_test.speaker", status_to_audit(status), device, result, ctx)
            return {"status": status, "test": test, "result": result}
        if test == "projection":
            result = self.runtime.projection.render_status_card(
                "Hardware Projection Test",
                "display_test",
                details=["Generated from /api/hardware/test", "Physical projector detection is reported by /api/hardware/scan"],
                accent="blue",
            )
            self.record_audit("hardware_test.projection", "ok", str(result.get("path")), result, ctx)
            return {"status": "completed", "test": test, "result": result}
        if test == "rgb":
            state = str(payload.get("state") or "success")
            result = self.api_lelamp_state(state, ctx)
            return {"status": result["status"], "test": test, "result": result}
        self.record_audit("hardware_test", "blocked", test, {"reason": "unknown test"}, ctx)
        raise ApiError("unknown_hardware_test", f"Unsupported hardware test: {test}", status=400)

    def api_assistant_providers_status(self, ctx: RequestContext) -> dict[str, object]:
        config = self.runtime.config
        hardware = probe_hardware(config, projection_preview_port=self.projection_preview_port)
        devices = hardware.get("devices") if isinstance(hardware.get("devices"), dict) else {}
        mic = devices.get("mic") if isinstance(devices.get("mic"), dict) else {}
        text_enabled = os.getenv("ASSISTANT_ENABLE_TEXT", "true").lower() not in {"0", "false", "no", "off"}
        pi_mic_enabled = os.getenv("ASSISTANT_ENABLE_PI_MIC", "true").lower() not in {"0", "false", "no", "off"}
        browser_mic_enabled = os.getenv("ALLOW_BROWSER_MIC", "false").lower() in {"1", "true", "yes", "on"}
        qwen_status = "available" if getattr(config, "dashscope_api_key", "") else "backend_missing"
        qwen = {
            "status": qwen_status,
            "model": getattr(config, "dashscope_realtime_model", "qwen3-omni-flash-realtime"),
            "url": redact_provider_url(getattr(config, "dashscope_realtime_url", "")),
            "text_input": bool(text_enabled),
            "pi_mic_input": bool(pi_mic_enabled and mic.get("status") == "available"),
            "pi_mic_status": str(mic.get("status") or "unavailable"),
            "browser_mic_input": bool(browser_mic_enabled),
            "transcription_model": getattr(config, "dashscope_realtime_transcription_model", "gummy-realtime-v1"),
            "voice": getattr(config, "dashscope_realtime_voice", "Cherry"),
            "tts": {
                "provider": getattr(config, "tts_provider", "openai"),
                "model": getattr(config, "dashscope_tts_model", getattr(config, "tts_model", "")),
                "voice": getattr(config, "dashscope_tts_voice", getattr(config, "tts_voice", "")),
                "status": server_tts_status(config),
                "mode": "server_side_only",
            },
        }
        foreground_provider = os.getenv("ASSISTANT_FRONTEND_PROVIDER", "").strip()
        if not foreground_provider:
            foreground_provider = "qwen_omni" if qwen_status == "available" else "local_frontend"
        result = {
            "foreground_provider": foreground_provider,
            "qwen_omni": qwen,
            "openclaw": {
                "status": "available",
                "router": "OfficeIntentRouter",
                "executor": "run_manual_agent",
                "permission_mode": config.permission_mode.value,
                "desktop_backend": config.desktop_backend,
            },
            "input": {
                "text": "available" if text_enabled else "unavailable",
                "pi_mic": qwen["pi_mic_status"],
                "browser_mic": "available" if browser_mic_enabled else "disabled",
            },
            "safety": {
                "qwen_direct_file_access": False,
                "qwen_direct_shell": False,
                "qwen_direct_desktop_control": False,
                "task_router": "OpenClaw",
            },
        }
        self.record_audit(
            "assistant_provider_status",
            "ok",
            "assistant_providers",
            {
                "foreground_provider": result["foreground_provider"],
                "qwen_omni_status": qwen_status,
                "openclaw_status": "available",
            },
            ctx,
        )
        return result

    def api_assistant_realtime_status(self, ctx: RequestContext) -> dict[str, object]:
        providers = self.api_assistant_providers_status(ctx)
        qwen = providers.get("qwen_omni") if isinstance(providers.get("qwen_omni"), dict) else {}
        status = str(qwen.get("status") or "backend_missing")
        return {
            "status": status,
            "provider": "qwen_omni",
            "model": qwen.get("model"),
            "text_input": qwen.get("text_input"),
            "pi_mic_input": qwen.get("pi_mic_input"),
            "browser_mic_input": qwen.get("browser_mic_input"),
            "message": "Qwen-Omni realtime is configured on the Raspberry Pi/server side." if status == "available" else "DASHSCOPE_API_KEY is required for Qwen-Omni realtime.",
        }

    def api_voice_status(self, ctx: RequestContext) -> dict[str, object]:
        status = self.build_voice_status()
        self.record_audit(
            "voice.status",
            status_to_audit(str(status.get("status"))),
            "voice_stack",
            {
                "mic": status.get("mic", {}).get("status") if isinstance(status.get("mic"), dict) else "",
                "asr": status.get("asr", {}).get("status") if isinstance(status.get("asr"), dict) else "",
                "vad": status.get("vad", {}).get("status") if isinstance(status.get("vad"), dict) else "",
            },
            ctx,
        )
        return status

    def build_voice_status(self) -> dict[str, object]:
        config = self.runtime.config
        hardware = probe_hardware(config, projection_preview_port=self.projection_preview_port)
        devices = hardware.get("devices") if isinstance(hardware.get("devices"), dict) else {}
        mic = devices.get("mic") if isinstance(devices.get("mic"), dict) else {"status": "unavailable", "details": {}}
        speaker = devices.get("speaker") if isinstance(devices.get("speaker"), dict) else {"status": "unavailable", "details": {}}
        wake_modules = {
            "pvporcupine": _module_available("pvporcupine"),
            "pvrecorder": _module_available("pvrecorder"),
        }
        vad_modules = {
            "webrtcvad": _module_available("webrtcvad"),
            "sounddevice": _module_available("sounddevice"),
            "pyaudio": _module_available("pyaudio"),
        }
        qwen_status = "available" if getattr(config, "dashscope_api_key", "") else "backend_missing"
        asr_status = "available" if (
            (config.asr_provider == "dashscope" and config.dashscope_api_key)
            or (config.asr_provider == "openai" and config.openai_api_key)
            or (config.asr_provider == "groq" and config.groq_api_key)
        ) else "backend_missing"
        vad_status = "available" if vad_modules["webrtcvad"] else "backend_missing"
        wake_status = "available" if all(wake_modules.values()) else "adapter_ready"
        mic_status = str(mic.get("status") or "unavailable")
        speaker_status = str(speaker.get("status") or "unavailable")
        overall = "available" if mic_status == "available" and asr_status == "available" and vad_status == "available" else "adapter_ready"
        return {
            "status": overall,
            "wake_word": {
                "status": wake_status,
                "default_wake_word": "小灯",
                "mode": "local_porcupine_available" if wake_status == "available" else "keyword_in_transcript_fallback",
                "modules": wake_modules,
            },
            "vad": {
                "status": vad_status,
                "backend": "webrtcvad" if vad_modules["webrtcvad"] else "rms_fallback",
                "modules": vad_modules,
                "endpointing": "record_wav_endpointed plan documented; pi-voice endpoint uses explicit bounded capture.",
            },
            "asr": {
                "status": asr_status,
                "provider": config.asr_provider,
                "model": config.asr_model,
                "dashscope_model": config.dashscope_asr_model,
            },
            "tts": {
                "status": server_tts_status(config),
                "provider": config.tts_provider,
                "model": config.tts_model,
                "voice": config.tts_voice,
                "server_side_only": True,
            },
            "realtime": {
                "status": qwen_status,
                "provider": "qwen_omni",
                "model": config.dashscope_realtime_model,
                "turn_detection": "server_vad",
                "transcription_model": config.dashscope_realtime_transcription_model,
            },
            "conversation": {
                "status": "available",
                "mode": "explicit_session_text_or_authorized_voice",
                "wake_word": "小灯",
                "multi_turn_context": True,
                "long_term_memory": str(self.runtime.config.memory_path),
                "barge_in_policy": "stop_current_server_tts_before_next_explicit_turn",
                "endpoints": [
                    "/api/voice/conversation/start",
                    "/api/voice/conversation/turn",
                    "/api/voice/conversation/stop",
                ],
            },
            "mic": {
                "status": mic_status,
                "configured_device": config.mic_device,
                "details": mic.get("details", {}),
            },
            "speaker": {
                "status": speaker_status,
                "configured_device": config.speaker_device,
                "details": speaker.get("details", {}),
            },
            "safety": [
                "Browser and Pi microphone capture require explicit user action.",
                "No continuous microphone stream is started from the web console.",
                "Continuous conversation sessions only process explicit text turns or separately authorized bounded voice captures.",
                "Cloud ASR/realtime providers are disabled when cloud AI policy disables provider keys.",
            ],
        }

    def api_voice_conversation_start(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        authorized = bool(payload.get("authorized"))
        if not authorized:
            result = {
                "status": "needs_confirmation",
                "message": "Starting a continuous conversation session requires explicit user confirmation.",
            }
            self.record_audit("voice_conversation.start", "blocked", "voice_conversation", result, ctx)
            return result
        session_id = str(payload.get("session_id") or f"voice_s_{uuid4().hex}")
        wake_word = str(payload.get("wake_word") or "小灯").strip() or "小灯"
        now = now_iso()
        session = {
            "session_id": session_id,
            "status": "running",
            "wake_word": wake_word,
            "started_at": now,
            "updated_at": now,
            "turns": [],
            "turn_count": 0,
            "memory_hits": [],
            "safety": [
                "No passive microphone stream is started.",
                "Text turns must include the wake word until wake gate is disabled.",
                "Memory writes require remember=true on an explicit turn.",
            ],
        }
        with self._voice_conversation_lock:
            self._voice_conversations[session_id] = session
        self.record_audit("voice_conversation.start", "ok", session_id, {"wake_word": wake_word}, ctx)
        return {**session, "status": "completed", "session": session}

    def api_voice_conversation_status(self, session_id: str, ctx: RequestContext) -> dict[str, object]:
        session_id = str(session_id or "").strip()
        with self._voice_conversation_lock:
            if session_id:
                session = self._voice_conversations.get(session_id)
            else:
                session = next(reversed(self._voice_conversations.values()), None) if self._voice_conversations else None
        if session is None:
            result = {"status": "empty", "session": None, "active_sessions": 0}
        else:
            result = {"status": "completed", "session": session, "active_sessions": len(self._voice_conversations)}
        self.record_audit("voice_conversation.status", "ok", session_id or "latest", {"found": session is not None}, ctx)
        return result

    def api_voice_conversation_turn(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        session_id = require_string(payload, "session_id")
        text = str(payload.get("text") or payload.get("message") or "").strip()
        if not text:
            raise ApiError("missing_message", "Missing conversation turn text.", status=400)
        with self._voice_conversation_lock:
            session = self._voice_conversations.get(session_id)
        if session is None:
            raise ApiError("voice_session_not_found", "Voice conversation session not found.", status=404)
        if str(session.get("status")) != "running":
            raise ApiError("voice_session_not_running", "Voice conversation session is not running.", status=409)
        wake_word = str(session.get("wake_word") or "小灯")
        wake_required = bool(payload.get("wake_required", True))
        woke = (wake_word in text) or text.lower().startswith(wake_word.lower())
        if wake_required and not woke:
            result = {
                "status": "waiting_wake_word",
                "session_id": session_id,
                "wake_word": wake_word,
                "message": "Wake word not detected; turn was ignored.",
            }
            self.record_audit("voice_conversation.turn", "blocked", session_id, {"reason": "wake_word_missing"}, ctx)
            return result
        clean_text = text.replace(wake_word, "", 1).strip(" ，,。") or text
        memory_hits = self.runtime.memory.search(clean_text, limit=5) if clean_text else []
        context = {
            "page": "voice",
            "voice_conversation_session": session_id,
            "turn_count": int(session.get("turn_count") or 0) + 1,
            "memory_hits": memory_hits,
        }
        assistant = self.api_assistant_message(
            {
                "text": clean_text,
                "session_id": session_id,
                "input_type": "voice_conversation_text",
                "page": "voice",
                "context": context,
                "speak": bool(payload.get("speak", False)),
            },
            ctx,
        )
        remembered = None
        if bool(payload.get("remember")):
            remembered = self.runtime.memory.remember(
                f"voice:{session_id}:{int(session.get('turn_count') or 0) + 1}",
                clean_text,
                "voice_conversation",
            )
        assistant_text = ""
        assistant_message = assistant.get("assistant_message") if isinstance(assistant.get("assistant_message"), dict) else {}
        if assistant_message:
            assistant_text = str(assistant_message.get("text") or "")
        elif isinstance(assistant.get("assistant_ack"), dict):
            assistant_text = str(assistant["assistant_ack"].get("text") or "")
        turn = {
            "timestamp": now_iso(),
            "input": clean_text,
            "wake_word_detected": woke,
            "assistant_text": assistant_text,
            "assistant": assistant,
            "memory_hits": memory_hits,
            "remembered": remembered,
        }
        with self._voice_conversation_lock:
            current = self._voice_conversations.get(session_id)
            if current is not None:
                turns = current.get("turns") if isinstance(current.get("turns"), list) else []
                turns.append(turn)
                current["turns"] = turns[-30:]
                current["turn_count"] = int(current.get("turn_count") or 0) + 1
                current["updated_at"] = now_iso()
                current["memory_hits"] = memory_hits
                session = current
        self.record_audit("voice_conversation.turn", "ok", session_id, {"turn_count": session.get("turn_count"), "remembered": bool(remembered)}, ctx)
        return {"status": "completed", "session": session, "turn": turn}

    def api_voice_conversation_stop(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        session_id = require_string(payload, "session_id")
        with self._voice_conversation_lock:
            session = self._voice_conversations.get(session_id)
            if session is None:
                raise ApiError("voice_session_not_found", "Voice conversation session not found.", status=404)
            session["status"] = "stopped"
            session["stopped_at"] = now_iso()
            session["updated_at"] = session["stopped_at"]
        self.record_audit("voice_conversation.stop", "ok", session_id, {"turn_count": session.get("turn_count")}, ctx)
        return {"status": "completed", "session": session}

    def api_voice_capture_once(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        if not bool(payload.get("authorized")):
            result = {"status": "needs_confirmation", "message": "Explicit voice capture authorization is required."}
            self.record_audit("voice.capture_once", "blocked", "pi_microphone", result, ctx)
            return result
        result = self.api_assistant_pi_voice_once(payload, ctx)
        self.record_audit("voice.capture_once", status_to_audit(str(result.get("status"))), "pi_microphone", {"status": result.get("status")}, ctx)
        return result

    def push_assistant_notification(
        self,
        *,
        event: str,
        text: str,
        status: str = "completed",
        attachment: str = "",
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        item = self.normalize_assistant_notification({
            "event": event,
            "text": text,
            "status": status,
            "attachment": attachment,
            "payload": payload or {},
        })
        with self._assistant_notification_lock:
            dedupe_key = self.assistant_notification_dedupe_key(item)
            if dedupe_key:
                existing_index = next(
                    (
                        index
                        for index, existing in enumerate(self._assistant_notifications)
                        if self.assistant_notification_dedupe_key(existing) == dedupe_key
                    ),
                    None,
                )
                if existing_index is not None:
                    del self._assistant_notifications[existing_index]
                    self._assistant_notifications.append(item)
                else:
                    self._assistant_notifications.append(item)
            else:
                self._assistant_notifications.append(item)
            self._assistant_notifications = self._assistant_notifications[-80:]
            self.persist_assistant_notifications_locked()
        return item

    def assistant_notification_dedupe_key(self, item: dict[str, object]) -> str:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        meeting_id = str(payload.get("meeting_id") or "").strip()
        title = str(payload.get("title") or "").strip()
        event = str(item.get("event") or "").strip()
        if meeting_id and event:
            return f"{event}:{meeting_id}"
        if title and event:
            return f"{event}:title:{title}"
        return ""

    def normalize_assistant_notification(self, item: dict[str, object]) -> dict[str, object]:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        return {
            "id": str(item.get("id") or f"ntf_{uuid4().hex}"),
            "event": redact_sensitive_text(str(item.get("event") or "assistant_notification"))[:120],
            "timestamp": str(item.get("timestamp") or now_iso()),
            "text": redact_sensitive_text(str(item.get("text") or ""))[:2000],
            "status": redact_sensitive_text(str(item.get("status") or "completed"))[:80],
            "attachment": redact_sensitive_text(str(item.get("attachment") or ""))[:4000],
            "payload": sanitize_event_payload(payload),
        }

    def assistant_notifications_path(self) -> Path:
        path = (self.runtime.config.workspace_dir / ".assistant" / "notifications.json").resolve()
        workspace = self.runtime.config.workspace_dir.resolve()
        if not path.is_relative_to(workspace):
            raise ApiError("invalid_assistant_notifications_path", "Assistant notification path is outside workspace.", status=500)
        return path

    def load_assistant_notifications(self) -> list[dict[str, object]]:
        path = self.assistant_notifications_path()
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.audit.record(
                "assistant_notifications.load",
                status="error",
                target=str(path),
                details={"reason": "invalid_json_or_unreadable"},
            )
            return []
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        normalized = [self.normalize_assistant_notification(item) for item in items if isinstance(item, dict)][-80:]
        try:
            atomic_write_json(path, {"items": normalized, "updated_at": now_iso()})
        except OSError as exc:
            self.audit.record(
                "assistant_notifications.persist",
                status="error",
                target=str(path),
                details={"reason": redact_sensitive_text(str(exc))[:500]},
            )
        return normalized

    def persist_assistant_notifications_locked(self) -> None:
        path = self.assistant_notifications_path()
        atomic_write_json(path, {"items": self._assistant_notifications[-80:], "updated_at": now_iso()})

    def api_assistant_notifications(self, since: str, ctx: RequestContext) -> dict[str, object]:
        with self._assistant_notification_lock:
            items = list(self._assistant_notifications)
        since_found = True
        if since:
            if since.startswith("ntf_"):
                seen = False
                filtered: list[dict[str, object]] = []
                for item in items:
                    if seen:
                        filtered.append(item)
                    elif str(item.get("id") or "") == since:
                        seen = True
                since_found = seen
                items = filtered if seen else items
            else:
                items = [item for item in items if str(item.get("timestamp") or "") > since]
        self.record_audit("assistant_notifications", "ok", "assistant_panel", {"count": len(items)}, ctx)
        return {"status": "ok", "items": items, "total": len(items), "since_found": since_found}

    def api_assistant_realtime_session(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        status = self.api_assistant_realtime_status(ctx)
        if status.get("status") != "available":
            self.record_audit("qwen_omni_session_start", "backend_missing", "qwen_omni", {"reason": "missing_dashscope_api_key"}, ctx)
            return {
                "status": "backend_missing",
                "session_id": None,
                "message": "DASHSCOPE_API_KEY is not configured; Raspberry Pi realtime voice session cannot start.",
                "provider": status,
            }
        session_id = str(payload.get("session_id") or f"asst_s_{uuid4().hex}")
        self.record_audit("qwen_omni_session_start", "adapter_ready", session_id, {"mode": "status_only"}, ctx)
        return {
            "status": "adapter_ready",
            "session_id": session_id,
            "message": "Qwen-Omni realtime client is present in the runtime; HTTP console session control is adapter_ready.",
            "provider": status,
        }

    def api_assistant_pi_voice_once(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        from .dashscope_asr import DashScopeASR, DashScopeASRError

        seconds = max(1, min(8, safe_int(payload.get("seconds"), 4)))
        scan = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
        mic_details = hardware_device_details(scan, "mic")
        device = str(payload.get("device") or mic_details.get("selected_device") or "").strip()
        if not device:
            status = "backend_missing" if mic_details.get("arecord_status") == "backend_missing" else "unavailable"
            result = {
                "status": status,
                "message": "No Raspberry Pi/server-side microphone was detected.",
                "configured_device": self.runtime.config.mic_device,
                "candidates": mic_details.get("candidates", []),
            }
            self.record_audit("qwen_omni_voice_input", status, "pi_microphone", result, ctx)
            return result
        output = self.runtime.workspace.path_for_new_file(f"assistant_pi_voice_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav")
        capture = record_microphone_sample(device, self.runtime.config.mic_rate, seconds, output)
        capture_status = normalize_hardware_test_status(str(capture.get("status") or "unavailable"))
        if capture_status != "completed":
            self.record_audit("qwen_omni_voice_input", status_to_audit(capture_status), device, capture, ctx)
            return {"status": capture_status, "capture": capture, "transcript": "", "message": "Pi microphone capture failed or is unavailable."}
        try:
            asr = DashScopeASR(
                api_key=self.runtime.config.dashscope_api_key,
                model=self.runtime.config.dashscope_asr_model,
                sample_rate=self.runtime.config.mic_rate,
            )
            transcript = asr.transcribe(output, language_hints=["zh", "en"]).strip()
        except DashScopeASRError as exc:
            result = {"status": "backend_missing" if "API_KEY" in str(exc) else "error", "capture": capture, "transcript": "", "message": str(exc)}
            self.record_audit("qwen_omni_voice_input", status_to_audit(str(result["status"])), device, result, ctx)
            return result
        if not transcript or transcript.lower() in {"none", "null", "undefined"}:
            result = {"status": "unavailable", "capture": capture, "transcript": "", "message": "ASR returned an empty transcript."}
            self.record_audit("qwen_omni_voice_input", "unavailable", device, result, ctx)
            return result
        message_payload = {
            "text": transcript,
            "input_type": "pi_voice",
            "page": str(payload.get("page") or "assistant"),
            "context": {
                **(payload.get("context") if isinstance(payload.get("context"), dict) else {}),
                "page": str(payload.get("page") or "assistant"),
                "pi_voice_audio_path": str(output),
            },
            "speak": bool(payload.get("speak", True)),
        }
        self.record_audit("qwen_omni_voice_input", "ok", device, {"transcript_chars": len(transcript), "audio_path": str(output), "seconds": seconds}, ctx)
        assistant = self.api_assistant_message(message_payload, ctx)
        return {"status": "completed", "transcript": transcript, "capture": capture, "assistant": assistant}

    def api_assistant_message(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        text = str(payload.get("text") or payload.get("message") or "").strip()
        if not text:
            raise ApiError("missing_message", "Missing assistant message.", status=400)
        session_id = str(payload.get("session_id") or f"asst_s_{uuid4().hex}")
        input_type = str(payload.get("input_type") or "text")
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        page = str(payload.get("page") or context.get("page") or "assistant")
        route = self.runtime.intent_router.route(text)
        route_payload = route.as_dict()
        route_kind = "chat" if assistant_route_is_chat(route_payload, text) else "task"
        message_id = f"msg_{uuid4().hex}"
        voice_enabled = bool(payload.get("speak", True))
        self.record_audit(
            "assistant_message_received",
            "ok",
            session_id,
            {"message_id": message_id, "input_type": input_type, "text_length": len(text), "page": page},
            ctx,
        )

        if route_kind == "chat":
            chat = self.qwen_omni_chat(text, ctx, message_id)
            reply = str(chat.get("text") or local_chat_reply(text))
            provider = str(chat.get("provider") or "local_frontend")
            provider_status = str(chat.get("status") or "available")
            speech_result: dict[str, object] = {"status": "skipped", "mode": "server_side_only", "reason": "voice_disabled"}
            if voice_enabled:
                speech_result = synthesize_and_play_on_server(self.runtime.config, reply, self.projection_preview_port)
                self.record_audit(
                    "tts_play",
                    status_to_audit(str(speech_result.get("status") or "unavailable")),
                    "server_speaker",
                    {"message_id": message_id, "provider": speech_result.get("provider")},
                    ctx,
                )
            self.record_audit(
                "assistant_chat",
                "ok",
                provider,
                {
                    "message_id": message_id,
                    "route": "ordinary_chat",
                    "openclaw_called": False,
                    "qwen_omni_status": provider_status if provider == "qwen_omni" else chat.get("qwen_omni_status", "backend_missing"),
                },
                ctx,
            )
            return {
                "session_id": session_id,
                "message_id": message_id,
                "route": {
                    "kind": "chat",
                    "intent": "ordinary_chat",
                    "requires_openclaw": False,
                    "requires_confirmation": False,
                },
                "assistant_message": {
                    "text": reply,
                    "streamed": False,
                    "speak": voice_enabled,
                    "provider": provider,
                    "provider_status": provider_status,
                    "speech": speech_result,
                },
            }

        high_risk = assistant_high_risk_policy(text)
        if high_risk["blocked"]:
            ack_text = "这个操作风险较高，当前 sandbox/audit_only 安全策略不会直接执行。"
            final_text = str(high_risk["message"])
            response = {
                "message_id": uuid4().hex,
                "detected_intent": "high_risk_blocked",
                "skills_to_call": [{"name": route.skill, "status": "blocked"}],
                "requires_confirmation": False,
                "confirmation": None,
                "result": {
                    "status": "blocked",
                    "summary": final_text,
                    "display_text": final_text,
                    "details": {
                        "blocked": True,
                        "intent": "high_risk_blocked",
                        "route_summary": route.summary,
                        "tool": route.skill,
                        "tool_args": {},
                        "tool_result": {"reason": high_risk["reason"], "policy": "sandbox_audit_only_preflight"},
                    },
                    "outputs": [],
                    "assistant_final_message": {"text": final_text, "speak": False},
                },
                "task_id": "",
            }
            task = self.create_task(
                title=text[:80],
                task_type="assistant",
                status="blocked",
                input_payload={
                    "session_id": session_id,
                    "message_id": message_id,
                    "input_type": input_type,
                    "message": text,
                    "page": page,
                    "route": route_payload,
                    "context": context,
                    "preflight_blocked": True,
                },
                output=response,
            )
            response["task_id"] = task["task_id"]
            self.append_task_event(task["task_id"], "task_acknowledged", {"status": "blocked", "text": ack_text})
            self.append_task_event(task["task_id"], "task_blocked", {"status": "blocked", "assistant_final_message": response["result"]["assistant_final_message"], "reason": high_risk["reason"]})
            self.record_audit(
                "assistant_route",
                "blocked",
                str(task["task_id"]),
                {"message_id": message_id, "kind": "task", "intent": "high_risk_blocked", "reason": high_risk["reason"]},
                ctx,
            )
            self.record_audit(
                "assistant_task_blocked",
                "blocked",
                str(task["task_id"]),
                {"intent": "high_risk_blocked", "reason": high_risk["reason"], "openclaw_called": False},
                ctx,
            )
            return {
                "session_id": session_id,
                "message_id": message_id,
                "assistant_ack": {"text": ack_text, "speak": voice_enabled},
                "route": {
                    "kind": "task",
                    "intent": "high_risk_blocked",
                    "requires_openclaw": False,
                    "requires_confirmation": False,
                    "summary": route.summary,
                    "skill": route.skill,
                    "blocked": True,
                },
                "task": {
                    "task_id": task["task_id"],
                    "status": "blocked",
                    "monitor_url": f"/api/tasks/{task['task_id']}",
                    "events_url": f"/api/tasks/{task['task_id']}/events",
                },
            }

        ack_text = str(context.get("foreground_reply") or "").strip() or assistant_ack_for_route(text, route_payload)
        task = self.create_task(
            title=text[:80],
            task_type="assistant",
            status="running",
            input_payload={
                "session_id": session_id,
                "message_id": message_id,
                "input_type": input_type,
                "message": text,
                "page": page,
                "route": route_payload,
                "context": context,
            },
            output={
                "assistant_ack": {"text": ack_text, "speak": voice_enabled},
                "events": [
                    {"event": "task_created", "timestamp": now_iso(), "status": "running"},
                    {"event": "task_acknowledged", "timestamp": now_iso(), "status": "running", "text": ack_text},
                ],
            },
        )
        self.record_audit(
            "assistant_route",
            "ok",
            str(task["task_id"]),
            {
                "message_id": message_id,
                "kind": "task",
                "intent": route.intent,
                "skill": route.skill,
                "requires_confirmation": route.requires_confirmation,
            },
            ctx,
        )
        self.record_audit(
            "openclaw_task_created",
            "ok",
            str(task["task_id"]),
            {"message_id": message_id, "intent": route.intent, "source": input_type},
            ctx,
        )
        if voice_enabled and ack_text:
            threading.Thread(
                target=self._speak_for_task,
                args=(ack_text, str(task["task_id"]), "assistant_ack_tts", ctx),
                name=f"assistant-ack-tts-{task['task_id']}",
                daemon=True,
            ).start()
        threading.Thread(
            target=self._run_assistant_task,
            args=(str(task["task_id"]), text, voice_enabled, ctx),
            name=f"assistant-task-{task['task_id']}",
            daemon=True,
        ).start()
        return {
            "session_id": session_id,
            "message_id": message_id,
            "assistant_ack": {"text": ack_text, "speak": voice_enabled},
            "route": {
                "kind": "task",
                "intent": route.intent,
                "requires_openclaw": True,
                "requires_confirmation": route.requires_confirmation,
                "summary": route.summary,
                "skill": route.skill,
            },
            "task": {
                "task_id": task["task_id"],
                "status": task["status"],
                "monitor_url": f"/api/tasks/{task['task_id']}",
                "events_url": f"/api/tasks/{task['task_id']}/events",
            },
        }

    def _speak_for_task(self, text: str, task_id: str, action: str, ctx: RequestContext) -> None:
        result = synthesize_and_play_on_server(self.runtime.config, text, self.projection_preview_port)
        self.record_audit(
            action,
            status_to_audit(str(result.get("status") or "unavailable")),
            "server_speaker",
            {"task_id": task_id, "provider": result.get("provider")},
            ctx,
        )

    def qwen_omni_chat(self, text: str, ctx: RequestContext, message_id: str) -> dict[str, object]:
        config = self.runtime.config
        if not getattr(config, "dashscope_api_key", ""):
            self.record_audit("qwen_omni_text_input", "backend_missing", "qwen_omni", {"message_id": message_id, "reason": "missing_dashscope_api_key"}, ctx)
            return {"status": "backend_missing", "provider": "local_frontend", "qwen_omni_status": "backend_missing", "text": local_chat_reply(text)}
        try:
            from .dashscope_realtime import DashScopeRealtimeClient, DashScopeRealtimeConfig, DashScopeRealtimeError

            client = DashScopeRealtimeClient(
                DashScopeRealtimeConfig(
                    api_key=getattr(config, "dashscope_api_key", ""),
                    model=getattr(config, "dashscope_realtime_model", "qwen3-omni-flash-realtime"),
                    url=getattr(config, "dashscope_realtime_url", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"),
                    voice=getattr(config, "dashscope_realtime_voice", "Cherry"),
                    transcription_model=getattr(config, "dashscope_realtime_transcription_model", "gummy-realtime-v1"),
                    instructions=(
                        "你是 LeLamp 本地 AI 办公终端的前台助手，类似小爱同学。"
                        "只负责普通自然对话、解释和引导；不要声称已经读取文件、查询天气、控制硬件或执行后台任务。"
                        "涉及文件、会议、投影、硬件、天气、审计或桌面操作时，应提示将交给 OpenClaw 后台处理。"
                        "回答使用简体中文，简短自然，适合语音朗读。"
                    ),
                )
            )
            self.record_audit("qwen_omni_text_input", "ok", "qwen_omni", {"message_id": message_id, "chars": len(text)}, ctx)
            started = time.perf_counter()
            result = client.ask_text(text, timeout=25)
            client.close()
            answer = str(result.get("text") or "").strip()
            if not answer:
                raise DashScopeRealtimeError("Qwen-Omni returned empty text.")
            self.record_audit(
                "qwen_omni_response",
                "ok",
                "qwen_omni",
                {"message_id": message_id, "chars": len(answer), "duration_ms": int((time.perf_counter() - started) * 1000)},
                ctx,
            )
            return {"status": "available", "provider": "qwen_omni", "text": answer}
        except Exception as exc:  # Realtime model failures must degrade honestly without breaking local task routing.
            self.record_audit("qwen_omni_response", "error", "qwen_omni", {"message_id": message_id, "error": str(exc)[:1000]}, ctx)
            return {"status": "error", "provider": "local_frontend", "qwen_omni_status": "error", "text": local_chat_reply(text), "error": str(exc)}

    def _run_assistant_task(self, task_id: str, text: str, voice_enabled: bool, ctx: RequestContext) -> None:
        from openclaw_cli import run_manual_agent

        started = time.perf_counter()
        self.append_task_event(task_id, "task_started", {"status": "running"})
        self.update_task(task_id, status="running", progress=0.35)
        self.record_audit("openclaw_run_manual_agent", "ok", task_id, {"text_length": len(text)}, ctx)
        try:
            result = run_manual_agent(self.runtime, text)
            response = self.manual_agent_response_from_result(result, text, task_id)
            status = str(response["result"]["status"])
            task_status = normalize_task_status(status)
            response["result"]["assistant_final_message"] = {
                "text": response["result"]["display_text"],
                "speak": voice_enabled,
            }
            event_name = {
                "completed": "task_completed",
                "waiting_confirmation": "task_waiting_confirmation",
                "blocked": "task_blocked",
                "failed": "task_failed",
            }.get(task_status, "task_failed")
            self.append_task_event(
                task_id,
                event_name,
                {
                    "status": task_status,
                    "assistant_final_message": response["result"]["assistant_final_message"],
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            self.update_task(task_id, status=task_status, progress=1.0, output=response)
            self.record_audit(
                f"assistant_{event_name}",
                status_to_audit(task_status),
                task_id,
                {
                    "intent": response.get("detected_intent"),
                    "skills": response.get("skills_to_call"),
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                },
                ctx,
            )
            if voice_enabled and task_status not in {"blocked", "waiting_confirmation"}:
                speech = synthesize_and_play_on_server(self.runtime.config, str(response["result"]["display_text"]), self.projection_preview_port)
                self.record_audit("tts_play", status_to_audit(str(speech.get("status") or "unavailable")), "server_speaker", {"task_id": task_id, "provider": speech.get("provider")}, ctx)
                response["result"]["speech"] = speech
                self.update_task(task_id, output=response)
        except BaseException as exc:  # noqa: BLE001 - CLI helpers may raise SystemExit; persist failure instead of losing the task.
            message = str(exc) or exc.__class__.__name__
            status = "backend_missing" if isinstance(exc, (ImportError, ModuleNotFoundError)) else "failed"
            final_text = f"后台执行失败：{message}"
            response = {
                "message_id": uuid4().hex,
                "detected_intent": "unknown",
                "skills_to_call": [],
                "requires_confirmation": False,
                "confirmation": None,
                "result": {
                    "status": status,
                    "summary": message,
                    "display_text": final_text,
                    "details": {"error": message},
                    "outputs": [],
                    "assistant_final_message": {"text": final_text, "speak": False},
                },
                "task_id": task_id,
            }
            self.append_task_event(task_id, "task_failed", {"status": "failed", "error": message})
            self.update_task(task_id, status="failed", progress=1.0, output=response, error={"code": status, "message": message})
            self.record_audit("assistant_task_failed", "error", task_id, {"error": message[:1000]}, ctx)

    def manual_agent_response_from_result(self, result: dict[str, object], text: str, task_id: str | None = None) -> dict[str, object]:
        route = result.get("route") if isinstance(result.get("route"), dict) else {}
        tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
        skill_name = str(tool.get("name") or route.get("skill") or "plan_office_task")
        dangerous = any(marker in text for marker in ["删除", "发送邮件", "支付", "提交表单", "全权", "full_control"])
        blocked = any(marker in text for marker in ["删除", "支付", "提交表单"])
        status = "blocked" if blocked else ("waiting_confirmation" if dangerous else normalize_result_status(result.get("result")))
        response_text = summarize_manual_result(result, blocked=blocked)
        return {
            "message_id": uuid4().hex,
            "detected_intent": route.get("intent") or "unknown",
            "skills_to_call": [{"name": skill_name, "status": "available" if not blocked else "blocked"}],
            "requires_confirmation": dangerous and not blocked,
            "confirmation": {
                "confirmation_id": uuid4().hex,
                "risk_level": "high" if dangerous else "low",
                "summary": "高风险动作需要逐任务确认。" if dangerous else "低风险本地计划。",
            } if dangerous and not blocked else None,
            "result": {
                "status": status,
                "summary": response_text,
                "display_text": response_text,
                "details": manual_result_details(result, blocked=blocked),
                "outputs": collect_outputs(result),
            },
            "task_id": task_id or "",
            "raw": result,
        }

    def api_manual(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        from openclaw_cli import run_manual_agent

        text = str(payload.get("message") or payload.get("text") or "").strip()
        if not text:
            raise ApiError("missing_message", "Missing assistant message.", status=400)
        voice_enabled = bool(payload.get("speak", True))
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        foreground_reply = str(context.get("foreground_reply") or "").strip()
        foreground_speech: dict[str, object] = {"status": "skipped", "mode": "server_side_only", "reason": "no_foreground_reply"}
        if voice_enabled and foreground_reply:
            foreground_speech = synthesize_and_play_on_server(self.runtime.config, foreground_reply, self.projection_preview_port)
            foreground_status = normalize_hardware_test_status(str(foreground_speech.get("status") or "unavailable"))
            self.record_audit("assistant_foreground_speak", status_to_audit(foreground_status), "server_speaker", foreground_speech, ctx)
        result = run_manual_agent(self.runtime, text)
        response = self.manual_agent_response_from_result(result, text)
        status = str(response["result"]["status"])
        response_text = str(response["result"]["display_text"])
        task = self.create_task(
            title=text[:80],
            task_type="assistant",
            status=normalize_task_status(status),
            input_payload={"message": text, "context": context},
            output=result,
        )
        response["task_id"] = task["task_id"]
        speech_result = {"status": "skipped", "mode": "server_side_only", "reason": "voice_disabled"}
        if voice_enabled and status not in {"blocked", "waiting_confirmation", "needs_confirmation"}:
            speech_result = synthesize_and_play_on_server(self.runtime.config, response_text, self.projection_preview_port)
            speech_status = normalize_hardware_test_status(str(speech_result.get("status") or "unavailable"))
            self.record_audit("assistant_auto_speak", status_to_audit(speech_status), "server_speaker", speech_result, ctx)
        response["speech"] = {
            "mode": "server_side_only",
            "status": speech_result.get("status"),
            "target": "raspberry_pi_server_speaker",
            "foreground": foreground_speech,
            "result": speech_result,
        }
        self.record_audit(
            "assistant_manual",
            status_to_audit(status),
            str(response["skills_to_call"][0]["name"]) if response.get("skills_to_call") else "OpenClaw",
            {"task_id": task["task_id"], "intent": response["detected_intent"], "speech": response["speech"]},
            ctx,
        )
        return response

    def api_assistant_confirm(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        confirmation_id = require_string(payload, "confirmation_id")
        result = {"status": "backend_missing", "confirmation_id": confirmation_id, "message": "Confirmation registry is not persistent yet; high-risk execution remains blocked by default."}
        self.record_audit("assistant_confirm", "backend_missing", confirmation_id, result, ctx)
        return result

    def api_assistant_speak(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        text = require_string(payload, "text").strip()
        if not text:
            raise ApiError("missing_text", "Missing text to speak.", status=400)
        if len(text) > 1200:
            raise ApiError("text_too_long", "Speech text is limited to 1200 characters.", status=400)
        result = synthesize_and_play_on_server(self.runtime.config, text, self.projection_preview_port)
        status = normalize_hardware_test_status(str(result.get("status") or "unavailable"))
        self.record_audit("assistant_speak", status_to_audit(status), "server_speaker", result, ctx)
        return {"status": status, "mode": "server_side_only", "target": "raspberry_pi_server_speaker", "text_chars": len(text), "result": result}

    def api_assistant_reject(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        confirmation_id = str(payload.get("confirmation_id") or "manual_reject")
        result = {"status": "blocked", "confirmation_id": confirmation_id, "message": "User rejected the requested action."}
        self.record_audit("assistant_reject", "blocked", confirmation_id, result, ctx)
        return result

    def api_mobile_status(self, ctx: RequestContext) -> dict[str, object]:
        status = self.runtime.mobile_bridge.status()
        self.record_audit(
            "mobile_bridge.status",
            status_to_audit(str(status.get("status"))),
            "mobile_bridge",
            {"configured": status.get("configured"), "provider": status.get("provider")},
            ctx,
        )
        return status

    def api_mobile_request(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        request_text = require_string(payload, "request")
        authorized = bool(payload.get("authorized"))
        result = self.runtime.mobile_bridge.request(request_text, authorized=authorized)
        status = str(result.get("status") or "unknown")
        task_status = normalize_task_status(status)
        task = self.create_task("移动端桥接请求", "assistant", task_status, {"authorized": authorized}, result)
        self.record_audit(
            "mobile_bridge.web_request",
            status_to_audit(status),
            "mobile_bridge",
            {"task_id": task["task_id"], "status": status, "authorized": authorized},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_smart_home_status(self, ctx: RequestContext) -> dict[str, object]:
        status = self.runtime.smart_home.status()
        self.record_audit(
            "smart_home.status",
            status_to_audit(str(status.get("status"))),
            "smart_home",
            {
                "provider": status.get("provider"),
                "configured": status.get("configured"),
                "known_entities": len(status.get("known_entities") or []),
            },
            ctx,
        )
        return status

    def api_smart_home_control(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        command = require_string(payload, "command")
        entity_name = str(payload.get("entity_name") or "").strip() or None
        result = self.runtime.smart_home.control(command, entity_name=entity_name)
        status = str(result.get("status") or "unknown")
        task_status = normalize_task_status(status)
        task = self.create_task("智能家居桥接请求", "assistant", task_status, {"entity_name": entity_name or ""}, result)
        self.record_audit(
            "smart_home.web_control",
            status_to_audit(status),
            "smart_home",
            {"task_id": task["task_id"], "status": status, "provider": result.get("provider")},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_desktop_task_execute_browser(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        task_id = require_string(payload, "task_id")
        authorized = bool(payload.get("authorized"))
        actor = str(payload.get("actor") or ctx.actor or "web_console")
        headless = payload.get("headless")
        allowed_hosts = list_string(payload.get("allowed_hosts"))
        task = self.runtime.desktop_tasks.get_task(task_id)
        result = self.runtime.browser_automation.execute_task(
            task,
            actor=actor,
            authorized=authorized,
            headless=bool(headless) if headless is not None else None,
            allowed_hosts=allowed_hosts,
        )
        self.runtime.desktop_tasks.record_execution(
            task_id,
            backend="playwright_browser",
            execution_status=str(result.get("status") or "unknown"),
            actor=actor,
            note=str(result.get("message") or ""),
            step_count=safe_int(result.get("step_count"), 0),
        )
        self.record_audit(
            "desktop_task.execute_browser",
            status_to_audit(str(result.get("status"))),
            task_id,
            {
                "authorized": authorized,
                "status": result.get("status"),
                "report_workspace_name": result.get("report_workspace_name"),
            },
            ctx,
        )
        return result

    def api_desktop_workflow_status(self, ctx: RequestContext | None = None) -> dict[str, object]:
        preflight = self.runtime.desktop.desktop_preflight(require_input_backend=True)
        payload = {
            "status": "available",
            "permission_mode": self.runtime.config.permission_mode.value,
            "desktop_backend": self.runtime.config.desktop_backend,
            "can_execute": bool(preflight.get("can_execute")),
            "preflight": preflight,
            "allowed_roots": [str(path) for path in self.runtime.config.allowed_roots],
            "supported_actions": [
                "open_url",
                "open_app",
                "open_file",
                "find_files",
                "media_control",
                "set_volume",
                "mouse_move",
                "mouse_click",
                "type_text",
                "hotkey",
                "screenshot",
                "low_level_probe",
            ],
            "setup_endpoint": "/api/desktop/workflow/setup",
            "safety": [
                "Plan endpoint never controls the desktop.",
                "Setup endpoint generates target-machine full_control validation steps without controlling the desktop.",
                "Execute endpoint requires explicit authorization.",
                "Execution is blocked unless OPENCLAW_PERMISSION_MODE=full_control.",
                "Low-level mouse, keyboard, and screenshot actions require full_control, a GUI session, and xdotool or XTest where applicable.",
                "File actions remain limited to workspace/shared_inbox/allowed_roots.",
            ],
        }
        if ctx:
            self.record_audit("desktop_workflow.status", "ok", "desktop_workflow", {"can_execute": payload["can_execute"]}, ctx)
        return payload

    def api_desktop_workflow_plan(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        goal = require_string(payload, "goal")
        steps = list_string(payload.get("steps")) or [goal]
        workflow = self.runtime.desktop.build_workflow(goal, steps)
        task = self.create_task("桌面工作流计划", "assistant", "completed", {"goal": goal, "steps": steps}, workflow)
        result = {"status": "completed", "task_id": task["task_id"], **workflow, "safety": self.api_desktop_workflow_status(ctx=None)["safety"]}
        self.record_audit("desktop_workflow.plan", "ok", goal, {"task_id": task["task_id"], "steps": len(steps)}, ctx)
        return result

    def api_desktop_workflow_setup(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        goal = require_string(payload, "goal")
        steps = list_string(payload.get("steps")) or [goal]
        setup = self.runtime.desktop.build_supervised_setup(goal, steps)
        task = self.create_task("桌面全权工作流验收包", "assistant", "completed", {"goal": goal, "steps": steps}, setup)
        result = {"status": "completed", "task_id": task["task_id"], **setup}
        self.record_audit("desktop_workflow.setup", "ok", goal, {"task_id": task["task_id"], "setup_status": setup.get("status")}, ctx)
        return result

    def api_desktop_workflow_execute(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        goal = require_string(payload, "goal")
        steps = list_string(payload.get("steps")) or [goal]
        authorized = bool(payload.get("authorized"))
        actor = str(payload.get("actor") or ctx.actor or "web_console")
        result = self.runtime.desktop.execute_workflow(goal, steps, authorized=authorized, actor=actor)
        status = str(result.get("status") or "unknown")
        task = self.create_task(
            "桌面工作流执行",
            "assistant",
            normalize_task_status(status),
            {"goal": goal, "steps": steps, "authorized": authorized},
            result,
        )
        self.record_audit(
            "desktop_workflow.execute",
            status_to_audit(status),
            goal,
            {"task_id": task["task_id"], "status": status, "authorized": authorized, "steps": len(steps)},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_desktop_control_action(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        action = require_string(payload, "action")
        authorized = bool(payload.get("authorized"))
        if not authorized:
            result = {"status": "needs_confirmation", "message": "Explicit authorization is required before low-level desktop control.", "action": action}
        elif action == "mouse_move":
            result = self.runtime.desktop.mouse_move(safe_int(payload.get("x"), 0), safe_int(payload.get("y"), 0))
        elif action == "mouse_click":
            result = self.runtime.desktop.mouse_click(safe_int(payload.get("button"), 1))
        elif action == "type_text":
            result = self.runtime.desktop.type_text(str(payload.get("text") or ""))
        elif action == "hotkey":
            result = self.runtime.desktop.send_hotkey(str(payload.get("hotkey") or ""))
        elif action == "screenshot":
            result = self.runtime.desktop.capture_screenshot()
        elif action == "low_level_probe":
            result = self.runtime.desktop.low_level_probe()
        else:
            raise ApiError("unsupported_desktop_action", f"Unsupported desktop control action: {action}", status=400)
        status = str(result.get("status") or "unknown")
        task = self.create_task(
            "低层桌面控制动作",
            "desktop",
            normalize_task_status(status),
            {"action": action, "authorized": authorized},
            result,
        )
        self.record_audit(
            "desktop_control.action",
            status_to_audit(status),
            action,
            {"task_id": task["task_id"], "authorized": authorized, "status": status},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_desktop_companion_start(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        interval_seconds = max(1, min(60, safe_int(payload.get("interval_seconds"), 5)))
        with self._desktop_companion_lock:
            if self._desktop_companion_thread is not None and self._desktop_companion_thread.is_alive():
                return {**self.api_desktop_companion_status(ctx=None), "message": "Desktop companion service is already running."}
            self._desktop_companion_stop.clear()
            self._desktop_companion_started_at = time.time()
            thread = threading.Thread(
                target=self._desktop_companion_loop,
                args=(interval_seconds,),
                name="openclaw-desktop-companion",
                daemon=True,
            )
            self._desktop_companion_thread = thread
            thread.start()
        result = {**self.api_desktop_companion_status(ctx=None), "message": "Desktop companion service started.", "interval_seconds": interval_seconds}
        self.record_audit("desktop_companion.start", "ok", "desktop_companion", result, ctx)
        return result

    def api_desktop_companion_stop(self, ctx: RequestContext) -> dict[str, object]:
        with self._desktop_companion_lock:
            thread = self._desktop_companion_thread
            self._desktop_companion_stop.set()
        if thread is not None:
            thread.join(timeout=3.0)
        with self._desktop_companion_lock:
            self._desktop_companion_thread = None
            self._desktop_companion_started_at = None
        result = {**self.api_desktop_companion_status(ctx=None), "message": "Desktop companion service stopped."}
        self.record_audit("desktop_companion.stop", "ok", "desktop_companion", result, ctx)
        return result

    def api_desktop_companion_run_once(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        limit = max(1, min(20, safe_int(payload.get("limit"), 5)))
        result = self.run_desktop_companion_once(limit=limit, actor="web_console_companion")
        self.record_audit("desktop_companion.run_once", status_to_audit(str(result.get("status"))), "desktop_companion", result, ctx)
        return result

    def _desktop_companion_loop(self, interval_seconds: int) -> None:
        while not self._desktop_companion_stop.is_set():
            try:
                self.run_desktop_companion_once(limit=5, actor="desktop_companion_daemon")
            except Exception as exc:
                self._desktop_companion_last_run = {"status": "error", "error": str(exc)[:1000], "timestamp": now_iso()}
                self.audit.record("desktop_companion.daemon", status="error", target="desktop_companion", details=self._desktop_companion_last_run)
            self._desktop_companion_stop.wait(interval_seconds)

    def run_desktop_companion_once(self, *, limit: int, actor: str) -> dict[str, object]:
        companion = DesktopCompanionService(
            workspace=self.runtime.workspace,
            audit=self.runtime.audit,
            backend=self.runtime.config.desktop_backend,
            permission_mode=self.runtime.config.permission_mode,
        )
        approved_listing = companion.list_approved_tasks(limit=limit, scan_limit=max(50, limit * 10))
        approved = approved_listing["tasks"]
        executed: list[dict[str, object]] = []
        for task in approved[:limit]:
            task_id = str(task.get("id") or "")
            if not task_id:
                continue
            result = companion.execute_task(task_id, actor=actor)
            if str(result.get("status")) in {"planned", "attempted"}:
                updated_task = self.runtime.desktop_tasks.update_status(
                    task_id,
                    "done",
                    actor=actor,
                    reason=f"desktop companion {result.get('status')}",
                )
                result = {**result, "final_status": "done", "task": updated_task}
            executed.append(result)
        payload = {
            "status": "completed",
            "processed": len(executed),
            "approved_count": len(approved),
            "scanned_count": approved_listing.get("scanned_count"),
            "backend": self.runtime.config.desktop_backend,
            "executed": executed,
            "timestamp": now_iso(),
        }
        self._desktop_companion_last_run = {
            "status": payload["status"],
            "processed": payload["processed"],
            "approved_count": payload["approved_count"],
            "backend": payload["backend"],
            "timestamp": payload["timestamp"],
        }
        self.audit.record("desktop_companion.run", target="desktop_companion", details=self._desktop_companion_last_run)
        return payload

    def api_full_control_request(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        purpose = str(payload.get("purpose") or "").strip()
        if len(purpose) < 10:
            raise ApiError("invalid_purpose", "Purpose must be at least 10 characters.", status=400)
        result = {"status": "waiting_confirmation", "step": 1, "request_id": uuid4().hex, "message": "full_control request recorded; backend mode change is not automatic."}
        self.record_audit("full_control_request", "ok", "full_control", {"purpose": purpose, "request_id": result["request_id"]}, ctx)
        return result

    def api_full_control_confirm(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        step = safe_int(payload.get("step"), 1)
        result = {
            "status": "backend_missing" if step >= 3 else "waiting_confirmation",
            "step": step,
            "message": "Runtime permission mode cannot be changed by this UI process; set OPENCLAW_PERMISSION_MODE=full_control and restart after admin approval.",
            "full_control_enabled": self.runtime.config.permission_mode.value == "full_control",
        }
        self.record_audit("full_control_confirm", status_to_audit(str(result["status"])), "full_control", result, ctx)
        return result

    def api_full_control_cancel(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        result = {"status": "blocked", "message": "full_control request cancelled."}
        self.record_audit("full_control_cancel", "blocked", "full_control", result, ctx)
        return result

    def api_recent_audit_from_params(self, params: dict[str, list[str]], ctx: RequestContext) -> dict[str, object]:
        limit = safe_int(params.get("limit", [params.get("page_size", ["50"])[0]])[0], 50)
        page = max(1, safe_int(params.get("page", ["1"])[0], 1))
        page_size = max(1, min(200, safe_int(params.get("page_size", [str(limit)])[0], limit)))
        status = params.get("status", [""])[0]
        action = params.get("action", [""])[0]
        query = params.get("q", [""])[0]
        return self.api_recent_audit(limit=max(limit, page * page_size), status=status, action=action, query=query, page=page, page_size=page_size, ctx=ctx)

    def api_recent_audit(
        self,
        *,
        limit: int = 50,
        status: str = "",
        action: str = "",
        query: str = "",
        page: int = 1,
        page_size: int | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        read_limit = max(1, min(limit, 1000))
        if query or action:
            read_limit = max(read_limit, 5000)
        events = read_recent_audit(self.runtime.config.audit_log_path, limit=read_limit)
        if status:
            events = [event for event in events if event.get("status") == status]
        if action:
            events = [event for event in events if action in str(event.get("action", ""))]
        if query:
            lowered = query.lower()
            events = [event for event in events if lowered in json.dumps(event, ensure_ascii=False).lower()]
        total = len(events)
        page_size = page_size or total or 1
        start = (max(1, page) - 1) * page_size
        items = [audit_event_dto(event) for event in events[start : start + page_size]]
        if ctx:
            self.record_audit("audit_recent", "ok", str(self.runtime.config.audit_log_path), {"total": total, "page": page}, ctx)
        return {"items": items, "events": items, "total": total, "page": page, "page_size": page_size, "path": str(self.runtime.config.audit_log_path)}

    def api_audit_export(self, query: str, ctx: RequestContext) -> tuple[str, bytes]:
        payload = self.api_recent_audit_from_params(urllib.parse.parse_qs(query), ctx)
        rows = payload["items"]
        header = ["timestamp", "actor", "action", "status", "target", "details", "request_id"]
        lines = [",".join(header)]
        for row in rows:
            values = [
                str(row.get("timestamp", "")),
                str(row.get("actor", "")),
                str(row.get("action", "")),
                str(row.get("status", "")),
                str(row.get("target", "")),
                json.dumps(row.get("details", {}), ensure_ascii=False),
                str(row.get("request_id", "")),
            ]
            lines.append(",".join(csv_escape(value) for value in values))
        self.record_audit("audit_export", "ok", "audit.csv", {"count": len(rows)}, ctx)
        return ("audit.csv", ("\n".join(lines) + "\n").encode("utf-8-sig"))

    def api_audit_export_signed(self, query: str, ctx: RequestContext) -> Path:
        parsed_query = urllib.parse.parse_qs(query)
        payload = self.api_recent_audit_from_params(parsed_query, ctx)
        rows = payload["items"]
        result = self.runtime.enterprise.export_signed_audit(rows, query={key: values for key, values in parsed_query.items() if key != "token"})
        status = str(result.get("status") or "")
        if status != "completed":
            raise ApiError(status or "needs_config", str(result.get("message") or "Signed audit export unavailable."), status=409, details=result)
        return Path(str(result["path"]))

    def api_verify_signed_audit(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        path_value = require_string(payload, "path")
        path = Path(path_value).expanduser().resolve()
        workspace = self.runtime.config.workspace_dir.resolve()
        if not path.is_file() or not path.is_relative_to(workspace):
            self.record_audit("enterprise.audit_export_verify", "blocked", redact_target(path_value), {"reason": "outside workspace"}, ctx)
            raise ApiError("blocked", "Signed audit export must be inside workspace.", status=403)
        return self.runtime.enterprise.verify_signed_audit_export(path)

    def api_tasks_recent(self, *, limit: int, ctx: RequestContext | None = None) -> dict[str, object]:
        tasks = self.load_tasks(limit=limit)
        if ctx:
            self.record_audit("tasks.recent", "ok", "web_tasks", {"count": len(tasks)}, ctx)
        return {"items": tasks, "tasks": tasks, "total": len(tasks)}

    def api_task_get(self, task_id: str, ctx: RequestContext) -> dict[str, object]:
        with self._task_lock:
            path = self.task_dir() / f"{sanitize_id(task_id)}.json"
            if not path.is_file() or not path.resolve().is_relative_to(self.task_dir()):
                raise ApiError("not_found", "Task not found.", status=404)
            task = json.loads(path.read_text(encoding="utf-8"))
        self.record_audit("tasks.get", "ok", task_id, {}, ctx)
        return task

    def api_task_events(self, task_id: str, ctx: RequestContext) -> dict[str, object]:
        task = self.api_task_get(task_id, ctx)
        output = task.get("output") if isinstance(task.get("output"), dict) else {}
        events = output.get("events") if isinstance(output.get("events"), list) else []
        return {"task_id": task_id, "status": task.get("status"), "events": events, "total": len(events)}

    def api_task_cancel(self, task_id: str, ctx: RequestContext) -> dict[str, object]:
        task = self.api_task_get(task_id, ctx)
        task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
        if task.get("type") == "meeting" and str(task_input.get("step") or "") == "realtime_capture" and task.get("status") in {"starting", "running", "stopping"}:
            self.record_audit("tasks.cancel", "blocked", task_id, {"reason": "realtime_capture_requires_meeting_stop", "meeting_id": task_input.get("meeting_id")}, ctx)
            raise ApiError(
                "realtime_capture_requires_stop",
                "Realtime capture tasks must be stopped through /api/meeting/realtime/stop so provider capture, workspace outputs, and task monitor stay consistent.",
                status=409,
                details={"task_id": task_id, "meeting_id": task_input.get("meeting_id"), "stop_endpoint": "/api/meeting/realtime/stop"},
            )
        if task.get("status") in {"completed", "blocked", "failed"}:
            raise ApiError("conflict", "Task is already finished.", status=409)
        task["status"] = "blocked"
        task["updated_at"] = now_iso()
        task["error"] = {"code": "cancelled", "message": "Cancelled by web user."}
        atomic_write_json(self.task_dir() / f"{sanitize_id(task_id)}.json", task)
        self.record_audit("tasks.cancel", "blocked", task_id, {}, ctx)
        return task

    def shared_file_dto(self, item: dict[str, object]) -> dict[str, object]:
        workspace_name = str(item.get("workspace_name") or item.get("relative_path") or item.get("name") or "")
        size = int(item.get("size_bytes") or item.get("size") or 0)
        return {
            "name": str(item.get("name") or Path(workspace_name).name),
            "relative_path": workspace_name,
            "workspace_name": workspace_name,
            "size": size,
            "size_bytes": size,
            "size_label": format_bytes(size),
            "sha256": str(item.get("sha256") or ""),
            "mime_type": mimetypes.guess_type(str(item.get("name") or workspace_name))[0] or "application/octet-stream",
            "uploaded_at": str(item.get("uploaded_at") or ""),
            "status": "ready",
            "allowed_actions": ["analyze", "summarize", "report_outline", "key_data_table", "search", "generate_minutes", "followup_package"],
        }

    def ensure_allowed_path(self, input_path: str, ctx: RequestContext, *, action: str = "file_read") -> SafePath:
        value = urllib.parse.unquote(str(input_path or "")).strip().replace("\\", "/")
        if not value:
            raise ApiError("missing_file_path", "Missing file_path.", status=400)
        candidates: list[Path] = []
        if Path(value).is_absolute():
            candidates.append(Path(value).expanduser().resolve())
        else:
            candidates.append((self.runtime.config.workspace_dir / value).resolve())
            if not value.startswith("shared_inbox/"):
                candidates.append((self.shared_space.inbox_dir / value).resolve())
        roots = tuple(path.resolve() for path in self.runtime.config.allowed_roots)
        for candidate in candidates:
            if candidate.is_file() and any(candidate.is_relative_to(root) for root in roots):
                return SafePath(candidate, str(candidate.relative_to(self.runtime.config.workspace_dir)))
        target = redact_target(value)
        self.record_audit(action, "blocked", target, {"reason": "outside allowed roots or file missing"}, ctx)
        raise ApiError("blocked", "File access blocked by workspace/shared_inbox/allowed roots policy.", status=403, details={"target": target})

    def workspace_relative_path(self, path_value: str) -> str:
        if not path_value:
            return ""
        path = Path(path_value).expanduser().resolve()
        workspace = self.runtime.config.workspace_dir.resolve()
        if path.is_relative_to(workspace):
            return str(path.relative_to(workspace))
        return ""

    def normalize_meeting_transcript_ref(self, transcript: str) -> str:
        value = str(transcript or "").strip()
        if not value:
            return ""
        path = Path(value).expanduser()
        if path.is_absolute():
            return self.workspace_relative_path(value) or value
        return value.removeprefix("./")

    def task_dir(self) -> Path:
        path = (self.runtime.config.workspace_dir / "web_tasks").resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_task(
        self,
        title: str,
        task_type: str,
        status: str,
        input_payload: dict[str, object],
        output: object,
        error: object | None = None,
    ) -> dict[str, object]:
        task_id = uuid4().hex
        now = now_iso()
        clean_title = redact_sensitive_text(title)[:240]
        payload = {
            "task_id": task_id,
            "title": clean_title,
            "type": task_type,
            "status": normalize_task_status(status),
            "progress": 1.0 if normalize_task_status(status) in {"completed", "blocked", "failed"} else 0.5,
            "created_at": now,
            "updated_at": now,
            "input": sanitize_event_payload(input_payload),
            "output": sanitize_event_payload(output),
            "error": sanitize_event_payload(error),
        }
        with self._task_lock:
            path = self.task_dir() / f"{task_id}.json"
            atomic_write_json(path, payload)
        return payload

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        output: object | None = None,
        error: object | None = None,
    ) -> dict[str, object]:
        with self._task_lock:
            path = self.task_dir() / f"{sanitize_id(task_id)}.json"
            if not path.is_file() or not path.resolve().is_relative_to(self.task_dir()):
                raise ApiError("not_found", "Task not found.", status=404)
            task = json.loads(path.read_text(encoding="utf-8"))
            if status is not None:
                task["status"] = normalize_task_status(status)
            if progress is not None:
                task["progress"] = max(0.0, min(1.0, float(progress)))
            if output is not None:
                existing_output = task.get("output") if isinstance(task.get("output"), dict) else {}
                if isinstance(output, dict) and isinstance(existing_output, dict) and "events" not in output and existing_output.get("events"):
                    output = {**output, "events": existing_output.get("events")}
                task["output"] = sanitize_event_payload(output)
            if error is not None:
                task["error"] = sanitize_event_payload(error)
            task["updated_at"] = now_iso()
            atomic_write_json(path, task)
            return task

    def append_task_event(self, task_id: str, event: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        with self._task_lock:
            path = self.task_dir() / f"{sanitize_id(task_id)}.json"
            if not path.is_file() or not path.resolve().is_relative_to(self.task_dir()):
                raise ApiError("not_found", "Task not found.", status=404)
            task = json.loads(path.read_text(encoding="utf-8"))
            output = task.get("output") if isinstance(task.get("output"), dict) else {}
            events = output.get("events") if isinstance(output.get("events"), list) else []
            clean_payload = sanitize_event_payload(payload or {})
            item = {"event": redact_sensitive_text(event)[:160], "timestamp": now_iso(), **clean_payload}
            events.append(item)
            output["events"] = events[-MAX_TASK_EVENTS:]
            task["output"] = sanitize_event_payload(output)
            task["updated_at"] = now_iso()
            atomic_write_json(path, task)
            return item

    def find_meeting_step_task(self, *, meeting_id: str = "", transcript: str = "", step_name: str = "") -> dict[str, object] | None:
        transcript_ref = self.normalize_meeting_transcript_ref(transcript)
        for task in self.load_tasks(limit=300):
            if task.get("type") != "meeting":
                continue
            task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
            if step_name and str(task_input.get("step") or "") != step_name:
                continue
            same_meeting = meeting_id and str(task_input.get("meeting_id") or "") == meeting_id
            same_transcript = (
                transcript_ref
                and self.normalize_meeting_transcript_ref(str(task_input.get("transcript") or "")) == transcript_ref
            )
            if same_meeting or same_transcript:
                return task
        return None

    def sync_realtime_capture_task(self, session: dict[str, object]) -> None:
        meeting_id = str(session.get("meeting_id") or "")
        if not meeting_id:
            return
        task = self.find_meeting_step_task(meeting_id=meeting_id, step_name="realtime_capture")
        if not task:
            return
        status = normalize_task_status(str(session.get("status") or task.get("status") or "running"))
        if status == "stopped":
            status = "running"
        final_count = safe_int(session.get("final_count"), 0)
        audio_seconds = float(session.get("audio_seconds") or 0.0)
        websocket_audio_frames = safe_int(session.get("websocket_audio_frames"), 0)
        progress = 0.5
        if status in {"completed", "failed", "blocked"}:
            progress = 1.0
        elif audio_seconds > 0 or final_count > 0:
            progress = 0.7
        output = tingwu_realtime_task_summary(session)
        output["monitor"] = {"final_count": final_count, "audio_seconds": audio_seconds, "websocket_audio_frames": websocket_audio_frames, "last_status_poll": now_iso()}
        task_output = task.get("output") if isinstance(task.get("output"), dict) else {}
        if "events" not in output and isinstance(task_output.get("events"), list):
            output["events"] = task_output["events"]
        task_payload = session.get("task_payload") if isinstance(session.get("task_payload"), dict) else {}
        provider_events = task_payload.get("events") if isinstance(task_payload.get("events"), list) else []
        if provider_events:
            existing = output.get("events") if isinstance(output.get("events"), list) else []
            seen = {
                (str(item.get("event") or ""), str(item.get("timestamp") or ""))
                for item in existing
                if isinstance(item, dict)
            }
            merged = list(existing)
            for item in provider_events:
                if not isinstance(item, dict):
                    continue
                marker = (str(item.get("event") or ""), str(item.get("timestamp") or ""))
                if marker in seen:
                    continue
                seen.add(marker)
                merged.append(item)
            output["events"] = merged[-200:]
        self.update_task(str(task.get("task_id") or ""), status=status, progress=progress, output=output)

    def add_realtime_monitor_to_output(self, output: object) -> object:
        if not isinstance(output, dict):
            return output
        if "monitor" in output:
            return output
        if not any(key in output for key in ("websocket_audio_frames", "audio_seconds", "final_count")):
            return output
        return {
            **output,
            "monitor": {
                "final_count": safe_int(output.get("final_count"), 0),
                "audio_seconds": float(output.get("audio_seconds") or 0.0),
                "websocket_audio_frames": safe_int(output.get("websocket_audio_frames"), 0),
                "last_status_poll": now_iso(),
            },
        }

    def append_realtime_task_events(self, meeting_id: str, events: list[dict[str, object]]) -> None:
        if not events:
            return
        task = self.find_meeting_step_task(meeting_id=meeting_id, step_name="realtime_capture")
        if not task:
            return
        task_id = str(task.get("task_id") or "")
        task_output = task.get("output") if isinstance(task.get("output"), dict) else {}
        existing_events = task_output.get("events") if isinstance(task_output.get("events"), list) else []
        seen = {
            (str(item.get("event") or ""), str(item.get("timestamp") or ""))
            for item in existing_events
            if isinstance(item, dict)
        }
        for event in events:
            event_name = str(event.get("event") or event.get("type") or "realtime_event")
            marker = (event_name, str(event.get("timestamp") or ""))
            if marker in seen:
                continue
            seen.add(marker)
            payload = {key: value for key, value in event.items() if key != "event"}
            self.append_task_event(task_id, event_name, payload)

    def upsert_meeting_step_task(
        self,
        title: str,
        transcript: str,
        step_name: str,
        status: str,
        output: object,
        *,
        meeting_id: str = "",
        provider: str = "",
    ) -> dict[str, object]:
        clean_title = redact_sensitive_text(title)[:240] or "Meeting"
        transcript_ref = self.normalize_meeting_transcript_ref(transcript)
        input_payload = {
            "transcript": transcript_ref,
            "step": step_name,
            "meeting_title": clean_title,
            "meeting_id": meeting_id,
            "provider": provider,
        }
        with self._task_lock:
            existing_path: Path | None = None
            for path in sorted(self.task_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
                try:
                    task = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if task.get("type") != "meeting":
                    continue
                task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
                same_step = str(task_input.get("step") or "") == step_name
                same_meeting = meeting_id and str(task_input.get("meeting_id") or "") == meeting_id
                same_transcript = (
                    transcript_ref
                    and self.normalize_meeting_transcript_ref(str(task_input.get("transcript") or "")) == transcript_ref
                )
                if same_step and (same_meeting or same_transcript):
                    existing_path = path
                    break

            if existing_path is None:
                task_id = uuid4().hex
                now = now_iso()
                task_output = self.add_realtime_monitor_to_output(output) if step_name == "realtime_capture" else output
                task = {
                    "task_id": task_id,
                    "title": f"会议工作流：{clean_title}",
                    "type": "meeting",
                    "status": normalize_task_status(status),
                    "progress": 1.0 if normalize_task_status(status) in {"completed", "blocked", "failed"} else 0.5,
                    "created_at": now,
                    "updated_at": now,
                    "input": input_payload,
                    "output": sanitize_event_payload(task_output),
                    "error": None,
                }
                path = self.task_dir() / f"{task_id}.json"
                atomic_write_json(path, task)
                return task

            task = json.loads(existing_path.read_text(encoding="utf-8"))
            task["title"] = f"会议工作流：{clean_title}"
            task["status"] = normalize_task_status(status)
            task["progress"] = 1.0 if normalize_task_status(status) in {"completed", "blocked", "failed"} else 0.5
            task["updated_at"] = now_iso()
            task["input"] = input_payload
            existing_output = task.get("output") if isinstance(task.get("output"), dict) else {}
            if isinstance(output, dict) and isinstance(existing_output, dict) and "events" not in output and existing_output.get("events"):
                output = {**output, "events": existing_output.get("events")}
            if step_name == "realtime_capture":
                output = self.add_realtime_monitor_to_output(output)
            clean_output = sanitize_event_payload(output)
            task["output"] = clean_output
            task["error"] = None if normalize_task_status(status) != "failed" else {"code": "meeting_step_failed", "message": summarize_dict(clean_output)}
            atomic_write_json(existing_path, task)
            return task

    def load_tasks(self, *, limit: int = 20) -> list[dict[str, object]]:
        tasks: list[dict[str, object]] = []
        for path in sorted(self.task_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(task, dict):
                tasks.append(task)
            if len(tasks) >= limit:
                break
        return tasks

    def create_meeting_job(self, title: str, transcript: str, step_name: str, status: str, result: dict[str, object]) -> dict[str, object]:
        task = self.create_task(
            title=f"会议工作流：{title}",
            task_type="meeting",
            status=status,
            input_payload={"transcript": transcript, "step": step_name, "meeting_title": title},
            output=result,
        )
        return self.meeting_job_from_task(task)

    def write_meeting_items_output(
        self,
        title: str,
        transcript: str,
        step_name: str,
        items: list[str],
        minutes_result: dict[str, object],
        *,
        output_dir: str = "",
        meeting_id: str = "",
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        items_key = "decisions" if step_name == "decisions" else "action_items"
        filename = safe_filename(title, default="meeting", suffix=f"_{items_key}.json")
        payload = {
            "title": title,
            "transcript": transcript,
            "step": step_name,
            "items": items,
            items_key: items,
            "source_minutes_path": minutes_result.get("path") or minutes_result.get("tingwu_minutes_path"),
            "generated_at": now_iso(),
            "provider": "tongyi_tingwu",
            "confirmation_required": step_name == "decisions",
        }
        if output_dir:
            output_path = self.write_meeting_output_json(
                output_dir,
                f"{items_key}.json",
                payload,
                action=f"meeting.{step_name}_extract",
                meeting_id=meeting_id,
                ctx=ctx,
            )
        else:
            output_path = self.runtime.workspace.write_json(
                filename,
                payload,
                action=f"meeting.{step_name}_extract",
            )
        status = "waiting_confirmation" if step_name == "decisions" else "completed"
        return {
            "status": status,
            "step": step_name,
            items_key: items,
            "items": items,
            "path": str(output_path),
            "source_minutes_path": minutes_result.get("path") or minutes_result.get("tingwu_minutes_path"),
            "confirmation": {
                "required": step_name == "decisions",
                "summary": "请用户确认后再作为正式会议结论使用。" if step_name == "decisions" else "行动项已生成，可继续创建提醒。",
            },
            "message": "已从实时会议生成可审查步骤输出。",
        }

    def meeting_output_dir(self, output_dir_value: str, *, meeting_id: str = "", ctx: RequestContext | None = None) -> Path | None:
        value = str(output_dir_value or "").strip()
        if not value:
            return None
        output_dir = Path(value).expanduser().resolve()
        workspace = self.runtime.config.workspace_dir.resolve()
        expected_root = (workspace / "meetings" / safe_filename(meeting_id, default="meeting")).resolve() if meeting_id else (workspace / "meetings").resolve()
        if not output_dir.is_relative_to(expected_root):
            if ctx is not None:
                self.record_audit(
                    "meeting_output_write",
                    "blocked",
                    meeting_id or str(output_dir),
                    {"reason": "meeting output directory is outside workspace/meetings/{meeting_id}", "output_dir": str(output_dir)},
                    ctx,
                )
            return None
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def write_meeting_output_text(
        self,
        output_dir_value: str,
        filename: str,
        content: str,
        *,
        action: str,
        meeting_id: str = "",
        ctx: RequestContext | None = None,
    ) -> Path:
        output_dir = self.meeting_output_dir(output_dir_value, meeting_id=meeting_id, ctx=ctx)
        if output_dir is None:
            if str(output_dir_value or "").strip():
                raise ApiError("invalid_meeting_output_dir", "Meeting output directory is outside workspace/meetings/{meeting_id}.", status=403)
            return self.runtime.workspace.write_text(filename, content, action=action)
        path = output_dir / safe_filename(filename, default="artifact")
        atomic_write_text_file(path, content)
        if ctx is not None:
            self.record_audit(action, "ok", str(path), {"meeting_id": meeting_id, "chars": len(content)}, ctx)
        else:
            self.runtime.audit.record(action, target=str(path), details={"meeting_id": meeting_id, "chars": len(content)})
        return path

    def write_meeting_output_json(
        self,
        output_dir_value: str,
        filename: str,
        payload: object,
        *,
        action: str,
        meeting_id: str = "",
        ctx: RequestContext | None = None,
    ) -> Path:
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        return self.write_meeting_output_text(output_dir_value, filename, content, action=action, meeting_id=meeting_id, ctx=ctx)

    def materialize_tingwu_workspace_file(
        self,
        result: dict[str, object],
        *,
        output_dir: str,
        filename: str,
        meeting_id: str,
        ctx: RequestContext,
        source_key: str = "path",
    ) -> dict[str, object]:
        source_value = str(result.get(source_key) or "").strip()
        source = Path(source_value).expanduser().resolve() if source_value else None
        meeting_dir = self.meeting_output_dir(output_dir, meeting_id=meeting_id, ctx=ctx)
        if meeting_dir is None and str(output_dir or "").strip():
            return {**result, "status": "blocked", "error": "Meeting output directory is outside workspace/meetings/{meeting_id}."}
        if source is None or not source.is_file() or meeting_dir is None:
            return result
        target = (meeting_dir / safe_filename(filename, default=source.name or "artifact")).resolve()
        if source == target:
            return result
        try:
            atomic_write_bytes(target, source.read_bytes())
        except OSError:
            return result
        self.record_audit(
            "meeting_output_workspace_copy",
            "ok",
            str(target),
            {"meeting_id": meeting_id, "source": str(source), "key": source_key},
            ctx,
        )
        return {**result, source_key: str(target), f"source_{source_key}": str(source)}

    def materialize_tingwu_followup_outputs(
        self,
        followup: dict[str, object],
        *,
        session: dict[str, object],
        ctx: RequestContext,
    ) -> dict[str, object]:
        meeting_id = str(session.get("meeting_id") or "")
        output_dir = str(session.get("output_dir") or "")
        minutes = followup.get("minutes") if isinstance(followup.get("minutes"), dict) else None
        if minutes is not None:
            minutes = self.materialize_tingwu_workspace_file(
                minutes,
                output_dir=output_dir,
                filename="followup_minutes.md",
                meeting_id=meeting_id,
                ctx=ctx,
            )
            followup["minutes"] = minutes
        transcript = followup.get("transcript") if isinstance(followup.get("transcript"), dict) else None
        if transcript is not None:
            transcript = self.materialize_tingwu_workspace_file(
                transcript,
                output_dir=output_dir,
                filename="followup_transcript.json",
                meeting_id=meeting_id,
                ctx=ctx,
            )
            followup["transcript"] = transcript
        email_path = str(followup.get("email_draft_path") or "")
        if email_path:
            copied = self.materialize_tingwu_workspace_file(
                {"path": email_path},
                output_dir=output_dir,
                filename="followup_email.md",
                meeting_id=meeting_id,
                ctx=ctx,
            )
            followup["email_draft_path"] = copied.get("path") or email_path
            if copied.get("source_path") and str(copied.get("source_path")) != str(copied.get("path")):
                followup["source_email_draft_path"] = copied.get("source_path")
        reminders = followup.get("reminders") if isinstance(followup.get("reminders"), dict) else None
        if reminders is not None:
            reminder_path = self.write_meeting_output_json(
                output_dir,
                "reminders.json",
                reminders,
                action="meeting.reminders_snapshot",
                meeting_id=meeting_id,
                ctx=ctx,
            )
            reminders = {**reminders, "store_path": str(reminder_path), "source_store_path": str(reminders.get("store_path") or "")}
            followup["reminders"] = reminders
        return followup

    def latest_projection_mtime(self) -> float:
        projection_dir = self.runtime.config.projection_dir
        if not projection_dir.exists():
            return 0.0
        latest = 0.0
        for path in projection_dir.glob("*.md"):
            try:
                if path.is_file():
                    latest = max(latest, path.stat().st_mtime)
            except OSError:
                continue
        return latest

    def materialize_tingwu_projection_output(
        self,
        projection: dict[str, object],
        *,
        meeting_id: str,
        projection_dir_before: float,
        ctx: RequestContext,
    ) -> dict[str, object]:
        projection_path_value = str(projection.get("path") or "").strip()
        projection_path = Path(projection_path_value).expanduser().resolve() if projection_path_value else None
        if projection_path is None or not projection_path.is_file():
            return projection

        workspace = self.runtime.config.workspace_dir.resolve()
        meeting_root = (workspace / "meetings" / safe_filename(meeting_id, default="meeting")).resolve()
        if projection_path.is_relative_to(meeting_root):
            return projection

        projection_dir = self.runtime.config.projection_dir.resolve()
        if not projection_path.is_relative_to(projection_dir) and not projection_path.is_relative_to(workspace):
            return projection
        try:
            projection_mtime = projection_path.stat().st_mtime
        except OSError:
            return projection
        if projection_path.is_relative_to(projection_dir) and projection_mtime + 0.001 < projection_dir_before:
            return projection

        if not meeting_root.is_relative_to(workspace):
            return projection
        meeting_root.mkdir(parents=True, exist_ok=True)
        workspace_projection = meeting_root / "projection_confirmation.md"
        try:
            atomic_write_text_file(workspace_projection, projection_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return projection
        self.record_audit(
            "meeting_projection_workspace_copy",
            "ok",
            str(workspace_projection),
            {"meeting_id": meeting_id, "source": str(projection_path)},
            ctx,
        )
        return {
            **projection,
            "path": str(workspace_projection),
            "source_projection_path": str(projection_path),
        }

    def write_tingwu_meeting_manifest(
        self,
        *,
        session: dict[str, object],
        minutes: dict[str, object],
        followup: dict[str, object] | None,
        outputs: list[dict[str, object]],
        job: dict[str, object],
        ctx: RequestContext,
    ) -> str:
        output_dir_value = str(session.get("output_dir") or "").strip()
        if not output_dir_value:
            return ""
        output_dir = Path(output_dir_value).expanduser().resolve()
        workspace = self.runtime.config.workspace_dir.resolve()
        meeting_id = str(session.get("meeting_id") or "")
        expected_root = (workspace / "meetings" / safe_filename(meeting_id, default="meeting")).resolve()
        if not output_dir.is_relative_to(expected_root):
            self.record_audit(
                "meeting_manifest",
                "blocked",
                meeting_id or str(output_dir),
                {"reason": "meeting output directory is outside workspace/meetings/{meeting_id}", "output_dir": str(output_dir)},
                ctx,
            )
            return ""

        def normalized_outputs() -> list[dict[str, object]]:
            seen: set[str] = set()
            items: list[dict[str, object]] = []
            skipped: list[str] = []
            for item in outputs:
                if not isinstance(item, dict):
                    continue
                path_value = str(item.get("path") or "")
                if not path_value or path_value in seen:
                    continue
                seen.add(path_value)
                path = Path(path_value).expanduser()
                path_resolved = path.resolve() if path.is_absolute() else (workspace / path_value).resolve()
                if not path_resolved.is_relative_to(workspace):
                    skipped.append(redact_sensitive_text(path_value)[:240])
                    continue
                redacted_path = redact_sensitive_text(str(path_resolved))
                redacted_workspace_path = redact_sensitive_text(str(path_resolved.relative_to(workspace)))
                items.append(
                    sanitize_event_payload({
                        "path": redacted_path,
                        "workspace_path": redacted_workspace_path,
                        "type": str(item.get("type") or path.suffix.lstrip(".") or "file"),
                        "exists": path_resolved.is_file(),
                        "inside_workspace": True,
                        **({"source": str(item.get("source"))} if item.get("source") else {}),
                        **({"step": str(item.get("step"))} if item.get("step") else {}),
                    })
                )
            if skipped:
                self.record_audit(
                    "meeting_manifest.output_skip",
                    "blocked",
                    meeting_id,
                    {"reason": "output path outside workspace", "count": len(skipped), "paths": skipped[:10]},
                    ctx,
                )
            return items

        manifest = sanitize_event_payload({
            "status": minutes.get("status") or session.get("status"),
            "provider": "tongyi_tingwu",
            "provider_status": minutes.get("provider_status") or session.get("status"),
            "openclaw_status": minutes.get("openclaw_status"),
            "content_status": minutes.get("content_status"),
            "provider_error": redact_sensitive_text(str(session.get("error") or minutes.get("provider_error") or ""))[:1000],
            "openclaw_error": redact_sensitive_text(str(minutes.get("error") or ""))[:1000],
            "meeting_id": meeting_id,
            "title": session.get("title") or minutes.get("title"),
            "provider_task_id": session.get("task_id") or minutes.get("provider_task_id"),
            "created_at": session.get("created_at"),
            "started_at": session.get("started_at"),
            "stopped_at": session.get("stopped_at"),
            "audio": {
                "path": session.get("audio_path"),
                "seconds": session.get("audio_seconds"),
                "bytes": session.get("audio_bytes"),
                "sample_rate": session.get("sample_rate"),
                "format": session.get("audio_format"),
                "rms": session.get("audio_rms"),
                "peak": session.get("audio_peak"),
            },
            "transcript_path": session.get("transcript_path"),
            "tingwu_minutes_path": session.get("minutes_path"),
            "openclaw_minutes_path": minutes.get("path"),
            "tingwu_http_operations": session.get("tingwu_http_operations") if isinstance(session.get("tingwu_http_operations"), list) else [],
            "outputs": normalized_outputs(),
            "job": job,
            "followup_status": followup.get("status") if isinstance(followup, dict) else None,
            "generated_at": now_iso(),
        })
        path = output_dir / "manifest.json"
        atomic_write_json(path, manifest)
        self.record_audit("meeting_manifest", "ok", str(path), {"meeting_id": meeting_id, "outputs": len(manifest["outputs"])}, ctx)
        return str(path)

    def find_aggregated_meeting_job(self, transcript: str = "", *, meeting_id: str = "") -> dict[str, object] | None:
        transcript_ref = self.normalize_meeting_transcript_ref(transcript)
        for job in self.aggregate_meeting_jobs(self.load_tasks(limit=200)):
            if meeting_id and job.get("meeting_id") == meeting_id:
                return job
            if transcript_ref and job.get("transcript") == transcript_ref:
                return job
            steps = job.get("steps") if isinstance(job.get("steps"), list) else []
            if transcript_ref and any(isinstance(step, dict) and step.get("input_file") == transcript_ref for step in steps):
                return job
        return None

    def meeting_job_from_task(self, task: dict[str, object]) -> dict[str, object]:
        output = task.get("output") if isinstance(task.get("output"), dict) else {}
        input_payload = task.get("input") if isinstance(task.get("input"), dict) else {}
        status = str(task.get("status") or "completed")
        step_name = str(input_payload.get("step") or "minutes")
        title = str(input_payload.get("meeting_title") or task.get("title") or "Meeting")
        return {
            "job_id": str(task.get("task_id")),
            "status": status,
            "title": title,
            "meeting_id": str(input_payload.get("meeting_id") or output.get("meeting_id") or ""),
            "transcript": self.normalize_meeting_transcript_ref(str(input_payload.get("transcript") or "")),
            "steps": [
                self.meeting_step_from_task(task, step_name, status, output, input_payload)
            ],
        }

    def meeting_step_from_task(
        self,
        task: dict[str, object],
        step_name: str,
        status: str,
        output: dict[str, object],
        input_payload: dict[str, object],
    ) -> dict[str, object]:
        return {
            "name": step_name,
            "status": status,
            "input_file": str(input_payload.get("transcript") or ""),
            "system_understanding": meeting_step_understanding(step_name, output),
            "ai_result": meeting_step_result(step_name, output),
            "confirmation": output.get("confirmation") if isinstance(output.get("confirmation"), dict) else {"required": status == "waiting_confirmation"},
            "output_path": first_output_path(output),
            "output": compact_meeting_step_output(step_name, output),
            "task_id": str(task.get("task_id") or ""),
            "updated_at": str(task.get("updated_at") or ""),
        }

    def aggregate_meeting_jobs(self, tasks: list[dict[str, object]]) -> list[dict[str, object]]:
        groups: dict[str, dict[str, object]] = {}
        order: list[str] = []
        for task in tasks:
            if task.get("type") != "meeting":
                continue
            output = task.get("output") if isinstance(task.get("output"), dict) else {}
            input_payload = task.get("input") if isinstance(task.get("input"), dict) else {}
            transcript = self.normalize_meeting_transcript_ref(str(input_payload.get("transcript") or ""))
            if not transcript:
                continue
            meeting_id = str(input_payload.get("meeting_id") or output.get("meeting_id") or "")
            minutes_output = output.get("minutes") if isinstance(output.get("minutes"), dict) else {}
            title = str(
                input_payload.get("meeting_title")
                or output.get("title")
                or minutes_output.get("title")
                or task.get("title")
                or Path(transcript).stem
                or "Meeting"
            )
            if not title:
                title = str(task.get("title") or Path(transcript).stem or "Meeting")
            key = f"meeting:{meeting_id}" if meeting_id else f"transcript:{transcript}"
            if key not in groups:
                groups[key] = {
                    "job_id": str(task.get("task_id")),
                    "status": "completed",
                    "title": title,
                    "transcript": transcript,
                    "meeting_id": meeting_id,
                    "steps": {},
                    "updated_at": str(task.get("updated_at") or ""),
                }
                order.append(key)
            group = groups[key]
            if meeting_id and not str(group.get("meeting_id") or ""):
                group["meeting_id"] = meeting_id
            if transcript and (
                not str(group.get("transcript") or "")
                or Path(str(group.get("transcript") or "")).is_absolute()
            ):
                group["transcript"] = transcript
            step_name = str(input_payload.get("step") or "minutes")
            status = str(task.get("status") or "completed")
            step = self.meeting_step_from_task(task, step_name, status, output, input_payload)
            steps = group["steps"] if isinstance(group.get("steps"), dict) else {}
            existing = steps.get(step_name)
            if not isinstance(existing, dict) or str(step.get("updated_at")) >= str(existing.get("updated_at") or ""):
                steps[step_name] = step
            group["steps"] = steps
            group["updated_at"] = max(str(group.get("updated_at") or ""), str(task.get("updated_at") or ""))
            if status in {"failed", "blocked"}:
                group["status"] = status
            elif status == "waiting_confirmation" and group.get("status") == "completed":
                group["status"] = "waiting_confirmation"
            if not str(group.get("title") or "").strip() or str(group.get("title")).startswith("会议工作流："):
                group["title"] = title

        jobs: list[dict[str, object]] = []
        for key in order:
            group = groups[key]
            steps_by_name = group.get("steps") if isinstance(group.get("steps"), dict) else {}
            ordered_steps = []
            for name in ("realtime_capture", "import_transcript", "minutes", "decisions", "action_items", "followup", "reminders", "projection_confirmation"):
                if isinstance(steps_by_name, dict) and isinstance(steps_by_name.get(name), dict):
                    ordered_steps.append(steps_by_name[name])
            jobs.append(
                {
                    "job_id": str(group.get("job_id")),
                    "status": str(group.get("status") or "completed"),
                    "title": str(group.get("title") or "Meeting"),
                    "meeting_id": str(group.get("meeting_id") or ""),
                    "transcript": str(group.get("transcript") or ""),
                    "steps": ordered_steps,
                    "updated_at": str(group.get("updated_at") or ""),
                }
            )
        return jobs

    def projection_cards(self) -> list[dict[str, object]]:
        cards = []
        if not self.runtime.config.projection_dir.exists():
            return cards
        for path in sorted(self.runtime.config.projection_dir.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)[:12]:
            text = path.read_text(encoding="utf-8", errors="replace")
            title = text.splitlines()[0].lstrip("# ").strip() if text.splitlines() else path.stem
            mode = "status"
            for line in text.splitlines():
                if line.lower().startswith("mode:"):
                    mode = line.split(":", 1)[1].strip()
                    break
            cards.append(
                {
                    "id": path.stem,
                    "title": title,
                    "subtitle": first_nonempty_line(text.splitlines()[2:]) or mode,
                    "mode": mode,
                    "accent": "green" if "success" in text.lower() or "ready" in text.lower() else "blue",
                    "created_at": time.strftime("%H:%M:%S", time.localtime(path.stat().st_mtime)),
                    "resolution": "1920 × 1080",
                    "path": str(path),
                    "html": markdown_to_html(text),
                }
            )
        return cards


def desktop_full_control_evidence(report: dict[str, object]) -> dict[str, bool]:
    steps = report.get("steps") if isinstance(report.get("steps"), list) else []
    completed = {
        str(step.get("id"))
        for step in steps
        if isinstance(step, dict) and step.get("status") == "completed"
    }
    return {
        "desktop_preflight": "desktop_preflight" in completed,
        "input_probe": "input_probe" in completed,
        "low_level_control_probe": "low_level_control_probe" in completed,
        "execution_probe": "execution_probe" in completed,
    }


def desktop_full_control_remediation(report: dict[str, object], missing_evidence: list[str]) -> list[str]:
    hints: list[str] = []
    steps = report.get("steps") if isinstance(report.get("steps"), list) else []
    details: dict[str, object] = {}
    for step in steps:
        if isinstance(step, dict) and step.get("id") == "desktop_preflight":
            details = step.get("details") if isinstance(step.get("details"), dict) else {}
            break
    errors = [str(item) for item in details.get("errors", [])] if isinstance(details.get("errors"), list) else []
    if "desktop_preflight" in missing_evidence:
        if "no_gui_session" in errors:
            hints.append("在目标办公电脑的真实桌面会话中运行脚本，确保 DISPLAY 或 WAYLAND_DISPLAY 可见。")
        if "missing_xdg_open" in errors:
            hints.append("安装 xdg-utils，确保 xdg-open 可用。")
        if "missing_xdotool" in errors or "missing_input_backend" in errors:
            hints.append("安装 xdotool，或确保系统 libXtst/XTest 可用。")
        if "missing_screenshot_backend" in errors:
            hints.append("安装 gnome-screenshot、spectacle、grim、ImageMagick import 或 xwd 中任意一个截图后端。")
        if any(error.startswith("runtime_permission_mode_") for error in errors):
            hints.append("停止旧控制台，用 OPENCLAW_PERMISSION_MODE=full_control 重新启动目标机 Web console。")
        if any(error.startswith("runtime_desktop_backend_") for error in errors):
            hints.append("用 OPENCLAW_DESKTOP_BACKEND=local 重新启动目标机 Web console。")
    if "input_probe" in missing_evidence:
        hints.append("确认 xdotool 能在目标桌面会话中执行 `xdotool getmouselocation`，或 XTest 能打开当前 DISPLAY。")
    if "low_level_control_probe" in missing_evidence:
        hints.append("确认低层控制探针能完成输入探针和截图探针。")
    if "execution_probe" in missing_evidence:
        hints.append("在验收页勾选授权，或运行目标机脚本完成监督式工作流执行探针。")
    return list(dict.fromkeys(hints))


def parse_json_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Expected JSON body.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Expected JSON object.")
    return payload


def endpoint_matches(url: object, expected: str) -> bool:
    parsed = urllib.parse.urlsplit(str(url or ""))
    expected_parsed = urllib.parse.urlsplit(expected)
    return (
        parsed.scheme.lower() == expected_parsed.scheme
        and (parsed.hostname or "").lower() == (expected_parsed.hostname or "").lower()
        and (parsed.path.rstrip("/") or "/") == (expected_parsed.path.rstrip("/") or "/")
        and (parsed.port or 443) == (expected_parsed.port or 443)
    )


def is_real_tingwu_microphone(selected: str, probe: dict[str, object]) -> bool:
    selected_normalized = str(selected or "").strip().lower()
    configured_normalized = str(probe.get("configured_device") or "").strip().lower()
    message = str(probe.get("message") or "").lower()
    fake_devices = {"fake-mic", "mock", "mock-mic"}
    return (
        bool(selected_normalized)
        and selected_normalized not in PLACEHOLDER_CAPTURE_DEVICES
        and selected_normalized not in fake_devices
        and configured_normalized not in fake_devices
        and str(probe.get("status") or "") != "mock"
        and "fake microphone" not in message
        and "tingwu_mock=1" not in message
    )


def capture_probe_matches_selected_microphone(selected: str, capture_probe: dict[str, object]) -> bool:
    selected_normalized = str(selected or "").strip().lower()
    capture_selected = str(capture_probe.get("selected_device") or "").strip().lower()
    fake_devices = {"fake-mic", "mock", "mock-mic"}
    return (
        bool(selected_normalized)
        and selected_normalized not in PLACEHOLDER_CAPTURE_DEVICES
        and capture_selected == selected_normalized
        and capture_selected not in fake_devices
    )


def tingwu_live_acceptance_commands() -> dict[str, object]:
    runtime_root = Path(__file__).resolve().parents[2]
    repo_root = runtime_root.parent
    venv_python = runtime_root / ".venv" / "bin" / "python"
    python = str(venv_python if venv_python.is_file() else Path(sys.executable).resolve())
    preflight_command = [
        python,
        str(repo_root / "scripts" / "preflight_tingwu_live.py"),
        "--capture-seconds",
        "3",
    ]
    acceptance_command = [
        python,
        str(repo_root / "scripts" / "verify_tingwu_live_suite.py"),
        "--env-file",
        ".env.tingwu.local",
        "--seconds",
        "12",
        "--preflight-capture-seconds",
        "3",
        "--spoken-phrase",
        "乐灯听悟验收测试",
        "--evidence-dir",
        "/tmp/lelamp-tingwu-evidence",
    ]
    audit_command = [
        python,
        str(repo_root / "scripts" / "audit_tingwu_live_evidence.py"),
        "/tmp/lelamp-tingwu-evidence/summary.json",
        "--check-files",
    ]
    credential_links = [
        {
            "label": "百炼中国站控制台",
            "url": "https://bailian.console.aliyun.com/",
        },
        {
            "label": "API Key 获取说明",
            "url": "https://help.aliyun.com/zh/model-studio/get-api-key/",
        },
        {
            "label": "APP ID 获取说明",
            "url": "https://help.aliyun.com/zh/model-studio/obtain-api-key-app-id-and-workspace-id/",
        },
        {
            "label": "通义听悟实时记录接入",
            "url": "https://help.aliyun.com/zh/tingwu/interface-and-implementation",
        },
    ]
    return {
        "cwd": str(runtime_root),
        "preflight_command": preflight_command,
        "acceptance_command": acceptance_command,
        "audit_command": audit_command,
        "credential_links": credential_links,
        "credentials_env": {
            "ENV_FILE": ".env.tingwu.local",
        },
        "microphone_env": {"OPENCLAW_MIC_DEVICE": "auto"},
    }


def tingwu_provider_preflight_next_actions(
    checks: dict[str, object],
    *,
    credential_diagnostics: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    commands = tingwu_live_acceptance_commands()
    runtime_root = str(commands["cwd"])
    acceptance_command = list(commands["acceptance_command"])  # type: ignore[arg-type]
    audit_command = list(commands["audit_command"])  # type: ignore[arg-type]
    credential_diagnostics = credential_diagnostics or {}
    credential_guidance = tingwu_credential_next_actions(
        str(credential_diagnostics.get("api_key_kind") or ""),
        str(credential_diagnostics.get("app_id_kind") or ""),
    )
    if checks.get("tingwu_api_key_configured") is not True or checks.get("tingwu_app_id_configured") is not True:
        return [
            {
                "id": "configure_tingwu_credentials",
                "status": "required",
                "message": "；".join(credential_guidance) or "复制 .env.tingwu.example 为 .env.tingwu.local，填入新 Key 和百炼 Model Studio 应用 App ID 后运行验收。",
                "credential_diagnostics": credential_diagnostics,
                "env": commands["credentials_env"],
                "links": commands["credential_links"],
                "cwd": runtime_root,
                "command": acceptance_command,
                "audit_command": audit_command,
            }
        ]
    if checks.get("official_tingwu_endpoint") is not True:
        return [
            {
                "id": "restore_official_tingwu_endpoint",
                "status": "required",
                "message": "恢复官方 DashScope HTTP/WS 端点后再验收。",
                "env": {
                    "TINGWU_HTTP_URL": OFFICIAL_TINGWU_HTTP_URL,
                    "TINGWU_WS_URL": OFFICIAL_TINGWU_WS_URL,
                },
                "cwd": runtime_root,
            }
        ]
    if checks.get("real_microphone_device") is not True or checks.get("microphone_available") is not True:
        return [
            {
                "id": "select_real_alsa_microphone",
                "status": "required",
                "message": "选择真实 USB/ALSA 麦克风，避免 default/pulse/mock/fake 设备。",
                "env": commands["microphone_env"],
                "cwd": runtime_root,
            }
        ]
    if checks.get("microphone_capture_device_matches") is not True:
        return [
            {
                "id": "match_selected_capture_device",
                "status": "required",
                "message": "确认预检采集设备和选中的 ALSA 设备一致。",
            }
        ]
    if checks.get("microphone_capture_open") is not True or checks.get("microphone_capture_signal") is not True:
        return [
            {
                "id": "capture_non_silent_pcm",
                "status": "required",
                "message": "靠近麦克风说话，确认 arecord 能打开设备并采到非静音 PCM。",
                "cwd": runtime_root,
                "command": commands["preflight_command"],
            }
        ]
    return [
        {
            "id": "start_live_meeting_acceptance",
            "status": "ready",
            "message": "开始实时会议，口播“乐灯听悟验收测试”，停止后拉取 AI 纪要。",
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        }
    ]


def tingwu_provider_acceptance_checklist(checks: dict[str, object]) -> list[dict[str, object]]:
    commands = tingwu_live_acceptance_commands()
    runtime_root = str(commands["cwd"])
    preflight_command = list(commands["preflight_command"])  # type: ignore[arg-type]
    acceptance_command = list(commands["acceptance_command"])  # type: ignore[arg-type]
    audit_command = list(commands["audit_command"])  # type: ignore[arg-type]
    credentials_env = commands["credentials_env"]
    microphone_env = commands["microphone_env"]
    credentials_ok = (
        checks.get("tingwu_api_key_configured") is True
        and checks.get("tingwu_app_id_configured") is True
        and checks.get("provider_configured") is True
    )
    endpoint_ok = checks.get("official_tingwu_endpoint") is True
    microphone_ok = (
        checks.get("microphone_available") is True
        and checks.get("real_microphone_device") is True
        and checks.get("microphone_capture_device_matches") is True
        and checks.get("microphone_capture_open") is True
        and checks.get("microphone_capture_signal") is True
    )
    preflight_ok = credentials_ok and endpoint_ok and microphone_ok

    def status(done: bool, *, ready_after_preflight: bool = False) -> str:
        if done:
            return "completed"
        if ready_after_preflight and preflight_ok:
            return "ready"
        return "blocked" if not preflight_ok else "pending"

    return [
        {
            "id": "import_transcript",
            "title": "导入 transcript",
            "status": "ready",
            "how_to_test": "从 shared_inbox/workspace/allowed roots 选择会议转写文件并点击导入；确认 import_transcript 步骤完成，越界路径被拒绝并写入审计。",
            "evidence": ["step_import_transcript_completed", "meeting_import_transcript", "allowed_roots_blocked"],
        },
        {
            "id": "credentials",
            "title": "配置通义听悟凭证",
            "status": "completed" if credentials_ok else "blocked",
            "how_to_test": "复制 .env.tingwu.example 为 .env.tingwu.local，填入新 Key 和 App ID；或确认等价环境变量已在运行 Web Console 的 shell 中配置。",
            "evidence": ["tingwu_api_key_configured", "tingwu_app_id_configured", "provider_configured"],
            "env": credentials_env,
            "links": commands["credential_links"],
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
        {
            "id": "local_audio_preflight",
            "title": "本地麦克风预检",
            "status": "completed" if endpoint_ok and microphone_ok else "blocked",
            "how_to_test": "点击本地预检，确认官方 DashScope 端点、真实 ALSA/USB 麦克风、设备一致、采集打开和非静音信号全部通过。",
            "env": microphone_env,
            "cwd": runtime_root,
            "command": preflight_command,
            "evidence": [
                "official_tingwu_endpoint",
                "real_microphone_device",
                "microphone_capture_device_matches",
                "microphone_capture_open",
                "microphone_capture_signal",
            ],
        },
        {
            "id": "live_realtime_create_task",
            "title": "开始实时会议并创建听悟任务",
            "status": status(False, ready_after_preflight=True),
            "how_to_test": "点击开始实时会议，现场口播“乐灯听悟验收测试”，确认通义听悟 CreateTask 和实时 meeting id 出现在诊断信息里。",
            "evidence": ["provider_task_id", "tingwu_http_operations", "meeting_id"],
            "env": credentials_env,
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
        {
            "id": "websocket_pcm_streaming",
            "title": "WebSocket PCM 音频推流",
            "status": status(False, ready_after_preflight=True),
            "how_to_test": "会议运行中查看实时任务监控，确认 websocket_audio_frames、audio_seconds、audio_rms、audio_peak 持续增长。",
            "evidence": ["websocket_audio_frames", "audio_seconds", "audio_rms", "audio_peak"],
            "env": credentials_env,
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
        {
            "id": "realtime_transcript",
            "title": "实时 transcript 回传",
            "status": status(False, ready_after_preflight=True),
            "how_to_test": "查看实时转写区域，确认听到的口播短语出现在 transcript，并保存到 workspace。",
            "evidence": ["transcript_path", "realtime_transcript", "spoken_phrase_detected"],
            "env": credentials_env,
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
        {
            "id": "stop_then_fetch_minutes",
            "title": "停止会议后拉取 AI 纪要",
            "status": status(False, ready_after_preflight=True),
            "how_to_test": "先点击停止实时会议，再点击拉取 AI 纪要；会议未停止时 fetch-minutes 应被阻断。",
            "evidence": ["stop_status_before_fetch", "tingwu_minutes_path", "ai_minutes_task_id"],
            "env": credentials_env,
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
        {
            "id": "openclaw_followup_outputs",
            "title": "OpenClaw 后处理产物",
            "status": status(False, ready_after_preflight=True),
            "how_to_test": "确认 decisions、action items、follow-up email draft、reminders、projection confirmation 都写入 workspace/meetings/{meeting_id}。",
            "evidence": ["openclaw_minutes_path", "followup_output_paths", "manifest_path"],
            "env": credentials_env,
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
        {
            "id": "ui_task_assistant_audit",
            "title": "UI、任务监控、助手通知和审计",
            "status": status(False, ready_after_preflight=True),
            "how_to_test": "刷新页面后确认 Meeting UI 可恢复会议输出；任务监控保留实时指标；AssistantPanel 出现会议通知；Audit 页面可搜索关键动作。",
            "evidence": ["task_monitor", "assistant_notifications", "audit_log"],
            "env": credentials_env,
            "cwd": runtime_root,
            "command": acceptance_command,
            "audit_command": audit_command,
        },
    ]


def require_string(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"Missing required field: {key}")
    return value


def list_string(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return [str(value)]


def shared_file_matches_type(item: dict[str, object], type_filter: str) -> bool:
    mime = str(item.get("mime_type") or "").lower()
    name = str(item.get("relative_path") or item.get("name") or "").lower()
    suffix = Path(name).suffix
    if type_filter == "audio":
        return mime.startswith("audio/") or suffix in {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".flac", ".aac", ".mp4"}
    if type_filter in {"image", "scan"}:
        return mime.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
    if type_filter in {"document", "text"}:
        return mime.startswith("text/") or suffix in DOCUMENT_WORKFLOW_SUFFIXES
    return type_filter in f"{mime} {name}"


def require_file_path(payload: dict[str, Any]) -> str:
    value = str(payload.get("file_path") or payload.get("filename") or payload.get("transcript") or "").strip()
    if not value:
        raise ApiError("missing_file_path", "Missing file_path.", status=400)
    return value


def is_text_workflow_path(path: Path) -> bool:
    return path.suffix.lower() in DOCUMENT_WORKFLOW_SUFFIXES


def document_adapter_status_from_runtime(runtime: OfficeRuntime) -> dict[str, str]:
    llm_status = "available" if runtime.config.openai_api_key else "local_rules"
    local_ocr_available = bool(shutil.which("tesseract") or _module_available("paddleocr"))
    api_ocr_available = bool(runtime.config.openai_api_key or runtime.config.dashscope_api_key)
    vision_ocr_status = "available" if (api_ocr_available or local_ocr_available) else "backend_missing"
    extraction = runtime.documents.extraction_status()
    pdf_status = str(extraction.get("pdf") or "backend_missing")
    docx_status = str(extraction.get("docx") or "backend_missing")
    pptx_status = str(extraction.get("pptx") or "backend_missing")
    xlsx_status = str(extraction.get("xlsx") or "backend_missing")
    analyzer_status = "available" if any(status == "available" for status in (pdf_status, docx_status, pptx_status, xlsx_status)) else "backend_missing"
    return {
        "document_analyzer": analyzer_status,
        "risk_scanner": analyzer_status,
        "report_outline": llm_status,
        "table_extractor": llm_status,
        "meeting_email_draft": llm_status,
        "scan_capture": "available",
        "scan_enhancement": "available",
        "ocr": vision_ocr_status,
        "vision_ocr": "available" if api_ocr_available else "backend_missing",
        "local_ocr": "available" if local_ocr_available else "backend_missing",
        "pdf_text": pdf_status,
        "docx_text": docx_status,
        "pptx_text": pptx_status,
        "xlsx_text": xlsx_status,
    }


def safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp_number(value: float | None, *, default: float, low: float, high: float) -> float:
    if value is None:
        value = default
    return max(low, min(high, float(value)))


def scan_offsets_from_payload(value: Any, yaw_delta: float) -> list[float]:
    offsets: list[float] = []
    if isinstance(value, list):
        for item in value:
            numeric = optional_float(item)
            if numeric is not None:
                offsets.append(clamp_number(numeric, default=0.0, low=-8.0, high=8.0))
            if len(offsets) >= 3:
                break
    if not offsets:
        offsets = [-yaw_delta, 0.0, yaw_delta]
    if 0.0 not in offsets:
        offsets.insert(min(1, len(offsets)), 0.0)
    return offsets[:3]


def scan_view_plan_from_payload(
    views_value: Any,
    offsets_value: Any,
    *,
    yaw_delta: float,
    pitch_delta: float,
    mode: str,
    view_limit: int,
) -> list[dict[str, object]]:
    planned: list[dict[str, object]] = []
    if isinstance(views_value, list):
        for index, item in enumerate(views_value):
            if not isinstance(item, dict):
                continue
            yaw = clamp_number(optional_float(item.get("yaw_offset")), default=0.0, low=-12.0, high=12.0)
            pitch = clamp_number(optional_float(item.get("pitch_offset")), default=0.0, low=-8.0, high=8.0)
            planned.append({"label": str(item.get("label") or f"view_{index + 1}"), "yaw_offset": yaw, "pitch_offset": pitch})
            if len(planned) >= view_limit:
                break
    if not planned and mode == "yaw":
        planned = [
            {"label": "left", "yaw_offset": offset, "pitch_offset": 0.0}
            for offset in scan_offsets_from_payload(offsets_value, yaw_delta)
        ]
    if not planned:
        base_plan = [
            ("center", 0.0, 0.0),
            ("left", -yaw_delta, 0.0),
            ("right", yaw_delta, 0.0),
            ("up", 0.0, pitch_delta),
            ("down", 0.0, -pitch_delta),
            ("left_up", -yaw_delta, pitch_delta),
            ("right_up", yaw_delta, pitch_delta),
            ("left_down", -yaw_delta, -pitch_delta),
            ("right_down", yaw_delta, -pitch_delta),
        ]
        planned = [{"label": label, "yaw_offset": yaw, "pitch_offset": pitch} for label, yaw, pitch in base_plan]
    deduped: list[dict[str, object]] = []
    seen: set[tuple[float, float]] = set()
    for item in planned:
        yaw = round(float(item["yaw_offset"]), 3)
        pitch = round(float(item["pitch_offset"]), 3)
        key = (yaw, pitch)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"label": item["label"], "yaw_offset": yaw, "pitch_offset": pitch})
        if len(deduped) >= view_limit:
            break
    return deduped or [{"label": "center", "yaw_offset": 0.0, "pitch_offset": 0.0}]


def serial_device_candidates() -> list[str]:
    candidates: list[str] = []
    for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*"):
        candidates.extend(str(path) for path in sorted(Path("/dev").glob(Path(pattern).name)))
    return candidates


def compact_scene_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    camera = snapshot.get("camera") if isinstance(snapshot.get("camera"), dict) else {}
    microphone = snapshot.get("microphone") if isinstance(snapshot.get("microphone"), dict) else {}
    reading = snapshot.get("reading") if isinstance(snapshot.get("reading"), dict) else {}
    hardware = snapshot.get("hardware") if isinstance(snapshot.get("hardware"), dict) else {}
    projection = hardware.get("projection") if isinstance(hardware.get("projection"), dict) else {}
    return {
        "task_id": snapshot.get("task_id"),
        "status": snapshot.get("status"),
        "camera": {
            "status": camera.get("status"),
            "camera_index": camera.get("camera_index"),
            "workspace_name": camera.get("workspace_name"),
            "image_path": camera.get("image_path"),
            "source": camera.get("source"),
        },
        "microphone": {
            "status": microphone.get("status"),
            "rms": microphone.get("rms"),
            "peak": microphone.get("peak"),
            "activity_detected": microphone.get("activity_detected"),
        },
        "projection": {
            "status": projection.get("status"),
        },
        "reading": {
            "presence": reading.get("presence"),
            "speech_active": reading.get("speech_active"),
            "lux": reading.get("lux"),
            "projector_blocked": reading.get("projector_blocked"),
        },
        "event_count": snapshot.get("event_count"),
    }


def infer_ambient_lux_from_scene_event(event: dict[str, object]) -> float:
    text = f"{event.get('event_type') or ''} {event.get('description') or ''}"
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:lux|照度)", text, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    event_type = str(event.get("event_type") or "")
    if event_type == "ambient_too_dark":
        return 45.0
    if event_type in {"ambient_too_bright", "projection_too_bright"}:
        return 1100.0
    return 300.0


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sanitize_id(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})[:80]


def format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def redact_target(target: str) -> str:
    value = str(target)
    sensitive = [".ssh", "id_rsa", "passwd", "cookie", "Cookies", "keychain", "token"]
    if any(marker in value for marker in sensitive):
        return Path(value).name if Path(value).name else "[redacted]"
    return value[:200]


def redact_provider_url(url: str) -> str:
    value = str(url or "")
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if not parsed.netloc:
        return value[:120]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def assistant_route_is_chat(route: dict[str, object], text: str) -> bool:
    intent = str(route.get("intent") or "")
    action = str(route.get("action") or "")
    normalized = text.lower()
    task_markers = [
        "天气",
        "气温",
        "下雨",
        "查询",
        "查一下",
        "查下",
        "搜索",
        "文件",
        "文档",
        "pdf",
        "会议",
        "纪要",
        "待办",
        "提醒",
        "投影",
        "硬件",
        "摄像头",
        "麦克风",
        "扬声器",
        "审计",
        "安全",
        "桌面",
        "电脑",
        "删除",
        "发送邮件",
        "支付",
        "提交表单",
        "openclaw",
        "workspace",
        "shared_inbox",
    ]
    if any(marker in normalized for marker in task_markers):
        return False
    if intent == "general_office_chat":
        return True
    if intent == "xiaoai_utility" and action == "answer_utility_query":
        return not any(marker in normalized for marker in ["几点", "日期", "几号", "星期", "周几", "汇率", "股价", "限行", "路况"])
    return False


def assistant_high_risk_policy(text: str) -> dict[str, object]:
    normalized = text.lower()
    blocked_markers = ["删除", "delete", "rm -", "支付", "付款", "购买", "提交表单", "submit form", "格式化", "清空"]
    email_markers = ["发送邮件", "发邮件", "send email"]
    if any(marker in normalized for marker in blocked_markers):
        return {
            "blocked": True,
            "reason": "destructive_or_external_side_effect",
            "message": "该请求涉及删除、支付、购买或提交表单等高风险外部副作用，当前默认安全策略已阻止，未调用后台执行器。",
        }
    if any(marker in normalized for marker in email_markers):
        return {
            "blocked": True,
            "reason": "automatic_email_sending_blocked",
            "message": "自动发送邮件默认被阻止；我只能生成邮件草稿，并需要你手动确认和发送。",
        }
    return {"blocked": False, "reason": "", "message": ""}


def local_chat_reply(text: str) -> str:
    normalized = text.strip().lower()
    if any(marker in normalized for marker in ["你是谁", "介绍一下", "你能做什么"]):
        return "我是 LeLamp 前台助手，负责和你实时沟通；需要处理文件、会议、投影、硬件或审计时，我会交给 OpenClaw 后台在安全边界内执行。"
    if any(marker in normalized for marker in ["累", "压力", "烦", "困", "焦虑"]):
        return "听起来今天工作强度不低。你可以先缓几分钟；需要的话，我也可以帮你把会议、文件或待办整理成更清晰的清单。"
    if any(marker in normalized for marker in ["谢谢", "感谢"]):
        return "不客气。需要处理办公任务时，直接告诉我要查什么、整理什么或投到显示器上。"
    return "我在。这个问题不需要调用后台工具；如果你要处理文件、会议、投影或硬件，我会再交给 OpenClaw 执行并把结果返回给你。"


def assistant_ack_for_route(text: str, route: dict[str, object]) -> str:
    intent = str(route.get("intent") or "")
    action = str(route.get("action") or "")
    if intent in {"weather", "web_search", "local_search"} or any(marker in text for marker in ["查询", "查一下", "查下", "搜索", "天气", "气温"]):
        return "正在为您查询，请稍后。"
    if intent in {"document", "email_draft", "scan"}:
        return "我先确认文件是否在共享空间或白名单目录内，再启动文档处理。"
    if intent == "meeting":
        return "我先按会议闭环拆解处理，后台会生成可审查的结果。"
    if intent == "projection":
        return "我先生成可检查的投影卡片，再同步到显示测试入口。"
    if intent in {"desk_observation", "environment_event"}:
        return "我先检查树莓派侧感知能力，再让后台执行受控观察。"
    if intent == "desktop" or action == "request_desktop_operation":
        return "我先按共享空间和权限边界处理，不会直接越权控制办公电脑。"
    if intent in {"security", "p0_status"}:
        return "我先读取当前安全和服务状态，再返回可审计结果。"
    return "我先理解你的目标，再让后台拆成可审查步骤处理。"


def normalize_task_status(status: str) -> str:
    mapping = {
        "ok": "completed",
        "completed": "completed",
        "ready": "completed",
        "available": "completed",
        "adapter_ready": "blocked",
        "backend_missing": "blocked",
        "unavailable": "blocked",
        "waiting_confirmation": "waiting_confirmation",
        "needs_confirmation": "waiting_confirmation",
        "blocked": "blocked",
        "error": "failed",
        "failed": "failed",
        "starting": "running",
        "running": "running",
        "stopping": "running",
        "stopped": "running",
        "finalizing": "running",
        "queued": "queued",
    }
    return mapping.get(status, "completed")


def normalize_hardware_test_status(status: str) -> str:
    if status in {"captured", "completed", "ok"}:
        return "completed"
    if status in {"needs_backend", "backend_missing"}:
        return "backend_missing"
    if status in {"blocked"}:
        return "blocked"
    if status in {"adapter_ready"}:
        return "adapter_ready"
    if status in {"error", "failed"}:
        return "error"
    return "unavailable"


def dedupe_scene_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        key = (str(event.get("event_type") or ""), str(event.get("description") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def hardware_device_details(scan: dict[str, object], key: str) -> dict[str, object]:
    devices = scan.get("devices")
    if not isinstance(devices, dict):
        return {}
    device = devices.get(key)
    if not isinstance(device, dict):
        return {}
    details = device.get("details")
    return details if isinstance(details, dict) else {}


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def server_tts_status(config: Any) -> str:
    provider = str(getattr(config, "tts_provider", "openai")).lower()
    if provider == "dashscope":
        return "available" if getattr(config, "dashscope_api_key", "") else "backend_missing"
    if provider == "elevenlabs":
        return "available" if getattr(config, "elevenlabs_api_key", "") else "backend_missing"
    if provider == "openai":
        return "available" if getattr(config, "openai_api_key", "") else "backend_missing"
    return "adapter_ready"


def synthesize_and_play_on_server(config: Any, text: str, projection_preview_port: int) -> dict[str, object]:
    provider = str(getattr(config, "tts_provider", "openai")).lower()
    provider_status = server_tts_status(config)
    if provider_status == "backend_missing":
        return {
            "status": "backend_missing",
            "provider": provider,
            "mode": "server_side_only",
            "message": "Server-side TTS is not configured; set the provider API key on the Raspberry Pi/server host.",
        }

    scan = probe_hardware(config, projection_preview_port=projection_preview_port)
    speaker_details = hardware_device_details(scan, "speaker")
    device = str(speaker_details.get("selected_device") or "").strip()
    if not device:
        return {
            "status": "unavailable",
            "provider": provider,
            "mode": "server_side_only",
            "message": "No server-side ALSA speaker device was detected.",
            "configured_device": getattr(config, "speaker_device", ""),
            "candidates": speaker_details.get("candidates", []),
        }

    voice_dir = Path(getattr(config, "workspace_dir")) / ".voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    output = voice_dir / f"assistant_reply_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
    try:
        if provider == "dashscope":
            tts = DashScopeTTS(
                api_key=getattr(config, "dashscope_api_key", ""),
                model=getattr(config, "dashscope_tts_model", ""),
                voice=getattr(config, "dashscope_tts_voice", ""),
                url=getattr(config, "dashscope_tts_url", ""),
            )
            synth = tts.speak_with_stats(text, output)
        elif provider == "elevenlabs":
            tts = ElevenLabsTTS(
                api_key=getattr(config, "elevenlabs_api_key", ""),
                voice_id=getattr(config, "elevenlabs_voice_id", ""),
                model_id=getattr(config, "elevenlabs_model_id", ""),
            )
            tts.speak(text, output)
            synth = {"path": str(output), "bytes": output.stat().st_size}
        else:
            tts = OpenAIAudioAPI(
                api_key=getattr(config, "openai_api_key", ""),
                base_url=getattr(config, "openai_base_url", "https://api.openai.com"),
            )
            tts.speak(
                text,
                model=getattr(config, "tts_model", "tts-1"),
                voice=getattr(config, "tts_voice", "alloy"),
                output_path=output,
            )
            synth = {"path": str(output), "bytes": output.stat().st_size}
    except (AudioAPIError, DashScopeTTSError, ElevenLabsError, OSError, RuntimeError) as exc:
        return {
            "status": "error",
            "provider": provider,
            "mode": "server_side_only",
            "message": str(exc),
            "output_path": str(output),
        }

    playback = play_audio_file(device, output, timeout=120)
    status = str(playback.get("status") or "unavailable")
    return {
        "status": status,
        "provider": provider,
        "mode": "server_side_only",
        "speaker_device": device,
        "configured_device": getattr(config, "speaker_device", ""),
        "configured_device_valid": bool(speaker_details.get("configured_device_valid")),
        "output_path": str(output),
        "text_chars": len(text),
        "synthesis": synth,
        "playback": playback,
        "message": "Assistant reply was synthesized and played on the Raspberry Pi/server-connected speaker.",
    }


def status_to_audit(status: str) -> str:
    if status in {"completed", "available", "ready"}:
        return "ok"
    if status in {"waiting_confirmation", "needs_confirmation"}:
        return "blocked"
    if status in {"adapter_ready", "backend_missing", "unavailable", "blocked", "error"}:
        return status
    return "ok" if status == "ok" else status


def tingwu_capture_status(session: dict[str, object]) -> str:
    status = str(session.get("status") or "")
    if status in {"starting", "running", "stopping"}:
        return status
    realtime_transcript = str(session.get("realtime_transcript") or "").strip()
    transcript_items = session.get("transcript") if isinstance(session.get("transcript"), list) else []
    try:
        audio_seconds = float(session.get("audio_seconds") or 0)
    except (TypeError, ValueError):
        audio_seconds = 0.0
    if status == "failed" and not (realtime_transcript or transcript_items or audio_seconds > 0):
        return "failed"
    transcript_path = Path(str(session.get("transcript_path") or ""))
    if transcript_path.is_file() or realtime_transcript or transcript_items or audio_seconds > 0:
        return "completed"
    return "failed" if status == "failed" else status or "completed"


def tingwu_realtime_task_summary(session: dict[str, object]) -> dict[str, object]:
    keys = (
        "provider",
        "status",
        "meeting_id",
        "title",
        "participants",
        "task_id",
        "data_id",
        "websocket_task_id",
        "created_at",
        "started_at",
        "stopped_at",
        "output_dir",
        "transcript_path",
        "audio_path",
        "minutes_path",
        "audio_bytes",
        "audio_seconds",
        "sample_rate",
        "audio_format",
        "websocket_audio_frames",
        "audio_rms",
        "audio_peak",
        "realtime_transcript",
        "partial_text",
        "final_count",
        "tingwu_http_operations",
        "error",
    )
    summary = {key: session.get(key) for key in keys if key in session}
    transcript = session.get("transcript")
    if isinstance(transcript, list):
        summary["transcript"] = transcript[-40:]
    task_payload = session.get("task_payload") if isinstance(session.get("task_payload"), dict) else {}
    events = task_payload.get("events") if isinstance(task_payload.get("events"), list) else []
    if events:
        summary["events"] = [item for item in events if isinstance(item, dict)][-200:]
    return sanitize_event_payload(summary)


def normalize_result_status(result: Any) -> str:
    if isinstance(result, dict):
        status = str(result.get("status") or "")
        if status:
            return status
        if result.get("permission") and isinstance(result.get("permission"), dict) and not result["permission"].get("allowed", True):
            return "blocked"
    return "completed"


def collect_outputs(result: Any) -> list[dict[str, str]]:
    outputs: list[dict[str, str]] = []
    if not isinstance(result, dict):
        return outputs
    seen: set[str] = set()

    def add_output(path_value: str, *, step: str = "") -> None:
        if not path_value or path_value in seen:
            return
        seen.add(path_value)
        item = {"path": path_value, "type": Path(path_value).suffix.lstrip(".") or "file"}
        if step:
            item["step"] = step
        outputs.append(item)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("path") and isinstance(item, str):
                    add_output(item)
                elif key.endswith("paths") and isinstance(item, dict):
                    for nested_key, nested_item in item.items():
                        if isinstance(nested_item, str):
                            add_output(nested_item, step=str(nested_key))
                        else:
                            walk(nested_item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(result)
    return outputs[:25]


def summarize_manual_result(result: dict[str, object], *, blocked: bool = False) -> str:
    if blocked:
        return "该请求命中高风险默认阻止策略，未执行。"
    route = result.get("route") if isinstance(result.get("route"), dict) else {}
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    payload = result.get("result")
    if isinstance(payload, dict):
        answer = payload.get("answer")
        if answer:
            return str(answer)
        tool_name = str(tool.get("name") or "")
        if tool_name == "get_weather":
            return format_weather_answer(payload)
        if tool_name in {"render_projection_markdown", "render_lamp_countdown"}:
            path = payload.get("path") or payload.get("card_path") or payload.get("countdown_path")
            return f"投影卡片已生成：{path}" if path else "投影卡片已生成，可在 Projection 页面查看。"
        if tool_name in {"analyze_workspace_document", "summarize_workspace_document", "create_report_outline", "extract_key_data_table"}:
            return summarize_dict(payload)
        if tool_name == "search_local_content":
            count = payload.get("count")
            matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
            names = [str(item.get("workspace_name") or item.get("name") or "") for item in matches if isinstance(item, dict)]
            suffix = f"：{', '.join(name for name in names[:3] if name)}" if names else ""
            return f"在允许的 workspace/shared_inbox 中找到 {count or len(matches)} 条结果{suffix}。"
        if tool_name == "plan_office_task":
            steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
            if steps:
                first = steps[0] if isinstance(steps[0], dict) else {}
                action = str(first.get("action") or "澄清目标")
                return f"我已经把请求拆成 {len(steps)} 个可审查步骤。下一步：{action}。"
            return "我需要更明确的目标或文件，才能继续执行办公任务。"
        if payload.get("status") in {"needs_llm", "needs_search", "backend_missing", "unavailable"}:
            return summarize_dict(payload)
        summary = summarize_dict(payload)
        if summary and not summary.startswith("{"):
            return summary
    return f"已调用 {tool.get('name', route.get('skill', 'OpenClaw'))}，但没有可直接展示的结果。详情已记录审计。"


def manual_result_details(result: dict[str, object], *, blocked: bool = False) -> dict[str, object]:
    route = result.get("route") if isinstance(result.get("route"), dict) else {}
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    payload = result.get("result")
    return {
        "blocked": blocked,
        "intent": route.get("intent") or "unknown",
        "route_summary": route.get("summary") or "",
        "tool": tool.get("name") or route.get("skill") or "unknown",
        "tool_args": tool.get("args") if isinstance(tool.get("args"), dict) else {},
        "tool_result": payload if isinstance(payload, dict) else {"value": payload},
        "event_log": result.get("event_log") or "",
    }


def format_weather_answer(payload: dict[str, object]) -> str:
    city = str(payload.get("city") or "当前城市")
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    forecast = payload.get("forecast") if isinstance(payload.get("forecast"), dict) else {}
    temp = current.get("temperature_c") or "-"
    feels = current.get("feels_like_c") or "-"
    desc = str(current.get("description") or forecast.get("midday_description") or "").strip() or "天气信息已获取"
    humidity = current.get("humidity") or "-"
    wind = current.get("wind_kmph") or "-"
    rain = forecast.get("midday_chance_of_rain")
    high = forecast.get("max_temp_c") or "-"
    low = forecast.get("min_temp_c") or "-"
    date = payload.get("target_date") or forecast.get("date") or payload.get("date") or "today"
    local_time = payload.get("local_time")
    rain_text = f"，午间降雨概率 {rain}%" if rain not in {None, ""} else ""
    time_text = f"（当地时间 {local_time}）" if local_time else ""
    return (
        f"{city} {date}{time_text}：{desc}。当前 {temp}°C，体感 {feels}°C，湿度 {humidity}%，"
        f"风速 {wind} km/h；今日 {low}-{high}°C{rain_text}。来源：{payload.get('source', 'weather adapter')}。"
    )


def document_result_payload(task: dict[str, object], result: dict[str, object], status: str) -> dict[str, object]:
    outputs = collect_outputs(result)
    metadata = {key: value for key, value in result.items() if key not in {"points", "summary_path", "analysis_path", "table_path", "outline_path"}}
    llm_status = str(result.get("status") or status)
    table_status = "available" if llm_status == "completed" else llm_status
    return {
        "task_id": task["task_id"],
        "status": status,
        "summary": summarize_dict(result),
        "metadata": metadata,
        "risks": [{"marker": item, "level": "medium"} for item in list_string(result.get("risk_markers"))],
        "outputs": outputs,
        "adapter_status": {
            "document_analyzer": "available",
            "report_outline": "available" if llm_status == "completed" else llm_status,
            "table_extractor": table_status,
            "ocr": "unavailable",
        },
        **result,
    }


def summarize_dict(payload: object) -> str:
    if not isinstance(payload, dict):
        return str(payload)[:240]
    for key in ("summary", "message", "path", "summary_path", "analysis_path", "table_path"):
        if payload.get(key):
            return str(payload[key])[:240]
    return json.dumps(payload, ensure_ascii=False)[:240]


def first_output_path(payload: object) -> str:
    outputs = collect_outputs(payload)
    return outputs[0]["path"] if outputs else ""


def extract_email_subject(draft: str) -> str:
    for line in draft.splitlines()[:30]:
        clean = line.strip().lstrip("-").strip()
        if clean.lower().startswith("subject:"):
            return clean.split(":", 1)[1].strip()
        if clean.startswith("主题："):
            return clean.split("：", 1)[1].strip()
    return ""


def compact_meeting_step_output(step_name: str, output: dict[str, object]) -> dict[str, object]:
    allowed_common = {
        "status",
        "provider",
        "provider_status",
        "openclaw_status",
        "meeting_id",
        "provider_task_id",
        "transcript_path",
        "transcript",
        "audio_path",
        "minutes_path",
        "tingwu_minutes_path",
        "manifest_path",
        "path",
        "output_dir",
        "provider_error",
        "openclaw_error",
        "error",
        "message",
        "content_status",
        "summary",
        "decisions",
        "action_items",
        "items",
        "turn_count",
        "final_count",
        "audio_seconds",
        "sample_rate",
        "audio_format",
        "websocket_audio_frames",
        "audio_rms",
        "audio_peak",
        "tingwu_http_operations",
        "diagnostics",
        "quality_notes",
        "fallback_reason",
        "parse_error",
    }
    if step_name == "followup":
        allowed_common.update({"required_output_paths", "email_draft_path", "followup_status"})
    compact = {key: value for key, value in output.items() if key in allowed_common}
    for key in ("minutes", "followup", "session"):
        value = output.get(key)
        if isinstance(value, dict):
            compact[key] = compact_meeting_step_output(key, value)
    tingwu_minutes = output.get("tingwu_minutes")
    if isinstance(tingwu_minutes, dict):
        compact["tingwu_minutes"] = {
            key: value
            for key, value in tingwu_minutes.items()
            if key in {"summary", "summary_source", "structured_summary", "decisions", "action_items"}
        }
    ai_minutes = output.get("ai_minutes")
    if isinstance(ai_minutes, dict):
        compact["ai_minutes"] = {
            key: value
            for key, value in ai_minutes.items()
            if key in {"summary", "decisions", "action_items", "source_data_id", "minutes_task_id"}
        }
    monitor = output.get("monitor")
    if isinstance(monitor, dict):
        compact["monitor"] = {
            key: value
            for key, value in monitor.items()
            if key in {"final_count", "audio_seconds", "websocket_audio_frames", "last_status_poll"}
        }
    http_operations = output.get("tingwu_http_operations")
    if isinstance(http_operations, list):
        compact["tingwu_http_operations"] = [
            {
                key: item.get(key)
                for key in ("timestamp", "action", "endpoint", "model", "request_task", "request_type", "request_data_id", "response_data_id", "response_status")
                if isinstance(item, dict) and key in item
            }
            for item in http_operations[-12:]
            if isinstance(item, dict)
        ]
    outputs = collect_outputs(output)
    if outputs:
        compact["outputs"] = outputs[:12]
    return sanitize_event_payload(compact)


def meeting_step_understanding(step_name: str, output: dict[str, object]) -> str:
    if step_name == "realtime_capture":
        return f"通义听悟实时采集中，已记录 {output.get('final_count', 0)} 条最终转写，音频约 {output.get('audio_seconds', 0)} 秒"
    if step_name == "import_transcript":
        return f"已解析 {output.get('parsed_count', 0)} 条发言，会议模式：{output.get('meeting_mode_enabled', False)}"
    if step_name == "minutes":
        return f"已汇总 {output.get('turn_count', 0)} 条发言，识别决策 {len(list_string(output.get('decisions')))} 条、行动项 {len(list_string(output.get('action_items')))} 条"
    if step_name == "decisions":
        return f"从会议内容提取 {len(list_string(output.get('decisions') or output.get('items')))} 条决策，等待用户确认"
    if step_name == "action_items":
        return f"从会议内容提取 {len(list_string(output.get('action_items') or output.get('items')))} 条行动项"
    if step_name == "followup":
        return "生成会议纪要、transcript 导出、follow-up 邮件草稿和可选投影确认"
    if step_name == "reminders":
        return f"基于行动项创建 {output.get('count', 0)} 条本地 reminder 草稿"
    if step_name == "projection_confirmation":
        return "生成显示器/投影预览用确认卡，不控制办公电脑"
    return summarize_dict(output)


def meeting_step_result(step_name: str, output: dict[str, object]) -> str:
    if step_name == "realtime_capture":
        return str(output.get("transcript_path") or output.get("realtime_transcript") or summarize_dict(output))
    if step_name == "minutes":
        return str(output.get("path") or summarize_dict(output))
    if step_name == "decisions":
        items = list_string(output.get("decisions") or output.get("items"))
        return "；".join(items[:3]) or "未识别到明确决策"
    if step_name == "action_items":
        items = list_string(output.get("action_items") or output.get("items"))
        return "；".join(items[:3]) or "未识别到行动项"
    if step_name == "followup":
        paths = collect_outputs(output)
        return "、".join(str(item.get("type") or item.get("path")) for item in paths[:4]) or summarize_dict(output)
    if step_name == "reminders":
        return str(output.get("message") or f"本地 reminder 草稿 {output.get('count', 0)} 条")
    if step_name == "projection_confirmation":
        projection = output.get("projection") if isinstance(output.get("projection"), dict) else {}
        return str(projection.get("path") or output.get("path") or summarize_dict(output))
    return summarize_dict(output)


def dedupe_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        marker = (str(event.get("event") or event.get("type") or ""), str(event.get("timestamp") or ""))
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(event)
    return merged


def first_nonempty_line(lines: list[str]) -> str:
    for line in lines:
        clean = line.strip("- #")
        if clean and not clean.lower().startswith("mode:"):
            return clean[:120]
    return ""


def audit_event_dto(event: dict[str, object]) -> dict[str, object]:
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    return {
        "timestamp": str(event.get("timestamp") or ""),
        "actor": str(event.get("actor") or event.get("user") or "openclaw"),
        "action": str(event.get("action") or ""),
        "status": str(event.get("status") or "ok"),
        "target": str(event.get("target") or ""),
        "details": details,
        "request_id": str(event.get("request_id") or event.get("id") or ""),
        "source_ip": str(event.get("source_ip") or ""),
        "permission_mode": str(event.get("permission_mode") or ""),
        "desktop_backend": str(event.get("desktop_backend") or ""),
    }


def csv_escape(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def read_system_sensors(workspace_dir: Path) -> dict[str, object]:
    sensors: dict[str, object] = {
        "cpu_temp": None,
        "cpu_usage": None,
        "memory_usage": None,
        "disk_usage": None,
    }
    temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        if temp_path.exists():
            sensors["cpu_temp"] = round(int(temp_path.read_text().strip()) / 1000, 1)
    except (OSError, ValueError):
        sensors["cpu_temp"] = None
    try:
        stat = os.statvfs(workspace_dir)
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
        sensors["disk_usage"] = round((total - free) / total, 4) if total else None
    except OSError:
        sensors["disk_usage"] = None
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
        values: dict[str, int] = {}
        for line in meminfo.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                values[parts[0].rstrip(":")] = int(parts[1])
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        sensors["memory_usage"] = round((total - available) / total, 4) if total else None
    except (OSError, ValueError):
        sensors["memory_usage"] = None
    return sensors


def read_recent_audit(path: Path, *, limit: int) -> list[dict[str, object]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    events: list[dict[str, object]] = []
    for line in lines[-limit:]:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def render_error_page(title: str, message: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{html.escape(title)}</title></head>
<body><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body>
</html>"""


def render_console_page(token: str) -> str:
    token_json = json.dumps(token)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LeLamp 控制台</title>
  <style>
    :root {{
      font-family: Inter, "PingFang SC", "Microsoft YaHei", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #101828;
      background: #f3f5f7;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; background: #f3f5f7; }}
    .app {{
      display: grid;
      grid-template-columns: 236px minmax(0, 1fr) 360px;
      grid-template-rows: 70px minmax(0, 1fr);
      min-height: 100vh;
    }}
    .topbar {{
      grid-column: 1 / -1;
      display: flex;
      align-items: center;
      gap: 22px;
      padding: 0 24px;
      background: #0b1424;
      color: #ffffff;
      border-bottom: 1px solid rgba(255,255,255,0.08);
    }}
    .brand {{
      min-width: 260px;
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .brand-mark {{
      width: 36px;
      height: 36px;
      border-radius: 8px;
      display: inline-grid;
      place-items: center;
      background: #ffffff;
      color: #0b1424;
      font-weight: 850;
    }}
    .brand-title {{ font-weight: 820; line-height: 1.1; }}
    .brand-subtitle {{ font-size: 12px; color: #b8c1d1; margin-top: 3px; }}
    .top-status {{
      min-width: 0;
      display: grid;
      grid-template-columns: repeat(5, minmax(130px, 1fr));
      gap: 10px;
      flex: 1;
    }}
    .status-item {{
      min-width: 0;
      border-left: 1px solid rgba(255,255,255,0.12);
      padding-left: 14px;
    }}
    .status-label {{ color: #b8c1d1; font-size: 12px; }}
    .status-value {{
      margin-top: 3px;
      color: #ffffff;
      font-size: 14px;
      font-weight: 760;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    nav {{ padding: 22px 16px; background: linear-gradient(180deg, #111827, #172235); color: #f9fafb; }}
    nav h1 {{ margin: 0 0 18px; font-size: 0; line-height: 1.15; letter-spacing: 0; }}
    nav button {{
      width: 100%; display: block; margin: 6px 0; padding: 11px 12px; border: 0; border-radius: 8px;
      text-align: left; background: transparent; color: #d1d5db; font: inherit; cursor: pointer;
    }}
    nav button.active, nav button:hover {{ background: #1f2937; color: #ffffff; }}
    .content {{ min-width: 0; display: grid; grid-template-rows: auto minmax(0, 1fr); }}
    header {{ padding: 18px 26px; background: #ffffff; border-bottom: 1px solid #d0d5dd; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
    .pill {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 7px 10px; background: #f8fafc; color: #344054; font-size: 14px; }}
    .pill.good {{ background: #ecfdf3; color: #027a48; border-color: #abefc6; }}
    .pill.warn {{ background: #fff7ed; color: #c2410c; border-color: #fed7aa; }}
    main {{ padding: 24px; overflow: auto; }}
    section.view {{ display: none; }}
    section.view.active {{ display: grid; gap: 18px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
	    .card, .panel {{ min-width: 0; border: 1px solid #d0d5dd; border-radius: 8px; background: #ffffff; padding: 18px; }}
    .card h3, .panel h2, .panel h3 {{ margin: 0 0 12px; letter-spacing: 0; }}
    .card h3 {{ font-size: 15px; color: #475467; }}
    .card strong {{ font-size: 22px; word-break: break-word; }}
    label {{ display: grid; gap: 7px; font-weight: 650; color: #344054; }}
    input, textarea, select {{
      width: 100%; border: 1px solid #cbd5e1; border-radius: 8px; padding: 10px 11px; font: inherit; background: #ffffff;
    }}
    textarea {{ min-height: 110px; resize: vertical; }}
    .drop-zone {{
      min-height: 180px;
      display: grid;
      place-items: center;
      gap: 10px;
      border: 2px dashed #98a2b3;
      border-radius: 8px;
      background: #f8fafc;
      color: #344054;
      text-align: center;
      padding: 24px;
      transition: border-color 120ms ease, background 120ms ease;
    }}
    .drop-zone strong {{ display: block; font-size: 20px; color: #101828; }}
    .drop-zone span {{ display: block; margin-top: 6px; color: #667085; font-size: 14px; }}
    .drop-zone.dragging {{
      border-color: #0f766e;
      background: #ecfdf3;
    }}
    .drop-zone input {{ display: none; }}
    button.primary, button.secondary {{
      border: 1px solid #0f766e; border-radius: 8px; padding: 10px 13px; font: inherit; font-weight: 750; cursor: pointer;
    }}
    button.primary {{ background: #0f766e; color: #ffffff; }}
    button.secondary {{ background: #ffffff; color: #0f766e; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid #eaecf0; padding: 10px 8px; text-align: left; vertical-align: top; font-size: 14px; }}
    th {{ color: #475467; }}
    code {{ background: #eef2f6; border-radius: 6px; padding: 2px 6px; }}
	    pre {{
	      max-width: 100%;
	      max-height: 360px;
	      overflow: auto;
	      background: #111827;
	      color: #e5e7eb;
	      border-radius: 8px;
	      padding: 14px;
	      font-size: 13px;
	      white-space: pre-wrap;
	      overflow-wrap: anywhere;
	    }}
    .assistant {{ border-left: 1px solid #d0d5dd; background: #ffffff; padding: 18px; display: grid; grid-template-rows: auto 1fr auto; gap: 14px; }}
    .assistant h2 {{ margin: 0 0 8px; font-size: 18px; }}
    .assistant p {{ margin: 0; color: #667085; font-size: 13px; line-height: 1.5; }}
    .assistant-log {{ overflow: auto; display: grid; align-content: start; gap: 10px; }}
	    .message {{ min-width: 0; border-radius: 8px; padding: 10px 12px; background: #f2f4f7; font-size: 14px; line-height: 1.45; }}
    .message.user {{ background: #ecfdf3; }}
    .message.system {{ background: #eef2ff; }}
    .message small {{ display: block; margin-top: 4px; color: #667085; }}
    .file-actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .link-button {{
      border: 1px solid #d0d5dd;
      border-radius: 8px;
      background: #ffffff;
      color: #0f172a;
      padding: 7px 9px;
      font: inherit;
      font-size: 13px;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      min-height: 34px;
    }}
    .preview-box {{
      margin-top: 14px;
      border: 1px solid #d0d5dd;
      border-radius: 8px;
      background: #f8fafc;
      padding: 14px;
    }}
    .preview-box h3 {{ margin: 0 0 10px; }}
    .preview-box pre {{ max-height: 420px; background: #ffffff; color: #101828; border: 1px solid #eaecf0; }}
	    .status-ok {{ color: #027a48; font-weight: 750; }}
	    .status-blocked, .status-error {{ color: #b42318; font-weight: 750; }}
	    .status-unavailable, .status-adapter_ready, .status-backend_missing, .status-needs_config, .status-needs_backend, .status-needs_mobile_bridge, .status-needs_confirmation, .status-partial {{ color: #c2410c; font-weight: 750; }}
		    .test-grid {{
		      display: grid;
		      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
		      gap: 12px;
		      align-items: start;
		    }}
		    .test-card {{
		      min-width: 0;
	      border: 1px solid #d0d5dd;
	      border-radius: 8px;
	      background: #ffffff;
	      padding: 14px;
	      display: grid;
	      gap: 10px;
	      align-content: start;
	    }}
	    .test-card h3 {{ margin: 0; font-size: 15px; }}
	    .test-card p {{ margin: 0; color: #667085; font-size: 13px; line-height: 1.45; }}
	    .test-button {{
	      border: 1px solid #98a2b3;
	      border-radius: 8px;
	      background: #f8fafc;
	      color: #101828;
	      padding: 9px 10px;
	      font: inherit;
	      font-weight: 720;
	      cursor: pointer;
	      text-align: left;
	    }}
	    .test-button:hover {{ border-color: #0f766e; background: #ecfdf3; }}
	    .test-button.manual {{ border-color: #fed7aa; background: #fff7ed; }}
		    .test-result {{
		      min-width: 0;
		      overflow: hidden;
	      border: 1px solid #eaecf0;
	      border-radius: 8px;
	      background: #f8fafc;
	      padding: 10px;
	      min-height: 58px;
	    }}
	    .test-result pre {{ max-height: 180px; margin: 8px 0 0; font-size: 12px; line-height: 1.45; }}
	    .test-summary {{
	      display: grid;
	      gap: 7px;
	      color: #344054;
	      font-size: 13px;
	      line-height: 1.45;
	    }}
	    .test-summary-row {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
	    .test-summary small {{ color: #667085; overflow-wrap: anywhere; }}
	    .test-details {{ margin-top: 8px; }}
	    .test-details summary {{
	      cursor: pointer;
	      color: #0f766e;
	      font-weight: 720;
	      list-style-position: inside;
	    }}
	    .test-toolbar {{ display: flex; gap: 10px; flex-wrap: wrap; }}
	    @media (max-width: 1180px) {{
	      .app {{ grid-template-columns: 190px minmax(0, 1fr); }}
	      .top-status {{ grid-template-columns: repeat(2, minmax(150px, 1fr)); }}
	      .assistant {{ grid-column: 1 / -1; border-left: 0; border-top: 1px solid #d0d5dd; }}
	      .cards, .test-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
	    }}
	    @media (max-width: 760px) {{
	      .app {{ grid-template-columns: 1fr; grid-template-rows: auto auto auto auto; }}
	      .topbar {{ display: grid; gap: 12px; padding: 14px; }}
	      .brand {{ min-width: 0; }}
	      .top-status {{ grid-template-columns: 1fr; }}
	      nav {{ position: static; }}
	      .grid, .cards, .test-grid {{ grid-template-columns: 1fr; }}
	    }}
  </style>
</head>
<body>
  <div class="app">
    <div class="topbar">
      <div class="brand">
        <span class="brand-mark">L</span>
        <div><div class="brand-title">LeLamp 控制台 Web UI</div><div class="brand-subtitle">基于 LeLamp / OpenClaw</div></div>
      </div>
      <div class="top-status" id="topStatus"></div>
    </div>
    <nav>
	      <h1>LeLamp 控制台</h1>
	      <button class="active" data-view="dashboard">Dashboard</button>
	      <button data-view="tests">Test Center</button>
	      <button data-view="shared">Shared Space</button>
      <button data-view="meeting">Meeting</button>
      <button data-view="documents">Documents</button>
      <button data-view="desktop">Desktop Tasks</button>
      <button data-view="projection">Projection</button>
      <button data-view="hardware">Hardware</button>
      <button data-view="audit">Audit</button>
      <button data-view="readiness">Readiness</button>
      <button data-view="settings">Settings</button>
    </nav>
    <div class="content">
      <header id="topbar"></header>
      <main>
	        <section id="dashboard" class="view active">
	          <div class="cards" id="dashboardCards"></div>
	          <div class="grid">
	            <div class="panel"><h2>最近共享文件</h2><div id="recentFiles"></div></div>
	            <div class="panel"><h2>最近审计</h2><div id="recentAudit"></div></div>
	          </div>
	        </section>
	        <section id="tests" class="view">
	          <div class="panel">
	            <h2>测试中心</h2>
	            <div class="test-toolbar">
	              <button class="primary" id="runSafeTests">一键运行安全测试</button>
	              <button class="secondary" id="clearTestResults">清空结果</button>
	            </div>
	          </div>
	          <div id="testGroups"></div>
	        </section>
	        <section id="shared" class="view">
          <div class="grid">
            <div class="panel">
              <h2>输入空间</h2>
              <div class="drop-zone" id="dropZone">
                <label>
                  <strong>把办公电脑文件拖到这里</strong>
                  <span>文件会进入树莓派的 <code>workspace/shared_inbox</code>，OpenClaw 只处理这个受控目录。</span>
                  <input type="file" id="dropInput" multiple>
                </label>
              </div>
              <p>也可以使用下面的文件选择器上传。</p>
              <form id="uploadForm">
                <label>文件 <input type="file" name="file" multiple></label>
                <button class="primary" type="submit">上传</button>
              </form>
            </div>
            <div class="panel">
              <h2>快速笔记</h2>
              <label>标题 <input id="noteTitle" value="办公电脑笔记"></label>
              <label>内容 <textarea id="noteText" placeholder="从办公电脑粘贴文本"></textarea></label>
              <button class="primary" id="saveNote">保存到 shared_inbox</button>
            </div>
          </div>
          <div class="panel"><h2>共享文件</h2><div id="sharedFiles"></div><div id="sharedPreview"></div></div>
        </section>
        <section id="meeting" class="view">
          <div class="panel">
            <h2>会议闭环</h2>
            <div class="grid">
              <label>Transcript 文件 <select id="meetingTranscript"></select></label>
              <label>会议标题 <input id="meetingTitle" value="Web控制台会议"></label>
              <label>参会人 <input id="meetingParticipants" value="Alice,Bob"></label>
              <label>收件人 <input id="meetingRecipient" value="待填写收件人"></label>
            </div>
            <div class="file-actions">
              <button class="primary" id="makeMinutes">生成纪要</button>
              <button class="secondary" id="makeFollowup">生成 Follow-up</button>
            </div>
          </div>
          <pre id="meetingOutput"></pre>
        </section>
        <section id="documents" class="view">
          <div class="panel">
            <h2>文档工作流</h2>
            <div class="grid">
              <label>Workspace 文件 <select id="documentFile"></select></label>
              <label>摘要风格 <select id="summaryStyle"><option>brief</option><option>outline</option><option>detailed</option></select></label>
              <label>扫描类型 <select id="scanType"><option value="document">document</option><option value="contract">contract</option><option value="business_card">business_card</option><option value="receipt">receipt</option><option value="whiteboard">whiteboard</option></select></label>
              <label>OCR 语言 <select id="scanLanguage"><option value="ch">ch</option><option value="en">en</option><option value="chi_sim+eng">chi_sim+eng</option></select></label>
            </div>
            <div class="file-actions">
              <button class="primary" id="analyzeDoc">分析</button>
              <button class="secondary" id="summarizeDoc">摘要</button>
              <button class="secondary" id="registerScan">登记扫描</button>
              <button class="secondary" id="runScanOcr">OCR</button>
              <button class="secondary" id="summarizeOcr">OCR 摘要</button>
              <button class="secondary" id="parseBusinessCard">名片解析</button>
            </div>
          </div>
          <pre id="documentOutput"></pre>
        </section>
        <section id="projection" class="view">
          <div class="grid">
            <div class="panel">
              <h2>生成显示器预览卡</h2>
              <label>标题 <input id="projectionTitle" value="Web控制台状态卡"></label>
              <label>类型 <select id="projectionMode"><option value="status">status</option><option value="countdown">countdown</option><option value="action_card">action_card</option></select></label>
              <label>正文/状态 <textarea id="projectionBody">ready</textarea></label>
              <button class="primary" id="createProjection">生成卡片</button>
            </div>
            <div class="panel"><h2>最新卡片</h2><div id="projectionLatest"></div></div>
          </div>
        </section>
        <section id="desktop" class="view">
          <div class="grid">
            <div class="panel">
              <h2>桌面任务请求</h2>
              <label>目标 <input id="desktopTaskGoal" value="在办公电脑上打开共享文件并人工确认"></label>
              <label>步骤 <textarea id="desktopTaskSteps">查看 shared_inbox 中的相关文件
确认是否需要 full_control
由用户在办公电脑上手动执行或批准后再接入后端</textarea></label>
              <button class="primary" id="requestDesktopTask">提交任务请求</button>
            </div>
            <div class="panel"><h2>任务队列</h2><div id="desktopTasks"></div></div>
          </div>
        </section>
        <section id="hardware" class="view">
          <div class="panel">
            <h2>LeLamp 状态</h2>
            <div class="file-actions">
              <button class="secondary stateBtn" data-state="idle">idle</button>
              <button class="secondary stateBtn" data-state="listening">listening</button>
              <button class="secondary stateBtn" data-state="thinking">thinking</button>
              <button class="secondary stateBtn" data-state="success">success</button>
              <button class="secondary stateBtn" data-state="blocked">blocked</button>
              <button class="secondary stateBtn" data-state="error">error</button>
            </div>
          </div>
          <pre id="hardwareOutput"></pre>
        </section>
        <section id="audit" class="view">
          <div class="panel"><h2>审计日志</h2><div id="auditTable"></div></div>
        </section>
        <section id="readiness" class="view">
          <div class="panel"><h2>MVP 验收状态</h2><div id="readinessReport"></div></div>
        </section>
        <section id="settings" class="view">
          <div class="panel"><h2>安全设置</h2><pre id="settingsJson"></pre></div>
        </section>
      </main>
    </div>
    <aside class="assistant">
      <div>
        <h2>小爱同学前台</h2>
        <p>自然语言入口会展示意图、Skill、确认状态和结果；后台由 OpenClaw 执行。</p>
      </div>
      <div class="assistant-log" id="assistantLog"></div>
      <div>
        <textarea id="assistantInput" placeholder="例如：总结刚上传的会议记录"></textarea>
        <button class="primary" id="assistantSend">发送</button>
      </div>
    </aside>
  </div>
  <script>
	    const TOKEN = {token_json};
	    const headers = {{ 'X-OpenClaw-Console-Token': TOKEN }};
	    const jsonHeaders = {{ ...headers, 'Content-Type': 'application/json' }};
	    const state = {{ security: null, files: [], testResults: {{}} }};
	    const SAFE_TEST_IDS = [
	      'security', 'skills', 'readiness', 'p0_status', 'shared_note', 'workspace_blocked_read',
	      'document_analysis', 'meeting_followup', 'projection_status', 'projection_countdown',
	      'projection_action', 'projection_calibration', 'scan_register', 'ocr_text_summary',
	      'desktop_audit_only', 'desktop_full_control_gate', 'desktop_task_queue', 'desktop_companion',
	      'lelamp_state', 'environment_event', 'hardware_status', 'smart_home_status',
	      'smart_home_control_guard', 'xiaoai_utility', 'xiaoai_features', 'intent_router',
	      'local_file_search', 'daily_reminder', 'mobile_bridge', 'voice_stack_status', 'audit_recent'
	    ];
	    const TEST_GROUPS = [
	      {{ title: '安全与 OpenClaw', tests: [
	        ['security', '安全状态'], ['skills', 'Skill 列表'], ['readiness', 'MVP 验收'], ['p0_status', 'P0 状态'], ['workspace_blocked_read', '未授权路径阻断'], ['audit_recent', '审计日志']
	      ] }},
	      {{ title: '共享空间与文档', tests: [
	        ['shared_note', '共享笔记'], ['document_analysis', '文档分析'], ['local_file_search', '本地搜索'], ['scan_register', '扫描登记'], ['ocr_text_summary', 'OCR 文本摘要'], ['scan_ocr', '图片 OCR', true]
	      ] }},
	      {{ title: '会议与投影', tests: [
	        ['meeting_followup', '会议 Follow-up'], ['projection_status', '状态卡'], ['projection_countdown', '倒计时卡'], ['projection_action', '行动卡'], ['projection_calibration', '校准计划']
	      ] }},
	      {{ title: '桌面协同', tests: [
	        ['desktop_audit_only', 'audit_only 计划'], ['desktop_full_control_gate', 'full_control 门禁'], ['desktop_task_queue', '桌面任务队列'], ['desktop_companion', '办公电脑 Companion']
	      ] }},
	      {{ title: 'LeLamp 与环境', tests: [
	        ['lelamp_state', '状态灯反馈'], ['environment_event', '环境事件'], ['hardware_status', '硬件状态'], ['camera_observe', '摄像头观察', true], ['screen_capture', '屏幕截图', true], ['screen_summary', '屏幕理解', true]
	      ] }},
	      {{ title: '前台助手与扩展', tests: [
	        ['xiaoai_utility', '小爱计算'], ['xiaoai_features', '小爱能力'], ['intent_router', '意图路由'], ['smart_home_status', '智能家居状态'], ['smart_home_control_guard', '智能家居门禁'], ['mobile_bridge', '手机桥接'], ['voice_stack_status', '语音栈状态']
	      ] }}
	    ];

    async function api(path, options = {{}}) {{
      const url = path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN);
      const response = await fetch(url, options);
      const text = await response.text();
      let payload;
      try {{ payload = JSON.parse(text); }} catch {{ payload = {{ status: 'error', body: text }}; }}
      if (!response.ok) throw payload;
      return payload;
    }}

    function escapeHtml(value) {{
      return String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
    }}
    function renderJson(value) {{ return escapeHtml(JSON.stringify(value, null, 2)); }}
    function setOutput(id, value) {{ document.getElementById(id).innerHTML = renderJson(value); }}
    function statusClass(value) {{ return 'status-' + String(value || 'ok').replace(/[^a-zA-Z0-9_-]/g, '_'); }}

    async function refreshAll() {{
      const [security, files, audit, p0, projection, desktopTasks, readiness] = await Promise.all([
        api('/api/security'),
        api('/api/shared/files'),
        api('/api/audit/recent?limit=25'),
        api('/api/p0'),
        api('/api/projection/latest'),
        api('/api/desktop/tasks?limit=25'),
        api('/api/readiness')
      ]);
      state.security = security;
      state.files = files.files || [];
      renderTopbar(security);
      renderDashboard(security, files, audit, p0);
      renderFiles();
      renderFileSelectors();
      renderAudit(audit.events || []);
      document.getElementById('settingsJson').innerHTML = renderJson(security);
      renderProjection(projection);
      renderDesktopTasks(desktopTasks);
      renderReadiness(readiness);
    }}

    function renderTopbar(security) {{
      const mode = escapeHtml(security.permission_mode);
      const desktop = escapeHtml(security.desktop_backend);
      const hardware = security.hardware_enabled ? 'hardware on' : 'hardware off';
      const shared = security.shared_inbox_dir || 'shared_inbox';
      const shortShared = String(shared).replace(String(security.workspace_dir || ''), '/workspace');
      document.getElementById('topStatus').innerHTML = `
        <div class="status-item"><div class="status-label">Pi 在线</div><div class="status-value">Raspberry Pi</div></div>
        <div class="status-item"><div class="status-label">权限模式</div><div class="status-value">SANDBOX ✓</div></div>
        <div class="status-item"><div class="status-label">桌面后端</div><div class="status-value">AUDIT_ONLY ✓</div></div>
        <div class="status-item"><div class="status-label">共享文件箱路径</div><div class="status-value">${{escapeHtml(shortShared)}}</div></div>
        <div class="status-item"><div class="status-label">硬件状态</div><div class="status-value">${{escapeHtml(hardware)}}</div></div>`;
      document.getElementById('topbar').innerHTML = `
        <span class="pill good">Pi online</span>
        <span class="pill ${{mode === 'sandbox' ? 'good' : 'warn'}}">${{mode}}</span>
        <span class="pill ${{desktop === 'audit_only' ? 'good' : 'warn'}}">${{desktop}}</span>
        <span class="pill">${{escapeHtml(hardware)}}</span>
        <span class="pill">shared_inbox</span>`;
    }}

    function renderDashboard(security, files, audit, p0) {{
      const cards = [
        ['permission', security.permission_mode],
        ['desktop', security.desktop_backend],
        ['shared files', files.files.length],
        ['P0 capabilities', (p0.p0 || []).length],
      ];
      document.getElementById('dashboardCards').innerHTML = cards.map(([label, value]) => `
        <div class="card"><h3>${{escapeHtml(label)}}</h3><strong>${{escapeHtml(value)}}</strong></div>`).join('');
      document.getElementById('recentFiles').innerHTML = fileTable((files.files || []).slice(0, 5));
      document.getElementById('recentAudit').innerHTML = auditList((audit.events || []).slice(-8).reverse());
    }}

    function fileTable(files) {{
      if (!files.length) return '<p>暂无共享文件</p>';
      return `<table><thead><tr><th>文件</th><th>路径</th><th>大小</th><th>操作</th></tr></thead><tbody>${{files.map(file => {{
        const encoded = encodeURIComponent(file.workspace_name);
        const download = `/api/shared/download?token=${{encodeURIComponent(TOKEN)}}&file=${{encoded}}`;
        return `<tr>
          <td>${{escapeHtml(file.name)}}</td>
          <td><code>${{escapeHtml(file.workspace_name)}}</code></td>
          <td>${{file.size_bytes}}</td>
          <td class="file-actions">
            <button class="link-button previewBtn" data-file="${{escapeHtml(file.workspace_name)}}">查看</button>
            <a class="link-button" href="${{download}}">下载</a>
          </td>
        </tr>`;
      }}).join('')}}</tbody></table>`;
    }}
    function renderFiles() {{
      document.getElementById('sharedFiles').innerHTML = fileTable(state.files) + '<p>选择文件后可在 Documents 或 Meeting 页面执行任务。</p>';
      document.querySelectorAll('.previewBtn').forEach(button => {{
        button.addEventListener('click', () => previewSharedFile(button.dataset.file));
      }});
    }}
    function renderFileSelectors() {{
      const options = state.files.map(file => `<option value="${{escapeHtml(file.workspace_name)}}">${{escapeHtml(file.workspace_name)}}</option>`).join('');
      document.getElementById('documentFile').innerHTML = options;
      document.getElementById('meetingTranscript').innerHTML = options;
    }}
    function auditList(events) {{
      if (!events.length) return '<p>暂无审计事件</p>';
      return `<table><thead><tr><th>时间</th><th>动作</th><th>状态</th><th>目标</th></tr></thead><tbody>${{events.map(event => `
        <tr><td>${{escapeHtml(event.timestamp)}}</td><td>${{escapeHtml(event.action)}}</td><td class="${{statusClass(event.status)}}">${{escapeHtml(event.status)}}</td><td>${{escapeHtml(event.target || '')}}</td></tr>`).join('')}}</tbody></table>`;
    }}
    function renderAudit(events) {{ document.getElementById('auditTable').innerHTML = auditList(events.slice().reverse()); }}
    function renderProjection(payload) {{
      if (payload.status !== 'ok') {{ document.getElementById('projectionLatest').innerHTML = '<p>暂无投影卡</p>'; return; }}
      document.getElementById('projectionLatest').innerHTML = `<p><code>${{escapeHtml(payload.name)}}</code></p><div>${{payload.html}}</div>`;
    }}
    function renderDesktopTasks(payload) {{
      const tasks = payload.tasks || [];
      if (!tasks.length) {{ document.getElementById('desktopTasks').innerHTML = '<p>暂无桌面任务请求</p>'; return; }}
      document.getElementById('desktopTasks').innerHTML = `<table><thead><tr><th>目标</th><th>状态</th><th>步骤</th><th>操作</th></tr></thead><tbody>${{tasks.map(task => `
        <tr>
          <td>${{escapeHtml(task.goal || task.id)}}<br><code>${{escapeHtml(task.id)}}</code></td>
          <td class="${{statusClass(task.status)}}">${{escapeHtml(task.status)}}</td>
          <td>${{(task.steps || []).map(step => `<div>${{escapeHtml(step.index)}}. ${{escapeHtml(step.description)}}</div>`).join('')}}</td>
          <td class="file-actions">
            <button class="link-button desktopStatusBtn" data-task="${{escapeHtml(task.id)}}" data-status="approved">批准</button>
            <button class="link-button desktopStatusBtn" data-task="${{escapeHtml(task.id)}}" data-status="rejected">拒绝</button>
            <button class="link-button desktopStatusBtn" data-task="${{escapeHtml(task.id)}}" data-status="done">完成</button>
          </td>
        </tr>`).join('')}}</tbody></table>`;
      document.querySelectorAll('.desktopStatusBtn').forEach(button => {{
        button.addEventListener('click', () => updateDesktopTaskStatus(button.dataset.task, button.dataset.status));
      }});
    }}
	    function renderReadiness(payload) {{
	      const items = payload.items || [];
	      const counts = payload.summary?.counts || {{}};
	      document.getElementById('readinessReport').innerHTML = `
	        <div class="cards">${{Object.entries(counts).map(([key, value]) => `<div class="card"><h3>${{escapeHtml(key)}}</h3><strong>${{escapeHtml(value)}}</strong></div>`).join('')}}</div>
        <table><thead><tr><th>能力</th><th>状态</th><th>证据</th><th>缺口/下一步</th></tr></thead><tbody>${{items.map(item => `
          <tr>
            <td>${{escapeHtml(item.capability)}}</td>
            <td class="${{statusClass(item.status)}}">${{escapeHtml(item.status)}}</td>
            <td>${{(item.evidence || []).map(entry => `<div>${{escapeHtml(entry)}}</div>`).join('')}}</td>
            <td><div>${{escapeHtml(item.gap || '')}}</div><div>${{escapeHtml(item.next_step || '')}}</div></td>
	          </tr>`).join('')}}</tbody></table>`;
	    }}
	    function renderTestGroups() {{
	      document.getElementById('testGroups').innerHTML = TEST_GROUPS.map(group => `
	        <div class="panel">
	          <h2>${{escapeHtml(group.title)}}</h2>
	          <div class="test-grid">${{group.tests.map(([id, label, manual]) => `
	            <div class="test-card">
	              <h3>${{escapeHtml(label)}}</h3>
	              <button class="test-button ${{manual ? 'manual' : ''}}" data-test="${{escapeHtml(id)}}">${{manual ? '手动运行' : '运行测试'}}</button>
	              <div class="test-result">${{renderTestResult(state.testResults[id])}}</div>
	            </div>`).join('')}}</div>
	        </div>`).join('');
	      document.querySelectorAll('.test-button').forEach(button => {{
	        button.addEventListener('click', () => runTest(button.dataset.test));
	      }});
	    }}
		    function renderTestResult(payload) {{
		      if (!payload) return '<span class="pill">未运行</span>';
		      const status = payload.status || payload.result?.status || 'ok';
		      const duration = payload.duration_ms === undefined ? '' : `${{payload.duration_ms}}ms`;
		      const result = payload.result === undefined ? payload : payload.result;
		      const summary = summarizeTestResult(result);
		      return `
		        <div class="test-summary">
		          <div class="test-summary-row"><span class="${{statusClass(status)}}">${{escapeHtml(status)}}</span><small>${{escapeHtml(duration)}}</small></div>
		          <small>${{escapeHtml(summary)}}</small>
		        </div>
		        <details class="test-details">
		          <summary>查看详情</summary>
		          <pre>${{renderJson(result)}}</pre>
		        </details>`;
		    }}
		    function summarizeTestResult(result) {{
		      if (!result || typeof result !== 'object') return String(result ?? '');
		      if (result.reason) return result.reason;
		      if (result.blocked_reason) return result.blocked_reason;
		      if (result.permission_mode) return `mode=${{result.permission_mode}}, desktop=${{result.desktop_backend}}`;
		      if (result.summary?.counts) return Object.entries(result.summary.counts).map(([key, value]) => `${{key}}=${{value}}`).join(', ');
		      if (Array.isArray(result.skills)) return `${{result.skills.length}} skills`;
		      if (Array.isArray(result.features)) return `${{result.features.length}} features`;
		      if (result.p0) return `${{result.p0.length}} P0 capabilities`;
		      if (result.file?.workspace_name) return result.file.workspace_name;
		      if (result.card?.path) return result.card.path.split('/').slice(-1)[0];
		      if (result.plan?.path) return result.plan.path.split('/').slice(-1)[0];
		      if (result.package?.minutes?.path) return `minutes: ${{result.package.minutes.path.split('/').slice(-1)[0]}}`;
		      if (result.task?.id) return `task: ${{result.task.id}}`;
		      if (result.companion?.execution_default) return `companion=${{result.companion.execution_default}}`;
		      if (result.cue?.state) return `state=${{result.cue.state}}`;
		      if (result.event_count !== undefined) return `${{result.event_count}} scene events`;
		      if (result.smart_home?.provider) return `provider=${{result.smart_home.provider}}`;
		      if (result.answer?.answer) return result.answer.answer;
		      if (result.route?.summary) return result.route.summary;
		      if (result.search?.count !== undefined) return `${{result.search.count}} matches`;
		      if (result.audit_log_path) return result.audit_log_path;
		      if (result.events) return `${{result.events.length}} audit events`;
		      if (result.note) return result.note;
		      const keys = Object.keys(result).slice(0, 4);
		      return keys.length ? keys.map(key => `${{key}}=${{shortValue(result[key])}}`).join(', ') : '完成';
		    }}
		    function shortValue(value) {{
		      if (value === null || value === undefined) return '';
		      if (Array.isArray(value)) return `[${{value.length}}]`;
		      if (typeof value === 'object') return '{{...}}';
		      return String(value).slice(0, 80);
		    }}
	    function cleanAssistantText(value) {{
	      const withoutPaths = String(value ?? '').split(' ').map(part => part.startsWith('/') ? '[工作区文件]' : part).join(' ');
	      return withoutPaths
	        .replace(/\\b(provider_status|mic_status|selected_mic|capture_status|capture_audio_bytes|capture_audio_rms|capture_audio_peak|openclaw_status|content_status|provider_error|openclaw_error|error|transcript|tingwu_minutes|openclaw_minutes)\\s*:\\s*[^\\n]+/gi, '')
	        .replace(/\\n{{3,}}/g, '\\n\\n')
	        .trim();
	    }}
	    function summarizeAssistantValue(value) {{
	      if (typeof value === 'string') return cleanAssistantText(value);
	      if (!value || typeof value !== 'object') return cleanAssistantText(value ?? '完成');
	      if (value.error?.message) return cleanAssistantText(value.error.message);
	      if (value.message) return cleanAssistantText(value.message);
	      if (value.text) return cleanAssistantText(value.text);
	      if (value.status === 'error' && value.body) return '操作失败，请到对应页面查看详情。';
	      if (value.test_suite) return `测试已完成：${{escapeHtml(value.test_suite)}}（${{escapeHtml(value.status || 'ok')}}）`;
	      if (value.test_id) return `测试已完成：${{escapeHtml(value.test_id)}}（${{escapeHtml(value.status || 'ok')}}）`;
	      if (value.task_id || value.task?.id) return '后台任务已创建，可在任务页面查看进度。';
	      if (value.files?.length) return `已上传 ${{value.files.length}} 个文件。`;
	      if (value.file?.workspace_name) return `文件已进入工作区：${{escapeHtml(value.file.workspace_name)}}`;
	      if (value.card || value.plan) return '投影内容已生成。';
	      if (value.status) return `操作已完成：${{escapeHtml(value.status)}}`;
	      return '操作已完成。';
	    }}
	    function addMessage(kind, value) {{
	      const node = document.createElement('div');
	      node.className = 'message ' + kind;
	      const summary = summarizeAssistantValue(value);
	      node.innerHTML = `<div>${{escapeHtml(summary)}}</div>`;
      document.getElementById('assistantLog').appendChild(node);
      node.scrollIntoView({{ block: 'end' }});
    }}
    async function uploadFiles(files) {{
      if (!files || !files.length) return;
      const form = new FormData();
      for (const file of files) form.append('file', file);
      const result = await api('/api/shared/upload', {{ method: 'POST', headers, body: form }});
      addMessage('system', result);
      await refreshAll();
    }}
    async function previewSharedFile(workspaceName) {{
      const result = await api('/api/shared/preview?file=' + encodeURIComponent(workspaceName));
      const target = document.getElementById('sharedPreview');
      if (result.status === 'ok') {{
        target.innerHTML = `<div class="preview-box"><h3>${{escapeHtml(result.workspace_name)}}</h3><pre>${{escapeHtml(result.text)}}</pre></div>`;
      }} else {{
        target.innerHTML = `<div class="preview-box"><h3>${{escapeHtml(result.workspace_name || workspaceName)}}</h3><p>这是非文本文件，请下载查看。</p></div>`;
      }}
      await refreshAll();
    }}
    async function updateDesktopTaskStatus(taskId, status) {{
      const result = await api('/api/desktop/task/status', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ task_id: taskId, status, actor: 'web_console' }}) }});
	      addMessage('system', result);
	      await refreshAll();
	    }}
	    async function runTest(testId) {{
	      state.testResults[testId] = {{ test_id: testId, status: 'running', result: {{ status: 'running' }} }};
	      renderTestGroups();
	      try {{
	        const result = await api('/api/test/run', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ test_id: testId }}) }});
	        state.testResults[testId] = result;
	        addMessage('system', {{ test_id: testId, status: result.status }});
	      }} catch (error) {{
	        state.testResults[testId] = {{ test_id: testId, status: 'error', result: error }};
	        addMessage('system', error);
	      }}
	      renderTestGroups();
	      await refreshAll();
	    }}
	    async function runSafeTests() {{
	      for (const testId of SAFE_TEST_IDS) {{
	        state.testResults[testId] = {{ test_id: testId, status: 'running', result: {{ status: 'running' }} }};
	      }}
	      renderTestGroups();
	      try {{
	        const result = await api('/api/test/run', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ test_id: 'all', test_ids: SAFE_TEST_IDS }}) }});
	        for (const item of result.results || []) state.testResults[item.test_id] = item;
	        addMessage('system', {{ test_suite: 'safe', status: result.status, count: result.count }});
	      }} catch (error) {{
	        addMessage('system', error);
	      }}
	      renderTestGroups();
	      await refreshAll();
	    }}

	    document.querySelectorAll('nav button').forEach(button => {{
      button.addEventListener('click', () => {{
        document.querySelectorAll('nav button').forEach(item => item.classList.remove('active'));
        document.querySelectorAll('section.view').forEach(item => item.classList.remove('active'));
        button.classList.add('active');
        document.getElementById(button.dataset.view).classList.add('active');
	      }});
	    }});
	    document.getElementById('runSafeTests').addEventListener('click', runSafeTests);
	    document.getElementById('clearTestResults').addEventListener('click', () => {{
	      state.testResults = {{}};
	      renderTestGroups();
	    }});
	    document.getElementById('uploadForm').addEventListener('submit', async event => {{
      event.preventDefault();
      const form = new FormData(event.target);
      const result = await api('/api/shared/upload', {{ method: 'POST', headers, body: form }});
      addMessage('system', result);
      await refreshAll();
    }});
    const dropZone = document.getElementById('dropZone');
    const dropInput = document.getElementById('dropInput');
    dropZone.addEventListener('click', () => dropInput.click());
    dropInput.addEventListener('change', () => uploadFiles(dropInput.files));
    for (const eventName of ['dragenter', 'dragover']) {{
      dropZone.addEventListener(eventName, event => {{
        event.preventDefault();
        dropZone.classList.add('dragging');
      }});
    }}
    for (const eventName of ['dragleave', 'drop']) {{
      dropZone.addEventListener(eventName, event => {{
        event.preventDefault();
        dropZone.classList.remove('dragging');
      }});
    }}
    dropZone.addEventListener('drop', event => uploadFiles(event.dataTransfer.files));
    document.getElementById('saveNote').addEventListener('click', async () => {{
      const result = await api('/api/shared/note', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ title: noteTitle.value, text: noteText.value }}) }});
      addMessage('system', result);
      await refreshAll();
    }});
    document.getElementById('analyzeDoc').addEventListener('click', async () => {{
      const result = await api('/api/document/analyze', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ filename: documentFile.value }}) }});
      setOutput('documentOutput', result);
      await refreshAll();
    }});
    document.getElementById('summarizeDoc').addEventListener('click', async () => {{
      const result = await api('/api/document/summarize', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ filename: documentFile.value, style: summaryStyle.value }}) }});
      setOutput('documentOutput', result);
      await refreshAll();
    }});
    document.getElementById('registerScan').addEventListener('click', async () => {{
      const result = await api('/api/scan/register', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ filename: documentFile.value, document_type: scanType.value }}) }});
      setOutput('documentOutput', result);
      await refreshAll();
    }});
    document.getElementById('runScanOcr').addEventListener('click', async () => {{
      const result = await api('/api/scan/ocr', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ filename: documentFile.value, language: scanLanguage.value }}) }});
      setOutput('documentOutput', result);
      await refreshAll();
    }});
    document.getElementById('summarizeOcr').addEventListener('click', async () => {{
      const result = await api('/api/scan/summarize-ocr', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ filename: documentFile.value }}) }});
      setOutput('documentOutput', result);
      await refreshAll();
    }});
    document.getElementById('parseBusinessCard').addEventListener('click', async () => {{
      const result = await api('/api/scan/business-card', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ filename: documentFile.value }}) }});
      setOutput('documentOutput', result);
      await refreshAll();
    }});
    document.getElementById('makeMinutes').addEventListener('click', async () => {{
      const result = await api('/api/meeting/minutes', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ transcript: meetingTranscript.value, title: meetingTitle.value, participants: meetingParticipants.value.split(',') }}) }});
      setOutput('meetingOutput', result);
      await refreshAll();
    }});
    document.getElementById('makeFollowup').addEventListener('click', async () => {{
      const result = await api('/api/meeting/followup', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ transcript: meetingTranscript.value, title: meetingTitle.value, participants: meetingParticipants.value.split(','), recipient: meetingRecipient.value, render_projection: false }}) }});
      setOutput('meetingOutput', result);
      await refreshAll();
    }});
    document.getElementById('createProjection').addEventListener('click', async () => {{
      const body = projectionBody.value;
      const result = await api('/api/projection/card', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ title: projectionTitle.value, mode: projectionMode.value, status: body, message: body, actions: body.split('\\n'), details: body.split('\\n') }}) }});
      addMessage('system', result);
      await refreshAll();
    }});
    document.getElementById('requestDesktopTask').addEventListener('click', async () => {{
      const result = await api('/api/desktop/task/request', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ goal: desktopTaskGoal.value, steps: desktopTaskSteps.value }}) }});
      addMessage('system', result);
      await refreshAll();
    }});
    document.querySelectorAll('.stateBtn').forEach(button => {{
      button.addEventListener('click', async () => {{
        const result = await api('/api/lelamp/state', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ state: button.dataset.state }}) }});
        setOutput('hardwareOutput', result);
        await refreshAll();
      }});
    }});
    document.getElementById('assistantSend').addEventListener('click', async () => {{
      const text = assistantInput.value.trim();
      if (!text) return;
      addMessage('user', text);
      assistantInput.value = '';
      const result = await api('/api/assistant/manual', {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify({{ text }}) }});
      addMessage('system', result);
      await refreshAll();
    }});

	    renderTestGroups();
	    refreshAll().catch(error => addMessage('system', error));
    setInterval(() => refreshAll().catch(() => {{}}), 8000);
  </script>
</body>
</html>"""
