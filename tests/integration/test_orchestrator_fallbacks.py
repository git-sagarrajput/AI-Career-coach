from __future__ import annotations

import re

import pytest
from fakes import FakeLLMClient
from integration_helpers import fixture_json, ml_request

from career_coach.analysis.orchestrator import run_analysis
from career_coach.analysis.rules import assert_report_integrity
from career_coach.errors import (
    InputValidationError,
    LLMAuthenticationError,
    LLMQuotaError,
    LLMResponseValidationError,
)
from career_coach.models.enums import AssessmentMethod, LLMStatus, ReportStatus
from career_coach.models.requests import AnalysisRequest, UploadedInput

EXTRACT = fixture_json("llm_responses/extract_ml_engineer.json")
ASSESS = fixture_json("llm_responses/assess_ml_engineer.json")
COACH = fixture_json("llm_responses/coach_ml_engineer.json")


def run(settings, script):
    llm = FakeLLMClient(script) if script is not None else None
    report = run_analysis(ml_request(), settings=settings, llm=llm)
    assert_report_integrity(report)
    return report, llm


def test_t67_no_llm(settings):
    report, _ = run(settings, None)
    assert report.status == ReportStatus.DETERMINISTIC_ONLY
    assert report.llm_status == LLMStatus.NOT_CONFIGURED
    assert report.executive_summary.generated_by == "deterministic"
    assert all(m.method == AssessmentMethod.DETERMINISTIC_KEYWORD for m in report.matches)
    assert report.matches and not report.strengths and not report.resume_suggestions


def test_t67_auth_error_on_call1(settings):
    report, llm = run(settings, {"extract_requirements": [LLMAuthenticationError()]})
    assert report.status == ReportStatus.DETERMINISTIC_ONLY
    assert report.llm_status == LLMStatus.AUTH_ERROR
    assert len(llm.calls) == 1


def test_t67_invalid_call2(settings):
    report, _ = run(
        settings, {"extract_requirements": [EXTRACT], "assess_evidence": [LLMResponseValidationError()]}
    )
    assert report.status == ReportStatus.PARTIAL
    assert report.llm_status == LLMStatus.INVALID_OUTPUT
    assert all(m.requirement.origin == "llm" for m in report.matches)
    assert all(m.method == AssessmentMethod.DETERMINISTIC_KEYWORD for m in report.matches)


def test_t67_ungrounded_call2_falls_back(settings):
    bad = {
        "assessments": [
            {
                "requirement_id": f"REQ-0{i}",
                "classification": "supported",
                "evidence": [],
                "explanation": "x",
                "uncertainty": "",
                "clarifying_question": "",
            }
            for i in range(1, 10)
        ]
    }
    report, _ = run(settings, {"extract_requirements": [EXTRACT], "assess_evidence": [bad]})
    assert report.status == ReportStatus.PARTIAL
    assert report.llm_status == LLMStatus.INVALID_OUTPUT


def test_t67_invalid_call3(settings):
    report, _ = run(
        settings,
        {
            "extract_requirements": [EXTRACT],
            "assess_evidence": [ASSESS],
            "coach": [LLMResponseValidationError()],
        },
    )
    assert report.status == ReportStatus.PARTIAL
    assert report.matches[0].method == AssessmentMethod.SEMANTIC_LLM
    assert not report.strengths and not report.development_actions
    assert "coaching_omitted" in {w.code for w in report.diagnostics.warnings}


def test_t67_quota_on_call3(settings):
    report, _ = run(
        settings, {"extract_requirements": [EXTRACT], "assess_evidence": [ASSESS], "coach": [LLMQuotaError()]}
    )
    assert report.status == ReportStatus.PARTIAL
    assert report.llm_status == LLMStatus.QUOTA_EXCEEDED


def test_t67_unverifiable_requirements(settings):
    bad = {
        "requirements": [
            {
                "text": "Invented",
                "category": "other",
                "importance": "required",
                "source_span_ids": ["J999"],
                "source_quote": "nothing",
            }
        ],
        "notes": [],
    }
    report, _ = run(settings, {"extract_requirements": [bad]})
    assert report.status == ReportStatus.DETERMINISTIC_ONLY
    assert report.llm_status == LLMStatus.INVALID_OUTPUT


def test_t68_prompts_exclude_contact_data(settings):
    _, llm = run(settings, {"extract_requirements": [EXTRACT], "assess_evidence": [ASSESS], "coach": [COACH]})
    sent = "\n".join(c["user"] + c["system"] for c in llm.calls)
    assert "alex.rivera@example.com" not in sent
    assert "555-0142" not in sent
    assert "Alex Rivera" not in sent
    assert "resume.docx" not in sent
    assert not re.search(r'"id":"R00[1-5]"', sent)  # contact-section spans


def test_input_errors_raise(settings):
    request = AnalysisRequest(resume=UploadedInput(filename="r.txt", data=b"hello"), jd_text="Python")
    with pytest.raises(Exception) as exc:
        run_analysis(request, settings=settings, llm=None)
    assert exc.value.code == "unsupported_format"
    good = ml_request()
    request = AnalysisRequest(resume=good.resume, jd_text="   x")
    report = run_analysis(request, settings=settings, llm=None)
    assert report.status == ReportStatus.DETERMINISTIC_ONLY
    with pytest.raises(InputValidationError):
        run_analysis(AnalysisRequest(resume=good.resume, jd_text="y" * 60_000), settings=settings, llm=None)


def test_jd_text_wins_over_file(settings):
    good = ml_request()
    request = AnalysisRequest(resume=good.resume, jd_text=good.jd_text, jd_file=good.resume)
    report = run_analysis(request, settings=settings, llm=None)
    assert "jd_text_preferred" in {w.code for w in report.diagnostics.warnings}
