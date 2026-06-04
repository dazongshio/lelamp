import { AlertTriangle, CloudOff, Database, KeyRound, PackageCheck, Server, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { buildEnterpriseLocalPlatform, getEnterpriseLocalPlatformStatus, getEnterprisePolicy, getSecurity } from "../api/security";
import { getServicesStatus } from "../api/services";
import type { EnterpriseLocalPlatformBuildResponse, EnterpriseLocalPlatformStatus, EnterprisePolicyStatus, SecurityStatus, ServiceStatus } from "../api/types";
import { Card } from "../components/Card";
import { ConfirmFlowCard } from "../components/ConfirmFlowCard";
import { PageHeader } from "../components/PageHeader";
import { SettingsConfigTable } from "../components/SettingsConfigTable";
import { StatusBadge } from "../components/StatusBadge";
import { mockSecurity } from "../data/mockSecurity";
import "./pages.css";

export function SettingsPage() {
  const [security, setSecurity] = useState<SecurityStatus>(mockSecurity);
  const [enterprise, setEnterprise] = useState<EnterprisePolicyStatus | null>(null);
  const [localPlatform, setLocalPlatform] = useState<EnterpriseLocalPlatformStatus | null>(null);
  const [platformBuild, setPlatformBuild] = useState<EnterpriseLocalPlatformBuildResponse | null>(null);
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    setError("");
    try {
      const [securityResult, servicesResult, enterpriseResult, platformResult] = await Promise.all([
        getSecurity(),
        getServicesStatus(),
        getEnterprisePolicy(),
        getEnterpriseLocalPlatformStatus(),
      ]);
      setSecurity(securityResult.data);
      setServices(servicesResult.data.services);
      setEnterprise(enterpriseResult.data);
      setLocalPlatform(platformResult.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  async function buildPlatform() {
    setError("");
    setBusy(true);
    try {
      const result = await buildEnterpriseLocalPlatform(true);
      setPlatformBuild(result.data);
      const status = await getEnterpriseLocalPlatformStatus();
      setLocalPlatform(status.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  return (
    <>
      <PageHeader title="设置" description="安全、权限与企业部署策略；默认只读，高风险变更需要分步确认" actions={<button className="ghost-button" onClick={() => void load()}>刷新配置</button>} />
      <div className="page-grid">
        {error && <div className="danger-panel">加载失败：{error}</div>}
        <div className="warning-banner">
          <AlertTriangle size={20} />
          <div>
            <strong>重要安全提醒</strong>
            <p>本 UI 严禁提供“一键关闭所有安全限制”的选项。任何降低安全级别的操作都需要明确目的、分步确认与记录。</p>
          </div>
        </div>

        <Card title="安全与权限配置（当前生效）">
          <SettingsConfigTable security={security} />
        </Card>

        <div className="settings-grid">
          <Card>
            <ConfirmFlowCard />
          </Card>
          <Card title="高风险提示">
            <div className="danger-panel">
              <strong>即使在全权控制模式下，所有高风险操作仍需逐项确认。</strong>
              <ul>
                <li>写入系统目录</li>
                <li>修改系统配置</li>
                <li>安装/卸载软件包</li>
                <li>删除文件或目录</li>
                <li>发送邮件、支付、提交表单</li>
                <li>访问未授权用户目录或密钥</li>
              </ul>
              <strong>Web UI 不提供一键关闭所有安全限制。</strong>
            </div>
          </Card>
        </div>

        <div className="settings-grid">
          <Card title="安全边界与策略概要">
            <div className="settings-summary">
              <div><ShieldCheck size={18} /><strong>沙箱隔离</strong><StatusBadge status={security.permission_mode === "sandbox" ? "enabled" : "warning"} /><span>{security.permission_mode === "sandbox" ? "仅处理授权内容" : "变更需确认"}</span></div>
              <div><ShieldCheck size={18} /><strong>桌面控制</strong><StatusBadge status={security.desktop_backend === "audit_only" ? "warning" : "blocked"} label={security.desktop_backend === "audit_only" ? "仅审计预览" : "需授权"} /><span>高风险动作不会自动执行</span></div>
              <div><ShieldCheck size={18} /><strong>文件访问范围</strong><StatusBadge status="enabled" label="受控" /><span>{security.allowed_roots.length} 个授权目录</span></div>
              <div><ShieldCheck size={18} /><strong>审计记录</strong><StatusBadge status="ok" label="已开启" /><span>关键操作会留痕</span></div>
              <div><CloudOff size={18} /><strong>云端 AI 策略</strong><StatusBadge status={enterprise?.cloud_ai_enabled ? "enabled" : "blocked"} label={enterprise?.cloud_ai_enabled ? "允许" : "禁用"} /><span>由企业策略控制</span></div>
              <div><KeyRound size={18} /><strong>审计签名</strong><StatusBadge status={enterprise?.audit_signing.status ?? "needs_config"} /><span>{enterprise?.audit_signing.key_configured ? "签名密钥已配置" : "待配置签名密钥"}</span></div>
              <div><Database size={18} /><strong>本地数据平台</strong><StatusBadge status={localPlatform?.status === "available" ? "available" : "pending"} /><span>{localPlatform?.status === "available" ? "交付包可用" : "等待生成"}</span></div>
              <div><AlertTriangle size={18} /><strong>默认拦截的高风险动作</strong><StatusBadge status="blocked" /><span>自动发送邮件、删除文件、支付、提交表单、任意目录读取</span></div>
            </div>
          </Card>
          <div className="stack">
            <Card title="系统运行状态">
              <div className="definition-grid">
                <span>文件工作区</span><strong>受控可用</strong>
                <span>上传入口</span><strong>仅限用户导入内容</strong>
                <span>投影输出</span><strong>演示内容单独隔离</strong>
                <span>访问令牌</span><strong>{security.token_required ?? security.console_token_required ? "已要求" : "未要求"}</strong>
                <span>云端 AI</span><strong>{enterprise?.cloud_ai_enabled ?? security.cloud_ai_enabled ?? true ? "允许使用" : "企业策略禁用"}</strong>
                <span>企业策略</span><strong>{enterprise?.policy_file_present ? "已配置" : "使用默认安全策略"}</strong>
                <span>审计签名</span><strong>{enterprise?.audit_signing.key_configured ? "已配置" : "待配置"}</strong>
                <span>LeLamp 版本</span><strong>v1.3.0</strong>
                <span>本地代理版本</span><strong>v0.5.2</strong>
              </div>
            </Card>
            <details className="advanced-panel">
              <summary>高级诊断</summary>
              <div className="advanced-panel__content">
                <Card title="系统目录">
                  <div className="definition-grid">
                    <span>工作区</span><strong>{security.workspace_dir}</strong>
                    <span>上传入口</span><strong>{security.shared_inbox_dir}</strong>
                    <span>投影目录</span><strong>{security.projection_dir}</strong>
                    <span>记忆文件</span><strong>{security.memory_path ?? "-"}</strong>
                    <span>审计日志</span><strong>{security.audit_log_path}</strong>
                    <span>授权目录</span><strong>{security.allowed_roots.join(" / ") || "-"}</strong>
                  </div>
                </Card>
                <Card title="企业策略文件">
                  <div className="definition-grid">
                    <span>策略文件</span><strong>{enterprise?.policy_file_present ? enterprise.policy_path : "未配置，使用默认策略"}</strong>
                    <span>签名算法</span><strong>{enterprise?.audit_signing.algorithm ?? "HMAC-SHA256"}</strong>
                    <span>本地平台目录</span><strong>{localPlatform?.platform_dir ?? "-"}</strong>
                  </div>
                </Card>
              </div>
            </details>
            <Card title="企业策略执行项">
              <div className="list-rows compact">
                {(enterprise?.enforced_controls ?? ["workspace_allowed_roots", "signed_audit_export", "cloud_ai_disable_env_gate"]).map((item) => (
                  <div className="row-between" key={item}>
                    <span>{friendlyControlName(item)}</span>
                    <StatusBadge status="implemented" />
                  </div>
                ))}
              </div>
            </Card>
            <Card title="关键服务状态">
              <div className="list-rows">
                {services.map((service) => (
                  <div className="row-between" key={service.name}>
                    <span><Server size={15} /> {friendlyServiceName(service.name)}</span>
                    <StatusBadge status={service.status} />
                  </div>
                ))}
                {!services.length && <span className="small muted">暂无服务状态。</span>}
              </div>
            </Card>
          </div>
        </div>

        <Card
          title="企业本地算力与数据平台"
          subtitle="生成本地模型网关、数据分区、企业策略模板和部署清单；不包含密钥或真实模型权重"
          action={<StatusBadge status={localPlatform?.status === "available" ? "available" : "pending"} />}
        >
          <div className="enterprise-platform-grid">
            <div className="enterprise-platform-panel">
              <div className="row-between">
                <strong><PackageCheck size={16} />交付包</strong>
                <button className="primary-button" onClick={() => void buildPlatform()} disabled={busy}>
                  生成平台包
                </button>
              </div>
              <div className="definition-grid">
                <span>交付状态</span><strong>{localPlatform?.status === "available" ? "可用" : "等待生成"}</strong>
                <span>服务模板</span><strong>{(platformBuild?.services ?? localPlatform?.services ?? []).length} 个</strong>
                <span>数据分区</span><strong>{(platformBuild?.data_zones ?? localPlatform?.data_zones ?? []).length} 个</strong>
                <span>安全说明</span><strong>不包含密钥或真实模型权重</strong>
              </div>
            </div>
            <div className="enterprise-platform-panel">
              <strong><Server size={16} />本地服务模板</strong>
              <div className="list-rows compact">
                {(platformBuild?.services ?? localPlatform?.services ?? []).map((service) => (
                  <div className="row-between" key={service.name}>
                    <span>{friendlyServiceName(service.name)}</span>
                    <StatusBadge status={service.status} />
                  </div>
                ))}
              </div>
            </div>
            <div className="enterprise-platform-panel">
              <strong><Database size={16} />数据分区</strong>
              <div className="list-rows compact">
                {(platformBuild?.data_zones ?? localPlatform?.data_zones ?? []).map((zone) => (
                  <div className="row-between" key={zone.name}>
                    <span>{zone.name}</span>
                    <span className="small muted">{zone.classification}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <details className="advanced-panel">
            <summary>平台包诊断</summary>
            <div className="advanced-panel__content">
              <Card title="输出文件">
                <div className="definition-grid">
                  <span>平台目录</span><strong>{localPlatform?.platform_dir ?? "-"}</strong>
                  <span>Manifest</span><strong>{platformBuild?.manifest_path ?? localPlatform?.manifest_path ?? "-"}</strong>
                  <span>Bundle</span><strong>{platformBuild?.bundle_path ?? localPlatform?.latest_bundle ?? "-"}</strong>
                  <span>Model Registry</span><strong>{platformBuild?.model_registry_path ?? localPlatform?.offline_model_registry ?? "-"}</strong>
                </div>
              </Card>
            </div>
          </details>
        </Card>
      </div>
    </>
  );
}

function friendlyServiceName(name: string) {
  const labels: Record<string, string> = {
    api: "后端服务",
    frontend: "Web 界面",
    meeting: "会议助手",
    projection: "投影服务",
    hardware: "硬件服务",
    audit: "审计服务",
    model_gateway: "本地模型网关",
    data_platform: "数据平台",
    policy_server: "策略服务",
  };
  return labels[name] ?? name.replace(/[_-]+/g, " ");
}

function friendlyControlName(name: string) {
  const labels: Record<string, string> = {
    workspace_allowed_roots: "文件访问白名单",
    signed_audit_export: "签名审计导出",
    cloud_ai_disable_env_gate: "云端 AI 策略门禁",
  };
  return labels[name] ?? name.replace(/[_-]+/g, " ");
}
