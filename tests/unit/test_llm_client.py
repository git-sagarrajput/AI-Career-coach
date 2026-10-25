from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest
from fakes import FakeSDK, fake_response
from google.genai import errors as genai_errors
from pydantic import BaseModel

from career_coach.config import load_settings
from career_coach.errors import (
    LLMAuthenticationError,
    LLMBudgetExceededError,
    LLMConfigurationError,
    LLMQuotaError,
    LLMResponseValidationError,
    LLMTransientError,
)
from career_coach.llm.client import (
    CallBudget,
    GeminiClient,
    build_llm_client,
    map_sdk_error,
    strip_code_fences,
)


class Tiny(BaseModel):
    value: str


OK = '{"value": "ok"}'


def client(script, retries: int = 2):
    s = load_settings({"GEMINI_API_KEY": "test-key", "GEMINI_MAX_RETRIES": str(retries)})
    sleeps: list[float] = []
    sdk = FakeSDK(script)
    return GeminiClient(s, sdk_client=sdk, sleep=sleeps.append), sdk, sleeps


def call(c, budget=None):
    return c.generate_structured(
        task="extract_requirements",
        system_instruction="sys",
        user_prompt="user",
        schema=Tiny,
        budget=budget or CallBudget(8, 180),
    )


def server_error(code=503):
    return genai_errors.ServerError(
        code, {"error": {"code": code, "message": "unavailable", "status": "UNAVAILABLE"}}
    )


def client_error(code, message="bad", status="INVALID_ARGUMENT", details=None):
    return genai_errors.ClientError(
        code, {"error": {"code": code, "message": message, "status": status, "details": details or []}}
    )


def test_build_client_none_without_key():
    assert build_llm_client(load_settings({})) is None


def test_t54_transient_then_ok():
    c, sdk, sleeps = client([server_error(), server_error(), fake_response(OK)])
    result = call(c)
    assert result.parsed.value == "ok"
    assert result.metrics.attempts == 3 and result.metrics.outcome == "ok"
    assert len(sleeps) == 2 and sleeps[0] >= 2.0 and sleeps[1] >= 4.0
    assert result.metrics.input_tokens == 10 and result.metrics.output_tokens == 5


def test_transient_exhausted():
    c, _, _ = client([server_error()] * 3)
    with pytest.raises(LLMTransientError):
        call(c)


def test_t55_quota_long_delay_not_retried():
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s"}]
    c, sdk, sleeps = client([client_error(429, "quota", "RESOURCE_EXHAUSTED", details), fake_response(OK)])
    with pytest.raises(LLMQuotaError) as exc:
        call(c)
    assert not sleeps and len(sdk.requests) == 1
    assert exc.value.metrics.error_category.value == "llm_quota"


def test_quota_short_delay_retried_once():
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3s"}]
    c, _, sleeps = client([client_error(429, "quota", "RESOURCE_EXHAUSTED", details), fake_response(OK)])
    assert call(c).parsed.value == "ok"
    assert sleeps == [3.0]


def test_t56_auth_and_model_errors():
    c, _, _ = client([client_error(400, "API key not valid. Please pass a valid API key.")])
    with pytest.raises(LLMAuthenticationError):
        call(c)
    c, _, _ = client([client_error(404, "models/x is not found", "NOT_FOUND")])
    with pytest.raises(LLMConfigurationError) as exc:
        call(c)
    assert exc.value.code == "model_not_found"


def test_network_errors_map_to_transient():
    assert isinstance(map_sdk_error(httpx.ConnectTimeout("t"), "m"), LLMTransientError)
    assert isinstance(map_sdk_error(TimeoutError(), "m"), LLMTransientError)


def test_t57_repair_then_valid():
    c, sdk, _ = client([fake_response('{"wrong": 1}'), fake_response(OK)])
    result = call(c)
    assert result.metrics.repaired is True
    repair_prompt = sdk.requests[1]["contents"]
    assert "value" in repair_prompt and "Field required" in repair_prompt
    assert '"wrong"' not in repair_prompt.split("Validation errors")[1]


def test_t58_invalid_twice():
    c, _, _ = client([fake_response("not json"), fake_response('{"x": 1}')])
    with pytest.raises(LLMResponseValidationError) as exc:
        call(c)
    assert exc.value.code == "schema_invalid"


def test_blocked_or_empty():
    c, _, _ = client([fake_response(None)])
    with pytest.raises(LLMResponseValidationError) as exc:
        call(c)
    assert exc.value.code == "blocked_or_empty"
    c, _, _ = client([fake_response(OK, blocked=True)])
    with pytest.raises(LLMResponseValidationError):
        call(c)


def test_t59_budget_exhausted_no_sdk_call():
    c, sdk, _ = client([fake_response(OK)])
    with pytest.raises(LLMBudgetExceededError):
        call(c, CallBudget(0, 180))
    assert sdk.requests == []
    clock = SimpleNamespace(t=time.monotonic())
    budget = CallBudget(5, 10, clock=lambda: clock.t)
    clock.t += 11
    with pytest.raises(LLMBudgetExceededError):
        call(c, budget)


def test_t60_fenced_json():
    assert strip_code_fences('```json\n{"value": "ok"}\n```') == '{"value": "ok"}'
    c, _, _ = client([fake_response('```json\n{"value": "fenced"}\n```')])
    assert call(c).parsed.value == "fenced"


def test_config_passes_schema():
    c, sdk, _ = client([fake_response(OK)])
    call(c)
    config = sdk.requests[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema["properties"]["value"]["type"] == "string"
    assert config.system_instruction == "sys"
