import { Lightbulb, Mic, Radio, RefreshCw, Send, Speaker, Speech, Volume2, Waves } from "lucide-react";
import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { apiErrorMessage } from "../api/client";
import {
  getVoiceRealtimeVoices,
  getVoiceStatus,
  postVoiceCaptureOnce,
  sendLeLampVoiceCommand,
  sendVoiceConversationTurn,
  startVoiceAssistant,
  startVoiceConversation,
  stopVoiceAssistant,
  stopVoiceConversation,
  updateVoiceRealtimeVoice,
} from "../api/assistant";
import type { LeLampVoiceCommandResponse, VoiceAssistantProcessStatus, VoiceCaptureResponse, VoiceConversationResponse, VoiceRealtimeVoicesResponse, VoiceStatus } from "../api/types";
import { Card, InfoCard } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { SkillChip } from "../components/SkillChip";
import { StatusBadge } from "../components/StatusBadge";
import "./pages.css";

export function VoicePage() {
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [realtimeVoices, setRealtimeVoices] = useState<VoiceRealtimeVoicesResponse | null>(null);
  const [selectedRealtimeVoice, setSelectedRealtimeVoice] = useState("");
  const [voiceAssistant, setVoiceAssistant] = useState<VoiceAssistantProcessStatus | null>(null);
  const [capture, setCapture] = useState<VoiceCaptureResponse | null>(null);
  const [conversation, setConversation] = useState<VoiceConversationResponse | null>(null);
  const [authorized, setAuthorized] = useState(false);
  const [conversationAuthorized, setConversationAuthorized] = useState(false);
  const [seconds, setSeconds] = useState(4);
  const [wakeWord, setWakeWord] = useState("小灯");
  const [turnText, setTurnText] = useState("小灯 帮我总结今天的工作状态");
  const [lampCommandText, setLampCommandText] = useState("点头");
  const [lampCommand, setLampCommand] = useState<LeLampVoiceCommandResponse | null>(null);
  const [rememberTurn, setRememberTurn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [lampBusy, setLampBusy] = useState(false);
  const [voiceAssistantBusy, setVoiceAssistantBusy] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [conversationBusy, setConversationBusy] = useState(false);
  const [error, setError] = useState("");
  const [voiceMessage, setVoiceMessage] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [statusResponse, voicesResponse] = await Promise.all([getVoiceStatus(), getVoiceRealtimeVoices()]);
      setStatus(statusResponse.data);
      setRealtimeVoices(voicesResponse.data);
      setVoiceAssistant(statusResponse.data.assistant_process ?? null);
      setSelectedRealtimeVoice(String(voicesResponse.data.voice || statusResponse.data.realtime.voice || ""));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function captureOnce() {
    setBusy(true);
    setError("");
    setCapture(null);
    try {
      const response = await postVoiceCaptureOnce({ seconds, authorized, speak: false });
      setCapture(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function startConversation() {
    setConversationBusy(true);
    setError("");
    try {
      const response = await startVoiceConversation({ authorized: conversationAuthorized, wakeWord });
      setConversation(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setConversationBusy(false);
    }
  }

  async function sendTurn() {
    const sessionId = conversation?.session?.session_id ?? conversation?.session_id;
    if (!sessionId) {
      setError("请先开启连续对话会话。");
      return;
    }
    setConversationBusy(true);
    setError("");
    try {
      const response = await sendVoiceConversationTurn({
        sessionId,
        text: turnText,
        wakeRequired: true,
        remember: rememberTurn,
        speak: false,
      });
      setConversation(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setConversationBusy(false);
    }
  }

  async function stopConversation() {
    const sessionId = conversation?.session?.session_id ?? conversation?.session_id;
    if (!sessionId) return;
    setConversationBusy(true);
    setError("");
    try {
      const response = await stopVoiceConversation(sessionId);
      setConversation(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setConversationBusy(false);
    }
  }

  async function sendLampCommand(text = lampCommandText) {
    const commandText = text.trim();
    if (!commandText) {
      setError("请输入台灯控制文字。");
      return;
    }
    setLampCommandText(commandText);
    setLampBusy(true);
    setError("");
    try {
      const response = await sendLeLampVoiceCommand(commandText);
      setLampCommand(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLampBusy(false);
    }
  }

  async function applyRealtimeVoice() {
    const voice = selectedRealtimeVoice.trim();
    if (!voice) {
      setError("请选择 Qwen 音色。");
      return;
    }
    setVoiceBusy(true);
    setError("");
    setVoiceMessage("");
    try {
      const response = await updateVoiceRealtimeVoice(voice);
      setRealtimeVoices(response.data.realtime);
      setSelectedRealtimeVoice(response.data.voice);
      setVoiceMessage(`已切换到 ${response.data.voice}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setVoiceBusy(false);
    }
  }

  async function startRealtimeVoiceAssistant() {
    setVoiceAssistantBusy(true);
    setError("");
    try {
      const response = await startVoiceAssistant();
      setVoiceAssistant(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setVoiceAssistantBusy(false);
    }
  }

  async function stopRealtimeVoiceAssistant() {
    setVoiceAssistantBusy(true);
    setError("");
    try {
      const response = await stopVoiceAssistant();
      setVoiceAssistant(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setVoiceAssistantBusy(false);
    }
  }

  const voiceOptions = realtimeVoices?.voices ?? status?.realtime.voices ?? [];
  const selectedVoiceDetail = voiceOptions.find((item) => item.voice === selectedRealtimeVoice);
  const realtimeAssistantProcess = status?.realtime.assistant_process as VoiceAssistantProcessStatus | undefined;
  const assistantProcess = voiceAssistant ?? status?.assistant_process ?? realtimeAssistantProcess;

  return (
    <>
      <PageHeader
        title="语音交互"
        description="唤醒词、语音识别、语音播报、麦克风和扬声器预检；录音必须手动授权"
        actions={<button className="ghost-button" onClick={() => void load()}><RefreshCw size={16} />刷新</button>}
      />
      <div className="page-grid">
        {error && <div className="danger-panel">操作失败：{error}</div>}
        <div className="grid-4">
          <InfoCard icon={<Speech size={20} />} label="唤醒词" value={friendlyStatus(status?.wake_word.status)} note={String(status?.wake_word.default_wake_word ?? "小灯")} status={<StatusBadge status={String(status?.wake_word.status ?? "pending")} />} />
          <InfoCard icon={<Waves size={20} />} label="语音活动检测" value={friendlyStatus(status?.vad.status)} note="用于判断一句话是否结束" status={<StatusBadge status={String(status?.vad.status ?? "pending")} />} />
          <InfoCard icon={<Mic size={20} />} label="麦克风" value={friendlyStatus(status?.mic.status)} note={status?.mic.configured_device ? "已选择输入设备" : "自动选择"} status={<StatusBadge status={status?.mic.status ?? "pending"} />} />
          <InfoCard icon={<Volume2 size={20} />} label="语音播报" value={friendlyStatus(status?.tts.status)} note={status?.tts.voice ? "已配置声音" : "等待配置"} status={<StatusBadge status={String(status?.tts.status ?? "pending")} />} />
        </div>

        <div className="voice-grid">
          <Card title="语音能力状态">
            <div className="voice-status-grid">
              <VoiceStatusRow icon={<Radio size={16} />} label="实时语音" value={String(status?.realtime.status ?? "pending")} detail={friendlyStatus(status?.realtime.status)} />
              <VoiceStatusRow icon={<Mic size={16} />} label="语音识别" value={String(status?.asr.status ?? "pending")} detail={friendlyStatus(status?.asr.status)} />
              <VoiceStatusRow icon={<Speaker size={16} />} label="扬声器" value={String(status?.speaker.status ?? "pending")} detail={status?.speaker.configured_device ? "已选择输出设备" : "等待选择输出"} />
              <VoiceStatusRow icon={<Waves size={16} />} label="断句" value={String(status?.vad.status ?? "pending")} detail={status?.vad.endpointing ? "已启用" : "等待检测"} />
              <VoiceStatusRow icon={<Speech size={16} />} label="连续对话" value={String(status?.conversation?.status ?? "pending")} detail={friendlyStatus(status?.conversation?.status)} />
            </div>
            <div className="realtime-voice-panel">
              <label>
                <span>Qwen 音色</span>
                <select className="input" value={selectedRealtimeVoice} onChange={(event) => setSelectedRealtimeVoice(event.target.value)}>
                  {voiceOptions.length === 0 && <option value="">等待后端返回音色</option>}
                  {voiceOptions.map((item) => (
                    <option key={item.voice} value={item.voice}>
                      {item.label && item.label !== item.voice ? `${item.label} / ${item.voice}` : item.voice}
                    </option>
                  ))}
                </select>
              </label>
              <button className="primary-button" onClick={() => void applyRealtimeVoice()} disabled={voiceBusy || !selectedRealtimeVoice}>
                <Volume2 size={16} />应用音色
              </button>
              <div className="realtime-voice-note span-2">
                <strong>{(selectedVoiceDetail?.label ?? selectedRealtimeVoice) || "未选择"}</strong>
                <span>
                  {selectedVoiceDetail?.description ?? "保存后会写入 DASHSCOPE_REALTIME_VOICE，新的实时语音会话使用该音色。"}
                </span>
                <small>
                  当前模型：{realtimeVoices?.model ?? status?.realtime.model ?? "pending"}；当前音色：{realtimeVoices?.voice ?? status?.realtime.voice ?? "pending"}
                  {voiceMessage ? `；${voiceMessage}` : ""}
                </small>
              </div>
              {realtimeVoices?.current_voice_supported === false && (
                <div className="warning-panel span-2">当前保存的音色不在该模型支持列表内，请重新选择并应用。</div>
              )}
            </div>
            <details className="advanced-panel">
              <summary>语音诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview voice-result-preview">{JSON.stringify(status ?? { status: "pending" }, null, 2)}</pre>
              </div>
            </details>
          </Card>

          <Card title="台灯文字命令" subtitle="直接复用应用内台灯语音 skill；输入文字后按同一套本地控制逻辑执行">
            <div className="lamp-command-form">
              <label className="span-2">
                <span>控制文字</span>
                <textarea className="input" value={lampCommandText} onChange={(event) => setLampCommandText(event.target.value)} />
              </label>
              <div className="lamp-command-actions span-2">
                <button className="primary-button" onClick={() => void sendLampCommand()} disabled={lampBusy}>
                  <Send size={16} />发送
                </button>
                {["开启语音助手", "关闭语音助手", "点头", "摇头", "停止跟随", "回到默认状态", "扫描成 PDF", "台灯状态"].map((item) => (
                  <button className="ghost-button" key={item} onClick={() => void sendLampCommand(item)} disabled={lampBusy}>{item}</button>
                ))}
              </div>
            </div>
            <div className="definition-grid">
              <span>执行状态</span><StatusBadge status={lampCommand?.status ?? "pending"} />
              <span>识别动作</span><strong>{lampCommandAction(lampCommand)}</strong>
              <span>回复</span><strong>{lampCommand?.reply ?? "等待输入文字命令"}</strong>
              <span>硬件结果</span><strong>{lampCommand?.hardware_result ?? lampCommand?.message ?? "尚未执行"}</strong>
              <span>扫描 PDF</span><strong>{lampCommandPdf(lampCommand)}</strong>
            </div>
            <details className="advanced-panel">
              <summary>台灯命令诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview voice-result-preview">{JSON.stringify(lampCommand ?? { status: "pending", text: lampCommandText }, null, 2)}</pre>
              </div>
            </details>
          </Card>

          <Card title="实时语音助手" subtitle="启动设备侧 Qwen Omni 实时语音循环；会监听服务器/树莓派麦克风，并把回复播到服务器扬声器">
            <div className="voice-assistant-control">
              <div className="definition-grid">
                <span>状态</span><StatusBadge status={assistantProcess?.status ?? "pending"} />
                <span>音色</span><strong>{assistantProcess?.voice ?? status?.realtime.voice ?? "等待加载"}</strong>
                <span>进程</span><strong>{assistantProcess?.pid ? `PID ${assistantProcess.pid}` : "未运行"}</strong>
                <span>日志</span><strong>{assistantProcess?.log ?? "尚未生成"}</strong>
              </div>
              <div className="lamp-command-actions">
                <button className="primary-button" onClick={() => void startRealtimeVoiceAssistant()} disabled={voiceAssistantBusy || Boolean(assistantProcess?.running)}>
                  <Mic size={16} />开启语音助手
                </button>
                <button className="ghost-button" onClick={() => void stopRealtimeVoiceAssistant()} disabled={voiceAssistantBusy || !assistantProcess?.running}>
                  停止语音助手
                </button>
                <button className="ghost-button" onClick={() => void load()} disabled={voiceAssistantBusy}>
                  <RefreshCw size={16} />刷新状态
                </button>
              </div>
              <p className={assistantProcess?.running ? "success-panel" : "warning-panel"}>
                {assistantProcess?.message ?? "语音助手未运行；开启后会持续监听设备侧麦克风。"}
              </p>
            </div>
          </Card>

          <Card title="单次授权录音" subtitle="只录制 1-8 秒；不启动连续监听；录音后尝试识别并交给助手处理">
            <div className="voice-capture-form">
              <label>
                <span>录音秒数</span>
                <input className="input" type="number" min={1} max={8} value={seconds} onChange={(event) => setSeconds(Number(event.target.value))} />
              </label>
              <label className="inline-check">
                <input type="checkbox" checked={authorized} onChange={(event) => setAuthorized(event.target.checked)} />
                我授权使用服务器/树莓派麦克风录音一次
              </label>
              <button className="primary-button" onClick={() => void captureOnce()} disabled={busy}><Mic size={16} />开始单次录音</button>
            </div>
            <div className="definition-grid">
              <span>状态</span><StatusBadge status={capture?.status ?? "pending"} />
              <span>识别文本</span><strong>{capture?.transcript || capture?.message || "等待授权录音"}</strong>
              <span>助手结果</span><strong>{capture?.assistant?.assistant_message?.text ?? "尚未生成"}</strong>
            </div>
            <details className="advanced-panel">
              <summary>录音诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview voice-result-preview">{JSON.stringify(capture ?? { status: "pending", message: "等待授权录音。" }, null, 2)}</pre>
              </div>
            </details>
          </Card>
        </div>

        <Card title="连续对话会话" subtitle="显式开启；文本内容需包含唤醒词；可授权写入长期记忆；不启动被动麦克风流">
          <div className="voice-conversation-form">
            <label>
              <span>唤醒词</span>
              <input className="input" value={wakeWord} onChange={(event) => setWakeWord(event.target.value)} />
            </label>
            <label className="inline-check">
              <input type="checkbox" checked={conversationAuthorized} onChange={(event) => setConversationAuthorized(event.target.checked)} />
              我授权开启显式连续对话会话
            </label>
            <div className="row">
              <button className="primary-button" onClick={() => void startConversation()} disabled={conversationBusy}>开启会话</button>
              <button className="ghost-button" onClick={() => void stopConversation()} disabled={conversationBusy || !(conversation?.session?.session_id ?? conversation?.session_id)}>停止会话</button>
            </div>
            <label className="span-2">
              <span>对话内容</span>
              <textarea className="input" value={turnText} onChange={(event) => setTurnText(event.target.value)} />
            </label>
            <label className="inline-check">
              <input type="checkbox" checked={rememberTurn} onChange={(event) => setRememberTurn(event.target.checked)} />
              授权把本轮对话写入长期记忆
            </label>
            <button className="primary-button" onClick={() => void sendTurn()} disabled={conversationBusy}>发送对话</button>
          </div>
          <div className="definition-grid">
            <span>状态</span><StatusBadge status={conversation?.status ?? "pending"} />
            <span>会话</span><strong>{conversation?.session ? "已开启" : "未开启"}</strong>
            <span>轮次</span><strong>{conversation?.session?.turn_count ?? 0}</strong>
            <span>最近回复</span><strong>{conversationSummary(conversation)}</strong>
          </div>
          <details className="advanced-panel">
            <summary>会话诊断</summary>
            <div className="advanced-panel__content">
              <pre className="json-preview voice-result-preview">{JSON.stringify(conversation ?? { status: "pending", message: "等待开启连续对话会话。" }, null, 2)}</pre>
            </div>
          </details>
        </Card>

        <Card title="安全约束">
          <div className="security-summary">
            <SkillChip><Lightbulb size={14} />台灯文字命令复用本地语音 skill</SkillChip>
            {(status?.safety ?? ["explicit capture only", "no continuous microphone stream from web console"]).map((item) => <SkillChip key={item}>{friendlySafety(item)}</SkillChip>)}
          </div>
        </Card>
      </div>
    </>
  );
}

function lampCommandAction(command: LeLampVoiceCommandResponse | null) {
  if (!command) return "等待命令";
  if (!command.handled) return "未命中台灯命令";
  const action = command.command?.action ? String(command.command.action) : "";
  const label = command.command?.label ? String(command.command.label) : "";
  return [action, label].filter(Boolean).join(" / ") || "已识别";
}

function lampCommandPdf(command: LeLampVoiceCommandResponse | null) {
  if (!command) return "尚未生成";
  const name = command.pdf_workspace_name ?? (command.pdf && typeof command.pdf === "object" ? (command.pdf as Record<string, unknown>).pdf_workspace_name : "");
  return name ? String(name) : "尚未生成";
}

function VoiceStatusRow({ icon, label, value, detail }: { icon: ReactNode; label: string; value: string; detail: string }) {
  return (
    <div className="voice-status-row">
      {icon}
      <strong>{label}</strong>
      <StatusBadge status={value} />
      <span>{detail}</span>
    </div>
  );
}

function friendlyStatus(value: unknown) {
  const status = String(value ?? "pending");
  const labels: Record<string, string> = {
    ok: "正常",
    enabled: "已启用",
    available: "可用",
    completed: "已完成",
    running: "运行中",
    pending: "等待检测",
    adapter_ready: "待接入",
    backend_missing: "待接入",
    needs_config: "需配置",
    unavailable: "不可用",
    failed: "失败",
  };
  return labels[status] ?? status;
}

function conversationSummary(conversation: VoiceConversationResponse | null) {
  if (!conversation) return "等待开启连续对话会话";
  if (conversation.message) return conversation.message;
  const turn = conversation.turn;
  if (turn && typeof turn === "object") {
    const text = turn.assistant_text ?? turn.response ?? turn.text;
    if (text) return String(text);
  }
  return conversation.session ? "会话已更新" : "等待开启";
}

function friendlySafety(item: string) {
  const labels: Record<string, string> = {
    "explicit capture only": "仅显式授权录音",
    "no continuous microphone stream from web console": "控制台不做被动连续监听",
  };
  return labels[item] ?? item.replace(/[_-]+/g, " ");
}
