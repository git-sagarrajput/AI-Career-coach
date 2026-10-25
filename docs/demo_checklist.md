# Live demonstration checklist

Artifact G (demo part). See the README for setup.

## Before the session (T-1 day)

- [ ] Fresh venv install from the README on the demo machine; `pytest` green; `ruff check .` clean.
- [ ] `python scripts/generate_sample_data.py` run; files present in `sample_data/`.
- [ ] `python scripts/check_model.py` succeeds; note the model name; keep a fallback model in `.env` comments.
- [ ] Run one full analysis to confirm quota; note timings from Diagnostics.
- [ ] Prepare a way to run without a key (rename `.env` or blank `GEMINI_API_KEY`) for the degraded-mode demo.
- [ ] Close apps that could display secrets; make sure `.env` is not open on screen; increase terminal font size.
- [ ] Browser zoom ~125%; theme readable on the projector.
- [ ] Save a Markdown export from rehearsal as a fallback (429/network contingency).

## Demo script (≈ 15 minutes)

1. **Architecture (2 min):** show the diagram in `docs/architecture.md` and the task-allocation table in the
   spec. The LLM appears in only three boxes.
2. **Deterministic ingestion (2 min):** upload `sample_data/resume_ml_engineer.docx`, run without a key or
   open Diagnostics → Sections/Links. Show `C++` / `.NET` preserved in spans.
3. **Full analysis (4 min):** paste `sample_data/jd_ml_engineer.txt`, tick link checks, click **Analyze** and
   narrate the stages. Show:
   - "Experience deploying ML inference services" → Supported/Partial with the ONNX/FastAPI span, method
     *semantic (AI)*.
   - Kubernetes → **Not shown in this resume** plus a clarifying question (never "you lack").
   - Coverage table: counts only, no percentage.
   - A rewrite suggestion (before/after) without invented metrics (`needs_user_input`).
4. **Guardrails (3 min):** Diagnostics → rejected/adjusted items and rule adjustments; show
   `rewrite_guard` in `src/career_coach/analysis/rules.py` and `test_t48_rewrite_guard_rejects`.
5. **Failure drills (3 min):**
   - Upload a `.txt` file as resume → specific error.
   - Upload `resume_scanned_page.pdf` → image-only warning.
   - Upload `resume_injection_test.pdf` → `possible_prompt_injection` warning, classifications unaffected.
   - Restart without a key → deterministic-only banner and report.
6. **Engineering (1 min):** run `pytest -q` live (offline, no key). Show a log line with `aid=` and no content.

## Contingencies

| Problem | Response |
|---|---|
| 429 / quota | Use the rehearsal Markdown export to explain degradation behaviour |
| 503 "high demand" / 504 / "exceeded its time or attempt budget" | Set `GEMINI_MODEL=gemini-3.6-flash` in `.env` and restart Streamlit |
| Network down | Deterministic-only demo; tests still run |
| Model slow | Narrate per-stage progress; show Diagnostics latency |
| Image-only JD/resume PDF | Enable OCR (README → Optional OCR) or paste the JD text |

## After the session

- Clear `sample_data/` outputs if the machine is shared.
- Rotate the API key if it was ever displayed.
