import { AlertTriangle, Camera, FileImage, FileText, ListTree, Mail, ScanLine, Table2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { apiErrorMessage } from "../api/client";
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
  runScanProcess,
} from "../api/documents";
import { getSecurity } from "../api/security";
import { getSharedFiles, getSharedPreview } from "../api/shared";
import type { DocumentAdapter, DocumentResult, ScanResult, SecurityStatus, SharedFile, SharedPreviewResponse } from "../api/types";
import { Card } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { mockSecurity } from "../data/mockSecurity";
import "./pages.css";

export function DocumentsPage() {
  const [searchParams] = useSearchParams();
  const [source, setSource] = useState("shared_inbox");
  const [files, setFiles] = useState<SharedFile[]>([]);
  const [selected, setSelected] = useState("");
  const [preview, setPreview] = useState<SharedPreviewResponse | null>(null);
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
      const [filesResult, adaptersResult, securityResult] = await Promise.all([
        getSharedFiles({ page_size: 100 }),
        getDocumentAdaptersStatus(),
        getSecurity(),
      ]);
      const nextFiles = filesResult.data.files ?? [];
      setFiles(nextFiles);
      setAdapters(adaptersResult.data.adapters);
      setSecurity(securityResult.data);
      setSelected((current) => current || nextFiles[0]?.relative_path || "");
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

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
    void getSharedPreview(selected)
      .then((response) => setPreview(response.data))
      .catch((err) => setPreview({ status: "blocked", workspace_name: selected, name: selected, size_bytes: 0, text: apiErrorMessage(err) }));
  }, [selected]);

  const selectedFile = useMemo(() => files.find((file) => file.relative_path === selected), [files, selected]);

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
      <PageHeader title="文档处理" description="上传或选择文件，执行总结、风险识别、表格提取和实体文档扫描" actions={<button className="ghost-button" onClick={() => void load()}>刷新</button>} />
      <div className="page-grid">
        {error && <div className="danger-panel">操作失败：{error}</div>}
        <div className="grid-2">
          <Card title="选择文档来源" subtitle="默认只处理用户主动上传或拖入的文件。">
            <div className="source-selector">
              {["shared_inbox", "workspace"].map((item) => (
                <button className={source === item ? "selected" : ""} key={item} onClick={() => setSource(item)}>
                  <FileText size={20} />
                  <strong>{item === "shared_inbox" ? "上传文件" : "工作区文件"}</strong>
                  <span>{item === "shared_inbox" ? "用户拖入或上传的资料" : "由系统生成的处理结果"}</span>
                  <StatusBadge status={item === "shared_inbox" ? "available" : "adapter_ready"} label={item === "shared_inbox" ? "可用" : "受限"} />
                </button>
              ))}
            </div>
          </Card>
          <Card title="选择文件">
            <select className="select" value={selected} onChange={(event) => setSelected(event.target.value)}>
              {files.map((file) => <option value={file.relative_path} key={file.relative_path}>{file.relative_path}</option>)}
            </select>
            <div className="selected-file">
              <strong>{selectedFile?.name ?? "暂无文件"}</strong>
              <span className="small">{selected ? compactDisplayPath(selected) : "请先上传文件"}</span>
              <span className="small muted">{selectedFile?.mime_type ?? "-"} · {selectedFile?.size_label ?? "-"} · {selectedFile?.uploaded_at ?? "-"}</span>
            </div>
            <div className="row">
              <button className="ghost-button" onClick={() => void load()}>更换/刷新文件</button>
              <button className="ghost-button" disabled>不提供服务器全盘浏览</button>
            </div>
          </Card>
        </div>

        <div className="grid-5">
          <Card className="action-card" title="文档分析">
            <FileText size={26} />
            <p>结构解析、段落与要点识别</p>
            <button className="ghost-button" onClick={() => void run("文档分析", runDocumentAnalyze)} disabled={!selected}>开始分析</button>
          </Card>
          <Card className="action-card" title="汇报提纲">
            <ListTree size={26} />
            <p>调用模型整理成汇报结构</p>
            <button className="ghost-button" onClick={() => void run("汇报提纲", (file) => runDocumentReportOutline(file, selectedFile?.name ?? "汇报提纲"))} disabled={!selected}>生成提纲</button>
          </Card>
          <Card className="action-card" title="风险标记">
            <AlertTriangle size={26} />
            <p>识别条款风险与合规问题</p>
            <button className="ghost-button" onClick={() => void run("风险扫描", runDocumentRisks)} disabled={!selected}>运行风险扫描</button>
          </Card>
          <Card className="action-card" title="关键数据表">
            <Table2 size={26} />
            <p>调用模型抽取关键数据为 CSV</p>
            <button className="ghost-button" onClick={() => void run("表格提取", runDocumentTableExtract)} disabled={!selected}>运行提取</button>
          </Card>
          <Card className="action-card" title="扫描状态">
            <ScanLine size={26} />
            <p>摄像头采集、图像增强、文字与结构识别</p>
            <button className="ghost-button" onClick={() => setMessage(`扫描：${friendlyStatus(adapters.scan_capture ?? "adapter_ready")} / 文字识别：${friendlyStatus(adapters.ocr ?? "unavailable")}`)}>检查状态</button>
          </Card>
        </div>

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
              <button className="ghost-button" onClick={() => void processSelectedScan()} disabled={scanBusy || !selected}>
                <ScanLine size={16} /> 处理当前图片
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
              <span>自动边界</span><StatusBadge status={scanResult?.enhancement ? "completed" : "adapter_ready"} />
              <span>视觉校正</span><StatusBadge status={scanResult?.enhancement ? "completed" : "adapter_ready"} />
              <span>去阴影/增强</span><StatusBadge status={scanResult?.enhancement ? "completed" : "adapter_ready"} />
              <span>文字/结构识别</span><StatusBadge status={scanResult?.status ?? adapters.ocr ?? "backend_missing"} />
              <span>视觉识别</span><StatusBadge status={adapters.vision_ocr ?? "backend_missing"} />
              <span>自动拍照候选</span><StatusBadge status={String(captureReadiness?.status ?? "pending")} />
            </div>
            <p className="small muted">识别能力会优先使用已配置的视觉模型；不可用时只完成图像增强和待处理记录，不伪造识别结果。</p>
          </Card>
          <Card title="扫描结果" action={<StatusBadge status={scanResult?.status ?? "pending"} />}>
            <div className="definition-grid">
              <span>原图</span><strong>{scanResult?.source_workspace_name || scanResult?.image ? "已保存" : "-"}</strong>
              <span>增强图</span><strong>{scanResult?.enhancement?.enhanced_workspace_name ? "已生成" : "-"}</strong>
              <span>识别文本</span><strong>{scanResult?.text_path ? "已生成" : "-"}</strong>
              <span>结构识别</span><strong>{scanResult?.structure_path ? "已生成" : "-"}</strong>
              <span>表格文件</span><strong>{scanResult?.table_paths?.length ? `${scanResult.table_paths.length} 个` : "-"}</strong>
              <span>拍照候选</span><strong>{String(captureReadiness?.stable_score ?? "-")}</strong>
            </div>
            <details className="advanced-panel">
              <summary>扫描诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview scan-result-preview">{JSON.stringify(scanResult ?? { status: "pending", message: "等待拍照或上传实体文档。" }, null, 2)}</pre>
                {captureReadiness && <pre className="json-preview scan-result-preview">{JSON.stringify(captureReadiness, null, 2)}</pre>}
              </div>
            </details>
          </Card>
        </div>

        <div className="grid-3">
          <Card title="文档整理成汇报提纲" action={<StatusBadge status={adapters.report_outline ?? "backend_missing"} />}>
            <div className="office-api-card">
              <ListTree size={22} />
              <span>调用模型读取已授权文档，生成 Markdown 汇报提纲和 PPT 页级结构。</span>
              <button className="primary-button" onClick={() => void run("汇报提纲", (file) => runDocumentReportOutline(file, selectedFile?.name ?? "汇报提纲"))} disabled={!selected}>生成汇报提纲</button>
            </div>
          </Card>
          <Card title="关键数据提取成表格" action={<StatusBadge status={adapters.table_extractor ?? "backend_missing"} />}>
            <div className="office-api-card">
              <Table2 size={22} />
              <span>调用模型抽取指标、事实、责任人、依据，写入 CSV 文件。</span>
              <button className="primary-button" onClick={() => void run("关键数据表", runDocumentTableExtract)} disabled={!selected}>生成关键数据表</button>
            </div>
          </Card>
          <Card title="会议纪要生成邮件" action={<StatusBadge status={adapters.meeting_email_draft ?? "backend_missing"} />}>
            <div className="office-api-card">
              <Mail size={22} />
              <span>在 Meeting 页面选择 transcript 后生成会后邮件草稿，不自动发送。</span>
              <a className="ghost-button office-api-link" href="/meeting">打开 Meeting 页面</a>
            </div>
          </Card>
        </div>

        <div className="documents-grid">
          <Card title="文档预览">
            <div className="doc-preview">
              <div className="doc-page">
                <strong>{preview?.name ?? "等待预览"}</strong>
                {preview?.document_text_backend && <span className="small muted">文档已解析</span>}
                {preview?.status === "ok" ? <pre className="json-preview">{preview.text}</pre> : <p>{preview?.text ?? "二进制文件无法直接预览，可下载或等待解析。"}</p>}
                {preview && <StatusBadge status={preview.status} />}
              </div>
            </div>
          </Card>
          <div className="stack">
            <Card title="结构化分析结果" action={<StatusBadge status={result?.status ?? "pending"} />}>
              <div className="definition-grid">
                <span>状态</span><StatusBadge status={result?.status ?? "pending"} />
                <span>输出数</span><strong>{result?.outputs?.length ?? 0}</strong>
                <span>摘要</span><strong>{result?.summary ?? message}</strong>
              </div>
            </Card>
            <Card title="关键元数据">
              <div className="list-rows compact">
                {Object.entries(result?.metadata ?? {}).slice(0, 8).map(([key, value]) => (
                  <div className="row-between" key={key}>
                    <span>{key}</span>
                    <strong>{String(value)}</strong>
                  </div>
                ))}
                {!Object.keys(result?.metadata ?? {}).length && <span className="small muted">等待分析结果。</span>}
              </div>
            </Card>
          </div>
          <div className="stack">
            <Card title="风险标记" action={<StatusBadge status={result?.risks?.length ? "warning" : "adapter_ready"} label={`${result?.risks?.length ?? 0} 项风险`} />}>
              <div className="risk-list">
                {(result?.risks ?? []).map((risk, index) => <div key={index}><StatusBadge status="warning" label={String(risk.level ?? "risk")} /> {String(risk.marker ?? JSON.stringify(risk))}</div>)}
                {!result?.risks?.length && <div className="blue-note">尚未发现风险或未运行风险扫描。</div>}
              </div>
            </Card>
            <Card title="表格提取预览" action={<StatusBadge status={result?.adapter_status?.table_extractor ?? "backend_missing"} />}>
              <div className="blue-note">{result?.table_path || scanResult?.table_paths?.length ? "表格结果已生成，可在输出文件中查看。" : String(result?.summary ?? "表格结构识别未必可用，状态以实际处理结果为准。")}</div>
            </Card>
            <Card title="输出文件">
              <div className="definition-grid">
                <span>汇报提纲</span><strong>{result?.outline_path ? "已生成" : "-"}</strong>
                <span>关键数据表</span><strong>{result?.table_path ? "已生成" : "-"}</strong>
                <span>处理方式</span><strong>{result?.model ? "模型处理" : "-"}</strong>
                <span>扫描摘要</span><strong>{String(scanResult?.summary ?? "-")}</strong>
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
  };
  return labels[value] ?? (value || "等待");
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
