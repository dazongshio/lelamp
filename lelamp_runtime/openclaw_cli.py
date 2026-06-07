from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from lelamp.office_agent.llm import LLMError, ResponsesLLM, ResponsesLLMConfig
from lelamp.office_agent.config import PermissionMode
from lelamp.office_agent.desktop_companion import build_desktop_companion
from lelamp.office_agent.hardware import LampHardware
from lelamp.office_agent.lelamp_voice_skill import parse_lamp_voice_command
from lelamp.office_agent.meeting_voice_skill import execute_runtime_meeting_voice_command, parse_meeting_voice_command
from lelamp.office_agent.prompts import OFFICE_AGENT_INSTRUCTIONS
from lelamp.office_agent.projection_viewer import ProjectionPreviewServer, find_free_port
from lelamp.office_agent.runtime import build_runtime
from lelamp.office_agent.shared_space import SharedSpaceServer, SharedSpaceService, find_lan_ip
from lelamp.office_agent.web_console import WebConsoleServer
from lelamp.office_agent.workspace import WorkspaceError


KNOWN_WEATHER_LOCATIONS: dict[str, str] = {
    "北京": "Beijing",
    "上海": "Shanghai",
    "广州": "Guangzhou",
    "深圳": "Shenzhen",
    "杭州": "Hangzhou",
    "南京": "Nanjing",
    "苏州": "Suzhou",
    "成都": "Chengdu",
    "重庆": "Chongqing",
    "武汉": "Wuhan",
    "西安": "Xian",
    "天津": "Tianjin",
    "香港": "Hong Kong",
    "澳门": "Macau",
}

KNOWN_WEATHER_TIMEZONES: dict[str, str] = {
    "北京": "Asia/Shanghai",
    "上海": "Asia/Shanghai",
    "广州": "Asia/Shanghai",
    "深圳": "Asia/Shanghai",
    "杭州": "Asia/Shanghai",
    "南京": "Asia/Shanghai",
    "苏州": "Asia/Shanghai",
    "成都": "Asia/Shanghai",
    "重庆": "Asia/Shanghai",
    "武汉": "Asia/Shanghai",
    "西安": "Asia/Shanghai",
    "天津": "Asia/Shanghai",
    "香港": "Asia/Hong_Kong",
    "澳门": "Asia/Macau",
}


def _first_workspace_file(runtime, suffixes: tuple[str, ...] = (".txt", ".md")) -> str | None:
    for item in runtime.workspace.list_files():
        if item.name.lower().endswith(suffixes):
            return item.name
    return None


def _text_file_from_route(runtime, route) -> str | None:
    slot_filename = route.slots.get("file")
    workspace_files = runtime.workspace.list_files()
    if slot_filename:
        for item in workspace_files:
            if item.name == slot_filename or item.name in slot_filename:
                return item.name
    return _first_workspace_file(runtime)


def _ensure_workspace_file(runtime, path_or_name: str) -> str:
    """Return a workspace filename, importing from allowed roots when needed."""
    try:
        return runtime.workspace.resolve_workspace_file(path_or_name).name
    except WorkspaceError:
        imported = runtime.workspace.import_file(path_or_name)
        return imported.name


def _ensure_workspace_relative_file(runtime, path_or_name: str) -> str:
    """Return a workspace-relative path, preserving subdirectories such as shared_inbox."""
    filesystem_path = Path(path_or_name).expanduser().resolve()
    if filesystem_path.is_file() and filesystem_path.is_relative_to(runtime.workspace.root):
        return str(filesystem_path.relative_to(runtime.workspace.root))
    try:
        path = runtime.workspace.resolve_workspace_file(path_or_name)
        return str(path.relative_to(runtime.workspace.root))
    except WorkspaceError:
        imported = runtime.workspace.import_file(path_or_name)
        return imported.name


def _quoted_or_default(text: str, default: str) -> str:
    quoted = re.findall(r"[“\"]([^”\"]+)[”\"]", text)
    if quoted:
        return quoted[0].strip()
    return default


def _assistant_state_dir(runtime) -> Path:
    path = runtime.config.workspace_dir / ".assistant"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manual_event_log_path(runtime) -> Path:
    return _assistant_state_dir(runtime) / "manual_tool_events.jsonl"


def _reminders_path(runtime) -> Path:
    return _assistant_state_dir(runtime) / "reminders.json"


def record_manual_tool_event(
    runtime,
    *,
    text: str,
    route,
    tool: str,
    args: dict[str, object],
    result: object,
    duration_ms: float,
    status: str = "ok",
) -> None:
    event = {
        "id": str(uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input": text,
        "route": route.as_dict(),
        "tool": {"name": tool, "args": args},
        "status": status,
        "duration_ms": round(duration_ms, 2),
        "result_summary": _jsonable(result),
    }
    path = _manual_event_log_path(runtime)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
        stream.write("\n")
    runtime.audit.record(
        "manual.tool",
        target=tool,
        details={
            "event_log": str(path),
            "duration_ms": round(duration_ms, 2),
            "args": args,
        },
    )


def fetch_weather(city: str, date: str = "today") -> dict[str, object]:
    display_city = clean_weather_city(city)
    lookup_city = KNOWN_WEATHER_LOCATIONS.get(display_city, display_city)
    timezone_name = KNOWN_WEATHER_TIMEZONES.get(display_city, "UTC")
    local_now = datetime.now(ZoneInfo(timezone_name))
    day_offset = {"today": 0, "tomorrow": 1, "day_after_tomorrow": 2}.get(date, 0)
    target_date = (local_now + timedelta(days=day_offset)).date().isoformat()
    query = urllib.parse.urlencode({"format": "j1", "lang": "zh"})
    url = f"https://wttr.in/{urllib.parse.quote(lookup_city)}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "openclaw-manual-weather/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise RuntimeError(f"Weather lookup failed for {display_city}: {exc}") from exc

    current = payload.get("current_condition", [{}])[0]
    weather_days = payload.get("weather", [])
    selected = next((day for day in weather_days if day.get("date") == target_date), None)
    if selected is None:
        selected = weather_days[min(day_offset, max(0, len(weather_days) - 1))] if weather_days else {}
    astronomy = (selected.get("astronomy") or [{}])[0]
    hourly = selected.get("hourly") or []
    noon = hourly[min(4, len(hourly) - 1)] if hourly else {}
    result = {
        "city": display_city,
        "lookup": lookup_city,
        "date": date,
        "timezone": timezone_name,
        "local_time": local_now.isoformat(timespec="seconds"),
        "target_date": target_date,
        "source": "wttr.in",
        "current": {
            "temperature_c": current.get("temp_C"),
            "feels_like_c": current.get("FeelsLikeC"),
            "humidity": current.get("humidity"),
            "wind_kmph": current.get("windspeedKmph"),
            "description": _weather_desc(current),
        },
        "forecast": {
            "date": target_date,
            "provider_forecast_date": selected.get("date"),
            "max_temp_c": selected.get("maxtempC"),
            "min_temp_c": selected.get("mintempC"),
            "avg_temp_c": selected.get("avgtempC"),
            "sunrise": astronomy.get("sunrise"),
            "sunset": astronomy.get("sunset"),
            "midday_chance_of_rain": noon.get("chanceofrain"),
            "midday_description": _weather_desc(noon),
        },
    }
    return result


def clean_weather_city(value: str) -> str:
    city = value.strip()
    city = re.sub(r"^(查看|查一下|查询|看下|看看|帮我看下|帮我查下|请|我想)", "", city).strip()
    city = re.sub(r"(今天|明天|后天|现在|当前|目前|会不会|是否|有没有|的|天气|气温|下雨|降雨|怎么样|如何)+$", "", city).strip()
    for known in sorted(KNOWN_WEATHER_LOCATIONS, key=len, reverse=True):
        if known in city:
            return known
    return city or "深圳"


def _weather_desc(item: dict) -> str:
    descriptions = item.get("lang_zh") or item.get("weatherDesc") or []
    if descriptions and isinstance(descriptions[0], dict):
        return str(descriptions[0].get("value", ""))
    return ""


def web_search(query: str, *, limit: int = 5) -> dict[str, object]:
    encoded = urllib.parse.urlencode({"q": query})
    url = f"https://duckduckgo.com/html/?{encoded}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 OpenClawManualSearch/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            body = response.read().decode("utf-8", "replace")
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        return {
            "query": query,
            "source": "duckduckgo.com/html",
            "status": "unavailable",
            "error": str(exc),
            "results": [],
            "note": "The local runtime cannot reach the internet right now; retry when network is available or swap in a production search provider.",
        }

    results: list[dict[str, str]] = []
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
        re.S,
    )
    for match in pattern.finditer(body):
        href = html.unescape(match.group("href"))
        title = _strip_html(match.group("title"))
        snippet = _strip_html(match.group("snippet"))
        parsed = urllib.parse.urlparse(href)
        if parsed.netloc == "duckduckgo.com":
            qs = urllib.parse.parse_qs(parsed.query)
            href = qs.get("uddg", [href])[0]
        if title and href:
            results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= limit:
            break

    return {
        "query": query,
        "source": "duckduckgo.com/html",
        "results": results,
        "note": "Manual search returns source snippets only; answer synthesis should cite these URLs.",
    }


def _strip_html(value: str) -> str:
    value = re.sub(r"<.*?>", "", value, flags=re.S)
    return html.unescape(value).strip()


def maybe_complete_with_llm(runtime, result: dict[str, object]) -> dict[str, object]:
    if result.get("status") != "needs_llm" or not result.get("llm_prompt"):
        return result
    if not runtime.config.openai_api_key:
        return result

    llm = ResponsesLLM(
        ResponsesLLMConfig(
            api_key=runtime.config.openai_api_key,
            base_url=runtime.config.openai_base_url,
            model=runtime.config.openai_model,
            reasoning_effort="low",
        )
    )
    try:
        answer = llm.complete(
            instructions=(
                "你是小爱同学风格的 LeLamp 桌面助手。用简体中文直接回答，"
                "保持简短、自然、适合语音朗读。"
            ),
            user_input=str(result["llm_prompt"]),
            context=context_snapshot(runtime),
            timeout=60,
        )
    except LLMError as exc:
        result["llm_error"] = str(exc)
        return result
    completed = dict(result)
    completed["status"] = "answered"
    completed["answer"] = answer
    completed["source"] = "ResponsesLLM"
    return completed


def load_reminders(runtime) -> list[dict[str, object]]:
    path = _reminders_path(runtime)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def save_reminders(runtime, reminders: list[dict[str, object]]) -> Path:
    path = _reminders_path(runtime)
    path.write_text(json.dumps(reminders, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def create_reminder(runtime, text: str) -> dict[str, object]:
    reminder_text = _extract_reminder_text(text)
    remind_at = _extract_reminder_time(text)
    reminders = load_reminders(runtime)
    reminder = {
        "id": str(uuid4()),
        "text": reminder_text,
        "remind_at": remind_at.isoformat() if remind_at else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "source": "manual",
    }
    reminders.append(reminder)
    path = save_reminders(runtime, reminders)
    return {"reminder": reminder, "store_path": str(path), "count": len(reminders)}


def list_reminders(runtime, *, include_done: bool = False) -> dict[str, object]:
    reminders = load_reminders(runtime)
    visible = reminders if include_done else [item for item in reminders if item.get("status") != "done"]
    visible.sort(key=lambda item: item.get("remind_at") or "")
    return {
        "count": len(visible),
        "store_path": str(_reminders_path(runtime)),
        "reminders": visible,
    }


def get_calendar_today(runtime) -> dict[str, object]:
    pending = list_reminders(runtime)["reminders"]
    today = datetime.now().date()
    events = []
    for item in pending:
        raw_time = item.get("remind_at")
        if not raw_time:
            continue
        try:
            event_time = datetime.fromisoformat(str(raw_time)).astimezone()
        except ValueError:
            continue
        if event_time.date() == today:
            events.append(
                {
                    "time": event_time.strftime("%H:%M"),
                    "title": item.get("text", ""),
                    "source": "local_reminder",
                    "id": item.get("id"),
                }
            )
    return {
        "date": today.isoformat(),
        "source": "local_reminders",
        "events": events,
        "note": "No OS calendar adapter is configured yet; this returns local manual reminders.",
    }


def capture_screen_snapshot(runtime) -> dict[str, object]:
    out_path = runtime.workspace.path_for_new_file(
        f"screen_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    )
    commands = [
        ["gnome-screenshot", "-f", str(out_path)],
        ["grim", str(out_path)],
        ["import", "-window", "root", str(out_path)],
        ["spectacle", "-b", "-n", "-o", str(out_path)],
    ]
    attempted = []
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        attempted.append(command[0])
        try:
            subprocess.run(command, check=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            attempted.append(f"{command[0]}:{type(exc).__name__}")
            continue
        if out_path.exists() and out_path.stat().st_size > 0:
            runtime.audit.record("desktop.capture_screen", target=str(out_path), details={"command": command[0]})
            return {
                "status": "captured",
                "path": str(out_path),
                "bytes": out_path.stat().st_size,
                "command": command[0],
            }
    runtime.audit.record(
        "desktop.capture_screen",
        status="blocked",
        target=str(out_path),
        details={"attempted": attempted, "reason": "no supported screenshot backend"},
    )
    return {
        "status": "unavailable",
        "path": str(out_path),
        "attempted": attempted,
        "install_hint": "Install gnome-screenshot, grim, ImageMagick import, or spectacle to enable screen snapshots.",
    }


def _extract_reminder_text(text: str) -> str:
    quoted = _quoted_or_default(text, "")
    if quoted:
        return quoted
    cleaned = re.sub(r"(帮我|请|设置|创建|添加|一个|提醒|闹钟|定时|到时候|记得)", "", text).strip()
    cleaned = re.sub(r"(今天|明天|后天)?\s*\d{1,2}[:：点]\d{0,2}", "", cleaned).strip()
    cleaned = re.sub(r"^(我|我需要|我要)", "", cleaned).strip()
    return cleaned or text


def _extract_reminder_time(text: str) -> datetime | None:
    now = datetime.now().astimezone()
    base = now
    if "后天" in text:
        base = now + timedelta(days=2)
    elif "明天" in text:
        base = now + timedelta(days=1)
    elif "今天" in text:
        base = now

    relative = re.search(r"(\d+)\s*(分钟|小时|天)后", text)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        if unit == "分钟":
            return now + timedelta(minutes=amount)
        if unit == "小时":
            return now + timedelta(hours=amount)
        return now + timedelta(days=amount)

    time_match = re.search(r"(\d{1,2})(?:[:：点](\d{1,2})?)?", text)
    if not time_match:
        return None
    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or 0)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if "明天" not in text and "后天" not in text and candidate < now:
        candidate += timedelta(days=1)
    return candidate


def run_lamp_voice_command(runtime, text: str) -> dict[str, object]:
    command = parse_lamp_voice_command(text)
    if command is None:
        return runtime.lelamp_voice.handle_text(text)
    if command.action in {"start_follow", "stop_follow", "status"}:
        return runtime.lelamp_voice.handle_text(text)
    with LampHardware(
        enabled=runtime.config.enable_hardware,
        port=runtime.config.hardware_port,
        lamp_id=runtime.config.lamp_id,
        audit=runtime.audit,
        rgb_enabled=runtime.config.enable_rgb,
    ) as hardware:
        runtime.lelamp_voice.set_hardware(hardware)
        return runtime.lelamp_voice.handle_text(text)


def run_meeting_voice_command(runtime, text: str) -> dict[str, object]:
    return runtime.meeting_voice.handle_text(
        text,
        executor=lambda command, raw_text: execute_runtime_meeting_voice_command(runtime, command, raw_text),
    )


def run_manual_agent(runtime, text: str) -> dict[str, object]:
    route = runtime.intent_router.route(text)
    started = time.perf_counter()
    result: object
    tool = "none"
    args: dict[str, object] = {}

    if route.intent == "p0_status":
        tool = "get_p0_status"
        result = runtime.p0.status()
    elif route.intent == "meeting_voice_control":
        tool = "control_meeting_by_voice"
        result = run_meeting_voice_command(runtime, text)
    elif route.intent == "lelamp_voice_control":
        tool = "control_lamp_by_voice"
        result = run_lamp_voice_command(runtime, text)
    elif route.intent == "lelamp_capabilities":
        tool = "list_lelamp_capabilities"
        result = runtime.lelamp_experience.capability_map()
    elif route.intent == "desk_observation":
        tool = "observe_desk_once"
        result = runtime.camera_observer.observe_once()
    elif route.intent == "environment_event":
        tool = "report_environment_event"
        event_type = "environment_observation"
        if "有人" in text or "靠近" in text:
            event_type = "person_nearby"
        elif "太暗" in text or "光线" in text:
            event_type = "ambient_too_dark"
        elif "遮挡" in text:
            event_type = "projection_blocked"
        elif "会议" in text:
            event_type = "meeting_likely_started"
        args = {"event_type": event_type, "description": text}
        result = runtime.lelamp_experience.report_affordance_event(event_type, text)
    elif route.intent == "xiaoai_features":
        tool = "list_xiaoai_features"
        result = runtime.xiaoai.feature_matrix()
    elif route.intent == "security":
        tool = "get_security_status"
        result = runtime.security_status()
    elif route.intent == "weather":
        tool = "get_weather"
        city = clean_weather_city(route.slots.get("city") or "深圳")
        date = route.slots.get("date") or "today"
        args = {"city": city, "date": date}
        result = fetch_weather(city, date)
    elif route.intent == "reminder":
        if route.action == "list_reminders":
            tool = "list_reminders"
            result = runtime.daily.list_reminders()
        else:
            tool = "create_reminder"
            args = {"text": text}
            result = runtime.daily.create_reminder(text)
    elif route.intent == "calendar":
        if route.action == "create_calendar_event":
            tool = "create_local_calendar_event"
            args = {"title": text}
            result = runtime.daily.create_event(text)
        else:
            tool = "get_local_agenda"
            date = route.slots.get("date") or "today"
            args = {"date": date}
            result = runtime.daily.agenda(date)
    elif route.intent == "local_search":
        tool = "search_local_content"
        query = route.slots.get("quoted_topic") or text
        args = {"query": query}
        result = runtime.file_search.search(query)
    elif route.intent == "web_search":
        tool = "web_search"
        query = route.slots.get("quoted_topic") or re.sub(
            r"^(帮我|请|联网搜索|网上查|搜索|查资料|查一下)\s*",
            "",
            text,
        ).strip()
        args = {"query": query}
        result = web_search(query)
    elif route.intent == "xiaoai_utility":
        if route.action == "web_search":
            tool = "web_search"
            query = route.slots.get("quoted_topic") or re.sub(
                r"^(帮我|请|查一下|查询|看看)\s*",
                "",
                text,
            ).strip()
            args = {"query": query}
            result = web_search(query)
        else:
            tool = "answer_xiaoai_utility"
            args = {"text": text}
            result = maybe_complete_with_llm(runtime, runtime.xiaoai.answer_utility(text))
    elif route.intent == "document":
        filename = _text_file_from_route(runtime, route)
        if not filename:
            raise SystemExit("No workspace text file found. Import one first with: openclaw_cli.py import /path/file.md")
        args = {"filename": filename}
        if route.action == "report_outline":
            tool = "create_report_outline"
            topic = route.slots.get("quoted_topic") or _quoted_or_default(text, "手动测试报告")
            args["topic"] = topic
            result = runtime.documents.create_report_outline([filename], topic)
        elif route.action == "key_data_table":
            tool = "extract_key_data_table"
            result = runtime.documents.extract_table_from_text(filename)
        elif "总结" in text or "摘要" in text or "summarize" in text.lower():
            tool = "summarize_workspace_document"
            style = "outline" if ("提纲" in text or "outline" in text.lower()) else "brief"
            args["style"] = style
            result = runtime.documents.summarize_text_file(filename, style=style)
        else:
            tool = "analyze_workspace_document"
            result = runtime.documents.analyze_text_file(filename)
    elif route.intent == "general_office_chat" and route.slots.get("file"):
        filename = _text_file_from_route(runtime, route)
        if not filename:
            raise SystemExit("No workspace text file found. Import one first with: openclaw_cli.py import /path/file.md")
        if "总结" in text or "摘要" in text or "summarize" in text.lower():
            tool = "summarize_workspace_document"
            style = "outline" if ("提纲" in text or "outline" in text.lower()) else "brief"
            args = {"filename": filename, "style": style}
            result = runtime.documents.summarize_text_file(filename, style=style)
        else:
            tool = "analyze_workspace_document"
            args = {"filename": filename}
            result = runtime.documents.analyze_text_file(filename)
    elif route.intent == "projection":
        if route.action == "summarize_ppt_page":
            tool = "summarize_current_ppt_page"
            result = {
                "status": "needs_confirmation",
                "message": "请在 Projection 页面点击“捕获并总结当前页”，由浏览器弹出屏幕选择窗口后再总结 PPT 当前页。",
                "endpoint": "/api/projection/summarize-ppt-page",
                "safety": "默认不解析投影内容；只有用户主动选择屏幕/窗口后才截取一帧并调用视觉 API。",
            }
        elif route.action == "render_countdown":
            tool = "render_lamp_countdown"
            title = route.slots.get("quoted_topic") or "倒计时"
            seconds = int(route.slots.get("seconds") or "300")
            args = {"title": title, "seconds": seconds}
            result = runtime.lelamp_experience.render_countdown(title, seconds, text)
        else:
            tool = "render_projection_markdown"
            title = route.slots.get("quoted_topic") or "手动投影测试"
            body = text
            args = {"title": title, "mode": "status"}
            result = runtime.projection.render_markdown(title, body, mode="status")
    elif route.intent == "screen_snapshot":
        if route.action == "summarize_screen":
            tool = "summarize_current_screen"
            result = runtime.screen.summarize_current_screen()
        else:
            tool = "capture_screen_context"
            result = runtime.screen.capture_screen()
    elif route.intent == "meeting":
        if route.action == "generate_minutes":
            tool = "generate_meeting_minutes"
            result = runtime.meeting.generate_minutes()
        elif route.action == "meeting_followup_package":
            tool = "generate_meeting_followup_package"
            recipient = route.slots.get("recipient") or "待填写收件人"
            args = {"recipient": recipient}
            result = runtime.p0.generate_meeting_followup_package(recipient=recipient)
        elif route.action == "disable_meeting_mode":
            tool = "disable_meeting_mode"
            result = runtime.meeting.disable()
        else:
            tool = "enable_meeting_mode"
            title = route.slots.get("quoted_topic") or "手动测试会议"
            participants = ["用户", "OpenClaw"]
            args = {"title": title, "participants": participants}
            result = runtime.meeting.enable(title, participants)
    elif route.intent == "email_draft":
        filename = _text_file_from_route(runtime, route)
        if not filename:
            raise SystemExit("No workspace text file found. Import one first with: openclaw_cli.py import /path/file.md")
        tool = "draft_email_from_note"
        recipient = route.slots.get("recipient") or "待填写收件人"
        intent = route.slots.get("quoted_topic") or text
        args = {"filename": filename, "recipient": recipient, "intent": intent}
        source = runtime.workspace.read_text(filename, max_chars=60000)
        result = runtime.p0.generate_followup_email_with_api(
            title=intent,
            recipient=recipient,
            decisions=[],
            action_items=[],
            minutes_text=source,
        )
    elif route.intent == "smart_home":
        tool = "control_smart_home_device"
        args = {"command": text}
        result = runtime.smart_home.control(text)
    elif route.intent == "media":
        tool = "control_desktop_media"
        args = {"command": text}
        result = runtime.desktop.media_control(text)
    elif route.intent == "mobile_bridge":
        tool = "request_mobile_bridge"
        args = {"request": text}
        result = runtime.mobile_bridge.request(text, authorized=False)
    elif route.intent == "desktop":
        args = {"text": text, "action": route.action}
        if route.action == "open_app":
            tool = "open_desktop_app"
            app_name = route.slots.get("app") or text
            args["app_name"] = app_name
            result = runtime.desktop.open_app(app_name)
        elif route.action == "open_url":
            tool = "open_desktop_url"
            url = route.slots.get("url") or text
            args["url_or_query"] = url
            result = runtime.desktop.open_url(url)
        elif route.action == "find_file":
            tool = "find_local_file"
            query = route.slots.get("file_query") or route.slots.get("file") or text
            args["query"] = query
            result = runtime.desktop.find_files(query)
        elif route.action == "open_file":
            tool = "open_local_file"
            query = route.slots.get("file_query") or route.slots.get("file") or text
            args["path_or_query"] = query
            result = runtime.desktop.open_file(query)
        elif route.action == "set_volume":
            tool = "set_system_volume"
            args["command"] = text
            result = runtime.desktop.set_volume(text)
        else:
            tool = "request_desktop_operation"
            args = {"task_description": text}
            planned = runtime.planner.plan(text)
            task = runtime.desktop_tasks.request_task(
                text,
                [str(step.get("action") or step) for step in planned.get("steps", [])],
                source="manual_agent",
            )
            result = {
                "permission": runtime.desktop.request_operation(text),
                "task": task,
            }
    else:
        tool = "plan_office_task"
        args = {"request": text}
        result = runtime.planner.plan(text)

    duration_ms = (time.perf_counter() - started) * 1000
    record_manual_tool_event(
        runtime,
        text=text,
        route=route,
        tool=tool,
        args=args,
        result=result,
        duration_ms=duration_ms,
    )
    return {
        "input": text,
        "route": route.as_dict(),
        "tool": {"name": tool, "args": args},
        "result": result,
        "event_log": str(_manual_event_log_path(runtime)),
    }


def context_snapshot(runtime) -> dict[str, object]:
    return {
        "security": {
            "permission_mode": runtime.config.permission_mode.value,
            "workspace_dir": str(runtime.config.workspace_dir),
            "allowed_roots": [str(path) for path in runtime.config.allowed_roots],
            "audit_log_path": str(runtime.config.audit_log_path),
            "desktop_backend": runtime.config.desktop_backend,
            "smart_home_provider": runtime.config.smart_home_provider,
        },
        "p0": runtime.p0.status(),
        "lelamp": runtime.lelamp_experience.capability_map(),
        "skills": runtime.skills.list_skills(),
        "xiaoai_features": runtime.xiaoai.feature_matrix(),
        "smart_home": runtime.smart_home.status(),
        "workspace_files": [
            {
                "name": item.name,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            }
            for item in runtime.workspace.list_files()
        ],
        "meeting": runtime.meeting.status(),
        "recent_memory": runtime.memory.list_recent(5),
    }


def run_tool(runtime, tool: str, args: argparse.Namespace) -> object:
    if tool == "lelamp":
        if args.lelamp_command == "status":
            return runtime.lelamp_experience.capability_map()
        if args.lelamp_command == "voice":
            return run_lamp_voice_command(runtime, args.text)
        if args.lelamp_command == "state":
            return runtime.lelamp_experience.state_cue(args.state)
        if args.lelamp_command == "observe":
            return runtime.camera_observer.observe_once(camera_index=args.camera_index)
        if args.lelamp_command == "env":
            return runtime.environment.ingest(
                {
                    "presence": args.presence,
                    "motion": args.motion,
                    "lux": args.lux,
                    "sound_level": args.sound_level,
                    "speech_active": args.speech_active,
                    "people_count": args.people_count,
                    "projector_blocked": args.projector_blocked,
                    "calendar_event_now": args.calendar_event_now,
                }
            )
        if args.lelamp_command == "countdown":
            return runtime.lelamp_experience.render_countdown(args.title, args.seconds, args.message)
        if args.lelamp_command == "actions":
            return runtime.lelamp_experience.render_action_confirmation(
                args.title,
                args.action or [],
                decisions=args.decision or [],
            )
        raise ValueError(f"Unknown lelamp command: {args.lelamp_command}")
    if tool == "meeting":
        if args.meeting_command == "voice":
            return run_meeting_voice_command(runtime, args.text)
        raise ValueError(f"Unknown meeting command: {args.meeting_command}")
    if tool == "p0":
        return runtime.p0.status()
    if tool == "skills":
        return runtime.skills.list_skills()
    if tool == "security":
        return runtime.security_status()
    if tool == "readiness":
        return runtime.readiness_report()
    if tool == "plan":
        return runtime.planner.plan(args.text, args.file or [])
    if tool == "import":
        try:
            return runtime.workspace.import_file(args.path).__dict__
        except WorkspaceError as exc:
            return {"status": "blocked", "path": args.path, "reason": str(exc)}
    if tool == "search":
        return runtime.file_search.search(args.query, limit=args.limit)
    if tool == "agenda":
        return runtime.daily.agenda(args.date)
    if tool == "remind":
        return runtime.daily.create_reminder(args.text)
    if tool == "event":
        return runtime.daily.create_event(args.title, participants=args.participant or [])
    if tool == "screen":
        return runtime.screen.summarize_current_screen() if args.summary else runtime.screen.capture_screen()
    if tool == "desktop-task":
        if args.desktop_task_command == "request":
            steps = args.step or [args.goal]
            return runtime.desktop_tasks.request_task(args.goal, steps, source="cli")
        if args.desktop_task_command == "list":
            return runtime.desktop_tasks.list_tasks(limit=args.limit)
        if args.desktop_task_command == "status":
            return runtime.desktop_tasks.update_status(
                args.task_id,
                args.status,
                actor=args.actor,
                reason=args.reason,
            )
        raise ValueError(f"Unknown desktop-task command: {args.desktop_task_command}")
    if tool == "desktop-companion":
        service = build_desktop_companion(
            workspace_dir=Path(args.workspace_dir).expanduser().resolve(),
            audit_log_path=Path(args.audit_log).expanduser().resolve(),
            backend=args.backend,
            permission_mode=PermissionMode(args.permission_mode),
        )
        if args.desktop_companion_command == "status":
            return service.status()
        if args.desktop_companion_command == "list-approved":
            return service.list_approved_tasks(limit=args.limit)
        if args.desktop_companion_command == "execute":
            return service.execute_task(args.task_id, actor=args.actor)
        raise ValueError(f"Unknown desktop-companion command: {args.desktop_companion_command}")
    if tool == "scan":
        if args.scan_command == "register":
            image_name = _ensure_workspace_relative_file(runtime, args.image)
            return runtime.scanning.register_scan_image(image_name, args.document_type)
        if args.scan_command == "ocr":
            image_name = _ensure_workspace_relative_file(runtime, args.image)
            return runtime.scanning.run_ocr(image_name, args.language)
        if args.scan_command == "summarize-ocr":
            filename = _ensure_workspace_relative_file(runtime, args.filename)
            return runtime.scanning.summarize_ocr_text(filename)
        if args.scan_command == "business-card":
            filename = _ensure_workspace_relative_file(runtime, args.filename)
            return runtime.scanning.analyze_business_card_text(filename)
        raise ValueError(f"Unknown scan command: {args.scan_command}")
    if tool == "summarize":
        return runtime.documents.summarize_text_file(args.filename, args.style)
    if tool == "analyze":
        return runtime.documents.analyze_text_file(args.filename)
    if tool == "minutes":
        if args.transcript:
            try:
                transcript_name = _ensure_workspace_file(runtime, args.transcript)
            except WorkspaceError as exc:
                return {"status": "blocked", "transcript": args.transcript, "reason": str(exc)}
            runtime.meeting.parse_transcript_file(transcript_name, args.title, args.participant or [])
        return runtime.meeting.generate_minutes()
    if tool == "followup":
        if args.transcript:
            try:
                transcript_name = _ensure_workspace_file(runtime, args.transcript)
            except WorkspaceError as exc:
                return {"status": "blocked", "transcript": args.transcript, "reason": str(exc)}
            runtime.meeting.parse_transcript_file(transcript_name, args.title, args.participant or [])
        return runtime.p0.generate_meeting_followup_package(
            recipient=args.recipient,
            create_reminders=not args.no_reminders,
            render_projection=not args.no_projection,
        )
    if tool == "project":
        return runtime.projection.render_markdown(args.title, args.body, args.mode)
    if tool == "project-screen":
        host = args.host
        port = find_free_port(host, args.port)
        server = ProjectionPreviewServer(
            runtime.config.projection_dir,
            runtime.audit,
            refresh_seconds=args.refresh_seconds,
        )
        server.serve(host=host, port=port, open_browser=args.open)
        return {"status": "stopped", "host": host, "port": port}
    if tool == "share-list":
        service = SharedSpaceService(runtime.workspace, runtime.audit)
        return {
            "shared_inbox": str(service.inbox_dir),
            "files": [item.as_dict() for item in service.list_files()],
        }
    if tool == "share-note":
        service = SharedSpaceService(runtime.workspace, runtime.audit)
        item = service.put_note(args.title, args.text, source="cli_note")
        return {"shared_inbox": str(service.inbox_dir), "file": item.as_dict()}
    if tool == "share-space":
        service = SharedSpaceService(runtime.workspace, runtime.audit)
        host = args.host
        port = find_free_port(host, args.port)
        token = args.token or None
        if host == "0.0.0.0" and not token:
            token = None
        server = SharedSpaceServer(
            service,
            runtime.audit,
            token=token,
            max_upload_bytes=args.max_mb * 1024 * 1024,
        )
        lan_ip = find_lan_ip()
        if lan_ip and host == "0.0.0.0":
            print(f"Detected LAN URL: http://{lan_ip}:{port}/?token={server.token}")
        server.serve(host=host, port=port)
        return {"status": "stopped", "host": host, "port": port}
    if tool == "web-console":
        host = args.host
        port = find_free_port(host, args.port)
        server = WebConsoleServer(
            runtime,
            token=args.token or None,
            max_upload_bytes=args.max_mb * 1024 * 1024,
            projection_preview_port=args.projection_preview_port,
        )
        server.serve(host=host, port=port)
        return {"status": "stopped", "host": host, "port": port}
    raise ValueError(f"Unknown tool: {tool}")


def load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    load_local_env(Path(".env"))
    load_local_env(Path(".env.tingwu.local"))
    parser = argparse.ArgumentParser(description="OpenClaw text AI CLI with Responses API LLM")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask = subparsers.add_parser("ask", help="Ask the gpt-5.5 AI with OpenClaw context")
    ask.add_argument("text", nargs="+")

    manual = subparsers.add_parser("manual", help="Manually test route -> tool execution without the LLM")
    manual.add_argument("text", nargs="+")

    lelamp = subparsers.add_parser("lelamp", help="LeLamp-specific affordance, projection, camera, and environment tools")
    lelamp_sub = lelamp.add_subparsers(dest="lelamp_command", required=True)

    lelamp_sub.add_parser("status", help="Show LeLamp-specific capability map")

    lelamp_voice = lelamp_sub.add_parser("voice", help="Parse and execute a LeLamp voice command")
    lelamp_voice.add_argument("text")

    lelamp_state = lelamp_sub.add_parser("state", help="Show RGB and movement cue for an assistant state")
    lelamp_state.add_argument("state")

    lelamp_observe = lelamp_sub.add_parser("observe", help="Capture one camera frame and infer desk scene events")
    lelamp_observe.add_argument("--camera-index", type=int, default=0)

    lelamp_env = lelamp_sub.add_parser("env", help="Infer environment scene events from sensor readings")
    lelamp_env.add_argument("--presence", action="store_true")
    lelamp_env.add_argument("--motion", action="store_true")
    lelamp_env.add_argument("--lux", type=float)
    lelamp_env.add_argument("--sound-level", type=float)
    lelamp_env.add_argument("--speech-active", action="store_true")
    lelamp_env.add_argument("--people-count", type=int)
    lelamp_env.add_argument("--projector-blocked", action="store_true")
    lelamp_env.add_argument("--calendar-event-now", action="store_true")

    lelamp_countdown = lelamp_sub.add_parser("countdown", help="Render a projection countdown card")
    lelamp_countdown.add_argument("title")
    lelamp_countdown.add_argument("seconds", type=int)
    lelamp_countdown.add_argument("--message", default="")

    lelamp_actions = lelamp_sub.add_parser("actions", help="Render a projection action-confirmation card")
    lelamp_actions.add_argument("title")
    lelamp_actions.add_argument("--action", action="append")
    lelamp_actions.add_argument("--decision", action="append")

    meeting = subparsers.add_parser("meeting", help="Meeting local voice commands and workflow helpers")
    meeting_sub = meeting.add_subparsers(dest="meeting_command", required=True)
    meeting_voice = meeting_sub.add_parser("voice", help="Parse and execute a meeting voice command")
    meeting_voice.add_argument("text")

    subparsers.add_parser("p0", help="Show P0 office assistant capability status")
    subparsers.add_parser("skills", help="Show OpenClaw office skills with permission and I/O contracts")
    subparsers.add_parser("security", help="Show the active sandbox, allowed roots, audit log, and desktop backend")
    subparsers.add_parser("readiness", help="Show MVP readiness, backend gaps, and hardware validation gaps")

    plan = subparsers.add_parser("plan", help="Plan an office task without calling the LLM")
    plan.add_argument("text")
    plan.add_argument("--file", action="append")

    import_file = subparsers.add_parser("import", help="Import a file into the workspace")
    import_file.add_argument("path")

    search = subparsers.add_parser("search", help="Search allowed roots by filename and text content")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)

    agenda = subparsers.add_parser("agenda", help="Show local agenda and reminders")
    agenda.add_argument("date", nargs="?", default="today")

    remind = subparsers.add_parser("remind", help="Create a local reminder")
    remind.add_argument("text")

    event = subparsers.add_parser("event", help="Create a local calendar event with conflict detection")
    event.add_argument("title")
    event.add_argument("--participant", action="append")

    screen = subparsers.add_parser("screen", help="Capture or summarize current screen")
    screen.add_argument("--summary", action="store_true")

    desktop_task = subparsers.add_parser("desktop-task", help="Create and review auditable desktop task requests")
    desktop_task_sub = desktop_task.add_subparsers(dest="desktop_task_command", required=True)

    desktop_task_request = desktop_task_sub.add_parser("request", help="Request a desktop task for review without executing it")
    desktop_task_request.add_argument("goal")
    desktop_task_request.add_argument("--step", action="append", help="Proposed execution step; repeatable")

    desktop_task_list = desktop_task_sub.add_parser("list", help="List desktop task requests")
    desktop_task_list.add_argument("--limit", type=int, default=50)

    desktop_task_status = desktop_task_sub.add_parser("status", help="Update a desktop task review status")
    desktop_task_status.add_argument("task_id")
    desktop_task_status.add_argument("status", choices=["requested", "approved", "rejected", "done", "blocked"])
    desktop_task_status.add_argument("--actor", default="user")
    desktop_task_status.add_argument("--reason", default="")

    desktop_companion = subparsers.add_parser(
        "desktop-companion",
        help="Run an office-computer companion against a shared OpenClaw workspace",
    )
    desktop_companion.add_argument(
        "--workspace-dir",
        default=os.getenv("OPENCLAW_COMPANION_WORKSPACE", "workspace"),
        help="Shared OpenClaw workspace mounted on this office computer",
    )
    desktop_companion.add_argument(
        "--audit-log",
        default=os.getenv("OPENCLAW_COMPANION_AUDIT_LOG", "logs/desktop_companion_audit.jsonl"),
    )
    desktop_companion.add_argument(
        "--backend",
        default=os.getenv("OPENCLAW_COMPANION_DESKTOP_BACKEND", "audit_only"),
        choices=["audit_only", "local", "xdg", "linux"],
    )
    desktop_companion.add_argument(
        "--permission-mode",
        default=os.getenv("OPENCLAW_COMPANION_PERMISSION_MODE", "sandbox"),
        choices=["sandbox", "full_control"],
    )
    desktop_companion_sub = desktop_companion.add_subparsers(dest="desktop_companion_command", required=True)
    desktop_companion_sub.add_parser("status", help="Show companion safety status")
    desktop_companion_list = desktop_companion_sub.add_parser("list-approved", help="List approved tasks")
    desktop_companion_list.add_argument("--limit", type=int, default=50)
    desktop_companion_execute = desktop_companion_sub.add_parser("execute", help="Plan or execute an approved task")
    desktop_companion_execute.add_argument("task_id")
    desktop_companion_execute.add_argument("--actor", default="desktop_companion")

    scan = subparsers.add_parser("scan", help="Register paper scans and run local OCR adapters")
    scan_sub = scan.add_subparsers(dest="scan_command", required=True)

    scan_register = scan_sub.add_parser("register", help="Register an imported workspace image as a paper scan")
    scan_register.add_argument("image")
    scan_register.add_argument("--document-type", default="document")

    scan_ocr = scan_sub.add_parser("ocr", help="Run OCR when PaddleOCR or tesseract is installed")
    scan_ocr.add_argument("image")
    scan_ocr.add_argument("--language", default="ch")

    scan_summary = scan_sub.add_parser("summarize-ocr", help="Summarize OCR text already present in the workspace")
    scan_summary.add_argument("filename")

    scan_card = scan_sub.add_parser("business-card", help="Parse OCR text as a business card")
    scan_card.add_argument("filename")

    summarize = subparsers.add_parser("summarize", help="Summarize a workspace text file")
    summarize.add_argument("filename")
    summarize.add_argument("--style", default="brief")

    analyze = subparsers.add_parser("analyze", help="Analyze a workspace text file")
    analyze.add_argument("filename")

    minutes = subparsers.add_parser("minutes", help="Generate meeting minutes")
    minutes.add_argument("--transcript")
    minutes.add_argument("--title", default="Meeting")
    minutes.add_argument("--participant", action="append")

    followup = subparsers.add_parser("followup", help="Generate meeting minutes, transcript export, email draft, reminders, and projection confirmation")
    followup.add_argument("--transcript")
    followup.add_argument("--title", default="Meeting")
    followup.add_argument("--participant", action="append")
    followup.add_argument("--recipient", default="待填写收件人")
    followup.add_argument("--no-reminders", action="store_true")
    followup.add_argument("--no-projection", action="store_true")

    project = subparsers.add_parser("project", help="Render projection markdown")
    project.add_argument("title")
    project.add_argument("body")
    project.add_argument("--mode", default="status")

    project_screen = subparsers.add_parser("project-screen", help="Serve latest projection card as a local display preview page")
    project_screen.add_argument("--host", default="127.0.0.1")
    project_screen.add_argument("--port", type=int, default=8765)
    project_screen.add_argument("--refresh-seconds", type=int, default=2)
    project_screen.add_argument("--open", action="store_true", help="Open the preview URL in the default browser")

    share_space = subparsers.add_parser("share-space", help="Serve a controlled workspace inbox for office-computer uploads")
    share_space.add_argument("--host", default="127.0.0.1")
    share_space.add_argument("--port", type=int, default=8788)
    share_space.add_argument("--token", default="", help="Optional fixed upload token; generated if omitted")
    share_space.add_argument("--max-mb", type=int, default=50, help="Maximum upload size per request")

    subparsers.add_parser("share-list", help="List files in the controlled shared inbox")

    share_note = subparsers.add_parser("share-note", help="Write a text note into the controlled shared inbox")
    share_note.add_argument("title")
    share_note.add_argument("text")

    web_console = subparsers.add_parser("web-console", help="Serve the LeLamp/OpenClaw Raspberry Pi web control console")
    web_console.add_argument("--host", default="127.0.0.1")
    web_console.add_argument("--port", type=int, default=8790)
    web_console.add_argument("--token", default="", help="Optional fixed console token; generated if omitted")
    web_console.add_argument("--max-mb", type=int, default=50, help="Maximum upload size per request")
    web_console.add_argument("--projection-preview-port", type=int, default=8765)

    args = parser.parse_args()
    runtime = build_runtime()

    if args.command == "ask":
        llm = ResponsesLLM(
            ResponsesLLMConfig(
                api_key=runtime.config.openai_api_key,
                base_url=runtime.config.openai_base_url,
                model=runtime.config.openai_model,
                reasoning_effort=runtime.config.openai_reasoning_effort,
            )
        )
        user_text = " ".join(args.text)
        runtime.audit.record(
            "llm.request",
            details={
                "model": runtime.config.openai_model,
                "base_url": runtime.config.openai_base_url,
                "chars": len(user_text),
            },
        )
        try:
            output = llm.complete(
                instructions=OFFICE_AGENT_INSTRUCTIONS,
                user_input=user_text,
                context=context_snapshot(runtime),
            )
        except LLMError as exc:
            runtime.audit.record("llm.request", status="error", details={"error": str(exc)})
            raise SystemExit(str(exc)) from exc
        runtime.audit.record("llm.response", details={"chars": len(output)})
        print(output)
        return

    if args.command == "manual":
        result = run_manual_agent(runtime, " ".join(args.text))
        print(json.dumps(_jsonable(result), ensure_ascii=False, indent=2))
        return

    result = run_tool(runtime, args.command, args)
    print(json.dumps(_jsonable(result), ensure_ascii=False, indent=2))


def _jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return _jsonable(value.__dict__)
    return value


if __name__ == "__main__":
    main()
