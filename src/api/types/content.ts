import type { StatusKind } from "./base";

export interface SharedFile {
  name: string;
  relative_path: string;
  workspace_name?: string;
  size: number;
  size_bytes?: number;
  size_label: string;
  sha256: string;
  uploaded_at: string;
  status: StatusKind | string;
  mime_type?: string;
  allowed_actions?: string[];
}

export interface SharedFilesResponse {
  shared_inbox: string;
  files: SharedFile[];
  items?: SharedFile[];
  total?: number;
  page?: number;
  page_size?: number;
}

export interface SharedPreviewResponse {
  status: StatusKind | string;
  workspace_name: string;
  name: string;
  size_bytes: number;
  text?: string;
  truncated?: boolean;
  download_only?: boolean;
  document_text_backend?: string;
}

export interface DocmostSpace {
  id?: string;
  name?: string;
  slug?: string;
  [key: string]: unknown;
}

export interface DocmostStatus {
  provider: "docmost" | string;
  status: StatusKind | string;
  url: string;
  default_space: string;
  configured: boolean;
  message?: string;
  workspace?: Record<string, unknown>;
  spaces?: DocmostSpace[];
  resolved_space?: DocmostSpace;
  details?: Record<string, unknown>;
}

export interface DocmostSyncResponse {
  status: StatusKind | string;
  provider: "docmost" | string;
  source_file: string;
  source_kind: string;
  extraction?: Record<string, unknown>;
  docmost_url: string;
  docmost_page_id: string;
  docmost_page_url: string;
  docmost_page_title: string;
  docmost_space_id: string;
  docmost_space_slug: string;
  task_id?: string;
}

export interface WikiPageItem {
  path: string;
  title: string;
  excerpt: string;
  updated_at: string;
  size_bytes: number;
  size_label: string;
}

export interface WikiPagesResponse {
  status: StatusKind | string;
  root: string;
  workspace_root: string;
  pages: WikiPageItem[];
}

export interface WikiPageResponse {
  status: StatusKind | string;
  page: WikiPageItem;
  content: string;
}

export interface WikiSaveResponse {
  status: StatusKind | string;
  created: boolean;
  page: WikiPageItem;
  content: string;
  message?: string;
}

export interface AuditEvent {
  timestamp: string;
  actor?: string;
  action: string;
  status: StatusKind | string;
  target: string;
  details: Record<string, unknown>;
  request_id?: string;
  source_ip?: string;
  permission_mode?: string;
  desktop_backend?: string;
  user?: string;
  source?: string;
  session_id?: string;
}

export interface AuditResponse {
  items: AuditEvent[];
  events?: AuditEvent[];
  total: number;
  page: number;
  page_size: number;
  path: string;
}

