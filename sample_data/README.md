# Sample data

All documents here are **synthetic**. Names, employers and contact details are fictional
(`example.com` addresses, `555-01xx` phone numbers).

Generate (or regenerate) the files with:

```bash
python scripts/generate_sample_data.py
```

| File | Purpose |
|---|---|
| `resume_ml_engineer.pdf` | 2-section-heavy PDF resume with a skills table, ONNX/FastAPI inference bullet and GitHub/LinkedIn/portfolio links |
| `resume_ml_engineer.docx` | Same content as DOCX with heading styles, a table and hyperlinks |
| `resume_career_switcher.docx` | Teacher → data analyst; transferable evidence; no headings |
| `resume_injection_test.pdf` | Contains a white-text prompt-injection line (EC-08 demo) |
| `resume_scanned_page.pdf` | Last page is an image only (image-only warning demo) |
| `jd_ml_engineer.txt` | Requirements / Nice to have sections; Kubernetes + AWS required |
| `jd_data_analyst.txt` | SQL, dashboards, stakeholder communication, vague "rockstar" line |

Generated files are git-ignored.
