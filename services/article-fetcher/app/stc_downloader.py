"""STC / Nexus (libstc.cc) IPFS PDF source — optional, default-off last resort.

When ``settings.stc_enabled`` is True this resolves a DOI to a PDF via the
Nexus/STC Summa index over IPFS, served through a local Kubo gateway
(``settings.ipfs_gateway_url``). It is the final fallback in
``fetcher.fetch_article``, tried only after OpenAlex OA and the Sci-Hub mirrors.

Design mirrors the optional ``curl_cffi`` path in ``main.py``:
- ``libstc-geck`` is imported LAZILY inside ``_run_stc_query`` so the image
  builds and runs without it; a missing dep degrades to ``None``.
- Every failure mode (disabled, missing dep, no gateway, no PDF link, bad
  bytes, any exception) returns ``None``. ``download_pdf_via_stc`` NEVER raises,
  so a flaky STC path can never regress a fetch the legacy chain could serve.
"""
import asyncio
import logging
from typing import Optional

import requests

from app.config import settings

logger = logging.getLogger(__name__)


def _extract_pdf_cid(links) -> Optional[str]:
    """Pick the CID of the first link whose extension is 'pdf'."""
    if not links:
        return None
    try:
        for link in links:
            if isinstance(link, dict) and (link.get("extension") or "").lower() == "pdf":
                cid = link.get("cid")
                if cid:
                    return str(cid)
    except Exception:
        return None
    return None


def _fetch_ipfs_bytes(cid: str) -> bytes:
    """GET the object bytes from the local IPFS gateway."""
    url = "%s/ipfs/%s" % (settings.ipfs_gateway_url.rstrip("/"), cid)
    resp = requests.get(url, timeout=settings.stc_timeout)
    resp.raise_for_status()
    return resp.content


async def _search_pdf_cid_async(doi: str) -> Optional[str]:
    """Run a Summa search for the DOI and return the PDF link's CID, or None.

    Imports libstc-geck lazily; raises ImportError if the dep is absent (the
    caller treats that as a degrade-to-None, not a failure).
    """
    from stc_geck.client import StcGeck  # optional dep; ImportError handled by caller

    geck = StcGeck(ipfs_http_base_url=settings.ipfs_gateway_url, timeout=settings.stc_timeout)
    await geck.start()
    try:
        client = geck.get_summa_client()
        result = await client.search_documents({
            "index_alias": settings.stc_index_alias,
            "query": {"match": {"value": "uris:doi:%s" % doi}},
            "collectors": [{"top_docs": {"limit": 1}}],
        })
        doc = _first_document(result)
        if doc is None:
            return None
        return _extract_pdf_cid(doc.get("links"))
    finally:
        try:
            await geck.stop()
        except Exception:
            logger.warning("STC geck.stop() failed", exc_info=True)


def _first_document(result) -> Optional[dict]:
    """Defensively pull the first scored document dict out of a Summa result.

    Handles the common Summa shapes (a list of docs, a dict with 'documents',
    or a 'collector_outputs' collector_output.documents) and returns None for
    shapes it recognizes as empty. The caller's broad except in
    ``download_pdf_via_stc`` remains the ultimate safety net.
    """
    if result is None:
        return None
    # list of docs
    if isinstance(result, list):
        first = result[0] if result else None
    elif isinstance(result, dict):
        docs = result.get("documents")
        if docs is None:
            # collector_output -> [{collector_output: {documents: [{document: {...}}]}}]
            collectors = result.get("collector_outputs") or result.get("collectors")
            docs = None
            if isinstance(collectors, list):
                for c in collectors:
                    td = (c or {}).get("collector_output", c) if isinstance(c, dict) else None
                    if isinstance(td, dict) and td.get("documents"):
                        docs = td["documents"]
                        break
        first = docs[0] if isinstance(docs, list) and docs else None
    else:
        return None
    if first is None:
        return None
    # a scored doc may wrap the payload under 'document'
    if isinstance(first, dict) and "document" in first and isinstance(first["document"], dict):
        return first["document"]
    return first if isinstance(first, dict) else None


def _run_stc_query(doi: str) -> Optional[str]:
    """Sync wrapper over the async Summa search. Returns the PDF CID or None.

    Runs in article-fetcher's background-task thread where no event loop is
    running, so asyncio.run is safe.
    """
    return asyncio.run(_search_pdf_cid_async(doi))


def download_pdf_via_stc(doi: str) -> Optional[bytes]:
    """Resolve a DOI to PDF bytes via STC/Nexus over IPFS, or None.

    Inert unless settings.stc_enabled is True. Never raises.
    """
    if not settings.stc_enabled:
        return None
    try:
        cid = _run_stc_query(doi)
    except ImportError:
        logger.info("STC enabled but libstc-geck not installed; skipping STC for DOI %s", doi)
        return None
    except Exception:
        logger.warning("STC query failed for DOI %s; skipping STC", doi, exc_info=True)
        return None

    if not cid:
        logger.info("STC: no PDF link found for DOI %s", doi)
        return None

    try:
        content = _fetch_ipfs_bytes(cid)
    except Exception:
        logger.warning("STC: IPFS fetch failed for CID %s (DOI %s)", cid, doi, exc_info=True)
        return None

    if not content or not content.startswith(b"%PDF"):
        logger.warning("STC: object for DOI %s is not a valid PDF", doi)
        return None

    logger.info("Fetched %d bytes for DOI %s via STC/Nexus IPFS", len(content), doi)
    return content
