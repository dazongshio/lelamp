import { request } from "./client";
import type { ApiResult, RemoteSshResult, RemoteSshStatus } from "./types";

export interface RemoteSshPayload {
  host: string;
  user: string;
  port: number;
  keyPath?: string;
  timeoutSeconds?: number;
}

function apiPayload(payload: RemoteSshPayload) {
  return {
    host: payload.host,
    user: payload.user,
    port: payload.port,
    key_path: payload.keyPath,
    timeout_seconds: payload.timeoutSeconds,
  };
}

export async function getRemoteSshStatus(): Promise<ApiResult<RemoteSshStatus>> {
  const data = await request<RemoteSshStatus>("/api/remote/ssh/status");
  return { data, source: "api" };
}

export async function testRemoteSsh(payload: RemoteSshPayload): Promise<ApiResult<RemoteSshResult>> {
  const data = await request<RemoteSshResult>("/api/remote/ssh/test", {
    method: "POST",
    body: JSON.stringify(apiPayload(payload)),
  });
  return { data, source: "api" };
}

export async function runRemoteSshCommand(payload: RemoteSshPayload & {
  command: string;
  authorized: boolean;
}): Promise<ApiResult<RemoteSshResult>> {
  const data = await request<RemoteSshResult>("/api/remote/ssh/run", {
    method: "POST",
    body: JSON.stringify({
      ...apiPayload(payload),
      command: payload.command,
      authorized: payload.authorized,
    }),
  });
  return { data, source: "api" };
}

export async function bootstrapRemoteCodex(payload: RemoteSshPayload & {
  authorized: boolean;
}): Promise<ApiResult<RemoteSshResult>> {
  const data = await request<RemoteSshResult>("/api/remote/ssh/bootstrap-codex", {
    method: "POST",
    body: JSON.stringify({
      ...apiPayload(payload),
      authorized: payload.authorized,
    }),
  });
  return { data, source: "api" };
}

export async function openRemoteCodex(payload: RemoteSshPayload & {
  authorized: boolean;
}): Promise<ApiResult<RemoteSshResult>> {
  const data = await request<RemoteSshResult>("/api/remote/ssh/open-codex", {
    method: "POST",
    body: JSON.stringify({
      ...apiPayload(payload),
      authorized: payload.authorized,
    }),
  });
  return { data, source: "api" };
}

export async function sendRemoteVoiceCommand(payload: RemoteSshPayload & {
  text: string;
}): Promise<ApiResult<RemoteSshResult>> {
  const data = await request<RemoteSshResult>("/api/remote/voice-command", {
    method: "POST",
    body: JSON.stringify({
      ...apiPayload(payload),
      text: payload.text,
    }),
  });
  return { data, source: "api" };
}
