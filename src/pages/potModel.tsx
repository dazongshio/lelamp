import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { StatLine, StatusPill, type PillTone } from "../components/ProjectorConsole";

export type PotSnapshot = Record<string, unknown>;

interface PotLoader {
  key: string;
  label: string;
  run: () => Promise<{ data: unknown }>;
}

export interface PotFailure {
  key: string;
  label: string;
  message: string;
}

export interface PotModule {
  title: string;
  route: string;
  status: unknown;
  description: string;
  facts: Array<{ label: string; value: ReactNode }>;
  chips?: string[];
}

export interface PotGroup {
  title: string;
  subtitle: string;
  modules: PotModule[];
}


export function PotModuleCard({ item, search }: { item: PotModule; search: string }) {
  const tone = toneForStatus(item.status);
  return (
    <article className="pc-pot-module-card">
      <div className="pc-pot-module-head">
        <div>
          <strong>{item.title}</strong>
          <span>{item.description}</span>
        </div>
        <StatusPill tone={tone}>{friendlyStatus(item.status)}</StatusPill>
      </div>
      <div className="pc-pot-facts">
        {item.facts.map((fact) => (
          <StatLine label={fact.label} value={fact.value} key={fact.label} />
        ))}
      </div>
      {item.chips?.length ? (
        <div className="pc-source-pill-row">
          {item.chips.map((chip) => <span className="pc-chip" key={chip}>{chip}</span>)}
        </div>
      ) : null}
      <Link className="ghost-button" to={{ pathname: item.route, search }}>打开</Link>
    </article>
  );
}

export function buildGroups(snapshot: PotSnapshot): PotGroup[] {
  const assistant = record(snapshot.assistant);
  const qwenOmni = record(assistant.qwen_omni);
  const openclaw = record(assistant.openclaw);
  const voice = record(snapshot.voice);
  const voiceRealtime = record(voice.realtime);
  const voiceAssistant = record(snapshot.voiceAssistant);
  const voiceVoices = record(snapshot.voiceVoices);
  const cameraStream = record(snapshot.cameraStream);
  const meetingProvider = record(snapshot.meetingProvider);
  const tingwu = record(record(meetingProvider.providers).tongyi_tingwu);
  const meetingRealtime = record(snapshot.meetingRealtime);
  const meetingLocal = record(snapshot.meetingLocal);
  const meetingJobs = record(snapshot.meetingJobs);
  const documentAdapters = record(snapshot.documentAdapters);
  const projectionService = record(snapshot.projectionService);
  const projectionLatest = record(snapshot.projectionLatest);
  const projectionProfile = record(snapshot.projectionProfile);
  const sceneRecent = record(snapshot.sceneRecent);
  const sceneSuggestions = record(snapshot.sceneSuggestions);
  const lampMotion = record(snapshot.lampMotion);
  const hardware = record(snapshot.hardware);
  const mobile = record(snapshot.mobile);
  const smartHome = record(snapshot.smartHome);
  const remote = record(snapshot.remote);
  const desktopTasks = record(snapshot.desktopTasks);
  const browserAutomation = record(snapshot.browserAutomation);
  const desktopCompanion = record(snapshot.desktopCompanion);
  const desktopWorkflow = record(snapshot.desktopWorkflow);
  const sharedFiles = record(snapshot.sharedFiles);
  const workspaceFiles = record(snapshot.workspaceFiles);
  const docmost = record(snapshot.docmost);
  const wikiPages = record(snapshot.wikiPages);
  const services = record(snapshot.services);
  const skills = record(snapshot.skills);
  const security = record(snapshot.security);
  const policy = record(snapshot.policy);
  const localPlatform = record(snapshot.localPlatform);
  const checklist = record(snapshot.checklist);
  const validation = record(snapshot.validation);

  return [
    {
      title: "AI 与语音",
      subtitle: "助手、Qwen Omni、音色、语音助手和相机预览",
      modules: [
        {
          title: "AI 助手",
          route: "/assistant",
          status: qwenOmni.status ?? assistant.status,
          description: "文本、树莓派麦克风、Qwen Omni 与 OpenClaw 路由。",
          facts: [
            { label: "foreground", value: String(assistant.foreground_provider ?? "--") },
            { label: "qwen model", value: String(qwenOmni.model ?? "--") },
            { label: "openclaw", value: friendlyStatus(openclaw.status) },
          ],
          chips: ["chat", "local commands", "tool routing"],
        },
        {
          title: "语音助手",
          route: "/voice",
          status: voiceAssistant.status ?? voice.status,
          description: "开启/关闭语音助手、单次听写、连续对话和本地台灯语音指令。",
          facts: [
            { label: "process", value: boolText(voiceAssistant.running) },
            { label: "voice", value: String(voiceAssistant.voice ?? voiceRealtime.voice ?? "--") },
            { label: "mic", value: friendlyStatus(record(voice.mic).status) },
          ],
          chips: ["wake word", "ASR", "TTS"],
        },
        {
          title: "Qwen 音色",
          route: "/voice",
          status: voiceVoices.status ?? voiceRealtime.status,
          description: "所有 Qwen 实时音色集中选择，前端只在语音页执行切换。",
          facts: [
            { label: "current", value: String(voiceVoices.voice ?? voiceRealtime.voice ?? "--") },
            { label: "count", value: numberText(voiceVoices.voice_count ?? arrayFrom(voiceVoices.voices).length) },
            { label: "model", value: String(voiceVoices.model ?? voiceRealtime.model ?? "--") },
          ],
          chips: ["voice picker", "realtime"],
        },
        {
          title: "相机实时预览",
          route: "/scene",
          status: cameraStream.status,
          description: "相机视频流、照片快照和 cam0 旋转配置的入口。",
          facts: [
            { label: "camera", value: numberText(cameraStream.camera_index) },
            { label: "always on", value: boolText(cameraStream.always_on) },
            { label: "preview", value: cameraStream.preview_url ? "available" : "--" },
          ],
          chips: ["camera", "snapshot", "stream"],
        },
      ],
    },
    {
      title: "会议与文档",
      subtitle: "听悟、实时会议、文件适配器、共享空间和工作区文件",
      modules: [
        {
          title: "听悟会议",
          route: "/meeting",
          status: tingwu.status ?? meetingProvider.status,
          description: "通义听悟实时听写、说话人、纪要、待办、决策和后处理。",
          facts: [
            { label: "configured", value: boolText(tingwu.configured) },
            { label: "active", value: String(tingwu.active_meeting_id ?? "--") },
            { label: "mic", value: friendlyStatus(tingwu.mic_status) },
          ],
          chips: ["minutes", "actions", "decisions"],
        },
        {
          title: "实时会议状态",
          route: "/meeting",
          status: meetingRealtime.status ?? meetingLocal.status,
          description: "在线会议会话、本地转写缓存和导出后的文件路径。",
          facts: [
            { label: "meeting id", value: String(meetingRealtime.meeting_id ?? "--") },
            { label: "final lines", value: numberText(meetingRealtime.final_count ?? arrayFrom(meetingRealtime.transcript).length) },
            { label: "local turns", value: numberText(meetingLocal.turn_count) },
          ],
          chips: ["realtime", "transcript"],
        },
        {
          title: "会议任务",
          route: "/results",
          status: countStatus(arrayFrom(meetingJobs.items).length),
          description: "会议纪要、待办、邮件草稿和投影确认等后处理结果。",
          facts: [
            { label: "jobs", value: numberText(meetingJobs.total ?? arrayFrom(meetingJobs.items).length) },
            { label: "latest", value: latestTitle(arrayFrom(meetingJobs.items), "title") },
            { label: "result center", value: "available" },
          ],
          chips: ["post-processing", "artifacts"],
        },
        {
          title: "文档适配器",
          route: "/documents",
          status: adaptersStatus(documentAdapters),
          description: "文档分析、摘要、风险、表格抽取、汇报提纲和扫描增强。",
          facts: [
            { label: "adapters", value: objectSize(record(documentAdapters.adapters)) },
            { label: "scan", value: "PDF / OCR / enhance" },
            { label: "workspace", value: "shared files" },
          ],
          chips: ["OCR", "tables", "outline"],
        },
        {
          title: "共享空间",
          route: "/shared",
          status: countStatus(arrayFrom(sharedFiles.files ?? sharedFiles.items).length),
          description: "树莓派共享空间文件入口，沙箱模式下 AI 默认只处理这里。",
          facts: [
            { label: "files", value: numberText(sharedFiles.total ?? arrayFrom(sharedFiles.files ?? sharedFiles.items).length) },
            { label: "root", value: compactPath(String(sharedFiles.shared_inbox ?? "--")) },
            { label: "mode", value: "sandbox source" },
          ],
          chips: ["inbox", "upload", "preview"],
        },
        {
          title: "工作区归档",
          route: "/documents",
          status: countStatus(arrayFrom(workspaceFiles.files ?? workspaceFiles.items).length),
          description: "扫描、会议、文档、投影输出和历史结果的归档文件。",
          facts: [
            { label: "files", value: numberText(workspaceFiles.total ?? arrayFrom(workspaceFiles.files ?? workspaceFiles.items).length) },
            { label: "viewer", value: "md / pdf / office" },
            { label: "结果", value: "结果中心" },
          ],
          chips: ["archive", "preview"],
        },
      ],
    },
    {
      title: "投影、场景与台灯",
      subtitle: "投影服务、校准、双摄双麦、位姿和硬件检查",
      modules: [
        {
          title: "投影服务",
          route: "/projection",
          status: projectionService.status,
          description: "外接显示器/投影仪输出、Markdown 投影、PPT 播放和换页。",
          facts: [
            { label: "projector", value: friendlyStatus(projectionService.physical_projector) },
            { label: "target", value: String(projectionService.output_target ?? "--") },
            { label: "preview", value: projectionService.preview_url ? "available" : "--" },
          ],
          chips: ["markdown", "ppt", "display"],
        },
        {
          title: "投影校准",
          route: "/projection",
          status: projectionProfile.status,
          description: "亮度、对比度、缩放、梯形校正和校准图片分析。",
          facts: [
            { label: "mode", value: String(record(projectionProfile.profile).mode ?? "--") },
            { label: "keystone", value: `${String(record(projectionProfile.profile).keystone_x ?? 0)}, ${String(record(projectionProfile.profile).keystone_y ?? 0)}` },
            { label: "latest", value: compactPath(String(projectionLatest.name ?? projectionLatest.path ?? "--")) },
          ],
          chips: ["keystone", "brightness"],
        },
        {
          title: "场景感知",
          route: "/scene",
          status: sceneRecent.status ?? sceneSuggestions.status,
          description: "双摄、左右麦克风、环境事件和类似微信的听写展示入口。",
          facts: [
            { label: "events", value: numberText(sceneRecent.total ?? arrayFrom(sceneRecent.events).length) },
            { label: "suggestions", value: numberText(sceneSuggestions.total ?? arrayFrom(sceneSuggestions.suggestions).length) },
            { label: "source", value: String(sceneSuggestions.source ?? "--") },
          ],
          chips: ["cam0", "cam1", "left/right mic"],
        },
        {
          title: "台灯位姿",
          route: "/motors",
          status: lampMotion.status ?? lampMotion.pose_status,
          description: "五轴当前位置、默认/扫描/投影位姿和步长控制。",
          facts: [
            { label: "hardware", value: boolText(lampMotion.hardware_enabled) },
            { label: "serial", value: boolText(lampMotion.serial_detected) },
            { label: "pose", value: friendlyStatus(lampMotion.pose_status ?? lampMotion.status) },
          ],
          chips: ["default pose", "scan pose", "projection pose"],
        },
        {
          title: "硬件检查",
          route: "/hardware",
          status: hardware.hardware_enabled === false ? "needs_hardware" : hardware.scan ? record(hardware.scan).status : "ok",
          description: "相机、麦克风、扬声器、投影、RGB、传感器和系统电源状态。",
          facts: [
            { label: "enabled", value: boolText(hardware.hardware_enabled) },
            { label: "devices", value: objectSize(record(hardware.devices)) },
            { label: "power", value: friendlyStatus(record(hardware.sensors).power_state) },
          ],
          chips: ["camera", "mic", "speaker", "rgb"],
        },
      ],
    },
    {
      title: "电脑、移动端与外部设备",
      subtitle: "LAN SSH、远程 Codex、桌面自动化、手机桥接和智能家居",
      modules: [
        {
          title: "远程电脑 SSH",
          route: "/remote",
          status: remote.status,
          description: "通过局域网 SSH 连接另一台电脑，安装/打开 Codex 并接收语音控制。",
          facts: [
            { label: "saved host", value: String(record(remote.saved_target).host ?? "--") },
            { label: "LAN URL", value: String(remote.console_lan_url ?? "--") },
            { label: "ssh", value: String(remote.ssh_binary ?? "--") },
          ],
          chips: ["full control", "codex", "voice control"],
        },
        {
          title: "桌面工作流",
          route: "/desktop",
          status: desktopWorkflow.status,
          description: "计划、设置和执行受控桌面任务，默认受权限边界约束。",
          facts: [
            { label: "can execute", value: boolText(desktopWorkflow.can_execute) },
            { label: "backend", value: String(desktopWorkflow.desktop_backend ?? "--") },
            { label: "actions", value: numberText(arrayFrom(desktopWorkflow.supported_actions).length) },
          ],
          chips: ["workflow", "permission"],
        },
        {
          title: "桌面伴随进程",
          route: "/desktop",
          status: desktopCompanion.status,
          description: "处理桌面任务队列，支持轮询执行与单次执行。",
          facts: [
            { label: "backend", value: String(desktopCompanion.backend ?? "--") },
            { label: "interval", value: String(desktopCompanion.interval_seconds ?? "--") },
            { label: "queue", value: compactPath(String(desktopCompanion.queue_dir ?? "--")) },
          ],
          chips: ["queue", "companion"],
        },
        {
          title: "浏览器自动化",
          route: "/desktop",
          status: browserAutomation.status,
          description: "浏览器任务、网页打开、截图和受控自动化验证。",
          facts: [
            { label: "installed", value: boolText(browserAutomation.package_installed) },
            { label: "headless", value: boolText(browserAutomation.headless_default) },
            { label: "max steps", value: numberText(browserAutomation.max_steps) },
          ],
          chips: ["browser", "screenshots"],
        },
        {
          title: "桌面任务队列",
          route: "/desktop",
          status: countStatus(arrayFrom(desktopTasks.tasks).length),
          description: "从 AI 助手或前端排入的桌面任务。",
          facts: [
            { label: "tasks", value: numberText(arrayFrom(desktopTasks.tasks).length) },
            { label: "queue", value: compactPath(String(desktopTasks.queue_dir ?? "--")) },
            { label: "source", value: "desktop" },
          ],
          chips: ["task queue"],
        },
        {
          title: "手机桥接",
          route: "/mobile",
          status: mobile.status,
          description: "手机端请求、签名、共享密钥和移动设备能力。",
          facts: [
            { label: "configured", value: boolText(mobile.configured) },
            { label: "device", value: String(mobile.device_id ?? "--") },
            { label: "capabilities", value: numberText(arrayFrom(mobile.capabilities).length) },
          ],
          chips: ["mobile", "bridge"],
        },
        {
          title: "智能家居",
          route: "/smart-home",
          status: smartHome.status,
          description: "Home Assistant、Webhook 和已知实体控制。",
          facts: [
            { label: "configured", value: boolText(smartHome.configured) },
            { label: "provider", value: String(smartHome.provider ?? "--") },
            { label: "entities", value: numberText(arrayFrom(smartHome.known_entities).length) },
          ],
          chips: ["home assistant", "webhook"],
        },
      ],
    },
    {
      title: "Wiki、治理与验收",
      subtitle: "Docmost、本地 Wiki、技能、服务、审计、产品清单和安全设置",
      modules: [
        {
          title: "Docmost Wiki",
          route: "/wiki",
          status: docmost.status,
          description: "自建协作文档空间，接收会议纪要、扫描 PDF 和文档结果同步。",
          facts: [
            { label: "configured", value: boolText(docmost.configured) },
            { label: "space", value: String(docmost.default_space ?? "--") },
            { label: "url", value: String(docmost.url ?? "--") },
          ],
          chips: ["wiki", "docmost"],
        },
        {
          title: "本地 Wiki",
          route: "/wiki",
          status: wikiPages.status,
          description: "无需外部服务的 Markdown 页面管理和编辑。",
          facts: [
            { label: "pages", value: numberText(arrayFrom(wikiPages.pages).length) },
            { label: "root", value: compactPath(String(wikiPages.root ?? "--")) },
            { label: "workspace", value: compactPath(String(wikiPages.workspace_root ?? "--")) },
          ],
          chips: ["md", "local"],
        },
        {
          title: "技能注册表",
          route: "/assistant",
          status: countStatus(arrayFrom(skills.skills).length),
          description: "AI 助手可用技能、权限要求、输入输出契约和 fallback 行为。",
          facts: [
            { label: "skills", value: numberText(arrayFrom(skills.skills).length) },
            { label: "requires confirm", value: numberText(arrayFrom(skills.skills).filter((item) => Boolean(record(item).requires_confirmation)).length) },
            { label: "mode", value: "sandbox / full control" },
          ],
          chips: ["skills", "contracts"],
        },
        {
          title: "系统服务",
          route: "/settings",
          status: aggregateServiceStatus(arrayFrom(services.services)),
          description: "Web Console、AI 服务、投影、会议、硬件等后台服务状态。",
          facts: [
            { label: "services", value: numberText(arrayFrom(services.services).length) },
            { label: "ready", value: numberText(arrayFrom(services.services).filter((item) => toneForStatus(record(item).status) === "ok").length) },
            { label: "attention", value: numberText(arrayFrom(services.services).filter((item) => toneForStatus(record(item).status) !== "ok").length) },
          ],
          chips: ["status", "ops"],
        },
        {
          title: "安全策略",
          route: "/settings",
          status: policy.status ?? security.permission_mode,
          description: "沙箱控制、全权控制、允许根目录、审计签名和本地平台策略。",
          facts: [
            { label: "permission", value: String(security.permission_mode ?? policy.permission_mode ?? "--") },
            { label: "full control", value: boolText(security.full_control_enabled) },
            { label: "cloud ai", value: boolText(security.cloud_ai_enabled ?? policy.cloud_ai_enabled) },
          ],
          chips: ["policy", "audit", "boundaries"],
        },
        {
          title: "本地企业平台",
          route: "/settings",
          status: localPlatform.status,
          description: "离线模型、数据分区、平台 bundle 和企业版本地部署清单。",
          facts: [
            { label: "services", value: numberText(arrayFrom(localPlatform.services).length) },
            { label: "zones", value: numberText(arrayFrom(localPlatform.data_zones).length) },
            { label: "bundle", value: compactPath(String(localPlatform.latest_bundle ?? "--")) },
          ],
          chips: ["offline", "enterprise"],
        },
        {
          title: "产品清单",
          route: "/checklist",
          status: record(checklist.summary).software_mvp_ready ? "ok" : "degraded",
          description: "产品化清单、剩余缺口、部署说明和就绪状态。",
          facts: [
            { label: "total", value: numberText(record(checklist.summary).total) },
            { label: "remaining", value: numberText(record(checklist.summary).remaining_count) },
            { label: "ready", value: boolText(record(checklist.summary).software_mvp_ready) },
          ],
          chips: ["mvp", "readiness"],
        },
        {
          title: "验收验证",
          route: "/validation",
          status: validation.status,
          description: "目标功能验收、证据、缺口和可运行验证项。",
          facts: [
            { label: "completed", value: numberText(record(validation.summary).completed) },
            { label: "blocked", value: numberText(record(validation.summary).blocked) },
            { label: "items", value: numberText(arrayFrom(validation.items).length) },
          ],
          chips: ["acceptance", "qa"],
        },
      ],
    },
  ];
}

export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function arrayFrom(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function objectSize(value: Record<string, unknown>) {
  return Object.keys(value).length || "--";
}

function numberText(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : String(value ?? "--");
}

function boolText(value: unknown) {
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value === undefined || value === null || value === "") return "--";
  return String(value);
}

export function friendlyStatus(status: unknown) {
  const value = String(status ?? "pending");
  const labels: Record<string, string> = {
    ok: "就绪",
    online: "在线",
    available: "可用",
    enabled: "已启用",
    success: "成功",
    completed: "已完成",
    running: "运行中",
    starting: "启动中",
    stopped: "已停止",
    idle: "空闲",
    pending: "等待执行",
    optional: "可选",
    adapter_ready: "适配器就绪",
    degraded: "需要处理",
    warning: "警告",
    needs_config: "需要配置",
    needs_backend: "需要后端",
    backend_missing: "后端缺失",
    needs_hardware: "需要硬件",
    unavailable: "不可用",
    blocked: "已阻止",
    failed: "失败",
    error: "错误",
    task: "任务",
  };
  return labels[value] ?? value.replace(/[_-]+/g, " ");
}

export function toneForStatus(status: unknown): PillTone {
  const value = String(status ?? "pending");
  if (["ok", "online", "available", "enabled", "success", "completed", "adapter_ready", "ready"].includes(value)) return "ok";
  if (["blocked", "failed", "error", "unavailable", "needs_hardware", "unsupported"].includes(value)) return "blocked";
  if (["warning", "degraded", "needs_config", "needs_backend", "backend_missing", "pending", "starting", "stopping", "waiting_confirmation", "needs_confirmation"].includes(value)) return "warn";
  return "neutral";
}

function countStatus(count: number) {
  return count > 0 ? "ok" : "pending";
}

function aggregateServiceStatus(items: unknown[]) {
  if (!items.length) return "pending";
  if (items.some((item) => toneForStatus(record(item).status) === "blocked")) return "blocked";
  if (items.some((item) => toneForStatus(record(item).status) === "warn")) return "degraded";
  return "ok";
}

function adaptersStatus(adapters: Record<string, unknown>) {
  const adapterValues = Object.values(record(adapters.adapters));
  if (!adapterValues.length) return "pending";
  if (adapterValues.some((status) => toneForStatus(status) === "blocked")) return "blocked";
  if (adapterValues.some((status) => toneForStatus(status) === "warn")) return "degraded";
  return "ok";
}

function latestTitle(items: unknown[], field: string) {
  const first = record(items[0]);
  return String(first[field] ?? first.name ?? first.id ?? "--");
}

function compactPath(value: string) {
  if (!value || value === "--") return "--";
  const parts = value.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.length <= 3 ? value : `.../${parts.slice(-3).join("/")}`;
}
