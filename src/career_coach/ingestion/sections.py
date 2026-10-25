from __future__ import annotations

import re
from dataclasses import dataclass

from career_coach.ingestion.normalization import dedupe_key, normalize_block_text
from career_coach.models.documents import EvidenceSpan, ProcessingWarning, RawBlock, Section
from career_coach.models.enums import (
    BlockKind,
    DocumentRole,
    SectionDetection,
    SectionKind,
    Severity,
)

MAX_SPAN_CHARS = 600
_PRE_HEADING_LIMIT = 8

RESUME_LEXICON: dict[SectionKind, tuple[str, ...]] = {
    SectionKind.SUMMARY: (
        "summary",
        "professional summary",
        "profile",
        "professional profile",
        "about me",
        "objective",
        "career objective",
        "career summary",
    ),
    SectionKind.SKILLS: (
        "skills",
        "technical skills",
        "core competencies",
        "competencies",
        "technologies",
        "tools",
        "tech stack",
        "expertise",
        "key skills",
    ),
    SectionKind.EXPERIENCE: (
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "employment history",
        "work history",
        "career history",
        "relevant experience",
    ),
    SectionKind.PROJECTS: ("projects", "personal projects", "selected projects", "key projects", "portfolio"),
    SectionKind.EDUCATION: ("education", "academic background", "qualifications", "education and training"),
    SectionKind.CERTIFICATIONS: (
        "certifications",
        "certificates",
        "licenses",
        "licences",
        "courses",
        "training",
    ),
    SectionKind.OTHER: (
        "awards",
        "publications",
        "volunteering",
        "volunteer experience",
        "languages",
        "interests",
        "achievements",
        "honors",
        "honours",
    ),
}

JD_LEXICON: dict[SectionKind, tuple[str, ...]] = {
    SectionKind.ABOUT: ("about us", "about the company", "who we are", "about the team", "company overview"),
    SectionKind.RESPONSIBILITIES: (
        "responsibilities",
        "key responsibilities",
        "what you'll do",
        "what you will do",
        "the role",
        "duties",
        "your role",
        "role overview",
    ),
    SectionKind.REQUIREMENTS: (
        "requirements",
        "qualifications",
        "what you'll bring",
        "what you will bring",
        "must have",
        "must-have",
        "must haves",
        "minimum qualifications",
        "required skills",
        "required qualifications",
        "basic qualifications",
        "what we're looking for",
        "what we are looking for",
    ),
    SectionKind.PREFERRED: (
        "preferred",
        "nice to have",
        "nice-to-have",
        "nice to haves",
        "bonus",
        "bonus points",
        "desired",
        "preferred qualifications",
        "preferred skills",
        "pluses",
    ),
    SectionKind.BENEFITS: ("benefits", "perks", "what we offer", "compensation and benefits"),
    SectionKind.OTHER: ("how to apply", "equal opportunity", "location"),
}

_SUFFIX_OK = re.compile(r"^\s*(?:[&/,(\-]|and\b|\+)")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _lexicon(role: DocumentRole) -> dict[SectionKind, tuple[str, ...]]:
    return RESUME_LEXICON if role == DocumentRole.RESUME else JD_LEXICON


def _heading_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold().strip().rstrip(":").strip())


def match_lexicon(text: str, role: DocumentRole) -> SectionKind | None:
    key = _heading_key(text)
    best: tuple[int, SectionKind] | None = None
    for kind, phrases in _lexicon(role).items():
        for phrase in phrases:
            matched = key == phrase or (key.startswith(phrase) and _SUFFIX_OK.match(key[len(phrase) :]))
            if matched and (best is None or len(phrase) > best[0]):
                best = (len(phrase), kind)
    return best[1] if best else None


def is_short_heading_shape(text: str) -> bool:
    stripped = text.strip()
    return 0 < len(stripped) <= 60 and len(stripped.split()) <= 6 and not stripped.endswith(".")


def looks_like_heading(text: str) -> bool:
    """Used by the PDF parser to split heading lines out of merged text blocks."""
    if not is_short_heading_shape(text):
        return False
    return any(match_lexicon(text, role) is not None for role in DocumentRole)


@dataclass
class _Heading:
    kind: SectionKind
    title: str
    detection: SectionDetection


def _classify_heading(block: RawBlock, role: DocumentRole) -> _Heading | None:
    text = normalize_block_text(block.text)
    if not is_short_heading_shape(text):
        return None
    kind = match_lexicon(text, role)
    if kind is None:
        return None
    detection = SectionDetection.STYLE if block.kind == "heading" else SectionDetection.LEXICON
    return _Heading(kind=kind, title=text.rstrip(":").strip()[:80], detection=detection)


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_SPAN_CHARS:
        return [text]
    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT.split(text):
        while len(sentence) > MAX_SPAN_CHARS:
            cut = sentence.rfind(" ", 0, MAX_SPAN_CHARS)
            cut = cut if cut > 0 else MAX_SPAN_CHARS
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= MAX_SPAN_CHARS:
            current = candidate
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return [c for c in chunks if c]


def _block_kind(block: RawBlock) -> BlockKind:
    if block.kind == "table_row":
        return BlockKind.TABLE_ROW
    if block.kind == "list_item":
        return BlockKind.LIST_ITEM
    return BlockKind.PARAGRAPH


@dataclass
class SectionBuildResult:
    sections: list[Section]
    spans: list[EvidenceSpan]
    duplicates_removed: int
    warnings: list[ProcessingWarning]


def build_sections(blocks: list[RawBlock], role: DocumentRole) -> SectionBuildResult:
    """Assign blocks to sections, drop within-section duplicates and create evidence spans with stable IDs."""
    prefix = "R" if role == DocumentRole.RESUME else "J"
    pre_kind = SectionKind.CONTACT if role == DocumentRole.RESUME else SectionKind.ABOUT
    warnings: list[ProcessingWarning] = []

    # Group blocks: list of (heading or None, [blocks])
    groups: list[tuple[_Heading | None, list[RawBlock]]] = [(None, [])]
    for block in blocks:
        heading = _classify_heading(block, role)
        if heading is not None:
            groups.append((heading, []))
        else:
            groups[-1][1].append(block)

    headings_found = len(groups) > 1
    if not headings_found:
        warnings.append(
            ProcessingWarning(
                code="no_sections_detected",
                severity=Severity.INFO,
                component="sections",
                message="No section headings were detected; all content was kept in one section.",
            )
        )

    sections: list[Section] = []
    spans: list[EvidenceSpan] = []
    duplicates = 0
    counter = 0
    for heading, group_blocks in groups:
        if heading is None:
            if not group_blocks:
                continue
            if not headings_found or len(group_blocks) > _PRE_HEADING_LIMIT:
                kind, title, detection = SectionKind.OTHER, None, SectionDetection.NONE
            else:
                kind, title, detection = pre_kind, None, SectionDetection.NONE
        else:
            kind, title, detection = heading.kind, heading.title, heading.detection

        seen: set[str] = set()
        span_ids: list[str] = []
        for block in group_blocks:
            text = normalize_block_text(block.text)
            if not text:
                continue
            key = dedupe_key(text)
            if key is not None:
                if key in seen:
                    duplicates += 1
                    continue
                seen.add(key)
            for chunk in _split_long(text):
                counter += 1
                span_id = f"{prefix}{counter:03d}"
                spans.append(
                    EvidenceSpan(
                        id=span_id,
                        role=role,
                        section_kind=kind,
                        section_title=title,
                        text=chunk,
                        raw_text=block.text if len(text) <= MAX_SPAN_CHARS else chunk,
                        block_kind=_block_kind(block),
                        origin=block.origin,
                        page=block.page,
                        paragraph_index=block.paragraph_index,
                        table_index=block.table_index,
                        row_index=block.row_index,
                    )
                )
                span_ids.append(span_id)
        sections.append(
            Section(kind=kind, title=title, detection=detection, span_ids=span_ids, order=len(sections))
        )
    return SectionBuildResult(
        sections=sections, spans=spans, duplicates_removed=duplicates, warnings=warnings
    )
