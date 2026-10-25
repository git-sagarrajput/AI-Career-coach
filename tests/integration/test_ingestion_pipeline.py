from __future__ import annotations

from career_coach.ingestion.pipeline import ingest_document, ingest_text
from career_coach.models.enums import DocumentRole, SectionKind, SourceFormat


def test_t65_docx_and_pdf_resumes(settings, ml_resume_docx, ml_resume_pdf):
    for data, name, fmt in (
        (ml_resume_docx, "r.docx", SourceFormat.DOCX),
        (ml_resume_pdf, "r.pdf", SourceFormat.PDF),
    ):
        doc = ingest_document(data, name, DocumentRole.RESUME, settings)
        assert doc.source_format == fmt
        kinds = [s.kind for s in doc.sections]
        for expected in (
            SectionKind.CONTACT,
            SectionKind.SUMMARY,
            SectionKind.SKILLS,
            SectionKind.EXPERIENCE,
            SectionKind.PROJECTS,
            SectionKind.EDUCATION,
            SectionKind.CERTIFICATIONS,
        ):
            assert expected in kinds, (name, expected)
        texts = [s.text for s in doc.spans]
        assert any("ONNX models through a FastAPI inference API" in t for t in texts)
        assert any(t.startswith("Languages | Python, SQL, C++") for t in texts)
        urls = {link.url_normalized for link in doc.links}
        assert {"https://github.com/alex-rivera-demo", "https://portfolio.example.com"} <= urls
        again = ingest_document(data, name, DocumentRole.RESUME, settings)
        assert again.model_dump() | {"stats": None} == doc.model_dump() | {"stats": None}


def test_jd_text_sections(settings, jd_ml_text):
    jd = ingest_text(jd_ml_text, DocumentRole.JOB_DESCRIPTION, settings)
    kinds = {s.kind for s in jd.sections}
    assert {
        SectionKind.REQUIREMENTS,
        SectionKind.PREFERRED,
        SectionKind.RESPONSIBILITIES,
        SectionKind.BENEFITS,
    } <= kinds
    assert jd.spans[0].id == "J001"


def test_career_switcher_without_headings(settings, switcher_content):
    from _doc_builders import build_docx

    doc = ingest_document(
        build_docx(switcher_content, use_heading_styles=False), "s.docx", DocumentRole.RESUME, settings
    )
    assert [s.kind for s in doc.sections] == [SectionKind.OTHER]
    assert "no_sections_detected" in {w.code for w in doc.warnings}
