from __future__ import annotations

import shutil
import smtplib
import subprocess
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from ..audio_api import OpenAIAudioAPI
from ..dashscope_asr import DashScopeASR
from ..groq_asr import GroqASR
from ..meeting_voice_skill import MeetingVoiceCommand, default_meeting_title
from ..tingwu_meeting import TingwuMeetingError, normalize_minutes_payload, redact_sensitive_text, sanitize_event_payload
from ..utils import safe_filename
from ..routes._base import ApiError, RequestContext

def _helper(name:str):
    from .. import web_console
    return getattr(web_console,name)
def extract_email_subject(*a,**kw): return _helper("extract_email_subject")(*a,**kw)
def list_string(*a,**kw): return _helper("list_string")(*a,**kw)
def safe_int(*a,**kw): return _helper("safe_int")(*a,**kw)
def status_to_audit(*a,**kw): return _helper("status_to_audit")(*a,**kw)
def tingwu_capture_status(*a,**kw): return _helper("tingwu_capture_status")(*a,**kw)


class MeetingAsrMixin:
    def create_tingwu_asr_fallback_transcript(self, session: dict[str, object], ctx: RequestContext) -> dict[str, object]:
        meeting_id = str(session.get("meeting_id") or "")
        audio_path_value = str(session.get("audio_path") or "")
        audio_path = Path(audio_path_value).expanduser().resolve() if audio_path_value else None
        audio_seconds = float(session.get("audio_seconds") or 0)
        if audio_path is None or not audio_path.is_file():
            result = {"status": "failed", "reason": "audio_missing", "audio_path": audio_path_value}
            self.record_audit("meeting_realtime_asr_fallback", "failed", meeting_id, result, ctx)
            return result
        if audio_seconds <= 0 or int(session.get("audio_bytes") or 0) <= 0:
            result = {"status": "failed", "reason": "audio_empty", "audio_path": str(audio_path), "audio_seconds": audio_seconds}
            self.record_audit("meeting_realtime_asr_fallback", "failed", meeting_id, result, ctx)
            return result
        try:
            prepared_audio = self.prepare_audio_for_asr(audio_path, session, ctx)
            transcript_text = self.transcribe_meeting_audio(prepared_audio).strip()
        except Exception as exc:
            result = {"status": "failed", "reason": "asr_error", "audio_path": str(audio_path), "error": str(exc)[:1000]}
            self.record_audit("meeting_realtime_asr_fallback", "error", meeting_id, result, ctx)
            return result
        if not transcript_text or transcript_text.lower() in {"none", "null", "undefined"}:
            result = {"status": "failed", "reason": "asr_empty", "audio_path": str(audio_path), "provider": self.runtime.config.asr_provider}
            self.record_audit("meeting_realtime_asr_fallback", "unavailable", meeting_id, result, ctx)
            return result
        transcript_path = self.write_meeting_output_text(
            str(session.get("output_dir") or ""),
            "asr_fallback_transcript.md",
            self.format_asr_fallback_transcript(str(session.get("title") or "Tingwu Meeting"), transcript_text),
            action="meeting_realtime_asr_fallback",
            meeting_id=meeting_id,
            ctx=ctx,
        )
        result = {
            "status": "completed",
            "provider": self.runtime.config.asr_provider,
            "model": self.asr_model_label(),
            "audio_path": str(audio_path),
            "prepared_audio_path": str(prepared_audio),
            "path": str(transcript_path),
            "workspace_name": self.workspace_relative_path(str(transcript_path)),
            "transcript_chars": len(transcript_text),
            "transcript_text": transcript_text,
            "message": "Realtime provider returned no final speaker turns; generated transcript from saved meeting audio via configured ASR API.",
        }
        self.record_audit(
            "meeting_realtime_asr_fallback",
            "ok",
            meeting_id,
            {"provider": result["provider"], "model": result["model"], "chars": len(transcript_text), "path": result["workspace_name"]},
            ctx,
        )
        return result
    def prepare_audio_for_asr(self, audio_path: Path, session: dict[str, object], ctx: RequestContext) -> Path:
        if audio_path.suffix.lower() == ".wav":
            return audio_path
        if shutil.which("ffmpeg") is None:
            return audio_path
        meeting_dir = self.meeting_output_dir(str(session.get("output_dir") or ""), meeting_id=str(session.get("meeting_id") or ""), ctx=ctx)
        output = (meeting_dir / "asr_audio.wav") if meeting_dir is not None else self.runtime.workspace.path_for_new_file("meeting_asr_audio.wav")
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-vn",
            str(output),
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=60)
        if completed.returncode != 0 or not output.exists() or output.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg audio conversion failed: {completed.stderr[:500]}")
        return output
    def transcribe_meeting_audio(self, audio_path: Path) -> str:
        provider = str(self.runtime.config.asr_provider or "").strip().lower()
        if provider == "dashscope":
            from ..dashscope_asr import DashScopeASR

            return DashScopeASR(
                api_key=self.runtime.config.dashscope_api_key,
                model=self.runtime.config.dashscope_asr_model,
                sample_rate=self.runtime.config.dashscope_asr_sample_rate or self.runtime.config.tingwu_sample_rate,
            ).transcribe(audio_path, language_hints=["zh", "en"])
        if provider == "groq":
            from ..groq_asr import GroqASR

            return GroqASR(api_key=self.runtime.config.groq_api_key).transcribe(audio_path, model=self.runtime.config.asr_model, language="zh")
        if provider == "openai":
            return OpenAIAudioAPI(api_key=self.runtime.config.openai_api_key, base_url=self.runtime.config.openai_base_url).transcribe(
                audio_path,
                model=self.runtime.config.asr_model,
                language="zh",
            )
        raise RuntimeError(f"Unsupported ASR provider: {provider or 'missing'}")
    def asr_model_label(self) -> str:
        provider = str(self.runtime.config.asr_provider or "").strip().lower()
        if provider == "dashscope":
            return self.runtime.config.dashscope_asr_model
        return self.runtime.config.asr_model
    def format_asr_fallback_transcript(self, title: str, transcript_text: str) -> str:
        lines = [f"# {title} ASR Transcript", ""]
        for line in transcript_text.splitlines():
            clean = line.strip()
            if clean:
                lines.append(clean if ":" in clean else f"ASR: {clean}")
        if len(lines) == 2 and transcript_text.strip():
            lines.append(f"ASR: {transcript_text.strip()}")
        return "\n".join(lines) + "\n"
    def collect_existing_workspace_paths(self, payload: object) -> list[Path]:
        paths: list[Path] = []
        seen: set[Path] = set()
        workspace = self.runtime.workspace.root.resolve()
        projection_root = self.runtime.config.projection_dir.resolve()

        def add_path(value: str) -> None:
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = (workspace / candidate).resolve()
            else:
                candidate = candidate.resolve()
            if not candidate.is_file():
                return
            if not (candidate.is_relative_to(workspace) or candidate.is_relative_to(projection_root)):
                return
            if candidate in seen:
                return
            seen.add(candidate)
            paths.append(candidate)

        def visit(value: object, key: str = "") -> None:
            if isinstance(value, str):
                if key == "path" or key.endswith("_path") or key.endswith("Path"):
                    add_path(value)
                return
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    visit(child_value, str(child_key))
                return
            if isinstance(value, list):
                for item in value:
                    visit(item, key)

        visit(payload)
        return paths
    def send_email_draft(self, *, email_path: Path, recipient: str, subject: str) -> dict[str, object]:
        config = self.runtime.config
        if not config.smtp_host or not config.smtp_from:
            return {
                "status": "backend_missing",
                "message": "OPENCLAW_SMTP_HOST and OPENCLAW_SMTP_FROM/OPENCLAW_SMTP_USERNAME are required before sending email.",
                "configured": False,
                "host_configured": bool(config.smtp_host),
                "from_configured": bool(config.smtp_from),
            }
        draft = email_path.read_text(encoding="utf-8", errors="replace")
        parsed_subject = extract_email_subject(draft) or subject
        message = EmailMessage()
        message["From"] = config.smtp_from
        message["To"] = recipient
        message["Subject"] = parsed_subject
        message.set_content(draft)
        try:
            with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
                if config.smtp_tls:
                    smtp.starttls()
                if config.smtp_username:
                    smtp.login(config.smtp_username, config.smtp_password)
                smtp.send_message(message)
        except Exception as exc:
            return {
                "status": "failed",
                "message": str(exc)[:1000],
                "configured": True,
                "host": config.smtp_host,
                "port": config.smtp_port,
                "tls": config.smtp_tls,
            }
        return {
            "status": "completed",
            "configured": True,
            "host": config.smtp_host,
            "port": config.smtp_port,
            "tls": config.smtp_tls,
            "subject": parsed_subject,
        }
    def export_archive_name(self, path: Path) -> str:
        resolved = path.resolve()
        workspace = self.runtime.workspace.root.resolve()
        projection_root = self.runtime.config.projection_dir.resolve()
        if resolved.is_relative_to(workspace):
            return str(resolved.relative_to(workspace))
        if resolved.is_relative_to(projection_root):
            return f"projection/{resolved.relative_to(projection_root)}"
        return safe_filename(resolved.name, default="artifact")
