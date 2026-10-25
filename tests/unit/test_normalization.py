from __future__ import annotations

import pytest

from career_coach.ingestion.normalization import is_bullet_line, normalize_block_text, normalize_text
from career_coach.ingestion.sections import build_sections
from career_coach.models.documents import RawBlock
from career_coach.models.enums import DocumentRole

TOKENS = ["C++", "C#", "F#", ".NET", "ASP.NET", "Node.js", "CI/CD", "TCP/IP", "3.11", "R&D", "A/B"]


@pytest.mark.parametrize("token", TOKENS)
def test_t18_technical_tokens_preserved(token):
    text = f"Worked with {token} daily."
    assert token in normalize_text(text)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("e\ufb03cient \ufb01le", "efficient file"),
        ("a\u00a0b", "a b"),
        ("zero\u200bwidth\ufeff", "zerowidth"),
        ("\u201cquoted\u201d \u2018x\u2019", "\"quoted\" 'x'"),
        ("\u2022 bullet", "- bullet"),
        ("line1\r\nline2\rline3", "line1\nline2\nline3"),
        ("a\n\n\n\nb", "a\n\nb"),
        ("develop-\nment", "development"),
        ("2019 \u2013 2021", "2019 - 2021"),
        ("well-known", "well-known"),
    ],
)
def test_t19_normalization_cases(raw, expected):
    assert normalize_text(raw) == expected


@pytest.mark.parametrize("raw", ["\u2022 a\u00a0b\r\nc-\nd", "  x  \t y ", "\ufb01 \u2014 test", "- item"])
def test_t19_idempotent(raw):
    once = normalize_text(raw)
    assert normalize_text(once) == once


def test_bullet_detection_and_block_text():
    assert is_bullet_line("\u2022 Built X")
    assert is_bullet_line("- Built X")
    assert not is_bullet_line("-5 degrees")
    assert normalize_block_text("\u2022 Built\nX") == "Built X"


def test_t20_dedupe_within_section_only():
    long_bullet = (
        "Led the migration of a large legacy billing platform to a modern event driven architecture " * 2
    )
    blocks = [
        RawBlock(text="Experience", kind="heading"),
        RawBlock(text="Jane Doe - Resume", kind="paragraph", page=1),
        RawBlock(text=long_bullet, kind="list_item", page=1),
        RawBlock(text="Jane Doe - Resume", kind="paragraph", page=2),
        RawBlock(text="Projects", kind="heading"),
        RawBlock(text=long_bullet, kind="list_item", page=2),
    ]
    result = build_sections(blocks, DocumentRole.RESUME)
    assert result.duplicates_removed == 1
    assert sum(1 for s in result.spans if s.text.startswith("Led the migration")) == 2
