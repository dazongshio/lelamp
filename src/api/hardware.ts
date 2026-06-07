import { request, requestWithMock } from "./client";
import type { ApiResult, HardwareStatus, HardwareTestResponse, LeLampMotorControlResponse, LeLampMotorName, LeLampStateResponse } from "./types";

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

export async function readLeLampMotors(): Promise<ApiResult<LeLampMotorControlResponse>> {
  const data = await request<LeLampMotorControlResponse>("/api/lelamp/motor-control/read", {
    method: "POST",
    body: JSON.stringify({}),
  });
  return { data, source: "api" };
}

export async function moveLeLampMotors(payload: {
  mode?: "target" | "delta";
  target?: Partial<Record<LeLampMotorName, number>>;
  deltas?: Partial<Record<LeLampMotorName, number>>;
  motor?: LeLampMotorName;
  delta?: number;
  max_delta?: number;
  hold_seconds?: number;
}): Promise<ApiResult<LeLampMotorControlResponse>> {
  const data = await request<LeLampMotorControlResponse>("/api/lelamp/motor-control/move", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function saveLeLampPose(payload: {
  pose: "default" | "scan" | "projection";
  motors: Partial<Record<LeLampMotorName, number>>;
}): Promise<ApiResult<LeLampMotorControlResponse>> {
  const data = await request<LeLampMotorControlResponse>("/api/lelamp/motor-control/save-pose", {
    method: "POST",
    body: JSON.stringify(payload),
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
