# Literature-Search → Chat Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The design spec `docs/superpowers/specs/2026-07-03-litsearch-chat-integration-design.md` is the companion reference — read its §3 (verified handles) and §4 (architecture) before each task.

**Goal:** Wire `services/article-fetcher` into the chat app so the agent runs a literature search, answers from abstracts, shows a live paper side-panel, light-parses PDFs into the dialog, reads full texts for a grounded answer (looping when thin), and lets the user push any paper into the KB.

**Architecture:** Server-side **Celery**-driven pipeline (never client-poll-driven). The web request does the fast synchronous part (search + abstract answer) and fires the fetcher's async `/fetch` per paper, then dispatches `litsearch.monitor`. A `worker-litsearch` Celery worker (built from the backend image) reconciles fetch jobs, light-extracts full text with pypdf, and runs `litsearch.synthesize` (read full texts → answer → bounded re-search). The frontend `LiteraturePanel` only *displays* state via TanStack Query polling.

**Tech Stack:** FastAPI · SQLModel/Alembic · Celery + Redis · MinIO · httpx · pypdf · React 19 + TanStack Query + shadcn/ui.

## Global Constraints

- **Never touch `main` directly.** Work on `feature/litsearch-chat-integration` (off `osn-pre-main`); PRs target `osn-pre-main`.
- **Do not modify `term_dictionary/`** (owned by another agent; not in this repo anyway).
- Python: `>=3.11,<4.0`. Logging via stdlib `logging`, lazy `%s` args, **no `print()`**. `logging.exception(...)` in `except` blocks.
- Backend deps are managed with `uv` (`backend/pyproject.toml` + `uv.lock`). Add deps via `uv add`, not manual edits, then commit the lockfile.
- Graceful degradation everywhere a sidecar/LLM is called: swallow errors → `None`/`[]`/template, never 500 the chat.
- **Article-fetcher real port is `8200`** (not 8000). PDFs are read **server-side by bytes** (presigned URLs are unreachable).
- Full-text store/read key = the **persisted `LiteraturePaper.object_key`** (never recompute a doc_key; store/read must never diverge).
- Add-to-DB graph/ontology ingest is **gated by `LITSEARCH_INGEST_ENABLED`**; OSN signed off on option A (2026-07-04) → **default `true`** (full hard-parse → graph/ontology via the existing `enqueue_l1_parse`). Set `false` to stage the `Document` at L0 only.
- RU UI strings inline (match `chat.tsx` convention): «Литература», «Скачивание…», «добавлено в диалог», «Добавить в базу», «Ответ по аннотациям», «Ответ по полным текстам».
- TDD: write the failing test first, watch it fail, implement minimally, watch it pass, commit. Backend tests: `cd backend && uv run pytest <path> -v`. Frontend: `cd frontend && bun run test`.

### Test-harness preconditions (read before any task)
- **The test DB schema comes from Alembic, not `create_all`** (`init_db` has `SQLModel.metadata.create_all` commented out, `core/db.py:22`). After Task 2 adds its migration, **every backend task (7-12) must first run `cd backend && uv run alembic upgrade head`** against the test Postgres, or queries hit `relation experiments.literature_searches does not exist`.
- `backend/tests/conftest.py` already provides a **session-scoped, autouse `db` Session** plus **`fake_storage` and `fake_redis`** fixtures — reuse them. `fake_storage.open_document(minio_key=…)` returns a **stream object** exposing `.stream(chunk_size)`/`.close()`/`.release_conn()` (NO `.read()`); read bytes with `b"".join(obj.stream(8192))`.
- Because `db` is one shared Session, any test that intentionally triggers `IntegrityError` **must `session.rollback()`** afterward or it poisons later tests with `PendingRollbackError`.
- New test directories need an `__init__.py` (siblings like `tests/services/` have one). Create `tests/models/` and `tests/schemas/` with `__init__.py`.
- Frontend Playwright tests use the repo baseline: `playwright.config.ts` starts `bun run dev` and `auth.setup.ts` logs into a **live backend** for `storageState`. Tasks 13-14 tests therefore need the dev server + a running backend/DB even with route mocking — this is expected, not a unit test.

## File Structure

**Fetcher + infra (deterministic):**
- `services/article-fetcher/app/main.py` — M: add `object_key` to `JobResponse` + `/jobs`; pass OpenAlex `mailto` into `/search`.
- `services/article-fetcher/Dockerfile` — M: gate the headless-browser download behind a build arg (default off).
- `packages/tool_sdk/tool_sdk/queues.py` — M: register the `litsearch` queue.
- `compose.yml` — M: add `article-fetcher` + `worker-litsearch` services.
- `.env.example` — M: add config keys.

**Backend models/schemas:**
- `backend/app/models/litsearch.py` — C: `LiteratureSearch`, `LiteraturePaper`, enums.
- `backend/app/models/__init__.py` — M: export the new models.
- `backend/app/alembic/versions/<rev>_add_litsearch_tables.py` — C: migration.
- `backend/app/schemas/litsearch.py` — C: `LiteratureRef`, `*Public` DTOs, status enums re-export.
- `backend/app/schemas/chat.py` — M: `ChatMode.LITERATURE`; `ChatMessageResponse.literature`.

**Backend services/worker:**
- `backend/app/services/llm.py` — C: OpenAI-compatible client + `synthesize_from_abstracts`, `read_fulltexts`.
- `backend/app/services/litsearch_client.py` — C: httpx client to the fetcher.
- `backend/app/services/pdf_text.py` — C: pypdf light extraction.
- `backend/app/services/litsearch.py` — C: orchestration helpers (pure, DI'd).
- `backend/app/services/chat.py` — M: route `ChatMode.LITERATURE`.
- `backend/app/core/config.py` — M: settings.
- `backend/app/worker/__init__.py`, `litsearch_app.py`, `litsearch_tasks.py` — C: Celery worker + tasks.
- `backend/app/api/routes/litsearch.py` — C: router.
- `backend/app/api/main.py` — M: include the router.
- `backend/pyproject.toml` + `uv.lock` — M: add `pypdf`.

**Frontend:**
- `frontend/src/lib/litsearch.ts` — C: API helpers.
- `frontend/src/components/Chat/LiteraturePanel.tsx` — C: the panel.
- `frontend/src/routes/_layout/chat.tsx` — M: 3rd column, «Литература» tab, answers rendering, label maps.

**Tests:** mirror each backend module under `backend/tests/...`; frontend under `frontend/tests/` (Playwright, as configured).

---

## Task 1: Fetcher contract fixes + queue registration + config

Makes the fetcher reachable and its job API usable, and registers backend config. No app logic yet.

**Files:**
- Modify: `services/article-fetcher/app/main.py` (`JobResponse` ~L51-56, `get_job` ~L91-107, `search` ~L141-144)
- Modify: `services/article-fetcher/Dockerfile` (headless block ~L38-50)
- Modify: `packages/tool_sdk/tool_sdk/queues.py` (`_TASK_QUEUE_MAP`)
- Modify: `backend/app/core/config.py` (Settings)
- Modify: `.env.example`
- Test: `services/article-fetcher/tests/test_job_object_key.py`; `packages/tool_sdk/tests/test_queues_litsearch.py` (create if `tests/` absent)

**Interfaces produced:**
- Fetcher `GET /jobs/{id}` → `{job_id, status, url?, error?, object_key?}` (object_key set when `done`).
- `tool_sdk.queues.queue_for_task("litsearch.monitor") == "litsearch"`.
- `settings.ARTICLE_FETCHER_URL`, `.LLM_BASE_URL`, `.LLM_API_KEY`, `.LLM_MODEL`, `.LLM_TIMEOUT`, `.OPENALEX_MAILTO`, `.LITSEARCH_MAX_RESULTS`, `.LITSEARCH_MAX_ROUNDS`, `.LITSEARCH_FULLTEXT_CHAR_CAP`, `.LITSEARCH_FETCH_TIMEOUT`, `.LITSEARCH_INGEST_ENABLED`.

- [ ] **Step 1: Failing test — object_key in JobResponse.** In `services/article-fetcher/tests/test_job_object_key.py`, use FastAPI `TestClient` with a fake Redis (monkeypatch `main.redis_client`) holding a `done` job `{"job_id":"j1","status":"done","object_key":"j1.pdf"}` and monkeypatch `main.storage.presign_url` → `"http://x"`. Assert `client.get("/jobs/j1").json()["object_key"] == "j1.pdf"`.
- [ ] **Step 2: Run — expect FAIL** (`object_key` absent). `cd services/article-fetcher && uv run pytest tests/test_job_object_key.py -v`
- [ ] **Step 3: Implement.** Add `object_key: Optional[str] = None` to `JobResponse`; in `get_job` pass `object_key=job.get("object_key")`. In `search`, call `openalex.search(query, max_results, mailto=settings.openalex_mailto or None)` (`openalex.search` already accepts `mailto`, openalex.py:83 — just pass it).
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Queue route.** Add `"litsearch": "litsearch"` to `_TASK_QUEUE_MAP`. Test `test_queues_litsearch.py`: `assert queue_for_task("litsearch.monitor") == "litsearch"` and `"litsearch.*" in build_task_routes()`. Run both, expect PASS.
- [ ] **Step 6: Dockerfile slim.** Wrap the `invisible_playwright fetch` / headless install (Dockerfile ~L38-50) so it runs only when `ARG INSTALL_HEADLESS=false` is `true` (e.g. `RUN if [ "$INSTALL_HEADLESS" = "true" ]; then ...; fi`). Default build skips it. Verify `docker build --build-arg INSTALL_HEADLESS=false -f services/article-fetcher/Dockerfile .` reaches completion of that layer (or `--target`/dry parse if Docker unavailable — at minimum `hadolint`/syntax check).
- [ ] **Step 7: Config.** In `Settings` add the fields above with defaults (`ARTICLE_FETCHER_URL: str = "http://article-fetcher:8200"`, `LLM_BASE_URL: str = ""`, `LLM_MODEL: str = "gpt-4o-mini"`, `LLM_TIMEOUT: int = 60`, `OPENALEX_MAILTO: str = ""`, `LITSEARCH_MAX_RESULTS: int = 5`, `LITSEARCH_MAX_ROUNDS: int = 2`, `LITSEARCH_FULLTEXT_CHAR_CAP: int = 60000`, `LITSEARCH_FETCH_TIMEOUT: int = 180`, `LITSEARCH_INGEST_ENABLED: bool = False`). Mirror in `.env.example` (see spec §8). `cd backend && uv run python -c "from app.core.config import settings; print(settings.ARTICLE_FETCHER_URL)"` → `http://article-fetcher:8200`.
- [ ] **Step 8: Commit.** `git add services/article-fetcher packages/tool_sdk backend/app/core/config.py .env.example && git commit -m "litsearch: fetcher object_key + litsearch queue + backend config"`
  Record learnings to learnings/learnings-task1-fetcher-contract-config.md using the surfacing-subagent-learnings skill.

---

## Task 2: Data models + migration

**Files:**
- Create: `backend/app/models/litsearch.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/app/alembic/versions/<rev>_add_litsearch_tables.py`
- Test: `backend/tests/models/test_litsearch_models.py`

**Interfaces produced (exact):**
```python
class LitStage(StrEnum): SEARCHING="searching"; FETCHING="fetching"; READING="reading"; DONE="done"; FAILED="failed"
class FetchStatus(StrEnum): PENDING="pending"; DOWNLOADING="downloading"; DONE="done"; FAILED="failed"; SKIPPED="skipped"
class FulltextStatus(StrEnum): NONE="none"; ADDED="added"; FAILED="failed"
class LitIngestStatus(StrEnum): NONE="none"; QUEUED="queued"; RUNNING="running"; DONE="done"; FAILED="failed"

class LiteratureSearch(SQLModel, table=True):  # __tablename__="literature_searches", schema="experiments"
    id: uuid.UUID (pk); session_id: uuid.UUID (FK chat_session.id, CASCADE, index)
    question: str; stage: LitStage = SEARCHING; round: int = 0
    followup_of: uuid.UUID|None = None; followup_search_id: uuid.UUID|None = None
    error: str|None = None; created_at/updated_at: datetime (tz)

class LiteraturePaper(SQLModel, table=True):  # __tablename__="literature_papers", schema="experiments"
    id: uuid.UUID (pk); search_id: uuid.UUID (FK literature_searches.id, CASCADE, index)
    doi: str|None; title: str; authors: str; year: int|None; abstract: str
    pdf_url: str|None; citation_count: int|None
    fetch_status: FetchStatus = PENDING; fetch_job_id: str|None; object_key: str|None
    fulltext_status: FulltextStatus = NONE; fulltext_chars: int = 0
    ingest_status: LitIngestStatus = NONE; ingest_task_id: uuid.UUID|None
    document_id: uuid.UUID|None (FK experiments.documents.id, UNIQUE)
    created_at/updated_at: datetime (tz)
```

- [ ] **Step 0: Create `backend/tests/models/__init__.py`** (empty) so collection works.
- [ ] **Step 1: Failing test.** `test_litsearch_models.py` (use the autouse `db` Session): create a `ChatSession`+`LiteratureSearch`+two `LiteraturePaper`s; assert defaults (`stage==SEARCHING`, `fetch_status==PENDING`). Second test: two papers with the **same** non-null `document_id` under one search → `db.commit()` raises `IntegrityError` (UNIQUE); **immediately `db.rollback()`** in the test (shared session) so later tests aren't poisoned.
- [ ] **Step 2: Run — expect FAIL** (module missing). `cd backend && uv run pytest tests/models/test_litsearch_models.py -v`
- [ ] **Step 3: Implement** `models/litsearch.py` per the interface (follow `models/chat.py`/`documents.py` style; `get_datetime_utc` for timestamps; `document_id` column with `unique=True`). Export both models + enums from `models/__init__.py`.
- [ ] **Step 4: Migration.** `cd backend && uv run alembic revision -m "add litsearch tables"` then fill `upgrade()`/`downgrade()` to create/drop `experiments.literature_searches` + `experiments.literature_papers` with the UNIQUE constraint + FKs + indexes (model on `f8bfd1e20554_add_domain_and_chat_tables.py`). Verify `uv run alembic upgrade head` then `downgrade -1` then `upgrade head` cleanly on the test DB.
- [ ] **Step 5: Run — expect PASS.**
- [ ] **Step 6: Commit.** `git commit -m "litsearch: LiteratureSearch/Paper models + migration"`
  Record learnings to learnings/learnings-task2-models.md using the surfacing-subagent-learnings skill.

---

## Task 3: Schemas (DTOs + chat extension)

**Files:**
- Create: `backend/app/schemas/litsearch.py`
- Modify: `backend/app/schemas/chat.py`
- Test: `backend/tests/schemas/test_litsearch_schemas.py`

**Interfaces produced:**
```python
class LiteratureRef(SQLModel): search_id: uuid.UUID; paper_count: int
class LiteraturePaperPublic(SQLModel): id,doi,title,authors,year,abstract,pdf_url,citation_count,
    fetch_status,fulltext_status,fulltext_chars,ingest_status,document_id  # from LiteraturePaper
class LitAnswerRef(SQLModel): message_id: uuid.UUID; kind: Literal["abstracts","fulltext"]
class LiteratureSearchPublic(SQLModel): id,stage,round,followup_search_id,
    papers: list[LiteraturePaperPublic], answers: list[LitAnswerRef]
class PaperIngestStatusPublic(SQLModel):   # constructible for the no-task case (IngestUploadResponse can't express "none")
    status: str = "none"          # "none" | one of IngestStatus values
    progress: float = 0.0
    stage_name: str | None = None
    error: str | None = None
# chat.py:
ChatMode.LITERATURE = "literature"
ChatMessageResponse.literature: LiteratureRef | None = None   # mirrors subgraph opt-in slot
```

- [ ] **Step 0: Create `backend/tests/schemas/__init__.py`** (empty).
- [ ] **Step 1: Failing test.** Assert `ChatMode.LITERATURE == "literature"`; `ChatMessageResponse(claims=[], summary="x", tools_used=[], session_id=uuid4(), literature=LiteratureRef(search_id=uuid4(), paper_count=2)).model_dump()["literature"]["paper_count"] == 2`; a `LiteratureSearchPublic` round-trips with nested papers/answers.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** schemas + chat edits (add `LiteratureRef` import to `chat.py`; add the enum value + field; extend `__all__`).
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "litsearch: DTO schemas + ChatMode.LITERATURE + response.literature"`
  Record learnings to learnings/learnings-task3-schemas.md using the surfacing-subagent-learnings skill.

---

## Task 4: article-fetcher HTTP client

**Files:** Create `backend/app/services/litsearch_client.py`; Test `backend/tests/services/test_litsearch_client.py` (use `respx` — add as dev dep via `uv add --dev respx` if absent).

**Interfaces produced:**
```python
def search(query: str, max_results: int) -> list[dict]        # [] on error; each dict = OpenAlex normalized paper
def resolve(title: str) -> dict | None
def fetch_async(doi: str, *, url: str|None, conversation_id: str) -> str | None   # returns job_id or None
def job_status(job_id: str) -> dict | None                    # {status, object_key?, url?, error?} or None
def fetch_sync(doi: str, *, url: str|None) -> dict | None     # {doi, object_key, url} or None
# base URL = settings.ARTICLE_FETCHER_URL; timeout ~ settings.LLM_TIMEOUT or a dedicated const.
```

- [ ] **Step 1: Failing tests** (respx mock `http://article-fetcher:8200`): `search` parses `{"results":[...]}` → list; HTTP 500 → `[]`. `fetch_async` posts `/fetch` → returns `job_id`; connection error → `None`. `job_status` GET `/jobs/j1` → dict.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** with `httpx.Client`, each call wrapped in `try/except (httpx.HTTPError, ...)` → log via `logging.warning`/`exception` and return the degraded value (pattern: `science_kg_client.py`).
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "litsearch: article-fetcher HTTP client (graceful degradation)"`
  Record learnings to learnings/learnings-task4-fetcher-client.md using the surfacing-subagent-learnings skill.

---

## Task 5: pypdf light extraction

**Files:** add `pypdf` (`cd backend && uv add pypdf`); Create `backend/app/services/pdf_text.py`; Test `backend/tests/services/test_pdf_text.py` with a tiny generated PDF fixture.

**Interfaces produced:** `def extract_text(pdf_bytes: bytes, *, char_cap: int) -> str` — returns extracted text truncated to `char_cap`; raises `PdfExtractError` (module-defined) on unparseable input.

- [ ] **Step 1: Failing test.** pypdf **cannot** author text-bearing PDFs — commit a tiny fixture `backend/tests/fixtures/hello.pdf` containing "Hello Metallurgy" (generate it once with `reportlab` locally, or add `reportlab` as a dev dep and build it in a fixture). Load its bytes; assert `"Metallurgy" in extract_text(data, char_cap=1000)`. Second: `extract_text(b"not a pdf", char_cap=100)` raises `PdfExtractError`. Third: text longer than cap is truncated to `char_cap`.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** using `pypdf.PdfReader(io.BytesIO(pdf_bytes))`, join `page.extract_text()`; wrap parse errors in `PdfExtractError`; truncate to `char_cap`.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit.** `git add backend/pyproject.toml backend/uv.lock backend/app/services/pdf_text.py backend/tests && git commit -m "litsearch: pypdf light full-text extraction"`
  Record learnings to learnings/learnings-task5-pdf-text.md using the surfacing-subagent-learnings skill.

---

## Task 6: LLM client + synthesis helpers

**Files:** Create `backend/app/services/llm.py`; Test `backend/tests/services/test_llm.py` (respx).

**Interfaces produced:**
```python
def complete(messages: list[dict], *, temperature: float=0.2) -> str | None      # None if LLM_BASE_URL unset or error
def complete_json(messages: list[dict]) -> dict | None
def synthesize_from_abstracts(question: str, papers: list[dict]) -> str | None    # step 4
def read_fulltexts(question: str, papers_with_text: list[dict]) -> dict | None    # {answer:str, sufficient:bool, followup_query:str|None}
```

- [ ] **Step 1: Failing tests.** With `LLM_BASE_URL` unset (monkeypatch settings) `complete([...])` returns `None`. With respx mocking `POST {LLM_BASE_URL}/chat/completions` returning a normal choice, `complete` returns the content string. `complete_json` parses a JSON content payload; malformed JSON → `None`. `read_fulltexts` maps to `{answer,sufficient,followup_query}`; missing keys tolerated (defaults `sufficient=True, followup_query=None`).
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.** httpx POST to `f"{LLM_BASE_URL}/chat/completions"` with bearer `LLM_API_KEY`, model `LLM_MODEL`, `timeout=LLM_TIMEOUT`. Guard `if not settings.LLM_BASE_URL: return None`. `synthesize_from_abstracts` builds a prompt listing titles+abstracts and asks for a grounded Russian summary. `read_fulltexts` asks for JSON `{"answer","sufficient","followup_query"}` and uses `complete_json`. All wrapped try/except → `None`.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "litsearch: OpenAI-compatible LLM client + abstract/fulltext synthesis"`
  Record learnings to learnings/learnings-task6-llm.md using the surfacing-subagent-learnings skill.

---

## Task 7: Orchestration — `start_search`

**Files:** Create `backend/app/services/litsearch.py`; Test `backend/tests/services/test_litsearch_start.py`.

**Interfaces produced:**
```python
def start_search(session, chat_session_id: uuid.UUID, question: str, *, round: int=0,
                 followup_of: uuid.UUID|None=None) -> LiteratureSearch
# side effects: persists LiteratureSearch(stage=FETCHING after firing)+papers;
#   writes assistant ChatMessage #1 (abstract answer or template) with
#     message_metadata={"litsearch_kind":"abstracts","search_id":str(id)};
#   fires litsearch_client.fetch_async per fetchable paper (doi OR pdf_url present);
#   no-doi&no-url paper -> FetchStatus.SKIPPED;
#   dispatches celery signature "litsearch.monitor" args=[str(search_id), <deadline_ts>].
```
Dependencies are module-level imports (`litsearch_client`, `llm`, `tasks.celery_app`) so tests monkeypatch them.

- [ ] **Step 1: Failing test.** Monkeypatch `litsearch_client.search` → 2 papers (one with doi+pdf_url, one with neither), `llm.synthesize_from_abstracts` → "ABS", `litsearch_client.fetch_async` → "job1", and capture `tasks.celery_app.signature`. Call `start_search(session, chat_session_id, "q")`. Assert: a `LiteratureSearch` row (stage `FETCHING`), 2 papers (one `DOWNLOADING` with `fetch_job_id="job1"` + `object_key="job1.pdf"`, one `SKIPPED`), an assistant `ChatMessage` with content "ABS", and `signature` called once with name `"litsearch.monitor"`. Second test: `synthesize_from_abstracts` → `None` ⇒ assistant content is the template "Найдено 2 статей…".
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** `start_search` per interface (persist first; helper `_paper_from_openalex(dict)`; object_key set to `f"{job_id}.pdf"`; deadline_ts = now + `LITSEARCH_FETCH_TIMEOUT`). Commit the session.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "litsearch: start_search orchestration (search+abstract answer+fire fetch+dispatch monitor)"`
  Record learnings to learnings/learnings-task7-start-search.md using the surfacing-subagent-learnings skill.

---

## Task 8: Reconcile + `litsearch.monitor` Celery task

**Files:** Modify `backend/app/services/litsearch.py` (add `reconcile`), Create `backend/app/worker/__init__.py`, `backend/app/worker/litsearch_app.py`, `backend/app/worker/litsearch_tasks.py`; Test `backend/tests/services/test_litsearch_reconcile.py`.

**Interfaces produced:**
```python
# litsearch.py
def reconcile(session, search_id, *, now_ts: float, deadline_ts: float) -> bool
#   for each DOWNLOADING paper: job_status -> done: object_key + download bytes (storage) +
#     pdf_text.extract_text -> fulltext_status=ADDED, fulltext_chars=N, fetch_status=DONE;
#     failed: fetch_status/fulltext_status=FAILED. If now_ts>deadline_ts: DOWNLOADING->FAILED.
#   returns True if all papers terminal.
def try_begin_reading(session, search_id) -> bool   # single guarded UPDATE fetching->reading; True iff won.
# worker/litsearch_tasks.py
@celery_app.task(name="litsearch.monitor") def monitor(search_id: str, deadline_ts: float) -> None
#   reconcile; if all terminal & try_begin_reading -> dispatch "litsearch.synthesize";
#   else re-dispatch self with countdown=3 (until deadline+grace).
```
`storage` read: `app/services/storage.py::open_document(minio_key=object_key)` returns a **stream** — read bytes with `b"".join(obj.stream(8192))` (works with the `fake_storage` fixture, which exposes `.stream()` not `.read()`). Bucket is `MINIO_BUCKET` internally.

- [ ] **Step 1: Failing tests (reconcile).** Seed a search with 2 `DOWNLOADING` papers. Monkeypatch `litsearch_client.job_status`: paper A → `{"status":"done","object_key":"a.pdf"}`, B → `{"status":"failed","error":"x"}`; monkeypatch `storage` get → known PDF bytes and `pdf_text.extract_text` → "TXT". Call `reconcile(session, id, now_ts=0, deadline_ts=999)`: A becomes `DONE`/`ADDED`/`fulltext_chars>0`, B `FAILED`; returns `True`. Second test: a still-`downloading` paper with `now_ts > deadline_ts` → `FAILED`, returns `True`. Third (`try_begin_reading`): first call returns `True` and sets stage `READING`; second call returns `False` (idempotent guard).
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** `reconcile` + `try_begin_reading` (use a single `session.exec(update(LiteratureSearch).where(LiteratureSearch.id==id, LiteratureSearch.stage==LitStage.FETCHING).values(stage=LitStage.READING))`; check `result.rowcount==1`). Implement the worker: `litsearch_app.py` = `from app.services.tasks import celery_app; import app.worker.litsearch_tasks  # noqa` exposing `celery_app`; `litsearch_tasks.py` opens its own DB session (`with Session(engine) as session:`), calls reconcile/try_begin_reading, dispatches synthesize or re-schedules `monitor.apply_async(args=[search_id, deadline_ts], countdown=3)`.
- [ ] **Step 4: Run — expect PASS** (test the task body by calling the plain function with a fake session; do not require a live broker).
- [ ] **Step 5: Commit.** `git commit -m "litsearch: reconcile + guarded reading transition + monitor Celery task"`
  Record learnings to learnings/learnings-task8-monitor.md using the surfacing-subagent-learnings skill.

---

## Task 9: `litsearch.synthesize` — read full texts + bounded loop

**Files:** Modify `backend/app/services/litsearch.py` (`synthesize`), `backend/app/worker/litsearch_tasks.py` (task); Test `backend/tests/services/test_litsearch_synthesize.py`.

**Interfaces produced:**
```python
def synthesize(session, search_id) -> None
#   acquire redis lock litsearch_lock:{session_id} (SET NX EX 600); if not acquired -> return (retry later)
#   gather ready papers (fulltext_status==ADDED): read capped text from storage by object_key
#   zero ready -> assistant ChatMessage "…по аннотациям остаётся", stage=DONE
#   else llm.read_fulltexts(question, [...]) -> assistant ChatMessage #2 (fulltext answer / template)
#     with message_metadata={"litsearch_kind":"fulltext","search_id":str(id)}
#   (both assistant messages carry litsearch_kind so the API builds answers[] and the UI labels them)
#        if not sufficient and round<MAX_ROUNDS and followup_query: s=start_search(round+1, followup_of=id);
#              set this.followup_search_id=s.id  else stage=DONE
#   except: stage=FAILED, error=str(e)   finally: release lock
@celery_app.task(name="litsearch.synthesize") def synthesize_task(search_id: str) -> None
```

- [ ] **Step 1: Failing tests.** (a) One `ADDED` paper; `llm.read_fulltexts` → `{"answer":"FINAL","sufficient":true}`; storage returns text. Call `synthesize`: a 2nd assistant `ChatMessage` "FINAL", stage `DONE`. (b) `sufficient:false, followup_query:"more"`, `round=0`, monkeypatch `start_search` to return a new search → `followup_search_id` set, and `start_search` called with `round=1`. (c) `round==MAX_ROUNDS` with `sufficient:false` → stage `DONE`, no new search. (d) zero `ADDED` papers → template assistant message, stage `DONE`. (e) `llm.read_fulltexts` raises → stage `FAILED`, `error` set, lock released. Use a fake redis lock (monkeypatch) asserting acquire+release.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** `synthesize` per interface (redis via `redis.from_url(settings.REDIS_URL)`; lock key includes `session_id`). Worker task wraps it with its own `Session`.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "litsearch: synthesize task (fulltext read, bounded re-search, lock, recovery)"`
  Record learnings to learnings/learnings-task9-synthesize.md using the surfacing-subagent-learnings skill.

---

## Task 10: `add_to_database` (flag-gated ingest, idempotent)

**Files:** Modify `backend/app/services/litsearch.py` (`add_to_database`); Test `backend/tests/services/test_litsearch_add_to_db.py`.

**Interfaces produced:**
```python
def add_to_database(session, paper_id: uuid.UUID) -> LiteraturePaper
#   if paper.document_id set -> return paper (idempotent no-op)
#   if object_key missing -> litsearch_client.fetch_sync(doi,url=pdf_url) -> set object_key (guarded)
#   create Document(minio_key=object_key, filename=f"{doi or title}.pdf", mime_type="application/pdf", L0);
#   set paper.document_id
#   if settings.LITSEARCH_INGEST_ENABLED: create IngestTask(document_ids=[str(doc.id)]) + tasks.enqueue_l1_parse(task.id,[doc.id]);
#        paper.ingest_status=QUEUED, ingest_task_id=task.id
#   else: paper.ingest_status stays NONE (staged L0 only)
```

- [ ] **Step 1: Failing tests.** (a) `LITSEARCH_INGEST_ENABLED=False`, paper with `object_key`: `add_to_database` creates a `Document(minio_key==object_key)`, sets `document_id`, `ingest_status==NONE`, and does **not** call `enqueue_l1_parse` (monkeypatch to assert not called). (b) `=True`: creates `IngestTask`, calls `enqueue_l1_parse` once, `ingest_status==QUEUED`. (c) Idempotency: second call returns same `document_id`, no 2nd Document. (d) missing `object_key` → `fetch_sync` monkeypatched → sets object_key then proceeds.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** per interface (mirror `ingest.run_ingest` for the IngestTask+enqueue; rely on the `document_id` UNIQUE for the race, catching `IntegrityError` → reload).
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "litsearch: add_to_database (L0 stage default, gated graph/ontology ingest, idempotent)"`
  Record learnings to learnings/learnings-task10-add-to-db.md using the surfacing-subagent-learnings skill.

---

## Task 11: Chat routing + worker compose wiring

**Files:** Modify `backend/app/services/chat.py` (`answer_message`); Modify `compose.yml` (`worker-litsearch` + `article-fetcher`); Test `backend/tests/services/test_chat_literature.py`.

**Interfaces produced:** `answer_message` for `ChatMode.LITERATURE` calls `litsearch.start_search`, returns `ChatMessageResponse(claims=[Claim(text=<#1>, experiment_ids=[], confidence=LOW, kind=FACT)], summary=<#1>, tools_used=["litsearch"], session_id=…, mode_used="literature", literature=LiteratureRef(search_id, paper_count))`.

- [ ] **Step 1: Failing test.** Monkeypatch `litsearch.start_search` → a search with id + 3 papers and a persisted #1 message "ABS". Build `ChatMessageRequest(content="q", metadata=ChatMessageMetadata(mode=ChatMode.LITERATURE))`; call `answer_message`. Assert `resp.mode_used=="literature"`, `resp.literature.search_id`, `resp.literature.paper_count==3`, `"litsearch" in resp.tools_used`.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** a `mode == ChatMode.LITERATURE` branch early in `answer_message` (before the ontology/kg waterfall) returning the response; ensure it does not double-write #1 (start_search already persists it — `answer_message` should not also write an assistant row for this branch; only write the user row as the other branches do).
- [ ] **Step 4: Compose.** Add `article-fetcher` (spec §4.1) and `worker-litsearch` (build `backend/Dockerfile` via `context: .`/`dockerfile: backend/Dockerfile`, `command: ["celery","-A","app.worker.litsearch_app:celery_app","worker","-Q","litsearch","--concurrency=2","--loglevel=info"]` — the explicit `:celery_app` attribute avoids auto-discovery ambiguity, matching the workers' convention; same `env_file`/DB/Redis/MinIO env as `backend`, add `ARTICLE_FETCHER_URL`, `LLM_*`, `LITSEARCH_*`; `depends_on: db, redis, minio, article-fetcher`). `docker compose config` must parse.
- [ ] **Step 5: Run — expect PASS** (chat test) and `docker compose config -q` clean.
- [ ] **Step 6: Commit.** `git commit -m "litsearch: chat LITERATURE routing + worker-litsearch/article-fetcher compose"`
  Record learnings to learnings/learnings-task11-chat-compose.md using the surfacing-subagent-learnings skill.

---

## Task 12: API router

**Files:** Create `backend/app/api/routes/litsearch.py`; Modify `backend/app/api/main.py`; Test `backend/tests/api/routes/test_litsearch_api.py`.

**Endpoints:**
- `GET /api/v1/litsearch/{search_id}` → `LiteratureSearchPublic` (owner-checked via the search's chat session → 404 for foreign/unknown). Builds `papers` + `answers` — the session's assistant `ChatMessage`s whose `message_metadata.search_id == search_id`, each `LitAnswerRef(message_id, kind=message_metadata["litsearch_kind"])`, ordered by `created_at`.
- `POST /api/v1/litsearch/papers/{paper_id}/add-to-database` → `LiteraturePaperPublic` (owner-checked; 404 unknown).
- `GET /api/v1/litsearch/papers/{paper_id}/ingest-status` → **`PaperIngestStatusPublic`** (owner-checked). If `paper.ingest_task_id` is None → `PaperIngestStatusPublic()` (status="none"); else map the `IngestTask` (status→`str(task.status)`, progress, stage_name, error). This DTO is required because `IngestUploadResponse` needs a non-null `task_id` and `IngestStatus` has no "none" member.

- [ ] **Step 1: Failing tests** (FastAPI `TestClient` + auth fixture from `backend/tests`): create a session+search+papers for user A; GET as A → 200 with correct shape incl. `followup_search_id`; GET as user B → 404. POST add-to-database as A (monkeypatch `litsearch.add_to_database`) → 200; unknown paper → 404.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** router (prefix `/litsearch`, `CurrentUser`/`SessionDep`, an `_owned_search`/`_owned_paper` helper like `_get_owned_session`). Register in `api/main.py`.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "litsearch: API router (poll, add-to-database, ingest-status)"`
  Record learnings to learnings/learnings-task12-api.md using the surfacing-subagent-learnings skill.

---

## Task 13: Frontend — API lib + LiteraturePanel

**Files:** Create `frontend/src/lib/litsearch.ts`, `frontend/src/components/Chat/LiteraturePanel.tsx`; Test `frontend/tests/literature-panel.spec.ts` (Playwright, mock network).

**Interfaces produced:** `getSearch(searchId): Promise<LiteratureSearchPublic>`, `addToDatabase(paperId)`, `getIngestStatus(paperId): Promise<PaperIngestStatusPublic>` (auth'd fetch, mirror `postChatMessage.ts`; hand-declare the TS response types in `litsearch.ts` — the generated client won't cover these new endpoints). `LiteraturePanel({ searchId }: {searchId: string})`.

- [ ] **Step 1: Failing test.** Playwright test mounting the chat route with a mocked `GET /api/v1/litsearch/:id` returning 2 papers (one `downloading`, one `fetch_status=done, fulltext_status=added`); assert one card shows the «Скачивание…» spinner and the other shows the «добавлено в диалог» badge and an enabled «Добавить в базу» button.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** `litsearch.ts` + `LiteraturePanel.tsx`: `useQuery(["litsearch", searchId], () => getSearch(searchId), { refetchInterval: (q)=> ['done','failed'].includes(q.state.data?.stage) ? false : 2000 })`; render `Card` per paper (title, authors · year, citation `Badge`, truncated abstract), `LoadingButton loading={fetch_status==='downloading'}`, `Badge` «добавлено в диалог» when `fulltext_status==='added'`, «Добавить в базу» `LoadingButton` calling `addToDatabase` then its own `useQuery` on `getIngestStatus` (interval while `ingest_status` in queued/running). Follow `ingest.tsx` for icon-button/in-flight patterns.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "litsearch(fe): API lib + LiteraturePanel (poll, spinner, badge, add-to-db)"`
  Record learnings to learnings/learnings-task13-fe-panel.md using the surfacing-subagent-learnings skill.

---

## Task 14: Frontend — chat integration

**Files:** Modify `frontend/src/routes/_layout/chat.tsx`; Test `frontend/tests/chat-literature.spec.ts`.

- [ ] **Step 1: Client types.** The `ChatMode` union is code-generated (`frontend/src/client/types.gen.ts:44`, hardcoded `'auto'|'ontology'|'knowledge_graph'`) and the chat send path uses a **hand-written** `postChatMessage.ts` with its own `ChatModeUsed` union (`:30`) + a local response type. Add `'literature'` to `ChatMode` in `types.gen.ts` (or regenerate the client via the repo's generate-client script if the backend is running), and extend `postChatMessage.ts`'s `ChatModeUsed` + its `ChatMessageResponse` type with `mode_used` "literature" and an optional `literature?: { search_id: string; paper_count: number }`. Build must stay green: `cd frontend && bun run build`.
- [ ] **Step 2: Failing test.** Mock `postChatMessage` to return a response with `mode_used:"literature"` + `literature:{search_id,paper_count:2}` and a mocked `getSearch`; select the «Литература» tab, send a message; assert the third-column panel appears, the mode-used badge reads «Литература» (not raw "literature"), and after the poll reports a fulltext answer the chat thread shows «Ответ по полным текстам».
- [ ] **Step 3: Run — expect FAIL.**
- [ ] **Step 4: Implement.** Add «Литература» to the mode selector (`chat.tsx:20-36`) + `modeUsedLabel`/`modeUsedVariant` (`chat.tsx:38-51`). Change the grid (`chat.tsx:150`, currently `lg:grid-cols-[280px_1fr]`) to `lg:grid-cols-[280px_1fr_360px]`; render `<LiteraturePanel>` in the third column when an active literature `search_id` exists (track it in state from the send-mutation response; keep it after `stage=done`). For literature mode, suppress the pinned "Agent response" card and render answers from history; when the panel poll's `answers[]` grows, `queryClient.invalidateQueries(["chat-history"])`; label the two assistant messages «Ответ по аннотациям»/«Ответ по полным текстам» using their `message_metadata.litsearch_kind`. Behavior for the follow-up round (spec §4.6): when the poll returns a non-null `followup_search_id`, switch the panel's active `searchId` to it (render the newest round; earlier rounds' answers remain in the chat thread).
- [ ] **Step 5: Run — expect PASS.**
- [ ] **Step 6: Commit.** `git commit -m "litsearch(fe): chat 3-column layout, Литература mode, coherent two-answer rendering"`
  Record learnings to learnings/learnings-task14-fe-chat.md using the surfacing-subagent-learnings skill.

---

## Post-implementation

- [ ] Full backend suite green: `cd backend && uv run pytest -q`.
- [ ] Lint/type: `cd backend && uv run ruff check app && uv run mypy app` (new files clean).
- [ ] Frontend: `cd frontend && bun run build` + `bun run test`.
- [ ] Update `README.md` / `.env.example` note: the «Литература» chat mode + the `LITSEARCH_INGEST_ENABLED` add-to-DB ingest gate (default on / option A).
- [ ] Hand back to the PR/e2e/merge task (#12 in the session tracker).
