import { appendQuery, requestWithMock } from "./client";
import type { ApiResult, AuditResponse } from "./types";
import { mockAuditEvents } from "../data/mockAudit";
import { mockSecurity } from "../data/mockSecurity";

export interface AuditSearchParams {
  q?: string;
  action?: string;
  status?: string;
  page?: number;
  page_size?: number;
  limit?: number;
}

export function getAuditRecent(params: AuditSearchParams = { limit: 80 }): Promise<ApiResult<AuditResponse>> {
  return requestWithMock<AuditResponse>(
    appendQuery("/api/audit/recent", { ...params }),
    {
      items: mockAuditEvents,
      events: mockAuditEvents,
      total: mockAuditEvents.length,
      page: 1,
      page_size: mockAuditEvents.length,
      path: mockSecurity.audit_log_path,
    },
  );
}

export function searchAudit(params: AuditSearchParams): Promise<ApiResult<AuditResponse>> {
  return requestWithMock<AuditResponse>(
    appendQuery("/api/audit/search", { ...params }),
    {
      items: mockAuditEvents,
      events: mockAuditEvents,
      total: mockAuditEvents.length,
      page: 1,
      page_size: mockAuditEvents.length,
      path: mockSecurity.audit_log_path,
    },
  );
}

export function signedAuditExportUrl(params: AuditSearchParams = {}): string {
  return appendQuery("/api/audit/export-signed", { ...params });
}
