from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from career_coach.models.enums import LinkSyntaxStatus, ReachabilityStatus

Resolver = Callable[[str, int], list[str]]

_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet", ".corp")
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata"}
_NUMERIC_HOST = re.compile(r"^(?:0x[0-9a-f]+|[0-9]+)$", re.IGNORECASE)
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


def _ip_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _NUMERIC_HOST.match(host):
        try:
            return ipaddress.ip_address(int(host, 0))
        except ValueError:
            return None
    return None


def check_syntax(url: str) -> tuple[LinkSyntaxStatus, str | None]:
    """Pure syntax and destination-policy check (no network)."""
    if not url or len(url) > 2048 or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return LinkSyntaxStatus.INVALID_SYNTAX, "empty, too long, or contains whitespace/control characters"
    try:
        parts = urlsplit(url)
    except ValueError:
        return LinkSyntaxStatus.INVALID_SYNTAX, "could not be parsed"
    scheme = parts.scheme.lower()
    if scheme in ("mailto", "tel"):
        return LinkSyntaxStatus.NOT_WEB_LINK, f"{scheme} link (not fetched)"
    if scheme not in ("http", "https"):
        return LinkSyntaxStatus.UNSUPPORTED_SCHEME, f"scheme '{scheme or 'none'}' is not allowed"
    try:
        host = parts.hostname
        port = parts.port
    except ValueError:
        return LinkSyntaxStatus.INVALID_SYNTAX, "invalid host or port"
    if not host:
        return LinkSyntaxStatus.INVALID_SYNTAX, "missing host"
    if parts.username is not None or parts.password is not None:
        return LinkSyntaxStatus.BLOCKED_BY_POLICY, "credentials in URL"
    if port is not None and port not in (80, 443):
        return LinkSyntaxStatus.BLOCKED_BY_POLICY, f"non-standard port {port}"
    host = host.rstrip(".").lower()
    ip = _parse_ip_literal(host)
    if ip is not None:
        if not _ip_allowed(ip):
            return LinkSyntaxStatus.BLOCKED_BY_POLICY, "non-public IP address"
        return LinkSyntaxStatus.VALID, None
    if all(_NUMERIC_HOST.match(label) for label in host.split(".")):
        return LinkSyntaxStatus.BLOCKED_BY_POLICY, "ambiguous numeric host"
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return LinkSyntaxStatus.INVALID_SYNTAX, "host is not IDNA-encodable"
    if len(ascii_host) > 253 or not all(_LABEL.match(label) for label in ascii_host.split(".")):
        return LinkSyntaxStatus.INVALID_SYNTAX, "invalid host name"
    if ascii_host in _BLOCKED_HOSTS or ascii_host.endswith(_BLOCKED_SUFFIXES):
        return LinkSyntaxStatus.BLOCKED_BY_POLICY, "local or internal host name"
    if "." not in ascii_host:
        return LinkSyntaxStatus.BLOCKED_BY_POLICY, "single-label host name"
    return LinkSyntaxStatus.VALID, None


def system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def check_destination(
    host: str, port: int, resolver: Resolver | None = None
) -> tuple[ReachabilityStatus | None, str | None]:
    """Resolve host; every resolved address must be public. Returns (None, None) when allowed."""
    resolve = resolver or system_resolver
    try:
        addresses = resolve(host, port)
    except (OSError, UnicodeError):
        return ReachabilityStatus.DNS_FAILURE, "host name could not be resolved"
    if not addresses:
        return ReachabilityStatus.DNS_FAILURE, "host name could not be resolved"
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            return ReachabilityStatus.BLOCKED_BY_POLICY, "unparseable resolved address"
        if not _ip_allowed(ip):
            return ReachabilityStatus.BLOCKED_BY_POLICY, "host resolves to a non-public address"
    return None, None
