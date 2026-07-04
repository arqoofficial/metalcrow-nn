# Article Fetcher Service

Queues and executes article downloads from Sci-Hub by DOI, stores PDFs in MinIO, then notifies PDF Parser.

## Dev Commands

```bash
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8200 --reload
```

## Data Flow

```
POST /fetch {doi, conversation_id}  ← backend API
    ↓
Redis queue (job tracking)
    ↓
curl-based Sci-Hub scraper (tries mirrors: sci-hub.ru, sci-hub.ee)
    ↓
MinIO articles bucket  →  POST http://pdf-parser:8300/jobs (webhook)
```

## Project Structure

```
app/
├── main.py     # FastAPI — POST /fetch, GET /jobs/{job_id}
├── fetcher.py  # curl subprocess scraper — downloads PDF by DOI from Sci-Hub mirrors
├── storage.py  # MinIO client (articles bucket)
├── config.py   # Settings
└── schemas.py  # Request/response models
```

## Gotchas

- **No scidownl** — `fetch_article` tries `download_pdf_via_openalex` (httpx) FIRST, then falls back to `subprocess.run(["curl", ...])` against Sci-Hub mirrors. The old `tests/test_fetcher.py` references a non-existent `scihub_download` symbol and is dead code (pre-existing, pre-refactor).
- **`conversation_id` is required for RAG to work.** If omitted from `POST /fetch`, the pdf-parser webhook is silently skipped (logged as warning, no error returned). RAG ingestion will never fire. Always pass `conversation_id` when DOI is fetched in a conversation context.
- PDF Parser webhook URL must be `http://pdf-parser:8300/jobs` (Docker internal) — never localhost
- MinIO bucket is `articles` — must exist before service starts (created by MinIO init in compose)
- Job status is tracked in Redis — use the same Redis instance as backend (configured via `REDIS_URL`)
- Sci-Hub downloads can be slow or fail for some DOIs — `FetchError` is raised and the job status is set to FAILED
- **Container has httpx 0.28.1 and redis 5.3.1** (transitive deps of fastapi[standard]) — used directly for OpenAlex JSON + PDF GET; pytest is preinstalled, no pip install needed.
- **article-fetcher is NOT bind-mounted:** the Dockerfile only `COPY`s `app/` (not `tests/`). `scripts/test-in-container.sh` only supports ai-agent and backend; for article-fetcher use the manual recipe: `cid=$(docker compose ps -q article-fetcher); docker cp app/. "$cid":/app/app/` and `docker cp tests "$cid":/app/tests`, then `docker exec -w /app "$cid" python -m pytest tests/` (plain `docker exec`, NOT `-T` — see below). GOTCHA: if `/app/tests` already exists, `docker cp tests <cid>:/app/tests` nests into `/app/tests/tests` — `rm -rf tests` in-container first.
- **OpenAlex daily content-download cap tracked in Redis:** `openalex_content_downloads:{YYYYMMDD}` (INCR on success + EXPIRE ~2 days). If Redis unavailable, cap is treated as NOT exceeded (allow + WARNING) — blocking would break fetching entirely.
- **Direct PDF URL fallback with curl_cffi impersonation:** `_run_fetch` attempts optional `url` param (e.g., arXiv `pdf_url`) FIRST via `_download_pdf_from_url` (plain `requests.get` with browser UA); on `FetchError` (non-200/non-PDF), retries via `_download_pdf_via_curl_cffi` (curl_cffi impersonates real browser TLS+JA3+HTTP2, defeating fingerprint-gated Cloudflare like MDPI); on either failure, falls back to `fetch_article(doi)` (Sci-Hub path). curl_cffi is OPTIONAL — import is inside the retry fn, so `ImportError` re-raises the original plain-GET error, keeping behavior identical to before when not installed. curl_cffi does NOT solve JS challenges (still needs real browser). Scope: transport errors (timeout/ConnectionError) propagate uncaught to `_run_fetch`'s `except Exception` → Sci-Hub, as before.
- **OA PDF URL source precedence:** OpenAlex sends `pdf_url` (empty if no OA available); preference in `backend/app/api/routes/internal.py` is EuropePMC `?pdf=render` (regex PMC# from any location field) → `best_oa_location.pdf_url` (publisher OA). EuropePMC serves OA PDFs with no bot wall (unlike the publisher's own Cloudflare-gated CDN links).
- **Cloudflare `cf_clearance` cookies are NOT portable:** bound to solver's IP + UA + TLS fingerprint, so copying a browser's cookie into server-side client fails 403. Curl_cffi impersonates the fingerprint to defeat TLS-level gating, but won't make a stolen cookie work.
- **`docker exec` does NOT support `-T` flag** — that's `docker compose exec` only. Use `docker compose ps -q article-fetcher` to get the container ID for plain `docker exec`/`docker cp` commands.
- **`requests` is available in-container** (transitive of httpx/curl_cffi) — imports fine without adding a dep.
- **GECK `search_documents` result shape varies by version** — optional-import STC code must defensively handle list / `{documents:[...]}` / collector_output nesting AND the scored-doc `document` wrapper. `_first_document` handles all recognized shapes and returns None for unrecognized ones; caller's broad `except Exception` is the ultimate safety net.
- **EuropePMC fallback for bot-walled gold-OA publishers:** MDPI/Hindawi/Frontiers return 403 even via curl_cffi and aren't on Sci-Hub. Rescue: EuropePMC serves `?pdf=render` by DOI→PMCID; placed in `_europepmc_or_scihub()` between direct-URL failure and Sci-Hub, gate on `inEPMC=="Y"` + `hasPDF=="Y"` (406/404 otherwise). DOI→PMCID resolver: `https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:<bare-doi>&format=json&resultType=lite`. Reuse `_normalize_doi()` (strips scheme/host) and `_download_pdf_from_url()` for fetch. **Log-level trap:** article-fetcher main.py inherited `logging.basicConfig(level=logging.INFO)` fix so success logs are visible — missing this, EuropePMC lookups appear silent even when firing.
- **Live-verified EuropePMC DOIs:** `10.3390/ijms27073138`→PMC13073458, `10.1155/2012/848093`→PMC3546525, `10.3390/ijms23020585`→PMC8776015 (all `inEPMC:Y, hasPDF:Y, isOpenAccess:Y`).
- **Optional-import pattern for stc_geck:** import the client INSIDE the async function (not at module top), so sync wrapper can catch `ImportError` separately and log "dep missing" without failing the whole downloader module.
- **Pre-existing test failures in article-fetcher suite (NOT STC regressions):** `test_fetcher.py` (3 tests) reference removed `scihub_download` symbol — dead code from pre-refactor. `test_direct_url_fetch.py::test_run_fetch_direct_url_non_pdf_fails` asserts `_run_fetch` does NOT fall back to `fetch_article` on non-PDF URL, but current code DOES fall back; failure is about main.py logic, not fetcher.py changes. Test patches `fetch_article` as MagicMock so recent edits cannot affect it. Full suite: 4 failed / 49 passed; STC wiring adds no regressions.
- **SSRF guard via `assert_public_http_url`:** validates URL before fetch, checks DNS resolution to reject loopback/private IPs. `socket.getaddrinfo(host, None)` on IP-literals returns IP directly without DNS, so per-IP denylist works in unit tests. **Residual:** DNS-rebinding TOCTOU — guard resolves at check-time, fetch lib re-resolves at call-time (separate lookups); pinned-IP transport needed for full mitigation (not implemented, acceptable for user-sourced URLs).
- **curl subprocess redirect hops (`--max-redirs 5` — raised from 5→10 in `_CURL_BASE`) are NOT individually re-validated** — residual SSRF on Sci-Hub mirror fallback path only. Plain httpx GET (direct URL path) validates before-fetch; curl fallback accepts redirects without per-hop validation (low risk: Sci-Hub is attacker-unlikely). Standard practice: first-hop validated, redirects trusted on that domain. Max-redirs must be kept in sync with `safe_get` default (also 5→10); both are in `fetcher.py`.
- **`_DOI_RE` only matches bare `10.NNNN/...` but callers pass full resolver URLs** — `https://doi.org/10.1234/...` or `doi:10.1234/...` to fallback Sci-Hub path. `_normalize_doi()` strips scheme/host case-insensitively but slices from ORIGINAL string to preserve DOI suffix case (10.1234/Ab.Cd.5 → 10.1234/Ab.Cd.5, not lowercase).
