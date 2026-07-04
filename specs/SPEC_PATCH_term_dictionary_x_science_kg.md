# Патч: term_dictionary + vector search + GraphRAG hi-fix → science-knowledge-graph

> **Статус:** реализовано в PR #20 (`HOTFIX/science_knowledge_graph`, коммит `d6c20b8`).
> Исходный контекст: ветка `feature/extraction-benchmark` — бенчмаркнутый словарь
> (EntityRuler-паттерны, synonym map) для RU/EN металлургического домена.

---

## §1. Что интегрировано

`term_dictionary` по-прежнему целится в Postgres через `workers/etl` (SPEC_V5).
Этот патч — **второе, независимое** использование тех же артефактов внутри
`science-knowledge-graph`.

**Скопированные артефакты** (`science_kg/data/`, статический снапшот — не live path-dependency):

| Файл | Содержимое | Использование |
|------|------------|---------------|
| `entity_ruler_patterns.jsonl` | 924 паттерна → 924 после фильтра по лейблам | `nlp/term_dictionary_patterns.py` → `patterns.py` |
| `synonym_map.json` | 503 concept-кластера | fallback в `nlp/normalizer.py` |

**Не интегрировано** (вне скоупа): `abbreviations.json`, Postgres seed-файлы,
`term_dictionary` как runtime-зависимость в `pyproject.toml`.

**Почему снапшот, а не path-dependency:** science-kg не тянет LaBSE/numpy из
`term_dictionary/construction/`; достаточно минимального JSON-loader'а в
`term_dictionary_patterns.py`. Обновление — перекопировать файлы из
`feature/extraction-benchmark` и прогнать smoke на `desalination` / `опреснение`.

---

## §2. Маппинг лейблов

В **скопированном** `entity_ruler_patterns.jsonl` только четыре лейбла
(не ontology-контракт из README term_dictionary):

| Источник | `science_kg.EntityType` | Кол-во паттернов |
|----------|-------------------------|------------------|
| `MATERIAL` | `MATERIAL` | 677 |
| `PROCESS` | `REGIME` | 97 |
| `PROPERTY` | `PROPERTY` | 95 |
| `EQUIPMENT` | `EQUIPMENT` | 55 |

`LAB`, `DOCUMENT`, `PERSON`, `EXPERIMENT`, `QUANTITY_KIND` в снапшоте **отсутствуют** —
loader отбрасывает неизвестные лейблы на всякий случай (`LABEL_MAP` в
`term_dictionary_patterns.py`).

---

## §3. Точки интеграции (реализовано)

### NLP-слой

1. **`nlp/patterns.py`** — `ALL_PATTERNS = HAND_WRITTEN_PATTERNS + load_mapped_patterns()`.
   Hand-written идут первыми (приоритет на конфликтующих span'ах).

2. **`nlp/normalizer.py`** — hand-written alias map → synonym_map fallback.
   Только кластеры с `needs_review: false`. Текст, похожий на химическую формулу
   (`TiN`, `Ti-6Al-4V`), **не** проходит через synonym fallback (`_looks_like_formula`).

3. **`nlp/term_dictionary_patterns.py`** — минимальный reader jsonl + `LABEL_MAP`.

### Vector search (§7, см. ниже)

Затронуты `embeddings.py`, `graph/neo4j_client.py`, `rag/retriever.py`,
`api/routes.py`, `scripts/backfill_embeddings.py`.

### GraphRAG hi-fix

Затронуты `rag/generator.py`, `backend/app/services/chat.py`,
`backend/app/services/agent/__init__.py`.

**Проблема:** при пустом subgraph `generate_answer()` возвращал hardcoded
«The knowledge graph does not contain…» — backend воспринимал это как валидный
ответ и показывал на «привет» / «супер».

**Решение:** `generate_answer()` **всегда** вызывает LLM; system prompt различает
casual conversation (правило 1) и domain-вопросы без контекста (правило 2).
Backend больше не фильтрует по `matched_entities` — доверяет полю `answer`.

---

## §4. Vector search в GraphRAG

### Модель эмбеддингов (решение)

**Реализовано:** `text-embedding-3-small`, **1536-dim**, через тот же
OpenAI-compatible proxy, что и chat (`OPENAI_API_KEY`, `OPENAI_BASE_URL`).

**Не реализовано:** `intfloat/multilingual-e5-large` (768-dim) из SPEC_V3 §6.

**Обоснование:** не тащить sentence-transformers в Docker-образ; один провайдер
для chat + embeddings; проще деградация без ключа. Расхождение с SPEC_V3
зафиксировано явно в `science_kg/embeddings.py`.

### Neo4j

```cypher
CREATE VECTOR INDEX entity_embedding_idx IF NOT EXISTS
FOR (e:Entity) ON (e.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}
```

`e.embedding` — `LIST<FLOAT>` (Community Edition). Индекс создаётся в
`bootstrap_schema()`.

### Write-path

`api/routes.py::_compute_embeddings` — по одному `embed_text()` на уникальный
текст сущности/endpoint'а relation; результат передаётся в
`upsert_entities` / `upsert_relations`. Без API key embedding пропускается
(graceful degradation).

### Read-path

`retriever.retrieve()` — два канала, union по `text`:

1. **CONTAINS** — извлечённые термины вопроса → `_find_nodes`.
2. **Vector** — `embed_text(question)` → `vector_search(k=10)` → anchor nodes.

Далее 2-hop neighbourhood для каждого anchor. CONTAINS не заменяется.

### Backfill

`scripts/backfill_embeddings.py` — для узлов, залитых до появления embeddings:

```bash
docker compose exec science-knowledge-graph uv run python -m scripts.backfill_embeddings
```

---

## §5. Вне скоупа / отложено

- `workers/etl` / Postgres `entity_aliases` — отдельная задача.
- `abbreviations.json` — не подключён.
- Batching embedding API (сейчас N вызовов на документ).
- Переход на `multilingual-e5-large` — отдельное решение, если понадобится
  offline/локальный инференс без OpenAI.
- Бинарные demo PDF в `corpus/` удалены из репо (размер); для PDF-демо —
  свой файл через `POST /api/v1/documents/pdf` или `data/sample_docs.json`.

---

## §6. Файлы по компонентам

| Компонент | Файлы |
|-----------|-------|
| term_dictionary NLP | `data/entity_ruler_patterns.jsonl`, `data/synonym_map.json`, `nlp/term_dictionary_patterns.py`, `nlp/patterns.py`, `nlp/normalizer.py` |
| Embeddings | `embeddings.py`, `graph/neo4j_client.py` (`vector_search`, `set_embedding`, `list_entities_missing_embedding`) |
| Ingest | `api/routes.py` |
| Retrieval | `rag/retriever.py` |
| RAG generation | `rag/generator.py` |
| Backend wiring | `backend/.../chat.py`, `backend/.../agent/__init__.py` |
| Ops | `scripts/backfill_embeddings.py` |
