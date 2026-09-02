from __future__ import annotations

import re
from dataclasses import dataclass, field

from .audit import AuditLogger
from .lelamp_voice_skill import parse_lamp_voice_command
from .meeting_voice_skill import parse_meeting_voice_command
from .remote_control import parse_remote_voice_command


@dataclass(frozen=True)
class IntentRoute:
    intent: str
    skill: str
    confidence: float
    requires_confirmation: bool
    summary: str
    action: str = "answer"
    slots: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent,
            "skill": self.skill,
            "confidence": self.confidence,
            "requires_confirmation": self.requires_confirmation,
            "summary": self.summary,
            "action": self.action,
            "slots": self.slots,
        }


class OfficeIntentRouter:
    """Rule-first router for office assistant voice commands."""

    def __init__(self, audit: AuditLogger):
        self.audit = audit

    def route(self, text: str) -> IntentRoute:
        normalized = text.lower()
        route = self._route(normalized, text)
        self.audit.record("intent.route", target=text, details=route.as_dict())
        return route

    def _route(self, normalized: str, original: str) -> IntentRoute:
        meeting_command = parse_meeting_voice_command(original)
        if meeting_command is not None:
            return IntentRoute(
                intent="meeting_voice_control",
                skill="meeting_voice_control",
                confidence=0.92,
                requires_confirmation=False,
                summary="本地会议语音控制命令",
                action=meeting_command.action,
                slots={"label": meeting_command.label},
            )
        lamp_command = parse_lamp_voice_command(original)
        if lamp_command is not None:
            return IntentRoute(
                intent="lelamp_voice_control",
                skill="lelamp_voice_control",
                confidence=0.92,
                requires_confirmation=False,
                summary="本地台灯语音控制命令",
                action=lamp_command.action,
                slots={"label": lamp_command.label},
            )
        remote_command = parse_remote_voice_command(original)
        if remote_command is not None:
            return IntentRoute(
                intent="remote_control",
                skill="remote_ssh_control",
                confidence=0.9,
                requires_confirmation=False,
                summary="本地远程电脑 SSH 语音控制命令",
                action=remote_command.action,
                slots={"label": remote_command.label},
            )
        if self._has(
            normalized,
            "全权",
            "自动操作",
            "自动点击",
            "代我点击",
            "替我操作",
            "提交表单",
            "发送邮件",
            "发邮件",
            "删除文件",
            "购买",
            "支付",
        ):
            return IntentRoute(
                intent="desktop",
                skill="desktop_operator",
                confidence=0.9,
                requires_confirmation=True,
                summary="高风险桌面自动化或外部副作用操作，需要 full_control 与逐任务确认",
                action="request_desktop_operation",
                slots=self._slots(original),
            )
        if self._has(normalized, "p0", "核心办公", "办公助手进度", "办公能力状态"):
            return IntentRoute(
                intent="p0_status",
                skill="p0_office",
                confidence=0.88,
                requires_confirmation=False,
                summary="查看 P0 办公助手能力实现状态",
                action="p0_status",
                slots=self._slots(original),
            )
        if self._has(normalized, "lelamp能力", "灯的能力", "灯姿态", "姿态表达", "专属能力"):
            return IntentRoute(
                intent="lelamp_capabilities",
                skill="lelamp_affordance",
                confidence=0.88,
                requires_confirmation=False,
                summary="查看 LeLamp 专属姿态、观察、投影和环境感知能力",
                action="list_lelamp_capabilities",
                slots=self._slots(original),
            )
        if self._has(normalized, "观察桌面", "看一下桌面", "摄像头观察", "拍一下桌面"):
            return IntentRoute(
                intent="desk_observation",
                skill="lelamp_affordance",
                confidence=0.82,
                requires_confirmation=False,
                summary="摄像头单帧观察桌面并生成场景事件",
                action="observe_desk_once",
                slots=self._slots(original),
            )
        if self._has(normalized, "环境感知", "有人靠近", "光线太暗", "投影遮挡", "会议开始"):
            return IntentRoute(
                intent="environment_event",
                skill="lelamp_affordance",
                confidence=0.76,
                requires_confirmation=False,
                summary="记录或推理办公室环境事件",
                action="report_environment",
                slots=self._slots(original),
            )
        if self._has(normalized, "倒计时投影", "投影倒计时", "倒计时页"):
            return IntentRoute(
                intent="projection",
                skill="projection_assistant",
                confidence=0.82,
                requires_confirmation=False,
                summary="渲染投影倒计时卡片",
                action="render_countdown",
                slots=self._slots(original),
            )
        if self._has(normalized, "小爱功能", "小爱同学功能", "你会什么", "功能列表", "支持哪些功能"):
            return IntentRoute(
                intent="xiaoai_features",
                skill="xiaoai_utility",
                confidence=0.86,
                requires_confirmation=False,
                summary="列出小爱同学兼容能力和当前实现状态",
                action="list_xiaoai_features",
                slots=self._slots(original),
            )
        if self._has(normalized, "天气", "气温", "下雨", "降雨", "weather", "temperature", "rain"):
            return IntentRoute(
                intent="weather",
                skill="cloud_weather",
                confidence=0.82,
                requires_confirmation=False,
                summary="实时天气、气温、降雨和出行建议",
                action="get_weather",
                slots=self._slots(original),
            )
        if self._has(normalized, "提醒", "闹钟", "定时", "reminder", "alarm", "timer"):
            action = "list_reminders" if self._has(normalized, "查看", "查询", "列表", "有哪些", "list") else "create_reminder"
            return IntentRoute(
                intent="reminder",
                skill="daily_assistant",
                confidence=0.82,
                requires_confirmation=False,
                summary="本地提醒、闹钟和待办提示",
                action=action,
                slots=self._slots(original),
            )
        if self._has(normalized, "日程", "日历", "安排", "calendar", "schedule"):
            action = "create_calendar_event" if self._has(normalized, "安排", "创建", "添加", "约", "新增") else "get_calendar"
            return IntentRoute(
                intent="calendar",
                skill="daily_assistant",
                confidence=0.78,
                requires_confirmation=False,
                summary="查看本地日程和提醒",
                action=action,
                slots=self._slots(original),
            )
        if self._has(normalized, "本地搜索", "语义搜索", "文件内容", "在文件里找", "搜索本地", "搜本地"):
            return IntentRoute(
                intent="local_search",
                skill="local_file_search",
                confidence=0.82,
                requires_confirmation=False,
                summary="在允许目录中按文件名和文本内容搜索",
                action="search_local_content",
                slots=self._slots(original),
            )
        if self._has(normalized, "联网搜索", "网上查", "搜索", "查资料", "新闻", "最新", "引用", "source", "search"):
            return IntentRoute(
                intent="web_search",
                skill="web_research",
                confidence=0.78,
                requires_confirmation=False,
                summary="联网搜索、新闻资料和带来源的信息查询",
                action="web_search",
                slots=self._slots(original),
            )
        if self._has(
            normalized,
            "计算",
            "算一下",
            "换算",
            "换成",
            "等于多少",
            "多少厘米",
            "多少米",
            "多少公斤",
            "多少斤",
            "多少度",
            "翻译",
            "怎么说",
            "几点",
            "星期几",
            "周几",
            "几号",
            "日期",
            "笑话",
            "段子",
            "古诗",
            "诗词",
            "百科",
            "是什么",
            "是谁",
            "菜谱",
            "星座",
            "黄历",
            "汇率",
            "股价",
            "股票",
            "限行",
            "路况",
        ) or re.search(r"\d+\s*[\+\-\*/x×÷]\s*\d+", normalized):
            action = "answer_utility_query"
            if self._has(normalized, "新闻", "股票", "股价", "汇率", "限行", "路况"):
                action = "web_search"
            return IntentRoute(
                intent="xiaoai_utility",
                skill="xiaoai_utility",
                confidence=0.78,
                requires_confirmation=False,
                summary="小爱式日常问答、计算、换算、翻译或实时信息请求",
                action=action,
                slots=self._slots(original),
            )
        if self._has(normalized, "关闭会议模式", "结束会议模式", "退出会议模式", "stop meeting mode"):
            return IntentRoute(
                intent="meeting",
                skill="meeting_capture",
                confidence=0.9,
                requires_confirmation=False,
                summary="关闭会议模式，停止继续接收会议转写",
                action="disable_meeting_mode",
                slots=self._slots(original),
            )
        if self._has(normalized, "开启会议模式", "进入会议模式", "开始会议模式", "enable meeting mode"):
            return IntentRoute(
                intent="meeting",
                skill="meeting_capture",
                confidence=0.9,
                requires_confirmation=False,
                summary="开启会议模式，用户明确授权后再处理会议理解内容",
                action="meeting_mode",
                slots=self._slots(original),
            )
        if self._has(normalized, "会议", "纪要", "转写", "参会", "meeting", "minutes"):
            if self._has(normalized, "会后", "跟进包", "行动项", "邮件草稿", "follow-up", "followup"):
                action = "meeting_followup_package"
            else:
                action = "generate_minutes" if self._has(normalized, "纪要", "总结", "会后") else "meeting_mode"
            return IntentRoute(
                intent="meeting",
                skill="meeting_capture",
                confidence=0.86,
                requires_confirmation=False,
                summary="会议理解、纪要、待办或会后输出",
                action=action,
                slots=self._slots(original),
            )
        if self._has(normalized, "找文件", "查找文件", "搜索文件", "打开文件"):
            action = "open_file" if self._has(normalized, "打开文件") else "find_file"
            return IntentRoute(
                intent="desktop",
                skill="desktop_safe_actions",
                confidence=0.8,
                requires_confirmation=False,
                summary="在允许的本地目录中查找或打开文件",
                action=action,
                slots=self._slots(original),
            )
        if self._has(normalized, "合同", "pdf", "文档", "文件", "条款", "报告", "提纲", "汇报", "关键数据", "表格", "制表", "document"):
            if self._has(normalized, "关键数据", "表格", "制表", "csv", "数据表"):
                action = "key_data_table"
            elif self._has(normalized, "提纲", "汇报", "报告"):
                action = "report_outline"
            else:
                action = "analyze_document"
            return IntentRoute(
                intent="document",
                skill="document_workspace",
                confidence=0.84,
                requires_confirmation=False,
                summary="文档总结、合同解析、关键数据制表或汇报提纲",
                action=action,
                slots=self._slots(original),
            )
        if self._has(normalized, "扫描", "拍照", "ocr", "纸质", "名片", "scan"):
            return IntentRoute(
                intent="scan",
                skill="paper_scan",
                confidence=0.82,
                requires_confirmation=False,
                summary="实体文档采集、OCR 或结构化识别",
                action="scan_document",
                slots=self._slots(original),
            )
        if self._has(normalized, "ppt", "幻灯片", "当前页", "这一页", "这页") and self._has(normalized, "总结", "概括", "讲一下", "提炼"):
            return IntentRoute(
                intent="projection",
                skill="projection_assistant",
                confidence=0.86,
                requires_confirmation=False,
                summary="总结当前 PPT 页，需要用户在 Projection 页面主动授权屏幕捕获",
                action="summarize_ppt_page",
                slots=self._slots(original),
            )
        if self._has(normalized, "投影", "投屏", "墙上", "展示", "确认页", "project"):
            return IntentRoute(
                intent="projection",
                skill="projection_assistant",
                confidence=0.8,
                requires_confirmation=False,
                summary="投影展示、会议结论确认或校准",
                action="render_projection",
                slots=self._slots(original),
            )
        if self._has(normalized, "截图", "截屏", "屏幕", "当前窗口", "screenshot", "screen"):
            action = "summarize_screen" if self._has(normalized, "理解", "总结", "识别", "ocr", "看一下", "读一下") else "capture_screen"
            return IntentRoute(
                intent="screen_snapshot",
                skill="desktop_context",
                confidence=0.77,
                requires_confirmation=False,
                summary="获取当前屏幕快照，供后续视觉理解或投影说明使用",
                action=action,
                slots=self._slots(original),
            )
        if self._has(normalized, "邮件", "email", "发给", "跟进", "草稿"):
            return IntentRoute(
                intent="email_draft",
                skill="document_workspace",
                confidence=0.78,
                requires_confirmation=False,
                summary="根据会议或文档生成邮件草稿，不自动发送",
                action="draft_email",
                slots=self._slots(original),
            )
        if self._has(
            normalized,
            "客厅灯",
            "卧室灯",
            "台灯",
            "空调",
            "扫地机器人",
            "扫地机",
            "窗帘",
            "净化器",
            "风扇",
            "电视",
            "智能家居",
            "米家",
            "home assistant",
        ):
            return IntentRoute(
                intent="smart_home",
                skill="smart_home_bridge",
                confidence=0.82,
                requires_confirmation=False,
                summary="智能家居设备控制，通过 Home Assistant 或 webhook 适配器执行",
                action="smart_home_control",
                slots=self._slots(original),
            )
        if self._has(
            normalized,
            "播放音乐",
            "暂停音乐",
            "继续播放",
            "下一首",
            "上一首",
            "停止播放",
            "有声书",
            "电台",
            "白噪音",
        ):
            return IntentRoute(
                intent="media",
                skill="desktop_safe_actions",
                confidence=0.76,
                requires_confirmation=False,
                summary="桌面媒体播放控制或娱乐内容入口",
                action="media_control",
                slots=self._slots(original),
            )
        if self._has(normalized, "电话", "打给", "短信", "找手机", "手机在哪"):
            return IntentRoute(
                intent="mobile_bridge",
                skill="mobile_bridge",
                confidence=0.72,
                requires_confirmation=True,
                summary="电话、短信或找手机需要手机侧授权桥接",
                action="request_mobile_bridge",
                slots=self._slots(original),
            )
        if self._has(normalized, "电脑", "桌面", "点击", "打开", "自动操作", "全权", "desktop", "app"):
            action = "request_desktop_operation"
            if self._has(normalized, "找文件", "查找文件", "搜索文件"):
                action = "find_file"
            elif self._has(normalized, "打开文件"):
                action = "open_file"
            elif self._has(normalized, "网页", "网站", "网址", "http", "www", ".com", ".cn", ".net"):
                action = "open_url"
            elif self._has(normalized, "打开", "启动", "运行"):
                action = "open_app"
            summary = (
                "确定性的桌面安全动作：打开应用、网页或允许目录中的文件"
                if action != "request_desktop_operation"
                else "桌面自动化或全权模式操作，需要确认权限"
            )
            return IntentRoute(
                intent="desktop",
                skill="desktop_safe_actions" if action != "request_desktop_operation" else "desktop_operator",
                confidence=0.76,
                requires_confirmation=action == "request_desktop_operation",
                summary=summary,
                action=action,
                slots=self._slots(original),
            )
        if self._has(normalized, "音量", "静音", "声音大一点", "声音小一点", "volume"):
            return IntentRoute(
                intent="desktop",
                skill="desktop_safe_actions",
                confidence=0.74,
                requires_confirmation=False,
                summary="系统音量控制",
                action="set_volume",
                slots=self._slots(original),
            )
        if self._has(normalized, "权限", "安全", "沙箱", "审计", "日志"):
            return IntentRoute(
                intent="security",
                skill="audit_security",
                confidence=0.75,
                requires_confirmation=False,
                summary="安全状态、权限边界或审计日志说明",
                action="security_status",
                slots=self._slots(original),
            )
        return IntentRoute(
            intent="general_office_chat",
            skill="general_llm",
            confidence=0.5,
            requires_confirmation=False,
            summary="通用办公问答或澄清需求",
            action="answer",
            slots=self._slots(original),
        )

    def _has(self, text: str, *markers: str) -> bool:
        return any(marker in text for marker in markers)

    def _slots(self, text: str) -> dict[str, str]:
        slots: dict[str, str] = {}
        file_match = re.search(r"([\w\u4e00-\u9fff\- ]+\.(?:pdf|docx?|pptx?|xlsx?|txt|md))", text, re.I)
        if file_match:
            file_name = file_match.group(1).strip()
            file_name = re.sub(r"^(把|将|请|帮我|帮我把)\s*", "", file_name).strip()
            slots["file"] = file_name
        recipient_match = re.search(r"(?:发给|发送给|to)\s*([\w\u4e00-\u9fff@.\-]+)", text, re.I)
        if recipient_match:
            slots["recipient"] = recipient_match.group(1).strip()
        quoted = re.findall(r"[“\"]([^”\"]+)[”\"]", text)
        if quoted:
            slots["quoted_topic"] = quoted[0].strip()
        city_match = re.search(
            r"(?:查看|查一下|查询|看下|看看|帮我看下|帮我查下)?\s*"
            r"([\w\u4e00-\u9fff]{2,20})"
            r"(?:今天|明天|后天|的)?(?:天气|气温|下雨|降雨)",
            text,
            re.I,
        )
        if city_match:
            city = city_match.group(1).strip()
            city = re.sub(r"^(我想|请|帮我|帮我看下|帮我查下)", "", city).strip()
            city = re.sub(r"(今天|明天|后天|会不会|是否|有没有|的)+$", "", city).strip()
            if city and city not in {"今天", "明天", "后天"}:
                slots["city"] = city
        if "明天" in text:
            slots["date"] = "tomorrow"
        elif "后天" in text:
            slots["date"] = "day_after_tomorrow"
        elif "今天" in text:
            slots["date"] = "today"
        duration_match = re.search(r"(\d+)\s*(秒|分钟|分|小时)", text)
        if duration_match:
            amount = int(duration_match.group(1))
            unit = duration_match.group(2)
            multiplier = 1 if unit == "秒" else 60 if unit in {"分钟", "分"} else 3600
            slots["seconds"] = str(amount * multiplier)
        url_match = re.search(r"https?://[^\s，。]+|[\w.-]+\.[a-z]{2,}(?:/[^\s，。]*)?", text, re.I)
        if url_match:
            slots["url"] = url_match.group(0)
        app_match = re.search(r"(?:打开|启动|运行)\s*([\w\u4e00-\u9fff.+-]{1,30})", text)
        if app_match:
            slots["app"] = app_match.group(1).strip()
        file_query_match = re.search(r"(?:找文件|查找文件|搜索文件|打开文件)\s*([\w\u4e00-\u9fff._ -]{1,80})", text)
        if file_query_match:
            slots["file_query"] = file_query_match.group(1).strip()
        return slots
