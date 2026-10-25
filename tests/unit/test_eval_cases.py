from __future__ import annotations

import json
from pathlib import Path

import run_llm_eval

CASES = Path(__file__).resolve().parents[1] / "fixtures" / "eval_cases" / "cases.json"


def test_eval_cases_are_buildable_offline():
    cases = json.loads(CASES.read_text("utf-8"))
    assert len(cases) >= 10
    assert len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        request = run_llm_eval.build_request(case)
        assert request.resume.data
        for exp in case["expected_requirements"]:
            assert exp["match_any"] and exp["acceptable"]
