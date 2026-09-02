from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime
from pathlib import Path
from typing import Any

from ..audio_api import OpenAIAudioAPI
from ..dashscope_asr import DashScopeASR
from ..groq_asr import GroqASR
from ..hardware_probe import probe_hardware, record_microphone_sample
from ..routes._base import ApiError, RequestContext
from ..utils import safe_filename


def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)


def atomic_write_bytes(*args, **kwargs): return _helper("atomic_write_bytes")(*args, **kwargs)
def hardware_device_details(*args, **kwargs): return _helper("hardware_device_details")(*args, **kwargs)
def normalize_task_status(*args, **kwargs): return _helper("normalize_task_status")(*args, **kwargs)
def safe_int(*args, **kwargs): return _helper("safe_int")(*args, **kwargs)
def split_wave_channels(*args, **kwargs): return _helper("split_wave_channels")(*args, **kwargs)
def workspace_name_for_path(*args, **kwargs): return _helper("workspace_name_for_path")(*args, **kwargs)


class SceneCaptureMixin:
    def console_scene_image_url(self, workspace_name: str) -> str:
        query = urllib.parse.urlencode({"token": self.token, "file": workspace_name})
        return f"/api/scene/image?{query}"

    def capture_scene_stereo_transcript(self, *, seconds: int) -> dict[str, object]:
        output = self.runtime.workspace.path_for_new_file("scene_ambient_audio.wav")
        capture = self.record_stereo_microphone_sample(seconds=seconds, output=output)
        if str(capture.get("status") or "") != "completed":
            return {**capture, "transcripts": []}

        channels = split_wave_channels(output)
        transcripts: list[dict[str, object]] = []
        for channel in channels:
            channel_path = channel.get("path")
            if not isinstance(channel_path, Path):
                continue
            transcript_item = {
                "channel": channel.get("channel"),
                "label": channel.get("label"),
                "status": "pending",
                "text": "",
                "audio_workspace_name": workspace_name_for_path(self.runtime.workspace.root, channel_path),
                "rms": channel.get("rms"),
                "peak": channel.get("peak"),
                "duration_seconds": channel.get("duration_seconds"),
            }
            if safe_int(channel.get("rms"), 0) < 40 and safe_int(channel.get("peak"), 0) < 250:
                transcript_item["status"] = "silence"
                transcript_item["message"] = "未检测到明显声音。"
            else:
                try:
                    text = self.transcribe_scene_audio(channel_path)
                    transcript_item["text"] = text
                    transcript_item["status"] = "completed" if text else "empty"
                except Exception as exc:
                    transcript_item["status"] = "backend_missing"
                    transcript_item["message"] = str(exc)[:500]
            transcripts.append(transcript_item)

        return {
            **capture,
            "audio_workspace_name": workspace_name_for_path(self.runtime.workspace.root, output),
            "channels": [
                {key: value for key, value in channel.items() if key != "path"}
                for channel in channels
            ],
            "transcripts": transcripts,
        }

    def record_stereo_microphone_sample(self, *, seconds: int, output: Path) -> dict[str, object]:
        if shutil.which("arecord") is None:
            return {"status": "backend_missing", "message": "arecord not found", "output_path": str(output)}
        try:
            from ..hardware_probe import resolve_capture_device, trim, wave_metrics

            selected_device = resolve_capture_device(self.runtime.config.mic_device)
        except Exception as exc:
            return {
                "status": "unavailable",
                "message": str(exc),
                "configured_device": self.runtime.config.mic_device,
                "selected_device": "",
                "output_path": str(output),
            }

        seconds = max(1, min(10, int(seconds)))
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "arecord",
            "-D",
            selected_device,
            "-f",
            "S16_LE",
            "-c",
            "2",
            "-r",
            str(self.runtime.config.mic_rate),
            "-d",
            str(seconds),
            str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=seconds + 8)
        if result.returncode != 0:
            fallback_command = [
                "arecord",
                "-D",
                selected_device,
                "-f",
                "S16_LE",
                "-c",
                "1",
                "-r",
                str(self.runtime.config.mic_rate),
                "-d",
                str(seconds),
                str(output),
            ]
            fallback = subprocess.run(fallback_command, capture_output=True, text=True, check=False, timeout=seconds + 8)
            if fallback.returncode != 0:
                return {
                    "status": "unavailable",
                    "command": command,
                    "fallback_command": fallback_command,
                    "output_path": str(output),
                    "configured_device": self.runtime.config.mic_device,
                    "selected_device": selected_device,
                    "stderr": trim(result.stderr),
                    "fallback_stderr": trim(fallback.stderr),
                    "stdout": trim(result.stdout),
                }
            command = fallback_command

        metrics = wave_metrics(output)
        with wave.open(str(output), "rb") as stream:
            channel_count = stream.getnchannels()
            sample_rate = stream.getframerate()
        return {
            "status": "completed",
            "command": command,
            "output_path": str(output),
            "configured_device": self.runtime.config.mic_device,
            "selected_device": selected_device,
            "channel_count": channel_count,
            "sample_rate": sample_rate,
            **metrics,
        }

    def transcribe_scene_audio(self, audio_path: Path) -> str:
        config = self.runtime.config
        provider = config.asr_provider
        if provider == "dashscope":
            return DashScopeASR(
                api_key=config.dashscope_api_key,
                model=config.dashscope_asr_model,
                sample_rate=config.mic_rate,
            ).transcribe(audio_path, language_hints=["zh", "en"])
        if provider == "groq":
            return GroqASR(api_key=config.groq_api_key).transcribe(audio_path, model=config.asr_model, language="zh")
        if provider == "openai":
            return OpenAIAudioAPI(api_key=config.openai_api_key, base_url=config.openai_base_url).transcribe(
                audio_path,
                model=config.asr_model,
                language="zh",
            )
        raise RuntimeError(f"Unsupported ASR provider: {provider}")

    def capture_scene_camera_snapshot(self, title: str, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        camera_index = self.resolve_camera_index(payload.get("camera_index"))
        rotation_degrees = self.scene_camera_rotation_degrees(camera_index, payload)
        timeout_seconds = max(3, min(12, safe_int(payload.get("timeout_seconds"), 6)))
        capture = self.capture_from_camera_preview_snapshot(title, camera_index=camera_index, rotation_degrees=rotation_degrees)
        if str(capture.get("status") or "") != "captured":
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(self.runtime.camera_observer.capture_frame, camera_index=camera_index, rotation_degrees=rotation_degrees)
            try:
                capture = future.result(timeout=timeout_seconds)
            except FutureTimeoutError:
                future.cancel()
                capture = {
                    "status": "unavailable",
                    "message": f"设备相机拍照超过 {timeout_seconds} 秒未返回。",
                    "camera_index": camera_index,
                    "timeout_seconds": timeout_seconds,
                    "rotation_degrees": rotation_degrees,
                }
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        if str(capture.get("status") or "") != "captured":
            return {
                "status": "unavailable",
                "source": "device_camera_capture",
                "camera_index": camera_index,
                "rotation_degrees": rotation_degrees,
                "cam0_rotate_180": camera_index == 0 and rotation_degrees == 180,
                "capture": capture,
                "analysis": {},
                "events": [],
                "suggestions": [],
            }
        capture_path = Path(str(capture.get("path") or "")).expanduser().resolve()
        workspace_root = self.runtime.workspace.root.resolve()
        try:
            workspace_name = str(capture_path.relative_to(workspace_root))
        except ValueError:
            self.record_audit("scene_sensor_camera", "blocked", str(capture_path), {"camera_index": camera_index}, ctx)
            return {
                "status": "blocked",
                "source": "device_camera_capture",
                "camera_index": camera_index,
                "rotation_degrees": rotation_degrees,
                "cam0_rotate_180": camera_index == 0 and rotation_degrees == 180,
                "capture": capture,
                "analysis": {},
                "events": [],
                "suggestions": [],
                "message": "Camera capture is outside workspace.",
            }
        analysis = self.runtime.camera_observer.analyze_frame(workspace_name)
        events = [item for item in analysis.get("events", []) if isinstance(item, dict)]
        return {
            "status": "completed" if analysis.get("status") == "ok" else normalize_task_status(str(analysis.get("status") or "backend_missing")),
            "source": str(capture.get("source") or "device_camera_capture"),
            "camera_index": camera_index,
            "rotation_degrees": rotation_degrees,
            "cam0_rotate_180": camera_index == 0 and rotation_degrees == 180,
            "image_path": str(capture_path),
            "workspace_name": workspace_name,
            "capture": capture,
            "analysis": analysis,
            "events": events,
            "suggestions": self.runtime.scene.workflow_suggestions(events),
        }

    def capture_scene_microphone_activity(self, *, seconds: int) -> dict[str, object]:
        scan = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
        mic_details = hardware_device_details(scan, "mic")
        device = str(mic_details.get("selected_device") or "").strip()
        if not device:
            return {
                "status": "unavailable",
                "message": "No ALSA capture device was detected.",
                "configured_device": self.runtime.config.mic_device,
                "selected_device": "",
                "candidates": mic_details.get("candidates", []),
            }
        output = self.runtime.workspace.path_for_new_file(f"scene_mic_activity_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav")
        result = record_microphone_sample(device, self.runtime.config.mic_rate, seconds, output)
        result.setdefault("configured_device", self.runtime.config.mic_device)
        result.setdefault("selected_device", device)
        result["configured_device_valid"] = bool(mic_details.get("configured_device_valid"))
        result["candidates"] = mic_details.get("candidates", [])
        result["activity_detected"] = str(result.get("status") or "") == "completed" and (safe_int(result.get("rms"), 0) >= 120 or safe_int(result.get("peak"), 0) >= 900)
        result["purpose"] = "scene_activity_only_no_transcription"
        return result

    def capture_from_camera_preview_snapshot(self, title: str, *, camera_index: int, rotation_degrees: int = 0) -> dict[str, object]:
        if self._camera_stream_camera_index is not None and self._camera_stream_camera_index != camera_index:
            return {
                "status": "unavailable",
                "source": "camera_preview_snapshot",
                "camera_index": camera_index,
                "stream_camera_index": self._camera_stream_camera_index,
                "rotation_degrees": rotation_degrees,
                "message": "相机预览当前不是请求的相机，改用设备相机直接拍照。",
            }
        preview_url = os.getenv("LELAMP_CAMERA_STREAM_URL", "http://127.0.0.1:8788").rstrip("/")
        snapshot_url = f"{preview_url}/snapshot.jpg"
        try:
            request = urllib.request.Request(snapshot_url, headers={"Cache-Control": "no-store"})
            with urllib.request.urlopen(request, timeout=3) as response:
                content_type = str(response.headers.get("Content-Type") or "")
                data = response.read()
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            return {
                "status": "unavailable",
                "source": "camera_preview_snapshot",
                "snapshot_url": snapshot_url,
                "camera_index": camera_index,
                "rotation_degrees": rotation_degrees,
                "message": f"相机预览快照不可用：{type(exc).__name__}",
            }
        if not data or "image/jpeg" not in content_type.lower():
            return {
                "status": "unavailable",
                "source": "camera_preview_snapshot",
                "snapshot_url": snapshot_url,
                "camera_index": camera_index,
                "content_type": content_type,
                "bytes": len(data),
                "rotation_degrees": rotation_degrees,
                "message": "相机预览没有返回 JPEG 画面。",
            }
        path = self.runtime.workspace.path_for_new_file(safe_filename(title, default="camera_preview", suffix="_snapshot.jpg"))
        atomic_write_bytes(path, data)
        rotation = self.runtime.camera_observer.rotate_image_file(path, rotation_degrees=rotation_degrees)
        return {
            "status": "captured",
            "source": "camera_preview_snapshot",
            "path": str(path),
            "bytes": path.stat().st_size if path.exists() else len(data),
            "snapshot_url": snapshot_url,
            "camera_index": camera_index,
            "rotation_degrees": rotation_degrees,
            "rotation": rotation,
            "command": "camera_stream.snapshot",
        }

    def write_scene_observation_image(self, image_data_url: str, title: str) -> Path:
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
        path = self.runtime.workspace.path_for_new_file(safe_filename(title, default="desk_scene", suffix=f"_capture{extension}"))
        atomic_write_bytes(path, data)
        self.audit.record("scene.image_capture_write", target=str(path), details={"bytes": len(data), "mime_type": mime_type})
        return path
