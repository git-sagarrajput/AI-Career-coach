"""Test doubles: FakeLLMClient (scripted LLM) and FakeSDK (scripted google-genai surface)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from career_coach.llm.client import CallBudget, LLMResult
from career_coach.models.analysis import LLMCallMetrics


class FakeLLMClient:
    """Implements LLMClient. `script` maps task -> list of responses (dict/BaseModel) or exceptions."""

    def __init__(self, script: dict[str, list[Any]], model_name: str = "fake-model") -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.model_name = model_name
        self.calls: list[dict[str, str]] = []

    def generate_structured(
        self,
        *,
        task: str,
        system_instruction: str,
        user_prompt: str,
        schema: type[BaseModel],
        budget: CallBudget,
    ) -> LLMResult:
        budget.consume_or_raise()
        self.calls.append({"task": task, "system": system_instruction, "user": user_prompt})
        queue = self.script.get(task) or []
        if not queue:
            raise AssertionError(f"FakeLLMClient: no scripted response for {task}")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            item.metrics = LLMCallMetrics(task=task, model=self.model_name, attempts=1, outcome="error")
            raise item
        parsed = item if isinstance(item, BaseModel) else schema.model_validate(item)
        metrics = LLMCallMetrics(
            task=task, model=self.model_name, attempts=1, latency_ms=5, input_tokens=100, output_tokens=50
        )
        return LLMResult(parsed=parsed, metrics=metrics)


def fake_response(text: str | None, *, blocked: bool = False, prompt_tokens: int = 10, out_tokens: int = 5):
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(prompt_token_count=prompt_tokens, candidates_token_count=out_tokens),
        prompt_feedback=SimpleNamespace(block_reason="SAFETY") if blocked else None,
        candidates=[],
    )


class FakeSDK:
    """Mimics `genai.Client().models.generate_content` with scripted results."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        outer = self

        class _Models:
            def generate_content(self, *, model: str, contents: str, config: Any):
                outer.requests.append({"model": model, "contents": contents, "config": config})
                item = outer.script.pop(0)
                if isinstance(item, BaseException):
                    raise item
                if callable(item):
                    return item()
                return item

        self.models = _Models()
