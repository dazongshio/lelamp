import { Extension, Node as TiptapNode } from "@tiptap/core";
import { useEditor } from "@tiptap/react";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet, type EditorView } from "@tiptap/pm/view";
import type { ReactNode } from "react";
import * as Y from "yjs";
import { randomId } from "../utils/randomId";

export type LibraryView = "recent" | "mine" | "shared" | "meeting" | "scan" | "favorite" | "trash";
export type InspectorView = "comments" | "history" | "info" | "ai";
export type SaveState = "saved" | "saving" | "dirty" | "offline" | "conflict";
export type DocumentFolder = { id: string; name: string; view: LibraryView };

export const remoteCursorPluginKey = new PluginKey<DecorationSet>("lelampRemoteCursors");
export const DOCUMENT_FOLDERS_KEY = "lelamp-document-folders-v1";
export const CalloutExtension = TiptapNode.create({
  name: "callout",
  group: "block",
  content: "block+",
  defining: true,
  parseHTML: () => [{ tag: 'aside[data-type="callout"]' }],
  renderHTML: ({ HTMLAttributes }) => ["aside", { ...HTMLAttributes, "data-type": "callout" }, 0],
});
export const RemoteCursorExtension = Extension.create({
  name: "lelampRemoteCursors",
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: remoteCursorPluginKey,
        state: {
          init: () => DecorationSet.empty,
          apply(transaction, previous) {
            const cursors = transaction.getMeta(remoteCursorPluginKey) as RemoteCursor[] | undefined;
            if (!cursors) return previous.map(transaction.mapping, transaction.doc);
            const maximum = transaction.doc.content.size;
            const decorations: Decoration[] = [];
            cursors.forEach((cursor) => {
              const from = Math.max(1, Math.min(maximum, cursor.from));
              const to = Math.max(from, Math.min(maximum, cursor.to));
              if (to > from) {
                decorations.push(Decoration.inline(from, to, {
                  class: "docs-remote-selection",
                  style: `--cursor-color:${safeCursorColor(cursor.color)}`,
                }));
              }
              decorations.push(Decoration.widget(to, () => createRemoteCursor(cursor), { side: 1 }));
            });
            return DecorationSet.create(transaction.doc, decorations);
          },
        },
        props: {
          decorations(state) {
            return remoteCursorPluginKey.getState(state);
          },
        },
      }),
    ];
  },
});

export interface RemoteCursor {
  id: string;
  name: string;
  color: string;
  from: number;
  to: number;
}


export function DocsNavButton({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: ReactNode; label: string }) {
  return <button className={active ? "active" : ""} onClick={onClick}>{icon}<span>{label}</span></button>;
}

export function loadDocumentFolders(): DocumentFolder[] {
  try {
    const value = JSON.parse(localStorage.getItem(DOCUMENT_FOLDERS_KEY) || "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is DocumentFolder => (
      item && typeof item.id === "string" && typeof item.name === "string"
      && ["recent", "mine", "shared", "meeting", "scan", "favorite", "trash"].includes(item.view)
    ));
  } catch {
    return [];
  }
}

export function updateSharedText(sharedText: Y.Text, nextValue: string, origin: object) {
  const currentValue = sharedText.toString();
  let prefix = 0;
  while (prefix < currentValue.length && prefix < nextValue.length && currentValue[prefix] === nextValue[prefix]) prefix += 1;
  let currentSuffix = currentValue.length;
  let nextSuffix = nextValue.length;
  while (currentSuffix > prefix && nextSuffix > prefix && currentValue[currentSuffix - 1] === nextValue[nextSuffix - 1]) {
    currentSuffix -= 1;
    nextSuffix -= 1;
  }
  sharedText.doc?.transact(() => {
    if (currentSuffix > prefix) sharedText.delete(prefix, currentSuffix - prefix);
    if (nextSuffix > prefix) sharedText.insert(prefix, nextValue.slice(prefix, nextSuffix));
  }, origin);
}

export function libraryTitle(view: LibraryView): string {
  return { recent: "最近使用", mine: "我的文档", shared: "与我共享", meeting: "会议记录", scan: "扫描结果", favorite: "收藏", trash: "回收站" }[view];
}

export function friendlySource(source: string): string {
  return { meeting: "会议", scan: "扫描", imported: "导入", ai_generated: "AI 生成", manual: "文档" }[source] ?? "文档";
}

export function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "未知时间" : date.toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function formatRelative(value: string): string {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "";
  const minutes = Math.max(0, Math.round((Date.now() - timestamp) / 60_000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  if (minutes < 10080) return `${Math.floor(minutes / 1440)} 天前`;
  return new Date(timestamp).toLocaleDateString("zh-CN");
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function roleLabel(role: string): string {
  return {
    owner: "所有者",
    editor: "可编辑",
    commenter: "可评论",
    viewer: "可查看",
  }[role] ?? "无权限";
}

export function safeCursorColor(value: string): string {
  return /^#[0-9a-f]{6}$/i.test(value) ? value : "#3370ff";
}

export function createRemoteCursor(cursor: RemoteCursor): HTMLElement {
  const marker = document.createElement("span");
  marker.className = "docs-remote-cursor";
  marker.style.setProperty("--cursor-color", safeCursorColor(cursor.color));
  marker.setAttribute("aria-label", `${cursor.name} 的光标`);
  const label = document.createElement("span");
  label.textContent = cursor.name;
  marker.append(label);
  return marker;
}

export function insertLocalImage(view: EditorView, file: File, reportError: (message: string) => void) {
  if (file.size > 5 * 1024 * 1024) {
    reportError("粘贴到正文的图片不能超过 5 MB；更大的图片请作为附件上传。");
    return;
  }
  if (!["image/png", "image/jpeg", "image/gif", "image/webp"].includes(file.type)) {
    reportError("仅支持 PNG、JPEG、GIF 或 WebP 图片。");
    return;
  }
  const reader = new FileReader();
  reader.onerror = () => reportError("图片读取失败。");
  reader.onload = () => {
    const imageType = view.state.schema.nodes.image;
    if (!imageType || typeof reader.result !== "string") return;
    const node = imageType.create({ src: reader.result, alt: file.name || "文档图片" });
    view.dispatch(view.state.tr.replaceSelectionWith(node));
    view.focus();
  };
  reader.readAsDataURL(file);
}

export function safeDownloadName(value: string): string {
  return value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").trim() || "文档";
}

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character] ?? character);
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function getCollaborationClientId(): string {
  const key = "lelamp_document_client_id";
  const existing = sessionStorage.getItem(key);
  if (existing && /^[a-zA-Z0-9_-]{8,64}$/.test(existing)) return existing;
  const created = randomId().replace(/-/g, "").slice(0, 12);
  sessionStorage.setItem(key, created);
  return created;
}

export function getDocumentHeadings(editor: NonNullable<ReturnType<typeof useEditor>>) {
  const headings: Array<{ text: string; level: number; position: number }> = [];
  editor.state.doc.descendants((node, position) => {
    if (node.type.name === "heading") {
      headings.push({
        text: node.textContent || "未命名标题",
        level: Number(node.attrs.level || 1),
        position,
      });
    }
  });
  return headings;
}
