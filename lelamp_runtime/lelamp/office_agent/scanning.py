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


from .scan_capture import ScanCaptureMixin
from .scan_enhancement import ScanEnhancementMixin
from .scan_ocr import ScanOcrMixin
from .scan_results import ScanResultsMixin

class ScanService(ScanCaptureMixin, ScanEnhancementMixin, ScanOcrMixin, ScanResultsMixin):
    """Domain-composed service."""




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
