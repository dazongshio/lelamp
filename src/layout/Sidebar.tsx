import {
  BarChart3,
  Bot,
  CalendarCheck,
  ClipboardList,
  ClipboardCheck,
  ClipboardPenLine,
  FileText,
  FolderKanban,
  Gauge,
  HelpCircle,
  LampDesk,
  Monitor,
  MousePointerClick,
  Radar,
  Settings,
  SlidersHorizontal,
  Smartphone,
  Speech,
  Wand,
  Library,
} from "lucide-react";
import { NavLink, useLocation } from "react-router-dom";
import "./layout.css";

const navSections = [
  {
    label: "核心工作流",
    items: [
      { to: "/dashboard", label: "Dashboard", icon: Gauge },
      { to: "/shared", label: "文件工作区", icon: FolderKanban },
      { to: "/assistant", label: "助手", icon: Bot },
      { to: "/meeting", label: "会议助手", icon: CalendarCheck },
      { to: "/documents", label: "文档处理", icon: FileText },
      { to: "/wiki", label: "Wiki 知识库", icon: Library },
      { to: "/projection", label: "投影", icon: Monitor },
    ],
  },
  {
    label: "自动化与感知",
    items: [
      { to: "/desktop", label: "桌面代理", icon: MousePointerClick },
      { to: "/scene", label: "场景感知", icon: Radar },
      { to: "/voice", label: "语音", icon: Speech },
      { to: "/motors", label: "五轴控制", icon: SlidersHorizontal },
      { to: "/mobile", label: "移动端", icon: Smartphone },
      { to: "/smart-home", label: "智能设备", icon: Wand },
    ],
  },
  {
    label: "治理与验收",
    items: [
      { to: "/checklist", label: "功能清单", icon: ClipboardCheck },
      { to: "/validation", label: "验收测试", icon: ClipboardPenLine },
      { to: "/hardware", label: "硬件", icon: BarChart3 },
      { to: "/audit", label: "审计", icon: ClipboardList },
      { to: "/settings", label: "设置", icon: Settings },
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
          <LampDesk size={22} />
        </div>
        <div>
          <strong>LeLamp</strong>
          <span>智能办公终端</span>
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
                  >
                    <Icon size={18} />
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="sidebar__meta">
        <span>LeLamp v1.3.0</span>
        <span>OpenClaw v0.5.2</span>
        <span>© 2024 LeLamp Project</span>
      </div>
      <button className="sidebar__help">
        <HelpCircle size={18} />
        系统帮助 & 文档
      </button>
    </aside>
  );
}
