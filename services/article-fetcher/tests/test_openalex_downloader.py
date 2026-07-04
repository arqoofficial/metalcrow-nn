"""Tests for the OpenAlex PDF downloader (preferred fetch path before Sci-Hub)."""

from unittest.mock import MagicMock

from app import openalex_downloader


PDF_BODY = b"%PDF-1.7\n fake pdf content"
HTML_BODY = b"<!doctype html><html><head><title>Landing</title></head></html>"


class FakeResponse:
    def __init__(self, *, status_code=200, content=b"", content_type=None, json_data=None):
        self.status_code = status_code
        self.content = content
        self._json = json_data
        self.headers = {}
        if content_type is not None:
            self.headers["content-type"] = content_type

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# ---------------------------------------------------------------------------
# _looks_like_pdf
# ---------------------------------------------------------------------------


def test_looks_like_pdf_magic_bytes():
    assert openalex_downloader._looks_like_pdf(None, PDF_BODY) is True


def test_looks_like_pdf_content_type():
    assert openalex_downloader._looks_like_pdf("application/pdf", b"not magic but typed") is True


def test_looks_like_pdf_rejects_html():
    assert openalex_downloader._looks_like_pdf("text/html", HTML_BODY) is False


def test_looks_like_pdf_rejects_empty():
    assert openalex_downloader._looks_like_pdf("application/pdf", b"") is False


# ---------------------------------------------------------------------------
# _SELECT_FIELDS — content_url must be gone (OpenAlex 400s on it)
# ---------------------------------------------------------------------------


def test_select_fields_excludes_content_url():
    assert "content_url" not in openalex_downloader._SELECT_FIELDS


# ---------------------------------------------------------------------------
# download_pdf_via_openalex
# ---------------------------------------------------------------------------


def test_oa_skips_html_candidate_then_returns_pdf(monkeypatch):
    """First OA host returns Access-Denied HTML; downloader tries the next and wins."""
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_base", "https://api.openalex.org")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_key", "")
    monkeypatch.setattr(openalex_downloader, "_get_redis", lambda: None)

    work = {
        "has_content": {"pdf": False, "grobid_xml": False},
        "best_oa_location": {"is_oa": True, "pdf_url": "https://mdpi.example/blocked.pdf"},
        "locations": [
            {"is_oa": True, "pdf_url": "https://bmc.example/paper.pdf"},
        ],
    }

    access_denied = b"<!doctype html><html><body>Access Denied</body></html>"
    calls = []

    def get_handler(url, headers=None):
        calls.append(url)
        if url == "https://mdpi.example/blocked.pdf":
            return FakeResponse(content=access_denied, content_type="text/html")
        return FakeResponse(content=PDF_BODY, content_type="application/pdf")

    _patch_http(monkeypatch, work=work, get_handler=get_handler)

    result = openalex_downloader.download_pdf_via_openalex("10.1/abc")
    assert result == PDF_BODY
    # Both candidates attempted, in order; HTML one rejected, PDF one accepted.
    assert calls == ["https://mdpi.example/blocked.pdf", "https://bmc.example/paper.pdf"]


def test_oa_all_candidates_fail_returns_none(monkeypatch):
    """When every OA candidate yields HTML/errors, return None (→ Sci-Hub fallback)."""
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_base", "https://api.openalex.org")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_key", "")
    monkeypatch.setattr(openalex_downloader, "_get_redis", lambda: None)

    work = {
        "has_content": {"pdf": False, "grobid_xml": False},
        "best_oa_location": {"is_oa": True, "pdf_url": "https://a.example/a.pdf"},
        "primary_location": {"is_oa": True, "pdf_url": "https://b.example/b.pdf"},
        "locations": [{"is_oa": True, "pdf_url": "https://c.example/c.pdf"}],
    }

    calls = []

    def get_handler(url, headers=None):
        calls.append(url)
        return FakeResponse(content=HTML_BODY, content_type="text/html")

    _patch_http(monkeypatch, work=work, get_handler=get_handler)

    result = openalex_downloader.download_pdf_via_openalex("10.1/abc")
    assert result is None
    # All three distinct candidates were attempted before giving up.
    assert calls == [
        "https://a.example/a.pdf",
        "https://b.example/b.pdf",
        "https://c.example/c.pdf",
    ]


def test_oa_sends_browser_headers(monkeypatch):
    """OA PDF GET carries a browser UA, Accept and a same-host Referer."""
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_base", "https://api.openalex.org")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_key", "")
    monkeypatch.setattr(openalex_downloader, "_get_redis", lambda: None)

    work = {
        "has_content": {"pdf": False, "grobid_xml": False},
        "best_oa_location": {"is_oa": True, "pdf_url": "https://oa.example/dir/paper.pdf"},
    }

    captured = {}

    def get_handler(url, headers=None):
        captured["headers"] = headers
        return FakeResponse(content=PDF_BODY, content_type="application/pdf")

    _patch_http(monkeypatch, work=work, get_handler=get_handler)

    result = openalex_downloader.download_pdf_via_openalex("10.1/abc")
    assert result == PDF_BODY
    hdrs = captured["headers"]
    assert "Mozilla/5.0" in hdrs["User-Agent"]
    assert "application/pdf" in hdrs["Accept"]
    assert hdrs["Referer"] == "https://oa.example/"


def _patch_http(monkeypatch, *, work, get_handler):
    """Patch _http_get_json (work fetch) and _http_get_bytes (PDF fetch)."""

    def fake_get_json(url):
        return work

    monkeypatch.setattr(openalex_downloader, "_http_get_json", fake_get_json)
    monkeypatch.setattr(openalex_downloader, "_http_get_bytes", get_handler)


def test_managed_content_download_uses_api_key(monkeypatch):
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_base", "https://api.openalex.org")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_key", "SECRET")
    # Redis: counter below cap.
    fake_redis = MagicMock()
    fake_redis.get.return_value = "0"
    monkeypatch.setattr(openalex_downloader, "_get_redis", lambda: fake_redis)

    work = {
        "has_content": {"pdf": True, "grobid_xml": False},
        "content_url": "https://api.openalex.org/works/W1/content",
    }

    calls = []

    def get_handler(url, headers=None):
        calls.append(url)
        return FakeResponse(content=PDF_BODY, content_type="application/pdf")

    _patch_http(monkeypatch, work=work, get_handler=get_handler)

    result = openalex_downloader.download_pdf_via_openalex("10.1/abc")
    assert result == PDF_BODY
    assert len(calls) == 1
    assert "api_key=SECRET" in calls[0]
    assert calls[0].startswith("https://api.openalex.org/works/W1/content")
    # INCR fired on successful managed-content download.
    assert fake_redis.incr.called


def test_oa_pdf_url_no_api_key(monkeypatch):
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_base", "https://api.openalex.org")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_key", "SECRET")
    monkeypatch.setattr(openalex_downloader, "_get_redis", lambda: None)

    work = {
        "has_content": {"pdf": False, "grobid_xml": False},
        "best_oa_location": {"is_oa": True, "pdf_url": "https://oa.example/paper.pdf"},
    }

    calls = []

    def get_handler(url, headers=None):
        calls.append(url)
        return FakeResponse(content=PDF_BODY, content_type="application/pdf")

    _patch_http(monkeypatch, work=work, get_handler=get_handler)

    result = openalex_downloader.download_pdf_via_openalex("10.1/abc")
    assert result == PDF_BODY
    assert len(calls) == 1
    assert calls[0] == "https://oa.example/paper.pdf"
    assert "api_key" not in calls[0]


def test_oa_landing_page_html_rejected(monkeypatch):
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_base", "https://api.openalex.org")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_key", "")
    monkeypatch.setattr(openalex_downloader, "_get_redis", lambda: None)

    work = {
        "has_content": {"pdf": False, "grobid_xml": False},
        "open_access": {"is_oa": True, "oa_url": "https://landing.example/article"},
    }

    def get_handler(url, headers=None):
        return FakeResponse(content=HTML_BODY, content_type="text/html")

    _patch_http(monkeypatch, work=work, get_handler=get_handler)

    result = openalex_downloader.download_pdf_via_openalex("10.1/abc")
    assert result is None


def test_work_not_found_returns_none(monkeypatch):
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_base", "https://api.openalex.org")
    monkeypatch.setattr(openalex_downloader, "_get_redis", lambda: None)
    monkeypatch.setattr(openalex_downloader, "_http_get_json", lambda url: None)

    result = openalex_downloader.download_pdf_via_openalex("10.1/missing")
    assert result is None


def test_base_unset_returns_none(monkeypatch):
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_base", "")
    result = openalex_downloader.download_pdf_via_openalex("10.1/abc")
    assert result is None


def test_daily_cap_skips_managed_content(monkeypatch):
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_base", "https://api.openalex.org")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_key", "SECRET")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_content_daily_cap", 100)

    fake_redis = MagicMock()
    fake_redis.get.return_value = "100"  # at cap
    monkeypatch.setattr(openalex_downloader, "_get_redis", lambda: fake_redis)

    work = {
        "has_content": {"pdf": True, "grobid_xml": False},
        "content_url": "https://api.openalex.org/works/W1/content",
        "best_oa_location": {"is_oa": True, "pdf_url": "https://oa.example/paper.pdf"},
    }

    calls = []

    def get_handler(url, headers=None):
        calls.append(url)
        return FakeResponse(content=PDF_BODY, content_type="application/pdf")

    _patch_http(monkeypatch, work=work, get_handler=get_handler)

    result = openalex_downloader.download_pdf_via_openalex("10.1/abc")
    assert result == PDF_BODY
    # content_url must NOT be called; only the OA pdf_url.
    assert all("/content" not in c for c in calls)
    assert calls == ["https://oa.example/paper.pdf"]
    # No INCR since managed content was skipped.
    assert not fake_redis.incr.called


# ---------------------------------------------------------------------------
# _fetch_work — API-key failover (article-fetcher prefers key2, then key1)
# ---------------------------------------------------------------------------


def test_fetch_work_prefers_key2_then_fails_over_to_key1(monkeypatch):
    """key2 lookup yields no JSON (401/403/429/5xx -> None) -> retry with key1."""
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_base", "https://api.openalex.org")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_key", "KEY1")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_key_2", "KEY2")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_mailto", "")

    work = {"has_content": {"pdf": False}, "best_oa_location": {}}
    calls = []

    def fake_get_json(url):
        calls.append(url)
        # key2 is tried first (preferred) and "fails"; key1 returns the work.
        if "api_key=KEY2" in url:
            return None
        if "api_key=KEY1" in url:
            return work
        return None

    monkeypatch.setattr(openalex_downloader, "_http_get_json", fake_get_json)

    result = openalex_downloader._fetch_work("10.1/abc")
    assert result is work
    # Two attempts in order: key2 first (preferred), then key1.
    assert len(calls) == 2
    assert "api_key=KEY2" in calls[0]
    assert "api_key=KEY1" in calls[1]


def test_fetch_work_returns_none_when_all_keys_fail(monkeypatch):
    """Every key lookup fails -> None (so fetch_article falls back to Sci-Hub)."""
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_base", "https://api.openalex.org")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_key", "KEY1")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_key_2", "KEY2")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_mailto", "")

    calls = []

    def fake_get_json(url):
        calls.append(url)
        return None

    monkeypatch.setattr(openalex_downloader, "_http_get_json", fake_get_json)

    result = openalex_downloader._fetch_work("10.1/abc")
    assert result is None
    # Both keys attempted.
    assert len(calls) == 2
    assert "api_key=KEY2" in calls[0]
    assert "api_key=KEY1" in calls[1]


def test_fetch_work_single_key_no_failover(monkeypatch):
    """A single configured key behaves exactly as before (one attempt)."""
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_base", "https://api.openalex.org")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_key", "ONLYKEY")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_api_key_2", "")
    monkeypatch.setattr(openalex_downloader.settings, "openalex_mailto", "")

    work = {"has_content": {"pdf": False}}
    calls = []

    def fake_get_json(url):
        calls.append(url)
        return work

    monkeypatch.setattr(openalex_downloader, "_http_get_json", fake_get_json)

    result = openalex_downloader._fetch_work("10.1/abc")
    assert result is work
    assert len(calls) == 1
    assert "api_key=ONLYKEY" in calls[0]


# ---------------------------------------------------------------------------
# fetch_article integration
# ---------------------------------------------------------------------------


def test_fetch_article_uses_openalex_first(monkeypatch):
    from app import fetcher

    monkeypatch.setattr(fetcher, "download_pdf_via_openalex", lambda doi: PDF_BODY)

    def boom(url):
        raise AssertionError("Sci-Hub should not be called when OpenAlex succeeds")

    monkeypatch.setattr(fetcher, "_curl_get_bytes", boom)

    result = fetcher.fetch_article("10.1000/abc")
    assert result == PDF_BODY


def test_fetch_article_falls_back_to_scihub(monkeypatch):
    from app import fetcher

    monkeypatch.setattr(fetcher, "download_pdf_via_openalex", lambda doi: None)

    calls = []

    def fake_curl(url):
        calls.append(url)
        # First call = sci-hub page (return a page with a pdf url), second = pdf.
        if url.endswith("10.1000/abc"):
            return b'<iframe src="//cdn.example/paper.pdf"></iframe>'
        return PDF_BODY

    monkeypatch.setattr(fetcher, "_curl_get_bytes", fake_curl)

    result = fetcher.fetch_article("10.1000/abc")
    assert result == PDF_BODY
    assert len(calls) >= 1  # Sci-Hub path was taken
