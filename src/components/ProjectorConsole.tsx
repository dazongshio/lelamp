import type { ReactNode } from "react";

export type PillTone = "ok" | "warn" | "blocked" | "neutral";

export function StatusPill({ children, tone = "neutral" }: { children: ReactNode; tone?: PillTone }) {
  return <span className={`pc-status pc-status--${tone}`}>{children}</span>;
}

export function ConsoleTopbar({
  title,
  subtitle,
  statuses,
}: {
  title: ReactNode;
  subtitle: ReactNode;
  statuses?: ReactNode;
}) {
  return (
    <div className="pc-topbar">
      <div className="pc-topline">
        <span className="pc-topline-dot" />
        <div>
          <strong>{title}</strong>
          <span>{subtitle}</span>
        </div>
      </div>
      <div className="pc-topbar-spacer" />
      <div className="pc-topbar-statuses">{statuses}</div>
    </div>
  );
}

export function ConsolePageHead({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="pc-page-head">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="pc-actions">{actions}</div>}
    </header>
  );
}

export function Panel({
  title,
  subtitle,
  action,
  children,
  className = "",
  padded = false,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <section className={`pc-panel ${padded ? "pc-panel--padded" : ""} ${className}`}>
      {(title || subtitle || action) && (
        <div className="pc-panel-head">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <span>{subtitle}</span>}
          </div>
          {action}
        </div>
      )}
      <div className="pc-panel-body">{children}</div>
    </section>
  );
}

export function FlowStep({ index, title, note }: { index: string; title: string; note: string }) {
  return (
    <div className="pc-flow-step">
      <b>{index}</b>
      <strong>{title}</strong>
      <span>{note}</span>
    </div>
  );
}

export function StatLine({ label, value }: { label: ReactNode; value: ReactNode }) {
  return (
    <div className="pc-stat-line">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="pc-empty">{children}</div>;
}
