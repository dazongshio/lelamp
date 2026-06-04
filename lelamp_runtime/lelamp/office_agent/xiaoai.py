from __future__ import annotations

import ast
import math
import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .audit import AuditLogger


@dataclass(frozen=True)
class XiaoAiCapability:
    category: str
    examples: tuple[str, ...]
    status: str
    tool: str
    notes: str

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "examples": list(self.examples),
            "status": self.status,
            "tool": self.tool,
            "notes": self.notes,
        }


XIAOAI_CAPABILITIES: tuple[XiaoAiCapability, ...] = (
    XiaoAiCapability(
        "语音唤醒和连续对话",
        ("小爱同学", "小灯", "连续问答", "打断对话"),
        "implemented",
        "openclaw_assistant.py / openclaw_realtime_voice.py",
        "本仓库已有唤醒词、VAD、ASR、TTS 和实时语音入口；默认唤醒词是“小灯”。",
    ),
    XiaoAiCapability(
        "天气、时间、日程、提醒、闹钟、倒计时",
        ("今天深圳天气", "明天提醒我开会", "现在几点", "设置五分钟倒计时"),
        "implemented",
        "openclaw_cli.py manual / xiaoai_daily",
        "天气使用可替换的联网接口；提醒和日程先落本地工作区。",
    ),
    XiaoAiCapability(
        "百科、新闻、股票、汇率、问答",
        ("查一下 GPT-5.5", "今天新闻", "苹果股票", "美元汇率"),
        "implemented_with_api",
        "web_search / ResponsesLLM",
        "需要联网搜索或 LLM API；工具会返回来源或可审计的待查询请求。",
    ),
    XiaoAiCapability(
        "计算、单位换算、翻译、知识解释",
        ("计算 36*18", "1 米等于多少厘米", "把这句话翻译成英文"),
        "implemented",
        "xiaoai_utility",
        "计算和常见单位换算本地执行；翻译和开放解释优先走配置的 LLM API。",
    ),
    XiaoAiCapability(
        "打开应用、网页、文件、搜索文件",
        ("打开浏览器", "打开 github.com", "帮我找合同文件"),
        "implemented",
        "desktop_safe_actions",
        "默认 audit_only 只生成计划；设置 OPENCLAW_DESKTOP_BACKEND=local 后执行 xdg-open/playerctl 等本地动作。",
    ),
    XiaoAiCapability(
        "系统控制和媒体播放",
        ("暂停音乐", "下一首", "音量调到 30", "打开计算器"),
        "implemented",
        "desktop_safe_actions",
        "使用 playerctl、pactl/amixer 或本机应用命令；不可用时返回安装提示。",
    ),
    XiaoAiCapability(
        "智能家居/米家类设备控制",
        ("打开客厅灯", "空调调到 26 度", "扫地机器人回充"),
        "adapter_ready",
        "smart_home_bridge",
        "通过 Home Assistant REST、用户自定义 webhook 或后续米家适配器接入；未配置账号时不会假装执行。",
    ),
    XiaoAiCapability(
        "电话、短信、找手机、跨设备控制",
        ("打电话给张三", "发短信", "帮我找手机"),
        "adapter_ready",
        "mobile_bridge",
        "通过配置的手机伴随应用 webhook 转发；电话和短信需要显式授权，未配置时返回 needs_config。",
    ),
    XiaoAiCapability(
        "会议、文档、投影、扫描",
        ("开启会议模式", "总结这份 PDF", "扫描名片", "投影会议结论"),
        "implemented",
        "office_agent",
        "这是 LeLamp 相比普通小爱更强的桌面/办公扩展能力。",
    ),
)


class UtilityCalculationError(ValueError):
    pass


class XiaoAiService:
    """Rule-first XiaoAi-compatible local utilities.

    The service deliberately separates deterministic local actions from broad
    LLM/search tasks so callers can audit what actually ran on the machine.
    """

    _jokes = (
        "有的人打开计算器是为了算账，我打开计算器是为了确认自己没算错一加一。",
        "今天的电脑很安静，因为它正在后台认真假装没有更新。",
        "最稳定的计划，是先把提醒设好，再假装自己不会忘。",
    )
    _poems = (
        "山中无历日，寒尽不知年。",
        "海内存知己，天涯若比邻。",
        "会当凌绝顶，一览众山小。",
    )

    def __init__(self, audit: AuditLogger):
        self.audit = audit

    def feature_matrix(self) -> list[dict[str, object]]:
        self.audit.record("xiaoai.features", details={"count": len(XIAOAI_CAPABILITIES)})
        return [capability.as_dict() for capability in XIAOAI_CAPABILITIES]

    def answer_utility(self, text: str) -> dict[str, object]:
        normalized = text.strip().lower()
        result: dict[str, object]

        if self._looks_like_time_query(normalized):
            result = self._time_answer(text)
        elif self._looks_like_unit_conversion(normalized):
            result = self._unit_conversion_answer(text)
        elif self._looks_like_calculation(normalized):
            result = self._calculation_answer(text)
        elif self._has(normalized, "翻译", "怎么说", "translate"):
            result = self._llm_needed(text, "translation", "请把用户要翻译的内容翻译成目标语言，答案简洁。")
        elif self._has(normalized, "笑话", "讲个笑话", "段子", "joke"):
            result = {"type": "joke", "answer": random.choice(self._jokes), "source": "local"}
        elif self._has(normalized, "诗", "古诗", "诗词"):
            result = {"type": "poem", "answer": random.choice(self._poems), "source": "local"}
        elif self._has(normalized, "百科", "是什么", "是谁", "为什么", "怎么做", "菜谱", "星座", "黄历"):
            result = self._llm_needed(text, "knowledge", "请像桌面语音助手一样回答用户的知识问题，必要时提示需要联网核验。")
        elif self._has(normalized, "新闻", "股票", "股价", "汇率", "限行", "路况"):
            result = {
                "type": "realtime_info",
                "status": "needs_search",
                "query": text,
                "answer": "这是实时信息问题，应交给 web_search 或专用 API，并在回复中注明来源和时间。",
            }
        else:
            result = self._llm_needed(text, "general", "请作为简洁的中文桌面助手回答用户。")

        self.audit.record(
            "xiaoai.utility",
            target=text,
            details={key: value for key, value in result.items() if key != "answer"},
        )
        return result

    def _time_answer(self, text: str) -> dict[str, object]:
        now = datetime.now().astimezone()
        weekday_names = "一二三四五六日"
        if "星期" in text or "周几" in text:
            answer = f"今天是 {now.date().isoformat()}，星期{weekday_names[now.weekday()]}。"
            answer_type = "weekday"
        elif "几号" in text or "日期" in text:
            answer = f"今天是 {now.date().isoformat()}。"
            answer_type = "date"
        else:
            answer = f"现在是 {now.strftime('%H:%M')}。"
            answer_type = "time"
        return {
            "type": answer_type,
            "answer": answer,
            "timezone": now.tzname(),
            "iso_time": now.isoformat(),
        }

    def _calculation_answer(self, text: str) -> dict[str, object]:
        expression = _extract_expression(text)
        if not expression:
            raise UtilityCalculationError("No arithmetic expression found.")
        value = _safe_eval(expression)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return {
            "type": "calculation",
            "expression": expression,
            "value": value,
            "answer": f"{expression} = {value}",
        }

    def _unit_conversion_answer(self, text: str) -> dict[str, object]:
        conversion = convert_unit_query(text)
        if conversion is None:
            return self._llm_needed(text, "unit_conversion", "请识别并完成用户的单位换算。")
        return {
            "type": "unit_conversion",
            **conversion,
            "answer": (
                f"{conversion['value']} {conversion['from_unit']} = "
                f"{conversion['converted_value']} {conversion['to_unit']}"
            ),
        }

    def _llm_needed(self, text: str, kind: str, instruction: str) -> dict[str, object]:
        return {
            "type": kind,
            "status": "needs_llm",
            "query": text,
            "llm_prompt": f"{instruction}\n\n用户请求：{text}",
            "answer": "需要调用已配置的 LLM API 才能完成该类开放式请求。",
        }

    def _looks_like_time_query(self, text: str) -> bool:
        return self._has(text, "几点", "现在时间", "什么时间", "星期几", "周几", "几号", "日期", "time")

    def _looks_like_calculation(self, text: str) -> bool:
        if self._has(text, "计算", "算一下", "等于多少", "多少等于", "calculate"):
            return True
        return bool(re.search(r"\d+\s*[\+\-\*/x×÷]\s*\d+", text))

    def _looks_like_unit_conversion(self, text: str) -> bool:
        return self._has(text, "换算", "换成", "转成", "等于多少", "多少厘米", "多少米", "多少公斤", "多少斤") and any(
            unit.lower() in text for unit in UNITS
        )

    def _has(self, text: str, *markers: str) -> bool:
        return any(marker in text for marker in markers)


_ALLOWED_FUNCS: dict[str, Any] = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "pow": pow,
}
_ALLOWED_NAMES = {"pi": math.pi, "e": math.e}
_ALLOWED_BINOPS = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.FloorDiv: lambda left, right: left // right,
    ast.Mod: lambda left, right: left % right,
    ast.Pow: lambda left, right: left**right,
}
_ALLOWED_UNARYOPS = {
    ast.UAdd: lambda value: +value,
    ast.USub: lambda value: -value,
}


def _extract_expression(text: str) -> str:
    cleaned = text.lower()
    replacements = {
        "计算": "",
        "算一下": "",
        "等于多少": "",
        "等于": "",
        "多少": "",
        "加": "+",
        "减": "-",
        "乘以": "*",
        "乘": "*",
        "x": "*",
        "×": "*",
        "除以": "/",
        "除": "/",
        "÷": "/",
        "的平方": "**2",
        "平方": "**2",
        "的": "",
        "？": "",
        "?": "",
        "，": "",
        ",": "",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = cleaned.replace("^", "**")
    matches = re.findall(r"[0-9eEpiPI\.\+\-\*/%\(\) ,]+(?:\*\*[0-9\.]+)?", cleaned)
    if not matches:
        return ""
    expression = max(matches, key=len).strip(" ,")
    return expression[:120]


def _safe_eval(expression: str) -> int | float:
    try:
        node = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise UtilityCalculationError(f"Invalid arithmetic expression: {expression}") from exc
    return _eval_node(node.body)


def _eval_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Name) and node.id in _ALLOWED_NAMES:
        return _ALLOWED_NAMES[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_FUNCS:
        args = [_eval_node(arg) for arg in node.args]
        if len(args) > 3:
            raise UtilityCalculationError("Too many function arguments.")
        return _ALLOWED_FUNCS[node.func.id](*args)
    raise UtilityCalculationError("Unsupported arithmetic expression.")


@dataclass(frozen=True)
class Unit:
    dimension: str
    factor: float
    offset: float = 0.0


UNITS: dict[str, Unit] = {
    "毫米": Unit("length", 0.001),
    "mm": Unit("length", 0.001),
    "厘米": Unit("length", 0.01),
    "cm": Unit("length", 0.01),
    "米": Unit("length", 1.0),
    "m": Unit("length", 1.0),
    "千米": Unit("length", 1000.0),
    "公里": Unit("length", 1000.0),
    "km": Unit("length", 1000.0),
    "英寸": Unit("length", 0.0254),
    "inch": Unit("length", 0.0254),
    "in": Unit("length", 0.0254),
    "英尺": Unit("length", 0.3048),
    "foot": Unit("length", 0.3048),
    "ft": Unit("length", 0.3048),
    "克": Unit("mass", 0.001),
    "g": Unit("mass", 0.001),
    "千克": Unit("mass", 1.0),
    "公斤": Unit("mass", 1.0),
    "kg": Unit("mass", 1.0),
    "斤": Unit("mass", 0.5),
    "磅": Unit("mass", 0.45359237),
    "lb": Unit("mass", 0.45359237),
    "毫升": Unit("volume", 0.001),
    "ml": Unit("volume", 0.001),
    "升": Unit("volume", 1.0),
    "l": Unit("volume", 1.0),
    "摄氏度": Unit("temperature", 1.0),
    "摄氏": Unit("temperature", 1.0),
    "℃": Unit("temperature", 1.0),
    "华氏度": Unit("temperature", 1.0),
    "华氏": Unit("temperature", 1.0),
    "℉": Unit("temperature", 1.0),
}


def convert_unit_query(text: str) -> dict[str, object] | None:
    value_match = re.search(r"(-?\d+(?:\.\d+)?)", text)
    if not value_match:
        return None
    value = float(value_match.group(1))
    unit_tokens = sorted(UNITS, key=len, reverse=True)
    found: list[tuple[int, str]] = []
    for unit in unit_tokens:
        for match in re.finditer(re.escape(unit), text, re.I):
            found.append((match.start(), unit))
    found.sort(key=lambda item: item[0])
    found = _dedupe_units(found)
    units_after_value = [unit for pos, unit in found if pos >= value_match.end() - 1]
    if len(units_after_value) < 2:
        return None
    from_unit, to_unit = units_after_value[0], units_after_value[1]
    source = UNITS[from_unit]
    target = UNITS[to_unit]
    if source.dimension != target.dimension:
        return None
    converted = _convert_value(value, from_unit, to_unit)
    return {
        "value": _clean_number(value),
        "from_unit": from_unit,
        "to_unit": to_unit,
        "converted_value": _clean_number(converted),
        "dimension": source.dimension,
    }


def _dedupe_units(items: list[tuple[int, str]]) -> list[tuple[int, str]]:
    deduped: list[tuple[int, str]] = []
    occupied: set[int] = set()
    for pos, unit in items:
        span = set(range(pos, pos + len(unit)))
        if occupied & span:
            continue
        occupied |= span
        deduped.append((pos, unit))
    return deduped


def _convert_value(value: float, from_unit: str, to_unit: str) -> float:
    if UNITS[from_unit].dimension == "temperature":
        celsius = _to_celsius(value, from_unit)
        return _from_celsius(celsius, to_unit)
    base = value * UNITS[from_unit].factor
    return base / UNITS[to_unit].factor


def _to_celsius(value: float, unit: str) -> float:
    if unit in {"华氏度", "华氏", "℉"}:
        return (value - 32) * 5 / 9
    return value


def _from_celsius(value: float, unit: str) -> float:
    if unit in {"华氏度", "华氏", "℉"}:
        return value * 9 / 5 + 32
    return value


def _clean_number(value: float) -> int | float:
    rounded = round(value, 6)
    if float(rounded).is_integer():
        return int(rounded)
    return rounded
