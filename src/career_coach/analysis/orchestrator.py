"""The one place that knows the workflow order: ingestion -> deterministic analysis -> 3 LLM calls."""

from __future__ import annotations

import logging
import time
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from career_coach.analysis import rules
from career_coach.analysis.context_builder import build_context
from career_coach.analysis.deterministic_requirements import deterministic_requirements
from career_coach.analysis.skill_matching import (
    SkillMention,
    build_taxonomy_hints,
    deterministic_classify,
    find_skill_mentions,
)
from career_coach.analysis.taxonomy import CompiledTaxonomy, load_taxonomy
from career_coach.config import Settings
from career_coach.errors import (
    LLMAuthenticationError,
    LLMBudgetExceededError,
    LLMConfigurationError,
    LLMError,
    LLMQuotaError,
    LLMResponseValidationError,
)
from career_coach.ingestion.pipeline import ingest_document, ingest_text
from career_coach.links.url_policy import Resolver
from career_coach.links.validation import LinkCheckJob, start_link_checks
from career_coach.llm.client import CallBudget, LLMClient
from career_coach.llm.prompts import (
    PROMPT_VERSION,
    build_assess_prompt,
    build_coach_prompt,
    build_extract_prompt,
)
from career_coach.llm.schemas import AssessmentResponse, CoachingResponse, RequirementExtractionResponse
from career_coach.models.analysis import (
    AnalysisDiagnostics,
    AnalysisReport,
    ClarifyingQuestion,
    DevelopmentAction,
    DocumentSummary,
    ExecutiveSummary,
    JobRequirement,
    LLMCallMetrics,
    RequirementMatch,
    ResumeSuggestion,
    StageTiming,
    Strength,
)
from career_coach.models.documents import EvidenceSpan, LinkCheckResult, ParsedDocument, ProcessingWarning
from career_coach.models.enums import (
    AlignmentClassification,
    DocumentRole,
    LLMStatus,
    ReportStatus,
    SectionKind,
    Severity,
)
from career_coach.models.requests import AnalysisRequest, ProgressEvent
from career_coach.utils.logging_config import analysis_id_var
from career_coach.utils.timing import Stopwatch, elapsed_ms

logger = logging.getLogger(__name__)
ProgressFn = Callable[[ProgressEvent], None]
ParseCache = dict[str, ParsedDocument]

_SEVERITY_RANK = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


def llm_status_for(exc: BaseException) -> LLMStatus:
    if isinstance(exc, LLMConfigurationError):
        return LLMStatus.MODEL_NOT_FOUND if exc.code == "model_not_found" else LLMStatus.NOT_CONFIGURED
    if isinstance(exc, LLMAuthenticationError):
        return LLMStatus.AUTH_ERROR
    if isinstance(exc, LLMQuotaError):
        return LLMStatus.QUOTA_EXCEEDED
    if isinstance(exc, LLMResponseValidationError):
        return LLMStatus.INVALID_OUTPUT
    if isinstance(exc, LLMBudgetExceededError):
        return LLMStatus.BUDGET_EXCEEDED
    return LLMStatus.UNAVAILABLE


class _Run:
    """Mutable state for one analysis run."""

    def __init__(
        self,
        request: AnalysisRequest,
        settings: Settings,
        llm: LLMClient | None,
        progress: ProgressFn | None,
        taxonomy: CompiledTaxonomy,
    ) -> None:
        self.request = request
        self.settings = settings
        self.llm = llm
        self.progress = progress
        self.taxonomy = taxonomy
        self.timings: list[StageTiming] = []
        self.warnings: list[ProcessingWarning] = []
        self.rejected: Counter = Counter()
        self.llm_calls: list[LLMCallMetrics] = []
        self.llm_status = LLMStatus.OK if llm is not None else LLMStatus.NOT_CONFIGURED
        self.llm_message: str | None = None

    def emit(self, stage: str, status: str, message: str) -> None:
        if self.progress is not None:
            try:
                self.progress(ProgressEvent(stage=stage, status=status, message=message[:200]))  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                logger.debug("progress callback failed")

    def absorb(self, log: rules.RuleLog) -> None:
        self.warnings.extend(log.warnings)
        self.rejected.update(log.rejected)

    def llm_failed(self, exc: LLMError) -> None:
        self.llm_status = llm_status_for(exc)
        self.llm_message = exc.user_message
        metrics = getattr(exc, "metrics", None)
        if metrics is not None:
            self.llm_calls.append(metrics)
        logger.warning("llm stage failed status=%s code=%s", self.llm_status.value, exc.code)


def _parse(
    data: bytes,
    filename: str,
    role: DocumentRole,
    settings: Settings,
    cache: ParseCache | None,
    key_prefix: str,
) -> ParsedDocument:
    import hashlib

    key = f"{key_prefix}:{hashlib.sha256(data).hexdigest()}:{settings.fingerprint()}"
    if cache is not None and key in cache:
        return cache[key]
    doc = ingest_document(data, filename, role, settings)
    if cache is not None:
        cache[key] = doc
    return doc


def _deterministic_summary(reason: str, matches: list[RequirementMatch], partial: bool) -> ExecutiveSummary:
    counts = Counter(m.classification for m in matches)
    total = len(matches)
    ac = AlignmentClassification
    if partial:
        overview = (
            f"AI coaching unavailable: {reason} The {total} requirement assessments below passed "
            f"evidence checks. {counts[ac.SUPPORTED]} supported, {counts[ac.PARTIAL]} "
            f"partially supported, {counts[ac.AMBIGUOUS]} unclear and "
            f"{counts[ac.NOT_EVIDENCED]} not shown in this resume."
        )
    else:
        overview = (
            f"AI analysis unavailable: {reason} Below is keyword-based coverage of {total} candidate "
            f"requirements: {counts[ac.PARTIAL]} partially supported, "
            f"{counts[ac.AMBIGUOUS]} unclear and "
            f"{counts[ac.NOT_EVIDENCED]} not shown in this resume."
        )
    return ExecutiveSummary(overview=overview[:1200], generated_by="deterministic")


def _summary(doc: ParsedDocument) -> DocumentSummary:
    return DocumentSummary(
        role=doc.role,
        source_format=doc.source_format,
        stats=doc.stats,
        sections=[(s.kind, s.detection, len(s.span_ids)) for s in doc.sections],
    )


def run_analysis(
    request: AnalysisRequest,
    *,
    settings: Settings,
    llm: LLMClient | None,
    progress: ProgressFn | None = None,
    link_transport: httpx.BaseTransport | None = None,
    link_resolver: Resolver | None = None,
    parse_cache: ParseCache | None = None,
    taxonomy: CompiledTaxonomy | None = None,
) -> AnalysisReport:
    analysis_id = uuid.uuid4().hex[:12]
    token = analysis_id_var.set(analysis_id)
    try:
        return _run_analysis(
            analysis_id,
            request,
            settings,
            llm,
            progress,
            link_transport,
            link_resolver,
            parse_cache,
            taxonomy or load_taxonomy(),
        )
    finally:
        analysis_id_var.reset(token)


def _run_analysis(
    analysis_id: str,
    request: AnalysisRequest,
    settings: Settings,
    llm: LLMClient | None,
    progress: ProgressFn | None,
    link_transport: httpx.BaseTransport | None,
    link_resolver: Resolver | None,
    parse_cache: ParseCache | None,
    taxonomy: CompiledTaxonomy,
) -> AnalysisReport:
    start = time.perf_counter()
    run = _Run(request, settings, llm, progress, taxonomy)
    logger.info(
        "analysis started resume_size_kb=%d jd=%s check_links=%s llm=%s",
        len(request.resume.data) // 1024,
        "text" if (request.jd_text or "").strip() else "file",
        request.check_links,
        llm is not None,
    )

    # ---- ingestion
    run.emit("validate", "started", "Validating uploads")
    run.emit("parse_resume", "started", "Parsing resume")
    with Stopwatch("parse_resume", run.timings):
        resume = _parse(
            request.resume.data, request.resume.filename, DocumentRole.RESUME, settings, parse_cache, "resume"
        )
    run.emit("validate", "completed", "Uploads validated")
    run.emit("parse_resume", "completed", f"Resume parsed ({resume.stats.span_count} spans)")

    run.emit("parse_jd", "started", "Parsing job description")
    with Stopwatch("parse_jd", run.timings):
        if request.jd_text and request.jd_text.strip():
            jd = ingest_text(request.jd_text, DocumentRole.JOB_DESCRIPTION, settings)
            if request.jd_file is not None:
                run.warnings.append(
                    ProcessingWarning(
                        code="jd_text_preferred",
                        severity=Severity.INFO,
                        component="orchestrator",
                        message=(
                            "Both pasted text and a file were provided for the job description; "
                            "the pasted text was used."
                        ),
                    )
                )
        else:
            assert request.jd_file is not None  # noqa: S101 - guaranteed by AnalysisRequest validator
            jd = _parse(
                request.jd_file.data,
                request.jd_file.filename,
                DocumentRole.JOB_DESCRIPTION,
                settings,
                parse_cache,
                "jd",
            )
    run.emit("parse_jd", "completed", f"Job description parsed ({jd.stats.span_count} spans)")
    run.warnings.extend(resume.warnings)
    run.warnings.extend(jd.warnings)

    # ---- deterministic analysis
    run.emit("sections", "started", "Detecting sections and skills")
    with Stopwatch("sections", run.timings):
        resume_mentions = find_skill_mentions(resume.spans, taxonomy)
        fallback_requirements = deterministic_requirements(jd, taxonomy, settings.max_requirements)
    run.emit(
        "sections",
        "completed",
        f"{len(resume.sections)} resume sections, "
        f"{len({m.skill_id for m in resume_mentions})} skills recognised",
    )

    # ---- optional link checks (overlap with LLM calls)
    job: LinkCheckJob | None = None
    link_results: list[LinkCheckResult] | None = None
    link_deadline = time.monotonic() + settings.link_deadline_seconds
    if request.check_links and resume.links:
        run.emit(
            "links",
            "started",
            f"Checking {min(len(resume.links), settings.link_max_urls)} link(s) in background",
        )
        job = start_link_checks(resume.links, settings, transport=link_transport, resolver=link_resolver)
    elif request.check_links:
        link_results = []

    try:
        status, matches, coaching = _llm_stages(run, resume, jd, resume_mentions, fallback_requirements)

        if job is not None:
            with Stopwatch("links", run.timings):
                link_results = job.collect(max(0.0, link_deadline - time.monotonic()))
            run.emit("links", "completed", f"{len(link_results)} link(s) checked")
        elif not request.check_links:
            run.emit("links", "skipped", "Link reachability checks not requested")
    finally:
        if job is not None:
            job.cancel()

    # ---- report
    run.emit("report", "started", "Assembling report")
    with Stopwatch("report", run.timings):
        report = _assemble(run, analysis_id, status, resume, jd, matches, coaching, link_results, start)
    run.emit("report", "completed", "Report ready")
    logger.info(
        "analysis finished status=%s requirements=%d llm_calls=%d total_ms=%d warnings=%d",
        report.status.value,
        len(report.matches),
        len(report.diagnostics.llm_calls),
        report.diagnostics.total_ms,
        len(report.diagnostics.warnings),
    )
    return report


_Coaching = tuple[
    ExecutiveSummary | None,
    list[Strength],
    list[ResumeSuggestion],
    list[DevelopmentAction],
    list[ClarifyingQuestion],
]


def _deterministic_matches(
    run: _Run, requirements: list[JobRequirement], resume: ParsedDocument, mentions: list[SkillMention]
) -> list[RequirementMatch]:
    return [deterministic_classify(r, resume.spans, mentions, run.taxonomy) for r in requirements]


def _llm_stages(
    run: _Run,
    resume: ParsedDocument,
    jd: ParsedDocument,
    mentions: list[SkillMention],
    fallback_requirements: list[JobRequirement],
) -> tuple[ReportStatus, list[RequirementMatch], _Coaching]:
    settings = run.settings

    def deterministic_only(reason: str) -> tuple[ReportStatus, list[RequirementMatch], _Coaching]:
        matches = _deterministic_matches(run, fallback_requirements, resume, mentions)
        summary = _deterministic_summary(reason, matches, partial=False)
        return ReportStatus.DETERMINISTIC_ONLY, matches, (summary, [], [], [], [])

    if run.llm is None:
        run.llm_message = "Gemini API key not configured (see README → Configure Gemini)."
        for stage in ("requirements", "assessment", "coaching"):
            run.emit(stage, "skipped", "AI not configured")
        return deterministic_only("Gemini API key not configured.")

    llm = run.llm
    budget = CallBudget(settings.max_llm_attempts_per_analysis, settings.llm_total_budget_seconds)
    with Stopwatch("context", run.timings):
        ctx, ctx_warnings = build_context(resume, jd, settings)
    run.warnings.extend(ctx_warnings)
    resume_ctx: dict[str, EvidenceSpan] = {s.id: resume.span_by_id(s.id) for s in ctx.resume_spans}  # type: ignore[misc]
    jd_ctx: dict[str, EvidenceSpan] = {s.id: jd.span_by_id(s.id) for s in ctx.jd_spans}  # type: ignore[misc]
    jd_order = {s.id: i for i, s in enumerate(jd.spans)}

    # ---- call 1
    run.emit("requirements", "started", "Extracting job requirements (AI)")
    with Stopwatch("requirements", run.timings) as sw:
        try:
            system, user = build_extract_prompt(
                [s.as_row() for s in ctx.jd_spans],
                run.request.preferences.target_role,
                settings.max_requirements,
            )
            result = llm.generate_structured(
                task="extract_requirements",
                system_instruction=system,
                user_prompt=user,
                schema=RequirementExtractionResponse,
                budget=budget,
            )
            run.llm_calls.append(result.metrics)
            outcome = rules.finalize_requirements(
                result.parsed, jd_ctx, jd_order, run.taxonomy, settings.max_requirements
            )
            run.absorb(outcome.log)
            for note in result.parsed.notes[:5]:
                if note.strip():
                    run.warnings.append(
                        ProcessingWarning(
                            code="jd_ambiguity_note",
                            severity=Severity.INFO,
                            component="llm_requirements",
                            message=note.strip()[:300],
                        )
                    )
            if outcome.failed:
                raise LLMResponseValidationError(code="requirements_unverified")
            requirements = outcome.requirements
        except LLMError as exc:
            sw.fail()
            run.llm_failed(exc)
            run.emit("requirements", "failed", exc.user_message)
            run.emit("assessment", "skipped", "Skipped after requirement extraction failed")
            run.emit("coaching", "skipped", "Skipped after requirement extraction failed")
            return deterministic_only(exc.user_message)
    run.emit("requirements", "completed", f"{len(requirements)} requirements extracted and verified")

    # ---- call 2
    run.emit("assessment", "started", "Assessing resume evidence (AI)")
    hints = build_taxonomy_hints(requirements, list(resume_ctx.values()), mentions, run.taxonomy)
    with Stopwatch("assessment", run.timings) as sw:
        try:
            system, user = build_assess_prompt(
                [
                    {
                        "requirement_id": r.id,
                        "text": r.text,
                        "category": r.category.value,
                        "importance": r.importance.value,
                    }
                    for r in requirements
                ],
                [
                    {
                        "requirement_id": h.requirement_id,
                        "skill": h.skill_name,
                        "resume_span_ids": list(h.resume_span_ids),
                    }
                    for h in hints
                ],
                [s.as_row() for s in ctx.resume_spans],
            )
            result2 = llm.generate_structured(
                task="assess_evidence",
                system_instruction=system,
                user_prompt=user,
                schema=AssessmentResponse,
                budget=budget,
            )
            run.llm_calls.append(result2.metrics)
            assessed = rules.reconcile_assessments(
                result2.parsed, requirements, resume_ctx, hints, run.taxonomy
            )
            run.absorb(assessed.log)
            if assessed.failed:
                raise LLMResponseValidationError(code="assessments_unverified")
            matches = assessed.matches
        except LLMError as exc:
            sw.fail()
            run.llm_failed(exc)
            run.emit("assessment", "failed", exc.user_message)
            run.emit("coaching", "skipped", "Skipped after assessment failed")
            matches = _deterministic_matches(run, requirements, resume, mentions)
            return (
                ReportStatus.PARTIAL,
                matches,
                (_deterministic_summary(exc.user_message, matches, False), [], [], [], []),
            )
    run.emit("assessment", "completed", f"{len(matches)} requirements assessed and validated")

    # ---- call 3
    run.emit("coaching", "started", "Writing coaching (AI)")
    with Stopwatch("coaching", run.timings) as sw:
        try:
            cited = list(dict.fromkeys(e.span_id for m in matches for e in m.evidence))
            candidates = [
                s.id
                for s in ctx.resume_spans
                if resume_ctx[s.id].section_kind in (SectionKind.EXPERIENCE, SectionKind.PROJECTS)
            ]
            wanted = set(cited) | set([c for c in candidates if c not in cited][:15])
            prefs = run.request.preferences
            system, user = build_coach_prompt(
                [
                    {
                        "requirement_id": m.requirement.id,
                        "text": m.requirement.text,
                        "importance": m.requirement.importance.value,
                        "classification": m.classification.value,
                        "evidence_span_ids": [e.span_id for e in m.evidence],
                        "explanation": m.explanation,
                        "uncertainty": m.uncertainty or "",
                    }
                    for m in matches
                ],
                [s.as_row() for s in ctx.resume_spans if s.id in wanted],
                prefs.target_role,
                prefs.seniority.value,
                prefs.focus_areas,
            )
            result3 = llm.generate_structured(
                task="coach",
                system_instruction=system,
                user_prompt=user,
                schema=CoachingResponse,
                budget=budget,
            )
            run.llm_calls.append(result3.metrics)
            coaching = rules.finalize_coaching(result3.parsed, matches, resume_ctx, run.taxonomy)
            run.absorb(coaching.log)
        except LLMError as exc:
            sw.fail()
            run.llm_failed(exc)
            run.emit("coaching", "failed", exc.user_message)
            run.warnings.append(
                ProcessingWarning(
                    code="coaching_omitted",
                    severity=Severity.WARNING,
                    component="orchestrator",
                    message="AI coaching could not be generated or verified; coaching sections are omitted.",
                )
            )
            return (
                ReportStatus.PARTIAL,
                matches,
                (_deterministic_summary(exc.user_message, matches, True), [], [], [], []),
            )
    run.emit("coaching", "completed", "Coaching generated and checked")
    return (
        ReportStatus.COMPLETE,
        matches,
        (coaching.summary, coaching.strengths, coaching.suggestions, coaching.actions, coaching.questions),
    )


def _assemble(
    run: _Run,
    analysis_id: str,
    status: ReportStatus,
    resume: ParsedDocument,
    jd: ParsedDocument,
    matches: list[RequirementMatch],
    coaching: _Coaching,
    link_results: list[LinkCheckResult] | None,
    start: float,
) -> AnalysisReport:
    summary, strengths, suggestions, actions, questions = coaching
    matches = rules.sort_matches(matches)
    merged_questions = rules.merge_questions(matches, questions)
    cited_ids = (
        {e.span_id for m in matches for e in m.evidence}
        | {e.span_id for s in strengths for e in s.evidence}
        | {s.target_span_id for s in suggestions}
    )
    resume_spans = {sid: span for sid in sorted(cited_ids) if (span := resume.span_by_id(sid)) is not None}
    if not matches:
        run.warnings.append(
            ProcessingWarning(
                code="no_requirements_found",
                severity=Severity.WARNING,
                component="orchestrator",
                message="No requirements could be identified. Check that the job description includes "
                "responsibilities or qualifications.",
            )
        )
    if status == ReportStatus.COMPLETE:
        run.llm_status = LLMStatus.OK
    warnings = sorted(run.warnings, key=lambda w: (_SEVERITY_RANK[w.severity], w.component, w.code))
    report = AnalysisReport(
        analysis_id=analysis_id,
        created_at=datetime.now(UTC),
        status=status,
        llm_status=run.llm_status,
        llm_status_message=run.llm_message if status != ReportStatus.COMPLETE else None,
        executive_summary=summary,
        coverage=rules.compute_coverage(matches),
        matches=matches,
        evidence_gaps=rules.derive_gaps(matches, merged_questions),
        strengths=strengths,
        resume_suggestions=suggestions,
        development_actions=actions,
        clarifying_questions=merged_questions,
        resume=_summary(resume),
        job_description=_summary(jd),
        links=resume.links,
        link_checks=link_results,
        preferences=run.request.preferences,
        diagnostics=AnalysisDiagnostics(
            analysis_id=analysis_id,
            model=run.llm.model_name if run.llm is not None else None,
            prompt_version=PROMPT_VERSION,
            taxonomy_version=run.taxonomy.version,
            llm_calls=run.llm_calls,
            stage_timings=run.timings,
            total_ms=elapsed_ms(start),
            warnings=warnings,
            rejected_items=dict(run.rejected),
        ),
        resume_spans=resume_spans,
    )
    rules.assert_report_integrity(report)
    return report
