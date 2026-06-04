#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool, details: object = "") -> None:
    if not condition:
        raise AssertionError(f"{name} failed: {details}")
    print(f"ok - {name}")


def has_call(source: str, name: str) -> bool:
    return re.search(rf"\b{name}\s*\(", source) is not None


def main() -> int:
    meeting_page = read("src/pages/MeetingPage.tsx")
    meeting_api = read("src/api/meeting.ts")
    projection_page = read("src/pages/ProjectionPage.tsx")
    projection_api = read("src/api/projection.ts")
    documents_page = read("src/pages/DocumentsPage.tsx")
    documents_api = read("src/api/documents.ts")
    scene_page = read("src/pages/ScenePage.tsx")
    scene_api = read("src/api/scene.ts")
    validation_page = read("src/pages/ValidationPage.tsx")
    task_api = read("src/api/tasks.ts")
    assistant_panel = read("src/components/AssistantPanel.tsx")
    assistant_api = read("src/api/assistant.ts")
    assistant_css = read("src/components/components.css")
    app_shell = read("src/layout/AppShell.tsx")
    global_css = read("src/styles/global.css")
    pages_css = read("src/pages/pages.css")
    tokens_css = read("src/styles/tokens.css")

    realtime_endpoints = [
        "/api/meeting/provider/status",
        "/api/meeting/provider/preflight",
        "/api/meeting/realtime/status",
        "/api/meeting/realtime/events",
        "/api/meeting/realtime/start",
        "/api/meeting/realtime/stop",
        "/api/meeting/realtime/fetch-minutes",
    ]
    for endpoint in realtime_endpoints:
        assert_ok(f"meeting api endpoint {endpoint}", endpoint in meeting_api)

    assert_ok("meeting page starts realtime capture", has_call(meeting_page, "startMeetingRealtime"))
    assert_ok("meeting page stops realtime capture", has_call(meeting_page, "stopMeetingRealtime"))
    assert_ok("meeting page fetches Tingwu minutes", has_call(meeting_page, "fetchMeetingRealtimeMinutes"))
    assert_ok("meeting page polls realtime status", "window.setInterval" in meeting_page and "getMeetingRealtimeStatus(meetingId)" in meeting_page)
    assert_ok("meeting page polls realtime events", "getMeetingRealtimeEvents(meetingId)" in meeting_page)
    assert_ok(
        "meeting page finalizes stopped capture registration",
        "pendingStopMeetingId" in meeting_page
        and 'terminalStatus === "stopped" && pendingStopMeetingId === meetingId' in meeting_page
        and "stopMeetingRealtime(meetingId, false)" in meeting_page
        and "setPendingStopMeetingId(\"\")" in meeting_page,
    )
    assert_ok(
        "meeting page keeps pending stop until registration succeeds",
        meeting_page.index("const response = await stopMeetingRealtime(meetingId, false)")
        < meeting_page.index('if (terminalStatus === "stopped") setPendingStopMeetingId("")'),
    )
    assert_ok(
        "meeting page auto-registers failed capture outputs",
        "const terminalStatus = String(statusResult.data.status ?? \"\")" in meeting_page
        and 'terminalStatus === "failed"' in meeting_page
        and "shouldRegisterTerminalOutputs" in meeting_page
        and "const response = await stopMeetingRealtime(meetingId, false)" in meeting_page,
    )
    assert_ok(
        "meeting page gates controls during auto registration",
        "registeringTerminalOutputs" in meeting_page
        and "setRegisteringTerminalOutputs(true)" in meeting_page
        and "setRegisteringTerminalOutputs(false)" in meeting_page
        and "if (registeringTerminalOutputs) return" in meeting_page
        and "const realtimeControlsBusy = realtimeBusy || registeringTerminalOutputs" in meeting_page
        and "disabled={realtimeControlsBusy || realtimeActive}" in meeting_page
        and "disabled={realtimeControlsBusy || (!canStopRealtime && !canRegisterRealtimeOutputs)}" in meeting_page
        and "disabled={realtimeControlsBusy || !realtime?.meeting_id || realtimeActive}" in meeting_page,
    )
    assert_ok(
        "meeting page stops polling after stopped capture is registered",
        "const shouldPoll =" in meeting_page
        and 'realtimeStatus === "stopped" && pendingStopMeetingId === meetingId' in meeting_page
        and "if (!meetingId || !shouldPoll) return" in meeting_page,
    )
    assert_ok("meeting page renders realtime transcript", "realtimeTranscriptLines(realtime)" in meeting_page)
    assert_ok("meeting page renders audio metrics", all(token in meeting_page for token in ("audio_seconds", "websocket_audio_frames", "audio_rms", "audio_peak", "final_count")))
    assert_ok("meeting page renders Tingwu endpoint diagnostics", all(token in meeting_page for token in ("HTTP 端点", "WS 端点", "http_url", "ws_url")))
    assert_ok("meeting page renders configured and selected microphone diagnostics", all(token in meeting_page for token in ("配置麦克风", "实际麦克风", "configured_mic_device", "selected_mic_device")))
    assert_ok("meeting page renders microphone failure guidance", "麦克风诊断" in meeting_page and "tingwuMicMessage" in meeting_page and "mic_probe?.message" in meeting_page)
    assert_ok(
        "meeting page runs Tingwu local preflight",
        "runMeetingProviderPreflight" in meeting_api
        and "runMeetingProviderPreflight(1)" in meeting_page
        and "providerPreflight" in meeting_page
        and "providerPreflightBusy" in meeting_page
        and "本地预检" in meeting_page
        and "official_tingwu_endpoint" in meeting_page
        and "real_microphone_device" in meeting_page
        and "microphone_capture_device_matches" in meeting_page
        and "microphone_capture_signal" in meeting_page,
    )
    assert_ok(
        "meeting page gives local preflight next action",
        "preflightRecommendation(providerPreflight)" in meeting_page
        and "function preflightRecommendation" in meeting_page
        and "PreflightNextActionDetails" in meeting_page
        and "preflightPrimaryNextAction" in meeting_page
        and "formatShellCommand" in meeting_page
        and "formatRunnableCommand" in meeting_page
        and "工作目录" in meeting_page
        and "可复制执行" in meeting_page
        and "preflight?.next_actions" in meeting_page
        and "配置 TINGWU_API_KEY/DASHSCOPE_API_KEY" in meeting_page
        and "选择真实 USB/ALSA 麦克风" in meeting_page
        and "口播“乐灯听悟验收测试”" in meeting_page,
    )
    assert_ok(
        "meeting page links Tingwu credential guidance",
        "CredentialLinks" in meeting_page
        and "Tingwu credential links" in meeting_page
        and "已暴露的 key" in meeting_page
        and ".env.tingwu.local" in meeting_page
        and "误填 AccessKey ID" in meeting_page
        and "误填 AppKey" in meeting_page
        and "unexpected_app_id_shape" in meeting_page
        and "App ID 不是 tw_ 形状" in meeting_page
        and "credential_diagnostics?: {" in read("src/api/types.ts")
        and "links?: Array" in read("src/api/types.ts")
        and ".preflight-link-list" in pages_css,
    )
    assert_ok(
        "meeting page renders Tingwu acceptance checklist",
        "PreflightAcceptanceChecklist" in meeting_page
        and "acceptance_checklist" in meeting_page
        and "acceptanceChecklistWithRuntimeStatus" in meeting_page
        and "diagnostics.tingwuRealtimeDataId" in meeting_page
        and "import_transcript" in meeting_page
        and 'import_transcript: stepStatus("import_transcript") === "completed" ? "completed" : job ? "pending" : "ready"' in meeting_page
        and "websocket_audio_frames" in meeting_page
        and "stop_then_fetch_minutes" in meeting_page
        and "Tingwu acceptance checklist" in meeting_page
        and "how_to_test" in meeting_page
        and "AcceptanceChecklistCommand" in meeting_page
        and "preflight-acceptance-command" in meeting_page
        and "formatRunnableCommand(item.command, item.env, item.cwd)" in meeting_page
        and "formatRunnableCommand(item.audit_command, undefined, item.cwd)" in meeting_page
        and "preflight-acceptance-item" in pages_css,
    )
    assert_ok(
        "meeting provider preflight type covers capture checks",
        all(
            token in read("src/api/types.ts")
            for token in (
                "interface MeetingProviderPreflight",
                "checks: Record",
                "next_actions?",
                "audit_command?",
                "cwd?: string",
                "command?: string[]",
                "env?: Record<string, string>",
                "capture_probe",
                "selected_mic_device",
                "audio_format",
            )
        ),
    )
    assert_ok(
        "meeting page surfaces realtime start capture diagnostics",
        all(
            token in meeting_page
            for token in (
                "ApiClientError",
                "meetingRealtimeStartErrorMessage",
                "meetingRealtimeStartFailureResult",
                "meetingRealtimeStartFailureProvider",
                "formatCaptureProbeDiagnostics",
                "capture_probe",
                "audio_bytes",
                "audio_rms",
                "audio_peak",
                "selected_mic_device",
            )
        ),
    )
    assert_ok("meeting page normalizes mock microphone status", "function tingwuMicStatus" in meeting_page and 'status === "mock"' in meeting_page)
    assert_ok(
        "meeting page restores persisted Tingwu job result",
        all(token in meeting_page for token in ("meetingJobResult(activeJob)", "displayResult", 'stepOutput("minutes")', "tingwu_minutes_path", "openclaw_minutes_path")),
    )
    assert_ok(
        "meeting page renders realtime diagnostics",
        all(
            token in meeting_page
            for token in (
                "realtimeDiagnostics",
                "Provider 错误",
                "OpenClaw 错误",
                "providerError",
                "openclawError",
                "manifestPath",
                "通义听悟纪要",
                "OpenClaw 纪要",
                "tingwuMinutesPath",
                "openclawMinutesPath",
                "听悟 HTTP",
                "tingwuHttpActions",
                "tingwu_http_operations",
                "听悟链路",
                "Realtime dataId",
                "AI minutes dataId",
                "tingwuHttpChain",
                "CreateRealtimeMinutesTask",
                "GetTask",
            )
        ),
    )
    assert_ok(
        "meeting page prefers only structured Tingwu summary over output paths",
        "function structuredTingwuSummary" in meeting_page
        and "tingwuMinutes.structured_summary !== true" in meeting_page
        and 'tingwuMinutes.summary_source === "raw_payload"' in meeting_page
        and "const tingwuSummary = structuredTingwuSummary(tingwuMinutes)" in meeting_page
        and meeting_page.index("tingwuSummary") < meeting_page.index("nonEmptyString(minutes?.summary)")
        and meeting_page.index("tingwuSummary") < meeting_page.index("nonEmptyString(value.path)")
        and meeting_page.index("tingwuSummary") < meeting_page.index("nonEmptyString(minutes?.path)")
    )
    assert_ok("meeting page blocks fetch while active", '["starting", "running", "stopping", "finalizing"]' in meeting_page)
    assert_ok(
        "meeting page uses one active realtime status set",
        'const realtimeActiveStatuses = ["starting", "running", "stopping", "finalizing"]' in meeting_page
        and "const realtimeActive = realtimeActiveStatuses.includes(realtimeStatus)" in meeting_page
        and "disabled={realtimeControlsBusy || realtimeActive}" in meeting_page
        and "disabled={realtimeControlsBusy || !realtime?.meeting_id || realtimeActive}" in meeting_page,
    )
    assert_ok(
        "meeting page can register failed or stopped realtime outputs",
        "canRegisterRealtimeOutputs" in meeting_page
        and '["stopped", "failed"].includes(realtimeStatus)' in meeting_page
        and 'stopButtonLabel = canRegisterRealtimeOutputs ? "登记会议输出" : "停止实时会议"' in meeting_page
        and "(!canStopRealtime && !canRegisterRealtimeOutputs)" in meeting_page
        and "stopMeetingRealtime" in meeting_page,
    )
    assert_ok(
        "meeting page restores provider active meeting",
        "providerResult.data.providers.tongyi_tingwu.active_meeting_id" in meeting_page
        and "preferredMeetingId ?? nextActiveJob?.meeting_id ?? providerMeetingId" in meeting_page,
    )
    assert_ok("meeting page maps provider and web task ids", "provider_task_id" in meeting_page and "task_id_web" in meeting_page)
    assert_ok(
        "meeting page monitors realtime web task",
        all(
            token in meeting_page
            for token in (
                "getTask",
                "getTaskEvents",
                "loadRealtimeTaskMonitor",
                "realtimeTask",
                "realtimeTaskEvents",
                "实时任务监控",
                "taskMonitorValue",
                "websocket_audio_frames",
                "last_status_poll",
            )
        ),
    )
    assert_ok("task api exposes monitor endpoints", "/api/tasks/" in task_api and "/events" in task_api)
    assert_ok("meeting page keeps workflow steps", all(step in meeting_page for step in ("realtime_capture", "import_transcript", "minutes", "projection_confirmation")))
    assert_ok("meeting job type carries compact output", "output?: Record<string, unknown>" in read("src/api/types.ts"))
    assert_ok(
        "projection page can project workspace markdown",
        "/api/projection/markdown-file" in projection_api
        and "projectMarkdownFile" in projection_page
        and "投影 Markdown 文件" in projection_page
        and "meetings/.../projection_confirmation.md" in projection_page
        and "workspace/allowed-roots" in projection_page
        and "getDisplayMedia" in projection_page,
    )
    assert_ok(
        "projection page can project pptx sessions",
        "/api/projection/pptx/session" in projection_api
        and "projectPptxSession" in projection_page
        and "投影 PPT 文件" in projection_page
        and "上一页" in projection_page
        and "下一页" in projection_page
        and "workspace/allowed-roots" in projection_page,
    )
    assert_ok(
        "documents page exposes scan readiness and multi-provider OCR",
        "/api/scan/capture-readiness" in documents_api
        and "/api/scan/demo-image" in documents_api
        and "createDemoScanImage" in documents_page
        and "生成验收样张" in documents_page
        and "checkScanCaptureReadiness" in documents_page
        and "判断自动拍照候选" in documents_page
        and "OpenAI/DashScope" in documents_page
        and "vision_ocr" in documents_page,
    )
    assert_ok(
        "scene scan workflow links to documents scan",
        "/api/scene/workflow/trigger" in scene_api
        and "triggerResult.next_url" in scene_page
        and "继续到 Documents 扫描" in scene_page
        and "打开后续页面" in scene_page,
    )

    assert_ok("assistant api exposes notifications", "/api/assistant/notifications" in assistant_api)
    assert_ok("assistant panel polls notifications", "getAssistantNotifications(lastSeen)" in assistant_panel and "window.setInterval" in assistant_panel)
    assert_ok("assistant panel dedupes notifications", "seen.has(item.id)" in assistant_panel and "seen.add(item.id)" in assistant_panel)
    assert_ok("assistant panel updates deduped meeting notifications", "notificationKey" in assistant_panel and "mergeNotificationMessages" in assistant_panel)
    assert_ok("assistant panel has fixed collapse control", "assistant-panel__collapse" in assistant_panel and "aria-expanded" in assistant_panel)
    assert_ok(
        "assistant panel stays fixed on the right",
        all(
            token in assistant_css
            for token in (
                ".assistant-panel {",
                "position: fixed;",
                "right: 18px;",
                "bottom: calc(var(--footer-height) + 20px);",
                "width: var(--assistant-width);",
                ".assistant-panel--collapsed",
                "width: 52px;",
            )
        ),
    )
    assert_ok(
        "assistant panel uses triangle icon collapse affordance",
        all(token in assistant_panel for token in ("ChevronLeft", "ChevronRight", 'aria-label={collapsed ? "展开右侧助手" : "折叠右侧助手"}')),
    )
    assert_ok(
        "main layout keeps assistant as fixed overlay rail",
        "--assistant-width: 360px;" in tokens_css
        and "--assistant-rail-gutter: 82px;" in global_css
        and "padding: 20px var(--assistant-rail-gutter) 20px var(--content-padding-x);" in global_css
        and ".app-shell--assistant-collapsed" in global_css
        and "--assistant-rail-gutter: 72px;" in global_css
        and 'className={`app-shell ${assistantCollapsed ? "app-shell--assistant-collapsed" : ""}`}' in app_shell,
    )
    assert_ok(
        "assistant panel has mobile dock behavior",
        "@media (max-width: 1180px)" in assistant_css
        and "width: min(360px, calc(100vw - 28px));" in assistant_css
        and "max-height: min(640px, calc(100vh - 28px));" in assistant_css
        and "grid-template-columns: 1fr;" in global_css
        and "padding: 14px;" in global_css
        and 'window.matchMedia("(max-width: 1180px)")' in app_shell,
    )
    assert_ok(
        "preflight acceptance checklist wraps long evidence strings",
        ".preflight-acceptance-list" in pages_css
        and ".preflight-acceptance-item" in pages_css
        and ".preflight-acceptance-command" in pages_css
        and "min-width: 0;" in pages_css
        and pages_css.count("overflow-wrap: anywhere;") >= 3,
    )
    assert_ok(
        "validation page exposes full product target checks",
        all(
            token in validation_page
            for token in (
                "physical_projection_hardware",
                "document_scanning",
                "voice_scene_awareness",
                "projectionHardwareAuthorized",
                "scanWorkspaceName",
                "scanGenerateDemoImage",
                "generateScanDemoImage",
                "getSharedFiles({ type: \"image\"",
                "voiceSceneAuthorized",
            )
        ),
    )

    print("smoke_frontend_meeting_static complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
