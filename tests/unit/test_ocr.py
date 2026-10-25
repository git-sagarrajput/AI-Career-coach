from __future__ import annotations

import pymupdf
import pytest
from _doc_builders import build_image_only_pdf

from career_coach.config import load_settings
from career_coach.ingestion import ocr
from career_coach.ingestion.pdf_parser import parse_pdf


def _mixed_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Text page with enough characters")
    doc.insert_pdf(pymupdf.open(stream=build_image_only_pdf(), filetype="pdf"))
    return doc.tobytes()


def test_t17_ocr_disabled_gives_guidance(settings, monkeypatch):
    called = []
    monkeypatch.setattr(ocr, "ocr_page_blocks", lambda page: called.append(page) or [])
    raw = parse_pdf(_mixed_pdf(), settings)
    assert not called
    warning = next(w for w in raw.warnings if w.code == "image_only_page")
    assert "Enable OCR" in warning.message


def test_t17_ocr_enabled_but_unavailable(monkeypatch):
    s = load_settings({"CAREER_COACH_OCR_ENABLED": "true"})
    monkeypatch.setattr(ocr, "ocr_available", lambda: False)
    raw = parse_pdf(_mixed_pdf(), s)
    assert "image_only_page" in {w.code for w in raw.warnings}


def test_ocr_path_marks_origin(monkeypatch):
    s = load_settings({"CAREER_COACH_OCR_ENABLED": "true"})
    monkeypatch.setattr(ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(ocr, "ocr_page_blocks", lambda page: ["Scanned experience line"])
    raw = parse_pdf(_mixed_pdf(), s)
    assert raw.ocr_pages == [2]
    assert any(b.origin.value == "ocr" for b in raw.blocks)


def test_ocr_failure_is_warning(monkeypatch):
    s = load_settings({"CAREER_COACH_OCR_ENABLED": "true"})
    monkeypatch.setattr(ocr, "ocr_available", lambda: True)

    def boom(page):
        raise RuntimeError("tesseract missing")

    monkeypatch.setattr(ocr, "ocr_page_blocks", boom)
    raw = parse_pdf(_mixed_pdf(), s)
    assert "ocr_failed" in {w.code for w in raw.warnings}


@pytest.mark.skipif(not ocr.ocr_available(), reason="Tesseract not installed")
def test_real_ocr_smoke():
    s = load_settings({"CAREER_COACH_OCR_ENABLED": "true"})
    parse_pdf(_mixed_pdf(), s)


@pytest.fixture
def clean_tessdata_cache():
    ocr.find_tessdata.cache_clear()
    ocr.ocr_available.cache_clear()
    yield
    ocr.find_tessdata.cache_clear()
    ocr.ocr_available.cache_clear()


def test_find_tessdata_prefers_env(clean_tessdata_cache, monkeypatch, tmp_path):
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    assert ocr.find_tessdata() == str(tmp_path)
    assert ocr.ocr_available()


def test_find_tessdata_default_windows_install(clean_tessdata_cache, monkeypatch, tmp_path):
    tessdata = tmp_path / "Tesseract-OCR" / "tessdata"
    tessdata.mkdir(parents=True)
    (tessdata / "eng.traineddata").write_bytes(b"x")
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
    monkeypatch.setattr(ocr.sys, "platform", "win32")
    install_dirs = [tmp_path / "missing", tmp_path / "Tesseract-OCR"]
    monkeypatch.setattr(ocr, "_windows_install_dirs", lambda: install_dirs)
    assert ocr.find_tessdata() == str(tessdata)


def test_find_tessdata_none_when_not_installed(clean_tessdata_cache, monkeypatch, tmp_path):
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
    monkeypatch.setattr(ocr, "_windows_install_dirs", lambda: [tmp_path / "missing"])
    assert ocr.find_tessdata() is None
    assert not ocr.ocr_available()
