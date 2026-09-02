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

