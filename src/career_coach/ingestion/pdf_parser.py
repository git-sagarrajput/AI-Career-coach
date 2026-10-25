from __future__ import annotations

import logging

import pymupdf

from career_coach.config import Settings
from career_coach.errors import DocumentParseError
from career_coach.ingestion import ocr
from career_coach.ingestion.normalization import is_bullet_line, is_page_number_line
from career_coach.ingestion.sections import looks_like_heading
from career_coach.models.documents import (
    ExtractedLink,
    LinkSource,
    ProcessingWarning,
    RawBlock,
    RawExtraction,
)
from career_coach.models.enums import Severity, SpanOrigin

logger = logging.getLogger(__name__)
_COMPONENT = "pdf_parser"


def _split_block_lines(text: str, page_no: int, origin: SpanOrigin) -> list[RawBlock]:
    """Split a PyMuPDF text block into paragraphs, bullet items and heading lines."""
    out: list[RawBlock] = []
    current: list[str] = []
    current_kind = "paragraph"

    def flush() -> None:
        nonlocal current, current_kind
        if current:
            out.append(RawBlock(text="\n".join(current), kind=current_kind, page=page_no, origin=origin))
        current, current_kind = [], "paragraph"

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if is_page_number_line(stripped):
            continue
        if is_bullet_line(stripped):
            flush()
            current, current_kind = [stripped], "list_item"
        elif looks_like_heading(stripped):
            flush()
            out.append(RawBlock(text=stripped, kind="paragraph", page=page_no, origin=origin))
        else:
            current.append(stripped)
    flush()
    return out


def _inside(rect: tuple[float, float, float, float], boxes: list[pymupdf.Rect]) -> bool:
    r = pymupdf.Rect(rect)
    return any(box.contains(r) or (r & box).get_area() > 0.6 * max(r.get_area(), 1e-6) for box in boxes)


def _anchor_text(page: pymupdf.Page, rect: pymupdf.Rect) -> str | None:
    try:
        text = page.get_textbox(rect).strip()
    except Exception:  # noqa: BLE001
        return None
    return " ".join(text.split())[:200] or None


def parse_pdf(data: bytes, settings: Settings) -> RawExtraction:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise DocumentParseError(code="malformed_pdf") from exc
    blocks: list[RawBlock] = []
    links: list[ExtractedLink] = []
    warnings: list[ProcessingWarning] = []
    image_only: list[int] = []
    ocr_pages: list[int] = []
    table_count = 0
    try:
        for index in range(doc.page_count):
            page = doc[index]
            page_no = index + 1

            table_boxes: list[pymupdf.Rect] = []
            table_blocks: list[tuple[float, RawBlock]] = []
            try:
                finder = page.find_tables()
                for table in finder.tables:
                    rows = table.extract()
                    row_index = 0
                    y_top = float(table.bbox[1])
                    for row in rows:
                        cells = [" ".join(str(c).split()) for c in row if c and str(c).strip()]
                        if cells:
                            table_blocks.append(
                                (
                                    y_top + row_index * 0.01,
                                    RawBlock(
                                        text=" | ".join(cells),
                                        kind="table_row",
                                        page=page_no,
                                        table_index=table_count,
                                        row_index=row_index,
                                    ),
                                )
                            )
                            row_index += 1
                    if row_index:
                        table_boxes.append(pymupdf.Rect(table.bbox))
                        table_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.info("table extraction failed page=%s type=%s", page_no, type(exc).__name__)
                warnings.append(
                    ProcessingWarning(
                        code="pdf_table_extraction_failed",
                        severity=Severity.INFO,
                        component=_COMPONENT,
                        message=f"Table detection failed on page {page_no}; plain text was used instead.",
                        page=page_no,
                    )
                )
                table_boxes, table_blocks = [], []

            raw_blocks = page.get_text("blocks", sort=True)
            page_text = "".join(b[4] for b in raw_blocks if b[6] == 0).strip()
            if len(page_text) < settings.image_only_char_threshold and page.get_images():
                image_only.append(page_no)
                if settings.ocr_enabled and ocr.ocr_available() and len(ocr_pages) < settings.ocr_max_pages:
                    try:
                        for text in ocr.ocr_page_blocks(page):
                            blocks.extend(_split_block_lines(text, page_no, SpanOrigin.OCR))
                        ocr_pages.append(page_no)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("ocr failed page=%s type=%s", page_no, type(exc).__name__)
                        warnings.append(
                            ProcessingWarning(
                                code="ocr_failed",
                                component="ocr",
                                message=f"OCR failed on page {page_no}; its text was not analysed.",
                                page=page_no,
                            )
                        )
                else:
                    warnings.append(
                        ProcessingWarning(
                            code="image_only_page",
                            component=_COMPONENT,
                            message=(
                                f"Page {page_no} appears to be an image. Text on it was not analysed. "
                                "Enable OCR (see README) or upload a text-based PDF/DOCX."
                            ),
                            page=page_no,
                        )
                    )

            page_blocks: list[tuple[float, RawBlock]] = []
            for b in raw_blocks:
                if b[6] != 0 or not b[4].strip():
                    continue
                if table_boxes and _inside(b[:4], table_boxes):
                    continue
                for offset, block in enumerate(_split_block_lines(b[4], page_no, SpanOrigin.NATIVE)):
                    page_blocks.append((float(b[1]) + offset * 0.001, block))
            # Merge text and table rows into top-to-bottom reading order (stable sort keeps block order).
            merged = sorted([*page_blocks, *table_blocks], key=lambda item: item[0])
            blocks.extend(block for _, block in merged)

            for link in page.get_links():
                if link.get("kind") != pymupdf.LINK_URI or not link.get("uri"):
                    continue
                anchor = _anchor_text(page, link["from"]) if link.get("from") is not None else None
                links.append(
                    ExtractedLink(
                        url_raw=str(link["uri"])[:2048],
                        sources=[LinkSource(kind="pdf_annotation", page=page_no, anchor_text=anchor)],
                    )
                )
        page_count = doc.page_count
    finally:
        doc.close()

    if not blocks:
        raise DocumentParseError(
            "No extractable text was found. The file may be a scanned image. "
            "Upload a text-based PDF/DOCX, or enable OCR (see README).",
            code="no_extractable_text",
        )
    return RawExtraction(
        blocks=blocks,
        links=links,
        page_count=page_count,
        table_count=table_count,
        image_only_pages=image_only,
        ocr_pages=ocr_pages,
        warnings=warnings,
    )
