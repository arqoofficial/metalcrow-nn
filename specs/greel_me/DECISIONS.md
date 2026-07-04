# Grilling — свод решений (49 вопросов)

> Полная история: `GREEL_ME.md`

---

## Данные и онтология

| # | Решение |
|---|---------|
| 1 | **B** — Postgres нормализованный (experiments/materials/results); flat view для search; Neo4j — проекция |
| 2 | **C** — Гибридная граница Experiment + `source_anchor` + `grouping_key` |
| 3 | **B** — Material типизированный: composition для сплавов, SMILES только для молекул |
| 8 | **B→C** — Property/Regime: словари YAML сначала, автоканонизация по необходимости |
| 14 | **C** — DEDUP-LINK: правила по типу сущности + embedding fallback (P1) |
| 31 | **C** — Regime buckets в `regime_buckets.yaml` (default: <400 / 400–800 / >800 °C) |
| 32 | **C+** — Custom distance: d_comp + d_reg + d_embed с условными весами |

## Архитектура и инфра

| # | Решение |
|---|---------|
| 4 | **A** — 6+ микросервисов (договорённость команды) |
| 5 | **A** — Shared `packages/contracts/` + API Gateway |
| 24′ | **uv workspace** monorepo; Docker Compose — primary path (см. UV Policy в `greel_me_21_30.md`) |
| 34 | **A** — Gateway: nginx (SSE, rate limit ingest) |
| 36 | **A** — full-stack-fastapi-template = Chat Service + frontend |
| 38 | **C** — `packages/db/` + Alembic; schemas: auth, chat, experiments, staging |
| 12 | **C** — Write isolation: DB roles + internal network + upload validation |
| 16 | **C** — Deploy: VPS primary + laptop fallback |

## P0 / P1 / P2

| # | Решение |
|---|---------|
| 6 | **P0:** CSV ingest, hybrid search, chat+provenance, live reindex, 100% proof |
| 6 | **P1:** Wiki, gap heatmap, graph subgraph, custom metric |
| 6 | **P2:** hybrid scoring, mini-graph, UniExtract hold-out, path finding; BMF — слайд |
| 41 | **B+** — Hypothesis Factory: Chat tool `generate_hypothesis` (P1); KPI через чат |
| 42 | **A** — Extended claims: `kind: hypothesis` + `gap_cell` + P2 scores |
| 43 | **A** — Gap-click: `metadata.trigger` + `gap_cell` в message body |
| 44 | **D** — P2 scoring: heuristic novelty/value + LLM risk + `score_rationale` |
| 45 | **C** — Split validator: hypothesis soft, fact strict (Q7) |
| 46 | **B** — Mini-graph: opt-in `get_subgraph(depth=1, max_nodes=12)` |
| 47 | **C** — UniExtract: hold-out pre-bake + live 1 PDF; max 3 API calls |
| 48 | **A** — Path finding: Neo4j `shortestPath`; 503 без SQL fallback |
| 49 | **A** — BMF слайд: «architecture ready», heuristic MVP live |

## Search, Chat, Agent

| # | Решение |
|---|---------|
| 10 | **B** — Search pipeline: SQL pre-filter → BM25+vector+custom → RRF; rerank=false default |
| 7 | **C** — Chat: structured `claims[]` + validator |
| 22 | **B** — Agent tools: hybrid_search + sql_filter/aggregate + get_experiment_details |
| 22+ | **+P1** `generate_hypothesis`; **+P2** `get_subgraph` (см. Q41, Q46) |
| 23 | **C** — LLM: primary + fallback + degraded mode (таблица без summary) |
| 11 | **B→C** — Graph: Cypher templates; LLM→Cypher — P2 |
| 35 | **D** — Чаты персистентны в Postgres, привязаны к user (JWT) |
| 37 | **C** — Ingest: только `is_superuser` |

## Pipeline и данные

| # | Решение |
|---|---------|
| 13 | **B** — Reindex: 9 стадий linear pipeline с progress API |
| 9 | **A** — Hold-out: файлы целиком + demo script (3 вопроса) |
| 18 | **C** — Day-1 triage: structured first, PDF не блокирует P0 |
| 27 | **B** — PDF: Marker → LangExtract; UniExtract opt-in |
| 26 | **C** — Neo4j: full wipe+batch; SQL fallback для subgraph |
| 29 | **C** — Embeddings: API batch reindex + CPU query embed + Redis cache |
| 33 | **B** — Advanced Science: slice 500–1500 строк (Pd/Ni) |
| 39 | **B** — Sources: presigned MinIO URL, JWT required |

## Frontend, QA, процесс

| # | Решение |
|---|---------|
| 15 | **B** — P0 UI: /chat + /ingest; P1 stubs в nav |
| 19 | **C** — KPI: /metrics + eval/ + run_eval.py |
| 20 | **B** — Gap heatmap: Material × Property × Regime bucket |
| 21 | **C** — Wiki: Jinja + LLM summary (кэш) |
| 25 | **C** — Git: feature branches + merge windows 12/16/20 |
| 28 | **B** — Tests: contract + smoke_p0.py |
| 30 | **C** — Презентация: problem → demo → KPI → 1 слайд tech |
| 40 | **C** — Артефакты: greel_me/ + DECISIONS.md + SPEC_PATCH.md |

## UV Policy (обязательно)

- uv workspace, единый `uv.lock` в git
- `exclude-newer = "7 days"` в корневом pyproject.toml
- Только `uv sync --locked` / `uv add` + PR lockfile
- Owner `uv.lock`: **Data Engineer**
- Никакого `pip install` в рабочих окружениях
