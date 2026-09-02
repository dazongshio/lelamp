import { Bell, Camera, CameraOff, Eye, FileSearch, Image as ImageIcon, Lightbulb, MessageCircle, Mic, MonitorX, RefreshCw, RotateCw, Sparkles, Upload, Users, Video } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiErrorMessage } from "../api/client";
import {
  captureDeviceSceneObservation,
  captureSceneAmbientInput,
  captureSceneSensorSnapshot,
  getLeLampMotionStatus,
  getSceneRecent,
  getSceneWorkflowSuggestions,
  observeSceneImage,
  reportSceneEvent,
  runSceneOrientedScan,
  runSceneTracking,
  submitEnvironmentReading,
  triggerSceneWorkflow,
} from "../api/scene";
import type {
  LeLampMotionStatusResponse,
  SceneAmbientCaptureResponse,
  SceneEnvironmentResponse,
  SceneEvent,
  SceneObserveImageResponse,
  SceneOrientedScanResponse,
  SceneSensorSnapshotResponse,
  SceneTrackingRunResponse,
  SceneWorkflowSuggestion,
  SceneWorkflowTriggerResponse,
} from "../api/types";
import { Card, InfoCard } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { SkillChip } from "../components/SkillChip";
import { StatusBadge } from "../components/StatusBadge";
import { useCameraStream } from "../hooks/useCameraStream";
import {
  ambientAudioLevel,
  ambientCameraMetrics,
  ambientTranscriptLabel,
  ambientTranscriptStatus,
  cameraImageClass,
  cameraNote,
  cameraNoteFromAmbient,
  cameraStreamIssueMessage,
  fileToDataUrl,
  friendlyAudioChannel,
  friendlySafeDefault,
  friendlySceneEvent,
  friendlyStatus,
  friendlySuggestionCategory,
  friendlyViewLabel,
  isSceneEvent,
  micNote,
  motionStatusNote,
  poseSummary,
  projectionNote,
  readCameraIndex,
  readObjectStatus,
  readPreflight,
  sensorStatus,
  snapshotImageName,
  speechActivityLabel,
  systemSensorNote,
  triggerSummary,
} from "./scenePageUtils";
import "./pages.css";

export function ScenePage() {
  const [events, setEvents] = useState<SceneEvent[]>([]);
  const [suggestions, setSuggestions] = useState<SceneWorkflowSuggestion[]>([]);
  const [triggerResult, setTriggerResult] = useState<SceneWorkflowTriggerResponse | null>(null);
  const [imageResult, setImageResult] = useState<SceneObserveImageResponse | null>(null);
  const [sensorSnapshot, setSensorSnapshot] = useState<SceneSensorSnapshotResponse | null>(null);
  const [ambientInput, setAmbientInput] = useState<SceneAmbientCaptureResponse | null>(null);
  const [motionStatus, setMotionStatus] = useState<LeLampMotionStatusResponse | null>(null);
  const [orientedScan, setOrientedScan] = useState<SceneOrientedScanResponse | null>(null);
  const [trackingRun, setTrackingRun] = useState<SceneTrackingRunResponse | null>(null);
  const [environmentResult, setEnvironmentResult] = useState<SceneEnvironmentResponse | null>(null);
  const [lux, setLux] = useState(120);
  const [peopleCount, setPeopleCount] = useState(0);
  const [presence, setPresence] = useState(false);
  const [speechActive, setSpeechActive] = useState(false);
  const [projectorBlocked, setProjectorBlocked] = useState(false);
  const [calendarEventNow, setCalendarEventNow] = useState(false);
  const [cam0Rotate180, setCam0Rotate180] = useState(true);
  const [cameraPanelMode, setCameraPanelMode] = useState<"video" | "photo">("video");
  const [livePreviewCameraIndex, setLivePreviewCameraIndex] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const cameraStream = useCameraStream(false, { cameraIndex: livePreviewCameraIndex, width: 1280, height: 720, backend: "auto" });
  const cameraStreamIssue = cameraStream.error || cameraStreamIssueMessage(cameraStream.status);
  const selectedCameraIndex = cameraStream.cameraIndex ?? readCameraIndex(sensorSnapshot) ?? 1;

  const load = useCallback(async () => {
    setError("");
    try {
      const [recentResponse, suggestionResponse] = await Promise.all([
        getSceneRecent(30),
        getSceneWorkflowSuggestions(30),
      ]);
      setEvents(recentResponse.data.events);
      setSuggestions(suggestionResponse.data.suggestions);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void refreshMotionStatus();
  }, []);

  async function refreshMotionStatus() {
    try {
      const response = await getLeLampMotionStatus();
      setMotionStatus(response.data);
    } catch {
      setMotionStatus(null);
    }
  }

  async function analyzeFile(file: File) {
    setError("");
    setBusy("image");
    try {
      const imageDataUrl = await fileToDataUrl(file);
      const response = await observeSceneImage({
        image_data_url: imageDataUrl,
        title: "desk_scene_observation",
      });
      setImageResult(response.data);
      setSuggestions(response.data.suggestions ?? []);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function captureDeviceObservation() {
    setError("");
    setBusy("image");
    try {
      const response = await captureDeviceSceneObservation({
        title: "desk_scene_observation",
        camera_index: selectedCameraIndex,
        cam0_rotate_180: cam0Rotate180,
      });
      setImageResult(response.data);
      setSuggestions(response.data.suggestions ?? []);
      await load();
    } catch (err) {
      setError(`${apiErrorMessage(err)}。如设备相机不可用，请使用“上传图片”入口。`);
    } finally {
      setBusy("");
    }
  }

  async function captureSensorSnapshot() {
    setError("");
    setBusy("sensors");
    try {
      const response = await captureSceneSensorSnapshot({
        title: "desk_scene_sensor_snapshot",
        include_camera: true,
        include_mic: true,
        include_hardware: true,
        mic_seconds: 1,
        camera_index: selectedCameraIndex,
        cam0_rotate_180: cam0Rotate180,
        lux,
        people_count: peopleCount,
        presence,
        projector_blocked: projectorBlocked,
        calendar_event_now: calendarEventNow,
      });
      setSensorSnapshot(response.data);
      setSuggestions(response.data.suggestions ?? []);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  function mergeAmbientInput(previous: SceneAmbientCaptureResponse | null, next: SceneAmbientCaptureResponse): SceneAmbientCaptureResponse {
    return {
      ...next,
      cameras: next.include_cameras === false ? previous?.cameras ?? [] : next.cameras,
      camera_count: next.include_cameras === false ? previous?.camera_count ?? 0 : next.camera_count,
      microphone: next.include_mic === false ? previous?.microphone ?? { status: "skipped" } : next.microphone,
      transcripts: next.include_mic === false ? previous?.transcripts ?? [] : next.transcripts,
    };
  }

  async function captureAmbientCameras() {
    setError("");
    setBusy("ambient_cameras");
    setCameraPanelMode("photo");
    try {
      const response = await captureSceneAmbientInput({
        include_cameras: true,
        include_mic: false,
        mic_seconds: 1,
        camera_indices: [0, 1],
        cam0_rotate_180: cam0Rotate180,
      });
      setAmbientInput((previous) => mergeAmbientInput(previous, response.data));
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function captureAmbientTranscript() {
    setError("");
    setBusy("ambient_transcript");
    try {
      const response = await captureSceneAmbientInput({
        include_cameras: false,
        include_mic: true,
        mic_seconds: 4,
        camera_indices: [0, 1],
        cam0_rotate_180: cam0Rotate180,
      });
      setAmbientInput((previous) => mergeAmbientInput(previous, response.data));
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function openLiveCamera(cameraIndex = livePreviewCameraIndex) {
    setError("");
    setBusy("camera_stream");
    setCameraPanelMode("video");
    setLivePreviewCameraIndex(cameraIndex);
    try {
      await cameraStream.start({ cameraIndex, width: 1280, height: 720, backend: "auto" });
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function runOrientedObservation() {
    setError("");
    setBusy("oriented_scan");
    try {
      const response = await runSceneOrientedScan({
        authorized: true,
        title: "lelamp_oriented_scene",
        mode: "multi_axis",
        tilt_motor: "base_pitch",
        yaw_delta: 8,
        pitch_delta: 6,
        view_limit: 5,
        max_step: 3,
        hold_seconds: 0.45,
        camera_index: selectedCameraIndex,
        cam0_rotate_180: cam0Rotate180,
        include_mic: false,
        lux,
        people_count: peopleCount,
        presence,
        projector_blocked: projectorBlocked,
        calendar_event_now: calendarEventNow,
      });
      setOrientedScan(response.data);
      setMotionStatus(response.data.preflight ? readPreflight(response.data.preflight) : null);
      setSuggestions(response.data.suggestions ?? []);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function runTargetTracking() {
    setError("");
    setBusy("tracking_run");
    try {
      const response = await runSceneTracking({
        authorized: true,
        camera_index: selectedCameraIndex,
        backend: "yolo",
        frames: 16,
        move: true,
        max_step: 1.5,
        yaw_gain: 4,
        pitch_gain: 3,
        deadband: 0.1,
        min_hits: 2,
      });
      setTrackingRun(response.data);
      const preflight = response.data.preflight;
      setMotionStatus(preflight ? readPreflight(preflight) : null);
      await cameraStream.refresh();
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function submitReading() {
    setError("");
    setBusy("environment");
    try {
      const response = await submitEnvironmentReading({
        lux,
        people_count: peopleCount,
        presence,
        speech_active: speechActive,
        projector_blocked: projectorBlocked,
        calendar_event_now: calendarEventNow,
      });
      setEnvironmentResult(response.data);
      setSuggestions(response.data.suggestions ?? []);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function quickEvent(eventType: string, description: string) {
    setError("");
    setBusy(eventType);
    try {
      const response = await reportSceneEvent({ event_type: eventType, description, confidence: 0.9 });
      const nextSuggestions = response.data.suggestions as SceneWorkflowSuggestion[] | undefined;
      if (Array.isArray(nextSuggestions)) setSuggestions(nextSuggestions);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function runSuggestion(suggestion: SceneWorkflowSuggestion) {
    setError("");
    setBusy(suggestion.action);
    try {
      const event = suggestion.metadata?.event;
      const response = await triggerSceneWorkflow({
        action: suggestion.action,
        authorized: true,
        event: isSceneEvent(event) ? event : undefined,
        title: suggestion.title,
        ambient_lux: suggestion.action === "display_profile_adjustment" ? lux : undefined,
        participants: ["Unknown"],
      });
      setTriggerResult(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  const latest = events.length ? events[events.length - 1] : undefined;

  return (
    <>
      <PageHeader
        title="场景感知"
        description="用户授权后读取相机、麦克风活动、投影/显示和系统传感器；LeLamp 可左右转动并抬头/低头扫描场景"
        actions={
          <>
            <button className="primary-button" onClick={() => void captureSensorSnapshot()} disabled={busy === "sensors"}><Sparkles size={16} />读取当前传感器</button>
            <button className="ghost-button" onClick={() => void captureAmbientCameras()} disabled={busy === "ambient_cameras"}><Camera size={16} />检查双摄</button>
            <button className="ghost-button" onClick={() => void captureAmbientTranscript()} disabled={busy === "ambient_transcript"}><MessageCircle size={16} />语音听写</button>
            <button className="ghost-button" onClick={() => void (cameraStream.isRunning ? cameraStream.stop() : openLiveCamera(livePreviewCameraIndex))} disabled={cameraStream.loading || busy === "camera_stream"}>
              {cameraStream.isRunning ? <CameraOff size={16} /> : <Camera size={16} />}
              {cameraStream.isRunning ? "关闭相机常开" : "开启相机预览"}
            </button>
            <button className="ghost-button" onClick={() => void runOrientedObservation()} disabled={busy === "oriented_scan"}><RotateCw size={16} />左右/抬低扫描</button>
            <button className="ghost-button" onClick={() => void runTargetTracking()} disabled={busy === "tracking_run"}><Eye size={16} />目标追踪试运行</button>
            <button className="ghost-button" onClick={() => void load()}><RefreshCw size={16} />刷新事件</button>
          </>
        }
      />
      <div className="page-grid">
        {error && <div className="danger-panel">操作失败：{error}</div>}
        <div className="grid-4">
          <InfoCard icon={<Eye size={20} />} label="最近事件" value={latest ? friendlySceneEvent(latest.event_type) : "暂无"} note={latest?.suggestion ?? "点击读取当前传感器"} status={<StatusBadge status={latest ? "completed" : "pending"} />} />
          <InfoCard icon={<Camera size={20} />} label="相机视角" value={friendlyStatus(sensorStatus(sensorSnapshot, "camera"))} note={sensorSnapshot?.camera?.source ? `来源：${String(sensorSnapshot.camera.source)}` : "单帧采集，不常驻解析"} status={<StatusBadge status={sensorStatus(sensorSnapshot, "camera")} />} />
          <InfoCard icon={<MonitorX size={20} />} label="投影/显示" value={friendlyStatus(sensorStatus(sensorSnapshot, "projection"))} note={projectionNote(sensorSnapshot)} status={<StatusBadge status={sensorStatus(sensorSnapshot, "projection")} />} />
          <InfoCard icon={<Users size={20} />} label="会议/声音" value={speechActivityLabel(sensorSnapshot)} note={micNote(sensorSnapshot)} status={<StatusBadge status={sensorStatus(sensorSnapshot, "microphone")} />} />
          <InfoCard icon={<RotateCw size={20} />} label="智能台灯转动" value={friendlyStatus(motionStatus?.status ?? "pending")} note={motionStatusNote(motionStatus)} status={<StatusBadge status={motionStatus?.status ?? "pending"} />} />
        </div>

        <Card
          title="双摄像头检查 / 语音转文字"
          subtitle="双摄检查只采集 cam0/cam1 快照；语音听写只录制左右声道并生成类似微信的文字气泡"
          action={<StatusBadge status={ambientInput?.status ?? "pending"} label={ambientInput ? friendlyStatus(ambientInput.status) : "等待"} />}
        >
          <div className="scene-actions scene-actions--compact">
            <div className="segmented-control scene-camera-mode-toggle" role="tablist" aria-label="相机查看模式">
              <button className={cameraPanelMode === "video" ? "selected" : ""} type="button" onClick={() => void openLiveCamera(livePreviewCameraIndex)}>
                <Video size={15} />摄像
              </button>
              <button className={cameraPanelMode === "photo" ? "selected" : ""} type="button" onClick={() => setCameraPanelMode("photo")}>
                <ImageIcon size={15} />照片
              </button>
            </div>
            <label className="inline-check scene-toggle-control">
              <input type="checkbox" checked={cam0Rotate180} onChange={(event) => setCam0Rotate180(event.target.checked)} />
              <RotateCw size={15} />
              cam0 旋转 180°
            </label>
            <SkillChip muted>{cam0Rotate180 ? "cam0 default: 180deg" : "cam0 default: 0deg"}</SkillChip>
          </div>
          <div className="scene-sensor-grid">
            {[0, 1].map((index) => {
              const camera = ambientInput?.cameras.find((item) => Number(item.camera_index) === index);
              return (
                <div className="scene-sensor-card" key={index}>
                  <strong>{index === 0 ? "cam0 固定相机" : "cam1 灯头相机"}</strong>
                  <StatusBadge status={camera?.status ?? "pending"} label={camera ? friendlyStatus(camera.status) : "等待"} />
                  <span>{cameraNoteFromAmbient(camera)}</span>
                </div>
              );
            })}
            <div className="scene-sensor-card">
              <strong>左声道</strong>
              <StatusBadge status={ambientTranscriptStatus(ambientInput, "left")} label={ambientTranscriptLabel(ambientInput, "left")} />
              <span>{ambientAudioLevel(ambientInput, "left")}</span>
            </div>
            <div className="scene-sensor-card">
              <strong>右声道</strong>
              <StatusBadge status={ambientTranscriptStatus(ambientInput, "right")} label={ambientTranscriptLabel(ambientInput, "right")} />
              <span>{ambientAudioLevel(ambientInput, "right")}</span>
            </div>
          </div>
          {cameraPanelMode === "video" ? (
            <div className="scene-live-camera-panel">
              <div className="scene-live-toolbar">
                <div className="segmented-control" role="tablist" aria-label="实时相机选择">
                  {[0, 1].map((index) => (
                    <button className={livePreviewCameraIndex === index ? "selected" : ""} type="button" key={index} onClick={() => void openLiveCamera(index)}>
                      <Camera size={15} />{index === 0 ? "cam0" : "cam1"}
                    </button>
                  ))}
                </div>
                <button className="ghost-button" type="button" onClick={() => void (cameraStream.isRunning ? cameraStream.stop() : openLiveCamera(livePreviewCameraIndex))} disabled={cameraStream.loading || busy === "camera_stream"}>
                  {cameraStream.isRunning ? <CameraOff size={16} /> : <Video size={16} />}
                  {cameraStream.isRunning ? "停止摄像" : "开始摄像"}
                </button>
              </div>
              <div className="scene-camera-preview scene-camera-preview--inline">
                {cameraStream.isRunning && cameraStream.streamUrl ? (
                  <img className={livePreviewCameraIndex === 0 && cam0Rotate180 ? "camera-rotated-180" : undefined} src={cameraStream.streamUrl} alt={`camera ${livePreviewCameraIndex} live preview`} />
                ) : (
                  <div className="scene-camera-placeholder">
                    <Video size={28} />
                    <strong>等待实时摄像</strong>
                    <span>选择 cam0 或 cam1 后点击“开始摄像”。</span>
                  </div>
                )}
              </div>
              <div className="scene-actions">
                {cameraStream.previewUrl && <a className="ghost-button" href={cameraStream.previewUrl} target="_blank" rel="noreferrer">新窗口查看</a>}
                <SkillChip>camera {cameraStream.cameraIndex ?? livePreviewCameraIndex}</SkillChip>
                <SkillChip muted>{cameraStream.isRunning ? "live video" : "video stopped"}</SkillChip>
              </div>
              {cameraStreamIssue && <div className="danger-panel">相机预览不可用：{cameraStreamIssue}</div>}
            </div>
          ) : (
            <div className="scene-camera-pair-grid">
              {[0, 1].map((index) => {
                const camera = ambientInput?.cameras.find((item) => Number(item.camera_index) === index);
                return (
                  <div className="scene-camera-frame" key={index}>
                    <div className="row-between">
                      <strong>{index === 0 ? "cam0 固定相机" : "cam1 灯头相机"}</strong>
                      <StatusBadge status={camera?.status ?? "pending"} label={camera ? friendlyStatus(camera.status) : "等待"} />
                    </div>
                    {camera?.image_url ? (
                      <img className={cameraImageClass(index, camera, cam0Rotate180)} src={camera.image_url} alt={`camera ${index} capture`} />
                    ) : (
                      <div className="scene-camera-frame__empty">
                        <CameraOff size={22} />
                        <span>{camera?.message || "还没有相机图片"}</span>
                      </div>
                    )}
                    <span className="small muted">{ambientCameraMetrics(camera)}</span>
                  </div>
                );
              })}
            </div>
          )}
          <div className="scene-chat-list">
            {ambientInput?.transcripts.length ? (
              ambientInput.transcripts.map((item) => (
                <div className={`scene-chat-bubble scene-chat-bubble--${item.channel === "right" ? "right" : "left"}`} key={`${item.channel}-${item.audio_workspace_name ?? item.label}`}>
                  <div className="scene-chat-meta">
                    <Mic size={14} />
                    <strong>{item.label || friendlyAudioChannel(item.channel)}</strong>
                    <StatusBadge status={item.status} label={friendlyStatus(item.status)} />
                  </div>
                  <p>{item.text || item.message || "没有识别到文字"}</p>
                </div>
              ))
            ) : (
              <div className="scene-camera-placeholder">
                <MessageCircle size={28} />
                <strong>等待语音输入</strong>
                <span>点击“语音听写”后说一句话；系统会按左右声道生成文字气泡。</span>
              </div>
            )}
          </div>
          <div className="scene-actions">
            <button className="primary-button" onClick={() => void captureAmbientCameras()} disabled={busy === "ambient_cameras"}><Camera size={16} />检查双摄</button>
            <button className="ghost-button" onClick={() => void captureAmbientTranscript()} disabled={busy === "ambient_transcript"}><MessageCircle size={16} />录音转文字</button>
            <SkillChip>摄像头零和摄像头一</SkillChip>
            <SkillChip muted>{ambientInput?.microphone?.channel_count ? `${String(ambientInput.microphone.channel_count)} channel audio` : "left/right ASR"}</SkillChip>
          </div>
          <details className="advanced-panel">
            <summary>双摄 / 转写原始结果</summary>
            <div className="advanced-panel__content">
              <pre className="json-preview scene-preview">{JSON.stringify(ambientInput ?? { status: "pending", message: "等待采集双摄像头和左右声道语音。" }, null, 2)}</pre>
            </div>
          </details>
        </Card>

        <Card
          title="当前传感器快照"
          subtitle="一次性读取相机、麦克风活动、投影/显示连接、系统温度/负载，并转换成场景事件"
          action={<StatusBadge status={sensorSnapshot?.status ?? "pending"} label={sensorSnapshot ? friendlyStatus(sensorSnapshot.status) : "等待读取"} />}
        >
          <div className="scene-sensor-grid">
            <div className="scene-sensor-card">
              <strong>相机</strong>
              <StatusBadge status={sensorStatus(sensorSnapshot, "camera")} label={friendlyStatus(sensorStatus(sensorSnapshot, "camera"))} />
              <span>{cameraNote(sensorSnapshot)}</span>
            </div>
            <div className="scene-sensor-card">
              <strong>麦克风</strong>
              <StatusBadge status={sensorStatus(sensorSnapshot, "microphone")} label={friendlyStatus(sensorStatus(sensorSnapshot, "microphone"))} />
              <span>{micNote(sensorSnapshot)}</span>
            </div>
            <div className="scene-sensor-card">
              <strong>投影/显示</strong>
              <StatusBadge status={sensorStatus(sensorSnapshot, "projection")} label={friendlyStatus(sensorStatus(sensorSnapshot, "projection"))} />
              <span>{projectionNote(sensorSnapshot)}</span>
            </div>
            <div className="scene-sensor-card">
              <strong>系统传感器</strong>
              <StatusBadge status={sensorSnapshot ? "completed" : "pending"} label={sensorSnapshot ? "已读取" : "等待"} />
              <span>{systemSensorNote(sensorSnapshot)}</span>
            </div>
          </div>
          <div className="scene-actions">
            <button className="primary-button" onClick={() => void captureSensorSnapshot()} disabled={busy === "sensors"}><Sparkles size={16} />重新读取传感器</button>
            <SkillChip>仅单次显式读取</SkillChip>
            <SkillChip muted>不被动解析投影内容</SkillChip>
          </div>
          <details className="advanced-panel">
            <summary>传感器原始结果</summary>
            <div className="advanced-panel__content">
              <pre className="json-preview scene-preview">{JSON.stringify(sensorSnapshot ?? { status: "pending", message: "点击“读取当前传感器”后显示设备快照。" }, null, 2)}</pre>
            </div>
          </details>
        </Card>

        <Card
          title="智能台灯全方位扫描"
          subtitle="先读取电机姿态，再由用户显式触发左右和抬头/低头多视角采集；默认不后台常开、不解析投影内容"
          action={<StatusBadge status={orientedScan?.status ?? motionStatus?.status ?? "pending"} label={orientedScan ? friendlyStatus(orientedScan.status) : friendlyStatus(motionStatus?.status ?? "pending")} />}
        >
          <div className="scene-sensor-grid">
            <div className="scene-sensor-card">
              <strong>串口</strong>
              <StatusBadge status={motionStatus?.serial_detected ? "completed" : "needs_hardware"} label={motionStatus?.serial_detected ? "已检测" : "未检测"} />
              <span>{motionStatus?.port || "等待预检"} {motionStatus?.serial_candidates?.length ? `· 候选 ${motionStatus.serial_candidates.join(", ")}` : ""}</span>
            </div>
            <div className="scene-sensor-card">
              <strong>姿态读取</strong>
              <StatusBadge status={motionStatus?.pose_readable ? "completed" : "pending"} label={motionStatus?.pose_readable ? "可读取" : "待读取"} />
              <span>{poseSummary(motionStatus)}</span>
            </div>
            <div className="scene-sensor-card">
              <strong>硬件写入</strong>
              <StatusBadge status={motionStatus?.hardware_enabled ? "available" : "adapter_ready"} label={motionStatus?.hardware_enabled ? "已启用" : "未启用"} />
              <span>{motionStatus?.hardware_enabled ? "允许显式按钮触发小范围转动" : "需要以 OPENCLAW_ENABLE_HARDWARE=1 重启控制台后才会转动"}</span>
            </div>
            <div className="scene-sensor-card">
              <strong>最近扫描</strong>
              <StatusBadge status={orientedScan?.status ?? "pending"} label={orientedScan ? `${orientedScan.views.length} 个视角` : "等待"} />
              <span>{orientedScan?.message ?? "点击后按中心、左、右、抬头、低头多视角采集画面"}</span>
            </div>
          </div>
          <div className="scene-actions">
            <button className="ghost-button" onClick={() => void refreshMotionStatus()} disabled={busy === "motion_status"}><RefreshCw size={16} />重新预检</button>
            <button className="primary-button" onClick={() => void runOrientedObservation()} disabled={busy === "oriented_scan"}><RotateCw size={16} />授权左右/抬低扫描</button>
            <SkillChip>底座水平轴和底座俯仰轴</SkillChip>
            <SkillChip muted>结束后返回起始姿态</SkillChip>
          </div>
          {orientedScan?.views.length ? (
            <div className="oriented-view-grid">
              {orientedScan.views.map((view) => (
                <div className="oriented-view-card" key={view.index}>
                  <div className="row-between">
                    <strong>{friendlyViewLabel(view.label) || `视角 ${view.index + 1}`}</strong>
                    <SkillChip>{`请求 ${Number(view.requested_yaw_offset).toFixed(1)} / ${Number(view.requested_pitch_offset ?? 0).toFixed(1)}`}</SkillChip>
                  </div>
                  <span>{view.events.length ? `${view.events.length} 个事件` : "未生成事件"} · 实际 {Number(view.actual_yaw_offset ?? view.requested_yaw_offset).toFixed(1)} / {Number(view.actual_pitch_offset ?? view.requested_pitch_offset ?? 0).toFixed(1)}</span>
                  <small>{snapshotImageName(view.snapshot)}</small>
                </div>
              ))}
            </div>
          ) : (
            <span className="small muted">尚未执行全方位扫描。若按钮返回“未启用”，需要以硬件模式重启控制台。</span>
          )}
          <details className="advanced-panel">
            <summary>转动观察诊断</summary>
            <div className="advanced-panel__content">
              <pre className="json-preview scene-preview">{JSON.stringify(orientedScan ?? motionStatus ?? { status: "pending", message: "等待 LeLamp 预检或转动观察。" }, null, 2)}</pre>
            </div>
          </details>
        </Card>

        <Card
          title="目标追踪试运行"
          subtitle="复用 LeLamp 现有 person_tracker：YOLO/人脸检测，head 模式只控制 base_yaw + wrist_pitch；有限帧运行后自动恢复相机预览"
          action={<StatusBadge status={trackingRun?.status ?? "pending"} label={trackingRun ? friendlyStatus(trackingRun.status) : "等待"} />}
        >
          <div className="scene-sensor-grid">
            <div className="scene-sensor-card">
              <strong>目标检测</strong>
              <StatusBadge status={trackingRun?.target_count ? "completed" : trackingRun ? "no_target" : "pending"} label={trackingRun ? `${trackingRun.target_count} 帧` : "等待"} />
              <span>{trackingRun?.target_count ? "已检测到可追踪目标" : "当前视野未检测到人/脸目标"}</span>
            </div>
            <div className="scene-sensor-card">
              <strong>追踪移动</strong>
              <StatusBadge status={trackingRun?.move_count ? "completed" : trackingRun ? "pending" : "pending"} label={trackingRun ? `${trackingRun.move_count} 次` : "等待"} />
              <span>连续命中后才会发送小步移动</span>
            </div>
            <div className="scene-sensor-card">
              <strong>追踪轴</strong>
              <StatusBadge status="available" label="灯头模式" />
              <span>底座水平轴和腕部俯仰轴</span>
            </div>
            <div className="scene-sensor-card">
              <strong>相机预览</strong>
              <StatusBadge status={cameraStream.isRunning ? "online" : "stopped"} label={cameraStream.isRunning ? "已恢复" : "已关闭"} />
              <span>试运行会临时释放相机再恢复预览</span>
            </div>
          </div>
          <div className="scene-actions">
            <button className="primary-button" onClick={() => void runTargetTracking()} disabled={busy === "tracking_run"}><Eye size={16} />运行 16 帧追踪</button>
            <SkillChip>人物追踪器</SkillChip>
            <SkillChip muted>有限次数试运行</SkillChip>
          </div>
          <details className="advanced-panel">
            <summary>追踪诊断</summary>
            <div className="advanced-panel__content">
              <pre className="json-preview scene-preview">{JSON.stringify(trackingRun ?? { status: "pending", message: "等待运行目标追踪试运行。" }, null, 2)}</pre>
            </div>
          </details>
        </Card>

        <div className="scene-grid">
          <Card title="桌面图像观察" subtitle="用户主动拍照或上传后才分析，并生成场景建议">
            <div className="scene-actions">
              <button className="primary-button" onClick={() => void captureDeviceObservation()} disabled={busy === "image"}><Camera size={16} />调用设备相机拍照</button>
              <button className="ghost-button" onClick={() => uploadInputRef.current?.click()} disabled={busy === "image"}><Upload size={16} />上传图片</button>
              <SkillChip>仅显式授权拍照</SkillChip>
            </div>
            <input
              ref={uploadInputRef}
              className="hidden-file-input"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) void analyzeFile(file);
              }}
            />
            <div className="definition-grid">
              <span>状态</span><StatusBadge status={imageResult?.status ?? "pending"} />
              <span>观察结果</span><strong>{imageResult ? `${imageResult.events.length} 个事件` : "等待拍照或上传"}</strong>
              <span>事件数</span><strong>{imageResult?.events.length ?? 0}</strong>
            </div>
            <details className="advanced-panel">
              <summary>图像诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview scene-preview">{JSON.stringify(imageResult ?? { status: "pending", message: "等待拍照或上传桌面图片。" }, null, 2)}</pre>
              </div>
            </details>
          </Card>

          <Card title="环境读数" subtitle="模拟或接入传感器读数，用于生成会议、光照、投影遮挡等提示">
            <div className="environment-form">
              <label><span>照度</span><input className="input" type="number" value={lux} onChange={(event) => setLux(Number(event.target.value))} /></label>
              <label><span>人数</span><input className="input" type="number" value={peopleCount} onChange={(event) => setPeopleCount(Number(event.target.value))} /></label>
              <label className="inline-check"><input type="checkbox" checked={presence} onChange={(event) => setPresence(event.target.checked)} />有人靠近</label>
              <label className="inline-check"><input type="checkbox" checked={speechActive} onChange={(event) => setSpeechActive(event.target.checked)} />语音活动</label>
              <label className="inline-check"><input type="checkbox" checked={projectorBlocked} onChange={(event) => setProjectorBlocked(event.target.checked)} />投影遮挡</label>
              <label className="inline-check"><input type="checkbox" checked={calendarEventNow} onChange={(event) => setCalendarEventNow(event.target.checked)} />当前有会议日程</label>
            </div>
            <div className="scene-actions">
              <button className="primary-button" onClick={() => void submitReading()} disabled={busy === "environment"}><Lightbulb size={16} />提交读数</button>
              <button className="ghost-button" onClick={() => void quickEvent("projection_blocked", "用户手动报告投影路径被遮挡")} disabled={Boolean(busy)}>记录遮挡</button>
              <button className="ghost-button" onClick={() => void quickEvent("paper_detected", "用户手动报告桌面有纸质文件")} disabled={Boolean(busy)}>记录纸质文件</button>
            </div>
            <div className="definition-grid">
              <span>状态</span><StatusBadge status={environmentResult?.status ?? "pending"} />
              <span>生成事件</span><strong>{environmentResult?.event_count ?? 0}</strong>
              <span>建议</span><strong>{environmentResult?.suggestions?.length ?? 0} 条</strong>
            </div>
            <details className="advanced-panel">
              <summary>环境诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview scene-preview">{JSON.stringify(environmentResult ?? { status: "pending", message: "等待环境读数。" }, null, 2)}</pre>
              </div>
            </details>
          </Card>
        </div>

        <Card
          title="工作流建议/触发任务"
          subtitle="把场景事件转换成扫描任务、投影提示、会议模式或本地提醒；全部需要用户点击触发"
          action={<StatusBadge status={suggestions.length ? "completed" : "pending"} label={`${suggestions.length} 条`} />}
        >
          <div className="scene-workflow-grid">
            {suggestions.map((suggestion) => (
              <div className="scene-workflow-card" key={suggestion.action}>
                <div className="row-between">
                  <strong>{suggestion.title}</strong>
                  <StatusBadge status="completed" label={`${Math.round(Number(suggestion.confidence) * 100)}%`} />
                </div>
                <p>{suggestion.description}</p>
                <div className="scene-workflow-meta">
                  <SkillChip>{friendlySuggestionCategory(suggestion.category)}</SkillChip>
                  <SkillChip muted>{friendlySafeDefault(suggestion.safe_default)}</SkillChip>
                </div>
                <button className="primary-button" onClick={() => void runSuggestion(suggestion)} disabled={busy === suggestion.action}>
                  <Sparkles size={16} />触发
                </button>
                {triggerResult?.action === suggestion.action && triggerResult.next_url && (
                  <a className="ghost-button" href={triggerResult.next_url}>打开后续页面</a>
                )}
              </div>
            ))}
            {!suggestions.length && <span className="small muted">暂无可执行建议。提交环境读数、记录遮挡或上传桌面图像后会生成。</span>}
          </div>
          <div className="scene-workflow-result">
            <div className="row-between">
              <span className="small muted">最近触发结果</span>
              <Bell size={16} />
            </div>
            <div className="definition-grid">
              <span>状态</span><StatusBadge status={triggerResult?.status ?? "pending"} />
              <span>结果</span><strong>{triggerSummary(triggerResult)}</strong>
            </div>
            <details className="advanced-panel">
              <summary>触发诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview scene-preview">{JSON.stringify(triggerResult ?? { status: "pending", message: "等待用户点击触发建议。" }, null, 2)}</pre>
              </div>
            </details>
            {triggerResult?.next_url && <a className="primary-button scene-next-link" href={triggerResult.next_url}>继续到 Documents 扫描</a>}
          </div>
        </Card>

        <Card title="最近场景事件与工作流建议">
          <div className="scene-event-list">
            {events.slice().reverse().map((event, index) => (
              <div className="scene-event-card" key={`${event.event_type}-${index}`}>
                <div className="row-between">
                  <strong>{friendlySceneEvent(event.event_type)}</strong>
                  <StatusBadge status="completed" label={`${Math.round(Number(event.confidence) * 100)}%`} />
                </div>
                <p>{event.description}</p>
                <SkillChip>{event.suggestion}</SkillChip>
              </div>
            ))}
            {!events.length && <span className="small muted">暂无场景事件。提交读数或上传桌面图像后会显示。</span>}
          </div>
        </Card>
      </div>
    </>
  );
}
