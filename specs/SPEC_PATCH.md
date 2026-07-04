# SPEC v1.0 → v1.1 — Patch List

> На основе grilling-сессии (49 вопросов).  
> См. `DECISIONS.md` для полного контекста.

---

## §3 Архитектура

**ДОБАВИТЬ:**
- API Gateway (nginx) — единственный public port `:8080`
- `packages/contracts/`, `packages/db/`, `packages/common/` в monorepo
- uv workspace — обязательный менеджер зависимостей
- Chat Service на базе [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)
- Worker (Celery) — без public HTTP port

**УТОЧНИТЬ:**
- Read/write split: Postgres roles `reader` / `writer` / `chat_app` / `migrator`
- Neo4j — проекция из Postgres (full wipe + batch); SQL fallback для subgraph

---

## §4 Модель данных

**ЗАМЕНИТЬ** плоскую таблицу `experiments` как канон:

```text
Канон: нормализованные таблицы (schema experiments.*)
  materials, experiments, results, regimes, properties, documents
  entity_aliases, entity_same_as

Search projection: MATERIALIZED VIEW experiments_flat

Experiment граница: гибрид (Q2)
  source_anchor, grouping_key

Material (Q3):
  material_type, composition JSONB, smiles nullable
```

**ДОБАВИТЬ schemas Postgres:**
- `auth.*` — users (template)
- `chat.*` — chat_sessions, chat_messages
- `experiments.*` — доменные таблицы
- `staging.*` — worker temp

**ДОБАВИТЬ:**
- `documents` (id, minio_key, filename) для provenance download
- `dictionaries/regime_buckets.yaml`, `distance_weights.yaml`

---

## §5 Ключевые функции

**§5.2 Hybrid Search — уточнить pipeline:**
1. SQL pre-filter
2. BM25 + vector + custom (d_comp + d_reg + d_embed) внутри candidates
3. RRF (custom weight 1.5)
4. LLM rerank — opt-in, default false

**§5.4 Gap Analysis — P1 scope:**
- Heatmap Material × Property × Regime bucket (YAML config)
- Клик на gap → чат с `metadata.gap_cell` → tool `generate_hypothesis` (Q41–43)
- Tensor / BMF — не кодим; слайд «architecture ready» (Q49)

**§5.7 Hypothesis Factory — уточнить (Q41–45):**
- P1: Chat tool `generate_hypothesis` (sql_aggregate → hybrid_search → LLM)
- Claim `kind: "hypothesis"` + optional `gap_cell`; validator split (hypothesis soft, fact strict)
- P2 opt-in: hybrid scoring — heuristic `novelty`/`value`, LLM `risk`, `score_rationale`
- KPI side quest: через текст чата, без отдельного Analytics endpoint

**§5.6 Wiki — уточнить:**
- Jinja templates + optional LLM summary paragraph

---

## §6 Технологический стек

**ДОБАВИТЬ:**

| Компонент | Технология |
|-----------|------------|
| Gateway | nginx |
| Dependencies | uv workspace + uv.lock |
| Auth/Users/Chat base | full-stack-fastapi-template |
| Migrations | Alembic в packages/db/ |
| Chat sessions | Postgres (не Redis) |

**УТОЧНИТЬ embeddings (Q29):**
- Reindex: API batch embed
- Query: CPU e5-large в Search Service + Redis cache

---

## §8 API Design

**§8.3 Chat — ИЗМЕНИТЬ response:**

```json
{
  "claims": [{
    "text": "...",
    "experiment_ids": ["uuid"],
    "confidence": "high",
    "kind": "fact",
    "gap_cell": null,
    "novelty": null,
    "risk": null,
    "value": null
  }],
  "summary": "...",
  "tools_used": ["hybrid_search"],
  "subgraph": null,
  "session_id": "uuid"
}
```

**§8.3 Chat — message request (Q43):**
```json
{
  "content": "...",
  "metadata": {
    "trigger": "gap_click",
    "gap_cell": {"material_id": "uuid", "material": "...", "property": "...", "regime_bucket": "..."}
  }
}
```

**Agent tools (Q22 + Q41, Q46):**
- P1: `generate_hypothesis` — обязателен при `metadata.trigger == "gap_click"`
- P2: `get_subgraph(entity_ids, depth=1, max_nodes=12)` → optional `subgraph` в response

**ДОБАВИТЬ endpoints:**

```text
GET  /api/v1/chat/sessions
POST /api/v1/chat/sessions
GET  /api/v1/chat/sessions/{id}
POST /api/v1/chat/sessions/{id}/messages   # SSE

GET  /api/v1/sources/{doc_id}/download     # presigned MinIO URL

GET  /api/v1/metrics                       # KPI dashboard

GET  /api/v1/graph/path?from={id}&to={id}&max_depth=4   # P2, Neo4j shortestPath (Q48)
```

**Graph Service:** template_id + params (не raw Cypher от клиента); path unavailable → 503 при Neo4j down

---

## §9 UI/UX

**P0 экраны (уточнить):**
- `/login`, `/register` (template)
- `/chat` + session sidebar (P0, не optional)
- `/ingest` — superuser only

**P1 stubs:** `/wiki`, `/graph`, `/analytics/gaps`
- Gap heatmap: клик на ячейку → `/chat` с prefilled `metadata.gap_cell`
- P2: `/graph` From/To path highlight; optional `<MiniGraph>` в chat bubble

---

## §7 Ingestion — UniExtract (Q47)

- Default: Marker → LangExtract (Q27)
- P2: `parser=uniextract` flag; hold-out PDF pre-baked в seed
- Budget cap: max 3 UniExtract API calls (зафиксировать в `demo_script.md`)

## §10 Workflow

**ДОБАВИТЬ pre-flight (Q17):**
- contracts v0.1, uv.lock frozen, seed + holdout, dictionaries, demo_script.md
- full-stack-fastapi-template поднят до хакатона

**ДОБАВИТЬ process (Q25):**
- Merge windows: 12:00, 16:00, 20:00
- uv.lock owner: Data Engineer

---

## §12 Out of Scope — ИЗМЕНИТЬ

**УБРАТЬ:**
- ~~Авторизация и управление правами — open access в рамках demо~~

**ДОБАВИТЬ in scope:**
- JWT auth из template (login/register)
- Ingest/upload — только `is_superuser`
- RBAC минимальный: user vs superuser

**ОСТАВИТЬ out of scope:**
- Полноценный RBAC, OAuth, LDAP
- Мультитенантность

---

## §11 Риски — ДОБАВИТЬ

| Риск | Митигация |
|------|-----------|
| uv.lock конфликты | Один owner (DE) |
| LLM API down | Degraded mode + fallback provider |
| Neo4j sync fail | SQL subgraph fallback |
| PDF parsing slow | Marker ladder (Q27), не блокирует P0 |
| UniExtract API cost | Max 3 API calls; pre-bake hold-out PDFs (Q47) |

---

## Приложение B — ДОБАВИТЬ

- Advanced Science: использовать curated slice 500–1500 строк (Pd/Ni/superalloy), не 90k целиком
