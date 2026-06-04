import type { ProjectionCard } from "../api/types";
import "./components.css";

export function ProjectionPreview({ card, compact = false }: { card: ProjectionCard; compact?: boolean }) {
  return (
    <div className={`projection-preview projection-preview--${card.accent} ${compact ? "projection-preview--compact" : ""}`}>
      <div className="projection-preview__card">
        <div className="projection-preview__check">{card.mode === "countdown" ? "05:00" : card.mode === "action_card" ? "!" : "✓"}</div>
        <h3>{card.title}</h3>
        <p>{card.subtitle}</p>
        {!compact && <span>会议开始时间： {card.created_at}</span>}
      </div>
    </div>
  );
}
