import {
  Clock3,
  FileText,
  FolderOpen,
  LampDesk,
  Mic2,
  MoreHorizontal,
  Presentation,
  ScanLine,
  Speech,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { apiErrorMessage } from "../api/client";
import { getSharedFiles } from "../api/shared";
import { getRecentTasks } from "../api/tasks";
import type { SharedFile, TaskRecord } from "../api/types";
import { StatusPill } from "../components/ProjectorConsole";

type ActionCardProps = {
  title: string;
  description: string;
  to: string;
  search: string;
  icon: typeof Mic2;
  tone: "blue" | "green" | "purple" | "amber";
};

function ActionCard({ title, description, to, search, icon: Icon, tone }: ActionCardProps) {
  return (
    <Link
      className={`user-action-card user-action-card--${tone}`}
      to={{ pathname: to, search }}
    >
      <span className="user-action-card__icon"><Icon size={28} /></span>
      <span className="user-action-card__content">
        <strong>{title}</strong>
        <span>{description}</span>
      </span>
      <span className="user-action-card__arrow">进入</span>
    </Link>
  );
}

function taskStatus(status: TaskRecord["status"]) {
  const labels: Record<string, string> = {
    completed: "已完成",
    success: "已完成",
    running: "处理中",
    pending: "等待中",
    failed: "未完成",
  };
  return labels[String(status).toLowerCase()] ?? "处理中";
}

export function DashboardPage() {
  const location = useLocation();
  const [files, setFiles] = useState<SharedFile[]>([]);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [filesResult, taskResult] = await Promise.all([
        getSharedFiles({ page_size: 12 }),
        getRecentTasks(4),
      ]);
      setFiles(filesResult.data.files ?? []);
      setTasks(taskResult.data.tasks ?? taskResult.data.items ?? []);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const recentTask = useMemo(() => tasks[0], [tasks]);
  const scanSearch = location.search
    ? `${location.search}&scan=1`
    : "?scan=1";

  return (
    <main className="user-home">
      <section className="user-home__intro">
        <div>
          <span>欢迎使用</span>
          <h1>想用设备做什么？</h1>
          <p>选择一项，即可马上开始。</p>
        </div>
        <StatusPill tone="ok">设备已就绪</StatusPill>
      </section>

      {error && <div className="user-home__notice">部分状态暂时无法读取，不影响使用主要功能。</div>}

      <section className="user-action-grid" aria-label="常用功能">
        <ActionCard
          title="开始会议"
          description="录音、实时转写并整理会议纪要"
          to="/meeting"
          search={location.search}
          icon={Mic2}
          tone="blue"
        />
        <ActionCard
          title="投影内容"
          description="把设备画面显示到投影仪"
          to="/projection"
          search={location.search}
          icon={Presentation}
          tone="green"
        />
        <ActionCard
          title="扫描文档"
          description="拍摄纸质文件并识别文字"
          to="/documents"
          search={scanSearch}
          icon={ScanLine}
          tone="purple"
        />
        <ActionCard
          title="调整台灯"
          description="调整灯光、摄像头和转动位置"
          to="/motors"
          search={location.search}
          icon={LampDesk}
          tone="amber"
        />
      </section>

      <section className="user-home__bottom">
        <div className="user-shortcuts">
          <h2>其他功能</h2>
          <div className="user-shortcuts__grid">
            <Link to={{ pathname: "/voice", search: location.search }}>
              <Speech size={21} />
              <span><strong>语音助手</strong><small>唤醒、对话和语音控制</small></span>
            </Link>
            <Link to={{ pathname: "/documents", search: location.search }}>
              <FolderOpen size={21} />
              <span><strong>我的文件</strong><small>{files.length ? `${files.length} 个最近文件` : "查看共享文件"}</small></span>
            </Link>
            <Link to={{ pathname: "/results", search: location.search }}>
              <FileText size={21} />
              <span><strong>处理结果</strong><small>查看纪要和扫描结果</small></span>
            </Link>
            <Link to={{ pathname: "/pot", search: location.search }}>
              <MoreHorizontal size={21} />
              <span><strong>更多功能</strong><small>设备设置和高级选项</small></span>
            </Link>
          </div>
        </div>

        <aside className="user-recent">
          <div className="user-recent__title">
            <span><Clock3 size={19} />最近使用</span>
            <Link to={{ pathname: "/results", search: location.search }}>查看全部</Link>
          </div>
          {recentTask ? (
            <div className="user-recent__item">
              <span className="user-recent__icon"><FileText size={22} /></span>
              <span>
                <strong>{recentTask.title || "最近处理内容"}</strong>
                <small>{taskStatus(recentTask.status)}</small>
              </span>
            </div>
          ) : (
            <div className="user-recent__empty">还没有使用记录，选择上方功能开始吧。</div>
          )}
        </aside>
      </section>
    </main>
  );
}
