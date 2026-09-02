from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..llm import LLMError, ResponsesLLM, ResponsesLLMConfig
from ..projection import redact_projection_text
from ..projection_viewer import ProjectionPreviewServer
from ..shared_space import find_lan_ip
from ..utils import safe_filename
from ..routes._base import ApiError, RequestContext

def _helper(name:str):
    from .. import web_console
    return getattr(web_console,name)
def atomic_write_bytes(*a,**kw): return _helper("atomic_write_bytes")(*a,**kw)
def payload_bool(*a,**kw): return _helper("payload_bool")(*a,**kw)
def pid_alive(*a,**kw): return _helper("pid_alive")(*a,**kw)
def process_cmdline(*a,**kw): return _helper("process_cmdline")(*a,**kw)
def safe_int(*a,**kw): return _helper("safe_int")(*a,**kw)

class MediaRuntimeMixin:
    def build_pptx_session_payload(
        self,
        *,
        title: str,
        safe: SafePath,
        slides: list[dict[str, object]],
        slide_index: int,
        projection: dict[str, str] | None,
        status: str,
    ) -> dict[str, object]:
        slide_count = len(slides)
        if slide_count:
            slide_index = max(1, min(slide_count, slide_index))
            current_slide = slides[slide_index - 1]
        else:
            slide_index = 0
            current_slide = {}
        output: dict[str, object] = {
            "status": status,
            "title": redact_projection_text(title),
            "source_workspace_name": safe.workspace_name,
            "source_path": str(safe.path),
            "slide_index": slide_index,
            "slide_count": slide_count,
            "current_slide": current_slide,
            "slides": slides,
            "preview_url": self._projection_preview_url,
            "message": "PPTX text slides are ready for projection." if status == "ready" else "PPTX slide rendered to projection output.",
        }
        if projection:
            output.update({"projection": projection, "path": projection.get("path"), "projection_path": projection.get("path"), "mode": projection.get("mode")})
        return output

    def projection_display_profile_path(self) -> Path:
        return (self.runtime.config.projection_dir / "display_profile.json").resolve()

    def _complete_ppt_page_summary(self, prompt: str, image_data_url: str, payload: dict[str, Any]) -> tuple[str, str, str]:
        instructions = "你是严谨的演示助手。只基于用户主动捕获的 PPT 当前页截图进行总结。"
        context = {"task": "summarize_current_ppt_page", "source": payload.get("source") or "browser_capture"}
        errors: list[str] = []
        if self.runtime.config.openai_api_key:
            model = self.runtime.config.openai_vision_model
            try:
                summary = ResponsesLLM(
                    ResponsesLLMConfig(
                        api_key=self.runtime.config.openai_api_key,
                        base_url=self.runtime.config.openai_base_url,
                        model=model,
                        reasoning_effort="low",
                    )
                ).complete_multimodal(
                    instructions=instructions,
                    text=prompt,
                    image_data_url=image_data_url,
                    context=context,
                    timeout=120,
                )
                return summary, "OpenAI-compatible vision", model
            except LLMError as exc:
                errors.append(f"openai_compatible={str(exc)[:500]}")
        if getattr(self.runtime.config, "dashscope_api_key", ""):
            model = str(getattr(self.runtime.config, "dashscope_vision_model", "qwen-vl-plus") or "qwen-vl-plus")
            try:
                summary = ResponsesLLM(
                    ResponsesLLMConfig(
                        api_key=self.runtime.config.dashscope_api_key,
                        base_url=str(getattr(self.runtime.config, "dashscope_vision_base_url", "https://dashscope.aliyuncs.com/compatible-mode")),
                        model=model,
                        reasoning_effort="low",
                        wire_api=str(getattr(self.runtime.config, "dashscope_vision_wire_api", "chat_completions") or "chat_completions"),
                    )
                ).complete_multimodal(
                    instructions=instructions,
                    text=prompt,
                    image_data_url=image_data_url,
                    context={**context, "fallback_after": errors[-1] if errors else ""},
                    timeout=120,
                )
                return summary, "DashScope Qwen-VL", model
            except LLMError as exc:
                errors.append(f"dashscope_qwen_vl={str(exc)[:500]}")
        raise LLMError("; ".join(errors) or "No vision API provider is configured.")

    def write_ppt_page_capture(self, image_data_url: str, title: str) -> Path:
        match = re.match(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$", image_data_url)
        if not match:
            raise ApiError("invalid_image", "Expected a PNG, JPEG, or WebP data URL.", status=400)
        mime_type, raw_base64 = match.groups()
        import base64

        try:
            data = base64.b64decode("".join(raw_base64.split()), validate=True)
        except Exception as exc:
            raise ApiError("invalid_image", "Image data URL is not valid base64.", status=400) from exc
        if len(data) > self.max_upload_bytes:
            raise ApiError("image_too_large", f"Image exceeds upload limit of {self.max_upload_bytes} bytes.", status=413)
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime_type]
        path = self.runtime.workspace.path_for_new_file(safe_filename(title, default="ppt_page", suffix=f"_capture{extension}"))
        atomic_write_bytes(path, data)
        self.audit.record("projection.ppt_page_capture_write", target=str(path), details={"bytes": len(data), "mime_type": mime_type})
        return path

    def camera_stream_running(self) -> bool:
        with self._camera_stream_lock:
            process = self._camera_stream_process
            if process is not None and process.poll() is not None:
                self._camera_stream_process = None
                self._camera_stream_started_at = None
                self._camera_stream_camera_index = None
                process = None
        if process is not None:
            return self.camera_stream_healthcheck()
        if self.camera_stream_healthcheck():
            return True
        return False

    def camera_stream_healthcheck(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self._camera_stream_url}status.json", timeout=1.0) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError, TimeoutError):
            return False

    def start_camera_stream_service(
        self,
        *,
        camera_index: Any = None,
        width: int = 1280,
        height: int = 720,
        backend: str = "auto",
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        backend = backend if backend in {"auto", "face", "hog", "yolo"} else "auto"
        resolved_camera_index = self.resolve_camera_index(camera_index)
        with self._camera_stream_lock:
            existing = self._camera_stream_process
            if existing is not None and existing.poll() is None:
                if self._camera_stream_camera_index != resolved_camera_index:
                    existing.terminate()
                    try:
                        existing.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        existing.kill()
                    self._camera_stream_process = None
                    self._camera_stream_started_at = None
                    self._camera_stream_camera_index = None
                else:
                    current = self.api_camera_stream_status(ctx)
                    if str(current.get("status")) == "error":
                        existing.terminate()
                        try:
                            existing.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            existing.kill()
                        self._camera_stream_process = None
                        self._camera_stream_started_at = None
                        self._camera_stream_camera_index = None
                    else:
                        return {
                            **current,
                            "status": "starting" if not self.camera_stream_healthcheck() else current.get("status", "online"),
                            "message": "Camera preview is already starting.",
                        }
            elif self.camera_stream_running():
                current = self.api_camera_stream_status(ctx)
                if safe_int(current.get("camera_index"), -1) != resolved_camera_index:
                    self.stop_external_camera_stream_processes()
                    self._camera_stream_started_at = None
                    self._camera_stream_camera_index = None
                else:
                    return {
                        **current,
                        "status": "online",
                        "message": "Camera preview is already running.",
                    }

            backend = backend if backend in {"auto", "face", "hog", "yolo"} else "auto"
            command = [
                sys.executable,
                "-u",
                "-m",
                "lelamp.camera_stream",
                "--host",
                "0.0.0.0",
                "--port",
                str(self.camera_stream_port),
                "--camera-index",
                str(resolved_camera_index),
                "--backend",
                backend,
                "--width",
                str(max(320, min(3840, width))),
                "--height",
                str(max(240, min(2160, height))),
            ]
            log_path = self.runtime.config.workspace_dir / ".camera_stream.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                log_file = log_path.open("ab")
                process = self.processes.spawn(
                    command,
                    cwd=str(_helper("runtime_root")()),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
            except OSError as exc:
                return {
                    "status": "unavailable",
                    "preview_url": self._camera_stream_url,
                    "message": f"Camera preview could not start: {exc}",
                }
            self._camera_stream_process = process
            self._camera_stream_started_at = time.time()
            self._camera_stream_camera_index = resolved_camera_index

        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            current = self.api_camera_stream_status(ctx)
            if str(current.get("status")) in {"online", "error", "failed", "blocked", "unavailable"}:
                break
            time.sleep(0.2)
        current = self.api_camera_stream_status(ctx)
        if str(current.get("status")) == "online":
            current["message"] = "Camera preview started; open it in the browser."
        return current

    def stop_camera_stream_service(self, ctx: RequestContext | None = None) -> dict[str, object]:
        with self._camera_stream_lock:
            process = self._camera_stream_process
            self._camera_stream_process = None
            self._camera_stream_started_at = None
            self._camera_stream_camera_index = None
        if process is not None and process.poll() is None:
            self.processes.stop(process)
        else:
            self.stop_external_camera_stream_processes()
        return {
            "status": "stopped",
            "preview_url": self._camera_stream_url,
            "browser_preview_url": self.camera_stream_browser_url(ctx),
            "always_on": False,
            "message": "Camera preview stopped.",
        }

    def stop_external_camera_stream_processes(self) -> None:
        try:
            completed = subprocess.run(
                ["pgrep", "-f", r"lelamp\.camera_stream"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        current_pid = os.getpid()
        for raw_pid in completed.stdout.split():
            try:
                pid = int(raw_pid)
            except ValueError:
                continue
            if pid == current_pid:
                continue
            try:
                os.kill(pid, 15)
            except OSError:
                continue

    def camera_stream_browser_url(self, ctx: RequestContext | None = None) -> str:
        configured = os.getenv("LELAMP_CAMERA_BROWSER_URL", "").strip()
        if configured:
            return configured.rstrip("/") + "/"
        host = (ctx.host if ctx else "") or ""
        if host:
            web_host = host.rsplit("@", 1)[-1].split(":", 1)[0]
            if web_host:
                proto = (ctx.forwarded_proto if ctx else "") or "http"
                return f"{proto}://{web_host}:{self.camera_stream_port}/"
        return self._camera_stream_url

    def resolve_camera_index(self, value: Any = None, default: int = 0) -> int:
        if value not in (None, ""):
            return max(0, safe_int(value, default))
        configured = os.getenv("LELAMP_CAMERA_INDEX", "").strip() or os.getenv("LELAMP_CAMERA_STREAM_INDEX", "").strip()
        if configured:
            return max(0, safe_int(configured, default))
        detected = self.detect_available_camera_index()
        return detected if detected is not None else default

    def detect_available_camera_index(self) -> int | None:
        device_indices: list[int] = []
        for device in sorted(Path("/dev").glob("video*")):
            match = re.fullmatch(r"video(\d+)", device.name)
            if match:
                device_indices.append(safe_int(match.group(1), 0))
        candidates = sorted(set(device_indices + list(range(0, 6))))
        try:
            import cv2
        except Exception:
            return device_indices[0] if device_indices else None

        for index in candidates:
            camera = cv2.VideoCapture(index)
            try:
                if not camera.isOpened():
                    continue
                ok, frame = camera.read()
                if ok and frame is not None:
                    return index
            finally:
                camera.release()
        return device_indices[0] if device_indices else None

    def scene_camera_rotation_degrees(self, camera_index: int, payload: dict[str, Any]) -> int:
        if "rotation_degrees" in payload:
            return 180 if safe_int(payload.get("rotation_degrees"), 0) % 360 == 180 else 0
        if camera_index == 0 and payload_bool(payload.get("cam0_rotate_180"), default=True):
            return 180
        return 0

    def projection_preview_running(self) -> bool:
        with self._projection_preview_lock:
            if self._projection_preview_httpd is None or self._projection_preview_thread is None:
                return False
            if not self._projection_preview_thread.is_alive():
                self._projection_preview_httpd = None
                self._projection_preview_thread = None
                self._projection_preview_started_at = None
                return False
        try:
            with urllib.request.urlopen(f"{self._projection_preview_url}health", timeout=1.0) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError, TimeoutError):
            return False

    def projection_kiosk_running(self) -> bool:
        process = self._projection_kiosk_process
        if process is not None:
            if process.poll() is None:
                return True
            self._projection_kiosk_process = None
            self._projection_kiosk_started_at = None
        return self.find_projection_kiosk_pid() is not None

    def find_projection_kiosk_pid(self) -> int | None:
        try:
            completed = subprocess.run(
                ["pgrep", "-f", r"chromium.*lelamp-projection-kiosk-profile|chromium.*--app=.*8765"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        current_pid = os.getpid()
        for raw_pid in completed.stdout.split():
            try:
                pid = int(raw_pid)
            except ValueError:
                continue
            if pid == current_pid:
                continue
            cmdline = " ".join(process_cmdline(pid))
            if "chromium" in cmdline and "--app=" in cmdline and ("lelamp-projection-kiosk-profile" in cmdline or self._projection_preview_url in cmdline or f":{self.projection_preview_port}/" in cmdline):
                return pid
        return None

    def start_projection_kiosk(self) -> dict[str, object]:
        if self.projection_kiosk_running():
            return {
                "status": "online",
                "preview_url": self._projection_preview_url,
                "kiosk_running": True,
                "kiosk_pid": self._projection_kiosk_process.pid if self._projection_kiosk_process and self._projection_kiosk_process.poll() is None else self.find_projection_kiosk_pid(),
                "message": "Projection kiosk is already running.",
            }
        chromium = shutil.which("chromium") or "/usr/lib/chromium/chromium"
        if Path("/usr/lib/chromium/chromium").is_file():
            chromium = "/usr/lib/chromium/chromium"
        if not chromium or not Path(chromium).exists():
            return {
                "status": "unavailable",
                "preview_url": self._projection_preview_url,
                "kiosk_running": False,
                "message": "Chromium is not available for projection kiosk.",
            }
        profile_dir = Path(os.getenv("LELAMP_PROJECTION_KIOSK_PROFILE", "/tmp/lelamp-projection-kiosk-profile"))
        profile_dir.mkdir(parents=True, exist_ok=True)
        self._projection_kiosk_log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("WAYLAND_DISPLAY", "wayland-0")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
        env.setdefault("XDG_SESSION_TYPE", "wayland")
        env.setdefault("XDG_SESSION_DESKTOP", "rpd-labwc")
        env.setdefault("XDG_CURRENT_DESKTOP", "labwc")
        command = [
            chromium,
            "--ozone-platform=wayland",
            "--password-store=basic",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--disable-session-crashed-bubble",
            "--disable-features=TranslateUI,AutofillServerCommunication",
            "--kiosk",
            f"--app={self._projection_preview_url}",
        ]
        try:
            log_file = self._projection_kiosk_log_path.open("ab")
            process = subprocess.Popen(
                command,
                cwd=str(_helper("runtime_root")()),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
        except OSError as exc:
            return {
                "status": "unavailable",
                "preview_url": self._projection_preview_url,
                "kiosk_running": False,
                "message": f"Projection kiosk could not start: {exc}",
            }
        self._projection_kiosk_process = process
        self._projection_kiosk_started_at = time.time()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.2)
        running = process.poll() is None
        return {
            "status": "online" if running else "failed",
            "preview_url": self._projection_preview_url,
            "kiosk_running": running,
            "kiosk_pid": process.pid if running else None,
            "kiosk_log_path": str(self._projection_kiosk_log_path),
            "message": "Projection kiosk started." if running else "Projection kiosk exited immediately.",
        }

    def stop_projection_kiosk(self) -> dict[str, object]:
        process = self._projection_kiosk_process
        self._projection_kiosk_process = None
        self._projection_kiosk_started_at = None
        pids: list[int] = []
        if process is not None and process.poll() is None:
            pids.append(process.pid)
        external_pid = self.find_projection_kiosk_pid()
        if external_pid is not None and external_pid not in pids:
            pids.append(external_pid)
        for pid in pids:
            try:
                os.kill(pid, 15)
            except OSError:
                continue
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(pid_alive(pid) for pid in pids):
            time.sleep(0.2)
        for pid in pids:
            if pid_alive(pid):
                try:
                    os.kill(pid, 9)
                except OSError:
                    continue
        return {
            "status": "stopped",
            "preview_url": self._projection_preview_url,
            "kiosk_running": False,
            "message": "Projection kiosk stopped.",
        }

    def start_projection_preview_service(self) -> dict[str, object]:
        with self._projection_preview_lock:
            if self._projection_preview_httpd is not None and self._projection_preview_thread is not None:
                if self._projection_preview_thread.is_alive():
                    kiosk = self.start_projection_kiosk()
                    return {
                        **self.api_projection_service_status(),
                        "status": "online",
                        "kiosk": kiosk,
                        "message": "投影服务已运行，已确认全屏投影窗口。",
                    }
                self._projection_preview_httpd = None
                self._projection_preview_thread = None
                self._projection_preview_started_at = None

            self.runtime.config.projection_dir.mkdir(parents=True, exist_ok=True)
            preview = ProjectionPreviewServer(
                self.runtime.config.projection_dir,
                self.audit,
                refresh_seconds=2,
                display_profile_path=self.projection_display_profile_path(),
            )
            try:
                httpd = ThreadingHTTPServer(("0.0.0.0", self.projection_preview_port), preview.make_handler())
            except OSError as exc:
                return {
                    "status": "unavailable",
                    "preview_url": self._projection_preview_url,
                    "display_test_mode": True,
                    "physical_projector": "display_substitute",
                    "output_target": "external_monitor",
                    "message": f"Projection preview port {self.projection_preview_port} is unavailable: {exc}",
                }

            bound_host, bound_port = httpd.server_address[:2]
            preview_host = find_lan_ip() if bound_host in {"0.0.0.0", ""} else bound_host
            self._projection_preview_url = f"http://{preview_host or '127.0.0.1'}:{bound_port}/"

            def serve_preview() -> None:
                try:
                    httpd.serve_forever(poll_interval=0.5)
                finally:
                    httpd.server_close()

            thread = threading.Thread(target=serve_preview, name="openclaw-projection-preview", daemon=True)
            self._projection_preview_httpd = httpd
            self._projection_preview_thread = thread
            self._projection_preview_started_at = time.time()
            thread.start()

        kiosk = self.start_projection_kiosk()
        status = self.api_projection_service_status()
        return {
            **status,
            "status": "online" if str(kiosk.get("status")) == "online" or status.get("output_target") != "projector" else "adapter_ready",
            "kiosk": kiosk,
            "message": "投影预览服务已启动，已打开全屏投影窗口。" if str(kiosk.get("status")) == "online" else str(kiosk.get("message") or status.get("message") or ""),
        }

    def stop_projection_preview_service(self) -> dict[str, object]:
        kiosk = self.stop_projection_kiosk()
        with self._projection_preview_lock:
            httpd = self._projection_preview_httpd
            thread = self._projection_preview_thread
            if httpd is None or thread is None:
                self._projection_preview_started_at = None
                return {
                    "status": "stopped",
                    "preview_url": self._projection_preview_url,
                    "display_test_mode": True,
                    "physical_projector": "display_substitute",
                    "output_target": "external_monitor",
                    "kiosk": kiosk,
                    "message": "投影预览服务未运行，全屏投影窗口已关闭。",
                }
            self._projection_preview_httpd = None
            self._projection_preview_thread = None
            self._projection_preview_started_at = None

        httpd.shutdown()
        thread.join(timeout=2.0)
        return {
            "status": "stopped",
            "preview_url": self._projection_preview_url,
            "display_test_mode": True,
            "physical_projector": "display_substitute",
            "output_target": "external_monitor",
            "kiosk": kiosk,
            "message": "投影预览服务和全屏投影窗口已停止。",
        }
