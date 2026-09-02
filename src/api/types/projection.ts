import type { StatusKind } from "./base";

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
  projector_output?: string;
  started_at?: number | null;
  kiosk_running?: boolean;
  kiosk_started_at?: number | null;
  kiosk_pid?: number | null;
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
  projector_output?: string;
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

