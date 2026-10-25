from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from career_coach.errors import ConfigurationError

# (env var, field name, kind)
_ENV_MAP: list[tuple[str, str, str]] = [
    ("GEMINI_API_KEY", "gemini_api_key", "secret"),
    ("GEMINI_MODEL", "gemini_model", "str"),
    ("GEMINI_TIMEOUT_SECONDS", "gemini_timeout_seconds", "int"),
    ("GEMINI_MAX_RETRIES", "gemini_max_retries", "int"),
    ("GEMINI_MAX_OUTPUT_TOKENS", "gemini_max_output_tokens", "int"),
    ("CAREER_COACH_LLM_TOTAL_BUDGET_SECONDS", "llm_total_budget_seconds", "int"),
    ("CAREER_COACH_MAX_UPLOAD_MB", "max_upload_mb", "int"),
    ("CAREER_COACH_MAX_PAGES", "max_pages", "int"),
    ("CAREER_COACH_MAX_RESUME_CHARS", "max_resume_chars", "int"),
    ("CAREER_COACH_MAX_JD_CHARS", "max_jd_chars", "int"),
    ("CAREER_COACH_LINK_CHECK_ENABLED", "link_check_enabled", "bool"),
    ("CAREER_COACH_LINK_MAX_URLS", "link_max_urls", "int"),
    ("CAREER_COACH_LINK_WORKERS", "link_workers", "int"),
    ("CAREER_COACH_LINK_TIMEOUT_SECONDS", "link_timeout_seconds", "int"),
    ("CAREER_COACH_LINK_DEADLINE_SECONDS", "link_deadline_seconds", "int"),
    ("CAREER_COACH_OCR_ENABLED", "ocr_enabled", "bool"),
    ("CAREER_COACH_OCR_MAX_PAGES", "ocr_max_pages", "int"),
    ("CAREER_COACH_LOG_LEVEL", "log_level", "str"),
    ("CAREER_COACH_LOG_FILE", "log_file", "str"),
]

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gemini_api_key: SecretStr | None = None
    gemini_model: str = Field(default="gemini-3.8-flash", min_length=1, max_length=100)
    gemini_timeout_seconds: int = Field(default=60, ge=10, le=300)
    gemini_max_retries: int = Field(default=2, ge=0, le=3)
    gemini_max_output_tokens: int = Field(default=8192, ge=1024, le=32768)
    llm_total_budget_seconds: int = Field(default=180, ge=30, le=600)
    max_llm_attempts_per_analysis: int = Field(default=8, ge=3, le=20)
    max_upload_mb: int = Field(default=5, ge=1, le=20)
    max_pages: int = Field(default=10, ge=1, le=50)
    max_resume_chars: int = Field(default=24000, ge=4000, le=60000)
    max_jd_chars: int = Field(default=15000, ge=2000, le=40000)
    max_jd_raw_chars: int = 50000
    max_requirements: int = 25
    link_check_enabled: bool = False
    link_max_urls: int = Field(default=15, ge=0, le=50)
    link_workers: int = Field(default=5, ge=1, le=10)
    link_timeout_seconds: int = Field(default=10, ge=2, le=30)
    link_deadline_seconds: int = Field(default=20, ge=5, le=60)
    ocr_enabled: bool = False
    ocr_max_pages: int = Field(default=3, ge=1, le=10)
    image_only_char_threshold: int = 20
    log_level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR)$")
    log_file: str | None = None

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def llm_configured(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.get_secret_value().strip())

    def fingerprint(self) -> str:
        """Hash of settings that influence parsing (never includes the API key)."""
        parts = f"{self.max_pages}|{self.ocr_enabled}|{self.ocr_max_pages}|{self.image_only_char_threshold}"
        return hashlib.sha256(parts.encode()).hexdigest()[:12]


def _coerce(name: str, kind: str, raw: str) -> Any:
    value = raw.strip()
    if kind == "secret":
        return SecretStr(value) if value else None
    if kind == "bool":
        low = value.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ConfigurationError(f"{name} must be true/false.", code="invalid_configuration")
    if kind == "int":
        try:
            return int(value)
        except ValueError as exc:
            raise ConfigurationError(f"{name} must be an integer.", code="invalid_configuration") from exc
    if kind == "str" and name == "CAREER_COACH_LOG_LEVEL":
        return value.upper()
    return value or None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        from dotenv import load_dotenv

        load_dotenv(override=False)
        env = os.environ
    values: dict[str, Any] = {}
    for var, field, kind in _ENV_MAP:
        raw = env.get(var)
        if raw is None or (raw.strip() == "" and kind not in ("secret",)):
            continue
        coerced = _coerce(var, kind, raw)
        if coerced is not None:
            values[field] = coerced
    try:
        return Settings(**values)
    except ValidationError as exc:
        field_to_var = {f: v for v, f, _ in _ENV_MAP}
        bad = sorted({field_to_var.get(str(e["loc"][0]), str(e["loc"][0])) for e in exc.errors()})
        raise ConfigurationError(
            f"Invalid configuration value(s): {', '.join(bad)}. See .env.example for allowed ranges.",
            code="invalid_configuration",
        ) from None
