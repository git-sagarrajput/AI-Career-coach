from __future__ import annotations

import pytest

from career_coach.analysis.skill_matching import deterministic_classify, find_skill_mentions, skills_in_text
from career_coach.models.analysis import JobRequirement
from career_coach.models.documents import EvidenceSpan
from career_coach.models.enums import (
    AlignmentClassification,
    AssessmentMethod,
    BlockKind,
    DocumentRole,
    Importance,
    ImportanceSource,
    RequirementCategory,
    SectionKind,
)


def span(sid: str, text: str, section: SectionKind = SectionKind.EXPERIENCE) -> EvidenceSpan:
    return EvidenceSpan(
        id=sid,
        role=DocumentRole.RESUME,
        section_kind=section,
        text=text,
        raw_text=text,
        block_kind=BlockKind.LIST_ITEM,
    )


def req(text: str, taxonomy) -> JobRequirement:
    return JobRequirement(
        id="REQ-01",
        text=text,
        category=RequirementCategory.TECHNICAL_SKILL,
        importance=Importance.REQUIRED,
        importance_source=ImportanceSource.DEFAULT,
        source_span_ids=["J001"],
        origin="deterministic",
        taxonomy_skill_ids=skills_in_text(text, taxonomy),
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Built UIs in JavaScript", ["javascript"]),
        ("Backend in Java", ["java"]),
        ("Engine in C++ and C", ["cpp", "c"]),
        ("Web apps in ASP.NET", ["aspnet"]),
        ("Services on .NET", ["dotnet"]),
        ("Scripting in Python.", ["python"]),
        ("Used C# daily", ["csharp"]),
        ("Ran Node.js services", ["nodejs"]),
        ("Pipelines with CI/CD", ["cicd"]),
    ],
)
def test_t39_boundaries_and_longest_match(taxonomy, text, expected):
    assert skills_in_text(text, taxonomy) == expected


def test_t40_ambiguous_and_alias(taxonomy):
    mentions = find_skill_mentions(
        [span("R001", "Wrote Go services"), span("R002", "Wrote golang tools")], taxonomy
    )
    by_span = {m.span_id: m for m in mentions}
    assert by_span["R001"].ambiguous and by_span["R001"].match_type == "exact"
    assert by_span["R002"].skill_id == "go" and by_span["R002"].match_type == "alias"
    assert skills_in_text("go to the store", taxonomy) == []


def test_t41_repeated_evidence_kept(taxonomy):
    spans = [span(f"R00{i}", "Python scripting with Python") for i in range(1, 4)]
    mentions = [m for m in find_skill_mentions(spans, taxonomy) if m.skill_id == "python"]
    assert [m.span_id for m in mentions] == ["R001", "R002", "R003"]


def test_t42_deterministic_classify_table(taxonomy):
    resume = [
        span("R001", "Built APIs in Python and SQL"),
        span("R002", "Terraform, Docker", SectionKind.SKILLS),
    ]
    mentions = find_skill_mentions(resume, taxonomy)

    partial = deterministic_classify(req("Python and SQL", taxonomy), resume, mentions, taxonomy)
    assert partial.classification == AlignmentClassification.PARTIAL
    assert partial.method == AssessmentMethod.DETERMINISTIC_KEYWORD
    assert {e.span_id for e in partial.evidence} == {"R001"}

    skills_only = deterministic_classify(req("Terraform experience", taxonomy), resume, mentions, taxonomy)
    assert skills_only.classification == AlignmentClassification.AMBIGUOUS

    some = deterministic_classify(req("Python and Kubernetes", taxonomy), resume, mentions, taxonomy)
    assert some.classification == AlignmentClassification.AMBIGUOUS

    none = deterministic_classify(req("Kubernetes", taxonomy), resume, mentions, taxonomy)
    assert none.classification == AlignmentClassification.NOT_EVIDENCED and not none.evidence

    vague = deterministic_classify(req("Rockstar mindset", taxonomy), resume, mentions, taxonomy)
    assert vague.classification == AlignmentClassification.AMBIGUOUS
    assert "no recognised skill terms" in vague.explanation.lower()

    for match in (partial, skills_only, some, none, vague):
        assert match.classification != AlignmentClassification.SUPPORTED
