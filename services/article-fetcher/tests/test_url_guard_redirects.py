"""Tests for the raised SSRF redirect cap (safe_get max_redirects 5 -> 10).

Legit publishers (e.g. Springer) use longer redirect chains than 5; the old cap
false-blocked them. DNS is kept out of the unit test by monkeypatching
assert_public_http_url to a no-op so the redirect-count logic is tested in
isolation (per-hop revalidation itself is covered in test_url_guard.py).
"""
import pytest

import app.url_guard as url_guard
from app.url_guard import UnsafeUrlError, safe_get


class _Headers(dict):
    """Case-insensitive .get to mirror real response header mappings."""

    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class _Resp:
    def __init__(self, status_code, location=None, content=b"body"):
        self.status_code = status_code
        self.content = content
        self.headers = _Headers()
        if location is not None:
            self.headers["Location"] = location


def _make_getter(responses):
    queue = list(responses)
    calls = []

    def getter(url, **kwargs):
        calls.append(url)
        return queue.pop(0)

    getter.calls = calls
    return getter


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    # Keep DNS out of the redirect-count unit test.
    monkeypatch.setattr(url_guard, "assert_public_http_url", lambda url: None)


def test_safe_get_follows_eight_redirects_under_default_cap():
    responses = [
        _Resp(302, location=f"http://example.com/hop{i}") for i in range(8)
    ]
    responses.append(_Resp(200, content=b"%PDF-1.4 ok"))
    getter = _make_getter(responses)

    resp = safe_get(getter, "http://example.com/start")  # default max_redirects=10

    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 ok"
    assert len(getter.calls) == 9  # initial + 8 redirect hops


def test_safe_get_eleven_redirects_exceeds_default_cap():
    # 11 redirect hops > default cap of 10 -> raise.
    responses = [
        _Resp(302, location=f"http://example.com/hop{i}") for i in range(11)
    ]
    getter = _make_getter(responses)

    with pytest.raises(UnsafeUrlError, match="too many redirects"):
        safe_get(getter, "http://example.com/start")
