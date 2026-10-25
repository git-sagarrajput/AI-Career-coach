from __future__ import annotations

from career_coach.links.extraction import extract_links, extract_text_urls, normalize_url
from career_coach.models.documents import EvidenceSpan, ExtractedLink, LinkSource
from career_coach.models.enums import BlockKind, DocumentRole, LinkSyntaxStatus, SectionKind


def _span(text: str, sid: str = "R001") -> EvidenceSpan:
    return EvidenceSpan(
        id=sid,
        role=DocumentRole.RESUME,
        section_kind=SectionKind.CONTACT,
        text=text,
        raw_text=text,
        block_kind=BlockKind.PARAGRAPH,
        page=1,
    )


def test_t24_text_urls():
    text = "See https://x.com/a). Also www.github.com/u and linkedin.com/in/u; mail me at me@github.com/x."
    assert extract_text_urls(text) == ["https://x.com/a", "www.github.com/u", "linkedin.com/in/u"]
    assert normalize_url("www.github.com/u") == ("https://www.github.com/u", True)
    assert normalize_url("linkedin.com/in/u") == ("https://linkedin.com/in/u", True)
    assert normalize_url("HTTPS://GitHub.COM:443/Path?q=A#frag") == ("https://github.com/Path?q=A", False)


def test_t25_dedupe_merges_sources():
    structural = [
        ExtractedLink(
            url_raw="https://github.com/demo",
            sources=[LinkSource(kind="pdf_annotation", page=1, anchor_text="GitHub")],
        )
    ]
    links = extract_links([_span("Code: https://github.com/demo")], structural)
    assert len(links) == 1
    assert {s.kind for s in links[0].sources} == {"pdf_annotation", "text"}
    assert links[0].syntax_status == LinkSyntaxStatus.VALID


def test_mailto_reported_not_fetched():
    structural = [
        ExtractedLink(url_raw="mailto:a@example.com", sources=[LinkSource(kind="docx_relationship")])
    ]
    links = extract_links([], structural)
    assert links[0].syntax_status == LinkSyntaxStatus.NOT_WEB_LINK
