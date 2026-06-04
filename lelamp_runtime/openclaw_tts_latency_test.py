from __future__ import annotations

import argparse
from pathlib import Path

from lelamp.office_agent.dashscope_tts import DashScopeTTS
from lelamp.office_agent.runtime import build_runtime
from openclaw_voice import load_local_env


def main() -> None:
    load_local_env(Path(".env"))
    parser = argparse.ArgumentParser(description="DashScope TTS latency test without playback")
    parser.add_argument("text", nargs="?", default="我在，请说。")
    args = parser.parse_args()

    runtime = build_runtime()
    output_path = runtime.config.workspace_dir / ".assistant" / "tts_latency_test.wav"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tts = DashScopeTTS(
        api_key=runtime.config.dashscope_api_key,
        model=runtime.config.dashscope_tts_model,
        voice=runtime.config.dashscope_tts_voice,
        url=runtime.config.dashscope_tts_url,
    )
    print(tts.speak_with_stats(args.text, output_path))


if __name__ == "__main__":
    main()
