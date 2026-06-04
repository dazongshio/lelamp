#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "lelamp_runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

from lelamp.office_agent import hardware_probe


ARECORD_OUTPUT = """\
**** List of CAPTURE Hardware Devices ****
card 0: bcm2835 [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
  Subdevices: 8/8
  Subdevice #0: subdevice #0
card 1: Device [USB PnP Sound Device], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
card 2: ReSpeaker [seeed-8mic-voicecard], device 0: bcm2835-i2s-ac10x-codec0 ac10x-codec0-0 [bcm2835-i2s-ac10x-codec0 ac10x-codec0-0]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""


def assert_ok(name: str, condition: bool, details: object = "") -> None:
    if not condition:
        raise AssertionError(f"{name} failed: {details}")
    print(f"ok - {name}")


def main() -> int:
    devices = hardware_probe.parse_alsa_devices(ARECORD_OUTPUT)
    assert_ok("parse ALSA capture devices", len(devices) == 3, devices)
    assert_ok(
        "preferred capture device favors USB/ReSpeaker over first listed device",
        hardware_probe.preferred_capture_device(devices) == "plughw:1,0",
        devices,
    )
    assert_ok(
        "select audio auto uses preferred capture device",
        hardware_probe.select_audio_device("auto", devices) == "plughw:1,0",
        devices,
    )
    assert_ok(
        "select audio explicit missing device is not auto-replaced",
        hardware_probe.select_audio_device("missing-mic", devices) is None,
        devices,
    )

    original_run_probe = hardware_probe.run_probe
    try:
        hardware_probe.run_probe = lambda command, timeout=3.0: {  # type: ignore[assignment]
            "status": "ok",
            "command": command,
            "returncode": 0,
            "stdout": ARECORD_OUTPUT,
            "stderr": "",
        }
        assert_ok(
            "resolve auto capture device",
            hardware_probe.resolve_capture_device("auto") == "plughw:1,0",
        )
        assert_ok(
            "resolve blank capture device",
            hardware_probe.resolve_capture_device("  ") == "plughw:1,0",
        )
        assert_ok(
            "explicit ALSA capture device passes through",
            hardware_probe.resolve_capture_device("hw:9,3") == "hw:9,3",
        )
        assert_ok(
            "explicit ALSA alias passes through",
            hardware_probe.resolve_capture_device("dsnoop_usb_mic") == "dsnoop_usb_mic",
        )

        hardware_probe.run_probe = lambda command, timeout=3.0: {  # type: ignore[assignment]
            "status": "ok",
            "command": command,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        }
        try:
            hardware_probe.resolve_capture_device("auto")
        except RuntimeError as exc:
            assert_ok("auto without ALSA candidates fails clearly", "No ALSA capture device" in str(exc), str(exc))
        else:
            raise AssertionError("auto without ALSA candidates should fail")

        hardware_probe.run_probe = lambda command, timeout=3.0: {  # type: ignore[assignment]
            "status": "backend_missing",
            "command": command,
            "returncode": None,
            "stdout": "",
            "stderr": "arecord not found",
        }
        try:
            hardware_probe.resolve_capture_device("auto")
        except RuntimeError as exc:
            assert_ok("auto without arecord reports backend missing", "arecord not found" in str(exc), str(exc))
        else:
            raise AssertionError("auto without arecord should fail")
    finally:
        hardware_probe.run_probe = original_run_probe  # type: ignore[assignment]

    print("smoke_audio_device_resolution complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
