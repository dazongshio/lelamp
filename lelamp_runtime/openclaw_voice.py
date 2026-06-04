from __future__ import annotations

import argparse
import audioop
import math
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

from lelamp.office_agent.audio_api import AudioAPIError, OpenAIAudioAPI
from lelamp.office_agent.dashscope_asr import DashScopeASR, DashScopeASRError
from lelamp.office_agent.dashscope_tts import DashScopeTTS, DashScopeTTSError
from lelamp.office_agent.elevenlabs_tts import ElevenLabsError, ElevenLabsTTS
from lelamp.office_agent.groq_asr import GroqASR
from lelamp.office_agent.hardware_probe import resolve_capture_device
from lelamp.office_agent.llm import LLMError, ResponsesLLM, ResponsesLLMConfig
from lelamp.office_agent.prompts import OFFICE_AGENT_INSTRUCTIONS
from lelamp.office_agent.runtime import build_runtime


def load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def run_checked(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "command failed")


def record_wav(path: Path, *, device: str, rate: int, seconds: int) -> dict[str, int | float | str]:
    if shutil.which("arecord") is None:
        raise RuntimeError("arecord not found")
    selected_device = resolve_capture_device(device)
    run_checked(
        [
            "arecord",
            "-q",
            "-D",
            selected_device,
            "-f",
            "S16_LE",
            "-c",
            "1",
            "-r",
            str(rate),
            "-d",
            str(seconds),
            str(path),
        ]
    )
    import wave

    with wave.open(str(path), "rb") as stream:
        frames = stream.readframes(stream.getnframes())
        rms = audioop.rms(frames, stream.getsampwidth())
        peak = audioop.max(frames, stream.getsampwidth())
        duration = stream.getnframes() / stream.getframerate()
    return {
        "rms": rms,
        "peak": peak,
        "seconds": round(duration, 2),
        "configured_device": device,
        "selected_device": selected_device,
    }


def record_wav_endpointed(
    path: Path,
    *,
    device: str,
    rate: int,
    max_seconds: int,
    min_seconds: float = 0.4,
    silence_ms: int = 700,
    frame_ms: int = 100,
    speech_threshold: int = 1200,
    preroll_ms: int = 300,
    noise_frames: int = 5,
    noise_multiplier: float = 1.25,
    noise_floor_margin: int = 450,
    speech_start_ms: int = 150,
    vad_backend: str = "auto",
    vad_aggressiveness: int = 3,
) -> dict[str, int | float | bool | str]:
    """Record until speech is followed by a short silence window."""

    if shutil.which("arecord") is None:
        raise RuntimeError("arecord not found")
    selected_device = resolve_capture_device(device)
    channels = 1
    sample_width = 2
    if vad_backend in {"auto", "webrtcvad"} and frame_ms not in {10, 20, 30}:
        frame_ms = 30
    frame_bytes = int(rate * frame_ms / 1000) * sample_width * channels
    max_frames = max(1, int(max_seconds * 1000 / frame_ms))
    min_frames = max(1, int(min_seconds * 1000 / frame_ms))
    silence_frames_needed = max(1, int(silence_ms / frame_ms))
    preroll_frames = max(0, int(preroll_ms / frame_ms))
    speech_frames_needed = max(1, math.ceil(speech_start_ms / frame_ms))
    command = [
        "arecord",
        "-q",
        "-D",
        selected_device,
        "-f",
        "S16_LE",
        "-c",
        str(channels),
        "-r",
        str(rate),
        "-t",
        "raw",
    ]
    vad = None
    active_vad_backend = "rms"
    if vad_backend in {"auto", "webrtcvad"}:
        try:
            import webrtcvad

            vad = webrtcvad.Vad(max(0, min(3, vad_aggressiveness)))
            active_vad_backend = "webrtcvad"
        except Exception:
            if vad_backend == "webrtcvad":
                raise

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    chunks: list[bytes] = []
    preroll: list[bytes] = []
    speech_started = False
    speech_candidate_frames = 0
    speech_candidate_frames_max = 0
    above_threshold_frames = 0
    silence_frames = 0
    rms_values: list[int] = []
    noise_values: list[int] = []
    effective_threshold = speech_threshold
    peak = 0
    start = time.perf_counter()
    stop_reason = "max_seconds"

    try:
        for frame_index in range(max_frames):
            frame = process.stdout.read(frame_bytes)
            if not frame:
                stop_reason = "input_closed"
                break
            rms = audioop.rms(frame, sample_width)
            frame_peak = audioop.max(frame, sample_width)
            rms_values.append(rms)
            peak = max(peak, frame_peak)

            if not speech_started and len(noise_values) < noise_frames:
                noise_values.append(rms)
                sorted_noise = sorted(noise_values)
                noise_floor = sorted_noise[max(0, int(len(sorted_noise) * 0.4) - 1)]
                effective_threshold = max(
                    speech_threshold,
                    int(noise_floor * noise_multiplier) + noise_floor_margin,
                )
            if vad is not None:
                frame_is_speech = vad.is_speech(frame, rate) and rms >= effective_threshold
            else:
                frame_is_speech = rms >= effective_threshold

            if not speech_started:
                preroll.append(frame)
                if len(preroll) > preroll_frames:
                    preroll.pop(0)
                if len(noise_values) >= min(noise_frames, frame_index + 1) and frame_is_speech:
                    speech_candidate_frames += 1
                    above_threshold_frames += 1
                else:
                    speech_candidate_frames = 0
                speech_candidate_frames_max = max(speech_candidate_frames_max, speech_candidate_frames)
                if speech_candidate_frames >= speech_frames_needed:
                    speech_started = True
                    chunks.extend(preroll)
                    preroll = []
                elif frame_index + 1 >= max_frames:
                    chunks.extend(preroll)
                continue

            chunks.append(frame)
            if frame_is_speech:
                silence_frames = 0
            else:
                silence_frames += 1

            if len(chunks) >= min_frames and silence_frames >= silence_frames_needed:
                stop_reason = "silence"
                break
    finally:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)

    stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
    if process.returncode not in {None, 0, -15, -9} and not chunks and not preroll:
        raise RuntimeError(stderr.strip() or "arecord failed")

    if not chunks:
        chunks = preroll
    audio = b"".join(chunks)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(sample_width)
        stream.setframerate(rate)
        stream.writeframes(audio)

    duration = len(audio) / (rate * sample_width * channels) if audio else 0.0
    overall_rms = audioop.rms(audio, sample_width) if audio else 0
    return {
        "rms": overall_rms,
        "peak": peak,
        "seconds": round(duration, 2),
        "wall_seconds": round(time.perf_counter() - start, 2),
        "speech_started": speech_started,
        "stop_reason": stop_reason,
        "frame_rms_max": max(rms_values) if rms_values else 0,
        "noise_rms": round(sum(noise_values) / len(noise_values), 1) if noise_values else 0,
        "speech_threshold": effective_threshold,
        "above_threshold_frames": above_threshold_frames,
        "speech_candidate_frames_max": speech_candidate_frames_max,
        "vad_backend": active_vad_backend,
        "frame_ms": frame_ms,
        "configured_device": device,
        "selected_device": selected_device,
    }


def normalize_playback_device(device: str) -> str:
    value = device.strip()
    match = re.fullmatch(r"(plug)?hw:(\d+),?", value)
    if match:
        return f"plughw:{match.group(2)},0"
    match = re.fullmatch(r"(plug)?hw:(\d+),(\d+)", value)
    if match:
        return f"plughw:{match.group(2)},{match.group(3)}"
    return value


def play_wav(path: Path, *, device: str) -> None:
    if shutil.which("aplay") is None:
        raise RuntimeError("aplay not found")
    playback_device = normalize_playback_device(device)
    run_checked(["aplay", "-q", "-D", playback_device, str(path)])


def build_context(runtime) -> dict[str, object]:
    return {
        "security": {
            "permission_mode": runtime.config.permission_mode.value,
            "workspace_dir": str(runtime.config.workspace_dir),
            "allowed_roots": [str(path) for path in runtime.config.allowed_roots],
        },
        "workspace_files": [
            {"name": item.name, "size_bytes": item.size_bytes}
            for item in runtime.workspace.list_files()
        ],
        "meeting": runtime.meeting.status(),
        "recent_memory": runtime.memory.list_recent(5),
    }


def build_turn_input(transcript: str, history: list[dict[str, str]]) -> str:
    history_lines: list[str] = []
    for item in history[-8:]:
        role = "用户" if item["role"] == "user" else "OpenClaw"
        history_lines.append(f"{role}: {item['text']}")
    history_text = "\n".join(history_lines) if history_lines else "无"
    return (
        "以下内容来自本机麦克风的 ASR 转写。"
        "请把它当作用户刚刚对你说的话来回答，不要说自己只能看到文本。"
        "除非用户明确要求其他语言，否则必须使用简体中文回复。"
        "你正在进行持续多轮语音对话，应结合最近对话历史，回答要简短、自然、适合朗读。"
        "\n\n最近对话历史:\n"
        f"{history_text}"
        "\n\nASR transcript:\n"
        f"{transcript}"
    )


def should_exit(transcript: str, exit_phrases: list[str]) -> bool:
    normalized = transcript.strip().lower().replace(" ", "")
    return any(phrase and phrase.lower().replace(" ", "") in normalized for phrase in exit_phrases)


def main() -> None:
    load_local_env(Path(".env"))
    parser = argparse.ArgumentParser(description="No-UI OpenClaw voice conversation")
    parser.add_argument("--mic-device", default=None)
    parser.add_argument("--mic-rate", type=int, default=None)
    parser.add_argument("--speaker-device", default=None)
    parser.add_argument("--seconds", type=int, default=4)
    parser.add_argument("--threshold", type=int, default=40)
    parser.add_argument("--turns", type=int, default=0, help="0 means unlimited")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--language-hint", action="append", help="ASR language hint; repeatable")
    parser.add_argument("--no-tts", action="store_true", help="Skip TTS playback and print replies only")
    parser.add_argument("--max-history-turns", type=int, default=8)
    parser.add_argument(
        "--exit-phrase",
        action="append",
        default=["退出", "停止对话", "结束对话", "再见", "stop"],
        help="Phrase that exits the continuous voice loop; repeatable.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Record only; do not call ASR/LLM/TTS")
    args = parser.parse_args()

    runtime = build_runtime()
    mic_device = args.mic_device or runtime.config.mic_device
    mic_rate = args.mic_rate or runtime.config.mic_rate
    speaker_device = args.speaker_device or runtime.config.speaker_device
    work_dir = runtime.config.workspace_dir / ".voice"
    work_dir.mkdir(parents=True, exist_ok=True)

    audio_api = OpenAIAudioAPI(
        api_key=runtime.config.openai_api_key,
        base_url=runtime.config.openai_base_url,
    )
    groq_asr = GroqASR(api_key=runtime.config.groq_api_key)
    dashscope_asr = DashScopeASR(
        api_key=runtime.config.dashscope_api_key,
        model=runtime.config.dashscope_asr_model,
        sample_rate=runtime.config.dashscope_asr_sample_rate,
    )
    elevenlabs_tts = ElevenLabsTTS(
        api_key=runtime.config.elevenlabs_api_key,
        voice_id=runtime.config.elevenlabs_voice_id,
        model_id=runtime.config.elevenlabs_model_id,
    )
    dashscope_tts = DashScopeTTS(
        api_key=runtime.config.dashscope_api_key,
        model=runtime.config.dashscope_tts_model,
        voice=runtime.config.dashscope_tts_voice,
        url=runtime.config.dashscope_tts_url,
    )
    llm = ResponsesLLM(
        ResponsesLLMConfig(
            api_key=runtime.config.openai_api_key,
            base_url=runtime.config.openai_base_url,
            model=runtime.config.openai_model,
            reasoning_effort=runtime.config.openai_reasoning_effort,
        )
    )

    print("OpenClaw voice loop started.")
    print(
        f"mic={mic_device} rate={mic_rate} speaker={speaker_device} "
        f"model={runtime.config.openai_model} asr={runtime.config.asr_provider} "
        f"tts={runtime.config.tts_provider}"
    )
    print("Speak after each prompt. Say '退出' or press Ctrl+C to stop.")

    turn = 0
    history: list[dict[str, str]] = []
    while True:
        if args.turns and turn >= args.turns:
            break
        turn += 1
        input_path = work_dir / f"turn_{turn:04d}_input.wav"
        output_path = work_dir / f"turn_{turn:04d}_reply.wav"
        print(f"\nListening for {args.seconds}s...")
        stats = record_wav(input_path, device=mic_device, rate=mic_rate, seconds=args.seconds)
        print(f"audio stats: {stats}")
        if int(stats["rms"]) < args.threshold:
            print("Skipped: audio below threshold.")
            continue
        if args.dry_run:
            print(f"Recorded {input_path}")
            continue

        try:
            language_hints = args.language_hint or [args.language]
            if runtime.config.asr_provider == "dashscope":
                transcript = dashscope_asr.transcribe(input_path, language_hints=language_hints)
            elif runtime.config.asr_provider == "groq":
                transcript = groq_asr.transcribe(
                    input_path,
                    model=runtime.config.asr_model,
                    language=args.language,
                )
            else:
                transcript = audio_api.transcribe(
                    input_path,
                    model=runtime.config.asr_model,
                    language=args.language,
                )
            print(f"You: {transcript}")
            runtime.audit.record("voice.transcript", details={"chars": len(transcript)})
            if should_exit(transcript, args.exit_phrase):
                print("OpenClaw: 好的，语音对话已结束。")
                break
            llm_input = build_turn_input(transcript, history[-args.max_history_turns * 2 :])
            reply = llm.complete(
                instructions=OFFICE_AGENT_INSTRUCTIONS,
                user_input=llm_input,
                context=build_context(runtime),
            )
            print(f"OpenClaw: {reply}")
            history.extend(
                [
                    {"role": "user", "text": transcript},
                    {"role": "assistant", "text": reply},
                ]
            )
            if len(history) > args.max_history_turns * 2:
                history = history[-args.max_history_turns * 2 :]
            runtime.audit.record("voice.reply", details={"chars": len(reply)})
            if args.no_tts:
                continue
            try:
                if runtime.config.tts_provider == "elevenlabs":
                    elevenlabs_tts.speak(reply, output_path=output_path)
                elif runtime.config.tts_provider == "dashscope":
                    try:
                        dashscope_tts.speak(reply, output_path=output_path)
                    except DashScopeTTSError:
                        dashscope_tts.speak(reply, output_path=output_path)
                else:
                    audio_api.speak(
                        reply,
                        model=runtime.config.tts_model,
                        voice=runtime.config.tts_voice,
                        output_path=output_path,
                    )
                play_wav(output_path, device=speaker_device)
            except (AudioAPIError, DashScopeTTSError, ElevenLabsError, RuntimeError) as exc:
                runtime.audit.record("voice.tts_error", status="error", details={"error": str(exc)})
                print(f"TTS/playback failed; text reply is still available: {exc}", file=sys.stderr)
        except (
            AudioAPIError,
            DashScopeASRError,
            LLMError,
            RuntimeError,
        ) as exc:
            runtime.audit.record("voice.error", status="error", details={"error": str(exc)})
            print(f"Voice turn failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOpenClaw voice loop stopped.")
