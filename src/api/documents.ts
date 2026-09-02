import { request, requestWithMock } from "./client";
import type { ApiResult, DocumentResult, ScanResult } from "./types";

export type CollaborativeDocumentRole = "owner" | "editor" | "commenter" | "viewer";

export interface CollaborativeDocument {
  id: string;
  engine: "native" | "docmost";
  title: string;
  space_id: string;
  owner_id: string;
  owner_name: string;
  status: "active" | "archived" | "trashed";
  source_type: "manual" | "meeting" | "imported" | "ai_generated" | string;
  source_path: string;
  external_id?: string;
  created_at: string;
  updated_at: string;
  content_version: number;
  excerpt: string;
  role: CollaborativeDocumentRole;
  can_edit: boolean;
  can_comment: boolean;
  content?: string;
  favorite?: boolean;
  comment_count?: number;
  attachment_count?: number;
  permissions?: DocumentPermission[];
}

export interface DocumentPermission {
  principal_type: "user" | "space";
  principal_id: string;
  display_name: string;
  role: CollaborativeDocumentRole;
}

export interface DocumentComment {
  id: string;
  document_id: string;
  parent_id: string;
  body: string;
  anchor_text: string;
  author_id: string;
  author_name: string;
  created_at: string;
  updated_at: string;
  resolved: boolean;
}

export interface DocumentRevision {
  id: string;
  document_id: string;
  title: string;
  content_version: number;
  actor_id: string;
  actor_name: string;
  summary: string;
  created_at: string;
  restorable: boolean;
  content?: string;
}

export interface DocumentAttachment {
  id: string;
  document_id: string;
  filename: string;
  mime_type: string;
  size: number;
  checksum: string;
  created_by: string;
  created_by_name: string;
  created_at: string;
}

export interface DocumentCollaborationSession {
  status: string;
  document_id: string;
  token: string;
  expires_at: number;
  url: string;
  user: { id: string; name: string; color: string };
}

export async function listCollaborativeDocuments(options: {
  status?: "active" | "archived" | "trashed";
  query?: string;
  sourceType?: string;
  spaceId?: string;
} = {}): Promise<ApiResult<{ status: string; documents: CollaborativeDocument[]; count: number }>> {
  const params = new URLSearchParams();
  params.set("status", options.status ?? "active");
  if (options.query) params.set("q", options.query);
  if (options.sourceType) params.set("source_type", options.sourceType);
  if (options.spaceId) params.set("space_id", options.spaceId);
  const data = await request<{ status: string; documents: CollaborativeDocument[]; count: number }>(`/api/docs?${params}`);
  return { data, source: "api" };
}

export async function createCollaborativeDocument(payload: {
  title: string;
  content?: string;
  template?: "meeting" | "project" | "weekly" | "";
  source_type?: string;
  source_path?: string;
  external_id?: string;
  update_existing?: boolean;
  idempotency_key?: string;
  space_id?: string;
}): Promise<ApiResult<{ status: string; document: CollaborativeDocument }>> {
  const data = await request<{ status: string; document: CollaborativeDocument }>("/api/docs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function migrateWorkspaceMarkdown(paths?: string[]): Promise<ApiResult<{
  status: string;
  imported: CollaborativeDocument[];
  imported_count: number;
  error_count: number;
  errors: Array<{ path: string; message: string }>;
}>> {
  const data = await request<{
    status: string;
    imported: CollaborativeDocument[];
    imported_count: number;
    error_count: number;
    errors: Array<{ path: string; message: string }>;
  }>("/api/docs/migrate", {
    method: "POST",
    body: JSON.stringify(paths ? { paths } : {}),
  });
  return { data, source: "api" };
}

export async function getCollaborativeDocument(documentId: string): Promise<ApiResult<{ status: string; document: CollaborativeDocument }>> {
  const data = await request<{ status: string; document: CollaborativeDocument }>(`/api/docs/${encodeURIComponent(documentId)}`);
  return { data, source: "api" };
}

export async function updateCollaborativeDocument(
  documentId: string,
  payload: { title?: string; content?: string; base_version?: number; space_id?: string; summary?: string },
): Promise<ApiResult<{ status: string; document: CollaborativeDocument }>> {
  const data = await request<{ status: string; document: CollaborativeDocument }>(`/api/docs/${encodeURIComponent(documentId)}/update`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function trashCollaborativeDocument(documentId: string): Promise<ApiResult<{ status: string; document: CollaborativeDocument }>> {
  const data = await request<{ status: string; document: CollaborativeDocument }>(`/api/docs/${encodeURIComponent(documentId)}/trash`, {
    method: "POST",
    body: "{}",
  });
  return { data, source: "api" };
}

export async function restoreCollaborativeDocument(documentId: string): Promise<ApiResult<{ status: string; document: CollaborativeDocument }>> {
  const data = await request<{ status: string; document: CollaborativeDocument }>(`/api/docs/${encodeURIComponent(documentId)}/restore`, {
    method: "POST",
    body: "{}",
  });
  return { data, source: "api" };
}

export async function purgeCollaborativeDocument(documentId: string): Promise<ApiResult<{ status: string; document: { id: string; status: string } }>> {
  const data = await request<{ status: string; document: { id: string; status: string } }>(`/api/docs/${encodeURIComponent(documentId)}/purge`, {
    method: "POST",
    body: "{}",
  });
  return { data, source: "api" };
}

export async function setCollaborativeDocumentFavorite(
  documentId: string,
  favorite: boolean,
): Promise<ApiResult<{ status: string; document: CollaborativeDocument }>> {
  const data = await request<{ status: string; document: CollaborativeDocument }>(`/api/docs/${encodeURIComponent(documentId)}/favorite`, {
    method: "POST",
    body: JSON.stringify({ favorite }),
  });
  return { data, source: "api" };
}

export async function listDocumentComments(documentId: string): Promise<ApiResult<{ status: string; comments: DocumentComment[] }>> {
  const data = await request<{ status: string; comments: DocumentComment[] }>(`/api/docs/${encodeURIComponent(documentId)}/comments?include_resolved=true`);
  return { data, source: "api" };
}

export async function addDocumentComment(
  documentId: string,
  payload: { body: string; anchor_text?: string; parent_id?: string },
): Promise<ApiResult<{ status: string; comment: DocumentComment }>> {
  const data = await request<{ status: string; comment: DocumentComment }>(`/api/docs/${encodeURIComponent(documentId)}/comments`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function updateDocumentComment(
  documentId: string,
  commentId: string,
  payload: { body?: string; resolved?: boolean },
): Promise<ApiResult<{ status: string; comment: DocumentComment }>> {
  const data = await request<{ status: string; comment: DocumentComment }>(
    `/api/docs/${encodeURIComponent(documentId)}/comments/${encodeURIComponent(commentId)}`,
    { method: "POST", body: JSON.stringify(payload) },
  );
  return { data, source: "api" };
}

export async function listDocumentHistory(documentId: string): Promise<ApiResult<{ status: string; revisions: DocumentRevision[] }>> {
  const data = await request<{ status: string; revisions: DocumentRevision[] }>(`/api/docs/${encodeURIComponent(documentId)}/history`);
  return { data, source: "api" };
}

export async function getDocumentRevision(
  documentId: string,
  revisionId: string,
): Promise<ApiResult<{ status: string; revision: DocumentRevision }>> {
  const data = await request<{ status: string; revision: DocumentRevision }>(
    `/api/docs/${encodeURIComponent(documentId)}/history/${encodeURIComponent(revisionId)}`,
  );
  return { data, source: "api" };
}

export async function restoreDocumentRevision(documentId: string, revisionId: string): Promise<ApiResult<{ status: string; document: CollaborativeDocument }>> {
  const data = await request<{ status: string; document: CollaborativeDocument }>(
    `/api/docs/${encodeURIComponent(documentId)}/history/${encodeURIComponent(revisionId)}`,
    { method: "POST", body: "{}" },
  );
  return { data, source: "api" };
}

export async function setDocumentPermissions(documentId: string, permissions: DocumentPermission[]): Promise<ApiResult<{ status: string; permissions: DocumentPermission[] }>> {
  const data = await request<{ status: string; permissions: DocumentPermission[] }>(`/api/docs/${encodeURIComponent(documentId)}/permissions`, {
    method: "POST",
    body: JSON.stringify({ permissions }),
  });
  return { data, source: "api" };
}

export async function createDocumentShareLink(
  documentId: string,
  principalId: string,
): Promise<ApiResult<{ status: string; share_url: string; expires_at: number }>> {
  const data = await request<{ status: string; share_url: string; expires_at: number }>(
    `/api/docs/${encodeURIComponent(documentId)}/share-token`,
    {
      method: "POST",
      body: JSON.stringify({ principal_id: principalId, expires_in: 7 * 24 * 60 * 60 }),
    },
  );
  return { data, source: "api" };
}

export async function listDocumentAttachments(documentId: string): Promise<ApiResult<{ status: string; attachments: DocumentAttachment[] }>> {
  const data = await request<{ status: string; attachments: DocumentAttachment[] }>(`/api/docs/${encodeURIComponent(documentId)}/attachments`);
  return { data, source: "api" };
}

export async function uploadDocumentAttachment(
  documentId: string,
  file: File,
  options: { signal?: AbortSignal; onProgress?: (percent: number) => void } = {},
): Promise<ApiResult<{ status: string; attachment: DocumentAttachment }>> {
  const content_base64 = await fileToBase64(file, options);
  options.onProgress?.(55);
  const data = await request<{ status: string; attachment: DocumentAttachment }>(`/api/docs/${encodeURIComponent(documentId)}/attachments`, {
    method: "POST",
    body: JSON.stringify({ filename: file.name, mime_type: file.type, content_base64 }),
    signal: options.signal,
  });
  options.onProgress?.(100);
  return { data, source: "api" };
}

export async function downloadDocumentAttachment(documentId: string, attachmentId: string): Promise<{ attachment: DocumentAttachment; content: Uint8Array }> {
  const data = await request<{ status: string; attachment: DocumentAttachment; content_base64: string }>(
    `/api/docs/${encodeURIComponent(documentId)}/attachments/${encodeURIComponent(attachmentId)}`,
  );
  const binary = atob(data.content_base64);
  return {
    attachment: data.attachment,
    content: Uint8Array.from(binary, (character) => character.charCodeAt(0)),
  };
}

export async function exportDocumentMarkdown(documentId: string): Promise<{ filename: string; content: string }> {
  const data = await request<{ status: string; filename: string; content_base64: string }>(`/api/docs/${encodeURIComponent(documentId)}/export`);
  return { filename: data.filename, content: decodeBase64Utf8(data.content_base64) };
}

export async function getDocumentCollaborationSession(documentId: string, clientId?: string): Promise<ApiResult<DocumentCollaborationSession>> {
  const suffix = clientId ? `?client_id=${encodeURIComponent(clientId)}` : "";
  const data = await request<DocumentCollaborationSession>(`/api/docs/${encodeURIComponent(documentId)}/collaboration-token${suffix}`);
  return { data, source: "api" };
}

export async function generateDocumentAiSuggestion(
  documentId: string,
  payload: { operation: string; selected_text?: string },
): Promise<ApiResult<{ status: string; operation: string; suggestion: string; base_version: number }>> {
  const data = await request<{ status: string; operation: string; suggestion: string; base_version: number }>(
    `/api/docs/${encodeURIComponent(documentId)}/ai`,
    { method: "POST", body: JSON.stringify(payload) },
  );
  return { data, source: "api" };
}

export async function applyDocumentAiSuggestion(
  documentId: string,
  payload: { operation: string; content: string; base_version: number; mode: "replace" | "insert" },
): Promise<ApiResult<{ status: string; document: CollaborativeDocument }>> {
  const data = await request<{ status: string; document: CollaborativeDocument }>(
    `/api/docs/${encodeURIComponent(documentId)}/ai/apply`,
    { method: "POST", body: JSON.stringify(payload) },
  );
  return { data, source: "api" };
}

function fileToBase64(
  file: File,
  options: { signal?: AbortSignal; onProgress?: (percent: number) => void },
): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onabort = () => reject(new DOMException("上传已取消", "AbortError"));
    reader.onprogress = (event) => {
      if (event.lengthComputable) options.onProgress?.(Math.round((event.loaded / event.total) * 50));
    };
    reader.onload = () => resolve(String(reader.result ?? "").split(",", 2)[1] ?? "");
    if (options.signal?.aborted) {
      reader.abort();
      return;
    }
    options.signal?.addEventListener("abort", () => reader.abort(), { once: true });
    reader.readAsDataURL(file);
  });
}

function decodeBase64Utf8(value: string): string {
  const binary = atob(value);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

export async function runDocumentAnalyze(filePath: string): Promise<ApiResult<DocumentResult>> {
  const data = await request<DocumentResult>("/api/document/analyze", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath }),
  });
  return { data, source: "api" };
}

export async function runDocumentSummarize(filePath: string, style = "brief"): Promise<ApiResult<DocumentResult>> {
  const data = await request<DocumentResult>("/api/document/summarize", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath, style }),
  });
  return { data, source: "api" };
}

export async function runDocumentRisks(filePath: string): Promise<ApiResult<DocumentResult>> {
  const data = await request<DocumentResult>("/api/document/risks", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath }),
  });
  return { data, source: "api" };
}

export async function runDocumentTableExtract(filePath: string): Promise<ApiResult<DocumentResult>> {
  const data = await request<DocumentResult>("/api/document/table-extract", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath }),
  });
  return { data, source: "api" };
}

export async function runDocumentReportOutline(filePath: string, topic?: string): Promise<ApiResult<DocumentResult>> {
  const data = await request<DocumentResult>("/api/document/report-outline", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath, topic }),
  });
  return { data, source: "api" };
}

export function getDocumentAdaptersStatus(): Promise<ApiResult<{ adapters: Record<string, string> }>> {
  return requestWithMock<{ adapters: Record<string, string> }>("/api/document/adapters/status", {
    adapters: {
      document_analyzer: "adapter_ready",
      report_outline: "backend_missing",
      table_extractor: "backend_missing",
      meeting_email_draft: "backend_missing",
      scan_capture: "adapter_ready",
      scan_enhancement: "adapter_ready",
      ocr: "unavailable",
      vision_ocr: "backend_missing",
    },
  });
}

export async function runScanProcess(filePath: string, options: { document_type?: string; language?: string } = {}): Promise<ApiResult<ScanResult>> {
  const data = await request<ScanResult>("/api/scan/process", {
    method: "POST",
    body: JSON.stringify({ filename: filePath, ...options }),
  });
  return { data, source: "api" };
}

export async function runScanEnhance(filePath: string): Promise<ApiResult<ScanResult>> {
  const data = await request<ScanResult>("/api/scan/enhance", {
    method: "POST",
    body: JSON.stringify({ filename: filePath }),
  });
  return { data, source: "api" };
}

export async function checkScanCaptureReadiness(filePath: string): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/scan/capture-readiness", {
    method: "POST",
    body: JSON.stringify({ filename: filePath }),
  });
  return { data, source: "api" };
}

export async function createDemoScanImage(options: { title?: string; document_type?: string } = {}): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/scan/demo-image", {
    method: "POST",
    body: JSON.stringify(options),
  });
  return { data, source: "api" };
}

export async function captureDocumentScan(payload: {
  image_data_url: string;
  title: string;
  document_type?: string;
  language?: string;
}): Promise<ApiResult<ScanResult>> {
  const data = await request<ScanResult>("/api/scan/capture", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function captureDeviceDocumentScan(payload: {
  title: string;
  document_type?: string;
  language?: string;
  camera_index?: number;
}): Promise<ApiResult<ScanResult>> {
  const data = await request<ScanResult>("/api/scan/device-capture", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}
