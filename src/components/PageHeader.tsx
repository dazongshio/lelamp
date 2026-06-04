import { RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { ToggleSwitch } from "./ToggleSwitch";
import "./components.css";

interface PageHeaderProps {
  title: string;
  description: string;
  actions?: ReactNode;
}

export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="page-header__actions">
        {actions ?? (
          <>
            <span className="small muted">自动刷新：</span>
            <ToggleSwitch checked />
            <button className="icon-button" aria-label="刷新">
              <RefreshCw size={16} />
            </button>
            <span className="small muted">上次更新： {new Date().toLocaleString()}</span>
          </>
        )}
      </div>
    </header>
  );
}
