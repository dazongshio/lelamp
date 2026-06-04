import { request, requestWithMock } from "./client";
import type { ApiResult, HardwareStatus, HardwareTestResponse, LeLampStateResponse } from "./types";

export function getHardwareStatus(): Promise<ApiResult<HardwareStatus>> {
  return requestWithMock<HardwareStatus>("/api/hardware/status", {
    hardware_enabled: false,
    devices: {
      camera: { status: "adapter_ready", details: { note: "mock only" } },
      mic: { status: "adapter_ready", details: { note: "mock only" } },
      speaker: { status: "adapter_ready", details: { note: "mock only" } },
      projection: { status: "adapter_ready", details: { note: "mock only" } },
      rgb: { status: "adapter_ready", details: { note: "mock only" } },
    },
    sensors: {},
    events: [],
  });
}

export async function setLeLampState(state: string): Promise<ApiResult<LeLampStateResponse>> {
  const data = await request<LeLampStateResponse>("/api/lelamp/state", {
    method: "POST",
    body: JSON.stringify({ state }),
  });
  return { data, source: "api" };
}

export function scanHardware(): Promise<ApiResult<HardwareStatus>> {
  return requestWithMock<HardwareStatus>("/api/hardware/scan", {
    hardware_enabled: false,
    devices: {},
    sensors: {},
    events: [],
    scan: { status: "backend_missing", summary: {}, notes: ["mock only"] },
  });
}

export async function runHardwareTest(
  test: "scan" | "camera" | "mic" | "speaker" | "projection" | "rgb",
  params: Record<string, unknown> = {},
): Promise<ApiResult<HardwareTestResponse | HardwareStatus>> {
  const data = await request<HardwareTestResponse | HardwareStatus>("/api/hardware/test", {
    method: "POST",
    body: JSON.stringify({ test, ...params }),
  });
  return { data, source: "api" };
}
