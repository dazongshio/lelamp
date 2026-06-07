from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from lelamp.office_agent.utils import dedupe_path, safe_filename
from lelamp.office_agent.workspace import _workspace_date_for_filename, _workspace_task_for_filename


PROTECTED_ROOT_FILES = {
    "lelamp_motion_config.json",
}
PROTECTED_ROOT_DIRS = {
    ".assistant",
    ".lamp_voice",
    ".poses",
    ".voice",
    "browser_automation",
    "desktop_tasks",
    "meetings",
    "perception_runs",
    "shared_inbox",
    "validation_reports",
    "web_tasks",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Organize LeLamp workspace root artifacts into task/YYYY/MM/DD folders.")
    parser.add_argument("--workspace", default="lelamp_runtime/workspace")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        raise SystemExit(f"Workspace not found: {workspace}")

    moves: list[tuple[Path, Path]] = []
    skipped: list[str] = []
    for path in sorted(workspace.iterdir()):
        if path.is_dir():
            if path.name in PROTECTED_ROOT_DIRS:
                skipped.append(str(path.relative_to(workspace)))
            continue
        if not path.is_file():
            continue
        if path.name.startswith(".") or path.name in PROTECTED_ROOT_FILES:
            skipped.append(str(path.relative_to(workspace)))
            continue
        task = task_for_root_file(path.name)
        year, month, day = _workspace_date_for_filename(path.name)
        target = workspace / task / year / month / day / safe_filename(path.name, default="artifact")
        moves.append((path, target))

    index: dict[str, str] = {}
    for source, target in moves:
        final_target = dedupe_path(target)
        index[source.name] = str(final_target.relative_to(workspace))
        index[str(source.relative_to(workspace))] = str(final_target.relative_to(workspace))
        if args.apply:
            final_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(final_target))

    summary = {
        "workspace": str(workspace),
        "applied": bool(args.apply),
        "moved_count": len(moves),
        "skipped_count": len(skipped),
        "skipped": skipped,
        "tasks": task_counts(index),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if args.apply:
        index_path = workspace / ".workspace_file_index.json"
        existing = load_existing_index(index_path)
        existing.update(index)
        index_path.write_text(
            json.dumps({"updated_at": summary["updated_at"], "files": dict(sorted(existing.items()))}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def task_for_root_file(filename: str) -> str:
    name = filename.lower()
    if filename.startswith("codex_camscanner") or filename.startswith("lamp_head_scan") or filename.startswith("desk_observation"):
        return "scans"
    if re.search(r"(meeting|transcript|minutes|followup|action_items|decisions|reminders)", name):
        return "meetings"
    if "pdf" in name or name.endswith(".pdf"):
        return "documents"
    return _workspace_task_for_filename(filename)


def task_counts(index: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in set(index.values()):
        task = value.split("/", 1)[0]
        counts[task] = counts.get(task, 0) + 1
    return dict(sorted(counts.items()))


def load_existing_index(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    files = payload.get("files") if isinstance(payload, dict) else {}
    if not isinstance(files, dict):
        return {}
    return {str(key): str(value) for key, value in files.items()}


if __name__ == "__main__":
    raise SystemExit(main())
