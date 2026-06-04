import { Bot, CheckCircle2, Globe2, Play, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiErrorMessage } from "../api/client";
import {
  executeBrowserTask,
  executeDesktopWorkflow,
  getDesktopCompanionStatus,
  getBrowserAutomationStatus,
  getDesktopWorkflowStatus,
  getDesktopTasks,
  planDesktopWorkflow,
  runDesktopControlAction,
  runDesktopCompanionOnce,
  requestDesktopTask,
  setupDesktopWorkflow,
  startDesktopCompanion,
  stopDesktopCompanion,
  updateDesktopTaskStatus,
} from "../api/tasks";
import type { BrowserAutomationResult, BrowserAutomationStatus, DesktopCompanionRunResponse, DesktopCompanionStatus, DesktopControlActionResult, DesktopTask, DesktopWorkflowResult, DesktopWorkflowStatus } from "../api/types";
import { Card, InfoCard } from "../components/Card";
import { DataTable, type Column } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { SkillChip } from "../components/SkillChip";
import { StatusBadge } from "../components/StatusBadge";
import "./pages.css";

const defaultSteps = [
  "open https://example.com",
  "extract text",
  "screenshot",
].join("\n");

const defaultWorkflowSteps = [
  "打开网页 https://example.com",
  "找文件 api_docx_contract",
].join("\n");

export function DesktopAutomationPage() {
  const [status, setStatus] = useState<BrowserAutomationStatus | null>(null);
  const [tasks, setTasks] = useState<DesktopTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [goal, setGoal] = useState("受控浏览器打开网页并截图");
  const [stepsText, setStepsText] = useState(defaultSteps);
  const [authorized, setAuthorized] = useState(false);
  const [headless, setHeadless] = useState(true);
  const [allowedHosts, setAllowedHosts] = useState("example.com");
  const [result, setResult] = useState<BrowserAutomationResult | null>(null);
  const [companion, setCompanion] = useState<DesktopCompanionStatus | null>(null);
  const [companionRun, setCompanionRun] = useState<DesktopCompanionRunResponse | null>(null);
  const [workflowStatus, setWorkflowStatus] = useState<DesktopWorkflowStatus | null>(null);
  const [workflowGoal, setWorkflowGoal] = useState("本机桌面整理工作流");
  const [workflowStepsText, setWorkflowStepsText] = useState(defaultWorkflowSteps);
  const [workflowAuthorized, setWorkflowAuthorized] = useState(false);
  const [workflowResult, setWorkflowResult] = useState<DesktopWorkflowResult | null>(null);
  const [controlAuthorized, setControlAuthorized] = useState(false);
  const [controlAction, setControlAction] = useState("low_level_probe");
  const [controlText, setControlText] = useState("ctrl+l");
  const [controlX, setControlX] = useState(0);
  const [controlY, setControlY] = useState(0);
  const [controlResult, setControlResult] = useState<DesktopControlActionResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const [statusResult, tasksResult, companionResult, workflowResult] = await Promise.all([getBrowserAutomationStatus(), getDesktopTasks(50), getDesktopCompanionStatus(), getDesktopWorkflowStatus()]);
      setStatus(statusResult.data);
      setTasks(tasksResult.data.tasks);
      setCompanion(companionResult.data);
      setWorkflowStatus(workflowResult.data);
      setHeadless(statusResult.data.headless_default);
      setSelectedTaskId((current) => current || tasksResult.data.tasks[0]?.id || "");
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const selectedTask = useMemo(() => tasks.find((task) => task.id === selectedTaskId) ?? null, [selectedTaskId, tasks]);
  const taskColumns: Column<DesktopTask>[] = [
    { key: "goal", title: "任务", render: (row) => <strong>{row.goal}</strong> },
    { key: "status", title: "状态", render: (row) => <StatusBadge status={row.status} />, width: "112px" },
    { key: "steps", title: "步骤", render: (row) => <span>{row.steps.length}</span>, width: "72px" },
    { key: "updated_at", title: "更新时间", render: (row) => <span className="small muted">{row.updated_at.slice(11, 19)}</span>, width: "96px" },
  ];

  async function createTask() {
    const steps = stepsText.split("\n").map((item) => item.trim()).filter(Boolean);
    if (!goal.trim() || !steps.length) return;
    setBusy(true);
    setError("");
    try {
      const created = await requestDesktopTask(goal, steps);
      setSelectedTaskId(created.data.id);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function approveTask() {
    if (!selectedTaskId) return;
    setBusy(true);
    setError("");
    try {
      await updateDesktopTaskStatus(selectedTaskId, "approved", "web user approved browser automation task");
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function runTask() {
    if (!selectedTaskId) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const response = await executeBrowserTask(selectedTaskId, {
        authorized,
        headless,
        allowedHosts: allowedHosts.split(",").map((item) => item.trim()).filter(Boolean),
      });
      setResult(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function companionAction(action: "start" | "stop" | "once") {
    setBusy(true);
    setError("");
    try {
      if (action === "start") {
        const response = await startDesktopCompanion(5);
        setCompanion(response.data);
      } else if (action === "stop") {
        const response = await stopDesktopCompanion();
        setCompanion(response.data);
      } else {
        const response = await runDesktopCompanionOnce(5);
        setCompanionRun(response.data);
      }
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function runWorkflow(action: "plan" | "setup" | "execute") {
    const steps = workflowStepsText.split("\n").map((item) => item.trim()).filter(Boolean);
    if (!workflowGoal.trim() || !steps.length) return;
    setBusy(true);
    setError("");
    setWorkflowResult(null);
    try {
      const response = action === "plan"
        ? await planDesktopWorkflow(workflowGoal, steps)
        : action === "setup"
          ? await setupDesktopWorkflow(workflowGoal, steps)
          : await executeDesktopWorkflow(workflowGoal, steps, workflowAuthorized);
      setWorkflowResult(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function runControlAction() {
    setBusy(true);
    setError("");
    setControlResult(null);
    const payload: Record<string, unknown> = { action: controlAction, authorized: controlAuthorized };
    if (controlAction === "mouse_move") {
      payload.x = controlX;
      payload.y = controlY;
    } else if (controlAction === "mouse_click") {
      payload.button = 1;
    } else if (controlAction === "type_text") {
      payload.text = controlText;
    } else if (controlAction === "hotkey") {
      payload.hotkey = controlText;
    }
    try {
      const response = await runDesktopControlAction(payload);
      setControlResult(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="桌面代理"
        description="创建、审批并执行受控浏览器任务；全权桌面控制需要单独授权"
        actions={<button className="ghost-button" onClick={() => void load()} disabled={busy}><RefreshCw size={16} />刷新</button>}
      />
      <div className="page-grid">
        {error && <div className="danger-panel">操作失败：{error}</div>}
        <div className="grid-4">
          <InfoCard icon={<Bot size={20} />} label="浏览器代理" value={friendlyStatus(status?.status ?? "pending")} note="受控浏览器内执行" status={<StatusBadge status={status?.status ?? "pending"} />} />
          <InfoCard icon={<Globe2 size={20} />} label="运行环境" value={status?.package_installed ? "已安装" : "待安装"} note={status?.install_hint ?? "浏览器自动化组件"} />
          <InfoCard icon={<ShieldCheck size={20} />} label="权限模式" value={status?.permission_mode === "sandbox" ? "沙箱模式" : String(status?.permission_mode ?? "沙箱模式")} note="任务审批 + 执行授权" />
          <InfoCard icon={<CheckCircle2 size={20} />} label="步骤上限" value={String(status?.max_steps ?? "-")} note="防止长流程失控" />
        </div>

        <div className="desktop-automation-grid">
          <Card title="创建浏览器任务" subtitle="填写目标和步骤；创建后不会自动执行。">
            <div className="desktop-task-form">
              <label>
                <span>任务目标</span>
                <input className="input" value={goal} onChange={(event) => setGoal(event.target.value)} />
              </label>
              <label>
                <span>步骤（每行一步）</span>
                <textarea className="textarea desktop-steps-textarea" value={stepsText} onChange={(event) => setStepsText(event.target.value)} />
              </label>
              <div className="row">
                <button className="primary-button" onClick={() => void createTask()} disabled={busy}><Plus size={16} />创建任务</button>
                <SkillChip>不会自动执行</SkillChip>
              </div>
            </div>
          </Card>

          <Card title="任务队列" subtitle="选择任务后先审批，再授权执行">
            <DataTable
              rows={tasks}
              columns={taskColumns}
              rowKey={(row) => row.id}
              onRowClick={(row) => setSelectedTaskId(row.id)}
              rowClassName={(row) => row.id === selectedTaskId ? "selected-row" : ""}
            />
            {!tasks.length && <p className="small muted">暂无桌面任务。创建后会出现在这里。</p>}
          </Card>
        </div>

        <div className="desktop-run-grid">
          <Card title="审批与执行" action={<StatusBadge status={selectedTask?.status ?? "pending"} />}>
            <div className="desktop-task-detail">
              <div className="definition-grid">
                <span>任务目标</span><strong>{selectedTask?.goal ?? "-"}</strong>
                <span>状态</span><StatusBadge status={selectedTask?.status ?? "pending"} />
              </div>
              <ol className="step-list">
                {(selectedTask?.steps ?? []).map((step) => <li key={step.index}>{step.description}</li>)}
                {!selectedTask && <li>请选择或创建任务</li>}
              </ol>
              <div className="desktop-run-options">
                <label className="inline-check">
                  <input type="checkbox" checked={authorized} onChange={(event) => setAuthorized(event.target.checked)} />
                  我授权执行此已审批浏览器任务
                </label>
                <label className="inline-check">
                  <input type="checkbox" checked={headless} onChange={(event) => setHeadless(event.target.checked)} />
                  后台浏览器
                </label>
                <label>
                  <span>允许域名（逗号分隔）</span>
                  <input className="input" value={allowedHosts} onChange={(event) => setAllowedHosts(event.target.value)} placeholder="example.com" />
                </label>
              </div>
              <div className="row">
                <button className="success-button" onClick={() => void approveTask()} disabled={busy || !selectedTask || selectedTask.status === "approved"}>审批任务</button>
                <button className="primary-button" onClick={() => void runTask()} disabled={busy || !selectedTask}><Play size={16} />执行浏览器任务</button>
              </div>
            </div>
          </Card>

          <Card title="执行结果">
            <div className="desktop-result-summary">
              <div className="row-between">
                <span>状态</span>
                <StatusBadge status={result?.status ?? "pending"} />
              </div>
              <div className="row-between">
                <span>报告</span>
                <strong>{result?.report_workspace_name ? "已生成" : "-"}</strong>
              </div>
              <div className="row-between">
                <span>最终页面</span>
                <strong>{result?.final_url ?? "-"}</strong>
              </div>
              <div className="row-between">
                <span>截图</span>
                <strong>{result?.screenshots?.length ?? 0}</strong>
              </div>
            </div>
            <details className="advanced-panel">
              <summary>执行诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview desktop-result-preview">{JSON.stringify(result ?? { status: "pending", message: "等待执行。" }, null, 2)}</pre>
              </div>
            </details>
          </Card>
        </div>

        <Card title="办公电脑伴随服务" subtitle="轮询已审批桌面任务；默认只生成计划并记录审计" action={<StatusBadge status={companion?.status ?? "pending"} />}>
          <div className="desktop-companion-panel">
            <div className="definition-grid">
              <span>状态</span><StatusBadge status={companion?.status ?? "pending"} />
              <span>执行模式</span><strong>{friendlyStatus(companion?.backend ?? "-")}</strong>
              <span>最近执行</span><strong>{String(companion?.last_run?.processed ?? "-")}</strong>
            </div>
            <div className="row">
              <button className="primary-button" onClick={() => void companionAction("start")} disabled={busy}>启动服务</button>
              <button className="ghost-button" onClick={() => void companionAction("once")} disabled={busy}>执行一次</button>
              <button className="danger-button" onClick={() => void companionAction("stop")} disabled={busy}>停止服务</button>
            </div>
            <details className="advanced-panel">
              <summary>伴随服务诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview desktop-result-preview">{JSON.stringify(companionRun ?? companion ?? { status: "pending" }, null, 2)}</pre>
              </div>
            </details>
          </div>
        </Card>

        <div className="desktop-run-grid">
          <Card title="本机桌面工作流" subtitle="全权模式入口；当前安全模式下会先生成计划或被门禁阻断" action={<StatusBadge status={workflowStatus?.can_execute ? "available" : "blocked"} />}>
            <div className="desktop-task-form">
              <label>
                <span>目标</span>
                <input className="input" value={workflowGoal} onChange={(event) => setWorkflowGoal(event.target.value)} />
              </label>
              <label>
                <span>步骤（每行一步）</span>
                <textarea className="textarea desktop-steps-textarea" value={workflowStepsText} onChange={(event) => setWorkflowStepsText(event.target.value)} />
              </label>
              <div className="desktop-run-options">
                <label className="inline-check">
                  <input type="checkbox" checked={workflowAuthorized} onChange={(event) => setWorkflowAuthorized(event.target.checked)} />
                  我授权执行此桌面工作流
                </label>
                <div className="definition-grid">
                  <span>权限模式</span><strong>{workflowStatus?.permission_mode === "sandbox" ? "沙箱模式" : workflowStatus?.permission_mode ?? "-"}</strong>
                  <span>可执行</span><strong>{workflowStatus?.can_execute ? "可以执行" : "需授权或目标环境不满足"}</strong>
                  <span>桌面会话</span><strong>{((workflowStatus?.preflight as { session?: { has_gui_session?: boolean } } | undefined)?.session?.has_gui_session) ? "已检测到" : "未检测到"}</strong>
                </div>
              </div>
              <div className="row">
                <button className="ghost-button" onClick={() => void runWorkflow("plan")} disabled={busy}>生成计划</button>
                <button className="ghost-button" onClick={() => void runWorkflow("setup")} disabled={busy}>生成验收包</button>
                <button className="primary-button" onClick={() => void runWorkflow("execute")} disabled={busy}>门禁执行</button>
              </div>
            </div>
          </Card>
          <Card title="桌面工作流结果" action={<StatusBadge status={workflowResult?.status ?? "pending"} />}>
            <div className="definition-grid">
              <span>状态</span><StatusBadge status={workflowResult?.status ?? workflowStatus?.status ?? "pending"} />
              <span>结果</span><strong>{workflowResult?.message ?? workflowResult?.goal ?? "等待执行"}</strong>
            </div>
            <details className="advanced-panel">
              <summary>工作流诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview desktop-result-preview">{JSON.stringify(workflowResult ?? workflowStatus ?? { status: "pending" }, null, 2)}</pre>
              </div>
            </details>
          </Card>
        </div>

        <div className="desktop-run-grid">
          <Card title="低层桌面控制" subtitle="目标机处于全权控制且存在图形会话时，可执行鼠标、键盘和截图探针" action={<StatusBadge status={controlResult?.status ?? "pending"} />}>
            <div className="desktop-task-form">
              <label className="inline-check">
                <input type="checkbox" checked={controlAuthorized} onChange={(event) => setControlAuthorized(event.target.checked)} />
                我授权执行低层桌面控制动作
              </label>
              <label>
                <span>动作</span>
                <select className="input" value={controlAction} onChange={(event) => setControlAction(event.target.value)}>
                  <option value="low_level_probe">低层控制探针</option>
                  <option value="screenshot">截图</option>
                  <option value="hotkey">热键</option>
                  <option value="type_text">输入文字</option>
                  <option value="mouse_move">移动鼠标</option>
                  <option value="mouse_click">点击</option>
                </select>
              </label>
              {(controlAction === "hotkey" || controlAction === "type_text") && (
                <label>
                  <span>{controlAction === "hotkey" ? "热键" : "文字"}</span>
                  <input className="input" value={controlText} onChange={(event) => setControlText(event.target.value)} />
                </label>
              )}
              {controlAction === "mouse_move" && (
                <div className="grid-2">
                  <label><span>X</span><input className="input" type="number" value={controlX} onChange={(event) => setControlX(Number(event.target.value))} /></label>
                  <label><span>Y</span><input className="input" type="number" value={controlY} onChange={(event) => setControlY(Number(event.target.value))} /></label>
                </div>
              )}
              <button className="primary-button" onClick={() => void runControlAction()} disabled={busy}>门禁执行动作</button>
            </div>
          </Card>
          <Card title="低层控制结果" action={<StatusBadge status={controlResult?.status ?? "pending"} />}>
            <div className="definition-grid">
              <span>状态</span><StatusBadge status={controlResult?.status ?? "pending"} />
              <span>说明</span><strong>{controlResult?.message ?? "等待执行"}</strong>
            </div>
            <details className="advanced-panel">
              <summary>低层控制诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview desktop-result-preview">{JSON.stringify(controlResult ?? { status: "pending" }, null, 2)}</pre>
              </div>
            </details>
          </Card>
        </div>

        <Card title="安全约束">
          <div className="security-summary">
            {(status?.safety ?? ["approved task required", "explicit authorization required", "workspace artifacts only"]).map((item) => <SkillChip key={item}>{item}</SkillChip>)}
          </div>
        </Card>
      </div>
    </>
  );
}

function friendlyStatus(status: unknown) {
  const value = String(status ?? "");
  const labels: Record<string, string> = {
    ok: "正常",
    available: "可用",
    completed: "已完成",
    pending: "等待",
    running: "运行中",
    blocked: "已阻止",
    failed: "失败",
    unavailable: "不可用",
    adapter_ready: "待接入",
    backend_missing: "待接入",
    needs_config: "待配置",
    playwright_browser: "受控浏览器",
  };
  return labels[value] ?? (value || "等待");
}
