import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { AssistantPanel } from "../components/AssistantPanel";
import { SecurityFooter } from "./SecurityFooter";
import { Sidebar } from "./Sidebar";
import { TopStatusBar } from "./TopStatusBar";
import "./layout.css";

const initialAssistantMessages = [
  {
    id: "system-welcome",
    role: "system" as const,
    text: "小爱同学已就绪。默认只处理你主动上传、拖入或授权采集的内容，关键操作会先确认并保留记录。",
    time: new Date().toTimeString().slice(0, 5),
    status: "online",
  },
];

export function AppShell({ children }: { children: ReactNode }) {
  const [assistantCollapsed, setAssistantCollapsed] = useState(() => window.matchMedia("(max-width: 1180px)").matches);

  useEffect(() => {
    const query = window.matchMedia("(max-width: 1180px)");
    const sync = () => setAssistantCollapsed(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  return (
    <div className={`app-shell ${assistantCollapsed ? "app-shell--assistant-collapsed" : ""}`}>
      <Sidebar />
      <TopStatusBar />
      <main className="main-content">{children}</main>
      <AssistantPanel
        collapsed={assistantCollapsed}
        initialMessages={initialAssistantMessages}
        onCollapsedChange={setAssistantCollapsed}
      />
      <SecurityFooter />
    </div>
  );
}
