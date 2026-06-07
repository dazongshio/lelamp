from __future__ import annotations

import argparse
import json
import math
import os
import select
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .motor_control import write_goal_position_ordered
from .person_tracker import (
    Detection,
    FrameTarget,
    LampTargetController,
    TRACKING_MOTORS,
    clamp,
    clamp_soft_goal,
    detect_faces,
    get_yolo_model,
)


DEFAULT_OUTPUT_ROOT = Path("workspace/perception_runs")
_CAMERA_YOLO_MODEL_CACHE: dict[tuple[str, str], Any] = {}


@dataclass(frozen=True)
class TrackedDetection(Detection):
    track_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = super().as_dict()
        payload["track_id"] = self.track_id
        return payload


@dataclass(frozen=True)
class CameraObservation:
    camera_index: int
    timestamp: float
    frame_width: int
    frame_height: int
    detections: list[Detection]
    target: FrameTarget | None
    backend: str
    rejected_detections: list[Detection] | None = None

    @property
    def person_count(self) -> int:
        return len([item for item in self.detections if item.label == "person"])

    def as_dict(self) -> dict[str, Any]:
        return {
            "camera_index": self.camera_index,
            "timestamp": round(self.timestamp, 3),
            "frame": {"width": self.frame_width, "height": self.frame_height},
            "backend": self.backend,
            "person_count": self.person_count,
            "detections": [item.as_dict() for item in self.detections],
            "rejected_detections": [item.as_dict() for item in self.rejected_detections or []],
            "target": self.target.as_dict() if self.target else None,
            "track_ids": [
                item.track_id
                for item in self.detections
                if isinstance(item, TrackedDetection) and item.track_id is not None
            ],
        }


@dataclass(frozen=True)
class ControlSelection:
    target: FrameTarget | None
    source: str
    mode: str
    scale: float
    reason: str


class RpicamMjpegStream:
    def __init__(self, *, camera_index: int, width: int, height: int, framerate: float):
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.framerate = framerate
        self.process: subprocess.Popen[bytes] | None = None
        self._buffer = bytearray()

    def __enter__(self) -> "RpicamMjpegStream":
        command = [
            "rpicam-vid",
            "--camera",
            str(self.camera_index),
            "-n",
            "--codec",
            "mjpeg",
            "--width",
            str(self.width),
            "--height",
            str(self.height),
            "--framerate",
            str(self.framerate),
            "--timeout",
            "0",
            "--output",
            "-",
            "--flush",
            "--verbose",
            "0",
        ]
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        process = self.process
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def read(self, *, timeout: float = 2.0) -> Any | None:
        import cv2
        import numpy as np

        process = self.process
        if process is None or process.stdout is None:
            raise RuntimeError("rpicam stream is not started")

        deadline = time.monotonic() + timeout
        fd = process.stdout.fileno()
        while time.monotonic() < deadline:
            jpeg = self._pop_jpeg()
            if jpeg is not None:
                frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    return frame

            if process.poll() is not None:
                raise RuntimeError(f"rpicam-vid camera {self.camera_index} exited with {process.returncode}")

            remaining = max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select([fd], [], [], min(0.2, remaining))
            if not ready:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                raise RuntimeError(f"rpicam-vid camera {self.camera_index} produced no more data")
            self._buffer.extend(chunk)
            if len(self._buffer) > 8 * 1024 * 1024:
                start = self._buffer.rfind(b"\xff\xd8")
                if start > 0:
                    del self._buffer[:start]
                else:
                    self._buffer.clear()
        return None

    def _pop_jpeg(self) -> bytes | None:
        start = self._buffer.find(b"\xff\xd8")
        if start < 0:
            if len(self._buffer) > 4096:
                del self._buffer[:-2]
            return None
        if start > 0:
            del self._buffer[:start]
            start = 0
        end = self._buffer.find(b"\xff\xd9", start + 2)
        if end < 0:
            return None
        jpeg = bytes(self._buffer[start : end + 2])
        del self._buffer[: end + 2]
        return jpeg


class AudioMonitor:
    def __init__(
        self,
        *,
        device: str,
        sample_rate: int,
        vad_aggressiveness: int,
        active_dbfs: float,
        backend: str,
    ):
        self.requested_device = device
        self.sample_rate = sample_rate
        self.vad_aggressiveness = vad_aggressiveness
        self.active_dbfs = active_dbfs
        self.backend = backend
        self.selected_device: int | str | None = None
        self.selected_device_name: str | None = None
        self.stream: Any | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.thread: threading.Thread | None = None
        self._running = threading.Event()
        self.vad: Any | None = None
        self._lock = threading.Lock()
        self._latest = {
            "status": "starting",
            "rms": 0.0,
            "peak": 0.0,
            "dbfs": -120.0,
            "voice_active": False,
            "sound_active": False,
            "frames": 0,
        }

    def __enter__(self) -> "AudioMonitor":
        try:
            import webrtcvad

            self.vad = webrtcvad.Vad(self.vad_aggressiveness)
        except Exception:
            self.vad = None

        self.selected_device, self.selected_device_name = resolve_audio_device(self.requested_device)
        if self.backend == "arecord":
            self._start_arecord()
            backend = "arecord"
        elif self.backend == "sounddevice":
            self._start_sounddevice()
            backend = "sounddevice"
        else:
            try:
                self._start_sounddevice()
                backend = "sounddevice"
            except Exception as exc:
                self._start_arecord()
                backend = "arecord"
                with self._lock:
                    self._latest["sounddevice_error"] = f"{type(exc).__name__}: {exc}"

        with self._lock:
            self._latest = {
                **self._latest,
                "status": "running",
                "backend": backend,
                "selected_device": self.selected_device_name or str(self.selected_device),
                "sample_rate": self.sample_rate,
                "vad": bool(self.vad),
            }
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._running.clear()
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        if self.thread is not None:
            self.thread.join(timeout=2)

    def _start_sounddevice(self) -> None:
        import sounddevice as sd

        blocksize = int(self.sample_rate * 0.02)
        self.stream = sd.InputStream(
            device=self.selected_device,
            samplerate=self.sample_rate,
            channels=1,
            blocksize=blocksize,
            dtype="float32",
            callback=self._callback_float32,
        )
        self.stream.start()

    def _start_arecord(self) -> None:
        device = str(self.selected_device_name or self.selected_device or "default")
        if self.requested_device == "auto" and not device.startswith(("hw:", "plughw:", "default", "sysdefault")):
            device = "plughw:CARD=A311,DEV=0"
            self.selected_device_name = device
        command = [
            "arecord",
            "-q",
            "-D",
            device,
            "-f",
            "S16_LE",
            "-c",
            "1",
            "-r",
            str(self.sample_rate),
            "-t",
            "raw",
        ]
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            bufsize=0,
        )
        self._running.set()
        self.thread = threading.Thread(target=self._arecord_loop, name="lelamp-audio-monitor", daemon=True)
        self.thread.start()

    def _arecord_loop(self) -> None:
        import numpy as np

        if self.process is None or self.process.stdout is None:
            return
        bytes_per_window = int(self.sample_rate * 0.02) * 2
        fd = self.process.stdout.fileno()
        while self._running.is_set():
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            data = os.read(fd, bytes_per_window)
            if not data:
                if self.process.poll() is not None:
                    with self._lock:
                        self._latest = {
                            **self._latest,
                            "status": "audio_process_exited",
                            "returncode": self.process.returncode,
                        }
                    return
                continue
            if len(data) < 2:
                continue
            samples_i16 = np.frombuffer(data, dtype=np.int16)
            if samples_i16.size == 0:
                continue
            samples = samples_i16.astype(np.float32) / 32768.0
            self._update_metrics(samples, samples_i16.tobytes(), frames=int(samples_i16.size))

    def _callback_float32(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        import numpy as np

        samples = indata[:, 0].copy()
        pcm16 = np.clip(samples, -1.0, 1.0)
        pcm16 = (pcm16 * 32767.0).astype(np.int16).tobytes()
        self._update_metrics(samples, pcm16, frames=frames)

    def _update_metrics(self, samples: Any, pcm16: bytes, *, frames: int) -> None:
        import numpy as np

        rms = float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0
        peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
        dbfs = 20.0 * math.log10(max(rms, 1e-8))
        sound_active = dbfs >= self.active_dbfs
        voice_active = sound_active
        if self.vad is not None and self.sample_rate in {8000, 16000, 32000, 48000}:
            try:
                voice_active = bool(self.vad.is_speech(pcm16, self.sample_rate))
            except Exception:
                voice_active = sound_active

        with self._lock:
            self._latest = {
                **self._latest,
                "status": "running",
                "rms": round(rms, 6),
                "peak": round(peak, 6),
                "dbfs": round(dbfs, 2),
                "voice_active": bool(voice_active),
                "sound_active": bool(sound_active),
                "frames": int(self._latest.get("frames", 0)) + frames,
            }

    def latest(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._latest)


def resolve_audio_device(requested: str) -> tuple[int | str | None, str | None]:
    import sounddevice as sd

    if requested and requested != "auto":
        try:
            index = int(requested)
            name = str(sd.query_devices(index)["name"])
            if "Yundea A31-1" in name:
                return index, "plughw:CARD=A311,DEV=0"
            return index, name
        except ValueError:
            return requested, requested

    devices = sd.query_devices()
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels") or 0) > 0:
            name = str(device.get("name") or index)
            if "Yundea A31-1" in name:
                return index, "plughw:CARD=A311,DEV=0"
            return index, str(device.get("name") or index)
    raise RuntimeError("No input microphone device found")


def detect_people_yolo(
    frame: Any,
    *,
    model_name: str,
    confidence: float,
    image_size: int,
    device: str | None,
    tracker: str | None = None,
    model_cache_key: str | None = None,
) -> list[Detection]:
    if model_cache_key is None:
        model = get_yolo_model(model_name)
    else:
        cache_key = (model_cache_key, model_name)
        model = _CAMERA_YOLO_MODEL_CACHE.get(cache_key)
        if model is None:
            from ultralytics import YOLO

            model = YOLO(model_name)
            _CAMERA_YOLO_MODEL_CACHE[cache_key] = model
    predict_kwargs: dict[str, Any] = {
        "source": frame,
        "classes": [0],
        "conf": confidence,
        "imgsz": image_size,
        "verbose": False,
    }
    if device:
        predict_kwargs["device"] = device

    if tracker:
        results = model.track(
            **predict_kwargs,
            tracker=tracker,
            persist=True,
        )
    else:
        results = model.predict(**predict_kwargs)
    if not results or results[0].boxes is None:
        return []

    boxes = results[0].boxes
    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy()
    ids = None
    if getattr(boxes, "id", None) is not None:
        ids = boxes.id.cpu().numpy()
    detections: list[Detection] = []
    for index, box in enumerate(xyxy):
        x1, y1, x2, y2 = box
        x = max(0, int(round(x1)))
        y = max(0, int(round(y1)))
        width = max(0, int(round(x2 - x1)))
        height = max(0, int(round(y2 - y1)))
        if width <= 0 or height <= 0:
            continue
        track_id = int(ids[index]) if ids is not None and len(ids) > index else None
        detections.append(TrackedDetection("person", float(confs[index]), x, y, width, height, track_id))
    return detections


def analyze_camera(
    frame: Any,
    *,
    camera_index: int,
    backend: str,
    face_min_size: int,
    yolo_model: str,
    yolo_conf: float,
    yolo_imgsz: int,
    yolo_device: str | None,
    tracker: str | None,
    tracker_namespace: str | None,
    person_head_ratio: float,
) -> CameraObservation:
    height, width = frame.shape[:2]
    detections: list[Detection]
    if backend in {"yolo", "bytetrack", "botsort"}:
        detections = detect_people_yolo(
            frame,
            model_name=yolo_model,
            confidence=yolo_conf,
            image_size=yolo_imgsz,
            device=yolo_device,
            tracker=tracker if backend in {"bytetrack", "botsort"} else None,
            model_cache_key=tracker_namespace if backend in {"bytetrack", "botsort"} else None,
        )
    elif backend == "face":
        detections = detect_faces(frame, min_size=face_min_size)
    elif backend == "auto":
        detections = detect_people_yolo(
            frame,
            model_name=yolo_model,
            confidence=yolo_conf,
            image_size=yolo_imgsz,
            device=yolo_device,
            tracker=tracker,
            model_cache_key=tracker_namespace if tracker else None,
        )
        if not detections:
            detections = detect_faces(frame, min_size=face_min_size)
    else:
        raise ValueError(f"Unsupported backend for realtime environment: {backend}")

    target = crowd_target(
        detections,
        frame_width=width,
        frame_height=height,
        person_head_ratio=person_head_ratio,
    )
    return CameraObservation(
        camera_index=camera_index,
        timestamp=time.time(),
        frame_width=width,
        frame_height=height,
        detections=detections,
        target=target,
        backend=backend,
    )


def crowd_target(
    detections: list[Detection],
    *,
    frame_width: int,
    frame_height: int,
    person_head_ratio: float,
) -> FrameTarget | None:
    if not detections:
        return None

    people = [item for item in detections if item.label == "person"]
    candidates = people or detections
    x1 = min(item.x for item in candidates)
    y1 = min(item.y for item in candidates)
    x2 = max(item.x + item.width for item in candidates)
    y2 = max(item.y + item.height for item in candidates)
    confidence = sum(item.confidence for item in candidates) / len(candidates)
    label = "person_group" if people else "face_group"
    union = Detection(label, confidence, x1, y1, x2 - x1, y2 - y1)

    if people:
        target_x = sum(item.x + item.width / 2.0 for item in people) / len(people)
        ratio = clamp(person_head_ratio, 0.05, 0.5)
        target_y = sum(item.y + item.height * ratio for item in people) / len(people)
        track_ids = [
            item.track_id
            for item in people
            if isinstance(item, TrackedDetection) and item.track_id is not None
        ]
        if track_ids:
            target_label = f"tracked_person_group:{len(people)} ids={','.join(str(item) for item in track_ids)}"
        else:
            target_label = f"person_group:{len(people)}"
    else:
        target_x = sum(item.center[0] for item in candidates) / len(candidates)
        target_y = sum(item.center[1] for item in candidates) / len(candidates)
        target_label = f"face_group:{len(candidates)}"

    error_x = target_x - frame_width / 2.0
    error_y = target_y - frame_height / 2.0
    return FrameTarget(
        detection=union,
        frame_width=frame_width,
        frame_height=frame_height,
        target_x=target_x,
        target_y=target_y,
        target_label=target_label,
        error_x=error_x,
        error_y=error_y,
        normalized_error_x=error_x / max(1.0, frame_width / 2.0),
        normalized_error_y=error_y / max(1.0, frame_height / 2.0),
    )


def detection_touches_frame_edge(detection: Detection, *, frame_width: int, frame_height: int, margin: int) -> bool:
    return (
        detection.x <= margin
        or detection.y <= margin
        or detection.x + detection.width >= frame_width - margin
        or detection.y + detection.height >= frame_height - margin
    )


def filter_servo_camera_detections(observation: CameraObservation, *, args: argparse.Namespace) -> CameraObservation:
    if args.cam1_edge_filter_margin <= 0:
        return observation

    max_edge_area = observation.frame_width * observation.frame_height * args.cam1_edge_filter_max_area_ratio
    filtered: list[Detection] = []
    rejected: list[Detection] = []
    for detection in observation.detections:
        touches_edge = detection_touches_frame_edge(
            detection,
            frame_width=observation.frame_width,
            frame_height=observation.frame_height,
            margin=args.cam1_edge_filter_margin,
        )
        if detection.label == "person" and touches_edge and detection.area <= max_edge_area:
            rejected.append(detection)
        else:
            filtered.append(detection)

    if not rejected:
        return observation

    target = crowd_target(
        filtered,
        frame_width=observation.frame_width,
        frame_height=observation.frame_height,
        person_head_ratio=args.person_head_ratio,
    )
    return CameraObservation(
        camera_index=observation.camera_index,
        timestamp=observation.timestamp,
        frame_width=observation.frame_width,
        frame_height=observation.frame_height,
        detections=filtered,
        target=target,
        backend=f"{observation.backend}+cam1_edge_filter",
        rejected_detections=rejected,
    )


def scaled_target(target: FrameTarget, scale: float) -> FrameTarget:
    width = target.frame_width
    height = target.frame_height
    error_x = target.error_x * scale
    error_y = target.error_y * scale
    target_x = width / 2.0 + error_x
    target_y = height / 2.0 + error_y
    return FrameTarget(
        detection=target.detection,
        frame_width=width,
        frame_height=height,
        target_x=target_x,
        target_y=target_y,
        target_label=target.target_label,
        error_x=error_x,
        error_y=error_y,
        normalized_error_x=error_x / max(1.0, width / 2.0),
        normalized_error_y=error_y / max(1.0, height / 2.0),
    )


def choose_fixed_to_servo_target(
    *,
    fixed: CameraObservation | None,
    servo: CameraObservation,
    audio: dict[str, Any],
) -> ControlSelection:
    if servo.target is not None:
        reason = "cam1_servo_camera_fine_centering"
        if audio.get("voice_active"):
            reason += "+voice_active"
        return ControlSelection(
            target=servo.target,
            source="cam1_servo",
            mode="cam1_fine_visual_servo",
            scale=1.0,
            reason=reason,
        )

    if fixed is not None and fixed.target is not None:
        reason = "cam0_fixed_camera_coarse_people_bearing"
        if audio.get("voice_active"):
            reason += "+voice_active"
        return ControlSelection(
            target=fixed.target,
            source="cam0_fixed",
            mode="cam0_fixed_coarse_guide",
            scale=1.0,
            reason=reason,
        )

    if audio.get("voice_active") or audio.get("sound_active"):
        return ControlSelection(
            target=None,
            source="audio",
            mode="audio_only_no_direction",
            scale=0.0,
            reason="single_microphone_sound_detected_no_direction",
        )

    return ControlSelection(
        target=None,
        source="none",
        mode="no_control_target",
        scale=0.0,
        reason="no_people_or_active_sound",
    )


def draw_overlay(frame: Any, observation: CameraObservation, *, source: str) -> Any:
    import cv2

    annotated = frame.copy()
    height, width = annotated.shape[:2]
    cv2.line(annotated, (width // 2 - 18, height // 2), (width // 2 + 18, height // 2), (80, 220, 255), 1)
    cv2.line(annotated, (width // 2, height // 2 - 18), (width // 2, height // 2 + 18), (80, 220, 255), 1)
    for detection in observation.detections:
        color = (60, 230, 120) if detection.label == "person" else (255, 190, 80)
        cv2.rectangle(
            annotated,
            (detection.x, detection.y),
            (detection.x + detection.width, detection.y + detection.height),
            color,
            2,
        )
        cv2.putText(
            annotated,
            f"{detection.label} {detection.confidence:.2f}",
            (detection.x, max(18, detection.y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    if observation.target is not None:
        target = observation.target
        cv2.circle(annotated, (int(target.target_x), int(target.target_y)), 5, (0, 255, 255), -1)
        cv2.line(
            annotated,
            (width // 2, height // 2),
            (int(target.target_x), int(target.target_y)),
            (0, 255, 255),
            2,
        )
    cv2.putText(
        annotated,
        f"{source} cam={observation.camera_index} people={observation.person_count}",
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (235, 235, 235),
        2,
        cv2.LINE_AA,
    )
    return annotated


def save_snapshot(path: Path, frame: Any, observation: CameraObservation, *, source: str) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), draw_overlay(frame, observation, source=source))


def controller_from_args(args: argparse.Namespace) -> LampTargetController:
    return LampTargetController(
        port=args.port,
        lamp_id=args.id,
        motion_mode=args.motion_mode,
        yaw_gain=args.yaw_gain,
        pitch_gain=args.pitch_gain,
        max_step=args.max_step,
        deadband=args.deadband,
        invert_yaw=args.invert_yaw,
        invert_pitch=args.invert_pitch,
        yaw_min=args.yaw_min,
        yaw_max=args.yaw_max,
        pitch_min=args.pitch_min,
        pitch_max=args.pitch_max,
        base_pitch_gain=args.base_pitch_gain,
        elbow_pitch_gain=args.elbow_pitch_gain,
        wrist_roll_gain=args.wrist_roll_gain,
        wrist_pitch_gain=args.wrist_pitch_gain,
        base_pitch_min=args.base_pitch_min,
        base_pitch_max=args.base_pitch_max,
        elbow_pitch_min=args.elbow_pitch_min,
        elbow_pitch_max=args.elbow_pitch_max,
        wrist_roll_min=args.wrist_roll_min,
        wrist_roll_max=args.wrist_roll_max,
    )


class PidVisualServoController:
    def __init__(self, *, controller: LampTargetController, args: argparse.Namespace):
        from simple_pid import PID

        self.controller = controller
        self.yaw_pid = PID(
            args.pid_yaw_kp,
            args.pid_yaw_ki,
            args.pid_yaw_kd,
            setpoint=0.0,
            output_limits=(-args.max_step, args.max_step),
            sample_time=None,
        )
        self.pitch_pid = PID(
            args.pid_pitch_kp,
            args.pid_pitch_ki,
            args.pid_pitch_kd,
            setpoint=0.0,
            output_limits=(-args.max_step, args.max_step),
            sample_time=None,
        )
        self.invert_yaw = args.invert_yaw
        self.invert_pitch = args.invert_pitch
        self.deadband = args.deadband
        self.motion_mode = args.motion_mode
        self.base_pitch_gain = args.base_pitch_gain
        self.elbow_pitch_gain = args.elbow_pitch_gain
        self.wrist_roll_gain = args.wrist_roll_gain
        self.wrist_pitch_gain = args.wrist_pitch_gain

    def reset(self) -> None:
        self.yaw_pid.reset()
        self.pitch_pid.reset()

    def correction_deltas(self, target: FrameTarget) -> dict[str, float]:
        error_x = 0.0 if abs(target.normalized_error_x) < self.deadband else target.normalized_error_x
        error_y = 0.0 if abs(target.normalized_error_y) < self.deadband else target.normalized_error_y
        yaw_delta = float(self.yaw_pid(error_x))
        pitch_delta = -float(self.pitch_pid(error_y))
        if self.invert_yaw:
            yaw_delta = -yaw_delta
        if self.invert_pitch:
            pitch_delta = -pitch_delta

        if self.motion_mode == "head":
            return {
                "base_yaw": yaw_delta,
                "wrist_pitch": pitch_delta,
            }
        return {
            "base_yaw": yaw_delta,
            "base_pitch": pitch_delta * self.base_pitch_gain,
            "elbow_pitch": pitch_delta * self.elbow_pitch_gain,
            "wrist_roll": yaw_delta * self.wrist_roll_gain,
            "wrist_pitch": pitch_delta * self.wrist_pitch_gain,
        }

    def step(self, target: FrameTarget) -> dict[str, float]:
        deltas = self.correction_deltas(target)
        return self.step_deltas(deltas)

    def step_deltas(self, deltas: dict[str, float]) -> dict[str, float]:
        current = self.controller.robot.bus.sync_read("Present_Position", motors=list(deltas))
        goal_pos = {}
        for motor, delta in deltas.items():
            current_value = float(current[motor])
            limit_min, limit_max = self.controller.motor_limits[motor]
            goal_pos[motor] = clamp_soft_goal(current_value, current_value + delta, limit_min, limit_max)
        write_goal_position_ordered(self.controller.robot.bus, goal_pos)
        return {f"{motor}.pos": value for motor, value in goal_pos.items()}


class DryRunMotorLimits:
    def __init__(self, args: argparse.Namespace):
        self.motor_limits = {
            "base_yaw": (args.yaw_min, args.yaw_max),
            "base_pitch": (args.base_pitch_min, args.base_pitch_max),
            "elbow_pitch": (args.elbow_pitch_min, args.elbow_pitch_max),
            "wrist_roll": (args.wrist_roll_min, args.wrist_roll_max),
            "wrist_pitch": (args.pitch_min, args.pitch_max),
        }


class FixedToServoCoarseController:
    def __init__(self, *, controller: LampTargetController | None, args: argparse.Namespace):
        self.controller = controller
        self.args = args

    def correction_deltas(self, target: FrameTarget) -> dict[str, float]:
        error_x = 0.0 if abs(target.normalized_error_x) < self.args.cam0_deadband else target.normalized_error_x
        error_y = 0.0 if abs(target.normalized_error_y) < self.args.cam0_deadband else target.normalized_error_y
        yaw_delta = error_x * self.args.cam0_to_cam1_yaw_gain
        pitch_delta = error_y * self.args.cam0_to_cam1_pitch_gain
        if self.args.invert_yaw:
            yaw_delta = -yaw_delta
        if self.args.invert_pitch:
            pitch_delta = -pitch_delta
        yaw_delta = clamp(yaw_delta, -self.args.cam0_coarse_max_step, self.args.cam0_coarse_max_step)
        pitch_delta = clamp(pitch_delta, -self.args.cam0_coarse_max_step, self.args.cam0_coarse_max_step)

        deltas = {
            "base_yaw": yaw_delta,
            "wrist_pitch": pitch_delta,
        }
        if self.args.motion_mode == "all":
            deltas.update(
                {
                    "base_pitch": pitch_delta * self.args.cam0_base_pitch_gain,
                    "elbow_pitch": pitch_delta * self.args.cam0_elbow_pitch_gain,
                    "wrist_roll": yaw_delta * self.args.cam0_wrist_roll_gain,
                    "wrist_pitch": pitch_delta * self.args.cam0_wrist_pitch_gain,
                }
            )
        return deltas

    def step(self, target: FrameTarget) -> dict[str, float]:
        if self.controller is None:
            raise RuntimeError("Cannot write coarse guide deltas without a motor controller")
        deltas = self.correction_deltas(target)
        current = self.controller.robot.bus.sync_read("Present_Position", motors=list(deltas))
        goal_pos = {}
        for motor, delta in deltas.items():
            current_value = float(current[motor])
            limit_min, limit_max = self.controller.motor_limits[motor]
            goal_pos[motor] = clamp_soft_goal(current_value, current_value + delta, limit_min, limit_max)
        write_goal_position_ordered(self.controller.robot.bus, goal_pos)
        return {f"{motor}.pos": value for motor, value in goal_pos.items()}


def parse_float_list(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def load_center_pose(path_text: str, fallback: dict[str, float]) -> dict[str, float]:
    path = Path(path_text)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        motors = payload.get("motors", payload)
        return {motor: float(motors.get(motor, fallback[motor])) for motor in TRACKING_MOTORS}
    return dict(fallback)


class Cam1AcquisitionScanner:
    def __init__(
        self,
        *,
        controller: LampTargetController | None,
        motor_limits: Any,
        args: argparse.Namespace,
        dry_run: bool = False,
    ):
        self.controller = controller
        self.motor_limits = motor_limits.motor_limits
        self.args = args
        self.dry_run = dry_run
        self.center: dict[str, float] | None = None
        self.goals: list[dict[str, float]] = []
        self.goal_index = 0
        self.frames_on_goal = 0
        self.last_replan_frame = -10**9

    def step(self, *, frame_index: int, fixed_target: FrameTarget, write: bool) -> dict[str, Any]:
        if self.center is None or frame_index - self.last_replan_frame >= self.args.cam1_acquire_replan_frames:
            self._replan(frame_index, fixed_target)

        if not self.goals:
            self._replan(frame_index, fixed_target)
        goal = self.goals[self.goal_index % len(self.goals)]
        current = self._read_current()
        bounded: dict[str, float] = {}
        planned_deltas: dict[str, float] = {}
        for motor in ("base_yaw", "wrist_pitch"):
            current_value = float(current[motor])
            delta = clamp(
                goal[motor] - current_value,
                -self.args.cam1_acquire_max_step,
                self.args.cam1_acquire_max_step,
            )
            limit_min, limit_max = self.motor_limits[motor]
            bounded[motor] = clamp_soft_goal(current_value, current_value + delta, limit_min, limit_max)
            planned_deltas[motor] = bounded[motor] - current_value

        if write and not self.dry_run and self.controller is not None:
            write_goal_position_ordered(self.controller.robot.bus, bounded)

        reached = all(abs(goal[motor] - bounded[motor]) <= self.args.cam1_acquire_goal_tolerance for motor in bounded)
        self.frames_on_goal += 1
        if reached or self.frames_on_goal >= self.args.cam1_acquire_hold_frames:
            self.goal_index = (self.goal_index + 1) % len(self.goals)
            self.frames_on_goal = 0

        return {
            "mode": "cam0_guided_cam1_acquire_scan",
            "anchor": self.args.cam1_acquire_anchor,
            "center": {motor: round(value, 4) for motor, value in (self.center or {}).items()},
            "goal_index": self.goal_index,
            "goal": {motor: round(value, 4) for motor, value in goal.items()},
            "planned_deltas": {motor: round(value, 4) for motor, value in planned_deltas.items()},
            "sent_action": {f"{motor}.pos": value for motor, value in bounded.items()} if write else None,
            "reached_goal": reached,
        }

    def _read_current(self) -> dict[str, float]:
        if self.dry_run or self.controller is None:
            return dict(self.center or self._default_center())
        current = self.controller.robot.bus.sync_read("Present_Position", motors=["base_yaw", "wrist_pitch"])
        return {
            "base_yaw": float(current["base_yaw"]),
            "wrist_pitch": float(current["wrist_pitch"]),
        }

    def _default_center(self) -> dict[str, float]:
        fallback = {
            "base_yaw": 0.0,
            "base_pitch": -90.0,
            "elbow_pitch": 95.0,
            "wrist_roll": 0.0,
            "wrist_pitch": 12.0,
        }
        if self.args.cam1_acquire_anchor == "pose" and self.args.cam1_acquire_center_pose:
            return load_center_pose(self.args.cam1_acquire_center_pose, fallback)
        return fallback

    def _replan(self, frame_index: int, fixed_target: FrameTarget) -> None:
        current = self._read_current()
        if self.args.cam1_acquire_anchor == "pose" and self.args.cam1_acquire_center_pose:
            self.center = load_center_pose(self.args.cam1_acquire_center_pose, current)
        else:
            self.center = {
                "base_yaw": float(current["base_yaw"]),
                "wrist_pitch": float(current["wrist_pitch"]),
            }
        self.goals = self._build_goals(fixed_target)
        self.goal_index = 0
        self.frames_on_goal = 0
        self.last_replan_frame = frame_index

    def _build_goals(self, fixed_target: FrameTarget) -> list[dict[str, float]]:
        assert self.center is not None
        yaw_bias = self._yaw_bias(fixed_target)
        yaw_offsets = self._yaw_offsets(yaw_bias)
        pitch_offsets = parse_float_list(self.args.cam1_acquire_pitch_offsets)
        if not pitch_offsets:
            pitch_offsets = [0.0]

        goals: list[dict[str, float]] = []
        yaw_min, yaw_max = self.motor_limits["base_yaw"]
        pitch_min, pitch_max = self.motor_limits["wrist_pitch"]
        for yaw_offset in yaw_offsets:
            for pitch_offset in pitch_offsets:
                goals.append(
                    {
                        "base_yaw": clamp(self.center["base_yaw"] + yaw_offset, yaw_min, yaw_max),
                        "wrist_pitch": clamp(self.center["wrist_pitch"] + pitch_offset, pitch_min, pitch_max),
                    }
                )
        return goals

    def _yaw_bias(self, fixed_target: FrameTarget) -> float:
        span = abs(self.args.cam1_acquire_yaw_span)
        return clamp(
            fixed_target.normalized_error_x * self.args.cam0_to_cam1_yaw_sign * span,
            -span,
            span,
        )

    def _yaw_offsets(self, bias: float) -> list[float]:
        span = abs(self.args.cam1_acquire_yaw_span)
        step = max(1.0, abs(self.args.cam1_acquire_yaw_step))
        base_offsets = [bias, 0.0]
        value = step
        while value <= span:
            base_offsets.extend([bias + value, bias - value, value, -value])
            value += step

        ordered = sorted(base_offsets, key=lambda item: abs(item - bias))
        result: list[float] = []
        seen: set[float] = set()
        for offset in ordered:
            rounded = round(clamp(offset, -span, span), 3)
            if rounded not in seen:
                result.append(rounded)
                seen.add(rounded)
        return result


class SearchScanner:
    def __init__(self, *, controller: LampTargetController, args: argparse.Namespace):
        self.controller = controller
        self.center = {
            motor: float(value)
            for motor, value in controller.robot.bus.sync_read("Present_Position", motors=list(TRACKING_MOTORS)).items()
        }
        self.phase = 0.0
        self.args = args

    def step(self, *, write: bool) -> dict[str, float]:
        self.phase += self.args.scan_phase_step
        proposed = {
            "base_yaw": self.center["base_yaw"] + self.args.scan_yaw_amplitude * math.sin(self.phase),
            "base_pitch": self.center["base_pitch"] + self.args.scan_body_pitch_amplitude * math.sin(self.phase * 0.5),
            "elbow_pitch": self.center["elbow_pitch"] - self.args.scan_elbow_pitch_amplitude * math.sin(self.phase * 0.5),
            "wrist_roll": self.center["wrist_roll"] + self.args.scan_wrist_roll_amplitude * math.sin(self.phase),
            "wrist_pitch": self.center["wrist_pitch"] + self.args.scan_wrist_pitch_amplitude * math.sin(self.phase * 0.5),
        }
        current = self.controller.robot.bus.sync_read("Present_Position", motors=list(TRACKING_MOTORS))
        goal_pos: dict[str, float] = {}
        for motor, target in proposed.items():
            current_value = float(current[motor])
            bounded_step_target = current_value + clamp(
                target - current_value,
                -self.args.scan_max_step,
                self.args.scan_max_step,
            )
            limit_min, limit_max = self.controller.motor_limits[motor]
            goal_pos[motor] = clamp_soft_goal(current_value, bounded_step_target, limit_min, limit_max)
        if write:
            write_goal_position_ordered(self.controller.robot.bus, goal_pos)
        return {f"{motor}.pos": value for motor, value in goal_pos.items()}


class SceneScanner:
    def __init__(self, *, controller: LampTargetController, args: argparse.Namespace, dry_run: bool = False):
        self.controller = controller
        self.args = args
        self.dry_run = dry_run
        self.center = self._read_center()
        self.offsets = [float(item.strip()) for item in args.scene_scan_yaw_offsets.split(",") if item.strip()]
        self.pitch_offsets = [float(item.strip()) for item in args.scene_scan_pitch_offsets.split(",") if item.strip()]
        if not self.offsets:
            self.offsets = [-12.0, 0.0, 12.0]
        if not self.pitch_offsets:
            self.pitch_offsets = [0.0]
        self.active = False
        self.samples: list[dict[str, Any]] = []
        self.sample_index = 0
        self.last_started_frame = -10**9
        self.best_goal: dict[str, float] | None = None
        self.best_sample: dict[str, Any] | None = None

    def should_start(self, frame_index: int) -> bool:
        if not self.args.scene_scan:
            return False
        if self.active:
            return True
        return frame_index - self.last_started_frame >= self.args.scene_scan_interval_frames

    def start(self, frame_index: int) -> None:
        self.active = True
        self.samples = []
        self.sample_index = 0
        self.last_started_frame = frame_index
        self.best_goal = None
        self.best_sample = None
        self.center = self._read_center()

    def _read_center(self) -> dict[str, float]:
        if self.dry_run or not self.controller.robot.bus.is_connected:
            return {
                "base_yaw": 0.0,
                "base_pitch": -90.0,
                "elbow_pitch": 95.0,
                "wrist_roll": 0.0,
                "wrist_pitch": 10.0,
            }
        return {
            motor: float(value)
            for motor, value in self.controller.robot.bus.sync_read("Present_Position", motors=list(TRACKING_MOTORS)).items()
        }

    def current_offsets(self) -> tuple[float, float]:
        yaw_index = self.sample_index % len(self.offsets)
        pitch_index = (self.sample_index // len(self.offsets)) % len(self.pitch_offsets)
        return self.offsets[yaw_index], self.pitch_offsets[pitch_index]

    def sample_count(self) -> int:
        return len(self.offsets) * len(self.pitch_offsets)

    def observe_and_step(
        self,
        *,
        frame_index: int,
        primary: CameraObservation,
        secondary: CameraObservation | None,
        audio: dict[str, Any],
        write: bool,
    ) -> dict[str, Any]:
        if not self.active:
            self.start(frame_index)

        yaw_offset, pitch_offset = self.current_offsets()
        people_count = primary.person_count + (secondary.person_count if secondary else 0)
        confidence = sum(item.confidence for item in primary.detections)
        if secondary is not None:
            confidence += sum(item.confidence for item in secondary.detections) * 0.35
        target_error = abs(primary.target.normalized_error_x) + abs(primary.target.normalized_error_y) if primary.target else 2.0
        score = people_count * 10.0 + confidence - target_error
        sample = {
            "frame_index": frame_index,
            "sample_index": self.sample_index,
            "yaw_offset": yaw_offset,
            "pitch_offset": pitch_offset,
            "cam1_people": primary.person_count,
            "cam0_people": secondary.person_count if secondary else 0,
            "track_ids": primary.as_dict().get("track_ids", []),
            "voice_active": bool(audio.get("voice_active")),
            "sound_active": bool(audio.get("sound_active")),
            "score": round(score, 4),
        }
        self.samples.append(sample)
        if self.best_sample is None or score > float(self.best_sample["score"]):
            self.best_sample = sample
            self.best_goal = self._goal_for_offsets(yaw_offset, pitch_offset)

        current_goal = self._goal_for_offsets(yaw_offset, pitch_offset)
        action = self._write_goal(current_goal, write=write)
        self.sample_index += 1
        completed = self.sample_index >= self.sample_count()
        reposition_action = None
        if completed:
            self.active = False
            if self.best_goal is not None:
                reposition_action = self._write_goal(self.best_goal, write=write)

        return {
            "active": self.active,
            "completed": completed,
            "sample": sample,
            "samples": list(self.samples),
            "best_sample": self.best_sample,
            "scan_action": action,
            "reposition_action": reposition_action,
        }

    def _goal_for_offsets(self, yaw_offset: float, pitch_offset: float) -> dict[str, float]:
        return {
            "base_yaw": self.center["base_yaw"] + yaw_offset,
            "base_pitch": self.center["base_pitch"] + pitch_offset * 0.3,
            "elbow_pitch": self.center["elbow_pitch"] - pitch_offset * 0.25,
            "wrist_roll": self.center["wrist_roll"],
            "wrist_pitch": self.center["wrist_pitch"] + pitch_offset * 0.7,
        }

    def _write_goal(self, goal: dict[str, float], *, write: bool) -> dict[str, float]:
        if self.dry_run or not write:
            current = dict(self.center)
        else:
            current = self.controller.robot.bus.sync_read("Present_Position", motors=list(TRACKING_MOTORS))
        bounded: dict[str, float] = {}
        for motor, target in goal.items():
            current_value = float(current[motor])
            step_target = current_value + clamp(
                target - current_value,
                -self.args.scene_scan_max_step,
                self.args.scene_scan_max_step,
            )
            limit_min, limit_max = self.controller.motor_limits[motor]
            bounded[motor] = clamp_soft_goal(current_value, step_target, limit_min, limit_max)
        if write and not self.dry_run:
            write_goal_position_ordered(self.controller.robot.bus, bounded)
        return {f"{motor}.pos": value for motor, value in bounded.items()}


class FixedCameraSceneMemory:
    def __init__(self, *, max_age_frames: int):
        self.max_age_frames = max_age_frames
        self.best_sample: dict[str, Any] | None = None
        self.samples: list[dict[str, Any]] = []

    def observe(self, *, frame_index: int, fixed: CameraObservation | None, audio: dict[str, Any]) -> dict[str, Any]:
        people_count = fixed.person_count if fixed is not None else 0
        confidence = sum(item.confidence for item in fixed.detections) if fixed is not None else 0.0
        target_error = (
            abs(fixed.target.normalized_error_x) + abs(fixed.target.normalized_error_y)
            if fixed is not None and fixed.target is not None
            else 2.0
        )
        score = people_count * 10.0 + confidence - target_error
        sample = {
            "frame_index": frame_index,
            "source": "cam0_fixed_environment_scan",
            "cam0_people": people_count,
            "track_ids": fixed.as_dict().get("track_ids", []) if fixed is not None else [],
            "target": fixed.target.as_dict() if fixed is not None and fixed.target is not None else None,
            "voice_active": bool(audio.get("voice_active")),
            "sound_active": bool(audio.get("sound_active")),
            "score": round(score, 4),
        }
        self.samples.append(sample)
        self.samples = [
            item for item in self.samples if frame_index - int(item.get("frame_index", frame_index)) <= self.max_age_frames
        ]
        if sample["cam0_people"] > 0 and (
            self.best_sample is None
            or frame_index - int(self.best_sample.get("frame_index", frame_index)) > self.max_age_frames
            or float(sample["score"]) >= float(self.best_sample.get("score", -999))
        ):
            self.best_sample = sample

        return {
            "mode": "fixed_cam0_scene_scan",
            "description": "cam0 stays physically fixed and continuously scans the scene from its fixed viewpoint",
            "active": True,
            "current_sample": sample,
            "recent_samples": list(self.samples[-8:]),
            "best_sample": self.best_sample,
        }


class RecentTargetMemory:
    def __init__(self, *, max_age_frames: int):
        self.max_age_frames = max_age_frames
        self.frame_index: int | None = None
        self.target: FrameTarget | None = None
        self.source: str | None = None

    def observe(self, *, frame_index: int, target: FrameTarget | None, source: str) -> None:
        if target is None:
            return
        self.frame_index = frame_index
        self.target = target
        self.source = source

    def latest(self, *, frame_index: int) -> tuple[FrameTarget | None, dict[str, Any] | None]:
        if self.max_age_frames <= 0:
            return None, None
        if self.frame_index is None or self.target is None:
            return None, None
        age = frame_index - self.frame_index
        if age > self.max_age_frames:
            return None, None
        return self.target, {
            "source": self.source,
            "frame_index": self.frame_index,
            "age_frames": age,
            "max_age_frames": self.max_age_frames,
            "target": self.target.as_dict(),
        }


def recent_target_scale(*, age_frames: int, max_age_frames: int, min_scale: float) -> float:
    age_scale = (max_age_frames + 1 - age_frames) / max(1, max_age_frames + 1)
    return clamp(age_scale, min_scale, 1.0)


def run(args: argparse.Namespace) -> int:
    import cv2

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_ROOT / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "decisions.jsonl"
    print(
        json.dumps(
            {
                "status": "starting_realtime_environment",
                "message": "opening cameras, microphone, detector, and motor bus",
                "cam0_fixed_camera": args.cam0_fixed_camera,
                "cam1_servo_camera": args.cam1_servo_camera,
                "moving_enabled": bool(args.move),
                "frames": args.frames,
                "output_dir": str(output_dir),
                "log": str(log_path),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    controller_context = controller_from_args(args) if args.move else None
    controller = controller_context.__enter__() if controller_context else None
    pid_controller = PidVisualServoController(controller=controller, args=args) if controller is not None else None
    coarse_controller = FixedToServoCoarseController(controller=controller, args=args)
    acquire_scanner = (
        Cam1AcquisitionScanner(controller=controller, motor_limits=controller, args=args) if controller is not None else None
    )
    scanner = SearchScanner(controller=controller, args=args) if controller is not None else None
    scene_scanner = SceneScanner(controller=controller, args=args) if controller is not None else None
    dry_run_controller = DryRunMotorLimits(args) if not args.move else None
    dry_run_pid = (
        PidVisualServoController(controller=dry_run_controller, args=args) if dry_run_controller is not None else None
    )
    dry_run_coarse_controller = FixedToServoCoarseController(controller=None, args=args)
    dry_run_acquire_scanner = (
        Cam1AcquisitionScanner(
            controller=None,
            motor_limits=dry_run_controller,
            args=args,
            dry_run=True,
        )
        if dry_run_controller is not None
        else None
    )
    dry_run_scene_scanner = (
        SceneScanner(controller=dry_run_controller, args=args, dry_run=True) if dry_run_controller is not None else None
    )
    fixed_scene_memory = FixedCameraSceneMemory(max_age_frames=args.fixed_scene_memory_frames)
    cam1_target_memory = RecentTargetMemory(max_age_frames=args.cam1_target_memory_frames)
    cam0_target_memory = RecentTargetMemory(max_age_frames=args.cam0_target_memory_frames)
    hit_streak = 0
    empty_streak = 0
    last_control_key: tuple[str, str] | None = None

    try:
        with (
            RpicamMjpegStream(
                camera_index=args.cam1_servo_camera,
                width=args.width,
                height=args.height,
                framerate=args.framerate,
            ) as cam1_servo_stream,
            RpicamMjpegStream(
                camera_index=args.cam0_fixed_camera,
                width=args.width,
                height=args.height,
                framerate=args.framerate,
            ) as cam0_fixed_stream,
            AudioMonitor(
                device=args.audio_device,
                sample_rate=args.audio_sample_rate,
                vad_aggressiveness=args.vad_aggressiveness,
                active_dbfs=args.audio_active_dbfs,
                backend=args.audio_backend,
            ) as audio_monitor,
            log_path.open("w", encoding="utf-8") as log_file,
        ):
            print(
                json.dumps(
                    {
                        "status": "streams_started",
                        "message": "first frame may take a few seconds while YOLO/ByteTrack initializes",
                        "cam0_fixed_camera": args.cam0_fixed_camera,
                        "cam1_servo_camera": args.cam1_servo_camera,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            frame_index = 0
            while args.frames <= 0 or frame_index < args.frames:
                loop_started = time.monotonic()
                cam0_fixed_frame = cam0_fixed_stream.read(timeout=args.frame_timeout)
                cam1_servo_frame = cam1_servo_stream.read(timeout=args.frame_timeout)
                if cam0_fixed_frame is None:
                    print(json.dumps({"status": "cam0_fixed_frame_timeout", "frame_index": frame_index}, ensure_ascii=False))
                if cam1_servo_frame is None:
                    print(json.dumps({"status": "cam1_servo_frame_timeout", "frame_index": frame_index}, ensure_ascii=False))
                    frame_index += 1
                    continue

                cam0_fixed_obs = None
                if cam0_fixed_frame is not None:
                    cam0_fixed_obs = analyze_camera(
                        cam0_fixed_frame,
                        camera_index=args.cam0_fixed_camera,
                        backend=args.backend,
                        face_min_size=args.face_min_size,
                        yolo_model=args.yolo_model,
                        yolo_conf=args.yolo_conf,
                        yolo_imgsz=args.yolo_imgsz,
                        yolo_device=args.yolo_device,
                        tracker=args.tracker_config if args.backend in {"bytetrack", "botsort"} else None,
                        tracker_namespace=f"cam{args.cam0_fixed_camera}_fixed",
                        person_head_ratio=args.person_head_ratio,
                    )

                cam1_servo_obs = analyze_camera(
                    cam1_servo_frame,
                    camera_index=args.cam1_servo_camera,
                    backend=args.backend,
                    face_min_size=args.face_min_size,
                    yolo_model=args.yolo_model,
                    yolo_conf=args.yolo_conf,
                    yolo_imgsz=args.yolo_imgsz,
                    yolo_device=args.yolo_device,
                    tracker=args.tracker_config if args.backend in {"bytetrack", "botsort"} else None,
                    tracker_namespace=f"cam{args.cam1_servo_camera}_servo",
                    person_head_ratio=args.person_head_ratio,
                )
                cam1_servo_obs = filter_servo_camera_detections(cam1_servo_obs, args=args)
                audio = audio_monitor.latest()
                fixed_scene_scan = fixed_scene_memory.observe(frame_index=frame_index, fixed=cam0_fixed_obs, audio=audio)
                cam0_target_memory.observe(
                    frame_index=frame_index,
                    target=cam0_fixed_obs.target if cam0_fixed_obs is not None else None,
                    source="cam0_fixed",
                )
                cam0_memory_target, cam0_memory = cam0_target_memory.latest(frame_index=frame_index)
                cam1_target_memory.observe(
                    frame_index=frame_index,
                    target=cam1_servo_obs.target,
                    source="cam1_servo",
                )
                cam1_memory_target, cam1_memory = cam1_target_memory.latest(frame_index=frame_index)
                control = choose_fixed_to_servo_target(
                    fixed=cam0_fixed_obs,
                    servo=cam1_servo_obs,
                    audio=audio,
                )
                memory_used_for_control = False
                if cam1_servo_obs.target is None and cam1_memory_target is not None and cam1_memory is not None:
                    age = int(cam1_memory["age_frames"])
                    memory_scale = recent_target_scale(
                        age_frames=age,
                        max_age_frames=args.cam1_target_memory_frames,
                        min_scale=args.cam1_target_memory_min_scale,
                    )
                    control = ControlSelection(
                        target=cam1_memory_target,
                        source="cam1_servo_memory",
                        mode="cam1_fine_visual_servo",
                        scale=memory_scale,
                        reason=f"cam1_recent_target_memory_hold_last_error_age_{age}_frames",
                    )
                    cam1_memory = {**cam1_memory, "used_for_control": True, "control_scale": round(memory_scale, 4)}
                    memory_used_for_control = True
                elif cam1_memory is not None:
                    cam1_memory = {**cam1_memory, "used_for_control": False, "control_scale": None}
                if (
                    control.target is None
                    and cam0_memory_target is not None
                    and cam0_memory is not None
                ):
                    age = int(cam0_memory["age_frames"])
                    memory_scale = recent_target_scale(
                        age_frames=age,
                        max_age_frames=args.cam0_target_memory_frames,
                        min_scale=args.cam0_target_memory_min_scale,
                    )
                    control = ControlSelection(
                        target=cam0_memory_target,
                        source="cam0_fixed_memory",
                        mode="cam0_fixed_coarse_guide",
                        scale=memory_scale,
                        reason=f"cam0_recent_target_memory_continue_cam1_acquisition_age_{age}_frames",
                    )
                    cam0_memory = {**cam0_memory, "used_for_control": True, "control_scale": round(memory_scale, 4)}
                    memory_used_for_control = True
                elif cam0_memory is not None:
                    cam0_memory = {**cam0_memory, "used_for_control": False, "control_scale": None}

                sent_action: dict[str, float] | None = None
                planned_deltas: dict[str, float] | None = None
                search_action: dict[str, float] | None = None
                scene_scan: dict[str, Any] | None = fixed_scene_scan
                move_skipped: str | None = None
                active_scene_scanner = scene_scanner if scene_scanner is not None else dry_run_scene_scanner
                if active_scene_scanner is not None and active_scene_scanner.should_start(frame_index):
                    servo_scene_scan = active_scene_scanner.observe_and_step(
                        frame_index=frame_index,
                        primary=cam1_servo_obs,
                        secondary=cam0_fixed_obs,
                        audio=audio,
                        write=bool(args.move),
                    )
                    scene_scan = {**fixed_scene_scan, "servo_scene_scan": servo_scene_scan}
                    sent_action = servo_scene_scan.get("reposition_action") or servo_scene_scan.get("scan_action")
                    move_skipped = None if args.move else "dry_run_scene_scan"
                    control = ControlSelection(
                        target=control.target,
                        source=control.source,
                        mode="cam1_servo_scene_scan",
                        scale=control.scale,
                        reason="active_scene_scan_select_best_people_view",
                    )
                    if servo_scene_scan.get("completed"):
                        control = ControlSelection(
                            target=control.target,
                            source=control.source,
                            mode=control.mode,
                            scale=control.scale,
                            reason=f"{control.reason}+reposition_to_best_sample",
                        )
                elif control.target is not None:
                    control_key = (control.source, control.mode)
                    if control_key != last_control_key:
                        hit_streak = 0
                        if pid_controller is not None:
                            pid_controller.reset()
                        if dry_run_pid is not None:
                            dry_run_pid.reset()
                        last_control_key = control_key
                    hit_streak += 1
                    empty_streak = 0
                    if control.mode == "cam1_fine_visual_servo":
                        target_for_control = scaled_target(control.target, control.scale)
                        controller_for_plan = pid_controller if controller is not None else dry_run_pid
                        planned_deltas = (
                            controller_for_plan.correction_deltas(target_for_control)
                            if controller_for_plan is not None
                            else None
                        )
                        if controller is not None and pid_controller is not None:
                            if hit_streak >= args.min_hits:
                                sent_action = pid_controller.step_deltas(planned_deltas or {})
                            else:
                                move_skipped = f"waiting_for_{args.min_hits}_consecutive_cam1_visual_hits"
                        else:
                            move_skipped = "dry_run_no_servo_write"
                    elif control.mode == "cam0_fixed_coarse_guide":
                        active_acquire_scanner = acquire_scanner if controller is not None else dry_run_acquire_scanner
                        target_for_control = scaled_target(control.target, control.scale)
                        if active_acquire_scanner is not None:
                            acquire_action = active_acquire_scanner.step(
                                frame_index=frame_index,
                                fixed_target=target_for_control,
                                write=bool(args.move),
                            )
                            planned_deltas = acquire_action.get("planned_deltas")
                            sent_action = acquire_action.get("sent_action")
                            scene_scan = {**fixed_scene_scan, "cam1_acquisition": acquire_action}
                            control = ControlSelection(
                                target=control.target,
                                source=control.source,
                                mode="cam0_guided_cam1_acquire_scan",
                                scale=control.scale,
                                reason="cam0_fixed_target_visible_cam1_scan_until_person_enters_lamp_camera",
                            )
                            move_skipped = None if args.move else "dry_run_no_servo_write"
                        else:
                            controller_for_plan = coarse_controller if controller is not None else dry_run_coarse_controller
                            planned_deltas = controller_for_plan.correction_deltas(target_for_control)
                            if controller is not None:
                                if hit_streak >= args.min_hits:
                                    sent_action = coarse_controller.step(target_for_control)
                                else:
                                    move_skipped = f"waiting_for_{args.min_hits}_consecutive_cam0_fixed_hits"
                            else:
                                move_skipped = "dry_run_no_servo_write"
                    else:
                        move_skipped = control.reason
                else:
                    hit_streak = 0
                    last_control_key = None
                    empty_streak += 1
                    if (
                        args.move
                        and args.search_scan
                        and scanner is not None
                        and empty_streak >= args.scan_start_after
                    ):
                        search_action = scanner.step(write=True)
                        sent_action = search_action
                        move_skipped = None
                        control = ControlSelection(
                            target=None,
                            source=control.source,
                            mode="cam1_safe_search_scan",
                            scale=control.scale,
                            reason=f"{control.reason}+safe_search_scan",
                        )
                    elif args.search_scan and not args.move:
                        move_skipped = "dry_run_search_scan_available"
                    else:
                        move_skipped = control.reason

                elapsed = time.monotonic() - loop_started
                payload = {
                    "status": (
                        "scene_scan_reposition"
                        if scene_scan and scene_scan.get("servo_scene_scan", {}).get("completed")
                        else "scene_scanning"
                        if scene_scan and scene_scan.get("servo_scene_scan")
                        else "target_found"
                        if control.target
                        else ("search_scanning" if search_action else "no_target")
                    ),
                    "frame_index": frame_index,
                    "timestamp": round(time.time(), 3),
                    "loop_seconds": round(elapsed, 3),
                    "cam0_fixed_camera": cam0_fixed_obs.as_dict() if cam0_fixed_obs else None,
                    "cam1_servo_camera": cam1_servo_obs.as_dict(),
                    "primary_camera": cam1_servo_obs.as_dict(),
                    "secondary_camera": cam0_fixed_obs.as_dict() if cam0_fixed_obs else None,
                    "audio": audio,
                    "decision": {
                        "goal": "use_fixed_cam0_for_environment_detection_and_servo_cam1_for_people_centering",
                        "camera_roles": {
                            "cam0": "fixed_environment_camera",
                            "cam1": "servo_controlled_camera",
                            "cam0_fixed_camera_index": args.cam0_fixed_camera,
                            "cam1_servo_camera_index": args.cam1_servo_camera,
                            "primary_camera_field": "cam1_servo_camera",
                            "secondary_camera_field": "cam0_fixed_camera",
                        },
                        "open_source_algorithms": {
                            "detector": "Ultralytics YOLO",
                            "multi_object_tracker": args.tracker_config
                            if args.backend in {"bytetrack", "botsort"}
                            else None,
                            "visual_servo_controller": "simple-pid",
                            "voice_activity_detection": "WebRTC VAD",
                        },
                        "multi_person_policy": {
                            "cam0_fixed": "continuous scene scan; use all visible people to compute a crowd bearing",
                            "cam1_servo": "when people are visible in the lamp-head camera, center the visible crowd directly",
                            "handoff": "cam0 coarse-guides base_yaw/wrist_pitch until cam1 sees the target, then cam1 fine-centers",
                        },
                        "control_source": control.source,
                        "control_mode": control.mode,
                        "control_scale": control.scale,
                        "reason": control.reason,
                        "hit_streak": hit_streak,
                        "cam0_recent_target_memory": cam0_memory,
                        "cam1_recent_target_memory": cam1_memory,
                        "recent_target_memory_used_for_control": memory_used_for_control,
                        "cam1_memory_used_for_control": bool(
                            cam1_memory and cam1_memory.get("used_for_control")
                        ),
                        "moving_enabled": bool(args.move),
                        "motion_mode": args.motion_mode,
                        "target": control.target.as_dict() if control.target else None,
                        "planned_deltas": planned_deltas,
                        "search_action": search_action,
                        "scene_scan": scene_scan,
                        "empty_streak": empty_streak,
                        "sent_action": sent_action,
                        "move_skipped": move_skipped,
                        "motors_considered": list(TRACKING_MOTORS),
                    },
                    "artifacts": {
                        "output_dir": str(output_dir),
                        "log": str(log_path),
                    },
                }
                line = json.dumps(payload, ensure_ascii=False)
                print(line, flush=True)
                log_file.write(line + "\n")
                log_file.flush()

                if args.save_every > 0 and frame_index % args.save_every == 0:
                    save_snapshot(
                        output_dir / f"cam{args.cam1_servo_camera}_servo_{frame_index:04d}.jpg",
                        cam1_servo_frame,
                        cam1_servo_obs,
                        source="cam1_servo",
                    )
                    if cam0_fixed_frame is not None and cam0_fixed_obs is not None:
                        save_snapshot(
                            output_dir / f"cam{args.cam0_fixed_camera}_fixed_{frame_index:04d}.jpg",
                            cam0_fixed_frame,
                            cam0_fixed_obs,
                            source="cam0_fixed",
                        )

                if args.sleep > 0:
                    time.sleep(args.sleep)
                frame_index += 1
    finally:
        if controller_context is not None:
            controller_context.__exit__(None, None, None)
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass

    print(json.dumps({"status": "complete", "output_dir": str(output_dir), "log": str(log_path)}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dual-camera and microphone realtime perception for LeLamp")
    parser.add_argument(
        "--cam1-servo-camera",
        "--primary-camera",
        dest="cam1_servo_camera",
        type=int,
        default=0,
        help="Servo-controlled camera mounted on the lamp head; default camera index 0",
    )
    parser.add_argument(
        "--cam0-fixed-camera",
        "--secondary-camera",
        dest="cam0_fixed_camera",
        type=int,
        default=1,
        help="Fixed environment camera used for scene detection; default camera index 1",
    )
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--framerate", type=float, default=4.0)
    parser.add_argument("--frame-timeout", type=float, default=3.0)
    parser.add_argument("--frames", type=int, default=0, help="Number of frames to process; 0 means run until Ctrl+C")
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--backend", choices=("auto", "face", "yolo", "bytetrack", "botsort"), default="bytetrack")
    parser.add_argument(
        "--tracker-config",
        default="bytetrack.yaml",
        help="Ultralytics tracker config, e.g. bytetrack.yaml or botsort.yaml",
    )
    parser.add_argument("--face-min-size", type=int, default=40)
    parser.add_argument("--yolo-model", default="yolo11n.pt")
    parser.add_argument("--yolo-conf", type=float, default=0.25)
    parser.add_argument("--yolo-imgsz", type=int, default=320)
    parser.add_argument("--yolo-device", default="cpu")
    parser.add_argument("--person-head-ratio", type=float, default=0.22)
    parser.add_argument(
        "--cam1-edge-filter-margin",
        type=int,
        default=8,
        help="Ignore small cam1 person detections touching the frame edge; helps reject fixed objects near the lamp head",
    )
    parser.add_argument(
        "--cam1-edge-filter-max-area-ratio",
        type=float,
        default=0.12,
        help="Maximum frame-area ratio for cam1 edge-touching person detections to reject",
    )
    parser.add_argument("--audio-device", default="auto")
    parser.add_argument("--audio-backend", choices=("arecord", "sounddevice", "auto"), default="arecord")
    parser.add_argument("--audio-sample-rate", type=int, default=48000)
    parser.add_argument("--audio-active-dbfs", type=float, default=-45.0)
    parser.add_argument("--vad-aggressiveness", type=int, choices=(0, 1, 2, 3), default=2)
    parser.add_argument("--output-dir")
    parser.add_argument("--save-every", type=int, default=5)
    parser.add_argument(
        "--fixed-scene-memory-frames",
        type=int,
        default=120,
        help="How many frames of fixed cam0 scene scan history to retain",
    )

    parser.add_argument("--move", action="store_true", help="Actually write 5-servo target positions")
    parser.add_argument(
        "--search-scan",
        action="store_true",
        help="When no person is detected, perform a small safe 5-servo scan around the current pose",
    )
    parser.add_argument("--scan-start-after", type=int, default=3)
    parser.add_argument("--scan-phase-step", type=float, default=0.35)
    parser.add_argument("--scan-max-step", type=float, default=0.8)
    parser.add_argument("--scan-yaw-amplitude", type=float, default=8.0)
    parser.add_argument("--scan-body-pitch-amplitude", type=float, default=2.0)
    parser.add_argument("--scan-elbow-pitch-amplitude", type=float, default=2.0)
    parser.add_argument("--scan-wrist-roll-amplitude", type=float, default=1.2)
    parser.add_argument("--scan-wrist-pitch-amplitude", type=float, default=3.0)
    parser.add_argument(
        "--scene-scan",
        action="store_true",
        help="Additionally move servo cam1 through viewpoints; cam0 fixed scene scan is always active",
    )
    parser.add_argument("--scene-scan-interval-frames", type=int, default=90)
    parser.add_argument("--scene-scan-yaw-offsets", default="-18,-9,0,9,18")
    parser.add_argument("--scene-scan-pitch-offsets", default="-4,0,4")
    parser.add_argument("--scene-scan-max-step", type=float, default=1.2)
    parser.add_argument("--id", default="lelamp", help="Lamp ID")
    parser.add_argument("--port", default="/dev/ttyACM0", help="LeLamp motor serial port")
    parser.add_argument("--motion-mode", choices=("head", "all"), default="head")
    parser.add_argument("--yaw-gain", type=float, default=5.0)
    parser.add_argument("--pitch-gain", type=float, default=4.0)
    parser.add_argument("--pid-yaw-kp", type=float, default=2.6)
    parser.add_argument("--pid-yaw-ki", type=float, default=0.0)
    parser.add_argument("--pid-yaw-kd", type=float, default=0.18)
    parser.add_argument("--pid-pitch-kp", type=float, default=2.0)
    parser.add_argument("--pid-pitch-ki", type=float, default=0.0)
    parser.add_argument("--pid-pitch-kd", type=float, default=0.12)
    parser.add_argument("--max-step", type=float, default=1.2)
    parser.add_argument("--deadband", type=float, default=0.07)
    parser.add_argument("--min-hits", type=int, default=1)
    parser.add_argument("--cam0-to-cam1-yaw-gain", type=float, default=6.0)
    parser.add_argument("--cam0-to-cam1-pitch-gain", type=float, default=3.2)
    parser.add_argument("--cam0-coarse-max-step", type=float, default=3.0)
    parser.add_argument("--cam0-deadband", type=float, default=0.12)
    parser.add_argument("--cam0-base-pitch-gain", type=float, default=0.18)
    parser.add_argument("--cam0-elbow-pitch-gain", type=float, default=0.12)
    parser.add_argument("--cam0-wrist-roll-gain", type=float, default=0.0)
    parser.add_argument("--cam0-wrist-pitch-gain", type=float, default=0.55)
    parser.add_argument("--cam0-to-cam1-yaw-sign", type=float, default=1.0)
    parser.add_argument(
        "--cam1-acquire-anchor",
        choices=("current", "pose"),
        default="current",
        help="Use current head pose for cam1 acquisition by default; pose mode scans around a saved pose",
    )
    parser.add_argument("--cam1-acquire-center-pose", default="workspace/.poses/lelamp_center.json")
    parser.add_argument("--cam1-acquire-yaw-span", type=float, default=48.0)
    parser.add_argument("--cam1-acquire-yaw-step", type=float, default=8.0)
    parser.add_argument("--cam1-acquire-pitch-offsets", default="0,6,12,-6,-12")
    parser.add_argument("--cam1-acquire-max-step", type=float, default=2.0)
    parser.add_argument("--cam1-acquire-hold-frames", type=int, default=4)
    parser.add_argument("--cam1-acquire-goal-tolerance", type=float, default=2.0)
    parser.add_argument("--cam1-acquire-replan-frames", type=int, default=240)
    parser.add_argument(
        "--cam0-target-memory-frames",
        type=int,
        default=90,
        help="Continue cam1 acquisition from the last fixed cam0 person bearing through intermittent cam0 misses",
    )
    parser.add_argument(
        "--cam0-target-memory-min-scale",
        type=float,
        default=0.65,
        help="Minimum cam0 bearing gain while using recent fixed-camera target memory",
    )
    parser.add_argument(
        "--cam1-target-memory-frames",
        type=int,
        default=4,
        help="Continue fine centering from the last cam1 target for this many frames after a brief detection drop",
    )
    parser.add_argument(
        "--cam1-target-memory-min-scale",
        type=float,
        default=0.25,
        help="Minimum fine-centering gain while holding a recent cam1 target through short detection gaps",
    )
    parser.add_argument("--invert-yaw", action="store_true")
    parser.add_argument("--invert-pitch", dest="invert_pitch", action="store_true")
    parser.add_argument("--normal-pitch", dest="invert_pitch", action="store_false")
    parser.set_defaults(invert_pitch=True)
    parser.add_argument("--yaw-min", type=float, default=-85.0)
    parser.add_argument("--yaw-max", type=float, default=85.0)
    parser.add_argument("--pitch-min", type=float, default=-35.0)
    parser.add_argument("--pitch-max", type=float, default=35.0)
    parser.add_argument("--base-pitch-gain", type=float, default=0.3)
    parser.add_argument("--elbow-pitch-gain", type=float, default=0.25)
    parser.add_argument("--wrist-roll-gain", type=float, default=0.12)
    parser.add_argument("--wrist-pitch-gain", type=float, default=0.7)
    parser.add_argument("--base-pitch-min", type=float, default=-98.0)
    parser.add_argument("--base-pitch-max", type=float, default=30.0)
    parser.add_argument("--elbow-pitch-min", type=float, default=20.0)
    parser.add_argument("--elbow-pitch-max", type=float, default=100.0)
    parser.add_argument("--wrist-roll-min", type=float, default=-45.0)
    parser.add_argument("--wrist-roll-max", type=float, default=45.0)
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
