from __future__ import annotations

from .tingwu_meeting import *  # noqa: F401,F403


class TingwuSessionMixin:
    def __init__(self, config: OfficeAgentConfig, workspace: Workspace, audit: AuditLogger):
        self.config = config
        self.workspace = workspace
        self.audit = audit
        self._sessions: dict[str, TingwuMeetingSession] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._event_queues: dict[str, Queue[dict[str, Any]]] = {}
        self._pending_http_operations: dict[str, list[dict[str, Any]]] = {}
        self._http_operation_meeting_id = ""
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._workspace_lock_guard = threading.Lock()
        self._workspace_lock_fd: int | None = None
        self._workspace_lock_meeting_id = ""
        self._workspace_sessions_loaded = False
    def status(self) -> dict[str, object]:
        self._load_workspace_active_sessions(force=True)
        configured = bool(self.config.tingwu_api_key and self.config.tingwu_app_id)
        credential_diagnostics = {
            "api_key_kind": self.config.tingwu_api_key_kind or tingwu_credential_kind(self.config.tingwu_api_key),
            "app_id_kind": self.config.tingwu_app_id_kind or tingwu_credential_kind(self.config.tingwu_app_id, role="app_id"),
        }
        mic_probe = self.microphone_probe()
        mic_status = str(mic_probe.get("status") or "")
        if self.config.tingwu_mock:
            provider_status = "available"
            message = "ready (mock)"
        elif not configured:
            provider_status = "needs_config"
            if "aliyun_access_key_id" in credential_diagnostics.values():
                message = "DASHSCOPE_API_KEY must be a Bailian/DashScope API Key, not an Aliyun RAM AccessKey ID."
            elif "legacy_tingwu_appkey" in credential_diagnostics.values():
                message = "TINGWU_APP_ID must be the Bailian Model Studio app App ID, not a legacy Tingwu OpenAPI AppKey."
            else:
                message = "Set TINGWU_API_KEY/DASHSCOPE_API_KEY and TINGWU_APP_ID/TINGWU_MEETING_APP_ID."
        elif mic_status != "available":
            provider_status = "unavailable"
            message = f"Microphone is not ready: {mic_probe.get('message') or mic_status}"
        else:
            provider_status = "available"
            message = "ready"
        return {
            "provider": "tongyi_tingwu",
            "status": provider_status,
            "configured": configured,
            "mock": self.config.tingwu_mock,
            "api_key_configured": bool(self.config.tingwu_api_key),
            "app_id_configured": bool(self.config.tingwu_app_id),
            "credential_diagnostics": credential_diagnostics,
            "http_url": redact_provider_url(self.config.tingwu_http_url),
            "ws_url": redact_provider_url(self.config.tingwu_ws_url),
            "configured_mic_device": self.config.mic_device,
            "mic_device": self.config.mic_device,
            "selected_mic_device": mic_probe.get("selected_device") or self.config.mic_device,
            "mic_status": mic_status,
            "mic_probe": mic_probe,
            "audio_source": "file" if self.config.tingwu_audio_file.strip() else "microphone",
            "sample_rate": self.config.tingwu_sample_rate,
            "audio_format": self.config.tingwu_audio_format,
            "pcm_gain": self.config.tingwu_pcm_gain,
            "audio_file_speed": self.config.tingwu_audio_file_speed if self.config.tingwu_audio_file.strip() else 1.0,
            "transcription_model": self.config.tingwu_transcription_model,
            "analysis_model": self.config.tingwu_analysis_model,
            "language_hints": self.language_hints(),
            "translation_enabled": self.config.tingwu_translation_enabled,
            "translation_target_lang": self.translation_target_langs(),
            "phrase_id_configured": bool(self.config.tingwu_phrase_id.strip()),
            "hot_words_configured": bool(self.hot_words()),
            "audio_channel_mode": self.config.tingwu_audio_channel_mode,
            "capabilities": self.capabilities_status(),
            "active_meeting_id": self.active_meeting_id(),
            "active_count": len([item for item in self._sessions.values() if item.status in ACTIVE_MEETING_STATUSES]),
            "message": message,
        }
    def microphone_probe(self) -> dict[str, object]:
        if self.config.tingwu_audio_file.strip():
            capture_probe = preflight_wav_capture(self.config.tingwu_audio_file, self.config.tingwu_sample_rate)
            status = "available" if capture_probe.get("status") == "available" else "unavailable"
            return {
                "status": status,
                "configured_device": "TINGWU_AUDIO_FILE",
                "selected_device": str(capture_probe.get("selected_device") or self.config.tingwu_audio_file),
                "configured_device_valid": status == "available",
                "message": "ready (audio file)" if status == "available" else capture_probe.get("message", "audio file unavailable"),
                "audio_source": "file",
                "candidates": [],
                "capture_probe": capture_probe,
            }
        if self.config.tingwu_mock:
            return {
                "status": "mock",
                "configured_device": self.config.mic_device,
                "message": "TINGWU_MOCK=1 skips microphone hardware.",
                "candidates": [],
            }
        if self.config.mic_device == "fake-mic":
            return {
                "status": "available",
                "configured_device": self.config.mic_device,
                "selected_device": self.config.mic_device,
                "configured_device_valid": True,
                "message": "fake microphone for protocol tests",
                "candidates": [],
            }
        return probe_arecord_device(self.config.mic_device)
    def validate_microphone_ready(self) -> dict[str, object]:
        probe = self.microphone_probe()
        if str(probe.get("status")) != "available":
            raise TingwuMeetingError(
                f"Microphone is not ready: {probe.get('message') or probe.get('status')}",
                details={"mic_probe": probe},
            )
        selected = str(probe.get("selected_device") or self.config.mic_device).strip()
        if selected.lower() in PLACEHOLDER_CAPTURE_DEVICES:
            raise TingwuMeetingError(
                "Microphone is not ready: selected device is an unresolved ALSA placeholder. "
                "Use OPENCLAW_MIC_DEVICE=auto or a concrete device such as plughw:1,0.",
                details={"mic_probe": probe},
            )
        if self.config.tingwu_audio_file.strip():
            capture_probe = preflight_wav_capture(self.config.tingwu_audio_file, self.config.tingwu_sample_rate)
            probe["audio_source"] = "file"
        else:
            capture_probe = preflight_arecord_capture(
                selected,
                self.config.tingwu_sample_rate,
                duration_seconds=self.config.tingwu_preflight_capture_seconds,
            )
        probe["capture_probe"] = capture_probe
        if str(capture_probe.get("status")) != "available":
            raise TingwuMeetingError(
                f"Microphone capture preflight failed: {capture_probe.get('message') or capture_probe.get('status')}",
                details={"mic_probe": probe, "capture_probe": capture_probe},
            )
        audio_bytes = int(capture_probe.get("audio_bytes") or 0)
        audio_rms = int(capture_probe.get("audio_rms") or 0)
        audio_peak = int(capture_probe.get("audio_peak") or 0)
        if audio_bytes <= 0 or audio_rms <= 0 or audio_peak <= 0:
            raise TingwuMeetingError(
                "Microphone capture preflight failed: selected device produced no audio signal. "
                "Speak near the microphone or choose the correct OPENCLAW_MIC_DEVICE.",
                details={"mic_probe": probe, "capture_probe": capture_probe},
            )
        return probe
    def selected_mic_device(self, session: TingwuMeetingSession) -> str:
        probe = session.task_payload.get("mic_probe") if isinstance(session.task_payload.get("mic_probe"), dict) else {}
        selected = str(probe.get("selected_device") or "").strip()
        return selected or self.config.mic_device
    def language_hints(self) -> list[str]:
        return [item.strip() for item in self.config.tingwu_language_hints.split(",") if item.strip()]
    def translation_target_langs(self) -> list[str]:
        return [item.strip() for item in self.config.tingwu_translation_target_lang.split(",") if item.strip()]
    def hot_words(self) -> list[str]:
        return [item.strip() for item in re.split(r"[,，\n]", self.config.tingwu_hot_words) if item.strip()]
    def capabilities_status(self) -> dict[str, bool]:
        custom_prompt_enabled = bool(self.config.tingwu_custom_prompt_enabled and self.config.tingwu_custom_prompt.strip())
        return {
            "realtime_transcription": True,
            "speaker_diarization": True,
            "translation": bool(self.config.tingwu_translation_enabled and self.translation_target_langs()),
            "phrase_hot_words": bool(self.config.tingwu_phrase_id.strip()),
            "local_hot_words_note": bool(self.hot_words()),
            "key_information": self.config.tingwu_key_information_enabled,
            "actions": self.config.tingwu_actions_enabled,
            "full_summary": self.config.tingwu_full_summary_enabled,
            "conversational_summary": self.config.tingwu_conversational_enabled,
            "questions_answering": self.config.tingwu_questions_answering_enabled,
            "mind_map": self.config.tingwu_mind_map_enabled,
            "ppt_extraction": self.config.tingwu_ppt_extraction_enabled,
            "auto_chapters": self.config.tingwu_auto_chapters_enabled,
            "text_polish": self.config.tingwu_text_polish_enabled,
            "custom_prompt": custom_prompt_enabled,
            "meeting_agent_events": True,
        }
    def task_analysis_parameters(self) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            "model": self.config.tingwu_analysis_model,
            "keyInformationEnabled": self.config.tingwu_key_information_enabled,
            "actionsEnabled": self.config.tingwu_actions_enabled,
            "fullSummaryEnabled": self.config.tingwu_full_summary_enabled,
            "fullSummaryFormat": "markdown",
            "conversationalEnabled": self.config.tingwu_conversational_enabled,
            "questionsAnsweringEnabled": self.config.tingwu_questions_answering_enabled,
            "mindMapEnabled": self.config.tingwu_mind_map_enabled,
            "pptExtractionEnabled": self.config.tingwu_ppt_extraction_enabled,
            "autoChaptersEnabled": self.config.tingwu_auto_chapters_enabled,
            "textPolishEnabled": self.config.tingwu_text_polish_enabled,
            "customPromptEnabled": bool(self.config.tingwu_custom_prompt_enabled and self.config.tingwu_custom_prompt.strip()),
        }
        if self.config.tingwu_mind_map_format.strip():
            parameters["mindMapFormat"] = self.config.tingwu_mind_map_format.strip()
        if self.config.tingwu_auto_chapter_granularity.strip():
            parameters["autoChapterGranularity"] = self.config.tingwu_auto_chapter_granularity.strip()
        if self.config.tingwu_auto_chapter_title_length_level.strip():
            parameters["autoChapterTitleLengthLevel"] = self.config.tingwu_auto_chapter_title_length_level.strip()
        if parameters["customPromptEnabled"]:
            parameters["customPromptModel"] = self.config.tingwu_custom_prompt_model.strip() or "tingwu-turbo"
            parameters["customPromptTransType"] = self.config.tingwu_custom_prompt_trans_type.strip() or "chat"
            parameters["customPromptContent"] = self.config.tingwu_custom_prompt.strip()
        return parameters
    def active_meeting_id(self) -> str | None:
        with self._lock:
            for meeting_id, session in self._sessions.items():
                if session.status in ACTIVE_MEETING_STATUSES:
                    return meeting_id
        return None
    def start_realtime_meeting(self, *, title: str, participants: list[str], max_seconds: int = 7200, audio_file: str = "") -> dict[str, object]:
        with self._start_lock:
            self._load_workspace_active_sessions(force=True)
            if self.active_meeting_id():
                raise TingwuMeetingError("Another realtime meeting is already running.")
            if self._workspace_lock_fd is not None:
                raise TingwuMeetingError("Another realtime meeting is already running.")
            if not self._acquire_workspace_meeting_lock(title=title):
                self._load_workspace_active_sessions(force=True)
                active = self.active_meeting_id()
                detail = f": {active}" if active else ""
                raise TingwuMeetingError(f"Another realtime meeting is already running{detail}.")
            try:
                return self._start_realtime_meeting_locked(title=title, participants=participants, max_seconds=max_seconds, audio_file=audio_file)
            except Exception:
                self._release_workspace_meeting_lock()
                raise
    def _start_realtime_meeting_locked(self, *, title: str, participants: list[str], max_seconds: int = 7200, audio_file: str = "") -> dict[str, object]:
        self._load_workspace_active_sessions(force=True)
        if not title.strip():
            raise TingwuMeetingError("Meeting title is required.")
        if not self.config.tingwu_api_key and not self.config.tingwu_mock:
            raise TingwuMeetingError("TINGWU_API_KEY or DASHSCOPE_API_KEY is not configured.")
        if not self.config.tingwu_app_id and not self.config.tingwu_mock:
            raise TingwuMeetingError("TINGWU_APP_ID or TINGWU_MEETING_APP_ID is not configured.")
        if self.active_meeting_id():
            raise TingwuMeetingError("Another realtime meeting is already running.")
        source_audio = str(audio_file or self.config.tingwu_audio_file).strip()
        mic_probe = preflight_wav_capture(source_audio, self.config.tingwu_sample_rate) if source_audio else (self.validate_microphone_ready() if not self.config.tingwu_mock else self.microphone_probe())
        if source_audio and str(mic_probe.get("status")) != "available":
            raise TingwuMeetingError(str(mic_probe.get("message") or "Imported audio is not a valid mono PCM WAV file."))

        stored_title = redact_sensitive_text(title)[:240] or "Tingwu Meeting"
        stored_participants = [redact_sensitive_text(item)[:120] for item in participants]
        meeting_id = f"tingwu_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(8)}"
        output_dir = (self.workspace.root / "meetings" / meeting_id).resolve()
        if not output_dir.is_relative_to(self.workspace.root.resolve()):
            raise TingwuMeetingError("Invalid meeting output directory.")
        output_dir.mkdir(parents=True, exist_ok=True)

        previous_http_meeting_id = self._http_operation_meeting_id
        self._http_operation_meeting_id = meeting_id
        try:
            task = sanitize_event_payload(self.create_task(title=title, participants=participants))
        finally:
            self._http_operation_meeting_id = previous_http_meeting_id
        task["mic_probe"] = mic_probe
        task["pcm_gain"] = self.config.tingwu_pcm_gain
        if source_audio:
            task["audio_source_path"] = source_audio
            task["audio_file_speed"] = self.config.tingwu_audio_file_speed
        now = utc_now()
        data_id = self._extract_data_id(task)
        session = TingwuMeetingSession(
            meeting_id=meeting_id,
            title=stored_title,
            participants=stored_participants,
            task_id=data_id or meeting_id,
            status="running",
            created_at=now,
            data_id=data_id,
            sample_rate=self.config.tingwu_sample_rate,
            audio_format=self.config.tingwu_audio_format,
            output_dir=str(output_dir),
            transcript_path=str(output_dir / "transcript.md"),
            audio_path=str(output_dir / "audio.wav"),
            task_payload=task,
            tingwu_http_operations=self._drain_http_operations(meeting_id),
        )
        self._assign_workspace_meeting_lock(meeting_id)
        with self._lock:
            self._sessions[meeting_id] = session
            self._stop_events[meeting_id] = threading.Event()
            self._event_queues[meeting_id] = Queue()

        thread = threading.Thread(
            target=self._run_session,
            args=(meeting_id, max_seconds),
            name=f"tingwu-meeting-{meeting_id}",
            daemon=True,
        )
        with self._lock:
            self._threads[meeting_id] = thread
        thread.start()
        self.audit.record(
            "tingwu.meeting_start",
            target=meeting_id,
            details={
                "title": stored_title,
                "task_id": session.task_id,
                "participants": stored_participants,
                "mock": self.config.tingwu_mock,
                "mic_probe": mic_probe,
                "pcm_gain": self.config.tingwu_pcm_gain,
                "audio_file_speed": self.config.tingwu_audio_file_speed if self.config.tingwu_audio_file.strip() else 1.0,
            },
        )
        self._persist_session(session)
        return self.session_status(meeting_id)
    def stop_realtime_meeting(self, meeting_id: str | None = None, *, wait_seconds: float = 8) -> dict[str, object]:
        meeting_id = meeting_id or self.active_meeting_id()
        if not meeting_id:
            raise TingwuMeetingError("No realtime meeting is running.")
        session = self._sessions.get(meeting_id) or self._load_session(meeting_id)
        if session is None:
            raise TingwuMeetingError(f"Meeting not found: {meeting_id}")
        if session.status in {"stopped", "completed", "failed", "finalizing"}:
            return self.session_status(meeting_id)
        event = self._stop_events.get(meeting_id)
        if event is None:
            self._recover_interrupted_session(session)
            if session.status == "stopped":
                self._release_workspace_meeting_lock()
            return self.session_status(meeting_id)
        session.status = "stopping"
        event.set()
        thread = self._threads.get(meeting_id)
        if thread is not None:
            thread.join(timeout=wait_seconds)
        if thread is not None and thread.is_alive():
            if session.status in {"starting", "running", "stopping"}:
                session.status = "stopping"
                self._persist_session(session)
            return self.session_status(meeting_id)
        if session.status in {"running", "stopping", "starting"}:
            session.status = "stopped"
            session.stopped_at = session.stopped_at or utc_now()
            self._write_transcript(session)
            self._persist_session(session)
        if session.status in {"stopped", "failed", "completed"}:
            self._release_workspace_meeting_lock()
        return self.session_status(meeting_id)
    def session_status(self, meeting_id: str | None = None) -> dict[str, object]:
        if meeting_id is None:
            self._load_workspace_active_sessions(force=True)
        meeting_id = meeting_id or self.active_meeting_id()
        if not meeting_id:
            return {"status": "idle", "provider": "tongyi_tingwu", "active_meeting_id": None}
        session = self._sessions.get(meeting_id)
        if session is None:
            loaded = self._load_session(meeting_id)
            if loaded is None:
                raise TingwuMeetingError(f"Meeting not found: {meeting_id}")
            session = loaded
        payload = session.as_dict()
        if payload.get("status") == "stopped" and self._thread_alive(meeting_id):
            payload["status"] = "stopping"
        return {
            **payload,
            "provider": "tongyi_tingwu",
            "realtime_transcript": self.transcript_text(session),
            "final_count": len([item for item in session.transcript if item.final]),
        }
    def revise_transcript(
        self,
        meeting_id: str,
        *,
        index: int | None = None,
        text: str = "",
        speaker: str = "",
        rename_speaker_from: str = "",
    ) -> dict[str, object]:
        session = self._sessions.get(meeting_id) or self._load_session(meeting_id)
        if session is None:
            raise TingwuMeetingError(f"Meeting not found: {meeting_id}")
        if session.status in ACTIVE_MEETING_STATUSES:
            raise TingwuMeetingError("Stop the meeting before revising its transcript.")
        revisions_dir = self._session_output_dir(session) / "transcript_revisions"
        revisions_dir.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "saved_at": utc_now(),
            "transcript": [item.__dict__ for item in session.transcript],
        }
        snapshot_path = revisions_dir / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        atomic_write_text(snapshot_path, json.dumps(snapshot, ensure_ascii=False, indent=2))
        changed = 0
        if rename_speaker_from:
            replacement = speaker.strip()[:80]
            if not replacement:
                raise TingwuMeetingError("Replacement speaker name is empty.")
            for item in session.transcript:
                if item.speaker == rename_speaker_from:
                    item.speaker = replacement
                    changed += 1
        elif index is not None:
            if index < 0 or index >= len(session.transcript):
                raise TingwuMeetingError("Transcript item index is out of range.")
            item = session.transcript[index]
            clean_text = text.strip()
            clean_speaker = speaker.strip()[:80]
            if not clean_text:
                raise TingwuMeetingError("Transcript text cannot be empty.")
            item.text = clean_text[:4000]
            if clean_speaker:
                item.speaker = clean_speaker
            changed = 1
        else:
            raise TingwuMeetingError("No transcript revision was supplied.")
        self._write_transcript(session)
        self._persist_session(session)
        return {**self.session_status(meeting_id), "revision_path": str(snapshot_path), "changed": changed}
    def drain_events(self, meeting_id: str, limit: int = 100) -> list[dict[str, object]]:
        queue = self._event_queues.get(meeting_id)
        if queue is None:
            return []
        events: list[dict[str, object]] = []
        for _ in range(max(1, limit)):
            try:
                events.append(queue.get_nowait())
            except Empty:
                break
        return events
    def create_task(self, *, title: str, participants: list[str]) -> dict[str, Any]:
        if self.config.tingwu_mock:
            mock_task_id = f"mock_{secrets.token_hex(8)}"
            result = {
                "status": "mock",
                "task_id": mock_task_id,
                "data_id": mock_task_id,
                "websocket_url": "mock://tingwu/realtime",
                "title": title,
                "participants": participants,
            }
            self._record_tingwu_http_operation(
                self._http_operation_meeting_id,
                action="CreateTask",
                url=self.config.tingwu_http_url,
                request_payload={
                    "model": "tingwu-meeting",
                    "input": {
                        "task": "createTask",
                        "type": "realtime",
                        "format": self.config.tingwu_audio_format,
                        "sampleRate": self.config.tingwu_sample_rate,
                    },
                },
                response=result,
            )
            return result
        transcription_parameters = remove_none(
            {
                "model": self.config.tingwu_transcription_model,
                "languageHints": self.language_hints() or None,
                "diarizationEnabled": True,
                "diarizationSpeakerCount": 0,
                "translationEnabled": bool(self.config.tingwu_translation_enabled and self.translation_target_langs()),
                "translationTargetLang": self.translation_target_langs() or None,
                "phraseId": self.config.tingwu_phrase_id.strip() or None,
            }
        )
        payload = {
            "model": "tingwu-meeting",
            "input": {
                "task": "createTask",
                "appId": self.config.tingwu_app_id,
                "type": "realtime",
                "format": self.config.tingwu_audio_format,
                "sampleRate": self.config.tingwu_sample_rate,
            },
            "parameters": {
                "transcription": transcription_parameters,
                "audio": {"audioChannelMode": self.config.tingwu_audio_channel_mode.strip()},
                "analysis": self.task_analysis_parameters(),
            },
        }
        result = self._post_json(self.config.tingwu_http_url, payload, action="CreateTask")
        data_id = self._extract_data_id(result)
        if not data_id:
            safe_result = json.dumps(sanitize_event_payload(result), ensure_ascii=False)
            raise TingwuMeetingError(f"CreateTask did not return dataId: {safe_result[:1000]}")
        return result
    def get_task(self, task_id: str) -> dict[str, Any]:
        if self.config.tingwu_mock:
            result = {
                "status": "completed",
                "task_id": task_id,
                "output": {
                    "dataId": task_id,
                    "summarizationPathData": {
                        "Summarization": {
                            "ParagraphSummary": "这是通义听悟 mock 会议纪要。",
                        },
                    },
                    "meetingAssistancePathData": {
                        "MeetingAssistance": {
                            "KeySentences": [{"sentence": "确认 LeLamp 会议模块接入通义听悟。"}],
                            "Actions": [{"task": "继续验证实时转写、停止会议和会后纪要保存。"}],
                        },
                    },
                },
            }
            self._record_tingwu_http_operation(
                self._http_operation_meeting_id,
                action="GetTask",
                url=self.config.tingwu_http_url,
                request_payload={"model": "tingwu-meeting", "input": {"task": "getTask", "dataId": task_id}},
                response=result,
            )
            return result
        payload = {
            "model": "tingwu-meeting",
            "input": {"task": "getTask", "dataId": task_id},
        }
        return self._post_json(self.config.tingwu_http_url, payload, action="GetTask")
    def create_minutes_task(self, task_id: str) -> dict[str, Any]:
        if self.config.tingwu_mock:
            mock_minutes_id = f"mock_minutes_{secrets.token_hex(8)}"
            result = {
                "status": "completed",
                "task_id": mock_minutes_id,
                "data_id": mock_minutes_id,
                "source_data_id": task_id,
            }
            self._record_tingwu_http_operation(
                self._http_operation_meeting_id,
                action="CreateRealtimeMinutesTask",
                url=self.config.tingwu_http_url,
                request_payload={"model": "tingwu-meeting", "input": {"task": "createTask", "type": "realtime", "dataId": task_id}},
                response=result,
            )
            return result
        payload = {
            "model": "tingwu-meeting",
            "input": {
                "task": "createTask",
                "appId": self.config.tingwu_app_id,
                "type": "realtime",
                "dataId": task_id,
            },
            "parameters": {
                "analysis": self.task_analysis_parameters(),
            },
        }
        return self._post_json(self.config.tingwu_http_url, payload, action="CreateRealtimeMinutesTask")
    def finalize_meeting(self, meeting_id: str, *, retry_failed_minutes: bool = False) -> dict[str, object]:
        session = self._sessions.get(meeting_id)
        if session is None:
            session = self._load_session(meeting_id)
            if session is None:
                raise TingwuMeetingError(f"Meeting not found: {meeting_id}")
        if session.status in {"starting", "running", "stopping"}:
            if self._thread_alive(session.meeting_id):
                raise TingwuMeetingError("Realtime meeting is still running. Stop capture before fetching Tingwu AI minutes.")
            self._recover_interrupted_session(session)
            if session.status in {"starting", "running", "stopping"}:
                raise TingwuMeetingError("Realtime meeting is still running or owned by another process.")
        should_fetch_minutes = session.status not in {"completed", "failed"} or (
            retry_failed_minutes
            and session.status == "failed"
            and (not session.ai_minutes or not self._minutes_completed(session.ai_minutes))
        )
        if should_fetch_minutes:
            if not self._acquire_workspace_meeting_lock(title=session.title, meeting_id=session.meeting_id):
                raise TingwuMeetingError("Another realtime meeting is already running.")
            try:
                session.status = "finalizing"
                self._persist_session(session)
                previous_http_meeting_id = self._http_operation_meeting_id
                self._http_operation_meeting_id = session.meeting_id
                try:
                    session.ai_minutes = sanitize_event_payload(self.fetch_ai_minutes(session.task_id))
                finally:
                    self._http_operation_meeting_id = previous_http_meeting_id
                session.tingwu_http_operations.extend(self._drain_http_operations(session.meeting_id))
                del session.tingwu_http_operations[:-MAX_TINGWU_HTTP_OPERATIONS]
                session.minutes_path = str(self._write_minutes(session))
                if not self._minutes_completed(session.ai_minutes):
                    safe_minutes = json.dumps(sanitize_event_payload(session.ai_minutes), ensure_ascii=False)
                    raise TingwuMeetingError(f"Tingwu AI minutes did not complete: {safe_minutes[:1000]}")
                session.status = "completed"
                session.error = ""
                session.stopped_at = session.stopped_at or utc_now()
            except Exception as exc:
                session.status = "failed"
                session.error = redact_sensitive_text(str(exc))
                session.tingwu_http_operations.extend(self._drain_http_operations(session.meeting_id))
                del session.tingwu_http_operations[:-MAX_TINGWU_HTTP_OPERATIONS]
                if not session.minutes_path and session.ai_minutes:
                    session.minutes_path = str(self._write_minutes(session))
        self._persist_session(session)
        self.audit.record(
            "tingwu.meeting_finalize",
            status="ok" if session.status == "completed" else "error",
            target=meeting_id,
            details={"task_id": session.task_id, "status": session.status, "minutes_path": session.minutes_path, "error": session.error},
        )
        if session.status in {"completed", "failed"}:
            self._release_workspace_meeting_lock()
        return self.session_status(meeting_id)
    def _minutes_completed(self, payload: dict[str, Any]) -> bool:
        status = str(payload.get("status") or "").lower()
        if status in {"completed", "succeeded", "success", "finish", "finished"}:
            return True
        if status in {"timeout", "failed", "error", "canceled", "cancelled"}:
            return False
        return self._task_status(payload) == "completed"
    def fetch_ai_minutes(self, task_id: str, *, timeout_seconds: int = 60, interval_seconds: float = 2.0) -> dict[str, Any]:
        if self.config.tingwu_mock:
            create_result = self.create_minutes_task(task_id)
            minutes_task_id = self._extract_data_id(create_result) or task_id
            result = self.get_task(minutes_task_id)
            return {
                **self._hydrate_minutes_payload(result),
                "status": "completed",
                "source_data_id": task_id,
                "minutes_task_id": minutes_task_id,
                "create_task": create_result,
            }
        create_result = self.create_minutes_task(task_id)
        minutes_task_id = self._extract_data_id(create_result) or task_id
        deadline = time.monotonic() + max(1, timeout_seconds)
        last: dict[str, Any] = create_result
        while time.monotonic() < deadline:
            last = self.get_task(minutes_task_id)
            status = self._task_status(last)
            if status in {"completed", "succeeded", "success", "finish", "finished"}:
                return {
                    **self._hydrate_minutes_payload(last),
                    "source_data_id": task_id,
                    "minutes_task_id": minutes_task_id,
                    "create_task": create_result,
                }
            if status in {"failed", "error", "canceled", "cancelled"}:
                raise TingwuMeetingError(json.dumps(sanitize_event_payload(last), ensure_ascii=False)[:1000])
            time.sleep(interval_seconds)
        return {
            "status": "timeout",
            "source_data_id": task_id,
            "minutes_task_id": minutes_task_id,
            "create_task": create_result,
            "last": self._hydrate_minutes_payload(last),
        }
    def transcript_text(self, session: TingwuMeetingSession) -> str:
        lines = []
        for item in session.transcript:
            text = item.text.strip()
            if text:
                lines.append(f"{item.speaker}: {text}")
        if session.partial_text.strip():
            lines.append(f"Unknown: {session.partial_text.strip()}")
        return "\n".join(lines)
