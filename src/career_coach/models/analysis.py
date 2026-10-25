from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from career_coach.models.documents import (
    DocumentStats,
    EvidenceSpan,
    ExtractedLink,
    LinkCheckResult,
    ProcessingWarning,
)
from career_coach.models.enums import (
    AlignmentClassification,
    AssessmentMethod,
    DocumentRole,
    Effort,
    ErrorCategory,
    Importance,
    ImportanceSource,
    LLMStatus,
    ReportStatus,
    RequirementCategory,
    SectionDetection,
    SectionKind,
    SourceFormat,
)
from career_coach.models.requests import UserPreferences

_DOMAIN = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class LLMCallMetrics(BaseModel):
    task: Literal["extract_requirements", "assess_evidence", "coach"]
    model: str
    attempts: int = 0
    repaired: bool = False
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    outcome: Literal["ok", "validation_failed", "error"] = "ok"
    error_category: ErrorCategory | None = None


class JobRequirement(BaseModel):
    model_config = _DOMAIN

    id: str = Field(pattern=r"^REQ-\d{2}$")
    text: str = Field(min_length=3, max_length=300)
    category: RequirementCategory
    importance: Importance
    importance_source: ImportanceSource
    source_span_ids: list[str] = Field(min_length=1)
    source_quote: str | None = Field(default=None, max_length=300)
    origin: Literal["llm", "deterministic"]
    taxonomy_skill_ids: list[str] = []


class EvidenceRef(BaseModel):
    model_config = _DOMAIN

    span_id: str = Field(pattern=r"^R\d{3,}$")
    section_kind: SectionKind
    page: int | None = None
    excerpt: str = Field(max_length=300)
    note: str | None = Field(default=None, max_length=200)
    method: AssessmentMethod


class RequirementMatch(BaseModel):
    model_config = _DOMAIN

    requirement: JobRequirement
    classification: AlignmentClassification
    evidence: list[EvidenceRef] = []
    explanation: str = Field(max_length=600)
    uncertainty: str | None = Field(default=None, max_length=400)
    clarifying_question: str | None = Field(default=None, max_length=300)
    method: AssessmentMethod
    rule_adjustments: list[str] = []

    @model_validator(mode="after")
    def _invariants(self) -> RequirementMatch:
        c = self.classification
        if c in (AlignmentClassification.SUPPORTED, AlignmentClassification.PARTIAL) and not self.evidence:
            raise ValueError(f"{c} requires at least one evidence reference")
        if c == AlignmentClassification.NOT_EVIDENCED and self.evidence:
            raise ValueError("not_evidenced must not carry evidence")
        if c in (AlignmentClassification.AMBIGUOUS, AlignmentClassification.PARTIAL) and not self.uncertainty:
            raise ValueError(f"{c} requires an uncertainty note")
        return self


class EvidenceGap(BaseModel):
    model_config = _DOMAIN

    requirement_id: str
    requirement_text: str
    classification: Literal["ambiguous", "not_evidenced"]
    importance: Importance
    clarifying_question: str | None = None


class Strength(BaseModel):
    model_config = _DOMAIN

    title: str = Field(max_length=80)
    description: str = Field(max_length=400)
    evidence: list[EvidenceRef] = Field(min_length=1)
    related_requirement_ids: list[str] = []


class ResumeSuggestion(BaseModel):
    model_config = _DOMAIN

    target_span_id: str = Field(pattern=r"^R\d{3,}$")
    original_text: str = Field(max_length=600)
    issue: str = Field(max_length=300)
    suggested_rewrite: str = Field(max_length=600)
    rationale: str = Field(max_length=300)
    needs_user_input: bool


class DevelopmentAction(BaseModel):
    model_config = _DOMAIN

    priority: int = Field(ge=1, le=6)
    action: str = Field(max_length=200)
    reason: str = Field(max_length=300)
    next_step: str = Field(max_length=300)
    effort: Effort
    related_requirement_ids: list[str] = []


class ClarifyingQuestion(BaseModel):
    model_config = _DOMAIN

    requirement_id: str
    question: str = Field(max_length=300)


class ExecutiveSummary(BaseModel):
    model_config = _DOMAIN

    overview: str = Field(max_length=1200)
    strongest_areas: list[str] = Field(default=[], max_length=5)
    key_gaps: list[str] = Field(default=[], max_length=5)
    next_steps: list[str] = Field(default=[], max_length=5)
    generated_by: Literal["llm", "deterministic"]


COVERAGE_NOTE = (
    "Counts of requirement-level classifications based only on the supplied "
    "resume text. This is not a hiring probability or a measure of ability."
)


class CoverageSummary(BaseModel):
    model_config = _DOMAIN

    counts: dict[Importance, dict[AlignmentClassification, int]]
    total_requirements: int
    interpretation_note: str = COVERAGE_NOTE


class StageTiming(BaseModel):
    stage: str
    duration_ms: int
    status: Literal["ok", "skipped", "failed"]


class AnalysisDiagnostics(BaseModel):
    analysis_id: str
    model: str | None
    prompt_version: str
    taxonomy_version: str
    llm_calls: list[LLMCallMetrics] = []
    stage_timings: list[StageTiming] = []
    total_ms: int = 0
    warnings: list[ProcessingWarning] = []
    rejected_items: dict[str, int] = {}


class DocumentSummary(BaseModel):
    role: DocumentRole
    source_format: SourceFormat
    stats: DocumentStats
    sections: list[tuple[SectionKind, SectionDetection, int]]


class AnalysisReport(BaseModel):
    report_version: Literal["1.0"] = "1.0"
    analysis_id: str
    created_at: datetime
    status: ReportStatus
    llm_status: LLMStatus
    llm_status_message: str | None = None
    executive_summary: ExecutiveSummary | None = None
    coverage: CoverageSummary
    matches: list[RequirementMatch] = []
    evidence_gaps: list[EvidenceGap] = []
    strengths: list[Strength] = []
    resume_suggestions: list[ResumeSuggestion] = []
    development_actions: list[DevelopmentAction] = []
    clarifying_questions: list[ClarifyingQuestion] = []
    resume: DocumentSummary
    job_description: DocumentSummary
    links: list[ExtractedLink] = []
    link_checks: list[LinkCheckResult] | None = None
    preferences: UserPreferences = UserPreferences()
    diagnostics: AnalysisDiagnostics
    resume_spans: dict[str, EvidenceSpan] = Field(default={}, repr=False)
