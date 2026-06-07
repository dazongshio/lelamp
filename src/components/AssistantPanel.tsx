import type { ReactNode } from "react";
import { Bot, Camera, CameraOff, ChevronLeft, ChevronRight, ExternalLink, Send, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { getAssistantNotifications, getAssistantProvidersStatus, postAssistantMessage } from "../api/assistant";
import { getTask } from "../api/tasks";
import type { AssistantManualResponse, AssistantMessage, AssistantNotification, AssistantProviderStatus, TaskRecord } from "../api/types";
import { useCameraStream } from "../hooks/useCameraStream";
import { buildForegroundReply, isLocalControlText } from "../utils/foregroundReply";
import { createId } from "../utils/id";
import { ChatBubble } from "./ChatBubble";
import { StatusBadge } from "./StatusBadge";
import "./components.css";

interface AssistantPanelProps {
  initialMessages?: AssistantMessage[];
  children?: ReactNode;
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
  placeholder?: string;
}

function directReplyAttachment(kind: string): string {
  if (kind === "lamp_control") return "本地台灯控制 · 未调用 Qwen";
  if (kind === "meeting_control") return "本地会议控制 · 未调用 Qwen";
  return "直接回复 · 未触发本地任务";
}

export function AssistantPanel({
  collapsed = false,
  initialMessages = [],
  children,
  onCollapsedChange,
  placeholder = "向助手输入消息...",
}: AssistantPanelProps) {
  const [messages, setMessages] = useState<AssistantMessage[]>(initialMessages);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sessionId, setSessionId] = useState("");
  const [providerStatus, setProviderStatus] = useState<AssistantProviderStatus | null>(null);
  const cameraStream = useCameraStream(!collapsed);

  useEffect(() => {
    void getAssistantProvidersStatus()
      .then((result) => setProviderStatus(result.data))
      .catch(() => setProviderStatus(null));
  }, []);

  useEffect(() => {
    let lastSeen = "";
    const seen = new Set<string>();
    const poll = async () => {
      try {
        const result = await getAssistantNotifications(lastSeen);
        const fresh = result.data.items.filter((item) => !seen.has(item.id));
        if (!fresh.length) return;
        fresh.forEach((item) => seen.add(item.id));
        lastSeen = fresh[fresh.length - 1]?.id || lastSeen;
        setMessages((items) => mergeNotificationMessages(items, fresh));
      } catch {
        // Notification polling is opportunistic; normal chat must stay usable.
      }
    };
    const timer = window.setInterval(() => void poll(), 3500);
    void poll();
    return () => window.clearInterval(timer);
  }, []);

  async function send() {
    const text = input.trim();
    if (!text) return;
    void cameraStream.ensureStarted();
    const now = new Date().toTimeString().slice(0, 5);
    const userMessage: AssistantMessage = { id: createId("assistant-user"), role: "user", text, time: now };
    setMessages((items) => [...items, userMessage]);
    const foreground = buildForegroundReply(text, window.location.pathname.replace("/", "") || "dashboard");
    const localControl = isLocalControlText(text);
    if (!localControl) {
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
    }
    setInput("");
    setSending(true);
    try {
      const result = await postAssistantMessage(text, {
        page: window.location.pathname.replace("/", "") || "dashboard",
        foreground_reply: foreground.text,
        foreground_intent: foreground.intent,
        foreground_mode: foreground.mode,
      }, { sessionId, page: window.location.pathname.replace("/", "") || "dashboard", speak: true });
      setSessionId(result.data.session_id);
      if (result.data.assistant_message) {
        setMessages((items) => [
          ...items,
          {
            id: result.data.message_id,
            role: "assistant",
            text: result.data.assistant_message?.text ?? "",
            time: new Date().toTimeString().slice(0, 5),
            status: result.data.assistant_message?.provider_status ?? "completed",
            attachment: directReplyAttachment(result.data.route.kind),
          },
        ]);
        return;
      }
      const task = result.data.task;
      if (task?.task_id) {
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
    } catch (error) {
      const reply: AssistantMessage = {
        id: createId("assistant-error"),
        role: "assistant",
        text: apiErrorMessage(error),
        time: new Date().toTimeString().slice(0, 5),
        status: "error",
      };
      setMessages((items) => [...items, reply]);
    } finally {
      setSending(false);
    }
  }

  async function pollAssistantTask(taskId: string, queryMode: boolean) {
    for (let attempt = 0; attempt < 90; attempt += 1) {
      await delay(1200);
      const task = (await getTask(taskId)).data;
      if (["completed", "blocked", "failed", "waiting_confirmation"].includes(String(task.status))) {
        const final = assistantResponseFromTask(task);
        setMessages((items) => [
          ...items,
          {
            id: final.message_id || createId("assistant-final"),
            role: "assistant",
            text: final.result.display_text || final.result.summary,
            time: new Date().toTimeString().slice(0, 5),
            status: final.result.status,
            attachment: userFacingTaskAttachment(final, queryMode),
          },
        ]);
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
    <aside className={`assistant-panel ${collapsed ? "assistant-panel--collapsed" : ""}`}>
      <button
        aria-expanded={!collapsed}
        aria-label={collapsed ? "展开右侧助手" : "折叠右侧助手"}
        className="assistant-panel__collapse"
        onClick={() => onCollapsedChange?.(!collapsed)}
        title={collapsed ? "展开助手" : "折叠助手"}
        type="button"
      >
        {collapsed ? <ChevronLeft size={18} /> : <ChevronRight size={18} />}
      </button>
      <div className="assistant-panel__collapsed-rail" aria-hidden={!collapsed}>
        <span className="assistant-panel__icon">
          <Bot size={17} />
        </span>
        <span className="online-dot" />
      </div>
      <div className="assistant-panel__inner" aria-hidden={collapsed}>
        <div className="assistant-panel__header">
          <div className="row">
            <span className="assistant-panel__icon">
              <Bot size={17} />
            </span>
            <strong>右侧助手</strong>
          </div>
          <span className="online-dot">在线</span>
        </div>
        <div className="assistant-panel__status">
          {providerStatus && (
            <>
            <StatusBadge status={providerStatus.qwen_omni.status} label={`云端助手 ${friendlyStatus(providerStatus.qwen_omni.status)}`} />
            <StatusBadge status={providerStatus.openclaw.status} label={`本地代理 ${friendlyStatus(providerStatus.openclaw.status)}`} />
            <StatusBadge status={providerStatus.input.pi_mic} label={`麦克风 ${friendlyStatus(providerStatus.input.pi_mic)}`} />
            </>
          )}
          <StatusBadge status={cameraStream.isRunning ? "online" : "stopped"} label={`相机 ${cameraStream.isRunning ? "预览中" : "已关闭"}`} />
          {cameraStream.isRunning && cameraStream.previewUrl && (
            <a className="camera-preview-link" href={cameraStream.previewUrl} target="_blank" rel="noreferrer">
              <ExternalLink size={13} />
              看视角
            </a>
          )}
          <button
            className="camera-toggle-button"
            onClick={() => void (cameraStream.isRunning ? cameraStream.stop() : cameraStream.start())}
            disabled={cameraStream.loading}
            type="button"
          >
            {cameraStream.isRunning ? <CameraOff size={13} /> : <Camera size={13} />}
            {cameraStream.isRunning ? "关闭相机常开" : "开启相机预览"}
          </button>
        </div>
        {cameraStream.error && <div className="assistant-panel__notice">相机预览不可用：{cameraStream.error}</div>}
        <div className="assistant-panel__content">
          {messages.map((message) => (
            <ChatBubble key={message.id} message={message} />
          ))}
          {children}
        </div>
        <div className="assistant-panel__input">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void send();
            }}
            placeholder={placeholder}
            disabled={sending}
          />
          <button className="send-button" aria-label="发送" onClick={() => void send()} disabled={sending}>
            <Send size={17} />
          </button>
        </div>
        <div className="assistant-panel__foot">
          <span>助手可能会出错，请验证重要信息。</span>
          <button className="plain-button" onClick={() => setMessages([])}>
            <Trash2 size={14} />
            清除对话
          </button>
        </div>
      </div>
    </aside>
  );
}

function notificationKey(item: AssistantNotification): string {
  const meetingId = typeof item.payload?.meeting_id === "string" ? item.payload.meeting_id.trim() : "";
  return item.event && meetingId ? `${item.event}:${meetingId}` : "";
}

function notificationMessage(item: AssistantNotification): AssistantMessage {
  return {
    id: item.id,
    role: "assistant" as const,
    text: cleanAssistantText(item.text),
    time: new Date(item.timestamp).toTimeString().slice(0, 5),
    status: item.status,
    attachment: userFacingNotificationAttachment(item),
    notificationKey: notificationKey(item) || undefined,
  };
}

function mergeNotificationMessages(items: AssistantMessage[], notifications: AssistantNotification[]): AssistantMessage[] {
  let merged = [...items];
  notifications.forEach((item) => {
    const message = notificationMessage(item);
    if (message.notificationKey) {
      const existingIndex = merged.findIndex((candidate) => candidate.notificationKey === message.notificationKey);
      if (existingIndex >= 0) {
        merged = [
          ...merged.slice(0, existingIndex),
          message,
          ...merged.slice(existingIndex + 1),
        ];
        return;
      }
    }
    merged = [...merged, message];
  });
  return merged;
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function userFacingTaskAttachment(final: AssistantManualResponse, queryMode: boolean): string | undefined {
  if (queryMode) return undefined;
  if (final.requires_confirmation && final.confirmation?.summary) {
    return `需要确认：${final.confirmation.summary}`;
  }
  const outputCount = final.result.outputs?.length ?? 0;
  if (outputCount > 0) return `已生成 ${outputCount} 个工作区文件，可在对应页面查看。`;
  return undefined;
}

function userFacingNotificationAttachment(item: AssistantNotification): string | undefined {
  const attachment = String(item.attachment || "").trim();
  if (!attachment || looksLikeDiagnosticLog(attachment)) return undefined;
  return cleanAssistantText(attachment);
}

function cleanAssistantText(value: unknown): string {
  return String(value ?? "")
    .replace(/\/[^\s]+/g, "[工作区文件]")
    .replace(/\b(provider_status|mic_status|selected_mic|capture_status|capture_audio_bytes|capture_audio_rms|capture_audio_peak|openclaw_status|content_status|provider_error|openclaw_error|error|transcript|tingwu_minutes|openclaw_minutes)\s*:\s*[^\n]+/gi, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function looksLikeDiagnosticLog(value: string): boolean {
  const text = value.toLowerCase();
  const diagnosticKeys = [
    "provider_status:",
    "mic_status:",
    "selected_mic:",
    "capture_status:",
    "capture_audio_bytes:",
    "capture_audio_rms:",
    "capture_audio_peak:",
    "openclaw_status:",
    "content_status:",
    "provider_error:",
    "openclaw_error:",
    "transcript:",
    "tingwu_minutes:",
    "openclaw_minutes:",
  ];
  return diagnosticKeys.some((key) => text.includes(key)) || /\/home\/|\/workspace\/|\.jsonl\b|\.md\b|\.wav\b/.test(value);
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

function friendlyStatus(status: unknown) {
  const value = String(status ?? "");
  const labels: Record<string, string> = {
    ok: "正常",
    online: "在线",
    enabled: "已启用",
    available: "可用",
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
