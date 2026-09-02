import type { LeLampMotionStatusResponse, SceneAmbientCaptureResponse, SceneEvent, SceneOrientedScanResponse, SceneSensorSnapshotResponse, SceneWorkflowTriggerResponse } from "../api/types";

export function isSceneEvent(value: unknown): value is SceneEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Partial<SceneEvent>;
  return typeof event.event_type === "string" && typeof event.description === "string";
}

export function readPreflight(value: SceneOrientedScanResponse["preflight"]): LeLampMotionStatusResponse | null {
  if (!value || typeof value !== "object") return null;
  return value as LeLampMotionStatusResponse;
}

export function friendlySceneEvent(value: string) {
  const labels: Record<string, string> = {
    paper_detected: "发现纸质文件",
    projection_blocked: "投影被遮挡",
    meeting_detected: "检测到会议场景",
    environment_reading: "环境读数",
    desk_scene_observation: "桌面观察",
  };
  return labels[value] ?? value.replace(/[_-]+/g, " ");
}

export function friendlySuggestionCategory(value: string) {
  const labels: Record<string, string> = {
    scan: "扫描",
    projection: "投影",
    meeting: "会议",
    reminder: "提醒",
    desktop: "桌面任务",
  };
  return labels[value] ?? value.replace(/[_-]+/g, " ");
}

export function friendlySafeDefault(value: string) {
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

export function friendlyViewLabel(value: unknown) {
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

export function sensorStatus(snapshot: SceneSensorSnapshotResponse | null, key: "camera" | "microphone" | "projection") {
  if (!snapshot) return "pending";
  if (key === "projection") {
    const projection = snapshot.hardware?.projection;
    return readObjectStatus(projection);
  }
  return readObjectStatus(snapshot[key]);
}

export function cameraStreamIssueMessage(status: { status?: string; details?: Record<string, unknown>; message?: string } | null) {
  if (!status) return "";
  const publicStatus = String(status.status || "");
  if (!["error", "failed", "blocked", "unavailable"].includes(publicStatus)) return "";
  const details = status.details ?? {};
  const detailError = String(details.error || "");
  const detailStatus = String(details.status || "");
  const message = String(status.message || "");
  return detailError || (detailStatus ? `stream status: ${detailStatus}` : message);
}

export function cameraNoteFromAmbient(camera: SceneAmbientCaptureResponse["cameras"][number] | undefined) {
  if (!camera) return "等待采集";
  const workspace = camera.workspace_name || "";
  if (workspace) return workspace;
  return camera.message || String(camera.source || "无快照");
}

export function cameraImageClass(index: number, camera: SceneAmbientCaptureResponse["cameras"][number], cam0Rotate180: boolean) {
  const serverRotation = Number(camera.rotation_degrees);
  if (index === 0 && cam0Rotate180 && serverRotation !== 180) return "camera-rotated-180";
  return undefined;
}

export function ambientCameraMetrics(camera: SceneAmbientCaptureResponse["cameras"][number] | undefined) {
  if (!camera) return "等待采集";
  const metrics = camera.analysis?.metrics;
  if (!metrics || typeof metrics !== "object") return camera.workspace_name || camera.message || "无图像指标";
  const width = Number((metrics as Record<string, unknown>).width);
  const height = Number((metrics as Record<string, unknown>).height);
  const brightness = Number((metrics as Record<string, unknown>).brightness);
  const size = Number.isFinite(width) && Number.isFinite(height) ? `${width}x${height}` : "";
  const light = Number.isFinite(brightness) ? `亮度 ${brightness.toFixed(1)}` : "";
  const rotation = Number(camera.rotation_degrees);
  const rotationLabel = Number.isFinite(rotation) && rotation !== 0 ? `旋转 ${rotation}°` : "";
  return [camera.workspace_name, size, light, rotationLabel].filter(Boolean).join(" · ");
}

export function ambientTranscriptStatus(snapshot: SceneAmbientCaptureResponse | null, channel: "left" | "right") {
  if (!snapshot) return "pending";
  const item = findAmbientTranscript(snapshot, channel);
  if (!item) {
    const mono = findAmbientTranscript(snapshot, "mono");
    return mono?.status ?? "unavailable";
  }
  return item.status;
}

export function ambientTranscriptLabel(snapshot: SceneAmbientCaptureResponse | null, channel: "left" | "right") {
  if (!snapshot) return "等待";
  const item = findAmbientTranscript(snapshot, channel);
  if (item) return friendlyStatus(item.status);
  if (findAmbientTranscript(snapshot, "mono")) return "单声道";
  return "不可用";
}

export function ambientAudioLevel(snapshot: SceneAmbientCaptureResponse | null, channel: "left" | "right") {
  if (!snapshot) return "等待录音";
  const item = findAmbientTranscript(snapshot, channel) ?? findAmbientTranscript(snapshot, "mono");
  if (!item) return "没有该声道数据";
  const rms = Number(item.rms);
  const peak = Number(item.peak);
  const parts = [];
  if (Number.isFinite(rms)) parts.push(`RMS ${rms}`);
  if (Number.isFinite(peak)) parts.push(`Peak ${peak}`);
  return parts.length ? parts.join(" · ") : item.message || "已采集";
}

export function findAmbientTranscript(snapshot: SceneAmbientCaptureResponse, channel: "left" | "right" | "mono") {
  return snapshot.transcripts.find((item) => item.channel === channel);
}

export function friendlyAudioChannel(value: string) {
  const labels: Record<string, string> = {
    left: "左声道",
    right: "右声道",
    mono: "单声道",
  };
  return labels[value] ?? value;
}

export function readCameraIndex(snapshot: SceneSensorSnapshotResponse | null) {
  const value = snapshot?.camera?.camera_index;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function readObjectStatus(value: unknown) {
  if (!value || typeof value !== "object") return "unavailable";
  return String((value as Record<string, unknown>).status ?? "unavailable");
}

export function friendlyStatus(status: unknown) {
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

export function motionStatusNote(status: LeLampMotionStatusResponse | null) {
  if (!status) return "等待 LeLamp 串口预检";
  if (!status.serial_detected) return "未检测到 /dev/ttyACM* 或 /dev/ttyUSB*";
  if (!status.pose_readable) return status.message || "串口存在，但姿态不可读";
  if (!status.hardware_enabled) return "姿态可读；需重启启用硬件写入后才能转动";
  return status.message || "姿态可读，可由按钮触发小范围转动";
}

export function poseSummary(status: LeLampMotionStatusResponse | null) {
  if (!status?.pose_readable) return status?.pose_error || status?.message || "等待读取";
  const yaw = Number(status.pose?.base_yaw);
  const pitch = Number(status.pose?.base_pitch);
  const yawText = Number.isFinite(yaw) ? yaw.toFixed(1) : "?";
  const pitchText = Number.isFinite(pitch) ? pitch.toFixed(1) : "?";
  return `base_yaw ${yawText} · base_pitch ${pitchText}`;
}

export function snapshotImageName(snapshot: unknown) {
  if (!snapshot || typeof snapshot !== "object") return "无快照信息";
  const camera = (snapshot as Record<string, unknown>).camera;
  if (!camera || typeof camera !== "object") return "无相机快照";
  const workspace = String((camera as Record<string, unknown>).workspace_name ?? "");
  return workspace || "相机未返回图片";
}

export function cameraNote(snapshot: SceneSensorSnapshotResponse | null) {
  if (!snapshot) return "等待读取设备视角";
  const camera = snapshot.camera ?? {};
  const workspace = String(camera.workspace_name ?? "");
  const source = String(camera.source ?? "");
  const rotation = Number(camera.rotation_degrees);
  const rotationLabel = Number.isFinite(rotation) && rotation !== 0 ? ` · 旋转 ${rotation}°` : "";
  if (workspace) return `${source || "camera"} · ${workspace}${rotationLabel}`;
  return String(camera.message ?? source ?? "未获得相机画面");
}

export function micNote(snapshot: SceneSensorSnapshotResponse | null) {
  if (!snapshot) return "等待短采样";
  const mic = snapshot.microphone ?? {};
  const status = String(mic.status ?? "");
  if (status !== "completed") return String(mic.message ?? friendlyStatus(status));
  return `RMS ${String(mic.rms ?? 0)} · Peak ${String(mic.peak ?? 0)} · ${mic.activity_detected ? "有声音活动" : "未检测到明显声音"}`;
}

export function projectionNote(snapshot: SceneSensorSnapshotResponse | null) {
  if (!snapshot) return "待读取连接状态";
  const projection = snapshot.hardware?.projection;
  if (!projection || typeof projection !== "object") return "未获得投影/显示状态";
  const details = (projection as Record<string, unknown>).details;
  const physical = details && typeof details === "object" ? String((details as Record<string, unknown>).physical_projector ?? "") : "";
  return physical ? `物理输出：${physical}` : friendlyStatus((projection as Record<string, unknown>).status);
}

export function systemSensorNote(snapshot: SceneSensorSnapshotResponse | null) {
  if (!snapshot) return "待读取系统传感器";
  const sensors = snapshot.hardware?.sensors;
  if (!sensors || typeof sensors !== "object") return "无系统传感器数据";
  const data = sensors as Record<string, unknown>;
  const temp = data.cpu_temp !== null && data.cpu_temp !== undefined ? `${String(data.cpu_temp)}°C` : "温度未知";
  const memory = typeof data.memory_usage === "number" ? `${Math.round(data.memory_usage * 100)}% 内存` : "内存未知";
  const disk = typeof data.disk_usage === "number" ? `${Math.round(data.disk_usage * 100)}% 磁盘` : "磁盘未知";
  return `${temp} · ${memory} · ${disk}`;
}

export function speechActivityLabel(snapshot: SceneSensorSnapshotResponse | null) {
  if (!snapshot) return "待检测";
  if (Boolean(snapshot.reading?.speech_active)) return "检测到声音";
  return "未检测到声音";
}

export function triggerSummary(result: SceneWorkflowTriggerResponse | null) {
  if (!result) return "等待用户点击触发建议";
  return result.message || (result.next_url ? "已生成后续操作入口" : "建议已触发");
}

export function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read file."));
    reader.readAsDataURL(file);
  });
}
