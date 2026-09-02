import type { StatusKind } from "./base";
import type { SharedFile } from "./content";

export interface DocumentAdapter {
  name: string;
  status: StatusKind | string;
  backend: string;
  endpoint: string;
  lastHeartbeat: string;
  note: string;
}

export interface DocumentResult {
  task_id?: string;
  status: StatusKind | string;
  summary?: string;
  metadata?: Record<string, unknown>;
  risks?: Array<Record<string, unknown>>;
  outputs?: Array<{ path: string; type: string }>;
  adapter_status?: Record<string, StatusKind | string>;
  [key: string]: unknown;
}

export interface ScanResult {
  status: StatusKind | string;
  image?: string;
  source_image_path?: string;
  source_workspace_name?: string;
  ocr_input?: string;
  document_type?: string;
  summary?: string;
  text_path?: string;
  summary_path?: string;
  structure_path?: string;
  table_paths?: string[];
  business_card_path?: string;
  contract_path?: string;
  tables?: Array<{ title?: string; headers?: string[]; rows?: string[][] }>;
  entities?: Record<string, unknown>;
  risks?: Array<string | Record<string, unknown>>;
  business_card?: Record<string, unknown>;
  contract?: Record<string, unknown>;
  quality_notes?: string[];
  enhancement?: Record<string, unknown>;
  ocr?: Record<string, unknown>;
  message?: string;
  [key: string]: unknown;
}

export interface MeetingStep {
  id: number;
  title: string;
  status: StatusKind | string;
  input: string;
  understanding: string;
  result: string;
  confirmation: string;
  outputPath: string;
  taskId?: string;
}

export interface MeetingJob {
  job_id: string;
  status: StatusKind | string;
  title?: string;
  meeting_id?: string;
  transcript?: string;
  updated_at?: string;
  steps: Array<{
    name: string;
    status: StatusKind | string;
    input_file: string;
    system_understanding: string;
    ai_result: string;
    confirmation: Record<string, unknown>;
    output_path: string;
    output?: Record<string, unknown>;
    task_id?: string;
    updated_at?: string;
  }>;
}

export interface MeetingJobsResponse {
  items: MeetingJob[];
  total: number;
}

export interface TingwuProviderStatus {
  provider: "tongyi_tingwu" | string;
  status: StatusKind | string;
  configured: boolean;
  mock?: boolean;
  api_key_configured: boolean;
  app_id_configured: boolean;
  credential_diagnostics?: {
    api_key_kind?: string;
    app_id_kind?: string;
  };
  http_url?: string;
  ws_url?: string;
  configured_mic_device?: string;
  mic_device: string;
  selected_mic_device?: string;
  mic_status?: StatusKind | string;
  mic_probe?: Record<string, unknown>;
  sample_rate: number;
  audio_format: string;
  transcription_model?: string;
  analysis_model?: string;
  translation_enabled?: boolean;
  translation_target_lang?: string[];
  phrase_id_configured?: boolean;
  hot_words_configured?: boolean;
  audio_channel_mode?: string;
  capabilities?: Record<string, boolean>;
  language_hints: string[];
  active_meeting_id?: string | null;
  active_count?: number;
  message?: string;
}

export interface MeetingProviderStatus {
  status: StatusKind | string;
  primary_provider: "tongyi_tingwu" | string;
  providers: {
    tongyi_tingwu: TingwuProviderStatus;
    [key: string]: TingwuProviderStatus;
  };
}

export interface MeetingProviderAcceptanceItem {
  id: string;
  title: string;
  status: StatusKind | string;
  how_to_test: string;
  evidence?: string[];
  links?: Array<{ label: string; url: string }>;
  env?: Record<string, string>;
  cwd?: string;
  command?: string[];
  audit_command?: string[];
}

export interface MeetingProviderPreflight {
  status: StatusKind | string;
  provider: "tongyi_tingwu" | string;
  ready: boolean;
  checks: Record<string, boolean | string | number | null>;
  next_actions?: Array<{
    id: string;
    status: StatusKind | string;
    message: string;
    credential_diagnostics?: {
      api_key_kind?: string;
      app_id_kind?: string;
    };
    links?: Array<{ label: string; url: string }>;
    env?: Record<string, string>;
    cwd?: string;
    command?: string[];
    audit_command?: string[];
  }>;
  acceptance_checklist?: MeetingProviderAcceptanceItem[];
  provider_status: TingwuProviderStatus;
  credential_diagnostics?: {
    api_key_kind?: string;
    app_id_kind?: string;
  };
  capture_probe: Record<string, unknown>;
  capture_seconds: number;
  selected_mic_device: string;
  sample_rate: number;
  audio_format: string;
}

export interface MeetingModeStatus {
  status: StatusKind | string;
  meeting_mode_enabled: boolean;
  active_title?: string | null;
  participants: string[];
  turn_count: number;
}

export interface RealtimeTranscriptItem {
  timestamp: string;
  speaker: string;
  text: string;
  final: boolean;
}

export interface MeetingQaCitation extends RealtimeTranscriptItem {
  id: string;
}

export interface MeetingQaResponse {
  status: string;
  provider: "local_codex" | string;
  meeting_id: string;
  question: string;
  answer: string;
  insufficient_evidence: boolean;
  citations: MeetingQaCitation[];
}

export interface MeetingRealtimeStatus {
  provider: "tongyi_tingwu" | string;
  status: StatusKind | string;
  provider_status?: StatusKind | string;
  openclaw_status?: StatusKind | string;
  content_status?: StatusKind | string;
  meeting_id?: string | null;
  title?: string;
  participants?: string[];
  task_id?: string;
  started_at?: string | null;
  stopped_at?: string | null;
  transcript?: RealtimeTranscriptItem[];
  partial_text?: string;
  realtime_transcript?: string;
  final_count?: number;
  audio_seconds?: number;
  audio_bytes?: number;
  websocket_audio_frames?: number;
  audio_rms?: number;
  audio_peak?: number;
  tingwu_http_operations?: Array<Record<string, unknown>>;
  task_payload?: Record<string, unknown>;
  output_dir?: string;
  transcript_path?: string;
  audio_path?: string;
  minutes_path?: string;
  manifest_path?: string;
  error?: string;
  provider_error?: string;
  openclaw_error?: string;
  task_id_web?: string;
  provider_task_id?: string;
  job?: MeetingJob;
  outputs?: Array<{ path: string; type: string }>;
  minutes?: Record<string, unknown>;
  followup?: Record<string, unknown> | null;
  session?: Record<string, unknown>;
}

export interface MeetingRealtimeEventsResponse {
  status: StatusKind | string;
  meeting_id: string;
  events: Array<Record<string, unknown>>;
  total: number;
}

export interface MeetingInsightsResponse {
  status: string;
  meeting_id: string;
  chapters?: unknown;
  chapters_markdown: string;
  key_information?: unknown;
  key_information_markdown: string;
  highlights: Array<{ index: number; timestamp?: string; speaker?: string; text?: string; created_at?: string }>;
}

export interface MeetingLocalRealtimeResponse {
  task_id?: string;
  status: StatusKind | string;
  meeting_mode_enabled: boolean;
  active_title?: string | null;
  participants: string[];
  turn_count: number;
  speaker_counts: Record<string, number>;
  char_counts: Record<string, number>;
  transcript: RealtimeTranscriptItem[];
  turn?: RealtimeTranscriptItem;
  transcript_path?: string;
  workspace_name?: string;
  source?: string;
}

export interface MeetingTextImportResponse {
  status: StatusKind | string;
  source: string;
  file: SharedFile;
  job: MeetingJob;
  participants: string[];
}
