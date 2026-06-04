# OpenClaw Office Voice Assistant Plan

This plan is research-gated. Each implementation step starts by checking mature
open-source or proven product patterns, then adapting the smallest useful piece
to the current LeLamp runtime.

## Phase 1: Architecture Baseline

Status: researched.

Decision: use a Home Assistant/Wyoming-style voice satellite and pipeline
architecture, but keep the OpenClaw office pipeline instead of adopting a home
automation stack.

Deliverable:
- `assistant_research.md`
- Explicit assistant states: idle, wake, listening, thinking, speaking,
  follow-up, error.

## Phase 2: Wake / VAD / Endpointing

Research first:
- Picovoice Porcupine: commercial, reliable wake word.
- openWakeWord: open source, but Chinese/custom keyword support must be checked.
- Silero VAD: open source speech activity detection.
- WebRTC VAD: lightweight endpointing.
- DashScope Paraformer streaming endpointing: cloud-side option.

Implementation target:
- First practical version: keyword-in-transcript wake mode using DashScope ASR,
  plus RMS threshold.
- Product version: local wake word + VAD before cloud ASR.

## Phase 3: Office Intent Router

Research first:
- Rhasspy intent-event design.
- OpenVoiceOS/Mycroft skill activation phrases.
- LangGraph state routing for multi-step office tasks.

Implementation target:
- Route voice commands into structured intents:
  meeting, document, scan, projection, email_draft, desktop, security,
  general_office_chat.
- Risky desktop/full-control actions require explicit confirmation.

## Phase 4: Continuous Conversation State Machine

Research first:
- Home Assistant Assist satellite states.
- LiveKit Agents interruption/turn detection patterns.
- Voice assistant barge-in handling.

Implementation target:
- `openclaw_assistant.py` daemon loop.
- Wake phrase opens a follow-up window.
- Exit phrases return to idle.
- TTS failure does not kill the loop.

## Phase 5: Office Skill Execution

Research first:
- Anthropic Skills / OpenAI Agents tool manifests.
- MCP tool boundary for future desktop and document integrations.

Implementation target:
- Bind routed intents to existing OpenClaw services.
- Default to planning/confirmation for operations that need files, projection,
  desktop control, or sending data out.

## Phase 6: Hardware Feedback

Status: researched and implemented.

Research first:
- LeLamp original animation patterns.
- ELEGNT expressive motion primitives.
- Status light conventions in assistant devices.

Implementation target:
- State -> light/motion mapping:
  idle, wake, listening, thinking, speaking, blocked, success.
- Keep hardware optional via `OPENCLAW_ENABLE_HARDWARE`.

Implemented:
- `assistant_feedback.py` maps voice assistant states to RGB and recorded
  LeLamp motions.
- `openclaw_assistant.py` enters `error` state for ASR/LLM failures and applies
  feedback only when hardware is enabled.

## Phase 7: Latency Measurement

Status: researched and implemented.

Research first:
- Home Assistant Assist satellite state timing.
- LiveKit turn detection and interruption patterns.
- DashScope realtime ASR as a future streaming path.
- Silero/WebRTC VAD as local endpointing paths.

Implementation target:
- Print per-turn latency for record, ASR, route/plan, LLM, TTS/playback, and
  total.
- Keep latency logs on by default; allow `--no-latency-log`.

Implemented:
- `latency.py` provides a small `LatencyProbe`.
- `openclaw_assistant.py` records timing for wake-only replies, normal replies,
  empty ASR, threshold skips, and error paths.

## Phase 8: Endpointed Recording

Status: researched and implemented.

Research first:
- Phone assistant stacks use KWS + VAD/endpointing instead of fixed recording
  windows.
- Production systems generally use 300-700 ms post-speech silence to decide
  turn end.

Implementation target:
- Replace fixed `arecord -d N` with streaming PCM capture.
- Stop after speech starts and then reaches a silence window.
- Keep `--fixed-window` as a rollback switch.

Implemented:
- `record_wav_endpointed()` in `openclaw_voice.py`.
- Adaptive noise-floor thresholding plus consecutive speech-frame detection.
- `openclaw_assistant.py` now skips ASR when no speech was detected and prints
  endpoint stats.

## Phase 9: WebRTC VAD Endpointing

Status: researched and implemented.

Research first:
- WebRTC VAD is lightweight and works on 10/20/30 ms PCM frames.
- `webrtcvad-wheels` provides Python 3.12 wheels for the current uv runtime.
- Silero VAD remains a later option if we need higher accuracy and can accept
  ONNX/Torch dependencies.

Implemented:
- Added `webrtcvad-wheels` to dependencies.
- `record_wav_endpointed()` supports `vad_backend=auto|webrtcvad|rms`.
- WebRTC VAD is combined with an RMS gate to reduce false positives on this
  desk microphone.
- `--vad-backend`, `--vad-aggressiveness`, and `--speech-start-ms` are exposed
  in `openclaw_assistant.py`.
