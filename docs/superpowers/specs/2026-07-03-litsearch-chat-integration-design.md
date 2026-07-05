# Literature-Search → Chat Integration — Design Spec (v2, post-grilling)

**Date:** 2026-07-03
**Branch:** `feature/litsearch-chat-integration` (forked from `osn-pre-main`)
**Status:** Revised after two adversarial reviews + a shipped-implementation cross-check.

## 1. Goal

Wire the existing `services/article-fetcher` (OpenAlex search + tiered PDF fetch — already in the
repo, currently **not** connected to the app) into the chat experience so the chat agent can run a
literature search, answer from abstracts, surface a side panel of found papers, quick-parse their
full text into the dialog, then read those full texts to produce a grounded final answer — looping
for more searches when the evidence is thin. Users can, at any time, push any found paper into the
permanent knowledge base (hard parse → graph + ontology).

## 2. Target pipeline (from the operator)

1. User asks a question (in the new **Литература** chat mode).
2. Agent initiates a literature search (OpenAlex via article-fetcher `/search`).
3. Search returns papers **with abstracts** (OpenAlex reconstructs them).
4. Agent replies immediately with what it can infer **from the abstracts** (LLM synthesis) — the
   first assistant message.
5. A **side panel** appears beside the chat listing the found papers (title, authors, year,
   abstract, citation count). Each card shows a **downloading spinner** while its PDF is fetched and
   an **«Добавить в базу»** button the user may click **at any time**; clicking sends that PDF to
   hard parsing and then ingests it into the graph + ontology.
6. A **light** PDF text extractor (pypdf — the pdftotext-equivalent) parses each downloaded PDF
   quickly; when a paper's text is ready its card shows a **«добавлено в диалог»** badge.
7. Once the light extraction of the batch finishes, the agent is prompted again to **read the full
   texts of any/all ready** PDFs (not limited to one; must not wait on stuck/failed ones).
8. The agent reads the full texts and presents its final answer. If it judges the evidence
   insufficient it may initiate **another** literature search (bounded), otherwise it stops.

## 3. Current-state facts (verified against code)

- **Chat** (`backend/app/api/routes/chat.py`, `services/chat.py`): FastAPI;
  `POST /api/v1/chat/sessions/{id}/messages` returns a single-event SSE carrying a
  `ChatMessageResponse` (requires `claims: list[Claim]` + `session_id: UUID`). `answer_message` is a
  deterministic waterfall — **no LLM in backend, no tool loop, no history feedback, single-shot.**
  Structured payloads ride on `ChatMessageResponse` and persist in `ChatMessage.message_metadata`.
- **Async model = Celery + Redis.** `backend/app/services/tasks.py` dispatches tasks **by name**
  (`celery_app.signature(name, args).apply_async()`); workers live in `workers/*`. There is **no**
  `BackgroundTasks` usage in the backend — Celery is the sanctioned mechanism.
- **Frontend** (`frontend/src/routes/_layout/chat.tsx`): React 19 + TanStack Query + shadcn/ui, **no
  streaming consumer** — updates are TanStack Query polling. Primitives exist (`Card`,
  `LoadingButton` spinner, `Badge`, `Skeleton`). The page also renders a **pinned "Agent response"
  card** bound to the last send-mutation result (chat.tsx:298) *in addition to* history — a
  double-render trap for two-answer flows. `modeUsedLabel`/`modeUsedVariant` (chat.tsx:38-51) have
  no `literature` entry.
- **article-fetcher** (`services/article-fetcher/app/main.py`): listens on **port 8200**
  (`Dockerfile:54/56`). `GET /search?query=&max_results=` → `{results:[{doi,title,authors,year,
  abstract,pdf_url,citation_count}]}`; `GET /resolve?title=`; async `POST /fetch {doi,url?,
  conversation_id?}` → `{job_id,status}` + `GET /jobs/{job_id}` → **`{job_id,status,url?,error?}`
  (NO `object_key`)**; `POST /fetch/sync` → `{doi,object_key,url}`. Async job stores its PDF at key
  **`{job_id}.pdf`** at bucket root (`main.py:289`); sync uses `{uuid4}.pdf`. Presigned `url`s point
  at `minio_public_endpoint` (defaults to an unreachable `localhost:9092`) → **the backend must read
  PDF bytes server-side, not via presigned URLs.** `/search` currently ignores the configured
  OpenAlex `mailto`/key (anonymous pool). The Dockerfile unconditionally downloads a headless
  browser (`invisible_playwright fetch`, ~100-400 MB) even though `HEADLESS_FETCH_ENABLED` is off.
  It POSTs an optional webhook `{job_id,doi,object_key,conversation_id}` on **success only**
  (`main.py:293`).
- **Hard parse + ingest**: `Document` (`experiments.documents`) needs `minio_key` + `filename`
  (+ optional `mime_type`, defaults `L0`). `POST /api/v1/ingest/upload` stores to app MinIO + creates
  a `Document`; `tasks.enqueue_l1_parse(task_id, document_ids)` dispatches Celery `parse.docling.parse`
  (the Docling "hard" slot — **currently a stub**), whose OKF output bridges to ontology
  (`ontology/ingest_bridge.py`) + Neo4j (`science-knowledge-graph`). App storage + parse-docling both
  `get_object(MINIO_BUCKET, minio_key)`, so a root-level `{job_id}.pdf` key is directly readable —
  **no PDF copy needed** once the key is known.
- **Light parse**: no `pdftotext` binary; **pypdf is NOT a backend dependency** (only `pdfplumber`
  in a sidecar). We add `pypdf` to the backend image and extract inside the Celery worker.

> Inherited pre-existing limitations (out of scope): Docling hard-parser + parts of the ontology
> extractor are stubs/mock; ingest runs end-to-end but is stub-quality until they're filled in. We
> wire the **real handles** so it lights up when they are, and surface ingest as *provisional* in the
> UI.

## 4. Architecture

**Driving principle (changed after review):** the pipeline is driven **server-side by Celery**, not
by client polling. The browser's job is only to *display* state and take user actions; closing the
panel never stalls steps 6-8.

### 4.1 article-fetcher as an internal sidecar

Add `article-fetcher` to `compose.yml` (internal-only, no published port), mirroring
`science-knowledge-graph`:

```yaml
article-fetcher:
  build: { context: ., dockerfile: services/article-fetcher/Dockerfile }
  environment:
    MINIO_ENDPOINT: http://minio:9000        # full scheme required by storage.py
    MINIO_BUCKET: ${MINIO_BUCKET}            # SAME bucket the backend/Docling read
    MINIO_ACCESS_KEY: ${MINIO_ROOT_USER}
    MINIO_SECRET_KEY: ${MINIO_ROOT_PASSWORD}
    MINIO_PUBLIC_ENDPOINT: http://minio:9000 # avoid the localhost:9092 dead presign default
    REDIS_URL: redis://redis:6379/0
    OPENALEX_MAILTO: ${OPENALEX_MAILTO:-}    # polite pool
  depends_on: { minio: {condition: service_healthy}, redis: {condition: service_healthy} }
```

Small **fetcher-code changes** (we own it):
- Add `object_key` to `JobResponse` and populate it in `GET /jobs/{id}` (makes the contract explicit
  instead of relying on the `{job_id}.pdf` convention). *(Backend also treats `{job_id}.pdf` as the
  fallback so this is belt-and-suspenders.)*
- Pass the configured `mailto`/key into `openalex.search` in `/search`.
- **Slim build for the sidecar**: guard the headless-browser download in the Dockerfile behind a
  build arg (default off) so folding it into root compose doesn't add hundreds of MB / a GitHub
  build-time dep for a disabled feature.

Backend reaches it via `ARTICLE_FETCHER_URL=http://article-fetcher:8200`.

### 4.2 Backend LLM client (new)

`backend/app/services/llm.py` — a thin OpenAI-compatible chat-completions client (httpx). Config
`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TIMEOUT`. **Graceful degradation:** unset
`LLM_BASE_URL` → helpers return `None` → callers emit a deterministic template (as the app already
degrades when a sidecar is down). `.env.example` default points at the existing OpenAI-compatible
proxy so e2e works. Helpers: `synthesize_from_abstracts(question, papers) -> str|None`;
`read_fulltexts(question, papers_with_text) -> {answer, sufficient, followup_query}|None` (JSON).

### 4.3 Persistence (new tables + Alembic migration)

`backend/app/models/litsearch.py`:

- **`LiteratureSearch`**: `id`, `session_id` (FK→chat_session CASCADE), `question`, `stage`
  (`LitStage`: `searching|fetching|reading|done|failed`), `round` (int, 0-based),
  `followup_of` (self-FK|None), `followup_search_id` (self-FK|None — set when a round spawns the
  next), `error` (str|None), `created_at`, `updated_at`.
- **`LiteraturePaper`**: `id`, `search_id` (FK CASCADE), `doi|None`, `title`, `authors`, `year|None`,
  `abstract`, `pdf_url|None`, `citation_count|None`,
  `fetch_status` (`pending|downloading|done|failed|skipped`), `fetch_job_id|None`, `object_key|None`,
  `fulltext_status` (`none|added|failed`), `fulltext_chars` (int, default 0),
  `ingest_status` (`none|queued|running|done|failed`), `ingest_task_id|None`,
  `document_id` (FK→documents|None, **UNIQUE** — DB-level add-to-DB idempotency),
  `created_at`, `updated_at`.

Full text is **not** stored in a column: it's read on demand from MinIO by the persisted
`object_key` (so the store/read key can never diverge) and capped at `LITSEARCH_FULLTEXT_CHAR_CAP`
(60 000). `fulltext_status=added ∧ fulltext_chars>0` = "text is available to the agent."

Terminal `fetch_status` = `{done, failed, skipped}`. A paper with **no `doi` and no `pdf_url`** is
created directly as `skipped` (never blocks the batch gate).

### 4.4 Orchestration (Celery-driven)

**New Celery worker `worker-litsearch`** built from the **backend image** (so it shares
`app.models`/`app.services`/the LLM client/storage), running
`celery -A app.worker.litsearch_app worker -Q litsearch`. Tasks registered in
`backend/app/worker/litsearch_tasks.py` (`@celery_app.task(name="litsearch.monitor" / ".synthesize")`).
The web process dispatches them by name via the existing producer `celery_app`.

- `services/litsearch_client.py` — httpx client to the fetcher (`search`, `resolve`, `fetch_async`,
  `job_status`), errors swallowed → `None`/`[]` (graceful degradation like `science_kg_client`).
- `services/litsearch.py` — pure, testable orchestration helpers:
  - `start_search(session, chat_session_id, question, *, round=0, followup_of=None) -> LiteratureSearch`
    (runs in the web request for round 0; called from the worker for rounds ≥1): fetcher `/search`;
    persist `LiteratureSearch(stage=searching)` + `LiteraturePaper` rows (no-DOI-no-URL → `skipped`);
    LLM abstract answer → persist assistant `ChatMessage` **#1** (step 4); for each fetchable paper
    fire fetcher `/fetch {doi, url: pdf_url, conversation_id: search_id}`, store `fetch_job_id`,
    `fetch_status=downloading`, `object_key={job_id}.pdf`; set `stage=fetching`; dispatch
    `litsearch.monitor(search_id)`.
  - Celery `litsearch.monitor(search_id, deadline_ts)` — the **server-side heartbeat** (self-reschedules
    with a short countdown, like the shipped `monitor_ingestion`): for each `downloading` paper query
    fetcher `/jobs/{id}`; on `done` → `object_key` (from response or `{job_id}.pdf`), download bytes
    from MinIO, **pypdf light-extract** (capped) → `fulltext_status=added`, `fetch_status=done`; on
    `failed` → both `failed`. When **now > deadline**, mark any still-`downloading` paper `failed`
    (per-batch `LITSEARCH_FETCH_TIMEOUT`, default 180 s → "read any/all ready", never hang). When all
    papers terminal (or deadline) and `stage==fetching`, transition via a **single guarded SQL
    UPDATE** (`UPDATE … SET stage='reading' WHERE id=… AND stage='fetching'`; proceed only if
    rowcount==1) and dispatch `litsearch.synthesize(search_id)`.
  - Celery `litsearch.synthesize(search_id)` — acquire a per-session Redis lock
    `litsearch_lock:{session_id}` (`SET NX EX`, so it can't clobber a concurrent user turn); gather
    ready papers' capped MinIO text; if **zero ready** → assistant message «Не удалось получить
    полные тексты; ответ по аннотациям выше остаётся в силе», `stage=done`. Else `llm.read_fulltexts`
    → persist assistant `ChatMessage` **#2** (steps 7-8). If `sufficient==False ∧ round<
    LITSEARCH_MAX_ROUNDS ∧ followup_query` → `s=start_search(round+1, followup_of=search_id)`, set
    `this.followup_search_id=s.id`; else `stage=done`. Wrap in try/except → on failure `stage=failed`
    + `error`; **always** release the lock. (Recovers the `reading`-forever wedge.)
  - `add_to_database(session, paper_id) -> LiteraturePaper` — **idempotent** (relies on the
    `document_id` UNIQUE constraint + a guarded update; a second concurrent call is a no-op/returns
    the existing doc). If `object_key` is missing, fetch synchronously first (`/fetch/sync`, guarded
    so two clicks don't double-fetch). Create `Document(minio_key=object_key, filename=<doi|title>.pdf,
    mime_type="application/pdf")`; **only if `LITSEARCH_INGEST_ENABLED`** (default **on** — OSN
    approved option A on 2026-07-04, see §7) also create an `IngestTask` + `enqueue_l1_parse` and set
    `ingest_status=queued`/`ingest_task_id`. Off → the Document is staged at L0 (option B) and
    `ingest_status` stays `none` with a UI note "ожидает ингеста".

### 4.5 API (new + one extension)

- **Extend** `ChatMode` with `LITERATURE = "literature"`. A `LITERATURE` message routes to
  `litsearch.start_search` and returns `ChatMessageResponse(claims=[abstract Claim], summary=<#1>,
  session_id=…, tools_used=["litsearch"], mode_used="literature",
  literature=LiteratureRef(search_id, paper_count))`. `ChatMessageResponse` gains optional
  `literature: LiteratureRef | None = None` (mirrors the opt-in `subgraph` slot).
- **New router** `backend/app/api/routes/litsearch.py`, prefix `/litsearch`, auth = current active
  user, ownership via the search's chat session (foreign → 404):
  - `GET /litsearch/{search_id}` → `LiteratureSearchPublic` **(read-only display)**: `{id, stage,
    round, followup_search_id, papers:[LiteraturePaperPublic], answers:[{message_id, kind}]}`.
  - `POST /litsearch/papers/{paper_id}/add-to-database` → `LiteraturePaperPublic`.
  - `GET /litsearch/papers/{paper_id}/ingest-status` → proxies `ingest.status` (hard-parse progress).

### 4.6 Frontend (display + actions only)

- `chat.tsx`: third grid track (`lg:grid-cols-[240px_1fr_360px]`) rendering
  `<LiteraturePanel searchId=… />`, pinned to an **explicit active search** (the session's latest
  literature search id, tracked in component state), **not** "latest message has a ref" — so the panel
  and «Добавить в базу» persist after `stage=done` and after answer #2 lands. Add a **«Литература»**
  mode tab; add `literature` to `modeUsedLabel`/`modeUsedVariant`.
- For literature mode, **suppress the pinned "Agent response" card**; render both litsearch answers
  from chat **history** (invalidate `["chat-history"]` when the panel poll's `answers[]` grows),
  labelled «Ответ по аннотациям» (#1) and «Ответ по полным текстам» (#2) — coherent threading, no
  double-render.
- `LiteraturePanel.tsx`: `useQuery(["litsearch", searchId], … , { refetchInterval: (stage==="done"
  || stage==="failed") ? false : 2000 })`. When `followup_search_id` appears, follow the chain (or
  render rounds stacked). Per paper `Card`: title, authors · year, citation `Badge`, truncated
  abstract; `LoadingButton` «Скачивание…» while `fetch_status==="downloading"`; `Badge` **«добавлено
  в диалог»** when `fulltext_status==="added"`; «Добавить в базу» `LoadingButton` whose **own**
  `useQuery` on `/ingest-status` runs (queued→running→done) **independent of** the search stage (so
  post-`done` ingests still animate). Ingest shows as *provisional* when `LITSEARCH_INGEST_ENABLED`
  is off.
- `frontend/src/lib/litsearch.ts` — `getSearch`, `addToDatabase`, `getIngestStatus` (auth'd fetch,
  mirroring `postChatMessage.ts` / `ingest.tsx`).

### 4.7 Data flow

```
POST /chat/.../messages (mode=literature)  [web]
  └ answer_message → litsearch.start_search
       ├ fetcher /search (sync; abstracts)      → persist Search+Papers (no-doi/url ⇒ skipped)
       ├ llm.synthesize_from_abstracts          → assistant #1  (step 4)
       ├ fetcher /fetch{doi,url=pdf_url,conv=search_id} per fetchable paper (object_key={job_id}.pdf)
       └ dispatch litsearch.monitor(search_id)   (stage=fetching)
  ⇢ returns ChatMessageResponse{#1, literature:{search_id}}

litsearch.monitor  [worker, self-rescheduling]      (steps 6-7, server-side; panel-independent)
   /jobs → object_key → pypdf extract (capped) → fulltext_status=added  («добавлено в диалог»)
   deadline → mark laggards failed
   all terminal & stage==fetching → guarded UPDATE stage=reading → dispatch litsearch.synthesize

litsearch.synthesize  [worker]                       (steps 7-8 + loop)
   lock litsearch_lock:{session}; gather ready fulltexts
   zero ready → assistant «по аннотациям остаётся», done
   else llm.read_fulltexts → assistant #2
        sufficient ⇒ done ; else round<MAX ⇒ start_search(round+1); wire followup_search_id
   on error ⇒ stage=failed; always release lock

Frontend LiteraturePanel polls GET /litsearch/{id}  → DISPLAY ONLY (papers/answers/ingest)
User «Добавить в базу» (any time) → POST add-to-database
   → Document(minio_key=object_key)[L0]  (+ enqueue_l1_parse iff LITSEARCH_INGEST_ENABLED)  (step 5)
```

## 5. Error handling & degradation

- Fetcher down / `/search` empty → search with 0 papers + assistant «Поиск недоступен / ничего не
  найдено» (LOW); no churn.
- LLM unset/erroring → synthesis returns `None` → deterministic template ("Найдено N статей: …").
- PDF fetch fails / DOI-less / paywalled → paper terminal (`failed`/`skipped`), excluded from the
  read; batch still advances. Dead DOIs cannot hang the loop (failed = terminal).
- Batch deadline (`LITSEARCH_FETCH_TIMEOUT`) → laggards marked `failed`, synthesis proceeds with
  whatever's ready (honors "read any/all ready", step 7).
- pypdf failure / scanned PDF → `fulltext_status=failed`, excluded.
- `synthesize` exception → `stage=failed` + `error`; lock always released; no `reading`-forever wedge.
- Idempotency: `fetching→reading` = one guarded SQL UPDATE (rowcount check); `add_to_database` guarded
  by the `document_id` UNIQUE constraint; fetch fired only for `pending` papers; per-session Redis
  lock serializes continuation vs. concurrent user turns.
- All silent caps (`max_results`, char cap, deadline, MAX_ROUNDS) are logged.

## 6. Testing (TDD)

- **litsearch_client / llm** — httpx mocked (respx): parse happy + error→None + unset base_url→None.
- **litsearch helpers** (pure, faked fetcher/LLM/storage): start_search persists rows + writes #1 +
  fires `/fetch` per fetchable paper + marks no-doi/url `skipped`; add_to_database creates Document,
  respects `LITSEARCH_INGEST_ENABLED` on/off, is idempotent (2nd call no-op); zero-ready synthesis
  path; sufficiency loop stops at MAX_ROUNDS.
- **Celery tasks** (called synchronously in tests): `monitor` reconciles done→extract→added,
  failed→failed, deadline→laggard failed, guarded transition fires synthesize once (concurrent-call
  idempotency); `synthesize` writes #2, loops on `sufficient=false`, `stage=failed` on LLM raise.
- **light extraction** — pypdf over a tiny known PDF → non-empty; corrupt bytes → failed.
- **API routes** — auth/ownership (foreign session → 404); GET poll shape incl. `followup_search_id`;
  add-to-database happy + not-found; `ChatMode.literature` round-trips `literature`.
- **Frontend** — panel renders cards from a mocked poll; «Скачивание…» spinner while downloading;
  «добавлено в диалог» on added; add-to-database posts + its own ingest poll animates independent of
  search stage; panel persists after `stage=done`; both answers thread with RU labels; poll stops at
  done/failed.

## 7. Coordination gates & scope

- **Add-to-DB ingest sign-off gate (was "AM §15"):** the graph/ontology ingest overlaps
  nornickel-kg's gated pipeline, so enabling it is a cross-workstream decision. **OSN signed off on
  2026-07-04 → option A is the default:** `LITSEARCH_INGEST_ENABLED=true` — «Добавить в базу» runs the
  full hard-parse → graph/ontology pipeline (`enqueue_l1_parse`, reusing the app's existing
  `ingest.run_ingest` handle; no new ontology/NuExtract logic, no `term_dictionary/` changes). Set the
  flag `false` for option B (stage the `Document` at L0 only). AM + nornickel-kg looped in on the bus
  so the shared ingest path stays aligned.
- **Spend cap:** if TDD/impl subagents hit the monthly cap → wait-for-reset (OSN rule); deterministic
  tasks are sequenced first so LLM-subagent work can defer cleanly.

### Out of scope
Un-stubbing Docling / the ontology LLM extractor (pre-existing); token-by-token streaming; any change
to `term_dictionary/` (owned by another agent, not in this repo); multi-user sharing (a search belongs
to its chat session's user).

## 8. Config summary (`.env.example` additions)

```
ARTICLE_FETCHER_URL=http://article-fetcher:8200   # NB: 8200, the fetcher's real port
OPENALEX_MAILTO=
LLM_BASE_URL=                # OpenAI-compatible; empty → template fallback
LLM_API_KEY=
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT=60
LITSEARCH_MAX_RESULTS=5
LITSEARCH_MAX_ROUNDS=2
LITSEARCH_FULLTEXT_CHAR_CAP=60000
LITSEARCH_FETCH_TIMEOUT=180
LITSEARCH_INGEST_ENABLED=true   # add-to-DB ingest (OSN-approved default A): true=full graph/ontology, false=stage L0 only (B)
```

## 9. What changed after grilling (traceability)

- **Poll-driven → Celery-driven** progression (both critics): steps 6-8 no longer stall when the
  panel is closed; matches the app's Celery model + the shipped Cosmetica `monitor_ingestion` pattern.
- **`object_key`**: `/jobs` didn't return it → added to `JobResponse` + `{job_id}.pdf` fallback; PDF
  read server-side (presigned URLs are unreachable).
- **Port 8200** (was 8000 — backend couldn't reach the fetcher).
- **pypdf added** to backend; extraction in the worker, not a GET handler.
- **Slim fetcher build** (headless browser download gated) to keep compose builds light.
- **DOI-less/failed papers terminal** + **batch deadline** (no infinite stall; "read any/all ready").
- **Follow-up loop reaches the client** via `followup_search_id`; answers also persist to history.
- **Coherent two-answer UI** (suppress stale pinned card; render from history with RU labels);
  **panel + add-to-DB persist** after `stage=done`; ingest card polls independently.
- **DB-level add-to-DB idempotency** (UNIQUE) + **per-session Redis lock** (continuation vs. turn) +
  **`reading→failed` recovery** + **zero-ready-papers guard**.
- **`/fetch` passes `pdf_url`** (fast path) + **`/search` uses OpenAlex `mailto`** (polite pool).
- **Add-to-DB ingest sign-off** → behind `LITSEARCH_INGEST_ENABLED`; OSN approved option A (2026-07-04) → default `true` (full graph/ontology).
