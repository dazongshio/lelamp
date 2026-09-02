from __future__ import annotations

import base64
import ctypes
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

from .audit import AuditLogger
from .config import PermissionMode


from .desktop_input import DesktopInputMixin
from .desktop_workflows import DesktopWorkflowMixin
from .desktop_apps import DesktopAppsMixin

class DesktopService(DesktopInputMixin, DesktopWorkflowMixin, DesktopAppsMixin):
    EXECUTION_BACKENDS = {"local", "xdg", "linux"}
    APP_ALIASES: dict[str, tuple[str, ...]] = {
        "浏览器": ("xdg-open",),
        "网页": ("xdg-open",),
        "chrome": ("google-chrome", "chromium", "chromium-browser"),
        "谷歌浏览器": ("google-chrome", "chromium", "chromium-browser"),
        "firefox": ("firefox",),
        "火狐": ("firefox",),
        "终端": ("gnome-terminal", "konsole", "xfce4-terminal", "xterm"),
        "命令行": ("gnome-terminal", "konsole", "xfce4-terminal", "xterm"),
        "文件管理器": ("nautilus", "dolphin", "thunar", "pcmanfm"),
        "文件夹": ("nautilus", "dolphin", "thunar", "pcmanfm"),
        "编辑器": ("code", "gedit", "kate", "mousepad"),
        "vscode": ("code",),
        "记事本": ("gedit", "kate", "mousepad"),
        "计算器": ("gnome-calculator", "kcalc", "galculator"),
        "播放器": ("vlc", "mpv"),
        "音乐": ("spotify", "vlc"),
        "wps": ("wps", "libreoffice"),
        "文档": ("libreoffice", "wps"),
        "表格": ("libreoffice", "wps"),
    }




from .desktop_support import (
    _command_result, _desktop_session, _extract_app_name, _extract_button,
    _extract_file_query, _extract_hotkey, _extract_text_to_type, _extract_url,
    _extract_web_query, _extract_xy, _normalize_query, _preflight_xtest_display,
    _with_xtest_display, _xtest_mouse_move, discover_x11_display, normalize_hotkey,
    normalize_url_or_search, xtest_available, xtest_hotkey, xtest_key_tap,
    xtest_mouse_click, xtest_mouse_move,
)
