from __future__ import annotations

from career_coach.analysis.rules import _norm, category_from_skills, importance_from_cues
from career_coach.analysis.skill_matching import skills_in_text
from career_coach.analysis.taxonomy import CompiledTaxonomy
from career_coach.models.analysis import JobRequirement
from career_coach.models.documents import ParsedDocument
from career_coach.models.enums import BlockKind, SectionKind

MAX_CANDIDATE_CHARS = 300


def deterministic_requirements(
    jd_doc: ParsedDocument, taxonomy: CompiledTaxonomy, max_requirements: int = 25
) -> list[JobRequirement]:
    """Fallback requirement candidates from JD requirement/preferred sections (or bullets)."""
    spans = [s for s in jd_doc.spans if s.section_kind in (SectionKind.REQUIREMENTS, SectionKind.PREFERRED)]
    if not spans:
        spans = [
            s
            for s in jd_doc.spans
            if s.block_kind == BlockKind.LIST_ITEM
            and s.section_kind not in (SectionKind.ABOUT, SectionKind.BENEFITS)
        ]
    seen: set[str] = set()
    requirements: list[JobRequirement] = []
    for span in spans:
        if len(span.text) > MAX_CANDIDATE_CHARS or len(span.text) < 3:
            continue
        key = _norm(span.text)
        if key in seen:
            continue
        seen.add(key)
        skill_ids = skills_in_text(span.text, taxonomy)
        importance, source = importance_from_cues(span.text, None, [span.section_kind], None)
        requirements.append(
            JobRequirement(
                id=f"REQ-{len(requirements) + 1:02d}",
                text=span.text,
                category=category_from_skills(skill_ids, taxonomy, span.text),
                importance=importance,
                importance_source=source,
                source_span_ids=[span.id],
                source_quote=span.text,
                origin="deterministic",
                taxonomy_skill_ids=skill_ids,
            )
        )
        if len(requirements) >= max_requirements:
            break
    return requirements
