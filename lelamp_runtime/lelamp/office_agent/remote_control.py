from __future__ import annotations

import ipaddress
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REMOTE_TARGET_FILE = Path(".remote") / "ssh_target.json"


@dataclass(frozen=True)
class RemoteVoiceCommand:
    action: str
    label: str
    reply: str
    args: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "label": self.label,
            "reply": self.reply,
            "args": self.args,
        }


def normalize_remote_voice_text(text: str) -> str:
    return (
        text.lower()
        .replace(" ", "")
        .replace("，", "")
        .replace("。", "")
        .replace(",", "")
        .replace(".", "")
        .replace("！", "")
        .replace("!", "")
        .replace("：", "")
        .replace(":", "")
    )


def remote_target_config_path(workspace_dir: Path) -> Path:
    return workspace_dir / REMOTE_TARGET_FILE


def load_saved_remote_target(workspace_dir: Path) -> dict[str, object] | None:
    path = remote_target_config_path(workspace_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    target = payload.get("target") if isinstance(payload.get("target"), dict) else payload
    host = str(target.get("host") or "").strip()
    user = str(target.get("user") or "").strip()
    key_path = str(target.get("key_path") or "").strip()
    if not host or not user or not key_path:
        return None
    return {
        "host": host,
        "user": user,
        "port": int(target.get("port") or 22),
        "key_path": key_path,
        "timeout_seconds": int(target.get("timeout_seconds") or 12),
        "saved_at": str(payload.get("saved_at") or ""),
        "source": str(payload.get("source") or "workspace"),
    }


def save_remote_target(workspace_dir: Path, target: dict[str, Any], *, source: str = "web_console") -> dict[str, object]:
    path = remote_target_config_path(workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = {
        "host": str(target.get("host") or ""),
        "user": str(target.get("user") or ""),
        "port": int(target.get("port") or 22),
        "key_path": str(target.get("key_path") or ""),
        "timeout_seconds": int(target.get("timeout_seconds") or 12),
    }
    payload = {
        "target": safe,
        "source": source,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "purpose": "LeLamp remote computer voice control and Codex bootstrap.",
    }
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {**payload, "path": str(path)}


def safe_remote_target_for_display(target: dict[str, Any] | None) -> dict[str, object] | None:
    if not target:
        return None
    key_path = Path(str(target.get("key_path") or ""))
    return {
        "host": str(target.get("host") or ""),
        "user": str(target.get("user") or ""),
        "port": int(target.get("port") or 22),
        "key_path": str(key_path),
        "key_name": key_path.name,
        "timeout_seconds": int(target.get("timeout_seconds") or 12),
    }


def is_allowed_remote_host(host: str) -> bool:
    value = str(host or "").strip()
    if not value:
        return False
    if value == "localhost" or value.endswith(".local") or value.endswith(".lan"):
        return True
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10"))


def parse_remote_voice_command(text: str) -> RemoteVoiceCommand | None:
    raw = text.strip()
    normalized = normalize_remote_voice_text(raw)
    if not normalized:
        return None

    remote_hint = any(
        marker in normalized
        for marker in (
            "远程电脑",
            "远程主机",
            "目标电脑",
            "那台电脑",
            "另一台电脑",
            "ssh电脑",
            "电脑",
            "mac",
            "macbook",
        )
    )
    slide_hint = remote_hint or any(marker in normalized for marker in ("ppt", "幻灯片", "演示文稿", "放映"))

    if any(marker in normalized for marker in ("远程电脑状态", "目标电脑状态", "电脑状态", "ssh状态", "远程状态")):
        return RemoteVoiceCommand("status", "remote_status", "我检查一下远程电脑状态。")

    if "codex" in normalized and any(marker in normalized for marker in ("打开", "启动", "开启", "运行", "进入", "连接")):
        return RemoteVoiceCommand("open_codex", "open_codex", "正在远程电脑上打开 Codex。")

    if remote_hint and any(marker in normalized for marker in ("打开语音控制", "开启语音控制", "启动语音控制", "语音控制电脑", "语音控制远程电脑", "控制电脑")):
        return RemoteVoiceCommand("open_codex", "open_codex", "正在远程电脑上打开 Codex，之后可以用语音发送受控电脑指令。")

    if any(marker in normalized for marker in ("关机", "重启", "格式化", "删除所有")) and remote_hint:
        return RemoteVoiceCommand("blocked_power", "blocked_power", "远程关机、重启或删除类命令不会通过语音直接执行。")

    url = extract_url(raw)
    if url and (remote_hint or any(marker in normalized for marker in ("打开网页", "打开网站", "访问"))):
        return RemoteVoiceCommand("open_url", "open_url", "正在远程电脑上打开网页。", {"url": url})

    app = parse_app_alias(normalized)
    if app and (remote_hint or any(marker in normalized for marker in ("打开", "启动", "开启"))):
        return RemoteVoiceCommand("open_app", f"open_{app[0]}", f"正在远程电脑上打开{app[1]}。", {"app": app[2]})

    if slide_hint and any(marker in normalized for marker in ("下一页", "下一张", "下一个", "往后翻", "翻下一页")):
        return RemoteVoiceCommand("key_next", "key_next", "已向远程电脑发送下一页。")

    if slide_hint and any(marker in normalized for marker in ("上一页", "上一张", "上一个", "往前翻", "翻上一页")):
        return RemoteVoiceCommand("key_prev", "key_prev", "已向远程电脑发送上一页。")

    if slide_hint and any(marker in normalized for marker in ("播放暂停", "暂停播放", "暂停", "继续播放", "播放")):
        return RemoteVoiceCommand("media_play_pause", "media_play_pause", "已向远程电脑发送播放/暂停。")

    if slide_hint and any(marker in normalized for marker in ("全屏", "退出全屏", "esc", "退出播放")):
        return RemoteVoiceCommand("key_escape", "key_escape", "已向远程电脑发送退出/返回。")

    volume = extract_volume_percent(normalized)
    if remote_hint and volume is not None:
        return RemoteVoiceCommand("set_volume", "set_volume", f"已把远程电脑音量设置到 {volume}%。", {"volume": str(volume)})

    if remote_hint and any(marker in normalized for marker in ("音量增大", "声音增大", "大声一点", "调大声音")):
        return RemoteVoiceCommand("volume_up", "volume_up", "已调大远程电脑音量。")

    if remote_hint and any(marker in normalized for marker in ("音量减小", "声音降低", "声音减小", "小声一点", "调小声音")):
        return RemoteVoiceCommand("volume_down", "volume_down", "已调小远程电脑音量。")

    if remote_hint and any(marker in normalized for marker in ("静音", "取消声音")):
        return RemoteVoiceCommand("mute", "mute", "已切换远程电脑静音。")

    if remote_hint and any(marker in normalized for marker in ("锁屏", "锁定屏幕", "锁电脑")):
        return RemoteVoiceCommand("lock_screen", "lock_screen", "已向远程电脑发送锁屏命令。")

    return None


def extract_url(text: str) -> str:
    match = re.search(r"https?://[^\s，。]+", text, re.IGNORECASE)
    if match:
        return match.group(0).strip()
    match = re.search(r"(?:打开网页|打开网站|访问)\s*([A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s，。]*)?)", text)
    if match:
        value = match.group(1).strip()
        return value if value.startswith(("http://", "https://")) else f"https://{value}"
    return ""


def extract_volume_percent(normalized: str) -> int | None:
    match = re.search(r"(?:音量|声音)(?:设置到|设到|调到|降低到|降到)?(\d{1,3})%?", normalized)
    if not match:
        return None
    value = max(0, min(100, int(match.group(1))))
    return value


def parse_app_alias(normalized: str) -> tuple[str, str, str] | None:
    aliases = (
        ("browser", "浏览器", "Google Chrome", ("浏览器", "chrome", "谷歌")),
        ("safari", "Safari", "Safari", ("safari",)),
        ("terminal", "终端", "Terminal", ("终端", "terminal", "命令行")),
        ("vscode", "VS Code", "Visual Studio Code", ("vscode", "visualstudiocode", "代码")),
        ("finder", "访达", "Finder", ("访达", "finder")),
        ("powerpoint", "PowerPoint", "Microsoft PowerPoint", ("ppt", "powerpoint", "幻灯片")),
        ("keynote", "Keynote", "Keynote", ("keynote",)),
    )
    if not any(marker in normalized for marker in ("打开", "启动", "开启")):
        return None
    for key, label, app_name, markers in aliases:
        if any(marker in normalized for marker in markers):
            return key, label, app_name
    return None


def remote_voice_command_script(command: RemoteVoiceCommand) -> str:
    action = command.action
    if action == "status":
        return remote_status_script()
    if action == "open_codex":
        return remote_open_codex_script()
    if action == "open_url":
        return remote_open_url_script(command.args.get("url", ""))
    if action == "open_app":
        return remote_open_app_script(command.args.get("app", ""))
    if action in {"key_next", "key_prev", "media_play_pause", "key_escape"}:
        return remote_key_script(action)
    if action in {"volume_up", "volume_down", "mute", "set_volume"}:
        return remote_volume_script(action, command.args.get("volume", ""))
    if action == "lock_screen":
        return remote_lock_script()
    return "echo REMOTE_STATUS=unsupported\nexit 22\n"


def remote_script_preamble() -> str:
    return r"""set -u
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.npm-global/bin:$PATH"
os_name="$(uname -s 2>/dev/null || echo unknown)"
have() { command -v "$1" >/dev/null 2>&1; }
echo "REMOTE_OS=$os_name"
"""


def remote_status_script() -> str:
    return remote_script_preamble() + r"""
echo "REMOTE_USER=$(whoami 2>/dev/null || true)"
echo "REMOTE_DATE=$(date 2>/dev/null || true)"
if have sw_vers; then sw_vers | sed 's/^/REMOTE_SW=/'; fi
if have codex; then
  echo "CODEX_STATUS=installed"
  codex --version || true
else
  echo "CODEX_STATUS=missing"
fi
exit 0
"""


def remote_open_codex_script() -> str:
    return remote_script_preamble() + r"""
mkdir -p "$HOME/.codex"
if ! have codex; then
  echo "CODEX_STATUS=missing"
  exit 20
fi
echo "CODEX_STATUS=installed"
codex --version || true
if codex remote-control start --json > "$HOME/.codex/remote-control-start.json" 2> "$HOME/.codex/remote-control-start.err"; then
  echo "CODEX_REMOTE_CONTROL=started"
  tail -c 4000 "$HOME/.codex/remote-control-start.json" 2>/dev/null || true
else
  echo "CODEX_REMOTE_CONTROL=unavailable"
  tail -c 4000 "$HOME/.codex/remote-control-start.err" 2>/dev/null || true
fi
if [ "$os_name" = "Darwin" ] && have osascript; then
  osascript <<APPLESCRIPT
tell application "Terminal"
  activate
  do script "cd $HOME; codex"
end tell
APPLESCRIPT
  echo "CODEX_TERMINAL=opened"
elif have x-terminal-emulator; then
  nohup x-terminal-emulator -e sh -lc 'cd "$HOME"; codex' >/dev/null 2>&1 &
  echo "CODEX_TERMINAL=opened"
elif have gnome-terminal; then
  nohup gnome-terminal -- sh -lc 'cd "$HOME"; codex' >/dev/null 2>&1 &
  echo "CODEX_TERMINAL=opened"
else
  echo "CODEX_TERMINAL=not_available"
fi
exit 0
"""


def remote_open_url_script(url: str) -> str:
    safe_url = sanitize_url(url)
    quoted = shlex.quote(safe_url)
    return remote_script_preamble() + f"""
url={quoted}
if [ -z "$url" ]; then echo "REMOTE_STATUS=missing_url"; exit 2; fi
if [ "$os_name" = "Darwin" ] && have open; then
  open "$url"
elif have xdg-open; then
  nohup xdg-open "$url" >/dev/null 2>&1 &
else
  echo "REMOTE_STATUS=no_url_opener"
  exit 23
fi
echo "REMOTE_STATUS=opened_url"
exit 0
"""


def remote_open_app_script(app_name: str) -> str:
    safe_app = sanitize_app_name(app_name)
    quoted = shlex.quote(safe_app)
    return remote_script_preamble() + f"""
app_name={quoted}
if [ -z "$app_name" ]; then echo "REMOTE_STATUS=missing_app"; exit 2; fi
if [ "$os_name" = "Darwin" ] && have open; then
  open -a "$app_name"
elif have gtk-launch; then
  gtk-launch "$app_name" >/dev/null 2>&1 || true
elif have xdg-open; then
  nohup xdg-open . >/dev/null 2>&1 &
else
  echo "REMOTE_STATUS=no_app_opener"
  exit 23
fi
echo "REMOTE_STATUS=opened_app"
exit 0
"""


def remote_key_script(action: str) -> str:
    key_map = {
        "key_next": ("124", "Right"),
        "key_prev": ("123", "Left"),
        "media_play_pause": ("49", "space"),
        "key_escape": ("53", "Escape"),
    }
    mac_code, x_key = key_map[action]
    return remote_script_preamble() + f"""
if [ "$os_name" = "Darwin" ] && have osascript; then
  if ! osascript -e 'tell application "System Events" to key code {mac_code}'; then
    echo "REMOTE_STATUS=macos_accessibility_blocked"
    exit 24
  fi
elif have xdotool; then
  xdotool key {shlex.quote(x_key)}
else
  echo "REMOTE_STATUS=no_key_backend"
  exit 23
fi
echo "REMOTE_STATUS=sent_key"
exit 0
"""


def remote_volume_script(action: str, volume: str = "") -> str:
    if action == "set_volume":
        try:
            value = max(0, min(100, int(volume)))
        except ValueError:
            value = 50
        return remote_script_preamble() + f"""
if [ "$os_name" = "Darwin" ] && have osascript; then
  if ! osascript -e 'set volume output volume {value}'; then
    echo "REMOTE_STATUS=macos_accessibility_blocked"
    exit 24
  fi
elif have pactl; then
  pactl set-sink-volume @DEFAULT_SINK@ {value}%
else
  echo "REMOTE_STATUS=no_volume_backend"
  exit 23
fi
echo "REMOTE_STATUS=set_volume"
exit 0
"""
    if action == "mute":
        mac_line = "osascript -e 'set volume with output muted'"
        linux_line = "pactl set-sink-mute @DEFAULT_SINK@ toggle"
    elif action == "volume_up":
        mac_line = "osascript -e 'set volume output volume ((output volume of (get volume settings)) + 10)'"
        linux_line = "pactl set-sink-volume @DEFAULT_SINK@ +10%"
    else:
        mac_line = "osascript -e 'set volume output volume ((output volume of (get volume settings)) - 10)'"
        linux_line = "pactl set-sink-volume @DEFAULT_SINK@ -10%"
    return remote_script_preamble() + f"""
if [ "$os_name" = "Darwin" ] && have osascript; then
  if ! {mac_line}; then
    echo "REMOTE_STATUS=macos_accessibility_blocked"
    exit 24
  fi
elif have pactl; then
  {linux_line}
else
  echo "REMOTE_STATUS=no_volume_backend"
  exit 23
fi
echo "REMOTE_STATUS=volume_changed"
exit 0
"""


def remote_lock_script() -> str:
    return remote_script_preamble() + r"""
if [ "$os_name" = "Darwin" ]; then
  /System/Library/CoreServices/Menu\ Extras/User.menu/Contents/Resources/CGSession -suspend
elif have loginctl; then
  loginctl lock-session
elif have gnome-screensaver-command; then
  gnome-screensaver-command -l
else
  echo "REMOTE_STATUS=no_lock_backend"
  exit 23
fi
echo "REMOTE_STATUS=locked"
exit 0
"""


def sanitize_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    if not re.match(r"^https?://", value, re.IGNORECASE):
        value = f"https://{value}"
    if not re.match(r"^https?://[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,2000}$", value):
        return ""
    return value


def sanitize_app_name(app_name: str) -> str:
    value = str(app_name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9 ._-]{1,80}", value):
        return ""
    return value


def run_remote_ssh_script(target: dict[str, Any], script: str, *, timeout_seconds: int = 20) -> dict[str, object]:
    ssh_binary = shutil.which("ssh")
    if not ssh_binary:
        return {"backend": "openssh", "exit_code": 127, "stdout": "", "stderr": "OpenSSH client is not installed.", "duration_seconds": 0}
    destination = f"{target['user']}@{target['host']}"
    command = [
        ssh_binary,
        "-i",
        str(target["key_path"]),
        "-p",
        str(target.get("port") or 22),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "--",
        destination,
        "sh",
        "-s",
    ]
    started = time.time()
    try:
        completed = subprocess.run(command, input=script, capture_output=True, text=True, timeout=timeout_seconds, check=False)
        exit_code = int(completed.returncode)
        stdout = completed.stdout[-20000:]
        stderr = completed.stderr[-20000:]
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = str(exc.stdout or "")[-20000:]
        stderr = (str(exc.stderr or "") + f"\nCommand timed out after {timeout_seconds}s.").strip()[-20000:]
    return {
        "backend": "openssh",
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_seconds": round(time.time() - started, 3),
    }


def execute_saved_remote_voice_command(runtime: Any, text: str) -> dict[str, object]:
    command = parse_remote_voice_command(text)
    if command is None:
        return {"handled": False, "status": "not_matched"}
    if command.action == "blocked_power":
        result = {
            "handled": True,
            "status": "blocked",
            "reply": command.reply,
            "command": command.as_dict(),
        }
        runtime.audit.record("remote_voice.command", status="blocked", target=text, details=result)
        return result
    target = load_saved_remote_target(runtime.config.workspace_dir)
    if target is None:
        result = {
            "handled": True,
            "status": "needs_config",
            "reply": "还没有保存远程电脑 SSH 目标。请先在远程电脑页面测试连接。",
            "command": command.as_dict(),
            "target": None,
        }
        runtime.audit.record("remote_voice.command", status="blocked", target=text, details=result)
        return result
    if not is_allowed_remote_host(str(target.get("host") or "")):
        result = {
            "handled": True,
            "status": "blocked",
            "reply": "远程电脑地址不在允许范围内，已阻止。",
            "command": command.as_dict(),
            "target": safe_remote_target_for_display(target),
        }
        runtime.audit.record("remote_voice.command", status="blocked", target=text, details=result)
        return result
    script = remote_voice_command_script(command)
    remote = run_remote_ssh_script(target, script, timeout_seconds=remote_voice_timeout(command, target))
    status = "completed" if int(remote.get("exit_code") or 0) == 0 else "failed"
    result = {
        "handled": True,
        "status": status,
        "reply": command.reply if status == "completed" else "远程电脑命令执行失败，请查看日志。",
        "command": command.as_dict(),
        "target": safe_remote_target_for_display(target),
        "remote": remote,
    }
    runtime.audit.record(
        "remote_voice.command",
        status="ok" if status == "completed" else "error",
        target=text,
        details={
            "command": command.as_dict(),
            "target": safe_remote_target_for_display(target),
            "exit_code": remote.get("exit_code"),
            "status": status,
        },
    )
    return result


def remote_voice_timeout(command: RemoteVoiceCommand, target: dict[str, Any]) -> int:
    base = int(target.get("timeout_seconds") or 12)
    if command.action == "open_codex":
        return max(30, min(90, base * 5))
    return max(5, min(30, base))
