import { appendQuery, readToken, request, requestWithMock } from "./client";
import type { ApiResult, SharedFile, SharedFilesResponse, SharedPreviewResponse } from "./types";
import { mockFiles } from "../data/mockFiles";

export type SharedFileAction =
  | "analyze"
  | "summarize"
  | "report_outline"
  | "key_data_table"
  | "search"
  | "generate_minutes"
  | "followup_package";

export interface SharedFilesParams {
  q?: string;
  type?: string;
  status?: string;
  page?: number;
  page_size?: number;
  sort?: string;
}

export function getSharedFiles(params: SharedFilesParams = {}): Promise<ApiResult<SharedFilesResponse>> {
  return requestWithMock<SharedFilesResponse>(
    appendQuery("/api/shared/files", { ...params }),
    { shared_inbox: "/workspace/shared_inbox", files: mockFiles, total: mockFiles.length, page: 1, page_size: mockFiles.length },
  );
}

export function getWorkspaceFiles(params: SharedFilesParams = {}): Promise<ApiResult<SharedFilesResponse>> {
  return requestWithMock<SharedFilesResponse>(
    appendQuery("/api/workspace/files", { ...params }),
    { shared_inbox: "/workspace/shared_inbox", files: mockFiles, total: mockFiles.length, page: 1, page_size: mockFiles.length },
  );
}

export async function uploadSharedFile(file: File): Promise<ApiResult<{ status: string; files: SharedFile[] }>> {
  const form = new FormData();
  form.append("file", file);
  const data = await request<{ status: string; files: SharedFile[] }>("/api/shared/upload", {
    method: "POST",
    body: form,
  });
  return { data, source: "api" };
}

export async function saveSharedNote(title: string, content: string): Promise<ApiResult<{ status: string; file: SharedFile }>> {
  const data = await request<{ status: string; file: SharedFile }>("/api/shared/note", {
    method: "POST",
    body: JSON.stringify({ title, content }),
  });
  return { data, source: "api" };
}

export async function runSharedFileAction(
  filePath: string,
  action: SharedFileAction,
  params: Record<string, unknown> = {},
): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/shared/file-action", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath, action, params }),
  });
  return { data, source: "api" };
}

export function getSharedPreview(filePath: string): Promise<ApiResult<SharedPreviewResponse>> {
  return requestWithMock<SharedPreviewResponse>(
    appendQuery("/api/shared/preview", { file: filePath }),
    {
      status: "backend_missing",
      workspace_name: filePath,
      name: filePath.split("/").pop() ?? filePath,
      size_bytes: 0,
      download_only: true,
    },
  );
}

export function getWorkspacePreview(filePath: string): Promise<ApiResult<SharedPreviewResponse>> {
  return requestWithMock<SharedPreviewResponse>(
    appendQuery("/api/workspace/preview", { file: filePath }),
    {
      status: "backend_missing",
      workspace_name: filePath,
      name: filePath.split("/").pop() ?? filePath,
      size_bytes: 0,
      download_only: true,
    },
  );
}

export type FileSource = "shared_inbox" | "workspace";

export function getFileViewUrl(source: FileSource, filePath: string): string {
  return buildFileUrl(source === "workspace" ? "/api/workspace/file" : "/api/shared/file", filePath);
}

export function getFileDownloadUrl(source: FileSource, filePath: string): string {
  return buildFileUrl(source === "workspace" ? "/api/workspace/download" : "/api/shared/download", filePath);
}

function buildFileUrl(path: string, filePath: string): string {
  const token = readToken();
  return appendQuery(path, { file: filePath, token: token || undefined });
}
