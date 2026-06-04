import type { MeetingStep } from "../api/types";
import { Card } from "./Card";
import { StatusBadge } from "./StatusBadge";
import "./components.css";

interface WorkflowStepperProps {
  steps: MeetingStep[];
  activeStepId?: number;
  onSelectStep?: (stepId: number) => void;
}

export function WorkflowStepper({ steps, activeStepId, onSelectStep }: WorkflowStepperProps) {
  return (
    <div className="workflow-stepper">
      {steps.map((step) => (
        <button
          className={`workflow-stepper__item workflow-stepper__item--${step.status} ${step.id === activeStepId ? "workflow-stepper__item--active" : ""}`}
          key={step.id}
          onClick={() => onSelectStep?.(step.id)}
          type="button"
        >
          <span>{step.id}</span>
          <strong>{step.title}</strong>
          <StatusBadge status={step.status} />
        </button>
      ))}
    </div>
  );
}

export function WorkflowStepCard({ step, variant = "card" }: { step: MeetingStep; variant?: "card" | "detail" }) {
  return (
    <Card className={`workflow-card workflow-card--${variant}`} title={<span>{step.id}. {step.title}</span>} action={<StatusBadge status={step.status} />}>
      <dl className="definition-list">
        <dt>输入文件</dt>
        <dd>{step.input}</dd>
        <dt>系统理解</dt>
        <dd>{step.understanding}</dd>
        <dt>AI 执行结果</dt>
        <dd>{step.result}</dd>
        <dt>用户确认点</dt>
        <dd>{step.confirmation}</dd>
        <dt>输出文件路径</dt>
        <dd className="mono small">{step.outputPath}</dd>
      </dl>
    </Card>
  );
}
