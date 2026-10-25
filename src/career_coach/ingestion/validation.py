from __future__ import annotations

import io
import zipfile
from enum import StrEnum
from pathlib import PurePath

import pymupdf

from career_coach.config import Settings
from career_coach.errors import DocumentParseError, InputValidationError, UnsupportedDocumentError
from career_coach.models.documents import ProcessingWarning
from career_coach.models.enums import Severity

_OLE_HEADER = bytes.fromhex("D0CF11E0A1B11AE1")
_MAX_ZIP_ENTRIES = 1000
_MAX_ZIP_UNCOMPRESSED = 50 * 1024 * 1024
_MAX_ZIP_RATIO = 100


class DetectedFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"


def _check_docx_archive(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            names = {i.filename for i in infos}
            if len(infos) > _MAX_ZIP_ENTRIES:
                raise UnsupportedDocumentError(
                    "This archive has too many entries.", code="suspicious_archive"
                )
            if sum(i.file_size for i in infos) > _MAX_ZIP_UNCOMPRESSED:
                raise UnsupportedDocumentError(
                    "This archive expands to an unsafe size.", code="suspicious_archive"
                )
            for info in infos:
                if info.compress_size > 0 and info.file_size / info.compress_size > _MAX_ZIP_RATIO:
                    raise UnsupportedDocumentError(
                        "This archive has a suspicious compression ratio.", code="suspicious_archive"
                    )
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise UnsupportedDocumentError(
                    "This file is not a valid Word .docx document.", code="malformed_docx"
                )
    except zipfile.BadZipFile as exc:
        raise UnsupportedDocumentError(
            "This file is not a valid Word .docx document.", code="malformed_docx"
        ) from exc


def _check_pdf(data: bytes, settings: Settings) -> None:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise DocumentParseError(
            "This PDF appears to be damaged and could not be opened.", code="malformed_pdf"
        ) from exc
    try:
        if doc.needs_pass:
            raise InputValidationError(
                "This PDF is password-protected. Remove the password and re-upload.", code="encrypted_pdf"
            )
        if doc.page_count > settings.max_pages:
            raise InputValidationError(
                f"This PDF has {doc.page_count} pages; the limit is {settings.max_pages}.",
                code="too_many_pages",
            )
        if doc.page_count == 0:
            raise InputValidationError("The PDF has no pages.", code="empty_file")
    finally:
        doc.close()


def validate_upload(data: bytes, filename: str, settings: Settings) -> DetectedFormat:
    if len(data) == 0:
        raise InputValidationError("The uploaded file is empty.", code="empty_file")
    if len(data) > settings.max_upload_bytes:
        size_mb = len(data) / (1024 * 1024)
        raise InputValidationError(
            f"File is {size_mb:.1f} MB; the limit is {settings.max_upload_mb} MB.", code="file_too_large"
        )
    if data.startswith(_OLE_HEADER):
        raise UnsupportedDocumentError(
            "This looks like a legacy .doc or a password-protected Word file. "
            "Save it as an unprotected .docx or PDF.",
            code="legacy_or_encrypted_office",
        )
    if b"%PDF-" in data[:1024]:
        _check_pdf(data, settings)
        return DetectedFormat.PDF
    if data.startswith(b"PK\x03\x04"):
        _check_docx_archive(data)
        return DetectedFormat.DOCX
    raise UnsupportedDocumentError(code="unsupported_format")


def extension_mismatch_warning(filename: str, fmt: DetectedFormat) -> ProcessingWarning | None:
    ext = PurePath(filename).suffix.lower().lstrip(".")
    if ext and ext != fmt.value:
        return ProcessingWarning(
            code="extension_mismatch",
            severity=Severity.INFO,
            component="validation",
            message=(
                f"File extension '.{ext}' does not match its content; it was read as {fmt.value.upper()}."
            ),
        )
    return None


def validate_jd_text(text: str, settings: Settings) -> str:
    stripped = (text or "").strip()
    if not stripped:
        raise InputValidationError("Please provide a job description.", code="empty_job_description")
    if len(stripped) > settings.max_jd_raw_chars:
        raise InputValidationError(
            f"The job description is {len(stripped):,} characters; "
            f"the limit is {settings.max_jd_raw_chars:,}.",
            code="job_description_too_long",
        )
    return stripped
