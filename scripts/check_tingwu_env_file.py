#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "lelamp_runtime"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(RUNTIME_ROOT))

from lelamp.office_agent.config import tingwu_credential_kind, tingwu_credential_next_actions  # noqa: E402
from verify_tingwu_live_suite import load_env_file  # noqa: E402


def check_env(path_value: str) -> dict[str, object]:
    loaded = load_env_file(path_value)
    api_value = loaded.get("TINGWU_API_KEY") or loaded.get("DASHSCOPE_API_KEY") or ""
    app_value = loaded.get("TINGWU_APP_ID") or loaded.get("TINGWU_MEETING_APP_ID") or ""
    api_kind = tingwu_credential_kind(api_value)
    app_kind = tingwu_credential_kind(app_value, role="app_id")
    api_ok = api_kind == "configured"
    app_ok = app_kind == "configured"
    return {
        "status": "ok" if api_ok and app_ok else "failed",
        "path": str(Path(path_value).expanduser()),
        "checks": {
            "api_key_kind": api_kind,
            "app_id_kind": app_kind,
            "api_key_configured": api_ok,
            "app_id_configured": app_ok,
        },
        "next_actions": tingwu_credential_next_actions(api_kind, app_kind),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a local Tingwu env file without printing or sending secrets.")
    parser.add_argument("--env-file", default=".env.tingwu.local")
    args = parser.parse_args()
    result = check_env(args.env_file)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
