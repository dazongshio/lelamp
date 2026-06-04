from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditLogger


@dataclass(frozen=True)
class PlanStep:
    skill: str
    action: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    permission: str = "sandbox"
    requires_confirmation: bool = False
    fallback: str = "return audited degraded result"

    def as_dict(self) -> dict[str, object]:
        return {
            "skill": self.skill,
            "action": self.action,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "permission": self.permission,
            "requires_confirmation": self.requires_confirmation,
            "fallback": self.fallback,
        }


class TaskPlanner:
    def __init__(self, audit: AuditLogger):
        self.audit = audit

    def plan(self, request: str, context_files: list[str] | None = None) -> dict[str, object]:
        context_files = context_files or []
        normalized = request.lower()
        steps: list[PlanStep] = []

        if any(marker in normalized for marker in ["meeting", "会议", "纪要"]):
            steps.extend(
                [
                    PlanStep(
                        skill="meeting_capture",
                        action="enable meeting mode or import transcript",
                        inputs=("meeting title", "participants", "transcript file or live transcript turns"),
                        outputs=("active meeting session",),
                        fallback="block transcript append until meeting mode is enabled",
                    ),
                    PlanStep(
                        skill="meeting_capture",
                        action="generate minutes and action items",
                        inputs=("active meeting transcript",),
                        outputs=("minutes markdown", "decisions", "action_items"),
                        fallback="return explicit missing-session error",
                    ),
                    PlanStep(
                        skill="projection_assistant",
                        action="render decisions for confirmation",
                        inputs=("decisions", "action_items"),
                        outputs=("projection confirmation card",),
                        fallback="write markdown card to projection_out without requiring physical projector",
                    ),
                ]
            )
        if any(marker in normalized for marker in ["pdf", "document", "合同", "文档", "文件"]):
            steps.extend(
                [
                    PlanStep(
                        skill="document_workspace",
                        action="verify files are imported",
                        inputs=("workspace filename", "allowed roots"),
                        outputs=("authorized workspace file list",),
                        fallback="reject source outside workspace/allowed roots and audit blocked action",
                    ),
                    PlanStep(
                        skill="document_workspace",
                        action="analyze, summarize, or compare documents",
                        inputs=("workspace text documents",),
                        outputs=("analysis JSON", "summary markdown", "comparison JSON"),
                        fallback="return text-only limitation when binary parser backend is missing",
                    ),
                ]
            )
        if any(marker in normalized for marker in ["email", "邮件"]):
            steps.append(
                PlanStep(
                    skill="document_workspace",
                    action="draft email without sending",
                    inputs=("confirmed meeting note or workspace document", "recipient label"),
                    outputs=("email draft markdown",),
                    requires_confirmation=True,
                    fallback="write draft only; never send email automatically",
                )
            )
        if any(marker in normalized for marker in ["scan", "扫描", "ocr"]):
            steps.extend(
                [
                    PlanStep(
                        skill="paper_scan",
                        action="capture/register image",
                        inputs=("explicit scan request", "workspace image filename or camera frame"),
                        outputs=("scan metadata JSON",),
                        fallback="return adapter_ready when camera capture adapter is unavailable",
                    ),
                    PlanStep(
                        skill="paper_scan",
                        action="run OCR backend when configured",
                        inputs=("registered scan image", "OCR language"),
                        outputs=("OCR request or OCR text output",),
                        fallback="return backend_missing until PaddleOCR is configured",
                    ),
                ]
            )
        if any(marker in normalized for marker in ["小爱", "天气", "提醒", "闹钟", "计算", "翻译", "换算"]):
            steps.append(
                PlanStep(
                    skill="xiaoai_utility",
                    action="route to local utility, search, or LLM-backed answer",
                    inputs=("user utility text",),
                    outputs=("local answer", "needs_llm/search status"),
                    fallback="return needs_llm/search instead of inventing unavailable external facts",
                )
            )
        if any(marker in normalized for marker in ["智能家居", "米家", "空调", "客厅灯", "扫地机器人"]):
            steps.append(
                PlanStep(
                    skill="smart_home_bridge",
                    action="resolve configured entity and call bridge API",
                    inputs=("device command", "configured entity map"),
                    outputs=("bridge status",),
                    requires_confirmation=True,
                    fallback="return needs_config unless a bridge and entity are explicitly configured",
                )
            )
        if any(marker in normalized for marker in ["打开应用", "打开网页", "找文件", "播放音乐", "音量"]):
            steps.append(
                PlanStep(
                    skill="desktop_safe_actions",
                    action="execute deterministic local desktop command if backend allows it",
                    inputs=("deterministic desktop command", "OPENCLAW_DESKTOP_BACKEND"),
                    outputs=("planned/opened/launched/blocked status",),
                    fallback="default audit_only records a plan without changing desktop state",
                )
            )
        if any(marker in normalized for marker in ["desktop", "电脑", "操作", "app"]):
            steps.append(
                PlanStep(
                    skill="desktop_operator",
                    action="request full-control permission",
                    inputs=("task description", "explicit user approval"),
                    outputs=("permission decision", "workflow plan"),
                    permission="full_control",
                    requires_confirmation=True,
                    fallback="block unless OPENCLAW_PERMISSION_MODE=full_control",
                )
            )

        if not steps:
            steps.append(
                PlanStep(
                    skill="document_workspace",
                    action="clarify target artifact and expected output",
                    inputs=("ambiguous user request",),
                    outputs=("clarification request",),
                    fallback="do not access files or execute desktop actions until the target is clear",
                )
            )

        payload = {
            "request": request,
            "context_files": context_files,
            "steps": [step.as_dict() for step in steps],
            "safety": {
                "default_permission": "sandbox",
                "file_scope": "workspace and configured OPENCLAW_ALLOWED_ROOTS only",
                "desktop_default": "audit_only",
                "full_control_requires_confirmation": True,
            },
        }
        self.audit.record("task.plan", target=request, details=payload)
        return payload
