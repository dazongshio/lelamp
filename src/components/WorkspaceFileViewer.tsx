import { ExternalLink, FileArchive, FileImage, FileText } from "lucide-react";
import type { ReactNode } from "react";
import { getFileDownloadUrl, getFileViewUrl, type FileSource } from "../api/shared";
import type { SharedPreviewResponse } from "../api/types";
import { StatusBadge } from "./StatusBadge";

interface WorkspaceFileViewerProps {
  source: FileSource;
  filePath: string;
  preview: SharedPreviewResponse | null;
  busy?: boolean;
  error?: string;
  title?: string;
  emptyText?: string;
  className?: string;
  compact?: boolean;
}

const imageSuffixes = new Set(["jpg", "jpeg", "png", "webp", "bmp", "gif"]);
const markdownSuffixes = new Set(["md", "markdown"]);
const plainTextSuffixes = new Set(["txt", "csv", "tsv", "json", "jsonl", "yaml", "yml", "log", "html", "xml"]);
const officeSuffixes = new Set(["doc", "docx", "ppt", "pptx", "xls", "xlsx"]);

export function WorkspaceFileViewer({
  source,
  filePath,
  preview,
  busy = false,
  error = "",
  title,
  emptyText = "请选择文件查看内容。",
  className = "",
  compact = false,
}: WorkspaceFileViewerProps) {
  const displayName = preview?.name || baseName(filePath) || "等待预览";
  const extension = fileExtension(displayName || filePath);
  const viewUrl = filePath ? getFileViewUrl(source, filePath) : "";
  const downloadUrl = filePath ? getFileDownloadUrl(source, filePath) : "";
  const kind = viewerKind(extension);
  const text = preview?.text ?? "";
  const status = busy ? "running" : error ? "failed" : preview?.status ?? (filePath ? "pending" : "empty");

  return (
    <div className={`workspace-file-viewer ${compact ? "workspace-file-viewer--compact" : ""} ${className}`.trim()}>
      <div className="workspace-file-viewer__toolbar">
        <div className="workspace-file-viewer__identity">
          {kind === "image" ? <FileImage size={18} /> : kind === "office" ? <FileArchive size={18} /> : <FileText size={18} />}
          <div>
            <span>{title || viewerTitle(kind)}</span>
            <strong>{displayName}</strong>
          </div>
        </div>
        <div className="workspace-file-viewer__actions">
          {!compact && preview?.document_text_backend && <span className="small muted">解析：{preview.document_text_backend}</span>}
          {!compact && preview?.truncated && <span className="small muted">已截断</span>}
          <StatusBadge status={status} />
          {viewUrl && (
            <a className="ghost-button workspace-file-viewer__link" href={viewUrl} target="_blank" rel="noreferrer">
              <ExternalLink size={14} />
              打开原文件
            </a>
          )}
          {downloadUrl && (
            <a className="ghost-button workspace-file-viewer__link" href={downloadUrl}>
              下载
            </a>
          )}
        </div>
      </div>
      <div className="workspace-file-viewer__body">
        {busy && <p className="small muted">正在读取文件...</p>}
        {!busy && error && <p className="danger-text">{error}</p>}
        {!busy && !error && !filePath && <p className="small muted">{emptyText}</p>}
        {!busy && !error && filePath && kind === "pdf" && (
          <iframe className="workspace-file-viewer__frame" src={viewUrl} title={displayName} />
        )}
        {!busy && !error && filePath && kind === "image" && (
          <img className="workspace-file-viewer__image" src={viewUrl} alt={displayName} />
        )}
        {!busy && !error && filePath && kind === "markdown" && (
          <div className="workspace-file-viewer__markdown">{renderMarkdown(text || previewFallbackText(preview, kind))}</div>
        )}
        {!busy && !error && filePath && kind === "office" && (
          <div className="workspace-file-viewer__office">
            <p className="small muted">浏览器不能直接内嵌编辑 Office 文件；这里显示后端抽取的正文，原文件可打开或下载。</p>
            <pre className="workspace-file-viewer__text">{text || previewFallbackText(preview, kind)}</pre>
          </div>
        )}
        {!busy && !error && filePath && kind === "text" && (
          <pre className="workspace-file-viewer__text">{text || previewFallbackText(preview, kind)}</pre>
        )}
        {!busy && !error && filePath && kind === "binary" && (
          <div className="workspace-file-viewer__empty">
            <FileArchive size={24} />
            <p>{previewFallbackText(preview, kind)}</p>
          </div>
        )}
      </div>
    </div>
  );
}

function viewerKind(extension: string) {
  if (extension === "pdf") return "pdf";
  if (imageSuffixes.has(extension)) return "image";
  if (markdownSuffixes.has(extension)) return "markdown";
  if (officeSuffixes.has(extension)) return "office";
  if (plainTextSuffixes.has(extension)) return "text";
  return "binary";
}

function viewerTitle(kind: string) {
  if (kind === "pdf") return "PDF 查看";
  if (kind === "image") return "图片查看";
  if (kind === "markdown") return "Markdown 查看";
  if (kind === "office") return "Office 查看";
  if (kind === "text") return "文本查看";
  return "文件查看";
}

function previewFallbackText(preview: SharedPreviewResponse | null, kind: string) {
  if (!preview) return "等待读取文件。";
  if (preview.text) return preview.text;
  if (preview.status && preview.status !== "ok" && preview.status !== "binary") return `预览状态：${preview.status}`;
  if (kind === "binary") return "这个文件暂不支持页面内预览，可打开原文件或下载。";
  return "没有可显示的正文。";
}

function fileExtension(value: string) {
  const name = baseName(value).toLowerCase();
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1) : "";
}

function baseName(value: string) {
  return String(value || "").replace(/\\/g, "/").split("/").filter(Boolean).pop() ?? "";
}

function renderMarkdown(text: string) {
  const lines = text.split(/\r?\n/);
  const nodes: ReactNode[] = [];
  let listItems: string[] = [];

  const flushList = () => {
    if (!listItems.length) return;
    const current = listItems;
    listItems = [];
    nodes.push(
      <ul key={`list-${nodes.length}`}>
        {current.map((item, index) => <li key={`${item}-${index}`}>{inlineMarkdown(item)}</li>)}
      </ul>,
    );
  };

  lines.forEach((line, index) => {
    const trimmed = line.trim();
    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
    const bullet = /^[-*]\s+(.+)$/.exec(trimmed);
    if (bullet) {
      listItems.push(bullet[1]);
      return;
    }
    flushList();
    if (!trimmed) {
      nodes.push(<br key={`br-${index}`} />);
    } else if (heading) {
      const level = heading[1].length;
      nodes.push(renderHeading(Math.min(level + 1, 5), `h-${index}`, inlineMarkdown(heading[2])));
    } else {
      nodes.push(<p key={`p-${index}`}>{inlineMarkdown(trimmed)}</p>);
    }
  });
  flushList();
  return nodes.length ? nodes : <p className="small muted">没有可显示的 Markdown 内容。</p>;
}

function renderHeading(level: number, key: string, children: ReactNode) {
  if (level <= 2) return <h2 key={key}>{children}</h2>;
  if (level === 3) return <h3 key={key}>{children}</h3>;
  if (level === 4) return <h4 key={key}>{children}</h4>;
  return <h5 key={key}>{children}</h5>;
}

function inlineMarkdown(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>;
    }
    return <span key={`${part}-${index}`}>{part}</span>;
  });
}
