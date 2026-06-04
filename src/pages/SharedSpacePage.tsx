import { FileSearch, FileText, ListTree, Mail, MoreVertical, Search, Table2, UploadCloud } from "lucide-react";
import { ChangeEvent, useCallback, useEffect, useMemo, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { getSharedFiles, runSharedFileAction, saveSharedNote, uploadSharedFile, type SharedFileAction } from "../api/shared";
import type { SharedFile } from "../api/types";
import { Card } from "../components/Card";
import { DataTable, type Column } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { ProgressBar } from "../components/ProgressBar";
import { StatusBadge } from "../components/StatusBadge";
import "./pages.css";

export function SharedSpacePage() {
  const [files, setFiles] = useState<SharedFile[]>([]);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [note, setNote] = useState("");
  const [noteTitle, setNoteTitle] = useState("共享笔记");
  const [statusMessage, setStatusMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setError("");
    try {
      const result = await getSharedFiles({ page_size: 100 });
      setFiles(result.data.files ?? []);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    return files.filter((file) => `${file.name} ${file.relative_path} ${file.status}`.toLowerCase().includes(query.toLowerCase()));
  }, [files, query]);

  const pageSize = 5;
  const paged = filtered.slice((page - 1) * pageSize, page * pageSize);
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setStatusMessage("上传中...");
    setError("");
    try {
      const result = await uploadSharedFile(file);
      setFiles((items) => [...result.data.files, ...items]);
      setStatusMessage(`已上传 ${result.data.files.length} 个文件到共享空间`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setStatusMessage("");
    } finally {
      event.target.value = "";
    }
  }

  async function saveNote() {
    if (!note.trim()) {
      setError("请输入要保存的笔记内容。");
      return;
    }
    setError("");
    setStatusMessage("保存笔记中...");
    try {
      const result = await saveSharedNote(noteTitle, note);
      setFiles((items) => [result.data.file, ...items]);
      setNote("");
      setStatusMessage(`已保存：${result.data.file.name}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setStatusMessage("");
    }
  }

  async function runAction(file: SharedFile, action: SharedFileAction) {
    setError("");
    setStatusMessage(`${file.name} 正在执行 ${action}...`);
    try {
      const params = action === "report_outline" ? { topic: file.name } : {};
      const result = await runSharedFileAction(file.relative_path, action, params);
      setStatusMessage(`${friendlyActionName(action)}：${friendlyStatus(result.data.status ?? "completed")}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setStatusMessage("");
    }
  }

  const columns: Column<SharedFile>[] = [
    { key: "name", title: "文件名", render: (row) => <strong>{row.name}</strong>, width: "22%" },
    { key: "size", title: "大小", render: (row) => row.size_label, width: "80px" },
    { key: "time", title: "上传时间", render: (row) => row.uploaded_at, width: "150px" },
    { key: "status", title: "状态", render: (row) => <StatusBadge status={row.status} />, width: "96px" },
    {
      key: "actions",
      title: "操作",
      render: (row) => (
        <div className="table-actions">
          <button onClick={() => void runAction(row, "analyze")}>分析</button>
          <button onClick={() => void runAction(row, "report_outline")}>提纲</button>
          <button onClick={() => void runAction(row, "key_data_table")}>表格</button>
          <button onClick={() => void runAction(row, "followup_package")}>邮件</button>
          <button onClick={() => void runAction(row, "search")}>搜索</button>
          <button onClick={() => void runAction(row, "generate_minutes")}>生成纪要</button>
          <MoreVertical size={15} />
        </div>
      ),
      width: "330px",
    },
  ];

  return (
    <>
      <PageHeader title="文件工作区" description="只处理用户主动上传、拖入或保存的文件；无法浏览任意办公电脑目录。" actions={<button className="ghost-button" onClick={() => void load()}>{loading ? "加载中" : "刷新列表"}</button>} />
      <div className="page-grid">
        {error && <div className="danger-panel">操作失败：{error}</div>}
        {statusMessage && <div className="blue-note">{statusMessage}</div>}
        <div className="shared-top">
          <Card title="上传文件">
            <label className="upload-zone">
              <UploadCloud size={54} />
              <strong>将文件拖拽到此处，或点击选择文件</strong>
              <span>支持上传文件。大小限制由系统配置控制</span>
              <span>允许类型：pdf, docx, txt, md, png, jpg, jpeg, gif, csv, xlsx</span>
              <input type="file" onChange={handleUpload} />
            </label>
            <p className="security-note">所有文件将保存到受控共享空间。</p>
          </Card>
          <Card title="粘贴文本并保存为共享笔记">
            <input className="input" value={noteTitle} onChange={(event) => setNoteTitle(event.target.value)} />
            <textarea className="textarea" value={note} onChange={(event) => setNote(event.target.value)} placeholder="在此粘贴文本内容（如会议记录、摘录、待办事项等）..." />
            <div className="row-between">
              <span className="small muted">支持 Markdown 语法 · {note.length}/20000</span>
              <button className="secondary-button" onClick={() => void saveNote()}>保存为共享笔记</button>
            </div>
          </Card>
          <div className="stack">
            <Card title="最近上传（最近 5 条）">
              <div className="list-rows compact">
                {files.slice(0, 5).map((file) => (
                  <div className="row-between" key={file.name}>
                    <span>{file.name}</span>
                    <span className="small muted">{file.uploaded_at.slice(11, 16)}</span>
                  </div>
                ))}
              </div>
              <a className="card-link">查看全部文件 →</a>
            </Card>
            <Card title="存储空间使用情况">
              <div className="row-between">
                <span>已用 2.48 GB / 10 GB</span>
                <strong>24.8%</strong>
              </div>
              <ProgressBar value={24.8} />
              <div className="row-between small muted">
                <span>文件数：128 个</span>
                <span>可用空间：7.52 GB</span>
              </div>
            </Card>
          </div>
        </div>

        <div className="blue-note">
          本界面无法浏览任意办公电脑目录。仅可访问用户主动导入的文件和系统生成的结果。
        </div>

        <div className="grid-3">
          <Card title="文档整理成汇报提纲">
            <div className="office-api-card">
              <ListTree size={22} />
              <span>在文件列表点“提纲”，调用 API 生成 Markdown 汇报提纲。</span>
            </div>
          </Card>
          <Card title="关键数据做表格">
            <div className="office-api-card">
              <Table2 size={22} />
              <span>在文件列表点“表格”，调用 API 生成 CSV 关键数据表。</span>
            </div>
          </Card>
          <Card title="会议纪要生成邮件">
            <div className="office-api-card">
              <Mail size={22} />
              <span>对会议 transcript 点“邮件”，生成会后邮件草稿和跟进包。</span>
            </div>
          </Card>
        </div>

        <Card title={`共享文件列表　共 ${filtered.length} 个文件`}>
          <div className="table-toolbar">
            <div className="search-input">
              <Search size={16} />
              <input value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="按文件名搜索" />
            </div>
            <select className="select">
              <option>全部类型</option>
              <option>PDF</option>
              <option>文本</option>
              <option>图片</option>
            </select>
            <select className="select">
              <option>全部状态</option>
              <option>已完成</option>
              <option>待接入</option>
            </select>
            <button className="ghost-button" onClick={() => void load()}>
              <FileSearch size={16} />
              刷新列表
            </button>
          </div>
          <DataTable rows={paged} columns={columns} rowKey={(row) => row.name} />
          {!paged.length && <p className="small muted">暂无文件。请从办公电脑拖入共享空间或保存一条笔记。</p>}
          <div className="pagination">
            <span>共 {filtered.length} 条</span>
            <button className="ghost-button" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
            {Array.from({ length: Math.min(5, pageCount) }, (_, index) => index + 1).map((item) => (
              <button className={item === page ? "primary-button" : "ghost-button"} key={item} onClick={() => setPage(item)}>{item}</button>
            ))}
            <button className="ghost-button" disabled={page >= pageCount} onClick={() => setPage((value) => value + 1)}>下一页</button>
            <span>10 条/页</span>
          </div>
        </Card>

        <Card title="安全说明">
          <div className="row">
            <FileText size={18} />
            <span>办公电脑可以把文件拖入共享空间，树莓派侧只能读取共享空间和已授权目录；不会读取密钥、邮箱、聊天记录、云盘或任意用户目录。</span>
          </div>
          <details className="advanced-panel">
            <summary>文件诊断</summary>
            <div className="advanced-panel__content">
              <Card title="当前列表">
                <DataTable
                  rows={paged}
                  columns={[
                    { key: "path", title: "相对路径", render: (row) => <span className="mono small">{row.relative_path}</span> },
                    { key: "sha", title: "sha256", render: (row) => <span className="mono small">{row.sha256}</span> },
                  ]}
                  rowKey={(row) => row.name}
                />
              </Card>
            </div>
          </details>
        </Card>
      </div>
    </>
  );
}

function friendlyActionName(action: SharedFileAction) {
  const labels: Record<SharedFileAction, string> = {
    analyze: "分析",
    summarize: "总结",
    report_outline: "生成提纲",
    key_data_table: "提取表格",
    followup_package: "生成邮件",
    search: "搜索",
    generate_minutes: "生成纪要",
  };
  return labels[action];
}

function friendlyStatus(value: unknown) {
  const status = String(value ?? "");
  const labels: Record<string, string> = {
    ok: "正常",
    completed: "已完成",
    running: "处理中",
    pending: "等待",
    blocked: "已阻止",
    failed: "失败",
  };
  return labels[status] ?? (status || "已完成");
}
