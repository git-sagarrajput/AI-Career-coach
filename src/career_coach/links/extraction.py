from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from career_coach.links.url_policy import check_syntax
from career_coach.models.documents import EvidenceSpan, ExtractedLink, LinkSource

TEXT_URL_RE = re.compile(
    r"(?i)\b(?:https?://|www\.)[^\s<>\"'()]+|\b(?:linkedin\.com|github\.com|gitlab\.com)/[^\s<>\"'()]+"
)
_TRAILING = ".,;:!?)]}"
_KNOWN_HOSTS = ("linkedin.com", "github.com", "gitlab.com")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def extract_text_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in TEXT_URL_RE.finditer(text):
        start = match.start()
        if start > 0 and text[start - 1] in "@.":
            continue
        candidate = match.group(0).rstrip(_TRAILING)
        if "@" in candidate.split("/", 1)[0]:
            continue
        if candidate:
            urls.append(candidate)
    return urls


def normalize_url(raw: str) -> tuple[str | None, bool]:
    """Return (normalized_url, scheme_was_added). Path and query are not modified."""
    value = raw.strip()
    added = False
    low = value.lower()
    if low.startswith("www.") or low.startswith(_KNOWN_HOSTS):
        value = "https://" + value
        added = True
    try:
        parts = urlsplit(value)
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https"):
            return (value if scheme else None), added
        host = (parts.hostname or "").lower()
        if not host:
            return None, added
        if ":" in host:
            host = f"[{host}]"
        else:
            try:
                host = host.encode("idna").decode("ascii")
            except UnicodeError:
                return None, added
        port = parts.port
    except ValueError:
        return None, added
    userinfo = ""
    if parts.username is not None:
        userinfo = parts.username + (f":{parts.password}" if parts.password is not None else "") + "@"
    netloc = userinfo + host
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        netloc += f":{port}"
    return urlunsplit((scheme, netloc, parts.path, parts.query, "")), added


def extract_links(spans: list[EvidenceSpan], structural: list[ExtractedLink]) -> list[ExtractedLink]:
    """Merge structural and text links, dedupe by normalized URL (first occurrence order), validate syntax."""
    order: list[str] = []
    merged: dict[str, dict] = {}

    def add(raw: str, source: LinkSource) -> None:
        normalized, added = normalize_url(raw)
        key = normalized or raw.strip()
        if key not in merged:
            order.append(key)
            merged[key] = {
                "url_raw": raw.strip()[:2048],
                "url_normalized": normalized,
                "normalized": added,
                "sources": [],
            }
        if source not in merged[key]["sources"]:
            merged[key]["sources"].append(source)

    for link in structural:
        for source in link.sources:
            add(link.url_raw, source)
    for span in spans:
        for url in extract_text_urls(span.text):
            add(url, LinkSource(kind="text", span_id=span.id, page=span.page))

    links: list[ExtractedLink] = []
    for key in order:
        item = merged[key]
        status, reason = check_syntax(item["url_normalized"] or item["url_raw"])
        links.append(ExtractedLink(**item, syntax_status=status, reason=reason))
    return links
