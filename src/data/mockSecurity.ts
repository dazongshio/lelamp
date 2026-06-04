import type { SecurityStatus, ServiceStatus } from "../api/types";

export const mockSecurity: SecurityStatus = {
  permission_mode: "sandbox + audit_only",
  workspace_dir: "/workspace",
  allowed_roots: ["/workspace", "/workspace/shared_inbox"],
  audit_log_path: "/var/log/lelamp/audit.log",
  projection_dir: "/workspace/projection",
  shared_inbox_dir: "/workspace/shared_inbox",
  memory_path: "/workspace/memory/local.json",
  desktop_backend: "OpenClaw (Xpra)",
  hardware_enabled: true,
  meeting_mode_enabled: true,
  smart_home_provider: "disabled",
  console_token_required: true,
  projection_preview_url: "http://192.168.1.88:8080/projection",
};

export const mockServices: ServiceStatus[] = [
  { name: "LeLamp Core", status: "online", uptime: "12d 04h 21m" },
  { name: "OpenClaw (Xpra)", status: "online", uptime: "12d 04h 21m" },
  { name: "File Watcher", status: "online", uptime: "12d 04h 21m" },
  { name: "Audit Logger", status: "online", uptime: "12d 04h 21m" },
  { name: "Assistant Engine", status: "online", uptime: "12d 04h 21m" },
  { name: "Hardware Monitor", status: "online", uptime: "12d 04h 21m" },
];

export const networkInfo = [
  ["当前 IP", "192.168.1.88"],
  ["访问 URL", "http://192.168.1.88:8080"],
  ["局域网掩码", "255.255.255.0"],
  ["网关", "192.168.1.1"],
  ["DNS", "192.168.1.1"],
];

export const hardwareMetrics = [
  ["CPU 温度", "46.2 °C", "ok"],
  ["CPU 使用率", "18%", "ok"],
  ["内存使用率", "42%", "ok"],
  ["磁盘使用率 (SD)", "38% (28GB / 58GB)", "ok"],
  ["风扇状态", "正常 (2320 RPM)", "ok"],
  ["供电状态", "5.05V / 2.1A", "ok"],
];
