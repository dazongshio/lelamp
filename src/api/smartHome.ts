import { request } from "./client";
import type { ApiResult, SmartHomeControlResponse, SmartHomeStatus } from "./types";

export async function getSmartHomeStatus(): Promise<ApiResult<SmartHomeStatus>> {
  const data = await request<SmartHomeStatus>("/api/smart-home/status");
  return { data, source: "api" };
}

export async function controlSmartHome(command: string, entityName = ""): Promise<ApiResult<SmartHomeControlResponse>> {
  const data = await request<SmartHomeControlResponse>("/api/smart-home/control", {
    method: "POST",
    body: JSON.stringify({ command, entity_name: entityName }),
  });
  return { data, source: "api" };
}
