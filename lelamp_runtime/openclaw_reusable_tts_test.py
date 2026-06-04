from __future__ import annotations

import argparse
from pathlib import Path

from lelamp.office_agent.dashscope_tts import ReusableDashScopeTTS
from lelamp.office_agent.runtime import build_runtime
from openclaw_voice import load_local_env


def main() -> None:
    load_local_env(Path(".env"))
    parser = argparse.ArgumentParser(description="DashScope reusable TTS latency test without playback")
    parser.add_argument("text", nargs="?", default="我在，请说。")
    parser.add_argument("--count", type=int, default=2)
    args = parser.parse_args()

    runtime = build_runtime()
    base_dir = runtime.config.workspace_dir / ".assistant"
    base_dir.mkdir(parents=True, exist_ok=True)
    tts = ReusableDashScopeTTS(
        api_key=runtime.config.dashscope_api_key,
        model=runtime.config.dashscope_tts_model,
        voice=runtime.config.dashscope_tts_voice,
        url=runtime.config.dashscope_tts_url,
    )
    try:
        print("prewarm", tts.start())
        for index in range(args.count):
            path = base_dir / f"reusable_tts_{index + 1:02d}.wav"
            print(tts.speak_with_stats(args.text, path))
    finally:
        tts.close()


if __name__ == "__main__":
    main()
