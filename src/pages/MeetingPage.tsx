import {
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FileText,
  Download,
  Mic,
  Pencil,
  Save,
  Send,
  Share2,
  Sparkles,
  Radio,
  Square,
  Star,
  Users,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { apiErrorMessage } from "../api/client";
import { randomId } from "../utils/randomId";
import {
  askMeeting,
  exportMeetingTranscript,
  getMeetingJobs,
  getMeetingInsights,
  getMeetingAudio,
  getMeetingProviderStatus,
  getMeetingRealtimeStatus,
  importMeetingMedia,
  startMeetingRealtime,
  shareMeetingClip,
  stopMeetingRealtime,
  toggleMeetingHighlight,
  updateMeetingTranscript,
} from "../api/meeting";
import type {
  MeetingJob,
  MeetingInsightsResponse,
  MeetingProviderStatus,
  MeetingQaResponse,
  MeetingRealtimeStatus,
} from "../api/types";
import { StatusPill } from "../components/ProjectorConsole";

type ScheduledMeeting = {
  id: string;
  title: string;
  startsAt: string;
  duration: number;
  participants: string;
};

const SCHEDULE_KEY = "lelamp_scheduled_meetings";

function localDateTime(minutesFromNow = 30) {
  const date = new Date(Date.now() + minutesFromNow * 60_000);
  date.setSeconds(0, 0);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function readSchedule(): ScheduledMeeting[] {
  try {
    const value = JSON.parse(localStorage.getItem(SCHEDULE_KEY) || "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function timeLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function elapsedLabel(startedAt?: string | null) {
  if (!startedAt) return "00:00";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return [hours, minutes, rest]
    .filter((_, index) => hours > 0 || index > 0)
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
}

export function MeetingPage() {
  const location = useLocation();
  const [provider, setProvider] = useState<MeetingProviderStatus | null>(null);
  const [session, setSession] = useState<MeetingRealtimeStatus | null>(null);
  const [latestJob, setLatestJob] = useState<MeetingJob | null>(null);
  const [title, setTitle] = useState("");
  const [participants, setParticipants] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [question, setQuestion] = useState("");
  const [qaResult, setQaResult] = useState<MeetingQaResponse | null>(null);
  const [qaBusy, setQaBusy] = useState(false);
  const [audioUrl, setAudioUrl] = useState("");
  const [playbackSeconds, setPlaybackSeconds] = useState(0);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editingText, setEditingText] = useState("");
  const [editingSpeaker, setEditingSpeaker] = useState("");
  const [renameFrom, setRenameFrom] = useState("");
  const [renameTo, setRenameTo] = useState("");
  const [insights, setInsights] = useState<MeetingInsightsResponse | null>(null);
  const [shareNotice, setShareNotice] = useState("");
  const audioRef = useRef<HTMLAudioElement>(null);
  const [now, setNow] = useState(Date.now());
  const [showSchedule, setShowSchedule] = useState(false);
  const [schedule, setSchedule] = useState<ScheduledMeeting[]>(readSchedule);
  const [scheduleTitle, setScheduleTitle] = useState("团队会议");
  const [scheduleTime, setScheduleTime] = useState(localDateTime);
  const [scheduleDuration, setScheduleDuration] = useState(60);
  const [scheduleParticipants, setScheduleParticipants] = useState("");

  const active = ["starting", "running", "stopping", "finalizing"].includes(session?.status || "");
  const stopping = ["stopping", "finalizing"].includes(session?.status || "");
  const transcript = session?.transcript || [];
  const providerReady = provider?.status === "available";

  const load = useCallback(async () => {
    const [providerResult, realtimeResult, jobsResult] = await Promise.allSettled([
      getMeetingProviderStatus(),
      getMeetingRealtimeStatus(),
      getMeetingJobs(),
    ]);
    if (providerResult.status === "fulfilled") setProvider(providerResult.value.data);
    if (realtimeResult.status === "fulfilled") setSession(realtimeResult.value.data);
    if (jobsResult.status === "fulfilled") setLatestJob(jobsResult.value.data.items?.[0] || null);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(async () => {
      setNow(Date.now());
      try {
        setSession((await getMeetingRealtimeStatus(session?.meeting_id || undefined)).data);
      } catch (err) {
        setError(apiErrorMessage(err));
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [active, session?.meeting_id]);

  useEffect(() => {
    let objectUrl = "";
    if (active || !session?.meeting_id || !session.audio_path) {
      setAudioUrl("");
      return;
    }
    void getMeetingAudio(session.meeting_id)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        setAudioUrl(objectUrl);
      })
      .catch((err) => setError(apiErrorMessage(err)));
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [active, session?.audio_path, session?.meeting_id]);

  useEffect(() => {
    if (!session?.meeting_id) {
      setInsights(null);
      return;
    }
    void getMeetingInsights(session.meeting_id).then((result) => setInsights(result.data)).catch(() => undefined);
  }, [session?.meeting_id, session?.status]);

  function transcriptOffset(timestamp: string) {
    if (!session?.started_at || !timestamp) return 0;
    return Math.max(0, (new Date(timestamp).getTime() - new Date(session.started_at).getTime()) / 1000);
  }

  function seekToTranscript(timestamp: string) {
    const player = audioRef.current;
    if (!player || !audioUrl) return;
    player.currentTime = transcriptOffset(timestamp);
    void player.play();
  }

  async function saveTranscriptItem(index: number) {
    if (!session?.meeting_id) return;
    setBusy(true);
    try {
      setSession((await updateMeetingTranscript({ meeting_id: session.meeting_id, index, text: editingText, speaker: editingSpeaker })).data);
      setEditingIndex(null);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function renameSpeaker() {
    if (!session?.meeting_id || !renameFrom || !renameTo.trim()) return;
    setBusy(true);
    try {
      setSession((await updateMeetingTranscript({ meeting_id: session.meeting_id, rename_speaker_from: renameFrom, speaker: renameTo.trim() })).data);
      setRenameFrom("");
      setRenameTo("");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function toggleHighlight(index: number) {
    if (!session?.meeting_id) return;
    try {
      const result = await toggleMeetingHighlight(session.meeting_id, index);
      setInsights((current) => current ? { ...current, highlights: result.data.highlights } : current);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function downloadTranscript(format: "txt" | "srt" | "vtt") {
    if (!session?.meeting_id) return;
    try {
      const blob = await exportMeetingTranscript(session.meeting_id, format);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${session.title || "meeting"}.${format}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function shareClip(index: number) {
    if (!session?.meeting_id) return;
    try {
      const result = await shareMeetingClip(session.meeting_id, index);
      await navigator.clipboard.writeText(result.data.url);
      setShareNotice(`片段链接已复制，有效期至 ${new Date(result.data.expires_at).toLocaleString("zh-CN")}`);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  const speakers = useMemo(() => [...new Set(transcript.map((item) => item.speaker).filter(Boolean))], [transcript]);

  const upcoming = useMemo(
    () => [...schedule].filter((item) => new Date(item.startsAt).getTime() + item.duration * 60_000 > now)
      .sort((a, b) => a.startsAt.localeCompare(b.startsAt)),
    [schedule, now],
  );

  async function startMeeting(meetingTitle = title, meetingParticipants = participants) {
    setBusy(true);
    setError("");
    try {
      const names = meetingParticipants.split(/[，,]/).map((item) => item.trim()).filter(Boolean);
      const result = await startMeetingRealtime({
        title: meetingTitle.trim() || "自动命名",
        participants: names,
        max_seconds: 8 * 60 * 60,
      });
      setSession(result.data);
      setTitle(meetingTitle.trim());
      setParticipants(meetingParticipants);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function importMedia(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      setSession((await importMeetingMedia(file)).data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function stopMeeting() {
    setBusy(true);
    setError("");
    try {
      const result = await stopMeetingRealtime(session?.meeting_id || undefined, true);
      const stoppedSession = result.data.session;
      setSession(
        stoppedSession && typeof stoppedSession.status === "string"
          ? { ...result.data, ...stoppedSession } as MeetingRealtimeStatus
          : result.data,
      );
      if (result.data.job) setLatestJob(result.data.job);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitQuestion() {
    if (!session?.meeting_id || !question.trim()) return;
    setQaBusy(true);
    setError("");
    try {
      const result = await askMeeting(session.meeting_id, question.trim());
      setQaResult(result.data);
      setQuestion("");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setQaBusy(false);
    }
  }

  function saveMeeting() {
    if (!scheduleTitle.trim() || !scheduleTime) {
      setError("请填写会议名称和开始时间。");
      return;
    }
    const next = [...schedule, {
      id: randomId(),
      title: scheduleTitle.trim(),
      startsAt: new Date(scheduleTime).toISOString(),
      duration: scheduleDuration,
      participants: scheduleParticipants.trim(),
    }];
    localStorage.setItem(SCHEDULE_KEY, JSON.stringify(next));
    setSchedule(next);
    setShowSchedule(false);
    setError("");
  }

  function removeMeeting(id: string) {
    const next = schedule.filter((item) => item.id !== id);
    localStorage.setItem(SCHEDULE_KEY, JSON.stringify(next));
    setSchedule(next);
  }

  const completedMeeting = !active && latestJob?.status === "completed" ? latestJob : null;
  const minutesStep = completedMeeting?.steps.find((step) => step.name === "final_result");
  const resultDocumentId = String(minutesStep?.output?.document_id || "");
  const resultPath = String(
    (minutesStep?.output?.minutes as { path?: string } | undefined)?.path
    || minutesStep?.ai_result
    || completedMeeting?.transcript
    || "",
  );
  const resultSearch = new URLSearchParams(location.search);
  if (resultPath) resultSearch.set("file", resultPath);

  return (
    <main className="meeting-room">
      <header className="meeting-room__header">
        <div>
          <span className="meeting-room__eyebrow"><Radio size={15} />智能会议</span>
          <h1>会议</h1>
          <p>在这里完成录音、实时转写和会议整理，无需离开当前程序。</p>
        </div>
        <div className="meeting-room__header-actions">
          <StatusPill tone={providerReady ? "ok" : "warn"}>
            {providerReady ? "麦克风与转写可用" : "转写服务待检查"}
          </StatusPill>
          <button className="ghost-button" onClick={() => setShowSchedule(true)}>
            <CalendarDays size={17} />预定会议
          </button>
        </div>
      </header>

      {error && <div className="meeting-room__alert">{error}</div>}

      <section className="meeting-room__grid">
        <article className={`meeting-live-card ${active ? "meeting-live-card--active" : ""}`}>
          <div className="meeting-live-card__top">
            <div className="meeting-live-card__state">
              <span className="meeting-live-card__mic"><Mic size={29} /></span>
              <div>
                <span>{active ? "正在记录" : "准备开始"}</span>
                <h2>{active ? session?.title || title : "开始一场新会议"}</h2>
              </div>
            </div>
            {active && <strong className="meeting-live-card__timer">{elapsedLabel(session?.started_at)}</strong>}
          </div>

          {!active ? (
            <div className="meeting-start-form">
              <label>
                <span>会议名称（可选）</span>
                <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="留空将根据会议内容自动生成" />
              </label>
              <label>
                <span>参会人（可选）</span>
                <input value={participants} onChange={(event) => setParticipants(event.target.value)} placeholder="多人请用逗号分隔" />
              </label>
              <button className="meeting-start-button" disabled={busy || !providerReady} onClick={() => void startMeeting()}>
                <Mic size={19} />{busy ? "正在启动" : "开始会议"}
              </button>
              <label className="meeting-import-button">
                <Upload size={18} />上传音视频生成妙记
                <input type="file" accept="audio/*,video/*,.m4a,.mkv" disabled={busy || !providerReady} onChange={(event) => { void importMedia(event.target.files?.[0]); event.currentTarget.value = ""; }} />
              </label>
            </div>
          ) : (
            <div className="meeting-running-actions">
              <span><i />正在通过设备麦克风实时转文字</span>
              <button className="meeting-stop-button" disabled={busy || stopping} onClick={() => void stopMeeting()}>
                <Square size={16} fill="currentColor" />{stopping ? "正在整理" : busy ? "正在停止" : "结束会议"}
              </button>
            </div>
          )}
        </article>

        <aside className="meeting-side-card">
          <div className="meeting-side-card__title">
            <div><CalendarDays size={19} /><strong>即将开始</strong></div>
            <button onClick={() => setShowSchedule(true)}>添加</button>
          </div>
          <div className="meeting-schedule-list">
            {upcoming.length ? upcoming.slice(0, 4).map((item) => (
              <div className="meeting-schedule-item" key={item.id}>
                <button className="meeting-schedule-item__main" onClick={() => void startMeeting(item.title, item.participants)} disabled={active}>
                  <span>{timeLabel(item.startsAt)}</span>
                  <strong>{item.title}</strong>
                  <small>{item.duration} 分钟{item.participants ? ` · ${item.participants}` : ""}</small>
                </button>
                <button className="meeting-schedule-item__remove" aria-label="删除预约" onClick={() => removeMeeting(item.id)}><X size={14} /></button>
              </div>
            )) : <div className="meeting-schedule-empty">还没有预定会议</div>}
          </div>
        </aside>
      </section>

      {completedMeeting && (
        <section className="meeting-result-card">
          <span className="meeting-result-card__icon"><CheckCircle2 size={25} /></span>
          <div>
            <span>会议结果已生成</span>
            <h2>{completedMeeting.title || "最近一次会议"}</h2>
            <p>
              已保存逐字稿和会议纪要
              {completedMeeting.steps.some((step) => step.name === "followup") ? "，会后整理也已完成。" : "。"}
            </p>
          </div>
          <Link
            className="primary-button"
            to={resultDocumentId
              ? { pathname: "/documents", search: `?document=${encodeURIComponent(resultDocumentId)}` }
              : { pathname: "/results", search: `?${resultSearch.toString()}` }}
          >
            <FileText size={17} />打开会议文档
          </Link>
        </section>
      )}

      <section className="meeting-transcript">
        <div className="meeting-transcript__header">
          <div>
            <h2>实时文字</h2>
            <span>{active ? "内容会随会议自动更新" : "开始会议后，识别到的讲话会显示在这里"}</span>
          </div>
          {session?.transcript_path && (
            <Link to={{ pathname: "/results", search: `?${resultSearch.toString()}` }}>
              查看完整记录 <ChevronRight size={16} />
            </Link>
          )}
          {!active && transcript.length > 0 && (
            <div className="meeting-export-actions">
              <Download size={15} />
              {(["txt", "srt", "vtt"] as const).map((format) => <button key={format} onClick={() => void downloadTranscript(format)}>{format.toUpperCase()}</button>)}
            </div>
          )}
        </div>
        {audioUrl && (
          <div className="meeting-audio-player">
            <strong>会议录音</strong>
            <audio ref={audioRef} src={audioUrl} controls preload="metadata" onTimeUpdate={(event) => setPlaybackSeconds(event.currentTarget.currentTime)} />
            <span>点击下方任一句话可跳转回听</span>
          </div>
        )}
        {!active && speakers.length > 0 && (
          <div className="meeting-speaker-manager">
            <strong>批量重命名说话人</strong>
            <select value={renameFrom} onChange={(event) => setRenameFrom(event.target.value)}>
              <option value="">选择原名称</option>
              {speakers.map((speaker) => <option key={speaker} value={speaker}>{speaker}</option>)}
            </select>
            <input value={renameTo} onChange={(event) => setRenameTo(event.target.value)} placeholder="真实姓名" />
            <button onClick={() => void renameSpeaker()} disabled={busy || !renameFrom || !renameTo.trim()}>应用</button>
          </div>
        )}
        <div className="meeting-transcript__body">
          {transcript.length ? transcript.map((item, index) => (
            <div className={`meeting-transcript__line ${audioUrl && Math.abs(playbackSeconds - transcriptOffset(item.timestamp)) < 3 ? "is-playing" : ""}`} key={`${item.timestamp}-${index}`}>
              {editingIndex === index ? (
                <div className="meeting-transcript-editor">
                  <input value={editingSpeaker} onChange={(event) => setEditingSpeaker(event.target.value)} aria-label="说话人" />
                  <textarea value={editingText} onChange={(event) => setEditingText(event.target.value)} aria-label="逐字稿内容" />
                  <button onClick={() => void saveTranscriptItem(index)} disabled={busy || !editingText.trim()}><Save size={15} />保存</button>
                  <button onClick={() => setEditingIndex(null)}>取消</button>
                </div>
              ) : (
                <>
                  <button className="meeting-transcript__seek" type="button" onClick={() => seekToTranscript(item.timestamp)} disabled={!audioUrl}>
                    <span>{item.speaker || "发言人"}</span>
                    <div>
                      <small>{item.timestamp ? new Date(item.timestamp).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) : ""}</small>
                      <p>{item.text}</p>
                    </div>
                  </button>
                  {!active && <button className="meeting-transcript__edit" aria-label="修订逐字稿" onClick={() => { setEditingIndex(index); setEditingText(item.text); setEditingSpeaker(item.speaker); }}><Pencil size={15} /></button>}
                  {!active && <button className={`meeting-transcript__edit ${insights?.highlights.some((highlight) => highlight.index === index) ? "is-highlighted" : ""}`} aria-label="标记重点" onClick={() => void toggleHighlight(index)}><Star size={15} fill="currentColor" /></button>}
                </>
              )}
            </div>
          )) : session?.partial_text ? (
            <div className="meeting-transcript__listening"><i /><p>{session.partial_text}</p></div>
          ) : (
            <div className="meeting-transcript__empty">
              <span><FileText size={28} /></span>
              <strong>{active ? "正在聆听…" : "暂无会议文字"}</strong>
              <p>{active ? "请正常讲话，识别结果会自动出现。" : "点击“开始会议”即可录音并实时转写。"}</p>
            </div>
          )}
        </div>
      </section>

      {(insights?.chapters_markdown || insights?.key_information_markdown || insights?.highlights.length) ? (
        <section className="meeting-insights">
          <div><h2>会议导航</h2><p>自动章节、关键信息和人工重点集中在这里。</p></div>
          <div className="meeting-insights__grid">
            <article><strong>自动章节</strong><pre>{insights.chapters_markdown || "暂未生成章节"}</pre></article>
            <article><strong>关键信息</strong><pre>{insights.key_information_markdown || "暂未生成关键信息"}</pre></article>
            <article><strong>我的重点</strong>{insights.highlights.length ? insights.highlights.map((item) => <div className="meeting-highlight-item" key={item.index}><button onClick={() => seekToTranscript(item.timestamp || "")}><span>{item.speaker || "发言人"}</span>{item.text}</button><button aria-label="分享音频片段" onClick={() => void shareClip(item.index)}><Share2 size={14} /></button></div>) : <p>点击逐字稿右侧星标添加重点。</p>}{shareNotice && <small>{shareNotice}</small>}</article>
          </div>
        </section>
      ) : null}

      <section className="meeting-qa">
        <div className="meeting-qa__header">
          <span><Sparkles size={20} /></span>
          <div><h2>问问这场会议</h2><p>由本机 Codex 根据逐字稿回答，并标出原文依据。</p></div>
        </div>
        <form className="meeting-qa__form" onSubmit={(event) => { event.preventDefault(); void submitQuestion(); }}>
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={transcript.length ? "例如：最终决定了什么？谁负责下一步？" : "会议产生逐字稿后即可提问"}
            disabled={!session?.meeting_id || !transcript.length || qaBusy}
          />
          <button type="submit" disabled={!session?.meeting_id || !transcript.length || !question.trim() || qaBusy}>
            <Send size={16} />{qaBusy ? "Codex 正在分析…" : "提问"}
          </button>
        </form>
        {qaResult && (
          <div className="meeting-qa__answer">
            <strong>{qaResult.insufficient_evidence ? "证据不足" : "回答"}</strong>
            <p>{qaResult.answer}</p>
            {!!qaResult.citations.length && (
              <div className="meeting-qa__citations">
                <span>原文依据</span>
                {qaResult.citations.map((citation) => (
                  <blockquote key={citation.id}>
                    <small>{citation.id} · {citation.speaker}{citation.timestamp ? ` · ${new Date(citation.timestamp).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}` : ""}</small>
                    <p>{citation.text}</p>
                  </blockquote>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      <footer className="meeting-room__footer">
        <span><CheckCircle2 size={14} />会议操作保留在本程序内</span>
        <span className="is-connected">录音、逐字稿、纪要和问答均保存在本机</span>
      </footer>

      {showSchedule && (
        <div className="meeting-modal-backdrop" onMouseDown={() => setShowSchedule(false)}>
          <section className="meeting-modal" onMouseDown={(event) => event.stopPropagation()}>
            <div className="meeting-modal__header">
              <div><span><CalendarDays size={20} /></span><div><h2>预定会议</h2><p>会议会保存在本程序中。</p></div></div>
              <button aria-label="关闭" onClick={() => setShowSchedule(false)}><X size={18} /></button>
            </div>
            <div className="meeting-modal__form">
              <label><span>会议名称</span><input value={scheduleTitle} onChange={(event) => setScheduleTitle(event.target.value)} /></label>
              <label><span>开始时间</span><input type="datetime-local" value={scheduleTime} onChange={(event) => setScheduleTime(event.target.value)} /></label>
              <label><span>会议时长</span><select value={scheduleDuration} onChange={(event) => setScheduleDuration(Number(event.target.value))}>
                <option value={30}>30 分钟</option><option value={60}>60 分钟</option><option value={90}>90 分钟</option><option value={120}>120 分钟</option>
              </select></label>
              <label><span><Users size={14} />参会人（可选）</span><input value={scheduleParticipants} onChange={(event) => setScheduleParticipants(event.target.value)} placeholder="多人请用逗号分隔" /></label>
            </div>
            <div className="meeting-modal__actions">
              <button className="ghost-button" onClick={() => setShowSchedule(false)}>取消</button>
              <button className="primary-button" onClick={saveMeeting}><Clock3 size={16} />保存预约</button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
