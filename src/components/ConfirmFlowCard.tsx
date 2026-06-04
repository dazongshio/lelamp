import { useState } from "react";
import { apiErrorMessage } from "../api/client";
import { cancelFullControl, confirmFullControl, requestFullControl } from "../api/settings";
import { StatusBadge } from "./StatusBadge";
import "./components.css";

const steps = ["目的说明", "影响确认", "最终确认", "生效"];

export function ConfirmFlowCard() {
  const [step, setStep] = useState(0);
  const [purpose, setPurpose] = useState("");
  const [requestId, setRequestId] = useState("");
  const [message, setMessage] = useState("全权控制不能一键开启。");
  const [status, setStatus] = useState("blocked");
  const canContinue = step > 0 || purpose.trim().length >= 10;

  async function next() {
    try {
      if (step === 0) {
        const response = await requestFullControl(purpose);
        setRequestId(String(response.data.request_id ?? ""));
        setStatus(String(response.data.status ?? "waiting_confirmation"));
        setMessage(String(response.data.message ?? "已记录请求"));
      } else {
        const response = await confirmFullControl(step + 1, requestId);
        setStatus(String(response.data.status ?? "waiting_confirmation"));
        setMessage(String(response.data.message ?? "已记录确认"));
      }
      setStep((value) => Math.min(3, value + 1));
    } catch (error) {
      setStatus("error");
      setMessage(apiErrorMessage(error));
    }
  }

  async function cancel() {
    try {
      const response = await cancelFullControl(requestId);
      setStatus(String(response.data.status ?? "blocked"));
      setMessage(String(response.data.message ?? "已取消"));
      setStep(0);
    } catch (error) {
      setStatus("error");
      setMessage(apiErrorMessage(error));
    }
  }

  return (
    <div className="confirm-flow">
      <div className="row">
        <strong>启用全权控制流程</strong>
        <StatusBadge status={status} label={status === "blocked" ? "高风险" : undefined} />
      </div>
      <div className="confirm-flow__steps">
        {steps.map((item, index) => (
          <span className={index <= step ? "active" : ""} key={item}>
            {index + 1}. {item}
          </span>
        ))}
      </div>
      <strong>步骤 {step + 1} / 4：{steps[step]}</strong>
      {step === 0 && (
        <textarea
          className="textarea"
          value={purpose}
          onChange={(event) => setPurpose(event.target.value)}
          placeholder="请清晰说明为什么需要临时提升至全权控制，例如：开发调试、兼容性验证、临时维护等（不少于 10 个字）..."
        />
      )}
      {step === 1 && <p className="warning-panel">全权控制可能允许桌面自动化，但仍不会绕过逐任务确认、文件白名单和审计记录。</p>}
      {step === 2 && <p className="danger-panel">最终确认：高风险操作如写入系统目录、删除文件、发送邮件、支付、提交表单仍默认阻止。</p>}
      {step === 3 && <p className="success-panel">流程已走到最终步骤。若当前运行环境不支持即时切换，会显示待接入，不会伪装生效。</p>}
      <p className="small muted">{message}</p>
      <div className="row">
        <button className="primary-button" disabled={!canContinue || step >= 3} onClick={() => void next()}>
          下一步：{steps[Math.min(3, step + 1)]}
        </button>
        <button className="ghost-button" onClick={() => void cancel()}>取消请求</button>
      </div>
    </div>
  );
}
