# science-knowledge-graph

Standalone knowledge-graph service: bilingual (RU/EN) spaCy/scispaCy NER
extraction over materials-science text + a Neo4j graph + GraphRAG
(hybrid subgraph retrieval → LLM answer via OpenAI-compatible API).

Internal-only sidecar in the `metalcrow` compose stack — no public port,
reachable from other containers as `http://science-knowledge-graph:8000`.
Shares the root `compose.yml` Neo4j service.

The Python package is `science_kg` (identifiers can't contain hyphens); the
compose service / directory name is `science-knowledge-graph`.

Called by `backend` over HTTP (`backend/app/services/science_kg_client.py`,
`SCIENCE_KG_URL`), wired into `POST /api/v1/graph/query` and chat /
`generate_hypothesis`. Degrades gracefully if unreachable — optional, same as Neo4j.

## API

- `POST /api/v1/documents` / `/documents/batch` / `/documents/pdf` — ingest text
  or PDF, extract entities/relations, upsert into Neo4j (with embeddings when configured).
- `GET /api/v1/search` — filter subgraph by `material`/`regime`/`property`.
- `GET /api/v1/entities/{text}/neighbourhood` — subgraph around one entity.
- `POST /api/v1/rag/query` — graph-grounded LLM answer with sources.
- `GET /api/v1/health`

## NLP: term_dictionary snapshot

Hand-written EntityRuler patterns (`nlp/patterns.py`) are merged with a static
snapshot from `feature/extraction-benchmark`:

- `science_kg/data/entity_ruler_patterns.jsonl` — spaCy patterns (MATERIAL, PROCESS→REGIME, PROPERTY, EQUIPMENT)
- `science_kg/data/synonym_map.json` — RU↔EN canonicalization fallback in `nlp/normalizer.py`

Loader: `nlp/term_dictionary_patterns.py`. Hand-written patterns take priority.
Details: `specs/SPEC_PATCH_term_dictionary_x_science_kg.md`.

## Retrieval: CONTAINS + vector search

GraphRAG retrieval (`rag/retriever.py`) combines two channels:

1. **Substring** — `CONTAINS` on terms extracted from the question.
2. **Semantic** — embedding of the full question → Neo4j vector index
   `entity_embedding_idx` (cosine, top-k anchors).

Anchors are deduplicated; each gets a 2-hop neighbourhood. Without embeddings
(no API key / API error) vector search is skipped — CONTAINS-only mode.

Embeddings are computed on ingest (`api/routes.py`) and stored on `:Entity.embedding`.
Model: `text-embedding-3-small` (1536-dim) via the same OpenAI-compatible proxy as chat
(`science_kg/embeddings.py`). Not the SPEC_V3 `multilingual-e5-large` — see spec patch above.

Backfill for nodes ingested before this feature:

```bash
docker compose exec science-knowledge-graph uv run python -m scripts.backfill_embeddings
```

## Running it

```bash
docker compose up -d --build science-knowledge-graph
```

The `en_core_sci_sm` spaCy model (~15 MB) is vendored under `models/` because
Allen AI hosts it only on S3 us-west-2, which is often blocked from RU networks.
Refresh: `scripts/fetch_spacy_models.sh` from a machine that can reach S3, then commit
the tarball and `SHA256SUMS`.

### Environment

In the repo-root `.env`:

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | RAG answers + text embeddings (required for full GraphRAG) |
| `OPENAI_BASE_URL` | Optional proxy (e.g. `api.proxyapi.ru`) |
| `OPENAI_MODEL` | Chat model for `/rag/query` |

Without `OPENAI_API_KEY`: ingestion, search, and neighbourhood still work;
RAG generation and vector search degrade (no embeddings written / queried).

## Bulk ingest from parser SHARED

One-shot script `scripts/ingest_shared_corpus.py`: walks parser `SHARED/` via
`/files/tree`, fetches OKF markdown, chunks text, POSTs to `/documents/batch`.
Not run on service start — invoke manually after Docling has produced OKF output.

Local (from repo root, after `make up`):

```bash
docker compose build science-knowledge-graph
docker compose up -d science-knowledge-graph
docker compose exec science-knowledge-graph \
  uv run python scripts/ingest_shared_corpus.py
```

Server (`compose.prod.yml` overlay):

```bash
docker compose -f compose.yml -f compose.prod.yml build science-knowledge-graph
docker compose -f compose.yml -f compose.prod.yml up -d science-knowledge-graph
docker compose -f compose.yml -f compose.prod.yml exec science-knowledge-graph \
  uv run python scripts/ingest_shared_corpus.py
```

Flags: `--limit N` (smoke test), `--concurrency`, `--batch-size`. Resume via
`scripts/.ingest_shared_progress.json` inside the container. See root
[README.md](../../README.md#ingest-shared--knowledge-graph) for coverage checks and prod notes.

## Load precomputed facts + vectors into Neo4j (server)

`docker compose up` только поднимает сервисы — Neo4j стартует **пустым**, ничего
не читается на старте. Граф нужно наполнить один раз из предрасчитанных
артефактов в `SHARED/` (spaCy-факты + OpenAI-эмбеддинги, собранные
`scripts/embed_facts.py`). Без этого шага `/rag/query` на каждый вопрос
отвечает «нет данных».

`compose.prod.yml` монтирует `SHARED` в контейнер read-only как `/shared`
(`./services/nornickel-2026-parser/SHARED:/shared:ro`), поэтому загрузчик
запускается без ручного `-v`. Эмбеддинги берутся из `vectors/` — **новых
вызовов OpenAI при загрузке нет**; Neo4j должен быть healthy. Запускать из
корня репозитория:

```bash
docker compose -f compose.yml -f compose.prod.yml run --rm \
  science-knowledge-graph \
  uv run python scripts/load_precomputed_facts.py \
    /shared/facts/facts \
    /shared/vectors \
    --md-dir /shared/RAW_DATA_646/RAW_DATA
```

Три входа: `facts/facts` — JSON с фактами; `vectors` — предрасчитанные
`entities.npy`/`.jsonl`; `--md-dir` — исходные тексты для `Document.text` (без
него источники в ответах не подтянутся). Загрузка идёт в 2 прохода (структура
графа → backfill эмбеддингов). Флаг `--skip-existing` пропускает уже
загруженные документы. Проверка после загрузки:

```bash
docker compose -f compose.yml -f compose.prod.yml exec neo4j \
  cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "MATCH (e:Entity) WITH count(e) AS ents MATCH (d:Document) RETURN ents, count(d) AS docs;"
```

Альтернатива: перенести уже наполненную БД — скопировать папку `neo4j-data/`
(при остановленном `neo4j`), тогда загрузчик не нужен.

## Demo data

```bash
docker compose exec science-knowledge-graph uv run python -m scripts.load_sample load
docker compose exec science-knowledge-graph uv run python -m scripts.load_sample query --material ВТ6
```

`data/sample_docs.json` is the default seed for `load`. For PDF ingestion demo,
POST your own file to `/api/v1/documents/pdf` (large demo PDFs are not kept in the repo).

## GraphRAG and chat

`/rag/query` always calls the LLM: it distinguishes casual messages (greetings)
from domain questions, including when the subgraph is empty. `backend` chat and
`generate_hypothesis` use the `answer` field directly — no pre-filter on
`matched_entities`. See `rag/generator.py` and spec patch §3.
