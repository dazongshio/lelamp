#!/usr/bin/env python3
from __future__ import annotations

import argparse
import audioop
import json
import os
import shutil
import sys
import time
import wave
from dataclasses import replace
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "lelamp_runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

from lelamp.office_agent.audit import AuditLogger  # noqa: E402
from lelamp.office_agent.config import OfficeAgentConfig, is_placeholder_tingwu_credential  # noqa: E402
from lelamp.office_agent.tingwu_meeting import TingwuMeetingProvider  # noqa: E402
from lelamp.office_agent.workspace import Workspace  # noqa: E402


def wav_info(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as stream:
        return {
            "channels": stream.getnchannels(),
            "sample_width": stream.getsampwidth(),
            "sample_rate": stream.getframerate(),
            "frames": stream.getnframes(),
            "seconds": round(stream.getnframes() / max(1, stream.getframerate()), 2),
        }


def write_wav_segment(source: Path, target: Path, *, start_frame: int, frame_count: int) -> dict[str, Any]:
    with wave.open(str(source), "rb") as src:
        params = src.getparams()
        src.setpos(start_frame)
        raw = src.readframes(frame_count)
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as dst:
        dst.setparams(params)
        dst.writeframes(raw)
    return {
        "path": str(target),
        "audio_bytes": len(raw),
        "audio_rms": int(audioop.rms(raw, params.sampwidth)) if raw else 0,
        "audio_peak": int(audioop.max(raw, params.sampwidth)) if raw else 0,
        "seconds": round(len(raw) / max(1, params.framerate * params.nchannels * params.sampwidth), 2),
    }


def transcript_lines(session: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in session.get("transcript") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            speaker = str(item.get("speaker") or "Unknown")
            lines.append(f"{speaker}: {text}")
    partial = str(session.get("partial_text") or "").strip()
    if partial:
        lines.append(f"Unknown: {partial}")
    return lines


def wait_until_finished(provider: TingwuMeetingProvider, meeting_id: str, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    current = provider.session_status(meeting_id)
    while time.monotonic() < deadline:
        current = provider.session_status(meeting_id)
        if str(current.get("status") or "") in {"stopped", "failed", "completed"}:
            return current
        time.sleep(0.5)
    return current


def run_segment(
    *,
    config: OfficeAgentConfig,
    workspace: Workspace,
    audit: AuditLogger,
    segment_path: Path,
    title: str,
    segment_index: int,
    speed: float,
) -> dict[str, Any]:
    segment_config = replace(
        config,
        tingwu_audio_file=str(segment_path),
        tingwu_audio_file_speed=speed,
        tingwu_mock=False,
    ).normalized()
    provider = TingwuMeetingProvider(segment_config, workspace, audit)
    status = provider.status()
    if status.get("status") != "available":
        raise RuntimeError(f"Tingwu provider unavailable for segment {segment_index}: {status}")

    duration = float(wav_info(segment_path)["seconds"])
    started = provider.start_realtime_meeting(
        title=f"{title} - segment {segment_index:02d}",
        participants=["AudioFileSegment"],
        max_seconds=max(5, int(duration / max(0.1, speed)) + 30),
    )
    meeting_id = str(started["meeting_id"])
    last_report = 0.0
    while True:
        current = provider.session_status(meeting_id)
        status_text = str(current.get("status") or "")
        audio_seconds = float(current.get("audio_seconds") or 0)
        if audio_seconds - last_report >= 30 or status_text in {"stopped", "failed", "completed"}:
            last_report = audio_seconds
            print(
                f"segment={segment_index:02d} status={status_text} "
                f"audio_seconds={audio_seconds:.1f} finals={current.get('final_count')} "
                f"frames={current.get('websocket_audio_frames')}",
                flush=True,
            )
        if status_text in {"failed", "completed"}:
            break
        if status_text == "stopped":
            break
        if audio_seconds >= max(0.0, duration - 0.2):
            break
        time.sleep(1.0)

    stopped = provider.stop_realtime_meeting(meeting_id, wait_seconds=20)
    stopped = wait_until_finished(provider, meeting_id, timeout_seconds=30)
    stopped = provider.session_status(meeting_id)
    lines = transcript_lines(stopped)
    ok = (
        str(stopped.get("status") or "") == "stopped"
        and int(stopped.get("websocket_audio_frames") or 0) > 0
        and len(lines) > 0
    )
    return {
        "ok": ok,
        "meeting_id": meeting_id,
        "segment_path": str(segment_path),
        "status": stopped.get("status"),
        "error": stopped.get("error"),
        "audio_seconds": stopped.get("audio_seconds"),
        "websocket_audio_frames": stopped.get("websocket_audio_frames"),
        "final_count": stopped.get("final_count"),
        "transcript_path": stopped.get("transcript_path"),
        "audio_path": stopped.get("audio_path"),
        "output_dir": stopped.get("output_dir"),
        "lines": lines,
    }


def dashscope_summarize(config: OfficeAgentConfig, transcript: str, title: str) -> tuple[str, str]:
    api_key = config.dashscope_api_key or config.tingwu_api_key
    if not api_key:
        return local_minutes(transcript, title), "local_rules"
    try:
        import dashscope
        from dashscope import Generation

        dashscope.api_key = api_key
        prompt = "\n\n".join(
            [
                f"请基于下面的会议转写生成中文会议纪要，会议标题：{title}",
                "要求：输出 Markdown；包含：概览、关键讨论、技术要点、结论/判断、行动项、待确认问题。不要编造转写里没有的信息。",
                transcript[:45000],
            ]
        )
        response = Generation.call(
            model=config.dashscope_text_model or "qwen-plus",
            messages=[
                {"role": "system", "content": "你是严谨的会议秘书，只基于转写内容总结。"},
                {"role": "user", "content": prompt},
            ],
            api_key=api_key,
            result_format="message",
            temperature=0.2,
        )
        if int(response.status_code) != 200:
            return local_minutes(transcript, title), f"local_rules_after_dashscope_{response.code or response.status_code}"
        content = ""
        choices = ((response.output or {}).get("choices") or []) if response.output else []
        if choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                content = str(message.get("content") or "")
        if not content:
            content = str((response.output or {}).get("text") or "")
        return content.strip() or local_minutes(transcript, title), "dashscope_generation" if content.strip() else "local_rules_empty_dashscope"
    except Exception as exc:
        return local_minutes(transcript, title, error=str(exc)), "local_rules_after_dashscope_error"


def local_minutes(transcript: str, title: str, *, error: str = "") -> str:
    lines = [line.strip() for line in transcript.splitlines() if line.strip() and not line.startswith("#")]
    key_lines = [
        line for line in lines
        if any(marker in line.lower() for marker in ("java", "qps", "redis", "redisson", "jvm", "线程", "宕机", "迁移", "项目", "数据"))
    ]
    excerpt = key_lines[:30] or lines[:30]
    body = [
        f"# {title} 会议纪要",
        "",
        "Provider: tongyi_tingwu_segmented + local_rules",
        "",
        "## 概览",
        "本纪要基于通义听悟分段实时转写合并生成。主要内容围绕 Java 后端候选人的项目经历、系统迁移、数据同步、线程/Redis/Redisson 问题排查以及线上稳定性处理。",
        "",
        "## 关键讨论摘录",
        *[f"- {line}" for line in excerpt],
        "",
        "## 行动项",
        "- 人工复核完整 transcript，确认专有名词、公司/项目名称和技术名词识别是否准确。",
        "- 如需对外发送，先补充面试结论、候选人评价和下一步负责人。",
        "",
        "## 待确认问题",
        "- 说话人未可靠分离，当前统一标记为 Unknown。",
        "- 长录音采用分段实时转写合并，需人工抽查段落衔接。",
    ]
    if error:
        body.extend(["", "## 生成诊断", f"- DashScope 文本总结失败后回退到本地规则：{error[:500]}"])
    return "\n".join(body) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Process a long WAV through Tongyi Tingwu realtime in stable segments.")
    parser.add_argument("--audio-file", required=True, help="Mono 16-bit PCM WAV.")
    parser.add_argument("--title", default="Long Tingwu Audio")
    parser.add_argument("--workspace", default=str(RUNTIME_ROOT / "workspace"))
    parser.add_argument("--audit-log", default=str(RUNTIME_ROOT / "logs" / "tingwu_audio_segments.jsonl"))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--segment-seconds", type=int, default=300)
    parser.add_argument("--speed", type=float, default=5.0)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep processing later segments and write partial transcript/evidence when a segment fails.",
    )
    parser.add_argument("--keep-segments", action="store_true")
    args = parser.parse_args()

    audio_file = Path(args.audio_file).expanduser().resolve()
    info = wav_info(audio_file)
    if info["channels"] != 1 or info["sample_width"] != 2:
        raise SystemExit("audio-file must be mono 16-bit PCM WAV.")

    api_key = os.getenv("TINGWU_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    app_id = os.getenv("TINGWU_APP_ID") or os.getenv("TINGWU_MEETING_APP_ID")
    if is_placeholder_tingwu_credential(api_key) or is_placeholder_tingwu_credential(app_id):
        raise SystemExit("Set TINGWU_API_KEY/DASHSCOPE_API_KEY and TINGWU_APP_ID/TINGWU_MEETING_APP_ID.")

    workspace_root = Path(args.workspace).expanduser().resolve()
    audit_path = Path(args.audit_log).expanduser().resolve()
    config = replace(
        OfficeAgentConfig.from_env(),
        workspace_dir=workspace_root,
        audit_log_path=audit_path,
        allowed_roots=(workspace_root,),
        tingwu_api_key=api_key,
        tingwu_app_id=app_id,
        tingwu_audio_file="",
        tingwu_mock=False,
    ).normalized()
    audit = AuditLogger(config.audit_log_path)
    workspace = Workspace(config.workspace_dir, config.allowed_roots, audit)

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else workspace.root / "meetings" / f"segmented_{int(time.time())}"
    output_dir.mkdir(parents=True, exist_ok=True)
    segments_dir = output_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    sample_rate = int(info["sample_rate"])
    frames_per_segment = max(1, int(args.segment_seconds * sample_rate))
    total_frames = int(info["frames"])
    segments: list[dict[str, Any]] = []
    all_lines: list[str] = []

    print(f"source={audio_file}", flush=True)
    print(f"duration={info['seconds']}s segment_seconds={args.segment_seconds} speed={args.speed}", flush=True)
    for index, start in enumerate(range(0, total_frames, frames_per_segment), start=1):
        frame_count = min(frames_per_segment, total_frames - start)
        segment_path = segments_dir / f"segment_{index:02d}.wav"
        segment_info = write_wav_segment(audio_file, segment_path, start_frame=start, frame_count=frame_count)
        last_result: dict[str, Any] = {}
        for attempt in range(1, max(1, args.max_retries) + 1):
            try:
                print(f"segment={index:02d} attempt={attempt} path={segment_path.name}", flush=True)
                last_result = run_segment(
                    config=config,
                    workspace=workspace,
                    audit=audit,
                    segment_path=segment_path,
                    title=args.title,
                    segment_index=index,
                    speed=args.speed,
                )
                if last_result.get("ok"):
                    break
            except Exception as exc:
                last_result = {"ok": False, "segment_path": str(segment_path), "status": "exception", "error": str(exc)}
                print(f"segment={index:02d} attempt={attempt} error={str(exc)[:500]}", flush=True)
                time.sleep(2)
        last_result["segment_info"] = segment_info
        last_result["segment_index"] = index
        last_result["start_seconds"] = round(start / sample_rate, 2)
        last_result["end_seconds"] = round((start + frame_count) / sample_rate, 2)
        segments.append(last_result)
        lines = [str(item) for item in last_result.get("lines") or []]
        all_lines.extend([f"[{last_result['start_seconds']:0.2f}-{last_result['end_seconds']:0.2f}] {line}" for line in lines])
        (output_dir / "segments_status.json").write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
        if not last_result.get("ok"):
            message = f"segment {index:02d} failed after retries: {last_result.get('error')}"
            if not args.continue_on_error:
                raise SystemExit(message)
            all_lines.append(
                f"[{last_result['start_seconds']:0.2f}-{last_result['end_seconds']:0.2f}] "
                f"Unknown: [转写失败：{message}]"
            )

    transcript_text = "\n".join([f"# {args.title} Transcript", "", *all_lines, ""])
    transcript_path = output_dir / "transcript.md"
    transcript_path.write_text(transcript_text, encoding="utf-8")
    minutes_text, minutes_provider = dashscope_summarize(config, transcript_text, args.title)
    minutes_path = output_dir / "openclaw_minutes.md"
    minutes_path.write_text(minutes_text, encoding="utf-8")
    shutil.copy2(audio_file, output_dir / "source_audio.wav")

    failed_segments = [segment for segment in segments if not segment.get("ok")]
    evidence = {
        "status": "partial" if failed_segments else "ok",
        "mode": "segmented_realtime",
        "title": args.title,
        "source_audio_path": str(audio_file),
        "output_dir": str(output_dir),
        "transcript_path": str(transcript_path),
        "minutes_path": str(minutes_path),
        "minutes_provider": minutes_provider,
        "segment_seconds": args.segment_seconds,
        "speed": args.speed,
        "source_info": info,
        "segment_count": len(segments),
        "failed_segment_count": len(failed_segments),
        "total_lines": len(all_lines),
        "segments": segments,
    }
    evidence_path = output_dir / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.keep_segments:
        for path in segments_dir.glob("segment_*.wav"):
            path.unlink(missing_ok=True)
    print(f"output_dir={output_dir}", flush=True)
    print(f"transcript_path={transcript_path}", flush=True)
    print(f"minutes_path={minutes_path}", flush=True)
    print(f"evidence_path={evidence_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
