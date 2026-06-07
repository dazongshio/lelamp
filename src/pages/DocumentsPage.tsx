import { AlertTriangle, BookOpen, Camera, ExternalLink, FileImage, FileText, ListTree, ScanLine, Table2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { apiErrorMessage, readToken } from "../api/client";
import {
  checkScanCaptureReadiness,
  captureDocumentScan,
  captureDeviceDocumentScan,
  createDemoScanImage,
  getDocumentAdaptersStatus,
  runDocumentAnalyze,
  runDocumentReportOutline,
  runDocumentRisks,
  runDocumentTableExtract,
  runScanEnhance,
  runScanProcess,
} from "../api/documents";
import { getSecurity } from "../api/security";
import { getSharedFiles, getSharedPreview, getWorkspaceFiles, getWorkspacePreview } from "../api/shared";
import { syncWorkspaceFileToWiki } from "../api/wiki";
import type { DocumentAdapter, DocumentResult, DocmostSyncResponse, ScanResult, SecurityStatus, SharedFile, SharedPreviewResponse } from "../api/types";
import { Card } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { WorkspaceFileViewer } from "../components/WorkspaceFileViewer";
import { mockSecurity } from "../data/mockSecurity";
import "./pages.css";

export function DocumentsPage() {
  const [searchParams] = useSearchParams();
  const [source, setSource] = useState("shared_inbox");
  const [files, setFiles] = useState<SharedFile[]>([]);
  const [selected, setSelected] = useState("");
  const [preview, setPreview] = useState<SharedPreviewResponse | null>(null);
  const [artifactPath, setArtifactPath] = useState("");
  const [artifactPreview, setArtifactPreview] = useState<SharedPreviewResponse | null>(null);
  const [artifactPreviewError, setArtifactPreviewError] = useState("");
  const [artifactPreviewBusy, setArtifactPreviewBusy] = useState(false);
  const [wikiBusyPath, setWikiBusyPath] = useState("");
  const [wikiResult, setWikiResult] = useState<DocmostSyncResponse | null>(null);
  const [result, setResult] = useState<DocumentResult | null>(null);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [captureReadiness, setCaptureReadiness] = useState<Record<string, unknown> | null>(null);
  const [adapters, setAdapters] = useState<Record<string, string>>({});
  const [security, setSecurity] = useState<SecurityStatus>(mockSecurity);
  const [message, setMessage] = useState("等待选择文件");
  const [error, setError] = useState("");
  const [scanBusy, setScanBusy] = useState(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [scanTitle, setScanTitle] = useState("实体文档扫描");
  const [scanType, setScanType] = useState("document");
  const [scanLanguage, setScanLanguage] = useState("chi_sim+eng");
  const cameraVideoRef = useRef<HTMLVideoElement | null>(null);
  const cameraStreamRef = useRef<MediaStream | null>(null);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    setError("");
    try {
      const listFiles = source === "workspace" ? getWorkspaceFiles : getSharedFiles;
      const [filesResult, adaptersResult, securityResult] = await Promise.all([
        listFiles({ page_size: source === "workspace" ? 300 : 100 }),
        getDocumentAdaptersStatus(),
        getSecurity(),
      ]);
      const nextFiles = filesResult.data.files ?? [];
      setFiles(nextFiles);
      setAdapters(adaptersResult.data.adapters);
      setSecurity(securityResult.data);
      setSelected((current) => nextFiles.some((file) => file.relative_path === current) ? current : nextFiles[0]?.relative_path || "");
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, [source]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (searchParams.get("scan") !== "1") return;
    setSource(searchParams.get("source") === "scene" ? "shared_inbox" : "shared_inbox");
    setScanType(searchParams.get("type") || "document");
    setMessage("场景工作流已打开扫描入口，请主动拍照或上传实体文档。");
  }, [searchParams]);

  useEffect(() => () => stopScanCamera(), []);

  useEffect(() => {
    if (!selected) return;
    const previewFile = source === "workspace" ? getWorkspacePreview : getSharedPreview;
    void previewFile(selected)
      .then((response) => setPreview(response.data))
      .catch((err) => setPreview({ status: "blocked", workspace_name: selected, name: selected, size_bytes: 0, text: apiErrorMessage(err) }));
  }, [selected, source]);

  useEffect(() => {
    setResult(null);
    setArtifactPath("");
    setArtifactPreview(null);
    setArtifactPreviewError("");
    setMessage(selected ? "等待操作" : "等待选择文件");
  }, [selected, source]);

  const selectedFile = useMemo(() => files.find((file) => file.relative_path === selected), [files, selected]);
  const selectedAnalysisSupported = isDocumentAnalysisSupported(selected);
  const analysisDisabled = !selected || !selectedAnalysisSupported;
  const resultArtifacts = useMemo(() => documentResultArtifacts(result, scanResult), [result, scanResult]);

  async function run(label: string, action: (filePath: string) => Promise<{ data: DocumentResult }>) {
    if (!selected) {
      setError("请先选择已上传的文件。");
      return;
    }
    setError("");
    setMessage(`${label} 执行中...`);
    try {
      const response = await action(selected);
      setResult(response.data);
      setMessage(`${label} 状态：${response.data.status}`);
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage(`${label} 失败`);
    }
  }

  async function processSelectedScan() {
    if (!selected) {
      setError("请先选择已上传的图片文件。");
      return;
    }
    setScanBusy(true);
    setError("");
    setMessage("正在处理实体文档扫描...");
    try {
      const response = await runScanProcess(selected, { document_type: scanType, language: scanLanguage });
      setScanResult(response.data);
      setMessage(`扫描处理状态：${response.data.status}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("扫描处理失败");
    } finally {
      setScanBusy(false);
    }
  }

  async function enhanceSelectedScan() {
    if (!selected) {
      setError("请先选择已上传的图片文件。");
      return;
    }
    setScanBusy(true);
    setError("");
    setMessage("正在自动识别四角并矫正图片...");
    try {
      const response = await runScanEnhance(selected);
      setScanResult(response.data);
      const correction = scanCorrection(response.data);
      setMessage(`四角矫正状态：${correction.status || response.data.status}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("四角矫正失败");
    } finally {
      setScanBusy(false);
    }
  }

  async function checkSelectedCaptureReadiness() {
    if (!selected) {
      setError("请先选择已上传的图片文件。");
      return;
    }
    setScanBusy(true);
    setError("");
    setMessage("正在判断当前图片是否满足自动拍照条件...");
    try {
      const response = await checkScanCaptureReadiness(selected);
      setCaptureReadiness(response.data);
      setMessage(`自动拍照候选状态：${String(response.data.status ?? "unknown")}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("自动拍照候选判断失败");
    } finally {
      setScanBusy(false);
    }
  }

  async function captureScanFromFile(file: File) {
    setScanBusy(true);
    setError("");
    setMessage("正在上传并识别实体文档...");
    try {
      const imageDataUrl = await fileToDataUrl(file);
      const response = await captureDocumentScan({
        image_data_url: imageDataUrl,
        title: scanTitle || file.name,
        document_type: scanType,
        language: scanLanguage,
      });
      setScanResult(response.data);
      setMessage(`扫描采集状态：${response.data.status}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("扫描采集失败");
    } finally {
      setScanBusy(false);
    }
  }

  async function openScanCamera() {
    setError("");
    setMessage("正在打开浏览器摄像头预览...");
    if (!navigator.mediaDevices?.getUserMedia) {
      setMessage("当前浏览器不支持预览拍照，已改用设备相机拍照扫描。");
      await captureScanFromDeviceCamera();
      return;
    }
    try {
      stopScanCamera();
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: false,
      });
      cameraStreamRef.current = stream;
      setCameraOpen(true);
      setCameraReady(false);
      window.setTimeout(async () => {
        if (!cameraVideoRef.current) return;
        cameraVideoRef.current.srcObject = stream;
        try {
          await cameraVideoRef.current.play();
          setCameraReady(true);
          setMessage("摄像头已打开，请把纸质文档放入画面后点击“拍照并识别”。");
        } catch (err) {
          setError(apiErrorMessage(err));
          setMessage("摄像头预览启动失败");
          stopScanCamera();
        }
      }, 0);
    } catch (err) {
      stopScanCamera();
      setMessage("浏览器预览不可用，已改用设备相机拍照扫描。");
      await captureScanFromDeviceCamera();
    }
  }

  async function captureScanFromDeviceCamera() {
    setScanBusy(true);
    setError("");
    setMessage("正在调用设备摄像头拍照扫描...");
    try {
      const response = await captureDeviceDocumentScan({
        title: scanTitle || "device_document_scan",
        document_type: scanType,
        language: scanLanguage,
        camera_index: 0,
      });
      setScanResult(response.data);
      setMessage(`设备拍照扫描状态：${response.data.status}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("设备摄像头拍照失败，可改用“上传图片扫描”。");
    } finally {
      setScanBusy(false);
    }
  }

  function stopScanCamera() {
    cameraStreamRef.current?.getTracks().forEach((track) => track.stop());
    cameraStreamRef.current = null;
    if (cameraVideoRef.current) cameraVideoRef.current.srcObject = null;
    setCameraOpen(false);
    setCameraReady(false);
  }

  async function captureScanFromCamera() {
    const video = cameraVideoRef.current;
    if (!video || !cameraReady || !video.videoWidth || !video.videoHeight) {
      setError("摄像头画面还没有准备好，请稍等一秒再拍照。");
      return;
    }
    setScanBusy(true);
    setError("");
    setMessage("正在拍照并识别实体文档...");
    try {
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("无法创建拍照画布。");
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const response = await captureDocumentScan({
        image_data_url: canvas.toDataURL("image/jpeg", 0.92),
        title: scanTitle || "camera_document_scan",
        document_type: scanType,
        language: scanLanguage,
      });
      setScanResult(response.data);
      setMessage(`拍照扫描状态：${response.data.status}`);
      stopScanCamera();
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("拍照扫描失败");
    } finally {
      setScanBusy(false);
    }
  }

  async function generateDemoScan() {
    setScanBusy(true);
    setError("");
    setMessage("正在生成扫描验收样张...");
    try {
      const response = await createDemoScanImage({ title: scanTitle || "validation_scan_demo", document_type: scanType });
      const workspaceName = String(response.data.workspace_name ?? "");
      if (workspaceName) {
        setSelected(workspaceName);
        setMessage(`已生成样张：${compactDisplayPath(workspaceName)}，可继续判断自动拍照候选或处理当前图片。`);
      } else {
        setMessage(`样张生成状态：${String(response.data.status ?? "unknown")}`);
      }
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("生成扫描验收样张失败");
    } finally {
      setScanBusy(false);
    }
  }

  async function openResultArtifact(path: string) {
    setArtifactPath(path);
    setArtifactPreview(null);
    setArtifactPreviewError("");
    setArtifactPreviewBusy(true);
    try {
      const response = await getWorkspacePreview(workspaceArtifactPath(path));
      setArtifactPreview(response.data);
    } catch (err) {
      setArtifactPreviewError(apiErrorMessage(err));
    } finally {
      setArtifactPreviewBusy(false);
    }
  }

  async function syncToWiki(filePath: string, title?: string) {
    const workspaceName = workspaceArtifactPath(filePath);
    if (!workspaceName) {
      setError("没有可同步的文件。");
      return;
    }
    setError("");
    setWikiBusyPath(workspaceName);
    setMessage("正在同步到 Wiki...");
    try {
      const response = await syncWorkspaceFileToWiki({
        filePath: workspaceName,
        title: title || workspaceName.split("/").pop() || "LeLamp 文档",
      });
      setWikiResult(response.data);
      setMessage(`已同步到 Wiki：${response.data.docmost_page_title}`);
      if (response.data.docmost_page_url) {
        window.open(response.data.docmost_page_url, "_blank", "noopener,noreferrer");
      }
    } catch (err) {
      setError(apiErrorMessage(err));
      setMessage("同步到 Wiki 失败");
    } finally {
      setWikiBusyPath("");
    }
  }

  const adapterRows: DocumentAdapter[] = Object.entries(adapters).map(([name, status]) => ({
    name,
    status,
    backend: name.includes("scan") || name === "ocr" || name === "vision_ocr" ? "Browser camera + OpenCV + OpenAI/DashScope vision + local OCR" : "OpenClaw local service",
    endpoint: name.includes("scan") ? "scan" : name === "ocr" || name === "vision_ocr" ? "ocr" : "document",
    lastHeartbeat: "on request",
    note: status === "available" ? "已接入本地文本能力" : "未接入能力如实显示，不伪装成功",
  }));

  return (
    <>
      <PageHeader title="文档处理" description="选择文件后直接查看、分析或扫描" actions={<button className="ghost-button" onClick={() => void load()}>刷新</button>} />
      <div className="page-grid">
        {error && <div className="danger-panel">操作失败：{error}</div>}
        <Card title="文件与操作" subtitle="上传区和工作区文件都可预览；分析只作用于当前选择文件。">
          <div className="document-command-strip">
            <div className="document-source-tabs" role="tablist" aria-label="文档来源">
              {["shared_inbox", "workspace"].map((item) => (
                <button className={source === item ? "selected" : ""} key={item} onClick={() => setSource(item)} type="button">
                  <FileText size={16} />
                  {item === "shared_inbox" ? "上传文件" : "工作区"}
                </button>
              ))}
            </div>
            <select className="select document-file-select" value={selected} onChange={(event) => setSelected(event.target.value)}>
              {files.map((file) => <option value={file.relative_path} key={file.relative_path}>{file.relative_path}</option>)}
            </select>
            <button className="ghost-button" onClick={() => void load()}>刷新列表</button>
          </div>
          <div className="selected-file selected-file--compact">
            <strong>{selectedFile?.name ?? "暂无文件"}</strong>
            <span className="small">{selected ? compactDisplayPath(selected) : "请先上传文件"}</span>
            <span className="small muted">{selectedFile?.mime_type ?? "-"} · {selectedFile?.size_label ?? "-"} · {selectedFile?.uploaded_at ?? "-"}</span>
          </div>
          <div className="document-action-row">
            <button className="primary-button" onClick={() => void run("文档分析", runDocumentAnalyze)} disabled={analysisDisabled}>
              <FileText size={16} /> 分析
            </button>
            <button className="ghost-button" onClick={() => void run("汇报提纲", (file) => runDocumentReportOutline(file, selectedFile?.name ?? "汇报提纲"))} disabled={analysisDisabled}>
              <ListTree size={16} /> 提纲
            </button>
            <button className="ghost-button" onClick={() => void run("风险扫描", runDocumentRisks)} disabled={analysisDisabled}>
              <AlertTriangle size={16} /> 风险
            </button>
            <button className="ghost-button" onClick={() => void run("表格提取", runDocumentTableExtract)} disabled={analysisDisabled}>
              <Table2 size={16} /> 表格
            </button>
            <button className="ghost-button" onClick={() => setMessage(`扫描：${friendlyStatus(adapters.scan_capture ?? "adapter_ready")} / 文字识别：${friendlyStatus(adapters.ocr ?? "unavailable")}`)}>
              <ScanLine size={16} /> 扫描状态
            </button>
            <button className="ghost-button" onClick={() => void syncToWiki(selected, selectedFile?.name)} disabled={!selected || wikiBusyPath === workspaceArtifactPath(selected)}>
              <BookOpen size={16} /> {wikiBusyPath === workspaceArtifactPath(selected) ? "同步中..." : "同步到 Wiki"}
            </button>
          </div>
          <div className="document-status-line">
            <StatusBadge status={!selectedAnalysisSupported && selected ? "unsupported" : result?.status ?? preview?.status ?? "pending"} />
            <span>{!selectedAnalysisSupported && selected ? "该文件类型可查看/下载，但不能做文本分析。" : message}</span>
          </div>
          {wikiResult?.docmost_page_url && (
            <a className="card-link" href={wikiResult.docmost_page_url} target="_blank" rel="noreferrer">
              <ExternalLink size={16} /> 打开最近同步的 Wiki 页面
            </a>
          )}
        </Card>

        <div className="scan-workbench">
          <Card title="实体文档采集" subtitle="手机全能扫描王式流程：拍照/上传、边界校正、增强、文字与结构识别" action={<StatusBadge status={scanResult?.status ?? adapters.ocr ?? "pending"} />}>
            <div className="scan-controls">
              <input className="input" value={scanTitle} onChange={(event) => setScanTitle(event.target.value)} placeholder="扫描标题" />
              <select className="select" value={scanType} onChange={(event) => setScanType(event.target.value)}>
                <option value="document">普通文档</option>
                <option value="contract">合同</option>
                <option value="business_card">名片</option>
                <option value="receipt">票据</option>
                <option value="whiteboard">白板</option>
              </select>
              <select className="select" value={scanLanguage} onChange={(event) => setScanLanguage(event.target.value)}>
                <option value="chi_sim+eng">中文+英文</option>
                <option value="ch">中文</option>
                <option value="en">英文</option>
              </select>
            </div>
            <div className="scan-actions">
              <button className="primary-button" onClick={() => void captureScanFromDeviceCamera()} disabled={scanBusy}>
                <Camera size={16} /> {scanBusy ? "处理中..." : "调用设备相机拍照"}
              </button>
              <button className="ghost-button" onClick={() => uploadInputRef.current?.click()} disabled={scanBusy}>
                <FileImage size={16} /> 上传图片扫描
              </button>
              <button className="ghost-button" onClick={() => void enhanceSelectedScan()} disabled={scanBusy || !selected}>
                <ScanLine size={16} /> 四角矫正/增强
              </button>
              <button className="ghost-button" onClick={() => void processSelectedScan()} disabled={scanBusy || !selected}>
                <ScanLine size={16} /> 处理并 OCR
              </button>
              <button className="ghost-button" onClick={() => void checkSelectedCaptureReadiness()} disabled={scanBusy || !selected}>
                判断自动拍照候选
              </button>
              <button className="ghost-button" onClick={() => void generateDemoScan()} disabled={scanBusy}>
                生成验收样张
              </button>
            </div>
            <details className="advanced-panel scan-browser-camera">
              <summary>浏览器摄像头预览</summary>
              <div className="advanced-panel__content">
                <p className="small muted">该入口需要 HTTPS 或 localhost。局域网 HTTP 下会自动改用设备相机拍照。</p>
                <button className="ghost-button" onClick={() => void openScanCamera()} disabled={scanBusy || cameraOpen}>
                  打开浏览器预览
                </button>
              </div>
            </details>
            {cameraOpen && (
              <div className="scan-camera-panel">
                <video ref={cameraVideoRef} className="scan-camera-preview" playsInline muted />
                <div className="scan-camera-actions">
                  <button className="primary-button" onClick={() => void captureScanFromCamera()} disabled={scanBusy || !cameraReady}>
                    拍照并识别
                  </button>
                  <button className="ghost-button" onClick={stopScanCamera} disabled={scanBusy}>
                    关闭摄像头
                  </button>
                </div>
              </div>
            )}
            <input
              ref={uploadInputRef}
              className="hidden-file-input"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) void captureScanFromFile(file);
              }}
            />
            <div className="scan-capability-grid">
              <span>自动边界</span><StatusBadge status={scanEnhancement(scanResult) ? "completed" : "adapter_ready"} />
              <span>视觉校正</span><StatusBadge status={String(scanCorrection(scanResult).status) === "detected" ? "completed" : scanEnhancement(scanResult) ? "adapter_ready" : "adapter_ready"} label={String(scanCorrection(scanResult).status || "") || undefined} />
              <span>去阴影/增强</span><StatusBadge status={scanEnhancement(scanResult) ? "completed" : "adapter_ready"} />
              <span>文字/结构识别</span><StatusBadge status={scanResult?.status ?? adapters.ocr ?? "backend_missing"} />
              <span>视觉识别</span><StatusBadge status={adapters.vision_ocr ?? "backend_missing"} />
              <span>自动拍照候选</span><StatusBadge status={String(captureReadiness?.status ?? "pending")} />
            </div>
            <p className="small muted">识别能力会优先使用已配置的视觉模型；不可用时只完成图像增强和待处理记录，不伪造识别结果。</p>
          </Card>
          <Card title="扫描结果" action={<StatusBadge status={scanResult?.status ?? "pending"} />}>
            <div className="definition-grid">
              <span>原图</span><strong>{scanResult?.source_workspace_name || scanResult?.image ? "已保存" : "-"}</strong>
              <span>四角状态</span><strong>{String(scanCorrection(scanResult).status ?? "-")}</strong>
              <span>四角置信度</span><strong>{String(scanCorrection(scanResult).confidence ?? "-")}</strong>
              <span>增强图</span><strong>{scanEnhancement(scanResult)?.enhanced_workspace_name ? "已生成" : "-"}</strong>
              <span>识别文本</span><strong>{scanResult?.text_path ? "已生成" : "-"}</strong>
              <span>结构识别</span><strong>{scanResult?.structure_path ? "已生成" : "-"}</strong>
              <span>表格文件</span><strong>{scanResult?.table_paths?.length ? `${scanResult.table_paths.length} 个` : "-"}</strong>
              <span>拍照候选</span><strong>{String(captureReadiness?.stable_score ?? "-")}</strong>
            </div>
            <ScanArtifactLinks scanResult={scanResult} />
            <details className="advanced-panel">
              <summary>扫描诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview scan-result-preview">{JSON.stringify(scanResult ?? { status: "pending", message: "等待拍照或上传实体文档。" }, null, 2)}</pre>
                {captureReadiness && <pre className="json-preview scan-result-preview">{JSON.stringify(captureReadiness, null, 2)}</pre>}
              </div>
            </details>
          </Card>
        </div>

        <div className="documents-grid">
          <Card title="文档预览">
            <WorkspaceFileViewer
              source={source === "workspace" ? "workspace" : "shared_inbox"}
              filePath={selected}
              preview={preview}
              title="文档查看"
            />
          </Card>
          <div className="stack">
            <Card title="分析摘要" action={<StatusBadge status={result?.status ?? "pending"} />}>
              <div className="document-result-summary">
                <div>
                  <span>输出</span>
                  <strong>{result?.outputs?.length ?? 0}</strong>
                </div>
                <div>
                  <span>风险</span>
                  <strong>{result?.risks?.length ?? 0}</strong>
                </div>
                <div>
                  <span>表格</span>
                  <strong>{result?.table_path || scanResult?.table_paths?.length ? "已生成" : "-"}</strong>
                </div>
              </div>
              <p className="blue-note">{String(result?.summary ?? message)}</p>
              <details className="advanced-panel compact-details">
                <summary>元数据</summary>
                <div className="list-rows compact">
                  {Object.entries(result?.metadata ?? {}).slice(0, 8).map(([key, value]) => (
                    <div className="row-between" key={key}>
                      <span>{key}</span>
                      <strong>{String(value)}</strong>
                    </div>
                  ))}
                  {!Object.keys(result?.metadata ?? {}).length && <span className="small muted">等待分析结果。</span>}
                </div>
              </details>
            </Card>
            <Card title="结果文件" action={<StatusBadge status={resultArtifacts.length ? "available" : "pending"} label={`${resultArtifacts.length} 个`} />}>
              <div className="document-artifact-list">
                {resultArtifacts.map((artifact) => (
                  <button
                    className={artifact.workspaceName === artifactPath ? "document-artifact-button selected" : "document-artifact-button"}
                    key={`${artifact.label}-${artifact.workspaceName}`}
                    onClick={() => void openResultArtifact(artifact.workspaceName)}
                    type="button"
                  >
                    <FileText size={16} />
                    <span>{artifact.label}</span>
                    <small>{compactDisplayPath(artifact.workspaceName)}</small>
                  </button>
                ))}
                {!resultArtifacts.length && <div className="blue-note">运行分析、提纲、表格提取或扫描后，结果文件会出现在这里。</div>}
              </div>
            </Card>
            {(artifactPath || artifactPreviewBusy || artifactPreviewError) && (
              <Card
                title="结果查看"
                action={
                  artifactPath ? (
                    <button className="ghost-button" onClick={() => void syncToWiki(artifactPath, artifactPath.split("/").pop())} disabled={wikiBusyPath === workspaceArtifactPath(artifactPath)}>
                      <BookOpen size={16} /> {wikiBusyPath === workspaceArtifactPath(artifactPath) ? "同步中..." : "同步到 Wiki"}
                    </button>
                  ) : undefined
                }
              >
                <WorkspaceFileViewer
                  source="workspace"
                  filePath={workspaceArtifactPath(artifactPath)}
                  preview={artifactPreview}
                  busy={artifactPreviewBusy}
                  error={artifactPreviewError}
                  title="结果查看"
                  compact
                />
              </Card>
            )}
            <Card title="风险" action={<StatusBadge status={result?.risks?.length ? "warning" : "adapter_ready"} label={`${result?.risks?.length ?? 0} 项风险`} />}>
              <div className="risk-list">
                {(result?.risks ?? []).map((risk, index) => <div key={index}><StatusBadge status="warning" label={String(risk.level ?? "risk")} /> {String(risk.marker ?? JSON.stringify(risk))}</div>)}
                {!result?.risks?.length && <div className="blue-note">尚未发现风险或未运行风险扫描。</div>}
              </div>
            </Card>
          </div>
        </div>

        <details className="advanced-panel">
          <summary>高级诊断</summary>
          <div className="advanced-panel__content">
            <Card title="能力适配状态">
              <div className="list-rows compact">
                {adapterRows.map((row) => (
                  <div className="row-between" key={row.name}>
                    <span>{row.name}</span>
                    <StatusBadge status={row.status} label={friendlyStatus(row.status)} />
                  </div>
                ))}
              </div>
            </Card>
            <Card title="安全范围">
              <div className="list-rows">
                {security.allowed_roots.map((root) => <div className="row-between" key={root}><strong>{compactDisplayPath(root)}</strong><span>可读/可分析</span></div>)}
              </div>
            </Card>
            <Card title="原始分析结果">
              <pre className="json-preview">{JSON.stringify(result ?? {}, null, 2)}</pre>
            </Card>
          </div>
        </details>
      </div>
    </>
  );
}

function friendlyStatus(status: unknown) {
  const value = String(status ?? "");
  const labels: Record<string, string> = {
    ok: "正常",
    available: "可用",
    completed: "已完成",
    pending: "等待",
    running: "处理中",
    blocked: "已阻止",
    failed: "失败",
    unavailable: "不可用",
    adapter_ready: "待接入",
    backend_missing: "待接入",
    needs_config: "待配置",
    unsupported: "不支持",
  };
  return labels[value] ?? (value || "等待");
}

interface DocumentArtifact {
  label: string;
  workspaceName: string;
}

function documentResultArtifacts(result: DocumentResult | null, scanResult: ScanResult | null): DocumentArtifact[] {
  const artifacts: DocumentArtifact[] = [];
  const add = (label: string, value: unknown) => {
    if (typeof value !== "string" || !value.trim()) return;
    const workspaceName = workspaceArtifactPath(value);
    if (!workspaceName) return;
    artifacts.push({ label, workspaceName });
  };
  add("分析结果", result?.analysis_path);
  add("文档摘要", result?.summary_path);
  add("汇报提纲", result?.outline_path);
  add("关键数据表", result?.table_path);
  add("扫描纪要", scanResult?.summary_path);
  add("OCR 文本", scanResult?.text_path);
  add("扫描结构", scanResult?.structure_path);
  (scanResult?.table_paths ?? []).forEach((path, index) => add(`扫描表格 ${index + 1}`, path));
  (result?.outputs ?? []).forEach((output) => add(outputArtifactLabel(output.path, output.type), output.path));
  return uniqueDocumentArtifacts(artifacts);
}

function outputArtifactLabel(pathValue: string, type: string) {
  const lower = `${pathValue} ${type}`.toLowerCase();
  if (lower.includes("outline")) return "汇报提纲";
  if (lower.includes("analysis")) return "分析结果";
  if (lower.includes("summary") || lower.includes("minutes")) return "纪要/摘要";
  if (lower.includes("table") || lower.endsWith(".csv")) return "关键数据表";
  return type ? `输出：${type}` : "输出文件";
}

function workspaceArtifactPath(pathValue: string) {
  const normalized = String(pathValue || "").trim().replace(/\\/g, "/");
  const marker = "/workspace/";
  const markerIndex = normalized.lastIndexOf(marker);
  if (markerIndex >= 0) return normalized.slice(markerIndex + marker.length);
  const runtimeMarker = "/lelamp_runtime/workspace/";
  const runtimeIndex = normalized.lastIndexOf(runtimeMarker);
  if (runtimeIndex >= 0) return normalized.slice(runtimeIndex + runtimeMarker.length);
  return normalized.replace(/^\.\//, "");
}

function uniqueDocumentArtifacts(artifacts: DocumentArtifact[]) {
  const seen = new Set<string>();
  return artifacts.filter((artifact) => {
    if (seen.has(artifact.workspaceName)) return false;
    seen.add(artifact.workspaceName);
    return true;
  });
}

function isDocumentAnalysisSupported(pathValue: string) {
  const suffix = fileSuffix(pathValue);
  return new Set([
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".log",
    ".html",
    ".xml",
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
  ]).has(suffix);
}

function fileSuffix(pathValue: string) {
  const name = String(pathValue || "").replace(/\\/g, "/").split("/").pop() || "";
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

function compactDisplayPath(value?: string) {
  const text = String(value ?? "");
  if (!text) return "-";
  const normalized = text.replace(/\\/g, "/");
  const workspaceMarker = "/workspace/";
  const workspaceIndex = normalized.lastIndexOf(workspaceMarker);
  if (workspaceIndex >= 0) return normalized.slice(workspaceIndex + 1);
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 2) return normalized;
  return `.../${parts.slice(-2).join("/")}`;
}

function scanEnhancement(scanResult: ScanResult | null): Record<string, unknown> | null {
  if (!scanResult) return null;
  if (scanResult.enhancement && typeof scanResult.enhancement === "object") return scanResult.enhancement;
  return scanResult;
}

function scanCorrection(scanResult: ScanResult | null): Record<string, unknown> {
  const enhancement = scanEnhancement(scanResult);
  const correction = enhancement?.auto_corner_correction;
  return correction && typeof correction === "object" ? correction as Record<string, unknown> : {};
}

function scanWorkspaceValue(scanResult: ScanResult | null, key: string): string {
  const enhancement = scanEnhancement(scanResult);
  const value = enhancement?.[key];
  return typeof value === "string" ? value : "";
}

function workspaceImageUrl(workspaceName: string): string {
  const token = readToken();
  const params = new URLSearchParams({ file: workspaceName });
  if (token) params.set("token", token);
  return `/api/scene/image?${params.toString()}`;
}

function ScanArtifactLinks({ scanResult }: { scanResult: ScanResult | null }) {
  const correction = scanCorrection(scanResult);
  const cornerPreview = scanWorkspaceValue(scanResult, "corner_preview_workspace_name") || String(correction.preview_workspace_name ?? "");
  const colorScan = scanWorkspaceValue(scanResult, "color_workspace_name") || scanWorkspaceValue(scanResult, "enhanced_workspace_name");
  const ocrScan = scanWorkspaceValue(scanResult, "ocr_workspace_name");
  const links = [
    ["四角预览", cornerPreview],
    ["矫正扫描图", colorScan],
    ["OCR 增强图", ocrScan],
  ].filter(([, workspaceName]) => workspaceName);
  if (!links.length) return null;
  return (
    <div className="scan-artifact-links">
      {links.map(([label, workspaceName]) => (
        <a className="ghost-button" key={label} href={workspaceImageUrl(workspaceName)} target="_blank" rel="noreferrer">
          {label}
        </a>
      ))}
    </div>
  );
}

async function fileToDataUrl(file: File): Promise<string> {
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("读取图片失败"));
    reader.onload = () => {
      if (typeof reader.result !== "string") {
        reject(new Error("无法生成图片数据"));
        return;
      }
      resolve(reader.result);
    };
    reader.readAsDataURL(file);
  });
}
