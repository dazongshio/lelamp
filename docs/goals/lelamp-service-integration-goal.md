# LeLamp Qwen-Omni 前台实时助手功能实施 Goal

> 当前 UI 已经基本完成。本文档用于下一阶段：把 Web UI 右侧/底部的前台助手升级为 **Qwen-Omni 实时对话助手**，同时保留 OpenClaw 作为后台办公任务执行器。
> 前台支持 **文字输入** 和 **语音输入**。普通对话由 Qwen-Omni 直接完成；办公任务、天气查询、文件处理、投影、硬件、会议等需要工具能力的请求，必须路由到 OpenClaw 或本地工具执行，并设置任务监控，后台完成后及时把结果回复到对话中。

---

## 1. Codex 短 `/goal`

在 Codex TUI 中不要粘贴全文，先把本文档保存到仓库，例如：

```bash
mkdir -p docs/goals
cp LeLamp_QwenOmni_Front_Assistant_Goal.md docs/goals/lelamp-qwen-omni-front-assistant-goal.md
```

然后使用短 goal：

```text
/goal Implement docs/goals/lelamp-qwen-omni-front-assistant-goal.md. Keep the existing UI. Add a Qwen-Omni foreground assistant supporting text and Raspberry Pi voice input, route ordinary chat to Qwen-Omni, route office/tool tasks to OpenClaw with monitored execution, append final results to the conversation, use TTS for voice output, and preserve sandbox/audit_only/allowed-roots/full_control safety boundaries.
```

然后再发送普通消息：

```text
Read docs/goals/lelamp-qwen-omni-front-assistant-goal.md and implement it now. Do not redesign the existing UI. Focus on the assistant runtime, text input, Qwen-Omni session, OpenClaw task routing, task monitoring, final result callbacks, TTS, and audit.
```

---

## 2. 背景与当前问题

当前 Web UI 助手主路径不是直接调用大模型对话，而是：

```text
前台规则即时回复
-> /api/assistant/manual
-> OfficeIntentRouter
-> run_manual_agent()
-> OpenClaw 本地工具/天气/文件/硬件/投影等
-> TTS 播放
```

这条链路适合安全可审计的办公任务执行，但不适合“小爱同学式”的实时自然对话体验。

现在需要新增一个明确的前台对话层：

```text
用户文字输入 / 树莓派麦克风语音输入
-> Qwen-Omni 前台实时助手
-> 先即时自然回应
-> 判断是否需要工具/办公执行
   -> 普通对话：Qwen-Omni 直接回复
   -> 工具/办公任务：调用 OfficeIntentRouter + OpenClaw
-> 为每个工具任务创建 task_id 和监控
-> 后台完成后追加最终结果到对话
-> 如开启语音输出，则通过服务端 TTS / 树莓派扬声器播放
```

---

## 3. 产品目标

实现一个“小爱同学式”的 Web UI 前台助手：

1. **支持文字输入**
   用户可以在 Web UI 对话框里直接输入文字，例如：
   - “今天天气怎么样？”
   - “帮我总结刚上传的会议记录”
   - “把这个 PDF 整理成汇报提纲”
   - “闲聊一下，今天工作有点累”
   - “打开投影确认卡”

2. **支持树莓派语音输入**
   语音输入优先来自树莓派麦克风阵列或树莓派连接的 USB 麦克风，而不是默认读取办公电脑麦克风。

3. **支持实时自然对话**
   普通闲聊、解释、问答、引导类内容由 Qwen-Omni 前台助手直接完成，不必调用 OpenClaw。

4. **支持办公任务执行**
   需要天气、本地文件、会议、文档、投影、硬件、桌面代理等能力时，Qwen-Omni 只负责前台交互和意图表达，实际动作必须交给 OpenClaw / 本地工具执行。

5. **及时响应用户**
   不管任务是否耗时，助手都必须先立即回复：
   - “正在为您查询，请稍后。”
   - “正在整理资料，请稍后。”
   - “我会先检查可用文件，然后开始分析。”
   - “这个操作需要确认，我先为你生成确认卡。”

6. **同步执行 + 任务监控**
   每个路由到后台的任务都必须创建 task_id，并进入任务监控状态。后台执行完成、失败、被阻止、超时或不可用时，必须及时把最终结果追加到对话中。

7. **安全可审计**
   所有关键动作必须写 audit log。Qwen-Omni 不得绕过 sandbox、audit_only、allowed roots、full_control、per-task confirmation 等安全边界。

---

## 4. 总体架构

### 4.1 新架构

```text
┌───────────────────────────────────────────────┐
│ Web UI AssistantPanel                          │
│ - text input                                   │
│ - mic button / Pi mic status                   │
│ - streaming messages                           │
│ - task timeline                                │
│ - confirmation cards                           │
└───────────────────┬───────────────────────────┘
                    │
                    ▼
┌───────────────────────────────────────────────┐
│ Assistant Gateway API                          │
│ /api/assistant/message                         │
│ /api/assistant/realtime                        │
│ /api/assistant/text                            │
│ /api/assistant/confirm                         │
│ /api/assistant/reject                          │
└───────────────────┬───────────────────────────┘
                    │
                    ▼
┌───────────────────────────────────────────────┐
│ Qwen-Omni Foreground Assistant Service         │
│ - text conversation                            │
│ - realtime voice session                       │
│ - intent hint extraction                       │
│ - immediate user-facing response               │
│ - streaming response                           │
└───────────────────┬───────────────────────────┘
                    │
       ordinary chat│             task/tool intent
                    │
                    ▼
        Qwen direct answer      ┌────────────────────────────┐
                                │ OfficeIntentRouter          │
                                │ safety rule verification    │
                                │ tool/task classification    │
                                └──────────────┬─────────────┘
                                               ▼
                                ┌────────────────────────────┐
                                │ OpenClaw / run_manual_agent │
                                │ local tools / skills / CLI  │
                                └──────────────┬─────────────┘
                                               ▼
                                ┌────────────────────────────┐
                                │ Task Monitor                │
                                │ SSE / WS / polling events   │
                                └──────────────┬─────────────┘
                                               ▼
                                Final assistant message + TTS
```

### 4.2 两个主要模块

#### 模块 A：Qwen-Omni 前台实时对话模块

职责：

- 接收用户文字输入。
- 接收树莓派麦克风语音输入。
- 对普通对话进行自然回复。
- 对任务型请求先做即时回应。
- 辅助识别用户意图，但不能直接执行高风险动作。
- 将需要工具执行的任务交给 OfficeIntentRouter。
- 将任务进度和最终结果自然地反馈给用户。
- 可选通过 TTS 播放回复。

#### 模块 B：OpenClaw 后台办公任务执行模块

职责：

- 接收来自前台助手的任务请求。
- 使用 OfficeIntentRouter 做规则路由和安全校验。
- 调用 run_manual_agent() 或现有 OpenClaw CLI / Skill / Adapter。
- 执行天气、文件、文档、会议、投影、硬件、桌面代理等任务。
- 创建 task_id。
- 写 audit log。
- 把结果返回给前台助手。
- 对高风险动作返回 waiting_confirmation，而不是自动执行。

---

## 5. 输入方式要求

### 5.1 文字输入

Web UI AssistantPanel 必须支持文字输入。

要求：

- 用户在输入框输入文字后，调用 `/api/assistant/message` 或 `/api/assistant/text`。
- 每条文本消息都必须创建 message_id。
- 前端立即显示用户消息。
- 后端必须尽快返回 assistant_ack。
- 如果是普通对话，Qwen-Omni 可以直接流式回复。
- 如果是任务型请求，前台助手先回复“正在处理”，然后进入 task monitoring。
- 文本输入与语音输入应复用同一套会话、消息、任务监控逻辑。

示例：

```text
用户：帮我查看今天深圳天气
助手立即回复：正在为您查询深圳今天的天气，请稍后。
后台：调用天气工具
助手最终回复：深圳今天多云，气温约 xx-xx℃，湿度 xx%，建议……
```

```text
用户：帮我把刚上传的 PDF 整理成汇报提纲
助手立即回复：正在整理刚上传的 PDF，我会先读取共享空间中的文件并生成提纲，请稍后。
后台：OpenClaw 文件读取 + 文档摘要
助手最终回复：已整理完成，提纲保存在 /workspace/outputs/xxx.md。主要包含……
```

```text
用户：我今天有点累
助手：听起来今天工作强度不低。你可以先休息几分钟，我也可以帮你整理今天的待办或会议内容。
后台：不调用 OpenClaw。
```

### 5.2 语音输入

语音输入优先使用树莓派侧设备。

要求：

- 显示树莓派麦克风状态。
- 如果树莓派麦克风不可用，UI 显示 unavailable / backend_missing。
- 不要默认启用办公电脑浏览器麦克风。
- 如果后续需要支持浏览器麦克风，必须单独配置 `ALLOW_BROWSER_MIC=true` 并在 UI 明确提示。
- 语音输入进入 Qwen-Omni realtime session。
- 识别后的文本也要显示在对话中。
- 语音任务与文字任务一样进入任务路由和监控。

### 5.3 输出方式

输出包括：

- Web UI 文本回复。
- 流式文本回复。
- 任务进度卡片。
- 任务完成结果卡片。
- 确认卡片。
- 可选 TTS 语音播放。

TTS 要求：

- TTS 仅播放助手最终文本或关键状态提示。
- 服务端播放走树莓派扬声器。
- 不要在浏览器端默认播放办公电脑扬声器，除非明确配置。
- TTS provider 可继续使用 dashscope。
- TTS 不等于对话模型，不要把 TTS 状态误展示为 LLM 状态。

---

## 6. 意图路由规则

### 6.1 不调用 OpenClaw 的普通对话

以下类型默认由 Qwen-Omni 直接回复，不调用 OpenClaw：

- 闲聊
- 安抚/鼓励
- 使用说明
- 简单概念解释
- UI 操作引导
- 不需要设备数据、不需要文件、不需要外部工具的普通问答

示例：

```text
用户：你是谁？
助手：我是 LeLamp 前台助手，可以和你对话，也可以帮你通过 OpenClaw 处理会议、文件、投影和硬件任务。
```

```text
用户：今天工作好累
助手：辛苦了。可以先休息一下。我也可以帮你把今天的会议内容整理成待办，减轻一点收尾工作。
```

### 6.2 必须调用 OpenClaw / 本地工具的请求

以下类型必须进入 OfficeIntentRouter + OpenClaw / 本地工具：

- 天气查询
- 文件读取
- 文档分析
- 文档摘要
- 合同风险
- 多文件对比
- 会议纪要
- follow-up email draft
- 投影卡片生成
- 投影预览启动/停止
- 硬件状态查询
- State Cue 触发
- Shared Space 文件操作
- audit 查询
- workspace 输出文件写入
- 桌面代理动作
- 任何需要本地设备、文件、传感器、网络工具、审计或权限控制的任务

示例：

```text
用户：帮我查看今天深圳天气
路由：weather_query
即时回复：正在为您查询深圳今天的天气，请稍后。
后台：天气工具，例如 wttr.in 或现有 weather adapter
最终回复：返回工具查询结果，不允许模型编造天气。
```

```text
用户：总结刚上传的会议记录
路由：meeting_or_document_summary
即时回复：正在读取刚上传的会议记录并生成总结，请稍后。
后台：OpenClaw shared_inbox 文件定位 + summarize skill
最终回复：输出总结和文件路径。
```

```text
用户：删除我电脑桌面上的所有临时文件
路由：high_risk_desktop_action
即时回复：这个操作风险较高，我不能直接执行。需要进入确认流程，并且当前 sandbox/audit_only 下默认阻止。
后台：不执行删除；写 blocked audit。
最终回复：说明被阻止原因。
```

---

## 7. API 设计

### 7.1 Assistant Message API

新增或改造：

```http
POST /api/assistant/message
```

请求：

```json
{
  "session_id": "optional",
  "input_type": "text",
  "text": "帮我查看今天深圳天气",
  "page": "dashboard",
  "context": {
    "selected_file": null,
    "selected_task_id": null
  }
}
```

响应应尽快返回，不要等长任务完成：

```json
{
  "ok": true,
  "data": {
    "session_id": "asst_s_...",
    "message_id": "msg_...",
    "assistant_ack": {
      "text": "正在为您查询深圳今天的天气，请稍后。",
      "speak": true
    },
    "route": {
      "kind": "task",
      "intent": "weather_query",
      "requires_openclaw": true,
      "requires_confirmation": false
    },
    "task": {
      "task_id": "task_...",
      "status": "running",
      "monitor_url": "/api/tasks/task_...",
      "events_url": "/api/tasks/task_.../events"
    }
  }
}
```

普通对话响应：

```json
{
  "ok": true,
  "data": {
    "session_id": "asst_s_...",
    "message_id": "msg_...",
    "route": {
      "kind": "chat",
      "intent": "ordinary_chat",
      "requires_openclaw": false
    },
    "assistant_message": {
      "text": "听起来今天工作强度不低。你可以先休息几分钟，我也可以帮你整理今天的待办或会议内容。",
      "streamed": true,
      "speak": true
    }
  }
}
```

### 7.2 Realtime API

新增或改造：

```http
GET /api/assistant/realtime/status
POST /api/assistant/realtime/session
WebSocket /api/assistant/realtime/ws
```

职责：

- 管理 Qwen-Omni realtime session。
- 支持树莓派麦克风输入。
- 支持文本消息进入同一 session。
- 推送 streaming transcript。
- 推送 streaming assistant reply。
- 推送 task event。
- 推送 final result。

### 7.3 Text API

如果暂时不实现完整 realtime WebSocket，也必须先实现文本路径：

```http
POST /api/assistant/text
```

要求：

- 文本输入必须可用。
- 文本也必须进入 Qwen-Omni 前台助手。
- 可以先用 request/response + SSE task event，后续再升级 WebSocket realtime。

### 7.4 Task Event API

```http
GET /api/tasks/{task_id}
GET /api/tasks/{task_id}/events
```

事件格式：

```json
{
  "event": "task_completed",
  "task_id": "task_...",
  "status": "completed",
  "assistant_final_message": {
    "text": "查询完成。深圳今天……",
    "speak": true
  },
  "result": {
    "summary": "...",
    "outputs": []
  }
}
```

必须支持的事件：

- task_created
- task_acknowledged
- task_routed
- task_started
- task_progress
- task_waiting_confirmation
- task_completed
- task_blocked
- task_failed
- task_timeout
- task_backend_missing
- task_unavailable

### 7.5 Confirmation API

```http
POST /api/assistant/confirm
POST /api/assistant/reject
```

要求：

- 高风险任务必须等待确认。
- full_control 下也必须逐任务确认。
- 用户拒绝后写 audit。
- 用户确认后继续执行后台任务并继续监控。

---

## 8. Qwen-Omni Provider 要求

### 8.1 配置

使用环境变量，不要把 key 写入前端：

```bash
DASHSCOPE_API_KEY=...
DASHSCOPE_REALTIME_MODEL=qwen3-omni-flash-realtime
DASHSCOPE_REALTIME_TRANSCRIPTION_MODEL=gummy-realtime-v1
DASHSCOPE_REALTIME_VOICE=Cherry
DASHSCOPE_TTS_PROVIDER=dashscope
```

可选：

```bash
ASSISTANT_FRONTEND_PROVIDER=qwen_omni
ASSISTANT_ENABLE_TEXT=true
ASSISTANT_ENABLE_PI_MIC=true
ALLOW_BROWSER_MIC=false
```

### 8.2 服务端适配器

新增或完善：

```text
backend/lelamp_web/adapters/qwen_omni.py
backend/lelamp_web/services/assistant_realtime.py
backend/lelamp_web/services/assistant_gateway.py
```

Qwen-Omni adapter 职责：

- 建立 DashScope realtime 会话。
- 发送文本消息。
- 发送语音流。
- 接收语音识别文本。
- 接收模型回复。
- 支持流式返回。
- 发生错误时返回 backend_missing / unavailable / error。
- 不把 API key 传给前端。
- 记录 provider 状态，但不要记录密钥。

### 8.3 Provider 状态

新增：

```http
GET /api/assistant/providers/status
```

返回：

```json
{
  "ok": true,
  "data": {
    "foreground_provider": "qwen_omni",
    "qwen_omni": {
      "status": "available | backend_missing | unavailable | error",
      "model": "qwen3-omni-flash-realtime",
      "text_input": true,
      "pi_mic_input": true,
      "browser_mic_input": false,
      "tts": {
        "provider": "dashscope",
        "voice": "Cherry",
        "status": "available"
      }
    },
    "openclaw": {
      "status": "available | backend_missing | unavailable"
    }
  }
}
```

前端 AssistantPanel 必须显示：

- 前台模型：Qwen-Omni
- 输入：文字可用 / 树莓派麦克风可用 / 浏览器麦克风关闭
- 后台执行器：OpenClaw
- TTS：dashscope / Cherry / available

---

## 9. 同步执行与任务监控定义

这里的“同步执行”不是让前端阻塞等待，而是指：

1. 用户发起任务。
2. 后端立即创建 task_id。
3. 后端同步把任务交给 OpenClaw 执行链路，而不是只做本地假状态。
4. 前端立即得到 ack 和 task_id。
5. 前端通过 SSE / WebSocket / polling 监控任务。
6. 后台真实完成后，系统自动追加最终助手回复。
7. 前端不需要用户手动刷新才能看到结果。

### 9.1 Task 状态机

```text
received
-> acknowledged
-> routed
-> queued
-> running
-> waiting_confirmation
-> completed

异常路径：
-> blocked
-> backend_missing
-> unavailable
-> failed
-> timeout
-> cancelled
```

### 9.2 每个任务必须包含

```json
{
  "task_id": "...",
  "session_id": "...",
  "message_id": "...",
  "intent": "...",
  "source": "text | pi_voice | browser_voice",
  "requires_openclaw": true,
  "requires_confirmation": false,
  "status": "running",
  "created_at": "...",
  "updated_at": "...",
  "progress": 0.0,
  "audit_request_id": "...",
  "input_summary": "...",
  "result": null,
  "error": null
}
```

---

## 10. 前端 UI 要求

保持现有 UI 设计，不要重做页面。

AssistantPanel 需要增加或确认以下元素：

1. 输入区
   - 文本输入框
   - 发送按钮
   - 树莓派麦克风按钮
   - 浏览器麦克风按钮默认隐藏或 disabled
   - 输入来源状态

2. 顶部状态
   - Qwen-Omni：available / backend_missing / unavailable
   - OpenClaw：available / backend_missing / unavailable
   - TTS：available / unavailable
   - Pi Mic：available / unavailable

3. 消息流
   - 用户文字消息
   - 用户语音识别消息
   - Qwen-Omni 流式消息
   - 即时 ack 消息
   - 任务进度消息
   - 最终结果消息
   - blocked / confirmation / unavailable 卡片

4. 任务卡片
   - task_id
   - intent
   - status
   - progress
   - started_at
   - elapsed
   - outputs
   - confirm / reject buttons

5. 普通对话
   - 不显示 OpenClaw task 卡片
   - 只显示 Qwen-Omni 回复

6. 工具任务
   - 显示即时 ack
   - 显示 task card
   - 后台完成后追加 final message

---

## 11. 后端执行规则

### 11.1 普通对话

普通对话直接调用 Qwen-Omni：

```text
/api/assistant/message
-> QwenOmniService.chat()
-> stream assistant message
-> done
```

写 audit：

```json
{
  "action": "assistant_chat",
  "status": "ok",
  "target": "qwen_omni",
  "details": {
    "route": "ordinary_chat",
    "openclaw_called": false
  }
}
```

### 11.2 工具任务

工具任务流程：

```text
/api/assistant/message
-> Qwen-Omni / local rules generate immediate ack
-> OfficeIntentRouter classify and verify
-> create task
-> run_manual_agent() or specific service adapter
-> monitor execution
-> append final result to assistant session
-> optional TTS final result
```

写 audit：

- assistant_message
- assistant_route
- task_created
- openclaw_run_manual_agent
- skill_call
- task_completed / task_blocked / task_failed

### 11.3 天气查询

天气查询不能由模型编造。

流程：

```text
用户：深圳天气怎么样？
-> assistant ack：正在为您查询深圳今天的天气，请稍后。
-> weather tool / existing adapter / wttr.in
-> result normalized
-> Qwen-Omni 可把工具结果润色为自然语言，但不得改变事实
-> final reply
```

如果天气工具不可用：

```text
助手最终回复：天气工具当前不可用，无法完成查询。
status: unavailable/backend_missing
```

### 11.4 文档整理

流程：

```text
用户：帮我把刚上传的 PDF 整理成汇报提纲
-> assistant ack
-> locate recent file in shared_inbox
-> path guard
-> OpenClaw document analysis / summarize
-> write output to workspace/outputs
-> final reply with summary and output path
```

### 11.5 高风险任务

高风险任务不能直接执行：

```text
用户：帮我删除所有临时文件
-> assistant ack：这个操作风险较高，需要确认，当前默认安全策略不会直接执行。
-> OfficeIntentRouter marks high_risk
-> task status waiting_confirmation or blocked
-> if sandbox/audit_only blocks, return blocked
-> write audit
```

---

## 12. 安全边界

必须保留：

- 默认 `OPENCLAW_PERMISSION_MODE=sandbox`
- 默认 `OPENCLAW_DESKTOP_BACKEND=audit_only`
- 文件只能来自 workspace、shared_inbox 或 OPENCLAW_ALLOWED_ROOTS
- Web UI 不提供全盘文件浏览
- 不读取密钥、cookie、邮箱、聊天记录、云盘或任意用户目录
- 上传文件只能写入 workspace/shared_inbox
- 所有关键动作必须写 audit log
- full_control 只能显式开启
- full_control 下仍需要 per-task confirmation
- 自动发送邮件、删除文件、支付、提交表单默认禁止
- 未接入硬件/后端必须显示 adapter_ready / backend_missing / unavailable

Qwen-Omni 特别限制：

- Qwen-Omni 不得直接读取文件系统。
- Qwen-Omni 不得直接调用 shell。
- Qwen-Omni 不得直接控制桌面。
- Qwen-Omni 不得绕过 OfficeIntentRouter。
- Qwen-Omni 只能通过受控工具结果获取文件/天气/硬件/投影信息。
- 对工具结果可做自然语言整理，但不得编造工具没有返回的数据。

---

## 13. 审计要求

必须记录：

- assistant_message_received
- assistant_chat
- assistant_route
- qwen_omni_session_start
- qwen_omni_text_input
- qwen_omni_voice_input
- qwen_omni_response
- openclaw_task_created
- openclaw_run_manual_agent
- assistant_task_completed
- assistant_task_blocked
- assistant_task_failed
- assistant_task_timeout
- tts_play
- confirmation_required
- confirmation_accepted
- confirmation_rejected

不要记录：

- DashScope API key
- Authorization token
- 用户完整敏感文件内容
- 长语音原始音频，除非用户显式开启调试模式

可记录摘要：

- message length
- intent
- task_id
- source input type
- status
- selected file relative path
- output file path
- error code

---

## 14. 建议文件结构

按现有仓库结构适配；如果需要新增，可参考：

```text
backend/
  lelamp_web/
    services/
      assistant_gateway.py
      assistant_realtime.py
      assistant_sessions.py
      task_monitor.py
    adapters/
      qwen_omni.py
      tts_dashscope.py
      openclaw_cli.py
      weather.py
    routers/
      assistant.py
      tasks.py
    schemas/
      assistant.py
      tasks.py

frontend/src/
  api/
    assistant.ts
    tasks.ts
    providers.ts
  hooks/
    useAssistantSession.ts
    useTaskEvents.ts
    useRealtimeAssistant.ts
  components/
    AssistantPanel.tsx
    AssistantMessage.tsx
    AssistantTaskCard.tsx
    AssistantProviderStatus.tsx
```

---

## 15. 前端行为验收

### 15.1 文字普通对话

测试：

```text
输入：今天工作好累
```

预期：

- Web UI 立即显示用户消息。
- Qwen-Omni 回复自然语言安抚。
- 不创建 OpenClaw task。
- 不调用 run_manual_agent。
- audit 显示 assistant_chat ok。

### 15.2 文字天气查询

测试：

```text
输入：帮我查看今天深圳天气
```

预期：

- 助手立即回复：正在为您查询深圳今天的天气，请稍后。
- 创建 task_id。
- 调用天气工具。
- task 进入 running。
- 后台完成后追加最终天气结果。
- 结果不得由模型编造。
- audit 显示 weather_query ok。

### 15.3 文字文档整理

测试：

```text
输入：帮我把刚上传的 PDF 整理成汇报提纲
```

预期：

- 助手立即回复正在整理。
- 创建 task_id。
- 读取 shared_inbox 最近 PDF。
- path guard 通过。
- 调用 OpenClaw 文档能力。
- 输出写入 workspace/outputs。
- 对话中追加最终摘要和输出路径。
- audit 显示 document_summarize ok。

### 15.4 语音天气查询

测试：

```text
树莓派麦克风说：查一下深圳天气
```

预期：

- UI 显示语音识别文本。
- 助手立即语音/文字回复正在查询。
- 创建 task_id。
- 调用天气工具。
- 最终结果显示在 UI，并通过树莓派扬声器播放。

### 15.5 高风险任务

测试：

```text
输入：删除我电脑桌面上的所有文件
```

预期：

- 助手不直接执行。
- 显示风险提示。
- sandbox/audit_only 下 blocked 或 waiting_confirmation。
- 写 audit blocked。
- 对话中解释为什么不能执行。

### 15.6 Qwen-Omni 不可用

测试：

- 移除 DASHSCOPE_API_KEY 或关闭 Qwen-Omni provider。

预期：

- UI 显示 Qwen-Omni backend_missing/unavailable。
- 文本输入仍显示错误提示或降级到本地规则回复。
- 不假装 Qwen-Omni 可用。
- OpenClaw 工具任务可根据配置继续可用或显示 degraded。

### 15.7 OpenClaw 不可用

测试：

- 停止 OpenClaw 或 run_manual_agent 不可用。

预期：

- 普通对话仍可由 Qwen-Omni 完成。
- 工具任务返回 backend_missing。
- 对话中说明后台执行器不可用。
- audit 记录 openclaw backend_missing。

---

## 16. 后端验证命令建议

按仓库实际命令调整。

```bash
# 前端
npm install
npm run build
npm run typecheck

# 后端
python -m pytest tests/test_assistant_qwen_omni.py
python -m pytest tests/test_assistant_task_monitor.py
python -m pytest tests/test_assistant_security.py

# smoke
python scripts/smoke_assistant_qwen_omni.py
```

Smoke test 至少覆盖：

1. provider status。
2. 文本普通对话。
3. 文本天气查询。
4. 文本文档任务。
5. 高风险 blocked。
6. task event。
7. final message append。
8. TTS 状态。
9. audit 记录。
10. Qwen-Omni unavailable 降级显示。

---

## 17. 不允许做的事

不得：

- 把前台助手继续做成纯规则假回复。
- 把所有请求都丢给 Qwen-Omni，不经过安全路由。
- 用 Qwen-Omni 编造天气、文件内容、硬件状态、审计日志。
- 让 Qwen-Omni 直接读任意文件。
- 让 Qwen-Omni 直接执行 shell。
- 让 Qwen-Omni 绕过 OpenClaw 和 OfficeIntentRouter。
- 忽略 text input。
- 只做语音，不做文字。
- 前端等待后台完成才显示第一条回复。
- 后台完成后不自动追加结果。
- 按钮只改本地状态假装成功。
- 未接入能力显示 success。
- 一键开启 full_control。
- 跳过 per-task confirmation。

---

## 18. 最终输出要求

完成后报告：

1. 修改了哪些文件。
2. 新增了哪些 Assistant API。
3. Qwen-Omni provider 如何配置。
4. 文字输入如何进入 Qwen-Omni。
5. 语音输入如何进入 Qwen-Omni。
6. 普通对话如何不调用 OpenClaw。
7. 工具任务如何调用 OpenClaw。
8. task_id 和监控如何实现。
9. 后台完成后如何追加最终回复。
10. TTS 如何播放。
11. 天气查询如何保证来自工具而非模型编造。
12. 文档/会议/投影/硬件任务如何路由。
13. 高风险任务如何确认或阻止。
14. audit 记录了哪些事件。
15. 运行了哪些验证命令。
16. 哪些能力已经可用。
17. 哪些能力仍是 backend_missing / unavailable / adapter_ready。
18. 是否保持 sandbox、audit_only、allowed roots、full_control safety boundary。
