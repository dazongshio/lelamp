import { Fan, Home, Lightbulb, Power, RefreshCw, Send, Thermometer } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { controlSmartHome, getSmartHomeStatus } from "../api/smartHome";
import type { SmartHomeControlResponse, SmartHomeStatus } from "../api/types";
import { Card, InfoCard } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { SkillChip } from "../components/SkillChip";
import { StatusBadge } from "../components/StatusBadge";
import "./pages.css";

const examples = [
  { icon: Lightbulb, label: "开灯", command: "打开客厅灯" },
  { icon: Power, label: "关灯", command: "关闭客厅灯" },
  { icon: Thermometer, label: "空调", command: "空调调到 26 度" },
  { icon: Fan, label: "风扇", command: "打开风扇" },
];

export function SmartHomePage() {
  const [status, setStatus] = useState<SmartHomeStatus | null>(null);
  const [command, setCommand] = useState("打开客厅灯");
  const [entityName, setEntityName] = useState("");
  const [result, setResult] = useState<SmartHomeControlResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const response = await getSmartHomeStatus();
      setStatus(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit() {
    if (!command.trim()) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const response = await controlSmartHome(command, entityName);
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
        title="智能设备"
        description="控制已接入的灯、空调、风扇等设备；未配置时只显示连接提示，不会假装执行"
        actions={<button className="ghost-button" onClick={() => void load()}><RefreshCw size={16} />刷新</button>}
      />
      <div className="page-grid">
        {error && <div className="danger-panel">操作失败：{error}</div>}
        <div className="grid-4">
          <InfoCard icon={<Home size={20} />} label="连接状态" value={status?.configured ? "已连接" : "待配置"} note="设备桥接服务" status={<StatusBadge status={status?.status ?? "pending"} />} />
          <InfoCard icon={<Power size={20} />} label="控制权限" value={status?.configured ? "可发送请求" : "不可执行"} note="执行前仍需用户主动发送" status={<StatusBadge status={status?.configured ? "available" : "needs_config"} />} />
          <InfoCard icon={<Lightbulb size={20} />} label="已知设备" value={String(status?.known_entities.length ?? 0)} note="可通过名称控制" />
          <InfoCard icon={<Send size={20} />} label="设备桥" value={status?.webhook_configured || status?.home_assistant_configured ? "已接入" : "未接入"} note="支持本地或自定义桥接" />
        </div>

        <div className="smart-home-grid">
          <Card title="控制请求" subtitle="只会调用已配置的设备桥；未配置时返回配置提示">
            <div className="smart-home-form">
              <label>
                <span>自然语言命令</span>
                <input className="input" value={command} onChange={(event) => setCommand(event.target.value)} />
              </label>
              <label>
                <span>指定实体名（可选）</span>
                <input className="input" value={entityName} onChange={(event) => setEntityName(event.target.value)} placeholder="例如：客厅灯" />
              </label>
              <div className="mobile-actions">
                {examples.map((item) => {
                  const Icon = item.icon;
                  return <button className="ghost-button" key={item.label} onClick={() => setCommand(item.command)}><Icon size={16} />{item.label}</button>;
                })}
                <button className="primary-button" onClick={() => void submit()} disabled={busy}><Send size={16} />发送控制请求</button>
              </div>
            </div>
          </Card>

          <Card title="执行结果" action={<StatusBadge status={result?.status ?? "pending"} />}>
            <div className="definition-grid">
              <span>状态</span><StatusBadge status={result?.status ?? "pending"} />
              <span>设备</span><strong>{String(result?.parsed?.entity_name ?? result?.parsed?.entity_id ?? (entityName || "-"))}</strong>
              <span>动作</span><strong>{friendlySmartHomeAction(result)}</strong>
              <span>说明</span><strong>{result?.response || result?.reason || result?.error || "等待发送控制请求"}</strong>
            </div>
            <details className="advanced-panel">
              <summary>执行诊断</summary>
              <div className="advanced-panel__content">
                <pre className="json-preview smart-home-result-preview">{JSON.stringify(result ?? { status: "pending", message: "等待发送控制请求。" }, null, 2)}</pre>
              </div>
            </details>
          </Card>
        </div>

        <Card title="配置与能力">
          <div className="security-summary">
            {(status?.capabilities ?? ["turn_on", "turn_off", "set_temperature"]).map((item) => <SkillChip key={item}>{friendlySmartHomeCapability(item)}</SkillChip>)}
          </div>
          <details className="advanced-panel">
            <summary>接入配置提示</summary>
            <div className="advanced-panel__content">
              <Card title="设备桥配置">
                <div className="blocked-examples">
                  {(result?.configure?.home_assistant ?? [
                    "OPENCLAW_SMART_HOME_PROVIDER=home_assistant",
                    "OPENCLAW_HOME_ASSISTANT_URL=http://homeassistant.local:8123",
                    "OPENCLAW_HOME_ASSISTANT_TOKEN=<long-lived-token>",
                    "OPENCLAW_SMART_HOME_ENTITIES={\"客厅灯\":\"light.living_room\"}",
                  ]).map((item) => <span className="mono small" key={item}>{item}</span>)}
                  {(result?.configure?.webhook ?? ["OPENCLAW_SMART_HOME_WEBHOOK_URL=https://your-bridge.example/control"]).map((item) => <span className="mono small" key={item}>{item}</span>)}
                </div>
              </Card>
            </div>
          </details>
        </Card>
      </div>
    </>
  );
}

function friendlySmartHomeAction(result: SmartHomeControlResponse | null) {
  if (!result) return "等待发送";
  const parsed = result.parsed ?? {};
  const service = String(result.service ?? parsed.service ?? "");
  const labels: Record<string, string> = {
    turn_on: "打开",
    turn_off: "关闭",
    set_temperature: "设置温度",
  };
  return labels[service] ?? (service || "已提交控制请求");
}

function friendlySmartHomeCapability(value: string) {
  const labels: Record<string, string> = {
    turn_on: "打开设备",
    turn_off: "关闭设备",
    set_temperature: "设置温度",
  };
  return labels[value] ?? value.replace(/[_-]+/g, " ");
}
