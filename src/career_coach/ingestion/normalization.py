from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u00ad"), None)
_CHAR_MAP = str.maketrans(
    {
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb00": "ff",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\u00a0": " ",
        "\u2007": " ",
        "\u202f": " ",
        "\u2002": " ",
        "\u2003": " ",
        "\u2009": " ",
        "\u200a": " ",
        "\u3000": " ",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
    }
)
_BULLET_GLYPHS = "•▪◦●■□➢►✓·\u2023\u2043"
_BULLET_RE = re.compile(rf"^[ \t]*(?:[{_BULLET_GLYPHS}][ \t]*|[*\-][ \t]+)", re.MULTILINE)
_DASH_SPACED = re.compile(r"(?<= )[\u2010\u2011\u2012\u2013\u2014](?= )")
_DASH_LINE_START = re.compile(r"^([ \t]*)[\u2010\u2011\u2012\u2013\u2014](?=[ \t])", re.MULTILINE)
_HYPHEN_BREAK = re.compile(r"(\w)-\n(?=[a-z])")
_PAGE_NUMBER = re.compile(r"^(?:page \d+(?: of \d+)?|\d+)$", re.IGNORECASE)
DEDUPE_MAX_LEN = 120


def is_bullet_line(raw: str) -> bool:
    stripped = raw.lstrip(" \t")
    if not stripped:
        return False
    if stripped[0] in _BULLET_GLYPHS:
        return True
    return len(stripped) > 1 and stripped[0] in "*-\u2013\u2014" and stripped[1] in " \t"


def normalize_text(raw: str) -> str:
    """Pure, idempotent normalization that preserves technical tokens (C++, C#, .NET, 3.11)."""
    s = unicodedata.normalize("NFC", raw)
    s = s.translate(_ZERO_WIDTH).translate(_CHAR_MAP)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _DASH_LINE_START.sub(r"\1-", s)
    s = _DASH_SPACED.sub("-", s)
    s = _BULLET_RE.sub("- ", s)
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = _HYPHEN_BREAK.sub(r"\1", s)
    return s.strip()


def normalize_block_text(raw: str) -> str:
    """Normalize and flatten a block to a single line; leading bullet marker removed."""
    s = normalize_text(raw)
    s = re.sub(r"^- ", "", s)
    s = re.sub(r"\s*\n\s*", " ", s)
    return s.strip()


def is_page_number_line(text: str) -> bool:
    return bool(_PAGE_NUMBER.match(text.strip()))


def dedupe_key(text: str) -> str | None:
    """Key used for exact-duplicate removal within a section; None when the block is too long."""
    if len(text) > DEDUPE_MAX_LEN:
        return None
    return re.sub(r"\s+", " ", text.casefold()).strip()
