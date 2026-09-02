import type { StatusKind } from "./base";
import type { AuditEvent } from "./content";

export interface HardwareDevice {
  key: string;
  label: string;
  status: StatusKind | string;
  note: string;
  metric?: string;
}

export interface HardwareStatus {
  hardware_enabled: boolean;
  devices?: Record<string, { status: StatusKind | string; details: Record<string, unknown> }>;
  sensors?: {
    cpu_temp?: number | null;
    cpu_usage?: number | null;
    memory_usage?: number | null;
    disk_usage?: number | null;
    power_state?: StatusKind | string;
    throttled?: string | null;
  };
  events?: AuditEvent[];
  camera?: { status: string; note: string };
  screen_context?: { status: string; note: string };
  lelamp?: Record<string, unknown>;
  smart_home?: Record<string, unknown>;
  scanned_at?: string;
  scan?: {
    status: StatusKind | string;
    summary?: Record<string, number>;
    notes?: string[];
  };
  probes?: Record<string, unknown>;
}

export interface LeLampStateResponse {
  status: StatusKind | string;
  state: string;
  cue?: Record<string, unknown>;
  hardware_enabled?: boolean;
}

export interface HardwareTestResponse {
  status: StatusKind | string;
  test: string;
  result: Record<string, unknown>;
}

export interface SceneEvent {
  event_type: string;
  description: string;
  confidence: string | number;
  suggestion: string;
}

export interface SceneWorkflowSuggestion {
  action: string;
  title: string;
  description: string;
  trigger: string;
  confidence: string | number;
  category: string;
  safe_default: string;
  requires_confirmation: boolean;
  metadata?: Record<string, unknown>;
}

export interface SceneRecentResponse {
  status: StatusKind | string;
  events: SceneEvent[];
  total: number;
}

export interface SceneWorkflowSuggestionsResponse {
  status: StatusKind | string;
  version?: string;
  source: string;
  events: SceneEvent[];
  suggestions: SceneWorkflowSuggestion[];
  total: number;
  safety: string[];
}

export interface SceneWorkflowTriggerResponse {
  task_id?: string;
  status: StatusKind | string;
  action: string;
  message: string;
  safety?: string;
  desktop_task?: Record<string, unknown>;
  reminder?: Record<string, unknown>;
  projection?: Record<string, unknown>;
  meeting?: Record<string, unknown>;
  display_profile?: Record<string, unknown>;
  preview_url?: string;
  next_url?: string;
  scan_request?: Record<string, unknown>;
}

export interface SceneObserveImageResponse {
  task_id?: string;
  status: StatusKind | string;
  source: string;
  image_path: string;
  workspace_name: string;
  camera_index?: number;
  rotation_degrees?: number;
  cam0_rotate_180?: boolean;
  analysis: Record<string, unknown>;
  events: SceneEvent[];
  suggestions?: SceneWorkflowSuggestion[];
}

export interface SceneSensorSnapshotResponse {
  task_id?: string;
  status: StatusKind | string;
  source: string;
  reading: Record<string, unknown>;
  reading_sources: Record<string, unknown>;
  camera: Record<string, unknown>;
  microphone: Record<string, unknown>;
  hardware: Record<string, unknown>;
  environment: Record<string, unknown>;
  events: SceneEvent[];
  event_count: number;
  suggestions?: SceneWorkflowSuggestion[];
  safety?: string[];
}

export interface SceneAmbientCamera {
  camera_index: number;
  status: StatusKind | string;
  rotation_degrees?: number;
  cam0_rotate_180?: boolean;
  source?: string;
  workspace_name?: string;
  image_url?: string;
  path?: string;
  events?: SceneEvent[];
  message?: string;
  analysis?: Record<string, unknown>;
}

export interface SceneAmbientTranscript {
  channel: "left" | "right" | "mono" | string;
  label: string;
  status: StatusKind | string;
  text: string;
  audio_workspace_name?: string;
  rms?: number;
  peak?: number;
  duration_seconds?: number;
  message?: string;
}

export interface SceneAmbientCaptureResponse {
  task_id?: string;
  status: StatusKind | string;
  source: string;
  include_cameras?: boolean;
  include_mic?: boolean;
  camera_count: number;
  cameras: SceneAmbientCamera[];
  microphone: Record<string, unknown>;
  transcripts: SceneAmbientTranscript[];
  safety?: string[];
}

export interface LeLampMotionStatusResponse {
  status: StatusKind | string;
  hardware_enabled: boolean;
  port: string;
  lamp_id: string;
  serial_detected: boolean;
  serial_candidates: string[];
  configured_port_exists: boolean;
  pose_readable: boolean;
  pose: Record<string, unknown>;
  pose_status?: StatusKind | string;
  pose_error?: string;
  read_duration_ms?: number;
  message?: string;
  safety?: string[];
}

export interface SceneOrientedScanView {
  index: number;
  label?: string;
  requested_yaw_offset: number;
  requested_pitch_offset?: number;
  actual_yaw_offset?: number;
  actual_pitch_offset?: number;
  movement_trace?: Array<Record<string, unknown>>;
  target_pose?: Record<string, unknown>;
  actual_pose?: Record<string, unknown>;
  snapshot?: Record<string, unknown>;
  events: SceneEvent[];
}

export interface SceneOrientedScanResponse {
  task_id?: string;
  status: StatusKind | string;
  source: string;
  message: string;
  preflight: LeLampMotionStatusResponse | Record<string, unknown>;
  scan?: Record<string, unknown>;
  views: SceneOrientedScanView[];
  events: SceneEvent[];
  event_count?: number;
  suggestions?: SceneWorkflowSuggestion[];
  safety?: string[];
  error?: string;
  duration_ms?: number;
}

export interface SceneTrackingRunResponse {
  task_id?: string;
  status: StatusKind | string;
  source: string;
  message: string;
  preflight?: LeLampMotionStatusResponse | Record<string, unknown>;
  request?: Record<string, unknown>;
  frames: Array<Record<string, unknown>>;
  target_count: number;
  move_count: number;
  return_code?: number;
  stderr_tail?: string;
  restored_stream?: Record<string, unknown>;
  duration_ms?: number;
}

export interface SceneEnvironmentResponse {
  task_id?: string;
  status: StatusKind | string;
  reading: Record<string, unknown>;
  events: SceneEvent[];
  event_count: number;
  suggestions?: SceneWorkflowSuggestion[];
}
