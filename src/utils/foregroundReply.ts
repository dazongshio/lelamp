export interface ForegroundReply {
  intent: string;
  mode: "query" | "task";
  text: string;
  attachment: string;
}

const intentRules: Array<{
  intent: string;
  patterns: RegExp[];
  replies: string[];
  backend: string;
  mode?: "query" | "task";
}> = [
  {
    intent: "weather_or_time",
    patterns: [/天气|气温|下雨|温度|今天|明天|时间|几点|日期|过0点|时区|查询|查一下|查下|看看|搜索|资料|信息|状态|进度/i],
    replies: [
      "正在为您查询，请稍后。",
    ],
    backend: "后台：校验时区 -> 解析日期 -> 调用天气/时间能力 -> 返回可播报结果",
    mode: "query",
  },
  {
    intent: "hardware_audio",
    patterns: [/扬声器|声音|播放|音频|麦克风|摄像头|硬件|alsa|aplay|设备|树莓派|rgb|状态灯/i],
    replies: [
      "我先检查树莓派侧设备和候选输出，重点确认声音打到哪一个硬件口。",
      "我会先做硬件状态核对，再让后台执行受控测试。",
      "我先把设备链路看清楚，避免只看到命令成功但实际没有声音。",
    ],
    backend: "后台：扫描设备 -> 选择候选输出 -> 执行测试 -> 写入审计",
  },
  {
    intent: "projection",
    patterns: [/投影|显示器|预览|投放|投屏|projection|display/i],
    replies: [
      "我先确认显示预览服务和最新卡片，再更新投放画面。",
      "我会先生成可检查的投影卡，再同步到显示测试入口。",
      "我先看投影输出目录和预览地址，确认画面实际写到哪里。",
    ],
    backend: "后台：检查 projection_dir -> 生成卡片 -> 刷新 preview -> 记录审计",
  },
  {
    intent: "meeting",
    patterns: [/会议|纪要|transcript|行动项|待办|follow.?up|决策|reminder|提醒/i],
    replies: [
      "我先按会议闭环拆解：纪要、决策、行动项和跟进草稿分开处理。",
      "我会先整理会议输入，再让后台生成可确认的 follow-up package。",
      "我先建立会议任务链路，关键输出会等你确认后再继续。",
    ],
    backend: "后台：读取 transcript -> 生成纪要 -> 提取决策/行动项 -> 准备确认",
  },
  {
    intent: "document",
    patterns: [/文档|文件|上传|共享空间|扫描|ocr|摘要|总结|合同|pdf|docx|表格|风险/i],
    replies: [
      "我先确认文件是否在共享空间或白名单目录内，再启动文档处理。",
      "我会先检查文件权限和类型，后台再做摘要、分析或风险标记。",
      "我先把文档输入定位清楚，避免读取未授权路径。",
    ],
    backend: "后台：路径校验 -> 文件读取 -> 调用文档 Skill/adapter -> 保存输出",
  },
  {
    intent: "security_settings",
    patterns: [/安全|sandbox|audit|审计|权限|full_control|白名单|allowed roots|设置|token/i],
    replies: [
      "我先按安全边界核对当前模式，任何高风险变更都不会直接执行。",
      "我会先读取当前权限和审计状态，再判断是否需要逐步确认。",
      "我先检查 sandbox、audit_only 和 allowed roots，再继续处理。",
    ],
    backend: "后台：读取安全状态 -> 校验策略 -> 必要时进入确认流程 -> 写审计",
  },
  {
    intent: "desktop_workspace",
    patterns: [/电脑|桌面|控制|共享文件夹|公共空间|下载|查看|companion|工作空间/i],
    replies: [
      "我先按共享空间模型处理，不会直接越权控制办公电脑。",
      "我会先确认公共空间里的文件和授权范围，再安排后续动作。",
      "我先把办公电脑和树莓派之间的边界确认清楚，再让后台执行。",
    ],
    backend: "后台：检查 shared_inbox/workspace -> 规划桌面任务 -> 等待确认 -> 写审计",
  },
  {
    intent: "general_office",
    patterns: [/.*/],
    replies: [
      "我先理解你的目标，再让后台拆成可审查步骤处理。",
      "我会先判断这属于哪类办公任务，再调用合适的 OpenClaw 能力。",
      "我先做任务识别，后台会按安全边界继续执行。",
    ],
    backend: "后台：意图识别 -> Skill 规划 -> 执行或等待确认 -> 返回结果",
  },
];

export function buildForegroundReply(message: string, page = "assistant"): ForegroundReply {
  const normalized = message.trim();
  const rule = intentRules.find((item) => item.patterns.some((pattern) => pattern.test(normalized))) ?? intentRules[intentRules.length - 1];
  const mode = rule.mode ?? "task";
  const reply = mode === "query" ? "正在为您查询，请稍后。" : selectReply(rule.replies, `${page}:${normalized}`);
  return {
    intent: rule.intent,
    mode,
    text: reply,
    attachment: mode === "query" ? "" : [`前台：已收到请求`, `识别方向：${rule.intent}`, rule.backend].join("\n"),
  };
}

function selectReply(replies: string[], seed: string) {
  if (!replies.length) return "我先确认你的需求，再让后台继续处理。";
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) >>> 0;
  }
  return replies[hash % replies.length];
}
