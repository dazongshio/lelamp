import { BellRing, MessageSquareText, Phone, RefreshCw, Send, Smartphone } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { getMobileBridgeStatus, sendMobileBridgeRequest } from "../api/mobile";
import type { MobileBridgeRequestResponse, MobileBridgeStatus } from "../api/types";
import { Card, InfoCard } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { SkillChip } from "../components/SkillChip";
import { StatusBadge } from "../components/StatusBadge";
import "./pages.css";

const examples = [
  { icon: BellRing, label: "找手机", text: "找手机" },
  { icon: Phone, label: "拨电话", text: "打电话给 12345678" },
  { icon: MessageSquareText, label: "发短信", text: "发短信给 12345678 内容是 我十分钟后到" },
];

export function MobileBridgePage() {
  const [status, setStatus] = useState<MobileBridgeStatus | null>(null);
  const [requestText, setRequestText] = useState("找手机");
  const [authorized, setAuthorized] = useState(false);
  const [result, setResult] = useState<MobileBridgeRequestResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const response = await getMobileBridgeStatus();
      setStatus(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function sendRequest(text = requestText) {
    if (!text.trim()) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const response = await sendMobileBridgeRequest(text, authorized);
      setResult(response.data);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="移动端桥接"
        description="通过手机侧伴随应用处理找手机、电话和短信；未配置时只显示连接提示，不会假装执行"
        actions={<button className="ghost-button" onClick={() => void load()}><RefreshCw size={16} />刷新</button>}
      />
      <div className="page-grid">
        {error && <div className="danger-panel">操作失败：{error}</div>}
        <div className="grid-4">
          <InfoCard icon={<Smartphone size={20} />} label="连接状态" value={status?.configured ? "已连接" : "待配置"} note="手机伴随应用" status={<StatusBadge status={status?.status ?? "pending"} />} />
          <InfoCard icon={<Send size={20} />} label="发送能力" value={status?.configured ? "可转发" : "不可执行"} note="电话和短信需额外授权" status={<StatusBadge status={status?.configured ? "available" : "needs_config"} />} />
          <InfoCard icon={<Smartphone size={20} />} label="目标手机" value={status?.device_id ? "已选择" : "默认设备"} note="可在接入配置中指定" />
          <InfoCard icon={<BellRing size={20} />} label="请求签名" value={status?.shared_secret_configured ? "已开启" : "未开启"} note="可选安全增强" />
        </div>

        <div className="mobile-grid">
          <Card title="移动端请求" subtitle="电话和短信必须勾选授权；找手机可直接转发到已配置 companion">
            <div className="mobile-form">
              <label>
                <span>请求</span>
                <textarea className="textarea mobile-request-textarea" value={requestText} onChange={(event) => setRequestText(event.target.value)} />
              </label>
              <label className="inline-check">
                <input type="checkbox" checked={authorized} onChange={(event) => setAuthorized(event.target.checked)} />
                我确认允许执行此移动端动作
              </label>
              <div className="mobile-actions">
                {examples.map((item) => {
                  const Icon = item.icon;
                  return <button className="ghost-button" key={item.label} onClick={() => setRequestText(item.text)}><Icon size={16} />{item.label}</button>;
                })}
                <button className="primary-button" onClick={() => void sendRequest()} disabled={busy}><Send size={16} />发送请求</button>
              </div>
            </div>
          </Card>

          <Card title="执行结果" action={<StatusBadge status={result?.status ?? "pending"} />}>
            <div className="definition-grid">
              <span>状态</span><StatusBadge status={result?.status ?? "pending"} />
              <span>动作</span><strong>{friendlyMobileAction(result?.parsed?.action)}</strong>
              <span>手机</span><strong>{status?.device_id ? "已选择目标手机" : "默认目标手机"}</strong>
              <span>说明</span><strong>{result?.response || result?.message || "等待发送请求"}</strong>
            </div>
            <details className="advanced-panel">
              <summary>执行诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview mobile-result-preview">{JSON.stringify(result ?? { status: "pending", message: "等待发送请求。" }, null, 2)}</pre>
              </div>
            </details>
          </Card>
        </div>

        <Card title="安全与配置">
          <div className="security-summary">
            {(status?.safety ?? ["phone companion required", "explicit authorization required for call/sms"]).map((item) => <SkillChip key={item}>{friendlyMobileSafety(item)}</SkillChip>)}
          </div>
          <details className="advanced-panel">
            <summary>接入配置提示</summary>
            <div className="advanced-panel__content">
              <Card title="手机桥接配置">
                <div className="blocked-examples">
                  {(result?.configure ?? [
                    "OPENCLAW_MOBILE_BRIDGE_WEBHOOK_URL=https://phone-bridge.example/action",
                    "OPENCLAW_MOBILE_BRIDGE_SHARED_SECRET=<optional-hmac-secret>",
                    "OPENCLAW_MOBILE_BRIDGE_DEVICE_ID=primary_phone",
                  ]).map((item) => <span className="mono small" key={item}>{item}</span>)}
                </div>
              </Card>
            </div>
          </details>
        </Card>
      </div>
    </>
  );
}

function friendlyMobileAction(value: unknown) {
  const action = String(value ?? "");
  const labels: Record<string, string> = {
    find_phone: "找手机",
    call: "拨电话",
    sms: "发短信",
    message: "发送消息",
  };
  return labels[action] ?? (action || "等待发送");
}

function friendlyMobileSafety(item: string) {
  const labels: Record<string, string> = {
    "phone companion required": "需要手机伴随应用",
    "explicit authorization required for call/sms": "电话和短信需要明确授权",
  };
  return labels[item] ?? item.replace(/[_-]+/g, " ");
}
