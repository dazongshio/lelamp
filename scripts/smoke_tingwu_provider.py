#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "lelamp_runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

if sys.version_info < (3, 12):
    raise SystemExit("smoke_tingwu_provider requires Python >= 3.12. Run it with lelamp_runtime/.venv/bin/python.")

from lelamp.office_agent.audit import AuditLogger  # noqa: E402
from lelamp.office_agent.config import OfficeAgentConfig  # noqa: E402
import lelamp.office_agent.tingwu_meeting as tingwu_module  # noqa: E402
from lelamp.office_agent.tingwu_meeting import TingwuMeetingError, TingwuMeetingProvider, TingwuRealtimeCallback  # noqa: E402
from lelamp.office_agent.runtime import build_runtime  # noqa: E402
from lelamp.office_agent.web_console import ApiError, RequestContext, WebConsoleServer  # noqa: E402
from lelamp.office_agent.workspace import Workspace  # noqa: E402


class FakeTingwuHTTPServer:
    SOURCE_DATA_ID = "fake-data-id"
    MINUTES_DATA_ID = "fake-minutes-data-id"

    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.get_task_status = 0
        self.unsafe_artifact_urls = False
        self.private_artifact_urls = False
        self.private_dns_artifact_urls = False
        self.redirect_private_artifact = False
        self.secret_artifact_error = False
        self.large_artifact = False
        self.large_create_response = False
        self.large_get_task_response = False
        self.fail_minutes_create_with_secret = False
        self.create_delay_seconds = 0.0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> "FakeTingwuHTTPServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def _handler(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                owner.posts.append(payload)
                owner.headers.append(dict(self.headers.items()))
                task = payload.get("input", {}).get("task")
                data_id = payload.get("input", {}).get("dataId")
                if task == "createTask" and not data_id:
                    if owner.large_create_response:
                        self._send_large_response(tingwu_module.MAX_TINGWU_API_BYTES + 1)
                        return
                    if owner.create_delay_seconds:
                        time.sleep(owner.create_delay_seconds)
                    self._send_json({"output": {"dataId": owner.SOURCE_DATA_ID, "status": 3}, "request_id": "req-create"})
                    return
                if task == "createTask" and data_id:
                    if owner.fail_minutes_create_with_secret:
                        self._send_json(
                            {
                                "code": "SecretFailure",
                                "message": "Authorization: Bearer should-not-persist token=leaky-token password=hunter2",
                            },
                            status=500,
                        )
                        return
                    self._send_json(
                        {
                            "output": {
                                "dataId": owner.MINUTES_DATA_ID,
                                "sourceDataId": data_id,
                                "status": 1,
                            },
                            "request_id": "req-minutes",
                        }
                    )
                    return
                if task == "getTask":
                    if owner.large_get_task_response:
                        self._send_large_response(tingwu_module.MAX_TINGWU_API_BYTES + 1)
                        return
                    transcription_path = (
                        "file:///etc/passwd"
                        if owner.unsafe_artifact_urls
                        else "http://artifact.evil.test/latest/meta-data/iam/security-credentials/"
                        if owner.private_dns_artifact_urls
                        else "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
                        if owner.private_artifact_urls
                        else f"{owner.base_url}/redirect-private-artifact"
                        if owner.redirect_private_artifact
                        else f"{owner.base_url}/secret-artifact.json?token=leaky-query-token&signature=leaky-signature"
                        if owner.secret_artifact_error
                        else f"{owner.base_url}/large-artifact.json"
                        if owner.large_artifact
                        else f"{owner.base_url}/transcription.json"
                    )
                    self._send_json(
                        {
                            "output": {
                                "status": owner.get_task_status,
                                "transcriptionPath": transcription_path,
                                "summarizationPath": f"{owner.base_url}/summarization.json",
                                "meetingAssistancePath": f"{owner.base_url}/assistance.json",
                            },
                            "request_id": "req-get",
                            "usage": {},
                        }
                    )
                    return
                self._send_json({"code": "InvalidTask", "message": "unexpected task"}, status=400)

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/transcription.json":
                    self._send_json(
                        {
                            "audioInfo": {"duration": 1200, "sampleRate": 16000},
                            "paragraphs": [
                                {
                                    "speakerId": "1",
                                    "words": [
                                        {"text": "真实路径"},
                                        {"text": "转写"},
                                        {"text": "文本"},
                                    ],
                                }
                            ],
                        }
                    )
                    return
                if self.path == "/redirect-private-artifact":
                    self.send_response(302)
                    self.send_header("Location", "http://169.254.169.254/latest/meta-data/iam/security-credentials/?token=redirect-secret")
                    self.end_headers()
                    return
                if self.path.startswith("/secret-artifact.json"):
                    self._send_json(
                        {
                            "message": "artifact failed token=artifact-token password=artifact-password signature=artifact-signature /latest/meta-data/iam/security-credentials/",
                        },
                        status=500,
                    )
                    return
                if self.path == "/large-artifact.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(tingwu_module.MAX_TINGWU_ARTIFACT_BYTES + 1))
                    self.end_headers()
                    return
                if self.path == "/summarization.json":
                    self._send_json(
                        {
                            "fullSummary": {"summary": "真实模式会议摘要"},
                            "summaries": [{"summary": "真实模式补充摘要"}],
                        }
                    )
                    return
                if self.path == "/assistance.json":
                    self._send_json(
                        {
                            "actionItems": [{"items": [{"content": "跟进真实模式协议测试"}]}],
                            "keyInformations": [{"text": "确认通义听悟真实路径协议"}],
                        }
                    )
                    return
                self.send_error(404)

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def _send_json(self, payload: object, *, status: int = 200) -> None:
                encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _send_large_response(self, declared_size: int, *, status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(declared_size))
                self.end_headers()

        return Handler


class FakePCMStreamer:
    instances: list["FakePCMStreamer"] = []

    def __init__(self, *, device: str, sample_rate: int, frame_ms: int = 100):
        self.device = device
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.started = False
        self.stopped = False
        self.frames_sent = [] if device == "empty-mic" else [b"\x01\x00" * 1600, b"\x02\x00" * 1600, b"\x03\x00" * 1600]
        FakePCMStreamer.instances.append(self)

    def start(self) -> None:
        self.started = True

    def frames(self):
        for frame in self.frames_sent:
            yield frame

    def stop(self) -> None:
        self.stopped = True


class FakeTingWuRealtime:
    instances: list["FakeTingWuRealtime"] = []
    fail_stop_once = False
    fail_custom_finish_once = False
    fail_start_close_once = False

    def __init__(
        self,
        *,
        model: str,
        audio_format: str | None,
        sample_rate: int | None,
        app_id: str,
        base_address: str,
        api_key: str,
        callback,
        data_id: str,
    ):
        self.model = model
        self.audio_format = audio_format
        self.sample_rate = sample_rate
        self.app_id = app_id
        self.base_address = base_address
        self.api_key = api_key
        self.callback = callback
        self.data_id = data_id
        self.request = SimpleNamespace(task_id=None)
        self.audio_frames: list[bytes] = []
        self.text_frames: list[dict[str, Any]] = []
        self.closed = False
        FakeTingWuRealtime.instances.append(self)

    def start(self) -> None:
        if FakeTingWuRealtime.fail_start_close_once:
            FakeTingWuRealtime.fail_start_close_once = False
            self.callback.on_open()
            self.callback.on_close(1001, "websocket is not connected")
            raise RuntimeError("socket is already closed.")
        task_id = self.request.task_id or "0123456789abcdef"
        self._send_text_frame(
            json.dumps(
                {
                    "header": {"action": "run-task", "task_id": task_id, "request_id": task_id, "streaming": "duplex"},
                    "payload": {
                        "model": self.model,
                        "task_group": "aigc",
                        "task": "multimodal-generation",
                        "function": "generation",
                        "input": {"appId": self.app_id, "dataId": self.data_id, "directive": "start"},
                        "parameters": {"format": self.audio_format, "sampleRate": self.sample_rate},
                    },
                },
                ensure_ascii=False,
            )
        )
        self.callback.on_open()
        self.callback.on_started(task_id)
        self.callback.on_speech_listen(
            {
                "header": {"event": "result-generated", "task_id": task_id},
                "payload": {"output": {"action": "speech-listen", "dataId": self.data_id}},
            }
        )

    def send_audio_frame(self, frame: bytes) -> None:
        self.audio_frames.append(frame)
        if len(self.audio_frames) == 1:
            self.callback.on_recognize_result(
                {
                    "header": {"event": "result-generated", "task_id": self.request.task_id},
                    "payload": {
                        "output": {
                            "action": "recognize-result",
                            "transcription": {
                                "sentenceId": 1,
                                "speaker_id": "2",
                                "sentenceEnd": "false",
                                "words": [{"text": "临时"}, {"text": "转写"}],
                            },
                        }
                    },
                }
            )
        if len(self.audio_frames) == 2:
            self.callback.on_recognize_result(
                {
                    "header": {"event": "result-generated", "task_id": self.request.task_id},
                    "payload": {
                        "output": {
                            "action": "recognize-result",
                            "transcription": {
                                "sentenceId": 1,
                                "speakerId": "1",
                                "sentenceEnd": True,
                                "text": "真实路径转写文本",
                            },
                        }
                    },
                }
            )

    def _send_text_frame(self, text: str) -> None:
        message = json.loads(text)
        if message.get("header", {}).get("action") == "finish-task" and message.get("payload", {}).get("input", {}).get("dataId") and FakeTingWuRealtime.fail_custom_finish_once:
            FakeTingWuRealtime.fail_custom_finish_once = False
            raise RuntimeError("forced custom finish failure")
        self.text_frames.append(message)
        if message.get("header", {}).get("action") == "finish-task":
            self.callback.on_stopped()

    def stop(self) -> None:
        if FakeTingWuRealtime.fail_stop_once:
            FakeTingWuRealtime.fail_stop_once = False
            raise RuntimeError("forced sdk stop failure")
        task_id = self.request.task_id or "0123456789abcdef"
        self._send_text_frame(
            json.dumps(
                {
                    "header": {"action": "finish-task", "task_id": task_id, "request_id": task_id, "streaming": "duplex"},
                    "payload": {
                        "model": self.model,
                        "task_group": "aigc",
                        "task": "multimodal-generation",
                        "function": "generation",
                        "input": {"appId": self.app_id, "directive": "stop"},
                    },
                },
                ensure_ascii=False,
            )
        )

    def close(self) -> None:
        self.closed = True


class FakeSlowConnectTingWuRealtime(FakeTingWuRealtime):
    def _connect(self, api_key: str) -> None:
        self.connected_api_key = api_key
        self.callback.on_open()

    def _send_start_request(self) -> None:
        super().start()

    def start(self) -> None:
        raise AssertionError("provider should wait for websocket open before sending start request")


def assert_ok(name: str, condition: bool, details: object = "") -> None:
    if not condition:
        raise AssertionError(f"{name} failed: {details}")
    print(f"ok - {name}")


def wait_for_thread_results(threads: list[threading.Thread], results: list[object], expected: int, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    for thread in threads:
        remaining = max(0.1, deadline - time.monotonic())
        thread.join(timeout=remaining)
    while len(results) < expected and time.monotonic() < deadline:
        time.sleep(0.02)


def wait_for_session_status(provider: TingwuMeetingProvider, meeting_id: str, statuses: set[str], *, timeout: float = 3.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        last = provider.session_status(meeting_id)
        if str(last.get("status") or "") in statuses:
            return last
        time.sleep(0.05)
    return last


def stop_and_wait(provider: TingwuMeetingProvider, meeting_id: str, *, wait_seconds: float = 3.0, timeout: float = 5.0) -> dict[str, object]:
    result = provider.stop_realtime_meeting(meeting_id, wait_seconds=wait_seconds)
    if str(result.get("status") or "") in {"starting", "running", "stopping"}:
        return wait_for_session_status(provider, meeting_id, {"stopped", "failed", "completed"}, timeout=timeout)
    return result


def wait_for_fake_realtime_frames(
    realtime: FakeTingWuRealtime,
    expected_frames: int,
    *,
    timeout: float = 5.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(realtime.audio_frames) < expected_frames:
        time.sleep(0.05)
    return len(realtime.audio_frames) >= expected_frames


def main() -> int:
    original_realtime = tingwu_module.TingWuRealtime
    original_streamer = tingwu_module.ArecordPCMStreamer
    original_probe = tingwu_module.probe_arecord_device
    original_capture_probe = tingwu_module.preflight_arecord_capture
    tingwu_module.TingWuRealtime = FakeTingWuRealtime
    tingwu_module.ArecordPCMStreamer = FakePCMStreamer
    capture_preflights: list[tuple[str, int]] = []

    def fake_probe_arecord_device(device: str) -> dict[str, Any]:
        candidates = [
            {
                "card_index": 1,
                "card_id": "USBMic",
                "card_name": "USB Microphone",
                "device_index": 0,
                "device_name": "USB Audio",
                "hw": "hw:1,0",
                "plughw": "plughw:1,0",
                "plughw_by_id": "plughw:CARD=USBMic,DEV=0",
            }
        ]
        if device == "auto":
            return {
                "status": "available",
                "configured_device": device,
                "selected_device": "plughw:1,0",
                "configured_device_valid": True,
                "auto_selected": True,
                "message": "ready",
                "candidates": candidates,
            }
        if device == "missing-mic":
            return {
                "status": "unavailable",
                "configured_device": device,
                "selected_device": "",
                "configured_device_valid": False,
                "message": "Configured microphone was not listed by arecord -l.",
                "candidates": candidates,
            }
        if device == "empty-mic":
            return {
                "status": "available",
                "configured_device": device,
                "selected_device": device,
                "configured_device_valid": True,
                "message": "ready",
                "candidates": candidates,
            }
        return original_probe(device)

    def fake_preflight_arecord_capture(device: str, sample_rate: int, duration_seconds: int = 1) -> dict[str, Any]:
        capture_preflights.append((device, sample_rate))
        if device == "blocked-capture":
            return {"status": "unavailable", "selected_device": device, "message": "forced capture open failure"}
        return {
            "status": "available",
            "selected_device": device,
            "sample_rate": sample_rate,
            "duration_seconds": duration_seconds,
            "audio_bytes": sample_rate * 2 * max(1, int(duration_seconds)),
            "audio_rms": 80,
            "audio_peak": 900,
            "message": "ready",
        }

    tingwu_module.probe_arecord_device = fake_probe_arecord_device
    tingwu_module.preflight_arecord_capture = fake_preflight_arecord_capture
    try:
        with tempfile.TemporaryDirectory(prefix="lelamp-tingwu-provider-") as tmp, FakeTingwuHTTPServer() as fake_http:
            root = Path(tmp)
            workspace_root = root / "workspace"
            audit_path = root / "audit.jsonl"
            config = OfficeAgentConfig(
                workspace_dir=workspace_root,
                audit_log_path=audit_path,
                allowed_roots=(workspace_root,),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            audit = AuditLogger(audit_path)
            workspace = Workspace(workspace_root, config.allowed_roots, audit)
            provider = TingwuMeetingProvider(config, workspace, audit)

            audit_canary_path = root / "audit_canary.jsonl"
            audit_canary = AuditLogger(audit_canary_path)
            audit_canary.record(
                "audit_token=action-token password=action-password",
                status="error token=status-token password=status-password",
                target="https://audit.example.test/path?token=audit-target-token password=audit-target-password /latest/meta-data/iam/security-credentials/",
                details={
                    "message": "bare dashscope key sk-audit-redaction-canary",
                    "authorization": "Bearer audit-bearer",
                    "api_key": "audit-api-key",
                    "clientSecret": "audit-client-secret",
                    "dashscope-token": "audit-dashscope-token",
                    "nested": [
                        "token=nested-token password=nested-password",
                        {
                            "password": "nested-password-key",
                            "url": "https://user:pass@audit.example.test/path?signature=audit-signature",
                            "metadata": "/latest/meta-data/iam/security-credentials/",
                        },
                    ],
                },
                actor="actor token=audit-actor-token password=audit-actor-password",
                request_id="request token=audit-request-token password=audit-request-password",
                permission_mode="sandbox token=audit-permission-token",
                desktop_backend="audit_only password=audit-desktop-password",
            )
            audit_canary_text = audit_canary_path.read_text(encoding="utf-8")
            audit_canary_payload = json.loads(audit_canary_text)
            audit_raw_canaries = [
                "action-token",
                "action-password",
                "status-token",
                "status-password",
                "audit-target-token",
                "audit-target-password",
                "security-credentials",
                "audit-bearer",
                "audit-api-key",
                "audit-client-secret",
                "audit-dashscope-token",
                "nested-token",
                "nested-password",
                "nested-password-key",
                "user:pass",
                "audit-signature",
                "audit-actor-token",
                "audit-actor-password",
                "audit-request-token",
                "audit-request-password",
                "audit-permission-token",
                "audit-desktop-password",
                "sk-audit-redaction-canary",
            ]
            assert_ok(
                "AuditLogger redacts sensitive fields and text before JSONL persistence",
                all(canary not in audit_canary_text for canary in audit_raw_canaries)
                and "[redacted]" in audit_canary_text
                and audit_canary_payload["details"]["authorization"] == "[redacted]"
                and audit_canary_payload["details"]["api_key"] == "[redacted]"
                and audit_canary_payload["details"]["clientSecret"] == "[redacted]"
                and audit_canary_payload["details"]["dashscope-token"] == "[redacted]"
                and audit_canary_payload["details"]["nested"][1]["password"] == "[redacted]",
                audit_canary_payload,
            )

            status = provider.status()
            assert_ok("real-mode provider available", status["status"] == "available" and status["configured"] is True, status)
            assert_ok("microphone preflight available", status["mic_status"] == "available", status)

            auto_config = OfficeAgentConfig(
                workspace_dir=root / "auto_workspace",
                audit_log_path=root / "auto_audit.jsonl",
                allowed_roots=(root / "auto_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="auto",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            auto_provider = TingwuMeetingProvider(
                auto_config,
                Workspace(auto_config.workspace_dir, auto_config.allowed_roots, AuditLogger(auto_config.audit_log_path)),
                AuditLogger(auto_config.audit_log_path),
            )
            auto_status = auto_provider.status()
            assert_ok("auto microphone selects ALSA device", auto_status["selected_mic_device"] == "plughw:1,0" and auto_status["mic_status"] == "available", auto_status)
            assert_ok(
                "provider status exposes configured and selected microphone devices",
                auto_status["configured_mic_device"] == "auto"
                and auto_status["mic_device"] == "auto"
                and auto_status["selected_mic_device"] == "plughw:1,0",
                auto_status,
            )

            concurrent_config = OfficeAgentConfig(
                workspace_dir=root / "concurrent_workspace",
                audit_log_path=root / "concurrent_audit.jsonl",
                allowed_roots=(root / "concurrent_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            concurrent_provider = TingwuMeetingProvider(
                concurrent_config,
                Workspace(concurrent_config.workspace_dir, concurrent_config.allowed_roots, AuditLogger(concurrent_config.audit_log_path)),
                AuditLogger(concurrent_config.audit_log_path),
            )
            concurrent_start_results: list[tuple[str, object]] = []
            concurrent_lock = threading.Lock()
            posts_before_concurrent = len(fake_http.posts)
            fake_http.create_delay_seconds = 0.2

            def concurrent_start(index: int) -> None:
                try:
                    result = concurrent_provider.start_realtime_meeting(title=f"concurrent start {index}", participants=["Alice"], max_seconds=30)
                    with concurrent_lock:
                        concurrent_start_results.append(("ok", result))
                except Exception as exc:
                    with concurrent_lock:
                        concurrent_start_results.append(("error", str(exc)))

            threads = [threading.Thread(target=concurrent_start, args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            wait_for_thread_results(threads, concurrent_start_results, 2, timeout=10)
            fake_http.create_delay_seconds = 0.0
            concurrent_successes = [item for status, item in concurrent_start_results if status == "ok"]
            concurrent_errors = [item for status, item in concurrent_start_results if status == "error"]
            concurrent_task_calls = [
                post.get("input", {})
                for post in fake_http.posts[posts_before_concurrent:]
                if isinstance(post.get("input"), dict) and post.get("input", {}).get("task") == "createTask" and not post.get("input", {}).get("dataId")
            ]
            assert_ok(
                "Concurrent realtime starts are serialized before CreateTask",
                len(concurrent_start_results) == 2
                and len(concurrent_successes) == 1
                and len(concurrent_errors) == 1
                and "Another realtime meeting is already running" in str(concurrent_errors[0])
                and len(concurrent_task_calls) == 1,
                {"results": concurrent_start_results, "posts": fake_http.posts[posts_before_concurrent:]},
            )
            if concurrent_successes:
                stop_and_wait(concurrent_provider, str(concurrent_successes[0].get("meeting_id")), wait_seconds=3, timeout=5)

            cross_config = OfficeAgentConfig(
                workspace_dir=root / "cross_process_workspace",
                audit_log_path=root / "cross_process_audit.jsonl",
                allowed_roots=(root / "cross_process_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            cross_providers = [
                TingwuMeetingProvider(
                    cross_config,
                    Workspace(cross_config.workspace_dir, cross_config.allowed_roots, AuditLogger(cross_config.audit_log_path)),
                    AuditLogger(cross_config.audit_log_path),
                )
                for _ in range(2)
            ]
            cross_start_results: list[tuple[str, int, object]] = []
            cross_lock = threading.Lock()
            posts_before_cross = len(fake_http.posts)
            fake_http.create_delay_seconds = 0.2

            def cross_start(index: int) -> None:
                try:
                    result = cross_providers[index].start_realtime_meeting(title=f"workspace lock {index}", participants=["Alice"], max_seconds=30)
                    with cross_lock:
                        cross_start_results.append(("ok", index, result))
                except Exception as exc:
                    with cross_lock:
                        cross_start_results.append(("error", index, str(exc)))

            cross_threads = [threading.Thread(target=cross_start, args=(index,)) for index in range(2)]
            for thread in cross_threads:
                thread.start()
            wait_for_thread_results(cross_threads, cross_start_results, 2, timeout=10)
            fake_http.create_delay_seconds = 0.0
            cross_successes = [(index, item) for status, index, item in cross_start_results if status == "ok"]
            cross_errors = [item for status, _, item in cross_start_results if status == "error"]
            cross_task_calls = [
                post.get("input", {})
                for post in fake_http.posts[posts_before_cross:]
                if isinstance(post.get("input"), dict) and post.get("input", {}).get("task") == "createTask" and not post.get("input", {}).get("dataId")
            ]
            assert_ok(
                "Workspace realtime lock serializes starts across provider instances before CreateTask",
                len(cross_start_results) == 2
                and len(cross_successes) == 1
                and len(cross_errors) == 1
                and "Another realtime meeting is already running" in str(cross_errors[0])
                and len(cross_task_calls) == 1,
                {"results": cross_start_results, "posts": fake_http.posts[posts_before_cross:]},
            )
            if cross_successes:
                provider_index, cross_started = cross_successes[0]
                stop_and_wait(cross_providers[provider_index], str(cross_started.get("meeting_id")), wait_seconds=3, timeout=5)

            lock_config = OfficeAgentConfig(
                workspace_dir=root / "workspace_lock_recovery",
                audit_log_path=root / "workspace_lock_recovery_audit.jsonl",
                allowed_roots=(root / "workspace_lock_recovery",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            lock_owner = TingwuMeetingProvider(
                lock_config,
                Workspace(lock_config.workspace_dir, lock_config.allowed_roots, AuditLogger(lock_config.audit_log_path)),
                AuditLogger(lock_config.audit_log_path),
            )
            assert_ok("Workspace realtime lock can be acquired", lock_owner._acquire_workspace_meeting_lock(title="held elsewhere"), "")  # noqa: SLF001
            lock_path = lock_config.workspace_dir / "meetings" / ".tingwu_realtime.lock"
            lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
            assert_ok(
                "Workspace realtime lock metadata is durable JSON",
                lock_payload.get("pid") and lock_payload.get("locked_at") and lock_payload.get("title") == "held elsewhere",
                lock_payload,
            )
            lock_active_dir = lock_config.workspace_dir / "meetings" / "tingwu_lock_active_elsewhere"
            lock_active_dir.mkdir(parents=True, exist_ok=True)
            lock_session = {
                "meeting_id": "tingwu_lock_active_elsewhere",
                "title": "lock active elsewhere",
                "participants": ["Alice"],
                "task_id": "fake-data-id",
                "data_id": "fake-data-id",
                "status": "running",
                "created_at": "2026-05-26T00:00:00+00:00",
                "started_at": "2026-05-26T00:00:01+00:00",
                "transcript": [{"timestamp": "2026-05-26T00:00:02+00:00", "speaker": "Alice", "text": "另一个进程仍在采集", "final": True}],
                "output_dir": str(lock_active_dir),
                "transcript_path": str(lock_active_dir / "transcript.md"),
                "audio_path": str(lock_active_dir / "audio.wav"),
                "minutes_path": str(lock_active_dir / "tingwu_ai_minutes.md"),
                "task_payload": {"data_id": "fake-data-id"},
                "ai_minutes": {},
                "error": "",
            }
            (lock_active_dir / "session.json").write_text(json.dumps(lock_session, ensure_ascii=False, indent=2), encoding="utf-8")
            lock_observer = TingwuMeetingProvider(
                lock_config,
                Workspace(lock_config.workspace_dir, lock_config.allowed_roots, AuditLogger(lock_config.audit_log_path)),
                AuditLogger(lock_config.audit_log_path),
            )
            lock_observer_status = lock_observer.status()
            lock_observer_session = lock_observer.session_status("tingwu_lock_active_elsewhere")
            posts_before_lock_observer_start = len(fake_http.posts)
            try:
                lock_observer.start_realtime_meeting(title="blocked by lock holder", participants=["Alice"], max_seconds=30)
                raise AssertionError("start should be blocked while another process holds the realtime lock")
            except Exception as exc:
                lock_observer_start_blocked = "Another realtime meeting is already running" in str(exc)
            assert_ok(
                "Held workspace lock prevents active persisted sessions from being recovered as interrupted",
                lock_observer_status.get("active_meeting_id") == "tingwu_lock_active_elsewhere"
                and lock_observer_status.get("active_count") == 1
                and lock_observer_session.get("status") == "running"
                and "Recovered from persisted running state" not in str(lock_observer_session.get("error") or "")
                and lock_observer_start_blocked
                and len(fake_http.posts) == posts_before_lock_observer_start,
                {"status": lock_observer_status, "session": lock_observer_session, "blocked": lock_observer_start_blocked},
            )
            lock_owner._release_workspace_meeting_lock()  # noqa: SLF001
            released_lock_text = lock_path.read_text(encoding="utf-8")
            released_lock_payload = json.loads(released_lock_text)
            assert_ok(
                "Workspace realtime lock release metadata is clean JSON",
                "\x00" not in released_lock_text and released_lock_payload.get("released_at") and "locked_at" not in released_lock_payload,
                released_lock_payload,
            )
            lock_recover_provider = TingwuMeetingProvider(
                lock_config,
                Workspace(lock_config.workspace_dir, lock_config.allowed_roots, AuditLogger(lock_config.audit_log_path)),
                AuditLogger(lock_config.audit_log_path),
            )
            lock_recovered_session = lock_recover_provider.session_status("tingwu_lock_active_elsewhere")
            assert_ok(
                "Released workspace lock allows interrupted active sessions to recover",
                lock_recovered_session.get("status") == "stopped"
                and "Recovered from persisted running state" in str(lock_recovered_session.get("error") or ""),
                lock_recovered_session,
            )

            held_lock_config = OfficeAgentConfig(
                workspace_dir=root / "held_lock_workspace",
                audit_log_path=root / "held_lock_audit.jsonl",
                allowed_roots=(root / "held_lock_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            held_lock_provider = TingwuMeetingProvider(
                held_lock_config,
                Workspace(held_lock_config.workspace_dir, held_lock_config.allowed_roots, AuditLogger(held_lock_config.audit_log_path)),
                AuditLogger(held_lock_config.audit_log_path),
            )
            assert_ok("Provider-held workspace lock can be acquired", held_lock_provider._acquire_workspace_meeting_lock(title="finalize window"), "")  # noqa: SLF001
            held_lock_provider._assign_workspace_meeting_lock("tingwu_lock_metadata_owner")  # noqa: SLF001
            held_lock_path = held_lock_config.workspace_dir / "meetings" / ".tingwu_realtime.lock"
            held_lock_payload = json.loads(held_lock_path.read_text(encoding="utf-8"))
            assert_ok(
                "Workspace realtime lock assignment updates meeting id",
                held_lock_payload.get("meeting_id") == "tingwu_lock_metadata_owner" and held_lock_payload.get("locked_at"),
                held_lock_payload,
            )
            posts_before_held_lock_start = len(fake_http.posts)
            try:
                held_lock_provider.start_realtime_meeting(title="blocked by own held lock", participants=["Alice"], max_seconds=30)
                raise AssertionError("start should be blocked while the provider still holds the workspace realtime lock")
            except Exception as exc:
                held_lock_start_blocked = "Another realtime meeting is already running" in str(exc)
            assert_ok(
                "Provider-held workspace lock blocks a new start even without an active in-memory session",
                held_lock_start_blocked and len(fake_http.posts) == posts_before_held_lock_start,
                {"blocked": held_lock_start_blocked, "posts": fake_http.posts[posts_before_held_lock_start:]},
            )
            held_lock_provider._release_workspace_meeting_lock()  # noqa: SLF001

            owner_config = OfficeAgentConfig(
                workspace_dir=root / "lock_owner_workspace",
                audit_log_path=root / "lock_owner_audit.jsonl",
                allowed_roots=(root / "lock_owner_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            owner_provider = TingwuMeetingProvider(
                owner_config,
                Workspace(owner_config.workspace_dir, owner_config.allowed_roots, AuditLogger(owner_config.audit_log_path)),
                AuditLogger(owner_config.audit_log_path),
            )
            owner_active = owner_provider.start_realtime_meeting(title="lock owner active", participants=["Alice"], max_seconds=30)
            stale_dir = owner_config.workspace_dir / "meetings" / "tingwu_other_stopped"
            stale_dir.mkdir(parents=True, exist_ok=True)
            stale_session = {
                "meeting_id": "tingwu_other_stopped",
                "title": "other stopped",
                "participants": ["Bob"],
                "task_id": "fake-data-id",
                "data_id": "fake-data-id",
                "status": "stopped",
                "created_at": "2026-05-26T00:00:00+00:00",
                "started_at": "2026-05-26T00:00:01+00:00",
                "stopped_at": "2026-05-26T00:00:02+00:00",
                "transcript": [{"timestamp": "2026-05-26T00:00:02+00:00", "speaker": "Bob", "text": "另一个停止后的会议", "final": True}],
                "output_dir": str(stale_dir),
                "transcript_path": str(stale_dir / "transcript.md"),
                "audio_path": str(stale_dir / "audio.wav"),
                "minutes_path": str(stale_dir / "tingwu_ai_minutes.md"),
                "task_payload": {"data_id": "fake-data-id"},
                "ai_minutes": {},
                "error": "",
            }
            (stale_dir / "session.json").write_text(json.dumps(stale_session, ensure_ascii=False, indent=2), encoding="utf-8")
            posts_before_wrong_finalize = len(fake_http.posts)
            try:
                owner_provider.finalize_meeting("tingwu_other_stopped")
                raise AssertionError("finalize should be blocked while this provider owns the workspace lock for another meeting")
            except Exception as exc:
                wrong_finalize_blocked = "Another realtime meeting is already running" in str(exc)
            assert_ok(
                "Workspace lock ownership blocks finalizing a different meeting in the same provider",
                wrong_finalize_blocked and len(fake_http.posts) == posts_before_wrong_finalize,
                {"blocked": wrong_finalize_blocked, "posts": fake_http.posts[posts_before_wrong_finalize:]},
            )
            stop_and_wait(owner_provider, str(owner_active.get("meeting_id")), wait_seconds=3, timeout=5)

            create_posts_before_large = len(fake_http.posts)
            fake_http.large_create_response = True
            try:
                provider.start_realtime_meeting(title="large create response", participants=["Alice"], max_seconds=30)
                raise AssertionError("large CreateTask response should fail")
            except Exception as exc:
                assert_ok(
                    "CreateTask blocks oversized Tingwu API responses before session creation",
                    "CreateTask response is too large" in str(exc)
                    and len(fake_http.posts) == create_posts_before_large + 1
                    and all("large create response" not in str(item.title) for item in provider._sessions.values()),  # noqa: SLF001
                    str(exc),
                )
            finally:
                fake_http.large_create_response = False

            realtime_count_before_protocol = len(FakeTingWuRealtime.instances)
            started = provider.start_realtime_meeting(title="protocol smoke", participants=["Alice"], max_seconds=30)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and len(FakeTingWuRealtime.instances) <= realtime_count_before_protocol:
                time.sleep(0.05)
            assert_ok(
                "protocol realtime client starts",
                len(FakeTingWuRealtime.instances) > realtime_count_before_protocol,
                {"instances": len(FakeTingWuRealtime.instances), "started": started, "status": provider.session_status(str(started["meeting_id"]))},
            )
            realtime = FakeTingWuRealtime.instances[realtime_count_before_protocol]
            meeting_id = str(started["meeting_id"])
            assert_ok("start returns running session", started["status"] == "running" and started["data_id"] == "fake-data-id", started)
            assert_ok(
                "capture preflight runs before CreateTask",
                ("fake-mic", 16000) in capture_preflights and started.get("task_payload", {}).get("mic_probe", {}).get("capture_probe", {}).get("status") == "available",
                {"capture_preflights": capture_preflights, "started": started},
            )
            realtime_create_payload = next(
                (
                    post
                    for post in fake_http.posts
                    if isinstance(post.get("input"), dict)
                    and post["input"].get("task") == "createTask"
                    and not post["input"].get("dataId")
                ),
                {},
            )
            realtime_create_parameters = realtime_create_payload.get("parameters") if isinstance(realtime_create_payload.get("parameters"), dict) else {}
            realtime_create_analysis = realtime_create_parameters.get("analysis") if isinstance(realtime_create_parameters.get("analysis"), dict) else {}
            realtime_create_transcription = realtime_create_parameters.get("transcription") if isinstance(realtime_create_parameters.get("transcription"), dict) else {}
            assert_ok(
                "CreateTask disables optional custom prompt and translation defaults",
                realtime_create_analysis.get("customPromptEnabled") is False
                and realtime_create_transcription.get("translationEnabled") is False,
                realtime_create_payload,
            )

            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                current = provider.session_status(meeting_id)
                if current.get("final_count"):
                    break
                time.sleep(0.05)
            assert_ok(
                "protocol realtime stream sent expected audio frames before stop",
                wait_for_fake_realtime_frames(realtime, 2),
                {"frames": len(realtime.audio_frames), "meeting_id": meeting_id, "status": provider.session_status(meeting_id)},
            )
            assert_ok(
                "Realtime PCM gain defaults to passthrough",
                realtime.audio_frames[0] == (b"\x01\x00" * 1600)
                and started.get("task_payload", {}).get("pcm_gain") == 1.0,
                {"pcm_gain": started.get("task_payload", {}).get("pcm_gain")},
            )

            posts_before_stop = len(fake_http.posts)
            stopped = stop_and_wait(provider, meeting_id, wait_seconds=3, timeout=5)
            assert_ok("stop saves stopped session before AI minutes fetch", stopped["status"] == "stopped", stopped)
            assert_ok("realtime transcript captured", "真实路径转写文本" in str(stopped.get("realtime_transcript")), stopped)
            assert_ok("partial transcript is not treated as final", stopped.get("final_count") == 1 and "临时转写" not in str(stopped.get("realtime_transcript")), stopped)
            assert_ok("workspace output stays under root", Path(str(stopped["output_dir"])).is_relative_to(workspace_root), stopped)
            assert_ok("audio level metrics persisted", stopped.get("audio_rms", 0) > 0 and stopped.get("audio_peak", 0) > 0, stopped)

            transcript_path = Path(str(stopped["transcript_path"]))
            audio_path = Path(str(stopped["audio_path"]))
            session_path = Path(str(stopped["output_dir"])) / "session.json"
            assert_ok("transcript saved", transcript_path.is_file() and "真实路径转写文本" in transcript_path.read_text(encoding="utf-8"), transcript_path)
            assert_ok(
                "stop does not fetch Tingwu AI minutes",
                not stopped.get("minutes_path")
                and not any(
                    isinstance(post.get("input"), dict)
                    and (post.get("input", {}).get("task") == "getTask" or post.get("input", {}).get("dataId") == "fake-data-id")
                    for post in fake_http.posts[posts_before_stop:]
                ),
                fake_http.posts[posts_before_stop:],
            )
            assert_ok(
                "provider artifacts use atomic writes without leaving temp files",
                session_path.is_file()
                and json.loads(session_path.read_text(encoding="utf-8")).get("meeting_id") == meeting_id
                and not list(Path(str(stopped["output_dir"])).glob(".*.tmp")),
                {"session_path": session_path, "temps": [str(item) for item in Path(str(stopped["output_dir"])).glob(".*.tmp")]},
            )
            with wave.open(str(audio_path), "rb") as audio:
                assert_ok("audio wav saved", audio.getnframes() > 0 and audio.getframerate() == 16000, audio_path)

            finalized = provider.finalize_meeting(meeting_id)
            minutes_path = Path(str(finalized["minutes_path"]))
            minutes_text = minutes_path.read_text(encoding="utf-8") if minutes_path.is_file() else ""
            assert_ok(
                "finalize fetches and saves Tingwu AI minutes",
                finalized["status"] == "completed"
                and minutes_path.is_file()
                and "真实模式会议摘要" in minutes_text
                and "确认通义听悟真实路径协议" in minutes_text
                and "跟进真实模式协议测试" in minutes_text,
                finalized,
            )
            official_shape = tingwu_module.normalize_minutes_payload(
                {
                    "output": {
                        "status": 0,
                        "summarizationPathData": {
                            "Summarization": {
                                "ParagraphSummary": "官方结构段落摘要",
                                "ConversationalSummary": [{"summary": "官方结构对话摘要"}],
                                "QuestionsAnsweringSummary": [{"question": "风险是什么", "answer": "需要验证真实听悟字段"}],
                            }
                        },
                        "meetingAssistancePathData": {
                            "MeetingAssistance": {
                                "KeySentences": [{"sentence": "官方结构决策句"}],
                                "Actions": [{"task": "官方结构待办项", "owner": "Alice"}],
                            }
                        },
                    }
                }
            )
            assert_ok(
                "Tingwu AI minutes normalization covers official Summarization and MeetingAssistance fields",
                official_shape.get("summary") == "官方结构段落摘要"
                and official_shape.get("summary_source") == "ParagraphSummary"
                and official_shape.get("structured_summary") is True
                and "官方结构决策句" in official_shape.get("decisions", [])
                and "官方结构待办项" in official_shape.get("action_items", []),
                official_shape,
            )
            raw_summary_shape = tingwu_module.normalize_minutes_payload({"output": {"status": 0, "unknown": {"text": "raw only"}}})
            assert_ok(
                "Tingwu AI minutes normalization marks raw JSON fallback as unstructured",
                raw_summary_shape.get("summary_source") == "raw_payload"
                and raw_summary_shape.get("structured_summary") is False
                and str(raw_summary_shape.get("summary") or "").strip(),
                raw_summary_shape,
            )
            non_summary_shape = tingwu_module.normalize_minutes_payload(
                {
                    "output": {
                        "status": 0,
                        "autoChaptersPathData": [{"title": "章节标题不能冒充摘要"}],
                        "transcriptionPathData": {
                            "paragraphs": [{"speakerId": "1", "text": "转写段落不能冒充摘要"}]
                        },
                    }
                }
            )
            assert_ok(
                "Tingwu AI minutes normalization does not treat chapters or transcript paragraphs as structured summary",
                non_summary_shape.get("summary_source") == "raw_payload"
                and non_summary_shape.get("structured_summary") is False
                and "章节标题不能冒充摘要" in str(non_summary_shape.get("summary") or ""),
                non_summary_shape,
            )
            task_calls = [post.get("input", {}) for post in fake_http.posts if isinstance(post.get("input"), dict)]
            minutes_create_payload = next(
                (
                    post
                    for post in fake_http.posts
                    if isinstance(post.get("input"), dict)
                    and post["input"].get("task") == "createTask"
                    and post["input"].get("dataId") == "fake-data-id"
                ),
                {},
            )
            minutes_create_parameters = minutes_create_payload.get("parameters") if isinstance(minutes_create_payload.get("parameters"), dict) else {}
            minutes_create_analysis = minutes_create_parameters.get("analysis") if isinstance(minutes_create_parameters.get("analysis"), dict) else {}
            assert_ok(
                "create/get task HTTP calls made after explicit finalize",
                any(call.get("task") == "createTask" and not call.get("dataId") for call in task_calls)
                and any(call.get("task") == "createTask" and call.get("dataId") == "fake-data-id" for call in task_calls)
                and any(call.get("task") == "getTask" and call.get("dataId") == "fake-minutes-data-id" for call in task_calls),
                fake_http.posts,
            )
            assert_ok(
                "Realtime minutes CreateTask disables optional custom prompt defaults",
                minutes_create_analysis.get("customPromptEnabled") is False,
                minutes_create_payload,
            )
            persisted_after_finalize = json.loads(session_path.read_text(encoding="utf-8"))
            persisted_http_operations = persisted_after_finalize.get("tingwu_http_operations")
            operation_actions = [
                str(item.get("action") or "")
                for item in persisted_http_operations
                if isinstance(item, dict)
            ] if isinstance(persisted_http_operations, list) else []
            assert_ok(
                "Tingwu HTTP operation chain persists to session JSON",
                {"CreateTask", "CreateRealtimeMinutesTask", "GetTask"}.issubset(set(operation_actions))
                and all(
                    isinstance(item, dict)
                    and item.get("endpoint") == str(config.tingwu_http_url)
                    for item in persisted_http_operations
                )
                and any(
                    isinstance(item, dict)
                    and item.get("action") == "CreateRealtimeMinutesTask"
                    and item.get("request_data_id") == "fake-data-id"
                    and item.get("response_data_id") == "fake-minutes-data-id"
                    for item in persisted_http_operations
                )
                and any(
                    isinstance(item, dict)
                    and item.get("action") == "GetTask"
                    and item.get("request_data_id") == "fake-minutes-data-id"
                    for item in persisted_http_operations
                ),
                persisted_http_operations,
            )
            http_restore_provider = TingwuMeetingProvider(
                config,
                Workspace(config.workspace_dir, config.allowed_roots, AuditLogger(config.audit_log_path)),
                AuditLogger(config.audit_log_path),
            )
            http_restore_status = http_restore_provider.session_status(meeting_id)
            restored_http_operations = http_restore_status.get("tingwu_http_operations")
            assert_ok(
                "Tingwu HTTP operation chain restores from session JSON",
                restored_http_operations == persisted_http_operations,
                {"restored": restored_http_operations, "persisted": persisted_http_operations},
            )
            http_call_count_after_finalize = len(fake_http.posts)
            stopped_again = provider.stop_realtime_meeting(meeting_id, wait_seconds=3)
            assert_ok(
                "stop is idempotent after meeting completion",
                stopped_again["status"] == "completed" and len(fake_http.posts) == http_call_count_after_finalize,
                {"stopped_again": stopped_again, "posts": fake_http.posts},
            )
            repeat_started = provider.start_realtime_meeting(title="protocol smoke", participants=["Alice"], max_seconds=30)
            repeat_meeting_id = str(repeat_started["meeting_id"])
            repeat_realtime = FakeTingWuRealtime.instances[-1]
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if provider.session_status(repeat_meeting_id).get("final_count"):
                    break
                time.sleep(0.05)
            assert_ok(
                "repeat realtime stream sent expected audio frames before stop",
                wait_for_fake_realtime_frames(repeat_realtime, 2),
                {"frames": len(repeat_realtime.audio_frames), "meeting_id": repeat_meeting_id, "status": provider.session_status(repeat_meeting_id)},
            )
            repeat_stopped = stop_and_wait(provider, repeat_meeting_id, wait_seconds=3, timeout=5)
            first_session_path = Path(str(stopped["output_dir"]), "session.json")
            repeat_session_path = Path(str(repeat_stopped["output_dir"]), "session.json")
            first_session_payload = json.loads(first_session_path.read_text(encoding="utf-8")) if first_session_path.is_file() else {}
            repeat_session_payload = json.loads(repeat_session_path.read_text(encoding="utf-8")) if repeat_session_path.is_file() else {}
            assert_ok(
                "Repeated same-title meetings get unique workspace output directories",
                repeat_meeting_id != meeting_id
                and repeat_stopped.get("status") == "stopped"
                and provider._workspace_lock_fd is None  # noqa: SLF001
                and repeat_stopped.get("output_dir") != stopped.get("output_dir")
                and first_session_path.is_file()
                and repeat_session_path.is_file()
                and first_session_payload.get("meeting_id") == meeting_id
                and repeat_session_payload.get("meeting_id") == repeat_meeting_id,
                {
                    "first": stopped.get("output_dir"),
                    "repeat": repeat_stopped.get("output_dir"),
                    "first_meeting_id": meeting_id,
                    "repeat_meeting_id": repeat_meeting_id,
                    "first_session_exists": first_session_path.is_file(),
                    "repeat_session_exists": repeat_session_path.is_file(),
                    "first_session_meeting_id": first_session_payload.get("meeting_id"),
                    "repeat_session_meeting_id": repeat_session_payload.get("meeting_id"),
                    "repeat_status": repeat_stopped.get("status"),
                    "repeat_error": repeat_stopped.get("error"),
                    "repeat_events": repeat_stopped.get("task_payload", {}).get("events") if isinstance(repeat_stopped.get("task_payload"), dict) else None,
                    "workspace_lock_fd": provider._workspace_lock_fd,  # noqa: SLF001
                },
            )
            secret_title_started = provider.start_realtime_meeting(
                title="protocol secret title token=title-token password=title-password",
                participants=["Alice password=participant-password"],
                max_seconds=30,
            )
            secret_title_meeting_id = str(secret_title_started["meeting_id"])
            secret_title_realtime = FakeTingWuRealtime.instances[-1]
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if provider.session_status(secret_title_meeting_id).get("final_count"):
                    break
                time.sleep(0.05)
            assert_ok(
                "secret-title realtime stream sent expected audio frames before stop",
                wait_for_fake_realtime_frames(secret_title_realtime, 2),
                {"frames": len(secret_title_realtime.audio_frames), "meeting_id": secret_title_meeting_id, "status": provider.session_status(secret_title_meeting_id)},
            )
            secret_title_stopped = stop_and_wait(provider, secret_title_meeting_id, wait_seconds=3, timeout=5)
            secret_title_session_text = json.dumps(secret_title_stopped, ensure_ascii=False)
            secret_title_audit_text = config.audit_log_path.read_text(encoding="utf-8")
            assert_ok(
                "Meeting title and participants do not leak into provider ids, paths, session, or audit",
                "title-token" not in secret_title_meeting_id
                and "title-password" not in secret_title_meeting_id
                and "participant-password" not in secret_title_meeting_id
                and "title-token" not in str(secret_title_stopped.get("output_dir") or "")
                and "title-password" not in str(secret_title_stopped.get("output_dir") or "")
                and "participant-password" not in str(secret_title_stopped.get("output_dir") or "")
                and "title-token" not in secret_title_session_text
                and "title-password" not in secret_title_session_text
                and "participant-password" not in secret_title_session_text
                and "title-token" not in secret_title_audit_text
                and "title-password" not in secret_title_audit_text
                and "participant-password" not in secret_title_audit_text,
                {
                    "meeting_id": secret_title_meeting_id,
                    "output_dir": secret_title_stopped.get("output_dir"),
                    "session": secret_title_session_text,
                    "audit": secret_title_audit_text,
                },
            )
            assert_ok("api key sent only as bearer header", all(item.get("Authorization") == "Bearer test-key" for item in fake_http.headers), fake_http.headers)
            provider._emit(
                meeting_id,
                "secret_error",
                {
                    "error": (
                        "Authorization: Bearer should-not-persist token=leaky-token password=hunter2 signature=abc123 "
                        "apiKeyValue=camel-error-key clientSecret=camel-error-secret dashscopeToken=camel-error-token "
                        "sk-provider-redaction-canary"
                    ),
                    "authorization": "Bearer should-not-persist",
                    "nested": {
                        "api_key": "should-not-persist",
                        "tingwu_api_key": "prefixed-api-key",
                        "apiKeyValue": "camel-api-key",
                        "clientSecret": "client-secret-value",
                        "dashscope-token": "dashscope-token-value",
                        "authorizationHeader": "Bearer prefixed-authorization",
                        "signature_value": "signature-value",
                    },
                },
            )
            secret_status = provider.session_status(meeting_id)
            secret_payload = json.dumps(secret_status.get("task_payload", {}).get("events", []), ensure_ascii=False)
            secret_drained = json.dumps(provider.drain_events(meeting_id, limit=20), ensure_ascii=False)
            assert_ok(
                "provider event payloads redact credentials before persistence",
                "should-not-persist" not in secret_payload
                and "leaky-token" not in secret_payload
                and "hunter2" not in secret_payload
                and "abc123" not in secret_payload
                and "camel-error-key" not in secret_payload
                and "camel-error-secret" not in secret_payload
                and "camel-error-token" not in secret_payload
                and "sk-provider-redaction-canary" not in secret_payload
                and "prefixed-api-key" not in secret_payload
                and "camel-api-key" not in secret_payload
                and "client-secret-value" not in secret_payload
                and "dashscope-token-value" not in secret_payload
                and "prefixed-authorization" not in secret_payload
                and "signature-value" not in secret_payload
                and "should-not-persist" not in secret_drained
                and "camel-error-key" not in secret_drained
                and "camel-error-secret" not in secret_drained
                and "camel-error-token" not in secret_drained
                and "sk-provider-redaction-canary" not in secret_drained
                and "prefixed-api-key" not in secret_drained
                and "camel-api-key" not in secret_drained
                and "client-secret-value" not in secret_drained
                and "dashscope-token-value" not in secret_drained
                and "prefixed-authorization" not in secret_drained
                and "signature-value" not in secret_drained,
                {"persisted": secret_payload, "drained": secret_drained},
            )
            provider._emit(meeting_id, "large_payload", {"blob": "x" * 5000, "items": list(range(40))})
            session_obj = provider._sessions[meeting_id]  # noqa: SLF001
            for index in range(80):
                session_obj.task_payload.setdefault("ai_result_events", [])
                callback = TingwuRealtimeCallback(provider, meeting_id)
                callback.on_ai_result({"payload": {"output": {"action": "ai-result", "text": f"{index}-" + ("y" * 5000)}}})
            bounded_status = provider.session_status(meeting_id)
            bounded_payload = bounded_status.get("task_payload", {}) if isinstance(bounded_status.get("task_payload"), dict) else {}
            bounded_events = bounded_payload.get("events") if isinstance(bounded_payload.get("events"), list) else []
            bounded_ai_events = bounded_payload.get("ai_result_events") if isinstance(bounded_payload.get("ai_result_events"), list) else []
            bounded_text = json.dumps({"events": bounded_events, "ai": bounded_ai_events}, ensure_ascii=False)
            assert_ok(
                "provider realtime events and ai events are bounded before persistence",
                len(bounded_events) <= tingwu_module.MAX_TINGWU_PROVIDER_EVENTS
                and len(bounded_ai_events) == tingwu_module.MAX_TINGWU_AI_EVENTS
                and "truncated" in bounded_text
                and "x" * 3000 not in bounded_text
                and "y" * 3000 not in bounded_text,
                {"events": len(bounded_events), "ai": len(bounded_ai_events), "sample": bounded_text[:1000]},
            )

            stash_event = {
                "header": {"event": "result-generated"},
                "payload": {
                    "output": {
                        "action": "recognize-result",
                        "transcription": {
                            "speakerId": "7",
                            "sentenceEnd": True,
                            "stashResult": {"words": [{"text": "缓存"}, {"word": "转写"}]},
                        },
                        "translations": {
                            "translations": {
                                "en": {"words": [{"text": "translated"}, {"text": " text"}]},
                            }
                        },
                    }
                },
            }
            assert_ok(
                "Tingwu realtime parser handles stashResult words and speakerId",
                tingwu_module.extract_transcript_text(stash_event) == "缓存转写"
                and tingwu_module.extract_speaker(stash_event) == "7"
                and tingwu_module.is_final_transcript(stash_event),
                stash_event,
            )
            translation_only_event = {
                "payload": {
                    "output": {
                        "action": "recognize-result",
                        "translations": {
                            "translations": {
                                "en": {"words": [{"text": "translation"}, {"value": " fallback"}]},
                            }
                        },
                    }
                }
            }
            assert_ok(
                "Tingwu realtime parser falls back to nested translations",
                tingwu_module.extract_transcript_text(translation_only_event) == "translationfallback",
                translation_only_event,
            )
            assert_ok(
                "Tingwu task status parser handles common API status fields",
                provider._task_status({"output": {"status": 0}}) == "completed"  # noqa: SLF001
                and provider._task_status({"output": {"task_status": "SUCCEEDED"}}) == "completed"  # noqa: SLF001
                and provider._task_status({"output": {"taskStatus": "FAILED"}}) == "failed"  # noqa: SLF001
                and provider._task_status({"TaskStatus": "RUNNING"}) == "running",  # noqa: SLF001
                "",
            )

            realtime_status = provider.session_status(meeting_id)
            assert_ok(
                "websocket audio frames sent",
                len(realtime.audio_frames) >= 1
                and realtime_status.get("websocket_audio_frames") == len(realtime.audio_frames),
                {"sdk_frames": len(realtime.audio_frames), "session": realtime_status},
            )
            start = realtime.text_frames[0]
            assert_ok(
                "run-task protocol sent",
                start["header"]["action"] == "run-task"
                and len(start["header"]["task_id"]) == 16
                and start["payload"]["input"]["appId"] == "tw_test_app"
                and start["payload"]["input"]["dataId"] == "fake-data-id"
                and start["payload"]["input"]["directive"] == "start"
                and start["payload"]["parameters"]["format"] == "pcm"
                and start["payload"]["parameters"]["sampleRate"] == 16000,
                start,
            )
            finish = realtime.text_frames[-1]
            assert_ok(
                "finish-task protocol sent",
                finish["header"]["action"] == "finish-task"
                and len(finish["header"]["task_id"]) == 16
                and finish["payload"]["input"]["appId"] == "tw_test_app"
                and "dataId" not in finish["payload"]["input"]
                and finish["payload"]["input"]["directive"] == "stop",
                finish,
            )

            gain_config = OfficeAgentConfig(
                workspace_dir=root / "pcm_gain_workspace",
                audit_log_path=root / "pcm_gain_audit.jsonl",
                allowed_roots=(root / "pcm_gain_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
                tingwu_pcm_gain=2.0,
            )
            gain_provider = TingwuMeetingProvider(
                gain_config,
                Workspace(gain_config.workspace_dir, gain_config.allowed_roots, AuditLogger(gain_config.audit_log_path)),
                AuditLogger(gain_config.audit_log_path),
            )
            gain_realtime_count = len(FakeTingWuRealtime.instances)
            gain_started = gain_provider.start_realtime_meeting(title="pcm gain smoke", participants=["Alice"], max_seconds=30)
            gain_meeting_id = str(gain_started["meeting_id"])
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and len(FakeTingWuRealtime.instances) <= gain_realtime_count:
                time.sleep(0.05)
            gain_realtime = FakeTingWuRealtime.instances[gain_realtime_count]
            assert_ok(
                "Realtime PCM gain amplifies streamed audio",
                wait_for_fake_realtime_frames(gain_realtime, 1)
                and gain_realtime.audio_frames[0] == tingwu_module.amplify_pcm16(b"\x01\x00" * 1600, 2.0)
                and gain_started.get("task_payload", {}).get("pcm_gain") == 2.0
                and gain_provider.status().get("pcm_gain") == 2.0,
                {"frames": len(gain_realtime.audio_frames), "started": gain_started, "status": gain_provider.status()},
            )
            gain_stopped = stop_and_wait(gain_provider, gain_meeting_id, wait_seconds=3, timeout=5)
            assert_ok(
                "Realtime PCM gain updates saved audio metrics",
                gain_stopped.get("audio_rms", 0) >= 2
                and gain_stopped.get("audio_peak", 0) >= 2
                and gain_stopped.get("audio_rms", 0) >= stopped.get("audio_rms", 0)
                and gain_stopped.get("audio_peak", 0) >= stopped.get("audio_peak", 0),
                {"gain": gain_stopped, "baseline": stopped},
            )

            FakeTingWuRealtime.fail_stop_once = True
            realtime_count_before_fallback = len(FakeTingWuRealtime.instances)
            fallback_session = provider.start_realtime_meeting(title="fallback stop smoke", participants=["Alice"], max_seconds=30)
            fallback_meeting_id = str(fallback_session["meeting_id"])
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and len(FakeTingWuRealtime.instances) <= realtime_count_before_fallback:
                time.sleep(0.05)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if provider.session_status(fallback_meeting_id).get("final_count"):
                    break
                time.sleep(0.05)
            fallback_realtime = FakeTingWuRealtime.instances[realtime_count_before_fallback]
            assert_ok(
                "fallback realtime stream sent expected audio frames before stop",
                wait_for_fake_realtime_frames(fallback_realtime, 2),
                {"frames": len(fallback_realtime.audio_frames), "meeting_id": fallback_meeting_id, "status": provider.session_status(fallback_meeting_id)},
            )
            fallback_stopped = stop_and_wait(provider, fallback_meeting_id, wait_seconds=3, timeout=5)
            fallback_finish = fallback_realtime.text_frames[-1]
            assert_ok(
                "custom finish-task fallback runs when sdk stop fails",
                fallback_stopped["status"] == "stopped"
                and fallback_finish["header"]["action"] == "finish-task"
                and fallback_finish["payload"]["input"]["appId"] == "tw_test_app"
                and fallback_finish["payload"]["input"]["dataId"] == "fake-data-id"
                and fallback_finish["payload"]["input"]["directive"] == "stop",
                fallback_finish,
            )

            audit_text = audit_path.read_text(encoding="utf-8")
            assert_ok("audit contains tingwu lifecycle", all(action in audit_text for action in ("tingwu.meeting_start", "tingwu.audio_save", "tingwu.meeting_finalize")), audit_text)

            bad_config = OfficeAgentConfig(
                workspace_dir=root / "bad_workspace",
                audit_log_path=root / "bad_audit.jsonl",
                allowed_roots=(root / "bad_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="missing-mic",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            bad_provider = TingwuMeetingProvider(
                bad_config,
                Workspace(bad_config.workspace_dir, bad_config.allowed_roots, AuditLogger(bad_config.audit_log_path)),
                AuditLogger(bad_config.audit_log_path),
            )
            bad_status = bad_provider.status()
            assert_ok(
                "provider status is unavailable when microphone is not ready",
                bad_status["status"] == "unavailable"
                and bad_status["configured"] is True
                and bad_status["mic_status"] == "unavailable",
                bad_status,
            )
            placeholder_config = OfficeAgentConfig(
                workspace_dir=root / "placeholder_workspace",
                audit_log_path=root / "placeholder_audit.jsonl",
                allowed_roots=(root / "placeholder_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="default",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            placeholder_provider = TingwuMeetingProvider(
                placeholder_config,
                Workspace(placeholder_config.workspace_dir, placeholder_config.allowed_roots, AuditLogger(placeholder_config.audit_log_path)),
                AuditLogger(placeholder_config.audit_log_path),
            )
            placeholder_status = placeholder_provider.status()
            posts_before_placeholder = len(fake_http.posts)
            try:
                placeholder_provider.start_realtime_meeting(title="placeholder mic", participants=["Alice"], max_seconds=30)
            except Exception as exc:
                placeholder_error = str(exc)
            else:
                raise AssertionError("placeholder microphone should fail before CreateTask")
            assert_ok(
                "provider rejects unresolved ALSA placeholder microphone before CreateTask",
                placeholder_status["status"] == "unavailable"
                and placeholder_status["mic_status"] == "unavailable"
                and placeholder_status["selected_mic_device"] == "default"
                and "unresolved ALSA placeholder" in str(placeholder_status.get("message") or placeholder_status.get("mic_probe"))
                and "unresolved ALSA placeholder" in placeholder_error
                and len(fake_http.posts) == posts_before_placeholder,
                {"status": placeholder_status, "error": placeholder_error, "posts": fake_http.posts[posts_before_placeholder:]},
            )
            alias_config = OfficeAgentConfig(
                workspace_dir=root / "alias_workspace",
                audit_log_path=root / "alias_audit.jsonl",
                allowed_roots=(root / "alias_workspace",),
                dashscope_api_key="dashscope-alias-key",
                tingwu_api_key="",
                tingwu_app_id="tw_alias_app",
                tingwu_mock=True,
            ).normalized()
            alias_provider = TingwuMeetingProvider(
                alias_config,
                Workspace(alias_config.workspace_dir, alias_config.allowed_roots, AuditLogger(alias_config.audit_log_path)),
                AuditLogger(alias_config.audit_log_path),
            )
            alias_status = alias_provider.status()
            assert_ok(
                "normalized config accepts DASHSCOPE_API_KEY as Tingwu API key alias",
                alias_config.tingwu_api_key == "dashscope-alias-key"
                and alias_status.get("configured") is True
                and alias_status.get("api_key_configured") is True,
                alias_status,
            )
            old_env = {key: os.environ.get(key) for key in ("DASHSCOPE_API_KEY", "TINGWU_APP_ID", "TINGWU_MEETING_APP_ID")}
            try:
                os.environ["DASHSCOPE_API_KEY"] = "env-dashscope-alias"
                os.environ.pop("TINGWU_APP_ID", None)
                os.environ["TINGWU_MEETING_APP_ID"] = "tw_env_meeting_app_alias"
                env_alias_config = OfficeAgentConfig(
                    workspace_dir=root / "env_alias_workspace",
                    audit_log_path=root / "env_alias_audit.jsonl",
                    allowed_roots=(root / "env_alias_workspace",),
                    dashscope_api_key="",
                    tingwu_api_key="",
                    tingwu_app_id="",
                    tingwu_mock=True,
                ).normalized()
            finally:
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            assert_ok(
                "normalized config falls back to env aliases when explicit Tingwu fields are empty",
                env_alias_config.tingwu_api_key == "env-dashscope-alias"
                and env_alias_config.tingwu_app_id == "tw_env_meeting_app_alias",
                {"api": env_alias_config.tingwu_api_key, "app": env_alias_config.tingwu_app_id},
            )
            placeholder_credential_config = OfficeAgentConfig(
                workspace_dir=root / "placeholder_credential_workspace",
                audit_log_path=root / "placeholder_credential_audit.jsonl",
                allowed_roots=(root / "placeholder_credential_workspace",),
                tingwu_api_key="replace_with_new_rotated_key",
                tingwu_app_id="replace_with_bailian_app_id",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
            ).normalized()
            placeholder_credential_provider = TingwuMeetingProvider(
                placeholder_credential_config,
                Workspace(placeholder_credential_config.workspace_dir, placeholder_credential_config.allowed_roots, AuditLogger(placeholder_credential_config.audit_log_path)),
                AuditLogger(placeholder_credential_config.audit_log_path),
            )
            placeholder_credential_status = placeholder_credential_provider.status()
            posts_before_placeholder_credentials = len(fake_http.posts)
            try:
                placeholder_credential_provider.start_realtime_meeting(title="placeholder credentials", participants=["Alice"], max_seconds=30)
                raise AssertionError("placeholder credentials should fail before CreateTask")
            except Exception as exc:
                placeholder_credential_error = str(exc)
            assert_ok(
                "provider treats Tingwu template credential values as missing config",
                placeholder_credential_config.tingwu_api_key == ""
                and placeholder_credential_config.tingwu_app_id == ""
                and placeholder_credential_status.get("status") == "needs_config"
                and "TINGWU_API_KEY or DASHSCOPE_API_KEY" in placeholder_credential_error
                and len(fake_http.posts) == posts_before_placeholder_credentials,
                {
                    "config": {
                        "api": placeholder_credential_config.tingwu_api_key,
                        "app": placeholder_credential_config.tingwu_app_id,
                    },
                    "status": placeholder_credential_status,
                    "error": placeholder_credential_error,
                    "posts": fake_http.posts[posts_before_placeholder_credentials:],
                },
            )
            wrong_credential_config = OfficeAgentConfig(
                workspace_dir=root / "wrong_credential_workspace",
                audit_log_path=root / "wrong_credential_audit.jsonl",
                allowed_roots=(root / "wrong_credential_workspace",),
                tingwu_api_key="LTAI_example_wrong_access_key_id",
                tingwu_app_id="AppKey example_wrong_tingwu_openapi_project_key",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
            ).normalized()
            wrong_credential_provider = TingwuMeetingProvider(
                wrong_credential_config,
                Workspace(wrong_credential_config.workspace_dir, wrong_credential_config.allowed_roots, AuditLogger(wrong_credential_config.audit_log_path)),
                AuditLogger(wrong_credential_config.audit_log_path),
            )
            wrong_credential_status = wrong_credential_provider.status()
            posts_before_wrong_credentials = len(fake_http.posts)
            try:
                wrong_credential_provider.start_realtime_meeting(title="wrong credentials", participants=["Alice"], max_seconds=30)
                raise AssertionError("wrong credential kinds should fail before CreateTask")
            except Exception as exc:
                wrong_credential_error = str(exc)
            assert_ok(
                "provider rejects AccessKey/AppKey-shaped Tingwu misconfiguration before cloud calls",
                wrong_credential_config.tingwu_api_key == ""
                and wrong_credential_config.tingwu_app_id == ""
                and wrong_credential_status.get("status") == "needs_config"
                and isinstance(wrong_credential_status.get("credential_diagnostics"), dict)
                and wrong_credential_status["credential_diagnostics"].get("api_key_kind") == "aliyun_access_key_id"
                and wrong_credential_status["credential_diagnostics"].get("app_id_kind") == "legacy_tingwu_appkey"
                and "TINGWU_API_KEY or DASHSCOPE_API_KEY" in wrong_credential_error
                and len(fake_http.posts) == posts_before_wrong_credentials,
                {
                    "status": wrong_credential_status,
                    "error": wrong_credential_error,
                    "posts": fake_http.posts[posts_before_wrong_credentials:],
                },
            )
            bare_appkey_config = OfficeAgentConfig(
                workspace_dir=root / "bare_appkey_workspace",
                audit_log_path=root / "bare_appkey_audit.jsonl",
                allowed_roots=(root / "bare_appkey_workspace",),
                tingwu_api_key="valid-dashscope-like-key",
                tingwu_app_id="legacy_openapi_project_key_without_tw_prefix",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
            ).normalized()
            bare_appkey_provider = TingwuMeetingProvider(
                bare_appkey_config,
                Workspace(bare_appkey_config.workspace_dir, bare_appkey_config.allowed_roots, AuditLogger(bare_appkey_config.audit_log_path)),
                AuditLogger(bare_appkey_config.audit_log_path),
            )
            bare_appkey_status = bare_appkey_provider.status()
            posts_before_bare_appkey = len(fake_http.posts)
            try:
                bare_appkey_provider.start_realtime_meeting(title="bare appkey", participants=["Alice"], max_seconds=30)
                raise AssertionError("unexpected app id shape should fail before CreateTask")
            except Exception as exc:
                bare_appkey_error = str(exc)
            assert_ok(
                "provider rejects App ID values that do not use the tw_ shape before cloud calls",
                bare_appkey_config.tingwu_api_key == "valid-dashscope-like-key"
                and bare_appkey_config.tingwu_app_id == ""
                and bare_appkey_config.tingwu_app_id_kind == "unexpected_app_id_shape"
                and bare_appkey_status.get("status") == "needs_config"
                and "TINGWU_APP_ID or TINGWU_MEETING_APP_ID" in bare_appkey_error
                and len(fake_http.posts) == posts_before_bare_appkey,
                {
                    "status": bare_appkey_status,
                    "error": bare_appkey_error,
                    "posts": fake_http.posts[posts_before_bare_appkey:],
                },
            )
            missing_config = OfficeAgentConfig(
                workspace_dir=root / "missing_config_workspace",
                audit_log_path=root / "missing_config_audit.jsonl",
                allowed_roots=(root / "missing_config_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
            )
            missing_config_provider = TingwuMeetingProvider(
                missing_config,
                Workspace(missing_config.workspace_dir, missing_config.allowed_roots, AuditLogger(missing_config.audit_log_path)),
                AuditLogger(missing_config.audit_log_path),
            )
            missing_config_status = missing_config_provider.status()
            try:
                missing_config_provider.start_realtime_meeting(title="missing config", participants=["Alice"], max_seconds=30)
                raise AssertionError("start should fail when Tingwu credentials are missing")
            except Exception as exc:
                missing_config_error = str(exc)
            assert_ok(
                "provider missing-config guidance includes supported app id aliases",
                missing_config_status.get("status") == "needs_config"
                and "TINGWU_APP_ID/TINGWU_MEETING_APP_ID" in str(missing_config_status.get("message") or "")
                and "TINGWU_APP_ID or TINGWU_MEETING_APP_ID" in missing_config_error,
                {"status": missing_config_status, "error": missing_config_error},
            )
            secret_url_config = OfficeAgentConfig(
                workspace_dir=root / "secret_url_workspace",
                audit_log_path=root / "secret_url_audit.jsonl",
                allowed_roots=(root / "secret_url_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url="https://user:pass@dashscope.example.test/generation?token=leaky-token",
                tingwu_ws_url="wss://user:pass@dashscope.example.test/inference?signature=leaky-signature",
                tingwu_mock=False,
                mic_device="missing-mic",
            )
            secret_url_provider = TingwuMeetingProvider(
                secret_url_config,
                Workspace(secret_url_config.workspace_dir, secret_url_config.allowed_roots, AuditLogger(secret_url_config.audit_log_path)),
                AuditLogger(secret_url_config.audit_log_path),
            )
            secret_url_status = secret_url_provider.status()
            secret_url_payload = json.dumps(secret_url_status, ensure_ascii=False)
            assert_ok(
                "provider status redacts Tingwu URL credentials and query strings",
                "leaky-token" not in secret_url_payload
                and "leaky-signature" not in secret_url_payload
                and "user:pass" not in secret_url_payload
                and secret_url_status.get("http_url") == "https://dashscope.example.test/generation"
                and secret_url_status.get("ws_url") == "wss://dashscope.example.test/inference",
                secret_url_status,
            )
            posts_before_bad_mic = len(fake_http.posts)
            try:
                bad_provider.start_realtime_meeting(title="bad mic", participants=["Alice"], max_seconds=30)
            except Exception as exc:
                assert_ok("microphone preflight blocks before CreateTask", "Microphone is not ready" in str(exc) and len(fake_http.posts) == posts_before_bad_mic, str(exc))
            else:
                raise AssertionError("microphone preflight failed: bad mic unexpectedly started")

            blocked_capture_config = OfficeAgentConfig(
                workspace_dir=root / "blocked_capture_workspace",
                audit_log_path=root / "blocked_capture_audit.jsonl",
                allowed_roots=(root / "blocked_capture_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="blocked-capture",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            blocked_capture_provider = TingwuMeetingProvider(
                blocked_capture_config,
                Workspace(blocked_capture_config.workspace_dir, blocked_capture_config.allowed_roots, AuditLogger(blocked_capture_config.audit_log_path)),
                AuditLogger(blocked_capture_config.audit_log_path),
            )
            blocked_capture_provider.microphone_probe = lambda: {  # type: ignore[method-assign]
                "status": "available",
                "configured_device": "blocked-capture",
                "selected_device": "blocked-capture",
                "configured_device_valid": True,
            }
            posts_before_blocked_capture = len(fake_http.posts)
            try:
                blocked_capture_provider.start_realtime_meeting(title="blocked capture", participants=["Alice"], max_seconds=30)
            except TingwuMeetingError as exc:
                capture_details = exc.details
                capture_probe = capture_details.get("capture_probe") if isinstance(capture_details.get("capture_probe"), dict) else {}
                mic_probe = capture_details.get("mic_probe") if isinstance(capture_details.get("mic_probe"), dict) else {}
                assert_ok(
                    "capture preflight failure keeps structured diagnostics before CreateTask",
                    "capture preflight failed" in str(exc).lower()
                    and len(fake_http.posts) == posts_before_blocked_capture
                    and capture_probe.get("status") == "unavailable"
                    and capture_probe.get("selected_device") == "blocked-capture"
                    and mic_probe.get("capture_probe") == capture_probe,
                    {"error": str(exc), "details": capture_details, "posts": fake_http.posts[posts_before_blocked_capture:]},
                )
            else:
                raise AssertionError("capture preflight failed: blocked capture unexpectedly started")

            silent_capture_config = OfficeAgentConfig(
                workspace_dir=root / "silent_capture_workspace",
                audit_log_path=root / "silent_capture_audit.jsonl",
                allowed_roots=(root / "silent_capture_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="silent-capture",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            silent_capture_provider = TingwuMeetingProvider(
                silent_capture_config,
                Workspace(silent_capture_config.workspace_dir, silent_capture_config.allowed_roots, AuditLogger(silent_capture_config.audit_log_path)),
                AuditLogger(silent_capture_config.audit_log_path),
            )
            silent_capture_provider.microphone_probe = lambda: {  # type: ignore[method-assign]
                "status": "available",
                "configured_device": "silent-capture",
                "selected_device": "silent-capture",
                "configured_device_valid": True,
            }
            posts_before_silent_capture = len(fake_http.posts)
            original_silent_capture_probe = tingwu_module.preflight_arecord_capture

            def silent_preflight_arecord_capture(device: str, sample_rate: int, duration_seconds: int = 1) -> dict[str, Any]:
                return {
                    "status": "available",
                    "selected_device": device,
                    "sample_rate": sample_rate,
                    "duration_seconds": duration_seconds,
                    "audio_bytes": sample_rate * 2 * max(1, int(duration_seconds)),
                    "audio_rms": 0,
                    "audio_peak": 0,
                    "message": "ready",
                }

            try:
                tingwu_module.preflight_arecord_capture = silent_preflight_arecord_capture
                silent_capture_provider.start_realtime_meeting(title="silent capture", participants=["Alice"], max_seconds=30)
            except TingwuMeetingError as exc:
                silent_details = exc.details
                silent_capture_probe = silent_details.get("capture_probe") if isinstance(silent_details.get("capture_probe"), dict) else {}
                assert_ok(
                    "silent capture preflight preserves audio level diagnostics before CreateTask",
                    "produced no audio signal" in str(exc)
                    and len(fake_http.posts) == posts_before_silent_capture
                    and silent_capture_probe.get("status") == "available"
                    and silent_capture_probe.get("audio_bytes", 0) > 0
                    and silent_capture_probe.get("audio_rms") == 0
                    and silent_capture_probe.get("audio_peak") == 0,
                    {"error": str(exc), "details": silent_details, "posts": fake_http.posts[posts_before_silent_capture:]},
                )
            finally:
                tingwu_module.preflight_arecord_capture = original_silent_capture_probe

            retry_ws_config = OfficeAgentConfig(
                workspace_dir=root / "retry_ws_workspace",
                audit_log_path=root / "retry_ws_audit.jsonl",
                allowed_roots=(root / "retry_ws_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            retry_ws_provider = TingwuMeetingProvider(
                retry_ws_config,
                Workspace(retry_ws_config.workspace_dir, retry_ws_config.allowed_roots, AuditLogger(retry_ws_config.audit_log_path)),
                AuditLogger(retry_ws_config.audit_log_path),
            )
            retry_realtime_count = len(FakeTingWuRealtime.instances)
            FakeTingWuRealtime.fail_start_close_once = True
            retry_started = retry_ws_provider.start_realtime_meeting(title="retry websocket", participants=["Alice"], max_seconds=30)
            retry_meeting_id = str(retry_started["meeting_id"])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and len(FakeTingWuRealtime.instances) < retry_realtime_count + 2:
                time.sleep(0.05)
            retry_realtime = FakeTingWuRealtime.instances[-1]
            assert_ok(
                "Realtime stream retries one startup websocket close before audio",
                len(FakeTingWuRealtime.instances) >= retry_realtime_count + 2
                and wait_for_fake_realtime_frames(retry_realtime, 2)
                and retry_ws_provider.session_status(retry_meeting_id).get("websocket_audio_frames", 0) >= 2,
                {"instances": len(FakeTingWuRealtime.instances), "status": retry_ws_provider.session_status(retry_meeting_id)},
            )
            retry_stopped = stop_and_wait(retry_ws_provider, retry_meeting_id, wait_seconds=3, timeout=5)
            retry_events = retry_stopped.get("task_payload", {}).get("events", []) if isinstance(retry_stopped.get("task_payload"), dict) else []
            retry_event_names = [str(item.get("event") or "") for item in retry_events if isinstance(item, dict)]
            assert_ok(
                "Realtime websocket retry records attempt diagnostics",
                "websocket_stream_attempt_failed" in retry_event_names and retry_stopped.get("status") == "stopped",
                {"events": retry_events, "status": retry_stopped},
            )

            slow_connect_config = OfficeAgentConfig(
                workspace_dir=root / "slow_connect_ws_workspace",
                audit_log_path=root / "slow_connect_ws_audit.jsonl",
                allowed_roots=(root / "slow_connect_ws_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            slow_connect_provider = TingwuMeetingProvider(
                slow_connect_config,
                Workspace(slow_connect_config.workspace_dir, slow_connect_config.allowed_roots, AuditLogger(slow_connect_config.audit_log_path)),
                AuditLogger(slow_connect_config.audit_log_path),
            )
            slow_realtime_count = len(FakeTingWuRealtime.instances)
            tingwu_module.TingWuRealtime = FakeSlowConnectTingWuRealtime
            try:
                slow_started = slow_connect_provider.start_realtime_meeting(title="slow websocket connect", participants=["Alice"], max_seconds=30)
                slow_meeting_id = str(slow_started["meeting_id"])
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and len(FakeTingWuRealtime.instances) <= slow_realtime_count:
                    time.sleep(0.05)
                slow_realtime = FakeTingWuRealtime.instances[slow_realtime_count]
                assert_ok(
                    "Realtime stream waits for websocket open before sending start request",
                    isinstance(slow_realtime, FakeSlowConnectTingWuRealtime)
                    and getattr(slow_realtime, "connected_api_key", "") == "test-key"
                    and wait_for_fake_realtime_frames(slow_realtime, 2)
                    and slow_connect_provider.session_status(slow_meeting_id).get("websocket_audio_frames", 0) >= 2,
                    {"realtime": type(slow_realtime).__name__, "status": slow_connect_provider.session_status(slow_meeting_id)},
                )
                slow_stopped = stop_and_wait(slow_connect_provider, slow_meeting_id, wait_seconds=3, timeout=5)
                assert_ok("Realtime slow websocket connect stops cleanly", slow_stopped.get("status") == "stopped", slow_stopped)
            finally:
                tingwu_module.TingWuRealtime = FakeTingWuRealtime

            empty_audio_config = OfficeAgentConfig(
                workspace_dir=root / "empty_audio_workspace",
                audit_log_path=root / "empty_audio_audit.jsonl",
                allowed_roots=(root / "empty_audio_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="empty-mic",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            empty_audio_provider = TingwuMeetingProvider(
                empty_audio_config,
                Workspace(empty_audio_config.workspace_dir, empty_audio_config.allowed_roots, AuditLogger(empty_audio_config.audit_log_path)),
                AuditLogger(empty_audio_config.audit_log_path),
            )
            posts_before_empty_audio = len(fake_http.posts)
            empty_audio_started = empty_audio_provider.start_realtime_meeting(title="empty audio", participants=["Alice"], max_seconds=30)
            empty_audio_meeting_id = str(empty_audio_started["meeting_id"])
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                empty_audio_status = empty_audio_provider.session_status(empty_audio_meeting_id)
                if empty_audio_status.get("status") == "failed":
                    break
                time.sleep(0.05)
            empty_audio_result = stop_and_wait(empty_audio_provider, empty_audio_meeting_id, wait_seconds=3, timeout=5)
            empty_audio_posts = [
                post.get("input", {})
                for post in fake_http.posts[posts_before_empty_audio:]
                if isinstance(post.get("input"), dict)
            ]
            empty_audio_path = Path(str(empty_audio_result.get("audio_path") or ""))
            assert_ok(
                "Empty microphone stream fails with diagnostic artifacts before AI minutes fetch",
                empty_audio_result.get("status") == "failed"
                and "No microphone audio frames were captured" in str(empty_audio_result.get("error") or "")
                and empty_audio_result.get("audio_seconds") == 0
                and empty_audio_path.is_file()
                and not any(call.get("task") == "getTask" for call in empty_audio_posts),
                {"result": empty_audio_result, "posts": empty_audio_posts, "audio_path": str(empty_audio_path)},
            )

            fake_http.get_task_status = 1
            timeout_config = OfficeAgentConfig(
                workspace_dir=root / "timeout_workspace",
                audit_log_path=root / "timeout_audit.jsonl",
                allowed_roots=(root / "timeout_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            timeout_provider = TingwuMeetingProvider(
                timeout_config,
                Workspace(timeout_config.workspace_dir, timeout_config.allowed_roots, AuditLogger(timeout_config.audit_log_path)),
                AuditLogger(timeout_config.audit_log_path),
            )
            timeout_session = timeout_provider.start_realtime_meeting(title="timeout minutes", participants=["Alice"], max_seconds=30)
            timeout_meeting_id = str(timeout_session["meeting_id"])
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if timeout_provider.session_status(timeout_meeting_id).get("final_count"):
                    break
                time.sleep(0.05)
            timeout_realtime = FakeTingWuRealtime.instances[-1]
            assert_ok(
                "timeout realtime stream sent expected audio frames before stop",
                wait_for_fake_realtime_frames(timeout_realtime, 2),
                {"frames": len(timeout_realtime.audio_frames), "meeting_id": timeout_meeting_id, "status": timeout_provider.session_status(timeout_meeting_id)},
            )
            original_fetch = timeout_provider.fetch_ai_minutes
            timeout_provider.fetch_ai_minutes = lambda task_id, timeout_seconds=60, interval_seconds=2.0: original_fetch(task_id, timeout_seconds=1, interval_seconds=0.1)  # type: ignore[method-assign]
            timeout_stopped = stop_and_wait(timeout_provider, timeout_meeting_id, wait_seconds=3, timeout=5)
            timeout_result = timeout_provider.finalize_meeting(timeout_meeting_id)
            assert_ok(
                "AI minutes timeout is not completed after explicit finalize",
                timeout_stopped["status"] == "stopped"
                and timeout_result["status"] == "failed"
                and timeout_result.get("minutes_path")
                and Path(str(timeout_result["minutes_path"])).is_file()
                and timeout_result.get("ai_minutes", {}).get("status") == "timeout",
                {"stopped": timeout_stopped, "final": timeout_result},
            )
            fake_http.get_task_status = 0
            restart_provider = TingwuMeetingProvider(
                timeout_config,
                Workspace(timeout_config.workspace_dir, timeout_config.allowed_roots, AuditLogger(timeout_config.audit_log_path)),
                AuditLogger(timeout_config.audit_log_path),
            )
            retry_result = restart_provider.finalize_meeting(timeout_meeting_id, retry_failed_minutes=True)
            assert_ok(
                "AI minutes fetch can retry after timeout and provider restart",
                retry_result["status"] == "completed"
                and retry_result.get("ai_minutes", {}).get("output", {}).get("summarizationPathData")
                and Path(str(retry_result["minutes_path"])).is_file()
                and "真实模式会议摘要" in Path(str(retry_result["minutes_path"])).read_text(encoding="utf-8"),
                retry_result,
            )
            retry_task_calls = [post.get("input", {}) for post in fake_http.posts if isinstance(post.get("input"), dict)]
            assert_ok(
                "AI minutes GetTask uses minutes task dataId returned by CreateTask",
                any(call.get("task") == "createTask" and call.get("dataId") == "fake-data-id" for call in retry_task_calls)
                and any(call.get("task") == "getTask" and call.get("dataId") == "fake-minutes-data-id" for call in retry_task_calls)
                and not any(
                    call.get("task") == "getTask" and call.get("dataId") == "fake-data-id"
                    for call in retry_task_calls
                ),
                retry_task_calls,
            )

            fake_http.unsafe_artifact_urls = True
            unsafe_payload = restart_provider.fetch_ai_minutes("fake-data-id", timeout_seconds=1, interval_seconds=0.1)
            unsafe_output = unsafe_payload.get("output", {}) if isinstance(unsafe_payload.get("output"), dict) else {}
            assert_ok(
                "Tingwu artifact fetch blocks unsupported URL schemes",
                "transcriptionPathError" in unsafe_output
                and "Blocked unsupported Tingwu artifact URL" in str(unsafe_output.get("transcriptionPathError"))
                and "root:" not in json.dumps(unsafe_output, ensure_ascii=False),
                unsafe_payload,
            )
            fake_http.unsafe_artifact_urls = False
            fake_http.private_artifact_urls = True
            private_payload = restart_provider.fetch_ai_minutes("fake-data-id", timeout_seconds=1, interval_seconds=0.1)
            private_output = private_payload.get("output", {}) if isinstance(private_payload.get("output"), dict) else {}
            assert_ok(
                "Tingwu artifact fetch blocks private network URLs",
                "transcriptionPathError" in private_output
                and "Blocked non-public Tingwu artifact URL" in str(private_output.get("transcriptionPathError"))
                and "security-credentials" not in json.dumps(private_output, ensure_ascii=False),
                private_payload,
            )
            fake_http.private_artifact_urls = False
            original_resolve_hostname_ips = tingwu_module.resolve_hostname_ips
            try:
                tingwu_module.resolve_hostname_ips = lambda hostname: [tingwu_module.ipaddress.ip_address("127.0.0.1")] if hostname == "artifact.evil.test" else original_resolve_hostname_ips(hostname)  # type: ignore[assignment]
                fake_http.private_dns_artifact_urls = True
                private_dns_payload = restart_provider.fetch_ai_minutes("fake-data-id", timeout_seconds=1, interval_seconds=0.1)
                private_dns_output = private_dns_payload.get("output", {}) if isinstance(private_dns_payload.get("output"), dict) else {}
                assert_ok(
                    "Tingwu artifact fetch blocks public-looking domains that resolve to private IPs",
                    "transcriptionPathError" in private_dns_output
                    and "Blocked non-public Tingwu artifact URL" in str(private_dns_output.get("transcriptionPathError"))
                    and "security-credentials" not in json.dumps(private_dns_output, ensure_ascii=False),
                    private_dns_payload,
                )
            finally:
                fake_http.private_dns_artifact_urls = False
                tingwu_module.resolve_hostname_ips = original_resolve_hostname_ips  # type: ignore[assignment]
            fake_http.redirect_private_artifact = True
            redirect_private_payload = restart_provider.fetch_ai_minutes("fake-data-id", timeout_seconds=1, interval_seconds=0.1)
            redirect_private_output = redirect_private_payload.get("output", {}) if isinstance(redirect_private_payload.get("output"), dict) else {}
            redirect_private_text = json.dumps(redirect_private_payload, ensure_ascii=False)
            assert_ok(
                "Tingwu artifact fetch revalidates redirects and blocks private targets",
                "transcriptionPathError" in redirect_private_output
                and "Blocked non-public Tingwu artifact URL" in str(redirect_private_output.get("transcriptionPathError"))
                and "redirect-secret" not in redirect_private_text
                and "security-credentials" not in redirect_private_text,
                redirect_private_payload,
            )
            fake_http.redirect_private_artifact = False
            fake_http.secret_artifact_error = True
            secret_artifact_payload = restart_provider.fetch_ai_minutes("fake-data-id", timeout_seconds=1, interval_seconds=0.1)
            secret_artifact_text = json.dumps(secret_artifact_payload, ensure_ascii=False)
            secret_artifact_output = secret_artifact_payload.get("output", {}) if isinstance(secret_artifact_payload.get("output"), dict) else {}
            assert_ok(
                "Tingwu artifact fetch errors are redacted before persistence",
                "transcriptionPathError" in secret_artifact_output
                and "artifact-token" not in secret_artifact_text
                and "artifact-password" not in secret_artifact_text
                and "artifact-signature" not in secret_artifact_text
                and "leaky-query-token" not in secret_artifact_text
                and "leaky-signature" not in secret_artifact_text
                and "security-credentials" not in secret_artifact_text,
                secret_artifact_payload,
            )
            fake_http.secret_artifact_error = False
            fake_http.large_artifact = True
            large_artifact_payload = restart_provider.fetch_ai_minutes("fake-data-id", timeout_seconds=1, interval_seconds=0.1)
            large_artifact_text = json.dumps(large_artifact_payload, ensure_ascii=False)
            large_artifact_output = large_artifact_payload.get("output", {}) if isinstance(large_artifact_payload.get("output"), dict) else {}
            assert_ok(
                "Tingwu artifact fetch blocks oversized response bodies",
                "transcriptionPathError" in large_artifact_output
                and "too large" in str(large_artifact_output.get("transcriptionPathError"))
                and "transcriptionPathData" not in large_artifact_output
                and len(large_artifact_text) < 10000,
                large_artifact_payload,
            )
            fake_http.large_artifact = False
            fake_http.large_get_task_response = True
            try:
                large_get_payload = restart_provider.fetch_ai_minutes("fake-data-id", timeout_seconds=1, interval_seconds=0.1)
                raise AssertionError(f"large GetTask response should fail: {large_get_payload}")
            except Exception as exc:
                assert_ok(
                    "GetTask blocks oversized Tingwu API responses",
                    "GetTask response is too large" in str(exc) and "transcriptionPathData" not in str(exc),
                    str(exc),
                )
            finally:
                fake_http.large_get_task_response = False

            empty_failure_dir = timeout_config.workspace_dir / "meetings" / "tingwu_empty_minutes_failure"
            empty_failure_dir.mkdir(parents=True, exist_ok=True)
            empty_failure_session = {
                "meeting_id": "tingwu_empty_minutes_failure",
                "title": "empty minutes failure",
                "participants": ["Alice"],
                "task_id": "fake-data-id",
                "data_id": "fake-data-id",
                "status": "failed",
                "created_at": "2026-05-26T00:00:00+00:00",
                "started_at": "2026-05-26T00:00:01+00:00",
                "stopped_at": "2026-05-26T00:00:02+00:00",
                "transcript": [{"timestamp": "2026-05-26T00:00:02+00:00", "speaker": "Alice", "text": "失败后重新拉取纪要", "final": True}],
                "output_dir": str(empty_failure_dir),
                "transcript_path": str(empty_failure_dir / "transcript.md"),
                "audio_path": str(empty_failure_dir / "audio.wav"),
                "minutes_path": "",
                "task_payload": {"data_id": "fake-data-id"},
                "ai_minutes": {},
                "error": "network error before ai_minutes payload",
            }
            (empty_failure_dir / "session.json").write_text(json.dumps(empty_failure_session, ensure_ascii=False, indent=2), encoding="utf-8")
            empty_retry_provider = TingwuMeetingProvider(
                timeout_config,
                Workspace(timeout_config.workspace_dir, timeout_config.allowed_roots, AuditLogger(timeout_config.audit_log_path)),
                AuditLogger(timeout_config.audit_log_path),
            )
            empty_retry_result = empty_retry_provider.finalize_meeting("tingwu_empty_minutes_failure", retry_failed_minutes=True)
            assert_ok(
                "AI minutes fetch can retry failed session with empty ai_minutes payload",
                empty_retry_result["status"] == "completed"
                and empty_retry_result.get("ai_minutes", {}).get("output", {}).get("summarizationPathData")
                and Path(str(empty_retry_result["minutes_path"])).is_file(),
                empty_retry_result,
            )

            fake_http.fail_minutes_create_with_secret = True
            secret_failure_dir = timeout_config.workspace_dir / "meetings" / "tingwu_secret_minutes_failure"
            secret_failure_dir.mkdir(parents=True, exist_ok=True)
            secret_failure_session = {
                "meeting_id": "tingwu_secret_minutes_failure",
                "title": "secret minutes failure",
                "participants": ["Alice"],
                "task_id": "fake-data-id",
                "data_id": "fake-data-id",
                "status": "stopped",
                "created_at": "2026-05-26T00:00:00+00:00",
                "started_at": "2026-05-26T00:00:01+00:00",
                "stopped_at": "2026-05-26T00:00:02+00:00",
                "transcript": [{"timestamp": "2026-05-26T00:00:02+00:00", "speaker": "Alice", "text": "HTTP 错误需要脱敏", "final": True}],
                "output_dir": str(secret_failure_dir),
                "transcript_path": str(secret_failure_dir / "transcript.md"),
                "audio_path": str(secret_failure_dir / "audio.wav"),
                "minutes_path": "",
                "task_payload": {"data_id": "fake-data-id"},
                "ai_minutes": {},
                "error": "",
            }
            (secret_failure_dir / "session.json").write_text(json.dumps(secret_failure_session, ensure_ascii=False, indent=2), encoding="utf-8")
            secret_failure_provider = TingwuMeetingProvider(
                timeout_config,
                Workspace(timeout_config.workspace_dir, timeout_config.allowed_roots, AuditLogger(timeout_config.audit_log_path)),
                AuditLogger(timeout_config.audit_log_path),
            )
            secret_failure_result = secret_failure_provider.finalize_meeting("tingwu_secret_minutes_failure", retry_failed_minutes=True)
            secret_failure_session_text = (secret_failure_dir / "session.json").read_text(encoding="utf-8")
            secret_failure_audit_text = timeout_config.audit_log_path.read_text(encoding="utf-8")
            assert_ok(
                "AI minutes HTTP errors are redacted before session and audit persistence",
                secret_failure_result["status"] == "failed"
                and "should-not-persist" not in secret_failure_session_text
                and "leaky-token" not in secret_failure_session_text
                and "hunter2" not in secret_failure_session_text
                and "should-not-persist" not in secret_failure_audit_text
                and "leaky-token" not in secret_failure_audit_text
                and "hunter2" not in secret_failure_audit_text,
                {"session": secret_failure_session_text, "audit": secret_failure_audit_text[-1000:]},
            )
            fake_http.fail_minutes_create_with_secret = False

            legacy_secret_dir = timeout_config.workspace_dir / "meetings" / "tingwu_legacy_secret_session"
            legacy_secret_dir.mkdir(parents=True, exist_ok=True)
            legacy_secret_session = {
                "meeting_id": "tingwu_legacy_secret_session",
                "title": "legacy secret session",
                "participants": ["Alice"],
                "task_id": "fake-data-id",
                "data_id": "fake-data-id",
                "status": "failed",
                "created_at": "2026-05-26T00:00:00+00:00",
                "transcript": [{"timestamp": "2026-05-26T00:00:02+00:00", "speaker": "Alice", "text": "旧 session 需要脱敏", "final": True}],
                "output_dir": str(legacy_secret_dir),
                "transcript_path": str(legacy_secret_dir / "transcript.md"),
                "audio_path": str(legacy_secret_dir / "audio.wav"),
                "minutes_path": str(legacy_secret_dir / "tingwu_ai_minutes.md"),
                "task_payload": {
                    "authorization": "Bearer legacy-secret-token",
                    "events": [
                        {
                            "event": "websocket_error",
                            "timestamp": "2026-05-26T00:00:03+00:00",
                            "error": "token=legacy-event-token signature=legacy-event-signature /latest/meta-data/iam/security-credentials/",
                        }
                    ],
                },
                "ai_minutes": {
                    "output": {
                        "transcriptionPath": "https://example.com/transcript.json?token=legacy-query-token&signature=legacy-query-signature",
                        "transcriptionPathError": "password=legacy-password /metadata/instance/compute?api-version=2021-02-01",
                    }
                },
                "error": "Authorization: Bearer legacy-error-token password=legacy-error-password",
            }
            legacy_session_path = legacy_secret_dir / "session.json"
            legacy_session_path.write_text(json.dumps(legacy_secret_session, ensure_ascii=False, indent=2), encoding="utf-8")
            legacy_provider = TingwuMeetingProvider(
                timeout_config,
                Workspace(timeout_config.workspace_dir, timeout_config.allowed_roots, AuditLogger(timeout_config.audit_log_path)),
                AuditLogger(timeout_config.audit_log_path),
            )
            legacy_status = legacy_provider.session_status("tingwu_legacy_secret_session")
            legacy_status_text = json.dumps(legacy_status, ensure_ascii=False)
            legacy_disk_text = legacy_session_path.read_text(encoding="utf-8")
            legacy_events = legacy_provider.drain_events("tingwu_legacy_secret_session", limit=20)
            legacy_web_runtime = build_runtime(
                OfficeAgentConfig(
                    workspace_dir=timeout_config.workspace_dir,
                    audit_log_path=root / "legacy_secret_web_audit.jsonl",
                    allowed_roots=(timeout_config.workspace_dir,),
                ).normalized()
            )
            legacy_web_server = WebConsoleServer(legacy_web_runtime, token="test-console")
            legacy_web_server.tingwu = legacy_provider  # type: ignore[assignment]
            legacy_web_server.upsert_meeting_step_task(
                "legacy secret session",
                str(legacy_secret_dir / "transcript.md"),
                "realtime_capture",
                "running",
                {"meeting_id": "tingwu_legacy_secret_session", "status": "running"},
                meeting_id="tingwu_legacy_secret_session",
                provider="tongyi_tingwu",
            )
            legacy_web_events = legacy_web_server.api_meeting_realtime_events(
                "tingwu_legacy_secret_session",
                RequestContext(request_id="smoke-legacy-secret-events", actor="smoke", source_ip="127.0.0.1"),
            )
            legacy_combined_text = json.dumps(
                {"status": legacy_status, "disk": legacy_disk_text, "drained": legacy_events, "web": legacy_web_events},
                ensure_ascii=False,
            )
            assert_ok(
                "Persisted legacy Tingwu sessions are redacted on load and realtime events",
                "legacy-secret-token" not in legacy_combined_text
                and "legacy-event-token" not in legacy_combined_text
                and "legacy-event-signature" not in legacy_combined_text
                and "legacy-query-token" not in legacy_combined_text
                and "legacy-query-signature" not in legacy_combined_text
                and "legacy-password" not in legacy_combined_text
                and "legacy-error-token" not in legacy_combined_text
                and "legacy-error-password" not in legacy_combined_text
                and "security-credentials" not in legacy_combined_text
                and "metadata/instance/compute" not in legacy_combined_text,
                legacy_combined_text[:2000],
            )

            corrupt_dir = timeout_config.workspace_dir / "meetings" / "tingwu_corrupt_session"
            corrupt_dir.mkdir(parents=True, exist_ok=True)
            corrupt_session_path = corrupt_dir / "session.json"
            corrupt_session_path.write_text('{"meeting_id": "tingwu_corrupt_session", "status": ', encoding="utf-8")
            corrupt_provider = TingwuMeetingProvider(
                timeout_config,
                Workspace(timeout_config.workspace_dir, timeout_config.allowed_roots, AuditLogger(timeout_config.audit_log_path)),
                AuditLogger(timeout_config.audit_log_path),
            )
            corrupt_status = corrupt_provider.status()
            try:
                corrupt_provider.session_status("tingwu_corrupt_session")
                raise AssertionError("corrupt session should not load")
            except Exception as exc:
                corrupt_not_found = "Meeting not found" in str(exc)
            corrupt_audit_text = timeout_config.audit_log_path.read_text(encoding="utf-8")
            assert_ok(
                "Corrupt persisted Tingwu session is audited and skipped instead of crashing recovery",
                corrupt_status.get("provider") == "tongyi_tingwu"
                and corrupt_not_found
                and "tingwu.session_load" in corrupt_audit_text
                and "tingwu_corrupt_session" in corrupt_audit_text,
                {"status": corrupt_status, "not_found": corrupt_not_found, "audit_tail": corrupt_audit_text[-1200:]},
            )

            finalizing_provider = TingwuMeetingProvider(
                timeout_config,
                Workspace(timeout_config.workspace_dir, timeout_config.allowed_roots, AuditLogger(timeout_config.audit_log_path)),
                AuditLogger(timeout_config.audit_log_path),
            )
            finalizing_dir = timeout_config.workspace_dir / "meetings" / "tingwu_finalizing_active"
            finalizing_dir.mkdir(parents=True, exist_ok=True)
            finalizing_session_obj = tingwu_module.TingwuMeetingSession(
                meeting_id="tingwu_finalizing_active",
                title="finalizing active",
                participants=["Alice"],
                task_id="fake-data-id",
                status="finalizing",
                created_at="2026-05-26T00:00:00+00:00",
                data_id="fake-data-id",
                output_dir=str(finalizing_dir),
                transcript_path=str(finalizing_dir / "transcript.md"),
                audio_path=str(finalizing_dir / "audio.wav"),
                minutes_path=str(finalizing_dir / "tingwu_ai_minutes.md"),
                task_payload={"data_id": "fake-data-id"},
            )
            finalizing_provider._sessions[finalizing_session_obj.meeting_id] = finalizing_session_obj  # noqa: SLF001
            finalizing_provider._event_queues[finalizing_session_obj.meeting_id] = tingwu_module.Queue()  # noqa: SLF001
            finalizing_status = finalizing_provider.session_status("tingwu_finalizing_active")
            provider_status_during_finalizing = finalizing_provider.status()
            try:
                finalizing_provider.start_realtime_meeting(title="should be blocked while finalizing", participants=["Alice"], max_seconds=30)
                raise AssertionError("start should be blocked while another session is finalizing")
            except Exception as exc:
                start_blocked = "Another realtime meeting is already running" in str(exc)
            stop_finalizing_status = finalizing_provider.stop_realtime_meeting("tingwu_finalizing_active")
            web_finalizing_runtime = build_runtime(
                OfficeAgentConfig(
                    workspace_dir=timeout_config.workspace_dir,
                    audit_log_path=root / "web_finalizing_audit.jsonl",
                    allowed_roots=(timeout_config.workspace_dir,),
                ).normalized()
            )
            web_finalizing_server = WebConsoleServer(web_finalizing_runtime, token="test-console")
            web_finalizing_server.tingwu = finalizing_provider  # type: ignore[assignment]
            web_finalizing_result = web_finalizing_server.api_meeting_realtime_stop(
                {"meeting_id": "tingwu_finalizing_active", "run_followup": True},
                RequestContext(request_id="smoke-finalizing-stop", actor="smoke", source_ip="127.0.0.1"),
            )
            assert_ok(
                "Finalizing sessions count as active and block new realtime meetings",
                finalizing_status["status"] == "finalizing"
                and provider_status_during_finalizing.get("active_meeting_id") == "tingwu_finalizing_active"
                and provider_status_during_finalizing.get("active_count") == 1
                and start_blocked
                and stop_finalizing_status.get("status") == "finalizing"
                and web_finalizing_result.get("status") == "finalizing"
                and web_finalizing_result.get("session", {}).get("meeting_id") == "tingwu_finalizing_active"
                and not web_finalizing_result.get("manifest_path")
                and not web_finalizing_server.find_meeting_step_task(meeting_id="tingwu_finalizing_active", step_name="minutes")
                and not web_finalizing_server._assistant_notifications,
                {
                    "session": finalizing_status,
                    "provider": provider_status_during_finalizing,
                    "stop": stop_finalizing_status,
                    "web_stop": web_finalizing_result,
                    "start_blocked": start_blocked,
                },
            )

            finalizing_restart_dir = timeout_config.workspace_dir / "meetings" / "tingwu_finalizing_restart"
            finalizing_restart_dir.mkdir(parents=True, exist_ok=True)
            finalizing_restart_session = {
                "meeting_id": "tingwu_finalizing_restart",
                "title": "finalizing restart",
                "participants": ["Alice"],
                "task_id": "fake-data-id",
                "data_id": "fake-data-id",
                "status": "finalizing",
                "created_at": "2026-05-26T00:00:00+00:00",
                "started_at": "2026-05-26T00:00:01+00:00",
                "transcript": [{"timestamp": "2026-05-26T00:00:02+00:00", "speaker": "Alice", "text": "重启后的 finalizing 可以恢复完成", "final": True}],
                "output_dir": str(finalizing_restart_dir),
                "transcript_path": str(finalizing_restart_dir / "transcript.md"),
                "audio_path": str(finalizing_restart_dir / "audio.wav"),
                "minutes_path": str(finalizing_restart_dir / "tingwu_ai_minutes.md"),
                "task_payload": {"data_id": "fake-data-id"},
                "ai_minutes": {},
                "error": "",
            }
            (finalizing_restart_dir / "session.json").write_text(json.dumps(finalizing_restart_session, ensure_ascii=False, indent=2), encoding="utf-8")
            finalizing_restart_provider = TingwuMeetingProvider(
                timeout_config,
                Workspace(timeout_config.workspace_dir, timeout_config.allowed_roots, AuditLogger(timeout_config.audit_log_path)),
                AuditLogger(timeout_config.audit_log_path),
            )
            finalizing_recovered_status = finalizing_restart_provider.session_status("tingwu_finalizing_restart")
            finalizing_recovered_final = finalizing_restart_provider.finalize_meeting("tingwu_finalizing_restart", retry_failed_minutes=True)
            assert_ok(
                "Persisted finalizing sessions recover after provider restart and can complete minutes",
                finalizing_recovered_status["status"] == "stopped"
                and finalizing_recovered_final["status"] == "completed"
                and Path(str(finalizing_recovered_final["minutes_path"])).is_file(),
                {"status": finalizing_recovered_status, "final": finalizing_recovered_final},
            )

            interrupted_dir = timeout_config.workspace_dir / "meetings" / "tingwu_interrupted_restart"
            interrupted_dir.mkdir(parents=True, exist_ok=True)
            interrupted_session = {
                "meeting_id": "tingwu_interrupted_restart",
                "title": "interrupted restart",
                "participants": ["Alice"],
                "task_id": "fake-data-id",
                "data_id": "fake-data-id",
                "status": "running",
                "created_at": "2026-05-26T00:00:00+00:00",
                "started_at": "2026-05-26T00:00:01+00:00",
                "transcript": [{"timestamp": "2026-05-26T00:00:02+00:00", "speaker": "Alice", "text": "重启后继续拉取纪要", "final": True}],
                "output_dir": str(root / "outside_workspace"),
                "transcript_path": str(root / "outside_workspace" / "transcript.md"),
                "audio_path": str(root / "outside_workspace" / "audio.wav"),
                "minutes_path": str(root / "outside_workspace" / "tingwu_ai_minutes.md"),
                "task_payload": {"data_id": "fake-data-id"},
                "ai_minutes": {},
                "error": "",
            }
            (interrupted_dir / "session.json").write_text(json.dumps(interrupted_session, ensure_ascii=False, indent=2), encoding="utf-8")
            recovered_provider = TingwuMeetingProvider(
                timeout_config,
                Workspace(timeout_config.workspace_dir, timeout_config.allowed_roots, AuditLogger(timeout_config.audit_log_path)),
                AuditLogger(timeout_config.audit_log_path),
            )
            recovered_status = recovered_provider.session_status("tingwu_interrupted_restart")
            assert_ok(
                "Persisted active session recovers to stopped after provider restart and clamps artifact paths",
                recovered_status["status"] == "stopped"
                and Path(str(recovered_status["output_dir"])).is_relative_to(timeout_config.workspace_dir / "meetings" / "tingwu_interrupted_restart")
                and Path(str(recovered_status["transcript_path"])).is_relative_to(timeout_config.workspace_dir)
                and Path(str(recovered_status["transcript_path"])).is_file()
                and "重启后继续拉取纪要" in Path(str(recovered_status["transcript_path"])).read_text(encoding="utf-8"),
                recovered_status,
            )
            recovered_final = recovered_provider.finalize_meeting("tingwu_interrupted_restart", retry_failed_minutes=True)
            assert_ok(
                "Recovered stopped session can fetch Tingwu AI minutes",
                recovered_final["status"] == "completed"
                and Path(str(recovered_final["minutes_path"])).is_relative_to(timeout_config.workspace_dir)
                and Path(str(recovered_final["minutes_path"])).is_file(),
                recovered_final,
            )

            auto_recover_dir = timeout_config.workspace_dir / "meetings" / "tingwu_auto_recover_on_start"
            auto_recover_dir.mkdir(parents=True, exist_ok=True)
            auto_recover_session = {
                "meeting_id": "tingwu_auto_recover_on_start",
                "title": "auto recover before start",
                "participants": ["Alice"],
                "task_id": "fake-data-id",
                "data_id": "fake-data-id",
                "status": "running",
                "created_at": "2026-05-26T00:00:00+00:00",
                "started_at": "2026-05-26T00:00:01+00:00",
                "transcript": [{"timestamp": "2026-05-26T00:00:02+00:00", "speaker": "Alice", "text": "start 前自动恢复遗留会议", "final": True}],
                "output_dir": str(auto_recover_dir),
                "transcript_path": str(auto_recover_dir / "transcript.md"),
                "audio_path": str(auto_recover_dir / "audio.wav"),
                "minutes_path": str(auto_recover_dir / "tingwu_ai_minutes.md"),
                "task_payload": {"data_id": "fake-data-id"},
                "ai_minutes": {},
                "error": "",
            }
            (auto_recover_dir / "session.json").write_text(json.dumps(auto_recover_session, ensure_ascii=False, indent=2), encoding="utf-8")
            auto_recover_direct_config = OfficeAgentConfig(
                workspace_dir=root / "auto_recover_direct_workspace",
                audit_log_path=root / "auto_recover_direct_audit.jsonl",
                allowed_roots=(root / "auto_recover_direct_workspace",),
                tingwu_api_key="test-key",
                tingwu_app_id="tw_test_app",
                tingwu_http_url=f"{fake_http.base_url}/generation",
                tingwu_ws_url="ws://fake-tingwu.local/inference",
                tingwu_mock=False,
                mic_device="fake-mic",
                tingwu_sample_rate=16000,
                tingwu_audio_format="pcm",
            )
            auto_recover_direct_dir = auto_recover_direct_config.workspace_dir / "meetings" / "tingwu_auto_recover_direct_status"
            auto_recover_direct_dir.mkdir(parents=True, exist_ok=True)
            auto_recover_direct_session = dict(auto_recover_session)
            auto_recover_direct_session.update(
                {
                    "meeting_id": "tingwu_auto_recover_direct_status",
                    "title": "auto recover direct status",
                    "output_dir": str(auto_recover_direct_dir),
                    "transcript_path": str(auto_recover_direct_dir / "transcript.md"),
                    "audio_path": str(auto_recover_direct_dir / "audio.wav"),
                    "minutes_path": str(auto_recover_direct_dir / "tingwu_ai_minutes.md"),
                }
            )
            (auto_recover_direct_dir / "session.json").write_text(json.dumps(auto_recover_direct_session, ensure_ascii=False, indent=2), encoding="utf-8")
            auto_recover_provider = TingwuMeetingProvider(
                timeout_config,
                Workspace(timeout_config.workspace_dir, timeout_config.allowed_roots, AuditLogger(timeout_config.audit_log_path)),
                AuditLogger(timeout_config.audit_log_path),
            )
            auto_recover_direct_provider = TingwuMeetingProvider(
                auto_recover_direct_config,
                Workspace(auto_recover_direct_config.workspace_dir, auto_recover_direct_config.allowed_roots, AuditLogger(auto_recover_direct_config.audit_log_path)),
                AuditLogger(auto_recover_direct_config.audit_log_path),
            )
            auto_recover_direct_trigger = auto_recover_direct_provider.session_status()
            auto_recover_direct_status = auto_recover_direct_provider.session_status("tingwu_auto_recover_direct_status")
            auto_recover_status = auto_recover_provider.status()
            auto_recover_loaded = auto_recover_provider.session_status("tingwu_auto_recover_on_start")
            auto_recover_started = auto_recover_provider.start_realtime_meeting(title="after auto recover", participants=["Alice"], max_seconds=30)
            assert_ok(
                "Provider startup scans persisted active sessions and recovers before allowing new start",
                auto_recover_direct_trigger.get("status") == "idle"
                and auto_recover_direct_status.get("meeting_id") == "tingwu_auto_recover_direct_status"
                and auto_recover_direct_status.get("status") == "stopped"
                and auto_recover_status.get("active_count") == 0
                and auto_recover_loaded.get("status") == "stopped"
                and "Recovered from persisted running state" in str(auto_recover_loaded.get("error") or "")
                and auto_recover_started.get("status") == "running"
                and auto_recover_started.get("meeting_id") != "tingwu_auto_recover_on_start",
                {
                    "direct_trigger": auto_recover_direct_trigger,
                    "direct": auto_recover_direct_status,
                    "status": auto_recover_status,
                    "loaded": auto_recover_loaded,
                    "started": auto_recover_started,
                },
            )
            stop_and_wait(auto_recover_provider, str(auto_recover_started.get("meeting_id")), wait_seconds=3, timeout=5)
            timeout_result["status"] = "failed"
            timeout_result["error"] = "Tingwu AI minutes did not complete: timeout"

            web_runtime = build_runtime(
                OfficeAgentConfig(
                    workspace_dir=timeout_config.workspace_dir,
                    audit_log_path=root / "web_register_audit.jsonl",
                    allowed_roots=(timeout_config.workspace_dir,),
                ).normalized()
            )
            web_server = WebConsoleServer(web_runtime, token="test-console")
            web_result = web_server.register_tingwu_outputs(
                timeout_result,
                RequestContext(request_id="smoke", actor="smoke", source_ip="127.0.0.1"),
                run_followup=True,
            )
            web_steps = {step.get("name"): step.get("status") for step in web_result.get("job", {}).get("steps", []) if isinstance(step, dict)}
            assert_ok(
                "Web register preserves provider failure while OpenClaw completes",
                web_result["status"] == "failed"
                and web_result.get("provider_status") == "failed"
                and web_result.get("openclaw_status") == "completed"
                and web_result.get("followup", {}).get("status") == "completed",
                web_result,
            )
            assert_ok(
                "Realtime capture remains completed when only Tingwu AI minutes fail",
                web_steps.get("realtime_capture") == "completed"
                and web_steps.get("import_transcript") == "completed"
                and web_steps.get("minutes") == "failed",
                web_steps,
            )
            empty_web_dir = timeout_config.workspace_dir / "meetings" / "tingwu_empty_web_register"
            empty_web_dir.mkdir(parents=True, exist_ok=True)
            empty_web_transcript = empty_web_dir / "transcript.md"
            empty_web_audio = empty_web_dir / "audio.wav"
            empty_web_session_path = empty_web_dir / "session.json"
            empty_web_transcript.write_text("# Empty Transcript\n", encoding="utf-8")
            empty_web_audio.write_bytes(b"RIFF$\x00\x00\x00WAVEfmt ")
            empty_web_session = {
                "meeting_id": "tingwu_empty_web_register",
                "title": "empty web register",
                "participants": ["Alice"],
                "task_id": "fake-data-id",
                "status": "failed",
                "created_at": "2026-05-26T00:00:00+00:00",
                "transcript": [],
                "realtime_transcript": "",
                "audio_seconds": 0,
                "audio_rms": 0,
                "audio_peak": 0,
                "output_dir": str(empty_web_dir),
                "transcript_path": str(empty_web_transcript),
                "audio_path": str(empty_web_audio),
                "minutes_path": "",
                "task_payload": {"events": [{"event": "meeting_error", "error": "No microphone audio frames were captured."}]},
                "ai_minutes": {},
                "error": "No microphone audio frames were captured.",
            }
            empty_web_session_path.write_text(json.dumps(empty_web_session, ensure_ascii=False, indent=2), encoding="utf-8")
            empty_web_result = web_server.register_tingwu_outputs(
                empty_web_session,
                RequestContext(request_id="smoke-empty", actor="smoke", source_ip="127.0.0.1"),
                run_followup=True,
            )
            empty_web_steps = {step.get("name"): step.get("status") for step in empty_web_result.get("job", {}).get("steps", []) if isinstance(step, dict)}
            assert_ok(
                "Web register blocks OpenClaw follow-up for empty Tingwu transcript",
                empty_web_result.get("status") == "failed"
                and empty_web_result.get("provider_status") == "failed"
                and empty_web_result.get("openclaw_status") == "failed"
                and empty_web_result.get("followup") is None
                and empty_web_steps.get("realtime_capture") == "failed"
                and empty_web_steps.get("import_transcript") == "failed"
                and empty_web_steps.get("minutes") == "failed",
                {"result": empty_web_result, "steps": empty_web_steps},
            )
            empty_manifest_path = str(empty_web_result.get("manifest_path") or "")
            empty_manifest = json.loads(Path(empty_manifest_path).read_text(encoding="utf-8"))
            assert_ok(
                "Failed Tingwu manifest records provider and OpenClaw errors",
                empty_manifest.get("status") == "failed"
                and empty_manifest.get("provider_status") == "failed"
                and empty_manifest.get("openclaw_status") == "failed"
                and "No microphone audio frames were captured" in str(empty_manifest.get("provider_error") or "")
                and "no speaker turns" in str(empty_manifest.get("openclaw_error") or ""),
                empty_manifest,
            )

            failed_stop_dir = timeout_config.workspace_dir / "meetings" / "tingwu_failed_stop_register"
            failed_stop_dir.mkdir(parents=True, exist_ok=True)
            failed_stop_transcript = failed_stop_dir / "transcript.md"
            failed_stop_audio = failed_stop_dir / "audio.wav"
            failed_stop_transcript.write_text(
                "Alice: 决定: 失败会话也要通过停止按钮登记输出。\n"
                "Bob: 待办: 检查 failed stop register 任务和 manifest。\n",
                encoding="utf-8",
            )
            failed_stop_audio.write_bytes(b"RIFF$\x00\x00\x00WAVEfmt ")
            failed_stop_session = {
                "meeting_id": "tingwu_failed_stop_register",
                "title": "failed stop register",
                "participants": ["Alice", "Bob"],
                "task_id": "fake-data-id",
                "status": "failed",
                "created_at": "2026-05-26T00:00:00+00:00",
                "stopped_at": "2026-05-26T00:00:02+00:00",
                "transcript": [
                    {
                        "timestamp": "2026-05-26T00:00:01+00:00",
                        "speaker": "Alice",
                        "text": "决定: 失败会话也要通过停止按钮登记输出。",
                        "final": True,
                    },
                    {
                        "timestamp": "2026-05-26T00:00:02+00:00",
                        "speaker": "Bob",
                        "text": "待办: 检查 failed stop register 任务和 manifest。",
                        "final": True,
                    },
                ],
                "realtime_transcript": "Alice: 决定: 失败会话也要通过停止按钮登记输出。\nBob: 待办: 检查 failed stop register 任务和 manifest。",
                "audio_seconds": 1.25,
                "audio_rms": 42,
                "audio_peak": 160,
                "output_dir": str(failed_stop_dir),
                "transcript_path": str(failed_stop_transcript),
                "audio_path": str(failed_stop_audio),
                "minutes_path": "",
                "task_payload": {"events": [{"event": "meeting_error", "error": "simulated provider failure after transcript"}]},
                "ai_minutes": {},
                "error": "simulated provider failure after transcript",
            }

            class FailedStopProvider:
                def stop_realtime_meeting(self, meeting_id=None):
                    return failed_stop_session

            web_server.tingwu = FailedStopProvider()  # type: ignore[assignment]
            notifications_before_failed_stop = len(web_server._assistant_notifications)
            failed_stop_result = web_server.api_meeting_realtime_stop(
                {"meeting_id": "tingwu_failed_stop_register", "run_followup": True},
                RequestContext(request_id="smoke-failed-stop", actor="smoke", source_ip="127.0.0.1"),
            )
            first_failed_stop_notification_id = str(web_server._assistant_notifications[-1].get("id") or "")
            failed_stop_repeat = web_server.api_meeting_realtime_stop(
                {"meeting_id": "tingwu_failed_stop_register", "run_followup": False},
                RequestContext(request_id="smoke-failed-stop-repeat", actor="smoke", source_ip="127.0.0.1"),
            )
            failed_stop_steps = {step.get("name"): step.get("status") for step in failed_stop_result.get("job", {}).get("steps", []) if isinstance(step, dict)}
            failed_stop_repeat_steps = {step.get("name"): step.get("status") for step in failed_stop_repeat.get("job", {}).get("steps", []) if isinstance(step, dict)}
            failed_stop_manifest = json.loads(Path(str(failed_stop_result.get("manifest_path") or "")).read_text(encoding="utf-8"))
            failed_stop_notification = web_server._assistant_notifications[-1]
            failed_stop_notifications = [
                item
                for item in web_server._assistant_notifications
                if item.get("event") == "meeting_realtime_provider_failed"
                and item.get("payload", {}).get("meeting_id") == "tingwu_failed_stop_register"
            ]
            assert_ok(
                "Web stop registers failed Tingwu sessions with transcript outputs and dedupes assistant notifications",
                failed_stop_result.get("status") == "failed"
                and failed_stop_result.get("provider_status") == "failed"
                and failed_stop_result.get("openclaw_status") == "completed"
                and failed_stop_repeat.get("status") == "failed"
                and failed_stop_repeat.get("provider_status") == "failed"
                and failed_stop_repeat.get("openclaw_status") == "completed"
                and failed_stop_result.get("followup", {}).get("status") == "completed"
                and failed_stop_steps.get("realtime_capture") == "completed"
                and failed_stop_steps.get("import_transcript") == "completed"
                and failed_stop_steps.get("minutes") == "failed"
                and failed_stop_repeat_steps.get("realtime_capture") == "completed"
                and failed_stop_repeat_steps.get("import_transcript") == "completed"
                and failed_stop_repeat_steps.get("minutes") == "failed"
                and failed_stop_repeat.get("task_id") == failed_stop_result.get("task_id")
                and failed_stop_manifest.get("meeting_id") == "tingwu_failed_stop_register"
                and failed_stop_manifest.get("provider_status") == "failed"
                and failed_stop_manifest.get("openclaw_status") == "completed"
                and failed_stop_notification.get("event") == "meeting_realtime_provider_failed"
                and len(web_server._assistant_notifications) == notifications_before_failed_stop + 1,
                {"result": failed_stop_result, "repeat": failed_stop_repeat, "steps": failed_stop_steps, "manifest": failed_stop_manifest, "notification": failed_stop_notification},
            )
            assert_ok(
                "Assistant notification dedupe keeps one updated cursor per meeting event",
                len(failed_stop_notifications) == 1
                and failed_stop_notification.get("id") != first_failed_stop_notification_id,
                {"notifications": failed_stop_notifications, "latest": failed_stop_notification},
            )
            external_output_path = root / "outside_workspace" / "leaked-output.md"
            external_output_path.parent.mkdir(parents=True, exist_ok=True)
            external_output_path.write_text("outside workspace", encoding="utf-8")
            manifest_path = web_server.write_tingwu_meeting_manifest(
                session=timeout_result,
                minutes=web_result.get("minutes", {}) if isinstance(web_result.get("minutes"), dict) else {},
                followup=web_result.get("followup", {}) if isinstance(web_result.get("followup"), dict) else None,
                outputs=[
                    {"path": str(timeout_result.get("transcript_path") or ""), "type": "markdown"},
                    {"path": str(external_output_path), "type": "markdown"},
                ],
                job=web_result.get("job", {}) if isinstance(web_result.get("job"), dict) else {},
                ctx=RequestContext(request_id="smoke-manifest", actor="smoke", source_ip="127.0.0.1"),
            )
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            manifest_outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), list) else []
            manifest_audit = (root / "web_register_audit.jsonl").read_text(encoding="utf-8")
            assert_ok(
                "Meeting manifest skips outputs outside workspace",
                manifest_path
                and all(item.get("inside_workspace") is True for item in manifest_outputs if isinstance(item, dict))
                and not any(str(item.get("path") or "") == str(external_output_path) for item in manifest_outputs if isinstance(item, dict))
                and "meeting_manifest.output_skip" in manifest_audit,
                manifest,
            )
            secret_external_output_path = root / "outside_workspace" / "token=skip-token_password=skip-password.md"
            secret_session = {
                **timeout_result,
                "title": "manifest secret title token=manifest-title-token password=manifest-title-password",
                "task_id": "token=manifest-provider-token password=manifest-provider-password",
                "audio_path": "https://audio-user:audio-pass@example.test/audio.wav?token=audio-query-token",
            }
            secret_minutes = {
                "status": "failed",
                "provider_status": "failed",
                "openclaw_status": "completed",
                "provider_error": "Authorization: Bearer minutes-bearer token=minutes-provider-token",
                "error": "clientSecret=minutes-client-secret signature=minutes-signature",
                "path": str(timeout_config.workspace_dir / "meetings" / "openclaw_minutes_token=minutes-path-token.md"),
            }
            secret_job = {
                "job_id": "manifest_secret_job",
                "title": "job secret token=job-title-token",
                "steps": [
                    {
                        "name": "minutes",
                        "ai_result": {
                            "clientSecret": "job-client-secret",
                            "authorizationHeader": "Bearer job-bearer",
                            "message": "token=job-message-token",
                        },
                    }
                ],
            }
            secret_manifest_path = web_server.write_tingwu_meeting_manifest(
                session=secret_session,
                minutes=secret_minutes,
                followup={"status": "completed"},
                outputs=[
                    {"path": str(timeout_result.get("transcript_path") or ""), "type": "markdown"},
                    {"path": str(secret_external_output_path), "type": "markdown"},
                ],
                job=secret_job,
                ctx=RequestContext(request_id="smoke-secret-manifest", actor="smoke", source_ip="127.0.0.1"),
            )
            secret_manifest_text = Path(secret_manifest_path).read_text(encoding="utf-8")
            secret_manifest_audit_text = (root / "web_register_audit.jsonl").read_text(encoding="utf-8")
            assert_ok(
                "Tingwu meeting manifest and output-skip audit redact secrets before persistence",
                "manifest-title-token" not in secret_manifest_text
                and "manifest-title-password" not in secret_manifest_text
                and "manifest-provider-token" not in secret_manifest_text
                and "manifest-provider-password" not in secret_manifest_text
                and "audio-user" not in secret_manifest_text
                and "audio-pass" not in secret_manifest_text
                and "audio-query-token" not in secret_manifest_text
                and "minutes-bearer" not in secret_manifest_text
                and "minutes-provider-token" not in secret_manifest_text
                and "minutes-client-secret" not in secret_manifest_text
                and "minutes-signature" not in secret_manifest_text
                and "minutes-path-token" not in secret_manifest_text
                and "job-title-token" not in secret_manifest_text
                and "job-client-secret" not in secret_manifest_text
                and "job-bearer" not in secret_manifest_text
                and "job-message-token" not in secret_manifest_text
                and "skip-token" not in secret_manifest_audit_text
                and "skip-password" not in secret_manifest_audit_text
                and "[redacted]" in secret_manifest_text,
                {"manifest": secret_manifest_text, "audit": secret_manifest_audit_text[-1200:]},
            )
            meeting_output_dir = timeout_config.workspace_dir / "meetings" / "tingwu_atomic_output"
            meeting_output_dir.mkdir(parents=True, exist_ok=True)
            output_ctx = RequestContext(request_id="smoke-output-write", actor="smoke", source_ip="127.0.0.1")
            output_path = web_server.write_meeting_output_json(
                str(meeting_output_dir),
                "atomic_output.json",
                {"ok": True},
                action="meeting.atomic_output_smoke",
                meeting_id="tingwu_atomic_output",
                ctx=output_ctx,
            )
            source_copy_path = timeout_config.workspace_dir / "copy-source.md"
            source_copy_path.write_text("copy source", encoding="utf-8")
            copied_output = web_server.materialize_tingwu_workspace_file(
                {"path": str(source_copy_path)},
                output_dir=str(meeting_output_dir),
                filename="copied.md",
                meeting_id="tingwu_atomic_output",
                ctx=output_ctx,
            )
            projection_source = timeout_config.workspace_dir / "projection-source.md"
            projection_source.write_text("# projection\n", encoding="utf-8")
            copied_projection = web_server.materialize_tingwu_projection_output(
                {"path": str(projection_source)},
                meeting_id="tingwu_atomic_output",
                projection_dir_before=0,
                ctx=output_ctx,
            )
            output_audit_text = (root / "web_register_audit.jsonl").read_text(encoding="utf-8")
            assert_ok(
                "Meeting output helpers write atomically and preserve web audit context",
                Path(output_path).is_file()
                and Path(str(copied_output.get("path") or "")).is_file()
                and Path(str(copied_projection.get("path") or "")).is_file()
                and not list(meeting_output_dir.glob(".*.tmp"))
                and "meeting.atomic_output_smoke" in output_audit_text
                and "meeting_output_workspace_copy" in output_audit_text
                and "meeting_projection_workspace_copy" in output_audit_text
                and "smoke-output-write" in output_audit_text
                and '"actor": "smoke"' in output_audit_text,
                {"output_dir": str(meeting_output_dir), "audit": output_audit_text[-2000:]},
            )
            invalid_output_dir = root / "outside_workspace" / "invalid_meeting_output"
            blocked_write = False
            try:
                web_server.write_meeting_output_json(
                    str(invalid_output_dir),
                    "should_not_write.json",
                    {"blocked": False},
                    action="meeting.invalid_output_smoke",
                    meeting_id="tingwu_atomic_output",
                    ctx=output_ctx,
                )
            except ApiError as exc:
                blocked_write = exc.code == "invalid_meeting_output_dir"
            blocked_copy = web_server.materialize_tingwu_workspace_file(
                {"path": str(source_copy_path)},
                output_dir=str(invalid_output_dir),
                filename="should_not_copy.md",
                meeting_id="tingwu_atomic_output",
                ctx=output_ctx,
            )
            invalid_audit_text = (root / "web_register_audit.jsonl").read_text(encoding="utf-8")
            assert_ok(
                "Meeting output helpers block invalid non-empty meeting output dirs",
                blocked_write
                and blocked_copy.get("status") == "blocked"
                and not (timeout_config.workspace_dir / "should_not_write.json").exists()
                and not (timeout_config.workspace_dir / "should_not_copy.md").exists()
                and "meeting_output_write" in invalid_audit_text
                and '"status": "blocked"' in invalid_audit_text,
                {"blocked_copy": blocked_copy, "audit": invalid_audit_text[-2000:]},
            )
            bounded_task = web_server.create_task("bounded task events", "meeting", "running", {"meeting_id": "bounded_task_events"}, {})
            for index in range(250):
                web_server.append_task_event(str(bounded_task["task_id"]), "tick", {"index": index})
            web_server.append_task_event(
                str(bounded_task["task_id"]),
                "secret_event token=event-token",
                {
                    "error": "Authorization: Bearer event-bearer token=event-token password=event-password",
                    "api_key": "event-api-key",
                    "nested": {
                        "clientSecret": "event-client-secret",
                        "dashscope-token": "event-dashscope-token",
                    },
                },
            )
            web_server.update_task(
                str(bounded_task["task_id"]),
                output={
                    "status": "failed",
                    "error": "token=update-token password=update-password",
                    "nested": {
                        "apiKeyValue": "update-api-key",
                        "authorizationHeader": "Bearer update-authorization",
                    },
                },
                error={
                    "message": "secret=update-secret signature=update-signature",
                    "refreshToken": "update-refresh-token",
                },
            )
            bounded_task_file = web_server.task_dir() / f"{bounded_task['task_id']}.json"
            bounded_task_text = bounded_task_file.read_text(encoding="utf-8")
            assert_ok(
                "Task update and event payloads redact credentials before persistence",
                "event-token" not in bounded_task_text
                and "event-password" not in bounded_task_text
                and "event-api-key" not in bounded_task_text
                and "event-client-secret" not in bounded_task_text
                and "event-dashscope-token" not in bounded_task_text
                and "update-token" not in bounded_task_text
                and "update-password" not in bounded_task_text
                and "update-api-key" not in bounded_task_text
                and "update-authorization" not in bounded_task_text
                and "update-secret" not in bounded_task_text
                and "update-signature" not in bounded_task_text
                and "update-refresh-token" not in bounded_task_text,
                bounded_task_text,
            )
            bounded_events_payload = web_server.api_task_events(
                str(bounded_task["task_id"]),
                RequestContext(request_id="smoke-bounded-task-events", actor="smoke", source_ip="127.0.0.1"),
            )
            bounded_task_events = bounded_events_payload.get("events") if isinstance(bounded_events_payload.get("events"), list) else []
            assert_ok(
                "Task event logs keep only bounded recent events",
                len(bounded_task_events) == 200
                and bounded_task_events[0].get("index") == 51
                and str(bounded_task_events[-1].get("event") or "") == "secret_event token=[redacted]",
                bounded_events_payload,
            )
            event_session = {
                "meeting_id": "tingwu_event_sync",
                "title": "event sync",
                "status": "failed",
                "transcript_path": str(timeout_config.workspace_dir / "meetings" / "tingwu_event_sync" / "transcript.md"),
                "task_payload": {
                    "events": [
                        {
                            "event": "websocket_error",
                            "timestamp": "2026-05-26T00:00:03+00:00",
                            "error": "forced realtime error",
                        }
                    ]
                },
            }
            web_server.upsert_meeting_step_task(
                "event sync",
                str(event_session["transcript_path"]),
                "realtime_capture",
                "running",
                {"meeting_id": "tingwu_event_sync", "status": "running"},
                meeting_id="tingwu_event_sync",
                provider="tongyi_tingwu",
            )
            web_server.sync_realtime_capture_task(event_session)
            event_task = web_server.find_meeting_step_task(meeting_id="tingwu_event_sync", step_name="realtime_capture") or {}
            event_output = event_task.get("output") if isinstance(event_task.get("output"), dict) else {}
            event_names = [item.get("event") for item in event_output.get("events", []) if isinstance(item, dict)]
            assert_ok(
                "Realtime capture task sync preserves provider error events",
                event_task.get("status") == "failed" and "websocket_error" in event_names,
                event_task,
            )
            assert_ok(
                "Realtime capture task sync stores compact session summary",
                "task_payload" not in event_output
                and "monitor" in event_output
                and event_output.get("meeting_id") == "tingwu_event_sync",
                event_output,
            )

            class PersistedEventsProvider:
                def __init__(self) -> None:
                    self.drained = False

                def drain_events(self, meeting_id, limit=200):
                    if self.drained:
                        return []
                    self.drained = True
                    return []

                def session_status(self, meeting_id=None):
                    return {
                        "meeting_id": meeting_id,
                        "title": "persisted events",
                        "status": "running",
                        "task_payload": {
                            "events": [
                                {
                                    "event": "websocket_error",
                                    "timestamp": "2026-05-26T00:00:03+00:00",
                                    "error": "persisted websocket error",
                                }
                            ]
                        },
                    }

            web_server.tingwu = PersistedEventsProvider()  # type: ignore[assignment]
            web_server.upsert_meeting_step_task(
                "persisted events",
                str(timeout_config.workspace_dir / "meetings" / "tingwu_persisted_events" / "transcript.md"),
                "realtime_capture",
                "running",
                {"meeting_id": "tingwu_persisted_events", "status": "running"},
                meeting_id="tingwu_persisted_events",
                provider="tongyi_tingwu",
            )
            persisted_events_first = web_server.api_meeting_realtime_events(
                "tingwu_persisted_events",
                RequestContext(request_id="smoke-events-1", actor="smoke", source_ip="127.0.0.1"),
            )
            persisted_events_second = web_server.api_meeting_realtime_events(
                "tingwu_persisted_events",
                RequestContext(request_id="smoke-events-2", actor="smoke", source_ip="127.0.0.1"),
            )
            persisted_event_task = web_server.find_meeting_step_task(meeting_id="tingwu_persisted_events", step_name="realtime_capture") or {}
            persisted_event_output = persisted_event_task.get("output") if isinstance(persisted_event_task.get("output"), dict) else {}
            persisted_task_events = [
                item
                for item in persisted_event_output.get("events", [])
                if isinstance(item, dict) and item.get("event") == "websocket_error"
            ]
            assert_ok(
                "Realtime events API falls back to persisted provider events without duplicating task log",
                any(item.get("event") == "websocket_error" for item in persisted_events_first.get("events", []) if isinstance(item, dict))
                and any(item.get("event") == "websocket_error" for item in persisted_events_second.get("events", []) if isinstance(item, dict))
                and len(persisted_task_events) == 1,
                {
                    "first": persisted_events_first,
                    "second": persisted_events_second,
                    "task": persisted_event_task,
                },
            )

            split_transcript_path = timeout_config.workspace_dir / "meetings" / "split_aggregation" / "transcript.md"
            split_transcript_path.parent.mkdir(parents=True, exist_ok=True)
            split_transcript_path.write_text("Alice: 决定: 聚合按 meeting_id 合并\n", encoding="utf-8")
            split_session = {
                "meeting_id": "tingwu_split_aggregation",
                "title": "split aggregation",
                "status": "completed",
                "transcript_path": str(split_transcript_path),
                "participants": ["Alice"],
                "realtime_transcript": "Alice: 决定: 聚合按 meeting_id 合并",
            }
            relative_transcript = web_server.workspace_relative_path(str(split_transcript_path))
            web_server.upsert_meeting_step_task(
                "split aggregation",
                str(split_transcript_path),
                "realtime_capture",
                "completed",
                split_session,
                meeting_id="tingwu_split_aggregation",
                provider="tongyi_tingwu",
            )
            web_server.upsert_meeting_step_task(
                "split aggregation",
                relative_transcript,
                "minutes",
                "completed",
                {"status": "completed", "meeting_id": "tingwu_split_aggregation", "path": str(split_transcript_path)},
                meeting_id="tingwu_split_aggregation",
                provider="tongyi_tingwu",
            )
            split_jobs = [
                item
                for item in web_server.aggregate_meeting_jobs(web_server.load_tasks(limit=200))
                if item.get("meeting_id") == "tingwu_split_aggregation"
            ]
            split_steps = {step.get("name"): step.get("status") for step in split_jobs[0].get("steps", []) if isinstance(step, dict)} if split_jobs else {}
            assert_ok(
                "Meeting task aggregation groups realtime outputs by meeting_id across absolute and relative transcripts",
                len(split_jobs) == 1
                and split_jobs[0].get("transcript") == relative_transcript
                and split_steps.get("realtime_capture") == "completed"
                and split_steps.get("minutes") == "completed",
                split_jobs,
            )
            warning_notification = web_server.build_tingwu_assistant_notification("stop", timeout_result, web_result)
            warning_payload = warning_notification.get("payload") if isinstance(warning_notification.get("payload"), dict) else {}
            assert_ok(
                "Assistant notification warns when Tingwu provider fails but OpenClaw fallback completes",
                warning_notification.get("event") == "meeting_realtime_provider_failed"
                and warning_notification.get("status") == "warning"
                and "通义听悟 AI 纪要未完成" in str(warning_notification.get("text"))
                and "OpenClaw" in str(warning_notification.get("text"))
                and "provider_status: failed" in str(warning_notification.get("attachment"))
                and "openclaw_status: completed" in str(warning_notification.get("attachment")),
                warning_notification,
            )
            assert_ok(
                "Assistant notification payload is compact",
                warning_payload.get("meeting_id") == timeout_result.get("meeting_id")
                and "result" not in warning_payload
                and "task_payload" not in json.dumps(warning_payload, ensure_ascii=False),
                warning_notification,
            )

            class CaptureFailureProvider:
                def start_realtime_meeting(self, *, title, participants, max_seconds=7200):
                    raise TingwuMeetingError(
                        "Microphone capture preflight failed: forced open failure token=leaky-capture-token",
                        details={
                            "mic_probe": {
                                "status": "available",
                                "configured_device": "capture-fail",
                                "selected_device": "capture-fail",
                                "capture_probe": {
                                    "status": "unavailable",
                                    "selected_device": "capture-fail",
                                    "audio_bytes": 0,
                                    "audio_rms": 0,
                                    "audio_peak": 0,
                                    "message": "forced open failure password=leaky-capture-password",
                                },
                            },
                            "capture_probe": {
                                "status": "unavailable",
                                "selected_device": "capture-fail",
                                "audio_bytes": 0,
                                "audio_rms": 0,
                                "audio_peak": 0,
                                "message": "forced open failure password=leaky-capture-password",
                            },
                        },
                    )

                def status(self):
                    return {
                        "provider": "tongyi_tingwu",
                        "status": "available",
                        "configured": True,
                        "mic_status": "available",
                        "selected_mic_device": "capture-fail",
                        "mic_device": "capture-fail",
                        "message": "ready",
                    }

            web_server.tingwu = CaptureFailureProvider()  # type: ignore[assignment]
            web_notifications_before = len(web_server._assistant_notifications)
            try:
                web_server.api_meeting_realtime_start(
                    {"title": "web capture failure token=web-title-token", "participants": ["Alice"], "max_seconds": 30},
                    RequestContext(request_id="smoke-start-fail", actor="smoke", source_ip="127.0.0.1"),
                )
            except ApiError as exc:
                failure_details = exc.details
                failure_provider = failure_details.get("provider") if isinstance(failure_details.get("provider"), dict) else {}
                failure_capture = failure_details.get("capture_probe") if isinstance(failure_details.get("capture_probe"), dict) else {}
                failure_notification = web_server._assistant_notifications[-1]
                failure_payload = failure_notification.get("payload") if isinstance(failure_notification.get("payload"), dict) else {}
                failure_notification_capture = failure_payload.get("capture_probe") if isinstance(failure_payload.get("capture_probe"), dict) else {}
                failure_text = json.dumps({"details": failure_details, "notification": failure_notification}, ensure_ascii=False)
                assert_ok(
                    "Web realtime start failure exposes structured capture diagnostics without leaking secrets",
                    exc.code == "meeting_provider_unavailable"
                    and exc.status == 409
                    and failure_provider.get("status") == "unavailable"
                    and failure_provider.get("probe_status_before_capture") == "available"
                    and failure_capture.get("status") == "unavailable"
                    and failure_capture.get("audio_rms") == 0
                    and failure_notification.get("event") == "meeting_realtime_start_failed"
                    and "capture_status: unavailable" in str(failure_notification.get("attachment"))
                    and "capture_audio_rms: 0" in str(failure_notification.get("attachment"))
                    and failure_notification_capture.get("selected_device") == "capture-fail"
                    and len(web_server._assistant_notifications) == web_notifications_before + 1
                    and "leaky-capture-token" not in failure_text
                    and "leaky-capture-password" not in failure_text
                    and "web-title-token" not in failure_text,
                    {"details": failure_details, "notification": failure_notification},
                )
            else:
                raise AssertionError("web realtime start should fail when capture preflight fails")

            empty_warning_notification = web_server.build_tingwu_assistant_notification("stop", empty_web_session, empty_web_result)
            empty_warning_payload = empty_warning_notification.get("payload") if isinstance(empty_warning_notification.get("payload"), dict) else {}
            assert_ok(
                "Assistant notification payload carries redacted failure reason",
                empty_warning_notification.get("event") == "meeting_realtime_failed"
                and "No microphone audio frames were captured" in str(empty_warning_payload.get("error"))
                and "No microphone audio frames were captured" in str(empty_warning_payload.get("provider_error"))
                and "no speaker turns" in str(empty_warning_payload.get("openclaw_error"))
                and "should-not-persist" not in json.dumps(empty_warning_payload, ensure_ascii=False),
                empty_warning_notification,
            )
            fetch_warning_notification = web_server.build_tingwu_assistant_notification("fetch_minutes", timeout_result, web_result)
            assert_ok(
                "Fetch-minutes notification warns when Tingwu provider fails but OpenClaw fallback completes",
                fetch_warning_notification.get("event") == "meeting_ai_minutes_provider_failed"
                and fetch_warning_notification.get("status") == "warning"
                and "通义听悟 AI 纪要未完成" in str(fetch_warning_notification.get("text"))
                and "OpenClaw" in str(fetch_warning_notification.get("text")),
                fetch_warning_notification,
            )

            stopping_session = {
                "meeting_id": "tingwu_still_stopping",
                "title": "still stopping",
                "status": "stopping",
                "transcript_path": str(timeout_config.workspace_dir / "meetings" / "tingwu_still_stopping" / "transcript.md"),
                "participants": ["Alice"],
            }

            class StoppingProvider:
                def stop_realtime_meeting(self, meeting_id=None):
                    return stopping_session

            web_server.tingwu = StoppingProvider()  # type: ignore[assignment]
            notifications_before_pending_stop = len(web_server._assistant_notifications)
            pending_result = web_server.api_meeting_realtime_stop(
                {"meeting_id": "tingwu_still_stopping", "run_followup": True},
                RequestContext(request_id="smoke-stop", actor="smoke", source_ip="127.0.0.1"),
            )
            assert_ok(
                "Web stop does not register final outputs while Tingwu stream is still stopping",
                pending_result.get("status") == "stopping"
                and pending_result.get("session", {}).get("meeting_id") == "tingwu_still_stopping"
                and len(web_server._assistant_notifications) == notifications_before_pending_stop,
                pending_result,
            )

            class FetchTooEarlyProvider:
                finalize_called = False

                def session_status(self, meeting_id=None):
                    return {"meeting_id": meeting_id, "status": "stopping"}

                def finalize_meeting(self, meeting_id, retry_failed_minutes=False):
                    self.finalize_called = True
                    raise AssertionError("finalize_meeting should not be called before capture stops")

            too_early_provider = FetchTooEarlyProvider()
            web_server.tingwu = too_early_provider  # type: ignore[assignment]
            try:
                web_server.api_meeting_realtime_fetch_minutes(
                    {"meeting_id": "tingwu_still_stopping", "run_followup": True},
                    RequestContext(request_id="smoke-fetch", actor="smoke", source_ip="127.0.0.1"),
                )
            except ApiError as exc:
                assert_ok(
                    "Web fetch-minutes is blocked while Tingwu stream is still stopping",
                    exc.code == "meeting_not_stopped" and exc.status == 409 and too_early_provider.finalize_called is False,
                    {"code": exc.code, "status": exc.status, "finalize_called": too_early_provider.finalize_called},
                )
            else:
                raise AssertionError("fetch-minutes before stop completion unexpectedly succeeded")
    finally:
        tingwu_module.TingWuRealtime = original_realtime
        tingwu_module.ArecordPCMStreamer = original_streamer
        tingwu_module.probe_arecord_device = original_probe
        tingwu_module.preflight_arecord_capture = original_capture_probe
    print("smoke_tingwu_provider complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
