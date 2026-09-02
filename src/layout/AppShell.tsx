import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import "./layout.css";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell pc-app">
      <Sidebar />
      <main className="main-content">{children}</main>
    </div>
  );
}
