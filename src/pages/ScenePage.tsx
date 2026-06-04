import { Bell, Camera, CameraOff, Eye, FileSearch, Lightbulb, MonitorX, RefreshCw, RotateCw, Sparkles, Upload, Users } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiErrorMessage } from "../api/client";
import {
  captureDeviceSceneObservation,
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
import "./pages.css";

export function ScenePage() {
  const [events, setEvents] = useState<SceneEvent[]>([]);
  const [suggestions, setSuggestions] = useState<SceneWorkflowSuggestion[]>([]);
  const [triggerResult, setTriggerResult] = useState<SceneWorkflowTriggerResponse | null>(null);
  const [imageResult, setImageResult] = useState<SceneObserveImageResponse | null>(null);
  const [sensorSnapshot, setSensorSnapshot] = useState<SceneSensorSnapshotResponse | null>(null);
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
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const cameraStream = useCameraStream(false, { cameraIndex: 1, width: 1280, height: 720, backend: "auto" });
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
            <button className="ghost-button" onClick={() => void (cameraStream.isRunning ? cameraStream.stop() : cameraStream.start())} disabled={cameraStream.loading}>
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
          <InfoCard icon={<RotateCw size={20} />} label="LeLamp 转动" value={friendlyStatus(motionStatus?.status ?? "pending")} note={motionStatusNote(motionStatus)} status={<StatusBadge status={motionStatus?.status ?? "pending"} />} />
        </div>

        <Card
          title="设备实时视角"
          subtitle="用于确认 LeLamp 当前看到的画面；开启后只显示预览，分析仍需用户点击读取传感器或转动观察"
          action={<StatusBadge status={cameraStream.isRunning ? "online" : "stopped"} label={cameraStream.isRunning ? "预览中" : "已关闭"} />}
        >
          <div className="scene-camera-preview">
            {cameraStream.isRunning && cameraStream.streamUrl ? (
              <img src={cameraStream.streamUrl} alt="LeLamp camera live preview" />
            ) : (
              <div className="scene-camera-placeholder">
                <Camera size={28} />
                <strong>相机预览未开启</strong>
                <span>点击“开启相机预览”后可在这里看到设备视角。</span>
              </div>
            )}
          </div>
          <div className="scene-actions">
            <button className="primary-button" onClick={() => void (cameraStream.isRunning ? cameraStream.stop() : cameraStream.start())} disabled={cameraStream.loading}>
              {cameraStream.isRunning ? <CameraOff size={16} /> : <Camera size={16} />}
              {cameraStream.isRunning ? "关闭相机常开" : "开启相机预览"}
            </button>
            {cameraStream.previewUrl && <a className="ghost-button" href={cameraStream.previewUrl} target="_blank" rel="noreferrer">新窗口查看</a>}
            <SkillChip>camera {selectedCameraIndex}</SkillChip>
            <SkillChip muted>{cameraStream.isRunning ? "always-on preview" : "manual preview"}</SkillChip>
          </div>
          {cameraStream.error && <div className="danger-panel">相机预览不可用：{cameraStream.error}</div>}
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
            <SkillChip>explicit one-shot</SkillChip>
            <SkillChip muted>no passive projection parsing</SkillChip>
          </div>
          <details className="advanced-panel">
            <summary>传感器原始结果</summary>
            <div className="advanced-panel__content">
              <pre className="json-preview scene-preview">{JSON.stringify(sensorSnapshot ?? { status: "pending", message: "点击“读取当前传感器”后显示设备快照。" }, null, 2)}</pre>
            </div>
          </details>
        </Card>

        <Card
          title="LeLamp 全方位扫描"
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
            <SkillChip>base_yaw + base_pitch</SkillChip>
            <SkillChip muted>returns to start pose</SkillChip>
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
              <StatusBadge status="available" label="head" />
              <span>base_yaw + wrist_pitch</span>
            </div>
            <div className="scene-sensor-card">
              <strong>相机预览</strong>
              <StatusBadge status={cameraStream.isRunning ? "online" : "stopped"} label={cameraStream.isRunning ? "已恢复" : "已关闭"} />
              <span>试运行会临时释放相机再恢复预览</span>
            </div>
          </div>
          <div className="scene-actions">
            <button className="primary-button" onClick={() => void runTargetTracking()} disabled={busy === "tracking_run"}><Eye size={16} />运行 16 帧追踪</button>
            <SkillChip>person_tracker</SkillChip>
            <SkillChip muted>bounded trial</SkillChip>
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
              <SkillChip>explicit capture only</SkillChip>
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
              <label><span>Lux</span><input className="input" type="number" value={lux} onChange={(event) => setLux(Number(event.target.value))} /></label>
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

function isSceneEvent(value: unknown): value is SceneEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Partial<SceneEvent>;
  return typeof event.event_type === "string" && typeof event.description === "string";
}

function readPreflight(value: SceneOrientedScanResponse["preflight"]): LeLampMotionStatusResponse | null {
  if (!value || typeof value !== "object") return null;
  return value as LeLampMotionStatusResponse;
}

function friendlySceneEvent(value: string) {
  const labels: Record<string, string> = {
    paper_detected: "发现纸质文件",
    projection_blocked: "投影被遮挡",
    meeting_detected: "检测到会议场景",
    environment_reading: "环境读数",
    desk_scene_observation: "桌面观察",
  };
  return labels[value] ?? value.replace(/[_-]+/g, " ");
}

function friendlySuggestionCategory(value: string) {
  const labels: Record<string, string> = {
    scan: "扫描",
    projection: "投影",
    meeting: "会议",
    reminder: "提醒",
    desktop: "桌面任务",
  };
  return labels[value] ?? value.replace(/[_-]+/g, " ");
}

function friendlySafeDefault(value: string) {
  const labels: Record<string, string> = {
    requires_user_click: "需用户点击",
    explicit_confirmation: "需明确确认",
    suggestion_only: "仅建议",
    create_desktop_task: "创建待确认任务",
    render_projection_status_card: "生成投影提示",
    enable_meeting_mode_after_click: "点击后开启会议模式",
    digital_display_profile: "更新显示配置",
    local_reminder_draft: "本地提醒草稿",
  };
  return labels[value] ?? value.replace(/[_-]+/g, " ");
}

function friendlyViewLabel(value: unknown) {
  const key = String(value ?? "");
  const labels: Record<string, string> = {
    center: "中心视角",
    left: "左侧视角",
    right: "右侧视角",
    up: "上方视角",
    down: "下方视角",
    left_up: "左上视角",
    right_up: "右上视角",
    left_down: "左下视角",
    right_down: "右下视角",
  };
  return labels[key] ?? key.replace(/[_-]+/g, " ");
}

function sensorStatus(snapshot: SceneSensorSnapshotResponse | null, key: "camera" | "microphone" | "projection") {
  if (!snapshot) return "pending";
  if (key === "projection") {
    const projection = snapshot.hardware?.projection;
    return readObjectStatus(projection);
  }
  return readObjectStatus(snapshot[key]);
}

function readCameraIndex(snapshot: SceneSensorSnapshotResponse | null) {
  const value = snapshot?.camera?.camera_index;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function readObjectStatus(value: unknown) {
  if (!value || typeof value !== "object") return "unavailable";
  return String((value as Record<string, unknown>).status ?? "unavailable");
}

function friendlyStatus(status: unknown) {
  const value = String(status ?? "");
  const labels: Record<string, string> = {
    ok: "正常",
    online: "在线",
    completed: "已完成",
    captured: "已采集",
    available: "可用",
    pending: "等待",
    skipped: "已跳过",
    unavailable: "不可用",
    adapter_ready: "待接入",
    backend_missing: "缺少后端",
    needs_hardware: "需要硬件",
    needs_confirmation: "需确认",
    blocked: "已阻止",
    error: "异常",
    failed: "失败",
  };
  return labels[value] ?? (value || "等待");
}

function motionStatusNote(status: LeLampMotionStatusResponse | null) {
  if (!status) return "等待 LeLamp 串口预检";
  if (!status.serial_detected) return "未检测到 /dev/ttyACM* 或 /dev/ttyUSB*";
  if (!status.pose_readable) return status.message || "串口存在，但姿态不可读";
  if (!status.hardware_enabled) return "姿态可读；需重启启用硬件写入后才能转动";
  return status.message || "姿态可读，可由按钮触发小范围转动";
}

function poseSummary(status: LeLampMotionStatusResponse | null) {
  if (!status?.pose_readable) return status?.pose_error || status?.message || "等待读取";
  const yaw = Number(status.pose?.base_yaw);
  const pitch = Number(status.pose?.base_pitch);
  const yawText = Number.isFinite(yaw) ? yaw.toFixed(1) : "?";
  const pitchText = Number.isFinite(pitch) ? pitch.toFixed(1) : "?";
  return `base_yaw ${yawText} · base_pitch ${pitchText}`;
}

function snapshotImageName(snapshot: unknown) {
  if (!snapshot || typeof snapshot !== "object") return "无快照信息";
  const camera = (snapshot as Record<string, unknown>).camera;
  if (!camera || typeof camera !== "object") return "无相机快照";
  const workspace = String((camera as Record<string, unknown>).workspace_name ?? "");
  return workspace || "相机未返回图片";
}

function cameraNote(snapshot: SceneSensorSnapshotResponse | null) {
  if (!snapshot) return "等待读取设备视角";
  const camera = snapshot.camera ?? {};
  const workspace = String(camera.workspace_name ?? "");
  const source = String(camera.source ?? "");
  if (workspace) return `${source || "camera"} · ${workspace}`;
  return String(camera.message ?? source ?? "未获得相机画面");
}

function micNote(snapshot: SceneSensorSnapshotResponse | null) {
  if (!snapshot) return "等待短采样";
  const mic = snapshot.microphone ?? {};
  const status = String(mic.status ?? "");
  if (status !== "completed") return String(mic.message ?? friendlyStatus(status));
  return `RMS ${String(mic.rms ?? 0)} · Peak ${String(mic.peak ?? 0)} · ${mic.activity_detected ? "有声音活动" : "未检测到明显声音"}`;
}

function projectionNote(snapshot: SceneSensorSnapshotResponse | null) {
  if (!snapshot) return "待读取连接状态";
  const projection = snapshot.hardware?.projection;
  if (!projection || typeof projection !== "object") return "未获得投影/显示状态";
  const details = (projection as Record<string, unknown>).details;
  const physical = details && typeof details === "object" ? String((details as Record<string, unknown>).physical_projector ?? "") : "";
  return physical ? `物理输出：${physical}` : friendlyStatus((projection as Record<string, unknown>).status);
}

function systemSensorNote(snapshot: SceneSensorSnapshotResponse | null) {
  if (!snapshot) return "待读取系统传感器";
  const sensors = snapshot.hardware?.sensors;
  if (!sensors || typeof sensors !== "object") return "无系统传感器数据";
  const data = sensors as Record<string, unknown>;
  const temp = data.cpu_temp !== null && data.cpu_temp !== undefined ? `${String(data.cpu_temp)}°C` : "温度未知";
  const memory = typeof data.memory_usage === "number" ? `${Math.round(data.memory_usage * 100)}% 内存` : "内存未知";
  const disk = typeof data.disk_usage === "number" ? `${Math.round(data.disk_usage * 100)}% 磁盘` : "磁盘未知";
  return `${temp} · ${memory} · ${disk}`;
}

function speechActivityLabel(snapshot: SceneSensorSnapshotResponse | null) {
  if (!snapshot) return "待检测";
  if (Boolean(snapshot.reading?.speech_active)) return "检测到声音";
  return "未检测到声音";
}

function triggerSummary(result: SceneWorkflowTriggerResponse | null) {
  if (!result) return "等待用户点击触发建议";
  return result.message || (result.next_url ? "已生成后续操作入口" : "建议已触发");
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read file."));
    reader.readAsDataURL(file);
  });
}
