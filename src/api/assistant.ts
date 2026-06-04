import { request, requestWithMock } from "./client";
import type {
  ApiResult,
  AssistantManualResponse,
  AssistantMessageResponse,
  AssistantNotificationsResponse,
  AssistantPiVoiceResponse,
  AssistantProviderStatus,
  CameraStreamStatus,
  VoiceCaptureResponse,
  VoiceConversationResponse,
  VoiceStatus,
} from "./types";

const providerStatusFallback: AssistantProviderStatus = {
  foreground_provider: "local_frontend",
  qwen_omni: {
    status: "backend_missing",
    model: "unavailable",
    text_input: true,
    pi_mic_input: false,
    pi_mic_status: "backend_missing",
    browser_mic_input: false,
    transcription_model: "unavailable",
    voice: "unavailable",
    tts: {
      provider: "unavailable",
      status: "backend_missing",
    },
  },
  openclaw: {
    status: "backend_missing",
  },
  input: {
    text: "available",
    pi_mic: "backend_missing",
    browser_mic: "backend_missing",
  },
};

export async function postAssistantManual(
  message: string,
  context: Record<string, unknown> = {},
): Promise<ApiResult<AssistantManualResponse>> {
  const data = await request<AssistantManualResponse>("/api/assistant/manual", {
    method: "POST",
    body: JSON.stringify({ message, context }),
  });
  return { data, source: "api" };
}

export async function postAssistantMessage(
  text: string,
  context: Record<string, unknown> = {},
  options: { sessionId?: string; page?: string; inputType?: "text" | "pi_voice" | "browser_voice"; speak?: boolean } = {},
): Promise<ApiResult<AssistantMessageResponse>> {
  const data = await request<AssistantMessageResponse>("/api/assistant/message", {
    method: "POST",
    body: JSON.stringify({
      session_id: options.sessionId,
      input_type: options.inputType ?? "text",
      text,
      page: options.page ?? "assistant",
      context,
      speak: options.speak ?? true,
    }),
  });
  return { data, source: "api" };
}

export function getAssistantProvidersStatus(): Promise<ApiResult<AssistantProviderStatus>> {
  return requestWithMock<AssistantProviderStatus>("/api/assistant/providers/status", providerStatusFallback);
}

export function getAssistantNotifications(since = ""): Promise<ApiResult<AssistantNotificationsResponse>> {
  const suffix = since ? `?since=${encodeURIComponent(since)}` : "";
  return requestWithMock<AssistantNotificationsResponse>(`/api/assistant/notifications${suffix}`, {
    status: "ok",
    items: [],
    total: 0,
  });
}

const cameraStreamFallback: CameraStreamStatus = {
  status: "stopped",
  preview_url: "",
  snapshot_url: "",
  stream_url: "",
  camera_index: 0,
  always_on: false,
  details: {},
  message: "Camera preview is stopped.",
};

export function getCameraStreamStatus(): Promise<ApiResult<CameraStreamStatus>> {
  return requestWithMock<CameraStreamStatus>("/api/camera-stream/status", cameraStreamFallback);
}

export async function startCameraStream(options: {
  camera_index?: number;
  width?: number;
  height?: number;
  backend?: string;
} = {}): Promise<ApiResult<CameraStreamStatus>> {
  const data = await request<CameraStreamStatus>("/api/camera-stream/start", {
    method: "POST",
    body: JSON.stringify(options),
  });
  return { data, source: "api" };
}

export async function stopCameraStream(): Promise<ApiResult<CameraStreamStatus>> {
  const data = await request<CameraStreamStatus>("/api/camera-stream/stop", {
    method: "POST",
    body: JSON.stringify({}),
  });
  return { data, source: "api" };
}

export async function postAssistantPiVoiceOnce(options: { seconds?: number; page?: string; speak?: boolean } = {}): Promise<ApiResult<AssistantPiVoiceResponse>> {
  const data = await request<AssistantPiVoiceResponse>("/api/assistant/pi-voice-once", {
    method: "POST",
    body: JSON.stringify({
      seconds: options.seconds ?? 4,
      page: options.page ?? "assistant",
      speak: options.speak ?? true,
    }),
  });
  return { data, source: "api" };
}

export async function getVoiceStatus(): Promise<ApiResult<VoiceStatus>> {
  const data = await request<VoiceStatus>("/api/voice/status");
  return { data, source: "api" };
}

export async function postVoiceCaptureOnce(options: { seconds?: number; authorized?: boolean; speak?: boolean } = {}): Promise<ApiResult<VoiceCaptureResponse>> {
  const data = await request<VoiceCaptureResponse>("/api/voice/capture-once", {
    method: "POST",
    body: JSON.stringify({
      seconds: options.seconds ?? 4,
      authorized: options.authorized ?? false,
      speak: options.speak ?? false,
      page: "voice",
    }),
  });
  return { data, source: "api" };
}

export async function startVoiceConversation(options: { authorized: boolean; wakeWord?: string }): Promise<ApiResult<VoiceConversationResponse>> {
  const data = await request<VoiceConversationResponse>("/api/voice/conversation/start", {
    method: "POST",
    body: JSON.stringify({ authorized: options.authorized, wake_word: options.wakeWord ?? "小灯" }),
  });
  return { data, source: "api" };
}

export async function sendVoiceConversationTurn(options: {
  sessionId: string;
  text: string;
  wakeRequired?: boolean;
  remember?: boolean;
  speak?: boolean;
}): Promise<ApiResult<VoiceConversationResponse>> {
  const data = await request<VoiceConversationResponse>("/api/voice/conversation/turn", {
    method: "POST",
    body: JSON.stringify({
      session_id: options.sessionId,
      text: options.text,
      wake_required: options.wakeRequired ?? true,
      remember: options.remember ?? false,
      speak: options.speak ?? false,
    }),
  });
  return { data, source: "api" };
}

export async function stopVoiceConversation(sessionId: string): Promise<ApiResult<VoiceConversationResponse>> {
  const data = await request<VoiceConversationResponse>("/api/voice/conversation/stop", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
  return { data, source: "api" };
}

export async function confirmAssistant(confirmationId: string): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/assistant/confirm", {
    method: "POST",
    body: JSON.stringify({ confirmation_id: confirmationId }),
  });
  return { data, source: "api" };
}

export async function rejectAssistant(confirmationId: string): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/assistant/reject", {
    method: "POST",
    body: JSON.stringify({ confirmation_id: confirmationId }),
  });
  return { data, source: "api" };
}

export function getAssistantFallback(): Promise<ApiResult<AssistantManualResponse>> {
  return requestWithMock<AssistantManualResponse>("/api/assistant/manual", {
    message_id: "mock",
    detected_intent: "mock",
    skills_to_call: [],
    requires_confirmation: false,
    confirmation: null,
    result: {
      status: "adapter_ready",
      summary: "VITE_USE_MOCK_API=true 时的开发占位回复。",
      outputs: [],
    },
    task_id: "mock",
  });
}
