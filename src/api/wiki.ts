import { request, requestWithMock } from "./client";
import type {
  ApiResult,
  DocmostStatus,
  DocmostSyncResponse,
  WikiPageResponse,
  WikiPagesResponse,
  WikiSaveResponse,
} from "./types";

export function getDocmostStatus(): Promise<ApiResult<DocmostStatus>> {
  return requestWithMock<DocmostStatus>("/api/docmost/status", {
    provider: "docmost",
    status: "needs_config",
    url: "",
    default_space: "General",
    configured: false,
    spaces: [],
  });
}

export async function syncWorkspaceFileToWiki(payload: {
  filePath: string;
  title?: string;
  space?: string;
}): Promise<ApiResult<DocmostSyncResponse>> {
  const data = await request<DocmostSyncResponse>("/api/docmost/sync-file", {
    method: "POST",
    body: JSON.stringify({
      file_path: payload.filePath,
      title: payload.title,
      space: payload.space,
    }),
  });
  return { data, source: "api" };
}

export function getWikiPages(): Promise<ApiResult<WikiPagesResponse>> {
  return requestWithMock<WikiPagesResponse>("/api/wiki/pages", {
    status: "ok",
    root: "workspace/wiki",
    workspace_root: "workspace",
    pages: [],
  });
}

export async function getWikiPage(path: string): Promise<ApiResult<WikiPageResponse>> {
  const data = await request<WikiPageResponse>(`/api/wiki/page?path=${encodeURIComponent(path)}`);
  return { data, source: "api" };
}

export async function saveWikiPage(payload: {
  path?: string;
  title: string;
  content: string;
}): Promise<ApiResult<WikiSaveResponse>> {
  const data = await request<WikiSaveResponse>("/api/wiki/page", {
    method: "POST",
    body: JSON.stringify({
      path: payload.path,
      title: payload.title,
      content: payload.content,
    }),
  });
  return { data, source: "api" };
}
