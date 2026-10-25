"""Shared helpers for integration-style tests."""

from __future__ import annotations

import json
from pathlib import Path

from _doc_builders import build_docx

from career_coach.analysis.orchestrator import run_analysis
from career_coach.config import load_settings
from career_coach.models.analysis import AnalysisReport
from career_coach.models.requests import AnalysisRequest, UploadedInput

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_json(relative: str) -> dict:
    return json.loads((FIXTURES / relative).read_text("utf-8"))


def ml_request(check_links: bool = False, **kwargs) -> AnalysisRequest:
    resume = build_docx(fixture_json("resumes/ml_engineer_onnx.json"))
    jd = (FIXTURES / "jd_ml_engineer.txt").read_text("utf-8")
    return AnalysisRequest(
        resume=UploadedInput(filename="resume.docx", data=resume),
        jd_text=jd,
        check_links=check_links,
        **kwargs,
    )


def minimal_report() -> AnalysisReport:
    return run_analysis(ml_request(), settings=load_settings({}), llm=None)
