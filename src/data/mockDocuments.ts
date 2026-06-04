import type { DocumentAdapter } from "../api/types";

export const documentAdapters: DocumentAdapter[] = [
  {
    name: "文档分析 Adapter",
    status: "adapter_ready",
    backend: "doc-analyzer",
    endpoint: "http://127.0.0.1:9091",
    lastHeartbeat: "14:33:01",
    note: "正常",
  },
  {
    name: "表格提取 Adapter",
    status: "backend_missing",
    backend: "table-extractor",
    endpoint: "-",
    lastHeartbeat: "-",
    note: "后端服务未配置或未启动",
  },
  {
    name: "OCR Adapter",
    status: "unavailable",
    backend: "ocr-engine",
    endpoint: "-",
    lastHeartbeat: "-",
    note: "未启用或不可用",
  },
];
