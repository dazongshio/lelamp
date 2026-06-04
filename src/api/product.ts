import { request } from "./client";
import type { ApiResult, DesktopValidationImportResponse, ProductChecklistResponse, TargetValidationRunResponse, TargetValidationStatusResponse } from "./types";

export async function getProductChecklist(): Promise<ApiResult<ProductChecklistResponse>> {
  const data = await request<ProductChecklistResponse>("/api/product/checklist");
  return { data, source: "api" };
}

export async function getTargetValidationStatus(): Promise<ApiResult<TargetValidationStatusResponse>> {
  const data = await request<TargetValidationStatusResponse>("/api/product/validation/status");
  return { data, source: "api" };
}

export async function runTargetValidation(testId: string, options: Record<string, unknown> = {}): Promise<ApiResult<TargetValidationRunResponse>> {
  const data = await request<TargetValidationRunResponse>("/api/product/validation/run", {
    method: "POST",
    body: JSON.stringify({ test_id: testId, ...options }),
  });
  return { data, source: "api" };
}

export async function importDesktopValidationResult(payload: Record<string, unknown>): Promise<ApiResult<DesktopValidationImportResponse>> {
  const data = await request<DesktopValidationImportResponse>("/api/product/validation/import-desktop-result", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}
