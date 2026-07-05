"""Tests for `app.cyberleninka` — the standalone Cyberleninka `/api/search` client.

Covers the normalization contract: `<b>`/HTML-tag stripping, the
stringified-Python-list `authors` field, the `ocr`-list -> `fulltext` join,
the DOI-less/PDF-less shape, the `url` prefix, and the top-`max_results` crop.
Uses a small INLINE fixture (no external files) and mocks `requests.post`.
"""

from app import cyberleninka


FIXTURE_ARTICLES = [
    {
        "name": "Роль <b>флотации</b> в обогащении <b>сульфидных</b> руд",
        "annotation": "Статья посвящена <b>флотации</b> сульфидных руд и её оптимизации.",
        "authors": "['Иванов И. И.', 'Петров П. П.']",
        "year": "2019",
        "journal": "Горный журнал",
        "link": "/article/n/rol-flotatsii-v-obogaschenii",
        "ocr": [
            "Введение. Флотация является ",
            "основным методом обогащения.",
            "Заключение.",
        ],
    },
    {
        "name": "Минимальная статья",
        "annotation": "",
        "authors": "[]",
        "year": "",
        "journal": "",
        "link": "/article/n/minimalnaya-statya",
        "ocr": [],
    },
]


class FakeResponse:
    def __init__(self, *, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {"found": 0, "articles": []}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_post(json_data):
    def _post(url, json=None, timeout=None, proxies=None):
        return FakeResponse(json_data=json_data)
    return _post


def test_search_strips_tags_from_title_and_abstract(monkeypatch):
    monkeypatch.setattr(
        cyberleninka.requests, "post",
        _fake_post({"found": 2, "articles": FIXTURE_ARTICLES}),
    )

    results = cyberleninka.search("флотация сульфидных руд", max_results=8)

    assert "<b>" not in results[0]["title"]
    assert "<b>" not in results[0]["abstract"]
    assert results[0]["title"] == "Роль флотации в обогащении сульфидных руд"
    assert results[0]["abstract"] == "Статья посвящена флотации сульфидных руд и её оптимизации."


def test_search_parses_stringified_authors_list(monkeypatch):
    monkeypatch.setattr(
        cyberleninka.requests, "post",
        _fake_post({"found": 2, "articles": FIXTURE_ARTICLES}),
    )

    results = cyberleninka.search("флотация", max_results=8)

    assert results[0]["authors"] == "Иванов И. И., Петров П. П."
    # Empty authors list -> "Unknown" fallback.
    assert results[1]["authors"] == "Unknown"


def test_search_joins_ocr_into_fulltext(monkeypatch):
    monkeypatch.setattr(
        cyberleninka.requests, "post",
        _fake_post({"found": 2, "articles": FIXTURE_ARTICLES}),
    )

    results = cyberleninka.search("флотация", max_results=8)

    assert results[0]["fulltext"] == (
        "Введение. Флотация является\nосновным методом обогащения.\nЗаключение."
    )
    assert results[1]["fulltext"] == ""


def test_search_has_no_doi_and_no_pdf(monkeypatch):
    monkeypatch.setattr(
        cyberleninka.requests, "post",
        _fake_post({"found": 2, "articles": FIXTURE_ARTICLES}),
    )

    results = cyberleninka.search("флотация", max_results=8)

    for r in results:
        assert r["doi"] is None
        assert r["pdf_url"] is None
        assert r["source"] == "cyberleninka"


def test_search_prefixes_url_with_cyberleninka_base(monkeypatch):
    monkeypatch.setattr(
        cyberleninka.requests, "post",
        _fake_post({"found": 2, "articles": FIXTURE_ARTICLES}),
    )

    results = cyberleninka.search("флотация", max_results=8)

    assert results[0]["url"] == "https://cyberleninka.ru/article/n/rol-flotatsii-v-obogaschenii"


def test_search_crops_to_max_results(monkeypatch):
    monkeypatch.setattr(
        cyberleninka.requests, "post",
        _fake_post({"found": 2, "articles": FIXTURE_ARTICLES}),
    )

    results = cyberleninka.search("флотация", max_results=1)

    assert len(results) == 1
    assert results[0]["title"] == "Роль флотации в обогащении сульфидных руд"


def test_search_parses_year_as_int(monkeypatch):
    monkeypatch.setattr(
        cyberleninka.requests, "post",
        _fake_post({"found": 2, "articles": FIXTURE_ARTICLES}),
    )

    results = cyberleninka.search("флотация", max_results=8)

    assert results[0]["year"] == 2019
    assert results[1]["year"] is None  # empty string year -> None


def test_search_proxy_failure_falls_back_to_direct(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None, proxies=None):
        calls.append(proxies)
        if proxies is not None:
            raise ConnectionError("proxy unreachable")
        return FakeResponse(json_data={"found": 1, "articles": [FIXTURE_ARTICLES[0]]})

    monkeypatch.setattr(cyberleninka.requests, "post", fake_post)

    results = cyberleninka.search(
        "флотация", max_results=8, proxy_url="socks5h://37.16.81.138:1080"
    )

    assert len(calls) == 2
    assert calls[0] == {"http": "socks5h://37.16.81.138:1080", "https": "socks5h://37.16.81.138:1080"}
    assert calls[1] is None
    assert len(results) == 1


def test_search_returns_empty_list_on_total_failure(monkeypatch):
    def fake_post(url, json=None, timeout=None, proxies=None):
        raise ConnectionError("boom")

    monkeypatch.setattr(cyberleninka.requests, "post", fake_post)

    results = cyberleninka.search("флотация", max_results=8)

    assert results == []


def test_search_max_results_zero_returns_empty_without_request(monkeypatch):
    def fake_post(*args, **kwargs):
        raise AssertionError("should not be called when max_results <= 0")

    monkeypatch.setattr(cyberleninka.requests, "post", fake_post)

    assert cyberleninka.search("флотация", max_results=0) == []


def test_parse_authors_handles_malformed_input():
    assert cyberleninka._parse_authors(None) == []
    assert cyberleninka._parse_authors("") == []
    assert cyberleninka._parse_authors("not a list") == []
    assert cyberleninka._parse_authors("{'a': 1}") == []
    assert cyberleninka._parse_authors("['A', 'B']") == ["A", "B"]


def test_parse_authors_handles_real_json_list():
    # Live API (2026-07-04) returns a real JSON list, not a stringified one —
    # the client must handle both shapes.
    assert cyberleninka._parse_authors(["Иванов И. И.", "Петров П. П."]) == [
        "Иванов И. И.", "Петров П. П.",
    ]
    assert cyberleninka._parse_authors([]) == []


def test_search_handles_real_list_authors_field(monkeypatch):
    article = dict(FIXTURE_ARTICLES[0])
    article["authors"] = ["Иванов И. И.", "Петров П. П."]
    monkeypatch.setattr(
        cyberleninka.requests, "post",
        _fake_post({"found": 1, "articles": [article]}),
    )

    results = cyberleninka.search("флотация", max_results=8)

    assert results[0]["authors"] == "Иванов И. И., Петров П. П."


def test_search_non_dict_body_returns_empty(monkeypatch):
    # A scraping-derived API can return a bare list / null on an errored search.
    monkeypatch.setattr(cyberleninka.requests, "post", _fake_post([1, 2, 3]))
    assert cyberleninka.search("флотация", max_results=8) == []


def test_search_articles_not_a_list_returns_empty(monkeypatch):
    monkeypatch.setattr(
        cyberleninka.requests, "post",
        _fake_post({"found": 0, "articles": "oops"}),
    )
    assert cyberleninka.search("флотация", max_results=8) == []


def test_search_skips_malformed_article_keeps_good_ones(monkeypatch):
    # One non-dict article + one article with a non-string name must not crash
    # the batch — the good article still comes through.
    good = dict(FIXTURE_ARTICLES[0])
    monkeypatch.setattr(
        cyberleninka.requests, "post",
        _fake_post({"found": 3, "articles": ["not-a-dict", {"name": {"x": 1}, "ocr": []}, good]}),
    )
    results = cyberleninka.search("флотация", max_results=8)
    assert len(results) >= 1
    assert any(r["source"] == "cyberleninka" for r in results)


# --------------------------------------------------------------------------- #
# fetch_fulltext: headless-render fallback when the plain-requests fetch is
# empty. The headless tier itself is never exercised here (no real browser) —
# `app.headless_downloader.fetch_html_via_headless` is monkeypatched directly.
# --------------------------------------------------------------------------- #
_FIXTURE_FULLTEXT_HTML = """
<html><body>
<div class="ocr" itemprop="articleBody">
<p>Введение. Флотация является основным методом.</p>
<p>Заключение по результатам.</p>
</div>
</body></html>
"""


class FakeGetResponse:
    def __init__(self, *, status_code=200, text=""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _enable_headless(monkeypatch):
    """The headless fallback is gated on settings.headless_fetch_enabled (default
    OFF). Turn it ON so the fallback path is exercised. cyberleninka reads the
    singleton via `from app.config import settings`, so patch that same object."""
    from app.config import settings as _settings
    monkeypatch.setattr(_settings, "headless_fetch_enabled", True)


def test_fetch_fulltext_falls_back_to_headless_when_plain_fetch_empty(monkeypatch):
    # Plain `requests.get` succeeds but the page has no extractable articleBody
    # (e.g. a JS interstitial served instead of the real article).
    _enable_headless(monkeypatch)
    monkeypatch.setattr(
        cyberleninka.requests, "get",
        lambda *a, **k: FakeGetResponse(text="<html><body>no article here</body></html>"),
    )

    from app import headless_downloader

    monkeypatch.setattr(
        headless_downloader, "fetch_html_via_headless",
        lambda url: _FIXTURE_FULLTEXT_HTML,
    )

    text = cyberleninka.fetch_fulltext("https://cyberleninka.ru/article/n/some-article")

    assert text == (
        "Введение. Флотация является основным методом.\nЗаключение по результатам."
    )


def test_fetch_fulltext_falls_back_to_headless_on_get_exception(monkeypatch):
    # The plain GET itself raises (e.g. connection reset) — must also trigger
    # the headless fallback rather than just returning "".
    _enable_headless(monkeypatch)

    def _boom(*a, **k):
        raise ConnectionError("boom")

    monkeypatch.setattr(cyberleninka.requests, "get", _boom)

    from app import headless_downloader

    monkeypatch.setattr(
        headless_downloader, "fetch_html_via_headless",
        lambda url: _FIXTURE_FULLTEXT_HTML,
    )

    text = cyberleninka.fetch_fulltext("https://cyberleninka.ru/article/n/some-article")

    assert "Введение" in text


def test_fetch_fulltext_never_raises_when_headless_fallback_raises(monkeypatch):
    _enable_headless(monkeypatch)
    monkeypatch.setattr(
        cyberleninka.requests, "get",
        lambda *a, **k: FakeGetResponse(text="<html><body>no article here</body></html>"),
    )

    from app import headless_downloader

    def _boom(url):
        raise RuntimeError("headless subprocess exploded")

    monkeypatch.setattr(headless_downloader, "fetch_html_via_headless", _boom)

    assert cyberleninka.fetch_fulltext("https://cyberleninka.ru/article/n/some-article") == ""


def test_fetch_fulltext_skips_headless_when_disabled(monkeypatch):
    # With headless_fetch_enabled OFF (the default), an empty plain fetch must
    # NOT spawn the headless fallback — it returns "" without calling it. (This
    # replaces a prior test that patched builtins.__import__, which CPython's
    # `from app import headless_downloader` submodule resolution bypasses, so it
    # silently exercised the REAL headless path.)
    from app.config import settings as _settings
    monkeypatch.setattr(_settings, "headless_fetch_enabled", False)
    monkeypatch.setattr(cyberleninka.requests, "get", lambda *a, **k: FakeGetResponse(text=""))

    from app import headless_downloader

    def _must_not_be_called(url):
        raise AssertionError("headless fallback must not fire when disabled")

    monkeypatch.setattr(headless_downloader, "fetch_html_via_headless", _must_not_be_called)

    assert cyberleninka.fetch_fulltext("https://cyberleninka.ru/article/n/some-article") == ""


def test_fetch_fulltext_returns_empty_string_for_empty_url():
    assert cyberleninka.fetch_fulltext("") == ""


def test_fetch_fulltext_uses_plain_result_without_headless_when_non_empty(monkeypatch):
    monkeypatch.setattr(
        cyberleninka.requests, "get",
        lambda *a, **k: FakeGetResponse(text=_FIXTURE_FULLTEXT_HTML),
    )

    from app import headless_downloader

    def _should_not_be_called(url):
        raise AssertionError("headless fallback must not fire when plain fetch succeeds")

    monkeypatch.setattr(headless_downloader, "fetch_html_via_headless", _should_not_be_called)

    text = cyberleninka.fetch_fulltext("https://cyberleninka.ru/article/n/some-article")
    assert "Введение" in text
