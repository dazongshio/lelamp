import {
  Check,
  Columns2,
  Download,
  Eye,
  FileCode2,
  FileText,
  Pencil,
  RefreshCw,
  Save,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link, useLocation } from "react-router-dom";
import remarkGfm from "remark-gfm";
import { getMeetingJobs } from "../api/meeting";
import { apiErrorMessage } from "../api/client";
import { listCollaborativeDocuments, type CollaborativeDocument } from "../api/documents";
import {
  getFileDownloadUrl,
  getWorkspaceFiles,
  getWorkspacePreview,
  saveWorkspaceMarkdown,
} from "../api/shared";
import { getRecentTasks } from "../api/tasks";
import type { MeetingJob, SharedFile, SharedPreviewResponse, TaskRecord } from "../api/types";
import { EmptyState, StatusPill } from "../components/ProjectorConsole";
import { WorkspaceFileViewer } from "../components/WorkspaceFileViewer";

interface ResultArtifact {
  id: string;
  label: string;
  path: string;
  type: "minutes" | "table" | "draft" | "scan" | "summary" | "other";
  source: string;
  status: string;
  documentId?: string;
}

type EditorMode = "preview" | "edit" | "split";

export function ResultCenterPage() {
  const location = useLocation();
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [jobs, setJobs] = useState<MeetingJob[]>([]);
  const [files, setFiles] = useState<SharedFile[]>([]);
  const [documents, setDocuments] = useState<CollaborativeDocument[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [preview, setPreview] = useState<SharedPreviewResponse | null>(null);
  const [draft, setDraft] = useState("");
  const [savedDraft, setSavedDraft] = useState("");
  const [mode, setMode] = useState<EditorMode>("preview");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [taskResult, jobsResult, filesResult, documentResult] = await Promise.all([
        getRecentTasks(80),
        getMeetingJobs(),
        getWorkspaceFiles({ page_size: 200 }),
        listCollaborativeDocuments({ status: "active", sourceType: "meeting" }),
      ]);
      setTasks(taskResult.data.tasks ?? taskResult.data.items ?? []);
      setJobs(jobsResult.data.items ?? []);
      setFiles(filesResult.data.files ?? []);
      setDocuments(documentResult.data.documents ?? []);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const artifacts = useMemo(() => collectArtifacts(tasks, jobs, files, documents), [tasks, jobs, files, documents]);
  const selected = artifacts.find((artifact) => artifact.id === selectedId) ?? artifacts[0] ?? null;
  const isMarkdown = Boolean(selected && /\.(md|markdown)$/i.test(selected.path));
  const dirty = isMarkdown && draft !== savedDraft;

  useEffect(() => {
    const requestedPath = new URLSearchParams(location.search).get("file");
    if (!requestedPath || !artifacts.length) return;
    const normalized = requestedPath.replace(/^\/+/, "");
    const match = artifacts.find((artifact) =>
      artifact.path === requestedPath
      || artifact.path === normalized
      || requestedPath.endsWith(`/${artifact.path}`),
    );
    if (match) setSelectedId(match.id);
  }, [artifacts, location.search]);

  useEffect(() => {
    if (!selected?.path) {
      setPreview(null);
      setDraft("");
      setSavedDraft("");
      return;
    }
    setLoading(true);
    setMessage("");
    setError("");
    void getWorkspacePreview(selected.path)
      .then((response) => {
        setPreview(response.data);
        const text = response.data.text ?? "";
        setDraft(text);
        setSavedDraft(text);
      })
      .catch((err) => {
        setPreview(null);
        setError(apiErrorMessage(err));
      })
      .finally(() => setLoading(false));
  }, [selected?.path]);

  async function saveMarkdown() {
    if (!selected || !isMarkdown) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      await saveWorkspaceMarkdown(selected.path, draft);
      setSavedDraft(draft);
      setMessage("已保存");
      window.setTimeout(() => setMessage(""), 2200);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="markdown-center">
      <header className="markdown-center__header">
        <div>
          <span className="markdown-center__eyebrow"><FileCode2 size={17} />Markdown 工作台</span>
          <h1>处理结果</h1>
          <p>查看、编辑并实时编译 Markdown 文档。</p>
        </div>
        <div className="markdown-center__header-actions">
          <StatusPill tone="ok">{artifacts.length} 个结果</StatusPill>
          <button className="ghost-button" onClick={() => void load()}><RefreshCw size={17} />刷新</button>
        </div>
      </header>

      {error && <div className="markdown-center__error">{error}</div>}

      <section className="markdown-workspace">
        <aside className="markdown-files">
          <div className="markdown-files__title">
            <strong>结果文件</strong>
            <span>{artifacts.length}</span>
          </div>
          <div className="markdown-files__list">
            {artifacts.map((artifact) => (
              <button
                type="button"
                className={`markdown-file ${artifact.id === selected?.id ? "active" : ""}`}
                key={artifact.id}
                onClick={() => {
                  if (dirty && !window.confirm("当前修改尚未保存，确定切换文件吗？")) return;
                  setSelectedId(artifact.id);
                }}
              >
                <span className="markdown-file__icon"><FileText size={19} /></span>
                <span>
                  <strong>{artifact.label}</strong>
                  <small>{compactPath(artifact.path)}</small>
                </span>
              </button>
            ))}
            {!artifacts.length && <EmptyState>会议纪要、摘要和扫描结果会显示在这里。</EmptyState>}
          </div>
        </aside>

        <section className="markdown-editor">
          <div className="markdown-editor__toolbar">
            <div className="markdown-editor__identity">
              <strong>{selected?.label ?? "请选择结果"}</strong>
              <span>{selected?.path ?? "尚无可查看的文件"}</span>
            </div>
            {isMarkdown && (
              <div className="markdown-editor__modes" aria-label="编辑模式">
                <button className={mode === "preview" ? "active" : ""} onClick={() => setMode("preview")}><Eye size={16} />阅读</button>
                <button className={mode === "edit" ? "active" : ""} onClick={() => setMode("edit")}><Pencil size={16} />编辑</button>
                <button className={mode === "split" ? "active" : ""} onClick={() => setMode("split")}><Columns2 size={16} />分栏</button>
              </div>
            )}
            <div className="markdown-editor__actions">
              {message && <span className="markdown-save-state"><Check size={15} />{message}</span>}
              {selected?.documentId && (
                <Link className="primary-button" to={`/documents?document=${encodeURIComponent(selected.documentId)}`}>
                  <FileText size={16} />打开统一文档
                </Link>
              )}
              {selected && <a className="ghost-button" href={getFileDownloadUrl("workspace", selected.path)}><Download size={16} />下载</a>}
              {isMarkdown && (
                <button className="primary-button" onClick={() => void saveMarkdown()} disabled={!dirty || saving}>
                  <Save size={16} />{saving ? "保存中" : dirty ? "保存修改" : "已保存"}
                </button>
              )}
            </div>
          </div>

          <div className={`markdown-editor__body markdown-editor__body--${mode}`}>
            {loading && <div className="markdown-editor__empty">正在编译文档…</div>}
            {!loading && !selected && <div className="markdown-editor__empty">选择左侧文件开始查看。</div>}
            {!loading && selected && isMarkdown && (
              <>
                {(mode === "edit" || mode === "split") && (
                  <div className="markdown-source-pane">
                    <div className="markdown-pane-label">Markdown 源码</div>
                    <textarea
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      spellCheck={false}
                      aria-label="Markdown 编辑器"
                    />
                  </div>
                )}
                {(mode === "preview" || mode === "split") && (
                  <div className="markdown-preview-pane">
                    <div className="markdown-pane-label">实时预览</div>
                    <article className="markdown-rendered">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{draft}</ReactMarkdown>
                    </article>
                  </div>
                )}
              </>
            )}
            {!loading && selected && !isMarkdown && (
              <WorkspaceFileViewer
                source="workspace"
                filePath={selected.path}
                preview={preview}
                compact
                title="文件预览"
              />
            )}
          </div>
        </section>
      </section>
    </main>
  );
}

function collectArtifacts(tasks: TaskRecord[], jobs: MeetingJob[], files: SharedFile[], documents: CollaborativeDocument[]): ResultArtifact[] {
  const artifacts: ResultArtifact[] = [];
  const canonicalMeetingFiles = files.filter((file) => file.relative_path.startsWith("meetings/会议记录/"));
  const add = (pathValue: string, source: string, status = "available", documentId?: string, title?: string) => {
    const path = workspacePath(pathValue);
    if (!path || !looksLikeArtifact(path)) return;
    const type = inferType(path);
    artifacts.push({
      id: `${type}:${path}`,
      label: title || artifactLabel(type, path),
      path,
      type,
      source,
      status,
      documentId,
    });
  };
  documents.forEach((document) => {
    if (document.source_path) add(document.source_path, "会议文档", "available", document.id, document.title);
  });
  canonicalMeetingFiles.forEach((file) => add(file.relative_path, "会议", String(file.status || "available")));
  tasks
    .filter((task) => task.type !== "meeting")
    .forEach((task) => collectPaths(task.output).forEach((path) => add(path, task.type || "任务", String(task.status || "available"))));
  jobs.forEach((job) => {
    const finalStep = (job.steps ?? []).find((step) => step.name === "final_result");
    const finalPath = finalStep ? collectPaths(finalStep.output).find((path) => /\.(md|markdown)$/i.test(path)) : "";
    if (finalPath) {
      add(finalPath, job.title || "会议", String(finalStep?.status || job.status || "available"));
      return;
    }
    if (canonicalMeetingFiles.some((file) => file.relative_path.includes(String(job.title || "")))) return;
    const followup = (job.steps ?? []).find((step) => step.name === "followup");
    const preferred = followup ? collectPaths(followup.output).find((path) => /followup_minutes\.md$/i.test(path)) : "";
    if (preferred) add(preferred, job.title || "会议", String(followup?.status || job.status || "available"));
  });
  files
    .filter((file) => !file.relative_path.startsWith("meetings/"))
    .forEach((file) => add(file.relative_path, "工作区", String(file.status || "available")));
  const seen = new Set<string>();
  return artifacts.filter((artifact) => {
    if (seen.has(artifact.id)) return false;
    seen.add(artifact.id);
    return true;
  }).slice(0, 100);
}

function collectPaths(value: unknown): string[] {
  const paths: string[] = [];
  const visit = (item: unknown) => {
    if (!item) return;
    if (typeof item === "string") {
      paths.push(item);
      return;
    }
    if (Array.isArray(item)) item.forEach(visit);
    if (typeof item === "object") {
      Object.entries(item as Record<string, unknown>).forEach(([key, next]) => {
        if (/path|file|workspace/i.test(key)) visit(next);
      });
    }
  };
  visit(value);
  return paths;
}

function workspacePath(value: string) {
  const normalized = String(value || "").replace(/\\/g, "/");
  const marker = "/workspace/";
  const markerIndex = normalized.lastIndexOf(marker);
  if (markerIndex >= 0) return normalized.slice(markerIndex + marker.length);
  const runtimeMarker = "/lelamp_runtime/workspace/";
  const runtimeIndex = normalized.lastIndexOf(runtimeMarker);
  if (runtimeIndex >= 0) return normalized.slice(runtimeIndex + runtimeMarker.length);
  return normalized.replace(/^\.\//, "");
}

function looksLikeArtifact(path: string) {
  const lower = path.toLowerCase();
  if (
    lower.startsWith(".archive/")
    || lower.startsWith("validation/")
    || /(^|\/)(codex_|test_|demo_|smoke[_-]|.*[_-]smoke(?:\.|_|-))/.test(lower)
  ) {
    return false;
  }
  return /\.(md|markdown|txt|json|csv|pdf|docx|pptx|xlsx|png|jpg|jpeg)$/i.test(path)
    && /(minutes|meeting|summary|analysis|outline|table|ocr|scan|draft|followup|决策|纪要|摘要|表格)/i.test(path);
}

function inferType(path: string): ResultArtifact["type"] {
  const lower = path.toLowerCase();
  if (/minutes|meeting|纪要/.test(lower)) return "minutes";
  if (/table|csv|xlsx|表格/.test(lower)) return "table";
  if (/draft|email|followup|邮件/.test(lower)) return "draft";
  if (/scan|ocr|扫描/.test(lower)) return "scan";
  if (/summary|analysis|outline|摘要|提纲/.test(lower)) return "summary";
  return "other";
}

function artifactLabel(type: ResultArtifact["type"], path: string) {
  const name = path.split("/").filter(Boolean).pop() ?? path;
  if (type === "minutes") return `会议记录 · ${name}`;
  if (type === "table") return `数据表格 · ${name}`;
  if (type === "draft") return `邮件草稿 · ${name}`;
  if (type === "scan") return `扫描结果 · ${name}`;
  if (type === "summary") return `文档摘要 · ${name}`;
  return name;
}

function compactPath(value: string) {
  const parts = value.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.length <= 2 ? value : `…/${parts.slice(-2).join("/")}`;
}
