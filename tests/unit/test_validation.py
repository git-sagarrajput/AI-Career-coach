from __future__ import annotations

import io
import zipfile

import pytest
from _doc_builders import build_encrypted_pdf, build_pdf_pages

from career_coach.config import load_settings
from career_coach.errors import DocumentParseError, InputValidationError, UnsupportedDocumentError
from career_coach.ingestion.validation import (
    DetectedFormat,
    extension_mismatch_warning,
    validate_jd_text,
    validate_upload,
)


def test_t01_valid_pdf_and_docx(settings, ml_resume_pdf, ml_resume_docx):
    assert validate_upload(ml_resume_pdf, "r.pdf", settings) == DetectedFormat.PDF
    assert validate_upload(ml_resume_docx, "r.docx", settings) == DetectedFormat.DOCX


def test_t02_extension_mismatch_is_warning(settings, ml_resume_docx):
    fmt = validate_upload(ml_resume_docx, "resume.pdf", settings)
    assert fmt == DetectedFormat.DOCX
    warning = extension_mismatch_warning("resume.pdf", fmt)
    assert warning is not None and warning.code == "extension_mismatch"
    assert extension_mismatch_warning("resume.docx", fmt) is None


@pytest.mark.parametrize("data", [b"plain text resume", b"\x89PNG\r\n\x1a\n0000", bytes(range(256))])
def test_t03_unsupported(settings, data):
    with pytest.raises(UnsupportedDocumentError) as exc:
        validate_upload(data, "r.txt", settings)
    assert exc.value.code == "unsupported_format"


def test_t04_too_large():
    s = load_settings({"CAREER_COACH_MAX_UPLOAD_MB": "1"})
    data = b"%PDF-" + b"0" * (1024 * 1024)
    with pytest.raises(InputValidationError) as exc:
        validate_upload(data, "r.pdf", s)
    assert exc.value.code == "file_too_large"


def test_t05_empty_inputs(settings):
    with pytest.raises(InputValidationError) as exc:
        validate_upload(b"", "r.pdf", settings)
    assert exc.value.code == "empty_file"
    with pytest.raises(InputValidationError) as exc:
        validate_jd_text("   \n ", settings)
    assert exc.value.code == "empty_job_description"
    with pytest.raises(InputValidationError) as exc:
        validate_jd_text("x" * 50_001, settings)
    assert exc.value.code == "job_description_too_long"


def test_t06_encrypted_pdf(settings):
    with pytest.raises(InputValidationError) as exc:
        validate_upload(build_encrypted_pdf(), "r.pdf", settings)
    assert exc.value.code == "encrypted_pdf"


def test_t07_ole_header(settings):
    with pytest.raises(UnsupportedDocumentError) as exc:
        validate_upload(bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 100, "r.doc", settings)
    assert exc.value.code == "legacy_or_encrypted_office"


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def test_t08_malformed_and_suspicious_docx(settings):
    with pytest.raises(UnsupportedDocumentError) as exc:
        validate_upload(_zip({"[Content_Types].xml": b"<x/>"}), "r.docx", settings)
    assert exc.value.code == "malformed_docx"
    bomb = _zip({"[Content_Types].xml": b"<x/>", "word/document.xml": b"0" * 2_000_000})
    with pytest.raises(UnsupportedDocumentError) as exc:
        validate_upload(bomb, "r.docx", settings)
    assert exc.value.code == "suspicious_archive"


def test_t09_too_many_pages(settings):
    data = build_pdf_pages([f"page {i}" for i in range(11)])
    with pytest.raises(InputValidationError) as exc:
        validate_upload(data, "r.pdf", settings)
    assert exc.value.code == "too_many_pages"


def test_malformed_pdf(settings):
    with pytest.raises(DocumentParseError) as exc:
        validate_upload(b"%PDF-1.7\n garbage garbage", "r.pdf", settings)
    assert exc.value.code == "malformed_pdf"
