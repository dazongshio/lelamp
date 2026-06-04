import { Download, Search, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { getAuditRecent, searchAudit, signedAuditExportUrl } from "../api/audit";
import { apiErrorMessage, readToken } from "../api/client";
import { verifySignedAuditExport } from "../api/security";
import type { AuditEvent } from "../api/types";
import { Card } from "../components/Card";
import { DataTable, type Column } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { SkillChip } from "../components/SkillChip";
import { StatusBadge } from "../components/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import "./pages.css";

const filters = ["ok", "blocked", "unavailable", "adapter_ready", "backend_missing"];

export function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [query, setQuery] = useState("");
  const [action, setAction] = useState("全部");
  const [status, setStatus] = useState("全部");
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  const [signedPath, setSignedPath] = useState("");
  const [verifyResult, setVerifyResult] = useState<Record<string, unknown> | null>(null);
  const [total, setTotal] = useState(0);
  const [path, setPath] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const params = {
        q: query,
        action: action === "全部" ? "" : action,
        status: status === "全部" ? "" : status,
        page_size: 100,
        limit: 200,
      };
      const result = query || action !== "全部" || status !== "全部" ? await searchAudit(params) : await getAuditRecent(params);
      const loaded = result.data.events ?? result.data.items ?? [];
      setEvents(loaded);
      setTotal(result.data.total);
      setPath(result.data.path);
      setSelected((current) => current ?? loaded[0] ?? null);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, [action, query, status]);

  useEffect(() => {
    void load();
  }, [load]);

  usePolling(load, 10000, true);

  const filtered = useMemo(() => events, [events]);
  const blockedCount = filtered.filter((event) => event.status === "blocked").length;

  const columns: Column<AuditEvent>[] = [
    { key: "time", title: "时间", render: (row) => <div><strong>{row.timestamp}</strong><div className="small muted">{friendlyActor(row.actor)}</div></div>, width: "190px" },
    { key: "action", title: "动作", render: (row) => friendlyAuditAction(row.action), width: "150px" },
    { key: "status", title: "状态", render: (row) => <StatusBadge status={row.status} />, width: "130px" },
    { key: "target", title: "对象", render: (row) => <span className="small">{compactDisplayPath(row.target)}</span>, width: "230px" },
    { key: "details", title: "摘要", render: (row) => detailText(row.details) },
  ];

  function exportCsv() {
    const token = readToken();
    const url = new URL("/api/audit/export.csv", window.location.origin);
    if (token) url.searchParams.set("token", token);
    window.open(url.toString(), "_blank", "noopener,noreferrer");
  }

  function exportSigned() {
    const token = readToken();
    const params = {
      q: query,
      action: action === "全部" ? "" : action,
      status: status === "全部" ? "" : status,
      page_size: 100,
      limit: 200,
    };
    const url = new URL(signedAuditExportUrl(params), window.location.origin);
    if (token) url.searchParams.set("token", token);
    window.open(url.toString(), "_blank", "noopener,noreferrer");
  }

  async function verifySigned() {
    if (!signedPath.trim()) {
      setError("请输入签名审计包路径。");
      return;
    }
    setError("");
    try {
      const result = await verifySignedAuditExport(signedPath.trim());
      setVerifyResult(result.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  return (
    <>
      <PageHeader title="审计日志" description="记录系统内所有关键操作与安全事件，保障可追溯与合规审计。" actions={<button className="ghost-button" onClick={() => void load()}>刷新</button>} />
      <div className="audit-layout">
        <div className="page-grid">
          {error && <div className="danger-panel">操作失败：{error}</div>}
          <Card>
            <div className="audit-filters">
              <div className="search-input">
                <Search size={16} />
                <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索目标、详情、用户或会话 ID..." />
              </div>
              <select className="select" value={action} onChange={(event) => setAction(event.target.value)}>
                <option>全部</option>
                <option value="file_read">文件读取</option>
                <option value="upload">上传</option>
                <option value="projection">投影</option>
                <option value="adapter">设备接入</option>
                <option value="assistant">助手</option>
              </select>
              <select className="select" value={status} onChange={(event) => setStatus(event.target.value)}>
                <option>全部</option>
                {filters.map((item) => <option value={item} key={item}>{friendlyStatusName(item)}</option>)}
              </select>
              <button className="ghost-button" onClick={() => void load()}>应用筛选</button>
            </div>
            <div className="filter-chips">
              {filters.map((item) => (
                <button key={item} onClick={() => setStatus(item)}>
                  状态: {friendlyStatusName(item)} <X size={13} />
                </button>
              ))}
              <button onClick={() => { setStatus("全部"); setQuery(""); setAction("全部"); }}>清除全部</button>
            </div>
          </Card>

          <Card
            title={`共 ${total || filtered.length} 条记录，阻止 ${blockedCount} 条`}
            action={<div className="row"><button className="ghost-button" onClick={exportCsv}><Download size={16} /> 导出 CSV</button><button className="primary-button" onClick={exportSigned}><Download size={16} /> 签名导出</button></div>}
          >
            <DataTable
              rows={filtered}
              columns={columns}
              rowKey={(row, index) => `${row.timestamp}-${row.request_id}-${index}`}
              onRowClick={setSelected}
              rowClassName={(row) => row.status === "blocked" ? "row-blocked" : ""}
            />
            {!filtered.length && <p className="small muted">审计日志为空或当前筛选没有结果。触发一次被阻止的高风险操作后会显示红色行。</p>}
          </Card>
        </div>

        <div className="stack audit-side">
          <Card title="事件详情" action={<button className="plain-button" onClick={() => setSelected(null)}><X size={16} /></button>}>
            {selected ? (
              <div className="stack">
                <div className="row"><StatusBadge status={selected.status} /><strong>{friendlyAuditAction(selected.action)}</strong></div>
                <span className="small muted">{selected.timestamp}</span>
                <div className="divider" />
                <h3>基本信息</h3>
                <div className="definition-grid">
                  <span>操作者</span><strong>{friendlyActor(selected.actor)}</strong>
                  <span>动作</span><strong>{selected.action}</strong>
                  <span>状态</span><StatusBadge status={selected.status} />
                  <span>对象</span><strong>{compactDisplayPath(selected.target)}</strong>
                </div>
                <div className="divider" />
                <h3>策略评估</h3>
                <div className="definition-grid">
                  <span>策略名称</span><strong>{selected.status === "blocked" ? "文件访问边界" : "操作留痕"}</strong>
                  <span>结果</span><strong className={selected.status === "blocked" ? "danger-text" : "success-text"}>{selected.status === "blocked" ? "阻止" : "记录"}</strong>
                </div>
                <details className="advanced-panel">
                  <summary>事件诊断</summary>
                  <div className="advanced-panel__content">
                    <div className="definition-grid">
                      <span>Request ID</span><strong>{selected.request_id || "-"}</strong>
                      <span>来源 IP</span><strong>{selected.source_ip || "-"}</strong>
                      <span>原始对象</span><strong className="mono">{selected.target}</strong>
                    </div>
                    <pre className="json-preview">{JSON.stringify(selected.details, null, 2)}</pre>
                  </div>
                </details>
              </div>
            ) : <p className="small muted">请选择一条审计事件。</p>}
          </Card>
          <Card title="查询条件">
            <div className="definition-grid">
              <span>状态</span><strong>{status === "全部" ? "全部" : friendlyStatusName(status)}</strong>
              <span>结果</span><strong>共 {filtered.length} 条记录</strong>
            </div>
            <div className="blocked-examples">
              <strong>被阻止的高风险事件必须可见</strong>
              <span>示例：读取未授权目录会被阻止并记录。</span>
              <span>示例：删除、发送、提交等动作需要额外确认。</span>
              <span>策略：仅允许访问用户授权内容。</span>
            </div>
            <details className="advanced-panel">
              <summary>日志诊断</summary>
              <div className="advanced-panel__content">
                <div className="definition-grid">
                  <span>日志路径</span><strong className="mono">{path || "-"}</strong>
                </div>
              </div>
            </details>
          </Card>
          <Card title="签名审计校验">
            <div className="stack">
              <input className="input" value={signedPath} onChange={(event) => setSignedPath(event.target.value)} placeholder="粘贴签名审计包路径" />
              <button className="ghost-button" onClick={() => void verifySigned()}>校验签名</button>
              <div className="definition-grid">
                <span>状态</span><StatusBadge status={String(verifyResult?.status ?? "pending")} />
                <span>说明</span><strong>{verifyResult ? "校验结果已返回" : "等待校验签名审计包"}</strong>
              </div>
              <details className="advanced-panel">
                <summary>校验诊断</summary>
                <div className="advanced-panel__content">
                  <pre className="json-preview">{JSON.stringify(verifyResult ?? { status: "pending", message: "签名导出需要配置审计签名密钥。" }, null, 2)}</pre>
                </div>
              </details>
            </div>
          </Card>
          <Card title="安全建议">
            <p>建议保持当前策略配置，如需访问外部路径，请通过受控的共享空间上传文件。</p>
            <SkillChip>blocked behavior is audited</SkillChip>
          </Card>
        </div>
      </div>
    </>
  );
}

function detailText(details: AuditEvent["details"]) {
  const reason = details.reason ?? details.message ?? details.policy ?? "";
  return <span>{String(reason || "详情已记录")}</span>;
}

function friendlyStatusName(value: string) {
  const labels: Record<string, string> = {
    ok: "正常",
    blocked: "已阻止",
    unavailable: "不可用",
    adapter_ready: "待接入",
    backend_missing: "待接入",
  };
  return labels[value] ?? value;
}

function friendlyAuditAction(value: string) {
  const labels: Record<string, string> = {
    file_read: "文件读取",
    upload: "上传",
    projection: "投影",
    adapter: "设备接入",
    assistant: "助手",
    hardware_test: "硬件测试",
    meeting: "会议",
  };
  return labels[value] ?? value.replace(/[_-]+/g, " ");
}

function friendlyActor(value?: string) {
  if (!value) return "系统";
  const labels: Record<string, string> = {
    web: "Web 用户",
    assistant: "助手",
    system: "系统",
  };
  return labels[value] ?? value;
}

function compactDisplayPath(value?: string) {
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
