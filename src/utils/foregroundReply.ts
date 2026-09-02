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
    intent: "voice_assistant_control",
    patterns: [/开启语音助手|打开语音助手|启动语音助手|开始语音助手|关闭语音助手|停止语音助手|退出语音助手|关闭实时语音|停止实时语音|开启实时语音|启动实时语音|开始实时语音|开启语音控制|启动语音控制|开始语音控制|语音助手状态|实时语音状态|语音控制状态/i],
    replies: [
      "正在执行本地语音助手控制。",
    ],
    backend: "本地：Qwen realtime voice process -> 设备侧麦克风/扬声器，不调用普通聊天",
  },
  {
    intent: "lamp_control",
    patterns: [/点头|摇头|回到默认|默认位置|默认状态|复位台灯|台灯复位|扫描成?pdf|扫描PDF|拍照扫描|灯头扫描|开始投影|启动投影|打开投影|进入投影|切到投影|投影位置|跟随我|开始跟随|启动跟随|停止跟随|停止追踪|别跟|不要跟|台灯状态|加电|掉电|松开电机|开灯|关灯|暖光|白光|红灯|绿灯|蓝灯|黄灯|紫灯/i],
    replies: [
      "正在执行本地台灯控制。",
    ],
    backend: "本地：LeLamp voice skill -> 硬件控制，不调用 Qwen",
  },
  {
    intent: "meeting_control",
    patterns: [/会议状态|当前会议|听悟状态|听写状态|转写状态|听悟服务状态|会议服务状态|开始会议|启动会议|会议开始|开会了|开始开会|开始实时会议|启动实时会议|开始会议记录|开启会议记录|启动会议记录|开始记录会议|开始记录|启动记录|开始听悟会议|启动听悟会议|打开听悟会议|开始听写|开启听写|启动听写|开始转写|开启转写|启动转写|实时转写|记录会议|录会议|开始录会议|停止会议|结束会议|散会|结束开会|停止实时会议|结束实时会议|停止会议记录|结束会议记录|关闭会议记录|停止记录会议|停止记录|结束记录|停止听悟会议|结束听悟会议|关闭听悟会议|停止听写|结束听写|关闭听写|停止转写|结束转写|关闭转写|拉取会议纪要|获取会议纪要|同步会议纪要|生成听悟纪要|获取听悟纪要|拉取听悟纪要|生成会议ai|会议智能纪要|听悟智能纪要|会议思维导图|生成思维导图|会议ppt|会议问答|开启会议模式|关闭会议模式|本地转写状态|本地实时转写|本地会议状态|导出会议转写|导出转写|导出会议原文|保存会议转写|保存会议原文|导出会议记录|保存会议记录|生成会议纪要|整理会议纪要|做会议纪要|生成纪要|整理纪要|总结会议内容|会议内容总结|会议总结|总结这次会议|整理这次会议|提取会议决策|生成会议决策|会议决策|决策事项|确认事项|提取会议待办|生成会议待办|会议待办|行动项|待办事项|生成待办事项|整理待办事项|任务列表|会议任务|生成会议提醒|会议提醒|待办提醒|生成会议跟进|会议跟进包|会后跟进|会后跟进包|会议投影确认|投影会议确认|投影决策待办|投影会议结果|显示会议结果|展示会议结果|导出会议材料|导出会议包|导出跟进包|打包会议材料|下载会议材料/i],
    replies: [
      "正在执行本地会议控制。",
    ],
    backend: "本地：meeting voice skill -> 会议/听悟控制，不调用 Qwen",
  },
  {
    intent: "remote_control",
    patterns: [/远程电脑|远程主机|目标电脑|另一台电脑|那台电脑|ssh电脑|打开\s*codex|启动\s*codex|运行\s*codex|电脑状态|ssh状态|ppt下一页|ppt上一页|幻灯片下一页|幻灯片上一页|下一页|上一页|远程.*音量|远程.*静音|远程.*锁屏/i],
    replies: [
      "正在执行远程电脑控制。",
    ],
    backend: "本地：remote SSH voice skill -> 已保存 SSH 目标，不调用 Qwen",
  },
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

export function isLampControlText(message: string): boolean {
  const normalized = message.trim();
  return intentRules[0].patterns.some((pattern) => pattern.test(normalized));
}

export function isLocalControlText(message: string): boolean {
  const normalized = message.trim();
  return intentRules
    .filter((rule) => rule.intent === "lamp_control" || rule.intent === "meeting_control" || rule.intent === "voice_assistant_control" || rule.intent === "remote_control")
    .some((rule) => rule.patterns.some((pattern) => pattern.test(normalized)));
}

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
