# Cyberleninka RU Search (EN/RU split) Implementation Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Add a Russian literature-search tool `literature_search_ru` backed by
cyberleninka.ru alongside the OpenAlex tool (renamed `literature_search_en`),
with separate per-tool caps. Cyberleninka returns full text inline, so RU papers
skip the entire download/fetch cascade.

**Confirmed facts (live-verified 2026-07-04 — do not re-derive):**
- Search: `POST https://cyberleninka.ru/api/search` body
  `{"mode":"articles","q":"<query>","size":N,"from":0}` → `{found, articles[]}`.
  Open, no auth. `found` runs to hundreds → request only top-N via `size`.
- Each article: `name` (title, contains `<b>` tags), `annotation` (abstract,
  `<b>` tags), `authors` (a STRINGIFIED python list, e.g. `"['A', 'B']"`),
  `year` (str), `journal`, `link` (`/article/n/<slug>` — prefix
  `https://cyberleninka.ru`), **`ocr`** (a LIST of text fragments — but only a
  ~750-char PREVIEW, NOT the full text). **No DOI.**
- **Full text = article-PAGE fetch (CORRECTION).** The search `ocr` is a
  preview; the FULL article (~9–55k chars) is on the article page in
  `<div class="ocr" itemprop="articleBody">` as `<p>` paragraphs. The fetcher's
  `cyberleninka.fetch_fulltext(url)` retrieves it; `search(with_fulltext=True)`
  (used by `/search_ru`) fetches the top-N pages in PARALLEL and fills
  `fulltext` (preview kept as fallback). So `/search_ru` returns papers with
  FULL text — RU `LiteraturePaper` rows are still created fulltext-ADDED at
  search time (no async wait), the fetch just happens inside `/search_ru`.
  `/pdf` URLs 404. Plain `requests` page fetch works reliably; an
  invisible-playwright fallback is added for pages that fail/empty.
- Proxy: only **socks5 `37.16.81.138:1080`** of the 5 given works; direct also
  works. Client tries socks5, falls back to direct.

**Global constraints:**
- Cyberleninka papers are DOI-less. **Dedup gap (must fix):** the current
  `_dedup_by_doi` keeps EVERY `doi is None` paper — so the SAME cyberleninka
  paper returned by two RU searches in a turn would be read TWICE. Fix by
  generalizing the shared helper (`litsearch._dedup_by_doi`, used by
  `agent_continue`, the read tool, the route, and chat.py's paper_count) to key
  on `doi` (lowercased) when present, else a normalized title
  (`" ".join(title.lower().split())`), else keep (both empty). Rename it
  `_dedup_papers` and update all call sites. This preserves EN behavior (DOI
  papers) and correctly collapses duplicate RU papers across searches, while a
  mixed EN(DOI)/RU(title) union still dedups each side properly.
- RU `LiteraturePaper` rows are created with `fulltext_text` = joined ocr,
  `fulltext_status=ADDED`, `fetch_status=SKIPPED` (nothing to fetch) — so they
  are terminal on creation and Phase B reads them with no download wait.
- Keep the OpenAlex tool's behavior identical, only renamed to
  `literature_search_en`. Persisted `LiteratureSearch` rows unchanged (add a
  `source`? NO — avoid migration; language is implicit in which tool ran).
- Never run pytest against the live `app` DB — use the scratch DB per the repo
  convention (see docs/litsearch-findings.md test note).

---

### Task A: article-fetcher cyberleninka client + `/search_ru`

**Files:** Create `services/article-fetcher/app/cyberleninka.py`; modify
`services/article-fetcher/app/main.py`, `.../config.py`; compose env passthrough.

- `cyberleninka.search(query, max_results, *, proxy_url=None, timeout=15.0) -> list[dict]`:
  POST the search API (via socks5 proxy if `proxy_url`, else direct; on proxy
  failure retry direct once). Normalize each of the first `max_results` articles:
  `{doi: None, title: strip(name), authors: ", ".join(parsed authors) or "Unknown",
    year: int(year) if year else None, abstract: strip(annotation),
    fulltext: "\n".join(ocr fragments, stripped), url: "https://cyberleninka.ru"+link,
    citation_count: None, source: "cyberleninka"}`. Strip `<b>`/HTML via a small
  regex. Parse `authors` with `ast.literal_eval` guarded by try/except → []. Never
  raise — log + return [] on any failure (mirror `openalex.search`).
- `config.py`: `cyberleninka_api_base="https://cyberleninka.ru/api"`,
  `cyberleninka_proxy_url="socks5h://37.16.81.138:1080"` (empty disables).
- `main.py`: `GET /search_ru?query=&max_results=5` →
  `{"results": cyberleninka.search(query, max_results, proxy_url=settings.cyberleninka_proxy_url or None)}`.
- Proxy transport: **use `requests` for the cyberleninka client** — the image
  has `requests` + `socks` (PySocks) which supports socks5 via
  `proxies={"https": "socks5h://37.16.81.138:1080"}`. httpx does NOT work here
  (its `socksio` extra is missing). On a proxy failure, retry once direct
  (`proxies=None`). Verify BOTH paths live against real cyberleninka.
- Tests: unit test `cyberleninka.search` against a MOCKED `requests.post`
  response. Do NOT depend on `/home/claude/a2a-shared/...` (outside repo &
  container) — inline a SMALL trimmed article dict as the fixture (2 articles,
  one with `<b>` tags + a 3-item `ocr` list + stringified authors). Assert:
  `<b>`/HTML stripped from title/abstract, authors parsed from the stringified
  list, `ocr` joined into `fulltext`, `doi is None`, `url` prefixed, and top-N
  crop (`size`). Add a live smoke check (real API) in the report, not the test.
- **Deploy:** article-fetcher runs a BAKED image (no bind mount) →
  `docker compose up -d --build article-fetcher` to deploy (controller does this
  after review, not the subagent).
- Commit.

### Task B: litsearch RU tool + client + inline-fulltext rows + EN rename

**Files:** `backend/app/services/litsearch_client.py`,
`backend/app/services/litsearch_tools.py`; tests.

- `litsearch_client.search_ru(query, max_results) -> list[dict]`: GET
  article-fetcher `/search_ru` (mirror existing `search`). Returns the normalized
  dicts (with `fulltext`).
- `litsearch_tools`:
  - Rename the OpenAlex tool's schema `name` `"litsearch_search"` →
    `"literature_search_en"` (keep the handler + `make_search_tool`; update the
    description). Update `run_loop`/Phase-B references and the grounded-tag check
    if any string-match the old name.
  - Add `SEARCH_RU_SCHEMA` (name `"literature_search_ru"`, RU-focused description:
    "Русскоязычная научная литература (Cyberleninka). Запрос — короткая фраза на
    русском.") + `litsearch_search_ru(session, chat_session_id, *, query, round, followup_of)`:
    calls `litsearch_client.search_ru`, creates the `LiteratureSearch` +
    `LiteraturePaper` rows, but each paper row is created with
    `fulltext_text=<ocr>`, `fulltext_status=ADDED`, `fetch_status=SKIPPED` (no
    `fetch_async`). Sets `search.stage = LitStage.FETCHING` still.
    **VERIFY (critical):** `reconcile` / the Phase-B heartbeat must treat
    `FetchStatus.SKIPPED` as TERMINAL, so a turn's RU papers don't stall the
    `all_terminal` wait forever. Read `reconcile` in `litsearch.py`; if SKIPPED
    is not already terminal there, make it so (an RU-only turn must reach the
    read phase without a download wait). Returns the same abstract payload shape
    as `litsearch_search` (idx/title/authors/year/doi/abstract).
  - `make_search_ru_tool(*, round, followup_of)` mirroring `make_search_tool`.
- Depends on the `_dedup_papers` rename (Global constraints) — do that rename in
  this task if Task A didn't, so RU duplicates collapse by title.
- Tests: `litsearch_search_ru` creates rows with ADDED fulltext + SKIPPED fetch;
  an RU-only turn's papers are all terminal (no download wait); duplicate RU
  papers across two searches dedup by title; the renamed EN tool still works.
- Commit.

### Task C: per-tool caps + Phase A wiring + EN/RU prompt

**Files:** `backend/app/services/agent/loop.py`, `backend/app/services/chat.py`,
`backend/app/core/config.py`; tests.

- `loop.py`: generalize the success cap to per-tool. Add
  `max_successful_by_tool: dict[str, int] | None = None`; track
  `successful_by_tool: dict[str,int]`. A search tool-result counts as successful
  for ITS tool name when it returns a non-empty `papers`. Force the answer when
  ANY tool reaches its cap OR total attempts hit `max_tool_calls`. Keep the
  existing single `max_successful_searches` working (or migrate chat.py to the
  dict form). The generic loop must not hardcode tool names — derive from the
  tool-call name.
- `config.py`: `LITSEARCH_MAX_SEARCHES_EN: int = 3`, `LITSEARCH_MAX_SEARCHES_RU: int = 3`
  (keep `LITSEARCH_MAX_SEARCHES` as a fallback/default).
- `chat.py` Phase A: register BOTH `make_search_tool(round=0)` (en) and
  `make_search_ru_tool(round=0)`; pass ONLY
  `max_successful_by_tool={"literature_search_en": settings.LITSEARCH_MAX_SEARCHES_EN,
   "literature_search_ru": settings.LITSEARCH_MAX_SEARCHES_RU}` and do NOT pass
  the combined `max_successful_searches` (else it would cap en+ru TOGETHER and
  defeat the separate caps the user asked for — en and ru each get their own,
  total up to en+ru). Keep `max_tool_calls=settings.LITSEARCH_MAX_SEARCH_ATTEMPTS`
  as the overall attempts ceiling. Keep the `max_successful_searches` param in
  `run_loop` (existing test_agent_loop tests use it) — just unused by chat.py.
  Update
  `_LITSEARCH_SYSTEM_PROMPT`: tell the model to use `literature_search_ru` for
  Russian-language / Russian-practice topics and `literature_search_en` for
  international literature, and to use BOTH when useful (the turn-union merges
  them). `first_tool_choice` when primed → still a search (pick en or leave auto).
- Tests: Phase A registers both tools; per-tool cap forces the answer; a turn
  mixing en+ru groups under one anchor and the union reads both.
- Commit.

### Task D (Step 3): subagent prompt-tuning via proxied probing

After A–C deploy: dispatch **exactly two probing subagents, one per engine**,
both routing their HTTP through socks5 `37.16.81.138:1080` (fallback direct):
- **Subagent 1 — OpenAlex only.** Tries many query formulations against
  OpenAlex (`api.openalex.org/works`, with the key) for representative
  metallurgy questions; records which phrasings return on-topic TOP hits;
  proposes OpenAlex-specific query-construction tips (canonical English domain
  terms; avoid "deep"→deep-sea collisions; one concept per query; no geography).
- **Subagent 2 — Cyberleninka only.** Same, against
  `cyberleninka.ru/api/search`; proposes cyberleninka-specific tips (natural
  Russian noun phrases; domain terms in Russian).

Run them in parallel (independent engines). Then synthesize both reports and
fold the winning per-engine guidance into `_LITSEARCH_SYSTEM_PROMPT`'s
query-construction section (one block per tool: `literature_search_en` vs
`literature_search_ru`).
