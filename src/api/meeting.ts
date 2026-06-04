import { request, requestWithMock } from "./client";
import type {
  ApiResult,
  MeetingJob,
  MeetingJobsResponse,
  MeetingTextImportResponse,
  MeetingLocalRealtimeResponse,
  MeetingModeStatus,
  MeetingProviderPreflight,
  MeetingProviderStatus,
  MeetingRealtimeEventsResponse,
  MeetingRealtimeStatus,
} from "./types";

export async function importTranscript(filePath: string, title?: string): Promise<ApiResult<MeetingJob>> {
  const data = await request<MeetingJob>("/api/meeting/import-transcript", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath, title }),
  });
  return { data, source: "api" };
}

export async function getMeetingStatus(): Promise<ApiResult<MeetingModeStatus>> {
  const data = await request<MeetingModeStatus>("/api/meeting/status");
  return { data, source: "api" };
}

export async function enableMeetingMode(title: string, participants: string[] = []): Promise<ApiResult<MeetingModeStatus>> {
  const data = await request<MeetingModeStatus>("/api/meeting/mode/enable", {
    method: "POST",
    body: JSON.stringify({ title, participants }),
  });
  return { data, source: "api" };
}

export async function disableMeetingMode(): Promise<ApiResult<MeetingModeStatus>> {
  const data = await request<MeetingModeStatus>("/api/meeting/mode/disable", {
    method: "POST",
    body: "{}",
  });
  return { data, source: "api" };
}

export async function getMeetingLocalRealtimeStatus(): Promise<ApiResult<MeetingLocalRealtimeResponse>> {
  const data = await request<MeetingLocalRealtimeResponse>("/api/meeting/local-realtime/status");
  return { data, source: "api" };
}

export async function appendMeetingLocalRealtimeTurn(payload: {
  speaker: string;
  text: string;
  source?: string;
}): Promise<ApiResult<MeetingLocalRealtimeResponse>> {
  const data = await request<MeetingLocalRealtimeResponse>("/api/meeting/local-realtime/turn", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function exportMeetingLocalRealtimeTranscript(): Promise<ApiResult<MeetingLocalRealtimeResponse>> {
  const data = await request<MeetingLocalRealtimeResponse>("/api/meeting/local-realtime/export", {
    method: "POST",
    body: JSON.stringify({ source: "web_console" }),
  });
  return { data, source: "api" };
}

export async function runMeetingMinutes(transcript: string): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/meeting/minutes", {
    method: "POST",
    body: JSON.stringify({ transcript, title: transcript.split("/").pop() ?? "Meeting", participants: ["Unknown"] }),
  });
  return { data, source: "api" };
}

export async function runMeetingFollowup(
  transcript: string,
  options: {
    recipient?: string;
    create_reminders?: boolean;
    render_projection?: boolean;
  } = {},
): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/meeting/followup", {
    method: "POST",
    body: JSON.stringify({
      transcript,
      title: transcript.split("/").pop() ?? "Meeting",
      participants: ["Unknown"],
      recipient: options.recipient ?? "待填写收件人",
      create_reminders: options.create_reminders ?? true,
      render_projection: options.render_projection ?? true,
    }),
  });
  return { data, source: "api" };
}

export async function exportMeetingPackage(
  transcript: string,
  options: {
    recipient?: string;
    authorized: boolean;
    create_reminders?: boolean;
    render_projection?: boolean;
  },
): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/meeting/export-package", {
    method: "POST",
    body: JSON.stringify({
      transcript,
      title: transcript.split("/").pop() ?? "Meeting",
      participants: ["Unknown"],
      recipient: options.recipient ?? "待填写收件人",
      authorized: options.authorized,
      create_reminders: options.create_reminders ?? true,
      render_projection: options.render_projection ?? true,
    }),
  });
  return { data, source: "api" };
}

export async function sendMeetingEmail(
  transcript: string,
  options: {
    recipient: string;
    authorized: boolean;
  },
): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/meeting/send-email", {
    method: "POST",
    body: JSON.stringify({
      transcript,
      title: transcript.split("/").pop() ?? "Meeting",
      participants: ["Unknown"],
      recipient: options.recipient,
      authorized: options.authorized,
    }),
  });
  return { data, source: "api" };
}

export async function runMeetingStep(
  endpoint: "decisions" | "action-items" | "reminders" | "projection-confirmation",
  payload: Record<string, unknown>,
): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>(`/api/meeting/${endpoint}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function confirmMeetingStep(taskId: string, note = "用户已确认会议步骤。"): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/meeting/confirm-step", {
    method: "POST",
    body: JSON.stringify({ task_id: taskId, note }),
  });
  return { data, source: "api" };
}

export function getMeetingJobs(): Promise<ApiResult<MeetingJobsResponse>> {
  return requestWithMock<MeetingJobsResponse>("/api/meeting/jobs", { items: [], total: 0 });
}

export function getMeetingProviderStatus(): Promise<ApiResult<MeetingProviderStatus>> {
  return requestWithMock<MeetingProviderStatus>("/api/meeting/provider/status", {
    status: "needs_config",
    primary_provider: "tongyi_tingwu",
    providers: {
      tongyi_tingwu: {
        provider: "tongyi_tingwu",
        status: "needs_config",
        configured: false,
        mock: false,
        api_key_configured: false,
        app_id_configured: false,
        http_url: "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        ws_url: "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        mic_device: "auto",
        sample_rate: 16000,
        audio_format: "pcm",
        language_hints: ["cn", "en"],
        active_meeting_id: null,
        active_count: 0,
      },
    },
  });
}

export async function runMeetingProviderPreflight(captureSeconds = 1): Promise<ApiResult<MeetingProviderPreflight>> {
  const data = await request<MeetingProviderPreflight>("/api/meeting/provider/preflight", {
    method: "POST",
    body: JSON.stringify({ capture_seconds: captureSeconds }),
  });
  return { data, source: "api" };
}

export function getMeetingRealtimeStatus(meetingId?: string): Promise<ApiResult<MeetingRealtimeStatus>> {
  const suffix = meetingId ? `?meeting_id=${encodeURIComponent(meetingId)}` : "";
  return requestWithMock<MeetingRealtimeStatus>(`/api/meeting/realtime/status${suffix}`, {
    provider: "tongyi_tingwu",
    status: "idle",
    active_meeting_id: null,
  } as MeetingRealtimeStatus);
}

export async function startMeetingRealtime(payload: {
  title: string;
  participants?: string[];
  max_seconds?: number;
}): Promise<ApiResult<MeetingRealtimeStatus>> {
  const data = await request<MeetingRealtimeStatus>("/api/meeting/realtime/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function stopMeetingRealtime(meetingId?: string, runFollowup = true): Promise<ApiResult<MeetingRealtimeStatus>> {
  const data = await request<MeetingRealtimeStatus>("/api/meeting/realtime/stop", {
    method: "POST",
    body: JSON.stringify({ meeting_id: meetingId, run_followup: runFollowup }),
  });
  return { data, source: "api" };
}

export async function fetchMeetingRealtimeMinutes(meetingId: string, runFollowup = true): Promise<ApiResult<MeetingRealtimeStatus>> {
  const data = await request<MeetingRealtimeStatus>("/api/meeting/realtime/fetch-minutes", {
    method: "POST",
    body: JSON.stringify({ meeting_id: meetingId, run_followup: runFollowup }),
  });
  return { data, source: "api" };
}

export function getMeetingRealtimeEvents(meetingId: string): Promise<ApiResult<MeetingRealtimeEventsResponse>> {
  return requestWithMock<MeetingRealtimeEventsResponse>(`/api/meeting/realtime/events?meeting_id=${encodeURIComponent(meetingId)}`, {
    status: "ok",
    meeting_id: meetingId,
    events: [],
    total: 0,
  });
}

export async function importMeetingText(
  text: string,
  title = "meeting_text",
  participants: string[] = ["Unknown"],
): Promise<ApiResult<MeetingTextImportResponse>> {
  const data = await request<MeetingTextImportResponse>("/api/meeting/import-text", {
    method: "POST",
    body: JSON.stringify({ title, text, participants }),
  });
  return { data, source: "api" };
}
