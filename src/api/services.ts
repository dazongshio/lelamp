import { requestWithMock } from "./client";
import type { ApiResult, ServicesStatusResponse } from "./types";
import { mockServices } from "../data/mockSecurity";

export function getServicesStatus(): Promise<ApiResult<ServicesStatusResponse>> {
  return requestWithMock<ServicesStatusResponse>("/api/services/status", { services: mockServices });
}
