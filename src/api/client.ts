import type { ApiEnvelope, ApiErrorPayload, ApiResult } from "./types";

const TOKEN_KEY = "openclaw_console_token";
const DOCUMENT_SESSION_KEY = "lelamp_document_session";
const env = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env ?? {};
const USE_MOCK_API = env.VITE_USE_MOCK_API === "true";
const API_BASE = env.VITE_API_BASE_URL ?? "";

export class ApiClientError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;

  constructor(error: ApiErrorPayload, status: number) {
    super(error.message);
    this.name = "ApiClientError";
    this.status = error.status ?? status;
    this.code = error.code;
    this.details = error.details;
  }
}

export function useMockApiEnabled(): boolean {
  return USE_MOCK_API;
}

export function readToken(): string {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token")?.trim() || localStorage.getItem(TOKEN_KEY) || "";
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  }
  return token;
}

export function readDocumentSession(): string {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("document_session")?.trim() || sessionStorage.getItem(DOCUMENT_SESSION_KEY) || "";
  if (token) sessionStorage.setItem(DOCUMENT_SESSION_KEY, token);
  return token;
}

export function setToken(token: string): void {
  if (token.trim()) {
    localStorage.setItem(TOKEN_KEY, token.trim());
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

function buildUrl(path: string): URL {
  const base = API_BASE || window.location.origin;
  return new URL(path, base);
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = readToken();
  const documentSession = readDocumentSession();
  const url = buildUrl(path);
  const headers = new Headers(init?.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (documentSession) {
    headers.set("X-LeLamp-Document-Session", documentSession);
  }
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(url, { ...init, headers });
  } catch (error) {
    throw new ApiClientError(
      {
        code: "network_error",
        message: error instanceof Error ? error.message : "Network error.",
        details: {},
      },
      0,
    );
  }

  const contentType = response.headers.get("Content-Type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? ((await response.json()) as ApiEnvelope<T>) : undefined;
  if (!response.ok) {
    if (payload && !payload.ok) {
      throw new ApiClientError(payload.error, response.status);
    }
    throw new ApiClientError(
      {
        code: `http_${response.status}`,
        message: response.statusText || `HTTP ${response.status}`,
        details: {},
      },
      response.status,
    );
  }
  if (!isJson && path.startsWith("/api/")) {
    throw new ApiClientError(
      {
        code: "invalid_api_response",
        message: "API returned a non-JSON response.",
        details: { content_type: contentType || "unknown" },
      },
      response.status,
    );
  }
  if (payload && payload.ok) {
    return payload.data;
  }
  if (payload && !payload.ok) {
    throw new ApiClientError(payload.error, response.status);
  }
  return (await response.text()) as T;
}

export async function requestBlob(path: string): Promise<Blob> {
  const token = readToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(buildUrl(path), { headers });
  if (!response.ok) {
    throw new ApiClientError(
      { code: `http_${response.status}`, message: response.statusText || `HTTP ${response.status}`, details: {} },
      response.status,
    );
  }
  return response.blob();
}

export async function requestWithMock<T>(path: string, fallback: T, init?: RequestInit): Promise<ApiResult<T>> {
  if (USE_MOCK_API) {
    return { data: fallback, source: "mock" };
  }
  const data = await request<T>(path, init);
  return { data, source: "api" };
}

export function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (error.status === 401) return "需要 token 或 token 无效。";
    if (error.status === 403 || error.code === "blocked") return "安全策略阻止了该操作。";
    if (error.status === 404) return "API 或资源不存在。";
    if (error.status === 409) return "当前状态需要确认或存在冲突。";
    if (error.code === "network_error") return "无法连接 LeLamp Web API。";
    return `${error.code}: ${error.message}`;
  }
  return error instanceof Error ? error.message : "未知错误";
}

export function appendQuery(path: string, params: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      query.set(key, String(value));
    }
  }
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}
