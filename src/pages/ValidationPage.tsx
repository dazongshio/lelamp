import { Camera, CheckCircle2, ClipboardCheck, FileScan, Lightbulb, Mic, MonitorCheck, MousePointerClick, RefreshCw, ShieldCheck, Video } from "lucide-react";
import type { ReactElement } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiErrorMessage, readToken } from "../api/client";
import { createDemoScanImage } from "../api/documents";
import { getTargetValidationStatus, importDesktopValidationResult, runTargetValidation } from "../api/product";
import { getSharedFiles, uploadSharedFile } from "../api/shared";
import type { DesktopValidationImportResponse, TargetValidationItem, TargetValidationRunResponse, TargetValidationStatusResponse } from "../api/types";
import { Card, InfoCard } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { SkillChip } from "../components/SkillChip";
import { StatusBadge } from "../components/StatusBadge";
import "./pages.css";

const iconMap: Record<string, ReactElement> = {
  projection_display_substitute: <MonitorCheck size={20} />,
  physical_projection_hardware: <Video size={20} />,
  meeting_asr_diarization: <Mic size={20} />,
  document_scanning: <FileScan size={20} />,
  voice_scene_awareness: <Lightbulb size={20} />,
  desktop_full_control: <MousePointerClick size={20} />,
};

export function ValidationPage() {
  const [data, setData] = useState<TargetValidationStatusResponse | null>(null);
  const [selectedId, setSelectedId] = useState("projection_display_substitute");
  const [result, setResult] = useState<TargetValidationRunResponse | null>(null);
  const [desktopImportResult, setDesktopImportResult] = useState<DesktopValidationImportResponse | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [meetingAuthorized, setMeetingAuthorized] = useState(true);
  const [meetingLiveCapture, setMeetingLiveCapture] = useState(false);
  const [meetingUseTingwu, setMeetingUseTingwu] = useState(false);
  const [meetingUseDemoAudio, setMeetingUseDemoAudio] = useState(false);
  const [audioWorkspaceName, setAudioWorkspaceName] = useState("");
  const [projectionHardwareAuthorized, setProjectionHardwareAuthorized] = useState(false);
  const [projectionHardwarePassed, setProjectionHardwarePassed] = useState(false);
  const [scanAuthorized, setScanAuthorized] = useState(false);
  const [scanWorkspaceName, setScanWorkspaceName] = useState("");
  const [scanGenerateDemoImage, setScanGenerateDemoImage] = useState(false);
  const [scanFiles, setScanFiles] = useState<Array<{ relative_path?: string; workspace_name?: string; name: string }>>([]);
  const [voiceSceneAuthorized, setVoiceSceneAuthorized] = useState(false);
  const [desktopAuthorized, setDesktopAuthorized] = useState(false);
  const [recordingState, setRecordingState] = useState<"idle" | "recording" | "uploading">("idle");
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordingChunksRef = useRef<BlobPart[]>([]);
  const recordingTimerRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    setError("");
    try {
      const [response, shared] = await Promise.all([getTargetValidationStatus(), getSharedFiles({ type: "audio", page_size: 20 })]);
      const scanShared = await getSharedFiles({ type: "image", page_size: 40 });
      setData(response.data);
      setAudioWorkspaceName((current) => current || shared.data.files[0]?.relative_path || shared.data.files[0]?.workspace_name || "");
      setScanFiles(scanShared.data.files);
      setScanWorkspaceName((current) => current || scanShared.data.files[0]?.relative_path || scanShared.data.files[0]?.workspace_name || "");
      setSelectedId((current) => response.data.items.some((item) => item.id === current) ? current : response.data.items[0]?.id ?? current);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const selected = useMemo(() => data?.items.find((item) => item.id === selectedId) ?? data?.items[0] ?? null, [data, selectedId]);
  const targetBundle = useMemo(() => {
    const artifacts = result?.report?.artifacts;
    const bundle = artifacts?.target_bundle;
    return bundle && typeof bundle === "object" ? bundle as Record<string, unknown> : null;
  }, [result]);

  async function run(item: TargetValidationItem) {
    setBusy(item.id);
    setError("");
    setResult(null);
    const options: Record<string, unknown> = {};
    if (item.id === "meeting_asr_diarization") {
      options.authorized = meetingAuthorized;
      options.live_capture = meetingLiveCapture;
      options.use_tingwu_realtime = meetingUseTingwu;
      options.use_demo_audio = meetingUseDemoAudio;
      if (audioWorkspaceName.trim()) options.audio_workspace_name = audioWorkspaceName.trim();
      options.participants = ["Alice", "Bob"];
    }
    if (item.id === "physical_projection_hardware") {
      options.authorized = projectionHardwareAuthorized;
      options.display_readable = projectionHardwarePassed;
      options.focus_ok = projectionHardwarePassed;
      options.keystone_ok = projectionHardwarePassed;
      options.brightness_ok = projectionHardwarePassed;
      options.ambient_lux = 320;
    }
    if (item.id === "document_scanning") {
      options.authorized = scanAuthorized;
      if (scanWorkspaceName.trim()) options.image_workspace_name = scanWorkspaceName.trim();
      options.generate_demo_image = scanGenerateDemoImage;
      options.document_type = "document";
      options.language = "chi_sim+eng";
    }
    if (item.id === "voice_scene_awareness") {
      options.authorized = voiceSceneAuthorized;
      options.wake_word = "小灯";
    }
    if (item.id === "desktop_full_control") {
      options.authorized = desktopAuthorized;
      options.steps = ["打开网页 about:blank"];
      options.console_token = readToken();
    }
    if (item.id === "projection_display_substitute") {
      options.ambient_lux = 320;
    }
    try {
      const response = await runTargetValidation(item.id, options);
      setResult(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function importDesktopResult(file: File | null) {
    if (!file) return;
    setBusy("desktop_import");
    setError("");
    setDesktopImportResult(null);
    try {
      const text = await file.text();
      const payload = JSON.parse(text) as Record<string, unknown>;
      const response = await importDesktopValidationResult(payload);
      setDesktopImportResult(response.data);
      await load();
    } catch (err) {
      setError(err instanceof SyntaxError ? "目标机验收结果不是有效 JSON。" : apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function uploadAudio(file: File | null) {
    if (!file) return;
    setBusy("audio_upload");
    setError("");
    try {
      const response = await uploadSharedFile(file);
      const uploaded = response.data.files[0];
      setAudioWorkspaceName(uploaded.relative_path || uploaded.workspace_name || uploaded.name);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function uploadScanImage(file: File | null) {
    if (!file) return;
    setBusy("scan_upload");
    setError("");
    try {
      const response = await uploadSharedFile(file);
      const uploaded = response.data.files[0];
      setScanWorkspaceName(uploaded.relative_path || uploaded.workspace_name || uploaded.name);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function generateScanDemoImage() {
    setBusy("scan_demo");
    setError("");
    try {
      const response = await createDemoScanImage({ title: "validation_scan_demo", document_type: "document" });
      const workspaceName = String(response.data.workspace_name ?? "");
      if (workspaceName) {
        setScanWorkspaceName(workspaceName);
        setScanGenerateDemoImage(false);
      }
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function startBrowserRecording() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("当前浏览器不支持 MediaRecorder 录音。");
      return;
    }
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordingChunksRef.current = [];
      const recorder = new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) recordingChunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        void uploadRecordedAudio();
      };
      recorder.start();
      setRecordingSeconds(0);
      setRecordingState("recording");
      recordingTimerRef.current = window.setInterval(() => {
        setRecordingSeconds((value) => value + 1);
      }, 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "浏览器录音授权失败。");
      setRecordingState("idle");
    }
  }

  function stopBrowserRecording() {
    if (recordingTimerRef.current !== null) {
      window.clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
    } else {
      setRecordingState("idle");
    }
  }

  async function uploadRecordedAudio() {
    const chunks = recordingChunksRef.current;
    if (!chunks.length) {
      setRecordingState("idle");
      return;
    }
    setRecordingState("uploading");
    try {
      const mimeType = mediaRecorderRef.current?.mimeType || "audio/webm";
      const extension = mimeType.includes("ogg") ? "ogg" : mimeType.includes("mp4") ? "m4a" : "webm";
      const blob = new Blob(chunks, { type: mimeType });
      const file = new File([blob], `validation_browser_meeting_${Date.now()}.${extension}`, { type: mimeType });
      await uploadAudio(file);
    } finally {
      mediaRecorderRef.current = null;
      recordingChunksRef.current = [];
      setRecordingState("idle");
    }
  }

  return (
    <>
      <PageHeader
        title="验收中心"
        description="逐项测试目标功能是否可用；可选硬件增强不会阻塞软件侧完成"
        actions={<button className="ghost-button" onClick={() => void load()} disabled={Boolean(busy)}><RefreshCw size={16} />刷新</button>}
      />
      <div className="page-grid">
        {error && <div className="danger-panel">验收失败：{error}</div>}
        <div className="grid-4">
          <InfoCard icon={<ClipboardCheck size={20} />} label="验收项" value={String(data?.summary.total ?? "-")} note="来自未完成/目标环境项" />
          <InfoCard icon={<CheckCircle2 size={20} />} label="已完成" value={String(data?.summary.completed ?? 0)} status={<StatusBadge status="completed" />} />
          <InfoCard icon={<ShieldCheck size={20} />} label="待接入" value={String(data?.summary.adapter_ready ?? 0)} status={<StatusBadge status="adapter_ready" />} />
          <InfoCard icon={<ShieldCheck size={20} />} label="可选/不适用" value={String((data?.summary.counts.optional ?? 0) + (data?.summary.counts.not_applicable ?? 0) + (data?.summary.counts.needs_hardware ?? 0))} status={<StatusBadge status="optional" />} />
        </div>

        <div className="validation-layout">
          <Card title="验收项目">
            <div className="validation-list">
              {(data?.items ?? []).map((item) => (
                <button className={item.id === selectedId ? "selected" : ""} key={item.id} onClick={() => setSelectedId(item.id)}>
                  {iconMap[item.id] ?? <ClipboardCheck size={20} />}
                  <span>
                    <strong>{item.feature}</strong>
                    <small>{item.area}</small>
                  </span>
                  <StatusBadge status={item.status} />
                </button>
              ))}
            </div>
          </Card>

          <Card title={selected?.feature ?? "验收详情"} action={<StatusBadge status={selected?.status ?? "pending"} />}>
            {selected ? (
              <div className="validation-detail">
                <p className="muted">{selected.gap}</p>
                <div className="validation-options">
                  {selected.id === "meeting_asr_diarization" && (
                    <>
                      <label className="inline-check">
                        <input type="checkbox" checked={meetingAuthorized} onChange={(event) => setMeetingAuthorized(event.target.checked)} />
                        授权写入测试会议发言
                      </label>
                      <label className="inline-check">
                        <input type="checkbox" checked={meetingLiveCapture} onChange={(event) => setMeetingLiveCapture(event.target.checked)} />
                        同时采集目标麦克风并调用语音识别
                      </label>
                      <label className="inline-check">
                        <input type="checkbox" checked={meetingUseTingwu} onChange={(event) => setMeetingUseTingwu(event.target.checked)} />
                        使用通义听悟实时分角色验收
                      </label>
                      <label className="inline-check">
                        <input type="checkbox" checked={meetingUseDemoAudio} onChange={(event) => setMeetingUseDemoAudio(event.target.checked)} />
                        生成演示会议音频跑语音识别和分角色
                      </label>
                      <label>
                        <span>已上传音频</span>
                        <input className="input" value={audioWorkspaceName} onChange={(event) => setAudioWorkspaceName(event.target.value)} placeholder="选择或上传会议音频" />
                      </label>
                      <label className="validation-file-input">
                        <span>上传音频文件</span>
                        <input type="file" accept="audio/*,.wav,.mp3,.m4a,.webm,.ogg,.flac,.aac,.mp4" onChange={(event) => void uploadAudio(event.target.files?.[0] ?? null)} />
                      </label>
                      <div className="validation-recorder">
                        <div>
                          <strong>浏览器录音验收</strong>
                          <span>{recordingState === "recording" ? `录音中 ${recordingSeconds}s` : recordingState === "uploading" ? "上传录音中" : "录音会先保存到共享空间，再调用语音识别验收"}</span>
                        </div>
                        {recordingState === "recording"
                          ? <button className="danger-button" onClick={stopBrowserRecording}>停止并上传</button>
                          : <button className="ghost-button" onClick={() => void startBrowserRecording()} disabled={recordingState === "uploading"}>开始录音</button>}
                      </div>
                    </>
                  )}
                  {selected.id === "physical_projection_hardware" && (
                    <>
                      <label className="inline-check">
                        <input type="checkbox" checked={projectionHardwareAuthorized} onChange={(event) => setProjectionHardwareAuthorized(event.target.checked)} />
                        授权记录真实投影硬件验收
                      </label>
                      <label className="inline-check">
                        <input type="checkbox" checked={projectionHardwarePassed} onChange={(event) => setProjectionHardwarePassed(event.target.checked)} />
                        现场确认画面可读、对焦/梯形/亮度通过
                      </label>
                      <div className="blue-note">未接真实投影仪时保持未勾选；系统只会生成校准图和 profile，不会伪造硬件通过。</div>
                    </>
                  )}
                  {selected.id === "document_scanning" && (
                    <>
                      <label className="inline-check">
                        <input type="checkbox" checked={scanAuthorized} onChange={(event) => setScanAuthorized(event.target.checked)} />
                        授权处理上传的实体文档样张
                      </label>
                      <label>
                        <span>已上传样张</span>
                        <input className="input" value={scanWorkspaceName} onChange={(event) => setScanWorkspaceName(event.target.value)} placeholder="选择或上传文档图片" />
                      </label>
                      <label>
                        <span>从共享空间图片选择</span>
                        <select className="select" value={scanWorkspaceName} onChange={(event) => setScanWorkspaceName(event.target.value)}>
                          <option value="">请选择图片样张</option>
                          {scanFiles.map((file) => {
                            const value = file.relative_path || file.workspace_name || file.name;
                            return <option value={value} key={value}>{value}</option>;
                          })}
                        </select>
                      </label>
                      <label className="validation-file-input">
                        <span>上传实体文档图片</span>
                        <input type="file" accept="image/png,image/jpeg,image/webp,image/bmp,image/tiff" onChange={(event) => void uploadScanImage(event.target.files?.[0] ?? null)} />
                      </label>
                      <label className="inline-check">
                        <input type="checkbox" checked={scanGenerateDemoImage} onChange={(event) => setScanGenerateDemoImage(event.target.checked)} />
                        运行验收时自动生成 demo 扫描样张
                      </label>
                      <div className="row">
                        <button className="ghost-button" onClick={() => void generateScanDemoImage()} disabled={busy === "scan_demo"}>
                          <Camera size={16} /> 立即生成样张
                        </button>
                      </div>
                      <div className="blue-note"><Camera size={15} /> 验收必须有真实图片或演示样张；图像采集/增强和文字/结构识别会分开判定。</div>
                    </>
                  )}
                  {selected.id === "voice_scene_awareness" && (
                    <>
                      <label className="inline-check">
                        <input type="checkbox" checked={voiceSceneAuthorized} onChange={(event) => setVoiceSceneAuthorized(event.target.checked)} />
                        授权写入语音/场景感知验收事件和记忆
                      </label>
                      <div className="blue-note">该验收会模拟唤醒词门控、记忆写入、会议/投影遮挡/环境亮度事件，并生成工作流建议和投影提示卡。</div>
                    </>
                  )}
                  {selected.id === "desktop_full_control" && (
                    <>
                      <label className="inline-check">
                        <input type="checkbox" checked={desktopAuthorized} onChange={(event) => setDesktopAuthorized(event.target.checked)} />
                        授权在目标机全权控制环境执行探针
                      </label>
                      <div className="blue-note">完成条件：目标机必须处于全权控制环境、存在图形会话，并通过打开应用、鼠标键盘输入、低层控制和工作流执行探针。当前控制台可继续保持沙箱和仅审计的安全默认。</div>
                      <label className="validation-file-input">
                        <span>导入目标机验收结果</span>
                        <input type="file" accept="application/json,.json" onChange={(event) => void importDesktopResult(event.target.files?.[0] ?? null)} />
                      </label>
                      {desktopImportResult && (
                        <div className="blue-note">
                          导入结果：{friendlyStatus(desktopImportResult.status)} / {desktopImportResult.workspace_name ? "报告已保存" : "未保存"}
                          {desktopImportResult.missing_evidence?.length ? ` / 缺少：${desktopImportResult.missing_evidence.join(", ")}` : ""}
                          {desktopImportResult.remediation?.length ? ` / 建议：${desktopImportResult.remediation.join("；")}` : ""}
                        </div>
                      )}
                    </>
                  )}
                </div>
                <div className="row">
                  <button className="primary-button" onClick={() => void run(selected)} disabled={busy === selected.id}>
                    {busy === selected.id ? "验收中" : selected.run_label}
                  </button>
                  <SkillChip>{friendlyStatus(selected.status)}</SkillChip>
                </div>
                <div className="validation-steps">
                  {selected.steps.map((step) => (
                    <div className="validation-step" key={step.id}>
                      <div className="row-between">
                        <strong>{step.label}</strong>
                        <StatusBadge status={step.status} />
                      </div>
                      <span>{step.evidence.join(" / ") || "等待证据"}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : <span className="muted">暂无验收项目。</span>}
          </Card>
        </div>

        <div className="grid-2">
          <Card title="最近一次验收报告" action={<StatusBadge status={result?.status ?? "pending"} />}>
            <div className="definition-grid">
              <span>状态</span><StatusBadge status={result?.status ?? "pending"} />
              <span>报告</span><strong>{result?.markdown_workspace_name ? "已生成" : "等待验收"}</strong>
              <span>记录</span><strong>{result?.json_workspace_name ? "已保存" : "-"}</strong>
            </div>
            {result?.report && (
              <div className="validation-step">
                <div className="row-between">
                  <strong>{result.report.feature}</strong>
                  <StatusBadge status={result.report.status} />
                </div>
                <span>{result.report.gap || "验收结果已生成。"}</span>
              </div>
            )}
            <details className="advanced-panel">
              <summary>验收诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview validation-result-preview">{JSON.stringify(result?.report ?? { status: "pending" }, null, 2)}</pre>
              </div>
            </details>
          </Card>
          <Card title="安全约束">
            <div className="security-summary">
              {(data?.safety ?? []).map((item) => <SkillChip key={item}>{friendlySafety(item)}</SkillChip>)}
            </div>
          </Card>
        </div>
        {targetBundle && (
          <Card title="目标机全权模式验收包" action={<StatusBadge status="completed" />}>
            <div className="definition-grid">
              <span>环境文件</span><strong>{targetBundle.env_workspace_name ? "已生成" : "-"}</strong>
              <span>执行脚本</span><strong>{targetBundle.script_workspace_name ? "已生成" : "-"}</strong>
              <span>依赖说明</span><strong>{targetBundle.deps_workspace_name ? "已生成" : "-"}</strong>
              <span>图形启动器</span><strong>{targetBundle.gui_launcher_workspace_name ? "已生成" : "-"}</strong>
              <span>桌面入口</span><strong>{targetBundle.desktop_entry_workspace_name ? "已生成" : "-"}</strong>
              <span>验收清单</span><strong>{targetBundle.checklist_workspace_name ? "已生成" : "-"}</strong>
              <span>回传结果</span><strong>等待目标机导入</strong>
            </div>
            <div className="blue-note">目标办公电脑回传的结果必须包含桌面预检、输入探针、低层控制探针和执行探针四类证据；导入有效报告后，全局桌面控制会显示为完成。</div>
            <details className="advanced-panel">
              <summary>验收包诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview">{JSON.stringify(targetBundle, null, 2)}</pre>
              </div>
            </details>
          </Card>
        )}
      </div>
    </>
  );
}

function friendlyStatus(value: unknown) {
  const status = String(value ?? "");
  const labels: Record<string, string> = {
    ok: "正常",
    completed: "已完成",
    implemented: "已实现",
    adapter_ready: "待接入",
    backend_missing: "待接入",
    needs_hardware: "需硬件",
    optional: "可选增强",
    not_applicable: "不适用",
    blocked: "已阻止",
    failed: "失败",
    running: "运行中",
    pending: "等待",
  };
  return labels[status] ?? (status || "等待");
}

function friendlySafety(item: string) {
  const labels: Record<string, string> = {
    "sandbox default": "默认沙箱",
    "explicit authorization required": "需要明确授权",
    "meeting mode explicit": "会议理解需手动开启",
    "workspace artifacts only": "结果只写入工作区",
  };
  return labels[item] ?? item.replace(/[_-]+/g, " ");
}
