from __future__ import annotations

from integration_helpers import minimal_report

from career_coach.reporting.presentation import build_report_view, report_to_markdown


def test_t64_markdown_is_deterministic_and_labelled():
    report = minimal_report()
    first = report_to_markdown(report)
    assert first == report_to_markdown(report)
    assert "Deterministic" in first
    assert "Not shown in this resume" in first
    coverage = first.split("## Evidence coverage")[1].split("## Requirements")[0]
    assert "%" not in coverage
    assert "lack" not in first.lower()
    assert "It is not a hiring prediction." in first


def test_view_model():
    report = minimal_report()
    view = build_report_view(report)
    assert view.banner is not None and "deterministic" in view.banner[1].lower()
    assert len(view.coverage_rows) == 3
    assert view.requirements and all(r.method == "keyword" for r in view.requirements)
    importance_order = [r.importance for r in view.requirements]
    assert importance_order == sorted(importance_order, key=["required", "preferred", "unspecified"].index)
