from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .audit import AuditLogger
from .utils import dedupe_path, safe_filename
from .workspace import Workspace


TEXT_PREVIEW_SUFFIXES = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".log",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".py",
    ".xml",
}


@dataclass(frozen=True)
class SharedFile:
    name: str
    workspace_name: str
    path: str
    size_bytes: int
    sha256: str
    uploaded_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "workspace_name": self.workspace_name,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "uploaded_at": self.uploaded_at,
        }


class SharedSpaceService:
    """Controlled inbox for office-computer files shared with the Pi runtime."""

    def __init__(self, workspace: Workspace, audit: AuditLogger, *, inbox_name: str = "shared_inbox"):
        self.workspace = workspace
        self.audit = audit
        self.inbox_dir = (workspace.root / safe_filename(inbox_name, default="shared_inbox")).resolve()
        if not self.inbox_dir.is_relative_to(workspace.root.resolve()):
            raise ValueError("Shared inbox must stay inside the OpenClaw workspace.")
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, filename: str, content: bytes, *, source: str = "upload") -> SharedFile:
        clean_name = safe_filename(Path(filename).name, default="upload")
        destination = dedupe_path((self.inbox_dir / clean_name).resolve())
        if not destination.is_relative_to(self.inbox_dir):
            self.audit.record(
                "shared_space.upload",
                status="blocked",
                target=filename,
                details={"reason": "destination outside shared inbox"},
            )
            raise ValueError("Invalid shared-space filename.")

        destination.write_bytes(content)
        shared = self.describe_file(destination)
        self.audit.record(
            "shared_space.upload",
            target=shared.workspace_name,
            details={
                "source": source,
                "size_bytes": shared.size_bytes,
                "sha256": shared.sha256,
            },
        )
        return shared

    def put_note(self, title: str, text: str, *, source: str = "note") -> SharedFile:
        filename = safe_filename(title, default="shared_note", suffix=".md")
        return self.put_bytes(filename, text.encode("utf-8"), source=source)

    def list_files(self) -> list[SharedFile]:
        files = [
            self.describe_file(path)
            for path in sorted(
                self.inbox_dir.rglob("*"),
                key=lambda item: item.stat().st_mtime if item.is_file() else 0,
                reverse=True,
            )
            if path.is_file() and not any(part.startswith(".") for part in path.relative_to(self.inbox_dir).parts)
        ]
        self.audit.record("shared_space.list", details={"count": len(files), "inbox_dir": str(self.inbox_dir)})
        return files

    def resolve_shared_file(self, workspace_name: str) -> Path:
        normalized = workspace_name.strip().replace("\\", "/")
        if normalized.startswith("shared_inbox/"):
            normalized = normalized[len("shared_inbox/") :]
        candidate = (self.inbox_dir / normalized).resolve()
        if not candidate.is_file() or not candidate.is_relative_to(self.inbox_dir):
            self.audit.record(
                "shared_space.resolve",
                status="blocked",
                target=workspace_name,
                details={"reason": "outside shared inbox"},
            )
            raise ValueError("Shared file not found or outside shared_inbox.")
        return candidate

    def read_preview(self, workspace_name: str, *, max_bytes: int = 200_000) -> dict[str, object]:
        path = self.resolve_shared_file(workspace_name)
        stat = path.stat()
        if path.suffix.lower() not in TEXT_PREVIEW_SUFFIXES:
            payload = {
                "status": "binary",
                "workspace_name": str(path.relative_to(self.workspace.root.resolve())),
                "name": path.name,
                "size_bytes": stat.st_size,
                "download_only": True,
            }
            self.audit.record("shared_space.preview", target=payload["workspace_name"], details=payload)
            return payload
        raw = path.read_bytes()[:max_bytes]
        text = raw.decode("utf-8", "replace")
        payload = {
            "status": "ok",
            "workspace_name": str(path.relative_to(self.workspace.root.resolve())),
            "name": path.name,
            "size_bytes": stat.st_size,
            "truncated": stat.st_size > max_bytes,
            "text": text,
        }
        self.audit.record(
            "shared_space.preview",
            target=payload["workspace_name"],
            details={"size_bytes": stat.st_size, "truncated": payload["truncated"]},
        )
        return payload

    def describe_file(self, path: Path) -> SharedFile:
        resolved = path.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(self.inbox_dir):
            raise ValueError("Shared-space file is outside the inbox.")
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return SharedFile(
            name=resolved.name,
            workspace_name=str(resolved.relative_to(self.workspace.root.resolve())),
            path=str(resolved),
            size_bytes=resolved.stat().st_size,
            sha256=digest.hexdigest(),
            uploaded_at=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(resolved.stat().st_mtime)),
        )


class SharedSpaceServer:
    def __init__(
        self,
        service: SharedSpaceService,
        audit: AuditLogger,
        *,
        token: str | None = None,
        max_upload_bytes: int = 50 * 1024 * 1024,
    ):
        self.service = service
        self.audit = audit
        self.token = token or secrets.token_urlsafe(18)
        self.max_upload_bytes = max(1, max_upload_bytes)

    def make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/health":
                    self._send_json(
                        {
                            "status": "ok",
                            "token_required": bool(server.token),
                            "workspace_dir": str(server.service.workspace.root),
                            "shared_inbox": str(server.service.inbox_dir),
                        }
                    )
                    return
                if not server._authorized(parsed, self.headers.get("X-OpenClaw-Share-Token")):
                    self._send_html(render_unauthorized_page())
                    server.audit.record("shared_space.request", status="blocked", target=parsed.path)
                    return
                if parsed.path == "/files":
                    self._send_json({"files": [item.as_dict() for item in server.service.list_files()]})
                    return
                if parsed.path not in {"/", "/index.html"}:
                    self.send_error(404)
                    return
                self._send_html(render_page(server.service, server.token))

            def do_POST(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/upload":
                    self.send_error(404)
                    return
                if not server._authorized(parsed, self.headers.get("X-OpenClaw-Share-Token")):
                    self._send_html(render_unauthorized_page(), status=403)
                    server.audit.record("shared_space.upload", status="blocked", details={"reason": "bad token"})
                    return

                length_header = self.headers.get("Content-Length", "0")
                try:
                    content_length = int(length_header)
                except ValueError:
                    content_length = 0
                if content_length <= 0 or content_length > server.max_upload_bytes:
                    self._send_html(render_error_page("Upload rejected", "The upload is empty or too large."), status=413)
                    server.audit.record(
                        "shared_space.upload",
                        status="blocked",
                        details={"reason": "invalid upload size", "content_length": content_length},
                    )
                    return

                content_type = self.headers.get("Content-Type", "")
                body = self.rfile.read(content_length)
                try:
                    result = server._handle_upload(content_type, body)
                except ValueError as exc:
                    self._send_html(render_error_page("Upload rejected", str(exc)), status=400)
                    return
                redirect = f"/?token={urllib.parse.quote(server.token)}&uploaded={len(result)}"
                self.send_response(303)
                self.send_header("Location", redirect)
                self.end_headers()

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def _send_html(self, content: str, *, status: int = 200) -> None:
                encoded = content.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _send_json(self, payload: object, *, status: int = 200) -> None:
                encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        return Handler

    def serve(self, *, host: str, port: int) -> None:
        httpd = ThreadingHTTPServer((host, port), self.make_handler())
        bound_host, bound_port = httpd.server_address[:2]
        url_host = "127.0.0.1" if bound_host in {"0.0.0.0", ""} else bound_host
        local_url = f"http://{url_host}:{bound_port}/"
        self.audit.record(
            "shared_space.start",
            target=local_url,
            details={
                "inbox_dir": str(self.service.inbox_dir),
                "workspace_dir": str(self.service.workspace.root),
                "max_upload_bytes": self.max_upload_bytes,
            },
        )
        print(f"OpenClaw shared space: {local_url}")
        if bound_host == "0.0.0.0":
            print(f"LAN URL: http://<raspberry-pi-ip>:{bound_port}/")
        print(f"Shared inbox: {self.service.inbox_dir}")
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
            self.audit.record("shared_space.stop", target=local_url)

    def _authorized(self, parsed: urllib.parse.ParseResult, header_token: str | None) -> bool:
        if not self.token:
            return True
        params = urllib.parse.parse_qs(parsed.query)
        provided = (header_token or params.get("token", [""])[0]).strip()
        return hmac.compare_digest(provided, self.token)

    def _handle_upload(self, content_type: str, body: bytes) -> list[SharedFile]:
        if "multipart/form-data" not in content_type:
            raise ValueError("Expected multipart/form-data upload.")
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        )
        if not message.is_multipart():
            raise ValueError("Malformed upload body.")

        uploaded: list[SharedFile] = []
        note_title = "shared_note"
        note_text = ""
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            field_name = part.get_param("name", header="content-disposition") or ""
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename and payload:
                uploaded.append(self.service.put_bytes(filename, payload, source="http_upload"))
            elif field_name == "note_title":
                note_title = _decode_text_part(part)
            elif field_name == "note_text":
                note_text = _decode_text_part(part)
        if note_text.strip():
            uploaded.append(self.service.put_note(note_title, note_text.strip(), source="http_note"))
        if not uploaded:
            raise ValueError("No file or note content was provided.")
        return uploaded


def _decode_text_part(part) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, "replace").strip()


def render_page(service: SharedSpaceService, token: str) -> str:
    files = service.list_files()
    rows = "\n".join(render_file_row(item) for item in files) or "<tr><td colspan=\"4\">No shared files yet.</td></tr>"
    action = f"/upload?token={urllib.parse.quote(token)}"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenClaw Shared Space</title>
  <style>
    :root {{
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #101828;
      background: #f4f6f8;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; background: #f4f6f8; }}
    header {{
      padding: 24px 32px;
      border-bottom: 1px solid #d0d5dd;
      background: #ffffff;
    }}
    main {{
      width: min(980px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 42px;
      display: grid;
      gap: 22px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 34px; line-height: 1.1; letter-spacing: 0; }}
    p {{ margin: 0; color: #475467; font-size: 16px; line-height: 1.5; }}
    section {{
      border: 1px solid #d0d5dd;
      border-radius: 8px;
      background: #ffffff;
      padding: 22px;
    }}
    h2 {{ margin: 0 0 16px; font-size: 22px; letter-spacing: 0; }}
    form {{ display: grid; gap: 14px; }}
    label {{ display: grid; gap: 8px; color: #344054; font-weight: 650; }}
    input, textarea, button {{
      width: 100%;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 11px 12px;
      font: inherit;
    }}
    textarea {{ min-height: 130px; resize: vertical; }}
    button {{
      width: fit-content;
      background: #0f766e;
      color: #ffffff;
      border-color: #0f766e;
      font-weight: 750;
      cursor: pointer;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 8px; border-bottom: 1px solid #eaecf0; text-align: left; font-size: 14px; }}
    th {{ color: #475467; font-weight: 760; }}
    code {{ padding: 2px 6px; border-radius: 6px; background: #eef2f6; }}
    .hint {{ margin-top: 8px; font-size: 14px; color: #667085; }}
  </style>
</head>
<body>
  <header>
    <h1>OpenClaw Shared Space</h1>
    <p>Upload office files into the Raspberry Pi workspace inbox. The assistant only reads this controlled shared directory.</p>
  </header>
  <main>
    <section>
      <h2>Upload File</h2>
      <form method="post" action="{html.escape(action)}" enctype="multipart/form-data">
        <label>File
          <input type="file" name="file" multiple>
        </label>
        <label>Quick note title
          <input type="text" name="note_title" placeholder="meeting_note">
        </label>
        <label>Quick note
          <textarea name="note_text" placeholder="Paste text from the office computer here."></textarea>
        </label>
        <button type="submit">Send to shared inbox</button>
      </form>
      <p class="hint">Workspace path: <code>{html.escape(str(service.inbox_dir))}</code></p>
    </section>
    <section>
      <h2>Shared Files</h2>
      <table>
        <thead><tr><th>Name</th><th>Workspace name</th><th>Size</th><th>Updated</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>"""


def render_file_row(item: SharedFile) -> str:
    return (
        "<tr>"
        f"<td>{html.escape(item.name)}</td>"
        f"<td><code>{html.escape(item.workspace_name)}</code></td>"
        f"<td>{item.size_bytes}</td>"
        f"<td>{html.escape(item.uploaded_at)}</td>"
        "</tr>"
    )


def render_unauthorized_page() -> str:
    return render_error_page("Unauthorized", "Open the shared-space URL with the current token from the Pi terminal.")


def render_error_page(title: str, message: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{html.escape(title)}</title></head>
<body><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body>
</html>"""


def find_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return str(probe.getsockname()[0])
    except OSError:
        return None
