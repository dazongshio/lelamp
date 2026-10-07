#!/usr/bin/env python3
"""Preview or apply the verified Pi 5 hardware profile; execute with uv run.

Only stdlib is required. Backups stay in the checkout's tmp directory.
No reboot, service restart, udev trigger, serial command, or motor movement.
"""

import argparse
from datetime import datetime
import difflib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "config/hardware/raspberry-pi-5"
BEGIN = "# BEGIN LELAMP PI5 HARDWARE PROFILE"
END = "# END LELAMP PI5 HARDWARE PROFILE"
MANAGED = "# Managed by LeLamp hardware profile."
INACTIVE_SECTIONS = {
    "none", "pi0", "pi02", "pi1", "pi2", "pi3", "pi3+", "pi4", "cm4", "cm5"
}
OVERLAY = re.compile(r"^dtoverlay\s*=\s*(imx219|imx290|vc4-kms-v3d)(?:,(.*))?$")


def merge_boot_config(original: str) -> str:
    """Merge active Pi 5 settings, retaining Ubuntu boot paths and other devices.

    Includes and unknown conditional filters require manual review because
    reading one file cannot establish which camera overlays are already active.
    """
    kept = []
    active = True
    section = "all"
    in_block = False
    vc4_options = None
    for number, line in enumerate(original.splitlines(keepends=True), 1):
        stripped = line.strip()
        if stripped == BEGIN:
            if in_block:
                raise ValueError("Nested LeLamp managed block")
            in_block = True
            continue
        if stripped == END:
            if not in_block:
                raise ValueError("LeLamp managed block has no opening marker")
            in_block = False
            # The managed block sets [all], so this remains the effective filter.
            section, active = "all", True
            continue
        if in_block:
            # Preserve existing extra KMS parameters across repeated application.
            parsed = OVERLAY.fullmatch(stripped.split("#", 1)[0].strip())
            if parsed and parsed.group(1) == "vc4-kms-v3d":
                vc4_options = tuple(
                    option.strip() for option in (parsed.group(2) or "").split(",")
                    if option.strip() and option.strip() != "noaudio"
                )
            continue
        statement = stripped.split("#", 1)[0].strip()
        if statement.startswith("[") and statement.endswith("]"):
            section = statement[1:-1]
            active = True if section in {"all", "pi5"} else False if section in INACTIVE_SECTIONS else None
            kept.append(line)
            continue
        if re.match(r"^include\s+", statement) and active is not False:
            raise ValueError(f"Line {number}: active include requires manual review: {statement}")
        overlay = OVERLAY.fullmatch(statement)
        managed = bool(overlay or re.match(r"^camera_auto_detect\s*=", statement))
        if managed and active is None:
            raise ValueError(f"Line {number}: unknown condition [{section}] controls a managed setting")
        if managed and active:
            if overlay and overlay.group(1) == "vc4-kms-v3d":
                options = tuple(
                    option.strip() for option in (overlay.group(2) or "").split(",")
                    if option.strip() and option.strip() != "noaudio"
                )
                if vc4_options is not None and options != vc4_options:
                    raise ValueError("Multiple active vc4-kms-v3d overlays use different parameters")
                vc4_options = options
            continue
        kept.append(line)
    if in_block:
        raise ValueError("LeLamp managed block has no closing marker")
    kms = ",".join(("vc4-kms-v3d", *(vc4_options or ()), "noaudio"))
    block = "\n".join((
        BEGIN, "[all]", "camera_auto_detect=0", "dtoverlay=imx290,cam0",
        "dtoverlay=imx219", f"dtoverlay={kms}", END, ""
    ))
    prefix = "".join(kept).rstrip("\r\n")
    return (prefix + "\n\n" if prefix else "") + block


def run(command):
    print("Run:", " ".join(map(str, command)), flush=True)
    subprocess.run(command, check=True, env={**os.environ, "TMPDIR": str(ROOT / "tmp")})


def plan_files(boot_config):
    original = boot_config.read_text(encoding="utf-8")
    planned = [
        (boot_config, merge_boot_config(original)),
        (Path("/etc/udev/rules.d/99-lelamp-hardware.rules"),
         (PROFILE / "99-lelamp-hardware.rules").read_text(encoding="utf-8")),
        (Path("/etc/systemd/system/lelamp-web-console.service.d/90-lelamp-hardware.conf"),
         (PROFILE / "lelamp-web-console.conf").read_text(encoding="utf-8")),
    ]
    for target, content in planned[1:]:
        if target.exists() and MANAGED not in target.read_text(encoding="utf-8"):
            raise ValueError(f"Refusing to replace an unmanaged configuration: {target}")
    return planned


def new_backup_directory():
    base = ROOT / "tmp" / ("hardware-profile-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    candidate = base
    for number in range(100):
        try:
            candidate.mkdir(parents=True, mode=0o700)
            return candidate
        except FileExistsError:
            candidate = base.with_name(base.name + f"-{number + 1}")
    raise RuntimeError("Unable to allocate a new project-local backup directory")


def apply_files(planned):
    changed = []
    for target, content in planned:
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            changed.append((target, content))
    if not changed:
        print("Configuration files already match this hardware profile.")
        return
    backup = new_backup_directory()
    # Save every existing target before changing any file.
    for target, _ in changed:
        if target.exists():
            saved = backup / target.relative_to(target.anchor)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
    print(f"Configuration backups: {backup}")
    for target, content in changed:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if target != planned[0][0]:
            target.chmod(0o644)
        print(f"Installed: {target}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write system files and save the A311 PCM volume at 60%%")
    parser.add_argument("--boot-config", type=Path, default=Path("/boot/firmware/config.txt"), help="Boot config to preview (or apply)")
    args = parser.parse_args(argv)
    try:
        if args.apply:
            if not sys.platform.startswith("linux") or os.geteuid() != 0:
                raise ValueError("Applying this profile requires root on the target Raspberry Pi")
            model_file = Path("/proc/device-tree/model")
            model = model_file.read_text(encoding="utf-8").rstrip("\x00") if model_file.exists() else ""
            if "Raspberry Pi 5" not in model:
                raise ValueError(f"This profile is only for Raspberry Pi 5; detected: {model or 'unknown'}")
            for command in ("amixer", "alsactl", "systemctl", "udevadm"):
                if not shutil.which(command):
                    raise ValueError(f"Required command not found: {command}")
            # Check the card/control before changing boot or service files.
            run(["amixer", "-c", "A311", "sget", "PCM"])
        planned = plan_files(args.boot_config)
        for target, content in planned:
            before = target.read_text(encoding="utf-8") if target.exists() else ""
            diff = "".join(difflib.unified_diff(
                before.splitlines(keepends=True), content.splitlines(keepends=True),
                fromfile=str(target), tofile=str(target) + " (profile)"
            ))
            print(diff or f"Unchanged: {target}")
        if not args.apply:
            print("Preview only; no system files changed.")
            print("With --apply: PCM 60% unmute, alsactl store, daemon-reload and udev rule reload.")
            print("No reboot, web service restart, udev trigger, or projector power command.")
            return 0
        apply_files(planned)
        run(["amixer", "-c", "A311", "sset", "PCM", "60%", "unmute"])
        run(["alsactl", "store", "A311"])
        run(["systemctl", "daemon-reload"])
        run(["udevadm", "control", "--reload-rules"])
        print("Profile installed. Camera/HDMI boot settings take effect after a later reboot.")
        print("New udev aliases appear on a later device reconnect; connected devices are not retriggered.")
        print("Restart the web service after aliases exist to apply its environment and access groups.")
        print("Keep the projector serial connection open while projecting; shut it down and wait at least 15 seconds before rebooting.")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Profile not completed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
