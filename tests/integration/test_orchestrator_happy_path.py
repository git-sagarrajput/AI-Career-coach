from __future__ import annotations

import httpx
from fakes import FakeLLMClient
from integration_helpers import fixture_json, ml_request

from career_coach.analysis.orchestrator import run_analysis
from career_coach.analysis.rules import assert_report_integrity
from career_coach.models.enums import AlignmentClassification as AC
from career_coach.models.enums import LLMStatus, ReachabilityStatus, ReportStatus


def golden_llm() -> FakeLLMClient:
    return FakeLLMClient(
        {
            "extract_requirements": [fixture_json("llm_responses/extract_ml_engineer.json")],
            "assess_evidence": [fixture_json("llm_responses/assess_ml_engineer.json")],
            "coach": [fixture_json("llm_responses/coach_ml_engineer.json")],
        }
    )


def test_golden_span_ids_still_match_fixture(settings):
    from career_coach.ingestion.pipeline import ingest_document, ingest_text
    from career_coach.models.enums import DocumentRole

    req = ml_request()
    resume = ingest_document(req.resume.data, "r.docx", DocumentRole.RESUME, settings)
    jd = ingest_text(req.jd_text, DocumentRole.JOB_DESCRIPTION, settings)
    assert "ONNX" in resume.span_by_id("R011").text
    assert "Mentored" in resume.span_by_id("R015").text
    assert "Kubernetes" in jd.span_by_id("J008").text
    assert "mentoring" in jd.span_by_id("J014").text


def test_t66_happy_path(settings):
    llm = golden_llm()
    events = []
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    report = run_analysis(
        ml_request(check_links=True),
        settings=settings,
        llm=llm,
        progress=events.append,
        link_transport=transport,
        link_resolver=lambda h, p: ["93.184.215.14"],
    )
    assert report.status == ReportStatus.COMPLETE
    assert report.llm_status == LLMStatus.OK
    assert [c["task"] for c in llm.calls] == ["extract_requirements", "assess_evidence", "coach"]
    assert len(report.diagnostics.llm_calls) == 3
    assert_report_integrity(report)

    m = {x.requirement.id: x for x in report.matches}
    assert m["REQ-02"].classification == AC.SUPPORTED
    assert [e.span_id for e in m["REQ-02"].evidence] == ["R011"]
    assert m["REQ-03"].classification == AC.NOT_EVIDENCED and "R6" in m["REQ-03"].rule_adjustments
    assert m["REQ-08"].classification == AC.AMBIGUOUS and "R1" in m["REQ-08"].rule_adjustments
    assert report.diagnostics.rejected_items["invalid_evidence_id_removed"] == 1

    assert "90%" not in report.executive_summary.overview
    assert [s.title for s in report.strengths] == ["Production ML serving"]
    assert {s.target_span_id for s in report.resume_suggestions} == {"R014", "R018"}
    assert report.diagnostics.rejected_items["rewrite_rejected"] == 2
    assert [a.priority for a in report.development_actions] == [1, 2]
    assert report.development_actions[0].related_requirement_ids == ["REQ-04"]
    assert {q.requirement_id for q in report.clarifying_questions} >= {"REQ-03"}
    assert "REQ-99" not in {q.requirement_id for q in report.clarifying_questions}

    assert report.link_checks is not None and len(report.link_checks) == len(report.links)
    assert all(c.status == ReachabilityStatus.REACHABLE for c in report.link_checks)
    stages = {(e.stage, e.status) for e in events}
    assert ("requirements", "completed") in stages and ("report", "completed") in stages
    assert set(report.resume_spans) >= {"R011", "R014", "R018"}
    assert all(sid in report.resume_spans for x in report.matches for sid in (e.span_id for e in x.evidence))
