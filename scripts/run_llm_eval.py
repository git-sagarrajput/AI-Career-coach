"""Live LLM quality evaluation over tests/fixtures/eval_cases (requires GEMINI_API_KEY; uses quota).

python scripts/run_llm_eval.py --runs 3 [--cases EC-02,EC-03] [--record]
Targets are proposed POC targets (spec 17.3), not industry standards.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _doc_builders import build_docx, build_pdf  # noqa: E402

from career_coach.analysis.orchestrator import run_analysis  # noqa: E402
from career_coach.analysis.taxonomy import load_taxonomy  # noqa: E402
from career_coach.config import load_settings  # noqa: E402
from career_coach.errors import CareerCoachError  # noqa: E402
from career_coach.llm.client import build_llm_client  # noqa: E402
from career_coach.models.analysis import AnalysisReport  # noqa: E402
from career_coach.models.enums import AssessmentMethod  # noqa: E402
from career_coach.models.requests import AnalysisRequest, UploadedInput  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
LACK = ("lacks", "does not have", "doesn't have", "has no ", "cannot")


def build_request(case: dict) -> AnalysisRequest:
    content = json.loads((FIXTURES / case["resume_fixture"]).read_text("utf-8"))
    content.update(case.get("resume_overrides", {}))
    if case.get("resume_format") == "pdf":
        name, data = "resume.pdf", build_pdf(content)
    else:
        name, data = "resume.docx", build_docx(content, **case.get("resume_options", {}))
    jd = case.get("jd_text") or (FIXTURES / case["jd_fixture"]).read_text("utf-8")
    return AnalysisRequest(resume=UploadedInput(filename=name, data=data), jd_text=jd)


def find_match(report: AnalysisReport, match_any: list[str]):
    for m in report.matches:
        text = m.requirement.text.casefold()
        if any(k.casefold() in text for k in match_any):
            return m
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--cases", default="")
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    llm = build_llm_client(settings)
    if llm is None:
        print("GEMINI_API_KEY is required for the live evaluation.")
        return 1
    taxonomy = load_taxonomy()
    cases = json.loads((FIXTURES / "eval_cases" / "cases.json").read_text("utf-8"))
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    m: Counter = Counter()
    stability: dict[tuple[str, str], list[str]] = defaultdict(list)
    case_lines: list[str] = []
    for case in cases:
        for run_no in range(args.runs):
            try:
                report = run_analysis(build_request(case), settings=settings, llm=llm, taxonomy=taxonomy)
            except CareerCoachError as exc:
                case_lines.append(f"- {case['id']} run {run_no + 1}: input error {exc.code}")
                continue
            diag = report.diagnostics
            for call in diag.llm_calls:
                m["calls"] += 1
                m["valid_first"] += call.outcome == "ok" and call.attempts == 1 and not call.repaired
                m["valid_after_repair"] += call.outcome == "ok"
            rej = diag.rejected_items
            extracted_kept = sum(1 for x in report.matches if x.requirement.origin == "llm")
            dropped = rej.get("requirement_citation_failed", 0) + rej.get("requirement_quote_mismatch", 0)
            m["req_kept"] += extracted_kept
            m["req_extracted"] += extracted_kept + dropped
            cited = sum(
                1 for x in report.matches for e in x.evidence if e.method == AssessmentMethod.SEMANTIC_LLM
            )
            m["evidence_valid"] += cited
            m["evidence_total"] += cited + rej.get("invalid_evidence_id_removed", 0)
            m["rewrites_rejected"] += rej.get("rewrite_rejected", 0)
            m["rewrites_total"] += rej.get("rewrite_rejected", 0) + len(report.resume_suggestions)
            m["lack_phrasing"] += sum("R6" in x.rule_adjustments for x in report.matches)

            for exp in case["expected_requirements"]:
                m["expected"] += 1
                match = find_match(report, exp["match_any"])
                if match is None:
                    continue
                m["found"] += 1
                cls = match.classification.value
                m["agree"] += cls in exp["acceptable"]
                if cls == "supported" and set(exp["acceptable"]) <= {"not_evidenced", "ambiguous"}:
                    m["critical_false_support"] += 1
                stability[(case["id"], exp["key"])].append(cls)

            for sug in report.resume_suggestions:
                for token in case.get("forbidden_in_rewrites", []):
                    if (
                        token.casefold() in sug.suggested_rewrite.casefold()
                        and token.casefold() not in sug.original_text.casefold()
                    ):
                        m["forbidden_in_rewrites"] += 1
            narrative = " ".join(
                [report.executive_summary.overview if report.executive_summary else ""]
                + [x.explanation for x in report.matches]
            )
            m["lack_in_final"] += sum(narrative.casefold().count(w) for w in LACK)
            expect_warning = case.get("expect_warning")
            warn_ok = not expect_warning or expect_warning in {w.code for w in diag.warnings}
            case_lines.append(
                f"- {case['id']} run {run_no + 1}: status={report.status.value} "
                f"llm={report.llm_status.value} "
                f"requirements={len(report.matches)} suggestions={len(report.resume_suggestions)} "
                f"warning_check={'ok' if warn_ok else 'MISSING ' + expect_warning}"
            )

    def ratio(a: str, b: str) -> str:
        return f"{m[a] / m[b]:.2f}" if m[b] else "n/a"

    stable = sum(1 for v in stability.values() if len(set(v)) == 1)
    rows = [
        ("Schema validity (first attempt)", ratio("valid_first", "calls"), ">= 0.90"),
        ("Schema validity (after repair)", ratio("valid_after_repair", "calls"), "1.00"),
        ("Requirement recall", ratio("found", "expected"), ">= 0.8"),
        ("Citation precision", ratio("req_kept", "req_extracted"), ">= 0.95"),
        ("Evidence-ID validity (pre-rules)", ratio("evidence_valid", "evidence_total"), ">= 0.95"),
        ("Classification agreement", ratio("agree", "found"), ">= 0.8"),
        ("Critical false support", str(m["critical_false_support"]), "0"),
        ("Rewrite guard rejections", ratio("rewrites_rejected", "rewrites_total"), "<= 0.2"),
        ("Forbidden tokens in accepted rewrites", str(m["forbidden_in_rewrites"]), "0"),
        ("Lack-of-skill phrasing before R6", str(m["lack_phrasing"]), "-> 0"),
        ("Lack-of-skill phrasing in final report", str(m["lack_in_final"]), "0"),
        (
            "Run-to-run stability",
            f"{stable / len(stability):.2f}" if stability and args.runs > 1 else "n/a",
            ">= 0.8",
        ),
    ]
    header = (
        f"LLM eval {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · model {settings.gemini_model} · "
        f"cases {len(cases)} · runs {args.runs}"
    )
    table = "\n".join(
        [header, "", "| Metric | Value | Proposed target |", "|---|---|---|"]
        + [f"| {a} | {b} | {c} |" for a, b, c in rows]
        + ["", *case_lines]
    )
    print(table)
    if args.record:
        with (ROOT / "docs" / "evaluation.md").open("a", encoding="utf-8") as fh:
            fh.write("\n\n### LLM evaluation run\n\n" + table + "\n")
        print("\nAppended to docs/evaluation.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
