export type StatusKind =
  | "ok"
  | "online"
  | "offline"
  | "enabled"
  | "success"
  | "available"
  | "completed"
  | "starting"
  | "stopping"
  | "stopped"
  | "queued"
  | "running"
  | "pending"
  | "waiting_confirmation"
  | "needs_confirmation"
  | "warning"
  | "degraded"
  | "blocked"
  | "failed"
  | "error"
  | "unavailable"
  | "adapter_ready"
  | "backend_missing"
  | "needs_backend"
  | "needs_config"
  | "needs_hardware"
  | "unsupported"
  | "optional"
  | "not_applicable"
  | "deployment_note"
  | "draft"
  | "empty"
  | "ready";

export interface ApiErrorPayload {
  code: string;
  message: string;
  details: Record<string, unknown>;
  status?: number;
}

export type ApiEnvelope<T> =
  | { ok: true; data: T }
  | { ok: false; error: ApiErrorPayload };

export interface ApiResult<T> {
  data: T;
  source: "api" | "mock";
}

export interface SecurityStatus {
  permission_mode: string;
  desktop_backend: string;
  workspace_dir: string;
  shared_inbox_dir: string;
  projection_dir: string;
  audit_log_path: string;
  allowed_roots: string[];
  hardware_enabled: boolean;
  meeting_mode_enabled: boolean;
  full_control_enabled?: boolean;
  token_required?: boolean;
  console_token_required?: boolean;
  memory_path?: string;
  smart_home_provider?: string;
  projection_preview_url?: string;
  cloud_ai_enabled?: boolean;
  enterprise_policy?: EnterprisePolicyStatus;
  enterprise_policy_status?: EnterprisePolicyStatus;
}

export interface EnterprisePolicyStatus {
  status: StatusKind | string;
  policy_path: string;
  policy_file_present: boolean;
  cloud_ai_enabled: boolean;
  permission_mode: string;
  desktop_backend: string;
  allowed_roots: string[];
  audit_log_path: string;
  audit_signing: {
    status: StatusKind | string;
    key_configured: boolean;
    algorithm: string;
  };
  retention: {
    audit_export_retention_days: number;
  };
  policy: Record<string, unknown>;
  enforced_controls: string[];
  local_platform?: EnterpriseLocalPlatformStatus;
}

export interface EnterpriseLocalPlatformService {
  name: string;
  status: StatusKind | string;
  purpose: string;
  endpoint?: string;
  path?: string;
}

export interface EnterpriseDataZone {
  name: string;
  classification: string;
  retention: string;
}

export interface EnterpriseLocalPlatformStatus {
  status: StatusKind | string;
  platform_dir: string;
  manifest_path: string;
  latest_bundle: string;
  services: EnterpriseLocalPlatformService[];
  data_zones: EnterpriseDataZone[];
  offline_model_registry: string;
  manifest?: Record<string, unknown>;
}

export interface EnterpriseLocalPlatformBuildResponse {
  task_id?: string;
  status: StatusKind | string;
  platform_dir: string;
  bundle_path: string;
  manifest_path: string;
  model_registry_path: string;
  compose_path: string;
  policy_template_path: string;
  services: EnterpriseLocalPlatformService[];
  data_zones: EnterpriseDataZone[];
  safety: string[];
}

export interface ServiceStatus {
  name: string;
  status: StatusKind | string;
  uptime?: string;
  note?: string;
  details?: Record<string, unknown>;
}

export interface ServicesStatusResponse {
  services: ServiceStatus[];
}

export interface ProductChecklistItem {
  area: string;
  feature: string;
  status: StatusKind | string;
  evidence: string[];
  gap: string;
  next_step: string;
}

export interface ProductChecklistResponse {
  summary: {
    total: number;
    counts: Record<string, number>;
    software_mvp_ready: boolean;
    remaining_count: number;
    deployment_note_count?: number;
  };
  areas: Record<string, ProductChecklistItem[]>;
  items: ProductChecklistItem[];
  remaining: ProductChecklistItem[];
  deployment_notes?: ProductChecklistItem[];
  readiness_summary?: Record<string, unknown>;
}

export interface TargetValidationStep {
  id: string;
  label: string;
  status: StatusKind | string;
  evidence: string[];
  details?: Record<string, unknown>;
}

export interface TargetValidationItem {
  id: string;
  area: string;
  feature: string;
  status: StatusKind | string;
  gap: string;
  steps: TargetValidationStep[];
  run_label: string;
  run_endpoint: string;
  artifacts?: Record<string, unknown>;
}

export interface TargetValidationStatusResponse {
  status: StatusKind | string;
  summary: {
    total: number;
    counts: Record<string, number>;
    completed: number;
    adapter_ready: number;
    blocked: number;
    backend_missing: number;
  };
  items: TargetValidationItem[];
  safety: string[];
}

export interface TargetValidationRunResponse {
  task_id?: string;
  status: StatusKind | string;
  report: TargetValidationItem;
  json_path: string;
  markdown_path: string;
  json_workspace_name: string;
  markdown_workspace_name: string;
}

export interface DesktopValidationImportResponse {
  task_id?: string;
  status: StatusKind | string;
  workspace_name: string;
  path: string;
  evidence: Record<string, boolean>;
  missing_evidence?: string[];
  remediation?: string[];
}

export interface SkillSpec {
  name: string;
  mode?: "sandbox" | "full_control" | string;
  description: string;
  implemented?: boolean;
  status: StatusKind | string;
  permission_notes?: string;
  input_contract?: string[];
  output_contract?: string[];
  fallback_behavior?: string;
  requires_confirmation: boolean;
}

export interface SharedFile {
  name: string;
  relative_path: string;
  workspace_name?: string;
  size: number;
  size_bytes?: number;
  size_label: string;
  sha256: string;
  uploaded_at: string;
  status: StatusKind | string;
  mime_type?: string;
  allowed_actions?: string[];
}

export interface SharedFilesResponse {
  shared_inbox: string;
  files: SharedFile[];
  items?: SharedFile[];
  total?: number;
  page?: number;
  page_size?: number;
}

export interface SharedPreviewResponse {
  status: StatusKind | string;
  workspace_name: string;
  name: string;
  size_bytes: number;
  text?: string;
  truncated?: boolean;
  download_only?: boolean;
  document_text_backend?: string;
}

export interface DocmostSpace {
  id?: string;
  name?: string;
  slug?: string;
  [key: string]: unknown;
}

export interface DocmostStatus {
  provider: "docmost" | string;
  status: StatusKind | string;
  url: string;
  default_space: string;
  configured: boolean;
  message?: string;
  workspace?: Record<string, unknown>;
  spaces?: DocmostSpace[];
  resolved_space?: DocmostSpace;
  details?: Record<string, unknown>;
}

export interface DocmostSyncResponse {
  status: StatusKind | string;
  provider: "docmost" | string;
  source_file: string;
  source_kind: string;
  extraction?: Record<string, unknown>;
  docmost_url: string;
  docmost_page_id: string;
  docmost_page_url: string;
  docmost_page_title: string;
  docmost_space_id: string;
  docmost_space_slug: string;
  task_id?: string;
}

export interface AuditEvent {
  timestamp: string;
  actor?: string;
  action: string;
  status: StatusKind | string;
  target: string;
  details: Record<string, unknown>;
  request_id?: string;
  source_ip?: string;
  permission_mode?: string;
  desktop_backend?: string;
  user?: string;
  source?: string;
  session_id?: string;
}

export interface AuditResponse {
  items: AuditEvent[];
  events?: AuditEvent[];
  total: number;
  page: number;
  page_size: number;
  path: string;
}

export interface TaskRecord {
  task_id: string;
  title: string;
  type: "document" | "meeting" | "assistant" | "projection" | "hardware" | string;
  status: StatusKind | string;
  progress: number;
  created_at: string;
  updated_at: string;
  input: Record<string, unknown>;
  output: unknown;
  error: null | Record<string, unknown>;
}

export interface DesktopTaskStep {
  index: number;
  description: string;
  requires_confirmation?: boolean;
}

export interface DesktopTask {
  id: string;
  status: StatusKind | string;
  goal: string;
  steps: DesktopTaskStep[];
  source: string;
  requires_full_control: boolean;
  created_at: string;
  updated_at: string;
  approval: Record<string, unknown>;
  execution: Record<string, unknown>;
  path?: string;
  workspace_name?: string;
}

export interface DesktopTasksResponse {
  queue_dir: string;
  tasks: DesktopTask[];
}

export interface BrowserAutomationStatus {
  status: StatusKind | string;
  backend: string;
  package_installed: boolean;
  workspace_output_dir: string;
  permission_mode: string;
  desktop_backend: string;
  headless_default: boolean;
  timeout_ms: number;
  max_steps: number;
  safety: string[];
  install_hint?: string;
  launch_probe?: Record<string, unknown>;
}

export interface BrowserAutomationResult {
  status: StatusKind | string;
  backend: string;
  task_id: string;
  goal?: string;
  message?: string;
  step_count?: number;
  report_path?: string;
  report_workspace_name?: string;
  workspace_dir?: string;
  screenshots?: Array<{ path: string; workspace_name: string }>;
  final_url?: string;
  page_title?: string;
  text_sample?: string;
  backend_status?: BrowserAutomationStatus;
  [key: string]: unknown;
}

export interface DesktopCompanionStatus {
  status: StatusKind | string;
  backend: string;
  permission_mode: string;
  queue_dir: string;
  started_at?: number | null;
  last_run?: Record<string, unknown> | null;
  safety: string[];
  message?: string;
  interval_seconds?: number;
}

export interface DesktopCompanionRunResponse {
  status: StatusKind | string;
  processed: number;
  approved_count: number;
  backend: string;
  executed: Array<Record<string, unknown>>;
  timestamp: string;
}

export interface DesktopWorkflowStatus {
  status: StatusKind | string;
  permission_mode: string;
  desktop_backend: string;
  can_execute: boolean;
  preflight?: Record<string, unknown>;
  allowed_roots: string[];
  supported_actions: string[];
  setup_endpoint?: string;
  safety: string[];
}

export interface DesktopWorkflowResult {
  task_id?: string;
  status: StatusKind | string;
  goal?: string;
  backend?: string;
  permission_mode?: string;
  authorized?: boolean;
  can_execute?: boolean;
  message?: string;
  workflow?: Record<string, unknown>;
  steps?: Array<Record<string, unknown>>;
  checks?: Array<Record<string, unknown>>;
  target_setup?: Record<string, unknown>;
  can_execute_on_this_runtime?: boolean;
  safety?: string[];
  permission?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface DesktopControlActionResult {
  task_id?: string;
  status: StatusKind | string;
  message?: string;
  action?: string;
  path?: string;
  preflight?: Record<string, unknown>;
  input_probe?: Record<string, unknown>;
  screenshot_probe?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface TaskEventsResponse {
  task_id: string;
  status: StatusKind | string;
  events: Array<Record<string, unknown>>;
  total: number;
}

export interface TaskItem {
  title: string;
  time: string;
  status: StatusKind | string;
}

export interface TasksResponse {
  items: TaskRecord[];
  tasks?: TaskRecord[];
  total: number;
}

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

export interface ProjectionCard {
  id: string;
  title: string;
  subtitle: string;
  mode: "status" | "countdown" | "confirmation" | "action_card";
  accent: "blue" | "green" | "yellow" | "red" | "purple";
  created_at: string;
  resolution: string;
  html?: string;
  path?: string;
}

export interface ProjectionLatestResponse {
  status: StatusKind | string;
  html: string;
  name?: string;
  path?: string;
  mtime?: number;
  cards?: ProjectionCard[];
}

export interface ProjectionServiceStatus {
  status: StatusKind | string;
  preview_url: string;
  display_test_mode: boolean;
  physical_projector: StatusKind | string;
  output_target?: string;
  started_at?: number | null;
  message?: string;
}

export interface ProjectionDisplayProfile {
  mode: string;
  brightness: number;
  contrast: number;
  scale: number;
  keystone_x: number;
  keystone_y: number;
  ambient_lux?: number | null;
  note?: string;
}

export interface ProjectionDisplayProfileResponse {
  status: StatusKind | string;
  profile: ProjectionDisplayProfile;
  path: string;
  preview_url: string;
  display_test_mode?: boolean;
  physical_projector?: StatusKind | string;
  message?: string;
}

export interface ProjectionCalibrationResponse {
  task_id?: string;
  status: StatusKind | string;
  path?: string;
  mode?: string;
  target?: string;
  pattern?: string;
  capture_path?: string;
  analysis_path?: string;
  report_path?: string;
  rectangle?: Record<string, unknown>;
  keystone?: Record<string, unknown>;
  brightness?: Record<string, unknown>;
  contrast?: Record<string, unknown>;
  focus?: Record<string, unknown>;
  obstruction?: Record<string, unknown>;
  recommendations?: string[];
  message?: string;
  [key: string]: unknown;
}

export interface PptPageSummaryResponse {
  task_id?: string;
  status: StatusKind | string;
  summary?: string;
  summary_path?: string;
  screenshot_path?: string;
  source_workspace_name?: string;
  source_path?: string;
  slide_index?: number;
  slide_count?: number;
  current_slide?: ProjectionPptxSlide;
  projection_path?: string;
  projection?: Record<string, unknown> | null;
  provider?: string;
  model?: string;
  message?: string;
}

export interface ProjectionMarkdownFileResponse {
  task_id?: string;
  status: StatusKind | string;
  source_workspace_name: string;
  source_path?: string;
  path?: string;
  projection_path?: string;
  preview_url?: string;
  mode?: string;
  chars?: number;
  message?: string;
}

export interface ProjectionPptxSlide {
  index: number;
  title: string;
  text: string;
  chars?: number;
  truncated?: boolean;
}

export interface ProjectionPptxSessionResponse {
  task_id?: string;
  status: StatusKind | string;
  title: string;
  source_workspace_name: string;
  source_path?: string;
  slide_index: number;
  slide_count: number;
  current_slide?: ProjectionPptxSlide;
  slides?: ProjectionPptxSlide[];
  path?: string;
  projection_path?: string;
  preview_url?: string;
  mode?: string;
  message?: string;
}

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
  realtime: Record<string, unknown>;
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
