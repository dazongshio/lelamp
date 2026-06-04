import { Lock } from "lucide-react";
import type { SecurityStatus } from "../api/types";
import { DataTable, type Column } from "./DataTable";
import { StatusBadge } from "./StatusBadge";

interface ConfigRow {
  key: string;
  label: string;
  value: string;
  protection: string;
  action: string;
}

export function SettingsConfigTable({ security }: { security: SecurityStatus }) {
  const rows: ConfigRow[] = [
    { key: "权限模式", label: "默认文件与操作边界", value: security.permission_mode === "sandbox" ? "沙箱模式" : "需管理员确认", protection: "受保护：需 3 步确认方可变更", action: "变更模式" },
    { key: "桌面控制", label: "投影与桌面连接权限", value: security.desktop_backend === "audit_only" ? "仅审计预览" : "全权模式需授权", protection: "只读", action: "查看" },
    { key: "文件访问范围", label: "仅限用户授权资料", value: `${security.allowed_roots.length} 个授权目录`, protection: "受保护：需管理员确认", action: "查看 / 变更" },
    { key: "文件工作区", label: "上传、拖入和生成结果", value: "受控工作区", protection: "只读", action: "查看" },
    { key: "投影输出", label: "演示预览与外接屏输出", value: "受控输出区", protection: "受保护：需管理员确认", action: "查看 / 变更" },
  ];

  const columns: Column<ConfigRow>[] = [
    {
      key: "key",
      title: "配置项",
      render: (row) => (
        <div>
          <strong>{row.key}</strong>
          <div className="small muted">{row.label}</div>
        </div>
      ),
      width: "31%",
    },
    { key: "value", title: "当前状态", render: (row) => <span className="config-value">{row.value}</span>, width: "30%" },
    { key: "protection", title: "保护状态", render: (row) => row.protection },
    {
      key: "action",
      title: "操作",
      render: (row) => (
        <button className="ghost-button">
          {row.action}
          <Lock size={14} />
        </button>
      ),
      width: "150px",
    },
  ];

  return (
    <div>
      <DataTable rows={rows} columns={columns} rowKey={(row) => row.key} />
      <div className="table-note">
        <StatusBadge status="ok" label="默认只读" />
        以上配置由系统策略控制，变更会写入审计记录并在管理员确认后生效。
      </div>
    </div>
  );
}
