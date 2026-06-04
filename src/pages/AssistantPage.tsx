import { Camera, CameraOff, CheckCircle2, ExternalLink, Mail, Mic, Send, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { getAssistantProvidersStatus, postAssistantMessage, postAssistantPiVoiceOnce } from "../api/assistant";
import { getSecurity } from "../api/security";
import { getSkills } from "../api/skills";
import { getRecentTasks, getTask } from "../api/tasks";
import type { AssistantManualResponse, AssistantMessage, AssistantProviderStatus, SecurityStatus, SkillSpec, TaskRecord } from "../api/types";
import { Card } from "../components/Card";
import { ChatBubble } from "../components/ChatBubble";
import { PageHeader } from "../components/PageHeader";
import { SkillChip } from "../components/SkillChip";
import { StatusBadge } from "../components/StatusBadge";
import { mockSecurity } from "../data/mockSecurity";
import { useCameraStream } from "../hooks/useCameraStream";
import { buildForegroundReply } from "../utils/foregroundReply";
import { createId } from "../utils/id";
import "./pages.css";

const quickPrompts = ["总结今天的会议纪要", "查找 5 月份的项目文档", "生成周报草稿", "显示系统状态", "清理临时文件"];

export function AssistantPage() {
  const [messages, setMessages] = useState<AssistantMessage[]>([
    {
      id: "assistant-live",
      role: "assistant",
      text: "我可以帮你整理文档、会议纪要和投影内容。涉及文件、发送邮件或桌面操作时会先请求确认。",
      time: new Date().toTimeString().slice(0, 5),
      status: "online",
    },
  ]);
  const [input, setInput] = useState("");
  const [lastResult, setLastResult] = useState<AssistantManualResponse | null>(null);
  const [skills, setSkills] = useState<SkillSpec[]>([]);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [security, setSecurity] = useState<SecurityStatus>(mockSecurity);
  const [providerStatus, setProviderStatus] = useState<AssistantProviderStatus | null>(null);
  const [sessionId, setSessionId] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [recordingPi, setRecordingPi] = useState(false);
  const cameraStream = useCameraStream(true);
  const speechStatus = "语音交互会在设备侧执行；当前页面只显示文字结果。";

  useEffect(() => {
    void Promise.all([getSkills(), getRecentTasks(5), getSecurity(), getAssistantProvidersStatus()])
      .then(([skillResult, taskResult, securityResult, providerResult]) => {
        setSkills(skillResult.data.skills);
        setTasks(taskResult.data.items);
        setSecurity(securityResult.data);
        setProviderStatus(providerResult.data);
      })
      .catch((err) => setError(apiErrorMessage(err)));
  }, []);

  async function sendMessage(text = input) {
    const trimmed = text.trim();
    if (!trimmed) return;
    void cameraStream.ensureStarted();
    const now = new Date().toTimeString().slice(0, 5);
    setMessages((items) => [
      ...items,
      { id: createId("assistant-user"), role: "user", text: trimmed, time: now },
    ]);
    const foreground = buildForegroundReply(trimmed, "assistant");
    setMessages((items) => [
      ...items,
      {
        id: createId("assistant-foreground"),
        role: "assistant",
        text: foreground.text,
        time: new Date().toTimeString().slice(0, 5),
        status: "running",
        attachment: foreground.attachment || undefined,
      },
    ]);
    setInput("");
    setSending(true);
    setError("");
    try {
      const result = await postAssistantMessage(trimmed, {
        page: "assistant",
        foreground_reply: foreground.text,
        foreground_intent: foreground.intent,
        foreground_mode: foreground.mode,
      }, { sessionId, page: "assistant", speak: true });
      setSessionId(result.data.session_id);
      const assistantMessage = result.data.assistant_message;
      if (result.data.route.kind === "chat" && assistantMessage) {
        const chatResult: AssistantManualResponse = {
          message_id: result.data.message_id,
          detected_intent: result.data.route.intent,
          skills_to_call: [],
          requires_confirmation: false,
          confirmation: null,
          result: {
            status: assistantMessage.provider_status ?? "completed",
            summary: assistantMessage.text,
            display_text: assistantMessage.text,
            outputs: [],
          },
          task_id: "",
        };
        setLastResult(chatResult);
        setMessages((items) => [
          ...items,
          {
            id: result.data.message_id,
            role: "assistant",
            text: assistantMessage.text,
            time: new Date().toTimeString().slice(0, 5),
            status: assistantMessage.provider_status ?? "completed",
            attachment: "直接回复 · 未触发本地任务",
          },
        ]);
      } else {
        const task = result.data.task;
        if (!task?.task_id) return;
        setMessages((items) => [
          ...items,
          {
            id: createId("assistant-task"),
            role: "system",
            text: "任务已创建，正在后台处理。",
            time: new Date().toTimeString().slice(0, 5),
            status: "running",
            attachment: `意图：${result.data.route.intent}`,
          },
        ]);
        void pollAssistantTask(task.task_id, foreground.mode === "query");
      }
      const taskResult = await getRecentTasks(5);
      setTasks(taskResult.data.items);
    } catch (err) {
      const message = apiErrorMessage(err);
      setError(message);
      setMessages((items) => [
        ...items,
        { id: createId("assistant-error"), role: "assistant", text: message, time: new Date().toTimeString().slice(0, 5), status: "error" },
      ]);
    } finally {
      setSending(false);
    }
  }

  async function capturePiVoice() {
    void cameraStream.ensureStarted();
    setRecordingPi(true);
    setError("");
    try {
      setMessages((items) => [
        ...items,
        {
          id: createId("assistant-pi-voice-start"),
          role: "system",
          text: "正在使用树莓派侧麦克风录音，请说话。",
          time: new Date().toTimeString().slice(0, 5),
          status: "running",
        },
      ]);
      const result = await postAssistantPiVoiceOnce({ seconds: 4, page: "assistant", speak: true });
      if (result.data.status !== "completed" || !result.data.transcript) {
        throw new Error(result.data.message || `Pi voice input ${result.data.status}`);
      }
      setMessages((items) => [
        ...items,
        {
          id: createId("assistant-pi-voice-user"),
          role: "user",
          text: result.data.transcript,
          time: new Date().toTimeString().slice(0, 5),
          status: "completed",
            attachment: "来源：设备麦克风",
        },
      ]);
      const assistant = result.data.assistant;
      if (assistant?.route.kind === "chat" && assistant.assistant_message) {
        setMessages((items) => [
          ...items,
          {
            id: assistant.message_id,
            role: "assistant",
            text: assistant.assistant_message?.text ?? "",
            time: new Date().toTimeString().slice(0, 5),
            status: assistant.assistant_message?.provider_status ?? "completed",
            attachment: "直接回复 · 未触发本地任务",
          },
        ]);
      } else {
        const task = assistant?.task;
        if (!task?.task_id) return;
        const routeIntent = assistant?.route.intent ?? "unknown";
        setMessages((items) => [
          ...items,
          {
            id: createId("assistant-pi-voice-task"),
            role: "system",
            text: "语音任务已创建，正在后台处理。",
            time: new Date().toTimeString().slice(0, 5),
            status: "running",
            attachment: `意图：${routeIntent}`,
          },
        ]);
        void pollAssistantTask(task.task_id, false);
      }
    } catch (err) {
      const message = apiErrorMessage(err);
      setError(message);
      setMessages((items) => [
        ...items,
        { id: createId("assistant-pi-voice-error"), role: "assistant", text: message, time: new Date().toTimeString().slice(0, 5), status: "error" },
      ]);
    } finally {
      setRecordingPi(false);
    }
  }

  async function pollAssistantTask(taskId: string, queryMode: boolean) {
    for (let attempt = 0; attempt < 90; attempt += 1) {
      await delay(1200);
      const task = (await getTask(taskId)).data;
      if (["completed", "blocked", "failed", "waiting_confirmation"].includes(String(task.status))) {
        const final = assistantResponseFromTask(task);
        setLastResult(final);
        setMessages((items) => [
          ...items,
          {
            id: final.message_id || createId("assistant-final"),
            role: "assistant",
            text: final.result.display_text || final.result.summary,
            time: new Date().toTimeString().slice(0, 5),
            status: final.result.status,
            attachment: queryMode ? undefined : [
              `意图：${final.detected_intent}`,
              `能力：${final.skills_to_call.map((skill) => `${skill.name}(${friendlyStatus(skill.status)})`).join(", ") || "未调用"}`,
              final.requires_confirmation && final.confirmation ? `确认：${final.confirmation.summary}` : "确认：无需高风险确认",
            ].join("\n"),
          },
        ]);
        const taskResult = await getRecentTasks(5);
        setTasks(taskResult.data.items);
        return;
      }
    }
    setMessages((items) => [
      ...items,
      {
        id: createId("assistant-timeout"),
        role: "assistant",
        text: "后台任务仍在处理中，请稍后到最近任务或审计日志查看。",
        time: new Date().toTimeString().slice(0, 5),
        status: "running",
      },
    ]);
  }

  return (
    <>
      <PageHeader title="智能助手" description="通过自然语言整理文档、会议、投影和办公任务" />
      <div className="assistant-page-grid">
        {error && <div className="danger-panel span-2">操作失败：{error}</div>}
        <Card className="conversation-card">
          <div className="conversation">
            {messages.map((message) => <ChatBubble key={message.id} message={message} />)}
          </div>
          <div className="quick-prompts">
            {quickPrompts.map((prompt) => (
              <button className="ghost-button" key={prompt} onClick={() => sendMessage(prompt)}>{prompt}</button>
            ))}
          </div>
          <div className="assistant-speech-controls">
            <span className="small muted">{speechStatus}</span>
            {providerStatus && (
              <>
                <StatusBadge status={providerStatus.qwen_omni.status} label={`云端助手 ${friendlyStatus(providerStatus.qwen_omni.status)}`} />
                <StatusBadge status={providerStatus.openclaw.status} label={`本地代理 ${friendlyStatus(providerStatus.openclaw.status)}`} />
                <StatusBadge status={providerStatus.qwen_omni.tts.status} label={`语音输出 ${friendlyStatus(providerStatus.qwen_omni.tts.status)}`} />
                <StatusBadge status={providerStatus.input.pi_mic} label={`麦克风 ${friendlyStatus(providerStatus.input.pi_mic)}`} />
                <button className="ghost-button" onClick={() => void capturePiVoice()} disabled={recordingPi || providerStatus.input.pi_mic !== "available"}>
                  <Mic size={15} />
                  树莓派录音
                </button>
              </>
            )}
            <StatusBadge status={cameraStream.isRunning ? "online" : "stopped"} label={`相机 ${cameraStream.isRunning ? "预览中" : "已关闭"}`} />
            {cameraStream.isRunning && cameraStream.previewUrl && (
              <a className="ghost-button" href={cameraStream.previewUrl} target="_blank" rel="noreferrer">
                <ExternalLink size={15} />
                查看设备视角
              </a>
            )}
            <button
              className="ghost-button"
              onClick={() => void (cameraStream.isRunning ? cameraStream.stop() : cameraStream.start())}
              disabled={cameraStream.loading}
            >
              {cameraStream.isRunning ? <CameraOff size={15} /> : <Camera size={15} />}
              {cameraStream.isRunning ? "关闭相机常开" : "开启相机预览"}
            </button>
            {cameraStream.error && <span className="small muted">相机预览不可用：{cameraStream.error}</span>}
          </div>
          <div className="assistant-input-large">
            <input value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void sendMessage(); }} placeholder="输入你的问题，或选择 / 使用技能..." disabled={sending} />
            <button className="send-button" onClick={() => void sendMessage()} disabled={sending}><Send size={18} /></button>
          </div>
        </Card>

        <Card title="执行进展">
          <div className="execution-grid">
            <section>
              <h3>识别意图</h3>
              <SkillChip>{lastResult?.detected_intent ?? "等待输入"}</SkillChip>
            </section>
            <section>
              <h3>计划能力</h3>
              <div className="list-rows compact">
                {(lastResult?.skills_to_call.length ? lastResult.skills_to_call : skills.slice(0, 3)).map((skill) => (
                  <div className="row-between" key={skill.name}>
                    <SkillChip>{skill.name}</SkillChip>
                    <CheckCircle2 size={16} color="var(--color-success)" />
                    <StatusBadge status={skill.status} label={friendlyStatus(skill.status)} />
                  </div>
                ))}
              </div>
            </section>
            <section>
              <h3>确认状态</h3>
              <p className="warning-inline">{lastResult?.confirmation?.summary ?? "高风险动作会等待逐任务确认"}</p>
              <div className="row-between">
                <span>{lastResult?.requires_confirmation ? "需要用户确认" : "无需高风险确认"}</span>
                <StatusBadge status={lastResult?.requires_confirmation ? "needs_confirmation" : "ok"} />
              </div>
            </section>
            <section>
              <h3>执行状态</h3>
              <ol className="step-list">
                {(lastResult?.skills_to_call ?? []).map((skill) => (
                  <li key={skill.name}>{skill.name} <StatusBadge status={skill.status} label={friendlyStatus(skill.status)} /></li>
                ))}
                {!lastResult && <li>等待用户输入 <StatusBadge status="pending" /></li>}
              </ol>
            </section>
            <section className="span-2">
              <h3>结果摘要</h3>
              <p>{lastResult?.result.display_text ?? lastResult?.result.summary ?? "尚未执行。发送消息后这里显示助手的处理结果。"}</p>
              {lastResult?.result.outputs.map((output) => (
                <div className="result-file" key={output.path}>
                  <Mail size={18} />
                  <span>{compactDisplayPath(output.path)}</span>
                  <strong>{output.type}</strong>
                </div>
              ))}
              {lastResult?.result.details && (
                <details className="advanced-panel assistant-result-detail">
                  <summary>高级诊断</summary>
                  <div className="advanced-panel__content">
                    <div className="row-between">
                      <strong>工具</strong>
                      <SkillChip>{lastResult.result.details.tool ?? "unknown"}</SkillChip>
                    </div>
                    <pre className="json-preview">{JSON.stringify({
                      args: lastResult.result.details.tool_args,
                      result: lastResult.result.details.tool_result,
                    }, null, 2)}</pre>
                  </div>
                </details>
              )}
              <div className="row">
                <button className="primary-button" disabled>自动发送邮件已默认阻止</button>
                <button className="ghost-button" disabled={!lastResult}>继续编辑草稿</button>
                <button className="ghost-button" disabled={!lastResult}>保存到共享空间</button>
              </div>
            </section>
          </div>
        </Card>

        <div className="stack">
          <Card title="最近任务" action={<a className="link-blue">查看全部</a>}>
            <div className="list-rows compact">
              {tasks.slice(0, 5).map((task) => (
                <div className="row-between" key={task.task_id}>
                  <span>{task.title}</span>
                  <StatusBadge status={task.status} />
                </div>
              ))}
              {!tasks.length && <span className="small muted">暂无任务。</span>}
            </div>
          </Card>
          <Card title="安全模式">
            <SkillChip>{security.permission_mode === "sandbox" ? "沙箱模式" : security.permission_mode}</SkillChip>
            <div className="row mode-badges">
              <StatusBadge status={security.permission_mode === "sandbox" ? "enabled" : "warning"} label={security.permission_mode === "sandbox" ? "沙箱已启用" : "需确认"} tone="primary" />
              <StatusBadge status={security.desktop_backend === "audit_only" ? "warning" : "blocked"} label={security.desktop_backend === "audit_only" ? "仅审计预览" : "全权需授权"} tone="warning" />
            </div>
            <p className="small muted">只处理用户授权的文件和任务；高风险动作会先等待确认。</p>
          </Card>
          <Card title="用户确认请求（需处理）">
            <div className="confirmation-box">
              <ShieldAlert size={20} />
              <strong>{lastResult?.confirmation?.summary ?? "暂无待确认请求"}</strong>
              <span>风险等级：{lastResult?.confirmation?.risk_level ?? "-"} · 高风险动作不会自动执行</span>
              <div className="row">
                <button className="success-button" disabled={!lastResult?.requires_confirmation}>允许执行</button>
                <button className="danger-button" disabled={!lastResult?.requires_confirmation}>拒绝</button>
                <button className="ghost-button">忽略</button>
              </div>
            </div>
          </Card>
          <Card title="安全提示">
            <p>写入、删除、投影控制、邮件发送等高风险操作必须经确认，可随时拒绝。</p>
          </Card>
        </div>

        <Card title="可用能力" className="skills-panel">
          <div className="skill-grid">
            {skills.map((skill) => <SkillChip key={skill.name} muted={!["available", "implemented"].includes(String(skill.status))}>{skill.name} · {friendlyStatus(skill.status)}</SkillChip>)}
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
    online: "在线",
    enabled: "已启用",
    available: "可用",
    implemented: "已实现",
    completed: "已完成",
    running: "运行中",
    pending: "等待",
    blocked: "已阻止",
    failed: "失败",
    unavailable: "不可用",
    adapter_ready: "待接入",
    backend_missing: "待接入",
    needs_config: "待配置",
  };
  return labels[value] ?? (value || "等待");
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

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function assistantResponseFromTask(task: TaskRecord): AssistantManualResponse {
  const output = task.output as Partial<AssistantManualResponse> | undefined;
  if (output?.result) {
    return output as AssistantManualResponse;
  }
  return {
    message_id: createId("assistant-task-result"),
    detected_intent: "unknown",
    skills_to_call: [],
    requires_confirmation: false,
    confirmation: null,
    result: {
      status: task.status,
      summary: task.error?.message ? String(task.error.message) : "任务已结束，但没有可展示的结果。",
      display_text: task.error?.message ? String(task.error.message) : "任务已结束，但没有可展示的结果。",
      outputs: [],
    },
    task_id: task.task_id,
  };
}
