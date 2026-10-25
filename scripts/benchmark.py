"""Benchmark analysis stages on the synthetic sample documents.

python scripts/benchmark.py --runs 5                # deterministic stages only (no network, no key)
python scripts/benchmark.py --runs 3 --with-llm     # includes Gemini calls (uses quota!)
python scripts/benchmark.py --runs 3 --with-links   # includes live link checks (network)
Add --record to append the results table to docs/evaluation.md.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _doc_builders import build_docx, build_pdf  # noqa: E402

from career_coach.analysis.orchestrator import run_analysis  # noqa: E402
from career_coach.analysis.taxonomy import load_taxonomy  # noqa: E402
from career_coach.config import load_settings  # noqa: E402
from career_coach.llm.client import build_llm_client  # noqa: E402
from career_coach.models.requests import AnalysisRequest, UploadedInput  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--with-llm", action="store_true")
    parser.add_argument("--with-links", action="store_true")
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    llm = build_llm_client(settings) if args.with_llm else None
    if args.with_llm and llm is None:
        print("--with-llm requires GEMINI_API_KEY")
        return 1
    taxonomy = load_taxonomy()
    content = json.loads((FIXTURES / "resumes" / "ml_engineer_onnx.json").read_text("utf-8"))
    jd = (FIXTURES / "jd_ml_engineer.txt").read_text("utf-8")
    inputs = {"pdf": ("resume.pdf", build_pdf(content)), "docx": ("resume.docx", build_docx(content))}

    rows: list[str] = []
    for label, (name, data) in inputs.items():
        per_stage: dict[str, list[int]] = {}
        totals: list[int] = []
        for _ in range(args.runs):
            request = AnalysisRequest(
                resume=UploadedInput(filename=name, data=data), jd_text=jd, check_links=args.with_links
            )
            report = run_analysis(request, settings=settings, llm=llm, taxonomy=taxonomy)
            totals.append(report.diagnostics.total_ms)
            for timing in report.diagnostics.stage_timings:
                per_stage.setdefault(timing.stage, []).append(timing.duration_ms)
        per_stage["TOTAL"] = totals
        for stage, values in per_stage.items():
            rows.append(
                f"| {label} | {stage} | {statistics.median(values):.0f} | {min(values)} | {max(values)} |"
            )

    header = [
        f"Benchmark {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · runs={args.runs} · "
        f"llm={'on' if llm else 'off'} · links={'on' if args.with_links else 'off'}",
        f"Python {platform.python_version()} · {platform.system()} {platform.release()} · "
        f"pymupdf {version('pymupdf')} · python-docx {version('python-docx')} · "
        f"model {settings.gemini_model if llm else 'n/a'}",
        "",
        "| Input | Stage | Median ms | Min ms | Max ms |",
        "|---|---|---|---|---|",
    ]
    table = "\n".join(header + rows)
    print(table)
    if args.record:
        target = ROOT / "docs" / "evaluation.md"
        with target.open("a", encoding="utf-8") as fh:
            fh.write("\n\n### Benchmark run\n\n" + table + "\n")
        print(f"\nAppended to {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
