from __future__ import annotations

import re

from pydantic import BaseModel

from career_coach.config import Settings
from career_coach.models.documents import EvidenceSpan, ParsedDocument, ProcessingWarning
from career_coach.models.enums import SectionKind, Severity

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(
    r"(?<![\w.])(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]?\d{3,4}(?!\w|\.\d)"
)
_INJECTION = re.compile(
    r"ignore (?:all |any )?(?:previous|prior|above) instructions|system prompt|you are (?:now )?(?:an?|the) "
    r"|disregard|rate this (?:candidate|resume) as|assistant:|<\|.*?\|>",
    re.I,
)

RESUME_PRIORITY = [
    SectionKind.EXPERIENCE,
    SectionKind.PROJECTS,
    SectionKind.SKILLS,
    SectionKind.SUMMARY,
    SectionKind.CERTIFICATIONS,
    SectionKind.EDUCATION,
    SectionKind.OTHER,
]
JD_PRIORITY = [
    SectionKind.REQUIREMENTS,
    SectionKind.PREFERRED,
    SectionKind.RESPONSIBILITIES,
    SectionKind.OTHER,
    SectionKind.ABOUT,
    SectionKind.BENEFITS,
]


class ContextSpan(BaseModel):
    id: str
    section: str
    text: str
    origin: str

    def as_row(self) -> dict[str, str]:
        return {"id": self.id, "section": self.section, "text": self.text, "origin": self.origin}


class LLMContext(BaseModel):
    jd_spans: list[ContextSpan]
    resume_spans: list[ContextSpan]
    omitted_resume_span_ids: list[str] = []
    omitted_jd_span_ids: list[str] = []
    approx_input_chars: int = 0


def mask_pii(text: str) -> str:
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", text))


def _budget(
    spans: list[EvidenceSpan], priority: list[SectionKind], budget: int
) -> tuple[list[EvidenceSpan], list]:
    order = {s.id: i for i, s in enumerate(spans)}
    rank = {kind: i for i, kind in enumerate(priority)}
    ranked = sorted(spans, key=lambda s: (rank.get(s.section_kind, len(priority)), order[s.id]))
    kept: list[EvidenceSpan] = []
    omitted: list[EvidenceSpan] = []
    used = 0
    for span in ranked:
        if used + len(span.text) <= budget:
            kept.append(span)
            used += len(span.text)
        else:
            omitted.append(span)
    kept.sort(key=lambda s: order[s.id])
    omitted.sort(key=lambda s: order[s.id])
    return kept, omitted


def _truncation_warning(label: str, omitted: list[EvidenceSpan]) -> ProcessingWarning:
    per_section: dict[str, int] = {}
    for span in omitted:
        per_section[span.section_kind.value] = per_section.get(span.section_kind.value, 0) + 1
    detail = ", ".join(f"{k}: {v}" for k, v in per_section.items())
    return ProcessingWarning(
        code="context_truncated",
        severity=Severity.WARNING,
        component="context_builder",
        message=(
            f"{len(omitted)} {label} span(s) exceeded the AI context budget and were not sent ({detail})."
        )[:300],
    )


def build_context(
    resume: ParsedDocument, jd: ParsedDocument, settings: Settings
) -> tuple[LLMContext, list[ProcessingWarning]]:
    warnings: list[ProcessingWarning] = []
    resume_candidates = [s for s in resume.spans if s.section_kind != SectionKind.CONTACT]
    masked = {s.id: mask_pii(s.text) for s in resume_candidates}
    resume_kept, resume_omitted = _budget(
        [s.model_copy(update={"text": masked[s.id]}) for s in resume_candidates],
        RESUME_PRIORITY,
        settings.max_resume_chars,
    )
    jd_kept, jd_omitted = _budget(jd.spans, JD_PRIORITY, settings.max_jd_chars)
    if resume_omitted:
        warnings.append(_truncation_warning("resume", resume_omitted))
    if jd_omitted:
        warnings.append(_truncation_warning("job description", jd_omitted))

    def to_ctx(span: EvidenceSpan, text: str | None = None) -> ContextSpan:
        return ContextSpan(
            id=span.id, section=span.section_kind.value, text=text or span.text, origin=span.origin.value
        )

    ctx = LLMContext(
        jd_spans=[to_ctx(s, mask_pii(s.text)) for s in jd_kept],
        resume_spans=[to_ctx(s) for s in resume_kept],
        omitted_resume_span_ids=[s.id for s in resume_omitted],
        omitted_jd_span_ids=[s.id for s in jd_omitted],
    )
    ctx.approx_input_chars = sum(len(s.text) for s in ctx.jd_spans) + sum(
        len(s.text) for s in ctx.resume_spans
    )
    warnings.extend(detect_injection_markers([*resume.spans, *jd.spans]))
    return ctx, warnings


def detect_injection_markers(spans: list[EvidenceSpan]) -> list[ProcessingWarning]:
    flagged = [s.id for s in spans if _INJECTION.search(s.text)]
    if not flagged:
        return []
    return [
        ProcessingWarning(
            code="possible_prompt_injection",
            severity=Severity.WARNING,
            component="context_builder",
            message=(
                "Some document text looks like instructions to an AI. It was treated as data only; "
                "review these spans."
            ),
            span_ids=flagged[:20],
        )
    ]
