# Goal: Integrate Tongyi Tingwu as LeLamp First Usable Meeting Module

当前 LeLamp 控制台 Web UI 的 Meeting 页面已经具备基础 UI 和任务结构，但会议能力仍不够可用。请采用通义听悟作为 LeLamp 第一版会议模块的外部会议引擎，完成从树莓派麦克风采集、实时转写、会后智能纪要、OpenClaw 后处理、workspace 保存、UI 展示、任务监控和审计的完整闭环。

本任务重点不是重新设计 UI，而是把会议功能真正做成可用的功能模块。

## Background

LeLamp 是运行在树莓派上的本地 AI 办公工作终端。Web UI 由办公电脑通过浏览器访问，用于共享工作空间、触发任务、查看状态、控制投影、查看审计日志和使用前台助手。

Meeting 会议模块必须支持：

- 导入 transcript
- 实时会议采集
- 生成会议纪要
- 提取 decisions
- 提取 action items
- 生成 follow-up email draft
- 创建 reminders
- 生成 projection confirmation
- 保存结果到 workspace
- 所有关键动作写 audit log
- 不越权读取办公电脑目录

现在采用通义听悟作为第一版会议引擎。

目标链路：

```text
树莓派麦克风 / USB 麦克风
-> LeLamp Meeting Service
-> 通义听悟 CreateTask 创建 realtime meeting
-> WebSocket 推送音频流
-> 实时 transcript 回传
-> 会议结束 stop
-> 创建会议纪要分析任务
-> GetTask 获取智能纪要状态与结果
-> OpenClaw 后处理 decisions/action items/follow-up/projection card
-> 保存到 /workspace/meetings/{meeting_id}
-> Meeting UI 展示结果
-> AssistantPanel 主动回复用户
-> Audit 页面可追溯
