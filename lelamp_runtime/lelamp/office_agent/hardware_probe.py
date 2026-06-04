from __future__ import annotations

import audioop
import math
import os
import shutil
import struct
import subprocess
import time
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import OfficeAgentConfig


OUTPUT_LIMIT = 4000
AUTO_CAPTURE_DEVICES = {"", "auto"}


def probe_hardware(config: OfficeAgentConfig, *, projection_preview_port: int) -> dict[str, object]:
    """Run bounded, read-only local probes for the hardware status page."""
    video_devices = sorted(str(path) for path in Path("/dev").glob("video*"))
    serial_devices = sorted(
        str(path)
        for pattern in ("ttyACM*", "ttyUSB*")
        for path in Path("/dev").glob(pattern)
    )
    target_port = str(config.hardware_port)

    v4l2 = run_probe(["v4l2-ctl", "--list-devices"])
    arecord = run_probe(["arecord", "-l"])
    aplay = run_probe(["aplay", "-l"])
    aplay_list = run_probe(["aplay", "-L"])
    pulse_info = run_probe(["pactl", "info"])
    xrandr = run_probe(["xrandr", "--query"], timeout=4)
    kmsprint = run_probe(["kmsprint"], timeout=4)
    vcgencmd_temp = run_probe(["vcgencmd", "measure_temp"])
    vcgencmd_throttled = run_probe(["vcgencmd", "get_throttled"])
    lsusb = run_probe(["lsusb"])
    drm_connectors = read_drm_connectors()

    camera_available = bool(video_devices) or "/dev/video" in str(v4l2.get("stdout", ""))
    capture_devices = parse_alsa_devices(str(arecord.get("stdout", "")))
    playback_devices = parse_alsa_devices(str(aplay.get("stdout", "")))
    mic_device = select_audio_device(config.mic_device, capture_devices)
    mic_auto = config.mic_device.strip() in AUTO_CAPTURE_DEVICES
    playback_names = parse_alsa_pcm_names(str(aplay_list.get("stdout", "")))
    default_sink = parse_pulse_default_sink(str(pulse_info.get("stdout", "")))
    speaker_device = select_playback_device(config.speaker_device, playback_devices, playback_names, default_sink)
    mic_available = mic_device is not None
    speaker_available = speaker_device is not None
    display_connected = any(item.get("status") == "connected" for item in drm_connectors) or " connected" in str(xrandr.get("stdout", ""))
    serial_target_exists = Path(target_port).exists()

    devices = {
        "camera": {
            "status": "available" if camera_available else "unavailable",
            "details": {
                "video_devices": video_devices,
                "v4l2_status": v4l2["status"],
                "probe": "v4l2-ctl --list-devices",
            },
        },
        "mic": {
            "status": "available" if mic_available else ("backend_missing" if arecord["status"] == "backend_missing" else "unavailable"),
            "details": {
                "configured_device": config.mic_device,
                "selected_device": mic_device,
                "configured_device_valid": bool(mic_device) if mic_auto else mic_device == config.mic_device,
                "auto_selected": bool(mic_device) if mic_auto else False,
                "candidates": capture_devices,
                "arecord_status": arecord["status"],
                "probe": "arecord -l",
            },
        },
        "speaker": {
            "status": "available" if speaker_available else ("backend_missing" if aplay["status"] == "backend_missing" else "unavailable"),
            "details": {
                "configured_device": config.speaker_device,
                "selected_device": speaker_device,
                "configured_device_valid": speaker_device == config.speaker_device,
                "candidates": playback_devices,
                "playback_names": playback_names,
                "default_sink": default_sink,
                "aplay_status": aplay["status"],
                "aplay_list_status": aplay_list["status"],
                "pulse_status": pulse_info["status"],
                "probe": "aplay -l",
            },
        },
        "projection": {
            "status": "available" if display_connected else ("unavailable" if drm_connectors else "adapter_ready"),
            "details": {
                "drm_connectors": drm_connectors,
                "xrandr_status": xrandr["status"],
                "kmsprint_status": kmsprint["status"],
                "preview_url": f"http://127.0.0.1:{projection_preview_port}/",
                "physical_projector": "connected" if display_connected else "not_detected",
            },
        },
        "rgb": {
            "status": rgb_status(config.enable_hardware, serial_target_exists, serial_devices),
            "details": {
                "hardware_enabled": config.enable_hardware,
                "configured_port": target_port,
                "configured_port_exists": serial_target_exists,
                "serial_candidates": serial_devices,
                "note": "RGB physical test requires OPENCLAW_ENABLE_HARDWARE=1 and an explicit test button.",
            },
        },
        "usb": {
            "status": "available" if lsusb["status"] == "ok" else lsusb["status"],
            "details": {
                "probe": "lsusb",
                "summary": first_lines(str(lsusb.get("stdout", "")), 12),
            },
        },
    }
    sensors = read_system_sensors(config.workspace_dir, vcgencmd_temp=vcgencmd_temp, vcgencmd_throttled=vcgencmd_throttled)
    summary = {
        "available": sum(1 for item in devices.values() if item["status"] == "available"),
        "adapter_ready": sum(1 for item in devices.values() if item["status"] == "adapter_ready"),
        "backend_missing": sum(1 for item in devices.values() if item["status"] == "backend_missing"),
        "unavailable": sum(1 for item in devices.values() if item["status"] == "unavailable"),
    }
    return {
        "hardware_enabled": config.enable_hardware,
        "scanned_at": datetime.now(UTC).isoformat(),
        "devices": devices,
        "sensors": sensors,
        "probes": {
            "video_devices": video_devices,
            "serial_devices": serial_devices,
            "drm_connectors": drm_connectors,
            "commands": {
                "v4l2": v4l2,
                "arecord": arecord,
                "aplay": aplay,
                "aplay_list": aplay_list,
                "pulse_info": pulse_info,
                "xrandr": xrandr,
                "kmsprint": kmsprint,
                "vcgencmd_temp": vcgencmd_temp,
                "vcgencmd_throttled": vcgencmd_throttled,
                "lsusb": lsusb,
            },
        },
        "scan": {
            "status": "completed",
            "summary": summary,
            "notes": [
                "Scan uses bounded read-only system probes and does not browse user files.",
                "Camera, microphone, speaker, and RGB tests require explicit user-triggered actions.",
            ],
        },
    }


def run_probe(command: list[str], *, timeout: float = 3.0) -> dict[str, object]:
    executable = command[0]
    if shutil.which(executable) is None:
        return {
            "status": "backend_missing",
            "command": command,
            "returncode": None,
            "stdout": "",
            "stderr": f"{executable} not found",
        }
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "error",
            "command": command,
            "returncode": None,
            "stdout": trim(exc.stdout or ""),
            "stderr": "probe timed out",
        }
    return {
        "status": "ok" if result.returncode == 0 else "unavailable",
        "command": command,
        "returncode": result.returncode,
        "stdout": trim(result.stdout),
        "stderr": trim(result.stderr),
    }


def read_drm_connectors() -> list[dict[str, str]]:
    connectors: list[dict[str, str]] = []
    for status_path in sorted(Path("/sys/class/drm").glob("*/status")):
        try:
            value = status_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        connectors.append({"connector": status_path.parent.name, "status": value})
    return connectors


def command_lists_audio_device(result: dict[str, object]) -> bool:
    if result.get("status") != "ok":
        return False
    text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".lower()
    return "card " in text or "device " in text


def parse_alsa_devices(value: str) -> list[dict[str, object]]:
    devices: list[dict[str, object]] = []
    for line in value.splitlines():
        clean = line.strip()
        if not clean.startswith("card ") or ", device " not in clean:
            continue
        try:
            left, right = clean.split(", device ", 1)
            card_index = int(left.split()[1].rstrip(":"))
            card_id = left.split(":", 1)[1].split("[", 1)[0].strip()
            card_name = left.split("[", 1)[1].split("]", 1)[0]
            device_index = int(right.split(":", 1)[0])
            device_name = right.split("[", 1)[1].split("]", 1)[0] if "[" in right else right.split(":", 1)[-1].strip()
        except (IndexError, ValueError):
            continue
        devices.append(
            {
                "card_index": card_index,
                "card_id": card_id,
                "card_name": card_name,
                "device_index": device_index,
                "device_name": device_name,
                "hw": f"hw:{card_index},{device_index}",
                "plughw": f"plughw:{card_index},{device_index}",
                "plughw_by_id": f"plughw:CARD={card_id},DEV={device_index}",
            }
        )
    return devices


def parse_alsa_pcm_names(value: str) -> list[str]:
    names: list[str] = []
    for line in value.splitlines():
        if not line or line.startswith((" ", "\t")):
            continue
        name = line.strip()
        if name and name not in names:
            names.append(name)
    return names


def parse_pulse_default_sink(value: str) -> str:
    for line in value.splitlines():
        if line.lower().startswith("default sink:"):
            return line.split(":", 1)[1].strip()
    return ""


def select_audio_device(configured: str, devices: list[dict[str, object]]) -> str | None:
    normalized = configured.strip()
    if not devices:
        return None
    if normalized in AUTO_CAPTURE_DEVICES:
        return preferred_capture_device(devices)
    if normalized in {"default", "pulse", "sysdefault"}:
        return normalized
    for device in devices:
        if normalized in {str(device.get("hw")), str(device.get("plughw")), str(device.get("plughw_by_id"))}:
            return normalized
    return None


def preferred_capture_device(devices: list[dict[str, object]]) -> str:
    for device in devices:
        haystack = " ".join(
            str(device.get(key, "")).lower()
            for key in ("card_id", "card_name", "device_name")
        )
        if any(marker in haystack for marker in ("usb", "mic", "microphone", "respeaker", "seeed", "array")):
            return str(device.get("plughw") or device.get("hw") or "")
    if devices:
        return str(devices[0].get("plughw") or devices[0].get("hw") or "")
    return ""


def first_plughw_device(devices: list[dict[str, object]]) -> str | None:
    if not devices:
        return None
    return str(devices[0].get("plughw") or devices[0].get("hw"))


def resolve_capture_device(configured: str) -> str:
    """Resolve OPENCLAW_MIC_DEVICE=auto to a concrete ALSA capture device.

    Explicit ALSA names are passed through unchanged so custom aliases such as
    dsnoop devices still work and invalid explicit values fail at arecord.
    """

    normalized = configured.strip()
    if normalized not in AUTO_CAPTURE_DEVICES:
        return normalized
    probe = run_probe(["arecord", "-l"])
    if probe.get("status") == "backend_missing":
        raise RuntimeError("arecord not found")
    if probe.get("status") != "ok":
        message = str(probe.get("stderr") or probe.get("stdout") or "arecord -l failed").strip()
        raise RuntimeError(message)
    selected = preferred_capture_device(parse_alsa_devices(str(probe.get("stdout", ""))))
    if not selected:
        raise RuntimeError("No ALSA capture device was listed by arecord -l")
    return selected


def select_playback_device(
    configured: str,
    devices: list[dict[str, object]],
    pcm_names: list[str] | None = None,
    default_sink: str = "",
) -> str | None:
    normalized = configured.strip()
    selected = select_audio_device(configured, devices)
    if selected and normalized not in AUTO_CAPTURE_DEVICES:
        return selected
    if pcm_names and normalized and normalized in pcm_names:
        return normalized
    if normalized in {"default", "pulse", "sysdefault"} and (pcm_names or devices):
        return normalized
    for name in ("default", "pulse"):
        if pcm_names and name in pcm_names:
            if default_sink or name == "default":
                return name
    if default_sink:
        lowered_sink = default_sink.lower()
        for device in devices:
            haystack = " ".join(
                str(device.get(key, "")).lower()
                for key in ("card_id", "card_name", "device_name", "hw", "plughw", "plughw_by_id")
            )
            if any(part and part in lowered_sink for part in haystack.replace("_", " ").split()):
                return str(device.get("plughw") or device.get("hw"))
    for device in devices:
        card_name = str(device.get("card_name") or "").lower()
        card_id = str(device.get("card_id") or "").lower()
        if any(marker in f"{card_name} {card_id}" for marker in ("usb", "a31", "yundea")):
            return str(device.get("plughw") or device.get("hw"))
    return selected or first_plughw_device(devices)


def rgb_status(enabled: bool, target_exists: bool, serial_devices: list[str]) -> str:
    if enabled and target_exists:
        return "available"
    if enabled and not target_exists:
        return "unavailable"
    if serial_devices:
        return "adapter_ready"
    return "adapter_ready"


def read_system_sensors(
    workspace_dir: Path,
    *,
    vcgencmd_temp: dict[str, object] | None = None,
    vcgencmd_throttled: dict[str, object] | None = None,
) -> dict[str, object]:
    sensors: dict[str, object] = {
        "cpu_temp": None,
        "cpu_usage": None,
        "memory_usage": None,
        "disk_usage": None,
        "power_state": "backend_missing",
        "throttled": None,
    }
    temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        if temp_path.exists():
            sensors["cpu_temp"] = round(int(temp_path.read_text().strip()) / 1000, 1)
    except (OSError, ValueError):
        sensors["cpu_temp"] = None
    if sensors["cpu_temp"] is None and vcgencmd_temp and vcgencmd_temp.get("status") == "ok":
        sensors["cpu_temp"] = parse_vcgencmd_temp(str(vcgencmd_temp.get("stdout", "")))
    try:
        load_one = os.getloadavg()[0]
        cpu_count = os.cpu_count() or 1
        sensors["cpu_usage"] = round(min(1.0, load_one / cpu_count), 4)
    except OSError:
        sensors["cpu_usage"] = None
    try:
        stat = os.statvfs(workspace_dir)
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
        sensors["disk_usage"] = round((total - free) / total, 4) if total else None
    except OSError:
        sensors["disk_usage"] = None
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
        values: dict[str, int] = {}
        for line in meminfo.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                values[parts[0].rstrip(":")] = int(parts[1])
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        sensors["memory_usage"] = round((total - available) / total, 4) if total else None
    except (OSError, ValueError):
        sensors["memory_usage"] = None
    if vcgencmd_throttled and vcgencmd_throttled.get("status") == "ok":
        throttled = str(vcgencmd_throttled.get("stdout", "")).strip()
        sensors["throttled"] = throttled
        sensors["power_state"] = "ok" if throttled.endswith("0x0") else "degraded"
    return sensors


def record_microphone_sample(device: str, rate: int, seconds: int, output: Path) -> dict[str, object]:
    if shutil.which("arecord") is None:
        return {"status": "backend_missing", "message": "arecord not found", "output_path": str(output)}
    try:
        selected_device = resolve_capture_device(device)
    except RuntimeError as exc:
        return {
            "status": "unavailable",
            "message": str(exc),
            "configured_device": device,
            "selected_device": "",
            "output_path": str(output),
        }
    seconds = max(1, min(5, int(seconds)))
    command = [
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
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=seconds + 8)
    if result.returncode != 0:
        return {
            "status": "unavailable",
            "command": command,
            "output_path": str(output),
            "configured_device": device,
            "selected_device": selected_device,
            "stderr": trim(result.stderr),
            "stdout": trim(result.stdout),
        }
    metrics = wave_metrics(output)
    return {
        "status": "completed",
        "command": command,
        "output_path": str(output),
        "configured_device": device,
        "selected_device": selected_device,
        **metrics,
    }


def play_speaker_tone(device: str, output: Path) -> dict[str, object]:
    if shutil.which("aplay") is None:
        return {"status": "backend_missing", "message": "aplay not found", "output_path": str(output)}
    write_tone(output)
    command = ["aplay", "-D", device, str(output)]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=8)
    if result.returncode != 0:
        return {
            "status": "unavailable",
            "command": command,
            "output_path": str(output),
            "stderr": trim(result.stderr),
            "stdout": trim(result.stdout),
        }
    return {
        "status": "completed",
        "command": command,
        "output_path": str(output),
        "duration_seconds": 0.6,
        "message": "Test tone was sent to the server-side audio output, not the browser client.",
    }


def play_audio_file(device: str, audio_path: Path, *, timeout: float = 120.0) -> dict[str, object]:
    if shutil.which("aplay") is None:
        return {"status": "backend_missing", "message": "aplay not found", "audio_path": str(audio_path)}
    if not audio_path.exists():
        return {"status": "unavailable", "message": "audio file not found", "audio_path": str(audio_path)}
    command = ["aplay", "-q", "-D", device, str(audio_path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)
    if result.returncode != 0:
        return {
            "status": "unavailable",
            "command": command,
            "audio_path": str(audio_path),
            "stderr": trim(result.stderr),
            "stdout": trim(result.stdout),
        }
    return {
        "status": "completed",
        "command": command,
        "audio_path": str(audio_path),
        "message": "Audio was played on the server-side speaker device.",
    }


def write_tone(output: Path) -> None:
    rate = 48000
    duration = 0.6
    frequency = 880
    amplitude = 0.18
    with wave.open(str(output), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        for index in range(int(rate * duration)):
            sample = int(32767 * amplitude * math.sin(2 * math.pi * frequency * index / rate))
            stream.writeframesraw(struct.pack("<hh", sample, sample))


def wave_metrics(path: Path) -> dict[str, object]:
    with wave.open(str(path), "rb") as stream:
        frames = stream.readframes(stream.getnframes())
        rms = audioop.rms(frames, stream.getsampwidth()) if frames else 0
        peak = audioop.max(frames, stream.getsampwidth()) if frames else 0
        duration = stream.getnframes() / stream.getframerate() if stream.getframerate() else 0
    return {
        "bytes": path.stat().st_size if path.exists() else 0,
        "duration_seconds": round(duration, 2),
        "rms": rms,
        "peak": peak,
    }


def parse_vcgencmd_temp(value: str) -> float | None:
    marker = "temp="
    if marker not in value:
        return None
    raw = value.split(marker, 1)[1].split("'")[0]
    try:
        return round(float(raw), 1)
    except ValueError:
        return None


def first_lines(value: str, limit: int) -> list[str]:
    return [line for line in value.splitlines()[:limit] if line.strip()]


def trim(value: object) -> str:
    return str(value or "")[:OUTPUT_LIMIT]
