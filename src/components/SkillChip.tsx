import type { ReactNode } from "react";
import "./components.css";

interface SkillChipProps {
  children: ReactNode;
  muted?: boolean;
}

export function SkillChip({ children, muted = false }: SkillChipProps) {
  return <span className={`skill-chip ${muted ? "skill-chip--muted" : ""}`}>{children}</span>;
}
