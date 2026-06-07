import { request } from "./client";
import type {
  ApiResult,
  LeLampMotionStatusResponse,
  SceneEnvironmentResponse,
  SceneEvent,
  SceneAmbientCaptureResponse,
  SceneObserveImageResponse,
  SceneOrientedScanResponse,
  SceneRecentResponse,
  SceneSensorSnapshotResponse,
  SceneTrackingRunResponse,
  SceneWorkflowSuggestionsResponse,
  SceneWorkflowTriggerResponse,
} from "./types";

export function getSceneRecent(limit = 20): Promise<ApiResult<SceneRecentResponse>> {
  return request<SceneRecentResponse>(`/api/scene/recent?limit=${limit}`).then((data) => ({ data, source: "api" }));
}

export function getSceneWorkflowSuggestions(limit = 20): Promise<ApiResult<SceneWorkflowSuggestionsResponse>> {
  return request<SceneWorkflowSuggestionsResponse>(`/api/scene/workflow-suggestions?limit=${limit}`).then((data) => ({
    data,
    source: "api",
  }));
}

export async function buildSceneWorkflowSuggestions(events: SceneEvent[]): Promise<ApiResult<SceneWorkflowSuggestionsResponse>> {
  const data = await request<SceneWorkflowSuggestionsResponse>("/api/scene/workflow-suggestions", {
    method: "POST",
    body: JSON.stringify({ events }),
  });
  return { data, source: "api" };
}

export async function triggerSceneWorkflow(payload: {
  action: string;
  authorized: boolean;
  event?: SceneEvent | Record<string, unknown>;
  title?: string;
  ambient_lux?: number;
  participants?: string[];
}): Promise<ApiResult<SceneWorkflowTriggerResponse>> {
  const data = await request<SceneWorkflowTriggerResponse>("/api/scene/workflow/trigger", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function observeSceneImage(payload: {
  image_data_url: string;
  title?: string;
}): Promise<ApiResult<SceneObserveImageResponse>> {
  const data = await request<SceneObserveImageResponse>("/api/scene/observe-image", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function captureDeviceSceneObservation(payload: {
  title?: string;
  camera_index?: number;
  cam0_rotate_180?: boolean;
  timeout_seconds?: number;
} = {}): Promise<ApiResult<SceneObserveImageResponse>> {
  const data = await request<SceneObserveImageResponse>("/api/scene/device-observe", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function captureSceneSensorSnapshot(payload: {
  title?: string;
  include_camera?: boolean;
  include_mic?: boolean;
  include_hardware?: boolean;
  mic_seconds?: number;
  camera_index?: number;
  cam0_rotate_180?: boolean;
  lux?: number;
  people_count?: number;
  presence?: boolean;
  speech_active?: boolean;
  projector_blocked?: boolean;
  calendar_event_now?: boolean;
} = {}): Promise<ApiResult<SceneSensorSnapshotResponse>> {
  const data = await request<SceneSensorSnapshotResponse>("/api/scene/sensor-snapshot", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function captureSceneAmbientInput(payload: {
  mic_seconds?: number;
  camera_indices?: number[];
  include_cameras?: boolean;
  include_mic?: boolean;
  cam0_rotate_180?: boolean;
} = {}): Promise<ApiResult<SceneAmbientCaptureResponse>> {
  const data = await request<SceneAmbientCaptureResponse>("/api/scene/ambient-capture", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export function getLeLampMotionStatus(): Promise<ApiResult<LeLampMotionStatusResponse>> {
  return request<LeLampMotionStatusResponse>("/api/lelamp/motion/status").then((data) => ({ data, source: "api" }));
}

export async function runSceneOrientedScan(payload: {
  authorized: boolean;
  mode?: "yaw" | "multi_axis" | "pan_tilt";
  tilt_motor?: "wrist_pitch" | "base_pitch";
  yaw_delta?: number;
  pitch_delta?: number;
  view_limit?: number;
  max_step?: number;
  hold_seconds?: number;
  camera_index?: number;
  cam0_rotate_180?: boolean;
  include_mic?: boolean;
  lux?: number;
  people_count?: number;
  presence?: boolean;
  projector_blocked?: boolean;
  calendar_event_now?: boolean;
  title?: string;
}): Promise<ApiResult<SceneOrientedScanResponse>> {
  const data = await request<SceneOrientedScanResponse>("/api/scene/oriented-scan", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function runSceneTracking(payload: {
  authorized: boolean;
  camera_index?: number;
  backend?: "auto" | "face" | "hog" | "yolo";
  frames?: number;
  move?: boolean;
  max_step?: number;
  yaw_gain?: number;
  pitch_gain?: number;
  deadband?: number;
  min_hits?: number;
}): Promise<ApiResult<SceneTrackingRunResponse>> {
  const data = await request<SceneTrackingRunResponse>("/api/scene/tracking-run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function submitEnvironmentReading(payload: Record<string, unknown>): Promise<ApiResult<SceneEnvironmentResponse>> {
  const data = await request<SceneEnvironmentResponse>("/api/scene/environment", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function reportSceneEvent(payload: {
  event_type: string;
  description: string;
  confidence?: number;
}): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/scene/report", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}
