# AI Career Coach (POC)

A locally run Streamlit application that shows, requirement by requirement, how the **evidence in your resume**
aligns with **one target job description**, and what you can truthfully do about the gaps.

- Documents are parsed **locally** (PyMuPDF, python-docx) into citable evidence spans (`R012`, `J004`, …).
- Deterministic analysis (skill taxonomy, URL policy, optional link checks) works **without any API key**.
- Exactly **three schema-constrained Gemini calls** extract requirements, assess evidence and write coaching.
- Every model output passes **deterministic guardrails**: schema validation, evidence-ID checks, reconciliation
  rules R1–R6, a rewrite guard (no invented numbers/tools), and an outcome-claim filter.
- There is **no match percentage**. Coverage is shown as counts; "Not shown in this resume" never means "lacks the skill".

The full specification lives in
[docs/ai_career_coach_problem_statement_and_engineering_spec.md](docs/ai_career_coach_problem_statement_and_engineering_spec.md)
and the ideation/architecture learning resource in
[docs/capstone_ideation_architecture_learning_resource.md](docs/capstone_ideation_architecture_learning_resource.md).
Architecture notes and deviations: [docs/architecture.md](docs/architecture.md).

## Requirements

- Python **3.11–3.13** (3.12 recommended)
- Optional: Tesseract OCR (only for scanned PDFs)

## Setup

### Windows (PowerShell)

```powershell
git clone https://github.com/git-sagarrajput/AI-Career-coach.git ai-career-coach
cd ai-career-coach
py -3.12 -m venv .venv                 # or: python -m venv .venv
.\.venv\Scripts\Activate.ps1           # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env            # then edit .env and set GEMINI_API_KEY
python scripts/generate_sample_data.py
python scripts/check_model.py          # optional: verifies key + model (no document data sent)
pytest
streamlit run app.py                   # opens http://localhost:8501
```

### macOS / Linux

```bash
git clone https://github.com/git-sagarrajput/AI-Career-coach.git ai-career-coach
cd ai-career-coach
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
cp .env.example .env                   # then edit .env and set GEMINI_API_KEY
python scripts/generate_sample_data.py
python scripts/check_model.py
pytest
streamlit run app.py
```

Run `streamlit run app.py` from the `ai-career-coach/` folder so `.streamlit/config.toml` and `.env` are picked up.

## Configure Gemini

1. Sign in to Google AI Studio → *Get API key* → create a key.
2. Put it in `.env` as `GEMINI_API_KEY=...`. Never paste the key into code, the UI, screenshots or chat.
3. Optionally set `GEMINI_MODEL` (default `gemini-3.8-flash`). Verify with `python scripts/check_model.py`.
   Fallback suggestions: `gemini-3.6-flash`, `gemini-3.5-flash-lite` (`gemini-2.5-flash` is no longer
   available to new API keys). If the default model is overloaded (503 "high demand", 504, or the AI stages
   end with "exceeded its time or attempt budget"), switch `GEMINI_MODEL` to a fallback and restart Streamlit.

**Free-tier caveats:** rate limits, quotas, available models and terms are set by Google and can change at any time.
Content submitted on the free tier **may be used by Google to improve its products**. This app does not promise
any quota, availability or zero cost, and it never silently switches to another model or provider.

Without a key the app still produces a **deterministic-only report** (parsing diagnostics, sections, links and
keyword-based coverage).

## Privacy

Files are processed on your computer. When AI analysis runs, selected extracted text (job description spans and
resume spans **excluding the contact section**, with emails and phone numbers masked) is sent to the Gemini API.
Nothing is written to disk by the app; logs contain metadata only. Use synthetic or redacted documents for demos.

## Configuration

All settings are environment variables (see [.env.example](.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | – | Enables AI analysis |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Model ID |
| `GEMINI_TIMEOUT_SECONDS` / `GEMINI_MAX_RETRIES` / `GEMINI_MAX_OUTPUT_TOKENS` | 60 / 2 / 8192 | Per-call limits |
| `CAREER_COACH_LLM_TOTAL_BUDGET_SECONDS` | 180 | Wall-clock budget for all LLM work |
| `CAREER_COACH_MAX_UPLOAD_MB` / `CAREER_COACH_MAX_PAGES` | 5 / 10 | Upload limits (keep MB in sync with `.streamlit/config.toml`) |
| `CAREER_COACH_MAX_RESUME_CHARS` / `CAREER_COACH_MAX_JD_CHARS` | 24000 / 15000 | AI context budgets |
| `CAREER_COACH_LINK_CHECK_ENABLED` | false | Default of the link-check checkbox |
| `CAREER_COACH_LINK_MAX_URLS` / `_WORKERS` / `_TIMEOUT_SECONDS` / `_DEADLINE_SECONDS` | 15 / 5 / 10 / 20 | Link-check bounds |
| `CAREER_COACH_OCR_ENABLED` / `CAREER_COACH_OCR_MAX_PAGES` | false / 3 | Optional OCR |
| `CAREER_COACH_LOG_LEVEL` / `CAREER_COACH_LOG_FILE` | INFO / – | Logging (metadata only) |
| `RUN_LIVE_TESTS` | – | `1` enables `pytest -m live` |

## Optional OCR

Install Tesseract (`winget install UB-Mannheim.TesseractOCR`, `brew install tesseract`, or
`sudo apt install tesseract-ocr`), then set `CAREER_COACH_OCR_ENABLED=true`. The app finds the language data via
`TESSDATA_PREFIX`, `tesseract` on `PATH`, or (Windows) the default install folders `%ProgramFiles%\Tesseract-OCR`
and `%LOCALAPPDATA%\Programs\Tesseract-OCR`, so no `PATH` change is needed after a default install. Restart
Streamlit after installing. Page images are never sent to Gemini.

## Tests and tooling

```bash
pytest                       # offline, no API key needed (network is blocked in tests)
ruff check .
python scripts/benchmark.py --runs 5            # deterministic stage timings
python scripts/run_llm_eval.py --runs 1         # live evaluation (uses quota)
RUN_LIVE_TESTS=1 pytest -m live                 # live Gemini contract test (uses quota)
```

## Expected user journey

1. Upload a resume (PDF/DOCX) and paste a job description (or upload a PDF/DOCX).
2. Optionally set target role, seniority, focus areas and tick **Check link reachability**.
3. Click **Analyze** and watch the stages: Validating → Parsing → Sections → Links → Requirements (AI) →
   Assessment (AI) → Coaching (AI) → Report.
4. Review the summary, coverage counts, requirement evidence, rewrite suggestions, roadmap and diagnostics.
5. Download the Markdown report; click **New analysis** to clear everything.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `ModuleNotFoundError: career_coach` | Activate `.venv`; run `pip install -e ".[dev]"` |
| "Gemini API key not configured" | Create `.env` in `ai-career-coach/`; restart Streamlit |
| "API key was rejected" | Create a new key in AI Studio |
| "Model … not available" | `python scripts/check_model.py`; set `GEMINI_MODEL` to a listed model |
| 503 "high demand", 504, or "exceeded its time or attempt budget" | Default model overloaded/slow; set `GEMINI_MODEL=gemini-3.6-flash` and restart Streamlit |
| "rate limit or quota reached" (429) | Wait; avoid repeated re-runs (identical inputs reuse the cached result) |
| "This PDF is password-protected" | Export an unprotected copy |
| "Page N appears to be an image" / "No extractable text" | The PDF is a screenshot/scan: paste the text, or install Tesseract and set `CAREER_COACH_OCR_ENABLED=true` |
| `pytest` hangs at `test_app_smoke.py` | Local loopback sockets are blocked (sandbox/firewall); run outside the sandbox or `--deselect` that test |
| All links `connection_error` | Corporate proxy/offline (`trust_env=False` ignores proxy vars by design) |
| Upload limit shows 200 MB | Run `streamlit run app.py` from the `ai-career-coach/` folder |
