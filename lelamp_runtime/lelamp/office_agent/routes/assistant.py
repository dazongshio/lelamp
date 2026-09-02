from __future__ import annotations
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import uuid4
from ..dashscope_asr import DashScopeASR, DashScopeASRError
from ..lelamp_voice_skill import parse_lamp_voice_command
from ..meeting_voice_skill import parse_meeting_voice_command
from ..remote_control import parse_remote_voice_command
from ._base import ApiError, NOT_HANDLED, RequestContext, exact_payload

def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)
def assistant_ack_for_route(*a,**kw): return _helper("assistant_ack_for_route")(*a,**kw)
def assistant_high_risk_policy(*a,**kw): return _helper("assistant_high_risk_policy")(*a,**kw)
def assistant_route_is_chat(*a,**kw): return _helper("assistant_route_is_chat")(*a,**kw)
def hardware_device_details(*a,**kw): return _helper("hardware_device_details")(*a,**kw)
def local_chat_reply(*a,**kw): return _helper("local_chat_reply")(*a,**kw)
def normalize_hardware_test_status(*a,**kw): return _helper("normalize_hardware_test_status")(*a,**kw)
def now_iso(*a,**kw): return _helper("now_iso")(*a,**kw)
def parse_system_audio_voice_command(*a,**kw): return _helper("parse_system_audio_voice_command")(*a,**kw)
def parse_voice_assistant_control_command(*a,**kw): return _helper("parse_voice_assistant_control_command")(*a,**kw)
def pid_alive(*a,**kw): return _helper("pid_alive")(*a,**kw)
def probe_hardware(*a,**kw): return _helper("probe_hardware")(*a,**kw)
def record_microphone_sample(*a,**kw): return _helper("record_microphone_sample")(*a,**kw)
def redact_provider_url(*a,**kw): return _helper("redact_provider_url")(*a,**kw)
def require_string(*a,**kw): return _helper("require_string")(*a,**kw)
def runtime_root(*a,**kw): return _helper("runtime_root")(*a,**kw)
def safe_int(*a,**kw): return _helper("safe_int")(*a,**kw)
def server_tts_status(*a,**kw): return _helper("server_tts_status")(*a,**kw)
def status_to_audit(*a,**kw): return _helper("status_to_audit")(*a,**kw)
def synthesize_and_play_on_server(*a,**kw): return _helper("synthesize_and_play_on_server")(*a,**kw)
def update_local_env_value(*a,**kw): return _helper("update_local_env_value")(*a,**kw)

class AssistantRoutesMixin:
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
        assistant_status = self.voice_assistant_process_status()
        return {
            "status": status,
            "provider": "qwen_omni",
            "model": qwen.get("model"),
            "voice": qwen.get("voice"),
            "assistant_process": assistant_status,
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

    def api_voice_assistant_status(self, ctx: RequestContext) -> dict[str, object]:
        status = self.voice_assistant_process_status()
        self.record_audit(
            "voice_assistant.status",
            status_to_audit(str(status.get("status"))),
            "qwen_realtime_voice",
            {"pid": status.get("pid"), "voice": status.get("voice")},
            ctx,
        )
        return status

    def api_voice_assistant_start(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        if not getattr(self.runtime.config, "dashscope_api_key", ""):
            result = {
                "status": "backend_missing",
                "running": False,
                "message": "DASHSCOPE_API_KEY 未配置，不能开启 Qwen 实时语音助手。",
                "provider": self.qwen_realtime_voice_payload(),
            }
            self.record_audit("voice_assistant.start", "backend_missing", "qwen_realtime_voice", result, ctx)
            return result
        with self._voice_assistant_lock:
            current = self.voice_assistant_process_status()
            if bool(current.get("running")):
                result = {**current, "status": "already_running", "message": "语音助手已经在运行。"}
                self.record_audit("voice_assistant.start", "ok", "qwen_realtime_voice", {"pid": result.get("pid")}, ctx)
                return result

            paths = self.voice_assistant_paths()
            paths["dir"].mkdir(parents=True, exist_ok=True)
            log_file = paths["log"].open("ab")
            max_seconds = safe_int(payload.get("max_seconds"), 0)
            command = [
                sys.executable,
                "-u",
                str(runtime_root() / "openclaw_realtime_voice.py"),
                "--profile",
                "omni_realtime_v1",
                "--latency-log",
                str(paths["latency_log"]),
            ]
            if max_seconds > 0:
                command.extend(["--max-seconds", str(max_seconds)])
            try:
                process = self.processes.spawn(
                    command,
                    cwd=str(runtime_root()),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
            except OSError as exc:
                result = {
                    "status": "error",
                    "running": False,
                    "message": f"语音助手启动失败：{exc}",
                    "log": str(paths["log"]),
                }
                self.record_audit("voice_assistant.start", "error", "qwen_realtime_voice", result, ctx)
                return result
            self._voice_assistant_process = process
            self._voice_assistant_started_at = time.time()
            paths["pid"].write_text(str(process.pid), encoding="utf-8")

        time.sleep(0.4)
        status = self.voice_assistant_process_status()
        if not bool(status.get("running")):
            status = {
                **status,
                "status": "error",
                "message": "语音助手进程启动后立即退出，请查看日志。",
            }
            self.record_audit("voice_assistant.start", "error", "qwen_realtime_voice", status, ctx)
            return status
        result = {
            **status,
            "status": "started",
            "message": "已开启语音助手。",
        }
        self.record_audit(
            "voice_assistant.start",
            "ok",
            "qwen_realtime_voice",
            {"pid": result.get("pid"), "voice": result.get("voice"), "log": result.get("log")},
            ctx,
        )
        return result

    def api_voice_assistant_stop(self, ctx: RequestContext) -> dict[str, object]:
        with self._voice_assistant_lock:
            process = self._voice_assistant_process
            pid = process.pid if process is not None and process.poll() is None else self.voice_assistant_saved_pid()
            self._voice_assistant_process = None
            self._voice_assistant_started_at = None
        if pid is None:
            result = {
                **self.voice_assistant_process_status(),
                "status": "not_running",
                "message": "语音助手没有在运行。",
            }
            self.record_audit("voice_assistant.stop", "ok", "qwen_realtime_voice", result, ctx)
            return result
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            result = {
                **self.voice_assistant_process_status(),
                "status": "error",
                "message": "没有权限停止语音助手进程。",
                "error": str(exc),
            }
            self.record_audit("voice_assistant.stop", "error", "qwen_realtime_voice", result, ctx)
            return result
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            if not pid_alive(pid):
                break
            time.sleep(0.1)
        if pid_alive(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.clear_voice_assistant_pid()
        result = {
            **self.voice_assistant_process_status(),
            "status": "stopped",
            "running": False,
            "pid": pid,
            "message": "已关闭语音助手。",
        }
        self.record_audit("voice_assistant.stop", "ok", "qwen_realtime_voice", result, ctx)
        return result

    def api_voice_realtime_voices(self, ctx: RequestContext) -> dict[str, object]:
        payload = self.qwen_realtime_voice_payload()
        self.record_audit(
            "voice.realtime_voices",
            status_to_audit(str(payload.get("status"))),
            "qwen_omni",
            {
                "model": payload.get("model"),
                "voice": payload.get("voice"),
                "voice_count": payload.get("voice_count"),
                "current_voice_supported": payload.get("current_voice_supported"),
            },
            ctx,
        )
        return payload

    def api_voice_realtime_voice_update(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        from ..dashscope_realtime import qwen_omni_realtime_voices, qwen_omni_voice_supported

        voice = require_string(payload, "voice")
        model = str(getattr(self.runtime.config, "dashscope_realtime_model", "qwen3.5-omni-plus-realtime") or "qwen3.5-omni-plus-realtime")
        voices = qwen_omni_realtime_voices(model)
        if not qwen_omni_voice_supported(model, voice):
            self.record_audit(
                "voice.realtime_voice_update",
                "blocked",
                voice,
                {"model": model, "reason": "unsupported_voice", "available": [item["voice"] for item in voices]},
                ctx,
            )
            raise ApiError(
                "unsupported_realtime_voice",
                f"Voice {voice!r} is not supported by {model}.",
                status=400,
                details={"model": model, "available": [item["voice"] for item in voices]},
            )
        env_path = runtime_root() / ".env"
        update_local_env_value(env_path, "DASHSCOPE_REALTIME_VOICE", voice)
        os.environ["DASHSCOPE_REALTIME_VOICE"] = voice
        self.runtime.config = replace(self.runtime.config, dashscope_realtime_voice=voice)
        result = self.qwen_realtime_voice_payload()
        self.record_audit(
            "voice.realtime_voice_update",
            "ok",
            voice,
            {"model": model, "env_key": "DASHSCOPE_REALTIME_VOICE", "env_file": str(env_path)},
            ctx,
        )
        return {
            "status": "ok",
            "message": "Qwen realtime voice updated.",
            "voice": voice,
            "model": model,
            "env_file": str(env_path),
            "realtime": result,
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
        from ..dashscope_asr import DashScopeASR, DashScopeASRError

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

        voice_control_action = parse_voice_assistant_control_command(text)
        if voice_control_action is not None:
            if voice_control_action == "start":
                voice_result = self.api_voice_assistant_start({}, ctx)
                intent = "start_voice_assistant"
            elif voice_control_action == "stop":
                voice_result = self.api_voice_assistant_stop(ctx)
                intent = "stop_voice_assistant"
            else:
                voice_result = self.api_voice_assistant_status(ctx)
                intent = "voice_assistant_status"
            reply = str(voice_result.get("message") or "语音助手状态已更新。")
            status = str(voice_result.get("status") or "completed")
            self.record_audit(
                "assistant_voice_assistant_local_command",
                status_to_audit(status),
                intent,
                {
                    "message_id": message_id,
                    "qwen_omni_called": False,
                    "voice_assistant": {
                        "status": voice_result.get("status"),
                        "pid": voice_result.get("pid"),
                        "running": voice_result.get("running"),
                    },
                },
                ctx,
            )
            return {
                "session_id": session_id,
                "message_id": message_id,
                "route": {
                    "kind": "voice_assistant_control",
                    "intent": intent,
                    "requires_openclaw": False,
                    "requires_confirmation": False,
                    "skill": "qwen_realtime_voice",
                },
                "assistant_message": {
                    "text": reply,
                    "streamed": False,
                    "speak": False,
                    "provider": "voice_assistant_local_control",
                    "provider_status": status,
                    "speech": {"status": "skipped", "reason": "local_voice_assistant_control"},
                },
                "voice_assistant": voice_result,
            }

        meeting_command = parse_meeting_voice_command(text)
        if meeting_command is not None:
            meeting_result = self.api_meeting_voice_command({"text": text}, ctx)
            reply = str(meeting_result.get("reply") or "已执行会议命令。")
            status = str(meeting_result.get("status") or "completed")
            self.record_audit(
                "assistant_meeting_local_command",
                status_to_audit(status),
                meeting_command.label,
                {
                    "message_id": message_id,
                    "command": meeting_result.get("command"),
                    "handled": meeting_result.get("handled"),
                    "qwen_omni_called": False,
                    "ai_assistant_kept_online": True,
                },
                ctx,
            )
            return {
                "session_id": session_id,
                "message_id": message_id,
                "route": {
                    "kind": "meeting_control",
                    "intent": str(meeting_command.action),
                    "requires_openclaw": False,
                    "requires_confirmation": False,
                    "skill": "meeting_voice",
                },
                "assistant_message": {
                    "text": reply,
                    "streamed": False,
                    "speak": False,
                    "provider": "meeting_local_skill",
                    "provider_status": status,
                    "speech": {"status": "skipped", "reason": "local_meeting_command"},
                },
                "meeting_result": meeting_result,
            }

        lamp_command = parse_lamp_voice_command(text)
        audio_command = parse_system_audio_voice_command(text)
        if lamp_command is not None or audio_command is not None:
            lamp_result = self.api_lelamp_voice_command({"text": text}, ctx)
            reply = str(lamp_result.get("reply") or "已执行台灯命令。")
            status = str(lamp_result.get("status") or "completed")
            self.record_audit(
                "assistant_lamp_local_command",
                status_to_audit(status),
                lamp_command.label if lamp_command is not None else "本机音量",
                {
                    "message_id": message_id,
                    "command": lamp_result.get("command"),
                    "handled": lamp_result.get("handled"),
                    "qwen_omni_called": False,
                },
                ctx,
            )
            return {
                "session_id": session_id,
                "message_id": message_id,
                "route": {
                    "kind": "lamp_control" if lamp_command is not None else "local_audio_control",
                    "intent": str(lamp_command.action) if lamp_command is not None else "set_system_volume",
                    "requires_openclaw": False,
                    "requires_confirmation": False,
                    "skill": "lelamp_voice",
                },
                "assistant_message": {
                    "text": reply,
                    "streamed": False,
                    "speak": False,
                    "provider": "lelamp_local_skill" if lamp_command is not None else "local_audio_control",
                    "provider_status": status,
                    "speech": {"status": "skipped", "reason": "local_device_command"},
                },
                "lamp_result": lamp_result,
            }

        remote_command = parse_remote_voice_command(text)
        if remote_command is not None:
            remote_result = self.api_remote_voice_command({"text": text}, ctx)
            reply = str(remote_result.get("reply") or "已执行远程电脑命令。")
            status = str(remote_result.get("status") or "completed")
            self.record_audit(
                "assistant_remote_local_command",
                status_to_audit(status),
                remote_command.label,
                {
                    "message_id": message_id,
                    "command": remote_result.get("command"),
                    "handled": remote_result.get("handled"),
                    "qwen_omni_called": False,
                },
                ctx,
            )
            return {
                "session_id": session_id,
                "message_id": message_id,
                "route": {
                    "kind": "remote_control",
                    "intent": str(remote_command.action),
                    "requires_openclaw": False,
                    "requires_confirmation": False,
                    "skill": "remote_ssh_control",
                },
                "assistant_message": {
                    "text": reply,
                    "streamed": False,
                    "speak": False,
                    "provider": "remote_ssh_local_skill",
                    "provider_status": status,
                    "speech": {"status": "skipped", "reason": "local_remote_command"},
                },
                "remote_result": remote_result,
            }

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

GET={"/api/assistant/providers/status":"api_assistant_providers_status", "/api/assistant/realtime/status":"api_assistant_realtime_status", "/api/voice/status":"api_voice_status", "/api/voice/realtime/voices":"api_voice_realtime_voices", "/api/voice/assistant/status":"api_voice_assistant_status"}
POST={"/api/assistant/manual":"api_manual", "/api/assistant/pi-voice-once":"api_assistant_pi_voice_once", "/api/assistant/realtime/session":"api_assistant_realtime_session", "/api/assistant/speak":"api_assistant_speak", "/api/assistant/confirm":"api_assistant_confirm", "/api/assistant/reject":"api_assistant_reject", "/api/voice/capture-once":"api_voice_capture_once", "/api/voice/realtime/voice":"api_voice_realtime_voice_update", "/api/voice/assistant/start":"api_voice_assistant_start", "/api/voice/conversation/start":"api_voice_conversation_start", "/api/voice/conversation/turn":"api_voice_conversation_turn", "/api/voice/conversation/stop":"api_voice_conversation_stop"}
def dispatch_get(server:Any,path:str,params:dict[str,list[str]],ctx:Any)->Any:
    if path=="/api/assistant/notifications": return server.api_assistant_notifications(params.get("since",[""])[0],ctx)
    if path=="/api/voice/conversation/status": return server.api_voice_conversation_status(params.get("session_id",[""])[0],ctx)
    method=GET.get(path); return NOT_HANDLED if method is None else getattr(server,method)(ctx)
def dispatch_post(server:Any,path:str,payload:dict[str,Any],ctx:Any)->Any:
    if path in {"/api/assistant/message","/api/assistant/text"}: return server.api_assistant_message(payload,ctx)
    if path=="/api/voice/assistant/stop": return server.api_voice_assistant_stop(ctx)
    return exact_payload(server,path,payload,ctx,POST)
