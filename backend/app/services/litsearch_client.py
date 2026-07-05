"""Синхронный HTTP-клиент к `article-fetcher` — internal-only сайдкару
OpenAlex-поиска + PDF-фетчинга (services/article-fetcher/, свой uv-workspace,
не часть этого), см. design doc §8 (litsearch → chat integration).

Все функции глушат `httpx.HTTPError` и ошибки разбора JSON, деградируя до
`None`/`[]` — недоступность article-fetcher не должна валить chat/litsearch
tool loop, только вернуть пустой результат. Тот же паттерн, что у
`science_kg_client.py` и `ontology_client.py`.

`trust_env=False` на каждом вызове: internal-only вызов по docker-сети,
системный HTTP(S)_PROXY/ALL_PROXY подхватывать не нужно.
"""

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
# /search_ru does MORE work than a plain search: cyberleninka's client fetches
# each result's full article PAGE inline (in parallel) before responding.
# Measured ~2s for max_results=5, but a slow page (bounded per-page at ~20s in
# the fetcher) could push the whole call past the 10s search budget — which
# would silently degrade RU results to []. Give it a generous dedicated budget.
_SEARCH_RU_TIMEOUT = httpx.Timeout(45.0, connect=3.0)
# fetch_sync runs the full server-side PDF-fetch chain (OpenAlex OA / EuropePMC /
# Sci-Hub / STC, see article-fetcher's `_fetch_pdf_bytes`) inline before responding —
# far slower than the metadata/job-status calls above, hence LITSEARCH_FETCH_TIMEOUT
# (a dedicated setting added for exactly this call, see app/core/config.py).
_FETCH_SYNC_TIMEOUT = httpx.Timeout(settings.LITSEARCH_FETCH_TIMEOUT, connect=5.0)


def search(query: str, max_results: int) -> list[dict[str, Any]]:
    """GET /search — OpenAlex keyword search proxied through article-fetcher.
    Response shape: {"results": [...normalized paper dicts...]}. [] on any error
    (including a malformed/missing "results" key)."""
    try:
        resp = httpx.get(
            f"{settings.ARTICLE_FETCHER_URL}/search",
            params={"query": query, "max_results": max_results},
            timeout=_TIMEOUT,
            trust_env=False,
        )
        resp.raise_for_status()
        data: Any = resp.json()
        if not isinstance(data, dict):
            return []
        results = data.get("results", [])
        return results if isinstance(results, list) else []
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("article-fetcher search failed: %s", exc)
        return []


def search_ru(query: str, max_results: int) -> list[dict[str, Any]]:
    """GET /search_ru — Cyberleninka (RU) keyword search proxied through
    article-fetcher. Each result already carries the FULL article text inline
    (`fulltext`, ~9-55k chars — the article-fetcher fetches the article pages
    itself before responding), not just an abstract/preview, so RU papers skip
    the whole async download cascade `search()`'s OpenAlex results go through.
    Response shape: {"results": [...normalized paper dicts...]}. [] on any
    error (including a malformed/missing "results" key). Mirrors `search()`."""
    try:
        resp = httpx.get(
            f"{settings.ARTICLE_FETCHER_URL}/search_ru",
            params={"query": query, "max_results": max_results},
            timeout=_SEARCH_RU_TIMEOUT,
            trust_env=False,
        )
        resp.raise_for_status()
        data: Any = resp.json()
        if not isinstance(data, dict):
            return []
        results = data.get("results", [])
        return results if isinstance(results, list) else []
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("article-fetcher search_ru failed: %s", exc)
        return []


def resolve(title: str) -> dict[str, Any] | None:
    """GET /resolve — title -> DOI via OpenAlex. Response shape: {"doi","title",
    "year"}. 404 (no DOI found for the title) and any transport/JSON error both
    degrade to None."""
    try:
        resp = httpx.get(
            f"{settings.ARTICLE_FETCHER_URL}/resolve",
            params={"title": title},
            timeout=_TIMEOUT,
            trust_env=False,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("article-fetcher resolve failed for title %r: %s", title, exc)
        return None


def fetch_async(doi: str, *, url: str | None, conversation_id: str) -> str | None:
    """POST /fetch — queue a background PDF-fetch job (202 JobResponse). Returns
    the job_id, or None if the sidecar rejects the request or is unreachable."""
    try:
        resp = httpx.post(
            f"{settings.ARTICLE_FETCHER_URL}/fetch",
            json={"doi": doi, "url": url, "conversation_id": conversation_id},
            timeout=_TIMEOUT,
            trust_env=False,
        )
        resp.raise_for_status()
        data: Any = resp.json()
        if not isinstance(data, dict):
            return None
        job_id = data.get("job_id")
        return job_id if isinstance(job_id, str) else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("article-fetcher fetch_async failed for DOI %s: %s", doi, exc)
        return None


def job_status(job_id: str) -> dict[str, Any] | None:
    """GET /jobs/{job_id} — poll an async fetch job. Response shape: {"status",
    "object_key"?, "url"?, "error"?}. 404 (unknown job) and any transport/JSON
    error both degrade to None."""
    try:
        resp = httpx.get(
            f"{settings.ARTICLE_FETCHER_URL}/jobs/{job_id}",
            timeout=_TIMEOUT,
            trust_env=False,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("article-fetcher job_status failed for job %s: %s", job_id, exc)
        return None


def fetch_sync(doi: str, *, url: str | None) -> dict[str, Any] | None:
    """POST /fetch/sync — inline fetch -> validate -> store -> presign (no
    background job). Response shape: {"doi","object_key","url"}. Can take up to
    LITSEARCH_FETCH_TIMEOUT seconds; 502 (nothing found) and any transport/JSON
    error both degrade to None."""
    try:
        resp = httpx.post(
            f"{settings.ARTICLE_FETCHER_URL}/fetch/sync",
            json={"doi": doi, "url": url},
            timeout=_FETCH_SYNC_TIMEOUT,
            trust_env=False,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("article-fetcher fetch_sync failed for DOI %s: %s", doi, exc)
        return None
