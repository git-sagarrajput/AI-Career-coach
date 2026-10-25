"""Deterministic post-validation rules (Q1-Q6, R0-R6, G1-G6), coverage and report integrity."""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from career_coach.analysis.skill_matching import (
    TaxonomyHint,
    evidence_ref,
    skills_in_text,
)
from career_coach.analysis.taxonomy import CompiledTaxonomy
from career_coach.errors import UnexpectedError
from career_coach.llm.schemas import AssessmentResponse, CoachingResponse, RequirementExtractionResponse
from career_coach.models.analysis import (
    AnalysisReport,
    ClarifyingQuestion,
    CoverageSummary,
    DevelopmentAction,
    EvidenceGap,
    EvidenceRef,
    ExecutiveSummary,
    JobRequirement,
    RequirementMatch,
    ResumeSuggestion,
    Strength,
)
from career_coach.models.documents import EvidenceSpan, ProcessingWarning
from career_coach.models.enums import (
    AlignmentClassification,
    AssessmentMethod,
    Importance,
    ImportanceSource,
    RequirementCategory,
    SectionKind,
    Severity,
)

logger = logging.getLogger(__name__)

AC = AlignmentClassification
_IMPORTANCE_ORDER = {Importance.REQUIRED: 0, Importance.PREFERRED: 1, Importance.UNSPECIFIED: 2}
_PREFERRED_CUE = re.compile(r"\b(preferred|nice to have|nice-to-have|a plus|bonus|desirable|ideally)\b", re.I)
_REQUIRED_CUE = re.compile(r"\b(must|required|minimum|essential)\b", re.I)
_WORD = re.compile(r"[a-z0-9+#./]+")
_LACK = re.compile(
    r"\b(?:the )?candidate (?:lacks|does not have|doesn't have|has no|cannot)\b"
    r"|\byou (?:lack|do not have|don't have)\b",
    re.I,
)
_OUTCOME = re.compile(
    r"guarantee|will (?:definitely |certainly )?(?:get|land|be (?:hired|offered|shortlisted))"
    r"|chances? of (?:getting|being|landing)|likelihood of|probability of"
    r"|\d{1,3}\s?%\s?(?:match|fit|chance|likely)|perfect fit|you are qualified",
    re.I,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*\s*(?:%|percent|k|m|bn|x|\+)?", re.I)
_NUMBER_CORE = re.compile(r"\d+(?:[.,]\d+)*")
_CURRENCY = "$€£₹¥"
_QUANTIFIERS = ("doubled", "tripled", "quadrupled", "halved", "tenfold", "twofold")

R1_NOTE = "The AI indicated support but cited no verifiable resume text."
R2_NOTE = "Possibly related text exists; please review."
R3_NOTE = "Keyword evidence was found that the AI assessment did not cite."
R5_DEFAULT = "Evidence is indirect or incomplete."


# ---------------------------------------------------------------- helpers


@dataclass
class RuleLog:
    warnings: list[ProcessingWarning] = field(default_factory=list)
    rejected: Counter = field(default_factory=Counter)
    truncated: int = 0

    def warn(
        self,
        code: str,
        message: str,
        component: str = "rules",
        span_ids: list[str] | None = None,
        severity: Severity = Severity.INFO,
    ) -> None:
        self.warnings.append(
            ProcessingWarning(
                code=code,
                severity=severity,
                component=component,
                message=message[:300],
                span_ids=span_ids or [],
            )
        )

    def finalize(self) -> None:
        if self.truncated:
            self.warn(
                "llm_output_truncated", f"{self.truncated} AI output field(s) were shortened to fit limits."
            )
            self.truncated = 0


def _trunc(value: str | None, limit: int, log: RuleLog) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    log.truncated += 1
    return value[: limit - 1].rstrip() + "…"


def _norm(text: str) -> str:
    text = re.sub(r"\s+", " ", text.casefold()).strip()
    return text.strip(" .,;:!?\"'()[]-")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def fix_lack_language(text: str) -> tuple[str, bool]:
    def repl(match: re.Match[str]) -> str:
        phrase = (
            "your resume does not show"
            if match.group(0).lower().startswith("you")
            else "the resume does not show"
        )
        start = match.start()
        at_sentence_start = start == 0 or text[:start].rstrip().endswith((".", "!", "?"))
        return phrase[0].upper() + phrase[1:] if at_sentence_start else phrase

    new = _LACK.sub(repl, text)
    return new, new != text


def remove_outcome_claims(text: str) -> tuple[str, bool]:
    sentences = _SENTENCE.split(text)
    kept = [s for s in sentences if not _OUTCOME.search(s)]
    return " ".join(kept).strip(), len(kept) != len(sentences)


def importance_from_cues(
    text: str, quote: str | None, cited_sections: list[SectionKind], model_value: Importance | None
) -> tuple[Importance, ImportanceSource]:
    combined = f"{text} {quote or ''}"
    if (cited_sections and all(s == SectionKind.PREFERRED for s in cited_sections)) or _PREFERRED_CUE.search(
        combined
    ):
        return Importance.PREFERRED, ImportanceSource.JD_CUE
    if any(s == SectionKind.REQUIREMENTS for s in cited_sections) or _REQUIRED_CUE.search(combined):
        return Importance.REQUIRED, ImportanceSource.JD_CUE
    if model_value is not None and model_value != Importance.UNSPECIFIED:
        return model_value, ImportanceSource.MODEL
    return Importance.UNSPECIFIED, ImportanceSource.DEFAULT


def quote_matches(quote: str, cited_texts: list[str]) -> bool:
    q = _norm(quote)
    if not q:
        return False
    if any(q in _norm(t) for t in cited_texts):
        return True
    q_tokens = _tokens(q)
    if not q_tokens:
        return False
    pool = set(_tokens(" ".join(cited_texts)))
    return sum(1 for t in q_tokens if t in pool) / len(q_tokens) >= 0.8


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(_tokens(a)), set(_tokens(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ---------------------------------------------------------------- call 1


@dataclass
class RequirementsOutcome:
    requirements: list[JobRequirement]
    failed: bool
    log: RuleLog


def finalize_requirements(
    resp: RequirementExtractionResponse,
    jd_ctx: dict[str, EvidenceSpan],
    jd_order: dict[str, int],
    taxonomy: CompiledTaxonomy,
    max_requirements: int = 25,
) -> RequirementsOutcome:
    log = RuleLog()
    returned = len(resp.requirements)
    dropped = 0
    candidates: list[dict] = []
    for wire in resp.requirements:
        ids = [i for i in dict.fromkeys(s.strip() for s in wire.source_span_ids) if i in jd_ctx]  # Q1
        if not ids:
            dropped += 1
            log.rejected["requirement_citation_failed"] += 1
            continue
        if not quote_matches(wire.source_quote, [jd_ctx[i].text for i in ids]):  # Q2
            dropped += 1
            log.rejected["requirement_quote_mismatch"] += 1
            continue
        text = _trunc(wire.text, 300, log)
        if len(text) < 3:
            dropped += 1
            continue
        candidates.append({"text": text, "wire": wire, "ids": ids})

    merged: list[dict] = []  # Q3
    for cand in candidates:
        for existing in merged:
            if (
                _norm(existing["text"]) == _norm(cand["text"])
                or _jaccard(existing["text"], cand["text"]) >= 0.85
            ):
                existing["ids"] = list(dict.fromkeys([*existing["ids"], *cand["ids"]]))
                log.rejected["requirement_duplicate_merged"] += 1
                break
        else:
            merged.append(cand)

    if returned == 0 or dropped > returned / 2 or not merged:  # Q6
        log.warn(
            "requirement_extraction_failed",
            f"AI requirement extraction failed verification ({dropped} of {returned} dropped).",
            severity=Severity.WARNING,
        )
        return RequirementsOutcome([], True, log)

    merged.sort(key=lambda c: min(jd_order.get(i, 10**6) for i in c["ids"]))  # Q5
    requirements: list[JobRequirement] = []
    for index, cand in enumerate(merged[:max_requirements], start=1):
        wire = cand["wire"]
        ids = sorted(cand["ids"], key=lambda i: jd_order.get(i, 10**6))
        importance, source = importance_from_cues(
            cand["text"], wire.source_quote, [jd_ctx[i].section_kind for i in ids], wire.importance
        )
        requirements.append(
            JobRequirement(
                id=f"REQ-{index:02d}",
                text=cand["text"],
                category=wire.category,
                importance=importance,
                importance_source=source,
                source_span_ids=ids,
                source_quote=_trunc(wire.source_quote, 300, log) or None,
                origin="llm",
                taxonomy_skill_ids=skills_in_text(cand["text"], taxonomy),
            )
        )
    if len(merged) > max_requirements:
        log.warn("requirements_capped", f"Only the first {max_requirements} requirements were kept.")
    for key in ("requirement_citation_failed", "requirement_quote_mismatch"):
        if log.rejected[key]:
            log.warn(key, f"{log.rejected[key]} AI-extracted requirement(s) were dropped by citation checks.")
    log.finalize()
    return RequirementsOutcome(requirements, False, log)


# ---------------------------------------------------------------- call 2


@dataclass
class AssessmentOutcome:
    matches: list[RequirementMatch]
    failed: bool
    log: RuleLog


def reconcile_assessments(
    resp: AssessmentResponse,
    requirements: list[JobRequirement],
    resume_ctx: dict[str, EvidenceSpan],
    hints: list[TaxonomyHint],
    taxonomy: CompiledTaxonomy,
) -> AssessmentOutcome:
    log = RuleLog()
    req_by_id = {r.id: r for r in requirements}
    hints_by_req: dict[str, list[TaxonomyHint]] = {}
    for hint in hints:
        hints_by_req.setdefault(hint.requirement_id, []).append(hint)

    built: dict[str, RequirementMatch] = {}
    corrected = 0
    for wire in resp.assessments:
        rid = wire.requirement_id.strip()
        if rid not in req_by_id:
            log.rejected["unknown_requirement_id"] += 1
            continue
        if rid in built:
            continue
        req = req_by_id[rid]
        rules: list[str] = []
        classification = wire.classification
        uncertainty = _trunc(wire.uncertainty, 400, log)
        explanation = _trunc(wire.explanation, 600, log) or "No explanation provided."

        evidence: list[EvidenceRef] = []  # R0
        for ev in wire.evidence:
            sid = ev.span_id.strip()
            if sid not in resume_ctx:
                log.rejected["invalid_evidence_id_removed"] += 1
                continue
            if any(e.span_id == sid for e in evidence):
                continue
            evidence.append(
                evidence_ref(resume_ctx[sid], AssessmentMethod.SEMANTIC_LLM, ev.relevance_note or None)
            )
        evidence = evidence[:3]

        if classification in (AC.SUPPORTED, AC.PARTIAL) and not evidence:  # R1
            classification = AC.AMBIGUOUS
            uncertainty = f"{uncertainty} {R1_NOTE}".strip()
            rules.append("R1")
            corrected += 1
        elif classification == AC.NOT_EVIDENCED and evidence:  # R2
            classification = AC.AMBIGUOUS
            uncertainty = f"{uncertainty} {R2_NOTE}".strip()
            rules.append("R2")

        if classification == AC.NOT_EVIDENCED:  # R3
            non_ambiguous = [s for s in req.taxonomy_skill_ids if not taxonomy.skills[s].ambiguous]
            req_hints = {h.skill_id: h for h in hints_by_req.get(rid, [])}
            if non_ambiguous and all(s in req_hints for s in non_ambiguous):
                span_ids = sorted({sp for s in non_ambiguous for sp in req_hints[s].resume_span_ids})[:3]
                evidence = [
                    evidence_ref(resume_ctx[sp], AssessmentMethod.TAXONOMY)
                    for sp in span_ids
                    if sp in resume_ctx
                ]
                if evidence:
                    classification = AC.AMBIGUOUS
                    uncertainty = f"{uncertainty} {R3_NOTE}".strip()
                    rules.append("R3")

        if classification in (AC.PARTIAL, AC.AMBIGUOUS) and not uncertainty:  # R5
            uncertainty = R5_DEFAULT
            rules.append("R5")

        explanation, c1 = fix_lack_language(explanation)  # R6
        uncertainty, c2 = fix_lack_language(uncertainty)
        question, c3 = fix_lack_language(_trunc(wire.clarifying_question, 300, log))
        if c1 or c2 or c3:
            rules.append("R6")

        built[rid] = RequirementMatch(
            requirement=req,
            classification=classification,
            evidence=evidence,
            explanation=_trunc(explanation, 600, log),
            uncertainty=_trunc(uncertainty, 400, log) or None,
            clarifying_question=question or None,
            method=AssessmentMethod.SEMANTIC_LLM,
            rule_adjustments=rules,
        )

    matches: list[RequirementMatch] = []
    for req in requirements:  # R4
        if req.id in built:
            matches.append(built[req.id])
        else:
            corrected += 1
            matches.append(
                RequirementMatch(
                    requirement=req,
                    classification=AC.AMBIGUOUS,
                    explanation="Not assessed by the AI.",
                    uncertainty=R5_DEFAULT,
                    method=AssessmentMethod.SEMANTIC_LLM,
                    rule_adjustments=["R4"],
                )
            )

    if log.rejected["invalid_evidence_id_removed"]:
        log.warn(
            "invalid_evidence_id_removed",
            f"{log.rejected['invalid_evidence_id_removed']} cited resume span ID(s) did not exist "
            "and were removed.",
        )
    failed = bool(requirements) and corrected > len(requirements) / 2
    if failed:
        log.warn(
            "assessment_validation_failed",
            "Most AI assessments failed evidence checks; keyword-based assessment is shown instead.",
            severity=Severity.WARNING,
        )
    log.finalize()
    return AssessmentOutcome(matches, failed, log)


# ---------------------------------------------------------------- call 3


def rewrite_guard(original: str, rewrite: str, taxonomy: CompiledTaxonomy) -> tuple[str, str] | None:
    """Return (reason_code, offending_token) when the rewrite must be rejected; None when accepted."""
    orig_cores = {m.group(0).replace(",", "") for m in _NUMBER_CORE.finditer(original)}
    for m in _NUMBER.finditer(rewrite):
        core = _NUMBER_CORE.match(m.group(0)).group(0).replace(",", "")  # type: ignore[union-attr]
        if core not in orig_cores:
            return "introduced_number", m.group(0).strip()
    for symbol in _CURRENCY:
        if symbol in rewrite and symbol not in original:
            return "introduced_number", symbol
    low_orig, low_new = original.casefold(), rewrite.casefold()
    for word in _QUANTIFIERS:
        if re.search(rf"\b{word}\b", low_new) and not re.search(rf"\b{word}\b", low_orig):
            return "introduced_quantifier", word
    orig_skills = set(skills_in_text(original, taxonomy))
    for skill_id in skills_in_text(rewrite, taxonomy):
        if skill_id not in orig_skills:
            return "introduced_skill", taxonomy.skills[skill_id].name
    if _norm(original) == _norm(rewrite):
        return "no_change", ""
    if len(rewrite) > 2 * len(original) + 150:
        return "too_long", ""
    return None


@dataclass
class CoachingOutcome:
    summary: ExecutiveSummary
    strengths: list[Strength]
    suggestions: list[ResumeSuggestion]
    actions: list[DevelopmentAction]
    questions: list[ClarifyingQuestion]
    log: RuleLog


def _narrative(text: str, limit: int, log: RuleLog) -> str:
    text, removed = remove_outcome_claims(text)  # G3
    if removed:
        log.rejected["outcome_claim_removed"] += 1
    text, changed = fix_lack_language(text)  # G6
    if changed:
        log.rejected["lack_language_rewritten"] += 1
    return _trunc(text, limit, log)


def _narrative_list(items: list[str], max_items: int, limit: int, log: RuleLog) -> list[str]:
    out = []
    for item in items:
        cleaned = _narrative(item, limit, log)
        if cleaned:
            out.append(cleaned)
    return out[:max_items]


def finalize_coaching(
    resp: CoachingResponse,
    matches: list[RequirementMatch],
    resume_ctx: dict[str, EvidenceSpan],
    taxonomy: CompiledTaxonomy,
) -> CoachingOutcome:
    log = RuleLog()
    req_ids = {m.requirement.id for m in matches}

    def valid_reqs(ids: list[str]) -> list[str]:
        kept = [i.strip() for i in ids if i.strip() in req_ids]
        if len(kept) != len(ids):
            log.rejected["invalid_requirement_reference"] += len(ids) - len(kept)
        return list(dict.fromkeys(kept))

    ws = resp.executive_summary
    summary = ExecutiveSummary(
        overview=_narrative(ws.overview, 1200, log) or "Summary unavailable.",
        strongest_areas=_narrative_list(ws.strongest_areas, 5, 200, log),
        key_gaps=_narrative_list(ws.key_gaps, 5, 200, log),
        next_steps=_narrative_list(ws.next_steps, 5, 200, log),
        generated_by="llm",
    )

    strengths: list[Strength] = []  # G1
    for ws_ in resp.strengths:
        ids = [i.strip() for i in ws_.evidence_span_ids if i.strip() in resume_ctx]
        if not ids:
            log.rejected["strength_without_evidence"] += 1
            continue
        title = _narrative(ws_.title, 80, log)
        description = _narrative(ws_.description, 400, log)
        if not title or not description:
            log.rejected["strength_removed"] += 1
            continue
        strengths.append(
            Strength(
                title=title,
                description=description,
                evidence=[
                    evidence_ref(resume_ctx[i], AssessmentMethod.SEMANTIC_LLM) for i in dict.fromkeys(ids)
                ][:3],
                related_requirement_ids=valid_reqs(ws_.related_requirement_ids),
            )
        )
        if len(strengths) == 5:
            break

    suggestions: list[ResumeSuggestion] = []  # G2
    seen_targets: set[str] = set()
    for sug in resp.resume_suggestions:
        sid = sug.target_span_id.strip()
        if sid not in resume_ctx or sid in seen_targets:
            log.rejected["rewrite_rejected"] += 1
            log.warn("rewrite_rejected", "Rewrite rejected: invalid_target.", component="rewrite_guard")
            continue
        original = resume_ctx[sid].text
        rewrite = sug.suggested_rewrite.strip()
        verdict = rewrite_guard(original, rewrite, taxonomy)
        if verdict is not None:
            reason, token = verdict
            log.rejected["rewrite_rejected"] += 1
            detail = f" (introduced '{token[:40]}')" if token else ""
            log.warn(
                "rewrite_rejected",
                f"Rewrite for {sid} rejected: {reason}{detail}.",
                component="rewrite_guard",
                span_ids=[sid],
            )
            continue
        rewrite, claims = remove_outcome_claims(rewrite)
        if claims or not rewrite:
            log.rejected["rewrite_rejected"] += 1
            continue
        seen_targets.add(sid)
        suggestions.append(
            ResumeSuggestion(
                target_span_id=sid,
                original_text=original,
                issue=_narrative(sug.issue, 300, log),
                suggested_rewrite=_trunc(rewrite, 600, log),
                rationale=_narrative(sug.rationale, 300, log),
                needs_user_input=sug.needs_user_input,
            )
        )
        if len(suggestions) == 6:
            break

    raw_actions = sorted(resp.development_actions, key=lambda a: a.priority)  # G5
    actions: list[DevelopmentAction] = []
    for wa in raw_actions:
        action = _narrative(wa.action, 200, log)
        if not action:
            continue
        actions.append(
            DevelopmentAction(
                priority=len(actions) + 1,
                action=action,
                reason=_narrative(wa.reason, 300, log),
                next_step=_narrative(wa.next_step, 300, log),
                effort=wa.effort,
                related_requirement_ids=valid_reqs(wa.related_requirement_ids),
            )
        )
        if len(actions) == 6:
            break

    questions: list[ClarifyingQuestion] = []  # G4
    for wq in resp.clarifying_questions:
        rid = wq.requirement_id.strip()
        if rid not in req_ids:
            log.rejected["invalid_requirement_reference"] += 1
            continue
        text = _narrative(wq.question, 300, log)
        if text:
            questions.append(ClarifyingQuestion(requirement_id=rid, question=text))
        if len(questions) == 5:
            break

    if log.rejected["outcome_claim_removed"]:
        log.warn(
            "outcome_claim_removed",
            f"{log.rejected['outcome_claim_removed']} AI sentence(s) making outcome claims were removed.",
        )
    log.finalize()
    return CoachingOutcome(summary, strengths, suggestions, actions, questions, log)


# ---------------------------------------------------------------- derivations


def compute_coverage(matches: list[RequirementMatch]) -> CoverageSummary:
    counts = {imp: {cls: 0 for cls in AlignmentClassification} for imp in Importance}
    for match in matches:
        counts[match.requirement.importance][match.classification] += 1
    return CoverageSummary(counts=counts, total_requirements=len(matches))


def derive_gaps(matches: list[RequirementMatch], questions: list[ClarifyingQuestion]) -> list[EvidenceGap]:
    q_by_req = {q.requirement_id: q.question for q in questions}
    gaps = [
        EvidenceGap(
            requirement_id=m.requirement.id,
            requirement_text=m.requirement.text,
            classification=m.classification.value,  # type: ignore[arg-type]
            importance=m.requirement.importance,
            clarifying_question=m.clarifying_question or q_by_req.get(m.requirement.id),
        )
        for m in matches
        if m.classification in (AC.AMBIGUOUS, AC.NOT_EVIDENCED)
    ]
    gaps.sort(key=lambda g: (_IMPORTANCE_ORDER[g.importance], g.requirement_id))
    return gaps


def merge_questions(
    matches: list[RequirementMatch], extra: list[ClarifyingQuestion]
) -> list[ClarifyingQuestion]:
    seen: set[str] = set()
    out: list[ClarifyingQuestion] = []
    for q in extra:
        if q.requirement_id not in seen:
            seen.add(q.requirement_id)
            out.append(q)
    for m in matches:
        if (
            m.clarifying_question
            and m.requirement.id not in seen
            and m.classification in (AC.AMBIGUOUS, AC.NOT_EVIDENCED)
        ):
            seen.add(m.requirement.id)
            out.append(ClarifyingQuestion(requirement_id=m.requirement.id, question=m.clarifying_question))
    return out


def sort_matches(matches: list[RequirementMatch]) -> list[RequirementMatch]:
    return sorted(matches, key=lambda m: (_IMPORTANCE_ORDER[m.requirement.importance], m.requirement.id))


def category_from_skills(skill_ids: list[str], taxonomy: CompiledTaxonomy, text: str) -> RequirementCategory:
    low = text.casefold()
    if re.search(r"\b(degree|bachelor|master|phd|diploma)\b", low):
        return RequirementCategory.EDUCATION
    if "certif" in low:
        return RequirementCategory.CERTIFICATION
    if re.search(r"\b\d+\+?\s*years?\b", low):
        return RequirementCategory.EXPERIENCE
    categories = [taxonomy.skills[s].category for s in skill_ids]
    if not categories:
        return RequirementCategory.OTHER
    first = categories[0]
    if first in ("platform", "data"):
        return RequirementCategory.TOOL_OR_PLATFORM
    if first == "soft":
        return RequirementCategory.SOFT_SKILL
    return RequirementCategory.TECHNICAL_SKILL


def assert_report_integrity(report: AnalysisReport) -> None:
    problems: list[str] = []
    req_ids = {m.requirement.id for m in report.matches}
    for m in report.matches:
        for ev in m.evidence:
            if ev.span_id not in report.resume_spans:
                problems.append(f"evidence {ev.span_id} missing from resume_spans")
        if m.classification in (AC.SUPPORTED, AC.PARTIAL) and not m.evidence:
            problems.append(f"{m.requirement.id} supported/partial without evidence")
        if m.classification == AC.NOT_EVIDENCED and m.evidence:
            problems.append(f"{m.requirement.id} not_evidenced with evidence")
    for s in report.strengths:
        for ev in s.evidence:
            if ev.span_id not in report.resume_spans:
                problems.append(f"strength evidence {ev.span_id} missing")
        problems.extend(
            f"strength references unknown {r}" for r in s.related_requirement_ids if r not in req_ids
        )
    for sug in report.resume_suggestions:
        if sug.target_span_id not in report.resume_spans:
            problems.append(f"suggestion target {sug.target_span_id} missing")
    for a in report.development_actions:
        problems.extend(
            f"action references unknown {r}" for r in a.related_requirement_ids if r not in req_ids
        )
    for q in report.clarifying_questions:
        if q.requirement_id not in req_ids:
            problems.append(f"question references unknown {q.requirement_id}")
    for g in report.evidence_gaps:
        if g.requirement_id not in req_ids:
            problems.append(f"gap references unknown {g.requirement_id}")
    if report.coverage.total_requirements != len(report.matches):
        problems.append("coverage total mismatch")
    if problems:
        logger.error("report integrity violation: %s", "; ".join(problems[:10]))
        raise UnexpectedError(code="report_integrity")
