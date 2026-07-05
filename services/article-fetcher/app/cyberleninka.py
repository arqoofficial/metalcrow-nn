"""Cyberleninka.ru literature search client — RU-language counterpart to `openalex`.

Cyberleninka's `/api/search` returns the FULL article text inline (the `ocr`
field), so RU papers never need the download/fetch cascade the OpenAlex/DOI
path uses — the caller can treat `fulltext` as already fetched.

Public API:
    from cyberleninka import search
    papers = search("флотация сульфидных руд", max_results=8)
    # -> [{doi, title, authors, year, abstract, fulltext, pdf_url,
    #      citation_count, source, url}, ...]

Two gotchas baked in (live-verified 2026-07-04, see
docs/superpowers/plans/2026-07-04-cyberleninka-ru-search.md):
  1. **Transport.** Cyberleninka is only reachable from this box via the
     socks5 proxy `37.16.81.138:1080` (direct also works, but the proxy is
     preferred / more reliable). `requests` + PySocks support socks5 via the
     `proxies=` kwarg; `httpx` does NOT work here (its `socksio` extra is
     missing from the image). On ANY proxy-path exception, retry once direct.
  2. **Field shapes.** `name`/`annotation` contain raw `<b>...</b>` highlight
     tags (strip with a small regex + unescape HTML entities). `authors` is a
     STRINGIFIED Python list (e.g. `"['Иванов И.', 'Петров П.']"`) — parse
     with `ast.literal_eval`, guarded, defaulting to `[]`. `ocr` is a LIST of
     text fragments = the full article text; join with "\n". There is no DOI
     and no PDF — `ocr` IS the full text.
"""
from __future__ import annotations

import ast
import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)

CYBERLENINKA_API_BASE = "https://cyberleninka.ru/api"
CYBERLENINKA_BASE_URL = "https://cyberleninka.ru"

_TAG_RE = re.compile(r"<[^>]+>")
# Full text on a cyberleninka article PAGE lives in
# <div class="ocr" itemprop="articleBody"> as <p> paragraphs (~20k chars). The
# search API's `ocr` field is only a ~750-char PREVIEW, so full text needs a
# page fetch. Anchor on itemprop=articleBody, then take the <p> text after it.
_ARTICLE_BODY_RE = re.compile(r'itemprop=["\']articleBody["\']', re.I)
_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)
_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _strip_tags(s: str | None) -> str:
    """Strip `<b>`/HTML tags and unescape HTML entities from a Cyberleninka field."""
    if not s:
        return ""
    return html.unescape(_TAG_RE.sub("", s)).strip()


def _parse_authors(raw: object) -> list[str]:
    """Parse Cyberleninka's `authors` field.

    The plan's live-verified fixture had this field as a STRINGIFIED Python
    list (e.g. `"['A', 'B']"`), parsed with `ast.literal_eval`. A follow-up
    live check (2026-07-04) found the production API actually returns a real
    JSON list in practice — so both shapes are handled: a plain list is used
    as-is, a string is `ast.literal_eval`'d. Never raises: any malformed input
    (not a string/list, not valid literal syntax, not actually a list after
    eval) degrades to an empty list.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        parsed = raw
    elif isinstance(raw, str):
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return []
    else:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(a) for a in parsed if a]


def _normalize(article: dict) -> dict:
    ocr_fragments = article.get("ocr") or []
    fulltext = "\n".join(_strip_tags(fragment) for fragment in ocr_fragments)
    authors = _parse_authors(article.get("authors"))
    year = article.get("year")
    link = article.get("link") or ""
    return {
        "doi": None,
        "title": _strip_tags(article.get("name")),
        "authors": ", ".join(authors) or "Unknown",
        "year": int(year) if year and str(year).isdigit() else None,
        "abstract": _strip_tags(article.get("annotation")),
        "fulltext": fulltext,
        "pdf_url": None,
        "citation_count": None,
        "source": "cyberleninka",
        "url": f"{CYBERLENINKA_BASE_URL}{link}" if link else CYBERLENINKA_BASE_URL,
    }


def _post_search(query: str, max_results: int, timeout: float, proxies: dict | None) -> requests.Response:
    return requests.post(
        f"{CYBERLENINKA_API_BASE}/search",
        json={"mode": "articles", "q": query, "size": max_results, "from": 0},
        timeout=timeout,
        proxies=proxies,
    )


def _proxies_for(proxy_url: str | None) -> dict | None:
    return {"http": proxy_url, "https": proxy_url} if proxy_url else None


def _extract_article_body(page: str) -> str:
    """Extract the `<p>` text following `itemprop="articleBody"` from an HTML page.

    Shared by the plain-``requests`` fetch and the headless-rendered fallback in
    `fetch_fulltext` so both paths run the exact same extraction."""
    m = _ARTICLE_BODY_RE.search(page)
    body = page[m.start():] if m else page
    parts = [_strip_tags(p) for p in _P_RE.findall(body)]
    return "\n".join(p for p in parts if p)


def fetch_fulltext(url: str, *, proxy_url: str | None = None, timeout: float = 20.0) -> str:
    """Fetch a cyberleninka article PAGE and extract its full OCR text.

    The search API only returns a ~750-char preview in `ocr`; the full article
    (~20k chars) is on the article page inside
    `<div class="ocr" itemprop="articleBody">` as `<p>` paragraphs. Tries the
    proxy first (if given), falls back to direct once.

    If the plain-``requests`` fetch fails outright or comes back with no
    extractable article body (empty page, JS interstitial, transient bot-block,
    etc.), falls back to the invisible-playwright headless renderer
    (`app.headless_downloader.fetch_html_via_headless`) to render the page and
    re-runs the SAME extraction on the rendered HTML. `headless_downloader` is
    imported lazily (inside this function) to avoid an import cycle and to keep
    its heavy optional deps out of this module's import path.

    Never raises — returns "" on any failure so a single unfetchable page never
    breaks the batch."""
    if not url:
        return ""
    headers = {"User-Agent": _BROWSER_UA}
    resp: requests.Response | None = None
    proxies = _proxies_for(proxy_url)
    if proxies:
        try:
            resp = requests.get(url, headers=headers, timeout=timeout, proxies=proxies)
        except Exception:
            logger.warning("Cyberleninka fulltext proxied GET failed for %s; retrying direct", url)
            resp = None
    if resp is None:
        try:
            resp = requests.get(url, headers=headers, timeout=timeout, proxies=None)
        except Exception:
            logger.warning("Cyberleninka fulltext GET failed for %s", url, exc_info=True)
            return _fetch_fulltext_via_headless(url)

    text = ""
    try:
        resp.raise_for_status()
        text = _extract_article_body(resp.text)
    except Exception:
        logger.warning("Cyberleninka fulltext GET failed for %s", url, exc_info=True)

    if text:
        return text
    return _fetch_fulltext_via_headless(url)


def _fetch_fulltext_via_headless(url: str) -> str:
    """Headless-render fallback for `fetch_fulltext`; see its docstring. Never raises."""
    # Respect the global headless kill-switch (settings.headless_fetch_enabled,
    # default OFF). Without this, the live /search_ru endpoint would spawn a
    # stealth-browser subprocess for EVERY empty-body page even when headless is
    # disabled — exactly what the flag exists to prevent. Gated like the PDF path.
    from app.config import settings  # lazy: mirror the other lazy imports here

    if not settings.headless_fetch_enabled:
        return ""
    logger.info("Cyberleninka fulltext empty via plain fetch for %s; trying headless fallback", url)
    try:
        from app import headless_downloader  # lazy: optional heavy dep, avoid import cycle
        page = headless_downloader.fetch_html_via_headless(url)
    except Exception:
        logger.warning("Cyberleninka headless fallback failed for %s", url, exc_info=True)
        return ""
    if not page:
        return ""
    return _extract_article_body(page)


def search(
    query: str,
    max_results: int = 8,
    *,
    proxy_url: str | None = None,
    timeout: float = 15.0,
    with_fulltext: bool = False,
) -> list[dict]:
    """Query Cyberleninka's `/api/search` and return normalized paper dicts.

    Each dict: {doi, title, authors, year, abstract, fulltext, pdf_url,
    citation_count, source, url}. `doi` and `pdf_url` are always None
    (Cyberleninka has neither).

    `fulltext` is the joined `ocr` PREVIEW (~750 chars) by default. Pass
    `with_fulltext=True` to additionally fetch each result's article PAGE (in
    parallel) and replace `fulltext` with the FULL article text (~20k chars);
    if a page fetch fails, the preview is kept as a fallback so `fulltext` is
    never empty for a found paper.

    Tries `proxy_url` (if given) first; on ANY exception on that path, retries
    once direct. Never raises — logs a warning and returns [] if both attempts
    fail (mirrors `openalex.search`'s never-raise contract).
    """
    if max_results <= 0:
        return []

    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    resp: requests.Response | None = None

    if proxies:
        try:
            resp = _post_search(query, max_results, timeout, proxies)
        except Exception:
            logger.warning(
                "Cyberleninka proxied request failed for query=%r; retrying direct",
                query, exc_info=True,
            )
            resp = None

    if resp is None:
        try:
            resp = _post_search(query, max_results, timeout, None)
        except Exception:
            logger.warning("Cyberleninka request failed for query=%r", query, exc_info=True)
            return []

    try:
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.warning("Cyberleninka request failed for query=%r", query, exc_info=True)
        return []

    # Defensive: cyberleninka is an undocumented, scraping-derived API whose
    # shapes have already drifted once (authors list vs stringified list). Guard
    # a non-dict body and normalize each article in isolation so one malformed
    # record (or a non-dict body) degrades to fewer/zero results instead of a
    # 500 — honouring the never-raises contract end to end.
    if not isinstance(data, dict):
        logger.warning("Cyberleninka returned a non-dict body for query=%r", query)
        return []
    articles = data.get("articles") or []
    if not isinstance(articles, list):
        logger.warning("Cyberleninka 'articles' was not a list for query=%r", query)
        return []
    results: list[dict] = []
    for article in articles[:max_results]:
        if not isinstance(article, dict):
            logger.warning("Cyberleninka skipped a non-dict article for query=%r", query)
            continue
        try:
            results.append(_normalize(article))
        except Exception:
            logger.warning(
                "Cyberleninka failed to normalize an article for query=%r; skipping",
                query, exc_info=True,
            )

    if with_fulltext and results:
        # Fetch each article page's FULL text in parallel; keep the preview as a
        # fallback when a page fetch returns nothing. Bounded pool so a burst of
        # RU papers doesn't open too many sockets at once.
        def _fill(paper: dict) -> None:
            full = fetch_fulltext(paper["url"], proxy_url=proxy_url, timeout=timeout)
            if full:
                paper["fulltext"] = full

        with ThreadPoolExecutor(max_workers=min(5, len(results))) as pool:
            list(pool.map(_fill, results))

    return results


if __name__ == "__main__":  # quick manual smoke test
    import json

    for p in search("флотация сульфидных руд", max_results=2, proxy_url="socks5h://37.16.81.138:1080"):
        print(json.dumps(
            {k: p[k] for k in ("title", "year", "url")} | {"fulltext_len": len(p["fulltext"])},
            ensure_ascii=False,
        ))
