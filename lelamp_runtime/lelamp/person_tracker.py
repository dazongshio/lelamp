from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TRACKING_BACKENDS = ("auto", "face", "hog", "yolo")
MOTION_MODES = ("head", "all")
TARGET_POINTS = ("box-center", "person-head", "face-first")
TRACKING_MOTORS = ("base_yaw", "base_pitch", "elbow_pitch", "wrist_roll", "wrist_pitch")
_YOLO_MODEL_CACHE: dict[str, Any] = {}
DEFAULT_POSE_DIR = Path("workspace/.poses")


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)

    def target_point(self, mode: str, *, person_head_ratio: float) -> tuple[float, float]:
        if mode == "person-head" and self.label == "person":
            ratio = clamp(person_head_ratio, 0.05, 0.5)
            return (self.x + self.width / 2.0, self.y + self.height * ratio)
        return self.center

    @property
    def area(self) -> int:
        return self.width * self.height

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "box": [self.x, self.y, self.width, self.height],
            "center": [round(self.center[0], 2), round(self.center[1], 2)],
            "area": self.area,
        }


@dataclass(frozen=True)
class FrameTarget:
    detection: Detection
    frame_width: int
    frame_height: int
    target_x: float
    target_y: float
    target_label: str
    error_x: float
    error_y: float
    normalized_error_x: float
    normalized_error_y: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "detection": self.detection.as_dict(),
            "frame": {"width": self.frame_width, "height": self.frame_height},
            "target_point": [round(self.target_x, 2), round(self.target_y, 2)],
            "target_label": self.target_label,
            "error_px": [round(self.error_x, 2), round(self.error_y, 2)],
            "error_norm": [round(self.normalized_error_x, 4), round(self.normalized_error_y, 4)],
        }


def detect_faces(frame: Any, *, min_size: int = 40) -> list[Detection]:
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(cascade_path)
    faces = detector.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(min_size, min_size),
    )
    if len(faces) == 0:
        return []
    return [Detection("face", 1.0, int(x), int(y), int(w), int(h)) for x, y, w, h in faces]


def detect_largest_face(frame: Any, *, min_size: int = 40) -> Detection | None:
    faces = detect_faces(frame, min_size=min_size)
    if not faces:
        return None
    return max(faces, key=lambda item: item.area)


def detect_largest_person_hog(frame: Any) -> Detection | None:
    import cv2

    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    boxes, weights = hog.detectMultiScale(
        frame,
        winStride=(8, 8),
        padding=(16, 16),
        scale=1.05,
    )
    if len(boxes) == 0:
        return None

    candidates = [
        Detection("person", float(weights[index]) if len(weights) > index else 1.0, int(x), int(y), int(w), int(h))
        for index, (x, y, w, h) in enumerate(boxes)
    ]
    return max(candidates, key=lambda item: item.area)


def get_yolo_model(model_name: str) -> Any:
    cached = _YOLO_MODEL_CACHE.get(model_name)
    if cached is not None:
        return cached

    from ultralytics import YOLO

    model = YOLO(model_name)
    _YOLO_MODEL_CACHE[model_name] = model
    return model


def detect_largest_person_yolo(
    frame: Any,
    *,
    model_name: str = "yolo11n.pt",
    confidence: float = 0.25,
    image_size: int = 640,
    device: str | None = None,
) -> Detection | None:
    model = get_yolo_model(model_name)
    predict_kwargs: dict[str, Any] = {
        "source": frame,
        "classes": [0],
        "conf": confidence,
        "imgsz": image_size,
        "verbose": False,
    }
    if device:
        predict_kwargs["device"] = device

    results = model.predict(**predict_kwargs)
    if not results:
        return None

    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return None

    candidates: list[Detection] = []
    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy()
    for index, box in enumerate(xyxy):
        x1, y1, x2, y2 = box
        x = max(0, int(round(x1)))
        y = max(0, int(round(y1)))
        width = max(0, int(round(x2 - x1)))
        height = max(0, int(round(y2 - y1)))
        if width <= 0 or height <= 0:
            continue
        candidates.append(Detection("person", float(confs[index]), x, y, width, height))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item.area)


def find_target(
    frame: Any,
    backend: str,
    *,
    face_min_size: int = 40,
    yolo_model: str = "yolo11n.pt",
    yolo_conf: float = 0.25,
    yolo_imgsz: int = 640,
    yolo_device: str | None = None,
    target_point: str = "face-first",
    person_head_ratio: float = 0.2,
) -> FrameTarget | None:
    height, width = frame.shape[:2]
    detection: Detection | None
    if backend == "face":
        detection = detect_largest_face(frame, min_size=face_min_size)
    elif backend == "hog":
        detection = detect_largest_person_hog(frame)
    elif backend == "yolo":
        detection = detect_largest_person_yolo(
            frame,
            model_name=yolo_model,
            confidence=yolo_conf,
            image_size=yolo_imgsz,
            device=yolo_device,
        )
    elif backend == "auto":
        detection = detect_largest_face(frame, min_size=face_min_size) or detect_largest_person_hog(frame)
    else:
        raise ValueError(f"Unsupported backend: {backend}")

    if detection is None:
        return None

    target_x, target_y, target_label = select_target_point(
        frame,
        detection,
        target_point=target_point,
        face_min_size=face_min_size,
        person_head_ratio=person_head_ratio,
    )
    error_x = target_x - width / 2.0
    error_y = target_y - height / 2.0
    return FrameTarget(
        detection=detection,
        frame_width=width,
        frame_height=height,
        target_x=target_x,
        target_y=target_y,
        target_label=target_label,
        error_x=error_x,
        error_y=error_y,
        normalized_error_x=error_x / max(1.0, width / 2.0),
        normalized_error_y=error_y / max(1.0, height / 2.0),
    )


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def point_inside_detection(detection: Detection, point: tuple[float, float]) -> bool:
    x, y = point
    return detection.x <= x <= detection.x + detection.width and detection.y <= y <= detection.y + detection.height


def select_target_point(
    frame: Any,
    detection: Detection,
    *,
    target_point: str,
    face_min_size: int,
    person_head_ratio: float,
) -> tuple[float, float, str]:
    if target_point == "face-first":
        if detection.label == "face":
            x, y = detection.center
            return x, y, "face"

        faces = detect_faces(frame, min_size=max(20, face_min_size))
        if detection.label == "person":
            faces = [face for face in faces if point_inside_detection(detection, face.center)]
        if faces:
            face = max(faces, key=lambda item: item.area)
            x, y = face.center
            return x, y, "face"

        x, y = detection.target_point("person-head", person_head_ratio=person_head_ratio)
        return x, y, "person-head"

    x, y = detection.target_point(target_point, person_head_ratio=person_head_ratio)
    return x, y, target_point


def clamp_soft_goal(current: float, proposed: float, low: float, high: float) -> float:
    if current < low:
        return current if proposed <= current else min(proposed, low)
    if current > high:
        return current if proposed >= current else max(proposed, high)
    return clamp(proposed, low, high)


class LampTargetController:
    def __init__(
        self,
        *,
        port: str,
        lamp_id: str,
        motion_mode: str,
        yaw_gain: float,
        pitch_gain: float,
        max_step: float,
        deadband: float,
        invert_yaw: bool,
        invert_pitch: bool,
        yaw_min: float,
        yaw_max: float,
        pitch_min: float,
        pitch_max: float,
        base_pitch_gain: float,
        elbow_pitch_gain: float,
        wrist_roll_gain: float,
        wrist_pitch_gain: float,
        base_pitch_min: float,
        base_pitch_max: float,
        elbow_pitch_min: float,
        elbow_pitch_max: float,
        wrist_roll_min: float,
        wrist_roll_max: float,
    ):
        from .follower import LeLampFollower, LeLampFollowerConfig

        self.robot = LeLampFollower(
            LeLampFollowerConfig(
                port=port,
                id=lamp_id,
                max_relative_target=max_step,
                disable_torque_on_disconnect=False,
            )
        )
        self.motion_mode = motion_mode
        self.yaw_gain = yaw_gain
        self.pitch_gain = pitch_gain
        self.max_step = max_step
        self.deadband = deadband
        self.invert_yaw = invert_yaw
        self.invert_pitch = invert_pitch
        self.yaw_min = yaw_min
        self.yaw_max = yaw_max
        self.pitch_min = pitch_min
        self.pitch_max = pitch_max
        self.base_pitch_gain = base_pitch_gain
        self.elbow_pitch_gain = elbow_pitch_gain
        self.wrist_roll_gain = wrist_roll_gain
        self.wrist_pitch_gain = wrist_pitch_gain
        self.motor_limits = {
            "base_yaw": (yaw_min, yaw_max),
            "base_pitch": (base_pitch_min, base_pitch_max),
            "elbow_pitch": (elbow_pitch_min, elbow_pitch_max),
            "wrist_roll": (wrist_roll_min, wrist_roll_max),
            "wrist_pitch": (pitch_min, pitch_max),
        }

    def __enter__(self) -> "LampTargetController":
        # Tracking only needs the motor bus and calibration. Avoid the full
        # LeLampFollower.connect() path because it reconfigures/locks every
        # servo on startup, which is fragile during rapid vision experiments.
        self.robot.bus.connect(handshake=True)
        self.robot.bus.enable_torque(list(TRACKING_MOTORS), num_retry=5)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.robot.bus.is_connected:
            self.robot.bus.disconnect(disable_torque=False)

    def correction_deltas(self, target: FrameTarget) -> dict[str, float]:
        yaw_delta = 0.0 if abs(target.normalized_error_x) < self.deadband else target.normalized_error_x * self.yaw_gain
        pitch_delta = (
            0.0 if abs(target.normalized_error_y) < self.deadband else target.normalized_error_y * self.pitch_gain
        )
        if self.invert_yaw:
            yaw_delta = -yaw_delta
        if self.invert_pitch:
            pitch_delta = -pitch_delta
        yaw_delta = clamp(yaw_delta, -self.max_step, self.max_step)
        pitch_delta = clamp(pitch_delta, -self.max_step, self.max_step)

        if self.motion_mode == "head":
            return {
                "base_yaw": yaw_delta,
                "wrist_pitch": pitch_delta,
            }
        if self.motion_mode == "all":
            return {
                "base_yaw": yaw_delta,
                "base_pitch": pitch_delta * self.base_pitch_gain,
                "elbow_pitch": pitch_delta * self.elbow_pitch_gain,
                "wrist_roll": yaw_delta * self.wrist_roll_gain,
                "wrist_pitch": pitch_delta * self.wrist_pitch_gain,
            }
        raise ValueError(f"Unsupported motion mode: {self.motion_mode}")

    def step(self, target: FrameTarget) -> dict[str, float]:
        deltas = self.correction_deltas(target)
        current = self.robot.bus.sync_read("Present_Position", motors=list(deltas))

        goal_pos = {}
        for motor, delta in deltas.items():
            current_value = float(current[motor])
            limit_min, limit_max = self.motor_limits[motor]
            goal_pos[motor] = clamp_soft_goal(current_value, current_value + delta, limit_min, limit_max)

        self.robot.bus.sync_write("Goal_Position", goal_pos)
        return {f"{motor}.pos": value for motor, value in goal_pos.items()}


def open_camera(camera_index: int, *, width: int | None, height: int | None) -> Any:
    import cv2

    camera = cv2.VideoCapture(camera_index)
    if width:
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height:
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if not camera.isOpened():
        raise RuntimeError(f"Unable to open camera index {camera_index}")
    return camera


def connect_motor_bus(args: argparse.Namespace, *, max_step: float | None = None) -> Any:
    from .follower import LeLampFollower, LeLampFollowerConfig

    robot = LeLampFollower(
        LeLampFollowerConfig(
            port=args.port,
            id=args.id,
            max_relative_target=max_step,
            disable_torque_on_disconnect=False,
        )
    )
    bus = robot.bus
    bus.connect(handshake=True)
    return bus


def pose_path(args: argparse.Namespace) -> Path:
    if args.pose_file:
        return Path(args.pose_file)
    return DEFAULT_POSE_DIR / f"{args.pose_name}.json"


def read_current_pose(bus: Any) -> dict[str, float]:
    current = bus.sync_read("Present_Position", motors=list(TRACKING_MOTORS))
    return {motor: float(current[motor]) for motor in TRACKING_MOTORS}


def save_pose(path: Path, pose: dict[str, float], *, lamp_id: str, port: str, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": name,
        "id": lamp_id,
        "port": port,
        "created_at": time.time(),
        "motors": pose,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_pose(path: Path) -> dict[str, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    motors = payload.get("motors", payload)
    missing = [motor for motor in TRACKING_MOTORS if motor not in motors]
    if missing:
        raise RuntimeError(f"Pose file {path} is missing motors: {missing}")
    return {motor: float(motors[motor]) for motor in TRACKING_MOTORS}


def interpolate_pose(
    current: dict[str, float],
    target: dict[str, float],
    *,
    max_step: float,
) -> dict[str, float]:
    next_pose = {}
    for motor in TRACKING_MOTORS:
        delta = target[motor] - current[motor]
        next_pose[motor] = current[motor] + clamp(delta, -max_step, max_step)
    return next_pose


def pose_reached(current: dict[str, float], target: dict[str, float], *, tolerance: float) -> bool:
    return all(abs(target[motor] - current[motor]) <= tolerance for motor in TRACKING_MOTORS)


def run_home(args: argparse.Namespace) -> int:
    path = pose_path(args)
    if args.home_command == "show":
        if not path.exists():
            print(json.dumps({"status": "missing", "pose_file": str(path)}, ensure_ascii=False))
            return 1
        print(path.read_text(encoding="utf-8"))
        return 0

    bus = connect_motor_bus(args, max_step=args.max_step)
    try:
        if args.home_command == "save":
            pose = read_current_pose(bus)
            save_pose(path, pose, lamp_id=args.id, port=args.port, name=args.pose_name)
            print(json.dumps({"status": "saved", "pose_file": str(path), "motors": pose}, ensure_ascii=False))
            return 0

        if args.home_command == "goto":
            if not path.exists():
                raise RuntimeError(f"Pose file does not exist: {path}. Run 'home save' first.")
            target = load_pose(path)
            current = read_current_pose(bus)
            print(
                json.dumps(
                    {"status": "starting", "pose_file": str(path), "current": current, "target": target},
                    ensure_ascii=False,
                )
            )
            for step_index in range(args.steps):
                current = read_current_pose(bus)
                if pose_reached(current, target, tolerance=args.tolerance):
                    print(json.dumps({"status": "reached", "step": step_index, "current": current}, ensure_ascii=False))
                    return 0
                next_pose = interpolate_pose(current, target, max_step=args.max_step)
                bus.sync_write("Goal_Position", next_pose)
                print(json.dumps({"status": "moving", "step": step_index, "target": next_pose}, ensure_ascii=False))
                time.sleep(args.sleep)

            current = read_current_pose(bus)
            status = "reached" if pose_reached(current, target, tolerance=args.tolerance) else "timeout"
            print(json.dumps({"status": status, "current": current, "target": target}, ensure_ascii=False))
            return 0 if status == "reached" else 1

        raise ValueError(f"Unsupported home command: {args.home_command}")
    finally:
        bus.disconnect(disable_torque=False)


def run_relax(args: argparse.Namespace) -> int:
    bus = connect_motor_bus(args, max_step=None)
    try:
        current = read_current_pose(bus)
        bus.disable_torque(num_retry=5)
        print(json.dumps({"status": "torque_disabled", "current": current}, ensure_ascii=False))
    finally:
        bus.disconnect(disable_torque=False)
    return 0


def run_tracker(args: argparse.Namespace) -> int:
    import cv2

    camera = open_camera(args.camera_index, width=args.width, height=args.height)
    controller_context = (
        LampTargetController(
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
        if args.move
        else None
    )

    controller = controller_context.__enter__() if controller_context else None
    frame_count = 0
    hit_streak = 0
    try:
        while args.frames <= 0 or frame_count < args.frames:
            ok, frame = camera.read()
            if not ok:
                print(json.dumps({"status": "camera_read_failed", "frame": frame_count}, ensure_ascii=False))
                return 1

            target = find_target(
                frame,
                args.backend,
                face_min_size=args.face_min_size,
                yolo_model=args.yolo_model,
                yolo_conf=args.yolo_conf,
                yolo_imgsz=args.yolo_imgsz,
                yolo_device=args.yolo_device,
                target_point=args.target_point,
                person_head_ratio=args.person_head_ratio,
            )
            payload: dict[str, Any] = {
                "status": "target_found" if target else "no_target",
                "frame_index": frame_count,
                "backend": args.backend,
                "moving": bool(args.move),
                "motion_mode": args.motion_mode,
                "target_point_mode": args.target_point,
            }
            if target is not None:
                hit_streak += 1
                payload["target"] = target.as_dict()
                payload["hit_streak"] = hit_streak
                if controller is not None and hit_streak >= args.min_hits:
                    payload["sent_action"] = controller.step(target)
                elif controller is not None:
                    payload["move_skipped"] = f"waiting_for_{args.min_hits}_consecutive_hits"
            else:
                hit_streak = 0

            print(json.dumps(payload, ensure_ascii=False))
            frame_count += 1
            if args.sleep > 0:
                time.sleep(args.sleep)
    finally:
        camera.release()
        if controller_context is not None:
            controller_context.__exit__(None, None, None)
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass
    return 0


def run_jog(args: argparse.Namespace) -> int:
    bus = connect_motor_bus(args, max_step=args.max_step)
    try:
        current = bus.sync_read("Present_Position")
        print(json.dumps({"status": "connected", "current": current}, ensure_ascii=False))

        for motor in [args.motor]:
            try:
                bus.enable_torque(motor, num_retry=3)
            except Exception as exc:
                print(json.dumps({"status": "torque_warning", "motor": motor, "error": str(exc)}, ensure_ascii=False))

        target = dict(current)
        target[args.motor] = clamp(float(current[args.motor]) + args.delta, -100.0, 100.0)
        bus.sync_write("Goal_Position", target)
        print(json.dumps({"status": "sent", "target": target}, ensure_ascii=False))
        time.sleep(args.hold)
        after = bus.sync_read("Present_Position")
        print(json.dumps({"status": "after", "current": after}, ensure_ascii=False))
    finally:
        bus.disconnect(disable_torque=False)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Track a person/face in the camera frame and optionally steer LeLamp")
    subparsers = parser.add_subparsers(dest="command")

    track = subparsers.add_parser("track", help="Track camera target and optionally steer LeLamp")
    track.add_argument("--id", default="lelamp", help="Lamp ID")
    track.add_argument("--port", default="/dev/ttyACM0", help="LeLamp motor serial port")
    track.add_argument("--camera-index", type=int, default=0, help="OpenCV camera index")
    track.add_argument("--backend", choices=TRACKING_BACKENDS, default="face")
    track.add_argument("--face-min-size", type=int, default=40, help="Minimum face box size in pixels for Haar detection")
    track.add_argument("--yolo-model", default="yolo11n.pt", help="Ultralytics YOLO model for --backend yolo")
    track.add_argument("--yolo-conf", type=float, default=0.25, help="YOLO person confidence threshold")
    track.add_argument("--yolo-imgsz", type=int, default=640, help="YOLO inference image size")
    track.add_argument("--yolo-device", default=None, help="YOLO device, e.g. cpu, 0, cuda:0")
    track.add_argument(
        "--target-point",
        choices=TARGET_POINTS,
        default="face-first",
        help="Image point to center; face-first tracks a detected face inside the person box before falling back to person-head",
    )
    track.add_argument(
        "--person-head-ratio",
        type=float,
        default=0.2,
        help="Vertical ratio from top of a person box used by --target-point person-head",
    )
    track.add_argument("--frames", type=int, default=30, help="Number of frames to process; 0 means run forever")
    track.add_argument("--sleep", type=float, default=0.05, help="Sleep seconds between frames")
    track.add_argument("--width", type=int)
    track.add_argument("--height", type=int)
    track.add_argument("--move", action="store_true", help="Move LeLamp to center the target")
    track.add_argument(
        "--motion-mode",
        choices=MOTION_MODES,
        default="head",
        help="'head' moves base_yaw+wrist_pitch; 'all' distributes correction across all 5 motors",
    )
    track.add_argument("--yaw-gain", type=float, default=8.0, help="Normalized x-error to motor step gain")
    track.add_argument("--pitch-gain", type=float, default=6.0, help="Normalized y-error to motor step gain")
    track.add_argument("--max-step", type=float, default=4.0, help="Max normalized motor step per frame")
    track.add_argument("--deadband", type=float, default=0.08, help="Ignore target errors smaller than this normalized value")
    track.add_argument("--min-hits", type=int, default=2, help="Consecutive detections required before moving")
    track.add_argument("--invert-yaw", action="store_true", help="Reverse base_yaw correction direction")
    track.add_argument("--invert-pitch", dest="invert_pitch", action="store_true", help="Reverse wrist_pitch correction direction")
    track.add_argument("--normal-pitch", dest="invert_pitch", action="store_false", help="Use non-inverted wrist_pitch correction direction")
    track.set_defaults(invert_pitch=True)
    track.add_argument("--yaw-min", type=float, default=-85.0, help="Soft lower limit for base_yaw normalized position")
    track.add_argument("--yaw-max", type=float, default=85.0, help="Soft upper limit for base_yaw normalized position")
    track.add_argument("--pitch-min", type=float, default=-35.0, help="Soft lower limit for wrist_pitch normalized position")
    track.add_argument("--pitch-max", type=float, default=35.0, help="Soft upper limit for wrist_pitch normalized position")
    track.add_argument("--base-pitch-gain", type=float, default=0.35, help="Share of vertical correction sent to base_pitch in all mode")
    track.add_argument("--elbow-pitch-gain", type=float, default=0.25, help="Share of vertical correction sent to elbow_pitch in all mode")
    track.add_argument("--wrist-roll-gain", type=float, default=0.0, help="Share of yaw correction sent to wrist_roll in all mode")
    track.add_argument("--wrist-pitch-gain", type=float, default=0.65, help="Share of vertical correction sent to wrist_pitch in all mode")
    track.add_argument("--base-pitch-min", type=float, default=-70.0, help="Soft lower limit for base_pitch normalized position")
    track.add_argument("--base-pitch-max", type=float, default=20.0, help="Soft upper limit for base_pitch normalized position")
    track.add_argument("--elbow-pitch-min", type=float, default=30.0, help="Soft lower limit for elbow_pitch normalized position")
    track.add_argument("--elbow-pitch-max", type=float, default=100.0, help="Soft upper limit for elbow_pitch normalized position")
    track.add_argument("--wrist-roll-min", type=float, default=-45.0, help="Soft lower limit for wrist_roll normalized position")
    track.add_argument("--wrist-roll-max", type=float, default=45.0, help="Soft upper limit for wrist_roll normalized position")

    jog = subparsers.add_parser("jog", help="Move one motor by a small normalized delta for hardware testing")
    jog.add_argument("--id", default="lelamp", help="Lamp ID")
    jog.add_argument("--port", default="/dev/ttyACM0", help="LeLamp motor serial port")
    jog.add_argument("--motor", choices=["base_yaw", "base_pitch", "elbow_pitch", "wrist_roll", "wrist_pitch"], default="base_yaw")
    jog.add_argument("--delta", type=float, default=5.0)
    jog.add_argument("--max-step", type=float, default=8.0)
    jog.add_argument("--hold", type=float, default=1.0)

    relax = subparsers.add_parser("relax", help="Disable torque so the lamp can be positioned by hand")
    relax.add_argument("--id", default="lelamp", help="Lamp ID")
    relax.add_argument("--port", default="/dev/ttyACM0", help="LeLamp motor serial port")

    home = subparsers.add_parser("home", help="Save/show/goto a reusable center pose")
    home_subparsers = home.add_subparsers(dest="home_command")

    home_save = home_subparsers.add_parser("save", help="Save the current 5-motor pose as the center pose")
    home_save.add_argument("--id", default="lelamp", help="Lamp ID")
    home_save.add_argument("--port", default="/dev/ttyACM0", help="LeLamp motor serial port")
    home_save.add_argument("--pose-name", default="lelamp_center")
    home_save.add_argument("--pose-file")
    home_save.add_argument("--max-step", type=float, default=8.0)

    home_show = home_subparsers.add_parser("show", help="Print the saved center pose")
    home_show.add_argument("--id", default="lelamp", help="Lamp ID")
    home_show.add_argument("--port", default="/dev/ttyACM0", help="LeLamp motor serial port")
    home_show.add_argument("--pose-name", default="lelamp_center")
    home_show.add_argument("--pose-file")

    home_goto = home_subparsers.add_parser("goto", help="Move all 5 motors back to the saved center pose")
    home_goto.add_argument("--id", default="lelamp", help="Lamp ID")
    home_goto.add_argument("--port", default="/dev/ttyACM0", help="LeLamp motor serial port")
    home_goto.add_argument("--pose-name", default="lelamp_center")
    home_goto.add_argument("--pose-file")
    home_goto.add_argument("--max-step", type=float, default=6.0, help="Max normalized motor step per interpolation tick")
    home_goto.add_argument("--steps", type=int, default=30)
    home_goto.add_argument("--sleep", type=float, default=0.04)
    home_goto.add_argument("--tolerance", type=float, default=1.0)

    raw_args = sys.argv[1:]
    if not raw_args:
        raw_args = ["track"]
    elif raw_args[0] not in {"track", "jog", "home", "relax", "-h", "--help"}:
        raw_args = ["track", *raw_args]
    args = parser.parse_args(raw_args)
    if args.command is None:
        args.command = "track"
    if args.command == "jog":
        return run_jog(args)
    if args.command == "relax":
        return run_relax(args)
    if args.command == "home":
        if args.home_command is None:
            parser.error("home requires a subcommand: save, show, or goto")
        return run_home(args)
    return run_tracker(args)


if __name__ == "__main__":
    raise SystemExit(main())
