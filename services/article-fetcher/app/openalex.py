"""Standalone OpenAlex literature search — decoupled from Cosmetica.

Handed to metalcrow (litellm-gw) by chemcrow-deploy, 2026-07-03.

Extracted from cosmetic-agent `backend/app/api/routes/internal.py` (the
`/openalex-search` handler + `_reconstruct_abstract`), with ALL Cosmetica
coupling removed: no FastAPI, no Redis, no Celery, no settings object, no
paper-pipeline kickoff, no daily download-cap. Pure `httpx` + stdlib.

Public API:
    from openalex import search
    papers = search("froth flotation collector reagent recovery", max_results=8)
    # -> [{doi, title, authors, year, abstract, pdf_url, citation_count, source}, ...]

Two hard-won gotchas baked in (these bit Cosmetica):
  1. **Abstracts.** OpenAlex NEVER returns a plain `abstract` — only an
     `abstract_inverted_index` (word -> [positions]). Reading `result["abstract"]`
     is always empty; you MUST invert it (`_reconstruct_abstract`).
  2. **Query relaxation.** Do NOT wrap every term in quotes / force exact phrases —
     OpenAlex AND-collapses them to ~0 hits (live: over-quoted -> 856 vs relaxed
     -> 13,678). Pass a natural keyword string; `search()` sends it as-is. If you
     build queries programmatically, relax them BEFORE calling (helper below).

Auth: since Feb-2026 OpenAlex prefers an `api_key` (query param). Pass `api_keys=`
for the premium/keyed pool, or `mailto=` for the anonymous "polite pool"
(recommended if you have no key — much better rate limits than bare anonymous).
"""
from __future__ import annotations

import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

OPENALEX_API_BASE = "https://api.openalex.org"
_RETRYABLE_STATUS = {429, 502, 503, 504}
_FAILOVER_STATUS = {401, 403, 429}


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """Rebuild plain abstract text from OpenAlex's `abstract_inverted_index`."""
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))


def relax_query(query: str) -> str:
    """Strip exact-phrase quoting so OpenAlex doesn't AND-collapse to ~0 hits.

    Only needed if you assemble queries with quotes; a plain keyword string is fine
    to pass straight to `search()`.
    """
    return re.sub(r'"([^"]*)"', r"\1", query).strip()


def _oa_pdf_url(result: dict) -> str | None:
    """Resolve a downloadable OA PDF URL, preferring a bot-wall-free EuropePMC link.

    Publisher `best_oa_location.pdf_url` is often Cloudflare-walled (MDPI, T&F -> 403);
    if the paper has a PMC copy, europepmc.org/articles/<PMCID>?pdf=render serves the
    same OA PDF with no bot wall (verified). Fall back to the publisher pdf_url.
    """
    for loc in result.get("locations") or []:
        u = loc.get("pdf_url") or loc.get("landing_page_url") or ""
        m = re.search(r"(PMC\d+)", u)
        if m:
            return f"https://europepmc.org/articles/{m.group(1)}?pdf=render"
    return (result.get("best_oa_location") or {}).get("pdf_url")


def search(
    query: str,
    max_results: int = 8,
    *,
    api_keys: list[str] | None = None,
    mailto: str | None = None,
    timeout: float = 8.0,
    max_attempts: int = 2,
) -> list[dict]:
    """Query OpenAlex `/works` and return normalized paper dicts.

    Each dict: {doi, title, authors, year, abstract, pdf_url, citation_count, source}.
    `doi` is bare (`10.x/...`), pdf_url may be None. Never raises on a search failure —
    logs a warning and returns [] (so a flaky OpenAlex never breaks the caller).

    Transient 429/5xx are retried with backoff; 401/403/429 fail over across api_keys.
    """
    if max_results <= 0:
        return []
    keys = [k for k in (api_keys or []) if k] or [None]  # [None] => no api_key
    url = f"{OPENALEX_API_BASE}/works"
    resp: httpx.Response | None = None

    for key_idx, api_key in enumerate(keys):
        params: dict = {"search": query, "per_page": max_results}
        if api_key:
            params["api_key"] = api_key
        if mailto:
            params["mailto"] = mailto
        failover = False
        for attempt in range(max_attempts):
            last = attempt + 1 == max_attempts
            try:
                resp = httpx.get(url, params=params, timeout=timeout)
            except httpx.TimeoutException:
                if last:
                    logger.warning("OpenAlex timed out for query=%r", query)
                    resp = None
                    break
                time.sleep(0.5 * (attempt + 1))
                continue
            if resp.status_code in _FAILOVER_STATUS and key_idx + 1 < len(keys):
                failover = True
                break
            if resp.status_code in _RETRYABLE_STATUS and not last:
                time.sleep(0.5 * (attempt + 1))
                continue
            break
        if failover:
            continue
        if resp is not None and resp.status_code in _RETRYABLE_STATUS and key_idx + 1 < len(keys):
            continue
        break

    if resp is None:
        return []
    try:
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.warning("OpenAlex request failed for query=%r", query, exc_info=True)
        return []

    papers: list[dict] = []
    for result in data.get("results", []):
        authors = [
            (a.get("author") or {}).get("display_name")
            for a in result.get("authorships", [])
        ]
        authors = [a for a in authors if a]
        doi = (result.get("doi") or "").replace("https://doi.org/", "").replace("http://dx.doi.org/", "")
        papers.append({
            "doi": doi or None,
            "title": result.get("title") or result.get("display_name") or "",
            "authors": ", ".join(authors) or "Unknown",
            "year": result.get("publication_year"),
            "abstract": result.get("abstract") or _reconstruct_abstract(result.get("abstract_inverted_index")),
            "pdf_url": _oa_pdf_url(result),
            "citation_count": result.get("cited_by_count"),
            "source": "openalex",
        })
    return papers


if __name__ == "__main__":  # quick manual smoke test
    import json
    for p in search("froth flotation collector reagent", max_results=3, mailto="you@example.com"):
        print(json.dumps({k: p[k] for k in ("doi", "title", "year", "pdf_url")}, ensure_ascii=False))
