import "./components.css";

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  return (
    <div className="progress-wrap">
      <div className="progress">
        <span style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
      </div>
      {label && <span className="small muted">{label}</span>}
    </div>
  );
}
