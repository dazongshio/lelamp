import { Camera, ChevronLeft, ChevronRight, ExternalLink, MonitorPlay, Presentation, ScanLine, Square, TestTube2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiErrorMessage } from "../api/client";
import {
  analyzeCalibrationCapture,
  applyCalibrationProfile,
  createCalibrationPattern,
  createProjectionCard,
  getProjectionLatest,
  getProjectionDisplayProfile,
  getProjectionServiceStatus,
  projectMarkdownFile,
  projectPptxSession,
  startProjectionService,
  stopProjectionService,
  summarizePptPage,
  updateProjectionDisplayProfile,
} from "../api/projection";
import type { PptPageSummaryResponse, ProjectionCalibrationResponse, ProjectionCard, ProjectionDisplayProfileResponse, ProjectionLatestResponse, ProjectionMarkdownFileResponse, ProjectionPptxSessionResponse, ProjectionServiceStatus } from "../api/types";
import { Card } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { ProjectionPreview } from "../components/ProjectionPreview";
import { StatusBadge } from "../components/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import {
  cacheBustedUrl,
  compactDisplayPath,
  formatProjectionTime,
  friendlyCardMode,
  friendlyDisplayMode,
  friendlyStatus,
  metricStatus,
} from "./projectionPageUtils";
import "./pages.css";

const templateCards: ProjectionCard[] = [
  { id: "tpl-status", title: "当前状态", subtitle: "会议准备中", mode: "status", accent: "green", created_at: "14:32", resolution: "1920 × 1080" },
  { id: "tpl-countdown", title: "会议开始倒计时", subtitle: "05:00", mode: "countdown", accent: "blue", created_at: "14:32", resolution: "1920 × 1080" },
  { id: "tpl-confirm", title: "会议已开始", subtitle: "请保持安静，感谢配合", mode: "confirmation", accent: "purple", created_at: "14:32", resolution: "1920 × 1080" },
  { id: "tpl-action", title: "请将手机调至静音", subtitle: "感谢您的配合", mode: "action_card", accent: "yellow", created_at: "14:32", resolution: "1920 × 1080" },
];

export function ProjectionPage() {
  const [selected, setSelected] = useState<ProjectionCard>(templateCards[2]);
  const [latest, setLatest] = useState<ProjectionLatestResponse | null>(null);
  const [service, setService] = useState<ProjectionServiceStatus | null>(null);
  const [displayProfile, setDisplayProfile] = useState<ProjectionDisplayProfileResponse | null>(null);
  const [pptSummary, setPptSummary] = useState<PptPageSummaryResponse | null>(null);
  const [pptxSession, setPptxSession] = useState<ProjectionPptxSessionResponse | null>(null);
  const [markdownProjection, setMarkdownProjection] = useState<ProjectionMarkdownFileResponse | null>(null);
  const [calibration, setCalibration] = useState<ProjectionCalibrationResponse | null>(null);
  const [pptBusy, setPptBusy] = useState(false);
  const [pptxBusy, setPptxBusy] = useState(false);
  const [markdownBusy, setMarkdownBusy] = useState(false);
  const [calibrationBusy, setCalibrationBusy] = useState(false);
  const [pptTitle, setPptTitle] = useState("PPT 当前页总结");
  const [pptxPath, setPptxPath] = useState("");
  const [pptxTitle, setPptxTitle] = useState("PPT 文件投影");
  const [pptxSlideIndex, setPptxSlideIndex] = useState(1);
  const [markdownPath, setMarkdownPath] = useState("meetings/");
  const [markdownTitle, setMarkdownTitle] = useState("Markdown 投影");
  const [calibrationTitle, setCalibrationTitle] = useState("投影校准测试图");
  const [notice, setNotice] = useState("外接显示器模式");
  const [ambientLux, setAmbientLux] = useState(320);
  const [brightness, setBrightness] = useState(1);
  const [contrast, setContrast] = useState(1);
  const [scale, setScale] = useState(1);
  const [keystoneX, setKeystoneX] = useState(0);
  const [keystoneY, setKeystoneY] = useState(0);
  const [error, setError] = useState("");
  const calibrationCameraRef = useRef<HTMLInputElement | null>(null);
  const calibrationUploadRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    try {
      const [latestResult, serviceResult, profileResult] = await Promise.all([getProjectionLatest(), getProjectionServiceStatus(), getProjectionDisplayProfile()]);
      setLatest(latestResult.data);
      setService(serviceResult.data);
      setDisplayProfile(profileResult.data);
      setBrightness(profileResult.data.profile.brightness);
      setContrast(profileResult.data.profile.contrast);
      setScale(profileResult.data.profile.scale);
      setKeystoneX(profileResult.data.profile.keystone_x);
      setKeystoneY(profileResult.data.profile.keystone_y);
      if (typeof profileResult.data.profile.ambient_lux === "number") setAmbientLux(profileResult.data.profile.ambient_lux);
      const card = latestResult.data.cards?.[0];
      if (card) setSelected(card);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  usePolling(load, 5000, true);

  async function choose(card: ProjectionCard) {
    setSelected(card);
    setError("");
    setNotice("生成卡片中...");
    try {
      const response = await createProjectionCard(card);
      setNotice(`已写入投影输出目录：${String(response.data.path ?? response.data.status)}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setNotice("生成失败");
    }
  }

  async function serviceAction(action: "start" | "stop") {
    setError("");
    try {
      const response = action === "start" ? await startProjectionService() : await stopProjectionService();
      setNotice(`${action}：${String(response.data.status ?? response.data.message ?? "ok")}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function summarizeCurrentPptPage() {
    setError("");
    setPptBusy(true);
    setNotice("请选择正在播放 PPT 的窗口或屏幕...");
    try {
      const imageDataUrl = await captureOneScreenFrame();
      setNotice("正在总结 PPT 当前页...");
      const response = await summarizePptPage({
        image_data_url: imageDataUrl,
        title: pptTitle,
        render_projection: true,
        source: "browser_screen_capture",
      });
      setPptSummary(response.data);
      setNotice(`PPT 总结状态：${response.data.status}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setNotice("PPT 当前页总结失败");
    } finally {
      setPptBusy(false);
    }
  }

  async function summarizeCurrentPptxTextPage() {
    setError("");
    setPptBusy(true);
    setNotice("正在总结 PPTX 当前页文本...");
    try {
      const response = await summarizePptPage({
        file_path: pptxPath,
        slide_index: pptxSession?.slide_index ?? pptxSlideIndex,
        title: pptTitle,
        render_projection: true,
        source: "workspace_pptx_text",
      });
      setPptSummary(response.data);
      setNotice(`PPTX 文本总结状态：${response.data.status}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setNotice("PPTX 文本总结失败");
    } finally {
      setPptBusy(false);
    }
  }

  async function projectPptx(action: "show" | "next" | "previous" = "show") {
    setError("");
    setPptxBusy(true);
    setNotice(action === "show" ? "正在读取并投影 PPT 文件..." : "正在切换 PPT 投影片...");
    try {
      const response = await projectPptxSession({
        file_path: pptxPath,
        title: pptxTitle,
        slide_index: pptxSession?.slide_index ?? pptxSlideIndex,
        action,
      });
      setPptxSession(response.data);
      setPptxSlideIndex(response.data.slide_index || 1);
      setNotice(`PPT 文件投影：${response.data.slide_index}/${response.data.slide_count}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setNotice("PPT 文件投影失败");
    } finally {
      setPptxBusy(false);
    }
  }

  async function projectWorkspaceMarkdown() {
    setError("");
    setMarkdownBusy(true);
    setNotice("正在投影 Markdown...");
    try {
      const response = await projectMarkdownFile({
        file_path: markdownPath,
        title: markdownTitle,
      });
      setMarkdownProjection(response.data);
      setNotice(`Markdown 投影状态：${response.data.status}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setNotice("Markdown 投影失败");
    } finally {
      setMarkdownBusy(false);
    }
  }

  async function createCalibration() {
    setError("");
    setCalibrationBusy(true);
    setNotice("正在生成校准测试图...");
    try {
      const response = await createCalibrationPattern(calibrationTitle);
      setCalibration(response.data);
      setNotice(`校准图状态：${response.data.status}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setNotice("校准图生成失败");
    } finally {
      setCalibrationBusy(false);
    }
  }

  async function analyzeCalibrationFile(file: File) {
    setError("");
    setCalibrationBusy(true);
    setNotice("正在分析校准照片...");
    try {
      const imageDataUrl = await fileToDataUrl(file);
      const response = await analyzeCalibrationCapture({
        image_data_url: imageDataUrl,
        title: calibrationTitle,
      });
      setCalibration(response.data);
      setNotice(`校准分析状态：${response.data.status}`);
    } catch (err) {
      setError(apiErrorMessage(err));
      setNotice("校准照片分析失败");
    } finally {
      setCalibrationBusy(false);
    }
  }

  async function applyDisplayProfile(mode: "manual" | "ambient" | "calibration") {
    setError("");
    try {
      const response = await updateProjectionDisplayProfile({
        mode,
        ambient_lux: mode === "manual" ? null : ambientLux,
        brightness,
        contrast,
        scale,
        keystone_x: keystoneX,
        keystone_y: keystoneY,
        calibration: mode === "calibration" && calibration ? calibration : undefined,
      });
      setDisplayProfile(response.data);
      setBrightness(response.data.profile.brightness);
      setContrast(response.data.profile.contrast);
      setScale(response.data.profile.scale);
      setKeystoneX(response.data.profile.keystone_x);
      setKeystoneY(response.data.profile.keystone_y);
      setNotice(`显示设置已应用：${friendlyDisplayMode(response.data.profile.mode)}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setNotice("显示 profile 更新失败");
    }
  }

  async function applyCalibrationAutomatically() {
    if (!calibration) return;
    setError("");
    setCalibrationBusy(true);
    setNotice("正在根据校准分析自动应用显示设置...");
    try {
      const response = await applyCalibrationProfile({
        calibration,
        ambient_lux: ambientLux,
      });
      setDisplayProfile(response.data);
      setBrightness(response.data.profile.brightness);
      setContrast(response.data.profile.contrast);
      setScale(response.data.profile.scale);
      setKeystoneX(response.data.profile.keystone_x);
      setKeystoneY(response.data.profile.keystone_y);
      setNotice(`校准设置已应用：${response.data.profile.note ?? friendlyDisplayMode(response.data.profile.mode)}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
      setNotice("校准 profile 应用失败");
    } finally {
      setCalibrationBusy(false);
    }
  }

  const recentCards = latest?.cards ?? [];
  const latestCard = recentCards[0] ?? null;
  const activeCard = selected.id.startsWith("tpl-") ? latestCard : selected;
  const activeHtml = activeCard?.html ?? latest?.html ?? "";
  const activePath = activeCard?.path ?? latest?.path;
  const activeName = latest?.name ?? (activePath ? compactDisplayPath(activePath) : "");
  const activeTitle = activeCard?.title ?? activeName ?? "等待投影结果";
  const previewUrl = service?.preview_url ?? latest?.path ?? "";
  const previewFrameUrl = service?.preview_url ? cacheBustedUrl(service.preview_url, latest?.mtime) : "";
  const latestUpdatedAt = formatProjectionTime(latest?.mtime);

  return (
    <>
      <PageHeader title="投影" description="选择要展示的内容，然后开始投影" actions={<button className="ghost-button" onClick={() => void load()}>刷新状态</button>} />
      <div className="page-grid projection-user-page">
        {error && <div className="danger-panel">操作失败：{error}</div>}
        <section className={`projection-user-hero ${service?.kiosk_running ? "is-running" : ""}`}>
          <div className="projection-user-hero__status">
            <span className="projection-user-hero__icon"><MonitorPlay size={25} /></span>
            <div>
              <span className="projection-user-eyebrow">当前状态</span>
              <h2>{service?.kiosk_running ? "正在投影" : service?.physical_projector === "connected" ? "投影设备已连接" : "等待连接投影设备"}</h2>
              <p>{service?.kiosk_running ? `正在展示：${activeTitle}` : "准备好内容后，点击右侧按钮即可开始。"}</p>
            </div>
          </div>
          <div className="projection-user-hero__actions">
            {service?.kiosk_running ? (
              <button className="projection-stop-button" onClick={() => void serviceAction("stop")}><Square size={17} />结束投影</button>
            ) : (
              <button className="projection-start-button" onClick={() => void serviceAction("start")}><MonitorPlay size={18} />开始投影</button>
            )}
            {previewUrl.startsWith("http") && <a className="projection-preview-link" href={previewUrl} target="_blank" rel="noreferrer">单独打开预览 <ExternalLink size={14} /></a>}
          </div>
        </section>

        <section className="projection-user-steps" aria-label="投影操作步骤">
          <div className="active"><b>1</b><span><strong>选择内容</strong><small>卡片、文档或演示文稿</small></span></div>
          <div><b>2</b><span><strong>确认预览</strong><small>检查画面是否正确</small></span></div>
          <div className={service?.kiosk_running ? "active" : ""}><b>3</b><span><strong>开始投影</strong><small>内容显示到投影设备</small></span></div>
        </section>

        <div className="projection-main projection-main--user">
          <Card
            title="投影画面"
            subtitle={latestUpdatedAt ? `最后更新于 ${latestUpdatedAt}` : "开始前请先确认画面内容"}
            action={<StatusBadge status={service?.kiosk_running ? "available" : "adapter_ready"} label={service?.kiosk_running ? "投影中" : "预览"} />}
          >
            <div className="projection-live-result">
              <div className="projection-live-result__frame">
                {previewFrameUrl ? (
                  <iframe title="当前投影预览" src={previewFrameUrl} />
                ) : activeHtml ? (
                  <div className="projection-html" dangerouslySetInnerHTML={{ __html: activeHtml }} />
                ) : (
                  <ProjectionPreview card={selected} />
                )}
              </div>
              <div className="projection-user-now">
                <div><span>当前内容</span><strong>{activeTitle}</strong></div>
                <div><span>设备</span><strong>{service?.physical_projector === "connected" ? "投影设备已连接" : "预览模式"}</strong></div>
                <span>{notice}</span>
              </div>
            </div>
          </Card>
          <Card title="快速展示" subtitle="点击一种内容，立即更新投影画面">
            <div className="projection-template-list">
              {templateCards.map((card) => (
                <button key={card.id} onClick={() => void choose(card)} className={selected.id === card.id ? "selected" : ""}>
                  <ProjectionPreview card={card} compact />
                  <strong>{friendlyCardMode(card.mode)}</strong>
                  <span>{card.subtitle}</span>
                </button>
              ))}
            </div>
          </Card>
        </div>

        <details className="projection-settings-group">
          <summary><span><strong>画面调整</strong><small>亮度、对比度、缩放和梯形校正</small></span><ChevronRight size={18} /></summary>
        <Card title="画面调整" subtitle="仅在画面过暗、变形或超出幕布时使用" action={<StatusBadge status={displayProfile?.status ?? "pending"} />}>
          <div className="display-profile-grid">
            <label>
              <span>环境亮度 lux</span>
              <input className="input" type="number" value={ambientLux} min={0} max={2000} onChange={(event) => setAmbientLux(Number(event.target.value))} />
            </label>
            <label>
              <span>亮度</span>
              <input className="input" type="number" step="0.05" min="0.55" max="1.65" value={brightness} onChange={(event) => setBrightness(Number(event.target.value))} />
            </label>
            <label>
              <span>对比度</span>
              <input className="input" type="number" step="0.05" min="0.7" max="1.6" value={contrast} onChange={(event) => setContrast(Number(event.target.value))} />
            </label>
            <label>
              <span>缩放</span>
              <input className="input" type="number" step="0.01" min="0.82" max="1.08" value={scale} onChange={(event) => setScale(Number(event.target.value))} />
            </label>
            <label>
              <span>横向梯形</span>
              <input className="input" type="number" step="0.5" min="-12" max="12" value={keystoneX} onChange={(event) => setKeystoneX(Number(event.target.value))} />
            </label>
            <label>
              <span>纵向梯形</span>
              <input className="input" type="number" step="0.5" min="-12" max="12" value={keystoneY} onChange={(event) => setKeystoneY(Number(event.target.value))} />
            </label>
          </div>
          <div className="row display-profile-actions">
            <button className="primary-button" onClick={() => void applyDisplayProfile("ambient")}>按环境亮度自适应</button>
            <button className="ghost-button" onClick={() => void applyDisplayProfile("calibration")} disabled={!calibration}>按校准结果应用</button>
            <button className="ghost-button" onClick={() => void applyDisplayProfile("manual")}>手动应用</button>
          </div>
          <div className="definition-grid">
            <span>当前模式</span><strong>{friendlyDisplayMode(displayProfile?.profile.mode)}</strong>
            <span>说明</span><strong className="definition-grid__wide-value">{displayProfile?.profile.note ?? displayProfile?.message ?? "-"}</strong>
          </div>
        </Card>
        </details>

        <Card title="总结这一页 PPT" subtitle="选择 PPT 窗口或屏幕，截取当前页并生成可投影摘要" action={<StatusBadge status={pptSummary?.status ?? "pending"} />}>
          <div className="ppt-summary-tool">
            <div className="row">
              <Presentation size={22} />
              <input className="input" value={pptTitle} onChange={(event) => setPptTitle(event.target.value)} placeholder="总结标题" />
              <button className="primary-button" onClick={() => void summarizeCurrentPptPage()} disabled={pptBusy}>
                {pptBusy ? "总结中..." : "捕获并总结当前页"}
              </button>
              <button className="ghost-button" onClick={() => void summarizeCurrentPptxTextPage()} disabled={pptBusy || !pptxPath.trim()}>
                总结已上传 PPTX 当前页
              </button>
            </div>
            <div className="definition-grid">
              <span>总结文件</span><strong>{compactDisplayPath(pptSummary?.summary_path)}</strong>
              <span>截图文件</span><strong>{compactDisplayPath(pptSummary?.screenshot_path)}</strong>
              <span>投影文件</span><strong>{compactDisplayPath(pptSummary?.projection_path)}</strong>
              <span>PPTX 页码</span><strong>{pptSummary?.slide_index && pptSummary?.slide_count ? `${pptSummary.slide_index}/${pptSummary.slide_count}` : "-"}</strong>
            </div>
            <pre className="json-preview ppt-summary-preview">{pptSummary?.summary ?? pptSummary?.message ?? "等待捕获 PPT 当前页。"}</pre>
            <p className="small muted">屏幕捕获需要用户主动选择 PPT 放映窗口；PPTX 文本总结只读取用户指定文件和页码。</p>
          </div>
        </Card>

        <Card title="投影 PPT 文件" subtitle="读取已授权的 .pptx，按页生成可投影文本版并支持翻页" action={<StatusBadge status={pptxSession?.status ?? "pending"} />}>
          <div className="ppt-summary-tool pptx-session-tool">
            <div className="row">
              <Presentation size={22} />
              <input className="input" value={pptxPath} onChange={(event) => setPptxPath(event.target.value)} placeholder="demo.pptx" />
              <input className="input" value={pptxTitle} onChange={(event) => setPptxTitle(event.target.value)} placeholder="投影标题" />
              <input className="input pptx-slide-input" type="number" min={1} value={pptxSlideIndex} onChange={(event) => setPptxSlideIndex(Number(event.target.value) || 1)} aria-label="演示文稿页码" />
              <button className="primary-button" onClick={() => void projectPptx("show")} disabled={pptxBusy || !pptxPath.trim()}>
                {pptxBusy ? "投影中..." : "投影此页"}
              </button>
            </div>
            <div className="row pptx-slide-actions">
              <button className="ghost-button" onClick={() => void projectPptx("previous")} disabled={pptxBusy || !pptxSession || pptxSession.slide_index <= 1}>
                <ChevronLeft size={16} /> 上一页
              </button>
              <button className="ghost-button" onClick={() => void projectPptx("next")} disabled={pptxBusy || !pptxSession || pptxSession.slide_index >= pptxSession.slide_count}>
                下一页 <ChevronRight size={16} />
              </button>
              <span className="small muted">
                {pptxSession ? `${pptxSession.slide_index}/${pptxSession.slide_count}` : "等待投影 PPT 文件"}
              </span>
            </div>
            <div className="definition-grid">
              <span>源文件</span><strong>{pptxSession?.source_workspace_name ? "已选择" : "等待选择"}</strong>
              <span>投影文件</span><strong>{pptxSession?.projection_path ?? pptxSession?.path ? "已生成" : "等待生成"}</strong>
              <span>预览状态</span><strong>{pptxSession?.preview_url || previewUrl ? "可打开" : "等待预览"}</strong>
              <span>当前页</span><strong>{pptxSession?.current_slide?.title ?? "-"}</strong>
            </div>
            <pre className="json-preview ppt-summary-preview">{pptxSession?.current_slide?.text || pptxSession?.message || "上传 .pptx 后选择文件并投影。"}</pre>
            <p className="small muted">此功能投影 PPTX 中可提取的文字内容；需要完整视觉版式时，使用上方“捕获并总结当前页”选择正在播放的 PPT 窗口。</p>
          </div>
        </Card>

        <Card title="投影 Markdown 文件" subtitle="读取已授权的 Markdown 或文本文件，并同步到外接显示器预览" action={<StatusBadge status={markdownProjection?.status ?? "pending"} />}>
          <div className="stack">
            <div className="row">
              <Presentation size={22} />
              <input className="input" value={markdownPath} onChange={(event) => setMarkdownPath(event.target.value)} placeholder="选择会议纪要或 Markdown 文件" />
              <input className="input" value={markdownTitle} onChange={(event) => setMarkdownTitle(event.target.value)} placeholder="投影标题" />
              <button className="primary-button" onClick={() => void projectWorkspaceMarkdown()} disabled={markdownBusy || !markdownPath.trim()}>
                {markdownBusy ? "投影中..." : "投影文件"}
              </button>
            </div>
            <div className="definition-grid">
              <span>源文件</span><strong>{markdownProjection?.source_workspace_name ? "已选择" : "等待选择"}</strong>
              <span>投影文件</span><strong>{markdownProjection?.projection_path ?? markdownProjection?.path ? "已生成" : "等待生成"}</strong>
              <span>预览状态</span><strong>{markdownProjection?.preview_url || previewUrl ? "可打开" : "等待预览"}</strong>
              <span>字符数</span><strong>{typeof markdownProjection?.chars === "number" ? markdownProjection.chars : "-"}</strong>
            </div>
            <p className="small muted">PPT 文件请使用上方“总结这一页 PPT”：由用户主动选择屏幕或窗口，只截取当前页，不读取办公电脑任意目录。</p>
          </div>
        </Card>

        <Card title="投影/外接显示器校准" subtitle="生成校准测试图，拍摄显示画面后分析亮度、清晰度、梯形和遮挡" action={<StatusBadge status={calibration?.status ?? "pending"} />}>
          <div className="calibration-tool">
            <div className="row calibration-actions">
              <TestTube2 size={22} />
              <input className="input" value={calibrationTitle} onChange={(event) => setCalibrationTitle(event.target.value)} />
              <button className="primary-button" onClick={() => void createCalibration()} disabled={calibrationBusy}>
                <ScanLine size={16} /> 生成校准图
              </button>
              <button className="ghost-button" onClick={() => calibrationCameraRef.current?.click()} disabled={calibrationBusy}>
                <Camera size={16} /> 拍照分析
              </button>
              <button className="ghost-button" onClick={() => calibrationUploadRef.current?.click()} disabled={calibrationBusy}>
                <Upload size={16} /> 上传照片
              </button>
              <button className="ghost-button" onClick={() => void applyCalibrationAutomatically()} disabled={calibrationBusy || !calibration?.analysis_path}>
                自动应用校正
              </button>
            </div>
            <input
              ref={calibrationCameraRef}
              className="hidden-file-input"
              type="file"
              accept="image/*"
              capture="environment"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) void analyzeCalibrationFile(file);
              }}
            />
            <input
              ref={calibrationUploadRef}
              className="hidden-file-input"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) void analyzeCalibrationFile(file);
              }}
            />
            <div className="calibration-metrics">
              <div><span>亮度</span><strong>{metricStatus(calibration?.brightness)}</strong></div>
              <div><span>清晰度</span><strong>{metricStatus(calibration?.focus)}</strong></div>
              <div><span>梯形</span><strong>{metricStatus(calibration?.keystone)}</strong></div>
              <div><span>遮挡</span><strong>{metricStatus(calibration?.obstruction)}</strong></div>
            </div>
            <div className="definition-grid">
              <span>校准图</span><strong>{compactDisplayPath(calibration?.path)}</strong>
              <span>拍照文件</span><strong>{compactDisplayPath(calibration?.capture_path)}</strong>
              <span>分析结果</span><strong>{calibration?.analysis_path ? "已生成" : "-"}</strong>
              <span>报告</span><strong>{compactDisplayPath(calibration?.report_path)}</strong>
            </div>
            <div className="list-rows compact">
              {(calibration?.recommendations?.length ? calibration.recommendations : ["等待生成校准图并上传校准照片。"]).map((item) => (
                <div className="blue-note" key={item}>{item}</div>
              ))}
            </div>
          </div>
        </Card>

        <Card title="最近生成的投影卡片" action={<a className="link-blue">查看全部 →</a>}>
          <div className="projection-thumbs">
            {(recentCards.length ? recentCards : templateCards).map((card) => (
              <button key={card.id} onClick={() => setSelected(card)}>
                <ProjectionPreview card={card} compact />
                <div className="row-between">
                  <strong>{card.title}</strong>
                  <span className="small muted">{card.created_at}</span>
                </div>
              </button>
            ))}
          </div>
        </Card>

        <div className="blue-note">
          当前使用已接入的物理投影输出；如果投影仪画面与本页不一致，请先刷新本页并确认投影窗口仍在 DPI 输出上。
          <a className="link-blue" href={previewUrl.startsWith("http") ? previewUrl : undefined} target="_blank" rel="noreferrer"> 在新窗口中打开 <ExternalLink size={14} /></a>
        </div>
        <details className="advanced-panel">
          <summary>高级诊断</summary>
          <div className="advanced-panel__content">
            <Card title="预览服务">
              <div className="definition-grid">
                <span>预览地址</span><strong>{previewUrl || "-"}</strong>
                <span>输出目标</span><strong>{service?.output_target ?? "-"}</strong>
                <span>配置文件</span><strong>{compactDisplayPath(displayProfile?.path)}</strong>
              </div>
            </Card>
            <Card title="投影输出路径">
              <div className="definition-grid">
                <span>PPT 摘要</span><strong>{compactDisplayPath(pptSummary?.projection_path)}</strong>
                <span>PPT 文件</span><strong>{compactDisplayPath(pptxSession?.projection_path ?? pptxSession?.path)}</strong>
                <span>标记文档</span><strong>{compactDisplayPath(markdownProjection?.projection_path ?? markdownProjection?.path)}</strong>
                <span>校准分析</span><strong>{compactDisplayPath(calibration?.analysis_path)}</strong>
              </div>
            </Card>
          </div>
        </details>
      </div>
    </>
  );
}

async function captureOneScreenFrame(): Promise<string> {
  const mediaDevices = navigator.mediaDevices as MediaDevices & {
    getDisplayMedia?: (constraints?: DisplayMediaStreamOptions) => Promise<MediaStream>;
  };
  if (!mediaDevices.getDisplayMedia) {
    throw new Error("当前浏览器不支持屏幕捕获。请使用支持 getDisplayMedia 的浏览器。");
  }
  const stream = await mediaDevices.getDisplayMedia({ video: true, audio: false });
  try {
    const video = document.createElement("video");
    video.srcObject = stream;
    video.muted = true;
    await video.play();
    await new Promise<void>((resolve) => {
      if (video.videoWidth && video.videoHeight) {
        resolve();
        return;
      }
      video.onloadedmetadata = () => resolve();
    });
    const width = video.videoWidth || 1280;
    const height = video.videoHeight || 720;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("无法创建截图画布。");
    context.drawImage(video, 0, 0, width, height);
    return canvas.toDataURL("image/png");
  } finally {
    stream.getTracks().forEach((track) => track.stop());
  }
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
