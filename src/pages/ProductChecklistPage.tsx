import { ClipboardCheck, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { apiErrorMessage } from "../api/client";
import { getProductChecklist } from "../api/product";
import type { ProductChecklistItem, ProductChecklistResponse } from "../api/types";
import { Card, InfoCard } from "../components/Card";
import { DataTable, type Column } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { SkillChip } from "../components/SkillChip";
import { StatusBadge } from "../components/StatusBadge";
import "./pages.css";

export function ProductChecklistPage() {
  const [data, setData] = useState<ProductChecklistResponse | null>(null);
  const [activeArea, setActiveArea] = useState("all");
  const [error, setError] = useState("");
  const location = useLocation();

  const load = useCallback(async () => {
    setError("");
    try {
      const response = await getProductChecklist();
      setData(response.data);
      setActiveArea((current) => current === "all" || response.data.areas[current] ? current : "all");
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const areas = Object.keys(data?.areas ?? {});
  const rows = useMemo(() => {
    if (!data) return [];
    if (activeArea === "all") return data.items;
    return data.areas[activeArea] ?? [];
  }, [activeArea, data]);

  const columns: Column<ProductChecklistItem>[] = [
    { key: "area", title: "模块", render: (row) => <span>{row.area}</span>, width: "190px" },
    { key: "feature", title: "小功能", render: (row) => <strong>{row.feature}</strong>, width: "240px" },
    { key: "status", title: "状态", render: (row) => <StatusBadge status={row.status} />, width: "130px" },
    { key: "evidence", title: "验收依据", render: (row) => <span>{friendlyEvidence(row.evidence)}</span> },
    { key: "gap", title: "缺口", render: (row) => <span>{row.gap || "-"}</span> },
  ];

  return (
    <>
      <PageHeader
        title="产品功能清单"
        description="按原始 5 大类逐项核对实现状态、证据、缺口和下一步"
        actions={<button className="ghost-button" onClick={() => void load()}><RefreshCw size={16} />刷新</button>}
      />
      <div className="page-grid">
        {error && <div className="danger-panel">加载失败：{error}</div>}
        <div className="grid-4">
          <InfoCard icon={<ClipboardCheck size={20} />} label="总功能点" value={String(data?.summary.total ?? "-")} note="来自用户原始清单拆分" />
          <InfoCard icon={<ClipboardCheck size={20} />} label="已实现" value={String(data?.summary.counts.implemented ?? 0)} note="软件侧已接入" status={<StatusBadge status="implemented" />} />
          <InfoCard icon={<ClipboardCheck size={20} />} label="待接入" value={String(data?.summary.counts.adapter_ready ?? 0)} note="等待外部配置/目标硬件" status={<StatusBadge status="adapter_ready" />} />
          <InfoCard icon={<ClipboardCheck size={20} />} label="未完成" value={String(data?.summary.remaining_count ?? 0)} note="真正未实现/待适配项" status={<StatusBadge status={(data?.summary.remaining_count ?? 0) ? "adapter_ready" : "completed"} />} />
        </div>

        <Card title="模块筛选">
          <div className="checklist-tabs">
            <button className={activeArea === "all" ? "selected" : ""} onClick={() => setActiveArea("all")}>全部</button>
            {areas.map((area) => (
              <button className={activeArea === area ? "selected" : ""} key={area} onClick={() => setActiveArea(area)}>{area}</button>
            ))}
          </div>
        </Card>

        <Card
          title="剩余未完成项"
          action={<Link className="ghost-button" to={{ pathname: "/validation", search: location.search }}>去目标验收</Link>}
        >
          <div className="checklist-gap-grid">
            {(data?.remaining ?? []).map((item) => (
              <div className="checklist-gap" key={`${item.area}-${item.feature}`}>
                <div className="row-between">
                  <strong>{item.feature}</strong>
                  <StatusBadge status={item.status} />
                </div>
                <span className="small muted">{item.area}</span>
                <p>{item.gap || item.next_step || "仍需目标环境验收。"}</p>
              </div>
            ))}
            {!data?.remaining.length && <span className="small muted">当前没有软件层未完成项；目标硬件验收请看 Validation 页面。</span>}
          </div>
        </Card>

        <Card title="部署/实机验收备注" action={<StatusBadge status="implemented" label={`${data?.summary.deployment_note_count ?? 0} 项备注`} />}>
          <div className="checklist-gap-grid">
            {(data?.deployment_notes ?? []).slice(0, 9).map((item) => (
              <div className="checklist-gap" key={`note-${item.area}-${item.feature}`}>
                <div className="row-between">
                  <strong>{item.feature}</strong>
                  <StatusBadge status={item.status} />
                </div>
                <span className="small muted">{item.area}</span>
                <p>{item.gap || item.next_step}</p>
              </div>
            ))}
            {!data?.deployment_notes?.length && <span className="small muted">暂无额外部署备注。</span>}
          </div>
        </Card>

        <Card title="逐项核对表">
          <DataTable rows={rows} columns={columns} rowKey={(row) => `${row.area}-${row.feature}`} />
        </Card>

        <Card title="安全与验收说明">
          <div className="security-summary">
            <SkillChip>默认沙箱</SkillChip>
            <SkillChip>会议理解手动开启</SkillChip>
            <SkillChip>全权模式门禁</SkillChip>
            <SkillChip>操作留痕</SkillChip>
            <span>状态来自当前运行时 API，不把未配置外设或未验收硬件标成已完成。</span>
          </div>
        </Card>
      </div>
    </>
  );
}

function friendlyEvidence(evidence: string[]) {
  if (!evidence.length) return "等待验收证据";
  return evidence.slice(0, 3).map((item) => item.replace(/[_-]+/g, " ").replace(/\/api\/[^\s]+/g, "接口已接入")).join(" / ");
}
