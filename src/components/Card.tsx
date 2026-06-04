import type { ReactNode } from "react";
import "./components.css";

interface CardProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}

export function Card({ title, subtitle, action, className = "", children }: CardProps) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <div className="card__header">
          <div>
            {title && <h2 className="card__title">{title}</h2>}
            {subtitle && <p className="card__subtitle">{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      <div className="card__body">{children}</div>
    </section>
  );
}

interface InfoCardProps {
  icon?: ReactNode;
  label: string;
  value: ReactNode;
  note?: ReactNode;
  status?: ReactNode;
}

export function InfoCard({ icon, label, value, note, status }: InfoCardProps) {
  return (
    <div className="info-card">
      <div className="info-card__icon">{icon}</div>
      <div className="info-card__content">
        <div className="row-between">
          <span className="info-card__label">{label}</span>
          {status}
        </div>
        <strong className="info-card__value">{value}</strong>
        {note && <span className="info-card__note">{note}</span>}
      </div>
    </div>
  );
}

interface MetricCardProps {
  label: string;
  value: string;
  note?: string;
  progress?: number;
  status?: ReactNode;
}

export function MetricCard({ label, value, note, progress, status }: MetricCardProps) {
  return (
    <div className="metric-card">
      <div className="row-between">
        <span className="metric-card__label">{label}</span>
        {status}
      </div>
      <strong className="metric-card__value">{value}</strong>
      {typeof progress === "number" && (
        <div className="progress">
          <span style={{ width: `${Math.min(100, Math.max(0, progress))}%` }} />
        </div>
      )}
      {note && <span className="metric-card__note">{note}</span>}
    </div>
  );
}
