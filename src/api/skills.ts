import { requestWithMock } from "./client";
import type { ApiResult, SkillSpec } from "./types";

const fallbackSkills: SkillSpec[] = [
  {
    name: "read_file",
    mode: "sandbox",
    description: "读取 workspace/shared_inbox/allowed roots 内文件。",
    implemented: true,
    status: "adapter_ready",
    permission_notes: "仅开发 mock；生产默认不启用。",
    requires_confirmation: false,
  },
  {
    name: "projection.card",
    mode: "sandbox",
    description: "生成显示器/投影预览卡。",
    implemented: true,
    status: "adapter_ready",
    permission_notes: "仅开发 mock；生产默认不启用。",
    requires_confirmation: false,
  },
];

export function getSkills(): Promise<ApiResult<{ skills: SkillSpec[] }>> {
  return requestWithMock<{ skills: SkillSpec[] }>("/api/skills", { skills: fallbackSkills });
}
