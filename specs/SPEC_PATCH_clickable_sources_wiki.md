# SPEC PATCH — Clickable chat sources + wiki PDF view (post-main integration)

## Context

Two things landed independently and must be reconciled:

1. **Our branch** added GraphRAG ingestion of the parser `SHARED/` corpus into
   Neo4j and made a chat answer's graph sources clickable — a chip under the
   answer opens the original document. It shipped its own plumbing to reach
   `SHARED/`: a parser `GET /files/raw` endpoint, a `PARSER_URL` setting, a
   `backend /graph-articles/{doc_id}/content` streaming proxy, a
   `fetchArticleBlob` helper, and an `ArticleViewerTab` bolted onto the old
   wiki page.

2. **`origin/main`** (PRs #28/#29 — "shared-files", "ontology-tuning") rewrote
   the wiki into a **SHARED file-tree browser** and added, canonically, the
   *same* SHARED-access capability we built:
   - parser `GET /api/v1/files/raw` (`raw_file_content`) — **duplicate of ours**
   - `settings.PARSER_URL` (+ `PARSER_TIMEOUT_S`, …) — **duplicate of ours**
   - `backend/app/services/parser_client.py` — `fetch_raw` / `fetch_markdown` /
     `fetch_tree`, `RawFileResponse`, `okf_to_raw_path`
   - wiki endpoints keyed by `okf_path`: `/wiki/tree`, `/wiki/documents/content`,
     `/wiki/documents/download/raw`, `/wiki/documents/download/markdown`
   - `wiki.tsx` file-tree UI with inline markdown + raw/markdown download buttons
   - chat: provenance shown via `splitClaimSource(claim.text)` — a *text* marker
     `— источник: «…»` produced by the **ontology** branch (unstructured)

So most of our SHARED plumbing is now redundant, and our chat/wiki UI changes
collide with main's rewrites.

Additionally, a bug report from testing: **the source links render only in the
live "Agent response" card, not in the chat history above** — they must appear
in both.

## Goals

1. Chat source links appear in **both** the live "Agent response" card **and**
   the persisted **history** messages.
2. Reuse **main's** SHARED infrastructure; delete our duplicates.
3. Clicking a source opens the document on the **new** wiki, with the **PDF
   viewable inline** (the original ask), reusing main's raw endpoint.
4. Keep our structured `ChatSource[]` (graph-RAG provenance) alongside main's
   text-based ontology provenance — they are different code paths and coexist.

## Non-goals

- No new backend route for raw bytes — main's `/wiki/documents/download/raw`
  and `parser_client.fetch_raw` cover it.
- Not changing main's wiki tree/search/download behaviour.

## Design

### Data: `ChatSource` carries `okf_path`

`ChatSource` (backend `app/schemas/chat.py`, frontend `postChatMessage.ts`)
gains `okf_path` — the wiki deep-link key. It is derived, not fetched:

- A GraphRAG source doc_id is `"<raw_path>::chunk{i}"` (see
  `scripts/ingest_shared_corpus.py`), e.g.
  `RAW_DATA/Обзоры/Медный купорос.pdf::chunk3`.
- `source_path` = doc_id split on `"::chunk"` → `RAW_DATA/Обзоры/Медный купорос.pdf`
- `okf_path` = `"01_docling_clean00/" + source_path + ".md"` — the stage-1 OKF
  markdown path main's wiki expects; `okf_to_raw_path` inverts it for raw
  download.
- `filename` = basename of source_path.

Because everything is derivable from the doc_id string, `_resolve_chat_sources`
**no longer needs the N+1 `science_kg_client.get_document` call** — it parses the
doc_ids returned by `rag_query`, deduplicates by `source_path`, and caps the
list. (`get_document` stays in the client but chat stops using it.)

### Persistence (already correct)

`chat.py::answer_message` already stores the full `ChatMessageResponse`
(including `claims[].sources`) in the assistant `ChatMessage.message_metadata`.
No backend change needed for goal 1 — the data is already in history; the
frontend just has to render it.

### Frontend: shared `SourceChips`, rendered in both places

Extract a `SourceChips({ sources })` component that renders each `ChatSource`
as a link to `"/wiki"` with `search={{ doc: source.okf_path }}`, labelled by
`filename`. Render it:
- in the live **Agent response** card, under each claim (as today), and
- in the **history** renderer: for `assistant` messages, read
  `message.message_metadata.claims[].sources` and render the same chips.

Coexists with main's `splitClaimSource` "Источник:" line (ontology text
provenance) — both can show.

### Wiki: `?doc=` deep-link + inline PDF viewer (on main's wiki)

- Add `validateSearch` to main's `/_layout/wiki` route for `doc?: string`
  (an okf_path). On load, if `doc` is set, select it (`setSelectedPath(doc)`)
  and expand the tree to reveal it.
- In main's wiki document panel, when the selected document's `raw_path` ends
  in `.pdf`, render an inline `<embed type="application/pdf">` fed by a blob
  from `GET /api/v1/wiki/documents/download/raw?okf_path=…` (authenticated
  fetch → blob URL, same pattern as `downloadWikiDocument`; add a small
  `fetchWikiRawBlobUrl` helper). Non-PDF raw keeps the existing download button.

### Removed (redundant with main)

- parser `app/presentation/router.py` — our `raw_file` (use main's
  `raw_file_content`).
- backend `app/core/config.py` — our `PARSER_URL` (use main's).
- backend `app/api/routes/graph_articles.py` + its registration in `api/main.py`.
- frontend `src/lib/fetchArticleBlob.ts` and the standalone `ArticleViewerTab`
  tab from our wiki changes (superseded by main's wiki + inline viewer above).

### Kept

- `scripts/ingest_shared_corpus.py`, `scripts/_ingest_lib.py`,
  `scripts/ingest_corpus.py` — ingestion.
- science-kg retrieval quality work (lemmatization, RU stopwords, exact-match
  anchors, `same_source_only`, coverage ranking, hybrid text+graph RAG,
  relevance-ordered sources) and the `{doc_id:path}` route fix.
- neo4j bind-mount (`./neo4j-data`) so the ingested graph survives `down -v`.
- `science_kg_client.get_document` (unused by chat now, harmless).

## Verification

1. `docker compose` up (both stacks on `metalcrow-net`); graph holds the 5
   ingested PDF articles.
2. Ask a knowledge_graph question → answer clean (no `::chunk`), chips appear
   **under the live answer**.
3. Reload / reselect the session → the same chips appear **in history**.
4. Click a chip → navigates to `/wiki?doc=<okf_path>`; wiki selects the doc,
   shows markdown, and renders the **PDF inline** (raw is `.pdf`).
5. `grep` confirms no remaining references to `graph-articles`, `fetchArticleBlob`,
   or a second `PARSER_URL`/`/files/raw` definition.
6. Existing tests green: `services/science-knowledge-graph/tests/test_rag.py`,
   relevant `backend/tests`.
