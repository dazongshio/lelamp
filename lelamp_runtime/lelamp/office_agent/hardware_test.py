from __future__ import annotations

import argparse
import audioop
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

try:
    from .hardware_probe import resolve_capture_device
except ImportError:
    from lelamp.office_agent.hardware_probe import resolve_capture_device


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def list_audio() -> None:
    for command in (["arecord", "-l"], ["aplay", "-l"]):
        if shutil.which(command[0]) is None:
            print(f"{command[0]} not found")
            continue
        result = run(command)
        print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())


def record_mic(device: str, rate: int, seconds: int, output: Path) -> None:
    if shutil.which("arecord") is None:
        raise RuntimeError("arecord not found")
    selected_device = resolve_capture_device(device)
    result = run(
        [
            "arecord",
            "-D",
            selected_device,
            "-f",
            "S16_LE",
            "-c",
            "1",
            "-r",
            str(rate),
            "-d",
            str(seconds),
            str(output),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    with wave.open(str(output), "rb") as stream:
        frames = stream.readframes(stream.getnframes())
        rms = audioop.rms(frames, stream.getsampwidth())
        peak = audioop.max(frames, stream.getsampwidth())
        duration = stream.getnframes() / stream.getframerate()
    print(
        {
            "file": str(output),
            "rate": rate,
            "configured_device": device,
            "selected_device": selected_device,
            "seconds": round(duration, 2),
            "rms": rms,
            "peak": peak,
        }
    )


def play_speaker(device: str, output: Path) -> None:
    if shutil.which("aplay") is None:
        raise RuntimeError("aplay not found")
    rate = 48000
    duration = 0.6
    freq = 880
    amp = 0.18
    with wave.open(str(output), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        for index in range(int(rate * duration)):
            sample = int(32767 * amp * math.sin(2 * math.pi * freq * index / rate))
            stream.writeframesraw(struct.pack("<hh", sample, sample))
    result = run(["aplay", "-D", device, str(output)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    print(f"played {output} on {device}")


def capture_camera(device_index: int, output: Path) -> None:
    for command in ("rpicam-still", "libcamera-still"):
        if shutil.which(command):
            result = run([command, "-o", str(output), "--timeout", "1000"])
            if result.returncode == 0:
                print(f"captured {output} with {command}")
                return
            print(result.stderr.strip() or result.stdout.strip())

    if shutil.which("gst-launch-1.0"):
        device = f"/dev/video{device_index}"
        result = run(
            [
                "gst-launch-1.0",
                "-q",
                "v4l2src",
                f"device={device}",
                "num-buffers=1",
                "!",
                "image/jpeg,width=1280,height=720,framerate=30/1",
                "!",
                "filesink",
                f"location={output}",
            ]
        )
        if result.returncode == 0 and output.exists():
            print(f"captured {output} with GStreamer from {device}")
            return
        print(result.stderr.strip() or result.stdout.strip())

    try:
        import cv2
    except Exception as exc:
        raise RuntimeError(
            "No camera capture backend found. Install libcamera/rpicam tools or "
            "opencv-python-headless."
        ) from exc

    camera = cv2.VideoCapture(device_index)
    ok, frame = camera.read()
    camera.release()
    if not ok:
        raise RuntimeError(f"Unable to read frame from camera index {device_index}")
    cv2.imwrite(str(output), frame)
    print(f"captured {output} with OpenCV")


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw hardware smoke tests")
    parser.add_argument("--list", action="store_true", help="List ALSA audio devices")
    parser.add_argument("--mic-device", default="auto")
    parser.add_argument("--mic-rate", type=int, default=16000)
    parser.add_argument("--speaker-device", default="hw:3,0")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--skip-mic", action="store_true")
    parser.add_argument("--skip-speaker", action="store_true")
    parser.add_argument("--skip-camera", action="store_true")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.list:
        list_audio()
    if not args.skip_mic:
        record_mic(args.mic_device, args.mic_rate, 3, args.out_dir / "openclaw_mic_test.wav")
    if not args.skip_speaker:
        play_speaker(args.speaker_device, args.out_dir / "openclaw_speaker_test.wav")
    if not args.skip_camera:
        capture_camera(args.camera_index, args.out_dir / "openclaw_camera_test.jpg")


if __name__ == "__main__":
    main()
