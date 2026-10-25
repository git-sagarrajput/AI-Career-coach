from __future__ import annotations

import pymupdf
import pytest
from _doc_builders import build_image_only_pdf, build_pdf_pages

from career_coach.errors import DocumentParseError
from career_coach.ingestion import pdf_parser
from career_coach.ingestion.pdf_parser import parse_pdf


def test_t13_pages_are_recorded(settings):
    raw = parse_pdf(build_pdf_pages(["First page text here", "Second page text here"]), settings)
    assert [b.page for b in raw.blocks] == [1, 2]
    assert raw.page_count == 2


def test_t14_link_annotation(settings):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "My GitHub profile", fontsize=11)
    page.insert_link(
        {"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(70, 60, 200, 76), "uri": "https://github.com/demo"}
    )
    raw = parse_pdf(doc.tobytes(), settings)
    assert len(raw.links) == 1
    source = raw.links[0].sources[0]
    assert raw.links[0].url_raw == "https://github.com/demo"
    assert source.kind == "pdf_annotation" and source.page == 1
    assert source.anchor_text and "GitHub" in source.anchor_text


def test_t15_image_only(settings):
    with pytest.raises(DocumentParseError) as exc:
        parse_pdf(build_image_only_pdf(), settings)
    assert exc.value.code == "no_extractable_text"

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Text page with enough characters")
    image_doc = pymupdf.open(stream=build_image_only_pdf(), filetype="pdf")
    doc.insert_pdf(image_doc)
    raw = parse_pdf(doc.tobytes(), settings)
    assert raw.image_only_pages == [2]
    assert [w.code for w in raw.warnings] == ["image_only_page"]


def test_t16_table_rows(settings, ml_resume_pdf):
    raw = parse_pdf(ml_resume_pdf, settings)
    rows = [b.text for b in raw.blocks if b.kind == "table_row"]
    if not rows:
        pytest.skip("find_tables did not detect the synthetic table on this PyMuPDF version")
    assert "Languages | Python, SQL, C++" in rows


def test_t16_table_failure_is_warning(settings, monkeypatch, ml_resume_pdf):
    def boom(self, *args, **kwargs):
        raise RuntimeError("table failure")

    monkeypatch.setattr(pymupdf.Page, "find_tables", boom)
    raw = parse_pdf(ml_resume_pdf, settings)
    assert "pdf_table_extraction_failed" in {w.code for w in raw.warnings}
    assert any("Python, SQL" in b.text for b in raw.blocks)


def test_bullets_and_headings_split(settings, ml_resume_pdf):
    raw = parse_pdf(ml_resume_pdf, settings)
    texts = [b.text for b in raw.blocks]
    assert "EXPERIENCE" in texts
    assert any(b.kind == "list_item" and "ONNX" in b.text for b in raw.blocks)
    assert pdf_parser.looks_like_heading("WORK HISTORY")
