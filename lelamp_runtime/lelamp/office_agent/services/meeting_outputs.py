from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..llm import LLMError, ResponsesLLM, ResponsesLLMConfig
from ..tingwu_meeting import redact_sensitive_text, sanitize_event_payload
from ..utils import safe_filename
from ..routes._base import ApiError, RequestContext

def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)
def atomic_write_bytes(*a,**kw): return _helper("atomic_write_bytes")(*a,**kw)
def atomic_write_json(*a,**kw): return _helper("atomic_write_json")(*a,**kw)
def atomic_write_text_file(*a,**kw): return _helper("atomic_write_text_file")(*a,**kw)
def compact_meeting_step_output(*a,**kw): return _helper("compact_meeting_step_output")(*a,**kw)
def first_output_path(*a,**kw): return _helper("first_output_path")(*a,**kw)
def meeting_step_result(*a,**kw): return _helper("meeting_step_result")(*a,**kw)
def meeting_step_understanding(*a,**kw): return _helper("meeting_step_understanding")(*a,**kw)
def normalize_task_status(*a,**kw): return _helper("normalize_task_status")(*a,**kw)
def now_iso(*a,**kw): return _helper("now_iso")(*a,**kw)
def safe_float(*a,**kw): return _helper("safe_float")(*a,**kw)
def safe_int(*a,**kw): return _helper("safe_int")(*a,**kw)
def sanitize_id(*a,**kw): return _helper("sanitize_id")(*a,**kw)
def summarize_dict(*a,**kw): return _helper("summarize_dict")(*a,**kw)
def tingwu_realtime_task_summary(*a,**kw): return _helper("tingwu_realtime_task_summary")(*a,**kw)


class MeetingOutputsMixin:
    def create_meeting_job(self, title: str, transcript: str, step_name: str, status: str, result: dict[str, object]) -> dict[str, object]:
        task = self.create_task(
            title=f"会议工作流：{title}",
            task_type="meeting",
            status=status,
            input_payload={"transcript": transcript, "step": step_name, "meeting_title": title},
            output=result,
        )
        return self.meeting_job_from_task(task)
    def write_meeting_items_output(
        self,
        title: str,
        transcript: str,
        step_name: str,
        items: list[str],
        minutes_result: dict[str, object],
        *,
        output_dir: str = "",
        meeting_id: str = "",
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        items_key = "decisions" if step_name == "decisions" else "action_items"
        filename = safe_filename(title, default="meeting", suffix=f"_{items_key}.json")
        payload = {
            "title": title,
            "transcript": transcript,
            "step": step_name,
            "items": items,
            items_key: items,
            "source_minutes_path": minutes_result.get("path") or minutes_result.get("tingwu_minutes_path"),
            "generated_at": now_iso(),
            "provider": "tongyi_tingwu",
            "confirmation_required": False,
        }
        if output_dir:
            output_path = self.write_meeting_output_json(
                output_dir,
                f"{items_key}.json",
                payload,
                action=f"meeting.{step_name}_extract",
                meeting_id=meeting_id,
                ctx=ctx,
            )
        else:
            output_path = self.runtime.workspace.write_json(
                filename,
                payload,
                action=f"meeting.{step_name}_extract",
            )
        status = "completed"
        return {
            "status": status,
            "step": step_name,
            items_key: items,
            "items": items,
            "path": str(output_path),
            "source_minutes_path": minutes_result.get("path") or minutes_result.get("tingwu_minutes_path"),
            "confirmation": {
                "required": False,
                "summary": "内容已生成。",
            },
            "message": "已从实时会议生成步骤输出。",
        }
    def meeting_output_dir(self, output_dir_value: str, *, meeting_id: str = "", ctx: RequestContext | None = None) -> Path | None:
        value = str(output_dir_value or "").strip()
        if not value:
            return None
        output_dir = Path(value).expanduser().resolve()
        workspace = self.runtime.config.workspace_dir.resolve()
        expected_root = (workspace / "meetings" / safe_filename(meeting_id, default="meeting")).resolve() if meeting_id else (workspace / "meetings").resolve()
        if not output_dir.is_relative_to(expected_root):
            if ctx is not None:
                self.record_audit(
                    "meeting_output_write",
                    "blocked",
                    meeting_id or str(output_dir),
                    {"reason": "meeting output directory is outside workspace/meetings/{meeting_id}", "output_dir": str(output_dir)},
                    ctx,
                )
            return None
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir
    def write_meeting_output_text(
        self,
        output_dir_value: str,
        filename: str,
        content: str,
        *,
        action: str,
        meeting_id: str = "",
        ctx: RequestContext | None = None,
    ) -> Path:
        output_dir = self.meeting_output_dir(output_dir_value, meeting_id=meeting_id, ctx=ctx)
        if output_dir is None:
            if str(output_dir_value or "").strip():
                raise ApiError("invalid_meeting_output_dir", "Meeting output directory is outside workspace/meetings/{meeting_id}.", status=403)
            return self.runtime.workspace.write_text(filename, content, action=action)
        path = output_dir / safe_filename(filename, default="artifact")
        atomic_write_text_file(path, content)
        if ctx is not None:
            self.record_audit(action, "ok", str(path), {"meeting_id": meeting_id, "chars": len(content)}, ctx)
        else:
            self.runtime.audit.record(action, target=str(path), details={"meeting_id": meeting_id, "chars": len(content)})
        return path
    def write_meeting_output_json(
        self,
        output_dir_value: str,
        filename: str,
        payload: object,
        *,
        action: str,
        meeting_id: str = "",
        ctx: RequestContext | None = None,
    ) -> Path:
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        return self.write_meeting_output_text(output_dir_value, filename, content, action=action, meeting_id=meeting_id, ctx=ctx)
    def materialize_tingwu_workspace_file(
        self,
        result: dict[str, object],
        *,
        output_dir: str,
        filename: str,
        meeting_id: str,
        ctx: RequestContext,
        source_key: str = "path",
    ) -> dict[str, object]:
        source_value = str(result.get(source_key) or "").strip()
        source = Path(source_value).expanduser().resolve() if source_value else None
        meeting_dir = self.meeting_output_dir(output_dir, meeting_id=meeting_id, ctx=ctx)
        if meeting_dir is None and str(output_dir or "").strip():
            return {**result, "status": "blocked", "error": "Meeting output directory is outside workspace/meetings/{meeting_id}."}
        if source is None or not source.is_file() or meeting_dir is None:
            return result
        target = (meeting_dir / safe_filename(filename, default=source.name or "artifact")).resolve()
        if source == target:
            return result
        try:
            atomic_write_bytes(target, source.read_bytes())
        except OSError:
            return result
        self.record_audit(
            "meeting_output_workspace_copy",
            "ok",
            str(target),
            {"meeting_id": meeting_id, "source": str(source), "key": source_key},
            ctx,
        )
        return {**result, source_key: str(target), f"source_{source_key}": str(source)}
    def materialize_meeting_final_markdown(
        self,
        *,
        title: str,
        meeting_id: str,
        started_at: str,
        followup: dict[str, object],
        minutes_result: dict[str, object],
        ctx: RequestContext,
    ) -> dict[str, object]:
        followup_minutes = followup.get("minutes") if isinstance(followup.get("minutes"), dict) else {}
        source_value = str(followup_minutes.get("path") or minutes_result.get("path") or "").strip()
        source = Path(source_value).expanduser().resolve() if source_value else None
        if source is None or not source.is_file():
            raise RuntimeError("会议最终 Markdown 来源文件不存在。")
        date_text = ""
        match = re.search(r"tingwu_(\d{4})(\d{2})(\d{2})_", meeting_id)
        if match:
            date_text = "-".join(match.groups())
        if not date_text:
            date_text = started_at[:10] if re.match(r"\d{4}-\d{2}-\d{2}", started_at) else datetime.now().strftime("%Y-%m-%d")
        result_dir = (self.runtime.config.workspace_dir.resolve() / "meetings" / "会议记录").resolve()
        if not result_dir.is_relative_to(self.runtime.config.workspace_dir.resolve()):
            raise RuntimeError("会议结果目录不在工作区内。")
        result_dir.mkdir(parents=True, exist_ok=True)
        filename = safe_filename(f"{date_text}_{title}.md", default=f"{date_text}_会议记录.md")
        actor = self.document_actor(ctx)
        existing_document = self.documents_workspace.find_document_by_external_id(
            meeting_id,
            actor=actor,
            source_type="meeting",
        )
        existing_source = str(existing_document.get("source_path") or "") if existing_document else ""
        existing_target = (self.runtime.config.workspace_dir.resolve() / existing_source).resolve() if existing_source else None
        target = (
            existing_target
            if existing_target is not None and existing_target.is_relative_to(self.runtime.config.workspace_dir.resolve())
            else (result_dir / filename).resolve()
        )
        content = source.read_text(encoding="utf-8", errors="replace")
        content = re.sub(r"\A#\s+[^\n]+", f"# {title}", content, count=1)
        atomic_write_text_file(target, content)
        result = {
            "status": "completed",
            "path": str(target),
            "workspace_name": self.workspace_relative_path(str(target)),
            "type": "markdown",
            "message": "会议摘要、逐字稿、决策和行动项已合并为一个可编辑 Markdown 文件。",
        }
        document = self.documents_workspace.import_markdown(
            actor=actor,
            source_path=target,
            title=title,
            source_type="meeting",
            external_id=meeting_id,
            update_existing=True,
        )
        result["document_id"] = str(document.get("id") or "")
        result["document_url"] = f"/documents?document={urllib.parse.quote(str(document.get('id') or ''))}"
        result["message"] = "会议摘要、逐字稿、决策和行动项已合并到一个可编辑的会议文档。"
        self.record_audit("meeting.final_markdown", "ok", str(target), {"meeting_id": meeting_id, "source": str(source)}, ctx)
        return result
    def generate_meeting_content_title(self, session: dict[str, object], *, fallback: str) -> str:
        transcript_items = session.get("transcript") if isinstance(session.get("transcript"), list) else []
        utterances = [
            str(item.get("text") or "").strip()
            for item in transcript_items
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        transcript_text = "\n".join(utterances)[:6000]
        if transcript_text:
            config = self.runtime.config
            try:
                if config.openai_api_key:
                    llm = ResponsesLLM(ResponsesLLMConfig(
                        api_key=config.openai_api_key,
                        base_url=config.openai_base_url,
                        model=config.openai_model,
                        reasoning_effort="low",
                    ))
                elif config.dashscope_api_key:
                    llm = ResponsesLLM(ResponsesLLMConfig(
                        api_key=config.dashscope_api_key,
                        base_url=getattr(config, "dashscope_vision_base_url", "https://dashscope.aliyuncs.com/compatible-mode"),
                        model=getattr(config, "dashscope_text_model", getattr(config, "dashscope_vision_model", "qwen-plus")),
                        reasoning_effort="low",
                        wire_api="chat_completions",
                    ))
                else:
                    raise LLMError("No text LLM configured.")
                generated = llm.complete(
                    instructions="根据会议逐字稿生成一个简洁、具体、便于搜索的中文会议名称。只输出名称，不要引号、日期、标点或解释，长度为6到18个汉字。",
                    user_input=transcript_text,
                    timeout=20,
                )
                cleaned = re.sub(r"[\r\n#“”\"'：:。！？!?]+", "", generated).strip()
                if 4 <= len(cleaned) <= 24:
                    return cleaned
            except Exception:
                pass
        keywords: list[str] = []
        keyword_groups = (
            ("会议功能", ("会议", "转写", "纪要")),
            ("远程控制", ("远程", "操控", "控制")),
            ("投影功能", ("投影", "显示")),
            ("设备功能", ("设备", "台灯")),
            ("产品功能", ("功能", "完成")),
        )
        joined = "".join(utterances)
        for label, terms in keyword_groups:
            if any(term in joined for term in terms) and label not in keywords:
                keywords.append(label)
            if len(keywords) == 2:
                break
        if keywords:
            return "与".join(keywords) + "讨论"
        for utterance in utterances:
            cleaned = re.sub(r"^(嗯+|啊+|那个|然后|就是|我们|今天|现在)+", "", utterance).strip(" ，。！？!?")
            if len(cleaned) >= 4:
                return cleaned[:18]
        generic = fallback.strip()
        return generic if generic and generic not in {"现场会议", "自动命名"} else "会议记录"
    def materialize_tingwu_followup_outputs(
        self,
        followup: dict[str, object],
        *,
        session: dict[str, object],
        ctx: RequestContext,
    ) -> dict[str, object]:
        meeting_id = str(session.get("meeting_id") or "")
        output_dir = str(session.get("output_dir") or "")
        minutes = followup.get("minutes") if isinstance(followup.get("minutes"), dict) else None
        if minutes is not None:
            minutes = self.materialize_tingwu_workspace_file(
                minutes,
                output_dir=output_dir,
                filename="followup_minutes.md",
                meeting_id=meeting_id,
                ctx=ctx,
            )
            followup["minutes"] = minutes
        transcript = followup.get("transcript") if isinstance(followup.get("transcript"), dict) else None
        if transcript is not None:
            transcript = self.materialize_tingwu_workspace_file(
                transcript,
                output_dir=output_dir,
                filename="followup_transcript.json",
                meeting_id=meeting_id,
                ctx=ctx,
            )
            followup["transcript"] = transcript
        email_path = str(followup.get("email_draft_path") or "")
        if email_path:
            copied = self.materialize_tingwu_workspace_file(
                {"path": email_path},
                output_dir=output_dir,
                filename="followup_email.md",
                meeting_id=meeting_id,
                ctx=ctx,
            )
            followup["email_draft_path"] = copied.get("path") or email_path
            if copied.get("source_path") and str(copied.get("source_path")) != str(copied.get("path")):
                followup["source_email_draft_path"] = copied.get("source_path")
        reminders = followup.get("reminders") if isinstance(followup.get("reminders"), dict) else None
        if reminders is not None:
            reminder_path = self.write_meeting_output_json(
                output_dir,
                "reminders.json",
                reminders,
                action="meeting.reminders_snapshot",
                meeting_id=meeting_id,
                ctx=ctx,
            )
            reminders = {**reminders, "store_path": str(reminder_path), "source_store_path": str(reminders.get("store_path") or "")}
            followup["reminders"] = reminders
        return followup
    def latest_projection_mtime(self) -> float:
        projection_dir = self.runtime.config.projection_dir
        if not projection_dir.exists():
            return 0.0
        latest = 0.0
        for path in projection_dir.glob("*.md"):
            try:
                if path.is_file():
                    latest = max(latest, path.stat().st_mtime)
            except OSError:
                continue
        return latest
    def materialize_tingwu_projection_output(
        self,
        projection: dict[str, object],
        *,
        meeting_id: str,
        projection_dir_before: float,
        ctx: RequestContext,
    ) -> dict[str, object]:
        projection_path_value = str(projection.get("path") or "").strip()
        projection_path = Path(projection_path_value).expanduser().resolve() if projection_path_value else None
        if projection_path is None or not projection_path.is_file():
            return projection

        workspace = self.runtime.config.workspace_dir.resolve()
        meeting_root = (workspace / "meetings" / safe_filename(meeting_id, default="meeting")).resolve()
        if projection_path.is_relative_to(meeting_root):
            return projection

        projection_dir = self.runtime.config.projection_dir.resolve()
        if not projection_path.is_relative_to(projection_dir) and not projection_path.is_relative_to(workspace):
            return projection
        try:
            projection_mtime = projection_path.stat().st_mtime
        except OSError:
            return projection
        if projection_path.is_relative_to(projection_dir) and projection_mtime + 0.001 < projection_dir_before:
            return projection

        if not meeting_root.is_relative_to(workspace):
            return projection
        meeting_root.mkdir(parents=True, exist_ok=True)
        workspace_projection = meeting_root / "projection_confirmation.md"
        try:
            atomic_write_text_file(workspace_projection, projection_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return projection
        self.record_audit(
            "meeting_projection_workspace_copy",
            "ok",
            str(workspace_projection),
            {"meeting_id": meeting_id, "source": str(projection_path)},
            ctx,
        )
        return {
            **projection,
            "path": str(workspace_projection),
            "source_projection_path": str(projection_path),
        }
    def write_tingwu_meeting_manifest(
        self,
        *,
        session: dict[str, object],
        minutes: dict[str, object],
        followup: dict[str, object] | None,
        outputs: list[dict[str, object]],
        job: dict[str, object],
        ctx: RequestContext,
    ) -> str:
        output_dir_value = str(session.get("output_dir") or "").strip()
        if not output_dir_value:
            return ""
        output_dir = Path(output_dir_value).expanduser().resolve()
        workspace = self.runtime.config.workspace_dir.resolve()
        meeting_id = str(session.get("meeting_id") or "")
        expected_root = (workspace / "meetings" / safe_filename(meeting_id, default="meeting")).resolve()
        if not output_dir.is_relative_to(expected_root):
            self.record_audit(
                "meeting_manifest",
                "blocked",
                meeting_id or str(output_dir),
                {"reason": "meeting output directory is outside workspace/meetings/{meeting_id}", "output_dir": str(output_dir)},
                ctx,
            )
            return ""

        def normalized_outputs() -> list[dict[str, object]]:
            seen: set[str] = set()
            items: list[dict[str, object]] = []
            skipped: list[str] = []
            for item in outputs:
                if not isinstance(item, dict):
                    continue
                path_value = str(item.get("path") or "")
                if not path_value or path_value in seen:
                    continue
                seen.add(path_value)
                path = Path(path_value).expanduser()
                path_resolved = path.resolve() if path.is_absolute() else (workspace / path_value).resolve()
                if not path_resolved.is_relative_to(workspace):
                    skipped.append(redact_sensitive_text(path_value)[:240])
                    continue
                redacted_path = redact_sensitive_text(str(path_resolved))
                redacted_workspace_path = redact_sensitive_text(str(path_resolved.relative_to(workspace)))
                items.append(
                    sanitize_event_payload({
                        "path": redacted_path,
                        "workspace_path": redacted_workspace_path,
                        "type": str(item.get("type") or path.suffix.lstrip(".") or "file"),
                        "exists": path_resolved.is_file(),
                        "inside_workspace": True,
                        **({"source": str(item.get("source"))} if item.get("source") else {}),
                        **({"step": str(item.get("step"))} if item.get("step") else {}),
                    })
                )
            if skipped:
                self.record_audit(
                    "meeting_manifest.output_skip",
                    "blocked",
                    meeting_id,
                    {"reason": "output path outside workspace", "count": len(skipped), "paths": skipped[:10]},
                    ctx,
                )
            return items

        manifest = sanitize_event_payload({
            "status": minutes.get("status") or session.get("status"),
            "provider": "tongyi_tingwu",
            "provider_status": minutes.get("provider_status") or session.get("status"),
            "openclaw_status": minutes.get("openclaw_status"),
            "content_status": minutes.get("content_status"),
            "provider_error": redact_sensitive_text(str(session.get("error") or minutes.get("provider_error") or ""))[:1000],
            "openclaw_error": redact_sensitive_text(str(minutes.get("error") or ""))[:1000],
            "meeting_id": meeting_id,
            "title": session.get("title") or minutes.get("title"),
            "provider_task_id": session.get("task_id") or minutes.get("provider_task_id"),
            "created_at": session.get("created_at"),
            "started_at": session.get("started_at"),
            "stopped_at": session.get("stopped_at"),
            "audio": {
                "path": session.get("audio_path"),
                "seconds": session.get("audio_seconds"),
                "bytes": session.get("audio_bytes"),
                "sample_rate": session.get("sample_rate"),
                "format": session.get("audio_format"),
                "rms": session.get("audio_rms"),
                "peak": session.get("audio_peak"),
            },
            "transcript_path": session.get("transcript_path"),
            "tingwu_minutes_path": session.get("minutes_path"),
            "openclaw_minutes_path": minutes.get("path"),
            "tingwu_http_operations": session.get("tingwu_http_operations") if isinstance(session.get("tingwu_http_operations"), list) else [],
            "outputs": normalized_outputs(),
            "job": job,
            "followup_status": followup.get("status") if isinstance(followup, dict) else None,
            "generated_at": now_iso(),
        })
        path = output_dir / "manifest.json"
        atomic_write_json(path, manifest)
        self.record_audit("meeting_manifest", "ok", str(path), {"meeting_id": meeting_id, "outputs": len(manifest["outputs"])}, ctx)
        return str(path)
