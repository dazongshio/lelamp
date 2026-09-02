import type { StatusKind } from "./base";

export interface AssistantMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  time: string;
  status?: StatusKind | string;
  attachment?: string;
  notificationKey?: string;
}

export interface AssistantSkillCall {
  name: string;
  status: StatusKind | string;
}

export interface AssistantConfirmation {
  confirmation_id: string;
  risk_level: "low" | "medium" | "high" | string;
  summary: string;
}

export interface AssistantManualResponse {
  message_id: string;
  detected_intent: string;
  skills_to_call: AssistantSkillCall[];
  requires_confirmation: boolean;
  confirmation: AssistantConfirmation | null;
  result: {
    status: StatusKind | string;
    summary: string;
    display_text?: string;
    details?: {
      blocked?: boolean;
      intent?: string;
      route_summary?: string;
      tool?: string;
      tool_args?: Record<string, unknown>;
      tool_result?: Record<string, unknown>;
      event_log?: string;
    };
    outputs: Array<{ path: string; type: string }>;
  };
  task_id: string;
  raw?: unknown;
}

export interface QwenRealtimeVoiceOption {
  voice: string;
  label: string;
  description?: string;
  languages?: string;
}

export interface VoiceRealtimeStatus {
  status: StatusKind | string;
  provider: string;
  model: string;
  voice: string;
  default_voice?: string;
  current_voice_supported?: boolean;
  voices?: QwenRealtimeVoiceOption[];
  voice_count?: number;
  doc_url?: string;
  turn_detection?: string;
  transcription_model?: string;
  [key: string]: unknown;
}

export interface VoiceRealtimeVoicesResponse extends VoiceRealtimeStatus {
  env_key?: string;
  env_file?: string;
}

export interface VoiceRealtimeVoiceUpdateResponse {
  status: StatusKind | string;
  message: string;
  voice: string;
  model: string;
  env_file: string;
  realtime: VoiceRealtimeVoicesResponse;
}

export interface VoiceAssistantProcessStatus {
  status: StatusKind | string;
  running: boolean;
  pid?: number | null;
  started_at?: number | null;
  model?: string;
  voice?: string;
  mic_device?: string;
  speaker_device?: string;
  log?: string;
  latency_log?: string;
  message?: string;
  [key: string]: unknown;
}

export interface AssistantProviderStatus {
  foreground_provider: string;
  qwen_omni: {
    status: StatusKind | string;
    model: string;
    url?: string;
    text_input: boolean;
    pi_mic_input: boolean;
    pi_mic_status: StatusKind | string;
    browser_mic_input: boolean;
    transcription_model: string;
    voice: string;
    tts: {
      provider: string;
      model?: string;
      voice?: string;
      status: StatusKind | string;
      mode?: string;
    };
  };
  openclaw: {
    status: StatusKind | string;
    router?: string;
    executor?: string;
    permission_mode?: string;
    desktop_backend?: string;
  };
  input: {
    text: StatusKind | string;
    pi_mic: StatusKind | string;
    browser_mic: StatusKind | string;
  };
  safety?: Record<string, unknown>;
}

export interface AssistantMessageResponse {
  session_id: string;
  message_id: string;
  route: {
    kind: "chat" | "task" | string;
    intent: string;
    requires_openclaw: boolean;
    requires_confirmation: boolean;
    summary?: string;
    skill?: string;
  };
  assistant_ack?: {
    text: string;
    speak: boolean;
  };
  assistant_message?: {
    text: string;
    streamed?: boolean;
    speak?: boolean;
    provider?: string;
    provider_status?: StatusKind | string;
    speech?: Record<string, unknown>;
  };
  task?: {
    task_id: string;
    status: StatusKind | string;
    monitor_url: string;
    events_url?: string;
  };
}

export interface AssistantNotification {
  id: string;
  event: string;
  timestamp: string;
  text: string;
  status: StatusKind | string;
  attachment?: string;
  payload?: Record<string, unknown>;
}

export interface AssistantNotificationsResponse {
  status: StatusKind | string;
  items: AssistantNotification[];
  total: number;
  since_found?: boolean;
}

export interface AssistantPiVoiceResponse {
  status: StatusKind | string;
  transcript: string;
  capture?: Record<string, unknown>;
  message?: string;
  assistant?: AssistantMessageResponse;
}

export interface CameraStreamStatus {
  status: StatusKind | string;
  preview_url: string;
  snapshot_url: string;
  stream_url: string;
  browser_preview_url?: string;
  browser_snapshot_url?: string;
  browser_stream_url?: string;
  camera_index: number;
  started_at?: number | null;
  always_on: boolean;
  details?: Record<string, unknown>;
  message?: string;
}

