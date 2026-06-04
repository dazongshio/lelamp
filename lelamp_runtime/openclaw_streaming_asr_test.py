from __future__ import annotations

import argparse
from pathlib import Path

from lelamp.office_agent.dashscope_streaming_asr import DashScopeStreamingASR
from lelamp.office_agent.runtime import build_runtime
from openclaw_voice import load_local_env


def main() -> None:
    load_local_env(Path(".env"))
    parser = argparse.ArgumentParser(description="DashScope streaming ASR microphone test")
    parser.add_argument("--mic-device", default=None)
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--silence-ms", type=int, default=700)
    parser.add_argument("--speech-threshold", type=int, default=1200)
    args = parser.parse_args()

    runtime = build_runtime()
    mic_device = args.mic_device or runtime.config.mic_device
    output_path = runtime.config.workspace_dir / ".assistant" / "streaming_asr_test.wav"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    asr = DashScopeStreamingASR(
        api_key=runtime.config.dashscope_api_key,
        model=runtime.config.dashscope_asr_model,
        sample_rate=runtime.config.dashscope_asr_sample_rate,
    )
    print(f"Streaming ASR listening on {mic_device} for up to {args.seconds}s.")
    result = asr.listen_once(
        device=mic_device,
        max_seconds=args.seconds,
        language_hints=[args.language],
        silence_ms=args.silence_ms,
        speech_threshold=args.speech_threshold,
        save_path=output_path,
    )
    print(result)


if __name__ == "__main__":
    main()
