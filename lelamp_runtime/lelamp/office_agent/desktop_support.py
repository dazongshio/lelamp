from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path

def normalize_url_or_search(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return "about:blank"
    if re.match(r"^https?://", cleaned, re.I):
        return cleaned
    if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", cleaned, re.I):
        return f"https://{cleaned}"
    query = urllib.parse.urlencode({"q": cleaned})
    return f"https://www.google.com/search?{query}"


def discover_x11_display() -> str:
    candidates = []
    if os.getenv("DISPLAY"):
        candidates.append(str(os.getenv("DISPLAY")))
    x11_dir = Path("/tmp/.X11-unix")
    if x11_dir.is_dir():
        for path in sorted(x11_dir.glob("X*")):
            suffix = path.name.removeprefix("X")
            if suffix.isdigit():
                candidates.append(f":{suffix}")
    seen: set[str] = set()
    for display in candidates:
        if display in seen:
            continue
        seen.add(display)
        if xtest_available(display):
            return display
    return ""


def xtest_available(display: str) -> bool:
    try:
        x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
        ctypes.cdll.LoadLibrary("libXtst.so.6")
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        handle = x11.XOpenDisplay(display.encode())
        if not handle:
            return False
        x11.XCloseDisplay(handle)
        return True
    except Exception:
        return False


def xtest_mouse_move(display: str, x: int, y: int, *, relative: bool = False) -> dict[str, object]:
    return _with_xtest_display(display, lambda x11, xtst, handle: _xtest_mouse_move(x11, xtst, handle, x, y, relative=relative))


def xtest_mouse_click(display: str, button: int) -> dict[str, object]:
    def run(x11: ctypes.CDLL, xtst: ctypes.CDLL, handle: int) -> dict[str, object]:
        xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeButtonEvent.restype = ctypes.c_int
        down = xtst.XTestFakeButtonEvent(handle, int(button), 1, 0)
        up = xtst.XTestFakeButtonEvent(handle, int(button), 0, 0)
        x11.XFlush(handle)
        return {"status": "completed" if down and up else "blocked", "button_down": int(down), "button_up": int(up)}

    return _with_xtest_display(display, run)


def xtest_hotkey(display: str, hotkey: str) -> dict[str, object]:
    keys = [part for part in hotkey.split("+") if part]
    if not keys:
        return {"status": "blocked", "message": "empty hotkey", "display": display}

    def run(x11: ctypes.CDLL, xtst: ctypes.CDLL, handle: int) -> dict[str, object]:
        keycodes = [_keysym_to_keycode(x11, handle, key) for key in keys]
        if not all(keycodes):
            return {"status": "blocked", "keycodes": keycodes, "message": "one or more keys could not be resolved"}
        xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeKeyEvent.restype = ctypes.c_int
        downs = [int(xtst.XTestFakeKeyEvent(handle, code, 1, 0)) for code in keycodes]
        ups = [int(xtst.XTestFakeKeyEvent(handle, code, 0, 0)) for code in reversed(keycodes)]
        x11.XFlush(handle)
        completed = all(downs) and all(ups)
        return {"status": "completed" if completed else "blocked", "keys": keys, "keycodes": keycodes, "downs": downs, "ups": ups}

    return _with_xtest_display(display, run)


def xtest_key_tap(display: str, key: str) -> dict[str, object]:
    return xtest_hotkey(display, key)


def _xtest_mouse_move(x11: ctypes.CDLL, xtst: ctypes.CDLL, handle: int, x: int, y: int, *, relative: bool) -> dict[str, object]:
    if relative:
        xtst.XTestFakeRelativeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeRelativeMotionEvent.restype = ctypes.c_int
        moved = xtst.XTestFakeRelativeMotionEvent(handle, int(x), int(y), 0)
    else:
        xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeMotionEvent.restype = ctypes.c_int
        moved = xtst.XTestFakeMotionEvent(handle, -1, int(x), int(y), 0)
    x11.XFlush(handle)
    return {"status": "completed" if moved else "blocked", "moved": int(moved), "relative": relative}


def _with_xtest_display(display: str, callback: object) -> dict[str, object]:
    try:
        x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
        xtst = ctypes.cdll.LoadLibrary("libXtst.so.6")
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        handle = x11.XOpenDisplay(display.encode())
        if not handle:
            return {"status": "blocked", "message": f"Cannot open display {display}."}
        try:
            result = callback(x11, xtst, handle)  # type: ignore[misc]
        finally:
            x11.XCloseDisplay(handle)
        return {"display": display, **result}
    except Exception as exc:
        return {"status": "blocked", "message": str(exc)[:500], "display": display}


def _keysym_to_keycode(x11: ctypes.CDLL, handle: int, key: str) -> int:
    aliases = {
        "ctrl": "Control_L",
        "control": "Control_L",
        "alt": "Alt_L",
        "shift": "Shift_L",
        "super": "Super_L",
        "return": "Return",
        "enter": "Return",
        "escape": "Escape",
        "esc": "Escape",
        "tab": "Tab",
        "space": "space",
    }
    name = aliases.get(key.lower(), key)
    x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
    x11.XStringToKeysym.restype = ctypes.c_ulong
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_ubyte
    keysym = x11.XStringToKeysym(name.encode())
    if not keysym and len(name) == 1:
        keysym = x11.XStringToKeysym(name.lower().encode())
    if not keysym:
        return 0
    return int(x11.XKeysymToKeycode(handle, keysym))


def _preflight_xtest_display(preflight: dict[str, object]) -> str:
    backends = preflight.get("input_backends") if isinstance(preflight.get("input_backends"), list) else []
    for backend in backends:
        if isinstance(backend, dict) and backend.get("name") == "xtest":
            return str(backend.get("display") or "")
    return ""


def normalize_hotkey(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"^(?:发送|按下|执行)?\s*(?:热键|快捷键|hotkey)[:：]?\s*", "", cleaned, flags=re.I)
    if not cleaned:
        return ""
    parts = [part.strip() for part in re.split(r"[+\s]+", cleaned) if part.strip()]
    aliases = {
        "ctrl": "ctrl",
        "control": "ctrl",
        "cmd": "super",
        "command": "super",
        "win": "super",
        "windows": "super",
        "alt": "alt",
        "shift": "shift",
        "enter": "Return",
        "return": "Return",
        "esc": "Escape",
        "escape": "Escape",
        "space": "space",
        "tab": "Tab",
    }
    normalized = [aliases.get(part.lower(), part) for part in parts]
    if not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in normalized):
        return ""
    return "+".join(normalized)


def _command_result(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return {
        "status": "completed" if completed.returncode == 0 else "blocked",
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip()[:500],
        "stderr": completed.stderr.strip()[:500],
    }


def _desktop_session() -> dict[str, object]:
    display = os.getenv("DISPLAY", "")
    wayland_display = os.getenv("WAYLAND_DISPLAY", "")
    session_type = os.getenv("XDG_SESSION_TYPE", "")
    desktop = os.getenv("XDG_CURRENT_DESKTOP", "")
    session_desktop = os.getenv("XDG_SESSION_DESKTOP", "")
    return {
        "has_gui_session": bool(display or wayland_display),
        "display": display,
        "wayland_display": wayland_display,
        "session_type": session_type,
        "desktop": desktop,
        "session_desktop": session_desktop,
    }


def _extract_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s，。]+", text, re.I)
    if match:
        return match.group(0)
    match = re.search(r"([\w.-]+\.[a-z]{2,}(?:/[^\s，。]*)?)", text, re.I)
    if match:
        return match.group(1)
    return None


def _extract_xy(text: str) -> tuple[int, int]:
    numbers = [int(item) for item in re.findall(r"-?\d+", text)[:2]]
    if len(numbers) >= 2:
        return max(0, numbers[0]), max(0, numbers[1])
    return 0, 0


def _extract_button(text: str) -> int:
    if any(marker in text for marker in ("右键", "right")):
        return 3
    if any(marker in text for marker in ("中键", "middle")):
        return 2
    match = re.search(r"\b([1-5])\b", text)
    return int(match.group(1)) if match else 1


def _extract_hotkey(text: str) -> str | None:
    match = re.search(r"(?:热键|快捷键|hotkey)[:：]?\s*([A-Za-z0-9_+.\-\s]{1,80})", text, re.I)
    return match.group(1).strip() if match else None


def _extract_text_to_type(text: str) -> str | None:
    match = re.search(r"(?:输入文字|键入|type text)[:：]?\s*(.{1,500})", text, re.I)
    return match.group(1).strip() if match else None


def _extract_app_name(text: str) -> str | None:
    match = re.search(r"(?:打开|启动|运行)\s*([\w\u4e00-\u9fff.+-]{1,30})", text)
    if match:
        return match.group(1).strip()
    return None


def _extract_file_query(text: str) -> str | None:
    cleaned = re.sub(r"(帮我|请|找文件|查找文件|搜索文件|打开文件|打开这个文件|文件)", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。")
    return cleaned or None


def _extract_web_query(text: str) -> str | None:
    cleaned = re.sub(r"(帮我|请|打开网页|打开网站|访问|网址|网页|网站)", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。")
    return cleaned or None


def _normalize_query(query: str) -> str:
    cleaned = _extract_file_query(query) or query
    cleaned = re.sub(r"\*+", "", cleaned).lower().strip()
    return cleaned
