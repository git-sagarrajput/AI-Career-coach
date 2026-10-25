# Evaluation

Artifact F. Test plan, LLM evaluation rubric and recorded results.

## Automated tests

```bash
pytest            # unit + integration; offline, no API key (network is blocked by an autouse fixture)
RUN_LIVE_TESTS=1 pytest -m live   # opt-in live contract test (uses Gemini quota)
```

Test IDs T-01…T-70 from the specification appear in test names (e.g. `test_t48_rewrite_guard_rejects`).

## LLM quality evaluation

Cases EC-01…EC-11 live in `tests/fixtures/eval_cases/cases.json` (synthetic data only). EC-12 (schema failure)
is covered offline by `tests/integration/test_orchestrator_fallbacks.py`.

```bash
python scripts/run_llm_eval.py --runs 3 --record
```

| Metric | Proposed POC target* |
|---|---|
| Schema validity (first attempt) | ≥ 0.90 |
| Schema validity (after repair) | 1.00 |
| Requirement recall | ≥ 0.8 |
| Citation precision | ≥ 0.95 |
| Evidence-ID validity (pre-rules) | ≥ 0.95 |
| Classification agreement | ≥ 0.8 |
| Critical false support | 0 |
| Rewrite guard rejections | ≤ 0.2 (informational) |
| Unsupported claims in final report | 0 |
| Run-to-run stability | ≥ 0.8 |

\*Proposed targets for this project, not industry standards. The first measured run is the baseline.

## Manual rubric (1–5, two reviewers if possible)

| Dimension | 1 | 3 | 5 |
|---|---|---|---|
| Evidence attribution | Claims lack or misuse evidence | Mostly cited; some loose links | Every claim traceable to the right span |
| Truthfulness of rewrites | Invents facts | Minor embellishment in tone | Same facts, clearer phrasing |
| Missing-evidence handling | Says "lacks skill" | Mixed | Consistently "not shown in resume" + useful question |
| Specificity/usefulness | Generic advice | Some specific items | Specific, actionable, prioritized |
| Tone and safety | Outcome promises or judgemental | Neutral | Supportive, no outcome claims |

Proposed target: mean ≥ 4.0 on each dimension across EC-01…EC-11.

| Case | Reviewer | Attribution | Rewrites | Missing evidence | Usefulness | Tone | Notes |
|---|---|---|---|---|---|---|---|
| _to be filled after a live run_ | | | | | | | |

## Recorded results

Results below are appended by `scripts/benchmark.py --record` and `scripts/run_llm_eval.py --record`.
The first benchmark run on the presenter's machine is the **baseline**; the NFR-06 target (< 3 s for the
deterministic stages) is a proposed target to be confirmed against it.


### Benchmark run

Benchmark 2026-10-18 07:23 UTC · runs=3 · llm=off · links=off
Python 3.13.13 · Windows 11 · pymupdf 1.28.2 · python-docx 1.2.0 · model n/a

| Input | Stage | Median ms | Min ms | Max ms |
|---|---|---|---|---|
| pdf | parse_resume | 127 | 125 | 127 |
| pdf | parse_jd | 0 | 0 | 0 |
| pdf | sections | 0 | 0 | 1 |
| pdf | TOTAL | 129 | 126 | 129 |
| docx | parse_resume | 30 | 29 | 31 |
| docx | parse_jd | 0 | 0 | 0 |
| docx | sections | 0 | 0 | 0 |
| docx | TOTAL | 32 | 31 | 33 |
