# Office Voice Assistant Research Notes

Date: 2026-05-04

Goal: keep OpenClaw as a heavy office agent, but provide a XiaoAI-style voice
interaction layer: always available, wakeable, conversational, skill-oriented,
and safe by default.

## Reference Architectures

### Home Assistant Assist / Wyoming

Useful idea: split the system into a voice satellite and a pipeline. The
satellite owns microphone, speaker, wake word, and local state. The pipeline
owns STT, intent/conversation, TTS, and tool execution. Home Assistant documents
explicit assistant states such as `IDLE`, `LISTENING`, and TTS-finished
transitions.

Why it matters here: LeLamp should behave like a desk satellite. OpenClaw
should remain the office pipeline and skill executor.

### Rhasspy

Useful idea: modular voice services with wake, ASR, intent recognition, and
output adapters. Rhasspy emits structured intent events rather than treating
everything as open-ended chat.

Why it matters here: office commands should route to meeting/document/scan/
projection/desktop skills first, and fall back to LLM chat only when no office
intent is clear.

### OpenVoiceOS / Mycroft

Useful idea: plugin architecture for STT, TTS, wake word, and skills. Skills are
separate units with clear activation phrases and handlers.

Why it matters here: OpenClaw skills should stay pluggable and auditable, not
hard-coded into one voice loop.

### Willow

Useful idea: privacy-focused assistant platform that optimizes wake word
accuracy and low-latency local/cloud inference.

Why it matters here: LeLamp should prefer local wake/VAD and only send audio to
cloud ASR after wake/explicit listening.

Primary sources checked:
- Home Assistant Assist satellite entity docs:
  https://developers.home-assistant.io/docs/core/entity/assist-satellite/
- LiveKit turn detection docs:
  https://docs.livekit.io/agents/logic/turns/
- openWakeWord repository:
  https://github.com/dscripka/openWakeWord
- OpenVoiceOS organization:
  https://github.com/openVoiceOS
- Apple ELEGNT paper page:
  https://machinelearning.apple.com/research/elegnt-expressive-functional-movement

## Recommended Architecture For LeLamp/OpenClaw

```text
Audio satellite loop
  -> idle / wake detection
  -> listening window
  -> ASR transcript
  -> office intent router
  -> OpenClaw skill planner / executor
  -> LLM response
  -> TTS playback
  -> follow-up window
  -> idle
```

## Reuse Candidates

| Area | Candidate | Reuse decision |
| --- | --- | --- |
| Voice pipeline pattern | Home Assistant Assist / Wyoming | Copy architecture/state machine, do not adopt whole HA stack. |
| Intent event pattern | Rhasspy | Copy structured intent/event idea. |
| Plugin/skill style | OpenVoiceOS/Mycroft | Copy plugin boundary; keep existing OpenClaw services. |
| Wake word | Picovoice Porcupine, livekit-wakeword, openWakeWord | Research next. Chinese custom wake word support is the key filter. |
| VAD/endpointer | Silero VAD, WebRTC VAD, energy threshold | Research next; current fixed-window recording is not enough. |
| ASR | DashScope Paraformer | Already working for Chinese office speech. |
| TTS | DashScope CosyVoice direct WebSocket | Already working after bypassing SDK timeout. |

## Implementation Principles

1. Voice layer should not directly perform risky actions.
2. Intent router should produce structured route data.
3. Skill execution should stay sandbox-first and audited.
4. Wake/listen/speak states should be explicit.
5. If an external component is copied, prefer protocol/architecture reuse over
   importing a large unrelated stack.

## Wake / VAD Research

### Picovoice Porcupine

Porcupine supports custom wake word training and Mandarin Chinese. It is the
best product-grade option when we need a reliable Chinese wake phrase such as
`小灯小灯`. It is commercial, so it should be isolated behind a provider
interface.

### livekit-wakeword

LiveKit's wake word project supports custom training across many languages,
including Chinese. It is promising for an open-source route, especially if we
later move more voice logic into the LiveKit ecosystem.

### openWakeWord

openWakeWord is attractive because it is open source and integrates with Home
Assistant style voice pipelines, but Python 3.12 packaging and Chinese custom
wake quality need more validation before it becomes the default.

### Silero VAD / WebRTC VAD

Silero VAD is a strong open-source VAD option with ONNX support. WebRTC VAD is
lighter and older. Both are useful for endpointing, but the current runtime can
start with RMS thresholding because DashScope ASR is already working.

### Decision

Use a staged approach:

1. MVP: ASR keyword wake (`小灯`, `OpenClaw`, `办公助手`) + RMS threshold.
2. Product prototype: local Porcupine custom wake word.
3. Open-source research track: livekit-wakeword/openWakeWord training.
4. Endpointing upgrade: Silero VAD if fixed-window recording feels too clumsy.

## Intent Routing / Skill Orchestration Research

### Rhasspy

Rhasspy stores voice command templates in `sentences.ini`, where each section is
an intent and sentence templates can include named slots. It also emits
structured JSON intent events. This is a good fit for deterministic office
commands such as "开启会议模式" or "扫描这份合同".

### Home Assistant Intents

Home Assistant's voice system uses sentence templates with slots/placeholders
and maps recognized text to structured intents. This reinforces the same
pattern: predictable command classes should not require an expensive LLM route
on every turn.

### OpenVoiceOS / Mycroft

OpenVoiceOS/Mycroft skills use activation phrases and intent handlers. This is
useful for keeping office skills separated and testable.

### LangGraph

LangGraph is well suited for multi-step workflows with state and conditional
edges, but it is heavier than needed for the first voice router. It should be
introduced when OpenClaw needs resumable multi-step execution across documents,
projection, and desktop automation.

### Decision

Use a hybrid office router:

1. Rule/template routing for high-confidence office commands.
2. Slot extraction for likely file names, topics, recipients, and target modes.
3. Confirmation requirement on risky desktop/full-control actions.
4. LLM fallback only for ambiguous or complex requests.
5. LangGraph later for resumable workflows, not for the first router.

## Continuous Conversation / Interruption Research

### Home Assistant Assist Satellite

Home Assistant's satellite model uses explicit states such as `IDLE`,
`LISTENING`, `PROCESSING`, and speaking/TTS completion. A key implementation
detail is that the satellite must report when TTS playback has finished so the
assistant can return to idle or enter a follow-up listening state.

### LiveKit Agents

LiveKit documents turn detection, endpointing, and adaptive interruption
handling. A production-grade voice agent keeps input monitoring active while the
assistant is speaking, so a user can barge in and interrupt long responses.

### Barge-in Pattern

Reliable barge-in requires non-blocking playback plus VAD/ASR running during
playback. A blocking `aplay` command cannot implement real interruption; it can
only support exit phrases after playback finishes.

### Decision

MVP:
- Explicit states in logs: idle, listening, thinking, speaking, follow-up,
  error.
- Wake phrase opens a follow-up window.
- Exit phrase returns to idle.
- TTS failure must not break the loop.

Product version:
- Replace blocking `aplay` with non-blocking audio playback.
- Run VAD during speaking.
- Stop playback on meaningful interruption and feed the new utterance to ASR.

## Hardware Feedback Research

### Home Assistant Assist Satellite

The Assist satellite developer docs define state constants:
`IDLE`, `LISTENING`, `PROCESSING`, and `RESPONDING`. This is the cleanest
baseline for LED/status feedback because it maps directly to a voice assistant
pipeline.

### LeLamp / ELEGNT

LeLamp already has expressive recorded motions and RGB service hooks. The
ELEGNT research argues that expressive movement improves perceived engagement
for non-anthropomorphic robots. For OpenClaw, movement should communicate state
and attention rather than become decorative.

### Decision

MVP state mapping:

| State | Light | Motion |
| --- | --- | --- |
| idle | dim white/off | idle |
| wake | warm white | wake_up |
| listening | blue | curious/scanning |
| thinking | purple | curious |
| speaking | green | nod |
| blocked/error | red/orange | headshake/shock |
| success | green | nod |

Keep this optional through `OPENCLAW_ENABLE_HARDWARE` because development
machines may not have LeLamp motors or `rpi_ws281x`.

## Latency Research

### Current Bottlenecks

The current MVP records fixed-length WAV files with `arecord -d N`, then sends
the complete file to ASR. This makes `idle-seconds` and `listen-seconds` hard
lower bounds for each turn. In the user's trace, the 5 second follow-up listen
window explains most of the gap between `listening` and `asr`.

### Proven Patterns

Home Assistant Assist and LiveKit voice agents both separate wake/listen/
processing/responding states and rely on endpointing rather than fixed windows.
LiveKit's turn detection docs emphasize VAD/semantic turn detection and
interruptions. DashScope Paraformer realtime is the cloud-side option if we
choose streaming ASR; Silero VAD or WebRTC VAD are the local endpointing
options if we keep file-based ASR.

### Decision

MVP measurement:
- Add per-turn latency logs for record, ASR, route/plan, LLM, TTS/playback, and
  total.

Next optimization:
- Replace fixed `listen-seconds` with VAD endpointing.
- Use shorter idle wake windows, or local wake word before cloud ASR.
- Consider streaming ASR only after stage logs show ASR itself dominates.

## Endpointed Recording Decision

The first optimization follows the phone-assistant pattern from the user's
research: keep capture local, detect the beginning of speech, and end the turn
after a short silence window instead of waiting for a fixed 3-5 second record.

Implementation notes:
- Use `arecord -t raw` and read 100 ms PCM frames in Python.
- Estimate a short local noise floor and derive an adaptive RMS threshold.
- Require consecutive above-threshold frames before declaring speech start.
- Stop after 700 ms of post-speech silence.
- Keep fixed-window recording available through `--fixed-window`.

Observed local validation:
- Silent desktop capture no longer reaches ASR.
- Speaker-to-mic wake phrase with `max_seconds=4` stopped on silence in about
  2.34 seconds instead of waiting the full 4 seconds.
