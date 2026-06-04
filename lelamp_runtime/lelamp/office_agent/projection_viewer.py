from __future__ import annotations

import html
import json
import re
import socket
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .audit import AuditLogger


def latest_projection_file(projection_dir: Path) -> Path | None:
    files = [path for path in projection_dir.glob("*.md") if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


@dataclass
class ProjectionDocument:
    title: str = "OpenClaw Display"
    mode: str = "meeting"
    fields: dict[str, str] = field(default_factory=dict)
    sections: dict[str, list[str]] = field(default_factory=dict)
    paragraphs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DisplayProfile:
    mode: str = "manual"
    brightness: float = 1.0
    contrast: float = 1.0
    scale: float = 1.0
    keystone_x: float = 0.0
    keystone_y: float = 0.0
    ambient_lux: float | None = None
    note: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "brightness": self.brightness,
            "contrast": self.contrast,
            "scale": self.scale,
            "keystone_x": self.keystone_x,
            "keystone_y": self.keystone_y,
            "ambient_lux": self.ambient_lux,
            "note": self.note,
        }


def parse_projection_markdown(markdown: str) -> ProjectionDocument:
    document = ProjectionDocument()
    current_section: str | None = None

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            current_section = None
            continue
        if line.startswith("# "):
            document.title = line[2:].strip() or document.title
            continue
        if line.startswith("## "):
            current_section = line[3:].strip()
            document.sections.setdefault(current_section, [])
            continue
        if line.startswith("### "):
            current_section = line[4:].strip()
            document.sections.setdefault(current_section, [])
            continue

        if not line.startswith("-") and ":" in line:
            key, value = line.split(":", 1)
            key_slug = slugify(key)
            document.fields[key_slug] = value.strip()
            if key_slug == "mode":
                document.mode = slugify(value) or document.mode
            continue

        if current_section:
            document.sections.setdefault(current_section, []).append(line)
        else:
            document.paragraphs.append(line)

    return document


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def projection_to_html(markdown: str) -> tuple[str, str, str]:
    document = parse_projection_markdown(markdown)
    mode = slugify(document.mode) or "meeting"
    if mode == "countdown":
        return document.title, mode, render_countdown_card(document)
    if mode in {"confirmation", "action_card"}:
        return document.title, mode, render_action_or_confirmation_card(document)
    if mode in {"status", "status_card", "warning"}:
        return document.title, mode, render_status_card(document)
    return document.title, mode, render_generic_card(markdown)


def render_countdown_card(document: ProjectionDocument) -> str:
    countdown = document.fields.get("countdown", "--:--")
    ends_at = document.fields.get("ends_at", "-")
    message = " ".join(document.paragraphs).strip() or "请在倒计时结束前完成当前步骤。"
    return f"""
<section class="countdown-layout">
  <div class="kicker">倒计时</div>
  <h1>{inline_markdown(document.title)}</h1>
  <div class="countdown-value">{html.escape(countdown)}</div>
  <div class="countdown-meta">
    <span>结束时间</span>
    <strong>{html.escape(ends_at)}</strong>
  </div>
  <p class="lead">{inline_markdown(message)}</p>
</section>"""


def render_action_or_confirmation_card(document: ProjectionDocument) -> str:
    decisions = list_items(section_lines(document, "decisions"))
    actions = list_items(section_lines(document, "action_items", "actions"))
    mode_label = "会议确认" if document.mode == "confirmation" else "行动确认"
    return f"""
<section class="board-layout">
  <div class="board-hero">
    <div class="kicker">{html.escape(mode_label)}</div>
    <h1>{inline_markdown(document.title)}</h1>
  </div>
  <div class="board-grid">
    {render_panel("决策", decisions, "decision")}
    {render_panel("行动项", actions, "action")}
  </div>
</section>"""


def render_status_card(document: ProjectionDocument) -> str:
    status = document.fields.get("status", "ready")
    accent = slugify(document.fields.get("accent", "blue")) or "blue"
    details = list_items(section_lines(document, "details"))
    return f"""
<section class="status-layout accent-{html.escape(accent)}">
  <div class="status-hero">
    <div class="kicker">状态卡</div>
    <h1>{inline_markdown(document.title)}</h1>
    <div class="status-value">{inline_markdown(status)}</div>
  </div>
  {render_panel("细节", details, "detail")}
</section>"""


def render_generic_card(markdown: str) -> str:
    return f"<section class=\"generic-layout\">{markdown_to_html(markdown)}</section>"


def section_lines(document: ProjectionDocument, *names: str) -> list[str]:
    wanted = {slugify(name) for name in names}
    for title, lines in document.sections.items():
        if slugify(title) in wanted:
            return lines
    return []


def list_items(lines: list[str]) -> list[str]:
    items: list[str] = []
    empty_values = {"none", "no action items.", "no details."}
    for line in lines:
        item = line.strip()
        if item.startswith("- [ ] "):
            item = item[6:].strip()
        elif item.startswith("- "):
            item = item[2:].strip()
        if item and item.lower() not in empty_values:
            items.append(item)
    return items


def render_panel(title: str, items: list[str], kind: str) -> str:
    if items:
        item_html = "\n".join(f"<li>{inline_markdown(item)}</li>" for item in items)
    else:
        item_html = "<li class=\"muted\">暂无内容</li>"
    return f"""
<section class="panel panel-{html.escape(kind)}">
  <div class="panel-heading">
    <span>{html.escape(title)}</span>
    <strong>{len(items)}</strong>
  </div>
  <ol class="panel-list">
    {item_html}
  </ol>
</section>"""


def markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    output: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            output.append("</ul>")
            in_list = False

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line:
            close_list()
            continue
        if line.startswith("# "):
            close_list()
            output.append(f"<h1>{inline_markdown(line[2:].strip())}</h1>")
        elif line.startswith("## "):
            close_list()
            output.append(f"<h2>{inline_markdown(line[3:].strip())}</h2>")
        elif line.startswith("### "):
            close_list()
            output.append(f"<h3>{inline_markdown(line[4:].strip())}</h3>")
        elif line.startswith("- [ ] "):
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li class=\"unchecked\">{inline_markdown(line[6:].strip())}</li>")
        elif line.startswith("- "):
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{inline_markdown(line[2:].strip())}</li>")
        else:
            close_list()
            if line.startswith("Mode:"):
                output.append(f"<p class=\"mode\">{inline_markdown(line)}</p>")
            elif line.startswith("Countdown:"):
                output.append(f"<p class=\"countdown\">{inline_markdown(line.replace('Countdown:', '').strip())}</p>")
            else:
                output.append(f"<p>{inline_markdown(line)}</p>")
    close_list()
    return "\n".join(output)


def inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    return escaped


def render_page(
    *,
    title: str,
    body_html: str,
    source_name: str,
    updated_at: str,
    refresh_seconds: int,
    mode: str = "meeting",
    display_profile: DisplayProfile | None = None,
) -> str:
    profile = display_profile or DisplayProfile()
    payload = {
        "source": source_name,
        "updated_at": updated_at,
        "refresh_seconds": refresh_seconds,
        "mode": mode,
        "display_profile": profile.as_dict(),
    }
    brightness = _css_number(profile.brightness)
    contrast = _css_number(profile.contrast)
    scale = _css_number(profile.scale)
    keystone_x = _css_number(profile.keystone_x)
    keystone_y = _css_number(profile.keystone_y)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="{refresh_seconds}">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: Inter, "PingFang SC", "Microsoft YaHei", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #eef2f6;
      color: #101828;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr auto;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.96), rgba(238,242,246,0.98)),
        #eef2f6;
    }}
    header, footer {{
      padding: 18px 40px;
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      color: #475467;
      font-size: 15px;
    }}
    header {{
      border-bottom: 1px solid rgba(148, 163, 184, 0.28);
    }}
    footer {{
      border-top: 1px solid rgba(148, 163, 184, 0.2);
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 720;
      color: #0f172a;
    }}
    .brand-mark {{
      width: 32px;
      height: 32px;
      border-radius: 8px;
      display: inline-grid;
      place-items: center;
      background: #0f766e;
      color: #ffffff;
      font-size: 16px;
      font-weight: 800;
    }}
    main {{
      width: min(1260px, calc(100vw - 80px));
      margin: 0 auto;
      align-self: center;
      padding: 34px 0 44px;
      transform-origin: center center;
      transform: perspective(1400px) rotateY({keystone_x}deg) rotateX({keystone_y}deg) scale({scale});
      filter: brightness({brightness}) contrast({contrast});
    }}
    .surface {{
      min-height: min(760px, calc(100vh - 170px));
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 26px;
    }}
    h1 {{
      margin: 0 0 20px;
      font-size: 72px;
      line-height: 1.02;
      font-weight: 760;
      letter-spacing: 0;
      color: #0f172a;
    }}
    h2 {{
      margin: 20px 0 10px;
      font-size: 34px;
      line-height: 1.12;
      color: #1f2937;
    }}
    h3 {{
      margin: 16px 0 8px;
      font-size: 25px;
      color: #374151;
    }}
    p, li {{
      font-size: 30px;
      line-height: 1.35;
      margin: 8px 0;
    }}
    ul {{
      margin: 8px 0 18px;
      padding-left: 1.2em;
    }}
    li + li {{ margin-top: 10px; }}
    .mode {{
      display: inline-flex;
      width: fit-content;
      border: 1px solid #cbd5e1;
      background: #ffffff;
      border-radius: 8px;
      padding: 7px 12px;
      font-size: 17px;
      color: #334155;
    }}
    .countdown {{
      font-variant-numeric: tabular-nums;
      font-size: 140px;
      line-height: 0.95;
      font-weight: 800;
      color: #0f766e;
      margin: 10px 0 18px;
    }}
    .unchecked::marker {{ content: "□ "; }}
    code {{
      background: #e5e7eb;
      padding: 0.08em 0.24em;
      border-radius: 6px;
      font-size: 0.88em;
    }}
    .empty {{
      border: 2px dashed #cbd5e1;
      padding: 44px;
      border-radius: 8px;
      color: #475569;
      background: #ffffff;
    }}
    .kicker {{
      display: inline-flex;
      align-items: center;
      width: fit-content;
      min-height: 34px;
      border: 1px solid rgba(15, 118, 110, 0.22);
      border-radius: 8px;
      padding: 6px 12px;
      background: #ecfdf5;
      color: #0f766e;
      font-size: 17px;
      font-weight: 760;
    }}
    .lead {{
      max-width: 980px;
      color: #344054;
      font-size: 34px;
    }}
    .countdown-layout,
    .status-layout,
    .board-layout,
    .generic-layout {{
      width: 100%;
    }}
    .countdown-layout {{
      display: grid;
      gap: 24px;
      align-content: center;
    }}
    .countdown-value {{
      font-variant-numeric: tabular-nums;
      font-size: 172px;
      line-height: 0.9;
      font-weight: 850;
      color: #0f766e;
    }}
    .countdown-meta {{
      display: inline-flex;
      align-items: baseline;
      gap: 16px;
      width: fit-content;
      border-left: 7px solid #f97316;
      padding: 10px 18px;
      background: rgba(255, 255, 255, 0.74);
      border-radius: 8px;
      color: #475467;
      font-size: 24px;
    }}
    .countdown-meta strong {{
      color: #101828;
      font-size: 32px;
      font-variant-numeric: tabular-nums;
    }}
    .board-hero {{
      display: grid;
      gap: 16px;
      margin-bottom: 28px;
    }}
    .board-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 22px;
      align-items: stretch;
    }}
    .panel {{
      min-height: 330px;
      border: 1px solid rgba(148, 163, 184, 0.36);
      border-radius: 8px;
      padding: 28px;
      background: rgba(255, 255, 255, 0.9);
      box-shadow: 0 18px 44px rgba(15, 23, 42, 0.08);
    }}
    .panel-heading {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding-bottom: 16px;
      margin-bottom: 18px;
      border-bottom: 1px solid rgba(148, 163, 184, 0.32);
      color: #344054;
      font-size: 24px;
      font-weight: 760;
    }}
    .panel-heading strong {{
      min-width: 42px;
      height: 42px;
      display: inline-grid;
      place-items: center;
      border-radius: 8px;
      background: #101828;
      color: #ffffff;
      font-size: 22px;
      font-variant-numeric: tabular-nums;
    }}
    .panel-list {{
      display: grid;
      gap: 14px;
      margin: 0;
      padding: 0;
      list-style: none;
      counter-reset: projection-item;
    }}
    .panel-list li {{
      counter-increment: projection-item;
      display: grid;
      grid-template-columns: 44px 1fr;
      gap: 14px;
      align-items: start;
      margin: 0;
      color: #1d2939;
      font-size: 29px;
      line-height: 1.28;
    }}
    .panel-list li::before {{
      content: counter(projection-item);
      width: 44px;
      height: 44px;
      display: inline-grid;
      place-items: center;
      border-radius: 8px;
      background: #e0f2fe;
      color: #075985;
      font-size: 22px;
      font-weight: 780;
    }}
    .panel-action .panel-list li::before {{
      background: #ffedd5;
      color: #9a3412;
    }}
    .panel-detail .panel-list li::before {{
      background: #ecfdf3;
      color: #027a48;
    }}
    .panel-list li.muted {{
      color: #667085;
    }}
    .status-layout {{
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(360px, 0.8fr);
      gap: 28px;
      align-items: stretch;
    }}
    .status-hero {{
      min-height: 430px;
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 20px;
      border-left: 9px solid #2563eb;
      padding: 36px 42px;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.9);
      box-shadow: 0 18px 44px rgba(15, 23, 42, 0.08);
    }}
    .status-value {{
      width: fit-content;
      max-width: 100%;
      border-radius: 8px;
      padding: 14px 20px;
      background: #eff6ff;
      color: #1d4ed8;
      font-size: 48px;
      font-weight: 820;
      line-height: 1.12;
    }}
    .accent-green .status-hero {{ border-left-color: #16a34a; }}
    .accent-green .status-value {{ background: #ecfdf3; color: #027a48; }}
    .accent-teal .status-hero {{ border-left-color: #0f766e; }}
    .accent-teal .status-value {{ background: #ccfbf1; color: #0f766e; }}
    .accent-orange .status-hero {{ border-left-color: #f97316; }}
    .accent-orange .status-value {{ background: #fff7ed; color: #c2410c; }}
    .meta {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .meta-pill {{
      min-width: 0;
      border: 1px solid rgba(148, 163, 184, 0.38);
      border-radius: 8px;
      padding: 8px 12px;
      background: rgba(255, 255, 255, 0.74);
    }}
    .profile-pill {{
      max-width: min(520px, 60vw);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    @media (max-width: 720px) {{
      header, footer {{ padding: 14px 18px; font-size: 13px; }}
      main {{ width: min(100vw - 32px, 1180px); padding: 22px 0 28px; }}
      .surface {{ min-height: calc(100vh - 130px); }}
      h1 {{ font-size: 42px; }}
      h2 {{ font-size: 28px; }}
      p, li {{ font-size: 24px; }}
      .lead {{ font-size: 25px; }}
      .countdown-value {{ font-size: 92px; }}
      .countdown-meta {{ font-size: 18px; }}
      .countdown-meta strong {{ font-size: 24px; }}
      .board-grid,
      .status-layout {{ grid-template-columns: 1fr; }}
      .panel {{ padding: 22px; min-height: auto; }}
      .panel-list li {{ font-size: 23px; grid-template-columns: 38px 1fr; }}
      .panel-list li::before {{ width: 38px; height: 38px; font-size: 19px; }}
      .status-hero {{ min-height: 310px; padding: 28px; }}
      .status-value {{ font-size: 34px; }}
    }}
  </style>
  <script>
    window.__OPENCLAW_PROJECTION__ = {json.dumps(payload, ensure_ascii=False)};
    document.addEventListener('keydown', (event) => {{
      if (event.key === 'f' || event.key === 'F') {{
        document.documentElement.requestFullscreen?.();
      }}
      if (event.key === 'r' || event.key === 'R') {{
        window.location.reload();
      }}
    }});
  </script>
</head>
<body class="mode-{html.escape(mode)}">
  <header>
    <div class="brand"><span class="brand-mark">OC</span><span>OpenClaw Display</span></div>
    <div class="meta meta-pill">{html.escape(source_name)}</div>
  </header>
  <main>
    <section class="surface">
      {body_html}
    </section>
  </main>
  <footer>
    <div>Updated: {html.escape(updated_at)}</div>
    <div class="meta-pill profile-pill">Display profile: {html.escape(profile.mode)} · brightness {brightness} · contrast {contrast}</div>
    <div>Auto refresh: {refresh_seconds}s</div>
  </footer>
</body>
</html>"""


class ProjectionPreviewServer:
    def __init__(self, projection_dir: Path, audit: AuditLogger, *, refresh_seconds: int = 2, display_profile_path: Path | None = None):
        self.projection_dir = projection_dir
        self.audit = audit
        self.refresh_seconds = max(1, refresh_seconds)
        self.display_profile_path = display_profile_path or projection_dir / "display_profile.json"

    def make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/health":
                    self._send_json({"status": "ok", "projection_dir": str(server.projection_dir), "display_profile": server.load_display_profile().as_dict()})
                    return
                if parsed.path == "/profile":
                    self._send_json(server.load_display_profile().as_dict())
                    return
                if parsed.path == "/latest":
                    latest = latest_projection_file(server.projection_dir)
                    self._send_json(
                        {
                            "path": str(latest) if latest else None,
                            "name": latest.name if latest else None,
                            "mtime": latest.stat().st_mtime if latest else None,
                        }
                    )
                    return
                if parsed.path not in {"/", "/index.html"}:
                    self.send_error(404)
                    return
                self._send_html(server.render_latest())

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def _send_html(self, content: str) -> None:
                encoded = content.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _send_json(self, payload: object) -> None:
                encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        return Handler

    def render_latest(self) -> str:
        profile = self.load_display_profile()
        latest = latest_projection_file(self.projection_dir)
        if latest is None:
            body = "<div class=\"empty\"><h1>No Projection Yet</h1><p>Render a card with openclaw_cli.py project or lelamp actions.</p></div>"
            return render_page(
                title="OpenClaw Display Preview",
                body_html=body,
                source_name="No projection file",
                updated_at="-",
                refresh_seconds=self.refresh_seconds,
                mode="empty",
                display_profile=profile,
            )
        text = latest.read_text(encoding="utf-8", errors="replace")
        updated_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest.stat().st_mtime))
        projection_title, mode, body_html = projection_to_html(text)
        return render_page(
            title=f"OpenClaw - {projection_title}",
            body_html=body_html,
            source_name=latest.name,
            updated_at=updated_at,
            refresh_seconds=self.refresh_seconds,
            mode=mode,
            display_profile=profile,
        )

    def load_display_profile(self) -> DisplayProfile:
        try:
            payload = json.loads(self.display_profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return DisplayProfile()
        if not isinstance(payload, dict):
            return DisplayProfile()
        return DisplayProfile(
            mode=str(payload.get("mode") or "manual"),
            brightness=_clamp_float(payload.get("brightness"), 0.55, 1.65, 1.0),
            contrast=_clamp_float(payload.get("contrast"), 0.7, 1.6, 1.0),
            scale=_clamp_float(payload.get("scale"), 0.82, 1.08, 1.0),
            keystone_x=_clamp_float(payload.get("keystone_x"), -12.0, 12.0, 0.0),
            keystone_y=_clamp_float(payload.get("keystone_y"), -12.0, 12.0, 0.0),
            ambient_lux=_optional_float(payload.get("ambient_lux")),
            note=str(payload.get("note") or ""),
        )

    def serve(self, *, host: str, port: int, open_browser: bool = False) -> None:
        self.projection_dir.mkdir(parents=True, exist_ok=True)
        httpd = ThreadingHTTPServer((host, port), self.make_handler())
        bound_host, bound_port = httpd.server_address[:2]
        url_host = "127.0.0.1" if bound_host in {"0.0.0.0", ""} else bound_host
        url = f"http://{url_host}:{bound_port}/"
        self.audit.record(
            "projection.preview_start",
            target=url,
            details={"projection_dir": str(self.projection_dir), "refresh_seconds": self.refresh_seconds},
        )
        if open_browser:
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        print(f"OpenClaw projection preview: {url}")
        print(f"Projection directory: {self.projection_dir}")
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
            self.audit.record("projection.preview_stop", target=url)


def build_display_profile(
    *,
    ambient_lux: float | None = None,
    calibration: dict[str, object] | None = None,
    mode: str = "manual",
    brightness: float | None = None,
    contrast: float | None = None,
    scale: float | None = None,
    keystone_x: float | None = None,
    keystone_y: float | None = None,
) -> DisplayProfile:
    next_brightness = 1.0
    next_contrast = 1.0
    note_parts: list[str] = []
    if ambient_lux is not None:
        if ambient_lux < 80:
            next_brightness = 1.18
            next_contrast = 1.06
            note_parts.append("low ambient light: lifted brightness gently")
        elif ambient_lux > 600:
            next_brightness = 1.42
            next_contrast = 1.18
            note_parts.append("bright room: increased digital brightness/contrast")
        elif ambient_lux > 300:
            next_brightness = 1.22
            next_contrast = 1.1
            note_parts.append("moderate room light: increased contrast")
        else:
            note_parts.append("ambient light is in normal range")
    if calibration:
        brightness_payload = calibration.get("brightness") if isinstance(calibration.get("brightness"), dict) else {}
        focus_payload = calibration.get("focus") if isinstance(calibration.get("focus"), dict) else {}
        keystone_payload = calibration.get("keystone") if isinstance(calibration.get("keystone"), dict) else {}
        if str(brightness_payload.get("status") or "") in {"too_dark", "dim", "warning"}:
            next_brightness = max(next_brightness, 1.28)
            next_contrast = max(next_contrast, 1.12)
            note_parts.append("calibration reported dim projection")
        if str(focus_payload.get("status") or "") in {"soft", "blurred", "warning"}:
            next_contrast = max(next_contrast, 1.16)
            note_parts.append("focus appears soft: increased contrast only")
        if keystone_payload:
            angle = _optional_float(keystone_payload.get("estimated_skew_degrees") or keystone_payload.get("skew_degrees"))
            if angle is not None:
                keystone_x = keystone_x if keystone_x is not None else max(-8.0, min(8.0, -angle * 0.45))
                note_parts.append("applied digital keystone preview compensation")
            else:
                horizontal = _optional_float(keystone_payload.get("horizontal_skew_pct"))
                vertical = _optional_float(keystone_payload.get("vertical_skew_pct"))
                if horizontal is not None and horizontal > 0.5:
                    keystone_x = keystone_x if keystone_x is not None else max(-8.0, min(8.0, -horizontal * 0.35))
                    note_parts.append("applied horizontal digital keystone compensation")
                if vertical is not None and vertical > 0.5:
                    keystone_y = keystone_y if keystone_y is not None else max(-8.0, min(8.0, vertical * 0.35))
                    note_parts.append("applied vertical digital keystone compensation")
    if brightness is not None:
        next_brightness = brightness
    if contrast is not None:
        next_contrast = contrast
    return DisplayProfile(
        mode=mode,
        brightness=_clamp_float(next_brightness, 0.55, 1.65, 1.0),
        contrast=_clamp_float(next_contrast, 0.7, 1.6, 1.0),
        scale=_clamp_float(scale, 0.82, 1.08, 1.0),
        keystone_x=_clamp_float(keystone_x, -12.0, 12.0, 0.0),
        keystone_y=_clamp_float(keystone_y, -12.0, 12.0, 0.0),
        ambient_lux=ambient_lux,
        note="; ".join(note_parts) or "manual display profile",
    )


def save_display_profile(path: Path, profile: DisplayProfile) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = profile.as_dict()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _clamp_float(value: object, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(low, min(high, number)), 3)


def _optional_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _css_number(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def find_free_port(host: str, preferred_port: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, preferred_port))
        except OSError:
            probe.bind((host, 0))
        return int(probe.getsockname()[1])
