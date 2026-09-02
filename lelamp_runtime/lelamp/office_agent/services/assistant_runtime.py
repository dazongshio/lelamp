from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..hardware_probe import probe_hardware
from ..routes._base import ApiError, RequestContext
from ..tingwu_meeting import redact_sensitive_text, sanitize_event_payload


def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)


def _module_available(*args, **kwargs): return _helper("_module_available")(*args, **kwargs)
def atomic_write_json(*args, **kwargs): return _helper("atomic_write_json")(*args, **kwargs)
def collect_outputs(*args, **kwargs): return _helper("collect_outputs")(*args, **kwargs)
def local_chat_reply(*args, **kwargs): return _helper("local_chat_reply")(*args, **kwargs)
def manual_result_details(*args, **kwargs): return _helper("manual_result_details")(*args, **kwargs)
def normalize_hardware_test_status(*args, **kwargs): return _helper("normalize_hardware_test_status")(*args, **kwargs)
def normalize_result_status(*args, **kwargs): return _helper("normalize_result_status")(*args, **kwargs)
def normalize_task_status(*args, **kwargs): return _helper("normalize_task_status")(*args, **kwargs)
def now_iso(*args, **kwargs): return _helper("now_iso")(*args, **kwargs)
def pid_alive(*args, **kwargs): return _helper("pid_alive")(*args, **kwargs)
def process_cmdline(*args, **kwargs): return _helper("process_cmdline")(*args, **kwargs)
def runtime_root(*args, **kwargs): return _helper("runtime_root")(*args, **kwargs)
def server_tts_status(*args, **kwargs): return _helper("server_tts_status")(*args, **kwargs)
def status_to_audit(*args, **kwargs): return _helper("status_to_audit")(*args, **kwargs)
def summarize_manual_result(*args, **kwargs): return _helper("summarize_manual_result")(*args, **kwargs)
def synthesize_and_play_on_server(*args, **kwargs): return _helper("synthesize_and_play_on_server")(*args, **kwargs)
QWEN_OMNI_VOICE_DOC_URL = "https://help.aliyun.com/zh/model-studio/omni-voice-list"


class AssistantRuntimeMixin:
    def voice_assistant_paths(self) -> dict[str, Path]:
        voice_dir = self.runtime.config.workspace_dir / ".voice"
        return {
            "dir": voice_dir,
            "pid": voice_dir / "realtime_assistant.pid",
            "log": voice_dir / "realtime_assistant.log",
            "latency_log": voice_dir / "realtime_latency.jsonl",
        }

    def voice_assistant_saved_pid(self) -> int | None:
        pid_path = self.voice_assistant_paths()["pid"]
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        if not pid_alive(pid):
            self.clear_voice_assistant_pid()
            return None
        cmdline = " ".join(process_cmdline(pid))
        if "openclaw_realtime_voice" not in cmdline:
            return None
        return pid

    def clear_voice_assistant_pid(self) -> None:
        try:
            self.voice_assistant_paths()["pid"].unlink()
        except FileNotFoundError:
            pass

    def voice_assistant_process_status(self) -> dict[str, object]:
        with self._voice_assistant_lock:
            process = self._voice_assistant_process
            if process is not None and process.poll() is None:
                pid = process.pid
                running = True
            else:
                if process is not None and process.poll() is not None:
                    self._voice_assistant_process = None
                    self._voice_assistant_started_at = None
                pid = self.voice_assistant_saved_pid()
                running = pid is not None
        paths = self.voice_assistant_paths()
        status = "running" if running else "stopped"
        return {
            "status": status,
            "running": running,
            "pid": pid,
            "started_at": self._voice_assistant_started_at,
            "model": self.runtime.config.dashscope_realtime_model,
            "voice": self.runtime.config.dashscope_realtime_voice,
            "mic_device": self.runtime.config.mic_device,
            "speaker_device": self.runtime.config.speaker_device,
            "log": str(paths["log"]),
            "latency_log": str(paths["latency_log"]),
            "message": "语音助手正在监听设备侧麦克风。" if running else "语音助手未运行。",
        }

    def qwen_realtime_voice_payload(self) -> dict[str, object]:
        from ..dashscope_realtime import (
            qwen_omni_realtime_default_voice,
            qwen_omni_realtime_voices,
            qwen_omni_voice_supported,
        )

        config = self.runtime.config
        model = str(getattr(config, "dashscope_realtime_model", "qwen3.5-omni-plus-realtime") or "qwen3.5-omni-plus-realtime")
        current_voice = str(getattr(config, "dashscope_realtime_voice", "") or qwen_omni_realtime_default_voice(model))
        voices = qwen_omni_realtime_voices(model)
        default_voice = qwen_omni_realtime_default_voice(model)
        return {
            "status": "available" if getattr(config, "dashscope_api_key", "") else "backend_missing",
            "provider": "qwen_omni",
            "model": model,
            "voice": current_voice,
            "default_voice": default_voice,
            "current_voice_supported": qwen_omni_voice_supported(model, current_voice),
            "voices": voices,
            "voice_count": len(voices),
            "doc_url": QWEN_OMNI_VOICE_DOC_URL,
            "env_key": "DASHSCOPE_REALTIME_VOICE",
            "env_file": str(runtime_root() / ".env"),
        }

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
        realtime_voice = self.qwen_realtime_voice_payload()
        voice_assistant = self.voice_assistant_process_status()
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
                "assistant_process": voice_assistant,
                "voice": realtime_voice["voice"],
                "default_voice": realtime_voice["default_voice"],
                "voices": realtime_voice["voices"],
                "voice_count": realtime_voice["voice_count"],
                "current_voice_supported": realtime_voice["current_voice_supported"],
                "doc_url": realtime_voice["doc_url"],
                "turn_detection": "server_vad",
                "transcription_model": config.dashscope_realtime_transcription_model,
            },
            "assistant_process": voice_assistant,
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
            from ..dashscope_realtime import DashScopeRealtimeClient, DashScopeRealtimeConfig, DashScopeRealtimeError

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
