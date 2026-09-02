import type { HardwareDevice } from "../api/types";

export const hardwareDevices: HardwareDevice[] = [
  { key: "hardware_enabled", label: "硬件总开关", status: "enabled", note: "硬件模块已加载" },
  { key: "camera_available", label: "摄像头 (Camera)", status: "available", note: "Logitech C920 1280x720 @ 30fps" },
  { key: "mic_available", label: "麦克风 (Mic)", status: "available", note: "USB Mic (Realtek)", metric: "-28 dBFS" },
  { key: "speaker_available", label: "扬声器 (Speaker)", status: "degraded", note: "USB Audio 音量受限", metric: "68%" },
  { key: "projection_available", label: "投影仪 (Projector)", status: "unavailable", note: "HDMI-1 未检测到投影仪或外接输出" },
  { key: "projection_rail", label: "投影舵机", status: "unavailable", note: "请检查 1 号舵机电源" },
  { key: "rgb_available", label: "状态光效 (RGB Cue)", status: "adapter_ready", note: "WS2812B 适配器未就绪" },
];

export const hardwareEvents = [
  { time: "14:31:02", title: "Speaker 音量降级", status: "degraded", detail: "音量限制启用 (上限 0.8)" },
  { time: "14:28:55", title: "Mic 增益自动调整", status: "ok", detail: "增益从 -38dB 调整至 -28dB" },
  { time: "14:26:11", title: "Projection 不可用", status: "unavailable", detail: "未检测到 HDMI 投影仪输出" },
  { time: "14:20:47", title: "RGB 适配器未就绪", status: "adapter_ready", detail: "等待 USB 适配器连接" },
  { time: "14:19:33", title: "硬件模块加载完成", status: "success", detail: "硬件管理服务已启动" },
];
