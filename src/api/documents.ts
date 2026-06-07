import { request, requestWithMock } from "./client";
import type { ApiResult, DocumentResult, ScanResult } from "./types";

export async function runDocumentAnalyze(filePath: string): Promise<ApiResult<DocumentResult>> {
  const data = await request<DocumentResult>("/api/document/analyze", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath }),
  });
  return { data, source: "api" };
}

export async function runDocumentSummarize(filePath: string, style = "brief"): Promise<ApiResult<DocumentResult>> {
  const data = await request<DocumentResult>("/api/document/summarize", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath, style }),
  });
  return { data, source: "api" };
}

export async function runDocumentRisks(filePath: string): Promise<ApiResult<DocumentResult>> {
  const data = await request<DocumentResult>("/api/document/risks", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath }),
  });
  return { data, source: "api" };
}

export async function runDocumentTableExtract(filePath: string): Promise<ApiResult<DocumentResult>> {
  const data = await request<DocumentResult>("/api/document/table-extract", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath }),
  });
  return { data, source: "api" };
}

export async function runDocumentReportOutline(filePath: string, topic?: string): Promise<ApiResult<DocumentResult>> {
  const data = await request<DocumentResult>("/api/document/report-outline", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath, topic }),
  });
  return { data, source: "api" };
}

export function getDocumentAdaptersStatus(): Promise<ApiResult<{ adapters: Record<string, string> }>> {
  return requestWithMock<{ adapters: Record<string, string> }>("/api/document/adapters/status", {
    adapters: {
      document_analyzer: "adapter_ready",
      report_outline: "backend_missing",
      table_extractor: "backend_missing",
      meeting_email_draft: "backend_missing",
      scan_capture: "adapter_ready",
      scan_enhancement: "adapter_ready",
      ocr: "unavailable",
      vision_ocr: "backend_missing",
    },
  });
}

export async function runScanProcess(filePath: string, options: { document_type?: string; language?: string } = {}): Promise<ApiResult<ScanResult>> {
  const data = await request<ScanResult>("/api/scan/process", {
    method: "POST",
    body: JSON.stringify({ filename: filePath, ...options }),
  });
  return { data, source: "api" };
}

export async function runScanEnhance(filePath: string): Promise<ApiResult<ScanResult>> {
  const data = await request<ScanResult>("/api/scan/enhance", {
    method: "POST",
    body: JSON.stringify({ filename: filePath }),
  });
  return { data, source: "api" };
}

export async function checkScanCaptureReadiness(filePath: string): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/scan/capture-readiness", {
    method: "POST",
    body: JSON.stringify({ filename: filePath }),
  });
  return { data, source: "api" };
}

export async function createDemoScanImage(options: { title?: string; document_type?: string } = {}): Promise<ApiResult<Record<string, unknown>>> {
  const data = await request<Record<string, unknown>>("/api/scan/demo-image", {
    method: "POST",
    body: JSON.stringify(options),
  });
  return { data, source: "api" };
}

export async function captureDocumentScan(payload: {
  image_data_url: string;
  title: string;
  document_type?: string;
  language?: string;
}): Promise<ApiResult<ScanResult>> {
  const data = await request<ScanResult>("/api/scan/capture", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}

export async function captureDeviceDocumentScan(payload: {
  title: string;
  document_type?: string;
  language?: string;
  camera_index?: number;
}): Promise<ApiResult<ScanResult>> {
  const data = await request<ScanResult>("/api/scan/device-capture", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { data, source: "api" };
}
