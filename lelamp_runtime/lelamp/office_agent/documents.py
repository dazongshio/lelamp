from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from .audit import AuditLogger
from .config import OfficeAgentConfig
from .llm import LLMError, ResponsesLLM, ResponsesLLMConfig
from .workspace import Workspace
from .utils import clamp_text, safe_filename

TEXT_WORKFLOW_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".log",
    ".html",
    ".xml",
}

OOXML_WORKFLOW_SUFFIXES = {".docx", ".pptx", ".xlsx"}
BINARY_DOCUMENT_SUFFIXES = {".pdf", *OOXML_WORKFLOW_SUFFIXES}
DOCUMENT_WORKFLOW_SUFFIXES = {*TEXT_WORKFLOW_SUFFIXES, *BINARY_DOCUMENT_SUFFIXES}


class DocumentExtractionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: str = "backend_missing",
        backend: str = "missing",
        details: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.backend = backend
        self.details = details or {}

    def as_payload(self, *, filename: str) -> dict[str, object]:
        return {
            "status": self.status,
            "message": str(self),
            "file_path": filename,
            "document_text_backend": self.backend,
            "details": self.details,
        }


@dataclass(frozen=True)
class DocumentAnalysis:
    filename: str
    chars: int
    lines: int
    words: int
    headings: list[str]
    key_value_pairs: dict[str, str]
    risk_markers: list[str]


class DocumentService:
    RISK_PATTERNS = (
        "赔偿",
        "违约",
        "终止",
        "独家",
        "保密",
        "不可撤销",
        "indemnity",
        "liability",
        "termination",
        "exclusive",
        "confidential",
        "non-compete",
    )

    def __init__(self, workspace: Workspace, audit: AuditLogger, config: OfficeAgentConfig | None = None):
        self.workspace = workspace
        self.audit = audit
        self.config = config

    def extraction_status(self) -> dict[str, object]:
        pdf_backend = "pdftotext" if shutil.which("pdftotext") else ("pypdf" if _module_available("pypdf") else "backend_missing")
        return {
            "text": "available",
            "pdf": "available" if pdf_backend != "backend_missing" else "backend_missing",
            "pdf_backend": pdf_backend,
            "docx": "available",
            "docx_backend": "ooxml",
            "pptx": "available",
            "pptx_backend": "ooxml",
            "xlsx": "available",
            "xlsx_backend": "ooxml",
            "supported_suffixes": sorted(DOCUMENT_WORKFLOW_SUFFIXES),
        }

    def analyze_text_file(self, filename: str) -> dict[str, object]:
        source = self.extract_document_text(filename, max_chars=200000)
        text = str(source["text"])
        analysis = self._analyze_text(filename, text)
        path = self.workspace.write_json(
            safe_filename(Path(filename).stem, suffix="_analysis.json"),
            analysis.__dict__,
            action="document.analysis_write",
        )
        payload = {**analysis.__dict__, "analysis_path": str(path), "source": source["source"]}
        self.audit.record("document.analyze", target=filename, details=payload)
        return payload

    def summarize_text_file(self, filename: str, style: str = "brief") -> dict[str, object]:
        source = self.extract_document_text(filename, max_chars=60000)
        text = str(source["text"])
        sentences = self._split_sentences(text)
        if style == "detailed":
            selected = sentences[:12]
        elif style == "outline":
            selected = sentences[:8]
        else:
            selected = sentences[:5]

        summary_lines = [f"# Summary: {filename}", "", f"Style: {style}", ""]
        if style == "outline":
            summary_lines.append("## Outline")
            summary_lines.extend(f"- {sentence}" for sentence in selected)
        else:
            summary_lines.append("## Key Points")
            summary_lines.extend(f"- {sentence}" for sentence in selected)

        analysis = self._analyze_text(filename, text)
        if analysis.risk_markers:
            summary_lines.extend(["", "## Risk Markers"])
            summary_lines.extend(f"- {marker}" for marker in analysis.risk_markers)

        path = self.workspace.write_text(
            safe_filename(Path(filename).stem, suffix="_summary.md"),
            "\n".join(summary_lines),
            action="document.summary_write",
        )
        payload = {
            "summary_path": str(path),
            "points": selected,
            "risk_markers": analysis.risk_markers,
            "source": source["source"],
        }
        self.audit.record("document.summarize", target=filename, details=payload)
        return payload

    def compare_text_files(self, left_filename: str, right_filename: str) -> dict[str, object]:
        left_source = self.extract_document_text(left_filename, max_chars=120000)
        right_source = self.extract_document_text(right_filename, max_chars=120000)
        left_text = str(left_source["text"])
        right_text = str(right_source["text"])
        left_lines = {line.strip() for line in left_text.splitlines() if line.strip()}
        right_lines = {line.strip() for line in right_text.splitlines() if line.strip()}

        only_left = sorted(left_lines - right_lines)[:100]
        only_right = sorted(right_lines - left_lines)[:100]
        common_count = len(left_lines & right_lines)
        report = {
            "left": left_filename,
            "right": right_filename,
            "common_line_count": common_count,
            "only_left": only_left,
            "only_right": only_right,
            "sources": [left_source["source"], right_source["source"]],
        }
        path = self.workspace.write_json(
            safe_filename(f"{Path(left_filename).stem}_vs_{Path(right_filename).stem}", suffix=".json"),
            report,
            action="document.compare_write",
        )
        report["compare_path"] = str(path)
        self.audit.record("document.compare", details=report)
        return report

    def extract_table_from_text(self, filename: str) -> dict[str, object]:
        if self.config and self.config.openai_api_key:
            return self.extract_key_data_table_with_api(filename)
        return self.extract_key_data_table_locally(filename)

    def create_report_outline(self, filenames: list[str], topic: str) -> dict[str, object]:
        if self.config and self.config.openai_api_key:
            return self.create_report_outline_with_api(filenames, topic)
        return self.create_report_outline_locally(filenames, topic)

    def create_report_outline_locally(self, filenames: list[str], topic: str) -> dict[str, object]:
        sources = self._read_sources(filenames, max_chars_per_file=60000)
        source_text = "\n\n".join(source["text"] for source in sources)
        analysis = self._analyze_text(topic, source_text)
        sentences = self._split_sentences(source_text)
        key_points = _dedupe_keep_order([*analysis.headings[:8], *sentences[:10]])[:12]
        facts = [f"{key}: {value}" for key, value in analysis.key_value_pairs.items()][:12]
        action_items = _lines_matching(source_text, ("待办", "todo", "action", "负责", "follow up"))[:12]
        decisions = _lines_matching(source_text, ("决定", "决策", "确认", "agreed", "decision", "decide"))[:12]
        slide_outline = _build_slide_outline(topic, key_points, decisions, action_items, analysis.risk_markers)
        lines = [
            f"# {topic} 汇报提纲",
            "",
            "Provider: local_rules",
            "",
            "## 背景/目的",
            f"- 基于 {len(sources)} 个用户授权文档生成本地规则提纲。",
            f"- 文档总字符数：{sum(len(source['text']) for source in sources)}。",
            "",
            "## 核心结论",
            *([f"- {item}" for item in key_points[:6]] or ["- 待确认：文档中没有足够清晰的结论句。"]),
            "",
            "## 关键事实",
            *([f"- {item}" for item in facts] or ["- 待确认：未识别到 key:value 形式的事实。"]),
            "",
            "## 已识别决策",
            *([f"- {item}" for item in decisions] or ["- 待确认：未识别到明确决策。"]),
            "",
            "## 建议行动",
            *([f"- {item}" for item in action_items] or ["- 补充负责人、截止时间和验收标准。"]),
            "",
            "## 风险与待确认",
            *([f"- {item}" for item in analysis.risk_markers] or ["- 待确认：未命中文本风险词。"]),
            "",
            "## PPT 页级提纲",
            *[f"- {item}" for item in slide_outline],
            "",
            "## 来源",
            *[f"- {source['filename']} ({source.get('backend', 'unknown')})" for source in sources],
            "",
        ]
        path = self.workspace.write_text(
            safe_filename(topic, default="report", suffix="_outline.md"),
            "\n".join(lines),
            action="document.report_outline.local",
        )
        payload = {
            "status": "completed",
            "outline_path": str(path),
            "sources": filenames,
            "provider": "local_rules",
            "model": "local_rules",
            "chars": len("\n".join(lines)),
            "points": key_points,
            "risk_markers": analysis.risk_markers,
            "decisions": decisions,
            "action_items": action_items,
            "message": "本地规则已生成可审查汇报提纲；配置 OPENAI_API_KEY 后可启用更强的 API 改写。",
        }
        self.audit.record("document.create_report_outline", details=payload)
        return payload

    def create_report_outline_with_api(self, filenames: list[str], topic: str) -> dict[str, object]:
        llm = self._llm()
        sources = self._read_sources(filenames, max_chars_per_file=60000)
        prompt = "\n\n".join(
            [
                f"请把以下用户授权的文档整理成一份可直接用于汇报的中文提纲。主题：{topic}",
                "要求：",
                "- 输出 Markdown。",
                "- 包含：汇报标题、背景/目的、核心结论、关键事实、风险与待确认问题、建议行动、可放入 PPT 的页级提纲。",
                "- 不要编造来源没有的信息；无法判断时写“待确认”。",
                "- 尽量把结论写具体，不要只给空模板。",
                self._format_sources(sources),
            ]
        )
        try:
            outline = llm.complete(
                instructions="你是严谨的本地办公文档分析助手。只基于用户授权文档生成汇报提纲。",
                user_input=prompt,
                context={"source_files": filenames, "task": "report_outline"},
                timeout=120,
            )
        except LLMError as exc:
            payload = {
                "status": "backend_missing",
                "message": str(exc),
                "outline_path": "",
                "sources": filenames,
                "provider": "ResponsesLLM",
            }
            self.audit.record("document.create_report_outline", status="blocked", details=payload)
            return payload

        path = self.workspace.write_text(
            safe_filename(topic, default="report", suffix="_outline.md"),
            outline,
            action="document.report_outline",
        )
        payload = {
            "status": "completed",
            "outline_path": str(path),
            "sources": filenames,
            "provider": "ResponsesLLM",
            "model": self.config.openai_model if self.config else "",
            "chars": len(outline),
        }
        self.audit.record("document.create_report_outline", details=payload)
        return payload

    def extract_key_data_table_with_api(self, filename: str) -> dict[str, object]:
        llm = self._llm()
        source = self.extract_document_text(filename, max_chars=100000)
        text = str(source["text"])
        prompt = "\n\n".join(
            [
                "请从以下用户授权文档中提取最适合汇报或复盘的关键数据，并输出严格 CSV。",
                "要求：",
                "- 第一行必须是表头。",
                "- 建议表头：类别, 指标/事项, 数值/结论, 时间/范围, 责任人/主体, 来源依据, 备注。",
                "- 只输出 CSV 内容，不要输出 Markdown 代码块或解释。",
                "- 如果没有明确数值，也可以提取关键事实/行动项；没有依据的单元格留空或写“待确认”。",
                f"文件名：{filename}",
                "文档内容：",
                text,
            ]
        )
        try:
            csv_text = llm.complete(
                instructions="你是严谨的本地办公数据抽取助手。只输出可被 csv.reader 解析的 CSV。",
                user_input=prompt,
                context={"source_file": filename, "task": "key_data_table"},
                timeout=120,
            )
        except LLMError as exc:
            payload = {
                "status": "backend_missing",
                "message": str(exc),
                "table_path": "",
                "rows": 0,
                "provider": "ResponsesLLM",
            }
            self.audit.record("document.table_extract", status="blocked", target=filename, details=payload)
            return payload

        csv_text = _strip_code_fence(csv_text)
        rows = list(csv.reader(io.StringIO(csv_text)))
        path = self.workspace.write_text(
            safe_filename(Path(filename).stem, suffix="_key_data.csv"),
            csv_text,
            action="document.key_data_table_write",
        )
        payload = {
            "status": "completed" if len(rows) > 1 else "backend_missing",
            "table_path": str(path),
            "rows": max(0, len(rows) - 1),
            "columns": rows[0] if rows else [],
            "provider": "ResponsesLLM",
            "model": self.config.openai_model if self.config else "",
            "source": filename,
            "document_source": source["source"],
        }
        self.audit.record("document.extract_key_data_table", target=filename, details=payload)
        return payload

    def extract_key_data_table_locally(self, filename: str) -> dict[str, object]:
        source = self.extract_document_text(filename, max_chars=100000)
        text = str(source["text"])
        analysis = self._analyze_text(filename, text)
        rows: list[list[str]] = [["类别", "指标/事项", "数值/结论", "时间/范围", "责任人/主体", "来源依据", "备注"]]
        for key, value in analysis.key_value_pairs.items():
            rows.append(["键值信息", key, value, "", "", key, "local_rules"])
        for line in _lines_matching(text, ("决定", "决策", "确认", "agreed", "decision", "decide")):
            rows.append(["决策", "会议/文档决策", line, "", _guess_owner(line), line, "local_rules"])
        for line in _lines_matching(text, ("待办", "todo", "action", "负责", "follow up")):
            rows.append(["行动项", "待办事项", line, _guess_time_scope(line), _guess_owner(line), line, "local_rules"])
        for line in _lines_matching(text, ("金额", "费用", "预算", "收入", "成本", "price", "cost", "revenue", "$", "¥", "￥", "%")):
            rows.append(["关键数据", "数值/指标", line, _guess_time_scope(line), _guess_owner(line), line, "local_rules"])
        for marker in analysis.risk_markers:
            rows.append(["风险", marker, f"文档命中风险词：{marker}", "", "", marker, "local_rules"])

        rows = _dedupe_csv_rows(rows)
        if len(rows) == 1:
            sentences = self._split_sentences(text)[:8]
            for sentence in sentences:
                rows.append(["关键事实", "文本要点", sentence, _guess_time_scope(sentence), _guess_owner(sentence), sentence, "local_rules"])

        csv_stream = io.StringIO()
        writer = csv.writer(csv_stream)
        writer.writerows(rows)
        csv_text = csv_stream.getvalue()
        path = self.workspace.write_text(
            safe_filename(Path(filename).stem, suffix="_key_data.csv"),
            csv_text,
            action="document.key_data_table_write.local",
        )
        payload = {
            "status": "completed" if len(rows) > 1 else "backend_missing",
            "table_path": str(path),
            "rows": max(0, len(rows) - 1),
            "columns": rows[0],
            "provider": "local_rules",
            "model": "local_rules",
            "source": filename,
            "document_source": source["source"],
            "message": "本地规则已提取关键数据表；配置 OPENAI_API_KEY 后可启用更强的 API 抽取。",
        }
        self.audit.record("document.extract_key_data_table", target=filename, details=payload)
        return payload

    def extract_document_text(self, filename: str, *, max_chars: int = 120000) -> dict[str, object]:
        path = self.workspace.resolve_workspace_file(filename)
        suffix = path.suffix.lower()
        try:
            if suffix in TEXT_WORKFLOW_SUFFIXES:
                text = self.workspace.read_text(filename, max_chars=max_chars)
                return {
                    "text": text,
                    "source": {
                        "filename": filename,
                        "workspace_name": filename,
                        "backend": "text",
                        "mime_family": "text",
                        "chars": len(text),
                        "truncated": text.endswith("\n[TRUNCATED]"),
                    },
                }
            if suffix == ".pdf":
                text, backend = self._extract_pdf_text(path, max_chars=max_chars)
            elif suffix == ".docx":
                text, backend = self._extract_docx_text(path, max_chars=max_chars), "ooxml_docx"
            elif suffix == ".pptx":
                text, backend = self._extract_pptx_text(path, max_chars=max_chars), "ooxml_pptx"
            elif suffix == ".xlsx":
                text, backend = self._extract_xlsx_text(path, max_chars=max_chars), "ooxml_xlsx"
            else:
                raise DocumentExtractionError(
                    "Unsupported document type for text extraction.",
                    backend="unsupported",
                    details={"suffix": suffix, "supported_suffixes": sorted(DOCUMENT_WORKFLOW_SUFFIXES)},
                )
        except DocumentExtractionError:
            raise
        except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise DocumentExtractionError(
                "Document parser failed before readable text could be extracted.",
                backend=f"{suffix.lstrip('.') or 'document'}_parser",
                details={"suffix": suffix, "error": str(exc)[:500]},
            ) from exc
        text = text.strip()
        if not text:
            raise DocumentExtractionError(
                "Document parser returned no readable text.",
                backend=backend,
                details={"suffix": suffix},
            )
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n[TRUNCATED]"
        source = {
            "filename": filename,
            "workspace_name": str(path.relative_to(self.workspace.root.resolve())),
            "backend": backend,
            "mime_family": suffix.lstrip("."),
            "chars": len(text),
            "truncated": truncated,
        }
        self.audit.record("document.extract_text", target=filename, details=source)
        return {"text": text, "source": source}

    def _llm(self) -> ResponsesLLM:
        if self.config is None:
            raise LLMError("OfficeAgentConfig is not available.")
        return ResponsesLLM(
            ResponsesLLMConfig(
                api_key=self.config.openai_api_key,
                base_url=self.config.openai_base_url,
                model=self.config.openai_model,
                reasoning_effort="low",
            )
        )

    def _read_sources(self, filenames: list[str], *, max_chars_per_file: int) -> list[dict[str, str]]:
        sources: list[dict[str, str]] = []
        for filename in filenames:
            extracted = self.extract_document_text(filename, max_chars=max_chars_per_file)
            source = extracted["source"] if isinstance(extracted.get("source"), dict) else {}
            sources.append(
                {
                    "filename": filename,
                    "text": str(extracted.get("text") or ""),
                    "backend": str(source.get("backend") or "unknown"),
                }
            )
        return sources

    def _format_sources(self, sources: list[dict[str, str]]) -> str:
        blocks = []
        for source in sources:
            blocks.append(f"## Source: {source['filename']} ({source.get('backend', 'unknown')})\n{source['text']}")
        return "\n\n".join(blocks)

    def _analyze_text(self, filename: str, text: str) -> DocumentAnalysis:
        lines = text.splitlines()
        headings = [
            line.strip("# ").strip()
            for line in lines
            if line.strip().startswith("#") or re.match(r"^\d+(\.\d+)*\s+", line.strip())
        ][:50]
        key_value_pairs: dict[str, str] = {}
        for line in lines:
            if ":" in line and len(key_value_pairs) < 50:
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()
                if 1 <= len(key) <= 80 and value:
                    key_value_pairs[key] = clamp_text(value, 500)

        lower_text = text.lower()
        risk_markers = [pattern for pattern in self.RISK_PATTERNS if pattern.lower() in lower_text]
        words = len(re.findall(r"\w+", text))
        return DocumentAnalysis(
            filename=filename,
            chars=len(text),
            lines=len(lines),
            words=words,
            headings=headings,
            key_value_pairs=key_value_pairs,
            risk_markers=risk_markers,
        )

    def _split_sentences(self, text: str) -> list[str]:
        candidates = re.split(r"(?<=[。！？.!?])\s+|\n+", text)
        return [clamp_text(item.strip(), 500) for item in candidates if item.strip()][:80]

    def _extract_pdf_text(self, path: Path, *, max_chars: int) -> tuple[str, str]:
        pdftotext = shutil.which("pdftotext")
        if pdftotext:
            with tempfile.NamedTemporaryFile(suffix=".txt") as output:
                try:
                    subprocess.run(
                        [pdftotext, "-layout", "-enc", "UTF-8", "-q", str(path), output.name],
                        check=True,
                        timeout=60,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                    )
                    return Path(output.name).read_text(encoding="utf-8", errors="replace")[: max_chars + 1], "pdftotext"
                except (OSError, subprocess.SubprocessError) as exc:
                    self.audit.record("document.pdf_extract", status="blocked", target=str(path), details={"backend": "pdftotext", "error": str(exc)[:500]})
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError as exc:
            raise DocumentExtractionError(
                "PDF text extraction requires pdftotext or the pypdf package.",
                backend="pdf_backend_missing",
                details={"install_hint": "Install poppler-utils or uv add pypdf."},
            ) from exc

        try:
            reader = PdfReader(str(path))
            parts: list[str] = []
            total = 0
            for page in reader.pages:
                page_text = page.extract_text() or ""
                if page_text:
                    parts.append(page_text)
                    total += len(page_text)
                if total >= max_chars:
                    break
            return "\n\n".join(parts), "pypdf"
        except Exception as exc:  # pragma: no cover - parser-specific failures vary by PDF.
            raise DocumentExtractionError("PDF parser failed.", backend="pypdf", details={"error": str(exc)[:500]}) from exc

    def _extract_docx_text(self, path: Path, *, max_chars: int) -> str:
        with zipfile.ZipFile(path) as archive:
            names = ["word/document.xml"]
            names.extend(sorted(name for name in archive.namelist() if name.startswith("word/header") or name.startswith("word/footer")))
            parts: list[str] = []
            for name in names:
                if name not in archive.namelist():
                    continue
                parts.append(_text_from_ooxml(archive.read(name)))
                if sum(len(part) for part in parts) >= max_chars:
                    break
        return "\n".join(part for part in parts if part.strip())

    def _extract_pptx_text(self, path: Path, *, max_chars: int) -> str:
        with zipfile.ZipFile(path) as archive:
            parts = []
            for name in sorted(item for item in archive.namelist() if item.startswith("ppt/slides/slide") and item.endswith(".xml")):
                text = _text_from_ooxml(archive.read(name))
                if text.strip():
                    parts.append(f"Slide {len(parts) + 1}\n{text}")
                if sum(len(part) for part in parts) >= max_chars:
                    break
        return "\n\n".join(parts)

    def _extract_xlsx_text(self, path: Path, *, max_chars: int) -> str:
        with zipfile.ZipFile(path) as archive:
            shared_strings = _xlsx_shared_strings(archive)
            sheet_names = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
            sections: list[str] = []
            for sheet_index, name in enumerate(sheet_names, start=1):
                rows = _xlsx_sheet_rows(archive.read(name), shared_strings)
                if rows:
                    preview = "\n".join(",".join(cell for cell in row) for row in rows[:200])
                    sections.append(f"Sheet {sheet_index}\n{preview}")
                if sum(len(part) for part in sections) >= max_chars:
                    break
        return "\n\n".join(sections)


def _strip_code_fence(text: str) -> str:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:csv)?\s*", "", clean, flags=re.I)
        clean = re.sub(r"\s*```$", "", clean)
    return clean.strip()


def _module_available(name: str) -> bool:
    try:
        __import__(name)
    except ImportError:
        return False
    return True


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        clean = " ".join(str(item).split())
        if not clean or clean in seen:
            continue
        seen.add(clean)
        output.append(clean)
    return output


def _lines_matching(text: str, markers: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for line in text.splitlines():
        clean = " ".join(line.strip(" -\t").split())
        if not clean:
            continue
        normalized = clean.lower()
        if any(marker.lower() in normalized for marker in markers):
            matches.append(clamp_text(clean, 500))
    return _dedupe_keep_order(matches)


def _build_slide_outline(topic: str, points: list[str], decisions: list[str], actions: list[str], risks: list[str]) -> list[str]:
    pages = [
        f"1. 标题页：{topic}",
        "2. 背景与目标：说明本次汇报解决什么问题",
    ]
    if points:
        pages.append(f"3. 核心结论：{points[0]}")
    if decisions:
        pages.append(f"4. 关键决策：{decisions[0]}")
    if actions:
        pages.append(f"5. 行动计划：{actions[0]}")
    if risks:
        pages.append(f"6. 风险与待确认：{', '.join(risks[:4])}")
    pages.append("7. 下一步：确认负责人、截止时间和验收标准")
    return pages


def _dedupe_csv_rows(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return rows
    output = [rows[0]]
    seen: set[tuple[str, ...]] = set()
    for row in rows[1:]:
        key = tuple(row[:6])
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _guess_owner(text: str) -> str:
    patterns = [
        r"(?:负责人|owner|Owner|由)\s*[:：]?\s*([A-Za-z0-9_\-\u4e00-\u9fff]{2,24})",
        r"([A-Za-z\u4e00-\u9fff]{2,24})\s*(?:负责|跟进|review|Review)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    if ":" in text:
        speaker = text.split(":", 1)[0].strip()
        if 1 <= len(speaker) <= 32:
            return speaker
    if "：" in text:
        speaker = text.split("：", 1)[0].strip()
        if 1 <= len(speaker) <= 32:
            return speaker
    return ""


def _guess_time_scope(text: str) -> str:
    patterns = [
        r"\d{4}[-/年]\d{1,2}(?:[-/月]\d{1,2}日?)?",
        r"\d{1,2}月\d{1,2}日",
        r"(?:今天|明天|后天|本周|下周|本月|下月|Q[1-4]|季度|月底|周[一二三四五六日天])",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return ""


def _text_from_ooxml(payload: bytes) -> str:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return ""
    chunks: list[str] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in {"t", "instrText"} and element.text:
            chunks.append(element.text)
        elif tag in {"br", "cr", "p"}:
            chunks.append("\n")
        elif tag == "tab":
            chunks.append("\t")
    text = "".join(chunks)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except ElementTree.ParseError:
        return []
    strings: list[str] = []
    for item in root:
        text_parts = [element.text or "" for element in item.iter() if element.tag.rsplit("}", 1)[-1] == "t"]
        strings.append("".join(text_parts))
    return strings


def _xlsx_sheet_rows(payload: bytes, shared_strings: list[str]) -> list[list[str]]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return []
    rows: list[list[str]] = []
    for row in root.iter():
        if row.tag.rsplit("}", 1)[-1] != "row":
            continue
        values: list[str] = []
        for cell in row:
            if cell.tag.rsplit("}", 1)[-1] != "c":
                continue
            values.append(_xlsx_cell_text(cell, shared_strings))
        if any(value.strip() for value in values):
            rows.append(values)
    return rows


def _xlsx_cell_text(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    inline_parts: list[str] = []
    value_text = ""
    for child in cell.iter():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "v" and child.text:
            value_text = child.text
        elif tag == "t" and child.text:
            inline_parts.append(child.text)
    if cell_type == "s" and value_text:
        try:
            return shared_strings[int(value_text)]
        except (ValueError, IndexError):
            return value_text
    if inline_parts:
        return "".join(inline_parts)
    return value_text
