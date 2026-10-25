from __future__ import annotations

from career_coach.ingestion.sections import build_sections, match_lexicon
from career_coach.models.documents import RawBlock
from career_coach.models.enums import DocumentRole, SectionDetection, SectionKind


def _p(text: str, kind: str = "paragraph") -> RawBlock:
    return RawBlock(text=text, kind=kind)


def test_t21_headings_detected():
    blocks = [
        _p("Jane Doe"),
        _p("SKILLS"),
        _p("Python, SQL"),
        _p("Work Experience:"),
        _p("Engineer at X"),
        _p("Projects & Publications"),
        _p("A demo app"),
    ]
    result = build_sections(blocks, DocumentRole.RESUME)
    kinds = [(s.kind, s.detection) for s in result.sections]
    assert kinds == [
        (SectionKind.CONTACT, SectionDetection.NONE),
        (SectionKind.SKILLS, SectionDetection.LEXICON),
        (SectionKind.EXPERIENCE, SectionDetection.LEXICON),
        (SectionKind.PROJECTS, SectionDetection.LEXICON),
    ]
    assert match_lexicon("Skills:", DocumentRole.RESUME) == SectionKind.SKILLS
    assert match_lexicon("Experience with Python", DocumentRole.RESUME) is None
    assert match_lexicon("Nice to have", DocumentRole.JOB_DESCRIPTION) == SectionKind.PREFERRED


def test_style_headings():
    result = build_sections([_p("Education", "heading"), _p("BSc")], DocumentRole.RESUME)
    assert result.sections[0].detection == SectionDetection.STYLE


def test_t22_no_headings_keeps_everything():
    texts = [f"Line number {i} with some content" for i in range(12)]
    result = build_sections([_p(t) for t in texts], DocumentRole.RESUME)
    assert len(result.sections) == 1
    assert result.sections[0].kind == SectionKind.OTHER
    assert "no_sections_detected" in {w.code for w in result.warnings}
    assert sum(len(s.text) for s in result.spans) == sum(len(t) for t in texts)


def test_t23_long_paragraph_split_and_stable_ids():
    sentence = "This sentence describes a responsibility in enough detail to take space. "
    paragraph = (sentence * 21).strip()
    assert len(paragraph) > 1500
    blocks = [_p("Experience"), _p(paragraph)]
    first = build_sections(blocks, DocumentRole.RESUME)
    second = build_sections(blocks, DocumentRole.RESUME)
    assert all(len(s.text) <= 600 for s in first.spans)
    assert len(first.spans) >= 3
    assert [s.id for s in first.spans] == [f"R{i:03d}" for i in range(1, len(first.spans) + 1)]
    assert [s.model_dump() for s in first.spans] == [s.model_dump() for s in second.spans]


def test_jd_prefix_and_ids():
    result = build_sections([_p("Requirements"), _p("Python", "list_item")], DocumentRole.JOB_DESCRIPTION)
    assert result.spans[0].id == "J001"
    assert result.spans[0].section_kind == SectionKind.REQUIREMENTS
