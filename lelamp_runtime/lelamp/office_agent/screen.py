from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .audit import AuditLogger
from .utils import safe_filename
from .workspace import Workspace, WorkspaceError


class ScreenContextService:
    """Screen capture plus optional OCR using system tools."""

    def __init__(self, workspace: Workspace, audit: AuditLogger):
        self.workspace = workspace
        self.audit = audit

    def capture_screen(self) -> dict[str, object]:
        out_path = self.workspace.path_for_new_file(
            f"screen_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        )
        commands = [
            ["gnome-screenshot", "-f", str(out_path)],
            ["grim", str(out_path)],
            ["import", "-window", "root", str(out_path)],
            ["spectacle", "-b", "-n", "-o", str(out_path)],
        ]
        attempted: list[str] = []
        for command in commands:
            if shutil.which(command[0]) is None:
                continue
            attempted.append(command[0])
            try:
                subprocess.run(command, check=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                attempted.append(f"{command[0]}:{type(exc).__name__}")
                continue
            if out_path.exists() and out_path.stat().st_size > 0:
                payload = {
                    "status": "captured",
                    "path": str(out_path),
                    "bytes": out_path.stat().st_size,
                    "command": command[0],
                }
                self.audit.record("screen.capture", target=str(out_path), details=payload)
                return payload

        payload = {
            "status": "unavailable",
            "path": str(out_path),
            "attempted": attempted,
            "install_hint": "Install gnome-screenshot, grim, ImageMagick import, or spectacle to enable screen capture.",
        }
        self.audit.record("screen.capture", status="blocked", target=str(out_path), details=payload)
        return payload

    def ocr_image(self, image_filename: str, *, language: str = "chi_sim+eng") -> dict[str, object]:
        try:
            image_path = self._resolve_image(image_filename)
        except WorkspaceError as exc:
            return {"status": "blocked", "reason": str(exc), "image": image_filename}
        if shutil.which("tesseract") is None:
            payload = {
                "status": "needs_backend",
                "image_path": str(image_path),
                "install_hint": "Install tesseract-ocr and Chinese/English language packs, or connect PaddleOCR.",
            }
            self.audit.record("screen.ocr", status="blocked", target=str(image_path), details=payload)
            return payload

        command = ["tesseract", str(image_path), "stdout", "-l", language]
        try:
            completed = subprocess.run(command, check=False, timeout=30, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            payload = {"status": "timeout", "image_path": str(image_path), "command": command}
            self.audit.record("screen.ocr", status="error", target=str(image_path), details=payload)
            return payload
        if completed.returncode != 0:
            payload = {
                "status": "error",
                "image_path": str(image_path),
                "stderr": completed.stderr.strip()[:1000],
                "command": command,
            }
            self.audit.record("screen.ocr", status="error", target=str(image_path), details=payload)
            return payload

        text = completed.stdout.strip()
        text_path = self.workspace.write_text(
            safe_filename(Path(image_path).stem, suffix="_ocr.txt"),
            text,
            action="screen.ocr_text_write",
        )
        payload = {
            "status": "ok",
            "image_path": str(image_path),
            "text_path": str(text_path),
            "chars": len(text),
            "preview": text[:1000],
        }
        self.audit.record("screen.ocr", target=str(image_path), details={"chars": len(text)})
        return payload

    def summarize_current_screen(self, *, language: str = "chi_sim+eng") -> dict[str, object]:
        capture = self.capture_screen()
        if capture.get("status") != "captured":
            return {
                "status": "unavailable",
                "capture": capture,
                "summary": "当前环境没有可用截图后端，无法读取屏幕。",
            }
        ocr = self.ocr_image(str(capture["path"]), language=language)
        summary = build_screen_summary(str(ocr.get("preview") or ""))
        summary_path = self.workspace.write_text(
            safe_filename("screen_context", suffix=".md"),
            summary,
            action="screen.summary_write",
        )
        payload = {
            "status": "ok" if ocr.get("status") == "ok" else "partial",
            "capture": capture,
            "ocr": ocr,
            "summary_path": str(summary_path),
            "summary": summary,
        }
        self.audit.record("screen.summary", details={"status": payload["status"], "summary_path": str(summary_path)})
        return payload

    def _resolve_image(self, image_filename: str) -> Path:
        candidate = Path(image_filename).expanduser()
        if candidate.is_absolute() and candidate.is_file() and candidate.resolve().is_relative_to(self.workspace.root):
            return candidate.resolve()
        return self.workspace.resolve_workspace_file(image_filename)


def build_screen_summary(text: str) -> str:
    cleaned_lines = [line.strip() for line in text.splitlines() if line.strip()]
    urls = sorted(set(re.findall(r"https?://[^\s)）]+|[\w.-]+\.[a-z]{2,}(?:/[^\s)）]*)?", text, re.I)))[:10]
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", text, re.I)))[:10]
    headings = cleaned_lines[:8]
    lines = [
        "# Screen Context",
        "",
        "## Visible Text",
        *([f"- {line}" for line in headings] or ["- OCR 未返回可用文本。"]),
        "",
        "## Detected Links",
        *([f"- {url}" for url in urls] or ["- None"]),
        "",
        "## Detected Emails",
        *([f"- {email}" for email in emails] or ["- None"]),
    ]
    return "\n".join(lines)
