from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from career_coach.config import Settings
from career_coach.errors import (
    CareerCoachError,
    LLMAuthenticationError,
    LLMBudgetExceededError,
    LLMConfigurationError,
    LLMError,
    LLMQuotaError,
    LLMRequestError,
    LLMResponseValidationError,
    LLMTransientError,
)
from career_coach.llm.prompts import REPAIR_SUFFIX
from career_coach.models.analysis import LLMCallMetrics

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)

_MAX_429_DELAY_S = 10.0
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class CallBudget:
    """Tracks remaining HTTP attempts and the wall-clock deadline for all LLM work in one analysis."""

    def __init__(self, max_attempts: int, total_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.remaining = max_attempts
        self._clock = clock
        self.deadline = clock() + total_seconds

    def seconds_left(self) -> float:
        return max(0.0, self.deadline - self._clock())

    def consume_or_raise(self) -> None:
        if self.remaining <= 0 or self.seconds_left() <= 0:
            raise LLMBudgetExceededError()
        self.remaining -= 1


@dataclass
class LLMResult(Generic[T]):
    parsed: T
    metrics: LLMCallMetrics


class LLMClient(Protocol):
    model_name: str

    def generate_structured(
        self, *, task: str, system_instruction: str, user_prompt: str, schema: type[T], budget: CallBudget
    ) -> LLMResult[T]: ...


def strip_code_fences(text: str) -> str:
    match = _FENCE.match(text)
    return match.group(1) if match else text.strip()


def summarize_validation_error(exc: ValidationError, max_errors: int = 10) -> str:
    """Field paths and messages only; never input values (which could echo document text)."""
    lines = []
    for err in exc.errors(include_input=False)[:max_errors]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
        lines.append(f"- {loc}: {err.get('msg', 'invalid')}")
    return "\n".join(lines)


def _retry_delay_seconds(exc: Any) -> float | None:
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        details = details.get("error", {}).get("details", [])
    if not isinstance(details, list):
        return None
    for item in details:
        if isinstance(item, dict) and "retryDelay" in item:
            match = re.match(r"^\s*([\d.]+)s\s*$", str(item["retryDelay"]))
            if match:
                return float(match.group(1))
    return None


def map_sdk_error(exc: BaseException, model: str) -> CareerCoachError:
    if isinstance(exc, CareerCoachError):
        return exc
    try:
        from google.genai import errors as genai_errors
    except ImportError:  # pragma: no cover
        genai_errors = None  # type: ignore[assignment]

    code = getattr(exc, "code", None)
    status = str(getattr(exc, "status", "") or "")
    message = str(getattr(exc, "message", "") or "")
    is_client = genai_errors is not None and isinstance(exc, genai_errors.ClientError)
    is_server = genai_errors is not None and isinstance(exc, genai_errors.ServerError)
    if is_client or (isinstance(code, int) and 400 <= code < 500):
        if code in (401, 403) or (
            code == 400 and ("API key not valid" in message or "API_KEY_INVALID" in message)
        ):
            return LLMAuthenticationError(code="auth_error")
        if code == 404:
            return LLMConfigurationError(
                f"Model '{model}' is not available for this key. "
                "Set GEMINI_MODEL (run scripts/check_model.py).",
                code="model_not_found",
            )
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            return LLMQuotaError(code="quota_exceeded")
        return LLMRequestError(f"The Gemini request was rejected (code {code}).", code="request_rejected")
    if is_server or (isinstance(code, int) and code >= 500):
        return LLMTransientError(code="server_error")
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError | TimeoutError | ConnectionError):
        return LLMTransientError("Could not reach Gemini (network/timeout).", code="network")
    return LLMError("The AI analysis failed unexpectedly.", code="unexpected_llm_error")


def _is_blocked(resp: Any) -> bool:
    feedback = getattr(resp, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        return True
    for cand in getattr(resp, "candidates", None) or []:
        reason = str(getattr(cand, "finish_reason", "") or "")
        if reason.endswith("SAFETY") or reason.endswith("PROHIBITED_CONTENT") or reason.endswith("BLOCKLIST"):
            return True
    return False


class GeminiClient:
    """LLMClient implementation for the Google Gen AI SDK (`google-genai`)."""

    def __init__(
        self,
        settings: Settings,
        sdk_client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.model_name = settings.gemini_model
        self._sleep = sleep
        if sdk_client is None:
            if not settings.llm_configured:
                raise LLMConfigurationError(code="missing_api_key")
            from google import genai
            from google.genai import types

            sdk_client = genai.Client(
                api_key=settings.gemini_api_key.get_secret_value(),  # type: ignore[union-attr]
                http_options=types.HttpOptions(
                    timeout=settings.gemini_timeout_seconds * 1000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        self._sdk = sdk_client

    def _config(self, schema: type[BaseModel], system_instruction: str) -> Any:
        try:
            from google.genai import types
        except ImportError:  # pragma: no cover
            types = None  # type: ignore[assignment]
        kwargs = {
            "system_instruction": system_instruction,
            "response_mime_type": "application/json",
            "response_json_schema": schema.model_json_schema(),
            "max_output_tokens": self.settings.gemini_max_output_tokens,
        }
        return types.GenerateContentConfig(**kwargs) if types is not None else kwargs

    def generate_structured(
        self, *, task: str, system_instruction: str, user_prompt: str, schema: type[T], budget: CallBudget
    ) -> LLMResult[T]:
        metrics = LLMCallMetrics(task=task, model=self.model_name)  # type: ignore[arg-type]
        start = time.perf_counter()
        attempt = 0
        transient_retries = 0
        quota_retried = False
        repair_used = False
        prompt = user_prompt
        config = self._config(schema, system_instruction)

        def finish(outcome: str, error: CareerCoachError | None = None) -> None:
            metrics.attempts = attempt
            metrics.repaired = repair_used
            metrics.latency_ms = int((time.perf_counter() - start) * 1000)
            metrics.outcome = outcome  # type: ignore[assignment]
            metrics.error_category = error.category if error else None
            if error is not None:
                error.metrics = metrics  # type: ignore[attr-defined]

        while True:
            try:
                budget.consume_or_raise()
            except LLMBudgetExceededError as exc:
                finish("error", exc)
                raise
            attempt += 1
            try:
                resp = self._sdk.models.generate_content(
                    model=self.model_name, contents=prompt, config=config
                )
            except Exception as exc:  # noqa: BLE001
                mapped = map_sdk_error(exc, self.model_name)
                logger.warning(
                    "llm attempt failed task=%s attempt=%d type=%s code=%s",
                    task,
                    attempt,
                    type(exc).__name__,
                    mapped.code,
                )
                if (
                    isinstance(mapped, LLMTransientError)
                    and transient_retries < self.settings.gemini_max_retries
                ):
                    transient_retries += 1
                    delay = min(
                        2.0 * (2 ** (transient_retries - 1)) + random.uniform(0, 0.5), budget.seconds_left()
                    )
                    if delay > 0:
                        self._sleep(delay)
                    continue
                if isinstance(mapped, LLMQuotaError) and not quota_retried:
                    delay = _retry_delay_seconds(exc)
                    if delay is not None and delay <= _MAX_429_DELAY_S and delay < budget.seconds_left():
                        quota_retried = True
                        self._sleep(delay)
                        continue
                finish("error", mapped)
                raise mapped from None

            usage = getattr(resp, "usage_metadata", None)
            if usage is not None:
                metrics.input_tokens = getattr(usage, "prompt_token_count", None)
                metrics.output_tokens = getattr(usage, "candidates_token_count", None)
            text = getattr(resp, "text", None)
            if text is None or _is_blocked(resp):
                err = LLMResponseValidationError(
                    "The AI declined or returned an empty response.", code="blocked_or_empty"
                )
                finish("validation_failed", err)
                raise err
            try:
                parsed = schema.model_validate_json(strip_code_fences(text))
            except ValidationError as exc:
                summary = summarize_validation_error(exc)
                logger.warning(
                    "llm output invalid task=%s attempt=%d repaired=%s", task, attempt, repair_used
                )
                if repair_used:
                    err = LLMResponseValidationError(code="schema_invalid", details=summary)
                    finish("validation_failed", err)
                    raise err from None
                repair_used = True
                prompt = user_prompt + REPAIR_SUFFIX.format(errors=summary)
                continue
            finish("ok")
            logger.info(
                "llm call task=%s model=%s attempts=%d ms=%d in_tok=%s out_tok=%s outcome=ok",
                task,
                self.model_name,
                attempt,
                metrics.latency_ms,
                metrics.input_tokens,
                metrics.output_tokens,
            )
            return LLMResult(parsed=parsed, metrics=metrics)


def build_llm_client(settings: Settings) -> LLMClient | None:
    """Return None when GEMINI_API_KEY is not set (caller produces deterministic-only report)."""
    if not settings.llm_configured:
        return None
    return GeminiClient(settings)
