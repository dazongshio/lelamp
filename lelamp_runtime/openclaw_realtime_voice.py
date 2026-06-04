from __future__ import annotations

import argparse
import array
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from lelamp.office_agent.dashscope_realtime import (
    DashScopeRealtimeClient,
    DashScopeRealtimeConfig,
    DashScopeRealtimeError,
)
from lelamp.office_agent.hardware_probe import resolve_capture_device
from lelamp.office_agent.runtime import build_runtime
from openclaw_voice import load_local_env


DEFAULT_INSTRUCTIONS = (
    "你是 LeLamp 桌面数字人助手。请用简体中文回答，保持简短、自然、适合朗读。"
    "如果用户用英文提问，可以用英文回答。"
)

REALTIME_PROFILES: dict[str, dict[str, Any]] = {
    "omni_realtime_v1": {
        "description": "DashScope Qwen3 Omni Realtime, ALSA PCM, server VAD, JSONL latency logging.",
        "model": "qwen3-omni-flash-realtime",
        "url": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        "voice": "Cherry",
        "input_rate": 16000,
        "output_rate": 24000,
        "frame_ms": 100,
        "vad": "server_vad",
        "vad_threshold": 0.5,
        "silence_ms": 600,
        "transcription_model": "gummy-realtime-v1",
        "instructions": DEFAULT_INSTRUCTIONS,
    }
}


def pcm16_stats(frame: bytes) -> tuple[int, int]:
    if not frame:
        return 0, 0
    samples = array.array("h")
    samples.frombytes(frame)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0, 0
    peak = max(abs(sample) for sample in samples)
    rms = int(math.sqrt(sum(sample * sample for sample in samples) / len(samples)))
    return rms, peak


def normalize_playback_device(device: str) -> str:
    value = device.strip()
    match = re.fullmatch(r"(plug)?hw:(\d+),?", value)
    if match:
        return f"plughw:{match.group(2)},0"
    match = re.fullmatch(r"(plug)?hw:(\d+),(\d+)", value)
    if match:
        return f"plughw:{match.group(2)},{match.group(3)}"
    return value


class RawPcmPlayer:
    def __init__(self, *, device: str, sample_rate: int, on_first_write=None):
        if shutil.which("aplay") is None:
            raise RuntimeError("aplay not found")
        self.device = normalize_playback_device(device)
        self.sample_rate = sample_rate
        self.on_first_write = on_first_write
        self._queue: queue.Queue[tuple[int | None, bytes] | None] = queue.Queue(maxsize=256)
        self._lock = threading.Lock()
        self._played_turns: set[int] = set()
        self._process: subprocess.Popen | None = None
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="realtime-pcm-player", daemon=True)
        self._thread.start()

    def write(self, pcm: bytes, *, turn_id: int | None = None) -> None:
        if self._closed.is_set() or not pcm:
            return
        try:
            self._queue.put_nowait((turn_id, pcm))
        except queue.Full:
            self.interrupt()

    def interrupt(self) -> None:
        self._clear_queue()
        self._stop_process()

    def close(self) -> None:
        self._closed.set()
        self._clear_queue()
        self._queue.put(None)
        self._stop_process()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._closed.is_set():
            item = self._queue.get()
            if item is None:
                break
            turn_id, pcm = item
            process = self._ensure_process()
            try:
                assert process.stdin is not None
                process.stdin.write(pcm)
                if (
                    turn_id is not None
                    and turn_id not in self._played_turns
                    and self.on_first_write is not None
                ):
                    self._played_turns.add(turn_id)
                    self.on_first_write(turn_id)
            except (BrokenPipeError, OSError):
                self._stop_process()

    def _ensure_process(self) -> subprocess.Popen:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self._process
            self._process = subprocess.Popen(
                [
                    "aplay",
                    "-q",
                    "-D",
                    self.device,
                    "-f",
                    "S16_LE",
                    "-c",
                    "1",
                    "-r",
                    str(self.sample_rate),
                    "-t",
                    "raw",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            return self._process

    def _stop_process(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)

    def _clear_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


class ArecordStreamer:
    def __init__(self, *, device: str, sample_rate: int, frame_ms: int):
        if shutil.which("arecord") is None:
            raise RuntimeError("arecord not found")
        self.configured_device = device
        self.device = resolve_capture_device(device)
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_bytes = int(sample_rate * frame_ms / 1000) * 2
        self.process: subprocess.Popen | None = None
        self.last_stderr = ""

    def start(self) -> None:
        self.process = subprocess.Popen(
            [
                "arecord",
                "-q",
                "-D",
                self.device,
                "-f",
                "S16_LE",
                "-c",
                "1",
                "-r",
                str(self.sample_rate),
                "-t",
                "raw",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        time.sleep(0.05)
        if self.process.poll() is not None:
            stderr = self.process.stderr.read().decode(errors="replace") if self.process.stderr else ""
            self.last_stderr = stderr.strip()
            raise RuntimeError(self.last_stderr or "arecord exited before streaming audio")

    def frames(self):
        if self.process is None:
            self.start()
        assert self.process is not None
        assert self.process.stdout is not None
        while self.process.poll() is None:
            frame = self.process.stdout.read(self.frame_bytes)
            if not frame:
                break
            yield frame

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        if process.stderr is not None:
            try:
                self.last_stderr = process.stderr.read().decode(errors="replace").strip()
            except OSError:
                pass


class LatencyTracker:
    def __init__(
        self,
        *,
        log_path: Path,
        profile: str,
        model: str,
        voice: str,
        mic_device: str,
        speaker_device: str,
        input_rate: int,
        output_rate: int,
        vad: str,
        silence_ms: int,
    ):
        self.log_path = log_path
        self.run_id = uuid.uuid4().hex
        self.profile = profile
        self.model = model
        self.voice = voice
        self.mic_device = mic_device
        self.speaker_device = speaker_device
        self.input_rate = input_rate
        self.output_rate = output_rate
        self.vad = vad
        self.silence_ms = silence_ms
        self.started_at = time.perf_counter()
        self.connected_at: float | None = None
        self.mic_started_at: float | None = None
        self._turn_id = 0
        self._turns: dict[int, dict[str, Any]] = {}
        self._active_turn_id: int | None = None
        self._last_turn_id: int | None = None
        self._written_reasons: set[tuple[int, str]] = set()
        self._lock = threading.Lock()

    @property
    def active_turn_id(self) -> int | None:
        with self._lock:
            return self._active_turn_id

    def mark_connected(self) -> None:
        self.connected_at = time.perf_counter()

    def mark_mic_started(self) -> None:
        self.mic_started_at = time.perf_counter()

    def speech_started(self) -> int:
        with self._lock:
            self._turn_id += 1
            turn_id = self._turn_id
            now = time.perf_counter()
            self._active_turn_id = turn_id
            self._last_turn_id = turn_id
            self._turns[turn_id] = {
                "turn_id": turn_id,
                "speech_started_at": now,
                "speech_stopped_at": None,
                "transcript_completed_at": None,
                "response_created_at": None,
                "first_audio_at": None,
                "first_playback_write_at": None,
                "audio_done_at": None,
                "response_done_at": None,
                "transcript": "",
                "audio_bytes": 0,
                "audio_chunks": 0,
            }
            return turn_id

    def mark(self, field: str, *, turn_id: int | None = None) -> None:
        with self._lock:
            target_id = turn_id or self._active_turn_id or self._last_turn_id
            if target_id is None or target_id not in self._turns:
                return
            self._turns[target_id][field] = time.perf_counter()

    def add_audio(self, byte_count: int) -> int | None:
        with self._lock:
            turn_id = self._active_turn_id
            if turn_id is None or turn_id not in self._turns:
                return None
            turn = self._turns[turn_id]
            if turn["first_audio_at"] is None:
                turn["first_audio_at"] = time.perf_counter()
            turn["audio_bytes"] += byte_count
            turn["audio_chunks"] += 1
            return turn_id

    def set_transcript(self, transcript: str) -> None:
        with self._lock:
            turn_id = self._active_turn_id
            if turn_id is None or turn_id not in self._turns:
                return
            self._turns[turn_id]["transcript"] = transcript
            self._turns[turn_id]["transcript_completed_at"] = time.perf_counter()

    def finish(self, *, turn_id: int | None = None, reason: str = "audio_done") -> dict[str, Any] | None:
        with self._lock:
            target_id = turn_id or self._active_turn_id or self._last_turn_id
            if target_id is None or target_id not in self._turns:
                return None
            turn = self._turns[target_id]
            if reason == "audio_done" and turn["audio_done_at"] is None:
                turn["audio_done_at"] = time.perf_counter()
            summary = self._summary(turn, reason)
            already_written = (target_id, reason) in self._written_reasons
            self._written_reasons.add((target_id, reason))
            if self._active_turn_id == target_id and reason in {"audio_done", "response_done"}:
                self._active_turn_id = None
        if already_written:
            return summary
        self._write(summary)
        return summary

    def _summary(self, turn: dict[str, Any], reason: str) -> dict[str, Any]:
        speech_started_at = turn["speech_started_at"]
        audio_seconds = turn["audio_bytes"] / (self.output_rate * 2) if turn["audio_bytes"] else 0.0
        return {
            "run_id": self.run_id,
            "turn_id": turn["turn_id"],
            "reason": reason,
            "profile": self.profile,
            "model": self.model,
            "voice": self.voice,
            "mic_device": self.mic_device,
            "speaker_device": self.speaker_device,
            "input_rate": self.input_rate,
            "output_rate": self.output_rate,
            "vad": self.vad,
            "silence_ms": self.silence_ms,
            "connect_seconds": self._delta(self.connected_at, self.started_at),
            "mic_start_seconds": self._delta(self.mic_started_at, self.started_at),
            "speech_duration_seconds": self._delta(turn["speech_stopped_at"], speech_started_at),
            "speech_to_transcript_seconds": self._delta(
                turn["transcript_completed_at"], speech_started_at
            ),
            "speech_to_first_audio_seconds": self._delta(turn["first_audio_at"], speech_started_at),
            "speech_to_first_playback_seconds": self._delta(
                turn["first_playback_write_at"], speech_started_at
            ),
            "speech_to_audio_done_seconds": self._delta(turn["audio_done_at"], speech_started_at),
            "speech_to_response_done_seconds": self._delta(
                turn["response_done_at"], speech_started_at
            ),
            "audio_done_to_response_done_seconds": self._delta(
                turn["response_done_at"], turn["audio_done_at"]
            ),
            "first_audio_to_playback_seconds": self._delta(
                turn["first_playback_write_at"], turn["first_audio_at"]
            ),
            "output_audio_seconds": round(audio_seconds, 3),
            "audio_chunks": turn["audio_chunks"],
            "audio_bytes": turn["audio_bytes"],
            "transcript_chars": len(turn["transcript"]),
            "transcript": turn["transcript"],
        }

    def _write(self, summary: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(summary, ensure_ascii=False) + "\n")

    @staticmethod
    def _delta(end: float | None, start: float | None) -> float | None:
        if end is None or start is None:
            return None
        return round(end - start, 3)


def print_latency_summary(summary: dict[str, Any]) -> None:
    keys = [
        ("speech_to_first_audio_seconds", "first_audio"),
        ("speech_to_first_playback_seconds", "first_playback"),
        ("speech_to_transcript_seconds", "transcript"),
        ("speech_duration_seconds", "speech"),
        ("speech_to_audio_done_seconds", "audio_done"),
        ("speech_to_response_done_seconds", "response_done"),
        ("output_audio_seconds", "out_audio"),
    ]
    parts = []
    for key, label in keys:
        value = summary.get(key)
        if value is not None:
            parts.append(f"{label}={value}s")
    print(f"\n[latency] turn={summary['turn_id']} " + " ".join(parts))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DashScope Qwen Omni realtime voice loop")
    parser.add_argument(
        "--profile",
        choices=sorted(REALTIME_PROFILES),
        default="omni_realtime_v1",
        help="Pinned runtime profile. Defaults to the current stable mode.",
    )
    parser.add_argument("--mic-device", default=None)
    parser.add_argument("--speaker-device", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--url", default=None)
    parser.add_argument("--voice", default=None)
    parser.add_argument("--input-rate", type=int, default=16000)
    parser.add_argument("--output-rate", type=int, default=24000)
    parser.add_argument("--frame-ms", type=int, default=100)
    parser.add_argument("--vad", choices=["server_vad", "semantic_vad"], default="server_vad")
    parser.add_argument("--vad-threshold", type=float, default=0.5)
    parser.add_argument("--silence-ms", type=int, default=600)
    parser.add_argument("--instructions", default=DEFAULT_INSTRUCTIONS)
    parser.add_argument("--transcription-model", default=None)
    parser.add_argument("--max-seconds", type=int, default=0, help="0 means run until Ctrl+C")
    parser.add_argument("--no-playback", action="store_true")
    parser.add_argument("--meter", action="store_true", help="Print microphone RMS/peak once per second")
    parser.add_argument(
        "--latency-log",
        default=None,
        help="JSONL latency log path. Defaults to workspace/.voice/realtime_latency.jsonl.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def pick_value(
    *,
    cli_value: Any,
    env_value: Any,
    profile_value: Any,
    cli_default: Any = None,
) -> Any:
    if cli_value != cli_default and cli_value is not None:
        return cli_value
    if env_value not in {None, ""}:
        return env_value
    return profile_value


def main() -> None:
    load_local_env(Path(".env"))
    args = parse_args()
    runtime = build_runtime()
    profile = REALTIME_PROFILES[args.profile]
    mic_device = args.mic_device or runtime.config.mic_device
    speaker_device = args.speaker_device or runtime.config.speaker_device
    model = pick_value(
        cli_value=args.model,
        env_value=runtime.config.dashscope_realtime_model,
        profile_value=profile["model"],
    )
    url = pick_value(
        cli_value=args.url,
        env_value=runtime.config.dashscope_realtime_url,
        profile_value=profile["url"],
    )
    voice = pick_value(
        cli_value=args.voice,
        env_value=runtime.config.dashscope_realtime_voice,
        profile_value=profile["voice"],
    )
    input_rate = int(
        pick_value(
            cli_value=args.input_rate,
            env_value=None,
            profile_value=profile["input_rate"],
            cli_default=16000,
        )
    )
    output_rate = int(
        pick_value(
            cli_value=args.output_rate,
            env_value=None,
            profile_value=profile["output_rate"],
            cli_default=24000,
        )
    )
    frame_ms = int(
        pick_value(
            cli_value=args.frame_ms,
            env_value=None,
            profile_value=profile["frame_ms"],
            cli_default=100,
        )
    )
    vad = str(
        pick_value(
            cli_value=args.vad,
            env_value=None,
            profile_value=profile["vad"],
            cli_default="server_vad",
        )
    )
    vad_threshold = float(
        pick_value(
            cli_value=args.vad_threshold,
            env_value=None,
            profile_value=profile["vad_threshold"],
            cli_default=0.5,
        )
    )
    silence_ms = int(
        pick_value(
            cli_value=args.silence_ms,
            env_value=None,
            profile_value=profile["silence_ms"],
            cli_default=600,
        )
    )
    instructions = str(
        pick_value(
            cli_value=args.instructions,
            env_value=None,
            profile_value=profile["instructions"],
            cli_default=DEFAULT_INSTRUCTIONS,
        )
    )
    transcription_model = pick_value(
        cli_value=args.transcription_model,
        env_value=runtime.config.dashscope_realtime_transcription_model,
        profile_value=profile["transcription_model"],
    )
    latency_log_path = (
        Path(args.latency_log).expanduser()
        if args.latency_log
        else runtime.config.workspace_dir / ".voice" / "realtime_latency.jsonl"
    )
    latency_tracker = LatencyTracker(
        log_path=latency_log_path,
        profile=args.profile,
        model=model,
        voice=voice,
        mic_device=mic_device,
        speaker_device=speaker_device,
        input_rate=input_rate,
        output_rate=output_rate,
        vad=vad,
        silence_ms=silence_ms,
    )

    player = None
    if not args.no_playback:
        player = RawPcmPlayer(
            device=speaker_device,
            sample_rate=output_rate,
            on_first_write=lambda turn_id: latency_tracker.mark(
                "first_playback_write_at", turn_id=turn_id
            ),
        )

    started_at = time.perf_counter()
    first_audio_latencies: list[float] = []

    def on_audio_delta(pcm: bytes) -> None:
        turn_id = latency_tracker.add_audio(len(pcm))
        if player is not None:
            player.write(pcm, turn_id=turn_id)

    def on_event(event: dict) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "input_audio_buffer.speech_started":
            turn_id = latency_tracker.speech_started()
            print(f"\n[barge-in] speech started turn={turn_id}")
            if player is not None:
                player.interrupt()
        elif event_type == "input_audio_buffer.speech_stopped":
            latency_tracker.mark("speech_stopped_at")
            print("\n[vad] speech stopped")
        elif event_type == "conversation.item.input_audio_transcription.completed":
            transcript = event.get("transcript")
            if transcript:
                latency_tracker.set_transcript(str(transcript))
                print(f"\nYou: {transcript}")
        elif event_type == "response.created":
            latency_tracker.mark("response_created_at")
        elif event_type == "response.audio_transcript.delta":
            delta = event.get("delta")
            if delta:
                print(delta, end="", flush=True)
        elif event_type == "response.audio.done":
            latency_tracker.mark("audio_done_at")
            first_audio_latency = client.first_audio_seconds
            if first_audio_latency is not None:
                first_audio_latencies.append(first_audio_latency)
            summary = latency_tracker.finish(reason="audio_done")
            if summary is not None:
                print_latency_summary(summary)
            else:
                print()
        elif event_type == "response.done":
            latency_tracker.mark("response_done_at")
            summary = latency_tracker.finish(reason="response_done")
            if summary is not None and args.verbose:
                print_latency_summary(summary)
        elif event_type == "error":
            print(f"\n[error] {event.get('error', event)}", file=sys.stderr)
        elif args.verbose and event_type not in {"response.audio.delta"}:
            print(f"\n[event] {event_type}")

    config = DashScopeRealtimeConfig(
        api_key=runtime.config.dashscope_api_key,
        model=model,
        url=url,
        voice=voice,
        instructions=instructions,
        input_sample_rate=input_rate,
        output_sample_rate=output_rate,
        turn_detection_type=vad,
        vad_threshold=vad_threshold,
        silence_duration_ms=silence_ms,
        transcription_model=transcription_model,
    )
    client = DashScopeRealtimeClient(config, on_audio_delta=on_audio_delta, on_event=on_event)
    mic = ArecordStreamer(device=mic_device, sample_rate=input_rate, frame_ms=frame_ms)

    print("DashScope realtime voice loop started.")
    print(
        f"profile={args.profile} model={model} voice={voice} mic={mic_device}@{input_rate} "
        f"speaker={speaker_device}@{output_rate} vad={vad}/{silence_ms}ms"
    )
    print(f"Latency log: {latency_log_path}")

    try:
        client.connect()
        latency_tracker.mark_connected()
        print("Realtime session connected. Starting microphone stream.")
        print("Speak naturally. Press Ctrl+C to stop.")
        last_meter_at = time.perf_counter()
        meter_rms = 0
        meter_peak = 0
        mic.start()
        latency_tracker.mark_mic_started()
        for frame in mic.frames():
            if args.meter:
                frame_rms, frame_peak = pcm16_stats(frame)
                meter_rms = max(meter_rms, frame_rms)
                meter_peak = max(meter_peak, frame_peak)
                now = time.perf_counter()
                if now - last_meter_at >= 1:
                    print(f"\n[mic] rms={meter_rms} peak={meter_peak}")
                    meter_rms = 0
                    meter_peak = 0
                    last_meter_at = now
            client.append_audio(frame)
            if args.max_seconds and time.perf_counter() - started_at >= args.max_seconds:
                break
    except (DashScopeRealtimeError, RuntimeError) as exc:
        print(f"Realtime voice failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        mic.stop()
        client.close()
        if player is not None:
            player.close()
        if first_audio_latencies:
            avg = sum(first_audio_latencies) / len(first_audio_latencies)
            print(f"\nAverage first-audio latency: {avg:.3f}s over {len(first_audio_latencies)} turns.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDashScope realtime voice loop stopped.")
