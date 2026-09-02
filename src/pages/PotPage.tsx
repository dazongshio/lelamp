import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { apiErrorMessage } from "../api/client";
import {
  ConsolePageHead,
  ConsoleTopbar,
  EmptyState,
  Panel,
  StatLine,
  StatusPill,
} from "../components/ProjectorConsole";
import { PotModuleCard, arrayFrom, buildGroups, friendlyStatus, record, toneForStatus, type PotFailure, type PotSnapshot } from "./potModel";
import { loaders } from "./potLoaders";

export function PotPage() {
  const location = useLocation();
  const [snapshot, setSnapshot] = useState<PotSnapshot>({});
  const [failures, setFailures] = useState<PotFailure[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadedAt, setLoadedAt] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const results = await Promise.allSettled(loaders.map((loader) => loader.run()));
    const next: PotSnapshot = {};
    const nextFailures: PotFailure[] = [];
    results.forEach((result, index) => {
      const loader = loaders[index];
      if (result.status === "fulfilled") {
        next[loader.key] = result.value.data;
      } else {
        nextFailures.push({
          key: loader.key,
          label: loader.label,
          message: apiErrorMessage(result.reason),
        });
      }
    });
    setSnapshot(next);
    setFailures(nextFailures);
    setLoadedAt(new Date().toLocaleTimeString());
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const groups = useMemo(() => buildGroups(snapshot), [snapshot]);
  const allModules = groups.flatMap((group) => group.modules);
  const readyCount = allModules.filter((item) => toneForStatus(item.status) === "ok").length;
  const warnCount = allModules.filter((item) => toneForStatus(item.status) === "warn").length;
  const blockedCount = allModules.filter((item) => toneForStatus(item.status) === "blocked").length + failures.length;
  const taskItems = arrayFrom(record(snapshot.tasks).items ?? record(snapshot.tasks).tasks);
  const auditItems = arrayFrom(record(snapshot.audit).events ?? record(snapshot.audit).items);

  return (
    <div className="pc-console">
      <ConsoleTopbar
        title="功能锅"
        subtitle="核心四页之外的已接入能力集中入口"
        statuses={
          <>
            <StatusPill tone="ok">保留 goal1 主流程</StatusPill>
            <StatusPill tone={failures.length ? "warn" : "ok"}>已加载 {loaders.length - failures.length}/{loaders.length} 个模块</StatusPill>
            <StatusPill tone="neutral">{loadedAt || "等待加载"}</StatusPill>
          </>
        }
      />

      <ConsolePageHead
        title="功能锅"
        description="这里收纳已经具备、但不应该挤进今日、会议、文档和结果四个主工作流的功能。默认只做状态检查和跳转，不直接执行舵机、远程连接、会议采集或发送动作。"
        actions={
          <>
            <button className="primary-button" onClick={() => void load()} disabled={loading}>{loading ? "正在刷新…" : "刷新功能池"}</button>
            <Link className="ghost-button" to={{ pathname: "/dashboard", search: location.search }}>返回工作台</Link>
          </>
        }
      />

      <section className="pc-pot-hero">
        <div className="pc-pot-score">
          <span>功能池状态</span>
          <strong>{allModules.length}</strong>
          <p>模块入口</p>
        </div>
        <div className="pc-pot-stats">
          <StatLine label="就绪" value={readyCount} />
          <StatLine label="需要处理" value={warnCount} />
          <StatLine label="已阻止或失败" value={blockedCount} />
          <StatLine label="收纳的功能入口" value="15+" />
        </div>
        <div className="pc-boundary">
          主界面只保留高频路径；低频、调试、硬件、治理和危险能力全部放进这个页面。需要执行时再进入对应旧功能页。
        </div>
      </section>

      {failures.length > 0 && (
        <Panel title="加载失败的模块" subtitle="不会影响其他模块显示">
          <div className="pc-pot-failure-grid">
            {failures.map((failure) => (
              <div className="pc-danger-note" key={failure.key}>
                <strong>{failure.label}</strong>
                <span>{failure.message}</span>
              </div>
            ))}
          </div>
        </Panel>
      )}

      <section className="pc-grid pc-pot-layout">
        <main className="pc-grid">
          {groups.map((group) => (
            <Panel title={group.title} subtitle={group.subtitle} key={group.title}>
              <div className="pc-pot-module-grid">
                {group.modules.map((item) => (
                  <PotModuleCard item={item} search={location.search} key={`${group.title}-${item.title}`} />
                ))}
              </div>
            </Panel>
          ))}
        </main>

        <aside className="pc-inspector pc-preview-drawer">
          <Panel title="最近长任务" subtitle="扫描、PDF、会议和桌面自动化">
            <div className="pc-row-list">
              {taskItems.slice(0, 7).map((task, index) => (
                <div className="pc-result-card" key={String(record(task).task_id ?? index)}>
                  <strong>{String(record(task).title ?? record(task).task_id ?? "任务")}</strong>
                  <span>{friendlyStatus(record(task).type ?? "任务")} / {friendlyStatus(record(task).status ?? "等待执行")}</span>
                </div>
              ))}
              {!taskItems.length && <EmptyState>暂无长任务记录。</EmptyState>}
              <Link className="ghost-button" to={{ pathname: "/audit", search: location.search }}>查看审计</Link>
            </div>
          </Panel>

          <Panel title="最近审计" subtitle="所有边界动作都应该落审计">
            <div className="pc-audit-strip">
              {auditItems.slice(0, 10).map((event, index) => (
                <span className="pc-chip" key={`${String(record(event).timestamp ?? "")}-${index}`}>
                  {String(record(event).action ?? "audit").replace(/[_-]+/g, " ")}
                </span>
              ))}
              {!auditItems.length && <span className="pc-chip">暂无审计</span>}
            </div>
          </Panel>

          <Panel title="锅的规则" subtitle="避免主流程过载">
            <div className="pc-row-list">
              <div className="pc-permission-row">
                <strong>核心四页不塞满功能</strong>
                <p>高频流程留在今日、会议、文档和结果页面；低频入口统一在功能锅。</p>
              </div>
              <div className="pc-permission-row">
                <strong>危险动作不在锅里直接执行</strong>
                <p>舵机、投影、SSH、会议采集、邮件发送必须进入对应页面再操作，减少误触。</p>
              </div>
              <div className="pc-permission-row">
                <strong>旧功能仍保留</strong>
                <p>没有删除旧路由，只是把入口收纳起来，方便继续调试和验收。</p>
              </div>
            </div>
          </Panel>
        </aside>
      </section>
    </div>
  );
}
