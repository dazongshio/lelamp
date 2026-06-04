import { Cpu, Mic, Monitor, Radio, Speaker, Video, Zap } from "lucide-react";
import type { HardwareDevice } from "../api/types";
import { Card } from "./Card";
import { StatusBadge } from "./StatusBadge";
import "./components.css";

const icons = [Cpu, Video, Mic, Speaker, Monitor, Zap, Radio];

export function HardwareStatusCard({ device, index }: { device: HardwareDevice; index: number }) {
  const Icon = icons[index % icons.length];
  return (
    <Card className="hardware-card">
      <div className="hardware-card__icon">
        <Icon size={22} />
      </div>
      <strong>{device.label}</strong>
      <StatusBadge status={device.status} />
      <p>{device.note}</p>
      {device.metric && <strong className="hardware-card__metric">{device.metric}</strong>}
    </Card>
  );
}
