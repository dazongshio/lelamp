import { getAssistantProvidersStatus, getCameraStreamStatus, getVoiceAssistantStatus, getVoiceRealtimeVoices, getVoiceStatus } from "../api/assistant";
import { getAuditRecent } from "../api/audit";
import { getDocumentAdaptersStatus } from "../api/documents";
import { getHardwareStatus } from "../api/hardware";
import { getMeetingJobs, getMeetingLocalRealtimeStatus, getMeetingProviderStatus, getMeetingRealtimeStatus } from "../api/meeting";
import { getMobileBridgeStatus } from "../api/mobile";
import { getProductChecklist, getTargetValidationStatus } from "../api/product";
import { getProjectionDisplayProfile, getProjectionLatest, getProjectionServiceStatus } from "../api/projection";
import { getRemoteSshStatus } from "../api/remote";
import { getSceneRecent, getSceneWorkflowSuggestions, getLeLampMotionStatus } from "../api/scene";
import { getSecurity, getEnterprisePolicy, getEnterpriseLocalPlatformStatus } from "../api/security";
import { getServicesStatus } from "../api/services";
import { getSharedFiles, getWorkspaceFiles } from "../api/shared";
import { getSkills } from "../api/skills";
import { getSmartHomeStatus } from "../api/smartHome";
import { getBrowserAutomationStatus, getDesktopCompanionStatus, getDesktopTasks, getDesktopWorkflowStatus, getRecentTasks } from "../api/tasks";
import { getDocmostStatus, getWikiPages } from "../api/wiki";

interface PotLoader {
  key: string;
  label: string;
  run: () => Promise<{ data: unknown }>;
}

export const loaders: PotLoader[] = [
  { key: "services", label: "系统服务", run: getServicesStatus },
  { key: "security", label: "权限边界", run: getSecurity },
  { key: "policy", label: "企业策略", run: getEnterprisePolicy },
  { key: "localPlatform", label: "本地平台", run: getEnterpriseLocalPlatformStatus },
  { key: "assistant", label: "AI 助手", run: getAssistantProvidersStatus },
  { key: "voice", label: "语音链路", run: getVoiceStatus },
  { key: "voiceAssistant", label: "语音助手进程", run: getVoiceAssistantStatus },
  { key: "voiceVoices", label: "Qwen 音色", run: getVoiceRealtimeVoices },
  { key: "cameraStream", label: "相机预览", run: getCameraStreamStatus },
  { key: "meetingProvider", label: "会议听写", run: getMeetingProviderStatus },
  { key: "meetingRealtime", label: "实时会议", run: () => getMeetingRealtimeStatus() },
  { key: "meetingLocal", label: "本地会议文本", run: getMeetingLocalRealtimeStatus },
  { key: "meetingJobs", label: "会议任务", run: getMeetingJobs },
  { key: "documentAdapters", label: "文档适配器", run: getDocumentAdaptersStatus },
  { key: "projectionService", label: "投影服务", run: getProjectionServiceStatus },
  { key: "projectionLatest", label: "最近投影", run: getProjectionLatest },
  { key: "projectionProfile", label: "投影校准", run: getProjectionDisplayProfile },
  { key: "sceneRecent", label: "场景事件", run: () => getSceneRecent(12) },
  { key: "sceneSuggestions", label: "场景建议", run: () => getSceneWorkflowSuggestions(12) },
  { key: "lampMotion", label: "台灯位姿", run: getLeLampMotionStatus },
  { key: "hardware", label: "硬件状态", run: getHardwareStatus },
  { key: "mobile", label: "手机桥接", run: getMobileBridgeStatus },
  { key: "smartHome", label: "智能家居", run: getSmartHomeStatus },
  { key: "remote", label: "远程电脑", run: getRemoteSshStatus },
  { key: "desktopTasks", label: "桌面任务队列", run: () => getDesktopTasks(20) },
  { key: "browserAutomation", label: "浏览器自动化", run: getBrowserAutomationStatus },
  { key: "desktopCompanion", label: "桌面伴随进程", run: getDesktopCompanionStatus },
  { key: "desktopWorkflow", label: "桌面工作流", run: getDesktopWorkflowStatus },
  { key: "sharedFiles", label: "共享空间", run: () => getSharedFiles({ page_size: 20 }) },
  { key: "workspaceFiles", label: "工作区文件", run: () => getWorkspaceFiles({ page_size: 20 }) },
  { key: "docmost", label: "Docmost Wiki", run: getDocmostStatus },
  { key: "wikiPages", label: "本地 Wiki", run: getWikiPages },
  { key: "tasks", label: "最近长任务", run: () => getRecentTasks(16) },
  { key: "skills", label: "技能注册表", run: getSkills },
  { key: "checklist", label: "产品清单", run: getProductChecklist },
  { key: "validation", label: "验收验证", run: getTargetValidationStatus },
  { key: "audit", label: "审计日志", run: () => getAuditRecent({ limit: 16 }) },
];

