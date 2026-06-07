import { Activity, BookOpen, CalendarCheck, CheckCircle2, ClipboardList, ExternalLink, FileStack, FileText, Mail, Mic, PlayCircle, Radio, Settings2, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiClientError, apiErrorMessage } from "../api/client";
import {
  appendMeetingLocalRealtimeTurn,
  disableMeetingMode,
  enableMeetingMode,
  exportMeetingLocalRealtimeTranscript,
  exportMeetingPackage,
  fetchMeetingRealtimeMinutes,
  getMeetingJobs,
  getMeetingLocalRealtimeStatus,
  getMeetingProviderStatus,
  getMeetingRealtimeEvents,
  getMeetingRealtimeStatus,
  getMeetingStatus,
  importMeetingText,
  importTranscript,
  runMeetingProviderPreflight,
  runMeetingFollowup,
  runMeetingMinutes,
  runMeetingStep,
  sendMeetingEmail,
  startMeetingRealtime,
  stopMeetingRealtime,
} from "../api/meeting";
import { getSharedFiles, getWorkspacePreview } from "../api/shared";
import { getTask, getTaskEvents } from "../api/tasks";
import { syncWorkspaceFileToWiki } from "../api/wiki";
import type { DocmostSyncResponse, MeetingJob, MeetingLocalRealtimeResponse, MeetingModeStatus, MeetingProviderAcceptanceItem, MeetingProviderPreflight, MeetingProviderStatus, MeetingRealtimeStatus, MeetingStep, SharedFile, SharedPreviewResponse, TaskRecord } from "../api/types";
import { Card } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { WorkspaceFileViewer } from "../components/WorkspaceFileViewer";
import "./pages.css";

const meetingStepMeta = [
  { name: "realtime_capture", title: "实时会议采集", action: "采集", description: "树莓派麦克风采集并推送到通义听悟实时转写" },
  { name: "import_transcript", title: "导入转写", action: "导入", description: "把会议转写导入为会议作业" },
  { name: "minutes", title: "生成会议纪要", action: "纪要", description: "生成结构化会议摘要与输出文件" },
  { name: "decisions", title: "提取决策", action: "决策", description: "从纪要或转写中抽取决策" },
  { name: "action_items", title: "提取行动项", action: "行动项", description: "抽取负责人、事项和后续状态" },
  { name: "followup", title: "生成会后邮件", action: "邮件", description: "生成会后跟进邮件草稿，不自动发送" },
  { name: "reminders", title: "创建提醒", action: "提醒", description: "创建本地提醒草稿，不自动同步外部日历" },
  { name: "projection_confirmation", title: "投影预览", action: "投影", description: "生成显示器/投影预览页" },
];

const realtimeActiveStatuses = ["starting", "running", "stopping", "finalizing"];

const meetingInsightTabs = [
  { id: "minutes", label: "纪要" },
  { id: "actions", label: "待办" },
  { id: "decisions", label: "决策" },
  { id: "qa", label: "问答" },
  { id: "ppt", label: "PPT" },
  { id: "mindmap", label: "思维导图" },
] as const;

type MeetingInsightTab = typeof meetingInsightTabs[number]["id"];

export function MeetingPage() {
  const [message, setMessage] = useState("等待用户操作");
  const [files, setFiles] = useState<SharedFile[]>([]);
  const [transcript, setTranscript] = useState("");
  const [jobs, setJobs] = useState<MeetingJob[]>([]);
  const [activeJob, setActiveJob] = useState<MeetingJob | null>(null);
  const [lastResult, setLastResult] = useState<Record<string, unknown> | null>(null);
  const [activeStepId, setActiveStepId] = useState(1);
  const [error, setError] = useState("");
  const [providerStatus, setProviderStatus] = useState<MeetingProviderStatus | null>(null);
  const [meetingMode, setMeetingMode] = useState<MeetingModeStatus | null>(null);
  const [localRealtime, setLocalRealtime] = useState<MeetingLocalRealtimeResponse | null>(null);
  const [realtime, setRealtime] = useState<MeetingRealtimeStatus | null>(null);
  const [realtimeEvents, setRealtimeEvents] = useState<Array<Record<string, unknown>>>([]);
  const [realtimeTask, setRealtimeTask] = useState<TaskRecord | null>(null);
  const [realtimeTaskEvents, setRealtimeTaskEvents] = useState<Array<Record<string, unknown>>>([]);
  const [providerPreflight, setProviderPreflight] = useState<MeetingProviderPreflight | null>(null);
  const [providerPreflightBusy, setProviderPreflightBusy] = useState(false);
  const [meetingTitle, setMeetingTitle] = useState(`LeLamp 实时会议 ${new Date().toISOString().slice(0, 10)}`);
  const [participants, setParticipants] = useState("Unknown");
  const [localSpeaker, setLocalSpeaker] = useState("Speaker 1");
  const [localTurnText, setLocalTurnText] = useState("确认：今天先完成 LeLamp 外接显示器测试。");
  const [realtimeBusy, setRealtimeBusy] = useState(false);
  const [localRealtimeBusy, setLocalRealtimeBusy] = useState(false);
  const [registeringTerminalOutputs, setRegisteringTerminalOutputs] = useState(false);
  const [pendingStopMeetingId, setPendingStopMeetingId] = useState("");
  const [meetingText, setMeetingText] = useState("Alice: 决定: 本周先完成 LeLamp 显示器测试。\nBob: 待办: 整理会议记录并生成纪要。");
  const [meetingTextImporting, setMeetingTextImporting] = useState(false);
  const [emailRecipient, setEmailRecipient] = useState("待填写收件人");
  const [emailAuthorized, setEmailAuthorized] = useState(false);
  const [exportAuthorized, setExportAuthorized] = useState(false);
  const [activeInsightTab, setActiveInsightTab] = useState<MeetingInsightTab>("minutes");
  const [selectedArtifact, setSelectedArtifact] = useState<MeetingArtifact | null>(null);
  const [artifactPreview, setArtifactPreview] = useState<SharedPreviewResponse | null>(null);
  const [artifactPreviewBusy, setArtifactPreviewBusy] = useState(false);
  const [artifactPreviewError, setArtifactPreviewError] = useState("");
  const [wikiBusyPath, setWikiBusyPath] = useState("");
  const [wikiResult, setWikiResult] = useState<DocmostSyncResponse | null>(null);

  const load = useCallback(async (preferredTranscript?: string, preferredMeetingId?: string) => {
    setError("");
    try {
      const [filesResult, jobsResult, providerResult] = await Promise.all([
        getSharedFiles({ page_size: 100 }),
        getMeetingJobs(),
        getMeetingProviderStatus(),
      ]);
      const meetingModeResult = await getMeetingStatus();
      const localRealtimeResult = await getMeetingLocalRealtimeStatus();
      const sharedFiles = filesResult.data.files ?? [];
      const nextTranscriptFiles = sharedFiles.filter(isTranscriptLike);
      const preferredTranscriptUsable = preferredTranscript && isTranscriptPathLike(preferredTranscript) ? preferredTranscript : "";
      const currentTranscriptUsable = transcript && isTranscriptPathLike(transcript) ? transcript : "";
      const nextTranscript = preferredTranscriptUsable || currentTranscriptUsable || nextTranscriptFiles[0]?.relative_path || "";
      const nextJobs = jobsResult.data.items;
      const providerMeetingId = providerResult.data.providers.tongyi_tingwu.active_meeting_id ?? undefined;
      const nextActiveJob = findJobForTranscript(nextJobs, nextTranscript, preferredMeetingId ?? providerMeetingId) ?? nextJobs[0] ?? null;
      const selectedMeetingId = preferredMeetingId ?? nextActiveJob?.meeting_id ?? providerMeetingId;
      const nextRealtime = await loadRealtimeStatus(selectedMeetingId);
      const nextMeetingId = nextRealtime.meeting_id ?? selectedMeetingId;
      const nextEvents = await loadRealtimeEvents(nextMeetingId);
      const nextTaskId = realtimeTaskId(nextRealtime, nextActiveJob);
      const nextTaskMonitor = await loadRealtimeTaskMonitor(nextTaskId);
      setFiles(sharedFiles);
      setTranscript(nextTranscript);
      setJobs(nextJobs);
      setActiveJob(nextActiveJob);
      setProviderStatus(providerResult.data);
      setMeetingMode(meetingModeResult.data);
      setLocalRealtime(localRealtimeResult.data);
      setRealtime(nextRealtime);
      setRealtimeEvents(mergeRealtimeEvents(nextEvents));
      setRealtimeTask(nextTaskMonitor.task);
      setRealtimeTaskEvents(mergeRealtimeEvents(nextTaskMonitor.events));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, [transcript]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const meetingId = realtime?.meeting_id ?? providerStatus?.providers.tongyi_tingwu.active_meeting_id;
    const realtimeStatus = String(realtime?.status ?? "");
    const shouldPoll =
      realtimeActiveStatuses.includes(realtimeStatus)
      || (realtimeStatus === "stopped" && pendingStopMeetingId === meetingId);
    if (!meetingId || !shouldPoll) return;
    const timer = window.setInterval(async () => {
      try {
        const [statusResult, eventsResult] = await Promise.all([getMeetingRealtimeStatus(meetingId), getMeetingRealtimeEvents(meetingId)]);
        setRealtime(statusResult.data);
        setRealtimeEvents((items) => mergeRealtimeEvents(eventsResult.data.events, items));
        const taskId = realtimeTaskId(statusResult.data, activeJob);
        if (taskId) {
          const taskMonitor = await loadRealtimeTaskMonitor(taskId);
          setRealtimeTask(taskMonitor.task);
          setRealtimeTaskEvents((items) => mergeRealtimeEvents(taskMonitor.events, items));
        }
        const terminalStatus = String(statusResult.data.status ?? "");
        const shouldRegisterTerminalOutputs =
          terminalStatus === "failed" || (terminalStatus === "stopped" && pendingStopMeetingId === meetingId);
        if (shouldRegisterTerminalOutputs) {
          if (registeringTerminalOutputs) return;
          setRegisteringTerminalOutputs(true);
          try {
            const response = await stopMeetingRealtime(meetingId, false);
            const nextRealtime = realtimeResponseSession(response.data);
            const session = response.data.session as { transcript_path?: string } | undefined;
            const nextTranscript = session?.transcript_path ?? response.data.transcript_path ?? statusResult.data.transcript_path ?? transcript;
            setRealtime(nextRealtime);
            setLastResult(response.data as unknown as Record<string, unknown>);
            setTranscript(nextTranscript);
            setMessage(realtimeResultMessage(response.data as unknown as Record<string, unknown>, "stop"));
            await load(nextTranscript, nextRealtime.meeting_id ?? meetingId);
            if (terminalStatus === "stopped") setPendingStopMeetingId("");
          } finally {
            setRegisteringTerminalOutputs(false);
          }
          return;
        }
        if (terminalStatus === "completed") {
          await load(statusResult.data.transcript_path, statusResult.data.meeting_id ?? undefined);
        }
      } catch (err) {
        setError(apiErrorMessage(err));
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [activeJob, load, pendingStopMeetingId, providerStatus, realtime?.meeting_id, realtime?.status, registeringTerminalOutputs, transcript]);

  const workflowSteps = useMemo(() => toWorkflowSteps(activeJob), [activeJob]);
  const activeStep = workflowSteps.find((step) => step.id === activeStepId) ?? workflowSteps[0];
  const persistedResult = useMemo(() => meetingJobResult(activeJob), [activeJob]);
  const displayResult = lastResult ?? persistedResult;
  const resultSummary = meetingResultSummary(displayResult);
  const decisions = meetingResultItems(displayResult, "decisions");
  const actionItems = meetingResultItems(displayResult, "action_items");
  const outputs = outputPaths(displayResult);
  const artifacts = meetingArtifacts(displayResult, activeJob, realtime);
  const diagnostics = realtimeDiagnostics(displayResult, realtime);
  const agentEvents = tingwuAgentEvents(displayResult, realtime);
  const realtimeStatus = String(realtime?.status ?? "idle");
  const realtimeMeetingId = realtime?.meeting_id ?? providerStatus?.providers.tongyi_tingwu.active_meeting_id;
  const realtimeControlsBusy = realtimeBusy || registeringTerminalOutputs;
  const realtimeActive = realtimeActiveStatuses.includes(realtimeStatus);
  const canStopRealtime = ["starting", "running", "stopping"].includes(realtimeStatus);
  const canRegisterRealtimeOutputs = Boolean(realtimeMeetingId) && ["stopped", "failed"].includes(realtimeStatus);
  const stopButtonLabel = canRegisterRealtimeOutputs ? "登记会议输出" : "停止实时会议";
  const preflightChecks = providerPreflightChecks(providerPreflight);
  const transcriptLines = realtimeTranscriptLines(realtime);
  const minutesReady = Boolean(resultSummary || diagnostics.tingwuMinutesPath || diagnostics.openclawMinutesPath);
  const tingwuCapabilities = providerStatus?.providers.tongyi_tingwu.capabilities ?? {};
  const enabledCapabilityCount = Object.values(tingwuCapabilities).filter(Boolean).length;
  const providerReady = providerStatus?.providers.tongyi_tingwu.status ?? "needs_config";
  const localSpeakerCounts = cleanSpeakerCounts(localRealtime?.speaker_counts);
  const transcriptFiles = useMemo(() => files.filter(isTranscriptLike), [files]);
  const canUseSelectedTranscript = Boolean(transcript && isTranscriptPathLike(transcript));

  async function run(label: string, stepId: number, action: () => Promise<{ data: Record<string, unknown> | MeetingJob }>) {
    if (!canUseSelectedTranscript) {
      setError("请选择可读的会议转写文件，或在中间输入会议文本后导入。");
      return;
    }
    setError("");
    setActiveStepId(stepId);
    setMessage(`${label} 执行中...`);
    try {
      const response = await action();
      setLastResult(response.data as Record<string, unknown>);
      setMessage(`${label} 状态：${String((response.data as Record<string, unknown>).status ?? "completed")}`);
      await load(transcript);
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage(`${label} 失败`);
    }
  }

  async function importMeetingTextContent() {
    const text = meetingText.trim();
    if (!text) {
      setError("请先粘贴会议转写文本或会议内容。");
      return;
    }
    setMeetingTextImporting(true);
    setError("");
    setMessage("会议文本导入中...");
    try {
      const participantsList = participants.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
      const response = await importMeetingText(text, `meeting_text_${new Date().toISOString().slice(0, 10)}`, participantsList);
      setLastResult(response.data as unknown as Record<string, unknown>);
      setTranscript(response.data.file.relative_path);
      setActiveJob(response.data.job);
      setActiveStepId(1);
      setMessage("会议文本已保存到文件工作区，并创建会议作业。");
      await load(response.data.file.relative_path);
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("会议文本导入失败");
    } finally {
      setMeetingTextImporting(false);
    }
  }

  async function setMeetingModeEnabled(enabled: boolean) {
    setError("");
    setMessage(enabled ? "开启会议模式中..." : "关闭会议模式中...");
    try {
      const participantsList = participants.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
      const response = enabled
        ? await enableMeetingMode(meetingTitle, participantsList)
        : await disableMeetingMode();
      setMeetingMode(response.data);
      setMessage(enabled ? "会议模式已开启。用户明确授权后，才处理会议理解内容。" : "会议模式已关闭。默认不解析投影内容。");
      await load(transcript);
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage(enabled ? "开启会议模式失败" : "关闭会议模式失败");
    }
  }

  async function appendLocalRealtimeTurn() {
    if (!localTurnText.trim()) {
      setError("请先输入本地实时 turn。");
      return;
    }
    setLocalRealtimeBusy(true);
    setError("");
    try {
      const response = await appendMeetingLocalRealtimeTurn({
        speaker: localSpeaker,
        text: localTurnText,
        source: "web_manual_realtime_turn",
      });
      setLocalRealtime(response.data);
      setMeetingMode((current) => current ? { ...current, turn_count: response.data.turn_count } : current);
      setMessage(`已追加 ${response.data.turn_count} 条本地实时转写。`);
      await load(transcript);
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("追加本地实时 turn 失败");
    } finally {
      setLocalRealtimeBusy(false);
    }
  }

  async function exportLocalRealtime() {
    setLocalRealtimeBusy(true);
    setError("");
    try {
      const response = await exportMeetingLocalRealtimeTranscript();
      setLocalRealtime(response.data);
      setTranscript(response.data.workspace_name ?? transcript);
      setMessage(`本地实时转写已导出：${compactDisplayPath(response.data.workspace_name ?? response.data.transcript_path)}`);
      await load(response.data.workspace_name ?? transcript);
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("导出本地实时 transcript 失败");
    } finally {
      setLocalRealtimeBusy(false);
    }
  }

  async function syncArtifactToWiki(artifact: MeetingArtifact | null) {
    if (!artifact?.workspaceName) {
      setError("请选择一个会议产物。");
      return;
    }
    setError("");
    setWikiBusyPath(artifact.workspaceName);
    setMessage("正在同步会议产物到 Wiki...");
    try {
      const response = await syncWorkspaceFileToWiki({
        filePath: artifact.workspaceName,
        title: artifact.label.replace(/\s*·\s*.*/, "") || artifact.workspaceName.split("/").pop() || "会议产物",
      });
      setWikiResult(response.data);
      setMessage(`会议产物已同步到 Wiki：${response.data.docmost_page_title}`);
      if (response.data.docmost_page_url) {
        window.open(response.data.docmost_page_url, "_blank", "noopener,noreferrer");
      }
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("同步会议产物到 Wiki 失败");
    } finally {
      setWikiBusyPath("");
    }
  }

  async function startRealtime() {
    setRealtimeBusy(true);
    setError("");
    setMessage("通义听悟实时会议启动中...");
    setActiveStepId(1);
    try {
      const response = await startMeetingRealtime({
        title: meetingTitle,
        participants: participants.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean),
      });
      setRealtime(response.data);
      setLastResult(response.data as unknown as Record<string, unknown>);
      setMessage("通义听悟实时会议已启动，正在从树莓派麦克风采集。");
      await load(response.data.transcript_path ?? transcript, response.data.meeting_id ?? undefined);
    } catch (err) {
      setError(meetingRealtimeStartErrorMessage(err));
      const failure = meetingRealtimeStartFailureResult(err);
      if (failure) setLastResult(failure);
      const provider = meetingRealtimeStartFailureProvider(err);
      if (provider) {
        setProviderStatus((current) => ({
          status: provider.status ?? "unavailable",
          primary_provider: "tongyi_tingwu",
          providers: {
            ...(current?.providers ?? {}),
            tongyi_tingwu: provider,
          },
        }));
      }
      setMessage("实时会议启动失败");
    } finally {
      setRealtimeBusy(false);
    }
  }

  async function runProviderPreflight() {
    setProviderPreflightBusy(true);
    setError("");
    setMessage("正在执行通义听悟本地预检...");
    try {
      const response = await runMeetingProviderPreflight(1);
      setProviderPreflight(response.data);
      setProviderStatus((current) => ({
        status: response.data.provider_status.status,
        primary_provider: "tongyi_tingwu",
        providers: {
          ...(current?.providers ?? {}),
          tongyi_tingwu: response.data.provider_status,
        },
      }));
      setLastResult(response.data as unknown as Record<string, unknown>);
      setMessage(response.data.ready ? "通义听悟本地预检通过。" : `通义听悟本地预检状态：${String(response.data.status)}`);
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("通义听悟本地预检失败");
    } finally {
      setProviderPreflightBusy(false);
    }
  }

  async function stopRealtime() {
    setRealtimeBusy(true);
    setError("");
    setMessage(canRegisterRealtimeOutputs ? "正在登记会议输出..." : "正在停止实时会议...");
    try {
      const response = await stopMeetingRealtime(realtime?.meeting_id ?? providerStatus?.providers.tongyi_tingwu.active_meeting_id ?? undefined, false);
      const nextRealtime = realtimeResponseSession(response.data);
      setRealtime(nextRealtime);
      setLastResult(response.data as unknown as Record<string, unknown>);
      const session = response.data.session as { transcript_path?: string } | undefined;
      const nextTranscript = session?.transcript_path ?? response.data.transcript_path ?? transcript;
      setTranscript(nextTranscript);
      const status = String(response.data.status ?? nextRealtime.status ?? "");
      const meetingId = nextRealtime.meeting_id ?? response.data.meeting_id ?? providerStatus?.providers.tongyi_tingwu.active_meeting_id ?? "";
      setPendingStopMeetingId(["starting", "running", "stopping"].includes(status) ? String(meetingId) : "");
      setMessage(realtimeResultMessage(response.data as unknown as Record<string, unknown>, "stop"));
      await load(nextTranscript, nextRealtime.meeting_id ?? undefined);
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("实时会议停止失败");
    } finally {
      setRealtimeBusy(false);
    }
  }

  async function fetchRealtimeMinutes() {
    const meetingId = realtime?.meeting_id ?? providerStatus?.providers.tongyi_tingwu.active_meeting_id;
    if (!meetingId) {
      setError("没有可拉取纪要的通义听悟会议。");
      return;
    }
    setRealtimeBusy(true);
    setError("");
    setMessage("正在拉取通义听悟 AI 纪要...");
    try {
      const response = await fetchMeetingRealtimeMinutes(meetingId, true);
      setRealtime(realtimeResponseSession(response.data));
      setLastResult(response.data as unknown as Record<string, unknown>);
      setPendingStopMeetingId("");
      setMessage(realtimeResultMessage(response.data as unknown as Record<string, unknown>, "fetch"));
      await load(transcript, meetingId);
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("拉取 AI 纪要失败");
    } finally {
      setRealtimeBusy(false);
    }
  }

  async function generateEmailDraftOnly() {
    await run("生成邮件草稿", 6, () => runMeetingFollowup(transcript, {
      recipient: emailRecipient,
      create_reminders: false,
      render_projection: false,
    }));
  }

  async function exportFollowupPackage() {
    if (!canUseSelectedTranscript) {
      setError("请选择可读的会议转写文件，或在中间输入会议文本后导入。");
      return;
    }
    setError("");
    setMessage("正在导出会议跟进包...");
    try {
      const response = await exportMeetingPackage(transcript, {
        recipient: emailRecipient,
        authorized: exportAuthorized,
      });
      setLastResult(response.data);
      setMessage(`导出状态：${String(response.data.status ?? "completed")}`);
      await load(transcript);
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("导出会议跟进包失败");
    }
  }

  async function sendFollowupEmail() {
    if (!canUseSelectedTranscript) {
      setError("请选择可读的会议转写文件，或在中间输入会议文本后导入。");
      return;
    }
    setError("");
    setMessage("正在发送会后邮件...");
    try {
      const response = await sendMeetingEmail(transcript, {
        recipient: emailRecipient,
        authorized: emailAuthorized,
      });
      setLastResult(response.data);
      setMessage(`邮件发送状态：${String(response.data.status ?? "completed")}`);
      await load(transcript);
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("发送会后邮件失败");
    }
  }

  async function selectMeetingJob(jobId: string) {
    const job = jobs.find((item) => item.job_id === jobId);
    if (!job) return;
    setActiveJob(job);
    setTranscript(job.transcript || transcript);
    setActiveStepId(1);
    setError("");
    if (job.meeting_id) {
      const [status, events] = await Promise.all([loadRealtimeStatus(job.meeting_id), loadRealtimeEvents(job.meeting_id)]);
      const taskMonitor = await loadRealtimeTaskMonitor(realtimeTaskId(status, job));
      setRealtime(status);
      setRealtimeEvents(mergeRealtimeEvents(events));
      setRealtimeTask(taskMonitor.task);
      setRealtimeTaskEvents(mergeRealtimeEvents(taskMonitor.events));
    } else {
      setRealtime(null);
      setRealtimeEvents([]);
      setRealtimeTask(null);
      setRealtimeTaskEvents([]);
    }
  }

  async function openMeetingArtifact(artifact: MeetingArtifact) {
    setSelectedArtifact(artifact);
    setArtifactPreview(null);
    setArtifactPreviewError("");
    if (!artifact.workspaceName) {
      setArtifactPreviewError("这个产物不在 workspace 内，不能直接预览。");
      return;
    }
    setArtifactPreviewBusy(true);
    try {
      const response = await getWorkspacePreview(artifact.workspaceName);
      setArtifactPreview(response.data);
    } catch (err) {
      setArtifactPreviewError(apiErrorMessage(err));
    } finally {
      setArtifactPreviewBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="实时会议"
        description="录音转写、说话人分离、AI 纪要和会后动作"
        actions={
          <div className="meeting-header-actions">
            <StatusBadge status={meetingMode?.meeting_mode_enabled ? "enabled" : "blocked"} label={meetingMode?.meeting_mode_enabled ? "会议理解已开启" : "默认不解析"} />
            <button className="ghost-button" onClick={() => void load()}>刷新</button>
          </div>
        }
      />
      <div className="meeting-workbench">
        {error && <div className="danger-panel">操作失败：{error}</div>}

        <section className="meeting-app-shell">
          <aside className="meeting-app-sidebar" aria-label="会议列表">
            <div className="meeting-pane-header">
              <div>
                <span>会议列表</span>
                <strong>{jobs.length} 场会议</strong>
              </div>
              <StatusBadge status={transcript ? "available" : "pending"} label={transcript ? "已选择" : "待导入"} />
            </div>
            <div className="meeting-list">
              {jobs.length ? jobs.map((job) => (
                <button
                  className={`meeting-list-item ${job.job_id === activeJob?.job_id ? "meeting-list-item--active" : ""}`}
                  key={job.job_id}
                  onClick={() => void selectMeetingJob(job.job_id)}
                  type="button"
                >
                  <strong>{compactDisplayPath(job.title || job.transcript || job.job_id)}</strong>
                  <span>{compactDisplayPath(job.transcript)}</span>
                  <StatusBadge status={job.status} label={friendlyStatus(job.status)} />
                </button>
              )) : (
                <div className="meeting-empty-state">暂无会议记录</div>
              )}
            </div>
            <div className="meeting-sidebar-section">
              <span className="small muted">会议资料</span>
              <select className="select" value={transcript} onChange={(event) => setTranscript(event.target.value)}>
                {transcriptFiles.map((file) => <option value={file.relative_path} key={file.relative_path}>{compactFileLabel(file.name)}</option>)}
                {!transcriptFiles.length && <option value="">暂无转写文件</option>}
              </select>
              {!transcriptFiles.length && <p className="small muted">PDF、图片、扫描件请在文档页查看；会议页只导入 txt/md/json 转写文本。</p>}
              <button className="ghost-button" onClick={() => void run("导入转写", 2, () => importTranscript(transcript, transcript.split("/").pop()))} disabled={!canUseSelectedTranscript}>
                <PlayCircle size={16} />
                导入为会议
              </button>
            </div>
            <div className="meeting-sidebar-section">
              <span className="small muted">听悟能力</span>
              <TingwuCapabilityPanel providerStatus={providerStatus} compact />
            </div>
          </aside>

          <main className="meeting-live-pane">
            <div className="meeting-live-topbar">
              <div>
                <span>当前会议</span>
                <h2>{activeJob?.title ?? meetingTitle}</h2>
              </div>
              <div className="meeting-live-badges">
                <StatusBadge status={meetingMode?.meeting_mode_enabled ? "enabled" : "blocked"} label={meetingMode?.meeting_mode_enabled ? "会议模式" : "未开启"} />
                <StatusBadge status={realtime?.status ?? "idle"} label={friendlyStatus(realtime?.status ?? "idle")} />
              </div>
            </div>

            <div className="meeting-console-status meeting-app-status">
              <div className="meeting-status-tile">
                <span>听悟链路</span>
                <strong>{friendlyStatus(providerReady)}</strong>
                <small>{enabledCapabilityCount} 项能力开启</small>
              </div>
              <div className="meeting-status-tile">
                <span>麦克风</span>
                <strong>{friendlyMicStatus(tingwuMicStatus(providerStatus))}</strong>
                <small>{compactText(providerStatus?.providers.tongyi_tingwu.selected_mic_device ?? providerStatus?.providers.tongyi_tingwu.configured_mic_device ?? "-", 32)}</small>
              </div>
              <div className="meeting-status-tile">
                <span>采集</span>
                <strong>{formatSeconds(realtime?.audio_seconds)}</strong>
                <small>{realtime?.final_count ?? localRealtime?.turn_count ?? 0} 条转写</small>
              </div>
            </div>

            <div className="meeting-form-grid meeting-live-form">
              <label>
                <span>会议标题</span>
                <input className="input" value={meetingTitle} onChange={(event) => setMeetingTitle(event.target.value)} placeholder="会议标题" />
              </label>
              <label>
                <span>参会人</span>
                <input className="input" value={participants} onChange={(event) => setParticipants(event.target.value)} placeholder="用逗号分隔" />
              </label>
            </div>

            <div className="meeting-command-row meeting-live-actions">
              <button className="secondary-button" onClick={() => void setMeetingModeEnabled(true)}>
                <CheckCircle2 size={16} />
                进入会议模式
              </button>
              <button className="ghost-button" onClick={() => void setMeetingModeEnabled(false)}>退出会议模式</button>
              <button className="primary-button" onClick={() => void startRealtime()} disabled={realtimeControlsBusy || realtimeActive}>
                <Mic size={16} /> 开始实时会议
              </button>
              <button className="danger-button" onClick={() => void stopRealtime()} disabled={realtimeControlsBusy || (!canStopRealtime && !canRegisterRealtimeOutputs)}>
                {canRegisterRealtimeOutputs ? <FileText size={16} /> : <Square size={16} />} {stopButtonLabel}
              </button>
              <button className="ghost-button" onClick={() => void fetchRealtimeMinutes()} disabled={realtimeControlsBusy || !realtime?.meeting_id || realtimeActive}>
                拉取 AI 纪要
              </button>
            </div>

            <div className="meeting-transcript-panel">
              <div className="meeting-pane-header">
                <div>
                  <span>实时转写</span>
                  <strong>{transcriptLines.length} 条记录</strong>
                </div>
                <StatusBadge status={transcriptLines.length ? "available" : "pending"} label={transcriptLines.length ? "有内容" : "等待"} />
              </div>
              <div className="realtime-transcript realtime-transcript--app">
                {transcriptLines.length ? transcriptLines.map((line) => <p key={line}>{line}</p>) : <p className="small muted">等待实时转写，或导入已有会议资料。</p>}
              </div>
            </div>

            <div className="meeting-lower-grid">
              <div className="meeting-text-card meeting-inline-panel">
                <div className="meeting-pane-header">
                  <div>
                    <span>粘贴文本</span>
                    <strong>导入会议内容</strong>
                  </div>
                </div>
                <textarea
                  className="textarea meeting-text-card__textarea"
                  value={meetingText}
                  onChange={(event) => setMeetingText(event.target.value)}
                  placeholder="粘贴会议转写、录音识别结果或会议笔记"
                />
                <div className="meeting-card-actions">
                  <button className="secondary-button" onClick={() => void importMeetingTextContent()} disabled={meetingTextImporting}>
                    {meetingTextImporting ? "导入中..." : "导入会议文本"}
                  </button>
                </div>
              </div>

              <div className="local-realtime-card meeting-inline-panel">
                <div className="meeting-pane-header">
                  <div>
                    <span>手动发言</span>
                    <strong>分角色补录</strong>
                  </div>
                  <StatusBadge status={localRealtime?.meeting_mode_enabled ? "enabled" : "blocked"} label={localRealtime?.meeting_mode_enabled ? "可记录" : "需开启"} />
                </div>
                <div className="local-realtime-form">
                  <input className="input" value={localSpeaker} onChange={(event) => setLocalSpeaker(event.target.value)} placeholder="发言人" />
                  <textarea className="textarea" value={localTurnText} onChange={(event) => setLocalTurnText(event.target.value)} placeholder="补充一句发言" />
                  <button className="primary-button" onClick={() => void appendLocalRealtimeTurn()} disabled={localRealtimeBusy || !meetingMode?.meeting_mode_enabled}>追加发言</button>
                  <button className="ghost-button" onClick={() => void exportLocalRealtime()} disabled={localRealtimeBusy || !localRealtime?.turn_count}>导出转写</button>
                </div>
                <div className="speaker-counts">
                  {Object.entries(localSpeakerCounts).map(([speaker, count]) => (
                    <span key={speaker}>{speaker}: {count}</span>
                  ))}
                  {!Object.keys(localSpeakerCounts).length && <span className="small muted">暂无分角色发言。</span>}
                </div>
              </div>
            </div>
          </main>

          <aside className="meeting-ai-pane" aria-label="AI 结果">
            <div className="meeting-pane-header">
              <div>
                <span>AI 结果</span>
                <strong>{minutesReady ? "已生成" : "待生成"}</strong>
              </div>
              <StatusBadge status={minutesReady ? "completed" : "pending"} label={minutesReady ? "已生成" : "待生成"} />
            </div>
            <div className="meeting-insight-tabs" role="tablist" aria-label="会议 AI 结果">
              {meetingInsightTabs.map((tab) => (
                <button
                  className={tab.id === activeInsightTab ? "selected" : ""}
                  key={tab.id}
                  onClick={() => {
                    setActiveInsightTab(tab.id);
                    setSelectedArtifact(null);
                    setArtifactPreview(null);
                    setArtifactPreviewError("");
                  }}
                  role="tab"
                  type="button"
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <MeetingInsightPanel
              activeTab={activeInsightTab}
              actionItems={actionItems}
              artifacts={artifacts}
              decisions={decisions}
              diagnostics={diagnostics}
              onOpenArtifact={(artifact) => void openMeetingArtifact(artifact)}
              outputs={outputs}
              preview={artifactPreview}
              previewBusy={artifactPreviewBusy}
              previewError={artifactPreviewError}
              resultSummary={resultSummary}
              selectedArtifact={selectedArtifact}
            />
            <div className="meeting-wiki-actions">
              <button className="ghost-button" onClick={() => void syncArtifactToWiki(selectedArtifact)} disabled={!selectedArtifact || wikiBusyPath === selectedArtifact.workspaceName}>
                <BookOpen size={16} />
                {wikiBusyPath === selectedArtifact?.workspaceName ? "同步中..." : "同步选中产物到 Wiki"}
              </button>
              {wikiResult?.docmost_page_url && (
                <a className="link-blue" href={wikiResult.docmost_page_url} target="_blank" rel="noreferrer">
                  <ExternalLink size={16} /> 打开最近同步页面
                </a>
              )}
            </div>
            <div className="meeting-ai-actions">
              <button className="secondary-button" onClick={() => void run("生成会议纪要", 3, () => runMeetingMinutes(transcript))} disabled={!canUseSelectedTranscript}>
                <ClipboardList size={16} />
                生成纪要
              </button>
              <button className="ghost-button" onClick={() => void run("提取决策", 4, () => runMeetingStep("decisions", { file_path: transcript, job_id: activeJob?.job_id }))} disabled={!canUseSelectedTranscript}>
                提取决策
              </button>
              <button className="ghost-button" onClick={() => void run("提取行动项", 5, () => runMeetingStep("action-items", { file_path: transcript, job_id: activeJob?.job_id }))} disabled={!canUseSelectedTranscript}>
                提取行动项
              </button>
              <button className="primary-button" onClick={() => void run("生成会后资料", 6, () => runMeetingFollowup(transcript))} disabled={!canUseSelectedTranscript}>
                <FileStack size={16} />
                会后资料
              </button>
            </div>
            <div className="meeting-email-panel">
              <div className="row">
                <Mail size={16} />
                <strong>会后邮件</strong>
              </div>
              <input className="input" value={emailRecipient} onChange={(event) => setEmailRecipient(event.target.value)} placeholder="收件人" />
              <label className="inline-check">
                <input type="checkbox" checked={exportAuthorized} onChange={(event) => setExportAuthorized(event.target.checked)} />
                <span>授权导出资料</span>
              </label>
              <button className="ghost-button" onClick={() => void exportFollowupPackage()} disabled={!canUseSelectedTranscript}>导出资料</button>
              <label className="inline-check">
                <input type="checkbox" checked={emailAuthorized} onChange={(event) => setEmailAuthorized(event.target.checked)} />
                <span>授权发送邮件</span>
              </label>
              <button className="danger-button" onClick={() => void sendFollowupEmail()} disabled={!canUseSelectedTranscript}>发送邮件</button>
            </div>
          </aside>
        </section>

        <section className="meeting-process-panel" aria-label="Meeting workflow">
          <div className="meeting-section-heading">
            <span>流程</span>
            <strong>{activeStep.title}</strong>
            <StatusBadge status={activeJob?.status ?? "pending"} label={friendlyStatus(activeJob?.status ?? "pending")} />
          </div>
          <div className="meeting-progress-list">
            {workflowSteps.slice(0, 8).map((step) => (
              <button
                className={`meeting-progress-item ${step.id === activeStep.id ? "meeting-progress-item--active" : ""}`}
                key={step.id}
                onClick={() => setActiveStepId(step.id)}
                type="button"
              >
                <span>{step.id}</span>
                <strong>{step.title}</strong>
                <StatusBadge status={step.status} label={friendlyStatus(step.status)} />
              </button>
            ))}
          </div>
        </section>

        <Card title="当前反馈" action={<StatusBadge status={String(lastResult?.status ?? activeJob?.status ?? "pending")} label={friendlyStatus(lastResult?.status ?? activeJob?.status ?? "pending")} />}>
          <div className="row">
            <CalendarCheck size={18} />
            <span>{message}</span>
          </div>
        </Card>

        <details className="advanced-panel">
          <summary>高级诊断</summary>
          <div className="advanced-panel__content">
            <Card title="通义听悟链路" subtitle="仅用于配置检查和问题定位，不作为普通用户主界面。" action={<StatusBadge status={providerStatus?.providers.tongyi_tingwu.status ?? "needs_config"} />}>
              <div className="definition-grid">
                <span>Provider</span><strong>tongyi_tingwu</strong>
                <span>配置麦克风</span><strong>{providerStatus?.providers.tongyi_tingwu.configured_mic_device ?? providerStatus?.providers.tongyi_tingwu.mic_device ?? "-"}</strong>
                <span>实际麦克风</span><strong>{providerStatus?.providers.tongyi_tingwu.selected_mic_device ?? "-"}</strong>
                <span>麦克风状态</span><StatusBadge status={tingwuMicStatus(providerStatus)} />
                <span>采样率</span><strong>{providerStatus?.providers.tongyi_tingwu.sample_rate ?? 16000} Hz · {providerStatus?.providers.tongyi_tingwu.audio_format ?? "pcm"}</strong>
                <span>识别/分析模型</span><strong>{providerStatus?.providers.tongyi_tingwu.transcription_model ?? "-"} · {providerStatus?.providers.tongyi_tingwu.analysis_model ?? "-"}</strong>
                <span>翻译</span><strong>{providerStatus?.providers.tongyi_tingwu.translation_enabled ? (providerStatus.providers.tongyi_tingwu.translation_target_lang ?? []).join(", ") || "enabled" : "off"}</strong>
                <span>热词库</span><strong>{providerStatus?.providers.tongyi_tingwu.phrase_id_configured ? "phraseId 已配置" : (providerStatus?.providers.tongyi_tingwu.hot_words_configured ? "仅本地热词备注" : "-")}</strong>
                <span>HTTP 端点</span><strong>{providerStatus?.providers.tongyi_tingwu.http_url ?? "-"}</strong>
                <span>WS 端点</span><strong>{providerStatus?.providers.tongyi_tingwu.ws_url ?? "-"}</strong>
                <span>麦克风诊断</span><strong>{tingwuMicMessage(providerStatus)}</strong>
                <span>状态</span><StatusBadge status={realtime?.status ?? "idle"} label={String(realtime?.status ?? "idle")} />
              </div>
              <TingwuCapabilityPanel providerStatus={providerStatus} />
              <div className="realtime-monitor" aria-label="Realtime capture monitor">
                <div><span>采集时长</span><strong>{formatSeconds(realtime?.audio_seconds)} / {Number(realtime?.websocket_audio_frames ?? 0)} 帧</strong></div>
                <div><span>最终转写</span><strong>{realtime?.final_count ?? 0}</strong></div>
                <div><span>RMS</span><strong>{formatNumber(realtime?.audio_rms)}</strong></div>
                <div><span>Peak</span><strong>{formatNumber(realtime?.audio_peak)}</strong></div>
              </div>
              <div className="meeting-card-actions">
                <button className="ghost-button" onClick={() => void runProviderPreflight()} disabled={providerPreflightBusy || realtimeActive}>
                  {providerPreflightBusy ? "预检中..." : "本地预检"}
                </button>
              </div>
            </Card>

            <Card title="本地预检">
              <div className="tingwu-preflight-panel">
                <div className="definition-grid">
                  <span>本地预检</span><StatusBadge status={providerPreflight?.status ?? "pending"} label={providerPreflight ? String(providerPreflight.status) : "未执行"} />
                  <span>凭证</span><strong>{preflightCredentialLabel(providerPreflight)}</strong>
                  <span>官方端点</span><StatusBadge status={preflightCheckStatus(providerPreflight, "official_tingwu_endpoint")} />
                  <span>真实麦克风</span><StatusBadge status={preflightCheckStatus(providerPreflight, "real_microphone_device")} />
                  <span>设备一致</span><StatusBadge status={preflightCheckStatus(providerPreflight, "microphone_capture_device_matches")} />
                  <span>采集打开</span><StatusBadge status={preflightCheckStatus(providerPreflight, "microphone_capture_open")} />
                  <span>采集信号</span><StatusBadge status={preflightCheckStatus(providerPreflight, "microphone_capture_signal")} />
                  <span>预检麦克风</span><strong>{providerPreflight?.selected_mic_device ?? "-"}</strong>
                  <span>采集探针</span><strong>{providerPreflightCaptureSummary(providerPreflight)}</strong>
                </div>
                <div className="preflight-checks" aria-label="Tingwu local preflight checks">
                  {preflightChecks.map((check) => (
                    <span key={check.key} className={`preflight-check preflight-check--${check.ok ? "ok" : "blocked"}`}>
                      {check.label}
                    </span>
                  ))}
                </div>
                <div className="preflight-next-action">{preflightRecommendation(providerPreflight)}</div>
                <PreflightNextActionDetails preflight={providerPreflight} />
                <PreflightAcceptanceChecklist preflight={providerPreflight} realtime={realtime} job={activeJob} task={realtimeTask} diagnostics={diagnostics} />
              </div>
            </Card>

            <Card title="任务与事件" action={<StatusBadge status={realtimeTask?.status ?? "pending"} />}>
              <div className="definition-grid">
                <span>会议 ID</span><strong>{realtime?.meeting_id ?? providerStatus?.providers.tongyi_tingwu.active_meeting_id ?? "-"}</strong>
                <span>作业 ID</span><strong>{activeJob?.job_id ?? "-"}</strong>
                <span>Web 任务</span><strong>{realtimeTask?.task_id ?? realtimeTaskId(realtime, activeJob) ?? "-"}</strong>
                <span>通义任务</span><strong>{realtime?.provider_task_id ?? realtime?.task_id ?? "-"}</strong>
                <span>进度</span><strong>{formatPercent(realtimeTask?.progress)}</strong>
                <span>更新时间</span><strong>{realtimeTask?.updated_at ?? "-"}</strong>
              </div>
              <div className="meeting-output-list realtime-events">
                {(realtimeEvents.length ? realtimeEvents : [{ event: "idle", timestamp: "-", status: realtime?.status ?? "idle" }]).slice(0, 8).map((event, index) => (
                  <span className="result-file" key={`${String(event.event ?? "event")}-${index}`}>
                    <CalendarCheck size={16} />
                    <span>{formatRealtimeEvent(event)}</span>
                  </span>
                ))}
              </div>
              <div className="meeting-output-list realtime-events">
                {(realtimeTaskEvents.length ? realtimeTaskEvents : taskOutputEvents(realtimeTask)).slice(0, 6).map((event, index) => (
                  <span className="result-file" key={`task-${String(event.event ?? "event")}-${index}`}>
                    <CalendarCheck size={16} />
                    <span>{String(event.event ?? event.type ?? "event")} · {String(event.timestamp ?? "-")}</span>
                  </span>
                ))}
                {!realtimeTaskEvents.length && !taskOutputEvents(realtimeTask).length && <span className="small muted">等待实时任务事件。</span>}
              </div>
              <div className="meeting-output-list realtime-events">
                {(agentEvents.length ? agentEvents : [{ type: "idle", timestamp: "-", text: "等待听悟 Agent 事件" }]).slice(0, 6).map((event, index) => (
                  <span className="result-file" key={`agent-${String(event.type ?? event.event ?? "event")}-${index}`}>
                    <CalendarCheck size={16} />
                    <span>{formatAgentEvent(event)}</span>
                  </span>
                ))}
              </div>
            </Card>

            <Card title="输出诊断" action={<StatusBadge status={diagnostics.status} />}>
              <div className="definition-grid">
                <span>Provider</span><strong>{diagnostics.providerStatus}</strong>
                <span>OpenClaw</span><strong>{diagnostics.openclawStatus}</strong>
                <span>内容状态</span><strong>{diagnostics.contentStatus || "-"}</strong>
                <span>Provider 错误</span><strong className={diagnostics.providerError ? "danger-text" : ""}>{diagnostics.providerError || "-"}</strong>
                <span>OpenClaw 错误</span><strong className={diagnostics.openclawError ? "danger-text" : ""}>{diagnostics.openclawError || "-"}</strong>
                <span>Manifest</span><strong>{diagnostics.manifestPath || "-"}</strong>
                <span>Transcript</span><strong>{diagnostics.transcriptPath || "-"}</strong>
                <span>Audio</span><strong>{diagnostics.audioPath || "-"}</strong>
                <span>通义听悟纪要</span><strong>{diagnostics.tingwuMinutesPath || "-"}</strong>
                <span>OpenClaw 纪要</span><strong>{diagnostics.openclawMinutesPath || "-"}</strong>
                <span>听悟 HTTP</span><strong>{diagnostics.tingwuHttpActions || "-"}</strong>
                <span>听悟链路</span><StatusBadge status={diagnostics.tingwuChainStatus} label={diagnostics.tingwuChainLabel} />
                <span>Realtime dataId</span><strong>{diagnostics.tingwuRealtimeDataId || "-"}</strong>
                <span>AI minutes dataId</span><strong>{diagnostics.tingwuMinutesDataId || "-"}</strong>
                <span>最近 dataId</span><strong>{diagnostics.tingwuLastDataId || "-"}</strong>
              </div>
            </Card>

            <Card title="原始结果" action={<StatusBadge status={String(lastResult?.status ?? "pending")} />}>
              <pre className="json-preview meeting-json-preview">{JSON.stringify(lastResult ?? persistedResult ?? activeJob ?? {}, null, 2)}</pre>
            </Card>
          </div>
        </details>
      </div>
    </>
  );
}

function PreflightNextActionDetails({ preflight }: { preflight: MeetingProviderPreflight | null }) {
  const action = preflightPrimaryNextAction(preflight);
  const envLines = action?.env ? Object.entries(action.env).map(([key, value]) => `${key}=${value}`) : [];
  const command = formatShellCommand(action?.command);
  const auditCommand = formatShellCommand(action?.audit_command);
  const runCommand = formatRunnableCommand(action?.command, action?.env, action?.cwd);
  const runAuditCommand = formatRunnableCommand(action?.audit_command, undefined, action?.cwd);
  if (!action || (!envLines.length && !command && !auditCommand && !action.cwd)) return null;
  return (
    <div className="preflight-command-panel" aria-label="Tingwu live acceptance next action">
      {envLines.length > 0 && (
        <div>
          <span>Env</span>
          <code>{envLines.join(" ")}</code>
        </div>
      )}
      {action.cwd && (
        <div>
          <span>工作目录</span>
          <code>{action.cwd}</code>
        </div>
      )}
      {command && (
        <div>
          <span>验收命令</span>
          <code>{command}</code>
        </div>
      )}
      {auditCommand && (
        <div>
          <span>审计命令</span>
          <code>{auditCommand}</code>
        </div>
      )}
      {runCommand && (
        <div>
          <span>可复制执行</span>
          <code>{runCommand}</code>
        </div>
      )}
      {runAuditCommand && (
        <div>
          <span>可复制审计</span>
          <code>{runAuditCommand}</code>
        </div>
      )}
      <CredentialLinks links={action.links} />
    </div>
  );
}

function PreflightAcceptanceChecklist({
  preflight,
  realtime,
  job,
  task,
  diagnostics,
}: {
  preflight: MeetingProviderPreflight | null;
  realtime: MeetingRealtimeStatus | null;
  job: MeetingJob | null;
  task: TaskRecord | null;
  diagnostics: RealtimeDiagnostics;
}) {
  const checklist = acceptanceChecklistWithRuntimeStatus(preflight?.acceptance_checklist ?? [], realtime, job, task, diagnostics);
  if (!checklist.length) return null;
  return (
    <div className="preflight-acceptance-list" aria-label="Tingwu acceptance checklist">
      {checklist.map((item) => (
        <div className="preflight-acceptance-item" key={item.id}>
          <div className="row-between">
            <strong>{item.title}</strong>
            <StatusBadge status={item.status} label={String(item.status)} />
          </div>
          <p>{item.how_to_test}</p>
          {item.evidence?.length ? <code>{item.evidence.join(" · ")}</code> : null}
          <CredentialLinks links={item.links} />
          <AcceptanceChecklistCommand item={item} />
        </div>
      ))}
    </div>
  );
}

function AcceptanceChecklistCommand({ item }: { item: MeetingProviderAcceptanceItem }) {
  const command = formatRunnableCommand(item.command, item.env, item.cwd);
  const auditCommand = formatRunnableCommand(item.audit_command, undefined, item.cwd);
  if (!command && !auditCommand) return null;
  return (
    <div className="preflight-acceptance-command" aria-label={`${item.title} acceptance command`}>
      {command && <code>{command}</code>}
      {auditCommand && <code>{auditCommand}</code>}
    </div>
  );
}

function CredentialLinks({ links }: { links?: Array<{ label: string; url: string }> }) {
  const safeLinks = (links ?? []).filter((link) => /^https:\/\/[A-Za-z0-9.-]+(?:\/|$)/.test(link.url));
  if (!safeLinks.length) return null;
  return (
    <div className="preflight-link-list" aria-label="Tingwu credential links">
      {safeLinks.map((link) => (
        <a key={link.url} href={link.url} target="_blank" rel="noreferrer">{link.label}</a>
      ))}
    </div>
  );
}

function MeetingInsightPanel({
  activeTab,
  actionItems,
  artifacts,
  decisions,
  diagnostics,
  onOpenArtifact,
  outputs,
  preview,
  previewBusy,
  previewError,
  resultSummary,
  selectedArtifact,
}: {
  activeTab: MeetingInsightTab;
  actionItems: string[];
  artifacts: MeetingArtifact[];
  decisions: string[];
  diagnostics: RealtimeDiagnostics;
  onOpenArtifact: (artifact: MeetingArtifact) => void;
  outputs: string[];
  preview: SharedPreviewResponse | null;
  previewBusy: boolean;
  previewError: string;
  resultSummary: string;
  selectedArtifact: MeetingArtifact | null;
}) {
  const tabArtifacts = artifactsForTab(artifacts, activeTab);
  const outputPatterns = artifactPatternsForTab(activeTab);
  if (activeTab === "minutes") {
    return (
      <div className="meeting-insight-panel">
        <p>{resultSummary || "尚未生成会议纪要。"}</p>
        <MeetingArtifactList artifacts={tabArtifacts} onOpen={onOpenArtifact} fallbackOutputs={[diagnostics.tingwuMinutesPath, diagnostics.openclawMinutesPath].filter(Boolean)} />
        <MeetingArtifactPreview artifact={selectedArtifact} preview={preview} busy={previewBusy} error={previewError} />
      </div>
    );
  }
  if (activeTab === "actions") {
    return (
      <div className="meeting-insight-panel">
        <div className="mini-table-wrap">
          <table className="mini-table">
            <tbody>
              <tr><th>#</th><th>行动项</th><th>状态</th></tr>
              {(actionItems.length ? actionItems : ["等待生成会后资料。"]).map((item, index) => (
                <tr key={item}><td>{index + 1}</td><td>{item}</td><td>已生成</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <MeetingArtifactList artifacts={tabArtifacts} onOpen={onOpenArtifact} />
        <MeetingArtifactPreview artifact={selectedArtifact} preview={preview} busy={previewBusy} error={previewError} />
      </div>
    );
  }
  if (activeTab === "decisions") {
    return (
      <div className="meeting-insight-panel">
        <ol className="dense-list">
          {(decisions.length ? decisions : ["等待生成纪要。"]).map((item) => <li key={item}>{item}</li>)}
        </ol>
        <MeetingArtifactList artifacts={tabArtifacts} onOpen={onOpenArtifact} />
        <MeetingArtifactPreview artifact={selectedArtifact} preview={preview} busy={previewBusy} error={previewError} />
      </div>
    );
  }
  if (activeTab === "qa") {
    return (
      <div className="meeting-insight-panel">
        <p>等待听悟问答回顾结果。</p>
        <MeetingArtifactList artifacts={tabArtifacts} onOpen={onOpenArtifact} fallbackOutputs={outputMatches(outputs, outputPatterns)} />
        <MeetingArtifactPreview artifact={selectedArtifact} preview={preview} busy={previewBusy} error={previewError} />
      </div>
    );
  }
  if (activeTab === "ppt") {
    return (
      <div className="meeting-insight-panel">
        <p>{diagnostics.tingwuChainStatus === "completed" ? "已拉取可用输出文件。" : "等待 PPT 提取结果。"}</p>
        <MeetingArtifactList artifacts={tabArtifacts} onOpen={onOpenArtifact} fallbackOutputs={outputMatches(outputs, outputPatterns)} />
        <MeetingArtifactPreview artifact={selectedArtifact} preview={preview} busy={previewBusy} error={previewError} />
      </div>
    );
  }
  return (
    <div className="meeting-insight-panel">
      <p>等待思维导图结果。</p>
      <MeetingArtifactList artifacts={tabArtifacts} onOpen={onOpenArtifact} fallbackOutputs={outputMatches(outputs, outputPatterns)} />
      <MeetingArtifactPreview artifact={selectedArtifact} preview={preview} busy={previewBusy} error={previewError} />
    </div>
  );
}

function MeetingArtifactList({
  artifacts,
  fallbackOutputs = [],
  onOpen,
}: {
  artifacts: MeetingArtifact[];
  fallbackOutputs?: string[];
  onOpen: (artifact: MeetingArtifact) => void;
}) {
  const fallbackArtifacts = fallbackOutputs.map((path) => meetingArtifactFromPath(path)).filter((item): item is MeetingArtifact => Boolean(item));
  const items = uniqueArtifacts([...artifacts, ...fallbackArtifacts]);
  return (
    <div className="meeting-output-list meeting-artifact-list">
      {items.map((artifact) => (
        <button className="result-file meeting-artifact-button" key={`${artifact.kind}-${artifact.path}`} onClick={() => onOpen(artifact)} type="button">
          <FileText size={16} />
          <span>{artifact.label}</span>
          <small>{artifact.stepLabel}</small>
        </button>
      ))}
      {!items.length && <span className="small muted">暂无可展示产物。</span>}
    </div>
  );
}

function MeetingArtifactPreview({
  artifact,
  preview,
  busy,
  error,
}: {
  artifact: MeetingArtifact | null;
  preview: SharedPreviewResponse | null;
  busy: boolean;
  error: string;
}) {
  if (!artifact && !busy && !error) return null;
  return (
    <div className="meeting-artifact-preview">
      <WorkspaceFileViewer
        source="workspace"
        filePath={artifact?.workspaceName ?? ""}
        preview={preview}
        busy={busy}
        error={error}
        title="会议产物查看"
        emptyText="选择左侧会议产物后在这里查看。"
        compact
      />
    </div>
  );
}

function MeetingOutputMatches({ outputs, patterns }: { outputs: string[]; patterns: string[] }) {
  const matches = outputMatches(outputs, patterns);
  return (
    <div className="meeting-output-list">
      {(matches.length ? matches : outputs.slice(0, 4)).map((item) => (
        <span className="result-file" key={item}>
          <FileText size={16} />
          <span>{compactDisplayPath(item)}</span>
        </span>
      ))}
      {!outputs.length && <span className="small muted">暂无输出文件。</span>}
    </div>
  );
}

function outputMatches(outputs: string[], patterns: string[]): string[] {
  return outputs.filter((item) => patterns.some((pattern) => item.toLowerCase().includes(pattern)));
}

function TingwuCapabilityPanel({ providerStatus, compact = false }: { providerStatus: MeetingProviderStatus | null; compact?: boolean }) {
  const capabilities = providerStatus?.providers.tongyi_tingwu.capabilities ?? {};
  const items = [
    ["实时转写", capabilities.realtime_transcription],
    ["说话人分离", capabilities.speaker_diarization],
    ["翻译", capabilities.translation],
    ["热词库", capabilities.phrase_hot_words],
    ["关键词/关键句", capabilities.key_information],
    ["行动项", capabilities.actions],
    ["全文摘要", capabilities.full_summary],
    ["发言总结", capabilities.conversational_summary],
    ["问答回顾", capabilities.questions_answering],
    ["章节速览", capabilities.auto_chapters],
    ["思维导图", capabilities.mind_map],
    ["PPT 提取", capabilities.ppt_extraction],
    ["口语书面化", capabilities.text_polish],
    ["自定义 Prompt", capabilities.custom_prompt],
    ["会议 Agent", capabilities.meeting_agent_events],
  ];
  return (
    <div className={`tingwu-capability-grid ${compact ? "tingwu-capability-grid--compact" : ""}`} aria-label="Tingwu enabled capabilities">
      {items.map(([label, enabled]) => (
        <span key={String(label)} className={`tingwu-capability ${enabled ? "tingwu-capability--on" : "tingwu-capability--off"}`}>
          {String(label)}
        </span>
      ))}
    </div>
  );
}

type RealtimeDiagnostics = ReturnType<typeof realtimeDiagnostics>;

interface MeetingArtifact {
  path: string;
  workspaceName: string;
  label: string;
  step: string;
  stepLabel: string;
  kind: MeetingInsightTab | "transcript" | "audio" | "email" | "projection" | "other";
  type: string;
}

function isTranscriptLike(file: SharedFile) {
  return isTranscriptPathLike(file.relative_path || file.name);
}

function isTranscriptPathLike(value?: string) {
  const text = String(value ?? "").replace(/\\/g, "/").toLowerCase();
  const suffix = text.includes(".") ? text.slice(text.lastIndexOf(".")) : "";
  if (![".txt", ".md", ".markdown", ".json"].includes(suffix)) return false;
  if (suffix === ".json") return /transcript|meeting|minutes|asr|tingwu|转写|会议|纪要/.test(text);
  return /transcript|meeting|minutes|asr|tingwu|转写|会议|纪要|speaker|发言/.test(text);
}

function compactFileLabel(name: string) {
  if (name.length <= 18) return name;
  const dot = name.lastIndexOf(".");
  const extension = dot > 0 ? name.slice(dot) : "";
  return `${name.slice(0, 12)}...${extension.slice(0, 5)}`;
}

function compactJobLabel(job: MeetingJob) {
  const title = job.title || job.transcript || job.job_id;
  const stepCount = job.steps.length;
  const label = `${compactDisplayPath(title)} · ${friendlyStatus(job.status)} · ${stepCount}/8`;
  if (label.length <= 76) return label;
  return `${label.slice(0, 68)}...`;
}

function compactDisplayPath(value?: string | null) {
  const text = String(value ?? "");
  if (!text) return "-";
  const normalized = text.replace(/\\/g, "/");
  const workspaceMarker = "/workspace/";
  const workspaceIndex = normalized.lastIndexOf(workspaceMarker);
  if (workspaceIndex >= 0) return normalized.slice(workspaceIndex + 1);
  const runtimeMarker = "/lelamp_runtime/";
  const runtimeIndex = normalized.lastIndexOf(runtimeMarker);
  if (runtimeIndex >= 0) return `...${normalized.slice(runtimeIndex + runtimeMarker.length - 1)}`;
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 2) return normalized;
  return `.../${parts.slice(-2).join("/")}`;
}

function friendlyStatus(status: unknown) {
  const value = String(status ?? "");
  const labels: Record<string, string> = {
    ok: "正常",
    online: "在线",
    enabled: "已开启",
    available: "可用",
    completed: "已完成",
    partial: "部分完成",
    starting: "启动中",
    running: "进行中",
    stopping: "停止中",
    stopped: "已停止",
    pending: "待处理",
    waiting_confirmation: "处理中",
    needs_confirmation: "需授权",
    warning: "需注意",
    blocked: "已阻止",
    failed: "失败",
    error: "错误",
    unavailable: "不可用",
    adapter_ready: "待接入",
    backend_missing: "待接入",
    needs_config: "待配置",
    ready: "就绪",
    idle: "空闲",
    draft: "草稿",
  };
  return labels[value] ?? (value || "待处理");
}

function friendlyMicStatus(status: unknown) {
  const value = String(status ?? "");
  if (value === "available" || value === "ok") return "可用";
  if (value === "adapter_ready" || value === "mock") return "待接入";
  if (value === "unavailable" || value === "failed") return "不可用";
  if (value === "needs_config") return "待配置";
  return friendlyStatus(value);
}

async function loadRealtimeStatus(meetingId?: string): Promise<MeetingRealtimeStatus> {
  try {
    return (await getMeetingRealtimeStatus(meetingId)).data;
  } catch {
    return {
      provider: "tongyi_tingwu",
      status: "idle",
      meeting_id: meetingId ?? null,
      active_meeting_id: null,
      error: meetingId ? "Realtime session metadata is not available for this meeting." : "",
    } as MeetingRealtimeStatus;
  }
}

async function loadRealtimeEvents(meetingId?: string | null): Promise<Array<Record<string, unknown>>> {
  if (!meetingId) return [];
  try {
    return (await getMeetingRealtimeEvents(meetingId)).data.events;
  } catch {
    return [];
  }
}

async function loadRealtimeTaskMonitor(taskId?: string | null): Promise<{ task: TaskRecord | null; events: Array<Record<string, unknown>> }> {
  if (!taskId) return { task: null, events: [] };
  try {
    const [taskResult, eventsResult] = await Promise.all([getTask(taskId), getTaskEvents(taskId)]);
    return { task: taskResult.data, events: eventsResult.data.events };
  } catch {
    return { task: null, events: [] };
  }
}

function findJobForTranscript(jobs: MeetingJob[], transcript: string, meetingId?: string) {
  if (meetingId) {
    const byMeetingId = jobs.find((job) => job.meeting_id === meetingId);
    if (byMeetingId) return byMeetingId;
  }
  if (!transcript) return null;
  return jobs.find((job) => sameTranscriptRef(job.transcript, transcript) || job.steps.some((step) => sameTranscriptRef(step.input_file, transcript))) ?? null;
}

function sameTranscriptRef(left?: string, right?: string) {
  const a = normalizeTranscriptRef(left);
  const b = normalizeTranscriptRef(right);
  return Boolean(a && b && (a === b || a.endsWith(`/${b}`) || b.endsWith(`/${a}`)));
}

function normalizeTranscriptRef(value?: string) {
  return String(value ?? "").replace(/\\/g, "/").replace(/^\.\//, "").replace(/\/+/g, "/").replace(/^.*\/workspace\//, "");
}

function meetingJobResult(job: MeetingJob | null): Record<string, unknown> | null {
  if (!job) return null;
  const stepOutput = (name: string): Record<string, unknown> | null => {
    const step = job.steps.find((item) => item.name === name);
    return step?.output && typeof step.output === "object" ? step.output : null;
  };
  const realtime = stepOutput("realtime_capture");
  const minutes = stepOutput("minutes");
  const followup = stepOutput("followup");
  const exportPackage = stepOutput("export_package");
  const emailSend = stepOutput("email_send");
  const decisions = stepOutput("decisions");
  const actionItems = stepOutput("action_items");
  const manifestPath = String(minutes?.manifest_path ?? followup?.manifest_path ?? realtime?.manifest_path ?? "");
  return {
    status: job.status,
    provider_status: minutes?.provider_status ?? realtime?.provider_status ?? realtime?.status,
    openclaw_status: minutes?.openclaw_status,
    content_status: minutes?.content_status,
    meeting_id: job.meeting_id,
    transcript_path: realtime?.transcript_path ?? minutes?.transcript_path ?? job.transcript,
    audio_path: realtime?.audio_path,
    manifest_path: manifestPath,
    tingwu_minutes_path: minutes?.tingwu_minutes_path ?? realtime?.minutes_path,
    openclaw_minutes_path: minutes?.path,
    session: realtime,
    minutes,
    followup,
    export_package: exportPackage,
    email_send: emailSend,
    decisions: listValue(decisions?.decisions ?? decisions?.items),
    action_items: listValue(actionItems?.action_items ?? actionItems?.items),
    outputs: [
      ...job.steps.map((step) => step.output_path).filter(Boolean).map((path) => ({ path, type: "file" })),
      ...outputPaths(minutes).map((path) => ({ path, type: "file" })),
      ...outputPaths(followup).map((path) => ({ path, type: "file" })),
      ...outputPaths(exportPackage).map((path) => ({ path, type: "file" })),
      ...outputPaths(emailSend).map((path) => ({ path, type: "file" })),
    ],
  };
}

function meetingArtifacts(
  value: Record<string, unknown> | null,
  job: MeetingJob | null,
  realtime: MeetingRealtimeStatus | null,
): MeetingArtifact[] {
  const artifacts: MeetingArtifact[] = [];
  const add = (path: unknown, step = "", label = "") => {
    if (typeof path !== "string" || !path.trim()) return;
    const artifact = meetingArtifactFromPath(path, step, label);
    if (artifact) artifacts.push(artifact);
  };

  outputPaths(value).forEach((path) => add(path));
  outputPaths(recordValue(realtime)).forEach((path) => add(path));
  if (Array.isArray(realtime?.outputs)) {
    realtime.outputs.forEach((item) => add(item.path, String(item.type ?? ""), String(item.type ?? "")));
  }
  job?.steps.forEach((step) => {
    add(step.output_path, step.name);
    outputPaths(recordValue(step.output)).forEach((path) => add(path, step.name));
    const outputs = recordValue(step.output)?.outputs;
    if (Array.isArray(outputs)) {
      outputs.forEach((item) => {
        const record = recordValue(item);
        add(record?.path, step.name, String(record?.type ?? ""));
      });
    }
  });
  return uniqueArtifacts(artifacts);
}

function meetingArtifactFromPath(pathValue: string, step = "", label = ""): MeetingArtifact | null {
  const path = pathValue.trim();
  if (!path || path.startsWith("http://") || path.startsWith("https://")) return null;
  const workspaceName = normalizeWorkspaceArtifactPath(path);
  const basename = path.replace(/\\/g, "/").split("/").filter(Boolean).pop() ?? path;
  const type = artifactType(path);
  const kind = artifactKind(path, step);
  const friendlyLabel = label || friendlyArtifactLabel(path, step, kind, type);
  return {
    path,
    workspaceName,
    label: `${friendlyLabel} · ${basename}`,
    step,
    stepLabel: artifactStepLabel(step, kind, type),
    kind,
    type,
  };
}

function friendlyArtifactLabel(pathValue: string, step: string, kind: MeetingArtifact["kind"], type: string) {
  const text = `${pathValue} ${step} ${kind} ${type}`.toLowerCase();
  if (text.includes("minutes") || text.includes("纪要")) return "会议纪要";
  if (text.includes("decision") || text.includes("决策") || text.includes("决定")) return "决策";
  if (text.includes("action") || text.includes("todo") || text.includes("followup") || text.includes("待办")) return "待办";
  if (text.includes("email") || text.includes("mail")) return "邮件草稿";
  if (text.includes("ppt") || text.includes("slide")) return "PPT";
  if (text.includes("mindmap")) return "思维导图";
  if (text.includes("transcript") || text.includes("转写")) return "转写";
  return "结果文件";
}

function uniqueArtifacts(items: MeetingArtifact[]): MeetingArtifact[] {
  const seen = new Set<string>();
  const result: MeetingArtifact[] = [];
  items.forEach((item) => {
    const key = item.workspaceName || item.path;
    if (!key || seen.has(key)) return;
    seen.add(key);
    result.push(item);
  });
  return result.slice(0, 40);
}

function artifactsForTab(artifacts: MeetingArtifact[], tab: MeetingInsightTab): MeetingArtifact[] {
  const direct = artifacts.filter((artifact) => artifact.kind === tab);
  if (direct.length) return direct;
  const patterns = artifactPatternsForTab(tab);
  return artifacts.filter((artifact) => patterns.some((pattern) => `${artifact.path} ${artifact.step} ${artifact.label}`.toLowerCase().includes(pattern)));
}

function artifactPatternsForTab(tab: MeetingInsightTab): string[] {
  if (tab === "minutes") return ["minutes", "summary", "纪要"];
  if (tab === "actions") return ["action", "todo", "reminder", "followup", "行动", "待办", "提醒"];
  if (tab === "decisions") return ["decision", "decisions", "决策", "决定"];
  if (tab === "qa") return ["qa", "question", "answer", "questions", "问答"];
  if (tab === "ppt") return ["ppt", "slide", "presentation"];
  return ["mind", "map", "mindmap", "思维导图"];
}

function artifactKind(pathValue: string, step: string): MeetingArtifact["kind"] {
  const text = `${pathValue} ${step}`.toLowerCase();
  if (step === "minutes" || text.includes("minutes") || text.includes("summary") || text.includes("纪要")) return "minutes";
  if (step === "action_items" || step === "reminders" || text.includes("action") || text.includes("todo") || text.includes("reminder") || text.includes("待办")) return "actions";
  if (step === "decisions" || text.includes("decision") || text.includes("决策")) return "decisions";
  if (text.includes("question") || text.includes("answer") || text.includes("qa")) return "qa";
  if (text.includes("ppt") || text.includes("slide") || text.includes("presentation")) return "ppt";
  if (text.includes("mind") || text.includes("mindmap") || text.includes("思维导图")) return "mindmap";
  if (text.includes("transcript")) return "transcript";
  if (text.match(/\.(wav|mp3|m4a|pcm)$/)) return "audio";
  if (text.includes("email") || text.includes("mail")) return "email";
  if (step === "projection_confirmation" || text.includes("projection")) return "projection";
  return "other";
}

function artifactStepLabel(step: string, kind: MeetingArtifact["kind"], type: string): string {
  const byStep: Record<string, string> = {
    realtime_capture: "实时采集",
    import_transcript: "转写",
    minutes: "纪要",
    decisions: "决策",
    action_items: "待办",
    followup: "会后资料",
    reminders: "提醒",
    projection_confirmation: "投影",
  };
  if (step && byStep[step]) return byStep[step];
  const byKind: Record<string, string> = {
    minutes: "纪要",
    actions: "待办",
    decisions: "决策",
    qa: "问答",
    ppt: "PPT",
    mindmap: "思维导图",
    transcript: "转写",
    audio: "录音",
    email: "邮件",
    projection: "投影",
    other: type || "文件",
  };
  return byKind[kind] ?? type;
}

function artifactType(pathValue: string): string {
  const name = pathValue.split("?")[0].split("#")[0];
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "file";
}

function normalizeWorkspaceArtifactPath(pathValue: string): string {
  const normalized = pathValue.trim().replace(/\\/g, "/");
  const workspaceMarker = "/workspace/";
  const workspaceIndex = normalized.lastIndexOf(workspaceMarker);
  if (workspaceIndex >= 0) return normalized.slice(workspaceIndex + workspaceMarker.length);
  const relativeMarker = "lelamp_runtime/workspace/";
  const relativeIndex = normalized.lastIndexOf(relativeMarker);
  if (relativeIndex >= 0) return normalized.slice(relativeIndex + relativeMarker.length);
  if (normalized.startsWith("workspace/")) return normalized.slice("workspace/".length);
  return normalized.replace(/^\.\//, "");
}

function toWorkflowSteps(job: MeetingJob | null): MeetingStep[] {
  if (!job) {
    return meetingStepMeta.map((step, index) => ({
      id: index + 1,
      title: step.title,
      status: index === 0 ? "pending" : "adapter_ready",
      input: "-",
      understanding: "等待真实 API 作业数据",
      result: "未执行",
      confirmation: "需要用户触发",
      outputPath: "-",
      taskId: undefined,
    }));
  }
  const stepByName = new Map(job.steps.map((step) => [step.name, step]));
  return meetingStepMeta.map((step, index) => {
    const apiStep = stepByName.get(step.name);
    return {
      id: index + 1,
      title: step.title,
      status: apiStep?.status ?? "pending",
      input: apiStep?.input_file || job.transcript || "-",
      understanding: apiStep?.system_understanding || "等待步骤执行",
      result: apiStep?.ai_result || "未执行",
      confirmation: apiStep ? "直接生成" : "等待执行",
      outputPath: apiStep?.output_path || "-",
      taskId: apiStep?.task_id,
    };
  });
}

function realtimeTranscriptLines(value: MeetingRealtimeStatus | null): string[] {
  if (!value) return [];
  const lines = Array.isArray(value.transcript)
    ? value.transcript.map((item) => `${item.speaker || "Unknown"}: ${item.text}`).filter(Boolean)
    : [];
  if (value.partial_text) lines.push(`Unknown: ${value.partial_text}`);
  if (!lines.length && value.realtime_transcript) return value.realtime_transcript.split("\n").filter(Boolean);
  return lines.slice(-12);
}

function mergeRealtimeEvents(...groups: Array<Array<Record<string, unknown>>>): Array<Record<string, unknown>> {
  const seen = new Set<string>();
  const merged: Array<{ event: Record<string, unknown>; index: number; time: number }> = [];
  groups.flat().forEach((event, index) => {
    const key = [
      String(event.event ?? event.type ?? "event"),
      String(event.timestamp ?? ""),
      String(event.text ?? event.status ?? ""),
    ].join("|");
    if (seen.has(key)) return;
    seen.add(key);
    const time = Date.parse(String(event.timestamp ?? ""));
    merged.push({ event, index, time: Number.isFinite(time) ? time : 0 });
  });
  return merged
    .sort((left, right) => (right.time - left.time) || (right.index - left.index))
    .slice(0, 60)
    .map((item) => item.event);
}

function realtimeResponseSession(value: MeetingRealtimeStatus): MeetingRealtimeStatus {
  const session = (value.session && typeof value.session === "object" ? value.session : value) as MeetingRealtimeStatus;
  return {
    ...session,
    provider: session.provider ?? value.provider ?? "tongyi_tingwu",
    status: session.status ?? value.status,
    provider_status: value.provider_status ?? session.provider_status,
    openclaw_status: value.openclaw_status ?? session.openclaw_status,
    task_id_web: value.task_id_web ?? session.task_id_web ?? value.task_id,
    provider_task_id: value.provider_task_id ?? session.provider_task_id ?? session.task_id,
    manifest_path: value.manifest_path ?? session.manifest_path,
    job: value.job ?? session.job,
    outputs: value.outputs ?? session.outputs,
    minutes: value.minutes ?? session.minutes,
    followup: value.followup ?? session.followup,
  };
}

function realtimeTaskId(realtime: MeetingRealtimeStatus | null, job: MeetingJob | null): string {
  return String(
    realtime?.task_id_web
      ?? job?.steps.find((step) => step.name === "realtime_capture")?.task_id
      ?? "",
  );
}

function formatSeconds(value?: number) {
  const seconds = Number(value ?? 0);
  if (!Number.isFinite(seconds) || seconds <= 0) return "0.0s";
  return `${seconds.toFixed(seconds >= 10 ? 0 : 1)}s`;
}

function formatNumber(value?: number) {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number)) return "0";
  return String(Math.round(number));
}

function formatPercent(value: unknown): string {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number) || number <= 0) return "0%";
  return `${Math.round(number * 100)}%`;
}

function taskOutputEvents(task: TaskRecord | null): Array<Record<string, unknown>> {
  const output = recordValue(task?.output);
  const events = output?.events;
  return Array.isArray(events) ? events.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
}

function tingwuAgentEvents(value: Record<string, unknown> | null, realtime: MeetingRealtimeStatus | null): Array<Record<string, unknown>> {
  const session = nestedRecord(value, "session");
  const taskPayload =
    recordValue(realtime?.task_payload)
    ?? recordValue(session?.task_payload)
    ?? recordValue(value?.task_payload);
  const agentEvents = taskPayload?.agent_events;
  if (!Array.isArray(agentEvents)) return [];
  return agentEvents.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)).slice(-12).reverse();
}

function formatRealtimeEvent(event: Record<string, unknown>): string {
  const name = String(event.event ?? event.type ?? "event");
  const speaker = String(event.speaker ?? "");
  const text = compactText(event.text);
  const timestamp = String(event.timestamp ?? "-");
  return [name, speaker && speaker !== "Unknown" ? speaker : "", text, timestamp].filter(Boolean).join(" · ");
}

function formatAgentEvent(event: Record<string, unknown>): string {
  const type = String(event.type ?? event.event ?? "agent");
  const agentId = String(event.agent_id ?? "");
  const dataId = String(event.data_id ?? "");
  const text = compactText(event.text);
  return [type, agentId, dataId ? `dataId ${dataId}` : "", text].filter(Boolean).join(" · ");
}

function compactText(value: unknown, maxLength = 80): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

function cleanSpeakerCounts(value?: Record<string, number> | null): Record<string, number> {
  const cleaned: Record<string, number> = {};
  Object.entries(value ?? {}).forEach(([speaker, count]) => {
    const safeSpeaker = safeSpeakerLabel(speaker);
    if (!safeSpeaker) return;
    cleaned[safeSpeaker] = (cleaned[safeSpeaker] ?? 0) + Number(count ?? 0);
  });
  return cleaned;
}

function safeSpeakerLabel(value: unknown): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text || text.length > 40 || text.includes("�") || text.includes("PK\u0003\u0004")) return "";
  if (/[\u0000-\u001f\u007f]/.test(text)) return "";
  if (!/^[\w .#:\-\u4e00-\u9fff]+$/u.test(text)) return "";
  return text || "Unknown";
}

function taskMonitorValue(task: TaskRecord | null, key: "websocket_audio_frames" | "audio_seconds" | "final_count" | "last_status_poll"): unknown {
  const output = recordValue(task?.output);
  const monitor = recordValue(output?.monitor);
  return monitor?.[key] ?? output?.[key];
}

function acceptanceChecklistWithRuntimeStatus(
  checklist: MeetingProviderAcceptanceItem[],
  realtime: MeetingRealtimeStatus | null,
  job: MeetingJob | null,
  task: TaskRecord | null,
  diagnostics: RealtimeDiagnostics,
): MeetingProviderAcceptanceItem[] {
  if (!checklist.length) return [];
  const stepStatus = (name: string) => String(job?.steps.find((step) => step.name === name)?.status ?? "");
  const hasRealtimeMeeting = Boolean(realtime?.meeting_id || job?.meeting_id);
  const realtimeStatus = String(realtime?.status ?? "");
  const frames = Number(taskMonitorValue(task, "websocket_audio_frames") ?? realtime?.websocket_audio_frames ?? 0);
  const audioSeconds = Number(taskMonitorValue(task, "audio_seconds") ?? realtime?.audio_seconds ?? 0);
  const finalCount = Number(taskMonitorValue(task, "final_count") ?? realtime?.final_count ?? 0);
  const transcriptLines = realtimeTranscriptLines(realtime);
  const transcriptSaved = Boolean(realtime?.transcript_path || diagnostics.transcriptPath || stepStatus("realtime_capture") === "completed");
  const stopped = ["stopped", "completed", "failed"].includes(realtimeStatus) || stepStatus("realtime_capture") === "completed";
  const minutesFetched = Boolean(diagnostics.tingwuMinutesPath || diagnostics.tingwuMinutesDataId || stepStatus("minutes") === "completed");
  const followupDone = stepStatus("decisions") === "completed"
    && stepStatus("action_items") === "completed"
    && stepStatus("followup") === "completed"
    && stepStatus("reminders") === "completed"
    && stepStatus("projection_confirmation") === "completed";
  const taskMonitorReady = Boolean(task?.task_id || frames > 0 || audioSeconds > 0);
  const diagnosticsReady = Boolean(diagnostics.tingwuHttpActions || diagnostics.manifestPath || diagnostics.tingwuChainStatus === "completed");

  const statusById: Record<string, string> = {
    import_transcript: stepStatus("import_transcript") === "completed" ? "completed" : job ? "pending" : "ready",
    live_realtime_create_task: diagnostics.tingwuRealtimeDataId
      ? "completed"
      : hasRealtimeMeeting
        ? (["starting", "running", "stopping", "finalizing"].includes(realtimeStatus) ? "running" : "pending")
        : "pending",
    websocket_pcm_streaming: frames > 0 && audioSeconds > 0 ? "completed" : hasRealtimeMeeting ? "running" : "pending",
    realtime_transcript: (transcriptSaved && (finalCount > 0 || transcriptLines.length > 0)) ? "completed" : hasRealtimeMeeting ? "running" : "pending",
    stop_then_fetch_minutes: minutesFetched ? "completed" : stopped ? "ready" : hasRealtimeMeeting ? "blocked" : "pending",
    openclaw_followup_outputs: followupDone ? "completed" : minutesFetched ? "ready" : "pending",
    ui_task_assistant_audit: taskMonitorReady && diagnosticsReady ? "completed" : hasRealtimeMeeting || job ? "running" : "pending",
  };

  return checklist.map((item) => ({
    ...item,
    status: statusById[item.id] ?? item.status,
  }));
}

function realtimeResultMessage(value: Record<string, unknown>, action: "stop" | "fetch"): string {
  const minutes = nestedRecord(value, "minutes");
  const providerStatus = String(value.provider_status ?? minutes?.provider_status ?? value.status ?? "");
  const openclawStatus = String(value.openclaw_status ?? minutes?.openclaw_status ?? "");
  const contentStatus = String(value.content_status ?? minutes?.content_status ?? "");
  const providerOk = providerStatus === "completed";
  const openclawOk = openclawStatus === "completed";
  if (openclawOk && contentStatus === "no_speech_detected") {
    return "会议音频和诊断已保存；本次没有识别到可用发言，OpenClaw 已生成空会议诊断纪要，没有伪造决策或行动项。";
  }
  if (action === "stop" && providerStatus === "stopped" && openclawOk) {
    return "实时会议已停止，转写和音频已保存；可以继续拉取通义听悟 AI 纪要。";
  }
  if (providerOk && openclawOk) {
    return action === "fetch"
      ? "通义听悟 AI 纪要已拉取，OpenClaw 后处理已保存。"
      : "实时会议已停止，通义听悟纪要和 OpenClaw 后处理已保存。";
  }
  if (!providerOk && openclawOk) {
    return action === "fetch"
      ? "通义听悟 AI 纪要未完成；已保存转写和诊断结果，并用 OpenClaw 生成了后处理输出。"
      : "实时会议已停止，但通义听悟 AI 纪要未完成；已保存转写和诊断结果，并用 OpenClaw 生成了后处理输出。";
  }
  if (providerOk && !openclawOk) {
    return action === "fetch"
      ? "通义听悟 AI 纪要已拉取，但 OpenClaw 后处理未完成，请查看任务和审计日志。"
      : "实时会议已停止，通义听悟纪要已保存，但 OpenClaw 后处理未完成，请查看任务和审计日志。";
  }
  return action === "fetch"
    ? "通义听悟 AI 纪要拉取和 OpenClaw 后处理均未完成，请查看任务和审计日志。"
    : "实时会议已停止，但通义听悟 AI 纪要和 OpenClaw 后处理均未完成，请查看任务和审计日志。";
}

function realtimeDiagnostics(value: Record<string, unknown> | null, realtime: MeetingRealtimeStatus | null) {
  const session = nestedRecord(value, "session");
  const minutes = nestedRecord(value, "minutes");
  const base = value ?? {};
  const tingwuChain = tingwuHttpChain(base, session, realtime);
  const providerStatus = String(base.provider_status ?? minutes?.provider_status ?? session?.status ?? realtime?.provider_status ?? realtime?.status ?? "idle");
  const openclawStatus = String(base.openclaw_status ?? minutes?.openclaw_status ?? realtime?.openclaw_status ?? "unknown");
  const contentStatus = String(base.content_status ?? minutes?.content_status ?? "");
  const providerError = String(session?.error ?? base.provider_error ?? minutes?.provider_error ?? realtime?.provider_error ?? realtime?.error ?? "");
  const openclawError = String(minutes?.error ?? base.openclaw_error ?? realtime?.openclaw_error ?? "");
  const status = providerStatus === "completed" && openclawStatus === "completed"
    ? "completed"
    : providerStatus === "idle"
      ? "adapter_ready"
      : (providerError || openclawError || providerStatus === "failed" || openclawStatus === "failed" ? "failed" : providerStatus);
  return {
    status,
    providerStatus,
    openclawStatus,
    contentStatus,
    providerError,
    openclawError,
    manifestPath: String(base.manifest_path ?? realtime?.manifest_path ?? ""),
    transcriptPath: String(session?.transcript_path ?? base.transcript_path ?? realtime?.transcript_path ?? ""),
    audioPath: String(session?.audio_path ?? base.audio_path ?? realtime?.audio_path ?? ""),
    tingwuMinutesPath: String(session?.minutes_path ?? base.tingwu_minutes_path ?? minutes?.tingwu_minutes_path ?? realtime?.minutes_path ?? ""),
    openclawMinutesPath: String(minutes?.path ?? base.openclaw_minutes_path ?? ""),
    tingwuHttpActions: tingwuHttpActionLabel(base, session, realtime),
    tingwuChainStatus: tingwuChain.status,
    tingwuChainLabel: tingwuChain.label,
    tingwuRealtimeDataId: tingwuChain.realtimeDataId,
    tingwuMinutesDataId: tingwuChain.minutesDataId,
    tingwuLastDataId: tingwuLastDataId(base, session, realtime),
  };
}

type TingwuHttpOperationSource = Pick<MeetingRealtimeStatus, "tingwu_http_operations"> | Record<string, unknown> | null;

function tingwuHttpOperations(...values: TingwuHttpOperationSource[]): Array<Record<string, unknown>> {
  for (const value of values) {
    const operations = value?.tingwu_http_operations;
    if (Array.isArray(operations)) {
      return operations.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item));
    }
  }
  return [];
}

function tingwuHttpActionLabel(base: Record<string, unknown>, session: Record<string, unknown> | null, realtime: MeetingRealtimeStatus | null): string {
  const actions = tingwuHttpOperations(base, session, realtime).map((item) => String(item.action ?? "")).filter(Boolean);
  return Array.from(new Set(actions)).join(" -> ");
}

function tingwuLastDataId(base: Record<string, unknown>, session: Record<string, unknown> | null, realtime: MeetingRealtimeStatus | null): string {
  const operations = tingwuHttpOperations(base, session, realtime);
  const latest = [...operations].reverse().find((item) => item.response_data_id || item.request_data_id);
  return String(latest?.response_data_id ?? latest?.request_data_id ?? "");
}

function tingwuHttpChain(base: Record<string, unknown>, session: Record<string, unknown> | null, realtime: MeetingRealtimeStatus | null) {
  const operations = tingwuHttpOperations(base, session, realtime);
  const create = operations.find((item) => item.action === "CreateTask" && item.request_type === "realtime" && item.response_data_id);
  const realtimeDataId = String(create?.response_data_id ?? "");
  const minutesCreate = operations.find((item) => item.action === "CreateRealtimeMinutesTask" && item.request_data_id === realtimeDataId && item.response_data_id);
  const minutesDataId = String(minutesCreate?.response_data_id ?? "");
  const getTask = operations.find((item) => item.action === "GetTask" && item.request_data_id === minutesDataId);
  if (realtimeDataId && minutesDataId && getTask) {
    return { status: "completed", label: "CreateTask -> CreateRealtimeMinutesTask -> GetTask", realtimeDataId, minutesDataId };
  }
  if (realtimeDataId) {
    return { status: "running", label: "CreateTask 已创建", realtimeDataId, minutesDataId };
  }
  return { status: operations.length ? "warning" : "pending", label: operations.length ? "链路不完整" : "等待 CreateTask", realtimeDataId, minutesDataId };
}

function listValue(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => String(item));
  return [];
}

function nestedRecord(value: Record<string, unknown> | null, key: string): Record<string, unknown> | null {
  const item = value?.[key];
  return item && typeof item === "object" && !Array.isArray(item) ? item as Record<string, unknown> : null;
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function nonEmptyString(value: unknown): string {
  return typeof value === "string" && value.trim() ? value : "";
}

function meetingRealtimeStartFailureResult(error: unknown): Record<string, unknown> | null {
  if (!(error instanceof ApiClientError)) return null;
  return {
    status: "failed",
    code: error.code,
    message: error.message,
    details: error.details,
  };
}

function meetingRealtimeStartFailureProvider(error: unknown): MeetingProviderStatus["providers"]["tongyi_tingwu"] | null {
  if (!(error instanceof ApiClientError)) return null;
  const provider = recordValue(error.details.provider);
  if (!provider) return null;
  return provider as unknown as MeetingProviderStatus["providers"]["tongyi_tingwu"];
}

function meetingRealtimeStartErrorMessage(error: unknown): string {
  const fallback = apiErrorMessage(error);
  if (!(error instanceof ApiClientError)) return fallback;
  const diagnostics = formatCaptureProbeDiagnostics(error.details);
  return [error.message || fallback, diagnostics].filter(Boolean).join("；");
}

function formatCaptureProbeDiagnostics(details: Record<string, unknown>): string {
  const provider = recordValue(details.provider);
  const micProbe = recordValue(details.mic_probe) ?? recordValue(provider?.mic_probe);
  const captureProbe = recordValue(details.capture_probe) ?? recordValue(provider?.capture_probe) ?? recordValue(micProbe?.capture_probe);
  const selectedMic = nonEmptyString(provider?.selected_mic_device) || nonEmptyString(micProbe?.selected_device) || nonEmptyString(provider?.mic_device);
  const probeMessage = nonEmptyString(captureProbe?.message) || nonEmptyString(micProbe?.message) || nonEmptyString(provider?.message);
  const parts = [
    selectedMic ? `麦克风 ${selectedMic}` : "",
    nonEmptyString(captureProbe?.status) ? `采集状态 ${String(captureProbe?.status)}` : "",
    hasOwn(captureProbe, "audio_bytes") ? `audio_bytes ${String(captureProbe?.audio_bytes)}` : "",
    hasOwn(captureProbe, "audio_rms") ? `audio_rms ${String(captureProbe?.audio_rms)}` : "",
    hasOwn(captureProbe, "audio_peak") ? `audio_peak ${String(captureProbe?.audio_peak)}` : "",
    probeMessage,
  ].filter(Boolean);
  return parts.join("，");
}

function hasOwn(value: Record<string, unknown> | null, key: string): boolean {
  return Boolean(value && Object.prototype.hasOwnProperty.call(value, key));
}

function preflightCheckStatus(preflight: MeetingProviderPreflight | null, key: string): string {
  if (!preflight) return "pending";
  return preflight.checks?.[key] === true ? "completed" : "blocked";
}

function preflightCredentialLabel(preflight: MeetingProviderPreflight | null): string {
  if (!preflight) return "-";
  const diagnostics = preflight.credential_diagnostics ?? preflight.provider_status?.credential_diagnostics ?? {};
  if (diagnostics.api_key_kind === "aliyun_access_key_id") return "误填 AccessKey ID";
  if (diagnostics.app_id_kind === "legacy_tingwu_appkey") return "误填 AppKey";
  if (diagnostics.app_id_kind === "unexpected_app_id_shape") return "App ID 不是 tw_ 形状";
  const apiKey = preflight.checks?.tingwu_api_key_configured === true;
  const appId = preflight.checks?.tingwu_app_id_configured === true;
  if (apiKey && appId) return "已配置";
  if (!apiKey && !appId) return "缺 API key / App ID";
  return apiKey ? "缺 App ID" : "缺 API key";
}

function providerPreflightCaptureSummary(preflight: MeetingProviderPreflight | null): string {
  if (!preflight) return "-";
  const probe = preflight.capture_probe ?? {};
  const parts = [
    `status=${String(probe.status ?? "-")}`,
    `audio_bytes=${String(probe.audio_bytes ?? 0)}`,
    `rms=${String(probe.audio_rms ?? 0)}`,
    `peak=${String(probe.audio_peak ?? 0)}`,
  ];
  return parts.join(" · ");
}

function providerPreflightChecks(preflight: MeetingProviderPreflight | null): Array<{ key: string; label: string; ok: boolean }> {
  const specs = [
    ["tingwu_api_key_configured", "API Key"],
    ["tingwu_app_id_configured", "App ID"],
    ["official_tingwu_endpoint", "官方端点"],
    ["microphone_available", "麦克风可用"],
    ["real_microphone_device", "真实设备"],
    ["microphone_capture_device_matches", "设备一致"],
    ["microphone_capture_open", "采集打开"],
    ["microphone_capture_signal", "采集有声"],
  ];
  return specs.map(([key, label]) => ({
    key,
    label,
    ok: preflight?.checks?.[key] === true,
  }));
}

function preflightRecommendation(preflight: MeetingProviderPreflight | null): string {
  if (!preflight) return "下一步：先运行本地预检，再开始实时会议。";
  const diagnostics = preflight.credential_diagnostics ?? preflight.provider_status?.credential_diagnostics ?? {};
  if (diagnostics.api_key_kind === "aliyun_access_key_id") {
    return "下一步：不要使用 RAM AccessKey ID；请在百炼生成新的 DashScope API Key，填入 .env.tingwu.local。";
  }
  if (diagnostics.app_id_kind === "legacy_tingwu_appkey") {
    return "下一步：不要使用老版听悟 OpenAPI AppKey；请从百炼应用配置页复制 App ID。";
  }
  if (diagnostics.app_id_kind === "unexpected_app_id_shape") {
    return "下一步：TINGWU_APP_ID 需要使用百炼应用 App ID，通常是 tw_ 开头；不要填旧 AppKey。";
  }
  const backendMessage = preflightPrimaryNextAction(preflight)?.message;
  if (backendMessage) return `下一步：${backendMessage}`;
  const checks = preflight.checks ?? {};
  if (checks.tingwu_api_key_configured !== true || checks.tingwu_app_id_configured !== true) {
    return "下一步：复制 .env.tingwu.example 为 .env.tingwu.local，填入新 Key 和百炼应用 App ID。";
  }
  if (checks.official_tingwu_endpoint !== true) {
    return "下一步：恢复官方 DashScope HTTP/WS 端点后再验收。";
  }
  if (checks.real_microphone_device !== true || checks.microphone_available !== true) {
    return "下一步：选择真实 USB/ALSA 麦克风，避免 default/pulse/mock/fake 设备。";
  }
  if (checks.microphone_capture_device_matches !== true) {
    return "下一步：确认预检采集设备和选中的 ALSA 设备一致。";
  }
  if (checks.microphone_capture_open !== true || checks.microphone_capture_signal !== true) {
    return "下一步：靠近麦克风说话，确认 arecord 能打开设备并采到非静音 PCM。";
  }
  return "下一步：开始实时会议，口播“乐灯听悟验收测试”，停止后拉取 AI 纪要。";
}

function preflightPrimaryNextAction(preflight: MeetingProviderPreflight | null) {
  return preflight?.next_actions?.find((item) => typeof item.message === "string" && item.message.trim());
}

function formatShellCommand(command: string[] | undefined): string {
  if (!command?.length) return "";
  return command.map(shellQuote).join(" ");
}

function formatRunnableCommand(command: string[] | undefined, env?: Record<string, string>, cwd?: string): string {
  const shellCommand = formatShellCommand(command);
  if (!shellCommand) return "";
  const envPrefix = env ? Object.entries(env).map(([key, value]) => `${key}=${shellQuote(value)}`).join(" ") : "";
  const commandWithEnv = [envPrefix, shellCommand].filter(Boolean).join(" ");
  return cwd ? `cd ${shellQuote(cwd)} && ${commandWithEnv}` : commandWithEnv;
}

function shellQuote(value: string): string {
  if (/^[A-Za-z0-9_./:=@+-]+$/.test(value)) return value;
  return `'${value.replace(/'/g, "'\\''")}'`;
}

function meetingResultSummary(value: Record<string, unknown> | null): string {
  if (!value) return "";
  const minutes = nestedRecord(value, "minutes");
  const session = nestedRecord(value, "session");
  const aiMinutes = nestedRecord(session, "ai_minutes") ?? nestedRecord(minutes, "ai_minutes");
  const tingwuMinutes = nestedRecord(minutes, "tingwu_minutes") ?? nestedRecord(session, "tingwu_minutes");
  const tingwuSummary = structuredTingwuSummary(tingwuMinutes);
  return String(
    tingwuSummary
      || nonEmptyString(aiMinutes?.summary)
      || nonEmptyString(minutes?.summary)
      || nonEmptyString(value.summary)
      || nonEmptyString(value.path)
      || nonEmptyString(minutes?.path)
      || nonEmptyString(session?.minutes_path),
  );
}

function structuredTingwuSummary(tingwuMinutes: Record<string, unknown> | null): string {
  if (!tingwuMinutes || tingwuMinutes.structured_summary !== true || tingwuMinutes.summary_source === "raw_payload") return "";
  return nonEmptyString(tingwuMinutes.summary);
}

function meetingResultItems(value: Record<string, unknown> | null, key: "decisions" | "action_items"): string[] {
  if (!value) return [];
  const minutes = nestedRecord(value, "minutes");
  const session = nestedRecord(value, "session");
  const aiMinutes = nestedRecord(session, "ai_minutes") ?? nestedRecord(minutes, "ai_minutes");
  const tingwuMinutes = nestedRecord(minutes, "tingwu_minutes") ?? nestedRecord(session, "tingwu_minutes");
  const normalized = listValue(value[key]);
  if (normalized.length) return normalized;
  const fromMinutes = listValue(minutes?.[key]);
  if (fromMinutes.length) return fromMinutes;
  const fromTingwuMinutes = listValue(tingwuMinutes?.[key]);
  if (fromTingwuMinutes.length) return fromTingwuMinutes;
  const fromAi = listValue(aiMinutes?.[key]);
  if (fromAi.length) return fromAi;
  return [];
}

function tingwuMicStatus(providerStatus: MeetingProviderStatus | null): string {
  const provider = providerStatus?.providers.tongyi_tingwu;
  if (!provider) return "unavailable";
  const status = String(provider.mic_status ?? "");
  if (status === "mock") return "adapter_ready";
  return status || (provider.mock ? "adapter_ready" : "unavailable");
}

function tingwuMicMessage(providerStatus: MeetingProviderStatus | null): string {
  const provider = providerStatus?.providers.tongyi_tingwu;
  if (!provider) return "-";
  const probeMessage = typeof provider.mic_probe?.message === "string" ? provider.mic_probe.message : "";
  return provider.message || probeMessage || "-";
}

function outputPaths(value: Record<string, unknown> | null): string[] {
  if (!value) return [];
  const paths: string[] = [];
  const visit = (item: unknown, key = "") => {
    if (typeof item === "string") {
      if (key.endsWith("path")) paths.push(item);
      return;
    }
    if (Array.isArray(item)) {
      item.forEach((entry) => visit(entry));
      return;
    }
    if (item && typeof item === "object") {
      Object.entries(item as Record<string, unknown>).forEach(([entryKey, entryValue]) => {
        if (entryKey === "path" && typeof entryValue === "string") {
          paths.push(entryValue);
        } else {
          visit(entryValue, entryKey);
        }
      });
    }
  };
  visit(value);
  return paths.filter((item, index, items) => item && items.indexOf(item) === index);
}

function emailDraftPath(value: Record<string, unknown> | null): string {
  if (!value) return "";
  const email = recordValue(value.email);
  return String(value.email_draft_path ?? email?.email_draft_path ?? "");
}

function emailProvider(value: Record<string, unknown> | null): string {
  if (!value) return "";
  const email = recordValue(value.email);
  return String(email?.provider ?? value.provider ?? "");
}

function exportPackagePath(value: Record<string, unknown> | null): string {
  if (!value) return "";
  const exportPackage = recordValue(value.export_package) ?? recordValue(value.shared_file);
  const sharedFile = recordValue(value.shared_file) ?? recordValue(exportPackage?.shared_file);
  return String(value.download_url ?? exportPackage?.download_url ?? sharedFile?.relative_path ?? value.path ?? "");
}

function emailSendStatus(value: Record<string, unknown> | null): string {
  if (!value) return "";
  const emailSend = recordValue(value.email_send);
  const smtp = recordValue(value.smtp) ?? recordValue(emailSend?.smtp);
  return String(value.status ?? emailSend?.status ?? smtp?.status ?? "");
}
