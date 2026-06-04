from __future__ import annotations

import importlib.util
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .config import OfficeAgentConfig
from .utils import safe_filename
from .workspace import Workspace


@dataclass(frozen=True)
class BrowserStep:
    description: str
    action: str
    selector: str = ""
    value: str = ""
    url: str = ""
    timeout_ms: int = 0

    def audit_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "description": self.description,
            "action": self.action,
        }
        if self.selector:
            payload["selector"] = self.selector
        if self.url:
            payload["url"] = self.url
        if self.timeout_ms:
            payload["timeout_ms"] = self.timeout_ms
        if self.value:
            payload["value"] = f"[redacted:{len(self.value)} chars]"
        return payload


class BrowserAutomationService:
    """Conservative Playwright-backed browser automation.

    This is not global desktop control. It only runs approved task steps inside
    a browser context, writes artifacts to the workspace, and records each run.
    """

    BACKEND = "playwright_browser"

    def __init__(self, workspace: Workspace, audit: AuditLogger, config: OfficeAgentConfig):
        self.workspace = workspace
        self.audit = audit
        self.config = config
        self.output_dir = (workspace.root / "browser_automation").resolve()
        if not self.output_dir.is_relative_to(workspace.root.resolve()):
            raise ValueError("Browser automation output must stay inside the workspace.")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._status_cache: tuple[float, dict[str, object]] | None = None

    def status(self, *, check_launch: bool = True) -> dict[str, object]:
        installed = _module_available("playwright")
        payload: dict[str, object] = {
            "status": "backend_missing" if not installed else "adapter_ready",
            "backend": self.BACKEND,
            "package_installed": installed,
            "workspace_output_dir": str(self.output_dir),
            "permission_mode": self.config.permission_mode.value,
            "desktop_backend": self.config.desktop_backend,
            "headless_default": self.config.browser_automation_headless,
            "timeout_ms": self.config.browser_automation_timeout_ms,
            "max_steps": self.config.browser_automation_max_steps,
            "safety": [
                "Requires an approved desktop task.",
                "Requires explicit execute authorization from the caller.",
                "Only accepts deterministic URL/click/fill/wait/text/screenshot steps.",
                "Only http/https navigation is allowed.",
                "Fill values are redacted in audit payloads.",
                "Artifacts are written inside the workspace.",
            ],
            "install_hint": "Run `uv sync --extra desktop` and `uv run python -m playwright install chromium`.",
        }
        if not installed or not check_launch:
            return payload

        now = time.monotonic()
        if self._status_cache and now - self._status_cache[0] < 60:
            return self._status_cache[1]

        launch = self._probe_launch()
        if launch["status"] == "available":
            payload.update({"status": "available", "launch_probe": launch})
        else:
            payload.update({"status": "backend_missing", "launch_probe": launch})
        self._status_cache = (now, payload)
        return payload

    def execute_task(
        self,
        task: dict[str, object],
        *,
        actor: str = "web_console",
        authorized: bool = False,
        headless: bool | None = None,
        allowed_hosts: list[str] | None = None,
    ) -> dict[str, object]:
        task_id = str(task.get("id") or "")
        goal = str(task.get("goal") or task_id or "browser_task")
        if task.get("status") != "approved":
            payload = {
                "status": "needs_confirmation",
                "backend": self.BACKEND,
                "task_id": task_id,
                "message": "Task must be approved before browser automation can execute.",
            }
            self._attach_report(payload, task_id or goal)
            self.audit.record("browser_automation.execute", status="blocked", target=task_id, details=payload)
            return payload
        if not authorized:
            payload = {
                "status": "needs_confirmation",
                "backend": self.BACKEND,
                "task_id": task_id,
                "message": "Explicit execute authorization is required.",
            }
            self._attach_report(payload, task_id or goal)
            self.audit.record("browser_automation.execute", status="blocked", target=task_id, details=payload)
            return payload

        status = self.status(check_launch=True)
        if status["status"] != "available":
            payload = {
                "status": "backend_missing",
                "backend": self.BACKEND,
                "task_id": task_id,
                "message": "Playwright or a Chromium browser runtime is not available.",
                "backend_status": status,
            }
            self._attach_report(payload, task_id or goal)
            self.audit.record("browser_automation.execute", status="blocked", target=task_id, details=payload)
            return payload

        steps = self._parse_task_steps(task)
        if not steps:
            payload = {
                "status": "blocked",
                "backend": self.BACKEND,
                "task_id": task_id,
                "message": "No supported browser automation steps were found.",
            }
            self._attach_report(payload, task_id or goal)
            self.audit.record("browser_automation.execute", status="blocked", target=task_id, details=payload)
            return payload
        if len(steps) > self.config.browser_automation_max_steps:
            payload = {
                "status": "blocked",
                "backend": self.BACKEND,
                "task_id": task_id,
                "message": f"Step count exceeds max_steps={self.config.browser_automation_max_steps}.",
                "step_count": len(steps),
            }
            self._attach_report(payload, task_id or goal)
            self.audit.record("browser_automation.execute", status="blocked", target=task_id, details=payload)
            return payload

        host_set = {host.strip().lower() for host in (allowed_hosts or []) if host.strip()}
        for step in steps:
            if step.url:
                host = urllib.parse.urlparse(step.url).hostname or ""
                if host:
                    host_set.add(host.lower())

        run_dir = self._run_dir(task_id or safe_filename(goal, default="browser_task"))
        run_dir.mkdir(parents=True, exist_ok=True)
        started_at = time.time()
        payload = self._execute_with_playwright(
            steps,
            run_dir=run_dir,
            task_id=task_id,
            goal=goal,
            actor=actor,
            headless=self.config.browser_automation_headless if headless is None else bool(headless),
            allowed_hosts=sorted(host_set),
        )
        payload["duration_ms"] = int((time.time() - started_at) * 1000)
        self._attach_report(payload, task_id or goal, run_dir=run_dir)
        self.audit.record(
            "browser_automation.execute",
            status=_audit_status(str(payload.get("status"))),
            target=task_id,
            details={
                "goal": goal,
                "status": payload.get("status"),
                "step_count": payload.get("step_count"),
                "report_workspace_name": payload.get("report_workspace_name"),
                "headless": payload.get("headless"),
            },
            actor=actor,
        )
        return payload

    def _execute_with_playwright(
        self,
        steps: list[BrowserStep],
        *,
        run_dir: Path,
        task_id: str,
        goal: str,
        actor: str,
        headless: bool,
        allowed_hosts: list[str],
    ) -> dict[str, object]:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        executed: list[dict[str, object]] = []
        final_url = ""
        page_title = ""
        text_sample = ""
        screenshots: list[dict[str, str]] = []
        status = "completed"
        message = "Browser automation completed."
        timeout_ms = self.config.browser_automation_timeout_ms

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=headless)
                context = browser.new_context(
                    viewport={"width": 1366, "height": 900},
                    user_agent="OpenClaw-BrowserAutomation/1.0",
                )
                page = context.new_page()
                for index, step in enumerate(steps, start=1):
                    started = time.time()
                    try:
                        step_result = self._run_step(page, step, index=index, run_dir=run_dir, timeout_ms=timeout_ms)
                        final_url = page.url
                        page_title = page.title()
                        if not _url_host_allowed(final_url, allowed_hosts):
                            status = "blocked"
                            message = f"Navigation reached an unapproved host: {urllib.parse.urlparse(final_url).hostname or final_url}"
                            step_result["status"] = "blocked"
                            step_result["message"] = message
                            executed.append(step_result)
                            break
                        if step_result.get("screenshot_path"):
                            screenshots.append(
                                {
                                    "path": str(step_result["screenshot_path"]),
                                    "workspace_name": str(Path(str(step_result["screenshot_path"])).relative_to(self.workspace.root)),
                                }
                            )
                        if step_result.get("text_sample"):
                            text_sample = str(step_result["text_sample"])
                        step_result["duration_ms"] = int((time.time() - started) * 1000)
                        executed.append(step_result)
                    except PlaywrightTimeoutError as exc:
                        status = "failed"
                        message = f"Browser step timed out: {step.description}"
                        executed.append(
                            {
                                "index": index,
                                **step.audit_dict(),
                                "status": "timeout",
                                "error": str(exc)[:500],
                                "duration_ms": int((time.time() - started) * 1000),
                            }
                        )
                        break
                    except PlaywrightError as exc:
                        status = "failed"
                        message = f"Browser step failed: {step.description}"
                        executed.append(
                            {
                                "index": index,
                                **step.audit_dict(),
                                "status": "failed",
                                "error": str(exc)[:500],
                                "duration_ms": int((time.time() - started) * 1000),
                            }
                        )
                        break
                if final_url:
                    final_screenshot = run_dir / "final.png"
                    page.screenshot(path=str(final_screenshot), full_page=True)
                    screenshots.append(
                        {
                            "path": str(final_screenshot),
                            "workspace_name": str(final_screenshot.relative_to(self.workspace.root)),
                        }
                    )
                context.close()
                browser.close()
        except PlaywrightError as exc:
            return {
                "status": "backend_missing" if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc).lower() else "failed",
                "backend": self.BACKEND,
                "task_id": task_id,
                "goal": goal,
                "actor": actor,
                "headless": headless,
                "allowed_hosts": allowed_hosts,
                "step_count": len(steps),
                "steps": [step.audit_dict() for step in steps],
                "executed_steps": executed,
                "screenshots": screenshots,
                "message": str(exc)[:1000],
                "install_hint": "Run `uv run python -m playwright install chromium`.",
            }

        return {
            "status": status,
            "backend": self.BACKEND,
            "task_id": task_id,
            "goal": goal,
            "actor": actor,
            "headless": headless,
            "allowed_hosts": allowed_hosts,
            "step_count": len(steps),
            "steps": [step.audit_dict() for step in steps],
            "executed_steps": executed,
            "screenshots": screenshots,
            "final_url": final_url,
            "page_title": page_title,
            "text_sample": text_sample,
            "message": message,
        }

    def _run_step(self, page: Any, step: BrowserStep, *, index: int, run_dir: Path, timeout_ms: int) -> dict[str, object]:
        result: dict[str, object] = {"index": index, **step.audit_dict(), "status": "completed"}
        if step.action == "goto":
            page.goto(step.url, wait_until="domcontentloaded", timeout=timeout_ms)
            return result
        if step.action == "click":
            locator = _locator(page, step.selector)
            locator.click(timeout=timeout_ms)
            return result
        if step.action == "fill":
            locator = _locator(page, step.selector)
            locator.fill(step.value, timeout=timeout_ms)
            return result
        if step.action == "press":
            page.keyboard.press(step.value)
            return result
        if step.action == "wait":
            if step.selector:
                _locator(page, step.selector).wait_for(timeout=step.timeout_ms or timeout_ms)
            else:
                page.wait_for_timeout(step.timeout_ms or 1000)
            return result
        if step.action == "extract_text":
            text = page.locator("body").inner_text(timeout=timeout_ms)
            text_path = run_dir / f"step-{index:02d}-text.txt"
            text_path.write_text(text, encoding="utf-8")
            result["text_path"] = str(text_path)
            result["text_workspace_name"] = str(text_path.relative_to(self.workspace.root))
            result["text_sample"] = text[:2000]
            return result
        if step.action == "screenshot":
            screenshot_path = run_dir / f"step-{index:02d}.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            result["screenshot_path"] = str(screenshot_path)
            result["screenshot_workspace_name"] = str(screenshot_path.relative_to(self.workspace.root))
            return result
        result.update({"status": "blocked", "message": f"Unsupported action: {step.action}"})
        return result

    def _parse_task_steps(self, task: dict[str, object]) -> list[BrowserStep]:
        parsed: list[BrowserStep] = []
        raw_steps = task.get("steps") if isinstance(task.get("steps"), list) else []
        for raw in raw_steps:
            description = str(raw.get("description") if isinstance(raw, dict) else raw or "").strip()
            if not description:
                continue
            step = parse_browser_step(description)
            if step is None:
                self.audit.record(
                    "browser_automation.step_parse",
                    status="blocked",
                    target=str(task.get("id") or ""),
                    details={"description": description, "reason": "unsupported_browser_step"},
                )
                return []
            parsed.append(step)
        return parsed

    def _run_dir(self, task_id: str) -> Path:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        clean_id = safe_filename(task_id, default="browser_task")
        return (self.output_dir / f"{timestamp}-{clean_id}").resolve()

    def _attach_report(self, payload: dict[str, object], task_id: str, *, run_dir: Path | None = None) -> None:
        output_dir = run_dir or self._run_dir(task_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        payload["workspace_dir"] = str(output_dir.relative_to(self.workspace.root))
        report_path = output_dir / "report.json"
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["report_path"] = str(report_path)
        payload["report_workspace_name"] = str(report_path.relative_to(self.workspace.root))

    def _probe_launch(self) -> dict[str, object]:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            return {"status": "backend_missing", "error": str(exc)[:500]}
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                browser.close()
            return {"status": "available", "browser": "chromium"}
        except PlaywrightError as exc:
            return {
                "status": "backend_missing",
                "browser": "chromium",
                "error": str(exc)[:1000],
                "install_hint": "Run `uv run python -m playwright install chromium`.",
            }


def parse_browser_step(description: str) -> BrowserStep | None:
    text = description.strip()
    normalized = text.lower()
    url = _extract_http_url(text)
    if url and _has_any(normalized, "open", "go to", "goto", "visit", "打开", "访问", "进入"):
        return BrowserStep(description=text, action="goto", url=url)
    if re.fullmatch(r"https?://\S+", text):
        return BrowserStep(description=text, action="goto", url=text)
    if _has_any(normalized, "screenshot", "capture page", "截图", "截屏"):
        return BrowserStep(description=text, action="screenshot")
    if _has_any(normalized, "extract text", "read page", "page text", "读取页面", "提取文本", "页面文本", "查看页面"):
        return BrowserStep(description=text, action="extract_text")

    click_match = re.search(r"(?:click|点击)\s*(?:text=|文本=|css=)?(.+)$", text, re.I)
    if click_match:
        selector = click_match.group(1).strip(" ：:=\t")
        if selector:
            if not selector.startswith(("text=", "css=", "#", ".", "[", "//")):
                selector = f"text={selector}"
            return BrowserStep(description=text, action="click", selector=selector)

    fill_match = re.search(r"(?:fill|type|填写|输入)\s+(.+?)\s*(?:=|为|with|value=|内容是)\s*(.+)$", text, re.I)
    if fill_match:
        selector = fill_match.group(1).strip()
        value = fill_match.group(2).strip()
        if selector and value:
            if not selector.startswith(("text=", "css=", "#", ".", "[", "//")):
                selector = f"css={selector}"
            return BrowserStep(description=text, action="fill", selector=selector, value=value)

    press_match = re.search(r"(?:press|按下)\s+([A-Za-z0-9+_-]+)$", text, re.I)
    if press_match:
        key = press_match.group(1).strip()
        if key.lower() in {"enter", "tab", "escape", "esc"} or re.fullmatch(r"[A-Za-z0-9]", key):
            return BrowserStep(description=text, action="press", value="Escape" if key.lower() == "esc" else key)

    wait_match = re.search(r"(?:wait|等待)\s*(\d+(?:\.\d+)?)?\s*(ms|毫秒|s|秒)?", text, re.I)
    if wait_match and wait_match.group(0).strip():
        amount = float(wait_match.group(1) or "1")
        unit = (wait_match.group(2) or "s").lower()
        timeout_ms = int(amount if unit in {"ms", "毫秒"} else amount * 1000)
        return BrowserStep(description=text, action="wait", timeout_ms=max(100, min(timeout_ms, 10000)))

    wait_selector = re.search(r"(?:wait for|等待出现)\s*(?:text=|文本=|css=)?(.+)$", text, re.I)
    if wait_selector:
        selector = wait_selector.group(1).strip()
        if selector:
            if not selector.startswith(("text=", "css=", "#", ".", "[", "//")):
                selector = f"text={selector}"
            return BrowserStep(description=text, action="wait", selector=selector)

    return None


def _locator(page: Any, selector: str) -> Any:
    if selector.startswith("text="):
        return page.get_by_text(selector.removeprefix("text="), exact=False).first
    if selector.startswith("css="):
        return page.locator(selector.removeprefix("css=")).first
    return page.locator(selector).first


def _extract_http_url(text: str) -> str:
    match = re.search(r"https?://[^\s，。\"']+", text, re.I)
    if not match:
        return ""
    url = match.group(0).rstrip(").,;，。")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url


def _url_host_allowed(url: str, allowed_hosts: list[str]) -> bool:
    if not url or url == "about:blank":
        return True
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return not allowed_hosts or host in {item.lower() for item in allowed_hosts}


def _has_any(text: str, *markers: str) -> bool:
    return any(marker in text for marker in markers)


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _audit_status(status: str) -> str:
    if status in {"completed", "available", "adapter_ready"}:
        return "ok"
    if status in {"blocked", "needs_confirmation", "backend_missing"}:
        return "blocked"
    return "error"
