import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import redis as redis_lib
import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from app import openalex
from app.config import settings
from app.europepmc import europepmc_pdf_url_for_doi
from app.fetcher import FetchError, fetch_article
from app.pdf_validate import validate_pdf as _validate_pdf
from app.storage import StorageClient
from app.url_guard import UnsafeUrlError, safe_get

# Surface operational INFO logs (download outcomes, "via EuropePMC", "completed for DOI").
# Without this the app logger inherits the WARNING root level under uvicorn, hiding every
# successful-fetch line and making the EuropePMC fallback look like it never fires.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

app = FastAPI(title="article-fetcher")

redis_client = redis_lib.from_url(settings.redis_url, decode_responses=True)
storage = StorageClient(
    endpoint_url=settings.minio_endpoint,
    access_key=settings.minio_access_key,
    secret_key=settings.minio_secret_key,
    bucket=settings.minio_bucket,
    region=settings.minio_region,
    public_endpoint=(settings.minio_public_endpoint or None),
)

JOB_TTL = settings.job_retention_days * 24 * 3600  # default 365 days in seconds


class FetchRequest(BaseModel):
    doi: str
    conversation_id: Optional[str] = None
    # Optional direct-download PDF URL (e.g. arXiv pdf_url). When set, the PDF is fetched
    # directly from this URL instead of the DOI/Sci-Hub path. Additive — DOI-only callers
    # omit it and keep the existing scidownl/Sci-Hub behavior.
    url: Optional[str] = None


class JobResponse(BaseModel):
    job_id: str
    status: str
    url: Optional[str] = None
    error: Optional[str] = None


@app.on_event("startup")
def on_startup():
    storage.ensure_bucket()
    # NOTE: the in-process headless warm-up was removed. It ran in a daemon thread,
    # but the stealth-browser solve now runs in a child process (Playwright's sync
    # API needs a main thread) — an in-process warm-up could not exercise that path
    # and would just hang. With the subprocess + hard timeout, a cold start only
    # costs the first real fetch up to the bounded ``headless_fetch_timeout``.


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/fetch", status_code=202, response_model=JobResponse)
def post_fetch(req: FetchRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "doi": req.doi,
        "conversation_id": req.conversation_id,
        "status": "pending",
        "object_key": None,
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    redis_client.set(f"job:{job_id}", json.dumps(job), ex=JOB_TTL)
    background_tasks.add_task(_run_fetch, job_id, req.doi, req.conversation_id, req.url)
    logger.info("Queued fetch job %s for DOI %s", job_id, req.doi)
    return JobResponse(job_id=job_id, status="pending")


@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str):
    raw = redis_client.get(f"job:{job_id}")
    if raw is None:
        raise HTTPException(status_code=404, detail="Job not found")

    job = json.loads(raw)
    url = None
    if job["status"] == "done" and job.get("object_key"):
        url = storage.presign_url(job["object_key"])

    return JobResponse(
        job_id=job["job_id"],
        status=job["status"],
        url=url,
        error=job.get("error"),
    )


@app.post("/fetch/sync")
def post_fetch_sync(req: FetchRequest):
    """Synchronous fetch: fetch -> validate -> store -> presign, all inline (no background task).

    Full-coverage chain via `_fetch_pdf_bytes` (same as the async path): honors `req.url`
    (direct-URL fast path, e.g. an OA `pdf_url` from `/search`) then EuropePMC + the DOI chain.
    Returns {"doi", "object_key", "url"} where url is a presigned GET. Raises 502 on failure.
    """
    try:
        pdf_bytes = _fetch_pdf_bytes(req.doi, req.url)
    except FetchError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not pdf_bytes:
        raise HTTPException(status_code=502, detail=f"No PDF found for DOI {req.doi}")
    object_key = f"{uuid.uuid4()}.pdf"
    storage.upload_pdf(object_key, pdf_bytes)
    logger.info("Sync fetch completed for DOI %s -> %s", req.doi, object_key)
    return {"doi": req.doi, "object_key": object_key, "url": storage.presign_url(object_key)}


@app.get("/resolve")
def resolve(title: str):
    """Resolve a paper title to its DOI via OpenAlex. Returns {"doi", "title", "year"} of
    the top hit, or 404 if there is no result / the top hit has no DOI."""
    results = openalex.search(title, max_results=1)
    if not results or not results[0].get("doi"):
        raise HTTPException(status_code=404, detail=f"No DOI found for title: {title!r}")
    top = results[0]
    return {"doi": top["doi"], "title": top.get("title"), "year": top.get("year")}


@app.get("/search")
def search(query: str, max_results: int = 5):
    """OpenAlex keyword search. Returns {"results": [...normalized paper dicts...]}."""
    return {"results": openalex.search(query, max_results)}


def _update_job(job_id: str, **kwargs) -> None:
    raw = redis_client.get(f"job:{job_id}")
    if not isinstance(raw, (str, bytes, bytearray)):
        return
    job = json.loads(raw)
    job.update(kwargs)
    redis_client.set(f"job:{job_id}", json.dumps(job), ex=JOB_TTL)


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _download_pdf_via_curl_cffi(url: str) -> bytes:
    """Second-chance direct download using curl_cffi's browser TLS+HTTP2 impersonation.

    Defeats Cloudflare gating that only fingerprints the client's TLS/JA3 + HTTP2
    (which a plain `requests` GET fails regardless of User-Agent). It does NOT solve a
    JS challenge — sites that require one still need a real browser. Optional dependency:
    raises ImportError (handled by the caller) when curl_cffi is not installed, so the
    fetcher behaves exactly as before until the dep is built into the image.
    """
    from curl_cffi import requests as cffi_requests  # optional dep; ImportError handled by caller

    # SSRF guard: validate the URL + every redirect hop before fetching.
    try:
        resp = safe_get(cffi_requests.get, url, impersonate="chrome", timeout=40)
    except UnsafeUrlError as exc:
        raise FetchError(f"Blocked unsafe URL {url}: {exc}") from exc
    return _validate_pdf(url, resp.status_code, resp.content, resp.headers.get("content-type", ""))


def _download_pdf_from_url(url: str) -> bytes:
    """Download a PDF directly from a URL (e.g. arXiv pdf_url, EuropePMC pdf=render,
    or an OA publisher link).

    Tries a plain requests GET (browser UA) first; if that is blocked (commonly a
    Cloudflare TLS-fingerprint 403 that a plain client can't pass) it retries once with
    curl_cffi browser impersonation. Raises FetchError if both fail, so _run_fetch falls
    back to the DOI/Sci-Hub path. curl_cffi is an OPTIONAL dependency — when it is not
    installed the curl_cffi attempt is skipped and behavior is identical to before.
    """
    try:
        # SSRF guard: validate the URL + every redirect hop before fetching.
        # An UnsafeUrlError becomes a FetchError so _run_fetch treats it as a
        # failed direct URL (it does NOT fall through to fetching it another way).
        try:
            resp = safe_get(requests.get, url, timeout=35, stream=False,
                            headers=_BROWSER_HEADERS)
        except UnsafeUrlError as unsafe_err:
            raise FetchError(f"Blocked unsafe URL {url}: {unsafe_err}") from unsafe_err
        return _validate_pdf(url, resp.status_code, resp.content,
                            resp.headers.get("content-type", ""))
    except FetchError as plain_err:
        try:
            content = _download_pdf_via_curl_cffi(url)
            logger.info("Direct URL %s fetched via curl_cffi impersonation after plain GET failed", url)
            return content
        except ImportError:
            cffi_err: Exception = plain_err  # curl_cffi not installed -> behave exactly as before
        except Exception as exc:
            cffi_err = exc

        # Third tier: stealth headless browser to execute an Akamai/Cloudflare JS
        # challenge that curl_cffi cannot solve. Flag-gated and lazily imported so
        # that when disabled (default) behavior is byte-for-byte identical: no
        # import, no browser, the original errors propagate as before.
        if settings.headless_fetch_enabled:
            try:
                from app.headless_downloader import download_pdf_via_headless

                content = download_pdf_via_headless(url)
                logger.info(
                    "Direct URL %s fetched via headless stealth browser after curl_cffi failed", url
                )
                return content
            except FetchError as headless_err:
                raise FetchError(
                    f"Direct URL failed via plain GET ({plain_err}), curl_cffi ({cffi_err}) "
                    f"and headless ({headless_err}) for {url}"
                ) from headless_err

        if cffi_err is plain_err:
            raise plain_err  # curl_cffi not installed + headless off -> behave exactly as before
        raise FetchError(
            f"Direct URL failed via plain GET ({plain_err}) and curl_cffi ({cffi_err}) for {url}"
        ) from cffi_err


def _europepmc_or_scihub(doi: str) -> bytes:
    """Try EuropePMC (bot-free OA PMC fulltext) before the Sci-Hub last resort.

    Gold-OA publishers (MDPI, Hindawi, ...) 403 even via curl_cffi and their DOIs
    aren't on Sci-Hub, but most have a PMC fulltext EuropePMC serves with no bot
    wall. If the EuropePMC URL is unavailable or its download fails, fall through
    to ``fetch_article(doi)`` (OpenAlex OA / Sci-Hub / STC).
    """
    epmc_url = europepmc_pdf_url_for_doi(doi)
    if epmc_url:
        try:
            pdf_bytes = _download_pdf_from_url(epmc_url)
            logger.info("Fetched %d bytes via EuropePMC %s (DOI %s)", len(pdf_bytes), epmc_url, doi)
            return pdf_bytes
        except Exception as epmc_err:
            logger.warning("EuropePMC %s failed (%s) for DOI %s; falling back to Sci-Hub",
                           epmc_url, epmc_err, doi)
    return fetch_article(doi)


def _fetch_pdf_bytes(doi: str, url: Optional[str] = None) -> bytes:
    """Full fetch chain shared by the async job (`_run_fetch`) and the sync endpoint (`/fetch/sync`).

    With ``url``: direct-URL fast path (plain GET -> curl_cffi -> optional headless), falling
    back to the DOI chain on failure. Without ``url``: EuropePMC -> OpenAlex-OA -> SciDB ->
    Sci-Hub -> STC (via ``_europepmc_or_scihub``). Raises FetchError if every tier fails.
    """
    if url:
        try:
            pdf_bytes = _download_pdf_from_url(url)
            logger.info("Fetched %d bytes via direct URL %s (DOI %s)", len(pdf_bytes), url, doi)
            return pdf_bytes
        except Exception as direct_err:
            logger.warning("Direct URL %s failed (%s) for DOI %s; falling back to EuropePMC/Sci-Hub",
                           url, direct_err, doi)
    return _europepmc_or_scihub(doi)


def _run_fetch(
    job_id: str,
    doi: str,
    conversation_id: Optional[str] = None,
    url: Optional[str] = None,
) -> None:
    _update_job(job_id, status="running")
    try:
        pdf_bytes = _fetch_pdf_bytes(doi, url)
        object_key = f"{job_id}.pdf"
        storage.upload_pdf(object_key, pdf_bytes)
        _update_job(job_id, status="done", object_key=object_key)
        logger.info("Job %s completed for DOI %s", job_id, doi)
        if settings.article_processor_webhook_url:
            if not conversation_id:
                logger.warning("Job %s: skipping webhook — no conversation_id provided", job_id)
            else:
                try:
                    requests.post(
                        settings.article_processor_webhook_url,
                        json={"job_id": job_id, "doi": doi, "object_key": object_key, "conversation_id": conversation_id},
                        timeout=settings.webhook_timeout,
                    )
                    logger.info("Webhook fired for job %s", job_id)
                except Exception:
                    logger.warning("Webhook POST failed for job %s", job_id, exc_info=True)
    except FetchError as e:
        _update_job(job_id, status="failed", error=str(e))
        logger.warning("Job %s failed for DOI %s: %s", job_id, doi, e)
    except Exception as e:
        _update_job(job_id, status="failed", error=str(e))
        logger.exception("Unexpected error in job %s", job_id)
