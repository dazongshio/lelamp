import { AppWindow, ChevronDown, CircleCheck, KeyRound, Laptop, Link2, MonitorUp, Play, RefreshCw, ShieldCheck, Terminal, Volume1, Volume2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { bootstrapRemoteCodex, getRemoteSshStatus, openRemoteCodex, runRemoteSshCommand, sendRemoteVoiceCommand, testRemoteSsh } from "../api/remote";
import type { RemoteSshResult, RemoteSshStatus } from "../api/types";
import "./pages.css";

export function RemoteComputerPage() {
  const [status, setStatus] = useState<RemoteSshStatus | null>(null);
  const [host, setHost] = useState("");
  const [user, setUser] = useState("");
  const [port, setPort] = useState(22);
  const [keyPath, setKeyPath] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState(12);
  const [command, setCommand] = useState("uname -a");
  const [voiceCommand, setVoiceCommand] = useState("打开 codex");
  const [authorized, setAuthorized] = useState(false);
  const [result, setResult] = useState<RemoteSshResult | null>(null);
  const [workflowNote, setWorkflowNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const output = result?.remote ?? result;

  const load = useCallback(async () => {
    setError("");
    try {
      const response = await getRemoteSshStatus();
      setStatus(response.data);
      setKeyPath((current) => current || response.data.default_key_path || "");
      const saved = response.data.saved_target;
      if (saved) {
        setHost((current) => current.trim() ? current : saved.host || current);
        setUser((current) => current.trim() ? current : saved.user || current);
        setPort((current) => (current !== 22 ? current : saved.port || current));
        setTimeoutSeconds((current) => (current !== 12 ? current : saved.timeout_seconds || current));
        setKeyPath((current) => current || saved.key_path || response.data.default_key_path || "");
      }
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function payload() {
    return {
      host: host.trim(),
      user: user.trim(),
      port,
      keyPath: keyPath.trim() || undefined,
      timeoutSeconds,
    };
  }

  async function testConnection() {
    setBusy(true);
    setError("");
    setWorkflowNote("");
    setResult(null);
    try {
      const target = payload();
      const response = await testRemoteSsh(target);
      let nextResult = response.data;
      if (response.data.status === "completed") {
        setWorkflowNote("SSH 已连通，正在检查并安装 Codex。");
        const bootstrap = await bootstrapRemoteCodex({ ...target, authorized: true });
        nextResult = bootstrap.data;
        if (bootstrap.data.status === "completed") {
          setWorkflowNote("Codex 已可用，正在远程电脑上打开 Codex。");
          const opened = await openRemoteCodex({ ...target, authorized: true });
          nextResult = opened.data;
        }
      }
      setResult(nextResult);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function runCommand(nextCommand = command) {
    setCommand(nextCommand);
    setBusy(true);
    setError("");
    setWorkflowNote("");
    setResult(null);
    try {
      const response = await runRemoteSshCommand({ ...payload(), command: nextCommand, authorized });
      setResult(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function installCodex() {
    setBusy(true);
    setError("");
    setWorkflowNote("");
    setResult(null);
    try {
      const response = await bootstrapRemoteCodex({ ...payload(), authorized: true });
      setResult(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function openCodex() {
    setBusy(true);
    setError("");
    setWorkflowNote("");
    setResult(null);
    try {
      const response = await openRemoteCodex({ ...payload(), authorized: true });
      setResult(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function runVoiceCommand() {
    return executeVoiceCommand(voiceCommand);
  }

  async function executeVoiceCommand(text: string) {
    setBusy(true);
    setError("");
    setWorkflowNote("");
    setResult(null);
    try {
      const response = await sendRemoteVoiceCommand({ ...payload(), text });
      setResult(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="remote-control">
      <header className="remote-control__header">
        <div><span><MonitorUp size={16} />跨设备控制</span><h1>远程电脑</h1><p>连接并控制你的另一台电脑。</p></div>
        <button className="ghost-button" onClick={() => void load()} disabled={busy}><RefreshCw size={16} className={busy ? "spin" : ""} />刷新状态</button>
      </header>

      {error && <div className="remote-control__notice remote-control__notice--error">操作失败：{error}</div>}
      {workflowNote && <div className="remote-control__notice">{workflowNote}</div>}

      <section className="remote-device-card">
        <div className="remote-device-card__icon"><Laptop size={38} /></div>
        <div className="remote-device-card__identity">
          <span>已绑定设备</span>
          <h2>{status?.saved_target ? `${status.saved_target.user} 的电脑` : "尚未连接电脑"}</h2>
          <p>{status?.saved_target?.host || "请在下方高级设置中添加设备"}</p>
        </div>
        <div className={`remote-device-card__status ${status?.status === "available" ? "is-ready" : ""}`}>
          <i />{status?.status === "available" ? "控制服务可用" : "控制服务不可用"}
        </div>
        <button className="primary-button" onClick={() => void testConnection()} disabled={busy || !host.trim() || !user.trim()}>
          <Link2 size={17} />{busy ? "正在连接" : "连接设备"}
        </button>
      </section>

      <section className="remote-actions">
        <div className="remote-section-title"><div><h2>常用操作</h2><p>点击后立即发送到已连接的电脑。</p></div></div>
        <div className="remote-action-grid">
          <button onClick={() => void openCodex()} disabled={busy || !host}><span className="blue"><AppWindow size={24} /></span><strong>打开编程助手</strong><small>在远程电脑启动</small></button>
          <button onClick={() => void executeVoiceCommand("远程电脑下一页")} disabled={busy || !host}><span className="purple"><Play size={24} /></span><strong>演示下一页</strong><small>控制幻灯片播放</small></button>
          <button onClick={() => void executeVoiceCommand("远程电脑音量增大")} disabled={busy || !host}><span className="orange"><Volume2 size={24} /></span><strong>增大音量</strong><small>音量增加一级</small></button>
          <button onClick={() => void executeVoiceCommand("远程电脑音量减小")} disabled={busy || !host}><span className="green"><Volume1 size={24} /></span><strong>减小音量</strong><small>音量降低一级</small></button>
        </div>
      </section>

      <section className="remote-command-card">
        <div><span><Terminal size={18} /></span><div><h2>告诉电脑要做什么</h2><p>支持打开应用、网页、演示翻页、音量和锁屏。</p></div></div>
        <div className="remote-command-card__input">
          <input value={voiceCommand} onChange={(event) => setVoiceCommand(event.target.value)} placeholder="例如：在远程电脑打开浏览器" onKeyDown={(event) => { if (event.key === "Enter") void runVoiceCommand(); }} />
          <button className="primary-button" onClick={() => void runVoiceCommand()} disabled={busy || !host.trim() || !voiceCommand.trim()}>执行</button>
        </div>
      </section>

      {result && (
        <section className={`remote-result ${result.status === "completed" ? "is-success" : "is-error"}`}>
          <span>{result.status === "completed" ? <CircleCheck size={22} /> : <Terminal size={22} />}</span>
          <div><strong>{friendlyStatus(result.status)}</strong><p>{result.message ?? result.reply ?? output?.stdout ?? output?.stderr ?? "操作已返回结果"}</p></div>
          {typeof output?.duration_seconds === "number" && <small>{output.duration_seconds} 秒</small>}
        </section>
      )}

      <details className="remote-advanced">
        <summary><div><span><KeyRound size={18} /></span><div><strong>高级连接设置</strong><small>设备地址、身份密钥和诊断命令</small></div></div><ChevronDown size={18} /></summary>
        <div className="remote-advanced__body">
          <div className="remote-advanced__grid">
            <label><span>设备地址</span><input value={host} onChange={(event) => setHost(event.target.value)} placeholder="192.168.3.50" /></label>
            <label><span>用户名</span><input value={user} onChange={(event) => setUser(event.target.value)} placeholder="目标电脑用户名" /></label>
            <label><span>端口</span><input type="number" value={port} onChange={(event) => setPort(Number(event.target.value) || 22)} /></label>
            <label><span>连接超时</span><input type="number" value={timeoutSeconds} onChange={(event) => setTimeoutSeconds(Number(event.target.value) || 12)} /></label>
            <label className="wide"><span>身份密钥</span><input value={keyPath} onChange={(event) => setKeyPath(event.target.value)} /></label>
          </div>
          <div className="remote-advanced__command">
            <input value={command} onChange={(event) => setCommand(event.target.value)} />
            <label><input type="checkbox" checked={authorized} onChange={(event) => setAuthorized(event.target.checked)} />我确认执行这条诊断命令</label>
            <button className="ghost-button" onClick={() => void runCommand()} disabled={busy || !authorized}><Terminal size={16} />执行诊断</button>
            <button className="ghost-button" onClick={() => void installCodex()} disabled={busy || !host}><ShieldCheck size={16} />检查编程助手</button>
          </div>
          <div className="remote-advanced__safety"><ShieldCheck size={16} />仅允许连接局域网或安全组网内的设备；危险命令会被拦截，所有操作均有记录。</div>
        </div>
      </details>
    </main>
  );
}

function friendlyStatus(status: unknown) {
  const value = String(status ?? "");
  const labels: Record<string, string> = {
    available: "可用",
    completed: "已完成",
    failed: "失败",
    pending: "等待",
    backend_missing: "待安装",
  };
  return labels[value] ?? (value || "等待");
}
