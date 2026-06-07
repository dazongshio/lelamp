import { ExternalLink, RefreshCw, Rows3, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { getDocmostStatus } from "../api/wiki";
import type { DocmostStatus } from "../api/types";
import { Card } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import "./pages.css";

export function WikiPage() {
  const [status, setStatus] = useState<DocmostStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setBusy(true);
    setError("");
    try {
      const response = await getDocmostStatus();
      setStatus(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const url = status?.url || "";
  const spaces = status?.spaces ?? [];
  const resolved = status?.resolved_space;

  return (
    <>
      <PageHeader
        title="Wiki 知识库"
        description="Docmost 文档管理入口；会议纪要、扫描 OCR、提纲和表格可从结果文件旁直接同步。"
        actions={
          <div className="button-row">
            <button className="ghost-button" onClick={() => void load()} disabled={busy}>
              <RefreshCw size={16} /> 刷新
            </button>
            <a className="primary-button" href={url || "#"} target="_blank" rel="noreferrer" aria-disabled={!url}>
              <ExternalLink size={16} /> 打开 Docmost
            </a>
          </div>
        }
      />
      <div className="page-grid wiki-page">
        {error && <div className="danger-panel">操作失败：{error}</div>}
        <Card title="连接状态" action={<StatusBadge status={status?.status ?? "pending"} />}>
          <div className="definition-grid">
            <span>服务地址</span><strong>{url || "-"}</strong>
            <span>默认空间</span><strong>{status?.default_space || "-"}</strong>
            <span>密钥状态</span><StatusBadge status={status?.configured ? "enabled" : "needs_config"} label={status?.configured ? "已配置" : "待配置"} />
            <span>当前空间</span><strong>{String(resolved?.name ?? resolved?.slug ?? "-")}</strong>
          </div>
          {status?.message && <p className="blue-note">{status.message}</p>}
        </Card>

        <Card title="空间" subtitle="同步时默认写入 General；也可在后端配置 DOCMOST_DEFAULT_SPACE。">
          <div className="list-rows">
            {spaces.map((space) => (
              <div className="row-between" key={String(space.id ?? space.slug ?? space.name)}>
                <span>
                  <Rows3 size={16} /> {String(space.name ?? space.slug ?? "Untitled")}
                </span>
                <strong>{String(space.slug ?? space.id ?? "")}</strong>
              </div>
            ))}
            {!spaces.length && <span className="small muted">暂无空间信息。</span>}
          </div>
        </Card>

        <Card title="同步方式">
          <div className="wiki-sync-guide">
            <div>
              <Search size={18} />
              <strong>从文档处理同步</strong>
              <span>选择文件或结果文件后点击“同步到 Wiki”。</span>
            </div>
            <div>
              <Search size={18} />
              <strong>从会议助手同步</strong>
              <span>在右侧 AI 结果面板选择纪要、待办、决策、PPT 或思维导图产物后同步。</span>
            </div>
          </div>
        </Card>
      </div>
    </>
  );
}
