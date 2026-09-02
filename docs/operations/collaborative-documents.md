# 协作文档部署、备份与恢复

LeLamp 使用应用内原生文档引擎，正文、权限、评论、历史和附件映射保存在工作区
`.documents` 目录；实时协作增量保存在 `.documents/collaboration`。用户不需要打开
Docmost，也不得把 Docmost 或模型密钥写入前端配置。

## 启动与检查

```bash
systemctl --user enable --now lelamp-web-console.service lelamp-collaboration.service
systemctl --user status lelamp-web-console.service lelamp-collaboration.service
```

默认主页面使用 `8790`，协作 WebSocket 使用 `8791`。两项服务必须使用相同的
`LELAMP_WEB_TOKEN`。反向代理使用 HTTPS 时，应把协作地址配置为同源的 WSS 地址，
并设置 `LELAMP_COLLAB_URL`。

启动日志只显示不含凭据的访问地址。首次访问需要在登录/设置界面输入控制令牌，令牌由
浏览器本地保存并通过 `Authorization` 请求头发送；不要把令牌拼进 URL、截图或日志。

## 备份

```bash
scripts/backup_document_workspace.sh
```

命令生成 `tar.gz` 和对应的 `sha256` 文件。备份包含文档正文、稳定 ID、权限、评论、
历史、附件和协作状态。原始会议 Markdown 位于工作区其他目录，应随常规工作区备份
一并保留；导入和迁移不会删除这些原文件。

## 恢复演练

只允许恢复到没有 `.documents` 的目标工作区：

```bash
scripts/restore_document_workspace.sh backups/lelamp-documents-时间.tar.gz /tmp/lelamp-restore
```

恢复脚本先检查归档路径，拒绝绝对路径、`..` 和非 `.documents` 内容，并拒绝覆盖已有
文档数据。恢复后可临时设置 `OPENCLAW_WORKSPACE=/tmp/lelamp-restore` 启动服务并检查
文档、权限、评论、附件和历史，再切换正式工作区。

## 升级与回滚

升级前先停止写入并备份：

```bash
systemctl --user stop lelamp-web-console.service lelamp-collaboration.service
scripts/backup_document_workspace.sh
systemctl --user start lelamp-collaboration.service lelamp-web-console.service
```

数据模型升级只能增量执行。发生故障时停止两项服务，把当前 `.documents` 移到隔离目录，
使用上述恢复命令恢复最近备份，然后重新启动。不要删除旧会议 Markdown。

## 故障降级

协作服务不可用时，编辑器显示“离线，修改尚未同步”，不得显示虚假的“已保存”。恢复
连接后 Yjs 会合并离线修改。主 API 不可用时不要继续覆盖正文；先恢复 API，再根据版本
冲突提示选择历史版本。附件最大 20 MB，上传可取消，存储文件名使用随机 ID。

## 许可证

当前原生方案使用 Tiptap、Yjs 和 Hocuspocus；具体版本以 `package-lock.json` 为准。
Docmost 未作为运行时主引擎。发布前需保留依赖许可证清单，并在升级依赖后重新检查
许可证及 NOTICE 要求。
