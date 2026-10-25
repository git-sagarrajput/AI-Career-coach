from __future__ import annotations

import threading
import time

import httpx

from career_coach.config import load_settings
from career_coach.links.validation import start_link_checks
from career_coach.models.documents import ExtractedLink, LinkSource
from career_coach.models.enums import LinkSyntaxStatus, ReachabilityStatus

PUBLIC = lambda host, port: ["93.184.215.14"]  # noqa: E731


def _link(url: str, status: LinkSyntaxStatus = LinkSyntaxStatus.VALID) -> ExtractedLink:
    return ExtractedLink(
        url_raw=url, url_normalized=url, sources=[LinkSource(kind="text")], syntax_status=status
    )


def _run(links, handler, settings=None, resolver=PUBLIC, timeout=5.0):
    s = settings or load_settings({})
    job = start_link_checks(links, s, transport=httpx.MockTransport(handler), resolver=resolver)
    return job.collect(timeout)


def test_t29_head_ok():
    results = _run([_link("https://example.com/a")], lambda r: httpx.Response(200))
    assert results[0].status == ReachabilityStatus.REACHABLE
    assert results[0].method == "HEAD"
    assert results[0].content_inspected is False and results[0].ownership_verified is False


class _NoReadStream(httpx.SyncByteStream):
    def __init__(self) -> None:
        self.read = False

    def __iter__(self):
        self.read = True
        yield b"body"


def test_t30_head_405_then_get_without_reading_body():
    streams: list[_NoReadStream] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(405)
        assert request.headers["range"] == "bytes=0-0"
        stream = _NoReadStream()
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    results = _run([_link("https://example.com/a")], handler)
    assert results[0].status == ReachabilityStatus.REACHABLE
    assert results[0].method == "GET"
    assert streams and not streams[0].read


def test_t31_redirect_to_private_ip_blocked():
    def resolver(host, port):
        return ["10.0.0.7"] if host == "internal.example.org" else ["93.184.215.14"]

    def handler(request):
        return httpx.Response(301, headers={"Location": "https://internal.example.org/admin"})

    results = _run([_link("https://example.com/a")], handler, resolver=resolver)
    assert results[0].status == ReachabilityStatus.BLOCKED_BY_POLICY
    assert results[0].redirects == 1


def test_t31_redirect_to_ip_literal_blocked():
    results = _run(
        [_link("https://example.com/a")],
        lambda r: httpx.Response(302, headers={"Location": "http://169.254.169.254/"}),
    )
    assert results[0].status == ReachabilityStatus.BLOCKED_BY_POLICY


def test_t32_redirect_limit():
    counter = {"n": 0}

    def handler(request):
        counter["n"] += 1
        return httpx.Response(302, headers={"Location": f"/hop{counter['n']}"})

    results = _run([_link("https://example.com/a")], handler)
    assert results[0].status == ReachabilityStatus.REDIRECT_LIMIT


def test_t33_timeout_with_single_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectTimeout("slow", request=request)

    results = _run([_link("https://example.com/a")], handler)
    assert results[0].status == ReachabilityStatus.TIMEOUT
    assert calls["n"] == 2


def test_t34_non_success():
    results = _run(
        [_link("https://example.com/404"), _link("https://linkedin.com/in/x")],
        lambda r: httpx.Response(404 if "404" in str(r.url) else 999),
    )
    assert [r.status for r in results] == [ReachabilityStatus.RESPONDED_NON_SUCCESS] * 2
    assert [r.http_status for r in results] == [404, 999]


def test_t35_cap_and_order():
    links = [_link(f"https://site{i}.example.com/") for i in range(20)]
    results = _run(links, lambda r: httpx.Response(200))
    assert [r.url for r in results] == [link.url_normalized for link in links]
    assert sum(r.status == ReachabilityStatus.REACHABLE for r in results) == 15
    assert [r.status for r in results[15:]] == [ReachabilityStatus.SKIPPED_LIMIT] * 5


def test_non_valid_links_not_checked():
    links = [
        _link("mailto:a@example.com", LinkSyntaxStatus.NOT_WEB_LINK),
        _link("http://127.0.0.1", LinkSyntaxStatus.BLOCKED_BY_POLICY),
    ]
    results = _run(links, lambda r: httpx.Response(200))
    assert [r.status for r in results] == [
        ReachabilityStatus.NOT_CHECKED,
        ReachabilityStatus.BLOCKED_BY_POLICY,
    ]


def test_t36_deadline():
    release = threading.Event()

    def handler(request):
        release.wait(3)
        return httpx.Response(200)

    s = load_settings({})
    job = start_link_checks(
        [_link("https://slow.example.com/")], s, transport=httpx.MockTransport(handler), resolver=PUBLIC
    )
    start = time.monotonic()
    results = job.collect(0.3)
    elapsed = time.monotonic() - start
    release.set()
    assert results[0].status == ReachabilityStatus.SKIPPED_DEADLINE
    assert elapsed < 0.8


def test_t37_cookies_never_sent():
    seen: list[str | None] = []

    def handler(request):
        seen.append(request.headers.get("cookie"))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/next", "Set-Cookie": "session=abc; Path=/"})
        return httpx.Response(200)

    results = _run([_link("https://example.com/start")], handler)
    assert results[0].status == ReachabilityStatus.REACHABLE
    assert seen == [None, None]
