"""Wire schemas sent to Gemini via `response_json_schema`.

Flat, no Optional fields ("" means none), no length constraints (limits are enforced on conversion).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from career_coach.models.enums import AlignmentClassification, Effort, Importance, RequirementCategory

_WIRE = ConfigDict(extra="ignore")


class WireRequirement(BaseModel):
    model_config = _WIRE

    text: str = Field(description="One atomic requirement, paraphrased minimally from the JD.")
    category: RequirementCategory = Field(description="Closest requirement category.")
    importance: Importance = Field(
        description="required / preferred only if the JD indicates it; else unspecified."
    )
    source_span_ids: list[str] = Field(description="IDs of JD spans (J###) that state this requirement.")
    source_quote: str = Field(description="Short verbatim quote from the cited span(s).")


class RequirementExtractionResponse(BaseModel):
    model_config = _WIRE

    requirements: list[WireRequirement] = Field(description="Atomic, de-duplicated job requirements.")
    notes: list[str] = Field(description="Ambiguities in the JD worth flagging; may be empty.")


class WireEvidence(BaseModel):
    model_config = _WIRE

    span_id: str = Field(description="Resume span ID (R###) that supports the assessment.")
    relevance_note: str = Field(
        description="Why this span is relevant; do not restate facts not in the span."
    )


class WireAssessment(BaseModel):
    model_config = _WIRE

    requirement_id: str = Field(description="Requirement ID (REQ-##) being assessed.")
    classification: AlignmentClassification = Field(
        description="supported / partial / ambiguous / not_evidenced."
    )
    evidence: list[WireEvidence] = Field(description="Cited resume spans; empty for not_evidenced.")
    explanation: str = Field(description="1-3 sentences grounded in the cited spans.")
    uncertainty: str = Field(
        description=(
            "What is unclear; empty string only when classification is supported and evidence is direct."
        )
    )
    clarifying_question: str = Field(
        description="Question for the user to clarify missing evidence; empty if none."
    )


class AssessmentResponse(BaseModel):
    model_config = _WIRE

    assessments: list[WireAssessment] = Field(description="Exactly one assessment per requirement.")


class WireSummary(BaseModel):
    model_config = _WIRE

    overview: str = Field(description="3-5 sentences; no scores or probabilities.")
    strongest_areas: list[str] = Field(description="Max 5 short items.")
    key_gaps: list[str] = Field(description="Max 5 short items; phrase as 'not shown in the resume'.")
    next_steps: list[str] = Field(description="Max 5 short items.")


class WireStrength(BaseModel):
    model_config = _WIRE

    title: str = Field(description="Short title.")
    description: str = Field(description="Grounded description of the strength.")
    evidence_span_ids: list[str] = Field(description="Resume span IDs (R###) supporting it.")
    related_requirement_ids: list[str] = Field(description="Related requirement IDs (REQ-##).")


class WireSuggestion(BaseModel):
    model_config = _WIRE

    target_span_id: str = Field(description="The one resume span (R###) being rewritten.")
    issue: str = Field(description="What could be clearer or more relevant.")
    suggested_rewrite: str = Field(
        description="Rewrite with exactly the same facts; no new numbers, tools, metrics or achievements."
    )
    rationale: str = Field(description="Why the rewrite helps for this job.")
    needs_user_input: bool = Field(description="True if the user should add a real detail (e.g. a metric).")


class WireAction(BaseModel):
    model_config = _WIRE

    priority: int = Field(description="1 = highest priority.")
    action: str = Field(description="Concrete development action.")
    reason: str = Field(description="Why, tied to requirement IDs.")
    next_step: str = Field(description="A concrete next step.")
    effort: Effort = Field(description="small / medium / large.")
    related_requirement_ids: list[str] = Field(description="Related requirement IDs (REQ-##).")


class WireQuestion(BaseModel):
    model_config = _WIRE

    requirement_id: str = Field(description="Requirement ID (REQ-##).")
    question: str = Field(description="Question the candidate could answer to add evidence.")


class CoachingResponse(BaseModel):
    model_config = _WIRE

    executive_summary: WireSummary
    strengths: list[WireStrength]
    resume_suggestions: list[WireSuggestion]
    development_actions: list[WireAction]
    clarifying_questions: list[WireQuestion]
