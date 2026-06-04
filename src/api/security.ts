import { request, requestWithMock } from "./client";
import type { ApiResult, EnterpriseLocalPlatformBuildResponse, EnterpriseLocalPlatformStatus, EnterprisePolicyStatus, SecurityStatus } from "./types";
import { mockSecurity } from "../data/mockSecurity";

export function getSecurity(): Promise<ApiResult<SecurityStatus>> {
  return requestWithMock<SecurityStatus>("/api/security", mockSecurity);
}

export async function getEnterprisePolicy(): Promise<ApiResult<EnterprisePolicyStatus>> {
  const data = await request<EnterprisePolicyStatus>("/api/security/enterprise-policy");
  return { data, source: "api" };
}

export async function getEnterpriseLocalPlatformStatus(): Promise<ApiResult<EnterpriseLocalPlatformStatus>> {
  const data = await request<EnterpriseLocalPlatformStatus>("/api/enterprise/local-platform/status");
  return { data, source: "api" };
}

export async function buildEnterpriseLocalPlatform(includeSamples = true): Promise<ApiResult<EnterpriseLocalPlatformBuildResponse>> {
  const data = await request<EnterpriseLocalPlatformBuildResponse>("/api/enterprise/local-platform/build", {
    method: "POST",
    body: JSON.stringify({ include_samples: includeSamples }),
  });
  return { data, source: "api" };
}

export async function verifySignedAuditExport(path: string): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/security/verify-signed-audit", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
  return { data, source: "api" };
}
