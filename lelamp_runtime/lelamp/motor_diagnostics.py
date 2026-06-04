from __future__ import annotations

import argparse
import ast
import csv
import json
from pathlib import Path
from typing import Any

from .follower import LeLampFollower, LeLampFollowerConfig
from .leader import LeLampLeader, LeLampLeaderConfig
from .motor_config import (
    DEFAULT_LED_COUNT,
    DEFAULT_RECORDINGS,
    EXPECTED_RECORDING_COLUMNS,
    MOTOR_IDS,
    MOTOR_MODEL,
)


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(__file__).resolve().parent
RECORDINGS_DIR = PACKAGE_ROOT / "recordings"


def _motor_map(robot: Any) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "id": motor.id,
            "model": motor.model,
            "norm_mode": getattr(motor.norm_mode, "value", str(motor.norm_mode)),
        }
        for name, motor in robot.bus.motors.items()
    }


def _check_motor_map(label: str, actual: dict[str, dict[str, Any]]) -> dict[str, Any]:
    expected_names = list(MOTOR_IDS)
    actual_names = list(actual)
    issues: list[str] = []

    if actual_names != expected_names:
        issues.append(f"{label} motor order is {actual_names}, expected {expected_names}")

    for name, expected_id in MOTOR_IDS.items():
        item = actual.get(name)
        if item is None:
            issues.append(f"{label} missing motor {name}")
            continue
        if item["id"] != expected_id:
            issues.append(f"{label} motor {name} id is {item['id']}, expected {expected_id}")
        if item["model"] != MOTOR_MODEL:
            issues.append(f"{label} motor {name} model is {item['model']}, expected {MOTOR_MODEL}")

    return {
        "status": "ok" if not issues else "error",
        "motors": actual,
        "issues": issues,
    }


def _recording_report(path: Path) -> dict[str, Any]:
    issues: list[str] = []
    rows = 0
    invalid_values = 0

    try:
        with path.open("r", newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            fieldnames = tuple(reader.fieldnames or ())
            if fieldnames != EXPECTED_RECORDING_COLUMNS:
                issues.append(
                    f"columns are {list(fieldnames)}, expected {list(EXPECTED_RECORDING_COLUMNS)}"
                )

            for row in reader:
                rows += 1
                for column in EXPECTED_RECORDING_COLUMNS:
                    if column == "timestamp":
                        continue
                    try:
                        float(row.get(column, ""))
                    except (TypeError, ValueError):
                        invalid_values += 1

    except OSError as exc:
        issues.append(f"cannot read file: {exc}")

    if rows == 0:
        issues.append("recording has no data rows")
    if invalid_values:
        issues.append(f"{invalid_values} motor position values are not numeric")

    return {
        "name": path.stem,
        "file": str(path),
        "rows": rows,
        "status": "ok" if not issues else "error",
        "issues": issues,
    }


def _check_recordings(recordings_dir: Path) -> dict[str, Any]:
    issues: list[str] = []
    if not recordings_dir.exists():
        return {
            "status": "error",
            "directory": str(recordings_dir),
            "recordings": [],
            "issues": [f"recordings directory does not exist: {recordings_dir}"],
        }

    files = sorted(recordings_dir.glob("*.csv"))
    reports = [_recording_report(path) for path in files]
    available = {item["name"] for item in reports}
    missing_defaults = sorted(set(DEFAULT_RECORDINGS) - available)
    if missing_defaults:
        issues.append(f"missing default recordings: {missing_defaults}")

    for item in reports:
        issues.extend(f"{item['name']}: {issue}" for issue in item["issues"])

    return {
        "status": "ok" if not issues else "error",
        "directory": str(recordings_dir),
        "expected_columns": list(EXPECTED_RECORDING_COLUMNS),
        "default_recordings": list(DEFAULT_RECORDINGS),
        "recordings": reports,
        "issues": issues,
    }


def _literal_led_count(value: ast.AST) -> int | None:
    if isinstance(value, ast.Constant) and isinstance(value.value, int):
        return value.value
    return None


def _scan_led_counts() -> dict[str, Any]:
    files = [
        RUNTIME_ROOT / "main.py",
        RUNTIME_ROOT / "smooth_animation.py",
        PACKAGE_ROOT / "office_agent" / "hardware.py",
        PACKAGE_ROOT / "service" / "rgb" / "rgb_service.py",
    ]
    counts: list[dict[str, Any]] = []
    issues: list[str] = []

    for path in files:
        if not path.exists():
            issues.append(f"missing expected runtime file: {path}")
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            issues.append(f"cannot parse {path}: {exc}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "led_count":
                count = _literal_led_count(node.value)
                counts.append({"file": str(path), "line": node.lineno, "led_count": count})
            elif isinstance(node, ast.arguments):
                for default in node.defaults:
                    if _literal_led_count(default) == DEFAULT_LED_COUNT:
                        continue

    non_default = [item for item in counts if item["led_count"] != DEFAULT_LED_COUNT]
    if non_default:
        issues.append(f"led_count should be {DEFAULT_LED_COUNT}; found {non_default}")

    return {
        "status": "ok" if not issues else "warning",
        "expected_led_count": DEFAULT_LED_COUNT,
        "occurrences": counts,
        "issues": issues,
    }


def run_diagnostics(lamp_id: str = "lelamp", port: str = "/dev/null") -> dict[str, Any]:
    follower = LeLampFollower(LeLampFollowerConfig(port=port, id=lamp_id))
    leader = LeLampLeader(LeLampLeaderConfig(port=port, id=lamp_id))

    checks = {
        "follower": _check_motor_map("follower", _motor_map(follower)),
        "leader": _check_motor_map("leader", _motor_map(leader)),
        "recordings": _check_recordings(RECORDINGS_DIR),
        "led_count": _scan_led_counts(),
    }
    errors = [
        f"{name}: {issue}"
        for name, check in checks.items()
        if check["status"] == "error"
        for issue in check["issues"]
    ]
    warnings = [
        f"{name}: {issue}"
        for name, check in checks.items()
        if check["status"] == "warning"
        for issue in check["issues"]
    ]
    return {
        "status": "ok" if not errors else "error",
        "lamp_id": lamp_id,
        "port": port,
        "expected_motor_ids": dict(MOTOR_IDS),
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }


def _print_human(report: dict[str, Any]) -> None:
    print(f"LeLamp motor diagnostics: {report['status']}")
    print(f"Lamp ID: {report['lamp_id']}")
    print(f"Port placeholder: {report['port']}")
    print()
    print("Expected motor IDs:")
    for name, motor_id in report["expected_motor_ids"].items():
        print(f"  {name}: {motor_id}")
    print()

    for check_name, check in report["checks"].items():
        print(f"{check_name}: {check['status']}")
        if check_name == "recordings":
            for item in check["recordings"]:
                print(f"  {item['name']}: {item['rows']} rows ({item['status']})")
        elif check_name == "led_count":
            for item in check["occurrences"]:
                print(f"  {item['file']}:{item['line']} led_count={item['led_count']}")
        for issue in check["issues"]:
            print(f"  - {issue}")
        print()

    if report["warnings"]:
        print("Warnings:")
        for warning in report["warnings"]:
            print(f"  - {warning}")
    if report["errors"]:
        print("Errors:")
        for error in report["errors"]:
            print(f"  - {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check LeLamp motor configuration without connecting hardware")
    parser.add_argument("--id", default="lelamp", help="Lamp ID used for constructing local config")
    parser.add_argument("--port", default="/dev/null", help="Placeholder port; no hardware connection is made")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a human-readable report")
    args = parser.parse_args()

    report = run_diagnostics(lamp_id=args.id, port=args.port)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
