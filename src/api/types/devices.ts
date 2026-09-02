import type { StatusKind } from "./base";
import type { AssistantMessageResponse, VoiceAssistantProcessStatus, VoiceRealtimeStatus } from "./assistant";

export interface MobileBridgeStatus {
  status: StatusKind | string;
  provider: string;
  configured: boolean;
  device_id: string;
  shared_secret_configured: boolean;
  capabilities: string[];
  safety: string[];
}

export interface MobileBridgeRequestResponse {
  task_id?: string;
  status: StatusKind | string;
  request?: string;
  provider?: string;
  parsed?: Record<string, unknown>;
  message?: string;
  configure?: string[];
  safety?: string;
  response?: string;
  http_status?: number;
  signed?: boolean;
  [key: string]: unknown;
}

export interface SmartHomeStatus {
  status: StatusKind | string;
  provider: string;
  configured: boolean;
  home_assistant_configured: boolean;
  webhook_configured: boolean;
  known_entities: string[];
  capabilities: string[];
}

export interface SmartHomeControlResponse {
  task_id?: string;
  status: StatusKind | string;
  command?: string;
  provider?: string;
  parsed?: Record<string, unknown>;
  configure?: Record<string, string[]>;
  response?: string;
  http_status?: number;
  service?: string;
  data?: Record<string, unknown>;
  reason?: string;
  error?: string;
  [key: string]: unknown;
}

export interface VoiceStatus {
  status: StatusKind | string;
  wake_word: Record<string, unknown>;
  vad: Record<string, unknown>;
  asr: Record<string, unknown>;
  tts: Record<string, unknown>;
  realtime: VoiceRealtimeStatus;
  assistant_process?: VoiceAssistantProcessStatus;
  conversation?: Record<string, unknown>;
  mic: {
    status: StatusKind | string;
    configured_device: string;
    details: Record<string, unknown>;
  };
  speaker: {
    status: StatusKind | string;
    configured_device: string;
    details: Record<string, unknown>;
  };
  safety: string[];
}

export interface VoiceCaptureResponse {
  status: StatusKind | string;
  transcript?: string;
  capture?: Record<string, unknown>;
  assistant?: AssistantMessageResponse;
  message?: string;
}

export interface LeLampVoiceCommandResponse {
  handled: boolean;
  text: string;
  status: StatusKind | string;
  reply?: string;
  command?: {
    action?: string;
    label?: string;
    reply?: string;
    rgb?: number[] | null;
    recording?: string | null;
    [key: string]: unknown;
  };
  hardware_result?: string;
  details?: Record<string, unknown>;
  pid?: number;
  log?: string;
  message?: string;
  [key: string]: unknown;
}

export type LeLampMotorName = "base_yaw" | "base_pitch" | "elbow_pitch" | "wrist_roll" | "wrist_pitch";

export interface LeLampMotorControlResponse {
  status: StatusKind | string;
  hardware_enabled?: boolean;
  port?: string;
  lamp_id?: string;
  motors?: LeLampMotorName[];
  pose?: Partial<Record<LeLampMotorName, number>>;
  saved_poses?: {
    default?: Partial<Record<LeLampMotorName, number>>;
    scan?: Partial<Record<LeLampMotorName, number>>;
    projection?: Partial<Record<LeLampMotorName, number>>;
  };
  pose_readable?: boolean;
  before?: Partial<Record<LeLampMotorName, number>>;
  target?: Partial<Record<LeLampMotorName, number>>;
  actual?: Partial<Record<LeLampMotorName, number>>;
  errors?: Partial<Record<LeLampMotorName, number>>;
  max_error?: number;
  error?: string;
  duration_ms?: number;
}

export interface VoiceConversationSession {
  session_id: string;
  status: StatusKind | string;
  wake_word: string;
  started_at?: string;
  updated_at?: string;
  stopped_at?: string;
  turn_count: number;
  turns: Array<Record<string, unknown>>;
  memory_hits?: Array<Record<string, unknown>>;
  safety?: string[];
}

export interface VoiceConversationResponse {
  status: StatusKind | string;
  session?: VoiceConversationSession | null;
  turn?: Record<string, unknown>;
  session_id?: string;
  wake_word?: string;
  message?: string;
  active_sessions?: number;
  [key: string]: unknown;
}
