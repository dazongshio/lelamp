import { CalendarCheck, HardDrive, Monitor, Server, UserCircle } from "lucide-react";
import { useCallback } from "react";
import { getRemoteSshStatus } from "../api/remote";
import { getHardwareStatus } from "../api/hardware";
import { getSecurity } from "../api/security";
import { StatusBadge } from "../components/StatusBadge";
import { useApi } from "../hooks/useApi";
import { usePolling } from "../hooks/usePolling";
import "./layout.css";

export function TopStatusBar() {
  const security = useApi(getSecurity, []);
  const hardware = useApi(getHardwareStatus, []);
  const remote = useApi(getRemoteSshStatus, []);

  const refresh = useCallback(async () => {
    await Promise.all([security.reload(), hardware.reload(), remote.reload()]);
  }, [security.reload, hardware.reload, remote.reload]);

  usePolling(refresh, 15000, true);

  const data = security.data;
  const hardwareStatus = data?.hardware_enabled ? "正常" : (hardware.data?.devices ? "部分可用" : "未启用");
  const meetingMode = data?.meeting_mode_enabled ? "会议理解已开启" : "会议理解手动开启";
  const fullControlEnabled = Boolean(data?.full_control_enabled);
  const sshReachable = remote.data?.saved_target?.host ? "远程电脑可连接" : "远程电脑未绑定";

  return (
    <header className="topbar">
      <div className="topbar__item">
        <Server size={22} className="topbar__shield" />
        <div>
          <strong>{security.error ? "连接异常" : "树莓派智能服务"}</strong>
          <span>智能助手运行在树莓派</span>
        </div>
      </div>
      <div className="topbar__item topbar__badge-item">
        <StatusBadge status="enabled" label="沙箱控制" tone="primary" />
        <span>沙箱控制不直连电脑</span>
      </div>
      <div className="topbar__item topbar__badge-item">
        <StatusBadge status={remote.data?.saved_target?.host ? "available" : "pending"} label={sshReachable} tone="warning" />
        <span>{remote.data?.saved_target?.host ?? "局域网电脑可选目标"}</span>
      </div>
      <div className="topbar__item topbar__badge-item">
        <StatusBadge status={fullControlEnabled ? "warning" : "blocked"} label="电脑直接控制" tone="warning" />
        <span>{fullControlEnabled ? "已开启" : "未开启"}</span>
      </div>
      <div className="topbar__item">
        <CalendarCheck size={22} className="topbar__shield" />
        <div>
          <strong>会议模式</strong>
          <span>{meetingMode}</span>
        </div>
      </div>
      <div className="topbar__item">
        <Monitor size={22} className="topbar__shield" />
        <div>
          <strong>树莓派共享空间</strong>
          <span>常开共享文件夹</span>
        </div>
      </div>
      <div className="topbar__item topbar__compact">
        <HardDrive size={22} className="topbar__shield" />
        <div>
          <strong>硬件状态</strong>
          <span>{hardwareStatus}</span>
        </div>
      </div>
      <div className="topbar__user">
        <UserCircle size={34} />
        <div>
          <strong>台灯管理员</strong>
          <span>管理员</span>
        </div>
      </div>
    </header>
  );
}
