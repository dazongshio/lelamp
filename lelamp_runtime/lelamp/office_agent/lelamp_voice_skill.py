from __future__ import annotations

import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import textwrap
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal

from lelamp.motion_config import complete_pose, get_action_recording, get_named_pose, load_motion_config

from .audit import AuditLogger
from .config import OfficeAgentConfig
from .hardware import LampHardware
from .utils import safe_filename


VOICE_COMMANDS_FILE = Path(__file__).with_name("lelamp_voice_commands.json")

LampVoiceAction = Literal[
    "set_rgb",
    "play",
    "start_follow",
    "stop_follow",
    "default_state",
    "scan_pose",
    "scan_pdf",
    "project",
    "relax_motors",
    "status",
]


@dataclass(frozen=True)
class LampVoiceCommand:
    action: LampVoiceAction
    label: str
    reply: str
    rgb: tuple[int, int, int] | None = None
    recording: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "label": self.label,
            "reply": self.reply,
            "rgb": list(self.rgb) if self.rgb is not None else None,
            "recording": self.recording,
        }


class LeLampVoiceSkill:
    """Deterministic voice commands for local LeLamp control."""

    def __init__(
        self,
        *,
        config: OfficeAgentConfig,
        audit: AuditLogger,
        hardware: LampHardware | None = None,
        camera_observer: Any | None = None,
        scanning: Any | None = None,
        workspace: Any | None = None,
        projection: Any | None = None,
        preview_snapshot: Callable[..., dict[str, object]] | None = None,
    ):
        self.config = config
        self.audit = audit
        self.hardware = hardware
        self.camera_observer = camera_observer
        self.scanning = scanning
        self.workspace = workspace
        self.projection = projection
        self.preview_snapshot = preview_snapshot
        self.state_dir = config.workspace_dir / ".lamp_voice"
        self.pid_path = self.state_dir / "follow.pid"
        self.log_path = self.state_dir / "follow.log"
        self.motion_config_path = config.workspace_dir / "lelamp_motion_config.json"
        self._scan_capture_condition = threading.Condition()
        self._scan_capture_owner: str | None = None
        self._scan_capture_started_at: float | None = None

    def set_hardware(self, hardware: LampHardware | None) -> None:
        self.hardware = hardware

    def set_scan_services(
        self,
        *,
        camera_observer: Any | None = None,
        scanning: Any | None = None,
        workspace: Any | None = None,
        projection: Any | None = None,
        preview_snapshot: Callable[..., dict[str, object]] | None = None,
    ) -> None:
        if camera_observer is not None:
            self.camera_observer = camera_observer
        if scanning is not None:
            self.scanning = scanning
        if workspace is not None:
            self.workspace = workspace
        if projection is not None:
            self.projection = projection
        if preview_snapshot is not None:
            self.preview_snapshot = preview_snapshot

    def handle_text(self, text: str) -> dict[str, object]:
        command = parse_lamp_voice_command(text)
        if command is None:
            return {"handled": False, "text": text}

        result = self.execute(command)
        self.audit.record(
            "lelamp.voice_command",
            target=text,
            status=str(result.get("status", "unknown")),
            details={k: v for k, v in result.items() if k != "text"},
        )
        return {"handled": True, "text": text, **result}

    def execute(self, command: LampVoiceCommand) -> dict[str, object]:
        if command.action == "status":
            return self._status_result(command)
        scan_capture_owner: str | None = None
        scan_capture_wait = None
        if command.action == "scan_pdf":
            scan_capture_owner = self._try_begin_scan_capture()
            if scan_capture_owner is None:
                return {
                    "status": "busy",
                    "reply": "正在调整扫描位并拍照，请等拍照完成后再执行扫描。",
                    "command": command.as_dict(),
                    "scan_capture": self._scan_capture_status(),
                }
        else:
            scan_capture_wait = self._wait_for_scan_capture_if_motor_command(command)
            if scan_capture_wait is not None and scan_capture_wait.get("status") == "blocked":
                return {
                    "status": "blocked",
                    "reply": "正在拍照扫描，电机命令暂不执行。",
                    "command": command.as_dict(),
                    "scan_capture_wait": scan_capture_wait,
                }
        try:
            previous_result = self._stop_previous_command(command)
        except Exception:
            if scan_capture_owner is not None:
                self._finish_scan_capture(scan_capture_owner, status="error_before_capture")
            raise
        if scan_capture_wait is not None:
            previous_result = {**(previous_result or {}), "scan_capture_wait": scan_capture_wait}
        if command.action == "default_state":
            return self._with_previous_result(self._default_state(command, stop_result=previous_result), previous_result)
        if command.action == "scan_pose":
            return self._with_previous_result(self._scan_pose_command(command), previous_result)
        if command.action == "scan_pdf":
            try:
                return self._with_previous_result(self._scan_pdf(command, capture_owner=scan_capture_owner), previous_result)
            except Exception:
                if scan_capture_owner is not None:
                    self._finish_scan_capture(scan_capture_owner, status="error")
                raise
        if command.action == "project":
            return self._with_previous_result(self._project(command), previous_result)
        if command.action == "relax_motors":
            return self._with_previous_result(self._relax_motors(command), previous_result)
        if command.action == "start_follow":
            return self._with_previous_result(self._start_follow(command), previous_result)
        if command.action == "stop_follow":
            return self._with_previous_result(self._stop_follow(command), previous_result)
        if not self.config.enable_hardware:
            return self._with_previous_result({
                "status": "needs_hardware",
                "reply": "硬件控制还没开启。请设置 OPENCLAW_ENABLE_HARDWARE=1 后再试。",
                "command": command.as_dict(),
            }, previous_result)
        hardware = self.hardware
        if hardware is None:
            return self._with_previous_result({
                "status": "hardware_unavailable",
                "reply": "台灯硬件控制没有连接。",
                "command": command.as_dict(),
            }, previous_result)
        if command.action == "set_rgb" and command.rgb is not None:
            result = hardware.set_rgb(*command.rgb)
            status = "executed" if not result.startswith("Hardware is disabled") else "hardware_unavailable"
            return self._with_previous_result({
                "status": status,
                "reply": command.reply if status == "executed" else "灯光硬件不可用，命令没有实际执行。",
                "command": command.as_dict(),
                "hardware_result": result,
            }, previous_result)
        if command.action == "play" and command.recording:
            if hasattr(hardware, "ensure_motion"):
                hardware.ensure_motion()
            result = hardware.play(command.recording)
            status = "executed" if not result.startswith("Hardware is disabled") else "hardware_unavailable"
            return self._with_previous_result({
                "status": status,
                "reply": command.reply if status == "executed" else "舵机动作硬件不可用，命令没有实际执行。",
                "command": command.as_dict(),
                "hardware_result": result,
            }, previous_result)
        return self._with_previous_result({"status": "unsupported", "reply": "这个台灯命令暂时不支持。", "command": command.as_dict()}, previous_result)

    def _stop_previous_command(self, command: LampVoiceCommand) -> dict[str, object] | None:
        stopped: dict[str, object] = {}
        if command.action != "stop_follow":
            follow_result = self._stop_follow(LampVoiceCommand("stop_follow", "stop_follow", "已停止跟随。"))
            if str(follow_result.get("status")) not in {"not_running"}:
                stopped["follow"] = follow_result
        hardware = self.hardware
        if command.action != "start_follow" and hardware is not None and hasattr(hardware, "interrupt_motion"):
            hardware.interrupt_motion()
            stopped["motion"] = {"status": "interrupted"}
        return stopped or None

    def _with_previous_result(self, result: dict[str, object], previous_result: dict[str, object] | None) -> dict[str, object]:
        if previous_result:
            return {**result, "previous_command": previous_result}
        return result

    def _try_begin_scan_capture(self) -> str | None:
        with self._scan_capture_condition:
            if self._scan_capture_owner is not None:
                return None
            owner = f"scan_capture_{time.monotonic_ns()}"
            self._scan_capture_owner = owner
            self._scan_capture_started_at = time.monotonic()
            return owner

    def _finish_scan_capture(self, owner: str | None, *, status: str) -> None:
        if owner is None:
            return
        with self._scan_capture_condition:
            if self._scan_capture_owner != owner:
                return
            duration_ms = None
            if self._scan_capture_started_at is not None:
                duration_ms = round((time.monotonic() - self._scan_capture_started_at) * 1000)
            self._scan_capture_owner = None
            self._scan_capture_started_at = None
            self._scan_capture_condition.notify_all()
        self.audit.record(
            "lelamp.scan_capture_gate",
            status=status,
            details={"duration_ms": duration_ms},
        )

    def _scan_capture_status(self) -> dict[str, object]:
        with self._scan_capture_condition:
            elapsed_ms = None
            if self._scan_capture_started_at is not None:
                elapsed_ms = round((time.monotonic() - self._scan_capture_started_at) * 1000)
            return {
                "active": self._scan_capture_owner is not None,
                "elapsed_ms": elapsed_ms,
            }

    def _wait_for_scan_capture_if_motor_command(self, command: LampVoiceCommand) -> dict[str, object] | None:
        if not self._command_may_move_motor(command):
            return None
        start = time.monotonic()
        timeout_seconds = 18.0
        with self._scan_capture_condition:
            while self._scan_capture_owner is not None:
                remaining = timeout_seconds - (time.monotonic() - start)
                if remaining <= 0:
                    return {
                        "status": "blocked",
                        "waited_ms": round((time.monotonic() - start) * 1000),
                        "reason": "scan_capture_timeout",
                    }
                self._scan_capture_condition.wait(timeout=min(0.25, remaining))
        waited_ms = round((time.monotonic() - start) * 1000)
        if waited_ms <= 0:
            return None
        return {"status": "waited", "waited_ms": waited_ms}

    @staticmethod
    def _command_may_move_motor(command: LampVoiceCommand) -> bool:
        return command.action in {
            "play",
            "start_follow",
            "stop_follow",
            "default_state",
            "scan_pose",
            "scan_pdf",
            "project",
            "relax_motors",
        }

    def _status_result(self, command: LampVoiceCommand) -> dict[str, object]:
        follow_pid = self._running_follow_pid()
        hardware = self.hardware
        details = {
            "hardware_enabled": self.config.enable_hardware,
            "rgb_enabled": self.config.enable_rgb,
            "hardware_attached": hardware is not None,
            "motion_available": bool(getattr(hardware, "animation_service", None)) if hardware else False,
            "rgb_available": bool(getattr(hardware, "rgb_service", None)) if hardware else False,
            "follow_running": follow_pid is not None,
            "follow_pid": follow_pid,
            "follow_log": str(self.log_path),
            "scan_capture": self._scan_capture_status(),
        }
        reply = "台灯正在跟随人。" if follow_pid is not None else "台灯语音控制已就绪。"
        if not self.config.enable_hardware:
            reply = "台灯语音解析已就绪，但硬件控制未开启。"
        return {"status": "reported", "reply": reply, "command": command.as_dict(), "details": details}

    def _start_follow(self, command: LampVoiceCommand) -> dict[str, object]:
        if not self.config.enable_hardware:
            return {
                "status": "needs_hardware",
                "reply": "硬件控制还没开启，不能启动跟随。",
                "command": command.as_dict(),
            }
        running_pid = self._running_follow_pid()
        if running_pid is not None:
            return {
                "status": "already_running",
                "reply": "已经在跟随人。",
                "command": command.as_dict(),
                "pid": running_pid,
                "log": str(self.log_path),
            }
        if self.hardware is not None and hasattr(self.hardware, "release_motion"):
            self.hardware.release_motion()

        self.state_dir.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("ab", buffering=0)
        args = [
            sys.executable,
            "-m",
            "lelamp.realtime_environment",
            "--move",
            "--search-scan",
            "--scene-scan",
            "--cam1-servo-camera",
            "0",
            "--cam0-fixed-camera",
            "1",
            "--audio-device",
            self.config.mic_device,
            "--port",
            self.config.hardware_port,
            "--id",
            self.config.lamp_id,
        ]
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        process = subprocess.Popen(
            args,
            cwd=str(Path(__file__).resolve().parents[2]),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
        self.pid_path.write_text(str(process.pid), encoding="utf-8")
        return {
            "status": "started",
            "reply": command.reply,
            "command": command.as_dict(),
            "pid": process.pid,
            "log": str(self.log_path),
        }

    def _stop_follow(self, command: LampVoiceCommand) -> dict[str, object]:
        pid = self._running_follow_pid()
        if pid is None:
            self._clear_pid()
            return {"status": "not_running", "reply": "现在没有在跟随。", "command": command.as_dict()}

        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            self._clear_pid()
            return {"status": "not_running", "reply": "跟随进程已经停止。", "command": command.as_dict()}
        except PermissionError as exc:
            return {
                "status": "error",
                "reply": "没有权限停止跟随进程。",
                "command": command.as_dict(),
                "error": str(exc),
            }

        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            if not _pid_alive(pid):
                self._clear_pid()
                return {"status": "stopped", "reply": command.reply, "command": command.as_dict()}
            time.sleep(0.1)

        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self._clear_pid()
        return {"status": "stopped", "reply": command.reply, "command": command.as_dict(), "forced": True}

    def _default_state(self, command: LampVoiceCommand, *, stop_result: dict[str, object] | None = None) -> dict[str, object]:
        follow_stop = stop_result.get("follow") if isinstance(stop_result, dict) and isinstance(stop_result.get("follow"), dict) else None
        if str((follow_stop or {}).get("status")) == "error":
            return {
                "status": "error",
                "reply": "停止跟随失败，暂时不能回到默认状态。",
                "command": command.as_dict(),
                "stop_result": follow_stop,
            }
        if not self.config.enable_hardware:
            return {
                "status": "needs_hardware",
                "reply": "硬件控制未开启，不能回到默认姿态。",
                "command": command.as_dict(),
            }
        hardware = self.hardware
        if hardware is None:
            return {
                "status": "hardware_unavailable",
                "reply": "台灯硬件控制没有连接，不能回到默认姿态。",
                "command": command.as_dict(),
            }
        default_pose = self._default_pose()
        default_pose_path = self._default_pose_path()
        if default_pose and hasattr(hardware, "hold_pose_result"):
            pose_result = dict(
                hardware.hold_pose_result(
                    default_pose,
                    pose_name=self._default_pose_name(),
                    pose_file=str(self.motion_config_path),
                    max_step=16.0,
                    step_seconds=0.02,
                    tolerance=8.0,
                    stable_seconds=0.02,
                    max_iterations=20,
                )
            )
            status = "executed" if bool(pose_result.get("reached")) else "blocked"
            hardware_result = (
                f"Holding lamp pose from motion config: {self._default_pose_name()}"
                if status == "executed"
                else f"Default pose not reached: {pose_result.get('worst_motor') or 'unknown'} max_error={pose_result.get('max_error')}"
            )
            return {
                "status": status,
                "reply": command.reply if status == "executed" else "默认姿态没有到位。",
                "command": command.as_dict(),
                "pose_result": pose_result,
                "hardware_result": hardware_result,
            }
        if default_pose_path is not None and hasattr(hardware, "hold_pose_file_result"):
            pose_result = dict(
                hardware.hold_pose_file_result(
                    default_pose_path,
                    pose_name=self._default_pose_name(),
                    max_step=16.0,
                    step_seconds=0.02,
                    tolerance=8.0,
                    stable_seconds=0.02,
                    max_iterations=20,
                )
            )
            status = "executed" if bool(pose_result.get("reached")) else "blocked"
            hardware_result = (
                f"Holding lamp pose from saved pose: {self._default_pose_name()}"
                if status == "executed"
                else f"Default pose not reached: {pose_result.get('worst_motor') or 'unknown'} max_error={pose_result.get('max_error')}"
            )
            return {
                "status": status,
                "reply": command.reply if status == "executed" else "默认姿态没有到位。",
                "command": command.as_dict(),
                "pose_result": pose_result,
                "hardware_result": hardware_result,
            }
        if hasattr(hardware, "hold_recording_final_pose"):
            result = hardware.hold_recording_final_pose("idle")
        else:
            result = hardware.play("idle")
        status = "executed" if not result.startswith("Hardware is disabled") else "hardware_unavailable"
        return {
            "status": status,
            "reply": command.reply if status == "executed" else "默认姿态没有实际执行。",
            "command": command.as_dict(),
            "hardware_result": result,
        }

    def _scan_pose_command(self, command: LampVoiceCommand) -> dict[str, object]:
        pose_result = self._hold_scan_pose()
        status = "executed" if self._scan_pose_ready(pose_result) else "blocked"
        return {
            "status": status,
            "reply": command.reply if status == "executed" else "扫描位置没有到位。",
            "command": command.as_dict(),
            "pose_result": pose_result,
        }

    def _project(self, command: LampVoiceCommand) -> dict[str, object]:
        pose_result = self._hold_projection_pose()
        if not bool(pose_result.get("reached")):
            return {
                "status": "blocked",
                "reply": "投影位置没有到位，已取消投影。",
                "command": command.as_dict(),
                "pose_result": pose_result,
            }
        projection = self.projection
        if projection is None or not hasattr(projection, "render_status_card"):
            return {
                "status": "backend_missing",
                "reply": "投影服务没有接入，不能开始投影。",
                "command": command.as_dict(),
                "pose_result": pose_result,
            }
        card = projection.render_status_card(
            "LeLamp 投影已就绪",
            "projecting",
            details=[
                "舵机已到达投影位置。",
                "投影预览页会显示最新投影内容。",
            ],
            accent="blue",
        )
        preview_url = os.getenv("LELAMP_PROJECTION_PREVIEW_URL", "http://127.0.0.1:8765/").strip() or "http://127.0.0.1:8765/"
        return {
            "status": "executed",
            "reply": command.reply,
            "command": command.as_dict(),
            "pose_result": pose_result,
            "projection": card,
            "preview_url": preview_url,
        }

    def _relax_motors(self, command: LampVoiceCommand) -> dict[str, object]:
        if not self.config.enable_hardware:
            return {
                "status": "needs_hardware",
                "reply": "硬件控制未开启，不能松开电机。",
                "command": command.as_dict(),
            }
        hardware = self.hardware
        if hardware is None:
            return {
                "status": "hardware_unavailable",
                "reply": "台灯硬件控制没有连接，不能松开电机。",
                "command": command.as_dict(),
            }
        if not hasattr(hardware, "relax_motors_result"):
            return {
                "status": "unsupported",
                "reply": "当前硬件适配器还不支持松开电机。",
                "command": command.as_dict(),
            }
        result = dict(hardware.relax_motors_result())
        status = "executed" if str(result.get("status")) == "torque_disabled" else "blocked"
        return {
            "status": status,
            "reply": command.reply if status == "executed" else "松开电机没有成功。",
            "command": command.as_dict(),
            "hardware_result": result,
        }

    def _scan_pdf(self, command: LampVoiceCommand, *, capture_owner: str | None = None) -> dict[str, object]:
        start_time = time.monotonic()
        pose_result = self._hold_scan_pose()
        camera_index = self._scan_camera_index()
        rotation_degrees = self._scan_camera_rotation_degrees()
        if not self._scan_pose_ready(pose_result):
            self._finish_scan_capture(capture_owner, status="scan_pose_not_ready")
            return {
                "status": "blocked",
                "reply": "扫描固定位置没有到位，已取消拍照扫描。",
                "command": command.as_dict(),
                "camera_index": camera_index,
                "rotation_degrees": rotation_degrees,
                "pose_result": pose_result,
                "duration_ms": round((time.monotonic() - start_time) * 1000),
            }
        title = f"lamp_head_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        capture = self._capture_lamp_head_frame(
            title,
            camera_index=camera_index,
            rotation_degrees=rotation_degrees,
            timeout_seconds=12,
        )
        if str(capture.get("status") or "") != "captured":
            self._finish_scan_capture(capture_owner, status="capture_unavailable")
            return {
                "status": "unavailable",
                "reply": "灯头摄像头没有拍到图片，请检查 cam1/灯头摄像头连接。",
                "command": command.as_dict(),
                "camera_index": camera_index,
                "rotation_degrees": rotation_degrees,
                "pose_result": pose_result,
                "capture": capture,
            }

        self._finish_scan_capture(capture_owner, status="captured")
        capture_path = Path(str(capture.get("path") or "")).expanduser().resolve()
        capture_workspace_name = self._workspace_name_for_path(capture_path)
        scan = self._process_scan_image(capture_workspace_name)
        pdf = self._create_scan_pdf(scan, fallback_workspace_name=capture_workspace_name, title=title)
        pdf_status = str(pdf.get("status") or "")
        archived_intermediates = {}
        if pdf_status == "created":
            archived_intermediates = self._archive_scan_pdf_intermediates(
                title=title,
                capture=capture,
                capture_workspace_name=capture_workspace_name,
                scan=scan,
                pdf=pdf,
            )
        status = "completed" if pdf_status == "created" else "partial"
        result = {
            "status": status,
            "reply": f"扫描完成，PDF 已生成：{pdf.get('pdf_workspace_name')}。" if pdf_status == "created" else "已拍照扫描，但 PDF 生成失败。",
            "command": command.as_dict(),
            "camera_index": camera_index,
            "rotation_degrees": rotation_degrees,
            "pose_result": pose_result,
            "capture": capture,
            "source_image_path": str(capture_path),
            "source_workspace_name": capture_workspace_name,
            "scan": scan,
            "pdf": pdf,
            "archived_intermediates": archived_intermediates,
            "pdf_path": pdf.get("pdf_path") or "",
            "pdf_workspace_name": pdf.get("pdf_workspace_name") or "",
            "duration_ms": round((time.monotonic() - start_time) * 1000),
        }
        return result

    def _archive_scan_pdf_intermediates(
        self,
        *,
        title: str,
        capture: dict[str, object],
        capture_workspace_name: str,
        scan: dict[str, object],
        pdf: dict[str, object],
    ) -> dict[str, object]:
        workspace = self._workspace()
        if workspace is None:
            return {"status": "skipped", "reason": "workspace_unavailable"}
        root = workspace.root.resolve()
        keep_paths: set[Path] = set()
        for value in (pdf.get("pdf_path"), pdf.get("pdf_workspace_name")):
            resolved = _resolve_workspace_artifact(root, workspace, value)
            if resolved is not None:
                keep_paths.add(resolved)

        candidates: set[Path] = set()

        def add(value: object) -> None:
            path = _resolve_workspace_artifact(root, workspace, value)
            if path is None or path in keep_paths:
                return
            try:
                relative = path.relative_to(root)
            except ValueError:
                return
            if relative.parts and relative.parts[0] == ".archive":
                return
            candidates.add(path)

        add(capture_workspace_name)
        add(capture.get("path"))
        _walk_workspace_artifacts(capture, add)
        _walk_workspace_artifacts(scan, add)
        _walk_workspace_artifacts(pdf, add)

        if not candidates:
            return {"status": "empty", "count": 0}

        day = datetime.now().strftime("%Y/%m/%d")
        archive_root = root / ".archive" / "scan_pdf_intermediates" / day / safe_filename(title, default="scan_pdf")
        archive_root.mkdir(parents=True, exist_ok=True)
        moved: list[dict[str, str]] = []
        for path in sorted(candidates):
            if not path.is_file():
                continue
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            destination = _dedupe_archive_path(archive_root / relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
            moved.append({"from": str(relative), "to": str(destination.relative_to(root))})

        manifest_path = archive_root / "manifest.tsv"
        try:
            manifest_path.write_text(
                "\n".join(f"{item['from']}\t{item['to']}" for item in moved) + ("\n" if moved else ""),
                encoding="utf-8",
            )
        except Exception:
            pass
        payload = {
            "status": "archived",
            "count": len(moved),
            "archive_workspace_name": str(archive_root.relative_to(root)),
            "manifest_workspace_name": str(manifest_path.relative_to(root)),
        }
        self.audit.record("scan.pdf_intermediates_archived", target=str(archive_root), details=payload)
        return payload

    def _hold_scan_pose(self) -> dict[str, object]:
        if not self.config.enable_hardware:
            return {
                "status": "disabled",
                "reached": False,
                "pose_name": self._scan_pose_name(),
                "reason": "Hardware is disabled or unavailable.",
            }
        hardware = self.hardware
        if hardware is None:
            return {
                "status": "hardware_unavailable",
                "reached": False,
                "pose_name": self._scan_pose_name(),
                "reason": "Lamp hardware is not connected.",
            }
        pose_path = self._scan_pose_path()
        scan_pose = self._scan_pose()
        if scan_pose and hasattr(hardware, "hold_pose_result"):
            return dict(
                hardware.hold_pose_result(
                    scan_pose,
                    pose_name=self._scan_pose_name(),
                    pose_file=str(self.motion_config_path),
                    max_step=14.0,
                    step_seconds=0.03,
                    tolerance=7.5,
                    stable_seconds=0.08,
                    max_iterations=28,
                )
            )
        if pose_path is not None and hasattr(hardware, "hold_pose_file_result"):
            return dict(
                hardware.hold_pose_file_result(
                    pose_path,
                    pose_name=self._scan_pose_name(),
                    max_step=14.0,
                    step_seconds=0.03,
                    tolerance=7.5,
                    stable_seconds=0.08,
                    max_iterations=28,
                )
            )
        if hasattr(hardware, "hold_recording_final_pose_result"):
            return dict(hardware.hold_recording_final_pose_result("scanning", tolerance=0.7, stable_seconds=0.45))
        if hasattr(hardware, "hold_recording_final_pose"):
            result = str(hardware.hold_recording_final_pose("scanning"))
            return {
                "status": "legacy_hold",
                "reached": result.startswith("Holding lamp pose"),
                "recording": "scanning",
                "hardware_result": result,
            }
        result = str(hardware.play("scanning"))
        return {
            "status": "legacy_play",
            "reached": False,
            "recording": "scanning",
            "hardware_result": result,
            "reason": "Cannot verify fixed scan pose with play-only hardware adapter.",
        }

    def _hold_projection_pose(self) -> dict[str, object]:
        if not self.config.enable_hardware:
            return {
                "status": "disabled",
                "reached": False,
                "pose_name": self._projection_pose_name(),
                "reason": "Hardware is disabled or unavailable.",
            }
        hardware = self.hardware
        if hardware is None:
            return {
                "status": "hardware_unavailable",
                "reached": False,
                "pose_name": self._projection_pose_name(),
                "reason": "Lamp hardware is not connected.",
            }
        projection_pose = self._projection_pose()
        if not projection_pose:
            return {
                "status": "missing_pose",
                "reached": False,
                "pose_name": self._projection_pose_name(),
                "pose_file": str(self._projection_pose_path() or ""),
                "reason": "Projection pose is not configured. Save a named pose `projection` first.",
            }
        if hasattr(hardware, "hold_pose_result"):
            return dict(
                hardware.hold_pose_result(
                    projection_pose,
                    pose_name=self._projection_pose_name(),
                    pose_file=str(self.motion_config_path),
                    max_step=14.0,
                    step_seconds=0.03,
                    tolerance=7.5,
                    stable_seconds=0.08,
                    max_iterations=28,
                )
            )
        return {
            "status": "unsupported",
            "reached": False,
            "pose_name": self._projection_pose_name(),
            "reason": "Hardware adapter cannot hold named poses.",
        }

    def _scan_pose_path(self) -> Path | None:
        configured = os.getenv("LELAMP_SCAN_POSE_FILE", "").strip()
        if configured:
            path = Path(configured).expanduser()
            return path if path.exists() else None
        pose_name = self._scan_pose_name()
        workspace = self._workspace()
        if workspace is not None:
            path = workspace.root / ".poses" / f"{pose_name}.json"
            if path.exists():
                return path
        fallback = self.config.workspace_dir / ".poses" / f"{pose_name}.json"
        return fallback if fallback.exists() else None

    def _scan_pose(self) -> dict[str, float]:
        pose = get_named_pose(load_motion_config(self.motion_config_path), "scan")
        return pose or self._pose_from_file(self._scan_pose_path())

    def _scan_pose_name(self) -> str:
        return os.getenv("LELAMP_SCAN_POSE_NAME", "lelamp_scan").strip() or "lelamp_scan"

    def _projection_pose_path(self) -> Path | None:
        configured = os.getenv("LELAMP_PROJECTION_POSE_FILE", "").strip()
        if configured:
            path = Path(configured).expanduser()
            return path if path.exists() else None
        pose_name = self._projection_pose_name()
        workspace = self._workspace()
        if workspace is not None:
            path = workspace.root / ".poses" / f"{pose_name}.json"
            if path.exists():
                return path
        fallback = self.config.workspace_dir / ".poses" / f"{pose_name}.json"
        return fallback if fallback.exists() else None

    def _projection_pose(self) -> dict[str, float]:
        pose = get_named_pose(load_motion_config(self.motion_config_path), "projection")
        return pose or self._pose_from_file(self._projection_pose_path())

    def _projection_pose_name(self) -> str:
        return os.getenv("LELAMP_PROJECTION_POSE_NAME", "lelamp_projection").strip() or "lelamp_projection"

    def _default_pose_path(self) -> Path | None:
        configured = os.getenv("LELAMP_DEFAULT_POSE_FILE", "").strip()
        if configured:
            path = Path(configured).expanduser()
            return path if path.exists() else None
        pose_name = self._default_pose_name()
        workspace = self._workspace()
        if workspace is not None:
            path = workspace.root / ".poses" / f"{pose_name}.json"
            if path.exists():
                return path
        fallback = self.config.workspace_dir / ".poses" / f"{pose_name}.json"
        return fallback if fallback.exists() else None

    def _default_pose(self) -> dict[str, float]:
        pose = get_named_pose(load_motion_config(self.motion_config_path), "default")
        return pose or self._pose_from_file(self._default_pose_path())

    def _default_pose_name(self) -> str:
        return os.getenv("LELAMP_DEFAULT_POSE_NAME", "lelamp_center").strip() or "lelamp_center"

    def _pose_from_file(self, path: Path | None) -> dict[str, float]:
        if path is None:
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        motors = payload.get("motors", payload) if isinstance(payload, dict) else {}
        return complete_pose(motors)

    def _scan_pose_ready(self, pose_result: dict[str, object]) -> bool:
        return bool(pose_result.get("reached"))

    def _capture_lamp_head_frame(
        self,
        title: str,
        *,
        camera_index: int,
        rotation_degrees: int,
        timeout_seconds: int,
    ) -> dict[str, object]:
        camera_observer = self.camera_observer
        if camera_observer is None or not hasattr(camera_observer, "capture_frame"):
            return {
                "status": "backend_missing",
                "message": "Camera observer service is not available.",
                "camera_index": camera_index,
                "rotation_degrees": rotation_degrees,
            }
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            camera_observer.capture_frame,
            camera_index=camera_index,
            rotation_degrees=rotation_degrees,
        )
        try:
            capture = future.result(timeout=timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            capture = {
                "status": "unavailable",
                "message": f"设备摄像头拍照超过 {timeout_seconds} 秒未返回。",
                "camera_index": camera_index,
                "rotation_degrees": rotation_degrees,
                "timeout_seconds": timeout_seconds,
            }
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if str(capture.get("status") or "") == "captured":
            return capture
        if self.preview_snapshot is not None:
            try:
                fallback_capture = self.preview_snapshot(title, camera_index=camera_index, rotation_degrees=rotation_degrees)
            except Exception as exc:
                fallback_capture = {
                    "status": "unavailable",
                    "source": "camera_preview_snapshot",
                    "camera_index": camera_index,
                    "rotation_degrees": rotation_degrees,
                    "message": f"相机预览快照不可用：{type(exc).__name__}",
                }
            if str(fallback_capture.get("status") or "") == "captured":
                fallback_capture["primary_capture"] = capture
                return fallback_capture
            return {
                "status": "unavailable",
                "message": "设备摄像头和预览快照都没有拍到图片。",
                "camera_index": camera_index,
                "rotation_degrees": rotation_degrees,
                "primary_capture": capture,
                "fallback_capture": fallback_capture,
            }
        return capture

    def _scan_camera_index(self) -> int:
        value = os.getenv("LELAMP_SCAN_CAMERA_INDEX", "0").strip()
        try:
            return int(value)
        except ValueError:
            return 0

    def _scan_camera_rotation_degrees(self) -> int:
        value = os.getenv("LELAMP_SCAN_CAMERA_ROTATION_DEGREES", os.getenv("LELAMP_SCAN_CAMERA_ROTATION", "180")).strip()
        try:
            degrees = int(value)
        except ValueError:
            return 180
        return 180 if degrees % 360 == 180 else 0

    def _process_scan_image(self, capture_workspace_name: str) -> dict[str, object]:
        scanning = self.scanning
        if scanning is None or not hasattr(scanning, "process_scan_image"):
            return {
                "status": "backend_missing",
                "reason": "Scan service is not available.",
                "image": capture_workspace_name,
            }
        try:
            return dict(scanning.process_scan_image(capture_workspace_name, document_type="document", language="chi_sim+eng"))
        except Exception as exc:
            return {
                "status": "error",
                "reason": "scan_processing_failed",
                "image": capture_workspace_name,
                "error": str(exc)[:500],
            }

    def _create_scan_pdf(self, scan: dict[str, object], *, fallback_workspace_name: str, title: str) -> dict[str, object]:
        workspace = self._workspace()
        if workspace is None:
            return {
                "status": "backend_missing",
                "reason": "Workspace service is not available.",
                "source_workspace_name": fallback_workspace_name,
            }
        enhancement = scan.get("enhancement") if isinstance(scan.get("enhancement"), dict) else {}
        ocr = scan.get("ocr") if isinstance(scan.get("ocr"), dict) else {}
        image_selection = _scan_pdf_image_selection(enhancement, fallback_workspace_name)
        image_workspace_name = str(image_selection["workspace_name"])
        try:
            image_path = workspace.resolve_workspace_file(image_workspace_name)
        except Exception as exc:
            return {
                "status": "error",
                "reason": "source_image_unavailable",
                "source_workspace_name": image_workspace_name,
                "error": str(exc)[:500],
            }
        pdf_path = workspace.path_for_new_file(safe_filename(title, default="lamp_head_scan", suffix=".pdf"))
        try:
            pdf_info = _write_professional_scan_pdf(
                pdf_path,
                title=title,
                image_path=image_path,
                source_workspace_name=image_workspace_name,
                scan=scan,
                enhancement=enhancement,
                ocr=ocr,
                workspace_root=workspace.root,
            )
        except Exception as reportlab_exc:
            fallback = self._create_basic_image_pdf(
                pdf_path,
                image_path=image_path,
                image_workspace_name=image_workspace_name,
                enhancement=enhancement,
                error=reportlab_exc,
            )
            if str(fallback.get("status") or "") != "created":
                return fallback
            fallback["fallback_reason"] = f"professional_pdf_failed: {type(reportlab_exc).__name__}"
            return fallback
        pdf_workspace_name = str(pdf_path.relative_to(workspace.root))
        metadata = {
            "status": "created",
            "format": "scanner_style_pdf",
            "pdf_path": str(pdf_path),
            "pdf_workspace_name": pdf_workspace_name,
            "source_image_path": str(image_path),
            "source_workspace_name": image_workspace_name,
            "color_workspace_name": str(enhancement.get("color_workspace_name") or image_workspace_name),
            "ocr_workspace_name": str(enhancement.get("ocr_workspace_name") or ""),
            "scan_image_selection": image_selection,
            "resolution_dpi": 300,
            **pdf_info,
        }
        try:
            metadata_path = workspace.write_json(
                safe_filename(title, default="lamp_head_scan", suffix="_pdf_metadata.json"),
                metadata,
                action="scan.pdf_metadata",
            )
            metadata["metadata_path"] = str(metadata_path)
        except Exception:
            pass
        return metadata

    def _create_basic_image_pdf(
        self,
        pdf_path: Path,
        *,
        image_path: Path,
        image_workspace_name: str,
        enhancement: dict[str, object],
        error: Exception | None = None,
    ) -> dict[str, object]:
        try:
            from PIL import Image  # type: ignore
        except Exception as exc:
            return {
                "status": "backend_missing",
                "reason": "Pillow or ReportLab is required to write scan PDFs.",
                "source_workspace_name": image_workspace_name,
                "install_hint": "Install reportlab and pillow to enable PDF export.",
                "error": str(exc)[:500],
            }
        try:
            with Image.open(image_path) as image:
                image.convert("RGB").save(pdf_path, "PDF", resolution=300.0)
        except Exception as exc:
            return {
                "status": "error",
                "reason": "pdf_write_failed",
                "source_workspace_name": image_workspace_name,
                "error": str(exc)[:500],
            }
        workspace = self._workspace()
        pdf_workspace_name = str(pdf_path.relative_to(workspace.root)) if workspace is not None else pdf_path.name
        return {
            "status": "created",
            "format": "basic_image_pdf",
            "scanner_style": True,
            "content_mode": "scan_image_only",
            "pdf_path": str(pdf_path),
            "pdf_workspace_name": pdf_workspace_name,
            "source_image_path": str(image_path),
            "source_workspace_name": image_workspace_name,
            "source_image_workspace_name": image_workspace_name,
            "color_workspace_name": str(enhancement.get("color_workspace_name") or image_workspace_name),
            "ocr_workspace_name": str(enhancement.get("ocr_workspace_name") or ""),
            "scan_image_selection": _scan_pdf_image_selection(enhancement, image_workspace_name),
            "resolution_dpi": 300,
            "page_count": 1,
            "professional_layout": False,
            "boundary_detected": bool(enhancement.get("boundary_detected")),
            "document_region_detected": bool(enhancement.get("boundary_detected") or enhancement.get("document_region_detected")),
            "warning": str(error)[:500] if error is not None else "",
        }

    def _workspace(self) -> Any | None:
        if self.workspace is not None:
            return self.workspace
        for service in (self.scanning, self.camera_observer):
            workspace = getattr(service, "workspace", None)
            if workspace is not None:
                return workspace
        return None

    def _workspace_name_for_path(self, path: Path) -> str:
        workspace = self._workspace()
        if workspace is not None:
            try:
                return str(path.resolve().relative_to(workspace.root.resolve()))
            except ValueError:
                pass
        return str(path)

    def _running_follow_pid(self) -> int | None:
        try:
            pid = int(self.pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        if _pid_alive(pid):
            return pid
        self._clear_pid()
        return None

    def _clear_pid(self) -> None:
        try:
            self.pid_path.unlink()
        except FileNotFoundError:
            pass


def _walk_workspace_artifacts(value: object, add: Callable[[object], None], *, parent_key: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if _looks_like_artifact_key(key_text):
                if isinstance(child, list):
                    for item in child:
                        add(item)
                else:
                    add(child)
            if isinstance(child, (dict, list, tuple)):
                _walk_workspace_artifacts(child, add, parent_key=key_text)
        return
    if isinstance(value, (list, tuple)):
        if _looks_like_artifact_key(parent_key):
            for item in value:
                add(item)
        else:
            for child in value:
                if isinstance(child, (dict, list, tuple)):
                    _walk_workspace_artifacts(child, add, parent_key=parent_key)


def _looks_like_artifact_key(key: str) -> bool:
    normalized = key.lower()
    return (
        normalized in {"path", "paths", "image", "ocr_input", "workspace_name", "metadata_path"}
        or normalized.endswith("_path")
        or normalized.endswith("_paths")
        or normalized.endswith("_workspace_name")
    )


def _resolve_workspace_artifact(root: Path, workspace: Any, value: object) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > 512 or "\n" in text or "\r" in text:
        return None
    if text.startswith(("http://", "https://", "data:")):
        return None
    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        path = candidate.resolve()
    else:
        path = (root / text).resolve()
    try:
        if path.is_file() and path.is_relative_to(root):
            return path
    except ValueError:
        return None
    return None


def _dedupe_archive_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for index in range(2, 10000):
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    return parent / f"{stem}-{time.monotonic_ns()}{suffix}"


def _write_professional_scan_pdf(
    pdf_path: Path,
    *,
    title: str,
    image_path: Path,
    source_workspace_name: str,
    scan: dict[str, object],
    enhancement: dict[str, object],
    ocr: dict[str, object],
    workspace_root: Path,
) -> dict[str, object]:
    from reportlab.lib.pagesizes import A4  # type: ignore
    from reportlab.lib.units import mm  # type: ignore
    from reportlab.platypus import SimpleDocTemplate  # type: ignore

    page_width, page_height = A4
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=0,
        leftMargin=0,
        topMargin=0,
        bottomMargin=0,
        title=title,
        author="LeLamp",
        subject="LeLamp document scan image",
    )
    metrics = enhancement.get("metrics") if isinstance(enhancement.get("metrics"), dict) else {}
    boundary = enhancement.get("boundary") if isinstance(enhancement.get("boundary"), dict) else {}
    boundary_detected = bool(enhancement.get("boundary_detected") or boundary.get("detected"))
    document_region_detected = bool(boundary_detected or enhancement.get("document_region_detected") or boundary.get("document_region_detected"))
    ocr_text = _read_workspace_text(ocr.get("text_path"), workspace_root, max_chars=12000)
    if not ocr_text:
        ocr_text = str(ocr.get("preview") or "").strip()
    table_data = _normalize_pdf_tables(scan.get("tables") or ocr.get("tables"))

    story: list[object] = [_fit_pdf_image(image_path, page_width, page_height)]
    doc.build(story)
    return {
        "professional_layout": True,
        "scanner_style": True,
        "content_mode": "scan_image_only",
        "page_count": 1,
        "ocr_status": str(scan.get("ocr_status") or ocr.get("status") or ""),
        "boundary_detected": boundary_detected,
        "document_region_detected": document_region_detected,
        "source_image_workspace_name": source_workspace_name,
        "has_ocr_text": bool(ocr_text),
        "table_count": len(table_data),
        "quality_metrics": _quality_metric_payload(metrics, boundary),
    }


def _register_scan_pdf_fonts(pdfmetrics: Any, TTFont: Any, UnicodeCIDFont: Any) -> tuple[str, str]:
    candidates = [
        Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf"),
        Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont("LeLampScanRegular", str(path)))
            pdfmetrics.registerFont(TTFont("LeLampScanBold", str(path)))
            return "LeLampScanRegular", "LeLampScanBold"
        except Exception:
            continue
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light", "STSong-Light"
    except Exception:
        return "Helvetica", "Helvetica-Bold"


def _scan_pdf_styles(stylesheet: Any, regular_font: str, bold_font: str) -> dict[str, Any]:
    from reportlab.lib import colors  # type: ignore
    from reportlab.lib.styles import ParagraphStyle  # type: ignore
    from reportlab.lib.units import mm  # type: ignore

    return {
        "eyebrow": ParagraphStyle(
            "LeLampEyebrow",
            parent=stylesheet["Normal"],
            fontName=bold_font,
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#059669"),
            spaceAfter=3 * mm,
        ),
        "title": ParagraphStyle(
            "LeLampTitle",
            parent=stylesheet["Title"],
            fontName=bold_font,
            fontSize=27,
            leading=32,
            textColor=colors.HexColor("#071027"),
            spaceAfter=2 * mm,
        ),
        "subtitle": ParagraphStyle(
            "LeLampSubtitle",
            parent=stylesheet["Normal"],
            fontName=regular_font,
            fontSize=12,
            leading=16,
            textColor=colors.HexColor("#536273"),
        ),
        "section": ParagraphStyle(
            "LeLampSection",
            parent=stylesheet["Heading1"],
            fontName=bold_font,
            fontSize=19,
            leading=24,
            textColor=colors.HexColor("#071027"),
            spaceBefore=2 * mm,
            spaceAfter=5 * mm,
        ),
        "heading": ParagraphStyle(
            "LeLampHeading",
            parent=stylesheet["Heading2"],
            fontName=bold_font,
            fontSize=12,
            leading=16,
            textColor=colors.HexColor("#182335"),
            spaceBefore=3 * mm,
            spaceAfter=2 * mm,
        ),
        "body": ParagraphStyle(
            "LeLampBody",
            parent=stylesheet["BodyText"],
            fontName=regular_font,
            fontSize=10,
            leading=15,
            textColor=colors.HexColor("#1f2937"),
        ),
        "small": ParagraphStyle(
            "LeLampSmall",
            parent=stylesheet["BodyText"],
            fontName=regular_font,
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor("#334155"),
        ),
        "mono": ParagraphStyle(
            "LeLampMono",
            parent=stylesheet["BodyText"],
            fontName=regular_font,
            fontSize=8.4,
            leading=12,
            textColor=colors.HexColor("#111827"),
        ),
    }


def _metadata_table(rows: list[tuple[str, object]], styles: dict[str, Any], regular_font: str, colors: Any) -> Any:
    from reportlab.lib.units import mm  # type: ignore
    from reportlab.platypus import Paragraph, Table, TableStyle  # type: ignore

    data = [
        [
            Paragraph(_escape_pdf_text(str(label)), styles["small"]),
            Paragraph(_escape_pdf_text(_format_pdf_value(value)), styles["small"]),
        ]
        for label, value in rows
        if _format_pdf_value(value)
    ]
    if not data:
        data = [[Paragraph("状态", styles["small"]), Paragraph("暂无可用数据", styles["small"])]]
    table = Table(data, colWidths=[32 * mm, 126 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), regular_font),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#475569")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dbe4ee")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0dae6")),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _data_table(rows: list[list[str]], styles: dict[str, Any], regular_font: str, colors: Any) -> Any:
    from reportlab.lib.units import mm  # type: ignore
    from reportlab.platypus import Paragraph, Table, TableStyle  # type: ignore

    column_count = max(1, max(len(row) for row in rows))
    normalized = [(row + [""] * column_count)[:column_count] for row in rows[:30]]
    available_width = 170 * mm
    col_width = available_width / column_count
    data = [[Paragraph(_escape_pdf_text(cell), styles["small"]) for cell in row] for row in normalized]
    table = Table(data, colWidths=[col_width] * column_count, hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), regular_font),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f5ef")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#064e3b")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d7e2ec")),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _bullet_list(items: list[str], styles: dict[str, Any]) -> Any:
    from reportlab.lib.units import mm  # type: ignore
    from reportlab.platypus import ListFlowable, ListItem, Paragraph  # type: ignore

    return ListFlowable(
        [ListItem(Paragraph(_escape_pdf_text(item), styles["body"]), leftIndent=2 * mm) for item in items if item.strip()],
        bulletType="bullet",
        start="circle",
        leftIndent=6 * mm,
        bulletFontSize=6,
    )


def _fit_pdf_image(image_path: Path, max_width: float, max_height: float) -> Any:
    from reportlab.platypus import Image as PdfImage  # type: ignore
    from PIL import Image  # type: ignore

    with Image.open(image_path) as image:
        width, height = image.size
    scale = min(max_width / max(1, width), max_height / max(1, height))
    return PdfImage(str(image_path), width=width * scale, height=height * scale, kind="proportional")


def _image_metadata(image_path: Path) -> dict[str, str]:
    try:
        from PIL import Image  # type: ignore

        with Image.open(image_path) as image:
            width, height = image.size
            mode = image.mode
        return {"size_label": f"{width} x {height}px · {mode}"}
    except Exception:
        return {"size_label": ""}


def _quality_rows(metrics: dict[str, object], boundary: dict[str, object]) -> list[tuple[str, object]]:
    rows = [
        ("边界置信度", _round_metric(boundary.get("confidence"))),
        ("亮度", _round_metric(metrics.get("brightness"))),
        ("对比度", _round_metric(metrics.get("contrast"))),
        ("清晰度", _round_metric(metrics.get("sharpness_laplacian"))),
        ("边缘密度", _round_metric(metrics.get("edge_density"))),
    ]
    return rows


def _quality_metric_payload(metrics: dict[str, object], boundary: dict[str, object]) -> dict[str, object]:
    return {
        "boundary_confidence": _round_metric(boundary.get("confidence")),
        "brightness": _round_metric(metrics.get("brightness")),
        "contrast": _round_metric(metrics.get("contrast")),
        "sharpness_laplacian": _round_metric(metrics.get("sharpness_laplacian")),
        "edge_density": _round_metric(metrics.get("edge_density")),
    }


def _entity_rows(entities: dict[str, object]) -> list[tuple[str, object]]:
    labels = {
        "emails": "邮箱",
        "phones": "电话",
        "dates": "日期",
        "amounts": "金额",
        "people": "人物",
        "organization": "组织",
        "organizations": "组织",
        "academic_info": "学术信息",
        "thesis_title": "论文题目",
        "degree": "学位",
        "field_of_study": "学科专业",
        "specialty": "学位类别",
    }
    rows: list[tuple[str, object]] = []
    for key, value in entities.items():
        formatted = _format_entity_value(value)
        if formatted:
            rows.append((labels.get(str(key), str(key)), formatted))
    return rows


def _format_entity_value(value: object) -> str:
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            formatted = _format_entity_value(item)
            if formatted:
                parts.append(f"{key}: {formatted}")
        return "；".join(parts)
    if isinstance(value, list):
        parts = [_format_entity_value(item) for item in value[:12]]
        return "；".join(part for part in parts if part)
    text = str(value).strip()
    return text if text and text.lower() not in {"none", "null"} else ""


def _normalize_pdf_tables(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    tables: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict):
            tables.append(item)
    return tables


def _table_rows(headers: object, rows: object) -> list[list[str]]:
    normalized: list[list[str]] = []
    if isinstance(headers, list) and headers:
        normalized.append([str(item) for item in headers])
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, list):
                normalized.append([str(item) for item in row])
            elif isinstance(row, dict):
                keys = list(row.keys())
                if not normalized:
                    normalized.append([str(key) for key in keys])
                normalized.append([str(row.get(key, "")) for key in keys])
    return normalized


def _read_workspace_text(path_value: object, workspace_root: Path, *, max_chars: int) -> str:
    if not path_value:
        return ""
    try:
        path = Path(str(path_value)).expanduser().resolve()
        if not path.is_relative_to(workspace_root.resolve()) or not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text[:max_chars]


def _paragraph_chunks(text: str, *, max_chars: int) -> list[str]:
    cleaned = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not cleaned:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in re.split(r"\n{2,}", cleaned):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and current_len + len(paragraph) > max_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        if len(paragraph) > max_chars:
            chunks.extend(textwrap.wrap(paragraph, width=max_chars, break_long_words=False, replace_whitespace=False))
            continue
        current.append(paragraph)
        current_len += len(paragraph)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _scan_pdf_title(title: str) -> str:
    text = title.replace("_", " ").strip()
    return text or "LeLamp Scan"


def _boundary_label(enhancement: dict[str, object], boundary: dict[str, object]) -> str:
    detected = bool(enhancement.get("boundary_detected") or boundary.get("detected"))
    document_region_detected = bool(detected or enhancement.get("document_region_detected") or boundary.get("document_region_detected"))
    confidence = _round_metric(boundary.get("confidence"))
    if detected:
        return f"已识别 · confidence={confidence}" if confidence != "" else "已识别"
    if document_region_detected:
        return f"纸面区域兜底 · confidence={confidence}" if confidence != "" else "纸面区域兜底"
    reason = str(boundary.get("reason") or "").strip()
    return f"未稳定识别 · {reason}" if reason else "未稳定识别"


def _scan_pdf_type_label(boundary_detected: bool, document_region_detected: bool) -> str:
    if boundary_detected:
        return "全页扫描 PDF"
    if document_region_detected:
        return "纸面区域扫描 PDF"
    return "原始拍照确认 PDF"


def _scan_pdf_image_selection(enhancement: dict[str, object], fallback_workspace_name: str) -> dict[str, object]:
    boundary_detected = bool(enhancement.get("boundary_detected"))
    document_region_detected = bool(boundary_detected or enhancement.get("document_region_detected"))
    if document_region_detected:
        workspace_name = str(
            enhancement.get("color_workspace_name")
            or enhancement.get("enhanced_workspace_name")
            or fallback_workspace_name
        )
        return {
            "workspace_name": workspace_name,
            "mode": "enhanced_document" if boundary_detected else "fallback_document_region",
            "reason": "document_boundary_detected" if boundary_detected else "paper_region_fallback_detected",
        }
    return {
        "workspace_name": fallback_workspace_name,
        "mode": "original_image",
        "reason": "document_boundary_not_detected",
    }


def _round_metric(value: object) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return ""


def _format_pdf_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, (list, tuple)):
        return "；".join(str(item) for item in value if str(item).strip())
    return str(value)


def _list_of_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _escape_pdf_text(value: object) -> str:
    import html

    pieces: list[str] = []
    for part in re.split(r"(\n)", str(value)):
        if part == "\n":
            pieces.append("<br/>")
            continue
        for match in re.finditer(r"[\x20-\x7e]+|[^\x20-\x7e]+", part):
            raw = match.group(0)
            text = html.escape(raw)
            if raw.isascii():
                pieces.append(f'<font name="Helvetica">{text}</font>')
            else:
                pieces.append(text)
    return "".join(pieces)


def parse_lamp_voice_command(text: str) -> LampVoiceCommand | None:
    normalized = _normalize(text)
    if not normalized:
        return None
    command_config = load_lamp_voice_command_config()
    negation = command_config.get("negation") if isinstance(command_config.get("negation"), dict) else {}
    allowed_negations = _markers_from_config(negation.get("allowed_markers"), ("停止跟随", "停止扫描", "别跟", "不要跟", "不跟"))
    if _is_negated(normalized, command_config) and not _contains_any(normalized, allowed_negations):
        return None

    configured_command = _parse_configured_command(normalized, command_config)
    if configured_command is not None:
        return configured_command

    color = _parse_color(normalized, command_config)
    if color is not None:
        label, rgb, reply = color
        return LampVoiceCommand("set_rgb", label, reply, rgb=rgb)

    recording = _parse_recording(normalized, command_config)
    if recording is not None:
        label, name, reply = recording
        return LampVoiceCommand("play", label, reply, recording=name)

    return None


def _normalize(text: str) -> str:
    return (
        text.lower()
        .replace(" ", "")
        .replace("，", "")
        .replace("。", "")
        .replace(",", "")
        .replace(".", "")
        .replace("！", "")
        .replace("!", "")
    )


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _is_negated(text: str, command_config: dict[str, object] | None = None) -> bool:
    command_config = command_config or load_lamp_voice_command_config()
    negation = command_config.get("negation") if isinstance(command_config.get("negation"), dict) else {}
    markers = _markers_from_config(negation.get("markers"), ("不要", "别", "先不", "不用", "不要执行"))
    return _contains_any(text, markers)


def _parse_color(text: str, command_config: dict[str, object] | None = None) -> tuple[str, tuple[int, int, int], str] | None:
    command_config = command_config or load_lamp_voice_command_config()
    if not _contains_any(text, ("灯", "光", "颜色", "台灯")):
        return None
    for item in _list_config_items(command_config.get("colors")):
        markers = _markers_from_config(item.get("markers"))
        if _contains_any(text, markers):
            rgb = _rgb_from_config(item.get("rgb"))
            if rgb is None:
                continue
            return str(item.get("label") or "color"), rgb, str(item.get("reply") or "已切换灯光。")
    return None


def _parse_recording(text: str, command_config: dict[str, object] | None = None) -> tuple[str, str, str] | None:
    command_config = command_config or load_lamp_voice_command_config()
    for item in _list_config_items(command_config.get("recordings")):
        markers = _markers_from_config(item.get("markers"))
        if _contains_any(text, markers):
            label = str(item.get("label") or "recording")
            recording = str(item.get("recording") or label)
            recording = get_action_recording(load_motion_config(), recording) or recording
            return label, recording, str(item.get("reply") or "已执行动作。")
    return None


def _parse_configured_command(text: str, command_config: dict[str, object]) -> LampVoiceCommand | None:
    for item in _list_config_items(command_config.get("commands")):
        markers = _markers_from_config(item.get("markers"))
        if not _contains_any(text, markers):
            continue
        action = str(item.get("action") or "")
        if action not in {
            "set_rgb",
            "play",
            "start_follow",
            "stop_follow",
            "default_state",
            "scan_pose",
            "scan_pdf",
            "project",
            "relax_motors",
            "status",
        }:
            continue
        rgb = _rgb_from_config(item.get("rgb"))
        return LampVoiceCommand(
            action,  # type: ignore[arg-type]
            str(item.get("label") or action),
            str(item.get("reply") or "已执行台灯命令。"),
            rgb=rgb,
            recording=str(item.get("recording")) if item.get("recording") else None,
        )
    return None


def load_lamp_voice_command_config() -> dict[str, object]:
    path = _lamp_voice_command_config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        if path != VOICE_COMMANDS_FILE:
            try:
                payload = json.loads(VOICE_COMMANDS_FILE.read_text(encoding="utf-8"))
            except Exception:
                return {}
        else:
            return {}
    return payload if isinstance(payload, dict) else {}


def _lamp_voice_command_config_path() -> Path:
    configured = os.getenv("LELAMP_VOICE_COMMANDS_FILE", "").strip()
    return Path(configured).expanduser() if configured else VOICE_COMMANDS_FILE


def _list_config_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _markers_from_config(value: object, fallback: tuple[str, ...] = ()) -> tuple[str, ...]:
    if not isinstance(value, list):
        return fallback
    markers = tuple(_normalize(str(item)) for item in value if str(item).strip())
    return markers or fallback


def _rgb_from_config(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    try:
        rgb = tuple(max(0, min(255, int(channel))) for channel in value)
    except (TypeError, ValueError):
        return None
    return rgb  # type: ignore[return-value]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
