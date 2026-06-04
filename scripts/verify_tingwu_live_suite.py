#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "lelamp_runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

from lelamp.office_agent.config import tingwu_credential_kind, tingwu_credential_next_actions  # noqa: E402

PYTHON = RUNTIME_ROOT / ".venv" / "bin" / "python"
DEFAULT_SPOKEN_PHRASE = "乐灯听悟验收测试"
TINGWU_CREDENTIAL_LINKS = [
    {
        "label": "Bailian China console",
        "url": "https://bailian.console.aliyun.com/",
    },
    {
        "label": "API Key help",
        "url": "https://help.aliyun.com/zh/model-studio/get-api-key/",
    },
    {
        "label": "API Key and App ID help",
        "url": "https://help.aliyun.com/zh/model-studio/obtain-api-key-app-id-and-workspace-id/",
    },
    {
        "label": "Tingwu realtime access docs",
        "url": "https://help.aliyun.com/zh/tingwu/interface-and-implementation",
    },
]
ENV_FILE_KEYS = {
    "DASHSCOPE_API_KEY",
    "TINGWU_API_KEY",
    "TINGWU_APP_ID",
    "TINGWU_MEETING_APP_ID",
    "OPENCLAW_MIC_DEVICE",
    "TINGWU_HTTP_URL",
    "TINGWU_WS_URL",
}

GOAL_REQUIREMENT_DEFINITIONS: tuple[dict[str, object], ...] = (
    {
        "id": "import_transcript",
        "requirement": "Meeting 模块支持导入 transcript，并进入本地会议闭环。",
        "checks": (("web_api", "step_import_transcript_completed"),),
        "evidence": ("web_api.steps.import_transcript", "web_api.followup_output_paths.transcript_export"),
    },
    {
        "id": "pi_usb_microphone_capture",
        "requirement": "树莓派 / USB 麦克风可采集真实 PCM 音频并保存。",
        "checks": (
            ("preflight", "real_microphone_device"),
            ("preflight", "microphone_capture_signal"),
            ("direct_provider", "real_microphone_device"),
            ("direct_provider", "audio_saved"),
            ("direct_provider", "audio_has_signal"),
            ("web_api", "real_microphone_device"),
            ("web_api", "audio_saved"),
            ("web_api", "audio_has_signal"),
        ),
        "evidence": ("preflight.capture_probe", "direct_provider.audio_path", "web_api.audio_path"),
    },
    {
        "id": "tingwu_realtime_create_task",
        "requirement": "LeLamp Meeting Service 使用通义听悟 CreateTask 创建 realtime meeting。",
        "checks": (
            ("direct_provider", "provider_configured"),
            ("direct_provider", "official_tingwu_endpoint"),
            ("direct_provider", "tingwu_http_operations_visible"),
            ("web_api", "provider_available"),
            ("web_api", "live_mode_not_mock"),
            ("web_api", "official_tingwu_endpoint"),
            ("web_api", "tingwu_http_operations_visible"),
        ),
        "evidence": ("direct_provider.tingwu_http_operations", "web_api.tingwu_http_operations"),
    },
    {
        "id": "websocket_pcm_streaming",
        "requirement": "WebSocket 推送 PCM 音频流，并能看到音频帧指标。",
        "checks": (
            ("direct_provider", "websocket_stream_started"),
            ("direct_provider", "websocket_audio_frames_sent"),
            ("web_api", "websocket_stream_started"),
            ("web_api", "websocket_audio_frames_sent"),
            ("web_api", "task_monitor_metrics_visible"),
        ),
        "evidence": ("direct_provider.provider_events", "web_api.task_monitor", "web_api.provider_events"),
    },
    {
        "id": "realtime_transcript",
        "requirement": "实时 transcript 回传、保存，并包含现场口播验收短语。",
        "checks": (
            ("direct_provider", "transcript_saved"),
            ("direct_provider", "realtime_transcript_non_empty"),
            ("direct_provider", "spoken_phrase_detected"),
            ("web_api", "transcript_saved"),
            ("web_api", "realtime_transcript_non_empty"),
            ("web_api", "spoken_phrase_detected"),
        ),
        "evidence": ("direct_provider.transcript_path", "web_api.transcript_path", "required_spoken_phrase"),
    },
    {
        "id": "stop_before_ai_minutes",
        "requirement": "会议 stop 后才允许显式拉取通义听悟 AI 纪要。",
        "checks": (
            ("direct_provider", "stopped_before_ai_minutes"),
            ("web_api", "stopped_before_ai_minutes"),
            ("web_api", "status_completed_after_fetch"),
        ),
        "evidence": ("direct_provider.stop_status_before_fetch", "web_api.active_fetch_minutes_probe", "web_api.status_after_fetch"),
    },
    {
        "id": "tingwu_ai_minutes",
        "requirement": "创建会议纪要分析任务，并通过 GetTask 获取结构化智能纪要。",
        "checks": (
            ("direct_provider", "ai_minutes_saved"),
            ("direct_provider", "ai_minutes_task_metadata"),
            ("direct_provider", "tingwu_minutes_structured"),
            ("web_api", "tingwu_minutes_saved"),
            ("web_api", "ai_minutes_task_metadata"),
            ("web_api", "tingwu_minutes_structured"),
        ),
        "evidence": ("direct_provider.minutes_path", "web_api.tingwu_minutes_path", "web_api.ai_minutes_task_id"),
    },
    {
        "id": "openclaw_postprocessing",
        "requirement": "OpenClaw 后处理生成纪要、decisions、action items、follow-up、reminders、projection confirmation。",
        "checks": (
            ("web_api", "openclaw_minutes_saved"),
            ("web_api", "followup_outputs_saved"),
            ("web_api", "manifest_indexes_followup_outputs"),
            ("web_api", "step_minutes_completed"),
            ("web_api", "step_decisions_waiting_confirmation"),
            ("web_api", "step_action_items_completed"),
            ("web_api", "step_followup_completed"),
            ("web_api", "step_reminders_completed"),
            ("web_api", "step_projection_confirmation_completed"),
            ("web_api", "all_required_steps"),
        ),
        "evidence": ("web_api.openclaw_minutes_path", "web_api.followup_output_paths", "web_api.steps"),
    },
    {
        "id": "workspace_outputs",
        "requirement": "所有会议产物保存到 workspace/meetings/{meeting_id}，并避免临时文件残留。",
        "checks": (
            ("direct_provider", "workspace_boundary"),
            ("direct_provider", "no_temp_files"),
            ("web_api", "manifest_saved"),
            ("web_api", "followup_outputs_saved"),
            ("web_api", "manifest_indexes_followup_outputs"),
            ("web_api", "no_temp_files"),
        ),
        "evidence": ("direct_provider.output_dir", "web_api.output_dir", "web_api.manifest_path"),
    },
    {
        "id": "meeting_ui_task_monitor",
        "requirement": "Meeting UI 展示结果，并通过任务监控恢复 realtime/后处理状态。",
        "checks": (
            ("web_api", "task_monitor_listed"),
            ("web_api", "task_monitor_metrics_visible"),
            ("web_api", "meeting_jobs_restore_outputs"),
            ("web_api", "web_restart_recovery_outputs"),
        ),
        "evidence": ("web_api.task_monitor", "web_api.meeting_jobs_restore", "web_api.web_restart_recovery"),
    },
    {
        "id": "assistant_panel_notifications",
        "requirement": "AssistantPanel 主动通知会议停止和 AI 纪要拉取结果。",
        "checks": (
            ("web_api", "assistant_stop_notification"),
            ("web_api", "assistant_fetch_notification"),
        ),
        "evidence": ("web_api.notifications", "web_api.notifications_path"),
    },
    {
        "id": "audit_and_safety_boundaries",
        "requirement": "关键动作写 audit log，并保持 sandbox/audit_only/allowed-roots/full_control 安全边界。",
        "checks": (
            ("direct_provider", "audit_lifecycle_visible"),
            ("direct_provider", "api_key_absent"),
            ("direct_provider", "app_id_absent_from_audit"),
            ("web_api", "sandbox_visible"),
            ("web_api", "audit_only_visible"),
            ("web_api", "workspace_allowed"),
            ("web_api", "allowed_roots_block_enforced"),
            ("web_api", "full_control_gate_enforced"),
            ("web_api", "full_control_audit_visible"),
            ("web_api", "audit_lifecycle_visible"),
            ("web_api", "api_key_absent"),
            ("web_api", "app_id_absent_from_audit"),
        ),
        "evidence": ("direct_provider.audit_log_path", "web_api.audit_log_path", "web_api.allowed_roots_probe", "web_api.full_control_gate"),
    },
)


def require_live_env() -> None:
    api_key = os.getenv("TINGWU_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    app_id = os.getenv("TINGWU_APP_ID") or os.getenv("TINGWU_MEETING_APP_ID")
    missing: list[str] = []
    if not is_valid_credential(api_key):
        missing.append("TINGWU_API_KEY or DASHSCOPE_API_KEY")
    if not is_valid_credential(app_id, role="app_id"):
        missing.append("TINGWU_APP_ID or TINGWU_MEETING_APP_ID")
    if missing:
        diagnostics = {
            "api_key_kind": tingwu_credential_kind(api_key),
            "app_id_kind": tingwu_credential_kind(app_id, role="app_id"),
        }
        raise SystemExit(
            "Missing live Tingwu credentials: "
            + ", ".join(missing)
            + f"; api_key_kind={diagnostics['api_key_kind']}; app_id_kind={diagnostics['app_id_kind']}"
        )


def is_placeholder_credential(value: str | None) -> bool:
    return not is_valid_credential(value)


def is_valid_credential(value: str | None, *, role: str = "") -> bool:
    return tingwu_credential_kind(value, role=role) == "configured"


def live_credential_configured(*keys: str) -> bool:
    role = "app_id" if any("APP_ID" in key for key in keys) else ""
    return any(is_valid_credential(os.getenv(key), role=role) for key in keys)


def scrub_placeholder_credentials(env: dict[str, str]) -> dict[str, str]:
    scrubbed = dict(env)
    for key in ("TINGWU_API_KEY", "DASHSCOPE_API_KEY"):
        if not is_valid_credential(scrubbed.get(key)):
            scrubbed.pop(key, None)
    for key in ("TINGWU_APP_ID", "TINGWU_MEETING_APP_ID"):
        if not is_valid_credential(scrubbed.get(key), role="app_id"):
            scrubbed.pop(key, None)
    return scrubbed


def default_env_file() -> Path:
    return RUNTIME_ROOT / ".env.tingwu.local"


def load_env_file(path_value: str) -> dict[str, str]:
    if not path_value:
        return {}
    path = Path(path_value).expanduser()
    checked_paths: list[Path] = []
    if not path.is_absolute():
        cwd_relative = path.resolve()
        runtime_relative = (RUNTIME_ROOT / path).resolve()
        checked_paths = [cwd_relative]
        if not cwd_relative.is_relative_to(RUNTIME_ROOT):
            checked_paths.append(runtime_relative)
        checked_paths = list(dict.fromkeys(checked_paths))
    else:
        checked_paths = [path.resolve()]
    path = next((candidate for candidate in checked_paths if candidate.is_file()), checked_paths[0])
    if not path.is_file():
        locations = ", ".join(str(candidate) for candidate in checked_paths)
        raise SystemExit(f"Tingwu env file not found. Checked: {locations}")
    loaded: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise SystemExit(f"Invalid Tingwu env file line {line_number}: expected KEY=value")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key not in ENV_FILE_KEYS:
            raise SystemExit(f"Unsupported Tingwu env file key on line {line_number}: {key}")
        value = raw_value.strip()
        if value[:1] in {"'", '"'}:
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise SystemExit(f"Invalid Tingwu env file quoting on line {line_number}: missing closing {quote}")
            value = value[1:-1]
        loaded[key] = value
    return loaded


def apply_env_file(path_value: str) -> dict[str, str]:
    loaded = load_env_file(path_value)
    for key, value in loaded.items():
        os.environ[key] = value
    return loaded


def run(name: str, command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print(f"== {name}")
    subprocess.run(command, cwd=cwd, env=env, check=True)
    print(f"ok - {name}")


def run_stage(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    details: dict[str, object] = {
        "stage": name,
        "command": command,
        "cwd": str(cwd),
        **(metadata or {}),
    }
    started_at = time.monotonic()
    try:
        run(name, command, cwd=cwd, env=env)
        return {**details, "status": "ok", "duration_seconds": round(time.monotonic() - started_at, 3)}
    except subprocess.CalledProcessError as exc:
        return {
            **details,
            "status": "failed",
            "returncode": exc.returncode,
            "command": exc.cmd,
            "duration_seconds": round(time.monotonic() - started_at, 3),
        }
    except Exception as exc:
        return {**details, "status": "failed", "error": str(exc), "duration_seconds": round(time.monotonic() - started_at, 3)}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_console(base_url: str, token: str, *, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(f"{base_url}/api/security", headers={"Authorization": f"Bearer {token}"})
    last_error: object = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=1.5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"Web console did not become ready at {base_url}: {last_error}")


def request_json(base_url: str, path: str, *, token: str, timeout: int = 30) -> dict[str, object]:
    request = urllib.request.Request(
        urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        envelope = json.loads(response.read().decode("utf-8"))
    if not isinstance(envelope, dict) or envelope.get("ok") is not True or not isinstance(envelope.get("data"), dict):
        raise RuntimeError(f"GET {path} returned invalid envelope: {envelope}")
    return envelope["data"]


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if hasattr(os, "killpg"):
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=6)
    except subprocess.TimeoutExpired:
        if hasattr(os, "killpg"):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=6)


def start_web_console(
    *,
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> subprocess.Popen[bytes]:
    log_stream = log_path.open("ab")
    try:
        return subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
    finally:
        log_stream.close()


def verify_web_restart_recovery(base_url: str, token: str, evidence: dict[str, object]) -> dict[str, object]:
    meeting_id = str(evidence.get("meeting_id") or "")
    if not meeting_id:
        raise RuntimeError("web restart recovery requires web evidence meeting_id")
    jobs_payload = request_json(base_url, "/api/meeting/jobs", token=token)
    jobs = jobs_payload.get("items") if isinstance(jobs_payload.get("items"), list) else []
    restored_job = next((item for item in jobs if isinstance(item, dict) and item.get("meeting_id") == meeting_id), {})
    restored_steps = restored_job.get("steps") if isinstance(restored_job.get("steps"), list) else []
    restored_outputs = {
        step.get("name"): step.get("output")
        for step in restored_steps
        if isinstance(step, dict) and isinstance(step.get("output"), dict)
    }
    restored_minutes = restored_outputs.get("minutes") if isinstance(restored_outputs.get("minutes"), dict) else {}
    restored_realtime = restored_outputs.get("realtime_capture") if isinstance(restored_outputs.get("realtime_capture"), dict) else {}
    restored_followup = restored_outputs.get("followup") if isinstance(restored_outputs.get("followup"), dict) else {}
    restored_tingwu_minutes = restored_minutes.get("tingwu_minutes") if isinstance(restored_minutes.get("tingwu_minutes"), dict) else {}

    tasks_payload = request_json(base_url, "/api/tasks?limit=50", token=token)
    tasks = tasks_payload.get("items") if isinstance(tasks_payload.get("items"), list) else []
    realtime_task = next(
        (
            item for item in tasks
            if isinstance(item, dict)
            and isinstance(item.get("input"), dict)
            and item["input"].get("step") == "realtime_capture"
            and item["input"].get("meeting_id") == meeting_id
        ),
        {},
    )
    realtime_task_output = realtime_task.get("output") if isinstance(realtime_task.get("output"), dict) else {}
    realtime_monitor = realtime_task_output.get("monitor") if isinstance(realtime_task_output.get("monitor"), dict) else {}

    notifications_payload = request_json(base_url, "/api/assistant/notifications", token=token)
    notifications = notifications_payload.get("items") if isinstance(notifications_payload.get("items"), list) else []
    restored = {
        "job_id": str(restored_job.get("job_id") or ""),
        "step_names": [
            str(step.get("name") or "")
            for step in restored_steps
            if isinstance(step, dict)
        ],
        "tingwu_minutes_path": str(restored_minutes.get("tingwu_minutes_path") or ""),
        "openclaw_minutes_path": str(restored_minutes.get("path") or ""),
        "tingwu_minutes_summary": str(restored_tingwu_minutes.get("summary") or ""),
        "tingwu_minutes_summary_source": str(restored_tingwu_minutes.get("summary_source") or ""),
        "tingwu_minutes_structured": restored_tingwu_minutes.get("structured_summary") is True,
        "websocket_audio_frames": int(restored_realtime.get("websocket_audio_frames") or 0),
        "followup_output_keys": sorted((restored_followup.get("required_output_paths") if isinstance(restored_followup.get("required_output_paths"), dict) else {}).keys()),
        "events_compacted": "events" not in restored_realtime,
        "task_monitor_frames": int(realtime_monitor.get("websocket_audio_frames") or 0),
        "task_monitor_audio_seconds": float(realtime_monitor.get("audio_seconds") or 0),
        "assistant_notifications": [
            str(item.get("event") or "")
            for item in notifications
            if isinstance(item, dict) and item.get("payload", {}).get("meeting_id") == meeting_id
        ],
    }
    required_followup_keys = {"openclaw_minutes", "transcript_export", "email_draft", "reminders", "projection_confirmation", "decisions", "action_items"}
    ok = (
        bool(restored_job)
        and restored["tingwu_minutes_path"] == str(evidence.get("tingwu_minutes_path") or "")
        and restored["openclaw_minutes_path"] == str(evidence.get("openclaw_minutes_path") or "")
        and restored["tingwu_minutes_summary"] == str(evidence.get("tingwu_minutes_summary") or "")
        and restored["tingwu_minutes_summary_source"] == str(evidence.get("tingwu_minutes_summary_source") or "")
        and restored["tingwu_minutes_summary_source"] != "raw_payload"
        and restored["tingwu_minutes_structured"] is True
        and restored["websocket_audio_frames"] > 0
        and set(restored["followup_output_keys"]) >= required_followup_keys
        and restored["events_compacted"] is True
        and restored["task_monitor_frames"] > 0
        and restored["task_monitor_audio_seconds"] > 0
        and "meeting_ai_minutes_ready" in restored["assistant_notifications"]
    )
    if not ok:
        raise RuntimeError(f"web restart recovery failed: {json.dumps(restored, ensure_ascii=False)}")
    return restored


def merge_web_restart_failure(evidence_path: Path, evidence: dict[str, object], restart_details: dict[str, object]) -> dict[str, object]:
    checks = evidence.get("checks") if isinstance(evidence.get("checks"), dict) else {}
    merged = {
        **evidence,
        "status": "failed",
        "checks": {**checks, "web_restart_recovery_outputs": False},
        "web_restart_recovery_error": restart_details,
    }
    try:
        evidence_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return merged


def evidence_ok(value: object) -> bool:
    if not isinstance(value, dict) or value.get("status") != "ok":
        return False
    checks = value.get("checks")
    if isinstance(checks, dict):
        return all(item is True for item in checks.values())
    return True


def read_evidence_file(path_value: str) -> dict[str, object]:
    path = Path(path_value)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"status": "invalid_json", "path": path_value}
        return payload if isinstance(payload, dict) else {"status": "invalid_json", "path": path_value}
    return {"status": "missing", "path": path_value}


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_stage_result(result: dict[str, object], evidence: dict[str, object]) -> dict[str, object]:
    if result.get("status") == "ok":
        return evidence
    merged = dict(evidence)
    merged["status"] = "failed"
    merged["stage_error"] = result
    return merged


def acceptance_blockers(
    *,
    stage_status: dict[str, str],
    evidence_files: dict[str, str],
    stage_runs: dict[str, object],
    stage_evidence: dict[str, object],
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    for stage_name in ("preflight", "direct_provider", "web_api"):
        status = stage_status.get(stage_name)
        if status == "ok":
            continue
        run = stage_runs.get(stage_name) if isinstance(stage_runs.get(stage_name), dict) else {}
        evidence = stage_evidence.get(stage_name) if isinstance(stage_evidence.get(stage_name), dict) else {}
        checks = evidence.get("checks") if isinstance(evidence.get("checks"), dict) else {}
        blockers.append(
            {
                "stage": stage_name,
                "status": status or "missing",
                "error": str(evidence.get("error") or run.get("error") or ""),
                "failed_checks": sorted(str(key) for key, value in checks.items() if value is not True),
                "evidence_json": str(evidence_files.get(stage_name) or run.get("evidence_json") or ""),
                "command": run.get("command") if isinstance(run.get("command"), list) else [],
                "cwd": str(run.get("cwd") or ""),
            }
        )
    return blockers


def stage_check(stage_evidence: dict[str, object], stage_name: str, check_name: str) -> bool:
    stage = stage_evidence.get(stage_name)
    if not isinstance(stage, dict):
        return False
    checks = stage.get("checks")
    return isinstance(checks, dict) and checks.get(check_name) is True


def goal_requirement_report(
    *,
    stage_status: dict[str, str],
    stage_evidence: dict[str, object],
    blockers: list[dict[str, object]],
) -> dict[str, object]:
    requirements: list[dict[str, object]] = []
    blocker_stages = {
        str(item.get("stage") or "")
        for item in blockers
        if isinstance(item, dict)
    }
    for definition in GOAL_REQUIREMENT_DEFINITIONS:
        required_checks = [
            (str(stage), str(check))
            for stage, check in definition["checks"]  # type: ignore[index]
        ]
        satisfied = [
            f"{stage}.{check}"
            for stage, check in required_checks
            if stage_check(stage_evidence, stage, check)
        ]
        missing = [
            f"{stage}.{check}"
            for stage, check in required_checks
            if not stage_check(stage_evidence, stage, check)
        ]
        required_stages = {stage for stage, _check in required_checks}
        skipped = sorted(stage for stage in required_stages if stage_status.get(stage) == "skipped")
        blocked = sorted(stage for stage in required_stages if stage in blocker_stages)
        if not missing and not skipped and all(stage_status.get(stage) == "ok" for stage in required_stages):
            status = "proven"
        elif satisfied:
            status = "partial"
        else:
            status = "blocked" if blocked else "missing"
        requirements.append(
            {
                "id": definition["id"],
                "requirement": definition["requirement"],
                "status": status,
                "required_checks": [f"{stage}.{check}" for stage, check in required_checks],
                "satisfied_checks": satisfied,
                "missing_checks": missing,
                "evidence": list(definition["evidence"]),  # type: ignore[index]
                "blocked_stages": blocked,
                "skipped_stages": skipped,
            }
        )

    preflight = stage_evidence.get("preflight") if isinstance(stage_evidence.get("preflight"), dict) else {}
    preflight_checks = preflight.get("checks") if isinstance(preflight.get("checks"), dict) else {}
    preflight_credential_diagnostics = (
        preflight.get("credential_diagnostics")
        if isinstance(preflight.get("credential_diagnostics"), dict)
        else {}
    )
    readiness = {
        "credentials_configured": preflight_checks.get("tingwu_api_key_configured") is True
        and preflight_checks.get("tingwu_app_id_configured") is True,
        "credential_diagnostics": preflight_credential_diagnostics,
        "dashscope_tingwu_import": preflight_checks.get("dashscope_tingwu_import") is True,
        "official_tingwu_endpoint": preflight_checks.get("official_tingwu_endpoint") is True,
        "microphone_ready": preflight_checks.get("real_microphone_device") is True
        and preflight_checks.get("microphone_capture_device_matches") is True
        and preflight_checks.get("microphone_capture_signal") is True,
        "microphone_capture_device_matches": preflight_checks.get("microphone_capture_device_matches") is True,
        "workspace_writable": preflight_checks.get("workspace_writable") is True,
        "audit_writable": preflight_checks.get("audit_writable") is True,
        "selected_mic_device": str(preflight.get("selected_mic_device") or ""),
    }
    return {
        "goal_requirements": requirements,
        "goal_readiness": readiness,
        "goal_completion_ready": all(item["status"] == "proven" for item in requirements),
    }


def live_acceptance_next_actions(
    *,
    args: argparse.Namespace,
    blockers: list[dict[str, object]],
    readiness: dict[str, object],
    evidence_root: Path,
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    python = str(PYTHON.resolve())
    runtime_cwd = str(RUNTIME_ROOT.resolve())
    preflight_command = [
        python,
        str(REPO_ROOT / "scripts" / "preflight_tingwu_live.py"),
        "--capture-seconds",
        str(max(1, args.preflight_capture_seconds)),
    ]
    acceptance_command = [
        python,
        str(REPO_ROOT / "scripts" / "verify_tingwu_live_suite.py"),
        "--env-file",
        ".env.tingwu.local",
        "--seconds",
        str(args.seconds),
        "--preflight-capture-seconds",
        str(max(1, args.preflight_capture_seconds)),
        "--spoken-phrase",
        args.spoken_phrase,
        "--evidence-dir",
        str(evidence_root),
    ]
    audit_command = [
        python,
        str(REPO_ROOT / "scripts" / "audit_tingwu_live_evidence.py"),
        str(evidence_root / "summary.json"),
        "--check-files",
    ]
    if readiness.get("credentials_configured") is not True:
        credential_diagnostics = readiness.get("credential_diagnostics") if isinstance(readiness.get("credential_diagnostics"), dict) else {}
        credential_guidance = tingwu_credential_next_actions(
            str(credential_diagnostics.get("api_key_kind") or ""),
            str(credential_diagnostics.get("app_id_kind") or ""),
        )
        actions.append(
            {
                "id": "configure_tingwu_credentials",
                "status": "required",
                "message": " ".join(credential_guidance) or "Set TINGWU_API_KEY or DASHSCOPE_API_KEY, and TINGWU_APP_ID or TINGWU_MEETING_APP_ID in the shell that runs live verification.",
                "credential_diagnostics": credential_diagnostics,
                "env": {
                    "ENV_FILE": ".env.tingwu.local",
                },
                "links": TINGWU_CREDENTIAL_LINKS,
                "cwd": runtime_cwd,
                "command": acceptance_command,
                "audit_command": audit_command,
            }
        )
    if readiness.get("microphone_ready") is not True:
        actions.append(
            {
                "id": "fix_microphone_preflight",
                "status": "required",
                "message": "Run the preflight command and confirm a concrete ALSA capture device opens with non-silent PCM signal.",
                "env": {"OPENCLAW_MIC_DEVICE": "auto"},
                "cwd": runtime_cwd,
                "command": preflight_command,
            }
        )
    if blockers:
        actions.append(
            {
                "id": "run_full_live_acceptance",
                "status": "pending",
                "message": f"After prerequisites are ready, speak {args.spoken_phrase!r} during both live capture stages and run the full live suite.",
                "env": {
                    "ENV_FILE": ".env.tingwu.local",
                },
                "links": TINGWU_CREDENTIAL_LINKS,
                "cwd": runtime_cwd,
                "command": acceptance_command,
                "audit_command": audit_command,
            }
        )
    if not actions:
        actions.append(
            {
                "id": "acceptance_complete",
                "status": "completed",
                "message": "All live Tingwu acceptance stages passed; archive summary.json and the per-stage evidence files.",
            }
        )
    return actions


def env_credential_diagnostics() -> dict[str, str]:
    api_key = os.getenv("TINGWU_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
    app_id = os.getenv("TINGWU_APP_ID") or os.getenv("TINGWU_MEETING_APP_ID") or ""
    return {
        "api_key_kind": tingwu_credential_kind(api_key),
        "app_id_kind": tingwu_credential_kind(app_id, role="app_id"),
    }


def write_summary(
    *,
    args: argparse.Namespace,
    suite_root: Path,
    audit_root: Path,
    evidence_root: Path,
    evidence_files: dict[str, str],
    stage_runs: dict[str, object],
    stage_evidence: dict[str, object],
) -> Path:
    expected_stages = {
        "preflight": not args.skip_preflight,
        "direct_provider": not args.skip_direct,
        "web_api": not args.skip_web,
    }
    acceptance_complete = all(expected_stages.values()) and all(
        evidence_ok(stage_evidence.get(name))
        for name in expected_stages
    )
    stage_status = {
        name: "skipped" if not expected else ("ok" if evidence_ok(stage_evidence.get(name)) else "failed")
        for name, expected in expected_stages.items()
    }
    blockers = acceptance_blockers(
        stage_status=stage_status,
        evidence_files=evidence_files,
        stage_runs=stage_runs,
        stage_evidence=stage_evidence,
    )
    goal_report = goal_requirement_report(
        stage_status=stage_status,
        stage_evidence=stage_evidence,
        blockers=blockers,
    )
    goal_readiness = goal_report.get("goal_readiness") if isinstance(goal_report.get("goal_readiness"), dict) else {}
    if not goal_readiness.get("credential_diagnostics"):
        goal_readiness["credential_diagnostics"] = env_credential_diagnostics()
    summary = {
        "status": "ok" if acceptance_complete else "partial",
        "acceptance_complete": acceptance_complete,
        "stage_status": stage_status,
        "acceptance_blockers": blockers,
        "next_actions": live_acceptance_next_actions(
            args=args,
            blockers=blockers,
            readiness=goal_readiness,
            evidence_root=evidence_root,
        ),
        "suite_root": str(suite_root),
        "audit_root": str(audit_root),
        "evidence_dir": str(evidence_root),
        "seconds": args.seconds,
        "preflight_capture_seconds": max(1, args.preflight_capture_seconds),
        "required_spoken_phrase": args.spoken_phrase,
        "skipped": {
            "preflight": args.skip_preflight,
            "direct_provider": args.skip_direct,
            "web_api": args.skip_web,
        },
        "evidence_files": evidence_files,
        "stage_runs": stage_runs,
        **goal_report,
    }
    summary.update(stage_evidence)
    summary_path = evidence_root / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full live Tingwu verification: preflight, direct provider, and Web API.")
    parser.add_argument("--seconds", type=int, default=12)
    parser.add_argument("--token", default="test-console")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--audit-log", default="")
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--skip-direct", action="store_true")
    parser.add_argument("--skip-web", action="store_true")
    parser.add_argument("--evidence-dir", default="", help="Directory for machine-readable acceptance evidence JSON files.")
    parser.add_argument("--spoken-phrase", default=DEFAULT_SPOKEN_PHRASE, help="Required phrase to speak during both live capture stages.")
    parser.add_argument("--preflight-capture-seconds", type=int, default=3, help="Seconds of microphone audio to sample during preflight.")
    parser.add_argument(
        "--env-file",
        default="",
        help="Local Tingwu env file to load before verification, for example .env.tingwu.local. Only known KEY=value entries are accepted.",
    )
    args = parser.parse_args()

    if not PYTHON.is_file():
        raise SystemExit(f"Runtime Python not found: {PYTHON}")

    suite_root = Path(args.workspace).expanduser().resolve() if args.workspace else Path(tempfile.mkdtemp(prefix="lelamp-tingwu-live-suite-"))
    suite_root.mkdir(parents=True, exist_ok=True)
    preflight_workspace = suite_root / "preflight_workspace"
    direct_workspace = suite_root / "direct_workspace"
    web_workspace = suite_root / "web_workspace"
    audit_root = Path(args.audit_log).expanduser().resolve() if args.audit_log else suite_root / "audit"
    audit_root.mkdir(parents=True, exist_ok=True)
    evidence_root = Path(args.evidence_dir).expanduser().resolve() if args.evidence_dir else suite_root / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)

    env_file_error = ""
    try:
        apply_env_file(args.env_file)
    except SystemExit as exc:
        env_file_error = str(exc)

    env = {**os.environ, "TINGWU_MOCK": "0", "OPENCLAW_MIC_DEVICE": os.getenv("OPENCLAW_MIC_DEVICE", "auto")}
    evidence_files: dict[str, str] = {}
    stage_runs: dict[str, object] = {}
    stage_evidence: dict[str, object] = {}

    try:
        if env_file_error:
            raise SystemExit(env_file_error)
        require_live_env()
    except SystemExit as exc:
        credential_error = str(exc)
        failure_status = "env_file_error" if env_file_error else "missing_credentials"
        preflight_evidence = evidence_root / "preflight.json"
        direct_evidence = evidence_root / "direct_provider.json"
        web_evidence = evidence_root / "web_api.json"
        preflight_command = [
            str(PYTHON),
            "../scripts/preflight_tingwu_live.py",
            "--workspace",
            str(preflight_workspace),
            "--audit-log",
            str(audit_root / "preflight.jsonl"),
            "--evidence-json",
            str(preflight_evidence),
            "--capture-seconds",
            str(max(1, args.preflight_capture_seconds)),
        ]
        direct_command = [
            str(PYTHON),
            "../scripts/verify_tingwu_live.py",
            "--seconds",
            str(args.seconds),
            "--workspace",
            str(direct_workspace),
            "--audit-log",
            str(audit_root / "direct.jsonl"),
            "--evidence-json",
            str(direct_evidence),
            "--spoken-phrase",
            args.spoken_phrase,
        ]
        web_port = 8790
        web_base_url = f"http://127.0.0.1:{web_port}"
        web_console_command = [
            str(PYTHON),
            "openclaw_cli.py",
            "web-console",
            "--host",
            "127.0.0.1",
            "--port",
            str(web_port),
            "--token",
            args.token,
        ]
        web_command = [
            str(PYTHON),
            "../scripts/verify_tingwu_web_live.py",
            "--base-url",
            web_base_url,
            "--token",
            args.token,
            "--seconds",
            str(args.seconds),
            "--evidence-json",
            str(web_evidence),
            "--spoken-phrase",
            args.spoken_phrase,
        ]
        missing_preflight = {
            "status": "failed",
            "error": credential_error,
            "failure_status": failure_status,
            "checks": {
                "tingwu_api_key_configured": live_credential_configured("TINGWU_API_KEY", "DASHSCOPE_API_KEY"),
                "tingwu_app_id_configured": live_credential_configured("TINGWU_APP_ID", "TINGWU_MEETING_APP_ID"),
            },
        }
        if not args.skip_preflight:
            preflight_env = scrub_placeholder_credentials(env)
            evidence_files["preflight"] = str(preflight_evidence)
            preflight_result = run_stage(
                "live preflight",
                preflight_command,
                cwd=RUNTIME_ROOT,
                env=preflight_env,
                metadata={
                    "evidence_json": str(preflight_evidence),
                    "workspace": str(preflight_workspace),
                    "audit_log": str(audit_root / "preflight.jsonl"),
                    "capture_seconds": max(1, args.preflight_capture_seconds),
                },
            )
            stage_runs["preflight"] = {
                **preflight_result,
                "error": str(preflight_result.get("error") or credential_error),
                "failure_status": failure_status,
            }
            stage_evidence["preflight"] = read_evidence_file(str(preflight_evidence))
            if not isinstance(stage_evidence["preflight"], dict) or stage_evidence["preflight"].get("status") == "missing":
                stage_evidence["preflight"] = {
                    **missing_preflight,
                    "workspace_dir": str(preflight_workspace),
                    "audit_log_path": str(audit_root / "preflight.jsonl"),
                }
                write_json(preflight_evidence, stage_evidence["preflight"])
            stage_evidence["preflight"] = merge_stage_result(stage_runs["preflight"], stage_evidence["preflight"])
        if not args.skip_direct:
            evidence_files["direct_provider"] = str(direct_evidence)
            stage_evidence["direct_provider"] = {
                "status": failure_status,
                "error": credential_error,
                "workspace_dir": str(direct_workspace),
                "audit_log_path": str(audit_root / "direct.jsonl"),
            }
            write_json(direct_evidence, stage_evidence["direct_provider"])
            stage_runs["direct_provider"] = {
                "status": "failed",
                "stage": "direct live provider verification",
                "command": direct_command,
                "cwd": str(RUNTIME_ROOT),
                "evidence_json": str(direct_evidence),
                "error": credential_error,
                "failure_status": failure_status,
                "workspace": str(direct_workspace),
                "audit_log": str(audit_root / "direct.jsonl"),
            }
        if not args.skip_web:
            evidence_files["web_api"] = str(web_evidence)
            stage_evidence["web_api"] = {
                "status": failure_status,
                "error": credential_error,
                "base_url": web_base_url,
                "workspace_dir": str(web_workspace),
                "audit_log_path": str(audit_root / "web.jsonl"),
            }
            write_json(web_evidence, stage_evidence["web_api"])
            stage_runs["web_api"] = {
                "status": "failed",
                "stage": "web live verification",
                "command": web_command,
                "cwd": str(RUNTIME_ROOT),
                "evidence_json": str(web_evidence),
                "error": credential_error,
                "failure_status": failure_status,
                "base_url": web_base_url,
                "workspace": str(web_workspace),
                "audit_log": str(audit_root / "web.jsonl"),
                "console_log": str(suite_root / "web-console-live.log"),
                "console_command": web_console_command,
            }
        summary_path = write_summary(
            args=args,
            suite_root=suite_root,
            audit_root=audit_root,
            evidence_root=evidence_root,
            evidence_files=evidence_files,
            stage_runs=stage_runs,
            stage_evidence=stage_evidence,
        )
        print(f"evidence_summary={summary_path}")
        print(f"live_suite_root={suite_root}")
        print(str(exc), file=sys.stderr)
        return 1

    if not args.skip_preflight:
        preflight_evidence = evidence_root / "preflight.json"
        evidence_files["preflight"] = str(preflight_evidence)
        result = run_stage(
            "live preflight",
            [
                str(PYTHON),
                "../scripts/preflight_tingwu_live.py",
                "--workspace",
                str(preflight_workspace),
                "--audit-log",
                str(audit_root / "preflight.jsonl"),
                "--evidence-json",
                str(preflight_evidence),
                "--capture-seconds",
                str(max(1, args.preflight_capture_seconds)),
            ],
            cwd=RUNTIME_ROOT,
            env=env,
            metadata={
                "evidence_json": str(preflight_evidence),
                "workspace": str(preflight_workspace),
                "audit_log": str(audit_root / "preflight.jsonl"),
                "capture_seconds": max(1, args.preflight_capture_seconds),
            },
        )
        stage_runs["preflight"] = result
        stage_evidence["preflight"] = read_evidence_file(str(preflight_evidence))
        stage_evidence["preflight"] = merge_stage_result(result, stage_evidence["preflight"])
    if not args.skip_direct:
        direct_evidence = evidence_root / "direct_provider.json"
        evidence_files["direct_provider"] = str(direct_evidence)
        result = run_stage(
            "direct live provider verification",
            [
                str(PYTHON),
                "../scripts/verify_tingwu_live.py",
                "--seconds",
                str(args.seconds),
                "--workspace",
                str(direct_workspace),
                "--audit-log",
                str(audit_root / "direct.jsonl"),
                "--evidence-json",
                str(direct_evidence),
                "--spoken-phrase",
                args.spoken_phrase,
            ],
            cwd=RUNTIME_ROOT,
            env=env,
            metadata={
                "evidence_json": str(direct_evidence),
                "workspace": str(direct_workspace),
                "audit_log": str(audit_root / "direct.jsonl"),
            },
        )
        stage_runs["direct_provider"] = result
        stage_evidence["direct_provider"] = read_evidence_file(str(direct_evidence))
        stage_evidence["direct_provider"] = merge_stage_result(result, stage_evidence["direct_provider"])
    if not args.skip_web:
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        web_console_command = [
            str(PYTHON),
            "openclaw_cli.py",
            "web-console",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--token",
            args.token,
        ]
        web_env = {
            **env,
            "LELAMP_WEB_TOKEN": args.token,
            "OPENCLAW_WORKSPACE_DIR": str(web_workspace),
            "OPENCLAW_AUDIT_LOG_PATH": str(audit_root / "web.jsonl"),
            "OPENCLAW_ALLOWED_ROOTS": str(web_workspace),
        }
        log_path = suite_root / "web-console-live.log"
        web_stage_metadata = {
            "base_url": base_url,
            "workspace": str(web_workspace),
            "audit_log": str(audit_root / "web.jsonl"),
            "console_log": str(log_path),
            "console_command": web_console_command,
        }
        process = start_web_console(command=web_console_command, cwd=RUNTIME_ROOT, env=web_env, log_path=log_path)
        web_stage_metadata["console_pid"] = process.pid
        try:
            wait_for_console(base_url, args.token)
            web_evidence = evidence_root / "web_api.json"
            evidence_files["web_api"] = str(web_evidence)
            result = run_stage(
                "web live verification",
                [
                    str(PYTHON),
                    "../scripts/verify_tingwu_web_live.py",
                    "--base-url",
                    base_url,
                    "--token",
                    args.token,
                    "--seconds",
                    str(args.seconds),
                    "--evidence-json",
                    str(web_evidence),
                    "--spoken-phrase",
                    args.spoken_phrase,
                ],
                cwd=RUNTIME_ROOT,
                env=web_env,
                metadata={**web_stage_metadata, "evidence_json": str(web_evidence)},
            )
            stage_runs["web_api"] = result
            stage_evidence["web_api"] = read_evidence_file(str(web_evidence))
            stage_evidence["web_api"] = merge_stage_result(result, stage_evidence["web_api"])
            stop_process(process)
            stage_runs["web_api"]["console_returncode"] = process.returncode

            if evidence_ok(stage_evidence.get("web_api")):
                restart_port = free_port()
                restart_base_url = f"http://127.0.0.1:{restart_port}"
                restart_command = [
                    str(PYTHON),
                    "openclaw_cli.py",
                    "web-console",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(restart_port),
                    "--token",
                    args.token,
                ]
                restart_log_path = suite_root / "web-console-live-restart.log"
                restart_process = start_web_console(command=restart_command, cwd=RUNTIME_ROOT, env=web_env, log_path=restart_log_path)
                try:
                    wait_for_console(restart_base_url, args.token)
                    restart_result = verify_web_restart_recovery(restart_base_url, args.token, stage_evidence["web_api"])
                    web_checks = stage_evidence["web_api"].get("checks") if isinstance(stage_evidence["web_api"].get("checks"), dict) else {}
                    stage_evidence["web_api"] = {
                        **stage_evidence["web_api"],
                        "web_restart_recovery": restart_result,
                        "checks": {**web_checks, "web_restart_recovery_outputs": True},
                    }
                    web_evidence.write_text(json.dumps(stage_evidence["web_api"], ensure_ascii=False, indent=2), encoding="utf-8")
                    stage_runs["web_api"].update({
                        "restart_base_url": restart_base_url,
                        "restart_console_log": str(restart_log_path),
                        "restart_console_command": restart_command,
                        "restart_console_pid": restart_process.pid,
                    })
                except Exception as exc:
                    restart_details = {
                        "status": "failed",
                        "error": str(exc),
                        "restart_base_url": restart_base_url,
                        "restart_console_log": str(restart_log_path),
                        "restart_console_command": restart_command,
                        "restart_console_pid": restart_process.pid,
                    }
                    stage_runs["web_api"].update(restart_details)
                    stage_evidence["web_api"] = merge_web_restart_failure(web_evidence, stage_evidence["web_api"], restart_details)
                finally:
                    stop_process(restart_process)
                    stage_runs["web_api"]["restart_console_returncode"] = restart_process.returncode
        except Exception as exc:
            print(f"web console log: {log_path}", file=sys.stderr)
            stage_runs["web_api"] = {
                "status": "failed",
                "stage": "web live verification",
                "error": str(exc),
                **web_stage_metadata,
            }
            stage_evidence["web_api"] = {
                "status": "failed",
                "error": f"web console failed; see {log_path}",
                "stage_error": stage_runs["web_api"],
            }
        finally:
            stop_process(process)
            if isinstance(stage_runs.get("web_api"), dict) and "console_returncode" not in stage_runs["web_api"]:
                stage_runs["web_api"]["console_returncode"] = process.returncode

    for name, path_value in evidence_files.items():
        stage_evidence.setdefault(name, read_evidence_file(path_value))
    summary_path = write_summary(
        args=args,
        suite_root=suite_root,
        audit_root=audit_root,
        evidence_root=evidence_root,
        evidence_files=evidence_files,
        stage_runs=stage_runs,
        stage_evidence=stage_evidence,
    )
    print(f"evidence_summary={summary_path}")
    print(f"live_suite_root={suite_root}")
    print("verify_tingwu_live_suite complete")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return 0 if summary.get("acceptance_complete") is True else 1


if __name__ == "__main__":
    if sys.version_info < (3, 10):
        raise SystemExit("verify_tingwu_live_suite requires Python >= 3.10.")
    raise SystemExit(main())
