# LeLamp 自建协作文档能力实施 Goal

> 本 Goal 用于把 LeLamp 当前的 Markdown 查看/编辑与 Docmost 接口升级为真正可日常使用的应用内自建协作文档系统。用户应在 LeLamp 中文前端内完成创建、编辑、协作、评论、检索、分享和版本恢复，不得被直接跳转到第三方页面。

---

## 1. Codex 短 `/goal`

在仓库根目录运行 Codex，并提交：

```text
/goal Implement docs/goals/self-hosted-collaborative-docs-goal.md. Build a self-hosted in-app Chinese collaborative document workspace. Deeply integrate the existing Docmost adapter as the collaboration engine, keep documents usable inside the LeLamp UI without redirecting users to Docmost, support rich-text and Markdown interoperability, real-time collaboration, comments, permissions, page history, attachments, search, links, autosave, offline/reconnect safety, export, and auditable AI actions. Preserve existing files and meeting results, use incremental migrations, and verify every acceptance criterion.
```

随后发送：

```text
Read docs/goals/self-hosted-collaborative-docs-goal.md and implement it now. Start with a repository and runtime audit, then deliver the work in usable phases. Do not replace working meeting, result-center, Wiki, shared-space, or security behavior. Do not expose a third-party-looking page or require users to leave the LeLamp application.
```

---

## 2. 产品目标

用户打开 LeLamp 的“文档”后，应获得统一的文档工作空间：

1. 新建空白文档或从模板创建文档。
2. 像普通在线文档一样直接输入、选择、拖拽和排版。
3. 支持标题、正文、列表、待办、引用、代码、表格、图片、附件、分割线、提示块和链接。
4. 输入 `/` 打开中文块菜单，输入 `@` 提及成员或引用文档。
5. 自动保存，不需要用户管理大量结果文件。
6. 多个浏览器同时打开同一文档时，可看到在线成员、光标和实时修改。
7. 可选中文本发起评论、回复评论、解决评论。
8. 可查看版本历史、比较修改并恢复旧版本。
9. 可按空间、标题、正文、创建者和更新时间搜索。
10. 可设置私有、空间可见、指定成员可查看或可编辑。
11. 会议结束后只生成一个主文档，转写、摘要、决定、行动项都写入该文档。
12. AI 可在用户授权范围内总结、改写、扩写、提取行动项，但不得静默覆盖原文。
13. 文档可导入和导出 Markdown；需要时可导出 HTML、PDF 或 DOCX。
14. 所有面向用户的文字均使用中文，技术诊断仅放在高级区域。

---

## 3. 明确不做

- 不实现任何第三方云平台账号、云盘或专有协议的克隆。
- 不把“打开 Docmost 新标签页”当成完成。
- 不用纯 `textarea` 冒充富文本编辑器。
- 不把每次自动保存生成一个新 Markdown 文件。
- 不允许 AI 修改、分享、删除文档时绕过权限与审计。
- 不在首个版本实现电子表格、多维表格、幻灯片或完整 Office 排版兼容。
- 不为了协作功能破坏现有本地 Markdown 文件、会议记录和结果中心链接。

---

## 4. 开源方案调研结论

### 4.1 首选：深度接入 Docmost

Docmost 已提供实时协作、空间、权限、群组、评论、页面历史、搜索、附件、嵌入和图表等能力，核心采用 AGPL-3.0。当前 LeLamp 已存在 Docmost 状态和同步接口，因此优先复用现有适配层，减少重复实现权限、协作和历史记录的风险。

实施原则：

- Docmost 是后台协作文档引擎，不是另一个面向用户的产品入口。
- LeLamp 负责统一导航、中文界面、设备身份、安全审计、AI 操作和会议工作流。
- 通过同源反向代理或正式 API 接入，避免跨域令牌暴露。
- 如果采用嵌入式编辑视图，必须统一主题、隐藏重复导航，并提供加载失败降级页。
- 使用 Docmost 前必须完成 AGPL-3.0 与企业扩展许可边界审查。

参考：

- Docmost 项目与能力清单：<https://github.com/docmost/docmost>
- Docmost 官方站点：<https://docmost.com/>

### 4.2 原生编辑器备选：BlockNote

若 Docmost 无法满足应用内定制或授权要求，使用 BlockNote 构建 LeLamp 原生块编辑器。BlockNote 是 React 块式富文本编辑器，基于 ProseMirror，并支持 Yjs 实时协作以及 Markdown/HTML 转换。它适合当前 React 前端，但评论、历史、权限、搜索和附件服务仍需自行实现或组合其他服务。

参考：

- BlockNote 项目：<https://github.com/TypeCellOS/BlockNote>
- BlockNote 实时协作：<https://www.blocknotejs.org/docs/features/collaboration>
- BlockNote 编辑器 API：<https://www.blocknotejs.org/docs/reference/editor/overview>

### 4.3 协作基础设施：Yjs + Hocuspocus

如果采用原生编辑器，使用 Yjs 作为 CRDT 文档模型，使用 Hocuspocus WebSocket 服务完成同步、鉴权和持久化。不得自行发明基于最后写入覆盖的“多人协作”。

参考：

- Yjs 协作编辑指南：<https://docs.yjs.dev/getting-started/a-collaborative-editor>
- Tiptap/Hocuspocus 协作概览：<https://tiptap.dev/docs/collaboration/getting-started/overview>

### 4.4 技术决策顺序

```text
先审计现有 Docmost 接入
→ 能满足许可、部署和 API 要求：深度接入 Docmost
→ 不能满足：记录阻碍与证据
→ 再采用 BlockNote + Yjs + Hocuspocus 原生方案
```

禁止同时维护两套主编辑器。迁移期可以保留旧 Markdown 编辑器作为只读/故障降级入口。

---

## 5. 用户信息架构

### 5.1 文档首页

布局包含：

- 左侧：最近使用、我的文档、与我共享、会议记录、收藏、回收站、空间列表。
- 中间：最近文档、置顶文档、模板、新建入口。
- 顶部：全局搜索、新建文档、导入。
- 列表字段：标题、所属空间、创建者、更新时间、协作成员。

### 5.2 文档编辑页

顶部栏：

- 返回文档列表。
- 可直接修改的文档标题。
- 保存状态：正在保存、已保存、离线、同步失败。
- 在线成员头像。
- 评论按钮。
- 分享按钮。
- 更多：历史、导出、复制、移动、删除。

正文区：

- 居中、适合长文阅读，支持宽页面切换。
- 块式编辑、浮动工具条、中文 `/` 菜单。
- 支持粘贴 Markdown、HTML、图片和表格。
- 支持拖放图片与附件。
- 支持文档目录和内部锚点。

右侧面板：

- 评论线程。
- 文档信息。
- 版本历史。
- AI 助手。

移动端：

- 保留阅读、基础编辑、评论和分享。
- 复杂属性放入底部抽屉。

---

## 6. 核心数据模型

即使 Docmost 保存正文，LeLamp 仍需维护稳定的本地映射：

```text
Document
- id: 稳定 UUID
- engine: docmost | native
- engine_document_id
- title
- space_id
- owner_id
- status: active | archived | trashed
- source_type: manual | meeting | imported | ai_generated
- source_path（可选，指向原始 Markdown）
- created_at / updated_at

DocumentPermission
- document_id / principal_type / principal_id
- role: owner | editor | commenter | viewer

DocumentRevision
- document_id / revision_id / actor / created_at
- summary / restorable

DocumentAttachment
- id / document_id / filename / mime_type / size
- storage_path / checksum / created_by

DocumentAuditEvent
- document_id / actor / action / status / timestamp
- before_revision / after_revision / details
```

要求：

- ID 与标题分离，重命名不会破坏链接。
- 任何外部引擎 ID 都通过映射表保存。
- Markdown 文件路径必须经过现有 allowed-roots 校验。
- 附件文件名不能直接作为存储主键。
- 删除先进入回收站，默认可恢复。

---

## 7. API 与实时协议

至少提供以下稳定接口；实际路径可按现有代码风格调整：

```text
GET    /api/docs
POST   /api/docs
GET    /api/docs/:id
PATCH  /api/docs/:id
DELETE /api/docs/:id
POST   /api/docs/:id/restore

GET    /api/docs/:id/content
PUT    /api/docs/:id/content
POST   /api/docs/:id/import-markdown
GET    /api/docs/:id/export?format=markdown|html|pdf|docx

GET    /api/docs/:id/comments
POST   /api/docs/:id/comments
PATCH  /api/docs/:id/comments/:commentId

GET    /api/docs/:id/history
POST   /api/docs/:id/history/:revisionId/restore

GET    /api/docs/:id/permissions
PUT    /api/docs/:id/permissions

POST   /api/docs/:id/attachments
GET    /api/docs/search?q=

GET    /api/docs/:id/collaboration-token
WS     /api/docs/:id/collaboration
```

规则：

- 前端不得持有 Docmost 管理员令牌。
- 协作令牌必须短时、限定单文档和具体角色。
- 所有写接口验证登录身份、文档权限和 CSRF/来源。
- 同一修改必须有幂等键或稳定更新 ID。
- WebSocket 重连后必须先完成状态同步，再显示“已保存”。
- API 错误给前端返回结构化中文可翻译错误码，不返回 HTML 堆栈。

---

## 8. Markdown 与富文本互操作

Markdown 仍是 LeLamp 的重要可移植格式，但不作为实时协作时的唯一真相来源。

要求：

- 导入 Markdown 时保留标题、列表、待办、引用、代码块、表格、链接和图片。
- 导出 Markdown 必须确定性稳定，相同内容重复导出不产生无意义差异。
- 不可无损表达的富文本块使用明确的 HTML 或扩展标记，不能静默丢失。
- 原始 Markdown 导入后保留来源信息。
- 会议主文档支持一键导出为单个 `.md`。
- 保存期间不得创建大量带时间戳的重复结果文件。
- 旧 Markdown 文档可逐份迁移；迁移失败时原文件保持不变。

---

## 9. 会议与结果中心集成

会议流程调整为：

```text
开始会议
→ 听悟实时转写
→ 同一个会议文档持续追加转写
→ 结束会议
→ 生成内容型标题
→ 在同一文档中写入摘要、决定、行动项和完整转写
→ 将本地会议纪要和引用写入自建协作文档
→ 结果中心只显示这个主文档
```

主文档建议结构：

```markdown
# 自动生成的会议标题

## 会议摘要

## 关键决定

## 行动项

## 完整转写
```

禁止为同一场会议默认生成多个难以辨认的文件。附件、音频和外部同步信息作为主文档的关联资源展示。

---

## 10. AI 文档能力

编辑器内提供中文 AI 菜单：

- 总结选中内容。
- 改写、缩写、扩写。
- 调整语气。
- 翻译。
- 提取决定和行动项。
- 根据全文生成标题和目录。
- 对文档问答并标注引用段落。

安全规则：

- AI 默认输出预览或建议，不直接覆盖选区。
- 用户点击“替换”或“插入到下方”后才写入。
- AI 只能读取当前用户有权访问的文档和明确选择的附件。
- 每次 AI 写入记录提示类型、影响范围和前后版本，不记录密钥。
- AI 失败不能破坏当前编辑状态。
- 高风险外发、分享、删除仍需单独确认。

---

## 11. 实施阶段

### 阶段 0：审计与技术验证

- 盘点现有 `DocumentsPage`、`WikiPage`、`ResultCenterPage`、Markdown 编辑器和 Docmost API。
- 检查 Docmost 服务是否可部署、版本、许可、认证方式、反向代理和数据目录。
- 制作最小验证：两个浏览器编辑同一测试文档，确认同步、断线重连和权限隔离。
- 输出 ADR，记录选用 Docmost 或原生方案的证据。

### 阶段 1：可用的单人文档

- 文档首页、新建、重命名、自动保存、删除/恢复。
- 富文本块编辑。
- Markdown 导入和导出。
- 图片与附件。
- 搜索和最近文档。
- 迁移现有结果文档，但不删除原文件。

### 阶段 2：多人协作

- WebSocket/Yjs 实时同步。
- 在线成员、光标和选区。
- 评论与回复。
- 分享和角色权限。
- 版本历史与恢复。
- 断线、冲突和重连测试。

### 阶段 3：业务融合

- 会议单主文档。
- 结果中心与共享空间统一跳转。
- AI 编辑建议。
- 文档内部链接、模板、收藏和空间。
- 审计与管理诊断。

### 阶段 4：发布质量

- 性能、可访问性、移动端和中文界面检查。
- 数据备份与恢复演练。
- 权限穿透、令牌泄露和附件路径测试。
- 迁移回滚与故障降级验证。

---

## 12. 验收标准

### 12.1 基础编辑

- [ ] 用户可在 LeLamp 前端内新建、打开、编辑、重命名和删除文档。
- [ ] 页面不会要求用户跳转到 Docmost。
- [ ] 连续输入 30 秒不会丢字或频繁抢焦点。
- [ ] 自动保存状态真实反映后端结果。
- [ ] 刷新页面后内容保持一致。
- [ ] 中文 `/` 菜单可插入常用块。
- [ ] Markdown 导入、编辑、再导出后主要结构不丢失。

### 12.2 协作

- [ ] 两个独立浏览器可同时编辑同一文档并在 1 秒左右看到更新。
- [ ] 同时编辑同一段不会发生整页最后写入覆盖。
- [ ] 能看到在线成员和协作光标。
- [ ] 断网编辑后重连可合并，不静默丢失内容。
- [ ] 评论可创建、回复、解决和重新打开。
- [ ] 历史版本可查看并恢复，恢复操作本身产生新版本。

### 12.3 权限与安全

- [ ] 查看者不能修改正文。
- [ ] 评论者只能评论，不能改正文。
- [ ] 编辑者不能越权修改成员角色。
- [ ] 无权限用户不能通过直接 API 或 WebSocket 获取正文。
- [ ] 前端和日志不暴露 Docmost 管理令牌、模型密钥或其他凭据。
- [ ] 附件无法使用路径穿越读取工作区外文件。
- [ ] AI 读取和写入均有文档级审计事件。

### 12.4 会议与文件

- [ ] 每场会议默认只产生一个主文档。
- [ ] 标题根据会议内容自动生成且可编辑。
- [ ] 摘要、决定、行动项和转写位于同一文档。
- [ ] 旧会议 Markdown 可打开且不被破坏。
- [ ] 结果中心、会议页和文档页指向同一个稳定文档 ID。

### 12.5 性能与可靠性

- [ ] 10 万字文档可打开、滚动和编辑，不阻塞整个页面。
- [ ] 20 MB 以内常见附件上传有进度、取消和失败提示。
- [ ] 后端重启后未保存修改可恢复或明确提示。
- [ ] 协作服务不可用时进入只读或本地草稿降级，不显示虚假的“已保存”。
- [ ] 数据备份可在干净环境恢复文档、权限、评论和附件映射。

---

## 13. 必须补充的测试

- 编辑器单元测试：块转换、Markdown 往返、标题与链接。
- API 测试：CRUD、权限矩阵、幂等、错误结构。
- 协作集成测试：并发输入、同段冲突、断线重连、历史恢复。
- 浏览器端到端测试：新建 → 编辑 → 评论 → 分享 → 导出。
- 会议测试：实时转写 → 结束 → 单主文档 → 内容型标题。
- 安全测试：越权访问、伪造协作令牌、路径穿越、超大附件、脚本注入。
- 迁移测试：现有 Markdown、空文档、中文文件名、图片引用和损坏文件。

测试必须使用隔离的临时空间和测试用户，不得污染真实会议与用户文档。

---

## 14. 完成定义

只有同时满足以下条件才可标记 Goal 完成：

1. 核心验收标准全部通过并有自动化测试证据。
2. 用户在 LeLamp 应用内完成完整文档流程，无第三方跳转。
3. 多人实时编辑、评论、权限和历史均使用真实后端，不是模拟数据。
4. 会议结果收敛为单一、可编辑、可检索的主文档。
5. 旧 Markdown 与现有会议数据完成兼容验证并保留回滚路径。
6. 管理令牌和用户凭据未进入前端、日志、仓库或导出包。
7. 部署、备份、恢复、升级和故障降级有中文操作说明。
8. 完成开源许可证审查并记录所采用版本及依赖。
