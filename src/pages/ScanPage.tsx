import { Link, useLocation } from "react-router-dom";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { runDocumentAnalyze, runDocumentReportOutline, runDocumentTableExtract } from "../api/documents";
import { getSharedFiles, getSharedPreview, getWorkspaceFiles, getWorkspacePreview } from "../api/shared";
import type { DocumentResult, SharedFile, SharedPreviewResponse } from "../api/types";
import {
  ConsolePageHead,
  ConsoleTopbar,
  EmptyState,
  Panel,
  StatLine,
  StatusPill,
} from "../components/ProjectorConsole";

export function ScanPage() {
  const location = useLocation();
  const [source, setSource] = useState<"shared_inbox" | "workspace">("shared_inbox");
  const [files, setFiles] = useState<SharedFile[]>([]);
  const [selected, setSelected] = useState("");
  const [preview, setPreview] = useState<SharedPreviewResponse | null>(null);
  const [result, setResult] = useState<DocumentResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("等待选择文件");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const response = source === "workspace"
        ? await getWorkspaceFiles({ page_size: 120 })
        : await getSharedFiles({ page_size: 120 });
      const nextFiles = response.data.files ?? [];
      setFiles(nextFiles);
      setSelected((current) => nextFiles.some((file) => file.relative_path === current) ? current : nextFiles[0]?.relative_path ?? "");
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, [source]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selected) {
      setPreview(null);
      return;
    }
    const loader = source === "workspace" ? getWorkspacePreview : getSharedPreview;
    void loader(selected)
      .then((response) => setPreview(response.data))
      .catch((err) => setPreview({ status: "blocked", workspace_name: selected, name: selected, size_bytes: 0, text: apiErrorMessage(err) }));
  }, [selected, source]);

  const selectedFile = useMemo(() => files.find((file) => file.relative_path === selected), [files, selected]);
  const artifacts = useMemo(() => documentArtifacts(result), [result]);

  async function run(label: string, action: (path: string) => Promise<{ data: DocumentResult }>) {
    if (!selected) {
      setError("请先选择共享空间文件。");
      return;
    }
    setBusy(true);
    setError("");
    setMessage(`${label} 执行中...`);
    try {
      const response = await action(selected);
      setResult(response.data);
      setMessage(`${label} 状态：${String(response.data.status ?? "completed")}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage(`${label} 失败`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pc-console">
      <ConsoleTopbar
        title="文档工作区"
        subtitle="文件、AI 交互、生成结果和电脑控制权限"
        statuses={
          <>
            <StatusPill tone="ok">树莓派共享空间</StatusPill>
            <StatusPill tone="warn">沙箱控制不直连电脑</StatusPill>
          </>
        }
      />

      <ConsolePageHead
        title="文档工作区"
        description="主文件和 AI 交互页面。树莓派 AI 服务读取共享空间文件，生成摘要、表格、会议材料和草稿；全权控制通过 SSH 直接控制电脑，但必须单独请求。"
        actions={
          <>
            <button className="primary-button" onClick={() => setSource("shared_inbox")}>导入文件</button>
            <button className="ghost-button" onClick={() => void run("汇报提纲", (path) => runDocumentReportOutline(path, selectedFile?.name ?? "汇报提纲"))} disabled={busy || !selected}>多文件对比</button>
            <Link className="danger-button" to={{ pathname: "/remote", search: location.search }}>请求全权控制</Link>
          </>
        }
      />

      {error && <div className="danger-panel">操作失败：{error}</div>}

      <section className="pc-grid pc-grid--documents">
        <aside className="pc-panel">
          <div className="pc-panel-head"><div><h2>文件夹树</h2><span>共享空间</span></div></div>
          <div className="pc-panel-body pc-folder-list">
            <button className={`pc-folder-row ${source === "shared_inbox" ? "active" : ""}`} onClick={() => setSource("shared_inbox")} type="button">
              <strong>树莓派共享空间</strong><StatusPill tone="ok">常开共享文件夹</StatusPill>
            </button>
            <button className={`pc-folder-row ${source === "workspace" ? "active" : ""}`} onClick={() => setSource("workspace")} type="button">
              <strong>工作区归档</strong><span>{files.length} files</span>
            </button>
            <div className="pc-folder-row"><strong>会议材料</strong><span>会议</span></div>
            <div className="pc-folder-row"><strong>扫描文件</strong><span>扫描</span></div>
            <div className="pc-folder-row"><strong>结果草稿</strong><span>文档</span></div>
            <div className="pc-boundary">AI 助手运行在树莓派。文件进入共享空间后，才进入树莓派 AI 服务的处理范围。</div>
          </div>
        </aside>

        <main className="pc-grid">
          <Panel
            title="文件列表"
            subtitle="共享空间文件行显示同步、权限和审计状态"
            action={<button className="ghost-button" onClick={() => void load()}>整理文件</button>}
          >
            <div className="pc-row-list">
              {files.slice(0, 8).map((file) => (
                <button className={`pc-file-row ${file.relative_path === selected ? "active" : ""}`} key={file.relative_path} onClick={() => setSelected(file.relative_path)} type="button">
                  <div className="pc-file-meta">
                    <strong>{file.name}</strong>
                    <span>{source === "workspace" ? "工作区归档" : "树莓派共享空间"} / {compactPath(file.relative_path)} / {file.size_label}</span>
                  </div>
                  <StatusPill tone={file.status === "ok" || file.status === "available" ? "ok" : "warn"}>{file.status === "ok" ? "同步状态 OK" : friendlyStatus(file.status)}</StatusPill>
                </button>
              ))}
              {!files.length && <EmptyState>共享空间暂无文件。</EmptyState>}
            </div>
          </Panel>

          <Panel title="文件内容预览" subtitle="预览选中文件的内容" action={<button className="ghost-button">打开原文件</button>}>
            <div className="pc-preview-box">{previewText(preview, selected)}</div>
          </Panel>
        </main>

        <aside className="pc-inspector">
          <Panel title="智能交互区" subtitle="询问智能助手">
            <div className="pc-command-box">
              <textarea value={`基于当前会议纪要，生成待办表格和邮件草稿，但不要发送。\n\n当前文件：${selected || "未选择"}`} readOnly />
              <div className="pc-source-pill-row">
                <span className="pc-chip">来源范围：当前文件</span>
                <span className="pc-chip">树莓派 AI 服务</span>
                <span className="pc-chip">不读取电脑桌面</span>
              </div>
              <button className="primary-button" onClick={() => void run("文档分析", runDocumentAnalyze)} disabled={busy || !selected}>询问智能助手</button>
            </div>
          </Panel>

          <Panel title="生成结果" subtitle="已生成的结果">
            <div className="pc-row-list">
              {artifacts.map((artifact) => (
                <div className="pc-result-card" key={artifact.path}>
                  <strong>{artifact.label}</strong>
                  <span>{compactPath(artifact.path)}。可在快速预览区查看，详情保存到结果中心。</span>
                </div>
              ))}
              {!artifacts.length && <div className="pc-result-card"><strong>等待生成</strong><span>{message}</span></div>}
              <div className="pc-actions" style={{ justifyContent: "flex-start" }}>
                <button className="ghost-button" disabled={!artifacts.length}>保存到结果中心</button>
                <button className="ghost-button" onClick={() => void run("表格提取", runDocumentTableExtract)} disabled={busy || !selected}>生成合同表格</button>
              </div>
            </div>
          </Panel>

          <Panel title="电脑控制权限" subtitle="权限模型">
            <div className="pc-row-list">
              <div className="pc-permission-row">
                <strong>沙箱控制不直连电脑</strong>
                <p>可读写树莓派共享空间，生成会议纪要、摘要、表格和草稿；不能打开电脑应用或执行任意命令。</p>
                <StatusPill tone="ok">当前模式</StatusPill>
              </div>
              <div className="pc-permission-row">
                <strong>全权控制通过 SSH 直接控制电脑</strong>
                <p>必须显式请求，可撤销，并写入审计。启用后才能操作 LAN 电脑桌面、打开应用或发送受控命令。</p>
                <Link className="danger-button" to={{ pathname: "/remote", search: location.search }}>请求全权控制</Link>
              </div>
            </div>
          </Panel>

          <Panel title="共享状态" subtitle="同步 / 权限 / 审计">
            <div className="pc-stat-list">
              <StatLine label="同步状态" value="在线" />
              <StatLine label="成员权限" value="所有者、查看者" />
              <StatLine label="共享审计" value="已启用" />
              <StatLine label="访问边界" value="仅限共享空间" />
            </div>
          </Panel>
        </aside>
      </section>
    </div>
  );
}

function documentArtifacts(result: DocumentResult | null) {
  const items: Array<{ label: string; path: string }> = [];
  const add = (label: string, path: unknown) => {
    if (typeof path === "string" && path) items.push({ label, path });
  };
  add("文档摘要", result?.summary_path);
  add("会议待办表格", result?.table_path);
  add("汇报提纲", result?.outline_path);
  add("分析结果", result?.analysis_path);
  (result?.outputs ?? []).forEach((output) => add(output.type || "输出结果", output.path));
  return items;
}

function previewText(preview: SharedPreviewResponse | null, selected: string) {
  if (!selected) return "请选择树莓派共享空间中的文件。";
  if (preview?.text) return preview.text;
  if (preview?.download_only) return `${preview.name || selected}\n\n该文件可查看/下载，但没有可直接展示的文本预览。`;
  return "正在读取文件内容预览...";
}

function compactPath(value: string) {
  const parts = value.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.length <= 3 ? value : `.../${parts.slice(-3).join("/")}`;
}

function friendlyStatus(status: unknown) {
  const value = String(status ?? "pending");
  const labels: Record<string, string> = {
    ok: "同步状态 OK",
    available: "available",
    completed: "completed",
    pending: "pending",
    running: "running",
    failed: "failed",
  };
  return labels[value] ?? value;
}
