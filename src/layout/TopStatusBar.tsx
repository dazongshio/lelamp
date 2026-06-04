import { CalendarCheck, HardDrive, UserCircle } from "lucide-react";
import { useCallback } from "react";
import { getHardwareStatus } from "../api/hardware";
import { getSecurity } from "../api/security";
import { StatusBadge } from "../components/StatusBadge";
import { useApi } from "../hooks/useApi";
import { usePolling } from "../hooks/usePolling";
import "./layout.css";

export function TopStatusBar() {
  const security = useApi(getSecurity, []);
  const hardware = useApi(getHardwareStatus, []);

  const refresh = useCallback(async () => {
    await Promise.all([security.reload(), hardware.reload()]);
  }, [security.reload, hardware.reload]);

  usePolling(refresh, 15000, true);

  const data = security.data;
  const hardwareStatus = data?.hardware_enabled ? "正常" : (hardware.data?.devices ? "部分可用" : "未启用");
  const meetingMode = data?.meeting_mode_enabled ? "会议理解已开启" : "会议理解手动开启";
  const desktopMode = data?.desktop_backend === "audit_only" ? "仅审计预览" : "需用户确认";

  return (
    <header className="topbar">
      <div className="topbar__item">
        <span className="topbar__led" />
        <div>
          <strong>{security.error ? "连接异常" : "设备在线"}</strong>
          <span>LeLamp 本地终端</span>
        </div>
      </div>
      <div className="topbar__item topbar__badge-item">
        <StatusBadge status={data?.permission_mode === "sandbox" ? "enabled" : "warning"} label="沙箱模式" tone="primary" />
        <span>{data?.permission_mode === "sandbox" ? "已启用" : "需确认"}</span>
      </div>
      <div className="topbar__item topbar__badge-item">
        <StatusBadge status={data?.desktop_backend === "audit_only" ? "warning" : "blocked"} label="安全控制" tone="warning" />
        <span>{desktopMode}</span>
      </div>
      <div className="topbar__item">
        <CalendarCheck size={22} className="topbar__shield" />
        <div>
          <strong>会议模式</strong>
          <span>{meetingMode}</span>
        </div>
      </div>
      <div className="topbar__item">
        <HardDrive size={22} className="topbar__shield" />
        <div>
          <strong>硬件状态</strong>
          <span>{hardwareStatus}</span>
        </div>
      </div>
      <div className="topbar__user">
        <UserCircle size={34} />
        <div>
          <strong>lelamp-admin</strong>
          <span>管理员</span>
        </div>
      </div>
    </header>
  );
}
