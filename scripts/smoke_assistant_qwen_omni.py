#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any


DONE_STATUSES = {"completed", "blocked", "failed", "waiting_confirmation"}


def request_json(base_url: str, token: str, path: str, payload: dict[str, Any] | None = None, *, timeout: int = 35) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    method = "GET"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        method = "POST"
    request = urllib.request.Request(f"{base_url.rstrip('/')}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            envelope = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"{method} {path} returned HTTP {exc.code}: {body}") from exc
    if not envelope.get("ok"):
        raise AssertionError(f"{method} {path} returned API error: {envelope}")
    return envelope["data"]


def wait_task(base_url: str, token: str, task_id: str, *, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = request_json(base_url, token, f"/api/tasks/{task_id}", timeout=20)
        if str(last.get("status")) in DONE_STATUSES:
            return last
        time.sleep(1)
    raise AssertionError(f"task {task_id} did not finish before timeout; last={last}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test LeLamp Assistant Gateway, Qwen-Omni chat, OpenClaw task routing, and audit-safe blocking.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8790")
    parser.add_argument("--token", default="test-console")
    parser.add_argument("--task-timeout", type=int, default=45)
    args = parser.parse_args()

    provider = request_json(args.base_url, args.token, "/api/assistant/providers/status")
    print(f"ok - provider status: foreground={provider['foreground_provider']} qwen={provider['qwen_omni']['status']} openclaw={provider['openclaw']['status']}")
    assert provider["input"]["browser_mic"] == "disabled", "browser mic must remain disabled by default"

    chat = request_json(
        args.base_url,
        args.token,
        "/api/assistant/message",
        {"text": "你是谁", "speak": False, "context": {"page": "assistant"}},
    )
    assert chat["route"]["kind"] == "chat", chat
    if provider["qwen_omni"]["status"] == "available":
        assert chat["assistant_message"]["provider"] == "qwen_omni", chat
    print(f"ok - ordinary chat provider: {chat['assistant_message']['provider']}")

    weather = request_json(
        args.base_url,
        args.token,
        "/api/assistant/message",
        {
            "text": "帮我查看今天深圳天气",
            "speak": False,
            "context": {"page": "assistant", "foreground_reply": "正在为您查询，请稍后。", "foreground_mode": "query"},
        },
        timeout=20,
    )
    assert weather["assistant_ack"]["text"] == "正在为您查询，请稍后。", weather
    assert weather["route"]["requires_openclaw"] is True, weather
    task = wait_task(args.base_url, args.token, weather["task"]["task_id"], timeout_seconds=args.task_timeout)
    assert task["status"] == "completed", task
    final = task["output"]["result"]["assistant_final_message"]["text"]
    assert "来源" in final or "wttr" in final.lower(), final
    events = request_json(args.base_url, args.token, f"/api/tasks/{weather['task']['task_id']}/events")
    assert any(item.get("event") == "task_completed" for item in events["events"]), events
    print("ok - weather query: ack, OpenClaw task, final message, events")

    risky = request_json(
        args.base_url,
        args.token,
        "/api/assistant/message",
        {"text": "删除我电脑桌面上的所有文件", "speak": False, "context": {"page": "assistant"}},
        timeout=20,
    )
    assert risky["route"]["intent"] == "high_risk_blocked", risky
    assert risky["route"]["requires_openclaw"] is False, risky
    blocked_task = request_json(args.base_url, args.token, f"/api/tasks/{risky['task']['task_id']}")
    assert blocked_task["status"] == "blocked", blocked_task
    print("ok - high-risk request preflight blocked without OpenClaw execution")

    print("smoke_assistant_qwen_omni complete")


if __name__ == "__main__":
    main()
