from __future__ import annotations

from .tingwu_meeting import *  # noqa: F401,F403


class TingwuClientMixin:
    def _write_minutes(self, session: TingwuMeetingSession) -> Path:
        path = self._session_artifact_path(session, "minutes_path", "tingwu_ai_minutes.md")
        minutes = normalize_minutes_payload(session.ai_minutes)
        feature_sections = tingwu_feature_sections(session.ai_minutes)
        lines = [
            f"# {session.title} AI Minutes",
            "",
            "Provider: tongyi_tingwu",
            f"Meeting ID: {session.meeting_id}",
            f"Task ID: {session.task_id}",
            "",
            "## Summary",
            minutes["summary"] or "通义听悟未返回摘要，使用 transcript 进入 OpenClaw 后处理。",
            "",
            "## Decisions",
            *([f"- {item}" for item in minutes["decisions"]] or ["- 暂无明确决策，需要人工补充。"]),
            "",
            "## Action Items",
            *([f"- {item}" for item in minutes["action_items"]] or ["- 暂无明确待办，需要人工补充。"]),
            "",
            *feature_sections,
            "",
            "## Raw Tingwu Result",
            "```json",
            json.dumps(session.ai_minutes, ensure_ascii=False, indent=2)[:20000],
            "```",
        ]
        atomic_write_text(path, "\n".join(lines) + "\n")
        return path
    def _persist_session(self, session: TingwuMeetingSession) -> None:
        output_dir = self._session_output_dir(session)
        self._session_artifact_path(session, "transcript_path", "transcript.md")
        self._session_artifact_path(session, "audio_path", "audio.wav")
        self._session_artifact_path(session, "minutes_path", "tingwu_ai_minutes.md", allow_empty=True)
        path = output_dir / "session.json"
        atomic_write_text(path, json.dumps(session.as_dict(), ensure_ascii=False, indent=2))
    def _session_output_dir(self, session: TingwuMeetingSession) -> Path:
        workspace = self.workspace.root.resolve()
        default = workspace / "meetings" / safe_filename(session.meeting_id, default="meeting")
        value = str(session.output_dir or "").strip()
        candidate = Path(value).expanduser() if value else default
        if not candidate.is_absolute():
            candidate = default
        candidate = candidate.resolve()
        if not candidate.is_relative_to(default.resolve()):
            candidate = default.resolve()
        session.output_dir = str(candidate)
        return candidate
    def _session_artifact_path(
        self,
        session: TingwuMeetingSession,
        attr: str,
        filename: str,
        *,
        allow_empty: bool = False,
    ) -> Path:
        output_dir = self._session_output_dir(session)
        value = str(getattr(session, attr) or "").strip()
        if allow_empty and not value:
            return output_dir / filename
        candidate = Path(value).expanduser() if value else output_dir / filename
        if not candidate.is_absolute():
            candidate = output_dir / candidate
        candidate = candidate.resolve()
        if not candidate.is_relative_to(output_dir):
            candidate = output_dir / filename
        setattr(session, attr, str(candidate))
        return candidate
    def _thread_alive(self, meeting_id: str) -> bool:
        thread = self._threads.get(meeting_id)
        return thread is not None and thread.is_alive()
    def _recover_interrupted_session(self, session: TingwuMeetingSession) -> None:
        if session.status not in RECOVERABLE_ACTIVE_STATUSES or self._thread_alive(session.meeting_id):
            return
        if self._workspace_meeting_lock_held_elsewhere():
            self._emit(session.meeting_id, "meeting_active_elsewhere", {"status": session.status})
            return
        previous_status = session.status
        session.status = "stopped"
        session.stopped_at = session.stopped_at or utc_now()
        note = f"Recovered from persisted {previous_status} state after provider restart; local capture stream is no longer active."
        if note not in session.error:
            session.error = f"{session.error}\n{note}".strip()
        self._write_transcript(session)
        self._persist_session(session)
        self._emit(session.meeting_id, "meeting_recovered", {"previous_status": previous_status, "status": session.status})
        self.audit.record(
            "tingwu.meeting_recovered",
            target=session.meeting_id,
            details={"previous_status": previous_status, "status": session.status, "transcript_path": session.transcript_path},
        )
    def _load_workspace_active_sessions(self, *, force: bool = False) -> None:
        if self._workspace_sessions_loaded and not force:
            return
        self._workspace_sessions_loaded = True
        meetings_dir = self.workspace.root / "meetings"
        if not meetings_dir.is_dir():
            return
        for path in sorted(meetings_dir.glob("*/session.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            meeting_id = str(data.get("meeting_id") or path.parent.name)
            status = str(data.get("status") or "")
            should_refresh = force and meeting_id in self._sessions and not self._thread_alive(meeting_id)
            if status in RECOVERABLE_ACTIVE_STATUSES and (meeting_id not in self._sessions or should_refresh):
                self._load_session(meeting_id)
            elif should_refresh and status not in RECOVERABLE_ACTIVE_STATUSES:
                self._load_session(meeting_id)
    def _load_session(self, meeting_id: str) -> TingwuMeetingSession | None:
        path = self.workspace.root / "meetings" / safe_filename(meeting_id, default="meeting") / "session.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.audit.record(
                "tingwu.session_load",
                status="error",
                target=str(path),
                details={"meeting_id": meeting_id, "error": redact_sensitive_text(str(exc))[:500]},
            )
            return None
        transcript = [
            TingwuTranscriptItem(
                timestamp=str(item.get("timestamp") or ""),
                speaker=str(item.get("speaker") or "Unknown"),
                text=str(item.get("text") or ""),
                final=bool(item.get("final")),
            )
            for item in data.get("transcript", [])
            if isinstance(item, dict)
        ]
        task_payload = data.get("task_payload") if isinstance(data.get("task_payload"), dict) else {}
        ai_minutes = data.get("ai_minutes") if isinstance(data.get("ai_minutes"), dict) else {}
        tingwu_http_operations = data.get("tingwu_http_operations") if isinstance(data.get("tingwu_http_operations"), list) else []
        session = TingwuMeetingSession(
            meeting_id=str(data.get("meeting_id") or meeting_id),
            title=str(data.get("title") or "Meeting"),
            participants=[str(item) for item in data.get("participants", [])],
            task_id=str(data.get("task_id") or ""),
            status=str(data.get("status") or "completed"),
            created_at=str(data.get("created_at") or ""),
            data_id=str(data.get("data_id") or data.get("task_id") or ""),
            websocket_task_id=str(data.get("websocket_task_id") or ""),
            started_at=data.get("started_at"),
            stopped_at=data.get("stopped_at"),
            transcript=transcript,
            partial_text=str(data.get("partial_text") or ""),
            audio_bytes=int(data.get("audio_bytes") or 0),
            audio_seconds=float(data.get("audio_seconds") or 0.0),
            sample_rate=int(data.get("sample_rate") or self.config.tingwu_sample_rate),
            audio_format=str(data.get("audio_format") or self.config.tingwu_audio_format),
            websocket_audio_frames=int(data.get("websocket_audio_frames") or 0),
            audio_rms=int(data.get("audio_rms") or 0),
            audio_peak=int(data.get("audio_peak") or 0),
            output_dir=str(data.get("output_dir") or path.parent),
            transcript_path=str(data.get("transcript_path") or path.parent / "transcript.md"),
            audio_path=str(data.get("audio_path") or path.parent / "audio.wav"),
            minutes_path=str(data.get("minutes_path") or ""),
            task_payload=sanitize_event_payload(task_payload),
            tingwu_http_operations=sanitize_event_payload(tingwu_http_operations)[-MAX_TINGWU_HTTP_OPERATIONS:],
            ai_minutes=sanitize_event_payload(ai_minutes),
            error=redact_sensitive_text(str(data.get("error") or "")),
        )
        self._session_output_dir(session)
        self._session_artifact_path(session, "transcript_path", "transcript.md")
        self._session_artifact_path(session, "audio_path", "audio.wav")
        self._session_artifact_path(session, "minutes_path", "tingwu_ai_minutes.md", allow_empty=True)
        self._sessions[session.meeting_id] = session
        self._event_queues.setdefault(session.meeting_id, Queue())
        self._recover_interrupted_session(session)
        self._persist_session(session)
        return session
    def _workspace_meeting_lock_path(self) -> Path:
        workspace = self.workspace.root.resolve()
        path = (workspace / "meetings" / TINGWU_WORKSPACE_LOCK_NAME).resolve()
        if not path.parent.is_relative_to(workspace):
            raise TingwuMeetingError("Invalid Tingwu workspace lock path.")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    def _write_workspace_lock_payload(self, fd: int, payload: dict[str, object]) -> None:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        os.fsync(fd)
        fsync_parent_dir(self._workspace_meeting_lock_path())
    def _acquire_workspace_meeting_lock(self, *, title: str = "", meeting_id: str = "") -> bool:
        with self._workspace_lock_guard:
            if self._workspace_lock_fd is not None:
                if meeting_id and self._workspace_lock_meeting_id != meeting_id:
                    return False
                if meeting_id and not self._workspace_lock_meeting_id:
                    self._workspace_lock_meeting_id = meeting_id
                return True
            path = self._workspace_meeting_lock_path()
            fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                return False
            except Exception:
                os.close(fd)
                raise
            payload = {
                "pid": os.getpid(),
                "locked_at": utc_now(),
                "title": redact_sensitive_text(title)[:200],
                "meeting_id": meeting_id,
            }
            self._write_workspace_lock_payload(fd, payload)
            self._workspace_lock_fd = fd
            self._workspace_lock_meeting_id = meeting_id
            return True
    def _assign_workspace_meeting_lock(self, meeting_id: str) -> None:
        if not meeting_id:
            return
        with self._workspace_lock_guard:
            if self._workspace_lock_fd is not None and not self._workspace_lock_meeting_id:
                self._workspace_lock_meeting_id = meeting_id
                try:
                    os.lseek(self._workspace_lock_fd, 0, os.SEEK_SET)
                    raw = os.read(self._workspace_lock_fd, 64 * 1024)
                    payload = json.loads(raw.decode("utf-8") or "{}") if raw else {}
                    if not isinstance(payload, dict):
                        payload = {}
                except Exception:
                    payload = {}
                payload["pid"] = os.getpid()
                payload["meeting_id"] = meeting_id
                payload.setdefault("locked_at", utc_now())
                self._write_workspace_lock_payload(self._workspace_lock_fd, payload)
    def _release_workspace_meeting_lock(self) -> None:
        with self._workspace_lock_guard:
            fd = self._workspace_lock_fd
            if fd is None:
                return
            try:
                self._write_workspace_lock_payload(fd, {"pid": os.getpid(), "released_at": utc_now()})
            except Exception:
                pass
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
                self._workspace_lock_fd = None
                self._workspace_lock_meeting_id = ""
    def _workspace_meeting_lock_held_elsewhere(self) -> bool:
        if self._workspace_lock_fd is not None:
            return False
        path = self._workspace_meeting_lock_path()
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)
    def _post_json(self, url: str, payload: dict[str, Any], *, action: str) -> dict[str, Any]:
        data = json.dumps(remove_none(payload), ensure_ascii=False).encode("utf-8")
        req = request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.tingwu_api_key}",
                "Content-Type": "application/json",
                "user-agent": "openclaw/0.1 tingwu-meeting",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                body = read_limited_response_body(response, max_bytes=MAX_TINGWU_API_BYTES, label=f"{action} response")
        except error.HTTPError as exc:
            detail = redact_sensitive_text(read_limited_response_body(exc, max_bytes=64 * 1024, label=f"{action} error response"))[:2000]
            raise TingwuMeetingError(f"{action} failed: HTTP {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise TingwuMeetingError(redact_sensitive_text(f"{action} failed: {exc}")) from exc
        except TingwuMeetingError:
            raise
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise TingwuMeetingError(f"{action} returned non-JSON response: {redact_sensitive_text(body)[:500]}") from exc
        if not isinstance(parsed, dict):
            raise TingwuMeetingError(f"{action} returned invalid payload.")
        logged_response = sanitize_event_payload(parsed)
        self._record_tingwu_http_operation(
            self._http_operation_meeting_id,
            action=action,
            url=url,
            request_payload=payload,
            response=logged_response,
        )
        return parsed if action == "GetTask" else logged_response
    def _record_tingwu_http_operation(
        self,
        meeting_id: str,
        *,
        action: str,
        url: str,
        request_payload: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        if not meeting_id:
            return
        input_payload = request_payload.get("input") if isinstance(request_payload.get("input"), dict) else {}
        response_status = self._task_status(response)
        logged_response = sanitize_event_payload(response)
        if action != "GetTask":
            response = logged_response
        operation = sanitize_event_payload(
            {
                "timestamp": utc_now(),
                "action": action,
                "endpoint": redact_provider_url(url),
                "model": request_payload.get("model"),
                "request_task": input_payload.get("task"),
                "request_type": input_payload.get("type"),
                "request_data_id": input_payload.get("dataId"),
                "response_data_id": self._extract_data_id(logged_response),
                "response_status": response_status,
            }
        )
        session = self._sessions.get(meeting_id)
        if session is not None:
            session.tingwu_http_operations.append(operation)
            del session.tingwu_http_operations[:-MAX_TINGWU_HTTP_OPERATIONS]
            return
        pending = self._pending_http_operations.setdefault(meeting_id, [])
        pending.append(operation)
        del pending[:-MAX_TINGWU_HTTP_OPERATIONS]
    def _drain_http_operations(self, meeting_id: str) -> list[dict[str, Any]]:
        pending = self._pending_http_operations.pop(meeting_id, [])
        return list(pending[-MAX_TINGWU_HTTP_OPERATIONS:])
    def _extract_data_id(self, payload: dict[str, Any]) -> str:
        candidates: list[Any] = [
            payload.get("data_id"),
            payload.get("dataId"),
            payload.get("task_id"),
            payload.get("TaskId"),
        ]
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        candidates.extend([output.get("dataId"), output.get("data_id"), output.get("task_id"), output.get("TaskId")])
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""
    def _task_status(self, payload: dict[str, Any]) -> str:
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        raw = first_present(
            output,
            ("status", "task_status", "taskStatus", "TaskStatus"),
            default=first_present(payload, ("status", "task_status", "taskStatus", "Status", "TaskStatus")),
        )
        if isinstance(raw, int):
            return {0: "completed", 1: "running", 2: "failed", 3: "transcribing"}.get(raw, str(raw))
        status = str(raw or "").lower()
        if status in {"0", "completed", "succeeded", "success", "finish", "finished"}:
            return "completed"
        if status in {"2", "failed", "error", "canceled", "cancelled"}:
            return "failed"
        return status or "running"
    def _hydrate_minutes_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        hydrated = dict(payload)
        hydrated_output = dict(output)
        artifact_keys = {
            "transcriptionPath",
            "translationsPath",
            "summarizationPath",
            "meetingAssistancePath",
            "autoChaptersPath",
            "mindMapPath",
            "pptExtractionPath",
            "textPolishPath",
            "customPromptPath",
            "keyInformationPath",
            "questionsAnsweringPath",
            "conversationalPath",
        }
        artifact_keys.update(
            str(key)
            for key, value in output.items()
            if str(key).endswith("Path") and isinstance(value, str) and value and value.lower() != "null"
        )
        for key in sorted(artifact_keys):
            url = output.get(key)
            if isinstance(url, str) and url and url.lower() != "null":
                hydrated_output[key] = redact_url_origin(url)
                try:
                    hydrated_output[f"{key}Data"] = sanitize_event_payload(self._fetch_json_url(url))
                except Exception as exc:
                    hydrated_output[f"{key}Error"] = redact_sensitive_text(str(exc))[:1000]
        hydrated["output"] = hydrated_output
        return hydrated
    def _fetch_json_url(self, url: str) -> Any:
        url = validate_tingwu_artifact_url(url, trusted_base_url=self.config.tingwu_http_url)
        try:
            with self._open_validated_artifact_url(url) as response:
                body = read_limited_response_body(response, label="Tingwu artifact response")
        except error.HTTPError as exc:
            detail = redact_sensitive_text(read_limited_response_body(exc, max_bytes=64 * 1024, label="Tingwu artifact error response"))[:1000]
            raise TingwuMeetingError(f"Tingwu artifact fetch failed: HTTP {exc.code} {detail}") from exc
        except error.URLError as exc:
            reason = redact_sensitive_text(str(getattr(exc, "reason", exc)))[:1000]
            raise TingwuMeetingError(f"Tingwu artifact fetch failed: {reason}") from exc
        except TingwuMeetingError:
            raise
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body
    def _open_validated_artifact_url(self, url: str) -> Any:
        current = url
        for _ in range(MAX_TINGWU_ARTIFACT_REDIRECTS + 1):
            validate_tingwu_artifact_url(current, trusted_base_url=self.config.tingwu_http_url)
            req = request.Request(current, headers={"user-agent": "openclaw/0.1 tingwu-meeting"}, method="GET")
            try:
                opener = request.build_opener(NoRedirectHandler)
                return opener.open(req, timeout=30)
            except error.HTTPError as exc:
                if exc.code not in {301, 302, 303, 307, 308}:
                    raise
                location = exc.headers.get("Location", "")
                if not location:
                    raise TingwuMeetingError(f"Tingwu artifact redirect missing Location: {redact_url_origin(current)}") from exc
                current = parse.urljoin(current, location)
        raise TingwuMeetingError(f"Tingwu artifact redirect limit exceeded: {MAX_TINGWU_ARTIFACT_REDIRECTS}")
    def _parse_ws_message(self, message: object) -> dict[str, Any]:
        if isinstance(message, (bytes, bytearray)):
            return {"type": "binary", "bytes": len(message)}
        try:
            parsed = json.loads(str(message))
        except json.JSONDecodeError:
            return {"type": "message", "message": str(message)}
        return parsed if isinstance(parsed, dict) else {"type": "message", "message": parsed}
