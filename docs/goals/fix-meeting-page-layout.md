# Goal: Fix Meeting Page Layout and Information Architecture

当前 LeLamp 控制台 Web UI 的 Meeting 会议工作流页面功能已经接上，但页面太乱：

- 主内容区被右侧 AssistantPanel 挤压。
- 7 个 workflow step card 横向并排，导致每张卡过窄。
- 中文和英文标题出现竖排、断裂、重叠。
- workflow stepper、作业摘要、步骤详情、右侧对话同时堆在首屏，信息密度过高。
- 小屏或 1440px 宽度下不可读。
- 部分卡片有水平溢出或被裁切。
- 用户很难知道当前正在处理哪一步、下一步该做什么。

请只优化 Meeting 页面布局，不要重做整个 UI，不要破坏现有功能、API、路由和安全边界。

## Scope

只重点修改：

- MeetingPage
- Meeting workflow 相关组件
- WorkflowStepper
- WorkflowStepCard
- Meeting job summary
- Meeting output/result sections
- AssistantPanel 在 Meeting 页面中的布局适配
- 必要的 CSS / layout tokens

不要重构其他页面，除非是抽取通用布局修复且不影响其他页面。

## Core Objective

把 Meeting 页面从“所有信息同时横向堆满”改成更清晰的工作台布局：

1. 顶部显示会议页标题、刷新按钮、当前作业状态。
2. Stepper 保留，但只作为流程导航，不承载大量内容。
3. 主区域左侧显示“当前步骤详情”，不要同时横向展开 7 个大卡片。
4. 7 个步骤改为：
   - 横向 stepper
   - 下方 compact step list / tabs
   - 当前选中步骤详情面板
5. 底部展示会议输出结果：
   - 会议纪要摘要
   - 决策列表
   - 行动项表格
   - follow-up email 草稿入口
6. 右侧 AssistantPanel 固定宽度，内部滚动，不挤压主内容到不可读。
7. 页面在 1440px 宽度下必须清晰可读。
8. 页面不能出现文字竖排、卡片重叠、横向溢出。

## Preserve Existing Requirements

Meeting 页面仍必须保留这些流程：

1. 导入 transcript
2. 生成会议纪要
3. 提取 decisions
4. 提取 action items
5. 生成 follow-up email
6. 创建 reminders
7. 投影确认

每一步仍然需要展示：

- 输入文件
- 系统理解
- AI 执行结果
- 用户确认点
- 输出文件路径

但不要把 7 个完整步骤卡片同时横向塞进一行。

## Proposed Layout

### 1. Page Shell

Meeting 页面使用三栏结构：

```text
┌──────────────────────────────────────────────────────────────┬─────────────────────┐
│ Main content                                                  │ Assistant panel      │
│                                                              │ fixed width          │
└──────────────────────────────────────────────────────────────┴─────────────────────┘
