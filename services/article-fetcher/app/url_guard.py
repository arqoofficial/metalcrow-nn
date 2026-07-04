"""Shared SSRF guard for all outbound PDF fetches.

URLs reaching the article-fetcher originate in semi-trusted upstream API
responses (OpenAlex / PDFVector / arXiv ``pdf_url``) or are scraped from Sci-Hub
mirror HTML. A malicious or compromised upstream — or a redirect — could point
the server at internal addresses (the cloud metadata endpoint
``169.254.169.254``, ``127.0.0.1``, or internal Docker service IPs). This module
validates every fetch URL (and each redirect hop) against a private/loopback/
link-local/reserved denylist BEFORE the request is made.

Pure stdlib (``socket``, ``ipaddress``, ``urllib.parse``) — no heavy deps.
"""
import ipaddress
import socket
import urllib.parse

__all__ = ["UnsafeUrlError", "assert_public_http_url", "safe_get"]


class UnsafeUrlError(Exception):
    """Raised when a URL is rejected as unsafe to fetch (SSRF guard)."""


def _ip_is_unsafe(ip_str: str) -> bool:
    """True if ``ip_str`` is a private/loopback/link-local/reserved/etc. address.

    Unwraps IPv4-mapped IPv6 (``::ffff:a.b.c.d``) and re-checks the mapped
    IPv4 address so an internal target cannot hide behind the v6 form.
    """
    ip = ipaddress.ip_address(ip_str)

    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped

    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_public_http_url(url: str) -> None:
    """Raise ``UnsafeUrlError`` unless ``url`` is a public http(s) URL.

    Fails closed: the hostname is resolved to ALL its IPs (IPv4 + IPv6); if ANY
    resolved IP is private/loopback/link-local/reserved/multicast/unspecified,
    the URL is rejected. Returns ``None`` on success.
    """
    parts = urllib.parse.urlsplit(url)

    if parts.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"Unsupported URL scheme: {parts.scheme!r}")

    host = parts.hostname
    if not host:
        raise UnsafeUrlError(f"URL has no host: {url!r}")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Could not resolve host {host!r}: {exc}") from exc

    if not infos:
        raise UnsafeUrlError(f"Host {host!r} resolved to no addresses")

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        if _ip_is_unsafe(ip_str):
            raise UnsafeUrlError(
                f"URL host {host!r} resolves to non-public IP {ip_str}"
            )


def safe_get(getter, url, *, max_redirects=10, redirect_kwarg="allow_redirects", **kwargs):
    """Fetch ``url`` via ``getter`` while validating every URL and redirect hop.

    ``getter`` is a callable ``(url, **kwargs) -> response`` (e.g. ``requests.get``,
    ``curl_cffi.requests.get``, or a thin ``httpx`` wrapper). The response must
    expose ``.status_code`` and ``.headers`` (a mapping with case-insensitive
    ``.get``). Library-agnostic redirect detection: ``300 <= status < 400`` and a
    ``Location`` header.

    Redirects are followed manually (``{redirect_kwarg: False}`` is passed so the
    underlying library does NOT auto-follow); each hop's target is re-validated
    with :func:`assert_public_http_url` before it is requested. Raises
    ``UnsafeUrlError`` on an unsafe URL or when ``max_redirects`` is exceeded.
    """
    assert_public_http_url(url)

    call_kwargs = dict(kwargs)
    call_kwargs[redirect_kwarg] = False

    current_url = url
    resp = getter(current_url, **call_kwargs)

    redirects = 0
    while _is_redirect(resp):
        if redirects >= max_redirects:
            raise UnsafeUrlError("too many redirects")
        location = resp.headers.get("location")
        next_url = urllib.parse.urljoin(current_url, location)
        assert_public_http_url(next_url)
        current_url = next_url
        resp = getter(current_url, **call_kwargs)
        redirects += 1

    return resp


def _is_redirect(resp) -> bool:
    status = getattr(resp, "status_code", 0)
    if not (300 <= status < 400):
        return False
    return resp.headers.get("location") is not None
