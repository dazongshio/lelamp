from __future__ import annotations

import base64
import csv
import io
import json
import mimetypes
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .config import OfficeAgentConfig
from .llm import LLMError, ResponsesLLM, ResponsesLLMConfig
from .workspace import Workspace, WorkspaceError
from .utils import safe_filename


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
IMAGE_MIME_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
RISK_MARKERS = ["liability", "termination", "confidential", "违约", "终止", "保密", "赔偿", "付款"]
TESSERACT_LANGUAGE_MAP = {
    "ch": "chi_sim",
    "zh": "chi_sim",
    "zh_cn": "chi_sim",
    "chi_sim": "chi_sim",
    "en": "eng",
    "eng": "eng",
    "chi_sim+eng": "chi_sim+eng",
}



class ScanResultsMixin:
    def _write_ocr_result(
        self,
        image_path: Path,
        *,
        backend: str,
        language: str,
        text: str,
        confidences: list[float],
    ) -> dict[str, object]:
        structure = _heuristic_structure_from_text(text, "document")
        return self._persist_structured_result(
            image_path,
            backend=backend,
            language=language,
            text=text,
            structure=structure,
            status="ok",
            confidence_avg=round(sum(confidences) / len(confidences), 4) if confidences else None,
        )
    def _persist_structured_result(
        self,
        image_path: Path,
        *,
        backend: str,
        language: str,
        text: str,
        structure: dict[str, Any],
        status: str,
        provider: str = "",
        model: str = "",
        confidence_avg: float | None = None,
    ) -> dict[str, object]:
        if not image_path.is_relative_to(self.workspace.root):
            raise WorkspaceError("OCR result source must stay inside workspace.")

        text_path = self.workspace.write_text(
            safe_filename(image_path.stem, suffix="_ocr.txt"),
            text,
            action="scan.ocr_text_write",
        )
        table_paths = self._write_table_artifacts(image_path, structure.get("tables"))
        business_card = structure.get("business_card") if isinstance(structure.get("business_card"), dict) else {}
        contract = structure.get("contract") if isinstance(structure.get("contract"), dict) else {}
        business_card_path = ""
        contract_path = ""
        if business_card:
            business_card_path = str(
                self.workspace.write_json(
                    safe_filename(image_path.stem, suffix="_business_card.json"),
                    business_card,
                    action="scan.business_card_result",
                )
            )
        if contract:
            contract_path = str(
                self.workspace.write_json(
                    safe_filename(image_path.stem, suffix="_contract.json"),
                    contract,
                    action="scan.contract_result",
                )
            )

        summary = {
            "status": status,
            "backend": backend,
            "provider": provider,
            "model": model,
            "image": str(image_path.relative_to(self.workspace.root)),
            "language": language,
            "text_path": str(text_path),
            "chars": len(text),
            "line_count": len([line for line in text.splitlines() if line.strip()]),
            "confidence_avg": confidence_avg,
            "preview": text[:1000],
            "summary": str(structure.get("summary") or ""),
            "key_points": structure.get("key_points") if isinstance(structure.get("key_points"), list) else [],
            "tables": structure.get("tables") if isinstance(structure.get("tables"), list) else [],
            "table_paths": table_paths,
            "entities": structure.get("entities") if isinstance(structure.get("entities"), dict) else {},
            "risks": structure.get("risks") if isinstance(structure.get("risks"), list) else [],
            "business_card": business_card,
            "business_card_path": business_card_path,
            "contract": contract,
            "contract_path": contract_path,
            "quality_notes": structure.get("quality_notes") if isinstance(structure.get("quality_notes"), list) else [],
            "uncertain_items": structure.get("uncertain_items") if isinstance(structure.get("uncertain_items"), list) else [],
        }
        structure_path = self.workspace.write_json(
            safe_filename(image_path.stem, suffix="_scan_structure.json"),
            summary,
            action="scan.structure_result",
        )
        summary_markdown = _scan_summary_markdown(summary)
        summary_path = self.workspace.write_text(
            safe_filename(image_path.stem, suffix="_scan_summary.md"),
            summary_markdown,
            action="scan.summary_write",
        )
        result_path = self.workspace.write_json(
            safe_filename(image_path.stem, suffix="_ocr_result.json"),
            summary,
            action="scan.ocr_result",
        )
        summary["structure_path"] = str(structure_path)
        summary["summary_path"] = str(summary_path)
        summary["result_path"] = str(result_path)
        self.audit.record(
            "scan.ocr",
            target=str(image_path),
            details={"backend": backend, "chars": len(text), "result_path": str(result_path)},
        )
        return summary
    def _write_table_artifacts(self, image_path: Path, tables: object) -> list[str]:
        if not isinstance(tables, list):
            return []
        paths: list[str] = []
        for index, table in enumerate(tables[:5], start=1):
            if not isinstance(table, dict):
                continue
            headers = table.get("headers") if isinstance(table.get("headers"), list) else []
            rows = table.get("rows") if isinstance(table.get("rows"), list) else []
            if not headers and not rows:
                continue
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            if headers:
                writer.writerow([str(item) for item in headers])
            for row in rows:
                if isinstance(row, list):
                    writer.writerow([str(item) for item in row])
            table_path = self.workspace.write_text(
                safe_filename(image_path.stem, suffix=f"_table_{index}.csv"),
                buffer.getvalue(),
                action="scan.table_result",
            )
            paths.append(str(table_path))
        return paths
    def _decode_image_data_url(self, image_data_url: str) -> tuple[str, bytes]:
        match = re.match(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$", image_data_url)
        if not match:
            raise ValueError("Expected a PNG, JPEG, or WebP data URL.")
        mime_type, raw_base64 = match.groups()
        try:
            data = base64.b64decode("".join(raw_base64.split()), validate=True)
        except Exception as exc:
            raise ValueError("Image data URL is not valid base64.") from exc
        return mime_type, data

from .scan_image_utils import (
    _center_document_crop, _decode_textin_image, _document_text_metrics,
    _draw_corner_preview, _empty_structure_value, _enhance_color_scan,
    _enhance_for_ocr, _extract_table_like_blocks, _fallback_document_region,
    _find_emails, _find_phone_candidates, _float_metric, _four_point_transform,
    _gray_world_white_balance, _heuristic_structure_from_text, _image_metrics,
    _image_path_to_data_url, _lift_paper_background, _merge_structure,
    _order_points, _order_textin_points, _parse_json_object, _perspective_correct,
    _scan_summary_markdown, _select_ocr_input, _textin_boundary_from_result,
    _textin_image_items, _trim_border,
)
