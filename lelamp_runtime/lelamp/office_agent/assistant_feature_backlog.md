# AI Assistant Feature Backlog

This backlog captures current mainstream AI assistant capabilities and maps them
to LeLamp/OpenClaw implementation work.

## Sources Reviewed

- OpenAI ChatGPT Agent: https://openai.com/index/introducing-chatgpt-agent/
- OpenAI Workspace Agents: https://openai.com/index/introducing-workspace-agents-in-chatgpt/
- Microsoft Copilot Vision: https://support.microsoft.com/en-us/topic/using-copilot-vision-with-microsoft-copilot-3c67686f-fa97-40f6-8a3e-0e45265d425f
- Google Gemini Live camera/screen sharing: https://blog.google/products/gemini/gemini-live-android-tips/
- Apple Intelligence Siri/App Intents: https://developer.apple.com/documentation/appintents/integrating-actions-with-siri-and-apple-intelligence
- Amazon Alexa+: https://www.aboutamazon.com/news/devices/new-alexa-generative-artificial-intelligence//
- Perplexity Assistant: https://www.perplexity.ai/help-center/en/articles/10450852-how-to-use-the-perplexity-android-assistant
- Anthropic Claude computer use: https://docs.anthropic.com/en/docs/build-with-claude/computer-use

## Product Signals

- ChatGPT Agent: multi-step research/action, browser takeover, user approval for consequential actions, interrupt/takeover controls.
- ChatGPT Workspace Agents: shared team workflows, cloud-running agents, reports, CRM/email follow-up, approvals, tool connections.
- Microsoft Copilot: voice wake, screen understanding, file/screen context, step-by-step app guidance, cross-device continuity.
- Google Gemini Live: natural live voice, camera/screen sharing, visual troubleshooting, shopping comparison, creative feedback.
- Apple Intelligence/Siri: personal context, onscreen awareness, Shortcuts/App Intents, cross-app actions, privacy-aware execution.
- Alexa+: smart home routines by voice, shopping, entertainment, calendar/email, household context, proactive daily assistance.
- Perplexity Assistant: answer engine plus Android app actions, reminders, email drafting, app-to-app daily tasks.
- Claude Computer Use: screenshot, mouse/keyboard, desktop automation, sandboxing, explicit consent and prompt-injection safeguards.

## P0: Add First

| Capability | Why it matters | Current status | Implementation target |
|---|---|---|---|
| Realtime voice with interruption | Core digital-human interaction | Done as `omni_realtime_v1` | Keep stable profile |
| Manual tool test entry | Needed before LLM tool loop | Done via `openclaw_cli.py manual` | Expand route coverage |
| Weather / external info tool | Common assistant query | Done via `get_weather` manual route | Replace wttr.in with production provider later |
| Screen/context capture | Copilot/Gemini/Siri baseline | CLI snapshot placeholder added | Add OCR/vision summary |
| Local app/file actions | Desktop assistant baseline | Deterministic safe actions added | `summarize_selected_text`, OCR/vision summary |
| Calendar/reminder | Alexa/Gemini/Perplexity baseline | Local manual reminder store added | OS calendar adapter later |
| Projection/card scheduler | Needed for digital-human presentation | Markdown projection only | Renderer/EventBus later |

## P1: Daily Assistant

| Capability | Implementation target |
|---|---|
| Timers/alarms/reminders | Local SQLite reminder store + notifier |
| Email draft / message reply | Existing draft path, add recipient/contact lookup |
| Web search with citations | Manual `web_search` route added; replace provider later |
| Maps/nearby POI | `search_places`, `route_plan` |
| Shopping/product compare | Search + comparison card |
| Translation/explanation | Local LLM route plus projection/subtitle output |
| Memory/preferences | Existing memory service, add consent UI and recall route |

## P2: Multimodal And Device Control

| Capability | Implementation target |
|---|---|
| Camera object/scene understanding | `scene.report_event` + vision backend |
| Screen sharing help | Screenshot + OCR/VLM + projection highlight |
| Smart home control | MQTT/Home Assistant adapter |
| Cross-app actions | Deterministic local routes first, LLM function calling second |
| Computer-use automation | Sandboxed Playwright/Desktop backend with confirmation gates |
| Smart routines | Rule editor: trigger, condition, action, cooldown |

## P3: Agentic Workflows

| Capability | Implementation target |
|---|---|
| Long-running workspace agents | Background job queue + status card |
| Approval workflow | `requires_confirmation` route + UI confirmation surface |
| Team/shared agents | Export/import skill packs |
| Weekly reports | Scheduled jobs pulling files/calendar/weather/search |
| Mobile dispatch | HTTP/MQTT command endpoint |
| Observability | SQLite event log joining ASR, LLM, tool, TTS, projection |

## Safety Requirements

- Every tool must declare read/write scope.
- Consequential actions require confirmation: send email, purchase, booking, file delete, desktop automation.
- Screen/computer-use tools require explicit active session and visible stop control.
- Tool results should be discarded if user interrupts, unless the tool has already caused an external side effect.
- Logs must include `call_id`, tool name, args summary, duration, status, and generated artifact paths.

## Immediate Next Implementation Slice

1. Add production providers for `web_search` and weather.
2. Add OS calendar adapter and reminder notifier loop.
3. Add OCR/VLM summary for `screen_snapshot`.
4. Add OCR/VLM summary for selected text and screenshots.
5. Start Electron/EventBus only after these local tools are testable from CLI.

## 2026-05-09 Implementation Notes

- Added a XiaoAi compatibility matrix and local utility service for time,
  calculation, unit conversion, jokes/poems, and LLM/search handoff.
- Added deterministic desktop routes for opening apps/URLs/files, file search,
  media control, and volume control. Default backend remains `audit_only`; set
  `OPENCLAW_DESKTOP_BACKEND=local` to execute.
- Added smart-home bridge service for Home Assistant REST or a custom webhook.
  Direct Mi Home, phone call, SMS, and find-phone flows remain adapter-gated.
- Added P0 office workflows:
  - local reminders and calendar events with conflict detection;
  - allowed-root filename and text-content search with JSON reports;
  - screen capture plus OCR summary adapter;
  - meeting follow-up package generating minutes, transcript export, email
    draft, optional reminders, and optional projection confirmation.
- Added LeLamp-specific affordances:
  - state-to-RGB/movement cue map for idle, listening, thinking, speaking,
    reminder, blocked, success, meeting, and projecting;
  - camera single-frame desk observation with optional OpenCV brightness and
    large-rectangle heuristics;
  - projection status, countdown, action, and confirmation cards;
  - environment sensor event inference for presence, lux, speech activity,
    projection blockage, and meeting-likely-started.
