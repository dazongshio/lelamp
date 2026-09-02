from __future__ import annotations

import hashlib
import html
import ipaddress
import importlib.util
import json
import math
import os
import re
import secrets
import shlex
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from lelamp.motor_control import LELAMP_MOTOR_ORDER

from ..audio_api import AudioAPIError, OpenAIAudioAPI
from ..config import tingwu_credential_next_actions
from ..dashscope_tts import DashScopeTTS, DashScopeTTSError
from ..documents import DOCUMENT_WORKFLOW_SUFFIXES
from ..elevenlabs_tts import ElevenLabsError, ElevenLabsTTS
from ..hardware_probe import play_audio_file, probe_hardware
from ..tingwu_meeting import PLACEHOLDER_CAPTURE_DEVICES, sanitize_event_payload

LELAMP_CONTROL_MOTORS = LELAMP_MOTOR_ORDER
OFFICIAL_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
OFFICIAL_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

__all__ = ['desktop_full_control_evidence', 'desktop_full_control_remediation', 'default_ssh_key_path', 'is_private_ssh_host', 'parse_safe_ssh_command', 'remote_codex_bootstrap_script']

def desktop_full_control_evidence(report: dict[str, object]) -> dict[str, bool]:
    steps = report.get("steps") if isinstance(report.get("steps"), list) else []
    completed = {
        str(step.get("id"))
        for step in steps
        if isinstance(step, dict) and step.get("status") == "completed"
    }
    return {
        "desktop_preflight": "desktop_preflight" in completed,
        "input_probe": "input_probe" in completed,
        "low_level_control_probe": "low_level_control_probe" in completed,
        "execution_probe": "execution_probe" in completed,
    }


def desktop_full_control_remediation(report: dict[str, object], missing_evidence: list[str]) -> list[str]:
    hints: list[str] = []
    steps = report.get("steps") if isinstance(report.get("steps"), list) else []
    details: dict[str, object] = {}
    for step in steps:
        if isinstance(step, dict) and step.get("id") == "desktop_preflight":
            details = step.get("details") if isinstance(step.get("details"), dict) else {}
            break
    errors = [str(item) for item in details.get("errors", [])] if isinstance(details.get("errors"), list) else []
    if "desktop_preflight" in missing_evidence:
        if "no_gui_session" in errors:
            hints.append("在目标办公电脑的真实桌面会话中运行脚本，确保 DISPLAY 或 WAYLAND_DISPLAY 可见。")
        if "missing_xdg_open" in errors:
            hints.append("安装 xdg-utils，确保 xdg-open 可用。")
        if "missing_xdotool" in errors or "missing_input_backend" in errors:
            hints.append("安装 xdotool，或确保系统 libXtst/XTest 可用。")
        if "missing_screenshot_backend" in errors:
            hints.append("安装 gnome-screenshot、spectacle、grim、ImageMagick import 或 xwd 中任意一个截图后端。")
        if any(error.startswith("runtime_permission_mode_") for error in errors):
            hints.append("停止旧控制台，用 OPENCLAW_PERMISSION_MODE=full_control 重新启动目标机 Web console。")
        if any(error.startswith("runtime_desktop_backend_") for error in errors):
            hints.append("用 OPENCLAW_DESKTOP_BACKEND=local 重新启动目标机 Web console。")
    if "input_probe" in missing_evidence:
        hints.append("确认 xdotool 能在目标桌面会话中执行 `xdotool getmouselocation`，或 XTest 能打开当前 DISPLAY。")
    if "low_level_control_probe" in missing_evidence:
        hints.append("确认低层控制探针能完成输入探针和截图探针。")
    if "execution_probe" in missing_evidence:
        hints.append("在验收页勾选授权，或运行目标机脚本完成监督式工作流执行探针。")
    return list(dict.fromkeys(hints))


def default_ssh_key_path() -> Path | None:
    for name in ("id_ed25519_lelamp_remote", "id_ed25519", "id_rsa", "id_ecdsa"):
        path = Path.home() / ".ssh" / name
        if path.is_file():
            return path.resolve()
    return None


def is_private_ssh_host(host: str) -> bool:
    value = str(host or "").strip()
    if not value:
        return False
    if value in {"localhost"} or value.endswith(".local") or value.endswith(".lan"):
        return True
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    shared_cgnat = ipaddress.ip_network("100.64.0.0/10")
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip in shared_cgnat)


def parse_safe_ssh_command(command: str) -> list[str]:
    import shlex

    text = command.strip()
    if not text:
        raise ApiError("missing_remote_command", "Missing remote command.", status=400)
    if len(text) > 1000:
        raise ApiError("remote_command_too_long", "Remote command is too long.", status=400)
    if re.search(r"[;&|`$<>\\\n\r]", text):
        raise ApiError(
            "unsafe_remote_command",
            "Remote command contains shell metacharacters. Use plain command arguments only.",
            status=400,
        )
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        raise ApiError("invalid_remote_command", "Remote command could not be parsed.", status=400) from exc
    if not argv:
        raise ApiError("missing_remote_command", "Missing remote command.", status=400)
    if len(argv) > 40:
        raise ApiError("remote_command_too_many_args", "Remote command has too many arguments.", status=400)
    executable = argv[0]
    if "/" in executable or executable.startswith("."):
        raise ApiError("unsafe_remote_command", "Use command names from PATH, not explicit executable paths.", status=400)
    return argv


def remote_codex_bootstrap_script() -> str:
    return r"""set -u
echo "CODEX_BOOTSTRAP_START=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.npm-global/bin:$PATH"
BREW_PROFILE_LINE='export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.npm-global/bin:$PATH"'
for profile in "$HOME/.zshenv" "$HOME/.zprofile" "$HOME/.zshrc" "$HOME/.profile"; do
  if [ -f "$profile" ]; then
    grep -F "$BREW_PROFILE_LINE" "$profile" >/dev/null 2>&1 || printf '\n%s\n' "$BREW_PROFILE_LINE" >> "$profile"
  else
    printf '%s\n' "$BREW_PROFILE_LINE" > "$profile"
  fi
done
if command -v codex >/dev/null 2>&1; then
  echo "CODEX_STATUS=installed"
  codex --version || true
  exit 0
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "CODEX_STATUS=installing_node"
  if command -v brew >/dev/null 2>&1; then
    brew install node
    export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
  elif command -v apt-get >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1; then
      sudo apt-get update
      sudo apt-get install -y nodejs npm
    else
      apt-get update
      apt-get install -y nodejs npm
    fi
  fi
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "CODEX_STATUS=missing_npm"
  echo "npm is required to install @openai/codex, and automatic Node.js/npm install did not succeed." >&2
  exit 20
fi
echo "CODEX_STATUS=installing"
if npm install -g @openai/codex; then
  hash -r 2>/dev/null || true
  if command -v codex >/dev/null 2>&1; then
    echo "CODEX_STATUS=installed"
    codex --version || true
    exit 0
  fi
fi
echo "CODEX_STATUS=user_install_fallback"
mkdir -p "$HOME/.npm-global"
npm config set prefix "$HOME/.npm-global"
npm install -g @openai/codex
PROFILE_LINE='export PATH="$HOME/.npm-global/bin:$PATH"'
if [ -f "$HOME/.profile" ]; then
  grep -F "$PROFILE_LINE" "$HOME/.profile" >/dev/null 2>&1 || printf '\n%s\n' "$PROFILE_LINE" >> "$HOME/.profile"
else
  printf '%s\n' "$PROFILE_LINE" > "$HOME/.profile"
fi
export PATH="$HOME/.npm-global/bin:$PATH"
if command -v codex >/dev/null 2>&1; then
  echo "CODEX_STATUS=installed"
  codex --version || true
  exit 0
fi
echo "CODEX_STATUS=failed"
exit 21
"""

