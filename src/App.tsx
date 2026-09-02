import { FormEvent, useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import { apiErrorMessage, readToken, request, setToken } from "./api/client";
import { AppShell } from "./layout/AppShell";

export function App() {
  const [authState, setAuthState] = useState<"checking" | "authenticated" | "anonymous">(() => readToken() ? "checking" : "anonymous");
  const [token, setTokenInput] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (authState !== "checking") return;
    let active = true;
    request<{ status: string }>("/api/health")
      .then(() => { if (active) setAuthState("authenticated"); })
      .catch(() => {
        if (!active) return;
        setToken("");
        setAuthState("anonymous");
        setError("访问令牌已失效，请重新输入。");
      });
    return () => { active = false; };
  }, [authState]);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = token.trim();
    if (!value) {
      setError("请输入访问令牌。");
      return;
    }
    setBusy(true);
    setError("");
    setToken(value);
    try {
      await request<{ status: string }>("/api/health");
      setAuthState("authenticated");
      setTokenInput("");
    } catch (err) {
      setToken("");
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (authState === "checking") {
    return (
      <main className="auth-screen" aria-busy="true" aria-live="polite">
        <section className="auth-card auth-card--checking">
          <div className="auth-mark">Le</div>
          <div><h1>正在连接设备</h1><p>正在验证本机控制台访问状态…</p></div>
          <span className="auth-progress" />
        </section>
      </main>
    );
  }

  if (authState === "anonymous") {
    return (
      <main className="auth-screen">
        <form className="auth-card" onSubmit={(event) => void signIn(event)}>
          <div className="auth-mark">Le</div>
          <div>
            <h1>智能台灯控制台</h1>
            <p>请输入设备访问令牌。令牌只保存在当前浏览器中，不会写入网址。</p>
          </div>
          <label>
            <span>访问令牌</span>
            <input
              type="password"
              value={token}
              onChange={(event) => setTokenInput(event.target.value)}
              autoComplete="current-password"
              autoFocus
              placeholder="LELAMP_WEB_TOKEN"
            />
          </label>
          {error && <div className="auth-error">{error}</div>}
          <button type="submit" disabled={busy}>{busy ? "正在验证…" : "进入控制台"}</button>
          <small>令牌由设备管理员配置在 lelamp_runtime/.env 中。</small>
        </form>
      </main>
    );
  }

  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}
