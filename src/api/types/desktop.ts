import type { StatusKind } from "./base";

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

export interface RemoteSshTarget {
  host: string;
  user: string;
  port: number;
  key_path?: string;
  key_name?: string;
}

export interface RemoteSshStatus {
  status: StatusKind | string;
  backend: string;
  ssh_binary: string;
  default_port: number;
  default_key_path: string;
  known_hosts_path: string;
  console_lan_ip?: string;
  console_lan_url?: string;
  saved_target?: {
    host: string;
    user: string;
    port: number;
    key_path: string;
    timeout_seconds?: number;
    saved_at?: string;
    source?: string;
  } | null;
  safety: string[];
  examples: Array<{ label: string; command: string }>;
}

export interface RemoteSshResult {
  task_id?: string;
  status: StatusKind | string;
  backend?: string;
  message?: string;
  reply?: string;
  target?: RemoteSshTarget;
  command?: string;
  exit_code?: number;
  stdout?: string;
  stderr?: string;
  duration_seconds?: number;
  installed?: boolean;
  remote?: {
    backend?: string;
    exit_code?: number;
    stdout?: string;
    stderr?: string;
    duration_seconds?: number;
  };
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

