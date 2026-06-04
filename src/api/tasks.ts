import { request, requestWithMock } from "./client";
import type {
  ApiResult,
  BrowserAutomationResult,
  BrowserAutomationStatus,
  DesktopCompanionRunResponse,
  DesktopCompanionStatus,
  DesktopControlActionResult,
  DesktopTask,
  DesktopTasksResponse,
  DesktopWorkflowResult,
  DesktopWorkflowStatus,
  TaskEventsResponse,
  TaskRecord,
  TasksResponse,
} from "./types";

export function getRecentTasks(limit = 20): Promise<ApiResult<TasksResponse>> {
  return requestWithMock<TasksResponse>(`/api/tasks/recent?limit=${limit}`, { items: [], tasks: [], total: 0 });
}

export async function getTask(taskId: string): Promise<ApiResult<TaskRecord>> {
  const data = await request<TaskRecord>(`/api/tasks/${encodeURIComponent(taskId)}`);
  return { data, source: "api" };
}

export async function getTaskEvents(taskId: string): Promise<ApiResult<TaskEventsResponse>> {
  const data = await request<TaskEventsResponse>(`/api/tasks/${encodeURIComponent(taskId)}/events`);
  return { data, source: "api" };
}

export async function cancelTask(taskId: string): Promise<ApiResult<TaskRecord>> {
  const data = await request<TaskRecord>(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST", body: "{}" });
  return { data, source: "api" };
}

export async function getDesktopTasks(limit = 50): Promise<ApiResult<DesktopTasksResponse>> {
  const data = await request<DesktopTasksResponse>(`/api/desktop/tasks?limit=${limit}`);
  return { data, source: "api" };
}

export async function requestDesktopTask(goal: string, steps: string[]): Promise<ApiResult<DesktopTask>> {
  const data = await request<DesktopTask>("/api/desktop/task/request", {
    method: "POST",
    body: JSON.stringify({ goal, steps, requires_full_control: false }),
  });
  return { data, source: "api" };
}

export async function updateDesktopTaskStatus(taskId: string, status: string, reason = ""): Promise<ApiResult<DesktopTask>> {
  const data = await request<DesktopTask>("/api/desktop/task/status", {
    method: "POST",
    body: JSON.stringify({ task_id: taskId, status, reason }),
  });
  return { data, source: "api" };
}

export async function getBrowserAutomationStatus(): Promise<ApiResult<BrowserAutomationStatus>> {
  const data = await request<BrowserAutomationStatus>("/api/desktop/automation/status");
  return { data, source: "api" };
}

export async function executeBrowserTask(
  taskId: string,
  options: { authorized: boolean; headless?: boolean; allowedHosts?: string[] },
): Promise<ApiResult<BrowserAutomationResult>> {
  const data = await request<BrowserAutomationResult>("/api/desktop/task/execute-browser", {
    method: "POST",
    body: JSON.stringify({
      task_id: taskId,
      authorized: options.authorized,
      headless: options.headless,
      allowed_hosts: options.allowedHosts ?? [],
    }),
  });
  return { data, source: "api" };
}

export async function getDesktopCompanionStatus(): Promise<ApiResult<DesktopCompanionStatus>> {
  const data = await request<DesktopCompanionStatus>("/api/desktop/companion/status");
  return { data, source: "api" };
}

export async function startDesktopCompanion(intervalSeconds = 5): Promise<ApiResult<DesktopCompanionStatus>> {
  const data = await request<DesktopCompanionStatus>("/api/desktop/companion/start", {
    method: "POST",
    body: JSON.stringify({ interval_seconds: intervalSeconds }),
  });
  return { data, source: "api" };
}

export async function stopDesktopCompanion(): Promise<ApiResult<DesktopCompanionStatus>> {
  const data = await request<DesktopCompanionStatus>("/api/desktop/companion/stop", {
    method: "POST",
    body: "{}",
  });
  return { data, source: "api" };
}

export async function runDesktopCompanionOnce(limit = 5): Promise<ApiResult<DesktopCompanionRunResponse>> {
  const data = await request<DesktopCompanionRunResponse>("/api/desktop/companion/run-once", {
    method: "POST",
    body: JSON.stringify({ limit }),
  });
  return { data, source: "api" };
}

export async function getDesktopWorkflowStatus(): Promise<ApiResult<DesktopWorkflowStatus>> {
  const data = await request<DesktopWorkflowStatus>("/api/desktop/workflow/status");
  return { data, source: "api" };
}

export async function planDesktopWorkflow(goal: string, steps: string[]): Promise<ApiResult<DesktopWorkflowResult>> {
  const data = await request<DesktopWorkflowResult>("/api/desktop/workflow/plan", {
    method: "POST",
    body: JSON.stringify({ goal, steps }),
  });
  return { data, source: "api" };
}

export async function setupDesktopWorkflow(goal: string, steps: string[]): Promise<ApiResult<DesktopWorkflowResult>> {
  const data = await request<DesktopWorkflowResult>("/api/desktop/workflow/setup", {
    method: "POST",
    body: JSON.stringify({ goal, steps }),
  });
  return { data, source: "api" };
}

export async function executeDesktopWorkflow(goal: string, steps: string[], authorized: boolean): Promise<ApiResult<DesktopWorkflowResult>> {
  const data = await request<DesktopWorkflowResult>("/api/desktop/workflow/execute", {
    method: "POST",
    body: JSON.stringify({ goal, steps, authorized }),
  });
  return { data, source: "api" };
}

export async function runDesktopControlAction(payload: Record<string, unknown>): Promise<ApiResult<DesktopControlActionResult>> {
  const data = await request<DesktopControlActionResult>("/api/desktop/control/action", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}
