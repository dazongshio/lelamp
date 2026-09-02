import type { StatusKind } from "../api/types";
import "./components.css";

interface StatusBadgeProps {
  status: StatusKind | string | boolean;
  label?: string;
  tone?: "success" | "warning" | "danger" | "primary" | "gray";
}

const labelMap: Record<string, string> = {
  ok: "正常",
  online: "在线",
  enabled: "已启用",
  success: "成功",
  completed: "已完成",
  partial: "部分完成",
  running: "运行中",
  starting: "启动中",
  stopping: "停止中",
  stopped: "已停止",
  queued: "排队中",
  pending: "等待执行",
  waiting_confirmation: "等待确认",
  warning: "警告",
  degraded: "降级",
  blocked: "已阻止",
  failed: "失败",
  error: "错误",
  unavailable: "不可用",
  adapter_ready: "待接入",
  backend_missing: "待接入",
  needs_backend: "待接入",
  needs_confirmation: "请确认",
  needs_hardware: "需硬件",
  unsupported: "不支持",
  optional: "可选增强",
  not_applicable: "不适用",
  deployment_note: "部署备注",
  draft: "草稿就绪",
  implemented: "已实现",
  available: "可用",
  needs_config: "需配置",
  empty: "暂无",
  ready: "就绪",
  idle: "空闲",
  offline: "离线",
  active: "活动中",
  inactive: "未启用",
  connected: "已连接",
  disconnected: "未连接",
  reachable: "可连接",
  configured: "已配置",
  unconfigured: "未配置",
  unknown: "未知",
  binary: "二进制文件",
};

function toneFor(status: string): string {
  if (["ok", "online", "enabled", "success", "completed", "implemented", "available"].includes(status)) return "success";
  if (["warning", "degraded", "needs_confirmation", "draft", "pending", "adapter_ready", "partial"].includes(status)) return "warning";
  if (["blocked", "error", "failed", "unavailable"].includes(status)) return "danger";
  if (["backend_missing", "needs_backend", "needs_config", "needs_hardware", "unsupported", "optional", "not_applicable", "deployment_note", "offline", "empty"].includes(status)) return "gray";
  return "primary";
}

export function StatusBadge({ status, label, tone }: StatusBadgeProps) {
  const normalized = typeof status === "boolean" ? (status ? "enabled" : "unavailable") : status;
  return (
    <span className={`status-badge status-badge--${tone ?? toneFor(String(normalized))}`}>
      <span className="status-badge__dot" />
      {label ?? labelMap[String(normalized)] ?? String(normalized)}
    </span>
  );
}
