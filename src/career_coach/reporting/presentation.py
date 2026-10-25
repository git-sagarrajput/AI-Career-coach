"""Pure conversion of AnalysisReport into UI view models and Markdown."""

from __future__ import annotations

from dataclasses import dataclass, field

from career_coach.models.analysis import AnalysisReport, EvidenceRef, RequirementMatch
from career_coach.models.enums import (
    AlignmentClassification,
    AssessmentMethod,
    Importance,
    ReachabilityStatus,
    ReportStatus,
)

DISCLAIMER = (
    "This is advisory feedback based only on the text of the documents you supplied. "
    "It is not a hiring prediction."
)
BADGE_AI = "AI-generated"
BADGE_AI_CHECKED = "AI-generated · evidence-checked"
BADGE_AI_GUARD = "AI-generated · guard-checked"
BADGE_DETERMINISTIC = "Deterministic"

CLASSIFICATION_LABELS = {
    AlignmentClassification.SUPPORTED: "Supported by resume",
    AlignmentClassification.PARTIAL: "Partially supported",
    AlignmentClassification.AMBIGUOUS: "Unclear — needs your input",
    AlignmentClassification.NOT_EVIDENCED: "Not shown in this resume",
}
CLASSIFICATION_ICONS = {
    AlignmentClassification.SUPPORTED: "🟢",
    AlignmentClassification.PARTIAL: "🟡",
    AlignmentClassification.AMBIGUOUS: "🟠",
    AlignmentClassification.NOT_EVIDENCED: "⚪",
}
CLASSIFICATION_LEGEND = (
    "🟢 Supported by resume · 🟡 Partially supported · 🟠 Unclear — needs your input · "
    '⚪ Not shown in this resume. "Not shown" means the supplied resume does not show it — '
    "not that you lack the skill."
)
METHOD_LABELS = {
    AssessmentMethod.SEMANTIC_LLM: "semantic (AI)",
    AssessmentMethod.TAXONOMY: "taxonomy",
    AssessmentMethod.DETERMINISTIC_KEYWORD: "keyword",
}
IMPORTANCE_LABELS = {
    Importance.REQUIRED: "Required",
    Importance.PREFERRED: "Preferred",
    Importance.UNSPECIFIED: "Unspecified",
}
RULE_EXPLANATIONS = {
    "R1": "support claimed without verifiable evidence",
    "R2": "evidence cited for a not-evidenced item",
    "R3": "keyword evidence the AI did not cite",
    "R4": "not assessed by the AI",
    "R5": "uncertainty note added",
    "R6": "lack-of-skill wording replaced",
}
REACHABILITY_LABELS = {
    ReachabilityStatus.REACHABLE: "Reachable (2xx)",
    ReachabilityStatus.RESPONDED_NON_SUCCESS: "Responded (non-2xx)",
    ReachabilityStatus.NOT_CHECKED: "Not checked",
    ReachabilityStatus.REDIRECT_LIMIT: "Too many redirects",
    ReachabilityStatus.TIMEOUT: "Timed out",
    ReachabilityStatus.DNS_FAILURE: "DNS failure",
    ReachabilityStatus.CONNECTION_ERROR: "Connection error",
    ReachabilityStatus.BLOCKED_BY_POLICY: "Blocked by policy",
    ReachabilityStatus.SKIPPED_LIMIT: "Skipped (limit)",
    ReachabilityStatus.SKIPPED_DEADLINE: "Skipped (deadline)",
    ReachabilityStatus.ERROR: "Error",
}
LINK_LEGEND = (
    "**Reachable** means a server answered 2xx; it does not mean the link is genuine, safe, relevant "
    "or owned by you. Content is never inspected and ownership is never verified. Some sites "
    "(e.g. LinkedIn, status 999) "
    "refuse automated checks; such links may still work in a browser."
)


@dataclass
class EvidenceView:
    span_id: str
    location: str
    excerpt: str
    note: str | None
    method: str
    badges: list[str]


@dataclass
class RequirementView:
    id: str
    text: str
    importance: str
    classification: AlignmentClassification
    label: str
    icon: str
    method: str
    evidence: list[EvidenceView]
    explanation: str
    uncertainty: str | None
    clarifying_question: str | None
    rule_notes: list[str]
    source: str


@dataclass
class ReportView:
    disclaimer: str
    banner: tuple[str, str] | None
    summary_badge: str
    coverage_rows: list[dict[str, object]]
    coverage_note: str
    requirements: list[RequirementView]
    strengths_badge: str
    suggestions_badge: str
    roadmap_badge: str
    link_rows: list[dict[str, object]] = field(default_factory=list)
    timing_rows: list[dict[str, object]] = field(default_factory=list)
    llm_rows: list[dict[str, object]] = field(default_factory=list)
    warning_rows: list[dict[str, object]] = field(default_factory=list)
    section_rows: list[dict[str, object]] = field(default_factory=list)


def _evidence_view(report: AnalysisReport, ev: EvidenceRef) -> EvidenceView:
    span = report.resume_spans.get(ev.span_id)
    location = ev.section_kind.value.title() + (f", p.{ev.page}" if ev.page else "")
    badges: list[str] = []
    if span is not None and span.origin.value == "ocr":
        badges.append("OCR")
    if span is not None and span.block_kind.value == "table_row":
        badges.append("table")
    return EvidenceView(ev.span_id, location, ev.excerpt, ev.note, METHOD_LABELS[ev.method], badges)


def _requirement_view(report: AnalysisReport, m: RequirementMatch) -> RequirementView:
    return RequirementView(
        id=m.requirement.id,
        text=m.requirement.text,
        importance=IMPORTANCE_LABELS[m.requirement.importance].lower(),
        classification=m.classification,
        label=CLASSIFICATION_LABELS[m.classification],
        icon=CLASSIFICATION_ICONS[m.classification],
        method=METHOD_LABELS[m.method],
        evidence=[_evidence_view(report, e) for e in m.evidence],
        explanation=m.explanation,
        uncertainty=m.uncertainty,
        clarifying_question=m.clarifying_question,
        rule_notes=[f"Adjusted by rule {r}: {RULE_EXPLANATIONS.get(r, '')}" for r in m.rule_adjustments],
        source="AI-extracted" if m.requirement.origin == "llm" else "Deterministic candidate",
    )


def coverage_rows(report: AnalysisReport) -> list[dict[str, object]]:
    rows = []
    for importance in Importance:
        counts = report.coverage.counts.get(importance, {})
        row: dict[str, object] = {"Importance": IMPORTANCE_LABELS[importance]}
        for cls in AlignmentClassification:
            row[CLASSIFICATION_LABELS[cls]] = counts.get(cls, 0)
        rows.append(row)
    return rows


def _banner(report: AnalysisReport) -> tuple[str, str] | None:
    reason = report.llm_status_message or report.llm_status.value
    if report.status == ReportStatus.DETERMINISTIC_ONLY:
        return "warning", f"AI analysis unavailable — showing deterministic results only. Reason: {reason}"
    if report.status == ReportStatus.PARTIAL:
        return "warning", (
            f"Partial report — some AI output could not be produced or verified and was omitted. "
            f"Reason: {reason}"
        )
    return None


def build_report_view(report: AnalysisReport) -> ReportView:
    complete = report.status == ReportStatus.COMPLETE
    summary_ai = report.executive_summary is not None and report.executive_summary.generated_by == "llm"
    link_rows: list[dict[str, object]] = []
    checks = {c.url: c for c in (report.link_checks or [])}
    for link in report.links:
        url = link.url_normalized or link.url_raw
        check = checks.get(url)
        link_rows.append(
            {
                "URL": url,
                "Found in": ", ".join(sorted({s.kind for s in link.sources})),
                "Syntax": link.syntax_status.value,
                "Reachability": REACHABILITY_LABELS[check.status] if check else "Not checked",
                "HTTP": check.http_status if check and check.http_status else "",
                "Detail": (check.detail if check and check.detail else link.reason) or "",
            }
        )
    diag = report.diagnostics
    return ReportView(
        disclaimer=DISCLAIMER,
        banner=_banner(report),
        summary_badge=BADGE_AI if summary_ai else BADGE_DETERMINISTIC,
        coverage_rows=coverage_rows(report),
        coverage_note=report.coverage.interpretation_note,
        requirements=[_requirement_view(report, m) for m in report.matches],
        strengths_badge=BADGE_AI_CHECKED,
        suggestions_badge=BADGE_AI_GUARD,
        roadmap_badge=BADGE_AI if complete else BADGE_DETERMINISTIC,
        link_rows=link_rows,
        timing_rows=[{"Stage": t.stage, "ms": t.duration_ms, "Status": t.status} for t in diag.stage_timings],
        llm_rows=[
            {
                "Task": c.task,
                "Model": c.model,
                "Attempts": c.attempts,
                "Repaired": c.repaired,
                "Latency ms": c.latency_ms,
                "Input tokens": c.input_tokens if c.input_tokens is not None else "n/a",
                "Output tokens": c.output_tokens if c.output_tokens is not None else "n/a",
                "Outcome": c.outcome,
            }
            for c in diag.llm_calls
        ],
        warning_rows=[
            {
                "Severity": w.severity.value,
                "Component": w.component,
                "Code": w.code,
                "Message": w.message,
                "Spans": ", ".join(w.span_ids),
                "Page": w.page or "",
            }
            for w in diag.warnings
        ],
        section_rows=[
            {"Document": doc.role.value, "Section": kind.value, "Detection": det.value, "Spans": count}
            for doc in (report.resume, report.job_description)
            for kind, det, count in doc.sections
        ],
    )


# ------------------------------------------------------------------ Markdown


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def report_to_markdown(report: AnalysisReport) -> str:
    view = build_report_view(report)
    diag = report.diagnostics
    out: list[str] = ["# AI Career Coach report", "", f"> {DISCLAIMER}", ""]
    out.append(f"- Generated: {report.created_at.strftime('%Y-%m-%d %H:%M UTC')}")
    out.append(f"- Report status: {report.status.value}")
    out.append(
        f"- Model: {diag.model or 'none (deterministic only)'} · Prompt version: {diag.prompt_version} · "
        f"Taxonomy: {diag.taxonomy_version}"
    )
    out.append(f"- Analysis ID: {report.analysis_id}")
    if view.banner:
        out += ["", f"**Note:** {view.banner[1]}"]

    if report.executive_summary:
        es = report.executive_summary
        out += ["", f"## Executive summary ({view.summary_badge})", "", es.overview]
        for title, items in (
            ("Strongest areas", es.strongest_areas),
            ("Key evidence gaps", es.key_gaps),
            ("Next steps", es.next_steps),
        ):
            if items:
                out += ["", f"**{title}**", ""] + [f"- {i}" for i in items]

    out += ["", f"## Evidence coverage ({BADGE_DETERMINISTIC})", ""]
    headers = list(view.coverage_rows[0].keys())
    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "---|" * len(headers))
    for row in view.coverage_rows:
        out.append("| " + " | ".join(_cell(row[h]) for h in headers) + " |")
    out += ["", f"_{view.coverage_note}_"]

    out += [
        "",
        "## Requirements",
        "",
        "| ID | Requirement | Importance | Classification | Method | Evidence | Explanation |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in view.requirements:
        evidence = ", ".join(e.span_id for e in r.evidence) or "—"
        out.append(
            f"| {r.id} | {_cell(r.text)} | {r.importance} | {r.label} | {r.method} | {evidence} | "
            f"{_cell(r.explanation)} |"
        )
    cited = [r for r in view.requirements if r.evidence]
    if cited:
        out += ["", "### Cited evidence", ""]
        for r in cited:
            for e in r.evidence:
                out.append(f'- **{r.id} ← {e.span_id}** ({e.location}): "{_cell(e.excerpt)}"')

    if report.strengths:
        out += ["", f"## Strengths ({BADGE_AI_CHECKED})", ""]
        for s in report.strengths:
            out.append(
                f"- **{s.title}** — {s.description} (evidence: {', '.join(e.span_id for e in s.evidence)})"
            )
    if report.evidence_gaps:
        out += ["", f"## Evidence gaps ({BADGE_DETERMINISTIC})", ""]
        for g in report.evidence_gaps:
            label = CLASSIFICATION_LABELS[AlignmentClassification(g.classification)]
            q = f" — _{g.clarifying_question}_" if g.clarifying_question else ""
            out.append(f"- {g.requirement_id} ({g.importance.value}, {label}): {g.requirement_text}{q}")
    if report.resume_suggestions:
        out += ["", f"## Resume suggestions ({BADGE_AI_GUARD})", ""]
        for sug in report.resume_suggestions:
            out += [
                f"### {sug.target_span_id}",
                "",
                f"- **Current:** {sug.original_text}",
                f"- **Suggested:** {sug.suggested_rewrite}",
                f"- **Why:** {sug.rationale}",
            ]
            if sug.needs_user_input:
                out.append("- _Add a real figure only if you have one._")
            out.append("")
    if report.development_actions:
        out += ["", f"## Development roadmap ({BADGE_AI})", ""]
        for a in report.development_actions:
            out.append(
                f"{a.priority}. **{a.action}** ({a.effort.value}) — {a.reason} Next step: {a.next_step}"
            )
    if report.clarifying_questions:
        out += ["", "## Clarifying questions", ""]
        out += [f"- {q.requirement_id}: {q.question}" for q in report.clarifying_questions]
    if diag.warnings:
        out += ["", "## Warnings", ""]
        out += [f"- [{w.severity.value}] {w.code}: {w.message}" for w in diag.warnings]
    out.append("")
    return "\n".join(out)
