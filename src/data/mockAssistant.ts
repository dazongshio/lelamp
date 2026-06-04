import type { AssistantMessage } from "../api/types";

export const assistantMessages: AssistantMessage[] = [
  {
    id: "m1",
    role: "user",
    text: "把这个文档整理成邮件草稿",
    time: "14:31:02",
    attachment: "产品路线图更新_20240519.md  2.4 MB",
  },
  {
    id: "m2",
    role: "assistant",
    text: "已为你整理邮件草稿，见右侧执行结果。",
    time: "14:31:05",
    status: "completed",
  },
  {
    id: "m3",
    role: "user",
    text: "检查今天的待办",
    time: "14:31:48",
  },
  {
    id: "m4",
    role: "assistant",
    text: "以下是你今天的待办清单，请确认是否需要我为你创建跟进事项。",
    time: "14:31:49",
    status: "completed",
  },
  {
    id: "m5",
    role: "user",
    text: "打开投影确认卡",
    time: "14:32:22",
  },
  {
    id: "m6",
    role: "assistant",
    text: "已在投影上显示确认卡，请确认是否继续执行高风险操作。",
    time: "14:32:23",
    status: "needs_confirmation",
  },
];
