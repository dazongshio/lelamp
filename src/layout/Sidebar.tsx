import {
  FileText,
  Home,
  LampDesk,
  MonitorUp,
  Mic2,
  MoreHorizontal,
  Presentation,
  ScanLine,
  Speech,
} from "lucide-react";
import { NavLink, useLocation } from "react-router-dom";
import { preloadPage } from "../pageLoaders";
import "./layout.css";

const navSections = [
  {
    label: "常用功能",
    items: [
      { to: "/dashboard", label: "首页", icon: Home },
      { to: "/meeting", label: "会议", icon: Mic2 },
      { to: "/projection", label: "投影", icon: Presentation },
      { to: "/documents", label: "文档", icon: FileText },
      { to: "/scan", label: "扫描", icon: ScanLine },
      { to: "/motors", label: "台灯控制", icon: LampDesk },
      { to: "/remote", label: "远程电脑", icon: MonitorUp },
      { to: "/voice", label: "语音助手", icon: Speech },
    ],
  },
  {
    label: "其他",
    items: [
      { to: "/results", label: "结果", icon: FileText },
      { to: "/pot", label: "更多", icon: MoreHorizontal },
    ],
  },
];

export function Sidebar() {
  const location = useLocation();
  const search = location.search;

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <div className="brand-mark">
          智
        </div>
        <div>
          <strong>智能投影助手</strong>
          <span>会议 · 投影 · 文档</span>
        </div>
      </div>
      <nav className="sidebar__nav" aria-label="主导航">
        {navSections.map((section) => (
          <div className="sidebar__section" key={section.label}>
            <span className="sidebar__section-label">{section.label}</span>
            <div className="sidebar__section-links">
              {section.items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={{ pathname: item.to, search }}
                    className={({ isActive }) => `sidebar__link ${isActive ? "active" : ""}`}
                    title={item.label}
                    onPointerEnter={() => preloadPage(item.to)}
                    onFocus={() => preloadPage(item.to)}
                  >
                    <span className="sidebar__dot"><Icon size={19} /></span>
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="sidebar-note">
        <strong>设备已就绪</strong>
        <span>如需帮助，请从首页选择要使用的功能。</span>
      </div>
    </aside>
  );
}
