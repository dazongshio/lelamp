#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


DEFAULT_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
DEFAULT_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


def request_json(
    base_url: str,
    path: str,
    *,
    token: str = "",
    method: str = "GET",
    payload: object | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict[str, object]]:
    data: bytes | None = None
    request_headers = dict(headers or {})
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        data=data,
        method=method,
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            payload_obj = json.loads(body)
        except json.JSONDecodeError:
            payload_obj = {"ok": False, "error": {"code": "non_json", "message": body, "details": {}}}
        return exc.code, payload_obj


def stop_tingwu_meeting_until_registered(
    base_url: str,
    token: str,
    meeting_id: str,
    *,
    run_followup: bool,
    timeout_seconds: float = 20.0,
) -> tuple[int, dict[str, object]]:
    deadline = time.monotonic() + timeout_seconds
    last_status = 0
    last_payload: dict[str, object] = {}
    active_statuses = {"starting", "running", "stopping", "finalizing"}
    while True:
        last_status, last_payload = request_json(
            base_url,
            "/api/meeting/realtime/stop",
            token=token,
            method="POST",
            payload={"meeting_id": meeting_id, "run_followup": run_followup},
        )
        data = last_payload.get("data", {}) if isinstance(last_payload.get("data"), dict) else {}
        if last_status == 200 and last_payload.get("ok") is True and str(data.get("status") or "") not in active_statuses:
            return last_status, last_payload
        if time.monotonic() >= deadline:
            return last_status, last_payload
        time.sleep(0.4)


def wait_for_tingwu_audio_or_terminal(
    base_url: str,
    token: str,
    meeting_id: str,
    *,
    min_frames: int = 3,
    timeout_seconds: float = 25.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    current: dict[str, object] = {}
    terminal_statuses = {"stopped", "failed", "completed"}
    while time.monotonic() < deadline:
        status, payload = request_json(base_url, f"/api/meeting/realtime/status?meeting_id={urllib.parse.quote(meeting_id)}", token=token)
        current = payload.get("data", {}) if status == 200 and isinstance(payload.get("data"), dict) else {}
        if int(current.get("websocket_audio_frames") or 0) >= min_frames:
            return current
        if str(current.get("status") or "") in terminal_statuses:
            return current
        time.sleep(0.5)
    return current


def live_tingwu_audio_unavailable(value: dict[str, object]) -> bool:
    session = value.get("session") if isinstance(value.get("session"), dict) else value
    minutes = value.get("minutes") if isinstance(value.get("minutes"), dict) else {}
    transcript_fallback = minutes.get("transcript_fallback") if isinstance(minutes.get("transcript_fallback"), dict) else {}
    error_text = " ".join(
        str(item or "")
        for item in (
            session.get("error") if isinstance(session, dict) else "",
            value.get("error"),
            minutes.get("provider_error") if isinstance(minutes, dict) else "",
            minutes.get("error") if isinstance(minutes, dict) else "",
        )
    )
    return (
        "No microphone audio frames were captured" in error_text
        or (
            int(session.get("websocket_audio_frames") or 0) > 0
            and int(session.get("final_count") or 0) <= 0
            and str(transcript_fallback.get("status") or "") == "failed"
            and str(transcript_fallback.get("reason") or "") == "asr_empty"
        )
        or (
            int(session.get("websocket_audio_frames") or 0) <= 0
            and float(session.get("audio_seconds") or 0) <= 0
            and str(session.get("status") or "") == "failed"
        )
    )


def upload_file(base_url: str, token: str, path: Path) -> tuple[int, dict[str, object]]:
    boundary = "----LeLampSmokeBoundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            b"Content-Type: text/plain\r\n\r\n",
            path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        urllib.parse.urljoin(base_url.rstrip("/") + "/", "api/shared/upload"),
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8", "replace"))


def create_minimal_pptx(path: Path) -> None:
    def slide_xml(title: str, body: str) -> str:
        return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p><a:p><a:r><a:t>{body}</a:t></a:r></a:p></p:txBody></p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>"""

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>""")
        archive.writestr("ppt/slides/slide1.xml", slide_xml("第一页标题", "第一页行动项"))
        archive.writestr("ppt/slides/slide2.xml", slide_xml("第二页标题", "第二页决策"))


def assert_ok(name: str, condition: bool, details: object = "") -> None:
    if not condition:
        raise AssertionError(f"{name} failed: {details}")
    print(f"ok - {name}")


def audit_action_visible(base_url: str, token: str, action: str) -> bool:
    status, payload = request_json(base_url, f"/api/audit/search?action={urllib.parse.quote(action)}&limit=5000", token=token)
    items = payload.get("data", {}).get("items", []) if isinstance(payload.get("data"), dict) else []
    return status == 200 and any(isinstance(item, dict) and action in str(item.get("action") or "") for item in items)


def tingwu_http_operation_chain_valid(operations: object, *, require_minutes: bool = False) -> bool:
    if not isinstance(operations, list):
        return False
    records = [item for item in operations if isinstance(item, dict)]
    if any(str(item.get("endpoint") or "") != DEFAULT_TINGWU_HTTP_URL for item in records):
        return False
    actions = [str(item.get("action") or "") for item in records]
    if "CreateTask" not in actions:
        return False
    if not require_minutes:
        return True
    if not {"CreateRealtimeMinutesTask", "GetTask"}.issubset(set(actions)):
        return False
    create = next((item for item in records if item.get("action") == "CreateTask" and item.get("response_data_id")), {})
    provider_task_id = str(create.get("response_data_id") or "")
    minutes = next((item for item in records if item.get("action") == "CreateRealtimeMinutesTask" and item.get("response_data_id")), {})
    minutes_source_id = str(minutes.get("request_data_id") or "")
    minutes_task_id = str(minutes.get("response_data_id") or "")
    get_task = next((item for item in records if item.get("action") == "GetTask" and item.get("request_data_id")), {})
    return bool(provider_task_id and minutes_task_id and minutes_source_id == provider_task_id and get_task.get("request_data_id") == minutes_task_id)


def run_restart_recovery_checks(base_url: str, token: str, workspace_root: Path) -> None:
    status, payload = request_json(base_url, "/api/meeting/jobs", token=token)
    jobs = payload.get("data", {}).get("items", []) if isinstance(payload.get("data"), dict) else []
    restored_job = next((item for item in jobs if isinstance(item, dict) and item.get("meeting_id")), {})
    restored_steps = restored_job.get("steps") if isinstance(restored_job.get("steps"), list) else []
    restored_outputs = {
        step.get("name"): step.get("output")
        for step in restored_steps
        if isinstance(step, dict) and isinstance(step.get("output"), dict)
    }
    restored_minutes = restored_outputs.get("minutes") if isinstance(restored_outputs.get("minutes"), dict) else {}
    restored_realtime = restored_outputs.get("realtime_capture") if isinstance(restored_outputs.get("realtime_capture"), dict) else {}
    restored_tingwu_minutes = restored_minutes.get("tingwu_minutes") if isinstance(restored_minutes.get("tingwu_minutes"), dict) else {}
    restored_tingwu_structured = restored_tingwu_minutes.get("structured_summary") is True
    restored_tingwu_source = str(restored_tingwu_minutes.get("summary_source") or "")
    assert_ok(
        "restart restored meeting jobs from workspace tasks",
        status == 200
        and payload.get("ok") is True
        and bool(restored_job)
        and restored_minutes.get("tingwu_minutes_path")
        and restored_minutes.get("path")
        and str(restored_tingwu_minutes.get("summary") or "").strip()
        and restored_tingwu_structured
        and restored_tingwu_source
        and restored_tingwu_source != "raw_payload"
        and int(restored_realtime.get("websocket_audio_frames") or 0) > 0
        and "events" not in restored_realtime,
        payload,
    )

    status, payload = request_json(base_url, "/api/tasks?limit=50", token=token)
    tasks = payload.get("data", {}).get("items", []) if isinstance(payload.get("data"), dict) else []
    realtime_task = next(
        (
            item for item in tasks
            if isinstance(item, dict)
            and isinstance(item.get("input"), dict)
            and item["input"].get("step") == "realtime_capture"
            and item["input"].get("meeting_id") == restored_job.get("meeting_id")
        ),
        {},
    )
    realtime_output = realtime_task.get("output") if isinstance(realtime_task.get("output"), dict) else {}
    realtime_monitor = realtime_output.get("monitor") if isinstance(realtime_output.get("monitor"), dict) else {}
    assert_ok(
        "restart restored realtime task monitor from workspace",
        bool(realtime_task)
        and int(realtime_monitor.get("websocket_audio_frames") or 0) > 0
        and float(realtime_monitor.get("audio_seconds") or 0) > 0,
        payload,
    )

    status, payload = request_json(base_url, "/api/assistant/notifications", token=token)
    notifications = payload.get("data", {}).get("items", []) if isinstance(payload.get("data"), dict) else []
    assert_ok(
        "restart restored assistant meeting notifications from workspace",
        status == 200
        and payload.get("ok") is True
        and any(isinstance(item, dict) and item.get("event") == "meeting_ai_minutes_ready" and item.get("payload", {}).get("meeting_id") == restored_job.get("meeting_id") for item in notifications),
        payload,
    )

    persisted_task_files = list((workspace_root / "web_tasks").glob("*.json"))
    assert_ok("restart recovery used persisted task files", bool(persisted_task_files), [str(item) for item in persisted_task_files[:5]])


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test LeLamp Web Console API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8790")
    parser.add_argument("--token", required=True)
    parser.add_argument("--expect-tingwu-unavailable", action="store_true")
    parser.add_argument("--expect-default-tingwu-endpoints", action="store_true")
    parser.add_argument("--expect-persisted-notification-redaction", action="store_true")
    parser.add_argument("--restart-recovery", action="store_true", help="Only verify persisted meeting jobs/tasks/notifications after restarting the Web Console with an existing workspace.")
    args = parser.parse_args()

    status, payload = request_json(args.base_url, "/api/security")
    assert_ok("unauthorized rejected", status == 401, payload)

    status, payload = request_json(args.base_url, "/api/security", token=args.token)
    assert_ok("security authorized", status == 200 and payload.get("ok") is True, payload)
    security = payload["data"]
    workspace_root = Path(str(security.get("workspace_dir") or "")).expanduser().resolve()
    audit_path = Path(str(security.get("audit_log_path") or "")).expanduser().resolve()
    assert_ok("sandbox visible", security.get("permission_mode") == "sandbox", security)
    assert_ok("audit_only visible", security.get("desktop_backend") == "audit_only", security)
    assert_ok("workspace listed in allowed roots", str(workspace_root) in {str(Path(str(item)).expanduser().resolve()) for item in security.get("allowed_roots", [])}, security)

    if args.restart_recovery:
        run_restart_recovery_checks(args.base_url, args.token, workspace_root)
        print("smoke_web_api restart recovery complete")
        return 0

    status, payload = request_json(
        args.base_url,
        "/api/settings/full-control/request",
        token=args.token,
        method="POST",
        payload={"purpose": "smoke test verifies full_control remains gated"},
    )
    full_control_request = payload.get("data", {})
    assert_ok(
        "full_control request is gated",
        status == 200
        and payload.get("ok") is True
        and full_control_request.get("status") == "waiting_confirmation"
        and bool(full_control_request.get("request_id")),
        payload,
    )
    status, payload = request_json(
        args.base_url,
        "/api/settings/full-control/confirm",
        token=args.token,
        method="POST",
        payload={"request_id": full_control_request.get("request_id"), "step": 3},
    )
    full_control_confirm = payload.get("data", {})
    assert_ok(
        "full_control confirm does not mutate runtime mode",
        status == 200
        and payload.get("ok") is True
        and full_control_confirm.get("status") == "backend_missing"
        and full_control_confirm.get("full_control_enabled") is False,
        payload,
    )
    status, payload = request_json(args.base_url, "/api/security", token=args.token)
    assert_ok(
        "sandbox still active after full_control confirm",
        status == 200
        and payload.get("ok") is True
        and payload.get("data", {}).get("permission_mode") == "sandbox"
        and payload.get("data", {}).get("full_control_enabled") is False,
        payload,
    )
    status, payload = request_json(
        args.base_url,
        "/api/settings/full-control/cancel",
        token=args.token,
        method="POST",
        payload={"request_id": full_control_request.get("request_id")},
    )
    assert_ok("full_control cancel is audited as blocked", status == 200 and payload.get("data", {}).get("status") == "blocked", payload)

    if args.expect_persisted_notification_redaction:
        status, payload = request_json(args.base_url, "/api/assistant/notifications", token=args.token)
        notification_text = json.dumps(payload.get("data", {}).get("items", []), ensure_ascii=False)
        notifications_path = workspace_root / ".assistant" / "notifications.json"
        persisted_notification_text = notifications_path.read_text(encoding="utf-8") if notifications_path.is_file() else ""
        assert_ok(
            "assistant loads persisted notifications with redaction",
            status == 200
            and payload.get("ok") is True
            and "old-secret-token" not in notification_text
            and "old-password" not in notification_text
            and "old-prefixed-key" not in notification_text
            and "old-camel-key" not in notification_text
            and "old-client-secret" not in notification_text
            and "old-dashscope-token" not in notification_text
            and "old-secret-token" not in persisted_notification_text
            and "old-password" not in persisted_notification_text
            and "old-prefixed-key" not in persisted_notification_text
            and "old-camel-key" not in persisted_notification_text
            and "old-client-secret" not in persisted_notification_text
            and "old-dashscope-token" not in persisted_notification_text
            and "[redacted]" in notification_text,
            {"api": payload, "persisted": persisted_notification_text},
        )

    note_title = "smoke_web_api_note"
    note_content = "Alice: 决定: 使用显示器测试投影\nBob: 待办: 检查 audit log\n"
    status, payload = request_json(
        args.base_url,
        "/api/shared/note",
        token=args.token,
        method="POST",
        payload={"title": note_title, "content": note_content},
    )
    assert_ok("note create", status == 200 and payload.get("ok") is True, payload)
    note_file = payload["data"]["file"]

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as stream:
        stream.write("smoke upload\nsha256 check\n")
        upload_path = Path(stream.name)
    expected_sha = hashlib.sha256(upload_path.read_bytes()).hexdigest()
    status, payload = upload_file(args.base_url, args.token, upload_path)
    assert_ok("upload file", status == 200 and payload.get("ok") is True, payload)
    uploaded = payload["data"]["files"][0]
    assert_ok("upload sha256", uploaded["sha256"] == expected_sha, uploaded)

    status, payload = request_json(args.base_url, "/api/shared/files", token=args.token)
    files = payload.get("data", {}).get("files", [])
    assert_ok("shared files list", status == 200 and any(item.get("relative_path") == note_file["relative_path"] for item in files), payload)

    meeting_payload = {"file_path": note_file["relative_path"], "title": "smoke_full_meeting_loop", "participants": ["Alice", "Bob"]}
    status, payload = request_json(args.base_url, "/api/meeting/import-transcript", token=args.token, method="POST", payload=meeting_payload)
    assert_ok("meeting import transcript", status == 200 and payload.get("ok") is True and payload.get("data", {}).get("status") == "completed", payload)

    status, payload = request_json(
        args.base_url,
        "/api/meeting/minutes",
        token=args.token,
        method="POST",
        payload={"transcript": note_file["relative_path"], "title": "smoke_full_meeting_loop", "participants": ["Alice", "Bob"]},
    )
    minutes = payload.get("data", {})
    assert_ok(
        "meeting minutes",
        status == 200
        and payload.get("ok") is True
        and minutes.get("status") == "completed"
        and minutes.get("path")
        and Path(str(minutes["path"])).is_file()
        and len(minutes.get("decisions", [])) >= 1
        and len(minutes.get("action_items", [])) >= 1,
        payload,
    )

    status, payload = request_json(args.base_url, "/api/meeting/decisions", token=args.token, method="POST", payload=meeting_payload)
    decisions = payload.get("data", {})
    assert_ok(
        "meeting decisions confirmation",
        status == 200
        and payload.get("ok") is True
        and decisions.get("status") == "waiting_confirmation"
        and decisions.get("path")
        and Path(str(decisions["path"])).is_file(),
        payload,
    )
    decision_task_id = str(decisions.get("task_id") or "")
    status, payload = request_json(
        args.base_url,
        "/api/meeting/confirm-step",
        token=args.token,
        method="POST",
        payload={"task_id": decision_task_id, "note": "smoke confirmed decisions"},
    )
    assert_ok(
        "meeting decision confirmed",
        status == 200
        and payload.get("ok") is True
        and payload.get("data", {}).get("status") == "completed"
        and payload.get("data", {}).get("confirmation", {}).get("confirmed") is True,
        payload,
    )

    status, payload = request_json(
        args.base_url,
        "/api/document/analyze",
        token=args.token,
        method="POST",
        payload={"file_path": note_file["relative_path"]},
    )
    document_task_id = str(payload.get("data", {}).get("task_id") or "")
    assert_ok("document analyze task for confirm-step boundary", status == 200 and payload.get("ok") is True and document_task_id, payload)
    status, payload = request_json(
        args.base_url,
        "/api/meeting/confirm-step",
        token=args.token,
        method="POST",
        payload={"task_id": document_task_id, "note": "should not mutate non-meeting task"},
    )
    status_after_boundary, task_after_boundary = request_json(args.base_url, f"/api/tasks/{urllib.parse.quote(document_task_id)}", token=args.token)
    document_task = task_after_boundary.get("data", {}) if isinstance(task_after_boundary.get("data"), dict) else {}
    assert_ok(
        "meeting confirm-step does not mutate non-meeting tasks",
        status == 403
        and payload.get("ok") is False
        and status_after_boundary == 200
        and document_task.get("type") == "document"
        and document_task.get("status") == "completed"
        and not (isinstance(document_task.get("output"), dict) and document_task.get("output", {}).get("confirmation")),
        {"blocked": payload, "task": task_after_boundary},
    )

    status, payload = request_json(args.base_url, "/api/meeting/action-items", token=args.token, method="POST", payload=meeting_payload)
    action_items = payload.get("data", {})
    assert_ok(
        "meeting action items",
        status == 200
        and payload.get("ok") is True
        and action_items.get("status") == "completed"
        and action_items.get("path")
        and Path(str(action_items["path"])).is_file(),
        payload,
    )

    status, payload = request_json(
        args.base_url,
        "/api/meeting/followup",
        token=args.token,
        method="POST",
        payload={
            "transcript": note_file["relative_path"],
            "title": "smoke_full_meeting_loop",
            "participants": ["Alice", "Bob"],
            "recipient": "team@example.local",
            "create_reminders": True,
            "render_projection": True,
        },
    )
    followup = payload.get("data", {})
    followup_status = str(followup.get("status") or "")
    followup_email = followup.get("email") if isinstance(followup.get("email"), dict) else {}
    followup_minutes = followup.get("minutes") if isinstance(followup.get("minutes"), dict) else {}
    followup_transcript = followup.get("transcript") if isinstance(followup.get("transcript"), dict) else {}
    followup_email_path = str(followup.get("email_draft_path") or "")
    assert_ok(
        "meeting followup package",
        status == 200
        and payload.get("ok") is True
        and followup_status in {"completed", "backend_missing"}
        and followup_minutes.get("path")
        and Path(str(followup_minutes["path"])).is_file()
        and followup_transcript.get("path")
        and Path(str(followup_transcript["path"])).is_file()
        and (
            (followup_status == "completed" and followup_email_path and Path(followup_email_path).is_file())
            or (followup_status == "backend_missing" and followup_email.get("status") == "backend_missing" and not followup_email_path)
        )
        and isinstance(followup.get("reminders"), dict)
        and isinstance(followup.get("projection"), dict),
        payload,
    )

    status, payload = request_json(args.base_url, "/api/meeting/reminders", token=args.token, method="POST", payload=meeting_payload)
    reminders = payload.get("data", {})
    assert_ok(
        "meeting reminders",
        status == 200
        and payload.get("ok") is True
        and reminders.get("status") == "completed"
        and reminders.get("path")
        and Path(str(reminders["path"])).is_file(),
        payload,
    )

    status, payload = request_json(args.base_url, "/api/meeting/projection-confirmation", token=args.token, method="POST", payload=meeting_payload)
    projection = payload.get("data", {})
    assert_ok(
        "meeting projection confirmation",
        status == 200
        and payload.get("ok") is True
        and projection.get("status") == "completed"
        and projection.get("path")
        and Path(str(projection["path"])).is_file(),
        payload,
    )

    status, payload = request_json(args.base_url, "/api/meeting/jobs", token=args.token)
    meeting_jobs = payload.get("data", {}).get("items", [])
    aggregate = next((item for item in meeting_jobs if item.get("transcript") == note_file["relative_path"]), {})
    aggregate_steps = {step.get("name"): step.get("status") for step in aggregate.get("steps", []) if isinstance(step, dict)}
    assert_ok(
        "meeting aggregate workflow",
        status == 200
        and aggregate_steps.get("import_transcript") == "completed"
        and aggregate_steps.get("minutes") == "completed"
        and aggregate_steps.get("decisions") == "completed"
        and aggregate_steps.get("action_items") == "completed"
        and aggregate_steps.get("followup") in {"completed", "blocked"}
        and aggregate_steps.get("reminders") == "completed"
        and aggregate_steps.get("projection_confirmation") == "completed",
        aggregate_steps,
    )

    blocked_path = "/api/shared/preview?file=../../etc/passwd"
    status, payload = request_json(args.base_url, blocked_path, token=args.token)
    assert_ok("blocked path 403", status == 403 and payload.get("ok") is False, payload)

    status, payload = request_json(
        args.base_url,
        "/api/projection/card",
        token=args.token,
        method="POST",
        payload={"type": "status", "title": "Smoke 投影卡", "message": "display preview smoke"},
    )
    assert_ok("projection card", status == 200 and payload.get("ok") is True, payload)

    status, payload = request_json(
        args.base_url,
        "/api/projection/markdown-file",
        token=args.token,
        method="POST",
        payload={"file_path": note_file["relative_path"], "title": "Smoke Markdown 投影"},
    )
    markdown_projection = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    projection_path = Path(str(markdown_projection.get("projection_path") or markdown_projection.get("path") or ""))
    assert_ok(
        "projection markdown file",
        status == 200
        and payload.get("ok") is True
        and markdown_projection.get("status") == "completed"
        and markdown_projection.get("source_workspace_name") == note_file["relative_path"]
        and projection_path.is_file()
        and projection_path.read_text(encoding="utf-8").strip(),
        payload,
    )

    with tempfile.NamedTemporaryFile("wb", suffix=".pptx", delete=False) as stream:
        pptx_path = Path(stream.name)
    create_minimal_pptx(pptx_path)
    status, payload = upload_file(args.base_url, args.token, pptx_path)
    assert_ok("upload pptx file", status == 200 and payload.get("ok") is True, payload)
    pptx_file = payload["data"]["files"][0]

    status, payload = request_json(
        args.base_url,
        "/api/projection/pptx/session",
        token=args.token,
        method="POST",
        payload={"file_path": pptx_file["relative_path"], "title": "Smoke PPTX 投影", "slide_index": 1},
    )
    pptx_projection = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    pptx_projection_path = Path(str(pptx_projection.get("projection_path") or pptx_projection.get("path") or ""))
    assert_ok(
        "projection pptx first slide",
        status == 200
        and payload.get("ok") is True
        and pptx_projection.get("status") == "completed"
        and pptx_projection.get("source_workspace_name") == pptx_file["relative_path"]
        and pptx_projection.get("slide_index") == 1
        and pptx_projection.get("slide_count") == 2
        and pptx_projection_path.is_file()
        and "第一页标题" in pptx_projection_path.read_text(encoding="utf-8"),
        payload,
    )

    status, payload = request_json(
        args.base_url,
        "/api/projection/pptx/session",
        token=args.token,
        method="POST",
        payload={"file_path": pptx_file["relative_path"], "title": "Smoke PPTX 投影", "slide_index": 1, "action": "next"},
    )
    pptx_next = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    pptx_next_path = Path(str(pptx_next.get("projection_path") or pptx_next.get("path") or ""))
    assert_ok(
        "projection pptx next slide",
        status == 200
        and payload.get("ok") is True
        and pptx_next.get("slide_index") == 2
        and pptx_next_path.is_file()
        and "第二页标题" in pptx_next_path.read_text(encoding="utf-8"),
        payload,
    )

    status, payload = request_json(
        args.base_url,
        "/api/projection/markdown-file",
        token=args.token,
        method="POST",
        payload={"file_path": "../../etc/passwd", "title": "blocked"},
    )
    assert_ok("projection markdown blocks outside path", status == 403 and payload.get("ok") is False, payload)

    status, payload = request_json(
        args.base_url,
        "/api/projection/pptx/session",
        token=args.token,
        method="POST",
        payload={"file_path": "../../etc/passwd", "title": "blocked"},
    )
    assert_ok("projection pptx blocks outside path", status == 403 and payload.get("ok") is False, payload)

    status, payload = request_json(args.base_url, "/api/hardware/status", token=args.token)
    assert_ok("hardware status", status == 200 and payload.get("ok") is True, payload)

    status, payload = request_json(args.base_url, "/api/hardware/scan", token=args.token)
    assert_ok("hardware scan", status == 200 and payload.get("ok") is True and "devices" in payload.get("data", {}), payload)

    status, payload = request_json(args.base_url, "/api/meeting/provider/status", token=args.token)
    provider = payload.get("data", {}).get("providers", {}).get("tongyi_tingwu", {})
    assert_ok(
        "tingwu provider status",
        status == 200
        and payload.get("ok") is True
        and provider.get("provider") == "tongyi_tingwu"
        and provider.get("status") in {"available", "needs_config", "unavailable"},
        payload,
    )
    assert_ok(
        "tingwu provider status exposes non-secret credential diagnostics",
        isinstance(provider.get("credential_diagnostics"), dict)
        and "api_key_kind" in provider["credential_diagnostics"]
        and "app_id_kind" in provider["credential_diagnostics"],
        provider,
    )
    provider_payload_text = json.dumps(provider, ensure_ascii=False)
    http_url = str(provider.get("http_url") or "")
    ws_url = str(provider.get("ws_url") or "")
    assert_ok(
        "tingwu provider status does not expose URL credentials or query strings",
        "@" not in http_url
        and "@" not in ws_url
        and "?" not in http_url
        and "?" not in ws_url
        and "leaky-token" not in provider_payload_text
        and "leaky-signature" not in provider_payload_text,
        provider,
    )
    assert_ok(
        "tingwu provider status exposes displayable endpoint diagnostics",
        http_url.startswith(("http://", "https://"))
        and ws_url.startswith(("ws://", "wss://"))
        and http_url
        and ws_url,
        provider,
    )
    if args.expect_default_tingwu_endpoints:
        assert_ok(
            "tingwu provider status uses expected default live endpoints in local suite",
            http_url == DEFAULT_TINGWU_HTTP_URL
            and ws_url == DEFAULT_TINGWU_WS_URL,
            provider,
        )

    status, payload = request_json(
        args.base_url,
        "/api/meeting/provider/preflight",
        token=args.token,
        method="POST",
        payload={"capture_seconds": 1},
    )
    preflight = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    preflight_checks = preflight.get("checks") if isinstance(preflight.get("checks"), dict) else {}
    preflight_capture = preflight.get("capture_probe") if isinstance(preflight.get("capture_probe"), dict) else {}
    preflight_next_actions = preflight.get("next_actions") if isinstance(preflight.get("next_actions"), list) else []
    preflight_acceptance = preflight.get("acceptance_checklist") if isinstance(preflight.get("acceptance_checklist"), list) else []
    runtime_python_suffix = "lelamp_runtime/.venv/bin/python"
    assert_ok(
        "tingwu provider preflight reports local readiness checks",
        status == 200
        and payload.get("ok") is True
        and preflight.get("provider") == "tongyi_tingwu"
        and preflight.get("status") in {"available", "needs_config", "unavailable"}
        and isinstance(preflight_checks, dict)
        and "official_tingwu_endpoint" in preflight_checks
        and "real_microphone_device" in preflight_checks
        and "microphone_capture_device_matches" in preflight_checks
        and "microphone_capture_open" in preflight_checks
        and "microphone_capture_signal" in preflight_checks
        and isinstance(preflight.get("credential_diagnostics"), dict)
        and isinstance(preflight_capture, dict)
        and any(isinstance(item, dict) and item.get("id") and item.get("message") for item in preflight_next_actions)
        and any(
            isinstance(item, dict)
            and (
                isinstance(item.get("env"), dict)
                or isinstance(item.get("cwd"), str)
                or isinstance(item.get("command"), list)
                or isinstance(item.get("audit_command"), list)
            )
            for item in preflight_next_actions
        )
        and any(isinstance(item, dict) and isinstance(item.get("cwd"), str) and "lelamp_runtime" in item.get("cwd", "") for item in preflight_next_actions)
        and any(
            isinstance(item, dict)
            and isinstance(item.get("command"), list)
            and any(str(part).endswith("verify_tingwu_live_suite.py") for part in item.get("command", []))
            and str(item.get("command", [""])[0]).endswith(runtime_python_suffix)
            for item in preflight_next_actions
        )
        and preflight.get("sample_rate") == 16000,
        payload,
    )
    configure_action = next(
        (
            item
            for item in preflight_next_actions
            if isinstance(item, dict) and item.get("id") == "configure_tingwu_credentials"
        ),
        {},
    )
    assert_ok(
        "tingwu provider preflight gives credential-specific guidance or ready acceptance",
        (
            preflight_checks.get("tingwu_api_key_configured") is True
            and preflight_checks.get("tingwu_app_id_configured") is True
            and isinstance(preflight.get("credential_diagnostics"), dict)
            and "api_key_kind" in preflight["credential_diagnostics"]
            and "app_id_kind" in preflight["credential_diagnostics"]
            and any(isinstance(item, dict) and item.get("id") == "start_live_meeting_acceptance" for item in preflight_next_actions)
        )
        or (
            isinstance(configure_action, dict)
            and isinstance(configure_action.get("credential_diagnostics"), dict)
            and "api_key_kind" in configure_action["credential_diagnostics"]
            and "app_id_kind" in configure_action["credential_diagnostics"]
            and ("Bailian" in str(configure_action.get("message") or "") or "百炼" in str(configure_action.get("message") or ""))
        ),
        preflight_next_actions,
    )
    assert_ok(
        "tingwu provider preflight exposes acceptance checklist",
        len(preflight_acceptance) >= 9
        and {"import_transcript", "credentials", "local_audio_preflight", "live_realtime_create_task", "websocket_pcm_streaming", "realtime_transcript", "stop_then_fetch_minutes", "openclaw_followup_outputs", "ui_task_assistant_audit"}
        <= {str(item.get("id") or "") for item in preflight_acceptance if isinstance(item, dict)}
        and all(isinstance(item, dict) and item.get("title") and item.get("how_to_test") and isinstance(item.get("evidence"), list) for item in preflight_acceptance)
        and any(
            isinstance(item, dict)
            and item.get("id") == "import_transcript"
            and item.get("status") == "ready"
            and "allowed roots" in str(item.get("how_to_test") or "")
            for item in preflight_acceptance
        )
        and any(
            isinstance(item, dict)
            and item.get("id") == "local_audio_preflight"
            and isinstance(item.get("command"), list)
            and str(item.get("command", [""])[0]).endswith(runtime_python_suffix)
            and any(str(part).endswith("preflight_tingwu_live.py") for part in item.get("command", []))
            for item in preflight_acceptance
        )
        and all(
            isinstance(item, dict)
            and isinstance(item.get("cwd"), str)
            and "lelamp_runtime" in item.get("cwd", "")
            and isinstance(item.get("command"), list)
            and str(item.get("command", [""])[0]).endswith(runtime_python_suffix)
            and any(str(part).endswith("verify_tingwu_live_suite.py") for part in item.get("command", []))
            and isinstance(item.get("audit_command"), list)
            and str(item.get("audit_command", [""])[0]).endswith(runtime_python_suffix)
            and any(str(part).endswith("audit_tingwu_live_evidence.py") for part in item.get("audit_command", []))
            for item in preflight_acceptance
            if isinstance(item, dict) and item.get("id") not in {"import_transcript", "local_audio_preflight"}
        ),
        preflight_acceptance,
    )
    if provider.get("mock") is True:
        assert_ok(
            "tingwu provider preflight blocks mock microphone as not live-ready",
            preflight.get("ready") is False
            and preflight_checks.get("real_microphone_device") is False
            and preflight_checks.get("microphone_capture_device_matches") is False
            and preflight_capture.get("status") == "blocked",
            preflight,
        )

    if provider.get("status") == "available":
        realtime_title = "smoke_tingwu_realtime token=web-title-token password=web-title-password"
        status, payload = request_json(
            args.base_url,
            "/api/meeting/realtime/start",
            token=args.token,
            method="POST",
            payload={"title": realtime_title, "participants": ["Alice", "Bob password=web-participant-password"], "max_seconds": 30},
        )
        tingwu_start = payload.get("data", {})
        meeting_id = str(tingwu_start.get("meeting_id") or "")
        assert_ok(
            "tingwu realtime start",
            status == 200
            and payload.get("ok") is True
            and tingwu_start.get("status") == "running"
            and meeting_id,
            payload,
        )
        realtime_task_id = str(tingwu_start.get("task_id_web") or tingwu_start.get("task_id") or "")
        realtime_start_text = json.dumps(tingwu_start, ensure_ascii=False)
        assert_ok(
            "tingwu realtime start redacts title and participant secrets",
            "web-title-token" not in realtime_start_text
            and "web-title-password" not in realtime_start_text
            and "web-participant-password" not in realtime_start_text
            and "web-title-token" not in meeting_id
            and "web-title-password" not in meeting_id
            and "web-participant-password" not in meeting_id,
            tingwu_start,
        )

        status, payload = request_json(args.base_url, f"/api/tasks/{urllib.parse.quote(realtime_task_id)}/cancel", token=args.token, method="POST", payload={})
        status_after_cancel_block, payload_after_cancel_block = request_json(args.base_url, f"/api/tasks/{urllib.parse.quote(realtime_task_id)}", token=args.token)
        realtime_task_after_cancel_block = payload_after_cancel_block.get("data", {}) if isinstance(payload_after_cancel_block.get("data"), dict) else {}
        assert_ok(
            "tingwu realtime task cancel is blocked in favor of meeting stop",
            status == 409
            and payload.get("ok") is False
            and payload.get("error", {}).get("code") == "realtime_capture_requires_stop"
            and status_after_cancel_block == 200
            and realtime_task_after_cancel_block.get("status") == "running",
            {"blocked": payload, "task": payload_after_cancel_block},
        )

        status, payload = request_json(args.base_url, f"/api/meeting/realtime/status?meeting_id={urllib.parse.quote(meeting_id)}", token=args.token)
        realtime_status = payload.get("data", {})
        assert_ok(
            "tingwu realtime status updates capture task",
            status == 200
            and payload.get("ok") is True
            and realtime_status.get("meeting_id") == meeting_id
            and float(realtime_status.get("audio_seconds") or 0) >= 0,
            payload,
        )

        status, payload = request_json(args.base_url, f"/api/meeting/realtime/events?meeting_id={urllib.parse.quote(meeting_id)}", token=args.token)
        realtime_events = payload.get("data", {}).get("events", [])
        assert_ok(
            "tingwu realtime events API",
            status == 200
            and payload.get("ok") is True
            and isinstance(realtime_events, list)
            and any(str(item.get("event") or "") in {"meeting_started", "transcript", "meeting_stopped", "websocket_open", "websocket_started", "tingwu_event"} for item in realtime_events if isinstance(item, dict)),
            payload,
        )

        status, payload = request_json(args.base_url, f"/api/tasks/{urllib.parse.quote(realtime_task_id)}/events", token=args.token)
        task_events = payload.get("data", {}).get("events", [])
        assert_ok(
            "tingwu realtime task event log",
            status == 200
            and payload.get("ok") is True
            and isinstance(task_events, list)
            and any(str(item.get("event") or "") in {"meeting_started", "transcript", "meeting_stopped", "tingwu_event"} for item in task_events if isinstance(item, dict)),
            payload,
        )

        status, payload = request_json(args.base_url, "/api/tasks?limit=50", token=args.token)
        task_items = payload.get("data", {}).get("items", [])
        realtime_task = next((item for item in task_items if isinstance(item, dict) and item.get("task_id") == realtime_task_id), {})
        assert_ok(
            "task monitor list route includes tingwu realtime task",
            status == 200
            and payload.get("ok") is True
            and isinstance(task_items, list)
            and bool(realtime_task),
            payload,
        )

        audio_ready_status = wait_for_tingwu_audio_or_terminal(args.base_url, args.token, meeting_id, min_frames=3, timeout_seconds=25)
        if int(audio_ready_status.get("websocket_audio_frames") or 0) <= 0 and str(audio_ready_status.get("status") or "") not in {"failed", "stopped", "completed"}:
            print(f"warning - tingwu live smoke did not observe audio frames before stop: {audio_ready_status}", file=sys.stderr)

        status, payload = stop_tingwu_meeting_until_registered(
            args.base_url,
            args.token,
            meeting_id,
            run_followup=False,
        )
        tingwu_stop = payload.get("data", {})
        if live_tingwu_audio_unavailable(tingwu_stop):
            assert_ok(
                "tingwu realtime live path reached provider but microphone produced no usable speech",
                payload.get("ok") is True
                and tingwu_stop.get("provider_status") in {"failed", "stopped"}
                and isinstance(tingwu_stop.get("session"), dict)
                and tingwu_stop["session"].get("task_id")
                and tingwu_stop["session"].get("transcript_path")
                and Path(str(tingwu_stop["session"]["transcript_path"])).is_file(),
                payload,
            )
            print("warning - skipping live Tingwu minutes assertions because no microphone audio frames were captured during smoke.", file=sys.stderr)
            status, payload = request_json(
                args.base_url,
                "/api/meeting/import-text",
                token=args.token,
                method="POST",
                payload={
                    "title": "smoke_meeting_text_import",
                    "text": "Alice: 决定: 使用本地会议素材进入 shared_inbox\nBob: 待办: 生成会议纪要",
                    "participants": ["Alice", "Bob"],
                },
            )
            assert_ok("meeting text import", status == 200 and payload.get("ok") is True and payload.get("data", {}).get("job"), payload)

            status, payload = request_json(
                args.base_url,
                "/api/hardware/test",
                token=args.token,
                method="POST",
                payload={"test": "projection"},
            )
            assert_ok("hardware projection test", status == 200 and payload.get("ok") is True, payload)

            status, payload = request_json(args.base_url, "/api/audit/search?status=blocked&limit=1000", token=args.token)
            blocked = payload.get("data", {}).get("items", [])
            assert_ok("audit blocked visible", status == 200 and len(blocked) >= 1, payload)
            assert_ok(
                "full_control gate is auditable",
                audit_action_visible(args.base_url, args.token, "full_control_request")
                and audit_action_visible(args.base_url, args.token, "full_control_confirm")
                and audit_action_visible(args.base_url, args.token, "full_control_cancel"),
                "full_control audit actions were not searchable",
            )

            print("smoke_web_api complete")
            return 0
        else:
            assert_ok(
                "tingwu realtime smoke observed microphone audio before stop",
                int(audio_ready_status.get("websocket_audio_frames") or tingwu_stop.get("session", {}).get("websocket_audio_frames") or 0) > 0,
                {"status": audio_ready_status, "stop": tingwu_stop},
            )
        session = tingwu_stop.get("session", {}) if isinstance(tingwu_stop.get("session"), dict) else {}
        minutes_data = tingwu_stop.get("minutes", {}) if isinstance(tingwu_stop.get("minutes"), dict) else {}
        manifest_path = Path(str(tingwu_stop.get("manifest_path") or ""))
        tingwu_job = tingwu_stop.get("job", {}) if isinstance(tingwu_stop.get("job"), dict) else {}
        tingwu_steps = {step.get("name"): step.get("status") for step in tingwu_job.get("steps", []) if isinstance(step, dict)}
        tingwu_step_outputs = {
            step.get("name"): step.get("output")
            for step in tingwu_job.get("steps", [])
            if isinstance(step, dict) and isinstance(step.get("output"), dict)
        }
        session_http_operations = session.get("tingwu_http_operations") if isinstance(session.get("tingwu_http_operations"), list) else []
        status, task_payload_after_stop = request_json(args.base_url, "/api/tasks?limit=50", token=args.token)
        task_items_after_stop = task_payload_after_stop.get("data", {}).get("items", [])
        realtime_task_after_stop = next((item for item in task_items_after_stop if isinstance(item, dict) and item.get("task_id") == realtime_task_id), {})
        realtime_task_output = realtime_task_after_stop.get("output") if isinstance(realtime_task_after_stop.get("output"), dict) else {}
        assert_ok(
            "tingwu realtime stop and outputs",
            status == 200
            and payload.get("ok") is True
            and tingwu_stop.get("status") == "stopped"
            and tingwu_stop.get("provider_status") == "stopped"
            and tingwu_stop.get("openclaw_status") == "completed"
            and session.get("transcript_path")
            and Path(str(session["transcript_path"])).is_file()
            and not session.get("minutes_path")
            and session.get("audio_path")
            and Path(str(session["audio_path"])).is_file()
            and Path(str(session["audio_path"])).stat().st_size > 44
            and float(session.get("audio_seconds") or 0) > 0
            and int(session.get("websocket_audio_frames") or 0) > 0
            and int(session.get("sample_rate") or 0) == 16000
            and session.get("audio_format") == "pcm"
            and int(session.get("audio_rms") or 0) > 0
            and int(session.get("audio_peak") or 0) > 0
            and manifest_path.is_file()
            and manifest_path.is_relative_to(Path(str(session["output_dir"])))
            and minutes_data.get("path")
            and Path(str(minutes_data["path"])).is_file()
            and tingwu_steps.get("realtime_capture") == "completed"
            and tingwu_steps.get("import_transcript") == "completed"
            and tingwu_steps.get("minutes") == "completed"
            and tingwu_steps.get("decisions") == "waiting_confirmation"
            and tingwu_steps.get("action_items") == "completed"
            and "followup" not in tingwu_steps
            and "reminders" not in tingwu_steps
            and "projection_confirmation" not in tingwu_steps,
            payload,
        )
        assert_ok(
            "tingwu meeting job carries compact persisted realtime outputs",
            isinstance(tingwu_step_outputs.get("realtime_capture"), dict)
            and isinstance(tingwu_step_outputs.get("minutes"), dict)
            and tingwu_step_outputs["realtime_capture"].get("transcript_path") == session.get("transcript_path")
            and int(tingwu_step_outputs["realtime_capture"].get("websocket_audio_frames") or 0) > 0
            and int(tingwu_step_outputs["realtime_capture"].get("sample_rate") or 0) == 16000
            and tingwu_step_outputs["realtime_capture"].get("audio_format") == "pcm"
            and tingwu_http_operation_chain_valid(tingwu_step_outputs["realtime_capture"].get("tingwu_http_operations"))
            and tingwu_step_outputs["minutes"].get("path") == minutes_data.get("path")
            and "events" not in tingwu_step_outputs["realtime_capture"],
            tingwu_step_outputs,
        )
        assert_ok(
            "tingwu task monitor carries realtime audio protocol",
            int(realtime_task_output.get("sample_rate") or 0) == 16000
            and realtime_task_output.get("audio_format") == "pcm"
            and tingwu_http_operation_chain_valid(realtime_task_output.get("tingwu_http_operations")),
            realtime_task_output,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
        manifest_outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), list) else []
        manifest_workspace_paths = {
            str(item.get("workspace_path") or "")
            for item in manifest_outputs
            if isinstance(item, dict)
        }
        output_dir = Path(str(session["output_dir"]))
        assert_ok(
            "tingwu meeting manifest archives realtime outputs",
            manifest.get("meeting_id") == meeting_id
            and manifest.get("provider") == "tongyi_tingwu"
            and manifest.get("provider_status") == "stopped"
            and manifest.get("openclaw_status") == "completed"
            and manifest_audio.get("sample_rate") == 16000
            and manifest_audio.get("format") == "pcm"
            and tingwu_http_operation_chain_valid(manifest.get("tingwu_http_operations"))
            and not manifest.get("tingwu_minutes_path")
            and manifest.get("openclaw_minutes_path") == minutes_data.get("path")
            and str(Path(str(session["transcript_path"])).relative_to(output_dir.parent.parent)) in manifest_workspace_paths
            and str(Path(str(session["audio_path"])).relative_to(output_dir.parent.parent)) in manifest_workspace_paths
            and str((output_dir / "session.json").relative_to(output_dir.parent.parent)) in manifest_workspace_paths
            and any(item.get("workspace_path") for item in manifest_outputs if isinstance(item, dict))
            and isinstance(manifest.get("job"), dict),
            manifest,
        )
        assert_ok(
            "tingwu stop HTTP operations are consistent across session, job, manifest, and task",
            tingwu_http_operation_chain_valid(session_http_operations)
            and session_http_operations == tingwu_step_outputs["realtime_capture"].get("tingwu_http_operations")
            and session_http_operations == manifest.get("tingwu_http_operations")
            and session_http_operations == realtime_task_output.get("tingwu_http_operations"),
            {
                "session": session_http_operations,
                "step": tingwu_step_outputs["realtime_capture"].get("tingwu_http_operations"),
                "manifest": manifest.get("tingwu_http_operations"),
                "task": realtime_task_output.get("tingwu_http_operations"),
            },
        )
        assert_ok(
            "tingwu web task and manifest writes leave no temp files",
            not list(output_dir.glob(".*.tmp")) and not list((output_dir.parent.parent / "web_tasks").glob(".*.tmp")),
            {
                "output_dir_temps": [item.name for item in output_dir.glob(".*.tmp")],
                "task_temps": [item.name for item in (output_dir.parent.parent / "web_tasks").glob(".*.tmp")],
            },
        )
        status, payload = request_json(
            args.base_url,
            "/api/meeting/realtime/fetch-minutes",
            token=args.token,
            method="POST",
            payload={"meeting_id": meeting_id, "run_followup": True},
        )
        tingwu_fetch = payload.get("data", {})
        fetch_session = tingwu_fetch.get("session", {}) if isinstance(tingwu_fetch.get("session"), dict) else {}
        fetch_minutes = tingwu_fetch.get("minutes", {}) if isinstance(tingwu_fetch.get("minutes"), dict) else {}
        fetch_tingwu_minutes = fetch_minutes.get("tingwu_minutes") if isinstance(fetch_minutes.get("tingwu_minutes"), dict) else {}
        fetch_followup = tingwu_fetch.get("followup", {}) if isinstance(tingwu_fetch.get("followup"), dict) else {}
        fetch_job = tingwu_fetch.get("job", {}) if isinstance(tingwu_fetch.get("job"), dict) else {}
        fetch_steps = {step.get("name"): step.get("status") for step in fetch_job.get("steps", []) if isinstance(step, dict)}
        fetch_step_outputs = {
            step.get("name"): step.get("output")
            for step in fetch_job.get("steps", [])
            if isinstance(step, dict) and isinstance(step.get("output"), dict)
        }
        fetch_session_http_operations = fetch_session.get("tingwu_http_operations") if isinstance(fetch_session.get("tingwu_http_operations"), list) else []
        fetch_manifest_path = Path(str(tingwu_fetch.get("manifest_path") or ""))
        fetch_manifest = json.loads(fetch_manifest_path.read_text(encoding="utf-8")) if fetch_manifest_path.is_file() else {}
        fetch_manifest_workspace_paths = {
            str(item.get("workspace_path") or "")
            for item in fetch_manifest.get("outputs", [])
            if isinstance(item, dict) and item.get("inside_workspace") is True
        }
        followup_paths = fetch_followup.get("required_output_paths") if isinstance(fetch_followup.get("required_output_paths"), dict) else {}
        required_followup_paths = [str(value) for value in followup_paths.values() if str(value)]
        assert_ok(
            "tingwu explicit fetch-minutes endpoint returns saved outputs",
            status == 200
            and payload.get("ok") is True
            and tingwu_fetch.get("status") == "completed"
            and fetch_session.get("meeting_id") == meeting_id
            and fetch_session.get("minutes_path")
            and Path(str(fetch_session["minutes_path"])).is_file()
            and fetch_minutes.get("path")
            and Path(str(fetch_minutes["path"])).is_file()
            and fetch_tingwu_minutes.get("summary")
            and fetch_manifest_path.is_file()
            and fetch_steps.get("realtime_capture") == "completed"
            and fetch_steps.get("minutes") == "completed"
            and fetch_steps.get("followup") == "completed"
            and fetch_steps.get("reminders") == "completed"
            and fetch_steps.get("projection_confirmation") == "completed",
            payload,
        )
        assert_ok(
            "tingwu meeting job carries compact persisted minutes and follow-up outputs",
            isinstance(fetch_step_outputs.get("minutes"), dict)
            and isinstance(fetch_step_outputs.get("followup"), dict)
            and fetch_step_outputs["minutes"].get("tingwu_minutes_path") == fetch_session.get("minutes_path")
            and fetch_step_outputs["minutes"].get("tingwu_minutes", {}).get("summary") == fetch_tingwu_minutes.get("summary")
            and fetch_step_outputs["minutes"].get("tingwu_minutes", {}).get("structured_summary") is True
            and fetch_step_outputs["minutes"].get("tingwu_minutes", {}).get("summary_source") == fetch_tingwu_minutes.get("summary_source")
            and fetch_step_outputs["minutes"].get("tingwu_minutes", {}).get("summary_source") != "raw_payload"
            and tingwu_http_operation_chain_valid(fetch_step_outputs["realtime_capture"].get("tingwu_http_operations"), require_minutes=True)
            and fetch_step_outputs["minutes"].get("path") == fetch_minutes.get("path")
            and isinstance(fetch_step_outputs["followup"].get("required_output_paths"), dict),
            fetch_step_outputs,
        )
        assert_ok(
            "tingwu manifest carries complete HTTP operation chain after minutes fetch",
            tingwu_http_operation_chain_valid(fetch_manifest.get("tingwu_http_operations"), require_minutes=True),
            fetch_manifest,
        )
        required_non_email_followup_paths = [
            str(value)
            for key, value in followup_paths.items()
            if key != "email_draft" and str(value)
        ] if isinstance(followup_paths, dict) else []
        assert_ok(
            "tingwu follow-up outputs stay in meeting directory and manifest",
            len(required_non_email_followup_paths) >= 4
            and (not str(followup_paths.get("email_draft") or "") or len(required_followup_paths) >= 5)
            and all(Path(path).is_file() and Path(path).resolve().is_relative_to(output_dir.resolve()) for path in required_followup_paths)
            and all(str(Path(path).resolve().relative_to(output_dir.parent.parent.resolve())) in fetch_manifest_workspace_paths for path in required_followup_paths),
            {"paths": followup_paths, "manifest": fetch_manifest},
        )
        persisted_realtime_task_text = ""
        if realtime_task_id:
            task_file = output_dir.parent.parent / "web_tasks" / f"{realtime_task_id}.json"
            persisted_realtime_task_text = task_file.read_text(encoding="utf-8") if task_file.is_file() else ""
            persisted_realtime_task = json.loads(persisted_realtime_task_text) if persisted_realtime_task_text else {}
            task_output = persisted_realtime_task.get("output") if isinstance(persisted_realtime_task.get("output"), dict) else {}
            task_monitor = task_output.get("monitor") if isinstance(task_output.get("monitor"), dict) else {}
            assert_ok(
                "tingwu realtime task monitor includes WebSocket audio frame count",
                int(task_output.get("websocket_audio_frames") or 0) > 0
                and int(task_monitor.get("websocket_audio_frames") or 0) > 0
                and float(task_monitor.get("audio_seconds") or 0) > 0
                and tingwu_http_operation_chain_valid(task_output.get("tingwu_http_operations"), require_minutes=True),
                persisted_realtime_task,
            )
            assert_ok(
                "tingwu fetch-minutes HTTP operations are consistent across session, job, manifest, and task",
                tingwu_http_operation_chain_valid(fetch_session_http_operations, require_minutes=True)
                and fetch_session_http_operations == fetch_step_outputs["realtime_capture"].get("tingwu_http_operations")
                and fetch_session_http_operations == fetch_manifest.get("tingwu_http_operations")
                and fetch_session_http_operations == task_output.get("tingwu_http_operations"),
                {
                    "session": fetch_session_http_operations,
                    "step": fetch_step_outputs["realtime_capture"].get("tingwu_http_operations"),
                    "manifest": fetch_manifest.get("tingwu_http_operations"),
                    "task": task_output.get("tingwu_http_operations"),
                },
            )
        output_text = json.dumps({"stop": tingwu_stop, "fetch": tingwu_fetch, "manifest": manifest}, ensure_ascii=False)
        audit_text_so_far = audit_path.read_text(encoding="utf-8") if audit_path.is_file() else ""
        required_tingwu_audit_actions = (
            "tingwu.meeting_start",
            "tingwu.audio_save",
            "tingwu.meeting_finalize",
            "meeting_realtime_stop",
            "meeting_realtime_fetch_minutes",
            "meeting.transcript_parse",
            "meeting.minutes_generated",
            "meeting.decisions_extract",
            "meeting.action_items_extract",
            "p0.meeting_followup_email_write",
            "daily.reminders_from_actions",
            "meeting.reminders_snapshot",
            "projection.render_markdown",
            "meeting_projection_workspace_copy",
            "p0.meeting_followup_package",
            "meeting_manifest",
        )
        assert_ok(
            "tingwu follow-up lifecycle is auditable",
            all(action in audit_text_so_far for action in required_tingwu_audit_actions),
            audit_text_so_far[-4000:],
        )
        assert_ok(
            "tingwu follow-up lifecycle audit actions are searchable",
            all(audit_action_visible(args.base_url, args.token, action) for action in required_tingwu_audit_actions),
            required_tingwu_audit_actions,
        )
        assert_ok(
            "tingwu realtime Web outputs redact title and participant secrets",
            "web-title-token" not in output_text
            and "web-title-password" not in output_text
            and "web-participant-password" not in output_text
            and "web-title-token" not in persisted_realtime_task_text
            and "web-title-password" not in persisted_realtime_task_text
            and "web-participant-password" not in persisted_realtime_task_text
            and "web-title-token" not in audit_text_so_far
            and "web-title-password" not in audit_text_so_far
            and "web-participant-password" not in audit_text_so_far,
            {
                "output": output_text,
                "task": persisted_realtime_task_text,
                "audit": audit_text_so_far,
            },
        )
        status, payload = request_json(args.base_url, "/api/assistant/notifications", token=args.token)
        notifications = payload.get("data", {}).get("items", [])
        latest_notification = next((item for item in notifications if isinstance(item, dict) and item.get("event") == "meeting_realtime_stopped"), {})
        latest_fetch_notification = next((item for item in notifications if isinstance(item, dict) and item.get("event") == "meeting_ai_minutes_ready"), {})
        notification_payload = latest_notification.get("payload") if isinstance(latest_notification.get("payload"), dict) else {}
        fetch_notification_payload = latest_fetch_notification.get("payload") if isinstance(latest_fetch_notification.get("payload"), dict) else {}
        assert_ok(
            "tingwu assistant notification",
            status == 200
            and payload.get("ok") is True
            and bool(latest_notification),
            payload,
        )
        assert_ok(
            "tingwu assistant notification payload is compact",
            notification_payload.get("meeting_id") == meeting_id
            and "result" not in notification_payload
            and "task_payload" not in json.dumps(notification_payload, ensure_ascii=False),
            latest_notification,
        )
        assert_ok(
            "tingwu explicit fetch-minutes assistant notification",
            bool(latest_fetch_notification)
            and fetch_notification_payload.get("meeting_id") == meeting_id
            and "result" not in fetch_notification_payload
            and "task_payload" not in json.dumps(fetch_notification_payload, ensure_ascii=False),
            latest_fetch_notification,
        )
        notifications_path = workspace_root / ".assistant" / "notifications.json"
        persisted_notifications = json.loads(notifications_path.read_text(encoding="utf-8"))
        persisted_items = persisted_notifications.get("items") if isinstance(persisted_notifications, dict) else []
        assert_ok(
            "assistant meeting notifications persist to workspace",
            notifications_path.is_file()
            and any(isinstance(item, dict) and item.get("id") == latest_notification.get("id") for item in persisted_items)
            and not list(notifications_path.parent.glob(".*.tmp")),
            {
                "path": str(notifications_path),
                "temps": [item.name for item in notifications_path.parent.glob(".*.tmp")],
                "items": persisted_items,
            },
        )
        status, payload = request_json(
            args.base_url,
            f"/api/assistant/notifications?since={urllib.parse.quote(str(latest_notification.get('id') or ''))}",
            token=args.token,
        )
        duplicate_notifications = payload.get("data", {}).get("items", [])
        assert_ok(
            "assistant notifications since id do not repeat meeting notice",
            status == 200
            and payload.get("ok") is True
            and payload.get("data", {}).get("since_found") is True
            and not any(
                isinstance(item, dict) and item.get("id") == latest_notification.get("id")
                for item in duplicate_notifications
            ),
            payload,
        )
        status, payload = request_json(
            args.base_url,
            "/api/assistant/notifications?since=ntf_missing_cursor",
            token=args.token,
        )
        recovered_notifications = payload.get("data", {}).get("items", [])
        assert_ok(
            "assistant notifications recover from expired since id",
            status == 200
            and payload.get("ok") is True
            and payload.get("data", {}).get("since_found") is False
            and any(
                isinstance(item, dict) and item.get("id") == latest_notification.get("id")
                for item in recovered_notifications
            ),
            payload,
        )
    elif args.expect_tingwu_unavailable:
        status, payload = request_json(
            args.base_url,
            "/api/meeting/realtime/start",
            token=args.token,
            method="POST",
            payload={"title": "smoke_tingwu_mic_unavailable", "participants": ["Alice"]},
        )
        error = payload.get("error", {}) if isinstance(payload.get("error"), dict) else {}
        details = error.get("details", {}) if isinstance(error.get("details"), dict) else {}
        error_provider = details.get("provider", {}) if isinstance(details.get("provider"), dict) else {}
        assert_ok(
            "tingwu realtime requires microphone",
            provider.get("status") == "unavailable"
            and status == 409
            and payload.get("ok") is False
            and error.get("code") == "meeting_provider_unavailable"
            and error_provider.get("status") == "unavailable"
            and error_provider.get("mic_status") != "available",
            payload,
        )
        status, payload = request_json(args.base_url, "/api/assistant/notifications", token=args.token)
        start_failed_notifications = payload.get("data", {}).get("items", [])
        assert_ok(
            "tingwu start failure notifies assistant when microphone is unavailable",
            status == 200
            and payload.get("ok") is True
            and any(
                isinstance(item, dict)
                and item.get("event") == "meeting_realtime_start_failed"
                and item.get("status") == "failed"
                and "mic_status" in str(item.get("attachment") or "")
                for item in start_failed_notifications
            ),
            payload,
        )
        notifications_path = workspace_root / ".assistant" / "notifications.json"
        persisted_notifications = json.loads(notifications_path.read_text(encoding="utf-8"))
        assert_ok(
            "assistant start failure notification persists for unavailable microphone",
            notifications_path.is_file()
            and any(
                isinstance(item, dict) and item.get("event") == "meeting_realtime_start_failed"
                for item in persisted_notifications.get("items", [])
            )
            and not list(notifications_path.parent.glob(".*.tmp")),
            {"path": str(notifications_path), "temps": [item.name for item in notifications_path.parent.glob(".*.tmp")]},
        )
    else:
        secret_start_title = "smoke_tingwu_requires_config token=secret-start-token password=hunter2"
        status, payload = request_json(
            args.base_url,
            "/api/meeting/realtime/start",
            token=args.token,
            method="POST",
            payload={"title": secret_start_title, "participants": ["Alice"]},
        )
        assert_ok("tingwu realtime requires config", status == 409 and payload.get("ok") is False, payload)
        status, payload = request_json(args.base_url, "/api/assistant/notifications", token=args.token)
        start_failed_notifications = payload.get("data", {}).get("items", [])
        start_failed_text = json.dumps(start_failed_notifications, ensure_ascii=False)
        assert_ok(
            "tingwu start failure notifies assistant when config is missing",
            status == 200
            and payload.get("ok") is True
            and any(
                isinstance(item, dict)
                and item.get("event") == "meeting_realtime_start_failed"
                and item.get("status") == "failed"
                and "needs_config" in str(item.get("attachment") or "")
                for item in start_failed_notifications
            )
            and "secret-start-token" not in start_failed_text
            and "hunter2" not in start_failed_text,
            payload,
        )
        notifications_path = workspace_root / ".assistant" / "notifications.json"
        persisted_notifications = json.loads(notifications_path.read_text(encoding="utf-8"))
        persisted_notification_text = json.dumps(persisted_notifications, ensure_ascii=False)
        assert_ok(
            "assistant start failure notification persists for missing config",
            notifications_path.is_file()
            and any(
                isinstance(item, dict) and item.get("event") == "meeting_realtime_start_failed"
                for item in persisted_notifications.get("items", [])
            )
            and not list(notifications_path.parent.glob(".*.tmp")),
            {"path": str(notifications_path), "temps": [item.name for item in notifications_path.parent.glob(".*.tmp")]},
        )
        assert_ok(
            "assistant persisted notifications redact secrets",
            "secret-start-token" not in persisted_notification_text and "hunter2" not in persisted_notification_text,
            persisted_notifications,
        )

    status, payload = request_json(
        args.base_url,
        "/api/meeting/import-text",
        token=args.token,
        method="POST",
        payload={
            "title": "smoke_meeting_text_import",
            "text": "Alice: 决定: 使用本地会议素材进入 shared_inbox\nBob: 待办: 生成会议纪要",
            "participants": ["Alice", "Bob"],
        },
    )
    assert_ok("meeting text import", status == 200 and payload.get("ok") is True and payload.get("data", {}).get("job"), payload)

    status, payload = request_json(
        args.base_url,
        "/api/hardware/test",
        token=args.token,
        method="POST",
        payload={"test": "projection"},
    )
    assert_ok("hardware projection test", status == 200 and payload.get("ok") is True, payload)

    status, payload = request_json(args.base_url, "/api/audit/search?status=blocked&limit=1000", token=args.token)
    blocked = payload.get("data", {}).get("items", [])
    assert_ok("audit blocked visible", status == 200 and len(blocked) >= 1, payload)
    assert_ok(
        "full_control gate is auditable",
        audit_action_visible(args.base_url, args.token, "full_control_request")
        and audit_action_visible(args.base_url, args.token, "full_control_confirm")
        and audit_action_visible(args.base_url, args.token, "full_control_cancel"),
        "full_control audit actions were not searchable",
    )

    print("smoke_web_api complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
