from __future__ import annotations

import argparse
from dataclasses import dataclass
from queue import Empty, Queue
import threading
from datetime import datetime
from pathlib import Path

from lelamp.office_agent.audio_api import AudioAPIError, OpenAIAudioAPI
from lelamp.office_agent.dashscope_asr import DashScopeASR, DashScopeASRError
from lelamp.office_agent.dashscope_streaming_asr import DashScopeStreamingASR
from lelamp.office_agent.dashscope_tts import DashScopeTTS, DashScopeTTSError, ReusableDashScopeTTS
from lelamp.office_agent.elevenlabs_tts import ElevenLabsError, ElevenLabsTTS
from lelamp.office_agent.groq_asr import GroqASR
from lelamp.office_agent.assistant_feedback import AssistantFeedback
from lelamp.office_agent.hardware import LampHardware
from lelamp.office_agent.latency import LatencyProbe
from lelamp.office_agent.llm import LLMError, ResponsesLLM, ResponsesLLMConfig
from lelamp.office_agent.prompts import OFFICE_AGENT_INSTRUCTIONS
from lelamp.office_agent.runtime import build_runtime
from openclaw_voice import (
    build_context,
    build_turn_input,
    load_local_env,
    play_wav,
    record_wav_endpointed,
    record_wav,
    should_exit,
)


@dataclass(frozen=True)
class SpeechJob:
    text: str
    path: Path


class SpeechQueue:
    def __init__(self, runtime, audio_api, dashscope_tts, reusable_dashscope_tts, elevenlabs_tts, speaker_device: str, no_tts: bool):
        self.runtime = runtime
        self.audio_api = audio_api
        self.dashscope_tts = dashscope_tts
        self.reusable_dashscope_tts = reusable_dashscope_tts
        self.elevenlabs_tts = elevenlabs_tts
        self.speaker_device = speaker_device
        self.no_tts = no_tts
        self.queue: Queue[SpeechJob] = Queue(maxsize=1)
        self.thread = threading.Thread(target=self._run, name="openclaw-tts-worker", daemon=True)
        self.thread.start()
        if not no_tts and runtime.config.tts_provider == "dashscope":
            self.prewarm()

    def prewarm(self) -> None:
        def worker() -> None:
            try:
                stats = self.reusable_dashscope_tts.start()
                print(f"[tts] prewarm {stats}")
            except DashScopeTTSError as exc:
                print(f"[tts] prewarm failed: {exc}")

        threading.Thread(target=worker, name="openclaw-tts-prewarm", daemon=True).start()

    def submit(self, text: str, path: Path) -> None:
        if self.no_tts:
            return
        while True:
            try:
                self.queue.get_nowait()
            except Empty:
                break
        self.queue.put_nowait(SpeechJob(text=text, path=path))

    def _run(self) -> None:
        while True:
            job = self.queue.get()
            speak(
                self.runtime,
                self.audio_api,
                self.dashscope_tts,
                self.reusable_dashscope_tts,
                self.elevenlabs_tts,
                job.text,
                job.path,
                self.speaker_device,
                self.no_tts,
            )


def set_state(
    runtime,
    state: str,
    details: dict[str, object] | None = None,
    feedback: AssistantFeedback | None = None,
) -> None:
    runtime.audit.record("assistant.state", target=state, details=details or {})
    if feedback is not None:
        feedback.apply(state)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {state}")


def contains_wake(text: str, wake_phrases: list[str]) -> bool:
    normalized = text.lower().replace(" ", "")
    return any(phrase.lower().replace(" ", "") in normalized for phrase in wake_phrases)


def strip_wake(text: str, wake_phrases: list[str]) -> str:
    cleaned = text
    for phrase in wake_phrases:
        cleaned = cleaned.replace(phrase, "")
    return cleaned.strip(" ，。,.")


def main() -> None:
    load_local_env(Path(".env"))
    parser = argparse.ArgumentParser(description="OpenClaw office voice assistant daemon")
    parser.add_argument("--mic-device", default=None)
    parser.add_argument("--mic-rate", type=int, default=None)
    parser.add_argument("--speaker-device", default=None)
    parser.add_argument("--idle-seconds", type=int, default=3)
    parser.add_argument("--listen-seconds", type=int, default=5)
    parser.add_argument("--threshold", type=int, default=40)
    parser.add_argument("--language", default="zh")
    parser.add_argument(
        "--wake-phrase",
        action="append",
        default=["小灯", "小灯小灯", "openclaw", "办公助手"],
    )
    parser.add_argument(
        "--exit-phrase",
        action="append",
        default=["退出", "停止对话", "结束对话", "再见", "stop"],
    )
    parser.add_argument("--followup-turns", type=int, default=3)
    parser.add_argument("--max-history-turns", type=int, default=8)
    parser.add_argument("--no-tts", action="store_true")
    parser.add_argument(
        "--async-tts",
        action="store_true",
        default=True,
        help="Generate and play TTS in the background so listening is not blocked.",
    )
    parser.add_argument(
        "--blocking-tts",
        action="store_false",
        dest="async_tts",
        help="Wait for TTS generation/playback before continuing.",
    )
    parser.add_argument("--no-latency-log", action="store_true")
    parser.add_argument(
        "--fixed-window",
        action="store_true",
        help="Use fixed-duration recording instead of endpoint detection.",
    )
    parser.add_argument(
        "--speech-threshold",
        type=int,
        default=1200,
        help="Minimum RMS threshold used by endpoint detection to decide speech frames.",
    )
    parser.add_argument(
        "--silence-ms",
        type=int,
        default=700,
        help="Stop recording after this much post-speech silence.",
    )
    parser.add_argument("--min-speech-seconds", type=float, default=0.4)
    parser.add_argument("--noise-ms", type=int, default=500)
    parser.add_argument("--noise-multiplier", type=float, default=1.25)
    parser.add_argument("--noise-margin", type=int, default=450)
    parser.add_argument("--speech-start-ms", type=int, default=150)
    parser.add_argument(
        "--vad-backend",
        choices=["auto", "webrtcvad", "rms"],
        default="auto",
        help="Endpoint detector backend. auto tries WebRTC VAD and falls back to RMS.",
    )
    parser.add_argument("--vad-aggressiveness", type=int, default=3, choices=[0, 1, 2, 3])
    parser.add_argument(
        "--streaming-asr",
        action="store_true",
        help="Use DashScope streaming ASR for capture and transcription.",
    )
    parser.add_argument(
        "--voice-reasoning-effort",
        default="low",
        choices=["low", "medium", "high", "xhigh"],
        help="Reasoning effort for short voice replies. Use xhigh only for complex office tasks.",
    )
    args = parser.parse_args()

    runtime = build_runtime()
    mic_device = args.mic_device or runtime.config.mic_device
    mic_rate = args.mic_rate or runtime.config.mic_rate
    speaker_device = args.speaker_device or runtime.config.speaker_device
    work_dir = runtime.config.workspace_dir / ".assistant"
    work_dir.mkdir(parents=True, exist_ok=True)

    audio_api = OpenAIAudioAPI(api_key=runtime.config.openai_api_key, base_url=runtime.config.openai_base_url)
    dashscope_asr = DashScopeASR(
        api_key=runtime.config.dashscope_api_key,
        model=runtime.config.dashscope_asr_model,
        sample_rate=runtime.config.dashscope_asr_sample_rate,
    )
    dashscope_streaming_asr = DashScopeStreamingASR(
        api_key=runtime.config.dashscope_api_key,
        model=runtime.config.dashscope_asr_model,
        sample_rate=runtime.config.dashscope_asr_sample_rate,
    )
    groq_asr = GroqASR(api_key=runtime.config.groq_api_key)
    dashscope_tts = DashScopeTTS(
        api_key=runtime.config.dashscope_api_key,
        model=runtime.config.dashscope_tts_model,
        voice=runtime.config.dashscope_tts_voice,
        url=runtime.config.dashscope_tts_url,
    )
    reusable_dashscope_tts = ReusableDashScopeTTS(
        api_key=runtime.config.dashscope_api_key,
        model=runtime.config.dashscope_tts_model,
        voice=runtime.config.dashscope_tts_voice,
        url=runtime.config.dashscope_tts_url,
    )
    elevenlabs_tts = ElevenLabsTTS(
        api_key=runtime.config.elevenlabs_api_key,
        voice_id=runtime.config.elevenlabs_voice_id,
        model_id=runtime.config.elevenlabs_model_id,
    )
    llm = ResponsesLLM(
        ResponsesLLMConfig(
            api_key=runtime.config.openai_api_key,
            base_url=runtime.config.openai_base_url,
            model=runtime.config.openai_model,
            reasoning_effort=args.voice_reasoning_effort,
        )
    )

    print("OpenClaw office assistant started.")
    print(f"wake={args.wake_phrase} mic={mic_device} speaker={speaker_device}")
    print("待机中。说“小灯”或“办公助手”唤醒。按 Ctrl+C 停止。")

    with LampHardware(
        enabled=runtime.config.enable_hardware,
        port=runtime.config.hardware_port,
        lamp_id=runtime.config.lamp_id,
        audit=runtime.audit,
    ) as hardware:
        feedback = AssistantFeedback(
            hardware=hardware,
            audit=runtime.audit,
            enabled=runtime.config.enable_hardware,
        )
        history: list[dict[str, str]] = []
        speech_queue = SpeechQueue(
            runtime,
            audio_api,
            dashscope_tts,
            reusable_dashscope_tts,
            elevenlabs_tts,
            speaker_device,
            args.no_tts,
        )
        index = 0
        active_turns_remaining = 0
        set_state(runtime, "idle", feedback=feedback)
        while True:
            index += 1
            latency = LatencyProbe(
                f"turn={index}",
                audit=runtime.audit,
                enabled=not args.no_latency_log,
            )
            listening = active_turns_remaining > 0
            seconds = args.listen_seconds if listening else args.idle_seconds
            input_path = work_dir / f"assistant_{index:06d}.wav"
            set_state(
                runtime,
                "listening" if listening else "idle_listen",
                {"seconds": seconds},
                feedback,
            )
            try:
                if args.streaming_asr or runtime.config.asr_provider == "streaming_dashscope":
                    set_state(runtime, "asr", feedback=feedback)
                    with latency.stage("streaming_asr"):
                        streaming_result = dashscope_streaming_asr.listen_once(
                            device=mic_device,
                            max_seconds=seconds,
                            language_hints=[args.language],
                            silence_ms=args.silence_ms,
                            speech_threshold=args.speech_threshold,
                            save_path=input_path,
                        )
                    stats = streaming_result
                    print(f"audio/asr stats: {stats}")
                    if not bool(stats.get("speech_started")):
                        latency.add("no_speech", 0.0)
                        latency.print_summary()
                        continue
                    transcript = str(stats.get("text", "")).strip()
                else:
                    with latency.stage("record"):
                        if args.fixed_window:
                            stats = record_wav(input_path, device=mic_device, rate=mic_rate, seconds=seconds)
                        else:
                            stats = record_wav_endpointed(
                                input_path,
                                device=mic_device,
                                rate=mic_rate,
                                max_seconds=seconds,
                                min_seconds=args.min_speech_seconds,
                                silence_ms=args.silence_ms,
                                speech_threshold=args.speech_threshold,
                                noise_frames=max(1, int(args.noise_ms / 100)),
                                noise_multiplier=args.noise_multiplier,
                                noise_floor_margin=args.noise_margin,
                                speech_start_ms=args.speech_start_ms,
                                vad_backend=args.vad_backend,
                                vad_aggressiveness=args.vad_aggressiveness,
                            )
                    print(f"audio stats: {stats}")
                    if not args.fixed_window and not bool(stats.get("speech_started")):
                        latency.add("no_speech", 0.0)
                        latency.print_summary()
                        continue
                    if int(stats["rms"]) < args.threshold:
                        latency.add("below_threshold", 0.0)
                        latency.print_summary()
                        continue
                    set_state(runtime, "asr", feedback=feedback)
                    with latency.stage("asr"):
                        transcript = transcribe(
                            runtime,
                            audio_api,
                            dashscope_asr,
                            groq_asr,
                            input_path,
                            args.language,
                        )
            except (AudioAPIError, DashScopeASRError) as exc:
                print(f"ASR failed: {exc}")
                set_state(runtime, "error", {"stage": "asr", "error": str(exc)}, feedback)
                latency.print_summary()
                continue

            if not transcript or transcript == "None":
                latency.add("empty_transcript", 0.0)
                latency.print_summary()
                continue
            print(f"You: {transcript}")

            if not listening:
                if not contains_wake(transcript, args.wake_phrase):
                    continue
                set_state(runtime, "wake", {"transcript": transcript}, feedback)
                transcript = strip_wake(transcript, args.wake_phrase)
                active_turns_remaining = args.followup_turns
                if not transcript:
                    reply = "我在，请说。"
                    print(f"OpenClaw: {reply}")
                    set_state(runtime, "speaking", feedback=feedback)
                    if args.async_tts:
                        speech_queue.submit(reply, work_dir / f"reply_{index:06d}.wav")
                        latency.add("tts_queued", 0.0)
                    else:
                        with latency.stage("tts_play"):
                            speak(runtime, audio_api, dashscope_tts, reusable_dashscope_tts, elevenlabs_tts, reply, work_dir / f"reply_{index:06d}.wav", speaker_device, args.no_tts)
                    set_state(runtime, "follow_up", {"remaining": active_turns_remaining}, feedback)
                    latency.print_summary()
                    continue

            if should_exit(transcript, args.exit_phrase):
                reply = "好的，我先回到待机。"
                print(f"OpenClaw: {reply}")
                set_state(runtime, "speaking", {"intent": "exit"}, feedback)
                if args.async_tts:
                    speech_queue.submit(reply, work_dir / f"reply_{index:06d}.wav")
                    latency.add("tts_queued", 0.0)
                else:
                    with latency.stage("tts_play"):
                        speak(runtime, audio_api, dashscope_tts, reusable_dashscope_tts, elevenlabs_tts, reply, work_dir / f"reply_{index:06d}.wav", speaker_device, args.no_tts)
                active_turns_remaining = 0
                set_state(runtime, "idle", feedback=feedback)
                latency.print_summary()
                continue

            with latency.stage("route_plan"):
                route = runtime.intent_router.route(transcript)
                plan = runtime.planner.plan(transcript)
            set_state(runtime, "thinking", {"intent": route.intent, "skill": route.skill}, feedback)
            llm_input = (
                build_turn_input(transcript, history[-args.max_history_turns * 2 :])
                + "\n\n办公意图路由:\n"
                + str(route.as_dict())
                + "\n\n建议执行计划:\n"
                + str(plan)
                + "\n\n执行约束:\n"
                + "如果 route.requires_confirmation 为 true，只能说明需要用户确认，不能声称已经执行。"
                + "如果缺少文件名、收件人或会议上下文，应简短追问缺失信息。"
                + "默认只生成草稿、计划或确认页，不自动发送邮件或控制桌面。"
            )
            try:
                with latency.stage("llm"):
                    reply = llm.complete(
                        instructions=OFFICE_AGENT_INSTRUCTIONS,
                        user_input=llm_input,
                        context=build_context(runtime),
                    )
            except LLMError as exc:
                print(f"LLM failed: {exc}")
                set_state(runtime, "error", {"stage": "llm", "error": str(exc)}, feedback)
                latency.print_summary()
                continue

            print(f"OpenClaw: {reply}")
            history.extend([{"role": "user", "text": transcript}, {"role": "assistant", "text": reply}])
            history = history[-args.max_history_turns * 2 :]
            set_state(runtime, "speaking", feedback=feedback)
            if args.async_tts:
                speech_queue.submit(reply, work_dir / f"reply_{index:06d}.wav")
                latency.add("tts_queued", 0.0)
            else:
                with latency.stage("tts_play"):
                    speak(runtime, audio_api, dashscope_tts, reusable_dashscope_tts, elevenlabs_tts, reply, work_dir / f"reply_{index:06d}.wav", speaker_device, args.no_tts)
            active_turns_remaining = max(active_turns_remaining - 1, 0)
            set_state(
                runtime,
                "follow_up" if active_turns_remaining else "idle",
                {"remaining": active_turns_remaining},
                feedback,
            )
            latency.print_summary()


def transcribe(runtime, audio_api, dashscope_asr, groq_asr, path: Path, language: str) -> str:
    if runtime.config.asr_provider == "dashscope":
        return dashscope_asr.transcribe(path, language_hints=[language])
    if runtime.config.asr_provider == "groq":
        return groq_asr.transcribe(path, model=runtime.config.asr_model, language=language)
    return audio_api.transcribe(path, model=runtime.config.asr_model, language=language)


def speak(runtime, audio_api, dashscope_tts, reusable_dashscope_tts, elevenlabs_tts, text: str, path: Path, speaker_device: str, no_tts: bool) -> None:
    if no_tts:
        return
    try:
        if runtime.config.tts_provider == "dashscope":
            try:
                stats = reusable_dashscope_tts.speak_with_stats(text, path)
                print(f"[tts] {stats}")
            except DashScopeTTSError:
                stats = dashscope_tts.speak_with_stats(text, path)
                print(f"[tts] fallback {stats}")
        elif runtime.config.tts_provider == "elevenlabs":
            elevenlabs_tts.speak(text, path)
        else:
            audio_api.speak(text, model=runtime.config.tts_model, voice=runtime.config.tts_voice, output_path=path)
        play_wav(path, device=speaker_device)
    except (AudioAPIError, DashScopeTTSError, ElevenLabsError, RuntimeError) as exc:
        print(f"TTS/playback failed; text reply is still available: {exc}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOpenClaw office assistant stopped.")
