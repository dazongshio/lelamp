import { DatabaseZap, Minus, Plus, RefreshCw, RotateCcw, Save, SlidersHorizontal } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { moveLeLampMotors, readLeLampMotors, saveLeLampPose } from "../api/hardware";
import type { LeLampMotorControlResponse, LeLampMotorName } from "../api/types";
import { Card } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import "./pages.css";

const motors: Array<{ key: LeLampMotorName; label: string }> = [
  { key: "base_yaw", label: "1 base_yaw" },
  { key: "base_pitch", label: "2 base_pitch" },
  { key: "elbow_pitch", label: "3 elbow_pitch" },
  { key: "wrist_roll", label: "4 wrist_roll" },
  { key: "wrist_pitch", label: "5 wrist_pitch" },
];

type MotorValues = Partial<Record<LeLampMotorName, number>>;
type MotorInputValues = Partial<Record<LeLampMotorName, string>>;

export function MotorControlPage() {
  const [current, setCurrent] = useState<MotorValues>({});
  const [target, setTarget] = useState<MotorInputValues>({});
  const [defaultPose, setDefaultPose] = useState<MotorInputValues>({});
  const [scanPose, setScanPose] = useState<MotorInputValues>({});
  const [projectionPose, setProjectionPose] = useState<MotorInputValues>({});
  const [steps, setSteps] = useState<MotorInputValues>({ base_yaw: "2", base_pitch: "2.5", elbow_pitch: "2", wrist_roll: "2", wrist_pitch: "2" });
  const [result, setResult] = useState<LeLampMotorControlResponse | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setBusy("read");
    setError("");
    setNotice("");
    try {
      const response = await readLeLampMotors();
      const pose = roundMotorValues(response.data.pose ?? {});
      const savedDefaultPose = roundMotorValues(response.data.saved_poses?.default ?? {});
      setCurrent(pose);
      setTarget((existing) => ({ ...motorValuesToInputs(savedDefaultPose), ...existing }));
      setDefaultPose(motorValuesToInputs(savedDefaultPose));
      setScanPose(motorValuesToInputs(response.data.saved_poses?.scan ?? {}));
      setProjectionPose(motorValuesToInputs(response.data.saved_poses?.projection ?? {}));
      setResult(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const maxError = useMemo(() => result?.max_error ?? 0, [result]);

  async function moveDelta(motor: LeLampMotorName, delta: number) {
    setBusy(motor);
    setError("");
    setNotice("");
    try {
      const response = await moveLeLampMotors({ mode: "delta", motor, delta, max_delta: Math.max(1, Math.abs(delta)), hold_seconds: 0.6 });
      applyResponse(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function applyTargets() {
    setBusy("apply");
    setError("");
    setNotice("");
    try {
      const targetValues = completeMotorValues(target);
      if (!targetValues) {
        setError("目标值需要填写 5 个轴的数字。");
        return;
      }
      const response = await moveLeLampMotors({ mode: "target", target: targetValues, max_delta: 90, hold_seconds: 0.8 });
      applyResponse(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function applyPose(pose: MotorInputValues, label: string) {
    const poseValues = completeMotorValues(pose);
    if (!poseValues) {
      setError(`${poseLabel(label)}需要填写 5 个轴的数字。`);
      return;
    }
    setBusy(label);
    setError("");
    setNotice("");
    try {
      const response = await moveLeLampMotors({ mode: "target", target: poseValues, max_delta: 90, hold_seconds: 0.8 });
      applyResponse(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function applyPoseMotor(motor: LeLampMotorName, value: string | undefined, label: string) {
    const numeric = inputNumber(value);
    if (numeric === null) {
      return;
    }
    setBusy(`${label}:${motor}`);
    setError("");
    setNotice("");
    try {
      const response = await moveLeLampMotors({ mode: "target", target: { [motor]: numeric }, max_delta: 90, hold_seconds: 0.8 });
      applyResponse(response.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  async function savePose(poseKey: "default" | "scan" | "projection", pose: MotorInputValues) {
    const completePose = completeMotorValues(pose);
    if (!completePose) {
      setError(`${poseLabel(poseKey)}需要填写 5 个轴的数字。`);
      return;
    }
    setBusy(`save:${poseKey}`);
    setError("");
    setNotice("");
    try {
      const response = await saveLeLampPose({ pose: poseKey, motors: completePose });
      setResult(response.data);
      setDefaultPose(motorValuesToInputs(response.data.saved_poses?.default ?? inputsToMotorValues(defaultPose)));
      setScanPose(motorValuesToInputs(response.data.saved_poses?.scan ?? inputsToMotorValues(scanPose)));
      setProjectionPose(motorValuesToInputs(response.data.saved_poses?.projection ?? inputsToMotorValues(projectionPose)));
      setNotice(`${poseLabel(poseKey)}已重设并写入后端。`);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy("");
    }
  }

  function applyResponse(data: LeLampMotorControlResponse) {
    setResult(data);
    const actual = roundMotorValues(data.actual ?? data.pose ?? {});
    setCurrent(actual);
    setTarget((existing) => ({ ...existing, ...motorValuesToInputs(actual) }));
  }

  function updateValue(setter: (value: MotorInputValues) => void, values: MotorInputValues, motor: LeLampMotorName, value: string) {
    setter({ ...values, [motor]: trimInputToFourDecimals(value) });
  }

  function normalizeValue(setter: (value: MotorInputValues) => void, values: MotorInputValues, motor: LeLampMotorName) {
    const numeric = inputNumber(values[motor]);
    if (numeric === null) {
      setter({ ...values, [motor]: "" });
      return;
    }
    setter({ ...values, [motor]: formatFixedInputNumber(numeric) });
  }

  return (
    <>
      <PageHeader
        title="五轴位姿控制"
        description="读取当前 5 个舵机位置，按步长加减或输入目标值后移动；所有动作都需要点击触发"
        actions={
          <div className="row">
            <button className="ghost-button" onClick={() => void load()} disabled={busy === "read"}><RefreshCw size={16} />读取当前</button>
            <button className="ghost-button" onClick={() => void applyPose(defaultPose, "default")} disabled={busy === "default"}><RotateCcw size={16} />默认位置</button>
            <button className="ghost-button" onClick={() => void applyPose(scanPose, "scan")} disabled={busy === "scan"}><SlidersHorizontal size={16} />扫描位置</button>
            <button className="ghost-button" onClick={() => void applyPose(projectionPose, "projection")} disabled={busy === "projection"}><SlidersHorizontal size={16} />投影位置</button>
            <button className="primary-button" onClick={() => void applyTargets()} disabled={busy === "apply"}><SlidersHorizontal size={16} />应用目标</button>
          </div>
        }
      />
      <div className="page-grid">
        {error && <div className="danger-panel">操作失败：{error}</div>}
        {notice && <div className="success-panel">{notice}</div>}
        <Card title="轴控制" subtitle="目标值可以手动输入，也可以点击“当前”同步舵机读数。">
          <div className="motor-pose-save-strip">
            <button className="ghost-button" onClick={() => void savePose("default", defaultPose)} disabled={busy === "save:default"}><DatabaseZap size={16} />重设默认位置的值</button>
            <button className="ghost-button" onClick={() => void savePose("scan", scanPose)} disabled={busy === "save:scan"}><DatabaseZap size={16} />重设扫描位置的值</button>
            <button className="ghost-button" onClick={() => void savePose("projection", projectionPose)} disabled={busy === "save:projection"}><DatabaseZap size={16} />重设投影位置的值</button>
          </div>
          <div className="motor-control-table">
            <div className="motor-control-table__header">
              <span>轴</span>
              <span>当前值</span>
              <span>目标值</span>
              <span>电机步长控制</span>
              <span>默认位置</span>
              <span>扫描位置</span>
              <span>投影位置</span>
              <span>动作</span>
            </div>
            {motors.map((motor) => {
              const currentValue = current[motor.key];
              const targetValue = target[motor.key] ?? formatFixedInputNumber(currentValue ?? 0);
              const stepValue = steps[motor.key] ?? "5";
              const defaultValue = defaultPose[motor.key];
              const scanValue = scanPose[motor.key];
              const projectionValue = projectionPose[motor.key];
              return (
                <div className="motor-control-row" key={motor.key}>
                  <strong>{motor.label}</strong>
                  <strong className="motor-current-value">{formatNumber(currentValue)}</strong>
                  <input className="input" type="number" step="0.0001" value={targetValue} onChange={(event) => updateValue(setTarget, target, motor.key, event.target.value)} onBlur={() => normalizeValue(setTarget, target, motor.key)} />
                  <input className="input" type="number" step="0.0001" value={stepValue} onChange={(event) => updateValue(setSteps, steps, motor.key, event.target.value)} onBlur={() => normalizeValue(setSteps, steps, motor.key)} />
                  <div className="motor-pose-cell">
                    <input className="input" type="number" step="0.0001" value={defaultValue ?? ""} onChange={(event) => updateValue(setDefaultPose, defaultPose, motor.key, event.target.value)} onBlur={() => normalizeValue(setDefaultPose, defaultPose, motor.key)} />
                    <button className="ghost-button" onClick={() => void applyPoseMotor(motor.key, defaultValue, "default")} disabled={busy === `default:${motor.key}`}>应用</button>
                  </div>
                  <div className="motor-pose-cell">
                    <input className="input" type="number" step="0.0001" value={scanValue ?? ""} onChange={(event) => updateValue(setScanPose, scanPose, motor.key, event.target.value)} onBlur={() => normalizeValue(setScanPose, scanPose, motor.key)} />
                    <button className="ghost-button" onClick={() => void applyPoseMotor(motor.key, scanValue, "scan")} disabled={busy === `scan:${motor.key}`}>应用</button>
                  </div>
                  <div className="motor-pose-cell">
                    <input className="input" type="number" step="0.0001" value={projectionValue ?? ""} onChange={(event) => updateValue(setProjectionPose, projectionPose, motor.key, event.target.value)} onBlur={() => normalizeValue(setProjectionPose, projectionPose, motor.key)} />
                    <button className="ghost-button" onClick={() => void applyPoseMotor(motor.key, projectionValue, "projection")} disabled={busy === `projection:${motor.key}`}>应用</button>
                  </div>
                  <div className="row motor-control-actions">
                    <button className="ghost-button" onClick={() => void moveDelta(motor.key, -Math.abs(inputNumber(stepValue) ?? 0))} disabled={busy === motor.key}><Minus size={16} /></button>
                    <button className="ghost-button" onClick={() => void moveDelta(motor.key, Math.abs(inputNumber(stepValue) ?? 0))} disabled={busy === motor.key}><Plus size={16} /></button>
                    <button className="ghost-button" onClick={() => setTarget({ ...target, [motor.key]: formatFixedInputNumber(currentValue ?? 0) })}><RotateCcw size={16} />当前</button>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>

        <Card title="执行结果">
          <div className="definition-grid">
            <span>状态</span><StatusBadge status={result?.status ?? "pending"} />
            <span>最大误差</span><strong>{formatNumber(maxError)}</strong>
            <span>端口</span><strong>{result?.port ?? "/dev/ttyACM0"}</strong>
            <span>硬件</span><strong>{result?.hardware_enabled === false ? "未启用写入" : "可写入"}</strong>
          </div>
          <details className="advanced-panel">
            <summary><Save size={14} /> 诊断数据</summary>
            <div className="advanced-panel__content">
              <pre className="json-preview voice-result-preview">{JSON.stringify(result ?? { status: "pending" }, null, 2)}</pre>
            </div>
          </details>
        </Card>
      </div>
    </>
  );
}

function formatNumber(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(4) : "-";
}

function round4(value: number) {
  return Math.round(value * 10000) / 10000;
}

function roundMotorValues(values: MotorValues): MotorValues {
  return Object.fromEntries(
    Object.entries(values)
      .map(([motor, value]) => [motor, round4(Number(value))] as const)
      .filter(([, value]) => Number.isFinite(value)),
  ) as MotorValues;
}

function motorValuesToInputs(values: MotorValues): MotorInputValues {
  return Object.fromEntries(
    Object.entries(roundMotorValues(values)).map(([motor, value]) => [motor, formatFixedInputNumber(value)]),
  ) as MotorInputValues;
}

function inputsToMotorValues(values: MotorInputValues): MotorValues {
  return Object.fromEntries(
    Object.entries(values)
      .map(([motor, value]) => [motor, inputNumber(value)] as const)
      .filter(([, value]) => value !== null)
      .map(([motor, value]) => [motor, value as number]),
  ) as MotorValues;
}

function completeMotorValues(values: MotorInputValues): Record<LeLampMotorName, number> | null {
  const entries = motors.map((motor) => [motor.key, inputNumber(values[motor.key])] as const);
  if (entries.some(([, value]) => !Number.isFinite(value))) {
    return null;
  }
  return Object.fromEntries(entries) as Record<LeLampMotorName, number>;
}

function poseLabel(value: string): string {
  if (value === "default") return "默认位置";
  if (value === "scan") return "扫描位置";
  if (value === "projection") return "投影位置";
  return "目标位置";
}

function partialMotorValues(values: MotorInputValues): MotorValues | null {
  const entries = Object.entries(values)
    .map(([motor, value]) => [motor, inputNumber(value)] as const)
    .filter(([, value]) => value !== null);
  if (!entries.length) {
    return null;
  }
  return Object.fromEntries(entries.map(([motor, value]) => [motor, value as number])) as MotorValues;
}

function inputNumber(value: string | undefined): number | null {
  if (value === undefined || value.trim() === "" || value === "-" || value === "." || value === "-.") {
    return null;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? round4(numeric) : null;
}

function formatFixedInputNumber(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "";
  }
  return round4(numeric).toFixed(4);
}

function trimInputToFourDecimals(value: string): string {
  const trimmed = value.trim();
  const match = trimmed.match(/^(-?\d*)(?:\.(\d*))?$/);
  if (!match) {
    return trimmed;
  }
  if (!trimmed.includes(".")) {
    return trimmed;
  }
  const integerPart = match[1] || (trimmed.startsWith("-") ? "-" : "");
  return `${integerPart}.${(match[2] ?? "").slice(0, 4)}`;
}
