# Litsearch — findings, fixes & open items (2026-07-04)

Working notes from a live debugging + hardening pass on the litsearch chat
feature (Phase A abstract answer in the backend, Phase B full-text read/answer
on the `litsearch` Celery queue) and the `article-fetcher` microservice.

## Fixed & deployed this session

- **Phase B was reading the wrong papers.** `read_fulltext` rebound to the
  *most-recent* search in the chat, and Phase B also offered `litsearch_search`;
  the model would search again mid-Phase-B, creating a new (empty) search that
  the read tool then bound to — so the LLM answered without the downloaded full
  texts, yet the turn was tagged `fulltext`. Fix: Phase B is now **read-only**,
  `read_fulltext` binds to the **processing `search_id`**, and `fulltext` is
  tagged only when a read actually returned text. (`litsearch.py`,
  `litsearch_tools.py`.)
- **No wait for downloads.** Phase B now heartbeat-re-enqueues (releasing the
  worker slot) until every paper is terminal, then injects a "papers downloaded
  — read them" turn and runs the read loop. Deadline anchored to
  `search.created_at + LITSEARCH_FETCH_TIMEOUT`.
- **NUL-byte crash.** `pdf_text.extract_text` now strips `\x00` (pypdf can emit
  it), which was crashing the worker with `psycopg.DataError: ... NUL (0x00)`.
- **Phase A search swarm + leaked tool-call markup.** `run_loop` gained opt-in
  `max_tool_calls` + `exhausted_system_msg`; Phase A caps `litsearch_search` at
  `LITSEARCH_MAX_SEARCHES=3` and, on cap or when a text reply contains DeepSeek
  `｜DSML｜` tool-call markup, drops that turn, appends a system "answer from the
  abstracts" message, and re-invokes (`tool_choice="none"`) so the reply is
  always real prose.
- **Deploy gap (root cause of "my fixes didn't take").** `worker-litsearch` had
  **no bind mount** — it ran baked image code, so Phase B edits never applied on
  restart. Added `./backend/app` mount to `compose.override.yml` (parity with
  `backend`). Celery still needs a `restart` to reimport.
- **Worker concurrency** raised `2 → 6` (see below).

## Concurrency & scaling (worker-litsearch)

- Phase B is **I/O-bound** (LLM calls + download waits; the heartbeat frees the
  slot during downloads), so a slot is held only for the ~30–60s read loop.
- **Phase A is unaffected by worker concurrency** — the abstract answer is
  produced synchronously in the FastAPI backend, so first replies stay
  concurrent regardless.
- `--concurrency=6` handles a burst of ~10 users with modest queueing. DB
  headroom is fine (Postgres `max_connections=100`, ~14 in use; Phase B uses ~1
  session/task).
- **To scale further, in order of leverage:**
  1. **Size to the LLM gateway**, not arbitrarily — all Phase-B tasks hit the
     same LiteLLM gateway; its concurrent-request/upstream rate limit is the true
     ceiling. Raising worker concurrency past that just moves the queue.
  2. **Switch Phase B to a gevent/eventlet pool** (`-P gevent -c 20+`) — far more
     efficient for I/O-bound waits than prefork. Needs monkey-patch + verifying
     psycopg/httpx compatibility (test before flipping).
  3. **Cap the shared SQLAlchemy pool** (`core/db.py` `create_engine` has no
     `pool_size`/`pool_pre_ping` today) before pushing concurrency high, or N
     processes × default 15 conns can approach `max_connections`. `pool_pre_ping`
     also hardens against the DB-restart stale-connection strand seen earlier.
  4. **Horizontal replicas** — `docker compose up --scale worker-litsearch=N`,
     all draining the same `litsearch` queue.

## Fixed this session (part 2 — OpenAlex key + turn-union "accumulate")

- **OpenAlex API key now used for search/resolve.** `article-fetcher/app/main.py`
  `/search` + `/resolve` pass `api_keys=settings.openalex_api_keys` + `mailto`;
  `OPENALEX_API_KEY`/`OPENALEX_MAILTO` forwarded into the container via compose
  and set in the gitignored `.env`. Verified live (keyed pool, HTTP 200).
  (commit `712ed04`)
- **"Bind to last search" replaced with a per-turn UNION** (the "accumulate"
  rework, plan `docs/superpowers/plans/2026-07-04-litsearch-turn-union.md`,
  Tasks 1-4). A turn's searches are grouped by reusing the dead
  `LiteratureSearch.followup_of` column as a turn-group key (anchor = first
  search, `followup_of=None`; members point at it). Phase B reads the
  DOI-deduped union of ALL the turn's papers and writes ONE grounded answer;
  the panel route aggregates the same union server-side (so the frontend fetch
  contract stays one anchor id) and now returns `queries: list[str]` (every
  search shown). Cap split: `LITSEARCH_MAX_SEARCHES` = successful (≥1 paper)
  searches, `LITSEARCH_MAX_SEARCH_ATTEMPTS` = total attempts. No DB migration.
  (commits `5d730b5`..`b150f1a`)
- **Pre-existing `NameError: settings` in Phase A fixed.** `chat.py` referenced
  `settings.LITSEARCH_MAX_SEARCHES` (added by the earlier cap commit `78143c8`)
  with no `from app.core.config import settings` — Phase A crashed on every
  litsearch, live and in 6 tests. Import added. (commit `e3810e0`)

## Open items (NOT yet fixed)

- **Broker-down dispatch failure strands member searches at `FETCHING`.** When
  `_dispatch_agent_continue` fails (broker unreachable) on a multi-search turn,
  only the **anchor** is marked `FAILED`; grouped member rows stay `FETCHING`.
  Benign today (the UI only polls the anchor, which stops on `FAILED`, and the
  route reads member papers regardless of stage), but a future stuck-search
  sweep / analytics counting `FETCHING` rows would trip on it. Fix = mark the
  whole turn (members via `followup_of`) `FAILED` in the dispatch-failure path.
- **DSML tool-call leak — root cause is gateway-side.** DeepSeek emits tool
  calls as `｜DSML｜` text that LiteLLM doesn't parse into structured
  `tool_calls`. The forced-reply mechanism hides it from users, but the real fix
  is parsing DeepSeek tool calls at the gateway. Separate LLM-gateway task.
- **`chat_message_session_id_fkey` FK violation** observed once in a live Phase-A
  request — a message insert for a session that doesn't exist (stale/deleted-
  session race). Not root-caused; sessions in the DB are otherwise intact.

## Next up — RU search engine (cyberleninka.ru) + EN/RU split

Requested 2026-07-04. A dedicated task, not yet started:

- **Split the search tool by language.** Rename the current OpenAlex tool
  `litsearch_search` → **`literature_search_en`**; add **`literature_search_ru`**
  backed by **cyberleninka.ru**. Each language gets its OWN cap (separate
  successful/attempt counters — extend the Task-1 cap model per-tool rather than
  one shared counter).
- **RU fetch cascade:** `literature_search_ru` must try cyberleninka's **native
  downloader FIRST**, ahead of the existing OA/EuropePMC/Sci-Hub/headless
  cascade in `article-fetcher`. (Open question: are there papers findable on
  cyberleninka but not downloadable there? If so, fall back to the generic
  cascade by DOI.)
- **API CONFIRMED (Account Manager, 2026-07-04).** Search: `POST
  https://cyberleninka.ru/api/search` body `{"mode":"articles","q":"<query>",
  "size":N,"from":M}` → `{found, articles[], agg_year...}`. Sample staged at
  `/home/claude/a2a-shared/cyberleninka-api/sample_search_response.json`. Open,
  no auth. Verified live by me: it returns exactly the Russian-domain
  literature OpenAlex lacks — e.g. "подземное захоронение сточных вод глубокие
  горизонты" → **232 hits** (all on-topic) where OpenAlex returns 0. This makes
  the RU tool essential, not optional, for Russian-domain questions.
- **Crop top-N ourselves (CONFIRMED).** `found` runs to hundreds (68–232 for
  the mine-water topic). Body takes `size`/`from` for paging → request only the
  top-N (mirror `LITSEARCH_MAX_RESULTS`).
- **NO DOWNLOADER NEEDED (key finding, 2026-07-04).** Each article in the
  search response carries an **`ocr` field = the FULL article text inline**
  (list of text fragments; join + strip `<b>`/HTML). So the RU tool gets search
  AND full text in ONE call — no PDF fetch, no parse, no cascade. (The `/pdf`
  URL 404s; `ocr` is the source.) This CONTRADICTS the original "use native
  cyberleninka downloader first" assumption — there's nothing to download, and
  the "found-but-not-downloadable" worry is moot. RU `LiteraturePaper` rows are
  created with `fulltext_text`=joined ocr + `fulltext_status=ADDED` immediately,
  so Phase B reads them with NO download wait (terminal on creation).
  Article fields: `name` (title, has `<b>`), `annotation` (abstract, has `<b>`),
  `authors` (stringified list — parse), `year`, `journal`, `link`
  (`/article/n/<slug>` → prefix `https://cyberleninka.ru`). **No DOI** —
  cyberleninka is DOI-less; dedup RU papers by `link`/title, not DOI.
- **Proxy (CONFIRMED 2026-07-04):** of the 5 given, only **socks5
  `37.16.81.138:1080`** works (HTTP 200); the other four fail (000). Direct from
  the box also works (200). Client: try socks5 first, fall back to direct.
- Fits the turn-union model cleanly: EN and RU searches in one turn become
  members of the same anchor group; the union read already dedups by DOI across
  sources. Just needs a second search tool + a cyberleninka client in
  `article-fetcher` + per-tool caps.

## Cosmetic queue (do after Task C/D)

- **Expandable article cards** (`frontend/src/components/Chat/LiteraturePanel.tsx`,
  `PaperCard`). Collapsed = title only; expanded (click title) = authors, DOI
  link (`https://doi.org/{doi}`), abstract, and — IF added to the base — a link
  to the wiki document. Effort: LOW for collapse/expand + title/authors/DOI/
  abstract (all fields already on `LiteraturePaperPublic`). The **wiki link is
  conditional**: `document_id` is present after ingest, and the branch has a
  `wiki.tsx`/`documents.tsx` route — add the deep-link only if that route can
  target a single `document_id` cheaply; otherwise skip it (per request) and
  show a "в базе" badge. Requested 2026-07-04.

## Test / deploy notes

- The `db` fixture deletes all Users at teardown — **never** run the suite
  against the live `app` DB. Use a throwaway scratch DB (`alembic upgrade head`
  first).
- `worker-litsearch` runs Phase B; `backend` runs Phase A. A code change to the
  loop needs **both** restarted.
