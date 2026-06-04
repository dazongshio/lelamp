import { ShieldCheck } from "lucide-react";
import "./layout.css";

export function SecurityFooter() {
  return (
    <footer className="security-footer">
      <ShieldCheck size={18} />
      <strong>安全边界：</strong>
      <span>
        默认只处理用户主动上传、拖入或授权采集的内容。会议理解和屏幕捕获均需手动开启，关键操作会保留审计记录。
      </span>
    </footer>
  );
}
