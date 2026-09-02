import {
  ArchiveRestore,
  ChevronDown,
  ChevronRight,
  Clock3,
  Code2,
  Download,
  FilePlus2,
  FileText,
  Folder,
  FolderPlus,
  Heading1,
  Heading2,
  ImagePlus,
  Link2,
  List,
  ListChecks,
  MessageSquare,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Quote,
  Search,
  Sparkles,
  Star,
  Table2,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { useEditor, EditorContent } from "@tiptap/react";
import { Extension, Node as TiptapNode } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import Table from "@tiptap/extension-table";
import TableRow from "@tiptap/extension-table-row";
import TableHeader from "@tiptap/extension-table-header";
import TableCell from "@tiptap/extension-table-cell";
import Image from "@tiptap/extension-image";
import Link from "@tiptap/extension-link";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet, type EditorView } from "@tiptap/pm/view";
import { HocuspocusProvider } from "@hocuspocus/provider";
import * as Y from "yjs";
import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiClientError, apiErrorMessage } from "../api/client";
import {
  addDocumentComment,
  applyDocumentAiSuggestion,
  createCollaborativeDocument,
  createDocumentShareLink,
  downloadDocumentAttachment,
  exportDocumentMarkdown,
  getCollaborativeDocument,
  getDocumentCollaborationSession,
  getDocumentRevision,
  generateDocumentAiSuggestion,
  listDocumentAttachments,
  listDocumentComments,
  listDocumentHistory,
  migrateWorkspaceMarkdown,
  purgeCollaborativeDocument,
  restoreCollaborativeDocument,
  restoreDocumentRevision,
  setDocumentPermissions,
  setCollaborativeDocumentFavorite,
  trashCollaborativeDocument,
  updateCollaborativeDocument,
  updateDocumentComment,
  uploadDocumentAttachment,
  type CollaborativeDocument,
  type DocumentAttachment,
  type DocumentComment,
  type DocumentPermission,
  type DocumentRevision,
} from "../api/documents";
import { htmlToMarkdown, markdownToHtml } from "../utils/documentMarkdown";
import { randomId } from "../utils/randomId";
import { DocumentToolbar } from "./DocumentToolbar";
import { DocumentEditor } from "./DocumentEditor";
import { DocumentList } from "./DocumentList";
import { useDocumentWorkspace } from "./useDocumentWorkspace";
import { useDocumentCollaboration } from "./useDocumentCollaboration";
import {
  CalloutExtension,
  DOCUMENT_FOLDERS_KEY,
  DocsNavButton,
  RemoteCursorExtension,
  createRemoteCursor,
  downloadBlob,
  escapeHtml,
  formatBytes,
  formatRelative,
  formatTime,
  friendlySource,
  getDocumentHeadings,
  insertLocalImage,
  libraryTitle,
  loadDocumentFolders,
  roleLabel,
  remoteCursorPluginKey,
  safeCursorColor,
  safeDownloadName,
  updateSharedText,
  type DocumentFolder,
  type InspectorView,
  type LibraryView,
  type RemoteCursor,
  type SaveState,
} from "./documentPageSupport";
import "./pages.css";

const DocumentInspector = lazy(async () => ({ default: (await import("./DocumentInspector")).DocumentInspector }));

export function DocumentsPage() {
  const [selectedId, setSelectedId] = useState(() => new URLSearchParams(window.location.search).get("document") ?? "");
  const [selected, setSelected] = useState<CollaborativeDocument | null>(null);
  const [libraryView, setLibraryView] = useState<LibraryView>("recent");
  const [activeFolder, setActiveFolder] = useState("");
  const [folders, setFolders] = useState<DocumentFolder[]>(() => loadDocumentFolders());
  const [expandedViews, setExpandedViews] = useState<LibraryView[]>(["mine", "meeting", "scan"]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(() => Number(localStorage.getItem("lelamp-docs-sidebar-width")) || 224);
  const [inspector, setInspector] = useState<InspectorView>("comments");
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [query, setQuery] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [notice, setNotice] = useState("");
  const [comments, setComments] = useState<DocumentComment[]>([]);
  const [revisions, setRevisions] = useState<DocumentRevision[]>([]);
  const [attachments, setAttachments] = useState<DocumentAttachment[]>([]);
  const [commentDraft, setCommentDraft] = useState("");
  const [collaboratorId, setCollaboratorId] = useState("");
  const [collaboratorRole, setCollaboratorRole] = useState<"editor" | "commenter" | "viewer">("editor");
  const [aiSuggestion, setAiSuggestion] = useState<{ operation: string; label: string; text: string; baseVersion: number } | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [widePage, setWidePage] = useState(false);
  const [revisionPreview, setRevisionPreview] = useState<DocumentRevision | null>(null);
  const uploadController = useRef<AbortController | null>(null);
  const [slashOpen, setSlashOpen] = useState(false);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [newMenuOpen, setNewMenuOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importPath, setImportPath] = useState("");
  const [busy, setBusy] = useState(false);
  const saveTimer = useRef<number | null>(null);
  const currentVersion = useRef(0);
  const loadingDocument = useRef(false);
  const {
    collaborationState, setCollaborationState, onlineMembers, setOnlineMembers,
    collaborationText, collaborationMetadata, collaborationProvider,
    collaborationOrigin, collaborationClientId,
  } = useDocumentCollaboration();
  const clearSelection = useCallback(() => setSelectedId(""), []);
  const { documents, setDocuments, loading, error, setError, reloadDocuments: loadList } = useDocumentWorkspace({
    libraryView, activeFolder, query, selectedId, clearSelection,
  });

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2, 3] } }),
      Placeholder.configure({ placeholder: "输入内容，或输入 / 插入标题、列表、引用和代码块…" }),
      TaskList,
      TaskItem.configure({ nested: true }),
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
      Image.configure({ allowBase64: true }),
      Link.configure({ openOnClick: false, autolink: true, linkOnPaste: true }),
      CalloutExtension,
      RemoteCursorExtension,
    ],
    content: "<p></p>",
    editable: false,
    editorProps: {
      attributes: { class: "collab-prose", spellcheck: "true", "aria-label": "文档正文编辑器" },
      handlePaste(view, event) {
        const image = [...(event.clipboardData?.files ?? [])].find((file) => file.type.startsWith("image/"));
        if (!image) return false;
        insertLocalImage(view, image, setError);
        return true;
      },
      handleDrop(view, event) {
        const image = [...(event.dataTransfer?.files ?? [])].find((file) => file.type.startsWith("image/"));
        if (!image) return false;
        event.preventDefault();
        insertLocalImage(view, image, setError);
        return true;
      },
    },
    onUpdate: ({ editor: activeEditor }) => {
      if (loadingDocument.current || !selected?.can_edit) return;
      const markdown = htmlToMarkdown(activeEditor.getHTML());
      if (collaborationText.current && collaborationText.current.toString() !== markdown) {
        updateSharedText(collaborationText.current, markdown, collaborationOrigin.current);
      }
      const text = activeEditor.state.doc.textBetween(0, activeEditor.state.doc.content.size, "\n");
      setSlashOpen(text.endsWith("/"));
      setMentionOpen(text.endsWith("@"));
      setSaveState("dirty");
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => void saveDocument(activeEditor.getHTML()), 900);
    },
    onSelectionUpdate: ({ editor: activeEditor }) => {
      collaborationProvider.current?.setAwarenessField("selection", {
        from: activeEditor.state.selection.from,
        to: activeEditor.state.selection.to,
      });
    },
  });

  function navigateLibrary(view: LibraryView, folderId = "") {
    setLibraryView(view);
    setActiveFolder(folderId);
    setSelectedId("");
    setQuery("");
  }

  function createSubfolder(view: LibraryView) {
    const name = window.prompt(`在“${libraryTitle(view)}”下新建文件夹`, "新建文件夹")?.trim();
    if (!name) return;
    const next = [...folders, { id: `folder-${randomId()}`, name: name.slice(0, 40), view }];
    setFolders(next);
    localStorage.setItem(DOCUMENT_FOLDERS_KEY, JSON.stringify(next));
    setExpandedViews((items) => items.includes(view) ? items : [...items, view]);
    setNotice(`文件夹“${name}”已创建`);
  }

  function beginSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const move = (pointerEvent: PointerEvent) => {
      const width = Math.max(180, Math.min(420, pointerEvent.clientX));
      setSidebarWidth(width);
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      setSidebarWidth((width) => {
        localStorage.setItem("lelamp-docs-sidebar-width", String(width));
        return width;
      });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  useEffect(() => {
    const timer = window.setTimeout(() => void loadList(), 250);
    return () => window.clearTimeout(timer);
  }, [loadList]);

  useEffect(() => {
    if (!selectedId || !editor) {
      setSelected(null);
      editor?.commands.setContent("<p></p>");
      editor?.setEditable(false);
      return;
    }
    loadingDocument.current = true;
    setError("");
    void Promise.all([
      getCollaborativeDocument(selectedId),
      listDocumentComments(selectedId),
      listDocumentHistory(selectedId),
      listDocumentAttachments(selectedId),
    ])
      .then(([documentResponse, commentResponse, historyResponse, attachmentResponse]) => {
        const document = documentResponse.data.document;
        setSelected(document);
        currentVersion.current = document.content_version;
        editor.commands.setContent(markdownToHtml(document.content ?? ""));
        editor.setEditable(document.can_edit);
        setComments(commentResponse.data.comments);
        setRevisions(historyResponse.data.revisions);
        setAttachments(attachmentResponse.data.attachments);
        setSaveState("saved");
      })
      .catch((err) => setError(apiErrorMessage(err)))
      .finally(() => {
        loadingDocument.current = false;
      });
  }, [editor, selectedId]);

  useEffect(() => {
    collaborationProvider.current?.destroy();
    collaborationProvider.current = null;
    collaborationText.current = null;
    collaborationMetadata.current = null;
    setOnlineMembers([]);
    if (!selectedId || !editor || !selected) return;
    let disposed = false;
    setCollaborationState("connecting");
    void getDocumentCollaborationSession(selectedId, collaborationClientId.current)
      .then((response) => {
        if (disposed) return;
        const session = response.data;
        const ydoc = new Y.Doc();
        const provider = new HocuspocusProvider({
          url: session.url,
          name: selectedId,
          document: ydoc,
          token: session.token,
        });
        const sharedText = ydoc.getText("markdown");
        const sharedMetadata = ydoc.getMap("metadata");
        collaborationProvider.current = provider;
        collaborationText.current = sharedText;
        collaborationMetadata.current = sharedMetadata;
        provider.setAwarenessField("user", session.user);
        const updateMembers = () => {
          const awarenessStates = Array.from(provider.awareness?.getStates().entries() ?? []);
          const members = awarenessStates
            .map(([, state]) => state.user as { id: string; name: string; color: string } | undefined)
            .filter((user): user is { id: string; name: string; color: string } => Boolean(user?.id));
          setOnlineMembers(Array.from(new Map(members.map((member) => [member.id, member])).values()));
          const cursors = awarenessStates.flatMap(([clientId, state]) => {
            const user = state.user as { id?: string; name?: string; color?: string } | undefined;
            const selection = state.selection as { from?: number; to?: number } | undefined;
            if (clientId === ydoc.clientID || !user?.id || !selection) return [];
            return [{
              id: user.id,
              name: user.name || "协作者",
              color: user.color || "#3370ff",
              from: Number(selection.from || 1),
              to: Number(selection.to || selection.from || 1),
            }];
          });
          editor.view.dispatch(editor.state.tr.setMeta(remoteCursorPluginKey, cursors));
        };
        provider.on("status", ({ status }: { status: string }) => setCollaborationState(status === "connected" ? "online" : "connecting"));
        provider.on("disconnect", () => setCollaborationState("offline"));
        provider.on("awarenessUpdate", updateMembers);
        provider.on("synced", () => {
          const collaborationVersion = Number(sharedMetadata.get("content_version") || 0);
          if (!sharedText.length || selected.content_version > collaborationVersion) {
            ydoc.transact(() => {
              updateSharedText(sharedText, selected.content ?? "", collaborationOrigin.current);
              sharedMetadata.set("content_version", selected.content_version);
            }, collaborationOrigin.current);
          } else if (sharedText.length && sharedText.toString() !== htmlToMarkdown(editor.getHTML())) {
            loadingDocument.current = true;
            editor.commands.setContent(markdownToHtml(sharedText.toString()));
            loadingDocument.current = false;
          }
          setCollaborationState("online");
          updateMembers();
        });
        sharedText.observe((event) => {
          if (event.transaction.origin === collaborationOrigin.current) return;
          const markdown = sharedText.toString();
          if (markdown === htmlToMarkdown(editor.getHTML())) return;
          loadingDocument.current = true;
          editor.commands.setContent(markdownToHtml(markdown));
          loadingDocument.current = false;
          setSaveState("dirty");
          if (saveTimer.current) window.clearTimeout(saveTimer.current);
          saveTimer.current = window.setTimeout(() => void saveDocument(editor.getHTML()), 900);
        });
      })
      .catch(() => setCollaborationState("offline"));
    return () => {
      disposed = true;
      collaborationProvider.current?.destroy();
      collaborationProvider.current = null;
      collaborationText.current = null;
      collaborationMetadata.current = null;
    };
  }, [editor, selected?.id, selectedId]);

  useEffect(() => {
    if (!selectedId || saveState === "dirty" || saveState === "saving") return;
    const timer = window.setInterval(() => {
      void getCollaborativeDocument(selectedId).then((response) => {
        const remote = response.data.document;
        if (remote.content_version > currentVersion.current && editor) {
          loadingDocument.current = true;
          editor.commands.setContent(markdownToHtml(remote.content ?? ""));
          currentVersion.current = remote.content_version;
          setSelected(remote);
          setNotice("已同步其他位置的修改");
          window.setTimeout(() => setNotice(""), 1800);
          loadingDocument.current = false;
        }
      }).catch(() => setSaveState(navigator.onLine ? "offline" : "offline"));
    }, 3000);
    return () => window.clearInterval(timer);
  }, [editor, saveState, selectedId]);

  async function saveDocument(html = editor?.getHTML() ?? "") {
    if (!selectedId || !selected?.can_edit || loadingDocument.current) return;
    setSaveState("saving");
    try {
      const content = htmlToMarkdown(html);
      const response = await updateCollaborativeDocument(selectedId, {
        content,
        base_version: currentVersion.current,
        summary: "自动保存",
      });
      currentVersion.current = response.data.document.content_version;
      collaborationMetadata.current?.set("content_version", response.data.document.content_version);
      setSelected(response.data.document);
      setSaveState("saved");
    } catch (err) {
      if (err instanceof ApiClientError && err.code === "document_version_conflict") {
        try {
          const latest = (await getCollaborativeDocument(selectedId)).data.document;
          const mergedContent = collaborationText.current?.toString() || htmlToMarkdown(html);
          const retried = await updateCollaborativeDocument(selectedId, {
            content: mergedContent,
            base_version: latest.content_version,
            summary: "合并协作修改",
          });
          currentVersion.current = retried.data.document.content_version;
          collaborationMetadata.current?.set("content_version", retried.data.document.content_version);
          setSelected(retried.data.document);
          setSaveState("saved");
          setNotice("已合并其他协作者的修改");
          return;
        } catch (retryError) {
          setError(apiErrorMessage(retryError));
          setSaveState("conflict");
          return;
        }
      }
      const message = apiErrorMessage(err);
      setError(message);
      setSaveState(message.includes("其他位置") || message.includes("同步") ? "conflict" : "offline");
    }
  }

  async function renameDocument(title: string) {
    if (!selected || title.trim() === selected.title) return;
    try {
      const response = await updateCollaborativeDocument(selected.id, { title: title.trim() || "无标题文档", summary: "修改标题" });
      setSelected(response.data.document);
      setDocuments((items) => items.map((item) => item.id === selected.id ? response.data.document : item));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function createDocument(template: "" | "meeting" | "project" | "weekly" = "") {
    setBusy(true);
    setError("");
    try {
      const labels = { "": "无标题文档", meeting: "会议记录", project: "项目计划", weekly: "周报" };
      const response = await createCollaborativeDocument({
        title: labels[template],
        template,
        idempotency_key: randomId(),
        space_id: activeFolder || "personal",
      });
      setNewMenuOpen(false);
      if (!activeFolder) setLibraryView("recent");
      await loadList();
      setSelectedId(response.data.document.id);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function importMarkdown() {
    if (!importPath.trim()) return;
    setBusy(true);
    setError("");
    try {
      const response = await createCollaborativeDocument({
        title: "",
        source_path: importPath.trim(),
        source_type: "imported",
        idempotency_key: randomId(),
      });
      setImportOpen(false);
      setImportPath("");
      await loadList();
      setSelectedId(response.data.document.id);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function migrateExistingMarkdown() {
    if (!window.confirm("扫描工作区并复制导入现有 Markdown？原文件不会修改或删除。")) return;
    setBusy(true);
    setError("");
    try {
      const response = await migrateWorkspaceMarkdown();
      setNotice(`已迁移 ${response.data.imported_count} 个文档${response.data.error_count ? `，${response.data.error_count} 个失败` : ""}`);
      setImportOpen(false);
      await loadList();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function moveToTrash() {
    if (!selected || !window.confirm(`将“${selected.title}”移入回收站？之后可以恢复。`)) return;
    await trashCollaborativeDocument(selected.id);
    setSelectedId("");
    await loadList();
  }

  async function restoreDocument() {
    if (!selected) return;
    await restoreDocumentById(selected);
  }

  async function purgeDocument() {
    if (!selected) return;
    await purgeDocumentById(selected);
  }

  async function restoreDocumentById(document: CollaborativeDocument) {
    try {
      await restoreCollaborativeDocument(document.id);
      setSelectedId("");
      await loadList();
      setNotice(`“${document.title}”已恢复`);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function purgeDocumentById(document: CollaborativeDocument) {
    if (!window.confirm(`永久删除“${document.title}”？此操作无法恢复。`)) return;
    try {
      await purgeCollaborativeDocument(document.id);
      setSelectedId("");
      await loadList();
      setNotice(`“${document.title}”已永久删除`);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function toggleFavorite() {
    if (!selected) return;
    try {
      const response = await setCollaborativeDocumentFavorite(selected.id, !selected.favorite);
      setSelected(response.data.document);
      setDocuments((items) => items.map((item) => item.id === selected.id ? response.data.document : item));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function duplicateDocument() {
    if (!selected || !editor) return;
    setBusy(true);
    try {
      const response = await createCollaborativeDocument({
        title: `${selected.title} 副本`,
        content: htmlToMarkdown(editor.getHTML()),
        source_type: "manual",
        space_id: selected.space_id,
        idempotency_key: randomId(),
      });
      await loadList();
      setSelectedId(response.data.document.id);
      setNotice("已创建文档副本");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function addComment() {
    if (!selected || !commentDraft.trim()) return;
    setBusy(true);
    try {
      const anchor = editor?.state.doc.textBetween(editor.state.selection.from, editor.state.selection.to, " ").trim() ?? "";
      await addDocumentComment(selected.id, { body: commentDraft, anchor_text: anchor });
      setCommentDraft("");
      setComments((await listDocumentComments(selected.id)).data.comments);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function toggleComment(comment: DocumentComment) {
    if (!selected) return;
    await updateDocumentComment(selected.id, comment.id, { resolved: !comment.resolved });
    setComments((await listDocumentComments(selected.id)).data.comments);
  }

  async function replyToComment(comment: DocumentComment) {
    if (!selected) return;
    const body = window.prompt("输入回复内容");
    if (!body?.trim()) return;
    try {
      await addDocumentComment(selected.id, { body: body.trim(), parent_id: comment.id });
      setComments((await listDocumentComments(selected.id)).data.comments);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function saveCollaborator() {
    if (!selected || !collaboratorId.trim()) return;
    const existing = selected.permissions ?? [];
    const next: DocumentPermission[] = [
      ...existing.filter((item) => item.principal_id !== collaboratorId.trim()),
      {
        principal_type: "user",
        principal_id: collaboratorId.trim(),
        display_name: collaboratorId.trim(),
        role: collaboratorRole,
      },
    ];
    try {
      const response = await setDocumentPermissions(selected.id, next);
      setSelected({ ...selected, permissions: response.data.permissions });
      setCollaboratorId("");
      setNotice("协作权限已更新");
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function removeCollaborator(principalId: string) {
    if (!selected) return;
    const next = (selected.permissions ?? []).filter((item) => item.principal_id !== principalId);
    try {
      const response = await setDocumentPermissions(selected.id, next);
      setSelected({ ...selected, permissions: response.data.permissions });
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function copyShareLink(principalId: string) {
    if (!selected) return;
    try {
      const response = await createDocumentShareLink(selected.id, principalId);
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(response.data.share_url);
        setNotice("文档级分享链接已复制，7 天内有效");
      } else {
        window.prompt("复制文档级分享链接（7 天内有效）", response.data.share_url);
      }
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function restoreRevision(revision: DocumentRevision) {
    if (!selected || !window.confirm(`恢复“${formatTime(revision.created_at)}”的版本？当前内容仍会保留在历史中。`)) return;
    const response = await restoreDocumentRevision(selected.id, revision.id);
    const document = response.data.document;
    setSelected(document);
    currentVersion.current = document.content_version;
    loadingDocument.current = true;
    editor?.commands.setContent(markdownToHtml(document.content ?? ""));
    loadingDocument.current = false;
    setRevisions((await listDocumentHistory(selected.id)).data.revisions);
  }

  async function previewRevision(revision: DocumentRevision) {
    if (!selected) return;
    try {
      setRevisionPreview((await getDocumentRevision(selected.id, revision.id)).data.revision);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function uploadAttachment(file: File | undefined) {
    if (!selected || !file) return;
    if (file.size > 20 * 1024 * 1024) {
      setError("附件不能超过 20 MB。");
      return;
    }
    setBusy(true);
    setUploadProgress(0);
    uploadController.current = new AbortController();
    try {
      await uploadDocumentAttachment(selected.id, file, {
        signal: uploadController.current.signal,
        onProgress: setUploadProgress,
      });
      setAttachments((await listDocumentAttachments(selected.id)).data.attachments);
      setNotice("附件已上传");
    } catch (err) {
      setError(err instanceof DOMException && err.name === "AbortError" ? "附件上传已取消。" : apiErrorMessage(err));
    } finally {
      setBusy(false);
      setUploadProgress(null);
      uploadController.current = null;
    }
  }

  async function downloadAttachment(attachment: DocumentAttachment) {
    if (!selected) return;
    try {
      const downloaded = await downloadDocumentAttachment(selected.id, attachment.id);
      const bytes = new Uint8Array(downloaded.content.byteLength);
      bytes.set(downloaded.content);
      const url = URL.createObjectURL(new Blob([bytes.buffer], { type: attachment.mime_type }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = attachment.filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function downloadMarkdown() {
    if (!selected) return;
    const exported = await exportDocumentMarkdown(selected.id);
    const url = URL.createObjectURL(new Blob([exported.content], { type: "text/markdown;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = exported.filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function downloadHtml() {
    if (!selected) return;
    const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>${escapeHtml(selected.title)}</title><style>body{max-width:820px;margin:48px auto;padding:0 24px;font:16px/1.75 system-ui;color:#1f2329}img{max-width:100%}pre{padding:16px;background:#f5f6f7;border-radius:8px;overflow:auto}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:8px}</style></head><body>${editor?.getHTML() ?? ""}</body></html>`;
    downloadBlob(new Blob([html], { type: "text/html;charset=utf-8" }), `${safeDownloadName(selected.title)}.html`);
  }

  function printPdf() {
    if (!selected) return;
    const popup = window.open("", "_blank", "noopener,noreferrer");
    if (!popup) {
      setError("浏览器阻止了打印窗口，请允许弹窗后重试。");
      return;
    }
    popup.document.write(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>${escapeHtml(selected.title)}</title><style>body{max-width:820px;margin:36px auto;font:15px/1.7 system-ui;color:#111}img{max-width:100%}@page{margin:18mm}pre{white-space:pre-wrap}</style></head><body><h1>${escapeHtml(selected.title)}</h1>${editor?.getHTML() ?? ""}<script>window.onload=()=>setTimeout(()=>window.print(),150)<\/script></body></html>`);
    popup.document.close();
  }

  async function downloadDocx() {
    if (!selected) return;
    setBusy(true);
    try {
      const { Document, HeadingLevel, Packer, Paragraph, TextRun } = await import("docx");
      const markdown = htmlToMarkdown(editor?.getHTML() ?? "");
      const paragraphs = markdown.split(/\r?\n/).map((line) => {
        const heading = /^(#{1,3})\s+(.+)$/.exec(line);
        if (heading) {
          const levels = [HeadingLevel.HEADING_1, HeadingLevel.HEADING_2, HeadingLevel.HEADING_3];
          return new Paragraph({ text: heading[2], heading: levels[heading[1].length - 1] });
        }
        const bullet = /^[-*]\s+(.+)$/.exec(line);
        if (bullet) return new Paragraph({ children: [new TextRun(bullet[1])], bullet: { level: 0 } });
        return new Paragraph({ children: [new TextRun(line)] });
      });
      const blob = await Packer.toBlob(new Document({ sections: [{ children: paragraphs }] }));
      downloadBlob(blob, `${safeDownloadName(selected.title)}.docx`);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function requestAiSuggestion(operation: string, label: string) {
    if (!selected || !editor) return;
    const selectedText = editor.state.doc.textBetween(editor.state.selection.from, editor.state.selection.to, "\n").trim();
    setAiBusy(true);
    setError("");
    try {
      const response = await generateDocumentAiSuggestion(selected.id, {
        operation,
        selected_text: selectedText || undefined,
      });
      setAiSuggestion({
        operation,
        label,
        text: response.data.suggestion,
        baseVersion: response.data.base_version,
      });
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setAiBusy(false);
    }
  }

  async function acceptAiSuggestion(mode: "replace" | "insert") {
    if (!selected || !editor || !aiSuggestion) return;
    loadingDocument.current = true;
    if (mode === "replace" && !editor.state.selection.empty) {
      editor.chain().focus().deleteSelection().insertContent(markdownToHtml(aiSuggestion.text)).run();
    } else {
      editor.chain().focus().insertContent(markdownToHtml(`\n\n${aiSuggestion.text}\n`)).run();
    }
    loadingDocument.current = false;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    setAiBusy(true);
    try {
      const response = await applyDocumentAiSuggestion(selected.id, {
        operation: aiSuggestion.operation,
        content: htmlToMarkdown(editor.getHTML()),
        base_version: aiSuggestion.baseVersion,
        mode,
      });
      currentVersion.current = response.data.document.content_version;
      setSelected(response.data.document);
      setSaveState("saved");
      setAiSuggestion(null);
      setRevisions((await listDocumentHistory(selected.id)).data.revisions);
      setNotice("AI 建议已写入，可从历史版本恢复");
    } catch (err) {
      setError(apiErrorMessage(err));
      setSaveState("conflict");
    } finally {
      setAiBusy(false);
    }
  }

  function insertSlashBlock(kind: "h1" | "h2" | "bullet" | "task" | "quote" | "code" | "table" | "image" | "link" | "divider" | "callout") {
    if (!editor) return;
    const from = editor.state.selection.from;
    if (editor.state.doc.textBetween(Math.max(0, from - 1), from) === "/") editor.commands.deleteRange({ from: from - 1, to: from });
    const chain = editor.chain().focus();
    if (kind === "h1") chain.setHeading({ level: 1 }).run();
    if (kind === "h2") chain.setHeading({ level: 2 }).run();
    if (kind === "bullet") chain.toggleBulletList().run();
    if (kind === "task") chain.toggleTaskList().run();
    if (kind === "quote") chain.toggleBlockquote().run();
    if (kind === "code") chain.toggleCodeBlock().run();
    if (kind === "table") chain.insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run();
    if (kind === "divider") chain.setHorizontalRule().run();
    if (kind === "callout") chain.insertContent('<aside data-type="callout"><p>提示内容</p></aside>').run();
    if (kind === "image") {
      const url = window.prompt("输入图片地址");
      if (url?.trim()) chain.setImage({ src: url.trim(), alt: "文档图片" }).run();
    }
    if (kind === "link") {
      const url = window.prompt("输入链接地址");
      if (url?.trim()) chain.setLink({ href: url.trim() }).run();
    }
    setSlashOpen(false);
  }

  function insertDocumentReference(documentItem: CollaborativeDocument) {
    if (!editor) return;
    const from = editor.state.selection.from;
    if (editor.state.doc.textBetween(Math.max(0, from - 1), from) === "@") {
      editor.commands.deleteRange({ from: from - 1, to: from });
    }
    editor.chain().focus().insertContent(
      `<a href="/documents?document=${encodeURIComponent(documentItem.id)}">@${escapeHtml(documentItem.title)}</a>&nbsp;`,
    ).run();
    setMentionOpen(false);
  }

  function insertMemberMention(permission: DocumentPermission) {
    if (!editor) return;
    const from = editor.state.selection.from;
    if (editor.state.doc.textBetween(Math.max(0, from - 1), from) === "@") {
      editor.commands.deleteRange({ from: from - 1, to: from });
    }
    editor.chain().focus().insertContent(`<strong>@${escapeHtml(permission.display_name)}</strong>&nbsp;`).run();
    setMentionOpen(false);
  }

  const sourceLabel = useMemo(() => {
    if (!selected) return "";
    if (selected.source_type === "meeting") return "会议记录";
    if (selected.source_type === "imported") return "导入文档";
    return "我的文档";
  }, [selected]);

  return (
    <main
      className={`docs-app ${inspectorOpen && selected ? "docs-app--inspector" : ""}${sidebarCollapsed ? " docs-app--nav-collapsed" : ""}`}
      style={{ "--docs-sidebar-width": `${sidebarWidth}px` } as CSSProperties}
    >
      <aside className="docs-nav">
        <div className="docs-nav__head">
          <strong><span className="docs-brand-mark"><FileText size={16} /></span>云文档</strong>
          <div className="docs-nav__head-actions">
            <button className="docs-icon-button" onClick={() => setSidebarCollapsed(true)} aria-label="收起左侧栏" title="收起左侧栏"><PanelLeftClose size={18} /></button>
            <div className="docs-new-wrap">
            <button className="docs-icon-button docs-icon-button--primary" onClick={() => setNewMenuOpen((value) => !value)} aria-label="新建文档"><Plus size={19} /></button>
            {newMenuOpen && (
              <div className="docs-popover docs-template-menu">
                <button onClick={() => void createDocument()}><FilePlus2 size={18} /><span><strong>空白文档</strong><small>从空白页面开始</small></span></button>
                <button onClick={() => void createDocument("meeting")}><MessageSquare size={18} /><span><strong>会议记录</strong><small>摘要、决定和行动项</small></span></button>
                <button onClick={() => void createDocument("project")}><ListChecks size={18} /><span><strong>项目计划</strong><small>目标、计划和风险</small></span></button>
                <button onClick={() => void createDocument("weekly")}><FileText size={18} /><span><strong>周报</strong><small>进展、问题和下周计划</small></span></button>
                <button onClick={() => { setImportOpen(true); setNewMenuOpen(false); }}><Download size={18} /><span><strong>导入 Markdown</strong><small>原文件保持不变</small></span></button>
              </div>
            )}
            </div>
          </div>
        </div>
        <nav className="docs-nav__links" aria-label="文档分类">
          {([
            ["recent", <Clock3 size={17} />],
            ["mine", <FileText size={17} />],
            ["shared", <Users size={17} />],
            ["meeting", <MessageSquare size={17} />],
            ["scan", <ImagePlus size={17} />],
            ["favorite", <ArchiveRestore size={17} />],
            ["trash", <Trash2 size={17} />],
          ] as Array<[LibraryView, ReactNode]>).map(([view, icon]) => {
            const viewFolders = folders.filter((folder) => folder.view === view);
            const expanded = expandedViews.includes(view);
            return (
              <div className="docs-nav-group" key={view}>
                <div className="docs-nav-group__row">
                  <DocsNavButton active={libraryView === view && !activeFolder} onClick={() => navigateLibrary(view)} icon={icon} label={libraryTitle(view)} />
                  <button className="docs-nav-group__toggle" onClick={() => setExpandedViews((items) => items.includes(view) ? items.filter((item) => item !== view) : [...items, view])} aria-label={`${expanded ? "收起" : "展开"}${libraryTitle(view)}`}>
                    {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </button>
                  <button className="docs-nav-group__add" onClick={() => createSubfolder(view)} aria-label={`在${libraryTitle(view)}下新建文件夹`} title="新建子文件夹"><FolderPlus size={14} /></button>
                </div>
                {expanded && viewFolders.length > 0 && (
                  <div className="docs-nav-folders">
                    {viewFolders.map((folder) => (
                      <button className={activeFolder === folder.id ? "active" : ""} key={folder.id} onClick={() => navigateLibrary(view, folder.id)}>
                        <Folder size={15} /><span>{folder.name}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </nav>
        <div className="docs-nav__space">
          <span>空间</span>
          <button className="active"><span className="docs-space-dot" />个人空间</button>
        </div>
        <div className="docs-nav__footer">
          <Users size={16} />
          <span>本机工作空间</span>
          <small>内容保存在设备内</small>
        </div>
      </aside>
      {!sidebarCollapsed && <div className="docs-nav-resizer" onPointerDown={beginSidebarResize} title="拖动调整侧栏宽度" />}
      {sidebarCollapsed && <button className="docs-nav-open" onClick={() => setSidebarCollapsed(false)} aria-label="展开左侧栏" title="展开左侧栏"><PanelLeftOpen size={19} /></button>}

      {!selected ? (
        <section className="docs-library">
          <header className="docs-library__header">
            <div>
              <h1>{libraryTitle(libraryView)}</h1>
              <p>{libraryView === "trash" ? "已删除的文档会保留在这里，可恢复或永久删除。" : "创建、编辑和整理文档，所有操作都在当前应用内完成。"}</p>
            </div>
            {libraryView !== "trash" && <button className="docs-primary-button" onClick={() => setNewMenuOpen(true)}><Plus size={17} />新建文档</button>}
          </header>
          <div className="docs-search">
            <Search size={17} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={libraryView === "trash" ? "搜索已删除文档" : "搜索标题、正文或创建者"} />
            <kbd>⌘ K</kbd>
          </div>
          {libraryView !== "trash" && (
            <section className="docs-template-strip">
              <header><strong>从模板开始</strong><span>快速创建常用文档</span></header>
              <div>
                <button onClick={() => void createDocument()}><span className="docs-template-icon docs-template-icon--blank"><Plus size={22} /></span><b>空白文档</b><small>自由开始创作</small></button>
                <button onClick={() => void createDocument("meeting")}><span className="docs-template-icon docs-template-icon--meeting"><MessageSquare size={21} /></span><b>会议记录</b><small>摘要与行动项</small></button>
                <button onClick={() => void createDocument("project")}><span className="docs-template-icon docs-template-icon--project"><ListChecks size={21} /></span><b>项目计划</b><small>目标与里程碑</small></button>
                <button onClick={() => void createDocument("weekly")}><span className="docs-template-icon docs-template-icon--weekly"><FileText size={21} /></span><b>工作周报</b><small>进展与下周计划</small></button>
              </div>
            </section>
          )}
          {error && <div className="docs-error">{error}</div>}
          <div className="docs-list-section-title">
            <strong>{libraryTitle(libraryView)}</strong>
            <span>{libraryView === "trash" ? `${documents.length} 个已删除文档` : `${documents.length} 个文档`}</span>
          </div>
          <div className={`docs-list-heading${libraryView === "trash" ? " docs-list-heading--trash" : ""}`}>
            <span>名称</span><span>所属空间</span><span>{libraryView === "trash" ? "删除时间" : "更新时间"}</span>
            {libraryView === "trash" && <span>操作</span>}
          </div>
          <DocumentList documents={documents} libraryView={libraryView} loading={loading}
            selectDocument={setSelectedId} createDocument={() => createDocument()}
            restoreDocument={restoreDocumentById} purgeDocument={purgeDocumentById} />
        </section>
      ) : (
        <DocumentEditor
          document={selected} saveState={saveState} notice={notice}
          collaborationState={collaborationState} onlineMembers={onlineMembers} widePage={widePage}
          goBack={() => setSelectedId("")} renameDocument={renameDocument}
          openComments={() => { setInspector("comments"); setInspectorOpen(true); }}
          openSharing={() => { setInspector("info"); setInspectorOpen(true); }}
          toggleWidePage={() => setWidePage((value) => !value)}
        >

          <DocumentToolbar editor={editor} toggleInsertMenu={() => setSlashOpen((value) => !value)} uploadAttachment={uploadAttachment} />

          {error && <div className="docs-error docs-error--editor">{error}<button onClick={() => setError("")}><X size={15} /></button></div>}
          <div className={`docs-page-scroll ${widePage ? "docs-page-scroll--wide" : ""}`}>
            {editor && getDocumentHeadings(editor).length > 1 && (
              <nav className="docs-toc" aria-label="文档目录">
                <strong>目录</strong>
                {getDocumentHeadings(editor).map((heading) => (
                  <button
                    key={`${heading.position}:${heading.text}`}
                    className={`docs-toc__level-${heading.level}`}
                    onClick={() => editor.chain().focus().setTextSelection(heading.position + 1).scrollIntoView().run()}
                  >
                    {heading.text}
                  </button>
                ))}
              </nav>
            )}
            <article className="docs-paper">
              <div className="docs-paper__meta"><span>{sourceLabel}</span><span>由 {selected.owner_name} 创建</span><span>{formatTime(selected.updated_at)} 更新</span></div>
              <EditorContent editor={editor} />
              {slashOpen && (
                <div className="docs-slash-menu">
                  <strong>插入内容</strong>
                  <button onClick={() => insertSlashBlock("h1")}><Heading1 size={18} /><span><b>一级标题</b><small>大标题</small></span></button>
                  <button onClick={() => insertSlashBlock("h2")}><Heading2 size={18} /><span><b>二级标题</b><small>章节标题</small></span></button>
                  <button onClick={() => insertSlashBlock("bullet")}><List size={18} /><span><b>无序列表</b><small>整理多个条目</small></span></button>
                  <button onClick={() => insertSlashBlock("task")}><ListChecks size={18} /><span><b>待办事项</b><small>记录行动项</small></span></button>
                  <button onClick={() => insertSlashBlock("quote")}><Quote size={18} /><span><b>引用</b><small>突出引用内容</small></span></button>
                  <button onClick={() => insertSlashBlock("callout")}><Sparkles size={18} /><span><b>提示块</b><small>突出提醒或说明</small></span></button>
                  <button onClick={() => insertSlashBlock("code")}><Code2 size={18} /><span><b>代码块</b><small>显示代码或命令</small></span></button>
                  <button onClick={() => insertSlashBlock("table")}><Table2 size={18} /><span><b>表格</b><small>插入 Markdown 表格</small></span></button>
                  <button onClick={() => insertSlashBlock("image")}><ImagePlus size={18} /><span><b>图片</b><small>插入图片地址</small></span></button>
                  <button onClick={() => insertSlashBlock("link")}><Link2 size={18} /><span><b>链接</b><small>为选中内容添加链接</small></span></button>
                  <button onClick={() => insertSlashBlock("divider")}><MoreHorizontal size={18} /><span><b>分割线</b><small>分隔不同章节</small></span></button>
                </div>
              )}
              {mentionOpen && (
                <div className="docs-slash-menu docs-mention-menu">
                  <strong>提及成员或引用文档</strong>
                  {(selected.permissions ?? []).filter((permission) => permission.principal_id !== selected.owner_id).slice(0, 5).map((permission) => (
                    <button key={`member:${permission.principal_id}`} onClick={() => insertMemberMention(permission)}>
                      <Users size={18} /><span><b>@{permission.display_name}</b><small>{roleLabel(permission.role)}</small></span>
                    </button>
                  ))}
                  {documents.filter((item) => item.id !== selected.id).slice(0, 8).map((item) => (
                    <button key={item.id} onClick={() => insertDocumentReference(item)}>
                      <FileText size={18} /><span><b>{item.title}</b><small>{friendlySource(item.source_type)} · 稳定文档链接</small></span>
                    </button>
                  ))}
                  {!documents.some((item) => item.id !== selected.id) && <small>没有其他可引用文档</small>}
                </div>
              )}
            </article>
          </div>
        </DocumentEditor>
      )}

      {selected && inspectorOpen && (
        <Suspense fallback={<aside className="docs-inspector docs-inspector--loading" aria-busy="true">正在加载文档信息…</aside>}>
          <DocumentInspector model={{
            selected, inspectorOpen, inspector, setInspector, setInspectorOpen,
            commentDraft, setCommentDraft, busy, addComment, comments, replyToComment, toggleComment,
            revisionPreview, setRevisionPreview, editor, revisions, previewRevision, restoreRevision,
            copyShareLink, removeCollaborator, collaboratorId, setCollaboratorId, collaboratorRole,
            setCollaboratorRole, saveCollaborator, attachments, downloadAttachment, uploadProgress,
            uploadController, uploadAttachment, toggleFavorite, duplicateDocument, downloadMarkdown,
            downloadHtml, printPdf, downloadDocx, restoreDocument, purgeDocument, moveToTrash,
            aiBusy, requestAiSuggestion, aiSuggestion, setAiSuggestion, acceptAiSuggestion,
          }} />
        </Suspense>
      )}
      {importOpen && (
        <div className="docs-modal-backdrop" role="dialog" aria-modal="true" aria-label="导入 Markdown">
          <div className="docs-modal">
            <header><div><h2>导入 Markdown</h2><p>复制内容生成新文档，原文件不会被修改。</p></div><button className="docs-icon-button" onClick={() => setImportOpen(false)}><X size={18} /></button></header>
            <label>工作区文件路径<input value={importPath} onChange={(event) => setImportPath(event.target.value)} placeholder="例如：meetings/会议记录/项目周会.md" autoFocus /></label>
            <footer>
              <button className="docs-secondary-button" onClick={() => void migrateExistingMarkdown()} disabled={busy}>迁移工作区全部 Markdown</button>
              <button className="docs-secondary-button" onClick={() => setImportOpen(false)}>取消</button>
              <button className="docs-primary-button" onClick={() => void importMarkdown()} disabled={!importPath.trim() || busy}>导入文档</button>
            </footer>
          </div>
        </div>
      )}
    </main>
  );
}
