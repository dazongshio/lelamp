import { BookOpen, ExternalLink, FilePlus2, RefreshCw, Save, SplitSquareHorizontal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { getDocmostStatus, getWikiPage, getWikiPages, saveWikiPage } from "../api/wiki";
import type { DocmostStatus, WikiPageItem } from "../api/types";
import { Card } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { renderMarkdown } from "../components/WorkspaceFileViewer";
import "./pages.css";

const defaultDraft = "# 新 Wiki 页面\n\n在这里记录会议纪要、扫描结论、项目资料或操作手册。";

export function WikiPage() {
  const [docmostStatus, setDocmostStatus] = useState<DocmostStatus | null>(null);
  const [pages, setPages] = useState<WikiPageItem[]>([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [title, setTitle] = useState("新 Wiki 页面");
  const [content, setContent] = useState(defaultDraft);
  const [savedContent, setSavedContent] = useState(defaultDraft);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [mode, setMode] = useState<"edit" | "preview" | "split">("split");

  const dirty = content !== savedContent;
  const currentPage = useMemo(() => pages.find((page) => page.path === selectedPath) ?? null, [pages, selectedPath]);

  async function load(preferredPath = selectedPath) {
    setBusy(true);
    setError("");
    try {
      const [statusResponse, pagesResponse] = await Promise.all([
        getDocmostStatus().catch(() => null),
        getWikiPages(),
      ]);
      if (statusResponse) setDocmostStatus(statusResponse.data);
      const nextPages = pagesResponse.data.pages;
      setPages(nextPages);
      const nextPath = preferredPath || nextPages[0]?.path || "";
      if (nextPath) {
        await openPage(nextPath, nextPages);
      } else {
        newDraft();
      }
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function openPage(path: string, knownPages = pages) {
    setError("");
    setMessage("");
    const existing = knownPages.find((page) => page.path === path);
    if (existing) setTitle(existing.title);
    try {
      const response = await getWikiPage(path);
      setSelectedPath(response.data.page.path);
      setTitle(response.data.page.title);
      setContent(response.data.content);
      setSavedContent(response.data.content);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  function newDraft() {
    setSelectedPath("");
    setTitle("新 Wiki 页面");
    setContent(defaultDraft);
    setSavedContent(defaultDraft);
    setMessage("");
  }

  async function save() {
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const contentWithTitle = syncMarkdownTitle(title, content);
      const response = await saveWikiPage({ path: selectedPath || undefined, title, content: contentWithTitle });
      const page = response.data.page;
      setSelectedPath(page.path);
      setTitle(page.title);
      setContent(response.data.content);
      setSavedContent(response.data.content);
      setPages((current) => upsertPage(current, page));
      setMessage(response.data.created ? "已创建 Wiki 页面。" : "已保存 Wiki 页面。");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    void load("");
  }, []);

  const docmostUrl = docmostStatus?.url || "";

  return (
    <>
      <PageHeader
        title="知识库"
        description="在控制台直接查看和编辑本地 Markdown Wiki；需要外部协作时再同步到 Docmost。"
        actions={
          <div className="button-row">
            <button className="ghost-button" onClick={() => void load(selectedPath)} disabled={busy || saving}>
              <RefreshCw size={16} /> 刷新
            </button>
            <button className="primary-button" onClick={() => void save()} disabled={saving || !title.trim()}>
              <Save size={16} /> {saving ? "保存中" : "保存"}
            </button>
          </div>
        }
      />

      <div className="wiki-workbench">
        {error && <div className="danger-panel wiki-wide">操作失败：{error}</div>}
        {message && <div className="success-panel wiki-wide">{message}</div>}

        <aside className="wiki-sidebar">
          <div className="wiki-sidebar__header">
            <div>
              <span>本地页面</span>
              <strong>{pages.length}</strong>
            </div>
            <button className="icon-button" onClick={newDraft} aria-label="新建 Wiki 页面" title="新建 Wiki 页面">
              <FilePlus2 size={16} />
            </button>
          </div>
          <div className="wiki-page-list">
            {pages.map((page) => (
              <button
                key={page.path}
                className={`wiki-page-item ${page.path === selectedPath ? "selected" : ""}`}
                onClick={() => void openPage(page.path)}
                disabled={busy || saving}
              >
                <span>{page.title}</span>
                <small>{page.excerpt || page.path}</small>
                <em>{formatUpdated(page.updated_at)} · {page.size_label}</em>
              </button>
            ))}
            {!pages.length && <p className="small muted">还没有 Wiki 页面，保存当前草稿即可创建。</p>}
          </div>
        </aside>

        <main className="wiki-editor-shell">
          <div className="wiki-editor-toolbar">
            <div className="wiki-editor-title">
              <BookOpen size={18} />
              <div>
                <span>{selectedPath || "workspace/wiki/新页面.md"}</span>
                <input className="wiki-title-input" value={title} onChange={(event) => setTitle(event.target.value)} />
              </div>
            </div>
            <div className="wiki-mode-tabs" aria-label="知识库显示模式">
              <button className={mode === "edit" ? "selected" : ""} onClick={() => setMode("edit")}>编辑</button>
              <button className={mode === "split" ? "selected" : ""} onClick={() => setMode("split")}>
                <SplitSquareHorizontal size={14} /> 双栏
              </button>
              <button className={mode === "preview" ? "selected" : ""} onClick={() => setMode("preview")}>预览</button>
            </div>
          </div>

          <div className={`wiki-editor-grid wiki-editor-grid--${mode}`}>
            {mode !== "preview" && (
              <textarea
                className="wiki-markdown-input"
                value={content}
                onChange={(event) => setContent(event.target.value)}
                spellCheck={false}
              />
            )}
            {mode !== "edit" && (
              <div className="wiki-preview workspace-file-viewer__markdown">
                {renderMarkdown(content)}
              </div>
            )}
          </div>

          <div className="wiki-editor-footer">
            <span className={dirty ? "dirty-dot" : "saved-dot"} />
            <span>{dirty ? "有未保存修改" : "已保存"}</span>
            {currentPage && <span>{currentPage.path}</span>}
          </div>
        </main>

        <aside className="wiki-docmost-panel">
          <Card title="协作知识库" action={<StatusBadge status={docmostStatus?.status ?? "pending"} />}>
            <div className="definition-grid">
              <span>服务地址</span><strong>{docmostUrl || "-"}</strong>
              <span>默认空间</span><strong>{docmostStatus?.default_space || "-"}</strong>
              <span>密钥状态</span><StatusBadge status={docmostStatus?.configured ? "enabled" : "needs_config"} label={docmostStatus?.configured ? "已配置" : "待配置"} />
            </div>
            {docmostStatus?.message && <p className="blue-note">{docmostStatus.message}</p>}
            {docmostUrl && (
              <a className="ghost-button wiki-docmost-link" href={docmostUrl} target="_blank" rel="noreferrer">
                <ExternalLink size={15} /> 打开 Docmost
              </a>
            )}
          </Card>
        </aside>
      </div>
    </>
  );
}

function upsertPage(pages: WikiPageItem[], page: WikiPageItem) {
  return [page, ...pages.filter((item) => item.path !== page.path)].sort((a, b) => {
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  });
}

function formatUpdated(value: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function syncMarkdownTitle(title: string, content: string) {
  const safeTitle = title.trim() || "新 Wiki 页面";
  const lines = content.split(/\r?\n/);
  const firstContentIndex = lines.findIndex((line) => line.trim());
  if (firstContentIndex >= 0 && /^#{1,3}\s+/.test(lines[firstContentIndex].trim())) {
    const next = [...lines];
    next[firstContentIndex] = `# ${safeTitle}`;
    return next.join("\n");
  }
  return `# ${safeTitle}\n\n${content.trimStart()}`;
}
