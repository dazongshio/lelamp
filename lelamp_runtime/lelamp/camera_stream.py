from __future__ import annotations

import argparse
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .person_tracker import TARGET_POINTS, TRACKING_BACKENDS, find_target, open_camera


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LeLamp Camera Stream</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, system-ui, sans-serif; }
    body { margin: 0; background: #101113; color: #f3f4f6; }
    main { min-height: 100vh; display: grid; grid-template-rows: auto 1fr; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 18px; border-bottom: 1px solid #2a2d33; background: #17191d; }
    h1 { margin: 0; font-size: 18px; font-weight: 650; }
    #status { font-size: 13px; color: #b8beca; white-space: nowrap; }
    .stage { display: grid; place-items: center; padding: 16px; }
    img { width: min(100%, 1280px); max-height: calc(100vh - 92px); object-fit: contain; background: #050506; border: 1px solid #2a2d33; }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>LeLamp Camera Stream</h1>
      <div id="status">connecting...</div>
    </header>
    <section class="stage">
      <img src="/stream.mjpg" alt="LeLamp camera stream">
    </section>
  </main>
  <script>
    async function updateStatus() {
      try {
        const res = await fetch('/status.json', { cache: 'no-store' });
        const data = await res.json();
        const target = data.target
          ? `${data.target.target_label} err=${data.target.error_norm.join(', ')} point=${data.target.target_point.join(', ')}`
          : 'no target';
        document.getElementById('status').textContent = `fps=${data.fps.toFixed(1)} backend=${data.backend} ${target}`;
      } catch {
        document.getElementById('status').textContent = 'status unavailable';
      }
    }
    setInterval(updateStatus, 500);
    updateStatus();
  </script>
</body>
</html>
"""


class CameraStreamState:
    def __init__(
        self,
        *,
        camera_index: int,
        backend: str,
        face_min_size: int,
        yolo_model: str,
        yolo_conf: float,
        yolo_imgsz: int,
        yolo_device: str | None,
        target_point: str,
        person_head_ratio: float,
        width: int | None,
        height: int | None,
        jpeg_quality: int,
    ):
        self.camera_index = camera_index
        self.backend = backend
        self.face_min_size = face_min_size
        self.yolo_model = yolo_model
        self.yolo_conf = yolo_conf
        self.yolo_imgsz = yolo_imgsz
        self.yolo_device = yolo_device
        self.target_point = target_point
        self.person_head_ratio = person_head_ratio
        self.width = width
        self.height = height
        self.jpeg_quality = jpeg_quality
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._running = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest_jpeg: bytes | None = None
        self._latest_status: dict[str, Any] = {
            "status": "starting",
            "backend": backend,
            "fps": 0.0,
            "target": None,
            "frame_index": 0,
        }

    def start(self) -> None:
        if self._thread is not None:
            return
        self._running.set()
        self._thread = threading.Thread(target=self._capture_loop, name="lelamp-camera-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def latest_status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._latest_status)

    def wait_for_frame(self, last_frame_index: int, timeout: float = 2.0) -> tuple[bytes | None, int]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._latest_status.get("frame_index", 0) == last_frame_index and self._running.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            return self._latest_jpeg, int(self._latest_status.get("frame_index", last_frame_index))

    def _capture_loop(self) -> None:
        import cv2

        camera = None
        try:
            camera = open_camera(self.camera_index, width=self.width, height=self.height)
            frame_index = 0
            last_fps_at = time.monotonic()
            frames_since_fps = 0
            fps = 0.0
            while self._running.is_set():
                ok, frame = camera.read()
                if not ok:
                    self._set_status({"status": "camera_read_failed", "backend": self.backend, "fps": fps})
                    time.sleep(0.2)
                    continue

                target = find_target(
                    frame,
                    self.backend,
                    face_min_size=self.face_min_size,
                    yolo_model=self.yolo_model,
                    yolo_conf=self.yolo_conf,
                    yolo_imgsz=self.yolo_imgsz,
                    yolo_device=self.yolo_device,
                    target_point=self.target_point,
                    person_head_ratio=self.person_head_ratio,
                )
                annotated = draw_overlay(frame, target, backend=self.backend, fps=fps)
                ok, encoded = cv2.imencode(
                    ".jpg",
                    annotated,
                    [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
                )
                if not ok:
                    continue

                frames_since_fps += 1
                now = time.monotonic()
                elapsed = now - last_fps_at
                if elapsed >= 1.0:
                    fps = frames_since_fps / elapsed
                    frames_since_fps = 0
                    last_fps_at = now

                status = {
                    "status": "target_found" if target else "no_target",
                    "backend": self.backend,
                    "fps": round(fps, 2),
                    "target": target.as_dict() if target else None,
                    "frame_index": frame_index,
                    "camera_index": self.camera_index,
                }
                with self._condition:
                    self._latest_jpeg = encoded.tobytes()
                    self._latest_status = status
                    self._condition.notify_all()
                frame_index += 1
        except Exception as exc:
            self._set_status(
                {
                    "status": "error",
                    "backend": self.backend,
                    "fps": 0.0,
                    "error": f"{type(exc).__name__}: {exc}",
                    "target": None,
                }
            )
        finally:
            if camera is not None:
                camera.release()

    def _set_status(self, status: dict[str, Any]) -> None:
        with self._condition:
            previous_frame = int(self._latest_status.get("frame_index", 0))
            self._latest_status = {**status, "frame_index": previous_frame + 1}
            self._condition.notify_all()


def draw_overlay(frame: Any, target: Any, *, backend: str, fps: float) -> Any:
    import cv2

    height, width = frame.shape[:2]
    annotated = frame.copy()
    cx = width // 2
    cy = height // 2
    cv2.line(annotated, (cx - 18, cy), (cx + 18, cy), (80, 220, 255), 1)
    cv2.line(annotated, (cx, cy - 18), (cx, cy + 18), (80, 220, 255), 1)

    label = f"{backend} fps={fps:.1f}"
    status_color = (180, 180, 180)
    if target is not None:
        detection = target.detection
        x, y, w, h = detection.x, detection.y, detection.width, detection.height
        tx, ty = int(target.target_x), int(target.target_y)
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (70, 230, 120), 2)
        cv2.circle(annotated, (tx, ty), 4, (70, 230, 120), -1)
        cv2.line(annotated, (cx, cy), (tx, ty), (70, 230, 120), 2)
        label = (
            f"{target.target_label} conf={detection.confidence:.2f} "
            f"err=({target.normalized_error_x:.2f},{target.normalized_error_y:.2f}) fps={fps:.1f}"
        )
        status_color = (70, 230, 120)

    cv2.rectangle(annotated, (8, 8), (min(width - 8, 620), 42), (20, 20, 20), -1)
    cv2.putText(annotated, label, (18, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.62, status_color, 2, cv2.LINE_AA)
    return annotated


class CameraStreamHandler(BaseHTTPRequestHandler):
    server_version = "LeLampCameraStream/0.1"

    @property
    def stream_state(self) -> CameraStreamState:
        return self.server.stream_state  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            self._send_bytes(INDEX_HTML.encode("utf-8"), content_type="text/html; charset=utf-8")
        elif self.path == "/status.json":
            payload = json.dumps(self.stream_state.latest_status(), ensure_ascii=False).encode("utf-8")
            self._send_bytes(payload, content_type="application/json; charset=utf-8")
        elif self.path == "/snapshot.jpg":
            jpeg = self.stream_state.latest_jpeg()
            if jpeg is None:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "No camera frame yet")
                return
            self._send_bytes(jpeg, content_type="image/jpeg")
        elif self.path == "/stream.mjpg":
            self._send_mjpeg()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_HEAD(self) -> None:
        if self.path in {"/", "/index.html"}:
            self._send_headers(HTTPStatus.OK, content_type="text/html; charset=utf-8", content_length=len(INDEX_HTML.encode("utf-8")))
        elif self.path == "/status.json":
            payload = json.dumps(self.stream_state.latest_status(), ensure_ascii=False).encode("utf-8")
            self._send_headers(HTTPStatus.OK, content_type="application/json; charset=utf-8", content_length=len(payload))
        elif self.path == "/snapshot.jpg":
            jpeg = self.stream_state.latest_jpeg()
            if jpeg is None:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "No camera frame yet")
                return
            self._send_headers(HTTPStatus.OK, content_type="image/jpeg", content_length=len(jpeg))
        elif self.path == "/stream.mjpg":
            self._send_headers(HTTPStatus.OK, content_type="multipart/x-mixed-replace; boundary=frame")
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _send_bytes(self, payload: bytes, *, content_type: str) -> None:
        self._send_headers(HTTPStatus.OK, content_type=content_type, content_length=len(payload))
        self.wfile.write(payload)

    def _send_headers(self, status: HTTPStatus, *, content_type: str, content_length: int | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if content_length is not None:
            self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _send_mjpeg(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last_frame = -1
        while True:
            jpeg, frame_index = self.stream_state.wait_for_frame(last_frame)
            if jpeg is None:
                continue
            last_frame = frame_index
            try:
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii"))
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.server.verbose:  # type: ignore[attr-defined]
            super().log_message(fmt, *args)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve LeLamp camera output as an annotated MJPEG browser stream")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--backend", choices=TRACKING_BACKENDS, default="face")
    parser.add_argument("--face-min-size", type=int, default=40)
    parser.add_argument("--yolo-model", default="yolo11n.pt")
    parser.add_argument("--yolo-conf", type=float, default=0.25)
    parser.add_argument("--yolo-imgsz", type=int, default=640)
    parser.add_argument("--yolo-device", default=None)
    parser.add_argument("--target-point", choices=TARGET_POINTS, default="face-first")
    parser.add_argument("--person-head-ratio", type=float, default=0.2)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--jpeg-quality", type=int, default=82)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    state = CameraStreamState(
        camera_index=args.camera_index,
        backend=args.backend,
        face_min_size=args.face_min_size,
        yolo_model=args.yolo_model,
        yolo_conf=args.yolo_conf,
        yolo_imgsz=args.yolo_imgsz,
        yolo_device=args.yolo_device,
        target_point=args.target_point,
        person_head_ratio=args.person_head_ratio,
        width=args.width,
        height=args.height,
        jpeg_quality=max(30, min(95, args.jpeg_quality)),
    )
    state.start()

    server = ThreadingHTTPServer((args.host, args.port), CameraStreamHandler)
    server.stream_state = state  # type: ignore[attr-defined]
    server.verbose = args.verbose  # type: ignore[attr-defined]

    print(f"LeLamp camera stream: http://{args.host}:{args.port}/")
    print(f"Local URL: http://127.0.0.1:{args.port}/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping camera stream.")
    finally:
        server.server_close()
        state.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
