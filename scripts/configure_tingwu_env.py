#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "lelamp_runtime"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(RUNTIME_ROOT))

from lelamp.office_agent.config import tingwu_credential_kind, tingwu_credential_next_actions  # noqa: E402
from verify_tingwu_live_suite import load_env_file  # noqa: E402

OFFICIAL_TINGWU_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
OFFICIAL_TINGWU_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


def resolve_env_file(path_value: str) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        cwd_relative = path.resolve()
        if cwd_relative.parent.exists() or cwd_relative.is_relative_to(RUNTIME_ROOT):
            return cwd_relative
        return (RUNTIME_ROOT / path).resolve()
    return path.resolve()


def shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def read_existing(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return load_env_file(str(path))


def prompt_secret(label: str, *, default: str = "") -> str:
    if default:
        entered = getpass.getpass(f"{label} [press Enter to keep existing]: ").strip()
        return entered or default
    return getpass.getpass(f"{label}: ").strip()


def prompt_text(label: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    entered = input(f"{label}{suffix}: ").strip()
    return entered or default


def validate_values(api_key: str, app_id: str) -> tuple[bool, dict[str, object]]:
    api_kind = tingwu_credential_kind(api_key)
    app_kind = tingwu_credential_kind(app_id, role="app_id")
    ok = api_kind == "configured" and app_kind == "configured"
    return ok, {
        "api_key_kind": api_kind,
        "app_id_kind": app_kind,
        "api_key_configured": api_kind == "configured",
        "app_id_configured": app_kind == "configured",
        "next_actions": tingwu_credential_next_actions(api_kind, app_kind),
    }


def write_env(path: Path, *, api_key: str, app_id: str, mic_device: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            "# Local-only Tongyi Tingwu live verification credentials.",
            "# Do not paste these values into chat, commits, issue trackers, screenshots, or evidence files.",
            f"export DASHSCOPE_API_KEY={shell_single_quote(api_key)}",
            f"export TINGWU_APP_ID={shell_single_quote(app_id)}",
            f"export OPENCLAW_MIC_DEVICE={shell_single_quote(mic_device or 'auto')}",
            f"export TINGWU_HTTP_URL={shell_single_quote(OFFICIAL_TINGWU_HTTP_URL)}",
            f"export TINGWU_WS_URL={shell_single_quote(OFFICIAL_TINGWU_WS_URL)}",
            "",
        ]
    )
    path.write_text(content, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Create lelamp_runtime/.env.tingwu.local without echoing secrets.")
    parser.add_argument("--env-file", default=".env.tingwu.local")
    parser.add_argument("--api-key", default="", help="Non-interactive DashScope API Key. Prefer interactive input.")
    parser.add_argument("--app-id", default="", help="Non-interactive Bailian Model Studio app ID, usually tw_***. Prefer interactive input.")
    parser.add_argument("--mic-device", default="", help="ALSA capture device, default auto.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing env file without prompting.")
    parser.add_argument("--yes", action="store_true", help="Write after validation without a final confirmation prompt.")
    args = parser.parse_args()

    env_path = resolve_env_file(args.env_file)
    existing = read_existing(env_path)
    if env_path.exists() and not args.force:
        print(f"Existing env file found: {env_path}")
    api_key = args.api_key.strip() or prompt_secret(
        "New Bailian/DashScope API Key",
        default=existing.get("DASHSCOPE_API_KEY") or existing.get("TINGWU_API_KEY") or "",
    )
    app_id = args.app_id.strip() or prompt_secret(
        "Bailian Model Studio App ID, usually tw_***",
        default=existing.get("TINGWU_APP_ID") or existing.get("TINGWU_MEETING_APP_ID") or "",
    )
    mic_device = args.mic_device.strip()
    if not mic_device:
        default_mic = existing.get("OPENCLAW_MIC_DEVICE") or "auto"
        mic_device = default_mic if args.yes else prompt_text("ALSA microphone device", default=default_mic)

    ok, diagnostics = validate_values(api_key, app_id)
    result = {
        "status": "ok" if ok else "failed",
        "path": str(env_path),
        "checks": {
            "api_key_kind": diagnostics["api_key_kind"],
            "app_id_kind": diagnostics["app_id_kind"],
            "api_key_configured": diagnostics["api_key_configured"],
            "app_id_configured": diagnostics["app_id_configured"],
        },
        "next_actions": diagnostics["next_actions"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not ok:
        return 1

    if not args.yes:
        confirm = input(f"Write {env_path}? Type yes to continue: ").strip().lower()
        if confirm != "yes":
            print("cancelled")
            return 2
    write_env(env_path, api_key=api_key, app_id=app_id, mic_device=mic_device)
    print(f"wrote {env_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
