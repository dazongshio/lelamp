# 协作文档验收记录

- 验收日期：2026-07-28
- 主测试命令：`npm run test:documents`
- 构建命令：`npm run build`
- 数据范围：自动化测试使用临时工作区和临时协作端口，不写入真实会议或用户文档。

## 基础编辑

- [x] 应用内新建、打开、编辑、重命名、创建副本、收藏、删除、恢复和所有者永久删除。
- [x] 文档页不跳转 Docmost，界面为中文。
- [x] Tiptap 富文本编辑器支持标题、列表、待办、引用、代码、表格、图片、附件、
  分割线、提示块和链接。
- [x] 中文 `/` 菜单和 `@` 成员/文档引用可用。
- [x] 自动保存使用内容版本；冲突时从 Yjs 合并后重试，不显示虚假成功。
- [x] 连续插入 720 个字符不丢字、不重建编辑视图，光标位置保持在末尾。
- [x] Markdown 往返保留标题、列表、待办、引用、代码块、表格、链接、图片和提示块。
- [x] Markdown 原始 HTML 会移除脚本、事件属性及危险 URL 协议。
- [x] 支持 Markdown、HTML、DOCX 导出和浏览器 PDF 打印。

证据：`scripts/test_document_editor.mjs`、`scripts/test_document_markdown.mjs`、
`scripts/test_document_frontend.mjs` 以及生产构建。

## 协作、评论与历史

- [x] 两个独立 Yjs 客户端并发编辑后内容一致，测试约 60–80 ms 收敛。
- [x] 同段并发修改使用 Yjs CRDT，不使用整页最后写入覆盖。
- [x] 在线成员按浏览器会话区分；Awareness 同步姓名、颜色、光标和选区。
- [x] 离线与在线修改在重连后合并。
- [x] 协作状态经服务重启后恢复。
- [x] 评论支持选区引用、回复、解决和重新打开。
- [x] 历史版本可查看、双栏比较和恢复；恢复会产生新版本。

证据：`scripts/test_document_collaboration.mjs`、
`scripts/check_document_collaboration_state.mjs` 和
`lelamp_runtime/lelamp/test/test_document_workspace.py`。

## 权限与安全

- [x] 所有者、编辑者、评论者、查看者权限矩阵由后端执行。
- [x] 查看者和评论者不能修改正文，编辑者不能修改成员权限。
- [x] 文档分享令牌短时、签名、限定单文档；篡改令牌或访问其他文档会失败。
- [x] 跨站写入按 `Origin` 与当前主机校验并阻止。
- [x] 协作令牌短时、限定文档和角色；伪造签名测试通过。
- [x] 主控制令牌不再注入前端 HTML；仓库和构建包未写入外部平台密钥。
- [x] 启动 URL、服务日志和启动审计不再包含控制令牌；旧的开发启动日志已清理。
- [x] 附件使用随机存储名、校验和及路径边界；无效内容和超过 20 MB 会失败。
- [x] AI 读取和确认写入均记录文档级审计，AI 不会静默覆盖正文。

证据：`test_document_sharing.py`、`test_document_ai.py`、
`test_document_workspace.py`、运行时 HTML/日志令牌扫描，以及永久删除的所有者与回收站
状态测试。

## 会议、迁移与结果中心

- [x] 同一会议重复结束仍复用一个主文件和一个稳定文档 ID。
- [x] 内容型标题可编辑，摘要、决定、行动项和完整转写位于同一文档。
- [x] 会议页直接打开统一文档；结果中心优先使用同一个稳定文档 ID。
- [x] 旧 Markdown 复制迁移，原文件不修改；空文档、中文名、图片引用和损坏编码已测。
- [x] 批量迁移具备幂等外部 ID，不会因重复执行产生重复文档。

证据：`test_meeting_document_integration.py`、迁移单元测试以及
`MeetingPage.tsx`、`ResultCenterPage.tsx` 的稳定 ID 路由。

## 性能、附件与恢复

- [x] 100,006 字文档可加载并插入内容；本次测试环境编辑低于 40 ms。
- [x] 20 MB 以内附件具有进度、取消、失败提示和下载入口。
- [x] 协作服务中断时显示离线状态；重连合并，不显示虚假的协作在线。
- [x] 协作增量经服务重启后仍存在。
- [x] 备份恢复覆盖正文、权限、评论、历史和附件映射，并在干净临时目录自动演练。

证据：`test_document_editor.mjs`、`test_document_frontend.mjs`、
`test_document_backup.py`、`backup_document_workspace.sh` 和
`restore_document_workspace.sh`。

## 发布说明

部署、备份、恢复、升级和故障降级说明见
`docs/operations/collaborative-documents.md`。引擎与许可证决策见
`docs/architecture/adr-001-collaborative-document-engine.md`。

当前设备系统 Chromium/Firefox 的 ARM 图形帧缓冲无法生成无头截图，因此组件端到端
测试使用 JSDOM 驱动真实 React 页面；实时网络、权限、持久化和重连由独立集成测试覆盖。
生产页面仍由设备浏览器正常访问。

`npm audit` 对 React Router 7.18.1 报告一项仅影响 RSC Action 模式的 CSRF 公告
（审计按受影响的 `react-router` 与直接依赖 `react-router-dom` 计为两个高危依赖项）。
LeLamp 使用纯客户端 `BrowserRouter`，不启用 RSC、Action 或 SSR，并在 Python API
层独立执行令牌、文档权限和同源写入校验，因此该路径不可达；后续有无冲突修复版时升级。
