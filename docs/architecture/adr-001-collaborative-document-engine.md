# ADR-001：LeLamp 协作文档引擎

- 状态：已接受
- 日期：2026-07-28

## 背景

LeLamp 需要在现有中文前端内提供完整的自建文档编辑与协作体验。仓库已有 Docmost 状态和“同步文件到 Wiki”适配器，但没有应用内编辑接口。

## 运行时证据

2026-07-28 对当前设备检查：

- Docmost 地址已配置为本机服务。
- `/api/workspace/info` 返回 HTTP 401。
- 当前 API 密钥不能完成基本状态读取。
- 前端只有 Markdown `textarea` 和预览，并无 CRDT 协作编辑器。
- 工作区已有约 85 个 Markdown 文件，不能通过破坏性迁移替换。

## 决策

采用 Goal 中定义的原生回退路线：

1. LeLamp 本地文档仓库负责稳定文档 ID、Markdown 可移植内容、权限、评论、历史、附件映射和审计。
2. 采用 Tiptap（基于 ProseMirror）的应用内富文本编辑前端。
3. 采用 Yjs + Hocuspocus 提供实时协作，禁止使用整页“最后写入覆盖”冒充协作。
4. Docmost 保留为可选外部同步目标，不作为用户进入文档编辑的必要条件。
5. 旧 Markdown 采用复制导入；原文件不修改、不删除。

## 结果

- Docmost 故障不会阻断文档功能。
- 需要自行维护协作服务、权限校验、备份和升级。
- Markdown 是导入导出格式；实时协作状态以 Yjs 文档为准。
- 在 Yjs 服务完成前，乐观版本 API 只能作为单人自动保存基础，不能宣称多人协作验收通过。

## 许可

- Tiptap Core 2.27.2 / React 2.27.1：MIT。
- Yjs 13.6.27：MIT。
- Hocuspocus Provider / Server 2.15.2：MIT。
- Marked 15.0.12、Turndown 7.2.0、docx 9.5.1：MIT。
- Docmost 核心：AGPL-3.0；企业目录具有单独许可，仍需在任何重新启用或分发前审查。

版本证据来自 2026-07-28 安装后的 `package-lock.json` 与各包 `package.json`。当前发布
不链接或嵌入 Docmost 代码，Docmost 仅保留为未启用的外部适配器。
