from __future__ import annotations

import hashlib
import hmac
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .config import OfficeAgentConfig
from .utils import dedupe_path, safe_filename


@dataclass
class EnterprisePolicyService:
    config: OfficeAgentConfig
    audit: AuditLogger

    def status(self) -> dict[str, object]:
        policy = self._load_policy()
        platform = self.local_platform_status()
        payload = {
            "status": "implemented",
            "policy_path": str(self.config.enterprise_policy_path),
            "policy_file_present": self.config.enterprise_policy_path.is_file(),
            "cloud_ai_enabled": self.config.cloud_ai_enabled,
            "permission_mode": self.config.permission_mode.value,
            "desktop_backend": self.config.desktop_backend,
            "allowed_roots": [str(path) for path in self.config.allowed_roots],
            "audit_log_path": str(self.config.audit_log_path),
            "audit_signing": {
                "status": "available" if self.config.audit_signing_key else "needs_config",
                "key_configured": bool(self.config.audit_signing_key),
                "algorithm": "HMAC-SHA256",
            },
            "retention": {
                "audit_export_retention_days": self.config.audit_export_retention_days,
            },
            "local_platform": platform,
            "policy": policy or self.default_policy(),
            "enforced_controls": [
                "workspace_allowed_roots",
                "meeting_mode_explicit_enable",
                "projection_no_passive_parse",
                "full_control_restart_gate",
                "email_send_explicit_authorization",
                "cloud_ai_disable_env_gate",
                "signed_audit_export",
            ],
        }
        self.audit.record("enterprise.policy_status", details={"cloud_ai_enabled": self.config.cloud_ai_enabled, "signing": bool(self.config.audit_signing_key)})
        return payload

    def local_platform_status(self) -> dict[str, object]:
        platform_dir = (self.config.workspace_dir / "enterprise_platform").resolve()
        bundle_dir = (self.config.workspace_dir / "enterprise_exports").resolve()
        manifest_path = platform_dir / "manifest.json"
        latest_bundle = ""
        if bundle_dir.exists():
            bundles = sorted(bundle_dir.glob("enterprise_platform_*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
            latest_bundle = str(bundles[0]) if bundles else ""
        manifest: dict[str, object] = {}
        if manifest_path.is_file():
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = payload if isinstance(payload, dict) else {}
            except (OSError, json.JSONDecodeError):
                manifest = {}
        status = "available" if manifest_path.is_file() else "not_built"
        return {
            "status": status,
            "platform_dir": str(platform_dir),
            "manifest_path": str(manifest_path),
            "latest_bundle": latest_bundle,
            "services": self.local_platform_services(),
            "data_zones": self.local_platform_data_zones(),
            "offline_model_registry": str(platform_dir / "models" / "registry.json"),
            "manifest": manifest,
        }

    def build_local_platform_bundle(self, *, include_samples: bool = True) -> dict[str, object]:
        platform_dir = (self.config.workspace_dir / "enterprise_platform").resolve()
        export_dir = (self.config.workspace_dir / "enterprise_exports").resolve()
        data_dir = platform_dir / "data"
        models_dir = platform_dir / "models"
        policies_dir = platform_dir / "policies"
        ops_dir = platform_dir / "ops"
        for path in (data_dir, models_dir, policies_dir, ops_dir, export_dir):
            path.mkdir(parents=True, exist_ok=True)

        now = datetime.now(UTC).isoformat()
        services = self.local_platform_services()
        data_zones = self.local_platform_data_zones()
        for zone in data_zones:
            (data_dir / str(zone["name"])).mkdir(parents=True, exist_ok=True)

        manifest = {
            "status": "available",
            "created_at": now,
            "version": "enterprise-platform-v1",
            "workspace_dir": str(self.config.workspace_dir),
            "policy_path": str(self.config.enterprise_policy_path),
            "cloud_ai_enabled": self.config.cloud_ai_enabled,
            "permission_mode": self.config.permission_mode.value,
            "desktop_backend": self.config.desktop_backend,
            "services": services,
            "data_zones": data_zones,
            "artifacts": {
                "compose": str(ops_dir / "docker-compose.enterprise.yml"),
                "policy_template": str(policies_dir / "enterprise_policy.template.json"),
                "model_registry": str(models_dir / "registry.json"),
                "runbook": str(platform_dir / "README.md"),
            },
            "safety": [
                "默认保持 workspace allowed_roots 边界。",
                "云 AI 可由 OPENCLAW_CLOUD_AI_ENABLED/企业策略关闭。",
                "全局桌面控制仍需 full_control 重启和逐任务授权。",
                "该包不包含真实模型权重、SIEM 密钥或 MDM 凭据。",
            ],
        }
        model_registry = {
            "status": "template",
            "created_at": now,
            "models": [
                {
                    "name": "local-llm",
                    "role": "document/meeting reasoning fallback",
                    "runtime": "ollama or vllm",
                    "path": "models/local-llm",
                    "configured": False,
                },
                {
                    "name": "local-ocr",
                    "role": "document OCR fallback",
                    "runtime": "paddleocr or tesseract",
                    "path": "models/local-ocr",
                    "configured": False,
                },
                {
                    "name": "local-asr",
                    "role": "meeting ASR fallback",
                    "runtime": "whisper.cpp or faster-whisper",
                    "path": "models/local-asr",
                    "configured": False,
                },
            ],
        }
        policy_template = {
            "security": {
                "cloud_ai_enabled": False,
                "permission_mode": "sandbox",
                "desktop_backend": "audit_only",
                "full_control_requires_restart": True,
                "email_send_requires_authorization": True,
            },
            "data_platform": {
                "data_zones": [zone["name"] for zone in data_zones],
                "audit_export_retention_days": self.config.audit_export_retention_days,
                "offline_model_registry": str(models_dir / "registry.json"),
            },
            "integrations": {
                "siem_sink": "",
                "mdm_profile_id": "",
                "local_model_endpoint": "http://127.0.0.1:11434",
            },
        }

        files: dict[Path, bytes] = {
            platform_dir / "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            models_dir / "registry.json": json.dumps(model_registry, ensure_ascii=False, indent=2).encode("utf-8"),
            policies_dir / "enterprise_policy.template.json": json.dumps(policy_template, ensure_ascii=False, indent=2).encode("utf-8"),
            ops_dir / "docker-compose.enterprise.yml": self.enterprise_compose_yaml().encode("utf-8"),
            platform_dir / "README.md": self.local_platform_runbook(manifest).encode("utf-8"),
        }
        if include_samples:
            files[data_dir / "README.md"] = self.data_platform_readme(data_zones).encode("utf-8")
        for path, data in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        zip_path = dedupe_path(export_dir / safe_filename(f"enterprise_platform_{timestamp}", suffix=".zip"))
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(platform_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(platform_dir.parent).as_posix())
        result = {
            "status": "completed",
            "platform_dir": str(platform_dir),
            "bundle_path": str(zip_path),
            "manifest_path": str(platform_dir / "manifest.json"),
            "model_registry_path": str(models_dir / "registry.json"),
            "compose_path": str(ops_dir / "docker-compose.enterprise.yml"),
            "policy_template_path": str(policies_dir / "enterprise_policy.template.json"),
            "services": services,
            "data_zones": data_zones,
            "safety": manifest["safety"],
        }
        self.audit.record("enterprise.local_platform_build", target=str(zip_path), details={"services": len(services), "data_zones": len(data_zones)})
        return result

    def export_signed_audit(self, rows: list[dict[str, object]], *, query: dict[str, object] | None = None) -> dict[str, object]:
        if not self.config.audit_signing_key:
            payload = {
                "status": "needs_config",
                "message": "OPENCLAW_AUDIT_SIGNING_KEY is required for signed audit export.",
                "algorithm": "HMAC-SHA256",
            }
            self.audit.record("enterprise.audit_export_signed", status="blocked", target="audit_signed_export", details=payload)
            return payload
        export_dir = (self.config.workspace_dir / "enterprise_exports").resolve()
        export_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        csv_bytes = self.audit_rows_csv(rows)
        manifest = {
            "created_at": datetime.now(UTC).isoformat(),
            "source_audit_log": str(self.config.audit_log_path),
            "row_count": len(rows),
            "query": query or {},
            "sha256": hashlib.sha256(csv_bytes).hexdigest(),
            "algorithm": "HMAC-SHA256",
            "retention_days": self.config.audit_export_retention_days,
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        signature = hmac.new(
            self.config.audit_signing_key.encode("utf-8"),
            manifest_bytes + b"\n" + csv_bytes,
            hashlib.sha256,
        ).hexdigest()
        signature_payload = {
            "algorithm": "HMAC-SHA256",
            "signature": signature,
            "signed_fields": ["manifest.json", "audit.csv"],
        }
        zip_path = dedupe_path(export_dir / safe_filename(f"audit_signed_{timestamp}", suffix=".zip"))
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("audit.csv", csv_bytes)
            archive.writestr("manifest.json", manifest_bytes)
            archive.writestr("signature.json", json.dumps(signature_payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
        result = {
            "status": "completed",
            "path": str(zip_path),
            "row_count": len(rows),
            "sha256": manifest["sha256"],
            "signature": signature,
            "algorithm": "HMAC-SHA256",
            "created_at": manifest["created_at"],
        }
        self.audit.record("enterprise.audit_export_signed", target=str(zip_path), details={"row_count": len(rows), "sha256": manifest["sha256"]})
        return result

    def verify_signed_audit_export(self, zip_path: Path) -> dict[str, object]:
        if not self.config.audit_signing_key:
            return {
                "status": "needs_config",
                "message": "OPENCLAW_AUDIT_SIGNING_KEY is required for signature verification.",
            }
        if not zip_path.is_file():
            return {"status": "blocked", "message": "Signed audit export file not found."}
        with zipfile.ZipFile(zip_path, "r") as archive:
            manifest_bytes = archive.read("manifest.json")
            csv_bytes = archive.read("audit.csv")
            signature_payload = json.loads(archive.read("signature.json").decode("utf-8"))
        expected = hmac.new(
            self.config.audit_signing_key.encode("utf-8"),
            manifest_bytes + b"\n" + csv_bytes,
            hashlib.sha256,
        ).hexdigest()
        actual = str(signature_payload.get("signature") or "")
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        ok = hmac.compare_digest(expected, actual) and hashlib.sha256(csv_bytes).hexdigest() == manifest.get("sha256")
        result = {
            "status": "completed" if ok else "failed",
            "valid": ok,
            "path": str(zip_path),
            "row_count": manifest.get("row_count"),
            "sha256": manifest.get("sha256"),
            "algorithm": signature_payload.get("algorithm"),
        }
        self.audit.record("enterprise.audit_export_verify", status="ok" if ok else "error", target=str(zip_path), details=result)
        return result

    def default_policy(self) -> dict[str, object]:
        return {
            "security": {
                "cloud_ai_enabled": self.config.cloud_ai_enabled,
                "permission_mode": self.config.permission_mode.value,
                "desktop_backend": self.config.desktop_backend,
                "full_control_requires_restart": True,
                "email_send_requires_authorization": True,
            },
            "retention": {
                "audit_export_retention_days": self.config.audit_export_retention_days,
            },
        }

    def local_platform_services(self) -> list[dict[str, object]]:
        return [
            {
                "name": "local_model_gateway",
                "status": "template",
                "purpose": "统一切换 OpenAI API、本地 Ollama/vLLM 或企业模型网关。",
                "endpoint": "http://127.0.0.1:11434",
            },
            {
                "name": "workspace_data_lake",
                "status": "available",
                "purpose": "按授权 workspace 分区保存文档、会议、扫描、投影和审计产物。",
                "path": str(self.config.workspace_dir / "enterprise_platform" / "data"),
            },
            {
                "name": "signed_audit_archive",
                "status": "available" if self.config.audit_signing_key else "needs_config",
                "purpose": "生成可验签的审计导出包。",
                "path": str(self.config.workspace_dir / "enterprise_exports"),
            },
            {
                "name": "siem_mdm_connector",
                "status": "template",
                "purpose": "预留企业 SIEM/MDM 对接配置，不内置凭据。",
                "path": str(self.config.workspace_dir / "enterprise_platform" / "ops"),
            },
        ]

    def local_platform_data_zones(self) -> list[dict[str, object]]:
        return [
            {"name": "documents", "classification": "user_authorized_files", "retention": "workspace_policy"},
            {"name": "meetings", "classification": "explicit_meeting_mode_outputs", "retention": "workspace_policy"},
            {"name": "scans", "classification": "explicit_camera_uploads", "retention": "workspace_policy"},
            {"name": "projection", "classification": "user_rendered_cards", "retention": "workspace_policy"},
            {"name": "audit", "classification": "append_only_logs", "retention": f"{self.config.audit_export_retention_days}_days_export"},
        ]

    def enterprise_compose_yaml(self) -> str:
        return "\n".join(
            [
                "services:",
                "  local-model-gateway:",
                "    image: ollama/ollama:latest",
                "    profiles: [local-ai]",
                "    ports:",
                "      - \"11434:11434\"",
                "    volumes:",
                "      - ../models:/root/.ollama",
                "  audit-export:",
                "    image: python:3.12-slim",
                "    profiles: [audit]",
                "    working_dir: /workspace",
                "    volumes:",
                "      - ../../:/workspace:ro",
                "    command: [\"python\", \"-m\", \"json.tool\", \"logs/audit.jsonl\"]",
                "",
            ]
        )

    def local_platform_runbook(self, manifest: dict[str, object]) -> str:
        return "\n".join(
            [
                "# LeLamp Enterprise Local Platform",
                "",
                "This package is a deployable template for local compute and data-platform setup.",
                "It intentionally contains no model weights, secrets, SIEM tokens, or MDM credentials.",
                "",
                "## Included",
                "- Data-zone folders for documents, meetings, scans, projection output, and audit exports.",
                "- Offline model registry template.",
                "- Enterprise policy template that can disable cloud AI.",
                "- Docker Compose template for a local model gateway.",
                "",
                "## Safety",
                *[f"- {item}" for item in manifest.get("safety", []) if isinstance(item, str)],
                "",
            ]
        )

    def data_platform_readme(self, zones: list[dict[str, object]]) -> str:
        lines = ["# Data Zones", ""]
        for zone in zones:
            lines.append(f"- `{zone['name']}`: {zone['classification']} / {zone['retention']}")
        lines.append("")
        return "\n".join(lines)

    def _load_policy(self) -> dict[str, object]:
        path = self.config.enterprise_policy_path
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def audit_rows_csv(rows: list[dict[str, object]]) -> bytes:
        header = ["timestamp", "actor", "action", "status", "target", "details", "request_id"]
        lines = [",".join(header)]
        for row in rows:
            values = [
                str(row.get("timestamp", "")),
                str(row.get("actor", "")),
                str(row.get("action", "")),
                str(row.get("status", "")),
                str(row.get("target", "")),
                json.dumps(row.get("details", {}), ensure_ascii=False),
                str(row.get("request_id", "")),
            ]
            lines.append(",".join(_csv_escape(value) for value in values))
        return ("\n".join(lines) + "\n").encode("utf-8-sig")


def _csv_escape(value: str) -> str:
    if any(ch in value for ch in [",", '"', "\n", "\r"]):
        return '"' + value.replace('"', '""') + '"'
    return value
