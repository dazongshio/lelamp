import { request, requestWithMock } from "./client";
import type { ApiResult, DocmostStatus, DocmostSyncResponse } from "./types";

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
