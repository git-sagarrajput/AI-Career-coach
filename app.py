"""Streamlit UI for AI Career Coach (the only module that imports streamlit)."""

from __future__ import annotations

import hashlib
import logging
import uuid

import streamlit as st
from pydantic import ValidationError

from career_coach.analysis.orchestrator import run_analysis
from career_coach.analysis.taxonomy import CompiledTaxonomy, load_taxonomy
from career_coach.config import Settings, load_settings
from career_coach.errors import CareerCoachError
from career_coach.llm.client import LLMClient, build_llm_client
from career_coach.llm.prompts import PROMPT_VERSION
from career_coach.models.analysis import AnalysisReport
from career_coach.models.enums import AlignmentClassification, Seniority
from career_coach.models.requests import AnalysisRequest, ProgressEvent, UploadedInput, UserPreferences
from career_coach.reporting.presentation import (
    BADGE_AI,
    BADGE_AI_CHECKED,
    BADGE_AI_GUARD,
    BADGE_DETERMINISTIC,
    CLASSIFICATION_ICONS,
    CLASSIFICATION_LABELS,
    CLASSIFICATION_LEGEND,
    LINK_LEGEND,
    build_report_view,
    report_to_markdown,
)
from career_coach.utils.logging_config import configure_logging

logger = logging.getLogger("career_coach.app")

PRIVACY_NOTE = (
    "**Privacy:** Your files are processed on this computer. To generate the AI analysis, selected text from "
    "your resume (excluding contact details) and the job description is sent to Google's Gemini API. On the "
    "free tier, Google may use submitted content to improve its products. Nothing is saved by this app after "
    "you close the tab or click *New analysis*. Use synthetic or redacted documents for demonstrations."
)
STAGE_LABELS = {
    "validate": "Validating",
    "parse_resume": "Parsing resume",
    "parse_jd": "Parsing job description",
    "sections": "Sections",
    "links": "Links",
    "requirements": "Requirements (AI)",
    "assessment": "Assessment (AI)",
    "coaching": "Coaching (AI)",
    "report": "Report",
}
STATUS_ICONS = {"started": "⏳", "completed": "✅", "skipped": "⏭️", "failed": "⚠️"}
SESSION_KEYS = ("parse_cache", "report_cache", "report", "last_error")


@st.cache_resource
def get_settings() -> Settings:
    return load_settings()


@st.cache_resource
def get_llm(_settings: Settings, model: str, configured: bool) -> LLMClient | None:
    return build_llm_client(_settings)


@st.cache_resource
def get_taxonomy() -> CompiledTaxonomy:
    return load_taxonomy()


def badge(text: str) -> str:
    return f"`{text}`"


def reset_session() -> None:
    for key in SESSION_KEYS:
        st.session_state.pop(key, None)
    st.session_state["nonce"] = st.session_state.get("nonce", 0) + 1


def cache_key(request: AnalysisRequest, settings: Settings, taxonomy_version: str) -> str:
    h = hashlib.sha256()
    h.update(hashlib.sha256(request.resume.data).digest())
    if request.jd_text and request.jd_text.strip():
        h.update(b"text:" + hashlib.sha256(request.jd_text.encode("utf-8")).digest())
    elif request.jd_file is not None:
        h.update(b"file:" + hashlib.sha256(request.jd_file.data).digest())
    h.update(
        f"|{settings.gemini_model}|{settings.llm_configured}|{PROMPT_VERSION}|{taxonomy_version}|"
        f"{request.preferences.model_dump_json()}|{request.check_links}".encode()
    )
    return h.hexdigest()


# ------------------------------------------------------------------ rendering


def render_report(report: AnalysisReport) -> None:
    view = build_report_view(report)
    st.divider()
    st.caption(view.disclaimer)
    if view.banner:
        st.warning(view.banner[1])

    es = report.executive_summary
    st.subheader("① Executive summary")
    st.markdown(badge(view.summary_badge))
    if es is not None:
        st.write(es.overview)
        cols = st.columns(3)
        for col, title, items in zip(
            cols,
            ("Strongest areas", "Key evidence gaps", "Next steps"),
            (es.strongest_areas, es.key_gaps, es.next_steps),
            strict=True,
        ):
            if items:
                col.markdown(f"**{title}**\n\n" + "\n".join(f"- {i}" for i in items))

    st.subheader("② Evidence coverage")
    st.markdown(badge(BADGE_DETERMINISTIC))
    st.table(view.coverage_rows)
    st.caption(view.coverage_note)
    st.caption(CLASSIFICATION_LEGEND)

    st.subheader("③ Strengths and evidence gaps")
    left, right = st.columns(2)
    with left:
        st.markdown(f"**Strengths** {badge(BADGE_AI_CHECKED)}")
        if not report.strengths:
            st.caption("No AI-generated strengths available.")
        for s in report.strengths:
            evidence_ids = ", ".join(e.span_id for e in s.evidence)
            st.markdown(f"- **{s.title}** — {s.description}  \n  _Evidence: {evidence_ids}_")
    with right:
        st.markdown(f"**Evidence gaps** {badge(BADGE_DETERMINISTIC)}")
        if not report.evidence_gaps:
            st.caption("No gaps: every requirement has supporting or partial evidence.")
        for g in report.evidence_gaps:
            cls = AlignmentClassification(g.classification)
            st.markdown(
                f"- {CLASSIFICATION_ICONS[cls]} **{g.requirement_id}** ({g.importance.value}) "
                f"{g.requirement_text}"
            )

    st.subheader("④ Requirement-by-requirement")
    if not view.requirements:
        st.info(
            "No requirements could be identified. Check that the job description includes "
            "responsibilities or qualifications."
        )
    else:
        f1, f2 = st.columns(2)
        cls_filter = f1.multiselect(
            "Filter by classification",
            list(AlignmentClassification),
            format_func=lambda c: CLASSIFICATION_LABELS[c],
            key="filter_cls",
        )
        imp_filter = f2.multiselect(
            "Filter by importance", ["required", "preferred", "unspecified"], key="filter_imp"
        )
        for r in view.requirements:
            if cls_filter and r.classification not in cls_filter:
                continue
            if imp_filter and r.importance not in imp_filter:
                continue
            with st.expander(f"{r.id} · {r.text} · {r.importance} · {r.icon} {r.label}"):
                st.markdown(f"**Method:** {r.method} · **Requirement source:** {r.source}")
                if r.evidence:
                    st.markdown("**Evidence**")
                    for e in r.evidence:
                        extra = f" `{'` `'.join(e.badges)}`" if e.badges else ""
                        st.markdown(
                            f"- **{e.span_id}** ({e.location}, {e.method}){extra}: “{e.excerpt}”"
                            + (f"  \n  _{e.note}_" if e.note else "")
                        )
                else:
                    st.caption("No resume evidence cited.")
                st.markdown(f"**Why:** {r.explanation}")
                if r.uncertainty:
                    st.markdown(f"**Uncertainty:** {r.uncertainty}")
                if r.clarifying_question:
                    st.markdown(f"**Question for you:** {r.clarifying_question}")
                for note in r.rule_notes:
                    st.caption(note)

    st.subheader("⑤ Resume improvement suggestions")
    st.markdown(badge(BADGE_AI_GUARD))
    if not report.resume_suggestions:
        st.caption(
            "No rewrite suggestions passed the truthfulness checks."
            if report.status.value == "complete"
            else "Not available without AI coaching."
        )
    for sug in report.resume_suggestions:
        with st.container(border=True):
            st.markdown(f"**{sug.target_span_id}** — {sug.issue}")
            c1, c2 = st.columns(2)
            c1.markdown(f"**Current**\n\n{sug.original_text}")
            c2.markdown(f"**Suggested**\n\n{sug.suggested_rewrite}")
            st.caption(sug.rationale)
            if sug.needs_user_input:
                st.caption("Add a real figure only if you have one.")

    st.subheader("⑥ Development roadmap")
    st.markdown(badge(BADGE_AI))
    if not report.development_actions:
        st.caption("Not available without AI coaching.")
    for a in report.development_actions:
        st.markdown(
            f"{a.priority}. **{a.action}** ({a.effort.value})  \n   {a.reason}  \n   "
            f"_Next step:_ {a.next_step}"
        )

    st.subheader("⑦ Clarifying questions")
    if not report.clarifying_questions:
        st.caption("None.")
    for q in report.clarifying_questions:
        st.markdown(f"- **{q.requirement_id}:** {q.question}")

    st.subheader("⑧ Diagnostics")
    diag = report.diagnostics
    with st.expander("Processing diagnostics", expanded=False):
        st.markdown(
            f"Analysis ID `{diag.analysis_id}` · status `{report.status.value}` · "
            f"LLM `{report.llm_status.value}` · model `{diag.model or 'none'}` · "
            f"prompt `{diag.prompt_version}` · taxonomy `{diag.taxonomy_version}` · "
            f"total {diag.total_ms} ms · LLM calls {len(diag.llm_calls)}"
        )
        st.markdown("**Warnings**")
        if view.warning_rows:
            st.dataframe(view.warning_rows, hide_index=True, width="stretch")
        else:
            st.caption("No warnings.")
        if diag.rejected_items:
            st.markdown("**Rejected / adjusted AI items**")
            st.table([{"Item": k, "Count": v} for k, v in sorted(diag.rejected_items.items())])
        st.markdown("**Sections**")
        st.dataframe(view.section_rows, hide_index=True, width="stretch")
        st.markdown("**Links**")
        if view.link_rows:
            st.dataframe(view.link_rows, hide_index=True, width="stretch")
            st.caption(LINK_LEGEND)
        else:
            st.caption("No links found in the resume.")
        st.markdown("**Stage timings**")
        st.dataframe(view.timing_rows, hide_index=True, width="stretch")
        if view.llm_rows:
            st.markdown("**LLM calls**")
            st.dataframe(view.llm_rows, hide_index=True, width="stretch")

    st.download_button(
        "Download report (Markdown)",
        data=report_to_markdown(report),
        file_name="career_coach_report.md",
        mime="text/markdown",
    )


# ------------------------------------------------------------------ main


def main() -> None:
    st.set_page_config(page_title="AI Career Coach", page_icon="🧭", layout="wide")
    try:
        settings = get_settings()
    except CareerCoachError as exc:
        st.error(exc.user_message)
        st.stop()
    secret = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None
    configure_logging(settings.log_level, secrets=[secret] if secret else None, log_file=settings.log_file)
    taxonomy = get_taxonomy()
    llm = get_llm(settings, settings.gemini_model, settings.llm_configured)

    st.session_state.setdefault("nonce", 0)
    nonce = st.session_state["nonce"]

    st.title("🧭 AI Career Coach")
    st.markdown("Understand how your resume's evidence aligns with a job — truthfully.")
    with st.expander("Privacy note", expanded="report" not in st.session_state):
        st.markdown(PRIVACY_NOTE)

    col_resume, col_jd = st.columns(2)
    with col_resume:
        st.markdown(f"**1. Resume** (PDF/DOCX ≤ {settings.max_upload_mb} MB)")
        resume_file = st.file_uploader("Resume", key=f"resume_upload_{nonce}", label_visibility="collapsed")
    with col_jd:
        st.markdown("**2. Job description**")
        jd_mode = st.radio(
            "Job description input",
            ["Paste text", "Upload file"],
            horizontal=True,
            key=f"jd_mode_{nonce}",
            label_visibility="collapsed",
        )
        jd_text = ""
        jd_file = None
        if jd_mode == "Paste text":
            jd_text = st.text_area(
                "Job description text",
                key=f"jd_text_{nonce}",
                height=200,
                max_chars=settings.max_jd_raw_chars,
                label_visibility="collapsed",
                placeholder="Paste the job description here…",
            )
            st.caption(f"{len(jd_text.strip()):,} characters (AI context limit {settings.max_jd_chars:,})")
        else:
            jd_file = st.file_uploader("Job description file (PDF/DOCX)", key=f"jd_upload_{nonce}")

    with st.expander("Optional: preferences and link checks"):
        p1, p2 = st.columns(2)
        target_role = p1.text_input("Target role", max_chars=100, key=f"role_{nonce}")
        seniority = p2.selectbox(
            "Seniority", list(Seniority), format_func=lambda s: s.value.title(), key=f"seniority_{nonce}"
        )
        focus = st.text_input("Focus areas", max_chars=200, key=f"focus_{nonce}")
        check_links = st.checkbox(
            "Check link reachability (sends requests to URLs found in your resume)",
            value=settings.link_check_enabled,
            key=f"links_{nonce}",
        )

    ready = resume_file is not None and (bool(jd_text.strip()) or jd_file is not None)
    b1, b2, b3, _ = st.columns([1, 1, 1, 3])
    analyze = b1.button("Analyze", type="primary", disabled=not ready)
    if b2.button("New analysis"):
        reset_session()
        st.rerun()
    rerun_ai = b3.button("Re-run AI", disabled=not ready or llm is None)

    if llm is not None:
        st.caption(f"AI status: ● Gemini configured ({settings.gemini_model})")
    else:
        st.caption("AI status: ○ Not configured — deterministic analysis only (set GEMINI_API_KEY in .env)")

    if (analyze or rerun_ai) and ready:
        try:
            request = AnalysisRequest(
                resume=UploadedInput(filename=resume_file.name, data=resume_file.getvalue()),
                jd_text=jd_text or None,
                jd_file=UploadedInput(filename=jd_file.name, data=jd_file.getvalue()) if jd_file else None,
                preferences=UserPreferences(target_role=target_role, seniority=seniority, focus_areas=focus),
                check_links=check_links,
            )
        except ValidationError:
            st.error("Some inputs are invalid. Check the preference field lengths and the job description.")
            st.stop()
        key = cache_key(request, settings, taxonomy.version)
        cached = st.session_state.get("report_cache")
        st.session_state.pop("last_error", None)
        if cached and cached[0] == key and not rerun_ai:
            st.session_state["report"] = cached[1]
            st.toast("Reused the previous result for identical inputs (no AI quota used).")
        else:
            parse_cache = st.session_state.setdefault("parse_cache", {})
            with st.status("Analyzing…", expanded=True) as status:

                def on_progress(event: ProgressEvent) -> None:
                    status.write(
                        f"{STATUS_ICONS.get(event.status, '')} {STAGE_LABELS.get(event.stage, event.stage)}: "
                        f"{event.message}"
                    )

                try:
                    report = run_analysis(
                        request,
                        settings=settings,
                        llm=llm,
                        progress=on_progress,
                        parse_cache=parse_cache,
                        taxonomy=taxonomy,
                    )
                except CareerCoachError as exc:
                    status.update(label="Analysis stopped", state="error")
                    st.session_state["last_error"] = (exc.category.value, exc.user_message, None)
                    st.session_state.pop("report", None)
                except Exception:  # noqa: BLE001
                    ref = uuid.uuid4().hex[:12]
                    logger.exception("unexpected error ref=%s", ref)
                    status.update(label="Analysis failed", state="error")
                    st.session_state["last_error"] = (
                        "unexpected",
                        "Unexpected error. Please try again.",
                        ref,
                    )
                    st.session_state.pop("report", None)
                else:
                    label = {
                        "complete": "Analysis complete",
                        "partial": "Partial analysis",
                        "deterministic_only": "Deterministic analysis complete",
                    }[report.status.value]
                    status.update(label=label, state="complete", expanded=False)
                    st.session_state["report"] = report
                    st.session_state["report_cache"] = (key, report)
            while len(parse_cache) > 2:
                parse_cache.pop(next(iter(parse_cache)))

    if "last_error" in st.session_state:
        _category, message, ref = st.session_state["last_error"]
        st.error(message + (f" (ref: {ref})" if ref else ""))

    report = st.session_state.get("report")
    if report is not None:
        render_report(report)
    elif "last_error" not in st.session_state:
        st.divider()
        st.markdown(
            "**How it works**\n\n"
            "1. Upload your resume and paste the job description.\n"
            "2. Click **Analyze**. Documents are parsed locally; up to three AI calls assess the evidence.\n"
            "3. Review evidence-cited results, truthful rewrite suggestions and a development roadmap."
        )


main()
