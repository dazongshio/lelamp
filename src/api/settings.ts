import { request } from "./client";
import type { ApiResult } from "./types";

export interface AudioSettings {
  status: string;
  volume: number;
  muted: boolean;
  backend: string;
}

export async function getAudioSettings(): Promise<ApiResult<AudioSettings>> {
  const data = await request<AudioSettings>("/api/settings/audio");
  return { data, source: "api" };
}

export async function updateAudioSettings(settings: { volume?: number; muted?: boolean }): Promise<ApiResult<AudioSettings>> {
  const data = await request<AudioSettings>("/api/settings/audio", {
    method: "POST",
    body: JSON.stringify(settings),
  });
  return { data, source: "api" };
}

export async function requestFullControl(purpose: string): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/settings/full-control/request", {
    method: "POST",
    body: JSON.stringify({ purpose }),
  });
  return { data, source: "api" };
}

export async function confirmFullControl(step: number, requestId?: string): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/settings/full-control/confirm", {
    method: "POST",
    body: JSON.stringify({ step, request_id: requestId }),
  });
  return { data, source: "api" };
}

export async function cancelFullControl(requestId?: string): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/settings/full-control/cancel", {
    method: "POST",
    body: JSON.stringify({ request_id: requestId }),
  });
  return { data, source: "api" };
}
