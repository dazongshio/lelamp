import { FileText, Folder, HardDrive, Monitor, Shield, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { getAuditRecent } from "../api/audit";
import { getHardwareStatus } from "../api/hardware";
import { getSecurity } from "../api/security";
import { getServicesStatus } from "../api/services";
import { getSharedFiles } from "../api/shared";
import { getRecentTasks } from "../api/tasks";
import type { AuditEvent, HardwareStatus, SecurityStatus, ServiceStatus, SharedFile, TaskRecord } from "../api/types";
import { Card, InfoCard } from "../components/Card";
import { DataTable, type Column } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { ProgressBar } from "../components/ProgressBar";
import { SkillChip } from "../components/SkillChip";
import { StatusBadge } from "../components/StatusBadge";
import { mockSecurity } from "../data/mockSecurity";
import { usePolling } from "../hooks/usePolling";
import "./pages.css";

export function DashboardPage() {
  const [security, setSecurity] = useState<SecurityStatus>(mockSecurity);
  const [files, setFiles] = useState<SharedFile[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [hardware, setHardware] = useState<HardwareStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [securityResult, filesResult, auditResult, taskResult, serviceResult, hardwareResult] = await Promise.all([
        getSecurity(),
        getSharedFiles({ page_size: 10 }),
        getAuditRecent({ limit: 20 }),
        getRecentTasks(10),
        getServicesStatus(),
        getHardwareStatus(),
      ]);
      setSecurity(securityResult.data);
      setFiles(filesResult.data.files ?? []);
      setEvents(auditResult.data.events ?? auditResult.data.items ?? []);
      setTasks(taskResult.data.tasks ?? taskResult.data.items ?? []);
      setServices(serviceResult.data.services);
      setHardware(hardwareResult.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  usePolling(load, 15000, true);

  const auditColumns: Column<AuditEvent>[] = [
    { key: "action", title: "动作", render: (row) => <strong>{friendlyAuditAction(row.action)}</strong> },
    { key: "status", title: "状态", render: (row) => <StatusBadge status={row.status} /> },
    { key: "target", title: "对象", render: (row) => <span className="small">{compactDisplayPath(row.target)}</span> },
    { key: "time", title: "时间", render: (row) => row.timestamp.slice(11, 19), width: "90px" },
  ];

  return (
    <>
      <PageHeader
        title="工作台"
        description="会议、文件、投影和设备状态总览"
        actions={<button className="ghost-button" onClick={() => void load()}>{loading ? "刷新中" : "刷新"}</button>}
      />
      <div className="page-grid">
        {error && <div className="danger-panel">加载失败：{error}</div>}
        <div className="grid-4">
          <InfoCard icon={<Shield size={20} />} label="安全模式" value={security.permission_mode === "sandbox" ? "沙箱模式" : "需确认"} note="默认只处理用户授权内容" status={<StatusBadge status={security.permission_mode === "sandbox" ? "enabled" : "warning"} />} />
          <InfoCard icon={<Monitor size={20} />} label="桌面代理" value={security.desktop_backend === "audit_only" ? "仅审计预览" : "待授权"} note="全权控制需要单独确认" status={<StatusBadge status={security.desktop_backend === "audit_only" ? "warning" : "blocked"} />} />
          <InfoCard icon={<Folder size={20} />} label="文件工作区" value="受限访问" note="只处理拖入或上传的文件" status={<StatusBadge status="enabled" label="安全" />} />
          <InfoCard icon={<Monitor size={20} />} label="投影" value={security.projection_preview_url ? "预览可用" : "待连接"} note="PPT/Markdown 可进入演示模式" status={<StatusBadge status={security.projection_preview_url ? "available" : "pending"} />} />
          <InfoCard icon={<FileText size={20} />} label="操作审计" value="已开启" note="关键操作会留痕" status={<StatusBadge status="enabled" />} />
          <InfoCard icon={<HardDrive size={20} />} label="硬件模块" value={security.hardware_enabled ? "已启用" : "未启用"} note="摄像头、麦克风、投影状态" status={<StatusBadge status={security.hardware_enabled} />} />
          <InfoCard icon={<Users size={20} />} label="会议理解" value={security.meeting_mode_enabled ? "已开启" : "手动开启"} note="默认不解析投影内容" status={<StatusBadge status={security.meeting_mode_enabled} />} />
          <InfoCard icon={<Folder size={20} />} label="最近文件" value={`${files.length} 个`} note="来自用户工作区" status={<StatusBadge status={files.length ? "available" : "pending"} />} />
        </div>

        <div className="grid-3">
          <Card title="服务运行状态">
            <div className="list-rows">
              {services.map((service) => (
                <div className="row-between" key={service.name}>
                  <span>{friendlyServiceName(service.name)}</span>
                  <div className="row">
                    <StatusBadge status={service.status} />
                    <span className="small muted">{service.uptime ? `运行 ${service.uptime}` : service.note ?? ""}</span>
                  </div>
                </div>
              ))}
              {!services.length && <span className="small muted">暂无服务状态。</span>}
            </div>
          </Card>
          <Card title="快速开始">
            <div className="list-rows">
              <div className="row-between"><span className="muted">导入资料</span><strong>拖入文件或粘贴会议文本</strong></div>
              <div className="row-between"><span className="muted">会议记录</span><strong>开启会议模式后再采集</strong></div>
              <div className="row-between"><span className="muted">投影演示</span><strong>PPT/Markdown 可预览投屏</strong></div>
              <div className="row-between"><span className="muted">安全边界</span><strong>默认沙箱，不自动解析屏幕</strong></div>
            </div>
          </Card>
          <Card title="硬件状态" action={<StatusBadge status="ok" label="正常" />}>
            <div className="list-rows">
              <MetricRow label="CPU 温度" value={formatSensor(hardware?.sensors?.cpu_temp, "°C")} />
              <MetricRow label="CPU 使用率" value={formatPercent(hardware?.sensors?.cpu_usage)} progress={percentNumber(hardware?.sensors?.cpu_usage)} />
              <MetricRow label="内存使用率" value={formatPercent(hardware?.sensors?.memory_usage)} progress={percentNumber(hardware?.sensors?.memory_usage)} />
              <MetricRow label="磁盘使用率" value={formatPercent(hardware?.sensors?.disk_usage)} progress={percentNumber(hardware?.sensors?.disk_usage)} />
              <MetricRow label="状态灯" value={friendlyDeviceStatus(hardware?.devices?.rgb?.status)} status={hardware?.devices?.rgb?.status ?? "adapter_ready"} />
              <MetricRow label="投影状态" value={friendlyDeviceStatus(hardware?.devices?.projection?.status)} status={hardware?.devices?.projection?.status ?? "adapter_ready"} />
            </div>
          </Card>
        </div>

        <div className="grid-3">
          <Card title="近期任务">
            <div className="list-rows">
              {tasks.map((task) => (
                <div className="row-between" key={task.task_id}>
                  <span>{task.title}</span>
                  <span className="small muted">{task.updated_at.slice(11, 19)}</span>
                  <StatusBadge status={task.status} />
                </div>
              ))}
              {!tasks.length && <span className="small muted">暂无任务。上传文件或触发文档/会议流程后会出现。</span>}
            </div>
          </Card>
          <Card title="最近文件">
            <div className="list-rows">
              {files.slice(0, 5).map((file) => (
                <div className="row-between" key={file.relative_path}>
                  <span>{file.name}</span>
                  <span className="small muted">{file.size_label}</span>
                </div>
              ))}
              {!files.length && <span className="small muted">文件工作区还没有内容。</span>}
            </div>
          </Card>
          <Card title="最近审计">
            <DataTable rows={events.slice(0, 5)} columns={auditColumns} rowKey={(row, index) => `${row.timestamp}-${index}`} />
            {!events.length && <span className="small muted">审计日志为空或尚未创建。</span>}
          </Card>
        </div>

        <Card title="安全态势总览" subtitle="本地 AI 办公终端安全边界始终可见">
          <div className="security-summary">
            <SkillChip>沙箱模式</SkillChip>
            <SkillChip>操作留痕</SkillChip>
            <SkillChip muted>全权模式未开启</SkillChip>
            <span>文件、会议和投影内容都需要用户主动导入或授权后才会被处理。</span>
          </div>
          <details className="advanced-panel">
            <summary>系统诊断</summary>
            <div className="definition-grid">
              <span>工作区</span><strong>{compactDisplayPath(security.workspace_dir)}</strong>
              <span>上传入口</span><strong>{compactDisplayPath(security.shared_inbox_dir)}</strong>
              <span>投影目录</span><strong>{compactDisplayPath(security.projection_dir)}</strong>
              <span>审计日志</span><strong>{compactDisplayPath(security.audit_log_path)}</strong>
              <span>访问范围</span><strong>{security.allowed_roots.length} 个授权目录</strong>
            </div>
          </details>
        </Card>
      </div>
    </>
  );
}

function MetricRow({ label, value, progress, status = "ok" }: { label: string; value: string; progress?: number; status?: string }) {
  return (
    <div className="row-between">
      <span>{label}</span>
      <div className="row metric-row">
        <strong>{value}</strong>
        {typeof progress === "number" && <ProgressBar value={progress} />}
        <StatusBadge status={status} label={status === "ok" ? "正常" : undefined} />
      </div>
    </div>
  );
}

function formatSensor(value: number | null | undefined, suffix: string) {
  return typeof value === "number" ? `${value} ${suffix}` : "待检测";
}

function percentNumber(value: number | null | undefined) {
  if (typeof value !== "number") return undefined;
  return value <= 1 ? Math.round(value * 100) : Math.round(value);
}

function formatPercent(value: number | null | undefined) {
  const percent = percentNumber(value);
  return typeof percent === "number" ? `${percent}%` : "待检测";
}

function friendlyServiceName(name: string) {
  const labels: Record<string, string> = {
    api: "后端服务",
    frontend: "Web 界面",
    meeting: "会议助手",
    projection: "投影服务",
    hardware: "硬件服务",
    audit: "审计服务",
  };
  return labels[name] ?? name.replace(/[_-]+/g, " ");
}

function friendlyAuditAction(action: string) {
  return action.replace(/[_-]+/g, " ");
}

function friendlyDeviceStatus(status?: string) {
  if (!status || status === "adapter_ready") return "待连接";
  if (["ok", "enabled", "available", "running"].includes(status)) return "正常";
  if (["blocked", "failed", "unavailable"].includes(status)) return "不可用";
  return status;
}

function compactDisplayPath(value?: string) {
  const text = String(value ?? "");
  if (!text) return "-";
  const marker = "/workspace/";
  const workspaceIndex = text.lastIndexOf(marker);
  if (workspaceIndex >= 0) return text.slice(workspaceIndex + 1);
  const parts = text.split("/").filter(Boolean);
  if (parts.length <= 2) return text;
  return `.../${parts.slice(-2).join("/")}`;
}
