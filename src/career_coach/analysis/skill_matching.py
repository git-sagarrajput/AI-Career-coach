from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from career_coach.analysis.taxonomy import CompiledTaxonomy
from career_coach.models.analysis import EvidenceRef, JobRequirement, RequirementMatch
from career_coach.models.documents import EvidenceSpan
from career_coach.models.enums import AlignmentClassification, AssessmentMethod, SectionKind

STRONG_SECTIONS = {SectionKind.EXPERIENCE, SectionKind.PROJECTS}
HINT_SECTIONS = {SectionKind.EXPERIENCE, SectionKind.PROJECTS, SectionKind.SKILLS}
MAX_HINT_SPANS = 5


@dataclass(frozen=True)
class SkillMention:
    skill_id: str
    span_id: str
    matched_text: str
    match_type: Literal["exact", "alias"]
    ambiguous: bool
    start: int = 0


@dataclass(frozen=True)
class TaxonomyHint:
    requirement_id: str
    skill_id: str
    skill_name: str
    resume_span_ids: tuple[str, ...]


def _raw_matches(text: str, taxonomy: CompiledTaxonomy) -> list[tuple[int, int, str, str]]:
    found: list[tuple[int, int, str, str]] = []
    if taxonomy.ci_regex is not None:
        for m in taxonomy.ci_regex.finditer(text):
            found.append((m.start(), m.end(), taxonomy.ci_lookup[m.group(0).casefold()], m.group(0)))
    if taxonomy.cs_regex is not None:
        for m in taxonomy.cs_regex.finditer(text):
            found.append((m.start(), m.end(), taxonomy.cs_lookup[m.group(0)], m.group(0)))
    # Longest match wins on overlap.
    found.sort(key=lambda f: (f[0], -(f[1] - f[0])))
    accepted: list[tuple[int, int, str, str]] = []
    last_end = -1
    for item in found:
        if item[0] >= last_end:
            accepted.append(item)
            last_end = item[1]
    return accepted


def skills_in_text(text: str, taxonomy: CompiledTaxonomy) -> list[str]:
    return list(dict.fromkeys(skill_id for _, _, skill_id, _ in _raw_matches(text, taxonomy)))


def find_skill_mentions(spans: list[EvidenceSpan], taxonomy: CompiledTaxonomy) -> list[SkillMention]:
    mentions: list[SkillMention] = []
    for span in spans:
        seen: set[str] = set()
        for start, _end, skill_id, matched in _raw_matches(span.text, taxonomy):
            if skill_id in seen:
                continue
            seen.add(skill_id)
            skill = taxonomy.skills[skill_id]
            match_type = "exact" if matched.casefold() == skill.name.casefold() else "alias"
            mentions.append(SkillMention(skill_id, span.id, matched, match_type, skill.ambiguous, start))
    mentions.sort(key=lambda m: (m.span_id, m.start))
    return mentions


def _mentions_by_skill(mentions: list[SkillMention]) -> dict[str, list[SkillMention]]:
    out: dict[str, list[SkillMention]] = {}
    for mention in mentions:
        out.setdefault(mention.skill_id, []).append(mention)
    return out


def build_taxonomy_hints(
    requirements: list[JobRequirement],
    resume_spans: list[EvidenceSpan],
    resume_mentions: list[SkillMention],
    taxonomy: CompiledTaxonomy,
) -> list[TaxonomyHint]:
    by_id = {s.id: s for s in resume_spans}
    by_skill = _mentions_by_skill([m for m in resume_mentions if m.span_id in by_id])
    hints: list[TaxonomyHint] = []
    for req in requirements:
        for skill_id in req.taxonomy_skill_ids:
            span_ids = sorted(
                {
                    m.span_id
                    for m in by_skill.get(skill_id, [])
                    if by_id[m.span_id].section_kind in HINT_SECTIONS
                }
            )[:MAX_HINT_SPANS]
            if span_ids:
                hints.append(TaxonomyHint(req.id, skill_id, taxonomy.skills[skill_id].name, tuple(span_ids)))
    return hints


def evidence_ref(span: EvidenceSpan, method: AssessmentMethod, note: str | None = None) -> EvidenceRef:
    excerpt = span.text if len(span.text) <= 300 else span.text[:299] + "…"
    return EvidenceRef(
        span_id=span.id,
        section_kind=span.section_kind,
        page=span.page,
        excerpt=excerpt,
        note=(note[:199] + "…") if note and len(note) > 200 else note,
        method=method,
    )


def deterministic_classify(
    requirement: JobRequirement,
    resume_spans: list[EvidenceSpan],
    resume_mentions: list[SkillMention],
    taxonomy: CompiledTaxonomy,
) -> RequirementMatch:
    """Keyword/taxonomy-only classification. Never returns `supported`."""
    method = AssessmentMethod.DETERMINISTIC_KEYWORD
    by_id = {s.id: s for s in resume_spans if s.section_kind != SectionKind.CONTACT}
    by_skill = _mentions_by_skill([m for m in resume_mentions if m.span_id in by_id])
    skill_ids = requirement.taxonomy_skill_ids
    if not skill_ids:
        return RequirementMatch(
            requirement=requirement,
            classification=AlignmentClassification.AMBIGUOUS,
            explanation="No recognised skill terms; semantic comparison unavailable.",
            uncertainty="Keyword matching cannot assess this requirement; review it manually.",
            method=method,
        )

    names = {sid: taxonomy.skills[sid].name for sid in skill_ids}
    non_ambiguous = [sid for sid in skill_ids if not taxonomy.skills[sid].ambiguous]
    found = {sid: by_skill.get(sid, []) for sid in skill_ids}
    found_any = [sid for sid in skill_ids if found[sid]]

    def strong(sid: str) -> list[str]:
        return sorted({m.span_id for m in found[sid] if by_id[m.span_id].section_kind in STRONG_SECTIONS})

    if not found_any:
        listed = ", ".join(names.values())
        return RequirementMatch(
            requirement=requirement,
            classification=AlignmentClassification.NOT_EVIDENCED,
            explanation=f"None of the recognised terms ({listed}) appear in the supplied resume.",
            uncertainty=(
                "Keyword matching only; equivalent experience described in other words is not detected."
            ),
            clarifying_question=f"Do you have experience with {listed} that is not described in this resume?",
            method=method,
        )

    if non_ambiguous and all(strong(sid) for sid in non_ambiguous):
        span_ids = sorted({sp for sid in non_ambiguous for sp in strong(sid)})[:3]
        return RequirementMatch(
            requirement=requirement,
            classification=AlignmentClassification.PARTIAL,
            evidence=[evidence_ref(by_id[sp], method) for sp in span_ids],
            explanation=(
                "The resume mentions "
                + ", ".join(names[s] for s in non_ambiguous)
                + " in experience or project content."
            ),
            uncertainty=(
                "Keyword presence alone does not show depth or context; AI assessment was unavailable."
            ),
            method=method,
        )

    span_ids = sorted({m.span_id for sid in found_any for m in found[sid]})[:3]
    return RequirementMatch(
        requirement=requirement,
        classification=AlignmentClassification.AMBIGUOUS,
        evidence=[evidence_ref(by_id[sp], method) for sp in span_ids],
        explanation="Some related terms appear (" + ", ".join(names[s] for s in found_any) + ").",
        uncertainty="Terms appear only partially or without usage context (e.g. only in a skills list).",
        clarifying_question="Can you describe where you applied this in a role or project?",
        method=method,
    )
