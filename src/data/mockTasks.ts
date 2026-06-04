import type { TaskItem } from "../api/types";

export const recentTasks: TaskItem[] = [
  { title: "总结刚上传的会议记录", time: "14:31:02", status: "completed" },
  { title: "提取合同关键条款", time: "14:22:41", status: "completed" },
  { title: "生成会议纪要（项目周会）", time: "14:18:07", status: "completed" },
  { title: "翻译报告为英文", time: "14:05:33", status: "running" },
  { title: "生成投影草稿（产品路线图）", time: "13:48:12", status: "error" },
];

export const recentUploads: TaskItem[] = [
  { title: "2024-05-19_周会记录.md", time: "14:28:33", status: "completed" },
  { title: "客户需求文档_v2.pdf", time: "14:21:09", status: "completed" },
  { title: "产品路线图草案.pptx", time: "14:02:51", status: "completed" },
  { title: "合同_样本_2024.docx", time: "13:42:18", status: "completed" },
  { title: "会议照片_20240519_01.jpg", time: "13:34:57", status: "completed" },
];
