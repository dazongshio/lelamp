from __future__ import annotations

import hashlib
import html
import ipaddress
import importlib.util
import json
import math
import os
import re
import secrets
import shlex
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from lelamp.motor_control import LELAMP_MOTOR_ORDER

from ..audio_api import AudioAPIError, OpenAIAudioAPI
from ..config import tingwu_credential_next_actions
from ..dashscope_tts import DashScopeTTS, DashScopeTTSError
from ..documents import DOCUMENT_WORKFLOW_SUFFIXES
from ..elevenlabs_tts import ElevenLabsError, ElevenLabsTTS
from ..hardware_probe import play_audio_file, probe_hardware
from ..tingwu_meeting import PLACEHOLDER_CAPTURE_DEVICES, sanitize_event_payload

LELAMP_CONTROL_MOTORS = LELAMP_MOTOR_ORDER
OFFICIAL_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
OFFICIAL_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

__all__ = ['normalize_voice_assistant_text', 'parse_system_audio_voice_command', 'parse_voice_assistant_control_command', 'assistant_route_is_chat', 'assistant_high_risk_policy', 'local_chat_reply', 'assistant_ack_for_route', 'normalize_task_status', 'normalize_hardware_test_status', '_module_available', 'server_tts_status', 'synthesize_and_play_on_server', 'status_to_audit', 'format_weather_answer']

def hardware_device_details(*args, **kwargs):
    from .. import web_helpers
    return web_helpers.hardware_device_details(*args, **kwargs)

def normalize_voice_assistant_text(text: str) -> str:
    return (
        text.lower()
        .replace(" ", "")
        .replace("，", "")
        .replace("。", "")
        .replace(",", "")
        .replace(".", "")
        .replace("！", "")
        .replace("!", "")
    )


def parse_system_audio_voice_command(text: str) -> dict[str, object] | None:
    normalized = normalize_voice_assistant_text(text)
    if not normalized or "远程" in normalized or not any(marker in normalized for marker in ("音量", "声音", "静音")):
        return None
    if any(marker in normalized for marker in ("取消静音", "解除静音", "打开声音", "恢复声音")):
        return {"muted": False}
    if any(marker in normalized for marker in ("静音", "关闭声音", "关掉声音")):
        return {"muted": True}
    percentage = re.search(r"(\d{1,3})(?:%|％|百分之)", normalized)
    if percentage is None:
        percentage = re.search(r"(?:到|为|成)(\d{1,3})", normalized)
    if percentage is not None:
        return {"volume": max(0, min(100, int(percentage.group(1))))}
    if any(marker in normalized for marker in ("最大音量", "音量最大", "声音最大")):
        return {"volume": 100}
    if any(marker in normalized for marker in ("最小音量", "音量最小", "声音最小")):
        return {"volume": 0}
    if any(marker in normalized for marker in ("大一点", "调大", "增大", "提高", "加大", "声音大", "音量大")):
        return {"delta": 10}
    if any(marker in normalized for marker in ("小一点", "调小", "减小", "降低", "声音小", "音量小")):
        return {"delta": -10}
    return None


def parse_voice_assistant_control_command(text: str) -> str | None:
    normalized = normalize_voice_assistant_text(text)
    if not normalized:
        return None
    status_markers = (
        "语音助手状态",
        "实时语音状态",
        "语音控制状态",
        "语音交互状态",
        "助手语音状态",
    )
    stop_markers = (
        "关闭语音助手",
        "停止语音助手",
        "退出语音助手",
        "关掉语音助手",
        "关闭实时语音",
        "停止实时语音",
        "关闭语音控制",
        "停止语音控制",
        "关闭语音交互",
        "停止语音交互",
    )
    start_markers = (
        "开启语音助手",
        "打开语音助手",
        "启动语音助手",
        "开始语音助手",
        "开启实时语音",
        "打开实时语音",
        "启动实时语音",
        "开始实时语音",
        "开启语音控制",
        "启动语音控制",
        "开始语音控制",
        "启动实施语音",
        "启动实时语音控制",
        "开启语音交互",
        "启动语音交互",
        "开始语音交互",
    )
    if any(marker in normalized for marker in status_markers):
        return "status"
    if any(marker in normalized for marker in stop_markers):
        return "stop"
    if any(marker in normalized for marker in start_markers):
        return "start"
    return None


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
        "unsupported": "unsupported",
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
    if status in {"adapter_ready", "backend_missing", "unsupported", "unavailable", "blocked", "error"}:
        return status
    return "ok" if status == "ok" else status


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

