"""Tests for the shared SSRF URL guard (app/url_guard.py).

Pure stdlib functions (socket/ipaddress/urllib) — no network is needed because
all test URLs use IP literals; ``getaddrinfo`` on an IP literal returns that IP
without DNS. ``safe_get`` is exercised with a fake getter + fake responses.
"""
import pytest

from app.url_guard import UnsafeUrlError, assert_public_http_url, safe_get


# ---------------------------------------------------------------------------
# assert_public_http_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1/",                          # loopback
        "http://10.0.0.5/",                           # private
        "http://192.168.1.1/",                        # private
        "http://172.16.0.1/",                         # private
        "http://[::1]/",                              # IPv6 loopback
        "ftp://example.com/x",                        # bad scheme
        "file:///etc/passwd",                         # bad scheme
        "http://0.0.0.0/",                            # unspecified
        "http:///no-host-here",                       # missing host
    ],
)
def test_assert_public_http_url_rejects(url):
    with pytest.raises(UnsafeUrlError):
        assert_public_http_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://93.184.216.34/",   # example.com public IP literal
        "https://8.8.8.8/",        # public DNS IP literal
    ],
)
def test_assert_public_http_url_passes(url):
    assert assert_public_http_url(url) is None


def test_assert_public_http_url_rejects_ipv4_mapped_loopback():
    # IPv4-mapped IPv6 form of 127.0.0.1 must also be rejected.
    with pytest.raises(UnsafeUrlError):
        assert_public_http_url("http://[::ffff:127.0.0.1]/")


# ---------------------------------------------------------------------------
# safe_get redirect revalidation
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status_code, location=None, content=b"body"):
        self.status_code = status_code
        self.content = content
        self.headers = {}
        if location is not None:
            self.headers["location"] = location


class _FakeHeaders(dict):
    """Case-insensitive .get to mirror real response header mappings."""

    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


def _make_getter(responses):
    """Return a fake getter yielding queued responses in order."""
    queue = list(responses)
    calls = []

    def getter(url, **kwargs):
        calls.append(url)
        assert "allow_redirects" in kwargs or "follow_redirects" in kwargs
        return queue.pop(0)

    getter.calls = calls
    return getter


def test_safe_get_blocks_redirect_to_internal():
    getter = _make_getter([_FakeResp(302, location="http://169.254.169.254/")])
    with pytest.raises(UnsafeUrlError):
        safe_get(getter, "http://93.184.216.34/start")


def test_safe_get_follows_public_redirect_then_200():
    getter = _make_getter([
        _FakeResp(302, location="http://8.8.8.8/next"),
        _FakeResp(200, content=b"%PDF-1.4 ok"),
    ])
    resp = safe_get(getter, "http://93.184.216.34/start")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 ok"
    assert getter.calls == ["http://93.184.216.34/start", "http://8.8.8.8/next"]


def test_safe_get_too_many_redirects():
    # Always-redirecting (to a public IP so the guard passes) -> hit the cap.
    responses = [_FakeResp(302, location="http://8.8.8.8/loop") for _ in range(10)]
    getter = _make_getter(responses)
    with pytest.raises(UnsafeUrlError):
        safe_get(getter, "http://93.184.216.34/start", max_redirects=3)


def test_safe_get_validates_initial_url():
    getter = _make_getter([_FakeResp(200)])
    with pytest.raises(UnsafeUrlError):
        safe_get(getter, "http://127.0.0.1/")


def test_safe_get_relative_redirect_resolved_and_checked():
    # Relative Location resolved against current URL; resolves to public host -> ok.
    getter = _make_getter([
        _FakeResp(302, location="/files/doc.pdf"),
        _FakeResp(200, content=b"%PDF ok"),
    ])
    resp = safe_get(getter, "http://8.8.8.8/start")
    assert resp.status_code == 200
    assert getter.calls[1] == "http://8.8.8.8/files/doc.pdf"


def test_safe_get_redirect_kwarg_for_httpx():
    captured = {}

    def getter(url, **kwargs):
        captured.update(kwargs)
        return _FakeResp(200)

    safe_get(getter, "http://8.8.8.8/", redirect_kwarg="follow_redirects")
    assert captured.get("follow_redirects") is False
    assert "allow_redirects" not in captured
