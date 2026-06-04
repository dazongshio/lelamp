import { request, requestWithMock } from "./client";
import type { ApiResult, PptPageSummaryResponse, ProjectionCalibrationResponse, ProjectionCard, ProjectionDisplayProfileResponse, ProjectionLatestResponse, ProjectionMarkdownFileResponse, ProjectionPptxSessionResponse, ProjectionServiceStatus } from "./types";
import { projectionCards } from "../data/mockProjection";

export function getProjectionLatest(): Promise<ApiResult<ProjectionLatestResponse>> {
  return requestWithMock<ProjectionLatestResponse>("/api/projection/latest", {
    status: "empty",
    html: "",
    cards: projectionCards,
  });
}

export function getProjectionServiceStatus(): Promise<ApiResult<ProjectionServiceStatus>> {
  return requestWithMock<ProjectionServiceStatus>("/api/projection/service/status", {
    status: "adapter_ready",
    preview_url: "http://127.0.0.1:8765/",
    display_test_mode: true,
    physical_projector: "display_substitute",
    output_target: "external_monitor",
    started_at: null,
    message: "External monitor display mode is available.",
  });
}

export async function getProjectionDisplayProfile(): Promise<ApiResult<ProjectionDisplayProfileResponse>> {
  const data = await request<ProjectionDisplayProfileResponse>("/api/projection/display-profile");
  return { data, source: "api" };
}

export async function updateProjectionDisplayProfile(payload: {
  mode?: string;
  ambient_lux?: number | null;
  brightness?: number;
  contrast?: number;
  scale?: number;
  keystone_x?: number;
  keystone_y?: number;
  calibration?: Record<string, unknown>;
}): Promise<ApiResult<ProjectionDisplayProfileResponse>> {
  const data = await request<ProjectionDisplayProfileResponse>("/api/projection/display-profile", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function createProjectionCard(card: ProjectionCard): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/projection/card", {
    method: "POST",
    body: JSON.stringify({
      title: card.title,
      type: card.mode === "action_card" ? "action" : card.mode,
      message: card.subtitle,
      status: card.subtitle,
      actions: [card.subtitle],
      details: [card.subtitle],
      accent: card.accent,
      duration_seconds: card.mode === "countdown" ? 300 : undefined,
    }),
  });
  return { data, source: "api" };
}

export async function startProjectionService(): Promise<ApiResult<ProjectionServiceStatus | Record<string, unknown>>> {
  const data = await request<ProjectionServiceStatus | Record<string, unknown>>("/api/projection/service/start", {
    method: "POST",
    body: "{}",
  });
  return { data, source: "api" };
}

export async function stopProjectionService(): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/projection/service/stop", {
    method: "POST",
    body: "{}",
  });
  return { data, source: "api" };
}

export async function summarizePptPage(payload: {
  image_data_url?: string;
  file_path?: string;
  slide_index?: number;
  title?: string;
  render_projection?: boolean;
  source?: string;
}): Promise<ApiResult<PptPageSummaryResponse>> {
  const data = await request<PptPageSummaryResponse>("/api/projection/summarize-ppt-page", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function projectMarkdownFile(payload: {
  file_path: string;
  title?: string;
}): Promise<ApiResult<ProjectionMarkdownFileResponse>> {
  const data = await request<ProjectionMarkdownFileResponse>("/api/projection/markdown-file", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function projectPptxSession(payload: {
  file_path: string;
  title?: string;
  slide_index?: number;
  action?: "show" | "next" | "previous";
}): Promise<ApiResult<ProjectionPptxSessionResponse>> {
  const data = await request<ProjectionPptxSessionResponse>("/api/projection/pptx/session", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function createCalibrationPattern(title = "投影校准测试图"): Promise<ApiResult<ProjectionCalibrationResponse>> {
  const data = await request<ProjectionCalibrationResponse>("/api/projection/calibration/pattern", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
  return { data, source: "api" };
}

export async function analyzeCalibrationCapture(payload: {
  image_data_url: string;
  title?: string;
}): Promise<ApiResult<ProjectionCalibrationResponse>> {
  const data = await request<ProjectionCalibrationResponse>("/api/projection/calibration/analyze", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function applyCalibrationProfile(payload: {
  calibration?: ProjectionCalibrationResponse | Record<string, unknown>;
  analysis_path?: string;
  ambient_lux?: number;
}): Promise<ApiResult<ProjectionDisplayProfileResponse>> {
  const data = await request<ProjectionDisplayProfileResponse>("/api/projection/calibration/apply", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}
