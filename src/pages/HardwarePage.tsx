import { Camera, Cpu, Mic, MonitorPlay, RotateCw, Speaker, Zap } from "lucide-react";
import { Fragment } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { getHardwareStatus, runHardwareTest, scanHardware, setLeLampState } from "../api/hardware";
import type { HardwareDevice, HardwareStatus, HardwareTestResponse } from "../api/types";
import { Card } from "../components/Card";
import { HardwareStatusCard } from "../components/HardwareStatusCard";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import "./pages.css";

const cues = ["idle", "listening", "thinking", "success", "blocked", "error"];

export function HardwarePage() {
  const [cue, setCue] = useState("idle");
  const [status, setStatus] = useState<HardwareStatus | null>(null);
  const [events, setEvents] = useState<Array<{ time: string; title: string; status: string; detail: string }>>([]);
  const [error, setError] = useState("");
  const [testResult, setTestResult] = useState<HardwareTestResponse | HardwareStatus | null>(null);
  const [testing, setTesting] = useState("");

  const load = useCallback(async () => {
    try {
      const result = await getHardwareStatus();
      setStatus(result.data);
      setEvents((result.data.events ?? []).map((event) => ({
        time: event.timestamp.slice(11, 19),
        title: friendlyHardwareAction(event.action),
        status: event.status,
        detail: friendlyAuditDetail(event.target, event.details),
      })));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  usePolling(load, 5000, true);

  async function triggerCue(next: string) {
    setCue(next);
    setError("");
    try {
      const response = await setLeLampState(next);
      setEvents((items) => [
        { time: new Date().toTimeString().slice(0, 8), title: `State Cue: ${next}`, status: String(response.data.status), detail: `hardware_enabled=${String(response.data.hardware_enabled)}` },
        ...items,
      ]);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function scanNow() {
    setError("");
    setTesting("scan");
    try {
      const response = await scanHardware();
      setStatus(response.data);
      setTestResult(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setTesting("");
    }
  }

  async function runTest(test: "camera" | "mic" | "speaker" | "projection" | "rgb") {
    setError("");
    setTesting(test);
    try {
      const response = await runHardwareTest(test, test === "rgb" ? { state: cue || "success" } : {});
      setTestResult(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setTesting("");
    }
  }

  async function runSpeakerDeviceTest(device: string) {
    setError("");
    setTesting(`speaker:${device}`);
    try {
      const response = await runHardwareTest("speaker", { device });
      setTestResult(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setTesting("");
    }
  }

  const devices = useMemo<HardwareDevice[]>(() => {
    const raw = status?.devices ?? {};
      const labels: Record<string, string> = {
      camera: "摄像头",
      mic: "麦克风",
      speaker: "扬声器",
      projection: "投影仪",
      rgb: "状态光效",
      usb: "USB 设备",
    };
    const mapped = Object.entries(raw).map(([key, value]) => ({
      key,
      label: labels[key] ?? key,
      status: value.status,
      note: friendlyDeviceNote(key, value.status, value.details),
    }));
    return [
      { key: "hardware_enabled", label: "硬件总开关", status: status?.hardware_enabled ? "enabled" : "adapter_ready", note: status?.hardware_enabled ? "硬件模块已启用" : "等待接入硬件桥" },
      ...mapped,
    ];
  }, [status]);

  return (
    <>
      <PageHeader
        title="硬件与设备状态"
        description="查看摄像头、麦克风、扬声器、投影和状态灯；所有拍照、录音、播放测试都需要手动触发"
        actions={
          <div className="row">
            <button className="ghost-button" onClick={() => void load()}><RotateCw size={16} /> 刷新</button>
            <button className="primary-button" onClick={() => void scanNow()} disabled={testing === "scan"}><Cpu size={16} /> 重新扫描硬件</button>
          </div>
        }
      />
      <div className="page-grid">
        {error && <div className="danger-panel">操作失败：{error}</div>}
        <Card title="硬件扫描与安全测试" subtitle="扫描只运行只读探测命令；录音、拍照、扬声器和 RGB 必须手动点击并写审计。">
          <div className="hardware-test-grid">
            <button onClick={() => void runTest("camera")} disabled={testing === "camera"}><Camera size={18} /><strong>测试摄像头</strong><span>拍摄一张受控测试照片</span></button>
            <button onClick={() => void runTest("mic")} disabled={testing === "mic"}><Mic size={18} /><strong>测试麦克风</strong><span>录制短音频并检测输入音量</span></button>
            <button onClick={() => void runTest("speaker")} disabled={testing === "speaker"}><Speaker size={18} /><strong>测试扬声器</strong><span>从树莓派/服务端音频输出播放 0.6 秒测试音</span></button>
            <button onClick={() => void runTest("projection")} disabled={testing === "projection"}><MonitorPlay size={18} /><strong>测试投影仪</strong><span>生成投影测试画面并写入投影输出</span></button>
            <button onClick={() => void runTest("rgb")} disabled={testing === "rgb"}><Zap size={18} /><strong>测试状态灯</strong><span>按当前状态触发灯效</span></button>
          </div>
          <p className="small muted">当前测试中：{testing || "无"} · 扫描时间：{status?.scanned_at ?? "未扫描"}</p>
          <p className="small muted">扬声器测试只会触发树莓派/服务端音频输出，不会使用访问控制台的办公电脑发声。</p>
        </Card>
        <Card title="扬声器输出选择" subtitle="如果默认测试听不到声音，请逐个测试候选输出。系统会把每次测试写入审计。">
          <div className="list-rows">
            <div className="row-between">
              <span>当前选中输出</span>
              <strong>{speakerSelectedLabel(status)}</strong>
              <StatusBadge status={status?.devices?.speaker?.status ?? "pending"} />
            </div>
            <div className="row-between">
              <span>系统默认输出</span>
              <strong>{status?.devices?.speaker?.details?.default_sink ? "已检测到" : "未检测到"}</strong>
            </div>
          </div>
          <div className="cue-buttons">
            {speakerCandidates(status).map((candidate) => (
              <button key={candidate.device} className={candidate.device === speakerSelectedDevice(status) ? "selected" : ""} onClick={() => void runSpeakerDeviceTest(candidate.device)} disabled={testing === `speaker:${candidate.device}`}>
                <strong>{candidate.label}</strong>
                <span>点击播放测试音</span>
              </button>
            ))}
            {!speakerCandidates(status).length && <span className="small muted">未扫描到 ALSA 播放候选设备。</span>}
          </div>
        </Card>
        <Card title="投影仪接入" subtitle="显示真实投影仪或外接显示输出状态；测试只生成投影画面，不直接修改投影仪固件。">
          <div className="definition-grid">
            <span>物理状态</span><StatusBadge status={projectionConnected(status) ? "available" : "adapter_ready"} label={projectionConnected(status) ? "已接入" : "替代模式"} />
            <span>输出口</span><strong>{projectionOutput(status) || "未检测到固定输出口"}</strong>
            <span>预览链接</span><strong>{projectionPreviewUrl(status) || "等待启动投影预览服务"}</strong>
            <span>系统状态</span><StatusBadge status={status?.devices?.projection?.status ?? "pending"} />
          </div>
          <div className="row">
            <button className="primary-button" onClick={() => void runTest("projection")} disabled={testing === "projection"}><MonitorPlay size={16} /> 生成投影测试画面</button>
          </div>
        </Card>
        <div className="grid-6 hardware-grid">
          {devices.map((device, index) => <HardwareStatusCard key={device.key} device={device} index={index} />)}
        </div>

        <Card title="状态光效触发控制" subtitle="手动触发状态光效，用于测试演示。实际运行中由助手自动触发。">
          <div className="cue-buttons">
            {cues.map((item) => (
              <button className={cue === item ? "selected" : ""} key={item} onClick={() => void triggerCue(item)}>
                <strong>{friendlyCue(item)}</strong>
                <span>{friendlyCueDescription(item)}</span>
              </button>
            ))}
          </div>
          <p className="small muted">
            提示：硬件动作受限且会记录审计；未接入状态灯或硬件桥时会显示“待接入”。
          </p>
        </Card>

        <div className="grid-3">
          <Card title="传感器 / 设备健康">
            <div className="list-rows">
              <SensorRow label="处理器温度" value={formatSensor(status?.sensors?.cpu_temp, "°C")} />
              <SensorRow label="处理器使用率" value={formatPercent(status?.sensors?.cpu_usage)} />
              <SensorRow label="内存使用率" value={formatPercent(status?.sensors?.memory_usage)} />
              <SensorRow label="工作区磁盘使用率" value={formatPercent(status?.sensors?.disk_usage)} />
              <SensorRow label="供电状态" value={friendlyPowerState(status?.sensors?.power_state)} status={String(status?.sensors?.power_state ?? "backend_missing")} />
              <SensorRow label="性能限制" value={friendlyThrottled(status?.sensors?.throttled)} status={status?.sensors?.throttled === "throttled=0x0" ? "ok" : "backend_missing"} />
            </div>
          </Card>
          <Card title="最近硬件事件">
            <div className="list-rows">
              {events.slice(0, 6).map((event, index) => (
                <div className="event-row" key={`${event.time}-${index}`}>
                  <StatusBadge status={event.status} />
                  <div><strong>{event.title}</strong><p>{event.time} · {event.detail}</p></div>
                </div>
              ))}
              {!events.length && <span className="small muted">暂无硬件/投影/状态灯审计事件。</span>}
            </div>
          </Card>
          <Card title="设备接入状态">
            <div className="list-rows">
              {devices.map((device) => (
                <div className="row-between" key={device.key}>
                  <strong>{device.label}</strong>
                  <StatusBadge status={device.status} />
                </div>
              ))}
            </div>
          </Card>
        </div>

        <div className="grid-2">
          <Card title="扫描摘要">
            <div className="definition-grid">
              {Object.entries(status?.scan?.summary ?? {}).map(([key, value]) => (
                <Fragment key={key}><span>{key}</span><strong>{value}</strong></Fragment>
              ))}
              {!Object.keys(status?.scan?.summary ?? {}).length && <><span>状态</span><strong>等待扫描</strong></>}
            </div>
            <div className="blue-note">{status?.scan?.notes?.join(" / ") ?? "扫描不会读取用户目录、密钥、聊天记录或云盘。"}</div>
          </Card>
          <Card title="最近一次测试结果" action={<StatusBadge status={testStatus(testResult)} />}>
            <div className="definition-grid">
              <span>测试状态</span><StatusBadge status={testStatus(testResult)} />
              <span>测试项目</span><strong>{testName(testResult)}</strong>
              <span>结果摘要</span><strong>{testSummary(testResult)}</strong>
            </div>
            <details className="advanced-panel">
              <summary>测试诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview">{JSON.stringify(testResult ?? {}, null, 2)}</pre>
              </div>
            </details>
          </Card>
        </div>

        <details className="advanced-panel">
          <summary>硬件高级诊断</summary>
          <div className="advanced-panel__content">
            <Card title="硬件桥状态">
              <div className="row">
                <Cpu size={18} />
                <span>hardware_enabled: {String(status?.hardware_enabled ?? false)} · camera: {status?.devices?.camera?.status ?? "adapter_ready"} · rgb: {status?.devices?.rgb?.status ?? "adapter_ready"}</span>
                <button className="ghost-button" onClick={() => void scanNow()}><RotateCw size={16} /> 重新扫描</button>
              </div>
            </Card>
            <Card title="设备详情">
              <pre className="json-preview">{JSON.stringify(status?.devices ?? {}, null, 2)}</pre>
            </Card>
          </div>
        </details>
      </div>
    </>
  );
}

function speakerSelectedDevice(status: HardwareStatus | null) {
  return String(status?.devices?.speaker?.details?.selected_device ?? status?.devices?.speaker?.details?.configured_device ?? "-");
}

function speakerCandidates(status: HardwareStatus | null) {
  const candidates = status?.devices?.speaker?.details?.candidates;
  if (!Array.isArray(candidates)) return [];
  return candidates
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const record = item as Record<string, unknown>;
      const device = String(record.plughw ?? record.hw ?? "");
      if (!device) return null;
      const label = `${String(record.card_name ?? record.card_id ?? "音频输出")} / ${String(record.device_name ?? "设备")}`;
      return { device, label };
    })
    .filter((item): item is { device: string; label: string } => Boolean(item));
}

function speakerSelectedLabel(status: HardwareStatus | null) {
  const selected = speakerSelectedDevice(status);
  return speakerCandidates(status).find((candidate) => candidate.device === selected)?.label ?? (selected === "-" ? "未选择" : "已选择输出设备");
}

function friendlyDeviceNote(key: string, status: unknown, details?: Record<string, unknown>) {
  if (String(status) === "adapter_ready" || String(status) === "backend_missing") return "等待接入或配置";
  if (key === "projection") {
    const connected = Boolean(details?.projector_connected);
    const output = String(details?.projector_output ?? "");
    if (connected) return output ? `真实投影仪已接入：${output}` : "真实投影仪已接入";
    return "未检测到真实投影仪，当前使用外接显示/预览替代模式";
  }
  const labels: Record<string, string> = {
    camera: "可用于拍照扫描和场景观察",
    mic: "可用于会议转写和语音交互",
    speaker: "可用于语音播报和测试音",
    rgb: "可用于显示助手状态",
    usb: "可用于识别外设接入情况",
  };
  return labels[key] ?? "设备状态已记录";
}

function projectionDetails(status: HardwareStatus | null) {
  const details = status?.devices?.projection?.details;
  return details && typeof details === "object" ? details as Record<string, unknown> : {};
}

function projectionConnected(status: HardwareStatus | null) {
  return Boolean(projectionDetails(status).projector_connected);
}

function projectionOutput(status: HardwareStatus | null) {
  return String(projectionDetails(status).projector_output ?? "");
}

function projectionPreviewUrl(status: HardwareStatus | null) {
  return String(projectionDetails(status).preview_url ?? "");
}

function friendlyHardwareAction(action: string) {
  const labels: Record<string, string> = {
    hardware_test: "硬件测试",
    hardware_scan: "硬件扫描",
    lelamp_state: "状态光效",
    projection: "投影测试",
    assistant: "助手操作",
  };
  return labels[action] ?? action.replace(/[_-]+/g, " ");
}

function friendlyAuditDetail(target?: string, details?: Record<string, unknown>) {
  if (target) return compactDisplayPath(target);
  const message = details?.message ?? details?.reason ?? details?.test;
  return message ? String(message) : "详情已记录";
}

function compactDisplayPath(value?: string) {
  const text = String(value ?? "");
  if (!text) return "-";
  const normalized = text.replace(/\\/g, "/");
  const marker = "/workspace/";
  const workspaceIndex = normalized.lastIndexOf(marker);
  if (workspaceIndex >= 0) return normalized.slice(workspaceIndex + 1);
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 2) return normalized;
  return `.../${parts.slice(-2).join("/")}`;
}

function SensorRow({ label, value, status = "ok" }: { label: string; value: string; status?: string }) {
  return <div className="row-between"><span>{label}</span><strong>{value}</strong><StatusBadge status={value === "待检测" ? "backend_missing" : status} label={status === "ok" && value !== "待检测" ? "正常" : undefined} /></div>;
}

function formatSensor(value: number | null | undefined, suffix: string) {
  return typeof value === "number" ? `${value} ${suffix}` : "待检测";
}

function percentNumber(value: number | null | undefined) {
  if (typeof value !== "number") return undefined;
  return value <= 1 ? Math.round(value * 100) : Math.round(value);
}

function formatPercent(value: number | null | undefined) {
  const percent = percentNumber(value);
  return typeof percent === "number" ? `${percent}%` : "待检测";
}

function testStatus(result: HardwareTestResponse | HardwareStatus | null) {
  if (!result) return "pending";
  if ("status" in result) return String(result.status);
  return String(result.scan?.status ?? "completed");
}

function testName(result: HardwareTestResponse | HardwareStatus | null) {
  if (!result) return "等待测试";
  if ("test" in result) {
    const labels: Record<string, string> = {
      camera: "摄像头",
      mic: "麦克风",
      speaker: "扬声器",
      projection: "投影",
      rgb: "状态灯",
    };
    return labels[result.test] ?? result.test;
  }
  return "硬件扫描";
}

function testSummary(result: HardwareTestResponse | HardwareStatus | null) {
  if (!result) return "尚未执行";
  if ("result" in result) {
    const message = result.result.message ?? result.result.summary ?? result.result.note;
    return message ? String(message) : "测试结果已记录";
  }
  return result.scan?.notes?.join(" / ") || "扫描结果已记录";
}

function friendlyCue(value: string) {
  const labels: Record<string, string> = {
    idle: "空闲",
    listening: "倾听中",
    thinking: "思考中",
    success: "完成",
    blocked: "已阻止",
    error: "错误",
  };
  return labels[value] ?? value;
}

function friendlyCueDescription(value: string) {
  const labels: Record<string, string> = {
    idle: "设备待命",
    listening: "等待语音或指令",
    thinking: "正在处理任务",
    success: "任务已完成",
    blocked: "需要确认或被拦截",
    error: "操作失败",
  };
  return labels[value] ?? "状态光效";
}

function friendlyPowerState(value: unknown) {
  const text = String(value ?? "");
  if (!text || text === "backend_missing" || text === "adapter_ready") return "待检测";
  if (["ok", "normal", "enabled"].includes(text)) return "正常";
  return text;
}

function friendlyThrottled(value: string | null | undefined) {
  if (!value) return "待检测";
  if (value === "throttled=0x0") return "正常";
  return "可能受限";
}
