from __future__ import annotations

import pytest

from career_coach.analysis import rules
from career_coach.analysis.skill_matching import build_taxonomy_hints, find_skill_mentions, skills_in_text
from career_coach.errors import UnexpectedError
from career_coach.llm.schemas import AssessmentResponse, CoachingResponse, RequirementExtractionResponse
from career_coach.models.analysis import JobRequirement
from career_coach.models.documents import EvidenceSpan
from career_coach.models.enums import (
    AlignmentClassification as AC,
)
from career_coach.models.enums import (
    BlockKind,
    DocumentRole,
    Importance,
    ImportanceSource,
    RequirementCategory,
    SectionKind,
)


def jspan(sid, text, section=SectionKind.REQUIREMENTS):
    return EvidenceSpan(
        id=sid,
        role=DocumentRole.JOB_DESCRIPTION,
        section_kind=section,
        text=text,
        raw_text=text,
        block_kind=BlockKind.LIST_ITEM,
    )


def rspan(sid, text, section=SectionKind.EXPERIENCE):
    return EvidenceSpan(
        id=sid,
        role=DocumentRole.RESUME,
        section_kind=section,
        text=text,
        raw_text=text,
        block_kind=BlockKind.LIST_ITEM,
    )


JD = {
    "J001": jspan("J001", "Strong SQL skills."),
    "J002": jspan("J002", "Experience with Kubernetes is a must."),
    "J003": jspan("J003", "Terraform experience.", SectionKind.PREFERRED),
    "J004": jspan("J004", "You will build dashboards.", SectionKind.RESPONSIBILITIES),
}
ORDER = {k: i for i, k in enumerate(JD)}
RESUME = {
    "R001": rspan("R001", "Wrote SQL queries for reporting."),
    "R002": rspan("R002", "Improved API latency by 30% using caching."),
    "R003": rspan("R003", "Kubernetes, Docker", SectionKind.SKILLS),
    "R004": rspan("R004", "Improved API performance for the recommendation service."),
}


def wreq(text, ids, quote, importance="unspecified", category="technical_skill"):
    return {
        "text": text,
        "category": category,
        "importance": importance,
        "source_span_ids": ids,
        "source_quote": quote,
    }


def make_req(rid, text, taxonomy, importance=Importance.REQUIRED):
    return JobRequirement(
        id=rid,
        text=text,
        category=RequirementCategory.TECHNICAL_SKILL,
        importance=importance,
        importance_source=ImportanceSource.JD_CUE,
        source_span_ids=["J001"],
        origin="llm",
        taxonomy_skill_ids=skills_in_text(text, taxonomy),
    )


# ---------------------------------------------------------------- Q rules


def test_t46_q1_q2_q3_q4_q5(taxonomy):
    resp = RequirementExtractionResponse.model_validate(
        {
            "requirements": [
                wreq("Kubernetes experience", ["J002", "J999"], "Experience with Kubernetes"),
                wreq("Strong SQL skills", ["J001"], "strong SQL skills"),
                wreq("Strong SQL skills.", ["J001"], "SQL skills"),
                wreq("Terraform experience", ["J003"], "Terraform experience"),
                wreq("Build dashboards", ["J004"], "build dashboards", importance="preferred"),
            ],
            "notes": [],
        }
    )
    out = rules.finalize_requirements(resp, JD, ORDER, taxonomy)
    assert not out.failed
    reqs = out.requirements
    assert [r.id for r in reqs] == ["REQ-01", "REQ-02", "REQ-03", "REQ-04"]
    assert reqs[0].text == "Strong SQL skills"  # sorted by JD order, duplicate merged
    assert reqs[1].source_span_ids == ["J002"]  # Q1 removed J999
    assert reqs[1].importance == Importance.REQUIRED and reqs[1].importance_source == ImportanceSource.JD_CUE
    assert reqs[2].importance == Importance.PREFERRED  # all cited spans preferred
    assert reqs[3].importance == Importance.PREFERRED and reqs[3].importance_source == ImportanceSource.MODEL
    assert "kubernetes" in reqs[1].taxonomy_skill_ids


def test_t46_q2_quote_mismatch_and_q6_failure(taxonomy):
    resp = RequirementExtractionResponse.model_validate(
        {
            "requirements": [
                wreq("Python", ["J001"], "Ten years of Python required"),
                wreq("Go", ["J999"], "Go"),
                wreq("SQL", ["J001"], "SQL skills"),
            ],
            "notes": [],
        }
    )
    out = rules.finalize_requirements(resp, JD, ORDER, taxonomy)
    assert out.failed
    assert out.log.rejected["requirement_quote_mismatch"] == 1
    assert out.log.rejected["requirement_citation_failed"] == 1
    assert rules.finalize_requirements(
        RequirementExtractionResponse(requirements=[], notes=[]), JD, ORDER, taxonomy
    ).failed


def test_q5_cap(taxonomy):
    jd = {f"J{i:03d}": jspan(f"J{i:03d}", f"Skill number {i} required") for i in range(1, 31)}
    order = {k: i for i, k in enumerate(jd)}
    resp = RequirementExtractionResponse.model_validate(
        {
            "requirements": [
                wreq(f"Skill number {i} unique{'x' * i}", [f"J{i:03d}"], f"Skill number {i}")
                for i in range(1, 31)
            ],
            "notes": [],
        }
    )
    out = rules.finalize_requirements(resp, jd, order, taxonomy)
    assert len(out.requirements) == 25


# ---------------------------------------------------------------- R rules


def _assess(items):
    return AssessmentResponse.model_validate({"assessments": items})


def _a(rid, cls, evidence=(), explanation="x", uncertainty="", question=""):
    return {
        "requirement_id": rid,
        "classification": cls,
        "evidence": [{"span_id": e, "relevance_note": "n"} for e in evidence],
        "explanation": explanation,
        "uncertainty": uncertainty,
        "clarifying_question": question,
    }


def test_t47_r0_to_r6(taxonomy):
    reqs = [
        make_req("REQ-01", "SQL", taxonomy),
        make_req("REQ-02", "Kubernetes", taxonomy),
        make_req("REQ-03", "Leadership vision", taxonomy),
        make_req("REQ-04", "Caching", taxonomy),
        make_req("REQ-05", "Python", taxonomy),
        make_req("REQ-06", "Docker", taxonomy),
    ]
    mentions = find_skill_mentions(list(RESUME.values()), taxonomy)
    hints = build_taxonomy_hints(reqs, list(RESUME.values()), mentions, taxonomy)
    resp = _assess(
        [
            _a("REQ-01", "supported", ["R001", "R999"]),
            _a("REQ-02", "not_evidenced", explanation="The candidate lacks Kubernetes."),
            _a("REQ-03", "supported", []),
            _a("REQ-04", "not_evidenced", ["R002"]),
            _a("REQ-05", "partial", ["R002"]),
            _a("REQ-05", "supported", ["R001"]),
            _a("REQ-77", "supported", ["R001"]),
        ]
    )
    out = rules.reconcile_assessments(resp, reqs, RESUME, hints, taxonomy)
    m = {x.requirement.id: x for x in out.matches}
    assert [e.span_id for e in m["REQ-01"].evidence] == ["R001"]  # R0
    assert m["REQ-01"].evidence[0].excerpt == RESUME["R001"].text
    assert out.log.rejected["invalid_evidence_id_removed"] == 1
    assert m["REQ-02"].classification == AC.AMBIGUOUS and "R3" in m["REQ-02"].rule_adjustments  # R3
    assert m["REQ-02"].evidence[0].method.value == "taxonomy"
    assert "lacks" not in m["REQ-02"].explanation and "R6" in m["REQ-02"].rule_adjustments  # R6
    assert m["REQ-03"].classification == AC.AMBIGUOUS and "R1" in m["REQ-03"].rule_adjustments  # R1
    assert m["REQ-04"].classification == AC.AMBIGUOUS and "R2" in m["REQ-04"].rule_adjustments  # R2
    assert (
        m["REQ-05"].classification == AC.PARTIAL and "R5" in m["REQ-05"].rule_adjustments
    )  # dup kept first, R5
    assert m["REQ-06"].explanation == "Not assessed by the AI." and "R4" in m["REQ-06"].rule_adjustments  # R4
    assert out.log.rejected["unknown_requirement_id"] == 1
    assert not out.failed


def test_r_failure_threshold(taxonomy):
    reqs = [make_req(f"REQ-0{i}", "SQL", taxonomy) for i in range(1, 5)]
    resp = _assess([_a("REQ-01", "supported", []), _a("REQ-02", "supported", [])])
    assert rules.reconcile_assessments(resp, reqs, RESUME, [], taxonomy).failed


def test_lack_language():
    assert rules.fix_lack_language("The candidate lacks Python.") == (
        "The resume does not show Python.",
        True,
    )
    assert rules.fix_lack_language("Note: you lack AWS")[0] == "Note: your resume does not show AWS"


# ---------------------------------------------------------------- G rules


@pytest.mark.parametrize(
    ("rewrite", "reason"),
    [
        ("Improved API performance by 40% for the recommendation service.", "introduced_number"),
        ("Improved API performance for the recommendation service on Kubernetes.", "introduced_skill"),
        ("Doubled API performance for the recommendation service.", "introduced_quantifier"),
        ("Improved API performance for the recommendation service", "no_change"),
        ("Improved API performance for the recommendation service, saving $10k.", "introduced_number"),
        ("Improved API performance " + "really " * 60, "too_long"),
    ],
)
def test_t48_rewrite_guard_rejects(taxonomy, rewrite, reason):
    verdict = rules.rewrite_guard(RESUME["R004"].text, rewrite, taxonomy)
    assert verdict is not None and verdict[0] == reason


def test_t49_rewrite_guard_accepts_existing_metric(taxonomy):
    assert (
        rules.rewrite_guard(RESUME["R002"].text, "Cut API latency by 30% by introducing caching.", taxonomy)
        is None
    )


def test_t50_outcome_claims_removed():
    text, removed = rules.remove_outcome_claims("Strong SQL evidence. You have a 90% chance of being hired.")
    assert removed and text == "Strong SQL evidence."
    assert rules.remove_outcome_claims("You are a perfect fit.")[0] == ""


def test_g_rules(taxonomy):
    reqs = [make_req("REQ-01", "SQL", taxonomy)]
    matches = rules.reconcile_assessments(
        _assess([_a("REQ-01", "supported", ["R001"])]), reqs, RESUME, [], taxonomy
    ).matches
    resp = CoachingResponse.model_validate(
        {
            "executive_summary": {
                "overview": "Good SQL. This guarantees an interview.",
                "strongest_areas": ["SQL"],
                "key_gaps": [],
                "next_steps": ["Perfect fit for the role."],
            },
            "strengths": [
                {
                    "title": "SQL",
                    "description": "Wrote SQL.",
                    "evidence_span_ids": ["R001"],
                    "related_requirement_ids": ["REQ-01", "REQ-99"],
                },
                {
                    "title": "Fake",
                    "description": "x",
                    "evidence_span_ids": ["R999"],
                    "related_requirement_ids": [],
                },
            ],
            "resume_suggestions": [
                {
                    "target_span_id": "R004",
                    "issue": "Vague",
                    "suggested_rewrite": "Improved recommendation-service "
                    "API performance; add the measured result if you have one.",
                    "rationale": "Clarity",
                    "needs_user_input": True,
                },
                {
                    "target_span_id": "R999",
                    "issue": "x",
                    "suggested_rewrite": "y",
                    "rationale": "z",
                    "needs_user_input": False,
                },
            ],
            "development_actions": [
                {
                    "priority": 5,
                    "action": "B",
                    "reason": "r",
                    "next_step": "n",
                    "effort": "small",
                    "related_requirement_ids": ["REQ-01"],
                },
                {
                    "priority": 2,
                    "action": "A",
                    "reason": "r",
                    "next_step": "n",
                    "effort": "large",
                    "related_requirement_ids": [],
                },
            ],
            "clarifying_questions": [
                {"requirement_id": "REQ-01", "question": "Q?"},
                {"requirement_id": "REQ-50", "question": "Q?"},
            ],
        }
    )
    out = rules.finalize_coaching(resp, matches, RESUME, taxonomy)
    assert out.summary.overview == "Good SQL."  # G3
    assert out.summary.next_steps == []
    assert len(out.strengths) == 1 and out.strengths[0].related_requirement_ids == ["REQ-01"]  # G1, G4
    assert [s.target_span_id for s in out.suggestions] == ["R004"]  # G2
    assert out.suggestions[0].original_text == RESUME["R004"].text
    assert [(a.priority, a.action) for a in out.actions] == [(1, "A"), (2, "B")]  # G5
    assert [q.requirement_id for q in out.questions] == ["REQ-01"]
    assert out.log.rejected["outcome_claim_removed"] == 2


# ---------------------------------------------------------------- coverage / integrity


def test_t51_coverage(taxonomy):
    reqs = [
        make_req("REQ-01", "SQL", taxonomy),
        make_req("REQ-02", "Kubernetes", taxonomy, Importance.PREFERRED),
    ]
    matches = rules.reconcile_assessments(
        _assess([_a("REQ-01", "supported", ["R001"]), _a("REQ-02", "not_evidenced")]),
        reqs,
        RESUME,
        [],
        taxonomy,
    ).matches
    cov = rules.compute_coverage(matches)
    assert cov.total_requirements == 2
    assert cov.counts[Importance.REQUIRED][AC.SUPPORTED] == 1
    assert cov.counts[Importance.PREFERRED][AC.NOT_EVIDENCED] == 1
    assert all(len(v) == 4 for v in cov.counts.values()) and len(cov.counts) == 3
    assert not any("percent" in f or "score" in f for f in type(cov).model_fields)


def test_t52_integrity_check_raises(taxonomy):
    from integration_helpers import minimal_report

    report = minimal_report()
    rules.assert_report_integrity(report)
    broken = report.model_copy(update={"resume_spans": {}})
    with pytest.raises(UnexpectedError):
        rules.assert_report_integrity(broken)
