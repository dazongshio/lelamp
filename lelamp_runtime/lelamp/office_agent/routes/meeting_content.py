from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.parse
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from ..meeting_voice_skill import parse_meeting_voice_command
from ..tingwu_meeting import TingwuMeetingError, feature_markdown, first_feature_value, preflight_arecord_capture, redact_sensitive_text, sanitize_event_payload
from ..utils import safe_filename
from ._base import ApiError, NOT_HANDLED, RequestContext, exact_payload


def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)


def atomic_write_json(*a, **kw): return _helper("atomic_write_json")(*a, **kw)
def atomic_write_text_file(*a, **kw): return _helper("atomic_write_text_file")(*a, **kw)
def capture_probe_matches_selected_microphone(*a, **kw): return _helper("capture_probe_matches_selected_microphone")(*a, **kw)
def dedupe_events(*a, **kw): return _helper("dedupe_events")(*a, **kw)
def endpoint_matches(*a, **kw): return _helper("endpoint_matches")(*a, **kw)
def is_real_tingwu_microphone(*a, **kw): return _helper("is_real_tingwu_microphone")(*a, **kw)
def list_string(*a, **kw): return _helper("list_string")(*a, **kw)
def normalize_task_status(*a, **kw): return _helper("normalize_task_status")(*a, **kw)
def now_iso(*a, **kw): return _helper("now_iso")(*a, **kw)
def parse_datetime(*a, **kw): return _helper("parse_datetime")(*a, **kw)
def require_file_path(*a, **kw): return _helper("require_file_path")(*a, **kw)
def require_string(*a, **kw): return _helper("require_string")(*a, **kw)
def safe_int(*a, **kw): return _helper("safe_int")(*a, **kw)
def sanitize_id(*a, **kw): return _helper("sanitize_id")(*a, **kw)
def status_to_audit(*a, **kw): return _helper("status_to_audit")(*a, **kw)
def tingwu_provider_acceptance_checklist(*a, **kw): return _helper("tingwu_provider_acceptance_checklist")(*a, **kw)
def tingwu_provider_preflight_next_actions(*a, **kw): return _helper("tingwu_provider_preflight_next_actions")(*a, **kw)



class MeetingContentRoutesMixin:
    def api_meeting_realtime_export(self, meeting_id: str, export_format: str, ctx: RequestContext) -> Path:
        meeting_id = str(meeting_id or "").strip()
        export_format = str(export_format or "txt").lower().strip()
        if export_format not in {"txt", "srt", "vtt"}:
            raise ApiError("unsupported_export_format", "仅支持 TXT、SRT 和 VTT。", status=400)
        try:
            session = self.tingwu.session_status(meeting_id)
        except TingwuMeetingError as exc:
            raise ApiError("not_found", str(exc), status=404) from exc
        transcript = session.get("transcript") if isinstance(session.get("transcript"), list) else []
        if not transcript:
            raise ApiError("empty_transcript", "会议没有可导出的逐字稿。", status=409)
        started_at = parse_datetime(str(session.get("started_at") or session.get("created_at") or ""))

        def offset(item: dict[str, object], fallback: float) -> float:
            item_time = parse_datetime(str(item.get("timestamp") or ""))
            return max(0.0, (item_time - started_at).total_seconds()) if item_time and started_at else fallback

        def caption_time(seconds: float, *, separator: str) -> str:
            millis = max(0, int(round(seconds * 1000)))
            hours, millis = divmod(millis, 3_600_000)
            minutes, millis = divmod(millis, 60_000)
            secs, millis = divmod(millis, 1000)
            return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"

        lines: list[str] = []
        if export_format == "vtt":
            lines.extend(["WEBVTT", ""])
        for index, raw_item in enumerate(transcript):
            item = raw_item if isinstance(raw_item, dict) else {}
            speaker = str(item.get("speaker") or "发言人")
            text = str(item.get("text") or "").strip()
            start = offset(item, index * 3.0)
            next_item = transcript[index + 1] if index + 1 < len(transcript) and isinstance(transcript[index + 1], dict) else {}
            end = max(start + 1.0, min(start + 8.0, offset(next_item, start + 3.0)))
            if export_format == "txt":
                lines.append(f"[{caption_time(start, separator='.')[0:8]}] {speaker}: {text}")
            else:
                if export_format == "srt":
                    lines.append(str(index + 1))
                separator = "," if export_format == "srt" else "."
                lines.extend([f"{caption_time(start, separator=separator)} --> {caption_time(end, separator=separator)}", f"{speaker}: {text}", ""])
        export_root = self.meeting_highlights_path(meeting_id).parent / "exports"
        export_root.mkdir(parents=True, exist_ok=True)
        path = export_root / f"transcript.{export_format}"
        atomic_write_text_file(path, "\n".join(lines).rstrip() + "\n")
        self.record_audit("meeting_realtime.export", "ok", meeting_id, {"format": export_format, "turn_count": len(transcript)}, ctx)
        return path
    def api_meeting_realtime_insights(self, meeting_id: str, ctx: RequestContext) -> dict[str, object]:
        if not meeting_id:
            raise ApiError("missing_meeting_id", "Missing meeting_id.", status=400)
        try:
            session = self.tingwu.session_status(meeting_id)
        except TingwuMeetingError as exc:
            raise ApiError("not_found", str(exc), status=404) from exc
        ai_minutes = session.get("ai_minutes") if isinstance(session.get("ai_minutes"), dict) else {}
        chapters = first_feature_value(ai_minutes, ("AutoChapters", "autoChapters", "Chapters", "chapters"))
        key_information = first_feature_value(
            ai_minutes,
            ("KeyInformation", "keyInformation", "KeyInformations", "keyInformations", "KeySentences", "keySentences"),
        )
        path = self.meeting_highlights_path(meeting_id)
        try:
            stored = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        except (OSError, json.JSONDecodeError):
            stored = []
        highlights = stored if isinstance(stored, list) else []
        result = {
            "status": "completed",
            "meeting_id": meeting_id,
            "chapters": sanitize_event_payload(chapters),
            "chapters_markdown": feature_markdown(chapters),
            "key_information": sanitize_event_payload(key_information),
            "key_information_markdown": feature_markdown(key_information),
            "highlights": highlights,
        }
        self.record_audit("meeting_realtime.insights", "ok", meeting_id, {"highlight_count": len(highlights)}, ctx)
        return result
    def api_meeting_realtime_highlight(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        meeting_id = require_string(payload, "meeting_id").strip()
        index = safe_int(payload.get("index"), -1)
        try:
            session = self.tingwu.session_status(meeting_id)
        except TingwuMeetingError as exc:
            raise ApiError("not_found", str(exc), status=404) from exc
        transcript = session.get("transcript") if isinstance(session.get("transcript"), list) else []
        if index < 0 or index >= len(transcript):
            raise ApiError("invalid_transcript_index", "逐字稿位置无效。", status=400)
        path = self.meeting_highlights_path(meeting_id)
        try:
            current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        except (OSError, json.JSONDecodeError):
            current = []
        highlights = current if isinstance(current, list) else []
        existing = next((item for item in highlights if isinstance(item, dict) and safe_int(item.get("index"), -1) == index), None)
        if existing:
            highlights = [item for item in highlights if item is not existing]
            highlighted = False
        else:
            item = transcript[index] if isinstance(transcript[index], dict) else {}
            highlights.append({"index": index, "timestamp": item.get("timestamp"), "speaker": item.get("speaker"), "text": item.get("text"), "created_at": now_iso()})
            highlighted = True
        atomic_write_json(path, highlights)
        self.record_audit("meeting_realtime.highlight", "ok", meeting_id, {"index": index, "highlighted": highlighted}, ctx)
        return {"status": "completed", "meeting_id": meeting_id, "highlighted": highlighted, "highlights": highlights}
    def api_meeting_realtime_share_clip(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        meeting_id = require_string(payload, "meeting_id").strip()
        index = safe_int(payload.get("index"), -1)
        expires_hours = max(1, min(24 * 30, safe_int(payload.get("expires_hours"), 24 * 7)))
        try:
            session = self.tingwu.session_status(meeting_id)
        except TingwuMeetingError as exc:
            raise ApiError("not_found", str(exc), status=404) from exc
        transcript = session.get("transcript") if isinstance(session.get("transcript"), list) else []
        if index < 0 or index >= len(transcript):
            raise ApiError("invalid_transcript_index", "逐字稿位置无效。", status=400)
        audio_path = Path(str(session.get("audio_path") or "")).expanduser().resolve()
        if not audio_path.is_file():
            raise ApiError("audio_not_found", "会议音频尚未生成。", status=404)
        started_at = parse_datetime(str(session.get("started_at") or session.get("created_at") or ""))
        item = transcript[index] if isinstance(transcript[index], dict) else {}
        item_time = parse_datetime(str(item.get("timestamp") or ""))
        start_seconds = max(0.0, (item_time - started_at).total_seconds() - 1.0) if item_time and started_at else max(0.0, index * 3.0 - 1.0)
        duration = 12.0
        clip_id = uuid4().hex
        clip_root = self.meeting_highlights_path(meeting_id).parent / "shared_clips"
        clip_root.mkdir(parents=True, exist_ok=True)
        clip_path = clip_root / f"{clip_id}.mp3"
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ApiError("ffmpeg_unavailable", "设备未安装 ffmpeg。", status=503)
        completed = subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-ss", f"{start_seconds:.3f}", "-i", str(audio_path), "-t", f"{duration:.3f}", "-vn", "-codec:a", "libmp3lame", "-q:a", "4", str(clip_path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0 or not clip_path.is_file():
            raise ApiError("clip_generation_failed", "音频片段生成失败。", status=422, details={"error": completed.stderr[-500:]})
        token_payload = {
            "meeting_id": meeting_id,
            "clip_id": clip_id,
            "exp": int(time.time()) + expires_hours * 3600,
        }
        encoded = base64.urlsafe_b64encode(json.dumps(token_payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
        signature = hmac.new(self.token.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        share_token = f"{encoded}.{signature}"
        base_url = (self.public_console_url(8790) or "").rstrip("/")
        url = f"{base_url}/api/meeting/shared-clip?share={urllib.parse.quote(share_token)}" if base_url else f"/api/meeting/shared-clip?share={urllib.parse.quote(share_token)}"
        result = {"status": "completed", "meeting_id": meeting_id, "index": index, "expires_at": datetime.fromtimestamp(token_payload["exp"], UTC).isoformat(), "url": url}
        self.record_audit("meeting_realtime.share_clip", "ok", meeting_id, {"index": index, "expires_hours": expires_hours, "clip_id": clip_id}, ctx)
        return result
    def api_meeting_shared_clip(self, token: str, ctx: RequestContext) -> Path:
        try:
            encoded, signature = str(token or "").split(".", 1)
            expected = hmac.new(self.token.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            padding = "=" * ((4 - len(encoded) % 4) % 4)
            payload = json.loads(base64.urlsafe_b64decode(f"{encoded}{padding}").decode("utf-8"))
            if int(payload.get("exp") or 0) < int(time.time()):
                raise ValueError("expired")
            meeting_id = str(payload.get("meeting_id") or "")
            clip_id = str(payload.get("clip_id") or "")
            if not re.fullmatch(r"[a-f0-9]{32}", clip_id):
                raise ValueError("clip")
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
            raise ApiError("invalid_share", "分享链接无效或已过期。", status=403) from exc
        path = self.meeting_highlights_path(meeting_id).parent / "shared_clips" / f"{clip_id}.mp3"
        if not path.is_file():
            raise ApiError("clip_not_found", "分享片段不存在。", status=404)
        self.record_audit("meeting_realtime.shared_clip_read", "ok", meeting_id, {"clip_id": clip_id}, ctx)
        return path
    def api_meeting_realtime_fetch_minutes(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        meeting_id = str(payload.get("meeting_id") or "").strip()
        if not meeting_id:
            raise ApiError("missing_meeting_id", "Missing meeting_id.", status=400)
        try:
            current = self.tingwu.session_status(meeting_id)
            if str(current.get("status") or "") in {"starting", "running", "stopping", "finalizing"}:
                self.record_audit(
                    "meeting_realtime_fetch_minutes",
                    "blocked",
                    meeting_id,
                    {"status": current.get("status"), "reason": "meeting_not_stopped"},
                    ctx,
                )
                raise ApiError(
                    "meeting_not_stopped",
                    "Realtime meeting is still running or stopping. Stop the meeting and wait for capture to finish before fetching AI minutes.",
                    status=409,
                    details={"meeting_id": meeting_id, "status": current.get("status")},
                )
            session = self.tingwu.finalize_meeting(meeting_id, retry_failed_minutes=True)
        except TingwuMeetingError as exc:
            raise ApiError("meeting_minutes_fetch_failed", str(exc), status=409) from exc
        result = self.register_tingwu_outputs(session, ctx, run_followup=bool(payload.get("run_followup", True)))
        self.record_audit("meeting_realtime_fetch_minutes", status_to_audit(str(result.get("status"))), meeting_id, {"minutes_path": session.get("minutes_path")}, ctx)
        self.push_assistant_notification(**self.build_tingwu_assistant_notification("fetch_minutes", session, result))
        return result
    def api_meeting_realtime_ask(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        meeting_id = require_string(payload, "meeting_id").strip()
        question = require_string(payload, "question").strip()
        if len(question) > 1000:
            raise ApiError("question_too_long", "问题不能超过 1000 个字符。", status=400)
        try:
            session = self.tingwu.session_status(meeting_id)
        except TingwuMeetingError as exc:
            raise ApiError("not_found", str(exc), status=404) from exc
        transcript = session.get("transcript") if isinstance(session.get("transcript"), list) else []
        turns: list[dict[str, str]] = []
        for index, item in enumerate(transcript[-500:], start=max(1, len(transcript) - 499)):
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                continue
            turns.append({
                "id": f"T{index}",
                "timestamp": str(item.get("timestamp") or ""),
                "speaker": str(item.get("speaker") or "发言人")[:80],
                "text": str(item.get("text") or "").strip()[:4000],
            })
        if not turns:
            raise ApiError("empty_transcript", "当前会议还没有可供问答的逐字稿。", status=409)
        codex = shutil.which(os.getenv("LELAMP_CODEX_BIN", "codex"))
        if not codex:
            raise ApiError("codex_unavailable", "本机未安装 Codex CLI。", status=503)
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "answer": {"type": "string"},
                "citations": {"type": "array", "items": {"type": "string"}},
                "insufficient_evidence": {"type": "boolean"},
            },
            "required": ["answer", "citations", "insufficient_evidence"],
        }
        transcript_text = "\n".join(
            f"[{item['id']}] {item['timestamp']} {item['speaker']}: {item['text']}" for item in turns
        )[:40000]
        prompt = (
            "你是会议记录问答助手。只能依据下面给出的逐字稿回答，不得使用外部知识，"
            "不得执行命令或读取任何文件。证据不足时明确说明。citations 只能填写逐字稿中的 T 编号。\n\n"
            f"问题：{question}\n\n逐字稿：\n{transcript_text}"
        )
        timeout_seconds = max(15, min(120, safe_int(os.getenv("LELAMP_CODEX_QA_TIMEOUT", "60"), 60)))
        try:
            with tempfile.TemporaryDirectory(prefix="lelamp-meeting-qa-") as temp_dir:
                temp_root = Path(temp_dir)
                schema_path = temp_root / "answer.schema.json"
                output_path = temp_root / "answer.json"
                schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
                codex_env = os.environ.copy()
                for inherited_key in ("LD_LIBRARY_PATH", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_WIRE_API"):
                    codex_env.pop(inherited_key, None)
                process = subprocess.Popen(
                    [
                        codex, "exec", "--ephemeral", "--sandbox", "read-only",
                        "--skip-git-repo-check", "--ignore-rules", "--color", "never",
                        "-C", temp_dir, "--output-schema", str(schema_path),
                        "--output-last-message", str(output_path), "-",
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                    env=codex_env,
                )
                try:
                    stdout, stderr = process.communicate(prompt, timeout=timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    raise ApiError("codex_timeout", "本地 Codex 问答超时。", status=504) from exc
                if process.returncode != 0 or not output_path.is_file():
                    detail = (stderr or stdout or "Codex 未返回结果").strip()
                    raise ApiError("codex_failed", "本地 Codex 问答失败。", status=502, details={"error": detail[-1000:]})
                answer_payload = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ApiError("codex_invalid_response", "本地 Codex 返回了无效结果。", status=502) from exc
        valid_ids = {item["id"] for item in turns}
        raw_citation_ids = [str(item).strip().strip("[]") for item in answer_payload.get("citations", [])]
        citation_ids = [item for item in raw_citation_ids if item in valid_ids]
        turn_by_id = {item["id"]: item for item in turns}
        citations = [{**turn_by_id[item_id]} for item_id in dict.fromkeys(citation_ids)]
        result = {
            "status": "completed",
            "provider": "local_codex",
            "meeting_id": meeting_id,
            "question": question,
            "answer": str(answer_payload.get("answer") or "").strip(),
            "insufficient_evidence": bool(answer_payload.get("insufficient_evidence")),
            "citations": citations,
        }
        self.record_audit(
            "meeting_realtime.ask",
            "ok",
            meeting_id,
            {"provider": "local_codex", "question_chars": len(question), "citation_count": len(citations)},
            ctx,
        )
        return result
    def api_meeting_realtime_transcript_update(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        meeting_id = require_string(payload, "meeting_id").strip()
        index_value = payload.get("index")
        index = safe_int(index_value, -1) if index_value is not None else None
        try:
            result = self.tingwu.revise_transcript(
                meeting_id,
                index=index,
                text=str(payload.get("text") or ""),
                speaker=str(payload.get("speaker") or ""),
                rename_speaker_from=str(payload.get("rename_speaker_from") or "").strip(),
            )
        except TingwuMeetingError as exc:
            raise ApiError("transcript_revision_failed", str(exc), status=409) from exc
        self.record_audit(
            "meeting_realtime.transcript_update",
            "ok",
            meeting_id,
            {"index": index, "changed": result.get("changed"), "speaker_rename": bool(payload.get("rename_speaker_from"))},
            ctx,
        )
        return result
    def api_meeting_minutes(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        target, title = self.load_meeting_transcript(payload, ctx, action="meeting_minutes")
        result = self.runtime.meeting.generate_minutes()
        task = self.create_task("会议纪要", "meeting", "completed", {"transcript": target}, result)
        job = self.create_meeting_job(str(result.get("title") or title), str(target), "minutes", "completed", result)
        self.record_audit("meeting_minutes", "ok", target, {"task_id": task["task_id"], "job_id": job["job_id"]}, ctx)
        return {"status": "completed", "task_id": task["task_id"], "job": job, **result}
