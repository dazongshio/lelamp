from __future__ import annotations

import os
import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path


class PermissionMode(StrEnum):
    SANDBOX = "sandbox"
    FULL_CONTROL = "full_control"


def _runtime_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _split_paths(value: str | None) -> list[Path]:
    if not value:
        return []
    return [Path(item).expanduser() for item in value.split(os.pathsep) if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _load_policy(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


TINGWU_PLACEHOLDER_CREDENTIAL_VALUES = {
    "",
    "placeholder-app",
    "placeholder-key",
    "replace_with_bailian_app_id",
    "replace_with_new_rotated_key",
}


def tingwu_credential_kind(value: str | None, *, role: str = "") -> str:
    normalized = str(value or "").strip()
    normalized_lower = normalized.lower()
    if not normalized:
        return "missing"
    if normalized_lower in TINGWU_PLACEHOLDER_CREDENTIAL_VALUES:
        return "placeholder"
    if normalized.startswith("LTAI"):
        return "aliyun_access_key_id"
    if normalized_lower.startswith("appkey "):
        return "legacy_tingwu_appkey"
    if role == "app_id" and not normalized.startswith("tw_"):
        return "unexpected_app_id_shape"
    return "configured"


def is_placeholder_tingwu_credential(value: str | None) -> bool:
    return tingwu_credential_kind(value) in {
        "missing",
        "placeholder",
        "aliyun_access_key_id",
        "legacy_tingwu_appkey",
    }


def is_valid_tingwu_credential(value: str | None, *, role: str = "") -> bool:
    return tingwu_credential_kind(value, role=role) == "configured"


def tingwu_credential_next_actions(api_key_kind: str, app_id_kind: str) -> list[str]:
    actions: list[str] = []
    if api_key_kind == "aliyun_access_key_id":
        actions.append("Replace RAM AccessKey ID with a fresh Bailian/DashScope API Key; rotate the exposed AccessKey in RAM.")
    elif api_key_kind in {"missing", "placeholder"}:
        actions.append("Create a fresh Bailian/DashScope API Key and set DASHSCOPE_API_KEY or TINGWU_API_KEY.")
    if app_id_kind == "legacy_tingwu_appkey":
        actions.append("Replace legacy Tingwu OpenAPI AppKey with the Bailian Model Studio application App ID.")
    elif app_id_kind in {"missing", "placeholder"}:
        actions.append("Copy the Bailian Model Studio application App ID into TINGWU_APP_ID; do not paste it into chat.")
    elif app_id_kind == "unexpected_app_id_shape":
        actions.append("Use the Bailian Model Studio application App ID for TINGWU_APP_ID; it usually starts with tw_.")
    return actions


def _real_tingwu_credential(*values: str | None) -> str:
    for value in values:
        normalized = str(value or "").strip()
        if normalized and is_valid_tingwu_credential(normalized):
            return normalized
    return ""


def _real_tingwu_app_id(*values: str | None) -> str:
    for value in values:
        normalized = str(value or "").strip()
        if normalized and is_valid_tingwu_credential(normalized, role="app_id"):
            return normalized
    return ""


@dataclass(frozen=True)
class OfficeAgentConfig:
    """Runtime configuration for the office agent.

    The defaults keep the agent in a local workspace and require explicit file
    import before document tools can read user content.
    """

    workspace_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("OPENCLAW_WORKSPACE_DIR", _runtime_root() / "workspace")
        ).expanduser()
    )
    audit_log_path: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "OPENCLAW_AUDIT_LOG_PATH",
                os.getenv("OPENCLAW_AUDIT_LOG", _runtime_root() / "logs" / "audit.jsonl"),
            )
        ).expanduser()
    )
    permission_mode: PermissionMode = field(
        default_factory=lambda: PermissionMode(
            os.getenv("OPENCLAW_PERMISSION_MODE", PermissionMode.SANDBOX.value)
        )
    )
    allowed_roots: tuple[Path, ...] = field(default_factory=tuple)
    enable_hardware: bool = field(
        default_factory=lambda: os.getenv("OPENCLAW_ENABLE_HARDWARE", "0").lower()
        in {"1", "true", "yes", "on"}
    )
    hardware_port: str = field(default_factory=lambda: os.getenv("LELAMP_PORT", "/dev/ttyACM0"))
    lamp_id: str = field(default_factory=lambda: os.getenv("LELAMP_ID", "lelamp"))
    projection_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("OPENCLAW_PROJECTION_DIR", _runtime_root() / "projection_out")
        ).expanduser()
    )
    memory_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("OPENCLAW_MEMORY_PATH", _runtime_root() / "memory" / "memory.jsonl")
        ).expanduser()
    )
    meeting_mode_enabled: bool = field(
        default_factory=lambda: os.getenv("OPENCLAW_MEETING_MODE", "0").lower()
        in {"1", "true", "yes", "on"}
    )
    desktop_backend: str = field(
        default_factory=lambda: os.getenv("OPENCLAW_DESKTOP_BACKEND", "audit_only")
    )
    browser_automation_headless: bool = field(
        default_factory=lambda: _env_bool("OPENCLAW_BROWSER_AUTOMATION_HEADLESS", True)
    )
    browser_automation_timeout_ms: int = field(
        default_factory=lambda: int(os.getenv("OPENCLAW_BROWSER_AUTOMATION_TIMEOUT_MS", "15000"))
    )
    browser_automation_max_steps: int = field(
        default_factory=lambda: int(os.getenv("OPENCLAW_BROWSER_AUTOMATION_MAX_STEPS", "12"))
    )
    smart_home_provider: str = field(
        default_factory=lambda: os.getenv("OPENCLAW_SMART_HOME_PROVIDER", "none")
    )
    home_assistant_url: str = field(
        default_factory=lambda: os.getenv("OPENCLAW_HOME_ASSISTANT_URL", "")
    )
    home_assistant_token: str = field(
        default_factory=lambda: os.getenv("OPENCLAW_HOME_ASSISTANT_TOKEN", "")
    )
    smart_home_webhook_url: str = field(
        default_factory=lambda: os.getenv("OPENCLAW_SMART_HOME_WEBHOOK_URL", "")
    )
    smart_home_entities: str = field(
        default_factory=lambda: os.getenv("OPENCLAW_SMART_HOME_ENTITIES", "")
    )
    mobile_bridge_webhook_url: str = field(
        default_factory=lambda: os.getenv("OPENCLAW_MOBILE_BRIDGE_WEBHOOK_URL", "")
    )
    mobile_bridge_shared_secret: str = field(
        default_factory=lambda: os.getenv("OPENCLAW_MOBILE_BRIDGE_SHARED_SECRET", "")
    )
    mobile_bridge_device_id: str = field(
        default_factory=lambda: os.getenv("OPENCLAW_MOBILE_BRIDGE_DEVICE_ID", "primary_phone")
    )
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_base_url: str = field(
        default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
    )
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-5.5"))
    openai_vision_model: str = field(default_factory=lambda: os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini"))
    openai_reasoning_effort: str = field(
        default_factory=lambda: os.getenv("OPENAI_REASONING_EFFORT", "xhigh")
    )
    asr_provider: str = field(default_factory=lambda: os.getenv("OPENCLAW_ASR_PROVIDER", "openai"))
    asr_model: str = field(default_factory=lambda: os.getenv("OPENCLAW_ASR_MODEL", "whisper-1"))
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    tts_provider: str = field(default_factory=lambda: os.getenv("OPENCLAW_TTS_PROVIDER", "openai"))
    tts_model: str = field(default_factory=lambda: os.getenv("OPENCLAW_TTS_MODEL", "tts-1"))
    tts_voice: str = field(default_factory=lambda: os.getenv("OPENCLAW_TTS_VOICE", "alloy"))
    elevenlabs_api_key: str = field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY", ""))
    elevenlabs_voice_id: str = field(
        default_factory=lambda: os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    )
    elevenlabs_model_id: str = field(
        default_factory=lambda: os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    )
    dashscope_api_key: str = field(default_factory=lambda: os.getenv("DASHSCOPE_API_KEY", ""))
    dashscope_asr_model: str = field(
        default_factory=lambda: os.getenv("DASHSCOPE_ASR_MODEL", "paraformer-realtime-v2")
    )
    dashscope_asr_sample_rate: int = field(
        default_factory=lambda: int(os.getenv("DASHSCOPE_ASR_SAMPLE_RATE", "16000"))
    )
    dashscope_tts_model: str = field(
        default_factory=lambda: os.getenv("DASHSCOPE_TTS_MODEL", "cosyvoice-v3-flash")
    )
    dashscope_tts_voice: str = field(
        default_factory=lambda: os.getenv("DASHSCOPE_TTS_VOICE", "longanyang")
    )
    dashscope_tts_url: str = field(default_factory=lambda: os.getenv("DASHSCOPE_TTS_URL", ""))
    dashscope_vision_model: str = field(
        default_factory=lambda: os.getenv("DASHSCOPE_VISION_MODEL", "qwen-vl-plus")
    )
    dashscope_vision_base_url: str = field(
        default_factory=lambda: os.getenv("DASHSCOPE_VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode")
    )
    dashscope_vision_wire_api: str = field(
        default_factory=lambda: os.getenv("DASHSCOPE_VISION_WIRE_API", "chat_completions")
    )
    dashscope_text_model: str = field(
        default_factory=lambda: os.getenv("DASHSCOPE_TEXT_MODEL", "qwen-plus")
    )
    dashscope_realtime_model: str = field(
        default_factory=lambda: os.getenv("DASHSCOPE_REALTIME_MODEL", "qwen3-omni-flash-realtime")
    )
    dashscope_realtime_url: str = field(
        default_factory=lambda: os.getenv(
            "DASHSCOPE_REALTIME_URL",
            "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        )
    )
    dashscope_realtime_voice: str = field(
        default_factory=lambda: os.getenv("DASHSCOPE_REALTIME_VOICE", "Cherry")
    )
    dashscope_realtime_transcription_model: str = field(
        default_factory=lambda: os.getenv(
            "DASHSCOPE_REALTIME_TRANSCRIPTION_MODEL",
            "gummy-realtime-v1",
        )
    )
    tingwu_api_key: str = field(
        default_factory=lambda: os.getenv("TINGWU_API_KEY", os.getenv("DASHSCOPE_API_KEY", ""))
    )
    tingwu_app_id: str = field(
        default_factory=lambda: os.getenv("TINGWU_APP_ID", os.getenv("TINGWU_MEETING_APP_ID", ""))
    )
    tingwu_api_key_kind: str = "missing"
    tingwu_app_id_kind: str = "missing"
    tingwu_http_url: str = field(
        default_factory=lambda: os.getenv(
            "TINGWU_HTTP_URL",
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        )
    )
    tingwu_ws_url: str = field(
        default_factory=lambda: os.getenv(
            "TINGWU_WS_URL",
            "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        )
    )
    tingwu_audio_format: str = field(default_factory=lambda: os.getenv("TINGWU_AUDIO_FORMAT", "pcm"))
    tingwu_sample_rate: int = field(default_factory=lambda: int(os.getenv("TINGWU_SAMPLE_RATE", "16000")))
    tingwu_pcm_gain: float = field(default_factory=lambda: float(os.getenv("TINGWU_PCM_GAIN", "1.0")))
    tingwu_audio_file: str = field(default_factory=lambda: os.getenv("TINGWU_AUDIO_FILE", ""))
    tingwu_audio_file_speed: float = field(default_factory=lambda: float(os.getenv("TINGWU_AUDIO_FILE_SPEED", "1.0")))
    tingwu_preflight_capture_seconds: int = field(
        default_factory=lambda: int(os.getenv("TINGWU_PREFLIGHT_CAPTURE_SECONDS", "3"))
    )
    tingwu_transcription_model: str = field(
        default_factory=lambda: os.getenv("TINGWU_TRANSCRIPTION_MODEL", "multilingual")
    )
    tingwu_language_hints: str = field(default_factory=lambda: os.getenv("TINGWU_LANGUAGE_HINTS", "cn,en"))
    tingwu_analysis_model: str = field(default_factory=lambda: os.getenv("TINGWU_ANALYSIS_MODEL", "default"))
    tingwu_mock: bool = field(
        default_factory=lambda: os.getenv("TINGWU_MOCK", "0").lower() in {"1", "true", "yes", "on"}
    )
    mic_device: str = field(default_factory=lambda: os.getenv("OPENCLAW_MIC_DEVICE", "auto"))
    mic_rate: int = field(default_factory=lambda: int(os.getenv("OPENCLAW_MIC_RATE", "48000")))
    speaker_device: str = field(
        default_factory=lambda: os.getenv("OPENCLAW_SPEAKER_DEVICE", "hw:3,0")
    )
    smtp_host: str = field(default_factory=lambda: os.getenv("OPENCLAW_SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("OPENCLAW_SMTP_PORT", "587")))
    smtp_username: str = field(default_factory=lambda: os.getenv("OPENCLAW_SMTP_USERNAME", ""))
    smtp_password: str = field(default_factory=lambda: os.getenv("OPENCLAW_SMTP_PASSWORD", ""))
    smtp_from: str = field(default_factory=lambda: os.getenv("OPENCLAW_SMTP_FROM", os.getenv("OPENCLAW_SMTP_USERNAME", "")))
    smtp_tls: bool = field(
        default_factory=lambda: os.getenv("OPENCLAW_SMTP_TLS", "1").lower() in {"1", "true", "yes", "on"}
    )
    enterprise_policy_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("OPENCLAW_ENTERPRISE_POLICY_PATH", _runtime_root() / "enterprise_policy.json")
        ).expanduser()
    )
    cloud_ai_enabled: bool = field(
        default_factory=lambda: _env_bool("OPENCLAW_CLOUD_AI_ENABLED", not _env_bool("OPENCLAW_DISABLE_CLOUD", False))
    )
    audit_signing_key: str = field(default_factory=lambda: os.getenv("OPENCLAW_AUDIT_SIGNING_KEY", ""))
    audit_export_retention_days: int = field(
        default_factory=lambda: int(os.getenv("OPENCLAW_AUDIT_EXPORT_RETENTION_DAYS", "30"))
    )

    @classmethod
    def from_env(cls) -> "OfficeAgentConfig":
        policy_path = Path(
            os.getenv("OPENCLAW_ENTERPRISE_POLICY_PATH", _runtime_root() / "enterprise_policy.json")
        ).expanduser()
        policy = _load_policy(policy_path)
        config = cls(allowed_roots=tuple(_split_paths(os.getenv("OPENCLAW_ALLOWED_ROOTS"))), enterprise_policy_path=policy_path)
        if policy:
            security = policy.get("security") if isinstance(policy.get("security"), dict) else {}
            retention = policy.get("retention") if isinstance(policy.get("retention"), dict) else {}
            if "cloud_ai_enabled" in security and os.getenv("OPENCLAW_CLOUD_AI_ENABLED") is None and os.getenv("OPENCLAW_DISABLE_CLOUD") is None:
                config = replace(config, cloud_ai_enabled=bool(security.get("cloud_ai_enabled")))
            if "audit_export_retention_days" in retention and os.getenv("OPENCLAW_AUDIT_EXPORT_RETENTION_DAYS") is None:
                try:
                    config = replace(config, audit_export_retention_days=int(retention["audit_export_retention_days"]))
                except (TypeError, ValueError):
                    pass
        return config.normalized()

    def normalized(self) -> "OfficeAgentConfig":
        workspace_dir = self.workspace_dir.resolve()
        audit_log_path = self.audit_log_path.resolve()
        allowed = [workspace_dir]
        allowed.extend(path.resolve() for path in self.allowed_roots)
        cloud_ai_enabled = self.cloud_ai_enabled
        openai_api_key = self.openai_api_key if cloud_ai_enabled else ""
        dashscope_api_key = self.dashscope_api_key if cloud_ai_enabled else ""
        groq_api_key = self.groq_api_key if cloud_ai_enabled else ""
        elevenlabs_api_key = self.elevenlabs_api_key if cloud_ai_enabled else ""
        raw_tingwu_api_key = self.tingwu_api_key or dashscope_api_key or os.getenv("DASHSCOPE_API_KEY", "")
        raw_tingwu_app_id = self.tingwu_app_id or os.getenv("TINGWU_APP_ID", "") or os.getenv("TINGWU_MEETING_APP_ID", "")
        tingwu_api_key = _real_tingwu_credential(
            self.tingwu_api_key,
            dashscope_api_key,
            os.getenv("DASHSCOPE_API_KEY", ""),
        ) if cloud_ai_enabled else ""
        tingwu_app_id = _real_tingwu_app_id(
            self.tingwu_app_id,
            os.getenv("TINGWU_APP_ID", ""),
            os.getenv("TINGWU_MEETING_APP_ID", ""),
        )
        return OfficeAgentConfig(
            workspace_dir=workspace_dir,
            audit_log_path=audit_log_path,
            permission_mode=self.permission_mode,
            allowed_roots=tuple(dict.fromkeys(allowed)),
            enable_hardware=self.enable_hardware,
            hardware_port=self.hardware_port,
            lamp_id=self.lamp_id,
            projection_dir=self.projection_dir.resolve(),
            memory_path=self.memory_path.resolve(),
            meeting_mode_enabled=self.meeting_mode_enabled,
            desktop_backend=self.desktop_backend,
            browser_automation_headless=self.browser_automation_headless,
            browser_automation_timeout_ms=max(1000, self.browser_automation_timeout_ms),
            browser_automation_max_steps=max(1, self.browser_automation_max_steps),
            smart_home_provider=self.smart_home_provider,
            home_assistant_url=self.home_assistant_url.rstrip("/"),
            home_assistant_token=self.home_assistant_token,
            smart_home_webhook_url=self.smart_home_webhook_url,
            smart_home_entities=self.smart_home_entities,
            mobile_bridge_webhook_url=self.mobile_bridge_webhook_url,
            mobile_bridge_shared_secret=self.mobile_bridge_shared_secret,
            mobile_bridge_device_id=self.mobile_bridge_device_id,
            openai_api_key=openai_api_key,
            openai_base_url=self.openai_base_url.rstrip("/"),
            openai_model=self.openai_model,
            openai_vision_model=self.openai_vision_model,
            openai_reasoning_effort=self.openai_reasoning_effort,
            asr_provider=self.asr_provider,
            asr_model=self.asr_model,
            groq_api_key=groq_api_key,
            tts_provider=self.tts_provider,
            tts_model=self.tts_model,
            tts_voice=self.tts_voice,
            elevenlabs_api_key=elevenlabs_api_key,
            elevenlabs_voice_id=self.elevenlabs_voice_id,
            elevenlabs_model_id=self.elevenlabs_model_id,
            dashscope_api_key=dashscope_api_key,
            dashscope_asr_model=self.dashscope_asr_model,
            dashscope_asr_sample_rate=self.dashscope_asr_sample_rate,
            dashscope_tts_model=self.dashscope_tts_model,
            dashscope_tts_voice=self.dashscope_tts_voice,
            dashscope_tts_url=self.dashscope_tts_url,
            dashscope_vision_model=self.dashscope_vision_model,
            dashscope_vision_base_url=self.dashscope_vision_base_url.rstrip("/"),
            dashscope_vision_wire_api=self.dashscope_vision_wire_api,
            dashscope_text_model=self.dashscope_text_model,
            dashscope_realtime_model=self.dashscope_realtime_model,
            dashscope_realtime_url=self.dashscope_realtime_url.rstrip("/"),
            dashscope_realtime_voice=self.dashscope_realtime_voice,
            dashscope_realtime_transcription_model=self.dashscope_realtime_transcription_model,
            tingwu_api_key=tingwu_api_key,
            tingwu_app_id=tingwu_app_id,
            tingwu_api_key_kind=tingwu_credential_kind(raw_tingwu_api_key) if cloud_ai_enabled else "missing",
            tingwu_app_id_kind=tingwu_credential_kind(raw_tingwu_app_id, role="app_id"),
            tingwu_http_url=self.tingwu_http_url.rstrip("/"),
            tingwu_ws_url=self.tingwu_ws_url,
            tingwu_audio_format=self.tingwu_audio_format,
            tingwu_sample_rate=self.tingwu_sample_rate,
            tingwu_pcm_gain=max(0.1, min(64.0, float(self.tingwu_pcm_gain))),
            tingwu_audio_file=self.tingwu_audio_file,
            tingwu_audio_file_speed=max(0.1, min(50.0, float(self.tingwu_audio_file_speed))),
            tingwu_preflight_capture_seconds=max(1, self.tingwu_preflight_capture_seconds),
            tingwu_transcription_model=self.tingwu_transcription_model,
            tingwu_language_hints=self.tingwu_language_hints,
            tingwu_analysis_model=self.tingwu_analysis_model,
            tingwu_mock=self.tingwu_mock,
            mic_device=(self.mic_device.strip() or "auto"),
            mic_rate=self.mic_rate,
            speaker_device=self.speaker_device,
            smtp_host=self.smtp_host,
            smtp_port=self.smtp_port,
            smtp_username=self.smtp_username,
            smtp_password=self.smtp_password,
            smtp_from=self.smtp_from,
            smtp_tls=self.smtp_tls,
            enterprise_policy_path=self.enterprise_policy_path.resolve(),
            cloud_ai_enabled=cloud_ai_enabled,
            audit_signing_key=self.audit_signing_key,
            audit_export_retention_days=max(1, self.audit_export_retention_days),
        )
