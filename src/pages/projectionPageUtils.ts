
export function metricStatus(value: Record<string, unknown> | undefined): string {
  if (!value) return "-";
  const status = String(value.status ?? "-");
  const numeric = value.value ?? value.laplacian_variance ?? value.horizontal_skew_pct ?? value.edge_density;
  return numeric === undefined || numeric === null ? status : `${status} · ${String(numeric)}`;
}

export function friendlyStatus(status: unknown) {
  const value = String(status ?? "");
  const labels: Record<string, string> = {
    ok: "正常",
    available: "可用",
    completed: "已完成",
    pending: "等待",
    running: "运行中",
    blocked: "已阻止",
    failed: "失败",
    unavailable: "不可用",
    adapter_ready: "待接入",
    backend_missing: "待接入",
    needs_config: "待配置",
  };
  return labels[value] ?? (value || "等待");
}

export function friendlyDisplayMode(mode?: string) {
  const value = String(mode ?? "");
  const labels: Record<string, string> = {
    manual: "手动",
    ambient: "环境亮度自适应",
    calibration: "校准结果",
  };
  return labels[value] ?? (value || "-");
}

export function friendlyCardMode(mode?: string) {
  const labels: Record<string, string> = {
    status: "状态通知",
    countdown: "会议倒计时",
    confirmation: "会议提示",
    action_card: "温馨提示",
  };
  return labels[String(mode ?? "")] ?? "投影卡片";
}

export function cacheBustedUrl(url: string, mtime?: number) {
  if (!url) return "";
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}v=${encodeURIComponent(String(mtime ?? Date.now()))}`;
}

export function formatProjectionTime(mtime?: number) {
  if (!mtime) return "-";
  const date = new Date(mtime * 1000);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function compactDisplayPath(value?: string) {
  const text = String(value ?? "");
  if (!text) return "-";
  const normalized = text.replace(/\\/g, "/");
  const workspaceMarker = "/workspace/";
  const workspaceIndex = normalized.lastIndexOf(workspaceMarker);
  if (workspaceIndex >= 0) return normalized.slice(workspaceIndex + 1);
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 2) return normalized;
  return `.../${parts.slice(-2).join("/")}`;
}
