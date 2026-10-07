#!/usr/bin/env -S uv run --no-project --offline python
"""Hold a native CEA 720p60 image on a Linux vc4 KMS connector.

No EDID is created, no serial commands are sent, and no display manager is
changed unless --manage-display-manager is explicitly requested. Run with uv.
The RGB asset is headerless, row-major R/G/B, exactly 1280 x 720 x 3 bytes.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes as C
import json
import mmap
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import subprocess
import sys
from typing import Callable, Iterable

WIDTH, HEIGHT = 1280, 720
SOCKET_NAME = "projector.sock"
u16, u32, u64, cint = C.c_uint16, C.c_uint32, C.c_uint64, C.c_int
p32 = C.POINTER(u32)


class ModeInfo(C.Structure):
    _fields_ = [("clock", u32)] + [(name, u16) for name in (
        "hdisplay", "hsync_start", "hsync_end", "htotal", "hskew", "vdisplay",
        "vsync_start", "vsync_end", "vtotal", "vscan")] + [
        ("vrefresh", u32), ("flags", u32), ("type", u32), ("name", C.c_char * 32)]


class Resources(C.Structure):
    _fields_ = [("count_fbs", cint), ("fbs", p32), ("count_crtcs", cint), ("crtcs", p32),
                ("count_connectors", cint), ("connectors", p32),
                ("count_encoders", cint), ("encoders", p32),
                ("min_width", u32), ("max_width", u32), ("min_height", u32), ("max_height", u32)]


class Connector(C.Structure):
    _fields_ = [("connector_id", u32), ("encoder_id", u32), ("connector_type", u32),
                ("connector_type_id", u32), ("connection", cint), ("mmWidth", u32),
                ("mmHeight", u32), ("subpixel", cint), ("count_modes", cint),
                ("modes", C.POINTER(ModeInfo)), ("count_props", cint), ("props", p32),
                ("prop_values", C.POINTER(u64)), ("count_encoders", cint), ("encoders", p32)]


class Encoder(C.Structure):
    _fields_ = [(name, u32) for name in (
        "encoder_id", "encoder_type", "crtc_id", "possible_crtcs", "possible_clones")]


class Crtc(C.Structure):
    _fields_ = [(name, u32) for name in ("crtc_id", "buffer_id", "x", "y", "width", "height")] + [
        ("mode_valid", cint), ("mode", ModeInfo), ("gamma_size", cint)]


CONNECTOR_TYPES = {
    0: "Unknown", 1: "VGA", 2: "DVI-I", 3: "DVI-D", 4: "DVI-A", 5: "Composite",
    6: "SVIDEO", 7: "LVDS", 8: "Component", 9: "DIN", 10: "DP", 11: "HDMI-A",
    12: "HDMI-B", 13: "TV", 14: "eDP", 15: "Virtual", 16: "DSI", 17: "DPI",
    18: "Writeback", 19: "SPI", 20: "USB",
}


def cea720_mode() -> ModeInfo:
    # Linux drm_edid.c CEA VIC 4: positive syncs, progressive, 16:9.
    return ModeInfo(74250, 1280, 1390, 1430, 1650, 0, 720, 725, 730, 750, 0,
                    60, 1 | 4 | (2 << 19), 1 << 5, b"1280x720")


def mode_dict(mode: ModeInfo) -> dict:
    return {"clock_khz": mode.clock,
            "h": [mode.hdisplay, mode.hsync_start, mode.hsync_end, mode.htotal],
            "v": [mode.vdisplay, mode.vsync_start, mode.vsync_end, mode.vtotal],
            "hskew": mode.hskew, "vscan": mode.vscan, "refresh_hz": mode.vrefresh,
            "flags": mode.flags, "name": mode.name.decode("ascii", errors="replace")}


def matches_cea720(mode: dict) -> bool:
    expected = mode_dict(cea720_mode())
    return all(mode.get(key) == expected[key] for key in (
        "clock_khz", "h", "v", "hskew", "vscan", "refresh_hz", "flags"))


def choose_crtc(crtcs: list[int], encoders: Iterable[tuple[int, int]]) -> int:
    """Prefer the current CRTC; possible_crtcs bits index resources.crtcs."""
    encoders = list(encoders)
    for mask, current in encoders:
        for index, crtc in enumerate(crtcs):
            if crtc == current and mask & (1 << index):
                return crtc
    for mask, _ in encoders:
        for index, crtc in enumerate(crtcs):
            if mask & (1 << index):
                return crtc
    raise RuntimeError("No compatible CRTC for the selected connector")


def rgb_to_xr24(rgb: bytes, width: int, height: int, pitch: int) -> bytearray:
    """Convert raw RGB to little-endian XR24, including kernel row padding."""
    if width <= 0 or height <= 0 or len(rgb) != width * height * 3:
        raise ValueError("RGB image size must equal width * height * 3")
    if pitch < width * 4:
        raise ValueError("Framebuffer pitch is smaller than a row of XR24 pixels")
    output = bytearray(pitch * height)
    for y in range(height):
        row = rgb[y * width * 3:(y + 1) * width * 3]
        start, end = y * pitch, y * pitch + width * 4
        output[start:end:4] = row[2::3]
        output[start + 1:end:4] = row[1::3]
        output[start + 2:end:4] = row[0::3]
    return output


def cleanup_all(actions: Iterable[Callable[[], None]]) -> None:
    """Attempt every release operation, retaining the first error."""
    first = None
    for action in actions:
        try:
            action()
        except BaseException as error:
            if first is None:
                first = error
    if first is not None:
        raise first


@contextlib.contextmanager
def managed_display_manager(enabled: bool, runner: Callable = subprocess.run):
    """Restart only the service active before this opt-in operation.

    The outer finally also runs if stopping partially succeeds then raises, or
    an inner KMS cleanup raises. Nothing is queried without the explicit flag.
    """
    unit = None
    if enabled:
        for candidate in ("display-manager.service", "gdm.service", "gdm3.service",
                          "lightdm.service", "sddm.service"):
            result = runner(["systemctl", "is-active", "--quiet", candidate],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if result.returncode == 0:
                result = runner(["systemctl", "show", "--property=Id", "--value", candidate],
                                capture_output=True, text=True, check=True, timeout=10)
                unit = result.stdout.strip() or candidate
                break
    try:
        if unit:
            runner(["systemctl", "stop", unit], check=True, timeout=25)
        yield unit
    finally:
        if unit:
            runner(["systemctl", "start", unit], check=True, timeout=25)


def _read_text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def cards(sysfs: Path = Path("/sys/class/drm"), dev: Path = Path("/dev/dri")) -> list[dict]:
    result = []
    for path in sorted(sysfs.glob("card*")):
        if not re.fullmatch(r"card[0-9]+", path.name):
            continue
        driver = ""
        link = path / "device/driver"
        if link.is_symlink():
            driver = link.resolve().name
        if not driver:
            for line in _read_text(path / "device/uevent").splitlines():
                if line.startswith("DRIVER="):
                    driver = line.partition("=")[2]
        result.append({"card": str(dev / path.name), "driver": driver})
    return result


def select_card(card: str | None, available: list[dict]) -> str:
    if card:
        path = Path(card)
        if not re.fullmatch(r"card[0-9]+", path.name):
            raise ValueError("--card must name a DRM primary node such as /dev/dri/card1")
        return str(path)
    matches = [entry["card"] for entry in available if entry["driver"] == "vc4-drm"]
    if len(matches) != 1:
        raise RuntimeError("Expected one vc4-drm card; use --card to select a primary node")
    return matches[0]


def check_hardware(card: str | None, connector: str,
                   sysfs: Path = Path("/sys/class/drm"), dev: Path = Path("/dev/dri")) -> dict:
    """Read sysfs only: do not open a DRM node, probe modes, or acquire master."""
    available = cards(sysfs, dev)
    selected = select_card(card, available)
    base = sysfs / (Path(selected).name + "-" + connector)
    return {"cards": available, "selected_card": selected,
            "connector": {"name": connector, "present": base.is_dir(),
                          "status": _read_text(base / "status"),
                          "enabled": _read_text(base / "enabled"),
                          "modes": _read_text(base / "modes").splitlines()},
            "drm_master_acquired": False,
            "note": "Connected/enabled does not confirm optical light or a visible picture."}


def state_directory(project: Path, requested: Path | None) -> Path:
    allowed = (project / "tmp").resolve()
    selected = (requested or allowed / "hardware").resolve()
    if selected != allowed and allowed not in selected.parents:
        raise ValueError("State directory must remain inside this project's tmp directory")
    return selected


class LibDrm:
    """Bind the native libdrm ABI only when serve is requested."""
    def __init__(self):
        if sys.platform != "linux" or sys.byteorder != "little":
            raise RuntimeError("Native display requires little-endian Linux and libdrm.so.2")
        if C.sizeof(ModeInfo) != 68 or C.sizeof(Encoder) != 20 or C.sizeof(Crtc) != 100:
            raise RuntimeError("Unexpected native libdrm structure layout")
        if C.sizeof(C.c_void_p) == 8 and (C.sizeof(Resources) != 80 or C.sizeof(Connector) != 88):
            raise RuntimeError("Unexpected 64-bit libdrm structure layout")
        self.library = C.CDLL("libdrm.so.2", use_errno=True)
        definitions = {
            "set_master": ("drmSetMaster", cint, [cint]),
            "drop_master": ("drmDropMaster", cint, [cint]),
            "set_cap": ("drmSetClientCap", cint, [cint, u64, u64]),
            "get_cap": ("drmGetCap", cint, [cint, u64, C.POINTER(u64)]),
            "get_resources": ("drmModeGetResources", C.POINTER(Resources), [cint]),
            "get_connector": ("drmModeGetConnectorCurrent", C.POINTER(Connector), [cint, u32]),
            "get_encoder": ("drmModeGetEncoder", C.POINTER(Encoder), [cint, u32]),
            "get_crtc": ("drmModeGetCrtc", C.POINTER(Crtc), [cint, u32]),
            "free_resources": ("drmModeFreeResources", None, [C.POINTER(Resources)]),
            "free_connector": ("drmModeFreeConnector", None, [C.POINTER(Connector)]),
            "free_encoder": ("drmModeFreeEncoder", None, [C.POINTER(Encoder)]),
            "free_crtc": ("drmModeFreeCrtc", None, [C.POINTER(Crtc)]),
            "create_dumb": ("drmModeCreateDumbBuffer", cint, [cint, u32, u32, u32, u32, p32, p32, C.POINTER(u64)]),
            "map_dumb": ("drmModeMapDumbBuffer", cint, [cint, u32, C.POINTER(u64)]),
            "destroy_dumb": ("drmModeDestroyDumbBuffer", cint, [cint, u32]),
            "add_fb": ("drmModeAddFB", cint, [cint, u32, u32, C.c_uint8, C.c_uint8, u32, u32, p32]),
            "remove_fb": ("drmModeRmFB", cint, [cint, u32]),
            "set_crtc": ("drmModeSetCrtc", cint, [cint, u32, u32, u32, u32, p32, cint, C.POINTER(ModeInfo)]),
        }
        try:
            for name, (symbol, result, args) in definitions.items():
                function = getattr(self.library, symbol)
                function.restype, function.argtypes = result, args
                setattr(self, name, function)
        except AttributeError as error:
            raise RuntimeError("libdrm lacks the modern dumb-buffer API; install a current libdrm2") from error

    @staticmethod
    def check(result: int, action: str) -> None:
        if result != 0:
            error = -result if result < -1 else (C.get_errno() or 1)
            raise OSError(error, action + ": " + os.strerror(error))


class NativeDisplay:
    """Own one KMS framebuffer, restoring the old CRTC when still available."""
    def __init__(self, card: str, connector: str, rgb: bytes):
        self.card, self.connector_name, self.rgb = card, connector, rgb
        self.drm = LibDrm()
        self.fd = None
        self.surface = None
        self.resource_ptr = self.connector_ptr = None
        self.handle, self.pitch, self.size, self.fb = u32(), u32(), u64(), u32()
        self.crtc_id = None
        self.previous = None
        self.previous_connectors = []
        self.mode_active = self.master = False
        self.report = {}

    def __enter__(self):
        try:
            self._start()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *args):
        self.close()

    def _encoder(self, encoder_id: int) -> tuple[int, int]:
        pointer = self.drm.get_encoder(self.fd, encoder_id)
        if not pointer:
            raise RuntimeError("Cannot read DRM encoder")
        try:
            return pointer.contents.possible_crtcs, pointer.contents.crtc_id
        finally:
            self.drm.free_encoder(pointer)

    def _start(self):
        d = self.drm
        self.fd = os.open(self.card, os.O_RDWR | os.O_CLOEXEC)
        try:
            d.check(d.set_master(self.fd), "Acquire DRM master")
        except OSError as error:
            raise RuntimeError("DRM master is busy; stop its owner or explicitly use --manage-display-manager") from error
        self.master = True
        # DRM_CLIENT_CAP_ASPECT_RATIO: otherwise 16:9 mode flags are rejected.
        d.check(d.set_cap(self.fd, 4, 1), "Enable picture aspect flags")
        supported = u64()
        d.check(d.get_cap(self.fd, 1, C.byref(supported)), "Query dumb-buffer support")
        if not supported.value:
            raise RuntimeError("Selected DRM card does not support dumb buffers")
        self.resource_ptr = d.get_resources(self.fd)
        if not self.resource_ptr:
            raise RuntimeError("Cannot read DRM resources")
        resources = self.resource_ptr.contents
        if not (0 < resources.count_crtcs <= 32 and 0 < resources.count_connectors <= 128):
            raise RuntimeError("Unexpected DRM resource counts")
        for index in range(resources.count_connectors):
            pointer = d.get_connector(self.fd, resources.connectors[index])
            if not pointer:
                continue
            connector = pointer.contents
            name = CONNECTOR_TYPES.get(connector.connector_type, "Unknown") + "-" + str(connector.connector_type_id)
            if name == self.connector_name:
                self.connector_ptr = pointer
                break
            d.free_connector(pointer)
        if not self.connector_ptr or self.connector_ptr.contents.connection != 1:
            raise RuntimeError(self.connector_name + " is not connected")
        connector = self.connector_ptr.contents
        if not (0 < connector.count_encoders <= 128):
            raise RuntimeError("Selected connector has no possible encoders")
        encoder_ids = [connector.encoders[index] for index in range(connector.count_encoders)]
        if connector.encoder_id in encoder_ids:
            encoder_ids.remove(connector.encoder_id)
            encoder_ids.insert(0, connector.encoder_id)
        self.crtc_id = choose_crtc([resources.crtcs[index] for index in range(resources.count_crtcs)],
                                  [self._encoder(encoder) for encoder in encoder_ids])
        old = d.get_crtc(self.fd, self.crtc_id)
        if not old:
            raise RuntimeError("Cannot snapshot the selected CRTC")
        try:
            self.previous = Crtc.from_buffer_copy(old.contents)
        finally:
            d.free_crtc(old)
        if self.previous.mode_valid and self.previous.buffer_id:
            for index in range(resources.count_connectors):
                pointer = d.get_connector(self.fd, resources.connectors[index])
                if not pointer:
                    continue
                try:
                    current = pointer.contents
                    if current.encoder_id and self._encoder(current.encoder_id)[1] == self.crtc_id:
                        self.previous_connectors.append(current.connector_id)
                finally:
                    d.free_connector(pointer)
        d.check(d.create_dumb(self.fd, WIDTH, HEIGHT, 32, 0, C.byref(self.handle),
                              C.byref(self.pitch), C.byref(self.size)), "Create dumb framebuffer")
        if self.size.value < self.pitch.value * HEIGHT:
            raise RuntimeError("Kernel returned an undersized dumb buffer")
        offset = u64()
        d.check(d.map_dumb(self.fd, self.handle, C.byref(offset)), "Map dumb framebuffer")
        self.surface = mmap.mmap(self.fd, self.size.value, flags=mmap.MAP_SHARED,
                                 prot=mmap.PROT_READ | mmap.PROT_WRITE, offset=offset.value)
        pixels = rgb_to_xr24(self.rgb, WIDTH, HEIGHT, self.pitch.value)
        self.surface[:len(pixels)] = pixels
        d.check(d.add_fb(self.fd, WIDTH, HEIGHT, 24, 32, self.pitch, self.handle, C.byref(self.fb)),
                "Register XR24 framebuffer")
        requested = cea720_mode()
        output = (u32 * 1)(connector.connector_id)
        d.check(d.set_crtc(self.fd, self.crtc_id, self.fb, 0, 0, output, 1, C.byref(requested)),
                "Set exact CEA 720p60")
        self.mode_active = True
        current = d.get_crtc(self.fd, self.crtc_id)
        if not current:
            raise RuntimeError("Cannot read back the active CRTC")
        try:
            valid = bool(current.contents.mode_valid)
            timing = mode_dict(current.contents.mode)
        finally:
            d.free_crtc(current)
        if not valid or not matches_cea720(timing):
            raise RuntimeError("Active timing differs from CEA 720p60: " + json.dumps(timing))
        self.report = {"state": "active", "card": self.card, "connector": self.connector_name,
                       "connector_id": connector.connector_id, "crtc_id": self.crtc_id,
                       "framebuffer_id": self.fb.value, "mode": timing,
                       "serial_commands_sent": False,
                       "note": "Active KMS timing does not confirm optical light or focus."}

    def _restore_crtc(self):
        d, old = self.drm, self.previous
        if old and old.mode_valid and old.buffer_id and self.previous_connectors:
            outputs = (u32 * len(self.previous_connectors))(*self.previous_connectors)
            d.check(d.set_crtc(self.fd, self.crtc_id, old.buffer_id, old.x, old.y,
                              outputs, len(outputs), C.byref(old.mode)), "Restore previous CRTC")
        else:
            d.check(d.set_crtc(self.fd, self.crtc_id, 0, 0, 0, None, 0, None), "Disable owned CRTC")

    def close(self):
        if self.fd is None:
            return
        d, fd = self.drm, self.fd
        actions = []
        if self.mode_active:
            actions.append(self._restore_crtc)
        if self.fb.value:
            actions.append(lambda: d.check(d.remove_fb(fd, self.fb), "Remove framebuffer"))
        if self.surface is not None:
            actions.append(self.surface.close)
        if self.handle.value:
            actions.append(lambda: d.check(d.destroy_dumb(fd, self.handle), "Destroy dumb buffer"))
        if self.connector_ptr:
            actions.append(lambda: d.free_connector(self.connector_ptr))
        if self.resource_ptr:
            actions.append(lambda: d.free_resources(self.resource_ptr))
        if self.master:
            actions.append(lambda: d.check(d.drop_master(fd), "Drop DRM master"))
        actions.append(lambda: os.close(fd))
        try:
            cleanup_all(actions)
        finally:
            self.fd = None
            self.surface = None
            self.master = self.mode_active = False


def _send_json(client: socket.socket, report: dict):
    client.sendall(json.dumps(report).encode("utf-8") + b"\n")


def _receive_line(client: socket.socket) -> bytes:
    data = bytearray()
    while b"\n" not in data:
        part = client.recv(4096)
        if not part:
            break
        data.extend(part)
        if len(data) > 16384:
            raise ValueError("Control message exceeds the allowed length")
    return bytes(data).partition(b"\n")[0]


class ControlServer:
    """One process owns the socket; stop replies after display restoration."""
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / SOCKET_NAME
        self.server = self.stop_client = None
        self.lock_fd = None
        self.bound = False

    def __enter__(self):
        import fcntl
        if len(os.fsencode(self.path)) > 107:
            raise ValueError("Unix socket path exceeds 107 bytes; choose a shorter project tmp state directory")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_fd = os.open(self.directory / "projector.lock",
                               os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("Another projector display process holds this state directory") from error
            try:
                entry = self.path.lstat()
            except FileNotFoundError:
                entry = None
            if entry is not None:
                if not stat.S_ISSOCK(entry.st_mode):
                    raise RuntimeError("Refusing to replace a non-socket control path")
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(1)
                    try:
                        probe.connect(str(self.path))
                    except (ConnectionRefusedError, FileNotFoundError):
                        self.path.unlink(missing_ok=True)
                    else:
                        raise RuntimeError("Projector control socket is already active")
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server.bind(str(self.path))
            self.bound = True
            os.chmod(self.path, 0o600)
            self.server.listen(4)
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *args):
        self.close()

    def run(self, report: dict, should_stop: Callable[[], bool] = lambda: False):
        while not should_stop():
            if not select.select([self.server], [], [], 1)[0]:
                continue
            client, _ = self.server.accept()
            try:
                client.settimeout(3)
                action = _receive_line(client).decode("ascii")
                if action == "stop":
                    self.stop_client = client
                    return
                _send_json(client, report if action == "status" else {"state": "error", "error": "Use status or stop"})
            except (OSError, UnicodeError, ValueError):
                pass
            finally:
                if client is not self.stop_client:
                    client.close()

    def finish(self, report: dict):
        if self.stop_client:
            try:
                _send_json(self.stop_client, report)
            except OSError:
                pass
            finally:
                self.stop_client.close()
                self.stop_client = None

    def close(self):
        actions = []
        if self.stop_client:
            actions.append(self.stop_client.close)
        if self.server is not None:
            actions.append(self.server.close)
        if self.bound:
            actions.append(lambda: self.path.unlink(missing_ok=True))
        if self.lock_fd is not None:
            actions.append(lambda: os.close(self.lock_fd))
        try:
            cleanup_all(actions)
        finally:
            self.stop_client = self.server = None
            self.lock_fd = None
            self.bound = False


def control_request(directory: Path, action: str) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(40)
        client.connect(str(directory / SOCKET_NAME))
        client.sendall(action.encode("ascii") + b"\n")
        response = _receive_line(client)
        if not response:
            raise RuntimeError("Display process closed without confirming its final state")
        return json.loads(response)



@contextlib.contextmanager
def display_signals():
    """Keep the image across SSH hangup; INT/TERM request orderly cleanup."""
    stopping = [False]
    def interrupted(signum, frame):
        # A signal requests exit; it must never interrupt KMS/service cleanup.
        stopping[0] = True
    old_handlers = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.signal(sig, interrupted)
        if hasattr(signal, "SIGHUP"):
            old_handlers[signal.SIGHUP] = signal.signal(signal.SIGHUP, signal.SIG_IGN)
        yield lambda: stopping[0]
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def serve(args, project: Path):
    rgb = args.image.read_bytes()
    if len(rgb) != WIDTH * HEIGHT * 3:
        raise ValueError("--image must be a headerless 1280 x 720 RGB image (2764800 bytes)")
    card = select_card(args.card, cards())
    directory = state_directory(project, args.state_dir)
    with display_signals() as should_stop:
        with ControlServer(directory) as control:
            error = None
            unit = None
            try:
                with managed_display_manager(args.manage_display_manager) as unit:
                    with NativeDisplay(card, args.connector, rgb) as display:
                        report = dict(display.report, display_manager_temporarily_stopped=unit)
                        try:
                            print(json.dumps(report), flush=True)
                        except OSError:
                            # Losing the SSH terminal must not drop valid video.
                            pass
                        control.run(report, should_stop=should_stop)
            except BaseException as exception:
                error = exception
            finally:
                final = {"state": "error" if error else "stopped",
                         "display_manager_restored": unit if not error else None,
                         "serial_commands_sent": False}
                if error:
                    final["error"] = str(error)
                control.finish(final)
            if error:
                raise error


def parser(project: Path) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = result.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Read DRM sysfs only; do not acquire DRM master")
    start = commands.add_parser("serve", help="Hold an exact CEA 720p60 RGB image until stop or SIGTERM")
    for command in (check, start):
        command.add_argument("--card", help="Primary DRM node; default: auto-detect vc4-drm")
        command.add_argument("--connector", default="HDMI-A-1", help="Connector name (default: HDMI-A-1)")
    start.add_argument("--image", type=Path, default=project / "assets/hardware/projector-test-card.rgb",
                       help="Headerless 1280 x 720 RGB file; default: assets/hardware/projector-test-card.rgb")
    start.add_argument("--manage-display-manager", action="store_true",
                       help="Explicitly stop the currently active display manager and restart it on exit")
    status = commands.add_parser("status", help="Read the running display process status over its project socket")
    stop = commands.add_parser("stop", help="Stop display and wait for cleanup/service restoration confirmation")
    for command in (start, status, stop):
        command.add_argument("--state-dir", type=Path,
                             help="Socket/lock directory inside this project's tmp (default: tmp/hardware)")
    return result


def main(argv=None) -> int:
    project = Path(__file__).resolve().parents[2]
    args = parser(project).parse_args(argv)
    try:
        if args.command == "check":
            print(json.dumps(check_hardware(args.card, args.connector), indent=2))
        elif args.command == "serve":
            serve(args, project)
        else:
            report = control_request(state_directory(project, args.state_dir), args.command)
            print(json.dumps(report, indent=2))
            if report.get("state") == "error":
                return 1
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print("projector_display: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
