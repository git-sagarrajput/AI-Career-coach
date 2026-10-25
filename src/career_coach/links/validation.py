"""Opt-in, bounded, SSRF-aware link reachability checks (never reads response bodies)."""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, wait
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import urljoin, urlsplit

import httpx

from career_coach.config import Settings
from career_coach.links.url_policy import Resolver, check_destination, check_syntax
from career_coach.models.documents import ExtractedLink, LinkCheckResult
from career_coach.models.enums import LinkSyntaxStatus, ReachabilityStatus

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 3
PER_HOST_CONCURRENCY = 2
USER_AGENT = "AI-Career-Coach-LinkCheck/0.1 (local POC)"


class LinkCheckJob:
    def __init__(
        self,
        links: list[ExtractedLink],
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._links = links
        self._settings = settings
        self._resolver = resolver
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._active = 0
        self._closing = False
        self._closed = False
        self._started = time.monotonic()
        self._host_sems: dict[str, threading.BoundedSemaphore] = defaultdict(
            lambda: threading.BoundedSemaphore(PER_HOST_CONCURRENCY)
        )

        self._preset: dict[int, LinkCheckResult] = {}
        to_check: list[tuple[int, str]] = []
        for index, link in enumerate(links):
            url = link.url_normalized or link.url_raw
            if link.syntax_status != LinkSyntaxStatus.VALID:
                status = (
                    ReachabilityStatus.BLOCKED_BY_POLICY
                    if link.syntax_status == LinkSyntaxStatus.BLOCKED_BY_POLICY
                    else ReachabilityStatus.NOT_CHECKED
                )
                self._preset[index] = LinkCheckResult(url=url, status=status, detail=link.reason)
            elif len(to_check) >= settings.link_max_urls:
                self._preset[index] = LinkCheckResult(
                    url=url, status=ReachabilityStatus.SKIPPED_LIMIT, detail="URL check limit reached"
                )
            else:
                to_check.append((index, url))

        self._client = httpx.Client(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=2.0),
            limits=httpx.Limits(
                max_connections=settings.link_workers, max_keepalive_connections=settings.link_workers
            ),
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        )
        self._executor = ThreadPoolExecutor(max_workers=settings.link_workers, thread_name_prefix="linkcheck")
        self._futures: dict[int, tuple[str, Future[LinkCheckResult]]] = {}
        for index, url in to_check:
            ctx = contextvars.copy_context()
            self._futures[index] = (url, self._executor.submit(ctx.run, self._check_one, url))
        if not self._futures:
            self.cancel()

    # ------------------------------------------------------------ public

    def collect(self, timeout_s: float) -> list[LinkCheckResult]:
        futures = [f for _, f in self._futures.values()]
        if futures:
            wait(futures, timeout=max(0.0, timeout_s))
        self.cancel()
        results: list[LinkCheckResult] = []
        for index in range(len(self._links)):
            if index in self._preset:
                results.append(self._preset[index])
                continue
            url, future = self._futures[index]
            if not future.done() or future.cancelled():
                results.append(
                    LinkCheckResult(
                        url=url,
                        status=ReachabilityStatus.SKIPPED_DEADLINE,
                        detail="link-check deadline reached",
                    )
                )
                continue
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                logger.warning("link check crashed type=%s", type(exc).__name__)
                results.append(
                    LinkCheckResult(url=url, status=ReachabilityStatus.ERROR, detail="internal error")
                )
        reachable = sum(1 for r in results if r.status == ReachabilityStatus.REACHABLE)
        logger.info("link checks collected total=%d reachable=%d", len(results), reachable)
        return results

    def cancel(self) -> None:
        self._cancel.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._closing = True
            if self._active == 0:
                self._close_client()

    # ------------------------------------------------------------ internals

    def _close_client(self) -> None:
        if not self._closed:
            self._closed = True
            self._client.close()

    def _check_one(self, url: str) -> LinkCheckResult:
        with self._lock:
            if self._closed:
                return LinkCheckResult(url=url, status=ReachabilityStatus.SKIPPED_DEADLINE)
            self._active += 1
        try:
            return self._run_checks(url)
        finally:
            with self._lock:
                self._active -= 1
                if self._closing and self._active == 0:
                    self._close_client()

    def _run_checks(self, url: str) -> LinkCheckResult:
        start = time.monotonic()
        budget = float(self._settings.link_timeout_seconds)
        current = url
        redirects = 0
        method: str | None = None
        retried = False

        def result(status: ReachabilityStatus, **kw) -> LinkCheckResult:
            return LinkCheckResult(
                url=url,
                status=status,
                redirects=redirects,
                method=method,
                elapsed_ms=int((time.monotonic() - start) * 1000),
                **kw,
            )

        while True:
            if self._cancel.is_set():
                return result(ReachabilityStatus.SKIPPED_DEADLINE, detail="cancelled")
            if time.monotonic() - start > budget:
                return result(ReachabilityStatus.TIMEOUT, detail="per-URL time budget exceeded")
            syntax, reason = check_syntax(current)
            if syntax != LinkSyntaxStatus.VALID:
                return result(ReachabilityStatus.BLOCKED_BY_POLICY, final_url=current, detail=reason)
            parts = urlsplit(current)
            port = parts.port or (443 if parts.scheme == "https" else 80)
            denied, reason = check_destination(parts.hostname or "", port, self._resolver)
            if denied is not None:
                return result(denied, final_url=current, detail=reason)

            try:
                with self._host_sems[parts.hostname or ""]:
                    method = "HEAD"
                    response = self._send("HEAD", current)
                    if response.status_code in (405, 501):
                        method = "GET"
                        response = self._send("GET", current, {"Range": "bytes=0-0"})
            except httpx.TimeoutException:
                if redirects == 0 and not retried and time.monotonic() - start < budget / 2:
                    retried = True
                    continue
                return result(ReachabilityStatus.TIMEOUT, final_url=current)
            except httpx.TransportError:
                if redirects == 0 and not retried and time.monotonic() - start < budget / 2:
                    retried = True
                    continue
                return result(ReachabilityStatus.CONNECTION_ERROR, final_url=current)

            status = response.status_code
            if 300 <= status < 400 and response.headers.get("location"):
                if redirects >= MAX_REDIRECTS:
                    return result(ReachabilityStatus.REDIRECT_LIMIT, http_status=status, final_url=current)
                redirects += 1
                current = urljoin(current, response.headers["location"])
                continue
            if 200 <= status < 300:
                return result(ReachabilityStatus.REACHABLE, http_status=status, final_url=current)
            return result(
                ReachabilityStatus.RESPONDED_NON_SUCCESS,
                http_status=status,
                final_url=current,
                detail="The server responded but refused or failed the automated check. "
                "The link may still work in a browser.",
            )

    def _send(self, method: str, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        request = self._client.build_request(method, url, headers=headers)
        response = self._client.send(request, stream=True)
        response.close()  # body is never read
        return response


def start_link_checks(
    links: list[ExtractedLink],
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
    resolver: Resolver | None = None,
) -> LinkCheckJob:
    return LinkCheckJob(links, settings, transport=transport, resolver=resolver)
