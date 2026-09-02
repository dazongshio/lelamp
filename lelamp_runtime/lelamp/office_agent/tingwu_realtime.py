from __future__ import annotations

from .tingwu_meeting import *  # noqa: F401,F403


class TingwuRealtimeMixin:
    def _run_session(self, meeting_id: str, max_seconds: int) -> None:
        session = self._sessions[meeting_id]
        self._assign_workspace_meeting_lock(meeting_id)
        session.status = "running"
        session.started_at = utc_now()
        self._emit(meeting_id, "meeting_started", {"task_id": session.task_id})
        self._persist_session(session)
        try:
            if self.config.tingwu_mock:
                self._run_mock_session(meeting_id, max_seconds)
            else:
                self._run_realtime_stream(meeting_id, max_seconds)
        except Exception as exc:
            session.status = "failed"
            session.error = redact_sensitive_text(str(exc))
            self._emit(meeting_id, "meeting_error", {"error": session.error})
            self.audit.record("tingwu.meeting_stream", status="error", target=meeting_id, details={"error": session.error[:1000]})
        finally:
            session.stopped_at = session.stopped_at or utc_now()
            final_status = session.status
            if final_status in {"starting", "running", "stopping"}:
                final_status = "stopped"
            self._write_transcript(session)
            if final_status in {"failed", "completed", "stopped"}:
                self._release_workspace_meeting_lock()
            session.status = final_status
            self._persist_session(session)
            self._emit(meeting_id, "meeting_stopped", {"status": session.status, "transcript_path": session.transcript_path})
    def _run_mock_session(self, meeting_id: str, max_seconds: int) -> None:
        session = self._sessions[meeting_id]
        stop_event = self._stop_events[meeting_id]
        session.websocket_task_id = session.websocket_task_id or f"mock_ws_{secrets.token_hex(4)}"
        self._emit(meeting_id, "websocket_open", {})
        self._emit(meeting_id, "websocket_started", {"websocket_task_id": session.websocket_task_id})
        samples = [
            "决定: 使用通义听悟作为第一版会议引擎。",
            "待办: 验证树莓派麦克风采集和实时转写。",
            "待办: 会后生成纪要、行动项和投影确认卡。",
        ]
        deadline = time.monotonic() + min(max(1, max_seconds), 3)
        index = 0
        session.sample_rate = int(session.sample_rate or self.config.tingwu_sample_rate)
        session.audio_format = session.audio_format or self.config.tingwu_audio_format
        frame = synthetic_pcm_frame(session.sample_rate)
        with StreamingWavWriter(session.audio_path, sample_rate=session.sample_rate) as audio_writer:
            while time.monotonic() < deadline and (index < len(samples) or not stop_event.is_set()):
                text = samples[index % len(samples)]
                self._append_transcript(meeting_id, text, speaker="Mock", final=True)
                audio_writer.write(frame)
                session.websocket_audio_frames += 1
                session.audio_bytes += len(frame)
                session.audio_seconds = round(session.audio_bytes / max(1, session.sample_rate * 2), 2)
                index += 1
                time.sleep(0.35)
            self._record_audio_saved(session, bytes_written=audio_writer.bytes_written, rms=audio_writer.rms, peak=audio_writer.peak)
    def _run_realtime_stream(self, meeting_id: str, max_seconds: int) -> None:
        session = self._sessions[meeting_id]
        stop_event = self._stop_events[meeting_id]
        data_id = session.data_id or self._extract_data_id(session.task_payload)
        if not data_id:
            raise TingwuMeetingError("Missing Tingwu dataId from CreateTask.")
        session.data_id = data_id
        session.task_id = data_id
        session.sample_rate = int(session.sample_rate or self.config.tingwu_sample_rate)
        session.audio_format = session.audio_format or self.config.tingwu_audio_format
        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                self._run_realtime_stream_once(meeting_id, max_seconds, attempt=attempt)
                return
            except Exception as exc:
                retryable = (
                    attempt < attempts
                    and not stop_event.is_set()
                    and session.websocket_audio_frames <= 0
                    and session.audio_bytes <= 0
                    and self._is_retryable_websocket_start_error(exc)
                )
                self._emit(
                    meeting_id,
                    "websocket_stream_attempt_failed",
                    {"attempt": attempt, "retryable": retryable, "error": redact_sensitive_text(str(exc))[:1000]},
                )
                if not retryable:
                    raise
                time.sleep(1.0)
    def _run_realtime_stream_once(self, meeting_id: str, max_seconds: int, *, attempt: int) -> None:
        session = self._sessions[meeting_id]
        stop_event = self._stop_events[meeting_id]
        data_id = session.data_id or session.task_id or self._extract_data_id(session.task_payload)
        if not data_id:
            raise TingwuMeetingError("Missing Tingwu dataId from CreateTask.")
        source_audio = str(session.task_payload.get("audio_source_path") or self.config.tingwu_audio_file).strip()
        if source_audio:
            streamer = WavPCMStreamer(
                path=source_audio,
                sample_rate=session.sample_rate,
                frame_ms=100,
                speed=self.config.tingwu_audio_file_speed,
            )
        else:
            streamer = ArecordPCMStreamer(
                device=self.selected_mic_device(session),
                sample_rate=session.sample_rate,
                frame_ms=100,
            )
        callback = TingwuRealtimeCallback(self, meeting_id)
        client = TingWuRealtime(
            model="tingwu-meeting-realtime",
            audio_format=session.audio_format,
            sample_rate=session.sample_rate,
            app_id=self.config.tingwu_app_id,
            base_address=self.config.tingwu_ws_url,
            api_key=self.config.tingwu_api_key,
            callback=callback,
            data_id=data_id,
        )
        client.request.task_id = secrets.token_hex(8)
        try:
            self._emit(meeting_id, "websocket_stream_attempt", {"attempt": attempt})
            self._start_tingwu_client(client, callback)
            session.websocket_task_id = getattr(client.request, "task_id", "") or session.websocket_task_id
            self._persist_session(session)
            if not callback.can_send_audio.wait(timeout=15):
                self._raise_callback_error(callback, default="Timed out waiting for Tingwu speech-listen event.")
            streamer.start()
            deadline = time.monotonic() + max(1, max_seconds)
            with StreamingWavWriter(session.audio_path, sample_rate=session.sample_rate) as audio_writer:
                for frame in streamer.frames():
                    if stop_event.is_set() or time.monotonic() >= deadline:
                        break
                    self._raise_callback_error(callback)
                    if not callback.can_send_audio.is_set():
                        time.sleep(0.1)
                        continue
                    frame = amplify_pcm16(frame, self.config.tingwu_pcm_gain)
                    audio_writer.write(frame)
                    session.websocket_audio_frames += 1
                    session.audio_bytes += len(frame)
                    session.audio_seconds = round(session.audio_bytes / max(1, session.sample_rate * 2), 2)
                    client.send_audio_frame(frame)
                self._record_audio_saved(session, bytes_written=audio_writer.bytes_written, rms=audio_writer.rms, peak=audio_writer.peak)
                if audio_writer.bytes_written <= 0:
                    raise TingwuMeetingError(
                        "No microphone audio frames were captured. Check ALSA device, microphone permissions, and input level."
                    )
        finally:
            streamer.stop()
            try:
                self._stop_tingwu_client(client, session)
            except Exception as exc:
                self._emit(meeting_id, "finish_task_error", {"error": redact_sensitive_text(str(exc))[:1000]})
            callback.stopped.wait(timeout=8)
            client.close()
    def _start_tingwu_client(self, client: TingWuRealtime, callback: TingwuRealtimeCallback) -> None:
        connect = getattr(client, "_connect", None)
        send_start = getattr(client, "_send_start_request", None)
        api_key = getattr(client, "api_key", None)
        if callable(connect) and callable(send_start) and api_key:
            connect(api_key)
            if not callback.opened.wait(timeout=20):
                self._raise_callback_error(callback, default="Timed out waiting for Tingwu websocket open event.")
            send_start()
            return
        client.start()
    def _is_retryable_websocket_start_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "socket is already closed",
                "websocket is not connected",
                "timed out waiting for tingwu speech-listen",
                "connection is already closed",
            )
        )
    def _handle_realtime_event(self, meeting_id: str, event: dict[str, Any]) -> None:
        output = event.get("payload", {}).get("output", {}) if isinstance(event.get("payload"), dict) else {}
        event_type = str(output.get("action") or event.get("type") or event.get("event") or "")
        if event_type == "task-failed":
            message = f"{output.get('errorCode') or ''}: {output.get('errorMessage') or ''}".strip(": ")
            raise TingwuMeetingError(redact_sensitive_text(message or json.dumps(event, ensure_ascii=False))[:1000])
        text = extract_transcript_text(event)
        speaker = extract_speaker(event)
        self._record_realtime_raw_event(meeting_id, event_type, event, text=text, speaker=speaker)
        if text:
            self._append_transcript(
                meeting_id,
                text,
                speaker=speaker,
                final=is_final_transcript(event),
            )
        self._emit(meeting_id, "tingwu_event", {"type": event_type, "text": text, "speaker": speaker, "final": is_final_transcript(event)})
    def _stop_tingwu_client(self, client: TingWuRealtime, session: TingwuMeetingSession) -> None:
        try:
            client.stop()
        except Exception as exc:
            self._emit(session.meeting_id, "sdk_stop_error", {"error": redact_sensitive_text(str(exc))[:1000]})
            try:
                self._send_tingwu_finish_task(client, session)
            except Exception as fallback_exc:
                self._emit(session.meeting_id, "finish_task_error", {"error": redact_sensitive_text(str(fallback_exc))[:1000]})
                raise
    def _send_tingwu_finish_task(self, client: TingWuRealtime, session: TingwuMeetingSession) -> None:
        task_id = session.websocket_task_id or getattr(client.request, "task_id", "") or secrets.token_hex(8)
        message = {
            "header": {
                "action": "finish-task",
                "task_id": task_id,
                "request_id": task_id,
                "streaming": "duplex",
            },
            "payload": {
                "model": "tingwu-meeting-realtime",
                "task_group": "aigc",
                "task": "multimodal-generation",
                "function": "generation",
                "input": {
                    "appId": self.config.tingwu_app_id,
                    "dataId": session.data_id or session.task_id,
                    "directive": "stop",
                },
            },
        }
        client._send_text_frame(json.dumps(message, ensure_ascii=False))  # noqa: SLF001
    def _raise_callback_error(self, callback: TingwuRealtimeCallback, *, default: str = "") -> None:
        try:
            message = callback.errors.get_nowait()
        except Empty:
            if default:
                raise TingwuMeetingError(default)
            return
        raise TingwuMeetingError(message)
    def _append_transcript(self, meeting_id: str, text: str, *, speaker: str = "Unknown", final: bool = False) -> None:
        session = self._sessions[meeting_id]
        if final:
            session.partial_text = ""
            item = TingwuTranscriptItem(timestamp=utc_now(), speaker=speaker or "Unknown", text=text, final=True)
            session.transcript.append(item)
            payload = item.__dict__
        else:
            session.partial_text = text
            payload = {"timestamp": utc_now(), "speaker": speaker, "text": text, "final": False}
        self._emit(meeting_id, "transcript", payload)
        self._write_transcript(session)
        self._persist_session(session)
    def _record_realtime_raw_event(self, meeting_id: str, event_type: str, event: dict[str, Any], *, text: str, speaker: str) -> None:
        session = self._sessions.get(meeting_id)
        if session is None:
            return
        raw_events = session.task_payload.setdefault("raw_realtime_events", [])
        if not isinstance(raw_events, list):
            raw_events = []
            session.task_payload["raw_realtime_events"] = raw_events
        raw_events.append(
            {
                "timestamp": utc_now(),
                "type": event_type,
                "speaker": speaker,
                "text": text,
                "final": is_final_transcript(event),
                "event": compact_event_payload(event),
            }
        )
        del raw_events[:-MAX_TINGWU_RAW_EVENTS]

        agent_event = extract_agent_event(event)
        if agent_event:
            self._append_agent_event(session, agent_event)
    def _record_agent_event(self, meeting_id: str, event: dict[str, Any]) -> None:
        session = self._sessions.get(meeting_id)
        if session is None:
            return
        agent_event = extract_agent_event(event) or {
            "timestamp": utc_now(),
            "type": "agent_result",
            "event": compact_event_payload(event),
        }
        self._append_agent_event(session, agent_event)
    def _append_agent_event(self, session: TingwuMeetingSession, event: dict[str, Any]) -> None:
        agent_events = session.task_payload.setdefault("agent_events", [])
        if not isinstance(agent_events, list):
            agent_events = []
            session.task_payload["agent_events"] = agent_events
        agent_events.append(compact_event_payload(event))
        del agent_events[:-MAX_TINGWU_AGENT_EVENTS]
    def _emit(self, meeting_id: str, event: str, payload: dict[str, object] | None = None) -> None:
        queue = self._event_queues.get(meeting_id)
        clean_payload = compact_event_payload(payload or {})
        item = {"event": event, "timestamp": utc_now(), **clean_payload}
        session = self._sessions.get(meeting_id)
        if session is not None:
            events = session.task_payload.setdefault("events", [])
            if isinstance(events, list):
                events.append(item)
                del events[:-MAX_TINGWU_PROVIDER_EVENTS]
        if queue is not None:
            queue.put(item)
    def _record_audio_saved(self, session: TingwuMeetingSession, *, bytes_written: int, rms: int, peak: int) -> Path:
        path = self._session_artifact_path(session, "audio_path", "audio.wav")
        session.audio_rms = int(rms)
        session.audio_peak = int(peak)
        self.audit.record(
            "tingwu.audio_save",
            target=str(path),
            details={
                "bytes": bytes_written,
                "seconds": session.audio_seconds,
                "sample_rate": session.sample_rate,
                "audio_format": session.audio_format,
                "rms": rms,
                "peak": peak,
            },
        )
        return path
    def _write_transcript(self, session: TingwuMeetingSession) -> Path:
        path = self._session_artifact_path(session, "transcript_path", "transcript.md")
        lines = [f"# {session.title} Transcript", ""]
        for item in session.transcript:
            lines.append(f"{item.speaker}: {item.text}")
        if session.partial_text:
            lines.append(f"Unknown: {session.partial_text}")
        atomic_write_text(path, "\n".join(lines) + "\n")
        return path
