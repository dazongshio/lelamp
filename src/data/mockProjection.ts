import type { ProjectionCard } from "../api/types";

export const projectionCards: ProjectionCard[] = [
  {
    id: "confirm-1",
    title: "会议已开始",
    subtitle: "请保持安静，感谢配合",
    mode: "confirmation",
    accent: "green",
    created_at: "14:32:18",
    resolution: "1920 × 1080",
  },
  {
    id: "countdown-1",
    title: "会议开始倒计时",
    subtitle: "05:00",
    mode: "countdown",
    accent: "blue",
    created_at: "14:27:03",
    resolution: "1920 × 1080",
  },
  {
    id: "status-1",
    title: "当前状态",
    subtitle: "会议准备中",
    mode: "status",
    accent: "blue",
    created_at: "14:21:45",
    resolution: "1920 × 1080",
  },
  {
    id: "action-1",
    title: "请将手机调至静音",
    subtitle: "感谢您的配合",
    mode: "action_card",
    accent: "yellow",
    created_at: "14:18:22",
    resolution: "1920 × 1080",
  },
  {
    id: "status-2",
    title: "设备检查完成",
    subtitle: "一切正常",
    mode: "status",
    accent: "green",
    created_at: "14:12:09",
    resolution: "1920 × 1080",
  },
];
