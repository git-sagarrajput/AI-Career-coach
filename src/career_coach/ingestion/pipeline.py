from __future__ import annotations

import hashlib
import logging
import time

from career_coach.config import Settings
from career_coach.errors import CareerCoachError, DocumentParseError
from career_coach.ingestion.docx_parser import parse_docx
from career_coach.ingestion.normalization import is_bullet_line, is_page_number_line
from career_coach.ingestion.pdf_parser import parse_pdf
from career_coach.ingestion.sections import build_sections
from career_coach.ingestion.validation import (
    DetectedFormat,
    extension_mismatch_warning,
    validate_jd_text,
    validate_upload,
)
from career_coach.links.extraction import extract_links
from career_coach.models.documents import DocumentStats, ParsedDocument, RawBlock, RawExtraction
from career_coach.models.enums import DocumentRole, SourceFormat
from career_coach.utils.timing import elapsed_ms

logger = logging.getLogger(__name__)


def _assemble(
    raw: RawExtraction, role: DocumentRole, fmt: SourceFormat, sha256: str, start: float, extra_warnings: list
) -> ParsedDocument:
    blocks = [b for b in raw.blocks if not is_page_number_line(b.text)]
    built = build_sections(blocks, role)
    if not built.spans:
        raise DocumentParseError(
            "No extractable text was found in this document.", code="no_extractable_text"
        )
    links = extract_links(built.spans, raw.links)
    stats = DocumentStats(
        page_count=raw.page_count,
        char_count=sum(len(s.text) for s in built.spans),
        span_count=len(built.spans),
        table_count=raw.table_count,
        link_count=len(links),
        duplicates_removed=built.duplicates_removed,
        image_only_pages=raw.image_only_pages,
        ocr_pages=raw.ocr_pages,
        extraction_ms=elapsed_ms(start),
    )
    return ParsedDocument(
        role=role,
        source_format=fmt,
        sha256=sha256,
        sections=built.sections,
        spans=built.spans,
        links=links,
        stats=stats,
        warnings=[*extra_warnings, *raw.warnings, *built.warnings],
    )


def ingest_document(data: bytes, filename: str, role: DocumentRole, settings: Settings) -> ParsedDocument:
    start = time.perf_counter()
    fmt = validate_upload(data, filename, settings)
    extra = [w for w in [extension_mismatch_warning(filename, fmt)] if w is not None]
    try:
        raw = parse_pdf(data, settings) if fmt == DetectedFormat.PDF else parse_docx(data)
    except CareerCoachError:
        raise
    except Exception as exc:  # noqa: BLE001
        # Exception messages may contain document text, so only the type is logged.
        logger.warning("parser failure format=%s type=%s", fmt.value, type(exc).__name__)
        raise DocumentParseError(code="parser_failure") from None
    doc = _assemble(raw, role, SourceFormat(fmt.value), hashlib.sha256(data).hexdigest(), start, extra)
    logger.info(
        "%s parsed format=%s spans=%d sections=%d tables=%d links=%d warnings=%d ms=%d",
        role.value,
        fmt.value,
        doc.stats.span_count,
        len(doc.sections),
        doc.stats.table_count,
        doc.stats.link_count,
        len(doc.warnings),
        doc.stats.extraction_ms,
    )
    return doc


def ingest_text(text: str, role: DocumentRole, settings: Settings) -> ParsedDocument:
    start = time.perf_counter()
    cleaned = validate_jd_text(text, settings)
    blocks: list[RawBlock] = []
    for index, line in enumerate(cleaned.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        if not line.strip():
            continue
        kind = "list_item" if is_bullet_line(line) else "paragraph"
        blocks.append(RawBlock(text=line, kind=kind, paragraph_index=index))
    raw = RawExtraction(blocks=blocks)
    sha = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
    return _assemble(raw, role, SourceFormat.TEXT, sha, start, [])
