# OpenClaw 开源项目选型建议

调研时间：2026-05-04

## 推荐组合

| 层级 | 首选项目 | 作用 | 协议关注 |
| --- | --- | --- | --- |
| Agent 编排 | LangGraph | 多步骤办公任务、可中断状态图、checkpoint | MIT |
| 实时语音入口 | LiveKit Agents | WebRTC、实时语音、工具调用入口 | Apache-2.0 |
| 可选云端 agent API | OpenAI Agents SDK | 工具、handoff、guardrails、托管 tracing | MIT |
| 本地 ASR | faster-whisper | 本地语音识别 | MIT |
| 说话人分离 | WhisperX / pyannote.audio | 会议角色分离与强制对齐 | BSD-2-Clause / MIT，模型权重另查 |
| OCR | PaddleOCR | 文档扫描、中文 OCR、表格识别基础 | Apache-2.0 |
| 唤醒词 | openWakeWord | 本地唤醒词 | Apache-2.0；当前 Python 3.12 runtime 中建议作为 Python 3.11 sidecar 进程接入 |
| 浏览器代理 | browser-use | 浏览器工作流自动化 | MIT |
| GUI 解析 | OmniParser | 屏幕 UI 元素解析，供 Computer Use 使用 | MIT |
| 代码/执行沙箱 | E2B / Firecracker / gVisor | 沙箱执行环境 | Apache-2.0 |

## 暂不建议直接并入主依赖

| 项目 | 原因 |
| --- | --- |
| OpenInterpreter | 能力贴近全权/沙箱代理，但协议为 AGPL-3.0，商用闭源风险高。 |
| MinerU / Marker | 文档结构化能力强，但常见发行协议含 AGPL/GPL 风险，适合先独立进程验证。 |
| UI-TARS | 适合研究 GUI agent，但依赖和模型较重，不适合作为当前 runtime 的第一批依赖。 |

## 当前仓库落地策略

1. 保留 LeLamp 原有 LiveKit demo，不改动硬件控制入口。
2. 新增 `lelamp.office_agent`，先实现工作区白名单、审计日志、技能注册和权限分级。
3. 用 `openclaw_agent.py` 作为办公 agent 入口，默认硬件关闭，避免非树莓派环境导入 `rpi_ws281x` 失败。
4. 后续每个重型项目通过适配器接入：
   - `meeting_capture` 接 faster-whisper + WhisperX/pyannote。
   - `paper_scan` 接 PaddleOCR。
   - `desktop_operator` 接 browser-use 或 OmniParser + GUI 自动化。
   - 沙箱执行接 Firecracker/gVisor/E2B，而不是让 agent 直接跑宿主命令。
