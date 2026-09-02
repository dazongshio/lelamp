# LeLamp/OpenClaw 本地AI办公工作终端调研证据记录

调研日期：2026-05-23

本记录只用于支撑产品设计方案，不把外部资料扩展成未经验证的承诺。结论采用“先证据、再判断、再标记缺口”的方式组织。

## 1. 证据边界

允许使用的材料：
- 用户提供的目标、约束和验收面。
- 当前仓库中与 LeLamp/OpenClaw 办公代理、语音、投影、扫描、桌面代理、安全边界相关的文件。
- 公开可验证资料，用于验证会议 AI、OCR、桌面代理安全、投影亮度、端侧算力和硬件风险。

不可据此直接下结论的内容：
- 未实测的投影亮度、散热、噪音、麦克风阵列效果。
- 未接入后端的 OCR、说话人分离、GUI 自动化能力。
- 未做企业安全评审的合规承诺。

## 2. 仓库内证据

### L1. LeLamp 现有硬件基础

证据：
- `README.md` 描述 LeLamp 是开源机器人灯，具备 5 轴运动、Pi Camera、麦克风/扬声器、24 个可编程 LED、动作录制与回放，并给出约 260 美元构建成本。
- `docs/0. Prerequisites.md` 给出构建门槛、3D 打印、电子焊接和约 310.64 美元 BOM 成本估计。
- `docs/5. LeLamp Control.md` 说明校准、舵机测试、RGB、音频、摄像头和动作录制回放流程。

判断：
- LeLamp 已有“桌面具身硬件终端”的机械和传感器底座，不应把产品降级为纯软件助手。
- 当前硬件不是办公终端成品，仍缺投影、算力、麦克风阵列、散热、结构空间和企业部署形态。

### L2. OpenClaw 办公代理层

证据：
- `lelamp_runtime/README.md` 明确存在 `lelamp.office_agent` 办公桌面助手层，并强调 desktop actions auditable。
- 同文件列出 P0 办公流程：本地提醒/日程、允许目录文件搜索、屏幕截图+OCR、会议 transcript 到纪要/导出/邮件草稿/提醒。
- 同文件列出 LeLamp 专属能力：状态到姿态/灯光、单帧桌面观察、环境传感事件推理、投影倒计时与行动确认卡片。
- 同文件说明桌面命令默认 `audit_only`，本地执行需要 `OPENCLAW_DESKTOP_BACKEND=local`；文件搜索/打开限制在 workspace 和 `OPENCLAW_ALLOWED_ROOTS`。

判断：
- OpenClaw 已经不是普通聊天助手雏形，而是围绕办公动作、文档、会议、投影和桌面控制的本地工作代理。
- 现阶段更适合定位为“可审计、白名单、确认优先”的办公代理，而不是全自主 GUI 操作员。

### L3. 权限与运行配置

证据：
- `lelamp_runtime/lelamp/office_agent/config.py` 定义 `PermissionMode.SANDBOX` 与 `PermissionMode.FULL_CONTROL`。
- `OfficeAgentConfig` 默认 workspace、audit log、permission mode、projection dir、memory path、desktop backend 等配置；默认权限模式来自 `OPENCLAW_PERMISSION_MODE`，默认值为 sandbox。
- `normalized()` 会把 workspace 加入 allowed roots，并追加用户显式配置的 `OPENCLAW_ALLOWED_ROOTS`。

判断：
- 产品安全边界应继承“默认沙箱、显式授权目录、全权模式环境变量开启”的设计。
- 企业版应把这些环境变量产品化为策略配置，而不是让用户手工配置。

### L4. Skill 体系与全权门禁

证据：
- `lelamp_runtime/lelamp/office_agent/skills.py` 声明 `SkillSpec`，每个 Skill 有 `name`、`mode`、`description`、`implemented`、`permission_notes`。
- 当前技能包含 `meeting_capture`、`document_workspace`、`paper_scan`、`projection_assistant`、`desktop_operator`、`xiaoai_utility`、`desktop_safe_actions`、`p0_office`、`local_file_search`、`screen_context`、`lelamp_affordance` 等。
- `desktop_operator` 标记为 `full_control`，要求 `OPENCLAW_PERMISSION_MODE=full_control` 和 per-task confirmation。
- `ensure_full_control()` 在非 full_control 时写入 blocked 审计并抛出权限错误。

判断：
- OpenClaw 的产品表达必须围绕 Skill 注册、权限模式、单项任务确认和审计记录展开。
- `mobile_bridge` 未实现，应在方案中标记为后续能力，不进入 MVP 核心承诺。

### L5. 文件白名单与审计

证据：
- `lelamp_runtime/lelamp/office_agent/workspace.py` 的 `Workspace` 是 rooted whitelist；导入文件必须是文件且位于 allowed roots，否则写入 blocked 审计并拒绝。
- 读 workspace 文件、写文本、写 JSON、解析新文件路径都会写审计；写入路径必须在 workspace 内。
- `lelamp_runtime/lelamp/office_agent/audit.py` 提供 append-only JSONL 审计日志，记录 id、timestamp、action、status、target、details。

判断：
- 文档与桌面协同场景必须写清“默认只看 workspace、显式 allowed roots、导入生成 sha256、所有动作可追溯”。
- 方案中不能承诺默认读取全盘、邮箱、云盘或浏览器 cookie。

### L6. 任务拆解

证据：
- `lelamp_runtime/lelamp/office_agent/task_planner.py` 会按会议、文档、邮件、扫描、小爱工具、智能家居、桌面安全动作、全权桌面操作等关键词拆成 Skill steps，并记录 `task.plan`。

判断：
- OpenClaw 可被设计为“任务计划器 + Skill 执行器 + 审计日志”的办公代理，不只是 LLM 对话。
- MVP 可先用规则/模板拆解，高风险或复杂任务再进入人工确认。

### L7. P0 办公闭环

证据：
- `lelamp_runtime/lelamp/office_agent/p0.py` 的 `P0OfficeService.status()` 标记会议全流程、文档工作台、日历提醒、屏幕理解适配、本地文件搜索、邮件草稿、安全桌面动作、审计权限为 P0 能力。
- `generate_meeting_followup_package()` 会生成纪要、导出 transcript、写邮件草稿、从 action items 生成提醒，并可渲染投影确认页。

判断：
- MVP 的演示路径应优先选择“会议 transcript -> 纪要 -> 行动项 -> 邮件草稿 -> 提醒 -> 投影确认”，因为仓库已有最多闭环证据。

### L8. 投影与场景感知

证据：
- `lelamp_runtime/lelamp/office_agent/projection.py` 可把 markdown 渲染到 projection output 目录，支持 confirmation、status card、countdown、action card、calibration plan。
- `lelamp_runtime/lelamp/office_agent/lelamp_experience.py` 把 assistant state 映射为 RGB 和录制动作，包含 idle、wake、listening、thinking、speaking、blocked、success、meeting、projecting 等状态。
- `lelamp_runtime/lelamp/office_agent/scanning.py` 定义纸质文档 pipeline：edge detection、perspective correction、shadow removal、OCR、structure extraction、semantic analysis，并把 PaddleOCR 作为推荐 OCR 后端。

判断：
- 投影不应定位成泛娱乐大屏，而应先作为办公确认面、倒计时面和会后行动项共识面。
- 扫描能力当前是 pipeline 和占位后端，方案中应标记 OCR 精度、拍摄角度、光照和纸张畸变为待验证。

## 3. 公开资料证据

### P1. 会议 AI 已成为办公套件标配

公开来源：
- Microsoft Teams Recap / Teams Premium Intelligent recap：`https://support.microsoft.com/en-us/teams/meetings/recap-in-microsoft-teams`
- Microsoft Teams Intelligent recap 隐私与数据安全：`https://learn.microsoft.com/en-us/microsoftteams/privacy/intelligent-recap`
- Zoom AI Companion Meeting Summary：`https://support.zoom.com/hc?id=zm_kb&sysparm_article=KB0058013`
- Zoom Smart Recording：`https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0061101`

证据摘要：
- Teams/Zoom 都已提供会议摘要、AI notes、follow-up tasks/action items、录制/转写后回顾、分享和管理能力。
- Teams 文档强调数据驻留、访问权限、敏感标签等企业治理；Zoom 文档强调主持人开启、会后邮件/聊天分享、可编辑/删除和限制条件。

判断：
- “会议理解 + 纪要 + 行动项”不是差异化本身，差异化应来自本地硬件、投影现场确认、多传感器上下文和文件/桌面后续执行。

### P2. AI 录音硬件验证了需求，但也暴露隐私与云依赖

公开来源：
- PLAUD Note 官方产品页：`https://global.plaud.ai/products/plaud-note-ai-voice-recorder`
- PLAUD AI 处理说明：`https://support.plaud.ai/hc/en-us/sections/10453705107855-Privacy`
- Limitless Pendant FAQ：`https://help.limitless.ai/en/articles/9124757-pendant-faq/`
- Limitless Privacy：`https://www.limitless.ai/privacy`

证据摘要：
- PLAUD/Limitless 均把录音、转写、摘要作为核心价值；PLAUD 宣称多语言转写、说话人标签和云端存储/处理；Limitless 明确要求录音时主动告知和取得同意，Pendant 指示灯不可完全关闭。

判断：
- 硬件化办公 AI 有明确市场信号。
- 本产品不能做“隐形录音器”，必须有可见录音/会议模式指示、同意提醒、企业策略和本地处理优先。

### P3. OCR 与文档解析有成熟开源路线，但真实文档质量要实测

公开来源：
- PaddleOCR PP-OCRv5 文档：`https://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html`
- PaddleOCR GitHub 文档：`https://github.com/PaddlePaddle/PaddleOCR`

证据摘要：
- PP-OCRv5 面向多场景、多语言文本识别，官方文档列出简体中文、繁体中文、英文、日文等默认能力及多语言支持。
- PaddleOCR 生态还包含结构化文档、表格和版面方向的 pipeline。

判断：
- MVP 可选 PaddleOCR 作为扫描后端，但合同/表格/名片等办公文档需要用用户真实样本做精度测试，不能仅凭模型介绍承诺生产可用。

### P4. 投影亮度是硬件落地的主要约束

公开来源：
- Epson projector guide：`https://epson.com/projector-guide-how-to-buy-a-projector-color-brightness`
- XGIMI lumens/ANSI lumens guide：`https://us.xgimi.com/blogs/projectors-101/lumens-ansi-lumens-in-projector`
- ProjectorCentral projector basics：`https://www.projectorcentral.com/projectors-101.cfm`

证据摘要：
- Epson 指出有窗会议室/教室建议至少约 2500 lumens，且亮度越高通常成本越高。
- XGIMI 将低/中/高环境光对应到约 300-600、600-1600、1600-2500 ANSI lumens 区间。
- ProjectorCentral 提醒未标注 ANSI/ISO 的亮度宣称要谨慎看待。

判断：
- LeLamp 办公终端的投影 MVP 应先限定为桌面/近距离确认卡片，不替代会议室主投影。
- 结构方案必须优先处理散热、噪音、体积和投影角度，不能只按 ID 美观选择。

### P5. 本地算力有可落地选项，但需要按负载分层

公开来源：
- NVIDIA Jetson Orin Nano Super Developer Kit：`https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-orin/nano-super-developer-kit/`
- Raspberry Pi AI HAT+ 文档：`https://www.raspberrypi.com/documentation/accessories/ai-hat-plus.html`

证据摘要：
- Jetson Orin Nano Super 官方标注 67 INT8 TOPS、8GB LPDDR5、7W-25W，适合边缘生成式 AI、VLM、机器人场景原型。
- Raspberry Pi AI HAT+ 有 13/26 TOPS 视觉 NPU；AI HAT+ 2 有 40 TOPS 和本地 LLM/VLM 能力，但普通 AI HAT+ 不支持 LLM/VLM。

判断：
- MVP 可先用开发板验证端侧推理和多模态流水线；量产需再做 BOM、热设计、启动时间和模型裁剪。
- 如果要强调“本地工作终端”，核心会议、OCR、文件分析和权限判断应能在本地完成；外部 LLM/ASR/TTS 只能作为显式授权增强。

### P6. 桌面代理安全需要人类确认、最小权限和隔离

公开来源：
- OpenAI ChatGPT agent：`https://openai.com/index/introducing-chatgpt-agent/`
- OpenAI Operator：`https://openai.com/index/introducing-operator/`
- Anthropic Computer Use tool：`https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/computer-use-tool`
- OWASP Top 10 for LLM Applications：`https://owasp.org/www-project-top-10-for-large-language-model-applications`
- NIST AI RMF：`https://www.nist.gov/itl/ai-risk-management-framework`

证据摘要：
- OpenAI 的 agent/Operator 产品强调用户可接管、高影响动作前确认、登录/支付等敏感信息由用户接管。
- Anthropic 文档提醒 computer use 要隔离敏感数据和敏感操作，注意 prompt injection。
- OWASP 将 prompt injection、敏感信息泄露、过度代理等列为 LLM 应用关键风险。
- NIST AI RMF 提供 Govern、Map、Measure、Manage 的风险管理框架。
- 链接抽查：Teams、Zoom、Epson、ProjectorCentral、TI、NVIDIA、PLAUD、Limitless、OWASP、NIST、PaddleOCR GitHub 在 2026-05-23 通过命令行抽查返回 200；Raspberry Pi 文档和 OpenAI 页面在命令行抽查中返回 403，但可通过浏览器/网页索引访问，作为辅助证据而非唯一依据。

判断：
- OpenClaw 的默认设计必须是 sandbox-first、白名单、按动作授权、日志审计。
- 全权模式只能作为显式开启的受控模式，必须有可见状态、停止开关、回放日志和企业策略。

## 4. 证据驱动的产品判断

1. 市场需求成立：会议 AI、AI 录音硬件、桌面代理都在增长，但云端会议摘要已经同质化。
2. 差异化不应是“又一个会议摘要工具”，而是“本地硬件终端 + 现场投影确认 + 文件白名单 + 可控执行 + 多传感器场景理解”。
3. 硬件推荐应选择底座投影作为 MVP 主线；球形内置投影的外观统一性强，但亮度、散热、重量、噪音、线缆和维护风险更高。
4. OpenClaw 的办公能力必须从 current repo 的 Skill/Planner/Workspace/Audit/P0 组合出发，优先做会议闭环、文档闭环、代办闭环和桌面协同闭环。
5. 安全边界是产品核心价值，不是附加功能；默认不可读取任意目录、不可自动发送邮件、不可自动删除/购买/支付/登录、不可静默录音。

## 5. 待验证缺口

1. 投影：在真实办公室 300/600/1000 ANSI lumens 下，20-40 英寸桌面/墙面卡片可读性、色彩、噪音和散热。
2. 麦克风：2-6 人会议、0.5-3 米距离、键盘噪音、空调噪音下的 ASR 和说话人分离表现。
3. 摄像头：灯头/底座角度对纸张扫描、桌面观察、屏幕 OCR 的覆盖率。
4. OCR：合同、扫描件、发票、名片、表格、多栏 PDF 的识别和结构化准确率。
5. 本地模型：目标硬件上 ASR/OCR/LLM/VLM 并发延迟、内存占用和热稳定性。
6. 企业部署：MDM/策略、日志保留、离线更新、密钥管理、用户授权 UI 和审计导出。
