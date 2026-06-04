import { request } from "./client";
import type { ApiResult, MobileBridgeRequestResponse, MobileBridgeStatus } from "./types";

export async function getMobileBridgeStatus(): Promise<ApiResult<MobileBridgeStatus>> {
  const data = await request<MobileBridgeStatus>("/api/mobile/status");
  return { data, source: "api" };
}

export async function sendMobileBridgeRequest(requestText: string, authorized: boolean): Promise<ApiResult<MobileBridgeRequestResponse>> {
  const data = await request<MobileBridgeRequestResponse>("/api/mobile/request", {
    method: "POST",
    body: JSON.stringify({ request: requestText, authorized }),
  });
  return { data, source: "api" };
}
