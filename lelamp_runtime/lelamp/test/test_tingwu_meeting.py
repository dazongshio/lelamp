import sys
import types


if "audioop" not in sys.modules:
    audioop = types.ModuleType("audioop")
    audioop.rms = lambda frame, sample_width: 0
    audioop.max = lambda frame, sample_width: 0
    sys.modules["audioop"] = audioop

if "dashscope.multimodal.tingwu.tingwu_realtime" not in sys.modules:
    dashscope = types.ModuleType("dashscope")
    multimodal = types.ModuleType("dashscope.multimodal")
    tingwu = types.ModuleType("dashscope.multimodal.tingwu")
    tingwu_realtime = types.ModuleType("dashscope.multimodal.tingwu.tingwu_realtime")

    class TingWuRealtime:
        pass

    class TingWuRealtimeCallback:
        pass

    tingwu_realtime.TingWuRealtime = TingWuRealtime
    tingwu_realtime.TingWuRealtimeCallback = TingWuRealtimeCallback
    sys.modules["dashscope"] = dashscope
    sys.modules["dashscope.multimodal"] = multimodal
    sys.modules["dashscope.multimodal.tingwu"] = tingwu
    sys.modules["dashscope.multimodal.tingwu.tingwu_realtime"] = tingwu_realtime


from lelamp.office_agent.tingwu_meeting import extract_speaker


def test_extract_speaker_from_transcription_speaker_id():
    event = {
        "payload": {
            "output": {
                "action": "recognize-result",
                "transcription": {
                    "text": "你好",
                    "speakerId": 2,
                },
            }
        }
    }

    assert extract_speaker(event) == "Speaker 2"


def test_extract_speaker_from_nested_sentence_role_id():
    event = {
        "payload": {
            "output": {
                "action": "recognize-result",
                "transcription": {
                    "sentences": [
                        {
                            "text": "继续测试",
                            "roleId": "3",
                        }
                    ]
                },
            }
        }
    }

    assert extract_speaker(event) == "Speaker 3"
