"""Opt-in live contract test: RUN_LIVE_TESTS=1 pytest -m live (uses Gemini quota, synthetic data only)."""

from __future__ import annotations

import os

import pytest

from career_coach.analysis.rules import finalize_requirements
from career_coach.analysis.taxonomy import load_taxonomy
from career_coach.config import load_settings
from career_coach.ingestion.pipeline import ingest_text
from career_coach.llm.client import CallBudget, GeminiClient
from career_coach.llm.prompts import build_extract_prompt
from career_coach.llm.schemas import RequirementExtractionResponse
from career_coach.models.enums import DocumentRole

pytestmark = pytest.mark.live


@pytest.fixture
def live_settings():
    if os.environ.get("RUN_LIVE_TESTS") != "1":
        pytest.skip("set RUN_LIVE_TESTS=1 to run live tests")
    from dotenv import dotenv_values

    env = {**dotenv_values(), **{k: v for k, v in os.environ.items() if v}}
    settings = load_settings(env)
    if not settings.llm_configured:
        pytest.skip("GEMINI_API_KEY not set")
    return settings


def test_live_requirement_extraction(live_settings, jd_ml_text):
    jd = ingest_text(jd_ml_text, DocumentRole.JOB_DESCRIPTION, live_settings)
    rows = [
        {"id": s.id, "section": s.section_kind.value, "text": s.text, "origin": s.origin.value}
        for s in jd.spans
    ]
    system, user = build_extract_prompt(rows, None, 25)
    client = GeminiClient(live_settings)
    result = client.generate_structured(
        task="extract_requirements",
        system_instruction=system,
        user_prompt=user,
        schema=RequirementExtractionResponse,
        budget=CallBudget(4, 120),
    )
    assert result.parsed.requirements
    jd_ctx = {s.id: s for s in jd.spans}
    outcome = finalize_requirements(
        result.parsed, jd_ctx, {s.id: i for i, s in enumerate(jd.spans)}, load_taxonomy()
    )
    assert not outcome.failed
