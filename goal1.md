# AI Projector Console Frontend Design

## Goal

Design a static Apple-style control console for an AI projector device. The console should feel like a real productivity tool, not a marketing page. It must clearly show the device architecture, shared file space, AI interaction area, result flow, and computer control permission model.

## Final Product Model

- The total AI assistant is installed and running on the Raspberry Pi inside the projector device.
- The Raspberry Pi storage is treated as the always-on shared space.
- Files placed in the shared space are available to the Raspberry Pi AI service.
- The LAN computer is not where the AI assistant runs.
- The computer is a control target that can be reached through SSH only when the control mode allows it.
- The UI must distinguish "SSH host reachable" from "direct computer control enabled".

## Permission Model

### Sandbox Control

Sandbox control does not directly control the computer through SSH.

In sandbox control, the Raspberry Pi AI service can:

- Read and write files in the Raspberry Pi shared space.
- Generate meeting notes, summaries, tables, and draft results.
- Save generated artifacts back into the shared space.
- Show computer connection status as reachable without using direct control.

In sandbox control, the Raspberry Pi AI service cannot:

- Operate the computer desktop.
- Open apps on the computer.
- Execute arbitrary commands on the computer.
- Read computer directories outside the shared-space workflow.

### Full Control

Full control means the Raspberry Pi AI service can directly control the LAN computer through SSH.

Full control must be:

- Explicitly requested by the user.
- Clearly marked as a higher-risk mode.
- Revocable.
- Logged for audit.

Example UI wording:

- "沙箱控制不直连电脑"
- "全权控制通过 SSH 直接控制电脑"
- "请求全权控制"

## Shared Space Model

The shared folder is a persistent space, not a one-time share action.

It should be represented as:

- A fixed folder in the document workspace.
- A shared-space file row in the file list.
- A status panel showing sync, members, permissions, and audit.

The current prototype uses:

- "树莓派共享空间"
- "常开共享文件夹"
- "团队共享空间"
- "同步状态"
- "成员权限"
- "共享审计"

The shared space is the user's primary bridge between files, AI processing, and optional computer control.

## Static Prototype Pages

Prototype location:

`web/ai-projector-console/`

Preview entry:

`web/ai-projector-console/index.html`

The prototype currently includes four static pages.

### 1. Today Workspace

File:

`web/ai-projector-console/index.html`

Purpose:

- Default landing page.
- Shows the AI assistant status, meeting reminder, quick module cards, AI command entry, result flow, and computer control permission state.

Key areas:

- Left navigation.
- Main meeting focus panel.
- Quick cards for meeting, files, scanning, and AI execution.
- Result flow.
- AI command box.
- Right-side computer control status.

Important wording:

- "AI 助手运行在树莓派"
- "树莓派 AI 服务"
- "沙箱控制"
- "全权控制"

### 2. Meeting Mode

File:

`web/ai-projector-console/meeting.html`

Purpose:

- Shows projector state, audio collection, real-time transcription, generated notes, and export controls.

Key areas:

- Projector control.
- Microphone/audio readiness.
- Real-time transcript.
- Auto-generated notes.
- Export authorization.

### 3. Document Workspace

File:

`web/ai-projector-console/documents.html`

Purpose:

- Main file and AI interaction page.
- Represents the Raspberry Pi shared space, file folders, file list, selected file preview, AI query area, generated results, and computer control permissions.

Layout:

- Left: folder tree and shared-space entry.
- Center: file list, always-on shared folder, selected file content preview.
- Right: AI interaction area, source scope, member permissions, generated results, access boundary, and computer control permissions.

Important areas:

- 文件夹树
- 文件列表
- 文件内容预览
- AI 交互区
- 来源范围
- 生成结果
- 电脑控制权限

Important wording:

- "树莓派共享空间"
- "AI 助手运行在树莓派"
- "树莓派 AI 服务"
- "沙箱控制不直连电脑"
- "全权控制通过 SSH 直接控制电脑"

### 4. Result Center

File:

`web/ai-projector-console/results.html`

Purpose:

- Central archive for generated artifacts.
- Collects meeting notes, document summaries, contract tables, email drafts, and scanned-file archives.

Key result types:

- Preview drawer for lightweight results.
- Detail page for complex results.
- Authorization panel for sensitive actions.

## UI Design Direction

The visual direction is Apple-inspired but app-like:

- Light glass panels.
- Soft shadows.
- Restrained system blue for primary actions.
- Dense but calm productivity layout.
- One primary action per major workflow area.
- Sidebar navigation with compact module labels.
- Right-side inspector panels for status, permission, and audit.

The product should feel like a macOS professional utility rather than a landing page.

## Button Hierarchy

Primary actions:

- Start meeting mode.
- Import files.
- Ask AI.
- Request full control.
- Open result details.

Secondary actions:

- View agenda.
- Test audio.
- Sort files.
- Filter files.
- Summarize full document.
- Multi-file comparison.
- Generate contract table.

Status or small actions:

- Preview.
- Open original.
- Save to result center.
- Export table.
- View audit.

Sensitive actions should use stronger visual treatment and should not execute silently.

## Result Display Strategy

Generated results should not interrupt the user by default.

Use:

- Result flow on the Today Workspace.
- Generated Results panel in the Document Workspace.
- Result Center for archived outputs.
- Preview drawer for quick reading.
- Detail page for complex artifacts.
- Authorization panel for actions that leave the device or control the computer.

## Current Verification

Static structure check:

```bash
/Users/zongshi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node web/ai-projector-console/check-static-pages.js
```

Expected output:

```text
Static page checks passed
```

The check confirms required page files, core wording, key architecture terms, and shared CSS selectors exist.
