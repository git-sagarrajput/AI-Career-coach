from __future__ import annotations

import pytest

from career_coach.links.url_policy import check_destination, check_syntax
from career_coach.models.enums import LinkSyntaxStatus, ReachabilityStatus


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "ftp://example.com/x"])
def test_t26_unsupported_schemes(url):
    assert check_syntax(url)[0] == LinkSyntaxStatus.UNSUPPORTED_SCHEME


def test_t26_mailto():
    assert check_syntax("mailto:me@example.com")[0] == LinkSyntaxStatus.NOT_WEB_LINK


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://169.254.169.254/latest",
        "http://[::1]",
        "http://10.0.0.5",
        "http://2130706433",
        "http://0x7f000001",
        "http://0177.0.0.1",
        "http://localhost",
        "http://a.internal",
        "http://u:p@x.com",
        "http://x.com:8080",
        "http://metadata.google.internal",
        "http://[::ffff:127.0.0.1]",
    ],
)
def test_t27_blocked(url):
    assert check_syntax(url)[0] == LinkSyntaxStatus.BLOCKED_BY_POLICY


@pytest.mark.parametrize("url", ["https://github.com/demo", "http://cafe.de/x", "https://example.com:443/a"])
def test_valid(url):
    assert check_syntax(url) == (LinkSyntaxStatus.VALID, None)


def test_invalid_syntax():
    assert check_syntax("https://exa mple.com")[0] == LinkSyntaxStatus.INVALID_SYNTAX
    assert check_syntax("https://")[0] == LinkSyntaxStatus.INVALID_SYNTAX


def test_t28_any_private_address_blocks():
    status, _ = check_destination("example.com", 443, lambda h, p: ["93.184.215.14", "10.0.0.1"])
    assert status == ReachabilityStatus.BLOCKED_BY_POLICY
    assert check_destination("example.com", 443, lambda h, p: ["93.184.215.14"]) == (None, None)


def test_dns_failure():
    def fail(host, port):
        raise OSError("nope")

    assert check_destination("nx.example", 443, fail)[0] == ReachabilityStatus.DNS_FAILURE
