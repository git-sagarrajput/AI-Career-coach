from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from career_coach.models.enums import (
    BlockKind,
    DocumentRole,
    LinkSyntaxStatus,
    ReachabilityStatus,
    SectionDetection,
    SectionKind,
    Severity,
    SourceFormat,
    SpanOrigin,
)

_DOMAIN = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ProcessingWarning(BaseModel):
    model_config = _DOMAIN

    code: str = Field(pattern=r"^[a-z_]+$")
    severity: Severity = Severity.WARNING
    component: str
    message: str = Field(max_length=300)
    span_ids: list[str] = []
    page: int | None = None


class LinkSource(BaseModel):
    model_config = _DOMAIN

    kind: Literal["docx_relationship", "pdf_annotation", "text"]
    span_id: str | None = None
    page: int | None = None
    anchor_text: str | None = Field(default=None, max_length=200)


class ExtractedLink(BaseModel):
    model_config = _DOMAIN

    url_raw: str = Field(max_length=2048)
    url_normalized: str | None = None
    normalized: bool = False
    sources: list[LinkSource] = Field(min_length=1)
    syntax_status: LinkSyntaxStatus = LinkSyntaxStatus.VALID
    reason: str | None = None


class LinkCheckResult(BaseModel):
    model_config = _DOMAIN

    url: str
    status: ReachabilityStatus
    http_status: int | None = None
    final_url: str | None = None
    redirects: int = Field(default=0, ge=0)
    method: Literal["HEAD", "GET"] | None = None
    elapsed_ms: int = Field(default=0, ge=0)
    content_inspected: Literal[False] = False
    ownership_verified: Literal[False] = False
    detail: str | None = None


class RawBlock(BaseModel):
    """Intermediate parser output (before normalization and span creation)."""

    model_config = ConfigDict(extra="forbid")

    text: str
    kind: Literal["paragraph", "heading", "list_item", "table_row"]
    page: int | None = None
    paragraph_index: int | None = None
    table_index: int | None = None
    row_index: int | None = None
    style_name: str | None = None
    origin: SpanOrigin = SpanOrigin.NATIVE
    contact_hint: bool = False


class RawExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[RawBlock]
    links: list[ExtractedLink] = []
    page_count: int | None = None
    table_count: int = 0
    image_only_pages: list[int] = []
    ocr_pages: list[int] = []
    warnings: list[ProcessingWarning] = []


class EvidenceSpan(BaseModel):
    model_config = _DOMAIN

    id: str = Field(pattern=r"^[RJ]\d{3,}$")
    role: DocumentRole
    section_kind: SectionKind
    section_title: str | None = Field(default=None, max_length=80)
    text: str = Field(min_length=1, max_length=600)
    raw_text: str = Field(repr=False)
    block_kind: BlockKind
    origin: SpanOrigin = SpanOrigin.NATIVE
    page: int | None = Field(default=None, ge=1)
    paragraph_index: int | None = Field(default=None, ge=0)
    table_index: int | None = None
    row_index: int | None = None


class Section(BaseModel):
    model_config = _DOMAIN

    kind: SectionKind
    title: str | None = None
    detection: SectionDetection
    span_ids: list[str]
    order: int = Field(ge=0)


class DocumentStats(BaseModel):
    model_config = _DOMAIN

    page_count: int | None = None
    char_count: int = 0
    span_count: int = 0
    table_count: int = 0
    link_count: int = 0
    duplicates_removed: int = 0
    image_only_pages: list[int] = []
    ocr_pages: list[int] = []
    extraction_ms: int = 0


class ParsedDocument(BaseModel):
    model_config = _DOMAIN

    role: DocumentRole
    source_format: SourceFormat
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sections: list[Section]
    spans: list[EvidenceSpan]
    links: list[ExtractedLink] = []
    stats: DocumentStats
    warnings: list[ProcessingWarning] = []

    _index: dict[str, EvidenceSpan] | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _check_integrity(self) -> ParsedDocument:
        prefix = "R" if self.role == DocumentRole.RESUME else "J"
        ids = [s.id for s in self.spans]
        if len(ids) != len(set(ids)):
            raise ValueError("span IDs must be unique")
        if any(not i.startswith(prefix) for i in ids):
            raise ValueError(f"span IDs must start with {prefix} for role {self.role}")
        by_id = {s.id: s for s in self.spans}
        seen: set[str] = set()
        for section in self.sections:
            for sid in section.span_ids:
                if sid not in by_id:
                    raise ValueError(f"section references unknown span {sid}")
                if sid in seen:
                    raise ValueError(f"span {sid} belongs to more than one section")
                if by_id[sid].section_kind != section.kind:
                    raise ValueError(f"span {sid} section_kind mismatch")
                seen.add(sid)
        if seen != set(ids):
            raise ValueError("every span must belong to exactly one section")
        return self

    def span_by_id(self, span_id: str) -> EvidenceSpan | None:
        if self._index is None:
            self._index = {s.id: s for s in self.spans}
        return self._index.get(span_id)
