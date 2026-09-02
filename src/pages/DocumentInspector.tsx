import { ArchiveRestore, Clock3, Download, FilePlus2, ImagePlus, MessageSquare, Paperclip, Sparkles, Star, Trash2, Users, X } from "lucide-react";
import type { Editor } from "@tiptap/core";
import type { Dispatch, RefObject, SetStateAction } from "react";
import { htmlToMarkdown } from "../utils/documentMarkdown";
import type { CollaborativeDocument, DocumentAttachment, DocumentComment, DocumentPermission, DocumentRevision } from "../api/documents";
import { formatBytes, formatRelative, formatTime, roleLabel, type InspectorView } from "./documentPageSupport";

type AiSuggestion = { operation: string; label: string; text: string; baseVersion: number };
type CollaboratorRole = "editor" | "commenter" | "viewer";

export interface DocumentInspectorModel {
  selected: CollaborativeDocument | null;
  inspectorOpen: boolean;
  inspector: InspectorView;
  setInspector: Dispatch<SetStateAction<InspectorView>>;
  setInspectorOpen: Dispatch<SetStateAction<boolean>>;
  commentDraft: string;
  setCommentDraft: Dispatch<SetStateAction<string>>;
  busy: boolean;
  addComment: () => Promise<void>;
  comments: DocumentComment[];
  replyToComment: (comment: DocumentComment) => Promise<void>;
  toggleComment: (comment: DocumentComment) => Promise<void>;
  revisionPreview: DocumentRevision | null;
  setRevisionPreview: Dispatch<SetStateAction<DocumentRevision | null>>;
  editor: Editor | null;
  revisions: DocumentRevision[];
  previewRevision: (revision: DocumentRevision) => Promise<void>;
  restoreRevision: (revision: DocumentRevision) => Promise<void>;
  copyShareLink: (principalId: string) => Promise<void>;
  removeCollaborator: (principalId: string) => Promise<void>;
  collaboratorId: string;
  setCollaboratorId: Dispatch<SetStateAction<string>>;
  collaboratorRole: CollaboratorRole;
  setCollaboratorRole: Dispatch<SetStateAction<CollaboratorRole>>;
  saveCollaborator: () => Promise<void>;
  attachments: DocumentAttachment[];
  downloadAttachment: (attachment: DocumentAttachment) => Promise<void>;
  uploadProgress: number | null;
  uploadController: RefObject<AbortController | null>;
  uploadAttachment: (file?: File) => Promise<void>;
  toggleFavorite: () => Promise<void>;
  duplicateDocument: () => Promise<void>;
  downloadMarkdown: () => Promise<void>;
  downloadHtml: () => void;
  printPdf: () => void;
  downloadDocx: () => Promise<void>;
  restoreDocument: () => Promise<void>;
  purgeDocument: () => Promise<void>;
  moveToTrash: () => Promise<void>;
  aiBusy: boolean;
  requestAiSuggestion: (operation: string, label: string) => Promise<void>;
  aiSuggestion: AiSuggestion | null;
  setAiSuggestion: Dispatch<SetStateAction<AiSuggestion | null>>;
  acceptAiSuggestion: (mode: "insert" | "replace") => Promise<void>;
}

export function DocumentInspector({ model }: { model: DocumentInspectorModel }) {
  const { selected, inspectorOpen, inspector, setInspector, setInspectorOpen, commentDraft, setCommentDraft, busy, addComment, comments, replyToComment, toggleComment, revisionPreview, setRevisionPreview, editor, revisions, previewRevision, restoreRevision, copyShareLink, removeCollaborator, collaboratorId, setCollaboratorId, collaboratorRole, setCollaboratorRole, saveCollaborator, attachments, downloadAttachment, uploadProgress, uploadController, uploadAttachment, toggleFavorite, duplicateDocument, downloadMarkdown, downloadHtml, printPdf, downloadDocx, restoreDocument, purgeDocument, moveToTrash, aiBusy, requestAiSuggestion, aiSuggestion, setAiSuggestion, acceptAiSuggestion } = model;
  return (
    <>
      {selected && inspectorOpen && (
        <aside className="docs-inspector">
          <header>
            <nav>
              <button className={inspector === "comments" ? "active" : ""} onClick={() => setInspector("comments")}>评论</button>
              <button className={inspector === "history" ? "active" : ""} onClick={() => setInspector("history")}>历史</button>
              <button className={inspector === "info" ? "active" : ""} onClick={() => setInspector("info")}>信息</button>
              <button className={inspector === "ai" ? "active" : ""} onClick={() => setInspector("ai")}>AI</button>
            </nav>
            <button className="docs-icon-button" onClick={() => setInspectorOpen(false)} aria-label="关闭侧栏"><X size={18} /></button>
          </header>
          {inspector === "comments" && (
            <div className="docs-inspector__body">
              <div className="docs-comment-box">
                <textarea value={commentDraft} onChange={(event) => setCommentDraft(event.target.value)} placeholder="添加评论；选中文字后评论可记录引用…" />
                <button className="docs-primary-button" disabled={!commentDraft.trim() || busy} onClick={() => void addComment()}>发送评论</button>
              </div>
              <div className="docs-comments">
                {comments.map((comment: DocumentComment) => (
                  <article className={comment.resolved ? "resolved" : ""} key={comment.id}>
                    <div><span>{comment.author_name.slice(0, 1)}</span><strong>{comment.author_name}</strong><time>{formatRelative(comment.created_at)}</time></div>
                    {comment.anchor_text && <blockquote>{comment.anchor_text}</blockquote>}
                    {comment.parent_id && <small>回复评论</small>}
                    <p>{comment.body}</p>
                    <div>
                      {!comment.parent_id && <button onClick={() => void replyToComment(comment)}>回复</button>}
                      <button onClick={() => void toggleComment(comment)}>{comment.resolved ? "重新打开" : "标记为已解决"}</button>
                    </div>
                  </article>
                ))}
                {!comments.length && <div className="docs-side-empty"><MessageSquare size={28} /><p>还没有评论</p><span>选择正文后添加第一条评论。</span></div>}
              </div>
            </div>
          )}
          {inspector === "history" && (
            <div className="docs-inspector__body docs-history">
              {revisionPreview && (
                <section className="docs-revision-compare">
                  <header><strong>版本对比</strong><button onClick={() => setRevisionPreview(null)}>关闭</button></header>
                  <div>
                    <article><b>历史版本</b><pre>{revisionPreview.content}</pre></article>
                    <article><b>当前版本</b><pre>{htmlToMarkdown(editor?.getHTML() ?? "")}</pre></article>
                  </div>
                </section>
              )}
              {revisions.map((revision: DocumentRevision) => (
                <article key={revision.id}>
                  <div><Clock3 size={16} /><strong>{formatTime(revision.created_at)}</strong></div>
                  <p>{revision.summary}</p>
                  <span>{revision.actor_name} · 版本 {revision.content_version}</span>
                  <div>
                    <button onClick={() => void previewRevision(revision)}>查看并对比</button>
                    <button onClick={() => void restoreRevision(revision)}>恢复此版本</button>
                  </div>
                </article>
              ))}
            </div>
          )}
          {inspector === "info" && (
            <div className="docs-inspector__body docs-info">
              <section>
                <h3>共享与权限</h3>
                {(selected.permissions ?? []).map((permission: DocumentPermission) => (
                  <div className="docs-member" key={`${permission.principal_type}:${permission.principal_id}`}>
                    <span>{permission.display_name.slice(0, 1)}</span>
                    <div><strong>{permission.display_name}</strong><small>{permission.principal_id}</small></div>
                    <b>{roleLabel(permission.role)}</b>
                    {permission.role !== "owner" && selected.role === "owner" && (
                      <>
                        <button onClick={() => void copyShareLink(permission.principal_id)}>复制链接</button>
                        <button onClick={() => void removeCollaborator(permission.principal_id)}>移除</button>
                      </>
                    )}
                  </div>
                ))}
                {selected.role === "owner" && (
                  <div className="docs-permission-form">
                    <input value={collaboratorId} onChange={(event) => setCollaboratorId(event.target.value)} placeholder="成员标识或用户名" />
                    <select value={collaboratorRole} onChange={(event) => setCollaboratorRole(event.target.value as typeof collaboratorRole)}>
                      <option value="editor">可编辑</option>
                      <option value="commenter">可评论</option>
                      <option value="viewer">可查看</option>
                    </select>
                    <button className="docs-secondary-button" onClick={() => void saveCollaborator()} disabled={!collaboratorId.trim()}>
                      <Users size={16} />添加
                    </button>
                  </div>
                )}
                <p>只有所有者可以管理成员；编辑者不能修改其他人的角色。</p>
              </section>
              <section>
                <h3>附件</h3>
                {attachments.map((attachment: DocumentAttachment) => (
                  <button className="docs-attachment" key={attachment.id} onClick={() => void downloadAttachment(attachment)}>
                    <Paperclip size={15} /><span>{attachment.filename}</span><small>{formatBytes(attachment.size)} · 下载</small>
                  </button>
                ))}
                {uploadProgress !== null && (
                  <div className="docs-upload-progress">
                    <div><span style={{ width: `${uploadProgress}%` }} /></div>
                    <small>{uploadProgress}%</small>
                    <button onClick={() => uploadController.current?.abort()}>取消</button>
                  </div>
                )}
                <label className="docs-secondary-button"><ImagePlus size={16} />上传附件<input type="file" onChange={(event) => void uploadAttachment(event.target.files?.[0])} /></label>
              </section>
              <section>
                <h3>文档操作</h3>
                <button className="docs-action-row" onClick={() => void toggleFavorite()}><Star size={17} />{selected.favorite ? "取消收藏" : "收藏文档"}</button>
                <button className="docs-action-row" onClick={() => void duplicateDocument()} disabled={busy}><FilePlus2 size={17} />创建副本</button>
                <button className="docs-action-row" onClick={() => void downloadMarkdown()}><Download size={17} />导出 Markdown</button>
                <button className="docs-action-row" onClick={downloadHtml}><Download size={17} />导出 HTML</button>
                <button className="docs-action-row" onClick={printPdf}><Download size={17} />打印或导出 PDF</button>
                <button className="docs-action-row" onClick={() => void downloadDocx()} disabled={busy}><Download size={17} />导出 DOCX</button>
                {selected.status === "trashed"
                  ? <>
                      <button className="docs-action-row" onClick={() => void restoreDocument()}><ArchiveRestore size={17} />恢复文档</button>
                      <button className="docs-action-row docs-action-row--danger" onClick={() => void purgeDocument()}><Trash2 size={17} />永久删除</button>
                    </>
                  : <button className="docs-action-row docs-action-row--danger" onClick={() => void moveToTrash()}><Trash2 size={17} />移入回收站</button>}
              </section>
            </div>
          )}
          {inspector === "ai" && (
            <div className="docs-inspector__body docs-ai">
              <div className="docs-ai__hero"><Sparkles size={23} /><div><strong>AI 文档助手</strong><p>先生成建议，确认后再写入正文。</p></div></div>
              {[
                ["summarize", "总结选中内容"],
                ["rewrite", "改写得更清晰"],
                ["shorten", "缩短内容"],
                ["expand", "扩写内容"],
                ["actions", "提取决定和行动项"],
                ["title_toc", "生成标题和目录"],
              ].map(([operation, label]) => (
                <button key={operation} disabled={aiBusy} onClick={() => void requestAiSuggestion(operation, label)}>
                  <Sparkles size={15} />{label}
                </button>
              ))}
              {aiBusy && <p className="docs-ai__note">正在生成建议…</p>}
              {aiSuggestion && (
                <section className="docs-ai-preview">
                  <strong>{aiSuggestion.label} · 预览</strong>
                  <textarea value={aiSuggestion.text} onChange={(event) => setAiSuggestion({ ...aiSuggestion, text: event.target.value })} />
                  <div>
                    <button onClick={() => setAiSuggestion(null)}>放弃</button>
                    <button onClick={() => void acceptAiSuggestion("insert")} disabled={aiBusy}>插入到下方</button>
                    <button onClick={() => void acceptAiSuggestion("replace")} disabled={aiBusy || editor?.state.selection.empty}>替换选中内容</button>
                  </div>
                </section>
              )}
              <p className="docs-ai__note">AI 不会静默覆盖正文；确认写入后会产生可恢复版本并记录审计事件。</p>
            </div>
          )}
        </aside>
      )}

    </>
  );
}
