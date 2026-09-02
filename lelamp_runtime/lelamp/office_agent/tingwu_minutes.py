from __future__ import annotations

import json
import re
from typing import Any
from urllib import request

def remove_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: remove_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [remove_none(item) for item in value]
    return value


def first_present(payload: dict[str, Any], keys: tuple[str, ...], *, default: Any = None) -> Any:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return default


class NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def extract_transcript_text(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    transcription = output.get("transcription") if isinstance(output.get("transcription"), dict) else {}
    translations = output.get("translations") if isinstance(output.get("translations"), dict) else {}
    ai_result = output.get("aiResult") if isinstance(output.get("aiResult"), dict) else {}
    candidates: list[Any] = [
        event.get("text"),
        event.get("transcript"),
        event.get("sentence"),
        event.get("result"),
        transcription.get("text"),
        transcription.get("sentence"),
        transcription.get("result"),
        transcription.get("words"),
        transcription.get("stashResult"),
        translations.get("text"),
        translations.get("sentence"),
        translations.get("words"),
        translations.get("translations"),
        ai_result.get("correction"),
        output.get("text"),
        output.get("sentence"),
        output.get("result"),
    ]
    legacy_output = event.get("output") if isinstance(event.get("output"), dict) else {}
    candidates.extend([legacy_output.get("text"), legacy_output.get("transcript"), legacy_output.get("sentence"), legacy_output.get("result")])
    for candidate in candidates:
        text = _text_from_candidate(candidate)
        if text:
            return text
    return ""


def _text_from_candidate(candidate: Any) -> str:
    if isinstance(candidate, str):
        return candidate.strip()
    if isinstance(candidate, dict):
        for key in ("text", "sentence", "transcript", "content", "paragraph", "paragraphText", "formalParagraphText", "summary", "result", "word", "value"):
            text = _text_from_candidate(candidate.get(key))
            if text:
                return text
        words = candidate.get("words")
        if isinstance(words, list):
            text = "".join(_text_from_candidate(item) for item in words).strip()
            if text:
                return text
        sentences = candidate.get("sentences")
        if isinstance(sentences, list):
            text = " ".join(_text_from_candidate(item) for item in sentences).strip()
            if text:
                return text
        translations = candidate.get("translations")
        if isinstance(translations, dict):
            text = " ".join(_text_from_candidate(item) for item in translations.values()).strip()
            if text:
                return text
        stash = candidate.get("stashResult")
        if isinstance(stash, dict):
            text = _text_from_candidate(stash)
            if text:
                return text
        nested_text = " ".join(_text_from_candidate(item) for item in candidate.values()).strip()
        if nested_text:
            return nested_text
    if isinstance(candidate, list):
        return " ".join(_text_from_candidate(item) for item in candidate).strip()
    return ""


def is_final_transcript(event: dict[str, Any]) -> bool:
    marker = str(event.get("type") or event.get("event") or event.get("status") or "").lower()
    if any(token in marker for token in ("sentence_end", "completed", "final")):
        return True
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    action = str(output.get("action") or "").lower()
    transcription = output.get("transcription") if isinstance(output.get("transcription"), dict) else {}
    translations = output.get("translations") if isinstance(output.get("translations"), dict) else {}
    legacy_output = event.get("output") if isinstance(event.get("output"), dict) else {}
    return bool(
        truthy_marker(event.get("final"))
        or truthy_marker(event.get("is_final"))
        or truthy_marker(legacy_output.get("final"))
        or truthy_marker(legacy_output.get("is_final"))
        or truthy_marker(transcription.get("sentenceEnd"))
        or truthy_marker(translations.get("sentenceEnd"))
        or action in {"ai-result", "speech-end"}
    )


def extract_speaker(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    transcription = output.get("transcription") if isinstance(output.get("transcription"), dict) else {}
    legacy_output = event.get("output") if isinstance(event.get("output"), dict) else {}
    candidates = [
        event.get("speaker"),
        event.get("speaker_id"),
        event.get("speakerId"),
        event.get("speakerID"),
        event.get("speakerName"),
        event.get("speakerLabel"),
        event.get("role"),
        event.get("role_id"),
        event.get("roleId"),
        event.get("channel"),
        event.get("channel_id"),
        event.get("channelId"),
        output.get("speaker"),
        output.get("speaker_id"),
        output.get("speakerId"),
        output.get("speakerID"),
        output.get("speakerName"),
        output.get("speakerLabel"),
        output.get("role"),
        output.get("role_id"),
        output.get("roleId"),
        output.get("channel"),
        output.get("channel_id"),
        output.get("channelId"),
        transcription.get("speaker"),
        transcription.get("speaker_id"),
        transcription.get("speakerId"),
        transcription.get("speakerID"),
        transcription.get("speakerName"),
        transcription.get("speakerLabel"),
        transcription.get("role"),
        transcription.get("role_id"),
        transcription.get("roleId"),
        transcription.get("channel"),
        transcription.get("channel_id"),
        transcription.get("channelId"),
        legacy_output.get("speaker"),
        legacy_output.get("speaker_id"),
        legacy_output.get("speakerId"),
        legacy_output.get("speakerName"),
    ]
    for candidate in candidates:
        speaker = normalize_speaker(candidate)
        if speaker:
            return speaker
    nested = find_nested_speaker(event)
    if nested:
        return nested
    return "Unknown"


def normalize_speaker(candidate: Any) -> str:
    if candidate in {None, ""}:
        return ""
    if isinstance(candidate, bool):
        return ""
    if isinstance(candidate, (int, float)):
        return f"Speaker {int(candidate)}"
    if isinstance(candidate, str):
        value = candidate.strip()
        if not value or value.lower() in {"unknown", "none", "null", "undefined"}:
            return ""
        if re.fullmatch(r"\d+(?:\.0+)?", value):
            return f"Speaker {int(float(value))}"
        return value
    if isinstance(candidate, dict):
        for key in (
            "speaker",
            "speaker_id",
            "speakerId",
            "speakerID",
            "speakerName",
            "speakerLabel",
            "role",
            "role_id",
            "roleId",
            "channel",
            "channel_id",
            "channelId",
        ):
            speaker = normalize_speaker(candidate.get(key))
            if speaker:
                return speaker
    return ""


def find_nested_speaker(value: Any, *, depth: int = 0) -> str:
    if depth > 6:
        return ""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized_key in {
                "speaker",
                "speakerid",
                "speakername",
                "speakerlabel",
                "role",
                "roleid",
                "channel",
                "channelid",
            }:
                speaker = normalize_speaker(item)
                if speaker:
                    return speaker
        for item in value.values():
            speaker = find_nested_speaker(item, depth=depth + 1)
            if speaker:
                return speaker
    elif isinstance(value, list):
        for item in value:
            speaker = find_nested_speaker(item, depth=depth + 1)
            if speaker:
                return speaker
    return ""


def extract_agent_event(event: dict[str, Any]) -> dict[str, Any] | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    action = str(output.get("action") or event.get("action") or event.get("type") or event.get("event") or "")
    agent_result = output.get("agent_result") if isinstance(output.get("agent_result"), dict) else {}
    commands = output.get("commands") if isinstance(output.get("commands"), list) else []
    if action != "agent_result" and not agent_result and not commands:
        return None
    command_items = [item for item in commands if isinstance(item, dict)]
    meeting_commands = [item for item in command_items if str(item.get("name") or "") == "meeting_state_change"]
    meeting_data_ids = [
        str((item.get("arguments") if isinstance(item.get("arguments"), dict) else {}).get("dataId") or "").strip()
        for item in meeting_commands
    ]
    return remove_none(
        {
            "timestamp": utc_now(),
            "type": action or "agent_result",
            "agent_id": str(agent_result.get("agentId") or output.get("agentId") or ""),
            "text": _text_from_candidate(agent_result) or _text_from_candidate(output.get("text")),
            "data_id": next((item for item in meeting_data_ids if item), ""),
            "meeting_state_commands": meeting_commands,
            "event": compact_event_payload(event),
        }
    )


def tingwu_feature_sections(payload: dict[str, Any]) -> list[str]:
    sections: list[str] = []
    section_specs = [
        ("Full Summary", ("FullSummary", "fullSummary", "ParagraphSummary", "paragraphSummary", "summary", "Summary")),
        ("Speaker Summary", ("ConversationalSummary", "conversationalSummary", "SpeakerSummary", "speakerSummary")),
        ("Key Information", ("KeyInformation", "keyInformation", "KeyInformations", "keyInformations", "KeySentences", "keySentences")),
        ("Questions And Answers", ("QuestionsAnswering", "questionsAnswering", "Questions", "questions", "QA", "qa")),
        ("Auto Chapters", ("AutoChapters", "autoChapters", "Chapters", "chapters")),
        ("Mind Map", ("MindMap", "mindMap")),
        ("PPT Extraction", ("PptExtraction", "pptExtraction", "PPTExtraction", "PPT")),
        ("Text Polish", ("TextPolish", "textPolish")),
        ("Custom Prompt", ("CustomPrompt", "customPrompt")),
        ("Translations", ("Translations", "translationsPathData", "translations")),
        ("Transcription", ("Transcription", "transcriptionPathData", "transcription")),
    ]
    for title, keys in section_specs:
        value = first_feature_value(payload, keys)
        text = feature_markdown(value)
        if text:
            sections.extend([f"## {title}", text, ""])
    while sections and sections[-1] == "":
        sections.pop()
    return sections


def first_feature_value(payload: Any, keys: tuple[str, ...]) -> Any:
    for item in minutes_candidate_objects(payload):
        value = first_present_case_insensitive(item, keys)
        if value is not None and value != "":
            return value
    return None


def feature_markdown(value: Any, *, depth: int = 0) -> str:
    if value is None or value == "":
        return ""
    if depth > 4:
        return json.dumps(value, ensure_ascii=False)[:4000]
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        items = [feature_markdown(item, depth=depth + 1).strip() for item in value]
        items = [item for item in items if item]
        if not items:
            return ""
        return "\n".join(f"- {item}" if "\n" not in item else f"- {item.replace(chr(10), chr(10) + '  ')}" for item in items[:30])
    if isinstance(value, dict):
        text = _text_from_candidate(value)
        if text and len(text) > 8:
            return text
        parts: list[str] = []
        for key, item in value.items():
            rendered = feature_markdown(item, depth=depth + 1).strip()
            if rendered:
                parts.append(f"- {key}: {rendered}" if "\n" not in rendered else f"- {key}:\n  {rendered.replace(chr(10), chr(10) + '  ')}")
        return "\n".join(parts[:30])
    return str(value)


def truthy_marker(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "final", "completed", "complete", "sentence_end"}
    return bool(value)


def normalize_minutes_payload(payload: dict[str, Any]) -> dict[str, Any]:
    text_blob = json.dumps(payload, ensure_ascii=False)
    candidates = minutes_candidate_objects(payload)
    summary = ""
    summary_source = ""
    decisions: list[str] = []
    action_items: list[str] = []
    for item in candidates:
        if not summary:
            summary_value, summary_key = first_present_case_insensitive_with_key(
                item,
                (
                    "fullSummary",
                    "FullSummary",
                    "full_summary",
                    "summaries",
                    "Summaries",
                    "paragraphSummary",
                    "ParagraphSummary",
                    "questionsAnswering",
                    "questionsAnsweringSummary",
                    "QuestionsAnswering",
                    "QuestionsAnsweringSummary",
                    "conversationalSummary",
                    "ConversationalSummary",
                    "abstract",
                    "Abstract",
                    "summaryMindMap",
                    "SummaryMindMap",
                ),
            )
            if summary_value is None and is_summary_container(item):
                summary_value, summary_key = first_present_case_insensitive_with_key(item, ("summary", "Summary"))
            summary = _text_from_candidate(summary_value)
            if summary:
                summary_source = summary_key
        decisions.extend(
            _list_from_candidate(
                first_present_case_insensitive(
                    item,
                    (
                        "decisions",
                        "decision",
                        "Decision",
                        "key_sentence",
                        "keySentences",
                        "KeySentences",
                        "keyInformation",
                        "KeyInformation",
                        "keyInformations",
                        "KeyInformations",
                        "key_information",
                        "conclusions",
                        "conclusion",
                        "Conclusion",
                        "meetingAssistance",
                        "MeetingAssistance",
                    ),
                )
            )
        )
        action_items.extend(
            _list_from_candidate(
                first_present_case_insensitive(
                    item,
                    (
                        "action_items",
                        "ActionItems",
                        "actionItems",
                        "actionItem",
                        "ActionItem",
                        "todo",
                        "Todo",
                        "todos",
                        "Todos",
                        "todoList",
                        "TodoList",
                        "tasks",
                        "Tasks",
                        "taskList",
                        "TaskList",
                        "actions",
                        "Actions",
                    ),
                )
            )
        )
    if not summary:
        summary = text_blob[:800]
        summary_source = "raw_payload"
    return {
        "summary": summary.strip(),
        "summary_source": summary_source,
        "structured_summary": summary_source != "raw_payload",
        "decisions": dedupe_strings(decisions),
        "action_items": dedupe_strings(action_items),
    }


def minutes_candidate_objects(payload: Any) -> list[dict[str, Any]]:
    roots: list[Any] = [payload]
    if isinstance(payload, dict):
        output = payload.get("output")
        if isinstance(output, dict):
            roots.append(output)
            for key in (
                "summarizationPathData",
                "meetingAssistancePathData",
                "autoChaptersPathData",
                "transcriptionPathData",
                "translationsPathData",
                "mindMapPathData",
                "pptExtractionPathData",
                "textPolishPathData",
                "customPromptPathData",
                "keyInformationPathData",
                "questionsAnsweringPathData",
                "conversationalPathData",
                "Summarization",
                "MeetingAssistance",
            ):
                value = first_present_case_insensitive(output, (key,))
                if value is not None:
                    roots.append(value)
    result: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            marker = id(value)
            if marker in seen:
                return
            seen.add(marker)
            result.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for root in roots:
        visit(root)
    return result


def is_summary_container(item: dict[str, Any]) -> bool:
    key_text = " ".join(str(key) for key in item.keys()).lower()
    return any(marker in key_text for marker in ("summary", "summarization", "abstract"))


def first_present_case_insensitive(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    value, _ = first_present_case_insensitive_with_key(item, keys)
    return value


def first_present_case_insensitive_with_key(item: dict[str, Any], keys: tuple[str, ...]) -> tuple[Any, str]:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key], key
    lowered = {str(key).lower(): (key, value) for key, value in item.items()}
    for key in keys:
        found = lowered.get(key.lower())
        if found is not None and found[1] is not None:
            return found[1], str(found[0])
    return None, ""


def _list_from_candidate(candidate: Any) -> list[str]:
    if candidate is None:
        return []
    if isinstance(candidate, str):
        parts = re.split(r"[\r\n]+|(?<=[。；;])\s*|[•●]", candidate)
        return [line.strip("- \t。；;").strip() for line in parts if line.strip("- \t。；;").strip()]
    if isinstance(candidate, list):
        values: list[str] = []
        for item in candidate:
            values.extend(_list_from_candidate(item))
        return values
    if isinstance(candidate, dict):
        for key in (
            "text",
            "Text",
            "content",
            "Content",
            "summary",
            "Summary",
            "title",
            "Title",
            "result",
            "Result",
            "description",
            "Description",
            "name",
            "Name",
            "sentence",
            "Sentence",
            "task",
            "Task",
            "action",
            "Action",
            "todo",
            "Todo",
        ):
            text = _text_from_candidate(first_present_case_insensitive(candidate, (key,)))
            if text:
                return [text]
        for key in ("items", "Items", "list", "List", "children", "Children", "sentences", "Sentences", "KeySentences", "Actions"):
            values = _list_from_candidate(first_present_case_insensitive(candidate, (key,)))
            if values:
                return values
        return [json.dumps(candidate, ensure_ascii=False)]
    return [str(candidate)]


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = value.strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result
