# Научный клубок — Техническая спецификация V3

> **Проект:** Knowledge Graph / поисково-аналитическая система для НИОКР
> **Команда:** 5 человек
> **Формат:** хакатон, time-boxed delivery
> **Версия:** 3.0 — консолидированная спецификация для старта разработки
> **Методология разработки:** [CODE_WITH_AGENTS.md](wiki/CODE_WITH_AGENTS.md), адаптация под этот проект — §10

Этот документ объединяет предыдущие итерации (V0–V2) в один самодостаточный источник правды: постановку проблемы, доменную модель, финальную архитектуру (модульный монолит на базе [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)), API-контракты, технологический стек, роли команды и риски. Исторический ход ревизий — в `SPEC_V0.md`/`SPEC_V1.md`/`SPEC_V2.md`, здесь он не повторяется. Всё, что нужно, чтобы взять этот документ и начать параллельную разработку, находится ниже.

---

## §1. Постановка проблемы

### Боль

Исследователи Норникеля работают с большим объёмом неструктурированных данных: внутренние отчёты, протоколы экспериментов, справочники материалов и оборудования, реестры сотрудников и лабораторий. Эти данные разрозненны — хранятся в PDF, DOC, таблицах, каталогах — и не связаны между собой. Чтобы ответить на вопрос *«что уже делали по сплавам X при режиме Y и какой был эффект на свойство Z?»*, исследователь вынужден вручную перебирать десятки документов, полагаться на память коллег, не видеть пробелов в экспериментальном покрытии и не иметь прозрачной истории решений с источниками.

### Пользователи

| Роль | Потребность |
|------|-------------|
| **R&D-инженер** | Быстро найти релевантные эксперименты по составу, режиму и свойству |
| **Руководитель лаборатории** | Видеть картину покрытия: какие эксперименты проведены, каких не хватает |
| **Аналитик** | Строить связи между материалами, условиями и результатами; генерировать отчёты с provenance |

---

## §2. Цели и метрики успеха

### Цели

1. Построить работающий онлайн-прототип, который принимает корпус документов и позволяет задавать вопросы в свободной форме
2. Связать сущности (материалы, эксперименты, свойства, режимы, оборудование, команды, выводы) в единое пространство знаний
3. Обеспечить прозрачность: каждый ответ содержит ссылку на первоисточник (provenance)
4. Выявлять пробелы — какие эксперименты не проводились, но логически следуют из имеющихся данных

### Метрики успеха (KPI)

| Метрика | Целевое значение | Способ измерения |
|---------|-------------------|-------------------|
| **Время ответа на вопрос** | < 15 секунд (онлайн-режим) | Замер от запроса до полного ответа |
| **Точность извлечения сущностей** | > 80% F1 на hold-out set | Ручная разметка 50–100 фрагментов |
| **Provenance coverage** | 100% утверждений со ссылкой | Автоматическая проверка наличия proof-ноды |
| **Полнота графа** | > 70% сущностей из корпуса связаны | Отношение связанных нод к изолированным |
| **Демонстрация online-добавления** | Успешная переиндексация hold-out за < 3 мин | Живое демо на презентации |
| **Кастомная RAG-метрика** | Выше baseline cosine similarity | A/B-сравнение на тестовых запросах |

**Инструментирование KPI:** `GET /api/v1/metrics` (роут внутри backend) + директория `eval/` с `run_eval.py`. Dashboard с реальными цифрами; F1/graph coverage — скриптом, если UI не успеваем.

---

## §3. Архитектура системы

### Высокоуровневая схема

Система — **модульный монолит**: один backend-процесс (FastAPI, Python 3.11) с доменами как `APIRouter`-модулями, поверх которого стоит ETL-контур из независимых Celery worker-образов.

```
┌────────────────────────────────────────────────────────────────────┐
│         OFFLINE — один или несколько Celery Worker-образов         │
│    (отдельные контейнеры, БЕЗ HTTP-порта, каждый — свой uv-проект  │
│     с независимым uv.lock; см. «Изоляция ML-зависимостей» ниже)    │
│                                                                     │
│  worker-etl:   PARSE → NORMALIZE → DEDUP-LINK → LOAD → BUILD-FLAT  │
│                (pandas, marker-pdf, langextract, spaCy, hdbscan)   │
│  worker-embed: EMBED (e5-large / CPU или batch API)                │
│  worker-graph: SYNC-NEO4J, BUILD-WIKI                               │
│  (набор образов — по факту найденных версионных конфликтов)        │
└───────────────────────────────┬─────────────────────────────────────┘
                                │  (читает/пишет через SQLModel в тот же Postgres)
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│                    STORAGE — общий для всех                        │
│                                                                     │
│  ┌──────────────────┐  ┌───────────┐  ┌──────────────────────────┐│
│  │ pgvector/pgvector │  │ Neo4j     │  │ MinIO (S3-compatible)    ││
│  │ :pg18             │  │ (опция,   │  │ PDF / DOC исходники      ││
│  │ schemas: public / │  │  P1)      │  │                          ││
│  │ experiments        │  └───────────┘  │ Redis (Celery broker +   ││
│  │                    │                 │ embed cache)            ││
│  └──────────────────┘                   └──────────────────────────┘│
└────────────────────────────────────────────┬─────────────────────────┘
                                             │
                                             ▼
┌────────────────────────────────────────────────────────────────────┐
│         ONLINE — один backend-контейнер (FastAPI, Python 3.11)      │
│         app/api/routes/*.py — по одному модулю на домен            │
│                                                                     │
│  auth.py (template)  chat.py     search.py    graph.py             │
│  users.py (template) wiki.py     analytics.py ingest.py (superuser)│
│                       sources.py                                    │
│                                                                     │
│  Один /docs, одна OpenAPI-схема, один пул соединений к Postgres     │
└───────────────────────────────┬─────────────────────────────────────┘
                                │  proxy_pass http://backend:8000 (internal network)
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│      FRONTEND-контейнер = build React SPA + nginx (единый          │
│      публичный порт: :5173 локально / :80 на сервере)              │
│                                                                     │
│  React 19 + TanStack Router (file-based) + TanStack Query          │
│  Tailwind v4 + shadcn/ui + автогенерируемый клиент (openapi-ts)    │
│                                                                     │
│  /login /signup /_layout/chat /_layout/wiki /_layout/graph         │
│  /_layout/analytics/gaps /_layout/ingest (superuser only)          │
└────────────────────────────────────────────────────────────────────┘
```

### Ключевые архитектурные решения

| # | Решение |
|---|---------|
| 1 | **Модульный монолит** — один FastAPI-процесс, домены = `APIRouter`-модули (`chat`, `search`, `graph`, `wiki`, `analytics`, `ingest`, `sources`) |
| 2 | **nginx фронтенд-контейнера — единственный публичный порт**, проксирует `/api/*` на backend по внутренней docker-сети; отдельного gateway-контейнера нет |
| 3 | **RBAC на уровне API** (`Depends(get_current_active_superuser)`), без разделения ролей на уровне Postgres |
| 4 | **uv workspace с одним пакетом `backend`** (внутреннее имя пакета — `app`) |
| 5 | **full-stack-fastapi-template — база всего проекта**, а не только чат-функциональности |
| 6 | **Celery + Redis** для ETL — тяжёлый 9-стадийный пайплайн не должен блокировать event loop онлайн-backend'а |
| 7 | **MinIO** — object storage для provenance-PDF с presigned URL |
| 8 | **Neo4j — опциональное добавление (P1)**, первый кандидат на отрез при нехватке времени |
| 9 | **Полная переиндексация** вместо инкрементальных миграций (~2.5 мин), выполняется в Celery worker |
| 10 | **Провенанс по умолчанию** — каждый факт хранит ссылку на PDF-источник |
| 11 | **Один или несколько Worker-образов с независимыми `uv.lock`** — деление не по «домену», а по факту найденных конфликтов зависимостей; ни один worker не получает публичный HTTP-порт |
| 12 | **PostgreSQL 18** (образ `pgvector/pgvector:pg18`, не ванильный `postgres:18` — иначе не установится расширение `vector`) |
| 13 | **Python 3.11** для backend (зафиксировано в `Dockerfile`/`pyproject.toml`/`.python-version`) |

Прямое следствие монолита: agent tools (`hybrid_search`, `sql_aggregate`, `graph_template`, `generate_hypothesis`, ...) вызываются как обычные Python-функции сервисного слоя внутри одного процесса, а не HTTP-запросами к соседним контейнерам — меньше сетевых точек отказа, ниже latency, не нужен internal service discovery/retry, и degraded mode проще: единственные сетевые failure mode — «LLM недоступен» и «Neo4j недоступен» (см. §11).

### Структура репозитория

```
metalcrow/
├── compose.yml                    # добавить: redis, minio, worker-*, (опц.) neo4j
├── compose.override.yml           # dev overrides
├── compose.prod.yml               # прод-оверлей, публикует только frontend:80
├── pyproject.toml                 # uv workspace root, members = ["backend"]
├── uv.lock                        # lockfile backend-workspace — НЕ включает workers/*
├── packages/
│   └── schema/                    # опционально: общие SQLModel-таблицы (experiments.*),
│                                   # почти без зависимостей (sqlmodel, pydantic) — path-dependency
│                                   # для backend И для каждого worker'а по отдельности
├── backend/
│   ├── Dockerfile                 # Python 3.11 + uv
│   ├── alembic.ini
│   ├── pyproject.toml             # package name "app"
│   └── app/
│       ├── main.py
│       ├── api/
│       │   ├── main.py            # регистрация роутеров
│       │   ├── deps.py            # CurrentUser, get_current_active_superuser
│       │   └── routes/
│       │       ├── login.py, users.py, utils.py, private.py   # из template
│       │       ├── chat.py        # sessions + SSE messages
│       │       ├── search.py      # hybrid search
│       │       ├── graph.py       # template-based Cypher / SQL fallback
│       │       ├── wiki.py
│       │       ├── analytics.py   # gaps, coverage, /metrics
│       │       ├── ingest.py      # upload, reindex, status (superuser)
│       │       └── sources.py     # presigned MinIO URL
│       ├── models/                # пакет SQLModel: materials.py, chat.py, experiments.py, ...
│       ├── services/              # бизнес-логика вне роутеров
│       │   ├── search.py          # BM25 + vector + custom + RRF
│       │   ├── graph.py
│       │   ├── wiki.py
│       │   ├── analytics.py
│       │   ├── agent/             # LLM-агент, tools, claims validator
│       │   └── embeddings.py
│       └── alembic/versions/      # миграции для experiments.* + chat.*
├── workers/                       # каждый — отдельный uv-проект (свой pyproject.toml + uv.lock),
│   │                               # НЕ workspace-member backend'а
│   ├── etl/                       # Celery worker: PARSE/NORMALIZE/DEDUP-LINK/LOAD/BUILD-FLAT
│   │   ├── Dockerfile             # Python 3.11 + uv (при конфликте deps — другая минорная версия или расщепление образов)
│   │   ├── pyproject.toml         # pandas, marker-pdf, langextract, spacy, hdbscan, packages/schema
│   │   ├── uv.lock
│   │   └── tasks/                 # parse.py, normalize.py, dedup_link.py, load.py, build_flat.py
│   ├── embed/                     # Celery worker: EMBED
│   │   ├── Dockerfile
│   │   ├── pyproject.toml         # sentence-transformers/torch, packages/schema
│   │   └── uv.lock
│   └── graph/                     # Celery worker: SYNC-NEO4J, BUILD-WIKI
│       ├── Dockerfile
│       ├── pyproject.toml         # neo4j-driver, jinja2, packages/schema
│       └── uv.lock
├── frontend/                      # React 19 + TanStack Router/Query
│   └── src/routes/_layout/        # chat.tsx, wiki.tsx, graph.tsx, analytics/gaps.tsx, ingest.tsx
├── dictionaries/                  # regime_buckets.yaml, distance_weights.yaml, synonyms.yaml
├── eval/                          # queries.json, run_eval.py
├── seed/                          # CSV/JSON для начальной загрузки
├── holdout/                       # файлы для live demo
└── .github/workflows/             # test-backend / playwright / pre-commit
```

### Dependency policy (uv)

| Правило | Детали |
|---------|--------|
| Lockfile в git | `uv.lock` в корне — единственный источник версий backend-workspace |
| Локальная разработка | `uv sync` в `backend/` или через workspace-команду из корня |
| Docker build | `uv sync --frozen --package app` |
| Добавление deps | `uv add <pkg>` в `backend/pyproject.toml` → PR + lockfile |
| Worker | Каждый worker в `workers/*` — **свой** `pyproject.toml`/`uv.lock`, не workspace member backend'а |
| Frontend | `bun.lock` в git, тот же принцип |

### Изоляция ML-зависимостей — несколько worker-образов и internal-only sidecar

Тяжёлый ML/ETL-стек (`torch`, `sentence-transformers`/e5-large, `spacy` + `ru_core_news_lg`, `marker-pdf`, `langextract`, `hdbscan`) — это ETL/NLP-пайплайн (стадии PARSE/NORMALIZE/DEDUP-LINK/EMBED), физически отделённый от лёгких онлайн-доменов (chat/search/graph/wiki/analytics/ingest-как-приём-файла: FastAPI, LangChain, JWT, Postgres/Neo4j/MinIO-клиенты).

**Правило изоляции:** настоящая изоляция версий — это не «несколько member'ов одного uv-workspace» (у него один общий `uv.lock`, и несовместимые transitive-констрейнты уронят весь workspace), а **несколько независимых uv-проектов**, каждый со своим `pyproject.toml` и своим `uv.lock`, которые не обязаны ничего резолвить совместно.

**Итоговая схема:**

- **Backend** (`backend/`) — один uv-проект, только лёгкий онлайн-стек. ML/ETL-библиотеки в него не добавляются вообще.
- **Worker(ы)** (`workers/etl/`, `workers/embed/`, `workers/graph/`) — каждый свой uv-проект, свой `Dockerfile` (Python 3.11 по умолчанию, как у backend; при конфликте deps — другая минорная версия или расщепление образов), свой `uv.lock`. Нарезка определяется по факту: сначала пробуем собрать всё в один `workers/etl/pyproject.toml`; если `uv lock` падает на несовместимых constraints — расщепляем конфликтующие библиотеки по разным worker-образам/очередям Celery.
- **Ни один worker не получает публичный HTTP-порт** — они читают задачи из Redis (Celery) и пишут прямо в Postgres/MinIO/Neo4j.
- **Общие таблицы без общего lockfile** — table-классы `experiments.*` можно вынести в маленький пакет `packages/schema/` (только `sqlmodel`+`pydantic`). Backend и каждый worker подключают его как path-dependency в своём собственном `pyproject.toml`.
- **Онлайн CPU-инференс эмбеддинга запроса в `/api/v1/search`** — либо считаем через внешний API (тот же провайдер, что для батчей при reindex), либо выносим в internal-only sidecar-контейнер без публичного порта (`http://embed-internal:8010/embed`, дергается backend'ом по внутренней docker-сети).

### nginx (frontend-контейнер) — конфиг для SSE

`frontend/nginx.conf` проксирует `/api/` на backend. Для чата (SSE) отдельный `location`:

```nginx
location /api/v1/chat/ {
  proxy_pass http://backend:8000;
  proxy_http_version 1.1;
  proxy_set_header Connection "";
  proxy_buffering off;
  proxy_cache off;
  proxy_read_timeout 300s;
  chunked_transfer_encoding on;
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
}
```

Rate limit на `/api/v1/ingest/` — по желанию, через `limit_req_zone` в основном `nginx.conf`.

---

## §4. Модель данных / Онтология

### Каноническая модель

```
┌────────────┐     проведён_в      ┌──────────────┐
│ Experiment │────────────────────▶│  Lab          │
│            │                     │              │
│ id (UUID)  │     выполнен        │ id           │
│ title      │────────────────────▶│ name         │
│ date       │                     │ organization │
│ description│  ┌──────────────┐   └──────────────┘
│ proof_ref  │──│  Researcher  │
└─────┬──────┘  │              │
      │         │ id, full_name│
      │         │ lab_id (FK)  │
      │         │ role         │
      │         └──────────────┘
      │
      ├── использует ──▶ ┌────────────────┐
      │                  │  Material       │
      │                  │                │
      │                  │ id             │
      │                  │ name           │
      │                  │ material_type  │  ← alloy | compound | pure_metal
      │                  │ formula        │
      │                  │ composition    │  ← JSONB (для alloy)
      │                  │ smiles         │  ← nullable (только для молекул)
      │                  │ aliases[]      │
      │                  └────────────────┘
      │
      ├── при_режиме ──▶ ┌────────────┐
      │                  │   Regime     │
      │                  │            │
      │                  │ id         │
      │                  │ temperature│
      │                  │ pressure   │
      │                  │ duration   │
      │                  │ medium     │
      │                  │ steps[]    │
      │                  └────────────┘
      │
      ├── на_установке ─▶ ┌──────────────┐
      │                   │ Equipment    │
      │                   │              │
      │                   │ id           │
      │                   │ name         │
      │                   │ type         │
      │                   │ lab_id (FK)  │
      │                   └──────────────┘
      │
      ├── измеряет ────▶ ┌────────────┐
      │                  │ Property   │
      │                  │            │
      │                  │ id         │
      │                  │ name       │
      │                  │ unit       │
      │                  │ category   │
      │                  └────────────┘
      │
      └── имеет_результат ▶ ┌────────────┐
                             │ Result     │
                             │            │
                             │ id         │
                             │ value      │
                             │ unit       │
                             │ uncertainty│
                             │ proof_ref  │
                             └────────────┘
```

### Postgres-схемы

| Схема | Таблицы | Источник |
|-------|---------|----------|
| `public` | `user` (template), `chat_session`, `chat_message` | template + расширение |
| `experiments` | `materials`, `experiments`, `results`, `regimes`, `properties`, `equipment`, `labs`, `researchers`, `documents`, `entity_aliases`, `entity_same_as` | Domain |

Роли на уровне Postgres не заводим — вся система живёт в одном backend-процессе с одним DB-пользователем; RBAC — на уровне API (`is_superuser`, см. §12). Для схемы `experiments` в `backend/app/alembic/env.py` выставляем `include_schemas=True` и `version_table_schema`, на каждой доменной SQLModel-модели — `__table_args__ = {"schema": "experiments"}`. Промежуточное состояние воркера (staging) хранится не в отдельной схеме, а колонкой-флагом (`status: draft|committed`) на строках `experiments.*` либо в JSON-поле `staging_payload` на `documents`.

### Нормализованные таблицы (schema `experiments`)

```sql
-- Материалы
CREATE TABLE experiments.materials (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    material_type   TEXT NOT NULL CHECK (material_type IN ('alloy', 'compound', 'pure_metal')),
    formula         TEXT,
    composition     JSONB,              -- {"Pd": 0.6, "Cu": 0.4} для alloy
    smiles          TEXT,               -- nullable, только для молекул
    embedding       vector(768),
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Алиасы сущностей
CREATE TABLE experiments.entity_aliases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type     TEXT NOT NULL,      -- 'material' | 'property' | 'regime' | ...
    entity_id       UUID NOT NULL,
    alias           TEXT NOT NULL,
    source          TEXT                -- откуда алиас
);

-- Дедупликация: связь дубликатов
CREATE TABLE experiments.entity_same_as (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type     TEXT NOT NULL,
    source_id       UUID NOT NULL,
    canonical_id    UUID NOT NULL,
    confidence      FLOAT DEFAULT 1.0,
    method          TEXT                -- 'exact_alias' | 'embedding' | 'manual'
);

-- Эксперименты
CREATE TABLE experiments.experiments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT,
    material_id     UUID REFERENCES experiments.materials(id),
    regime_id       UUID REFERENCES experiments.regimes(id),
    equipment_id    UUID,
    lab_id          UUID,
    researcher_id   UUID,
    date            DATE,
    description     TEXT,
    source_anchor   TEXT,               -- идентификатор блока в источнике
    grouping_key    TEXT,               -- (date, lab, researcher, material, regime) hash
    document_id     UUID REFERENCES experiments.documents(id),
    source_page     INT,
    source_paragraph TEXT,
    tags            TEXT[],
    embedding       vector(768),
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Результаты
CREATE TABLE experiments.results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id   UUID REFERENCES experiments.experiments(id),
    property_id     UUID REFERENCES experiments.properties(id),
    value           FLOAT,
    unit            TEXT,
    uncertainty     FLOAT,
    proof_ref       TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Режимы
CREATE TABLE experiments.regimes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    temperature     FLOAT,              -- Kelvin
    pressure        FLOAT,              -- Pa
    duration        FLOAT,              -- seconds
    medium          TEXT,
    steps           JSONB               -- [{step, temperature, duration, ...}]
);

-- Свойства
CREATE TABLE experiments.properties (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL UNIQUE,
    unit            TEXT,
    category        TEXT
);

-- Документы (provenance download)
CREATE TABLE experiments.documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    minio_key       TEXT NOT NULL,      -- ключ в MinIO
    filename        TEXT NOT NULL,
    mime_type       TEXT,
    uploaded_at     TIMESTAMPTZ DEFAULT now()
);

-- Лаборатории, исследователи, оборудование — аналогичные таблицы
```

### Search Projection — MATERIALIZED VIEW

```sql
CREATE MATERIALIZED VIEW experiments.experiments_flat AS
SELECT
    e.id,
    e.title,
    m.name          AS material_name,
    m.formula       AS material_formula,
    m.smiles        AS material_smiles,
    m.composition   AS material_composition,
    m.material_type,
    r.temperature, r.pressure, r.duration, r.medium,
    to_jsonb(r)     AS regime_json,
    p.name          AS property_name,
    res.value       AS property_value,
    res.unit        AS property_unit,
    res.uncertainty,
    eq.name         AS equipment_name,
    l.name          AS lab_name,
    rs.full_name    AS researcher,
    e.description   AS conclusion,
    d.filename      AS source_doc,
    e.source_page,
    e.source_paragraph,
    e.tags,
    e.embedding,
    e.created_at
FROM experiments.experiments e
LEFT JOIN experiments.materials m ON e.material_id = m.id
LEFT JOIN experiments.regimes r ON e.regime_id = r.id
LEFT JOIN experiments.results res ON res.experiment_id = e.id
LEFT JOIN experiments.properties p ON res.property_id = p.id
LEFT JOIN experiments.equipment eq ON e.equipment_id = eq.id
LEFT JOIN experiments.labs l ON e.lab_id = l.id
LEFT JOIN experiments.researchers rs ON e.researcher_id = rs.id
LEFT JOIN experiments.documents d ON e.document_id = d.id;

CREATE INDEX idx_flat_embedding ON experiments.experiments_flat
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_flat_material ON experiments.experiments_flat (material_name);
CREATE INDEX idx_flat_property ON experiments.experiments_flat (property_name);
CREATE INDEX idx_flat_tags ON experiments.experiments_flat USING GIN (tags);
```

`embedding vector(768)` требует `CREATE EXTENSION IF NOT EXISTS vector;`, доступного только на образе `pgvector/pgvector:pg18` (см. §6, §10 pre-flight).

### Граница `Experiment`

| Тип источника | Правило |
|---------------|---------|
| Структурированный каталог | 1 строка = 1 `Experiment` (документо-центричный) |
| Свободный текст | `(дата, лаборатория, исследователь, материал, режим)` + `source_anchor` + `grouping_key` |

### Граф (Neo4j) — проекция для визуализации (P1, опционально)

```cypher
(:Material)-[:USED_IN]->(:Experiment)
(:Experiment)-[:UNDER_REGIME]->(:Regime)
(:Experiment)-[:MEASURES]->(:Property)
(:Experiment)-[:PERFORMED_AT]->(:Lab)
(:Experiment)-[:BY]->(:Researcher)
(:Experiment)-[:ON_EQUIPMENT]->(:Equipment)
(:Experiment)-[:HAS_RESULT]->(:Result)
(:Experiment)-[:SOURCED_FROM]->(:Source)
(:Material)-[:RELATED_TO]->(:Material)
(:Experiment)-[:LEADS_TO]->(:Conclusion)
```

Синхронизация: **full wipe + batch** при каждом reindex, выполняется Celery-таском `SYNC-NEO4J`. Neo4j down → `503` для path-запросов; SQL fallback для subgraph API.

### Мета-сущности

| Сущность | Описание |
|----------|----------|
| **Источник (Document / Proof)** | Ссылка на конкретный документ в MinIO: файл, страница, абзац |
| **Тег/Тематика** | Классификационная метка (напр. «суперсплавы», «коррозия», «палладий») |
| **Вывод (Conclusion)** | Текстовая интерпретация результатов эксперимента с proof_ref |

### Словари и конфиги

| Файл | Содержимое |
|------|-----------|
| `dictionaries/regime_buckets.yaml` | Пороги bucket: `low: <400°C`, `medium: 400–800°C`, `high: >800°C` |
| `dictionaries/distance_weights.yaml` | Веса custom distance: `w_comp`, `w_reg`, `w_emb` |
| `dictionaries/synonyms.yaml` | Ручной словарь синонимов материалов, свойств, режимов |

---

## §5. Ключевые функции

### P0 / P1 / P2 приоритизация

| Приоритет | Фичи |
|-----------|------|
| **P0** | CSV/XLSX ingest, hybrid search + provenance, read-only chat со structured claims, live reindex hold-out, 100% provenance, JWT auth (login/signup) |
| **P1** | Wiki (Jinja + LLM summary), gap heatmap (Material × Property × Regime bucket), graph subgraph, custom distance metric, `generate_hypothesis` tool, extended claims `kind: hypothesis`, split validator |
| **P2** | Hybrid scoring (novelty/value/risk), opt-in mini-graph в чате, UniExtract hold-out PDF, path finding (Neo4j `shortestPath`), LLM→Cypher |
| **P2 stretch** | Tensor / BMF — слайд «architecture ready», без кода |

Все модули (`chat`, `search`, `graph`, `wiki`, `analytics`, `ingest`) — это `app/api/routes/<domain>.py` + `app/services/<domain>.py` внутри одного backend, а не отдельные контейнеры. Agent tools вызывают `app.services.search.hybrid_search(...)`, `app.services.graph.run_template(...)` и т.д. напрямую как Python-функции.

### §5.1. Связывание сущностей (Entity Linking)

- Извлечение именованных сущностей из текста (материалы, режимы, свойства)
- Нормализация: приведение синонимов к каноническим формам (Cu-Ni-сплав → купроникель)
- **DEDUP-LINK:**
  - P0: словарь + exact alias match
  - P1: правила по типу сущности + embedding fallback (HDBSCAN)
    - Material: `composition` / `alias` match
    - Property / Regime: словарь YAML
    - Researcher: fuzzy ФИО
  - Не удалять дубли, а связывать через `entity_same_as`

### §5.2. Гибридный поиск (Hybrid Retrieval)

Четырёхступенчатый pipeline:

```
1. SQL pre-filter        — WHERE material_name, temperature_min, tags
2. Candidate retrieval   — BM25 + vector + custom metric (если есть composition)
3. Reciprocal Rank Fusion — custom channel weight: 1.5
4. LLM rerank            — opt-in, default: false (P0 latency)
```

**Кастомная метрика расстояния:**

```
d_total = w_comp · d_comp + w_reg · d_reg + w_emb · d_embed

d_comp  — L1 на симплексе composition, норм. [0,1]
d_reg   — L1 по regime (temperature_k, pressure_pa, duration_s), min-max по корпусу
d_embed — 1 − cosine(e5_query, e5_experiment)

Default weights (P0):
  full:      w_comp=0.5, w_reg=0.3, w_emb=0.2
  no comp:   w_comp=0,   w_reg=0.2, w_emb=0.8
  comp only: w_comp=0.7, w_reg=0.3, w_emb=0

P1: weights → dictionaries/distance_weights.yaml
```

### §5.3. Граф-запросы (Graph Traversal)

- **P0/P1:** Cypher template library (5–8 шаблонов) + параметры
- **P2:** LLM → Cypher с whitelist операций
- Multi-hop запросы: «Какие свойства измерялись на сплавах, содержащих Pd, при температурах выше 800°C?»
- Визуализация подграфов в интерфейсе
- Модуль `graph` принимает `template_id` + `params` (не raw Cypher от клиента)
- Neo4j down → `503`; SQL fallback для subgraph (не для path)

### §5.4. Детекция пробелов (Gap Analysis)

**P1 scope:**
- Heatmap `Material × Property × Regime bucket` (buckets из YAML config)
- Клик на gap → чат с `metadata.gap_cell` → tool `generate_hypothesis`
- Tensor / BMF — не кодим; слайд «architecture ready» для жюри

### §5.5. История решений и Provenance

- Каждый факт в системе имеет `proof_ref` — ссылку на документ, страницу и абзац
- Timeline: хронология экспериментов по конкретному материалу/свойству
- Цепочки выводов: от эксперимента к заключению
- PDF-доступ: presigned MinIO URL через `GET /api/v1/sources/{doc_id}/download`

### §5.6. Wiki-страницы

- **Jinja-шаблоны** — детерминированный Markdown из SQL
- **+ LLM summary paragraph** — кэшируется, генерируется при reindex
- Три режима отображения: текст (Markdown), таблица, граф
- Полнотекстовый поиск по wiki

### §5.7. Генерация гипотез (Hypothesis Factory)

| Приоритет | Scope |
|-----------|-------|
| **P1 (core)** | Chat tool `generate_hypothesis`: `sql_aggregate → hybrid_search → LLM → structured claim` |
| **P2 (+6ч)** | Hybrid scoring: heuristic `novelty`/`value` + LLM `risk` + `score_rationale` |
| **P2 stretch** | BMF — только слайд в презентации |

**Pipeline `generate_hypothesis`:**
1. `sql_aggregate` — статистика по gap cell (соседи, coverage)
2. `hybrid_search` — поиск ближайших экспериментов
3. LLM — генерация гипотезы с structured claim

**Claim расширение:**
- `kind: "hypothesis"` + optional `gap_cell`
- P2: `novelty`, `risk`, `value`, `score_rationale`
- Validator split: hypothesis — soft (≥1 experiment_id, gap_cell required); fact — strict

**KPI side quest:** через текст чата, без отдельного Analytics endpoint.

---

## §6. Технологический стек

### Основной стек

| Слой | Технология |
|------|-----------|
| **Base template** | [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) (форк с shadcn/ui вместо Chakra) — база всего проекта, не только чата |
| **Backend** | FastAPI, **Python 3.11** |
| **ORM / миграции** | SQLModel + Alembic, один `alembic/versions/` на весь backend |
| **Auth** | JWT (`pyjwt`), `pwdlib[argon2,bcrypt]`, `is_superuser`-гейтинг |
| **Frontend** | React 19, TanStack Router (file-based routing) + TanStack Query, Tailwind v4, shadcn/ui (Radix), Biome (lint/format) |
| **API-клиент фронтенда** | Автогенерируемый (`@hey-api/openapi-ts`, `scripts/generate-client.sh`) из `openapi.json` backend'а |
| **Gateway / edge** | nginx внутри frontend-контейнера (`frontend/nginx.conf`) |
| **Основная БД** | `pgvector/pgvector:pg18` |
| **Task Queue** | Celery + Redis |
| **Хранилище файлов** | MinIO (S3-compatible) |
| **Графовая БД** | Neo4j Community (опционально, P1) |
| **Полнотекстовый поиск** | PostgreSQL FTS (tsvector) |
| **Парсинг PDF/DOC** | Marker → LangExtract (default); UniExtract — opt-in P2 |
| **Извлечение сущностей** | LangExtract + spaCy (ru) |
| **Эмбеддинги текста** | `intfloat/multilingual-e5-large` (768-мерные вектора, русский язык) |
| **Эмбеддинги молекул** | MatBERT / MolFormer (P1) |
| **LLM-агент** | LangChain + primary LLM + fallback LLM |
| **Chat sessions** | PostgreSQL (персистентные, per user) |
| **Визуализация графа** | react-force-graph / D3.js |
| **Контейнеризация** | Docker + Docker Compose (`compose.yml` + `compose.override.yml` dev + `compose.prod.yml` server) |
| **CI** | GitHub Actions: `test-backend.yml`, `playwright.yml`, `pre-commit.yml`; `deploy-*.yml` — ручной деплой вне CI |

### Embeddings — стратегия инференса

| Операция | Метод | Где |
|----------|-------|-----|
| Reindex (batch) | API batch embed | Worker (Celery) |
| Query (online) | CPU `e5-large` либо внешний API | Backend (`services/embeddings.py`) или internal-only sidecar |
| Query cache | Redis (TTL) | Backend |

### LLM провайдер

| Режим | Поведение |
|-------|-----------|
| **Normal** | Primary LLM (OpenAI / Anthropic) |
| **Fallback** | Secondary provider при ошибке |
| **Degraded** | Без LLM: таблица результатов + proof, без summary |

### Модели и pipeline

```
Текст (русский) ──▶ multilingual-e5-large ──▶ vector(768)
Молекулы (SMILES) ──▶ MolFormer ──▶ vector(512) [P1]
Таблицы/PDF ──▶ Marker → LangExtract ──▶ Markdown + JSON
NER (русский) ──▶ spaCy ru_core_news_lg + custom pipeline
LLM-реранкинг ──▶ GPT-4o / Claude через API (opt-in)
```

---

## §7. Ingestion & ETL Pipeline

### Общий поток — 9 стадий

```
Документы (PDF, DOC, XLSX, каталоги)
        │
        ▼
┌───────────────────────────────┐
│  1. PARSE                     │
│  PDF → Markdown (Marker)      │
│  DOC → Markdown (pandoc)      │
│  XLSX → CSV                   │
│  P2: UniExtract flag          │
└───────────┬───────────────────┘
            │
            ▼
┌───────────────────────────────┐
│  2. NORMALIZE                 │
│  • Канонические имена         │
│  • Единицы измерения          │
│  • Алиасы и синонимы (YAML)   │
└───────────┬───────────────────┘
            │
            ▼
┌───────────────────────────────┐
│  3. DEDUP-LINK                │
│  • Правила по типу сущности   │
│  • Embedding fallback (P1)    │
│  • entity_same_as записи      │
└───────────┬───────────────────┘
            │
            ▼
┌───────────────────────────────┐
│  4. LOAD                      │
│  • Нормализованные таблицы PG │
└───────────┬───────────────────┘
            │
            ▼
┌───────────────────────────────┐
│  5. BUILD-FLAT                │
│  • REFRESH MATERIALIZED VIEW  │
│  • experiments_flat           │
└───────────┬───────────────────┘
            │
            ▼
┌───────────────────────────────┐
│  6. EMBED                     │
│  • API batch embed (e5-large) │
│  • pgvector INSERT/UPDATE     │
└───────────┬───────────────────┘
            │
            ▼
┌───────────────────────────────┐
│  7. SYNC-NEO4J                │
│  • Full wipe + batch create   │
│  • Fail → warning, not error  │
└───────────┬───────────────────┘
            │
            ▼
┌───────────────────────────────┐
│  8. BUILD-WIKI                │
│  • Jinja templates → Markdown │
│  • LLM summary (cached)      │
└───────────┬───────────────────┘
            │
            ▼
┌───────────────────────────────┐
│  9. DONE                      │
│  • Progress API update        │
│  • Metrics recalculation      │
└───────────────────────────────┘
```

Каждая стадия — Celery-таск в одном из независимых worker-проектов (`workers/etl/tasks/`, `workers/embed/tasks/`, `workers/graph/tasks/`), оркестрируется цепочкой (`chain`/`chord`) через общий Redis-брокер. Прогресс по 9 стадиям пишется в таблицу `experiments.ingest_tasks`, которую опрашивает `GET /api/v1/ingest/status/{task_id}` (см. Приложение D.6) — источник данных не Redis/Celery result backend напрямую, а Postgres-таблица, обновляемая каждым таском.

### Детали по парсерам

| Тип источника | Парсер | Выход | Приоритет |
|---------------|--------|-------|-----------|
| Структурированный каталог (таблицы) | Детерминированный парсер (pandas + regex) | CSV с типизированными полями | P0 |
| Свободный текст (отчёты, статьи) | LangExtract с few-shot примерами | JSON с сущностями + grounding | P0 |
| PDF с таблицами и формулами | Marker → LangExtract | Markdown + JSON | P0 |
| Сложный PDF (hard tables) | UniExtract / ColPali (opt-in flag) | Markdown + JSON с bounding boxes | P2 |
| Справочники материалов | spaCy NER + словарь синонимов | Нормализованные записи материалов | P0 |

### Стратегия обработки данных

**Day-1 triage при получении данных:**
1. 30 мин inventory: что за файлы, форматы, объём
2. Structured first: CSV/каталоги → ingest сразу
3. PDF параллельно — не блокирует P0
4. Column mapping → smoke на ≥1 файле

### Стратегия обновления

- **Полная переиндексация** — CSV-файлы перезаписываются, скрипт пересоздаёт все стадии (~2.5 мин)
- **Hold-out:** 2–3 целых файла не индексируются при старте; загрузка на сцене + фиксированный demo script (3 контрольных вопроса)
- Нет инкрементальных миграций

### UniExtract budget

- Pre-hackathon: 1–2 hard PDF → UniExtract → seed JSON (pre-bake)
- Live demo: 1 known-hard PDF, `parser=uniextract` flag
- **Max 3 API calls** на весь хакатон (зафиксировать в `demo_script.md`)

---

## §8. API Design

Все эндпоинты — часть одного backend-процесса, за nginx фронтенд-контейнера. Один `/docs`, одна OpenAPI-схема. JWT auth на всех эндпоинтах, кроме auth-группы.

### §8.1. Auth & Users (template)

```
POST /api/v1/login/access-token   # JWT token (OAuth2 password flow)
POST /api/v1/login/test-token     # проверка текущего токена
POST /api/v1/users/signup         # самостоятельная регистрация
GET  /api/v1/users/me             # текущий пользователь
```

### §8.2. Search

```
POST /api/v1/search
```

Гибридный поиск по корпусу.

**Request:**
```json
{
  "query": "эксперименты с палладиевыми сплавами при температуре выше 800°C",
  "filters": {
    "material": "Pd",
    "temperature_min": 800,
    "tags": ["суперсплавы"]
  },
  "search_mode": "hybrid",
  "top_k": 20,
  "rerank": false
}
```

**Response:**
```json
{
  "results": [
    {
      "experiment_id": "uuid",
      "title": "...",
      "material": "Pd-Cu сплав",
      "material_composition": {"Pd": 0.6, "Cu": 0.4},
      "regime": {"temperature": 850, "medium": "аргон"},
      "property": "предел текучести",
      "value": 320.5,
      "unit": "МПа",
      "score": 0.94,
      "source": {
        "document_id": "uuid",
        "document": "report_03.pdf",
        "page": 12,
        "paragraph": "Результаты испытаний..."
      }
    }
  ],
  "total": 42,
  "search_meta": {
    "bm25_hits": 15,
    "vector_hits": 35,
    "custom_hits": 12,
    "reranked": false
  }
}
```

### §8.3. Graph

```
POST /api/v1/graph/query
```

Запросы к графу по шаблонам. **Не принимает raw Cypher.**

**Request:**
```json
{
  "template_id": "material_experiments_in_lab",
  "params": {"material": "никель"},
  "max_depth": 3
}
```

```
GET /api/v1/graph/subgraph/{entity_id}?depth=2
```

Подграф вокруг сущности для визуализации.

```
GET /api/v1/graph/path?from={id}&to={id}&max_depth=4       # P2
```

**Response (path):**
```json
{
  "nodes": [],
  "edges": [],
  "path_length": 3
}
```

Neo4j down → `503` (без SQL fallback для path).

### §8.4. Chat / Agent

```
POST /api/v1/chat/sessions                       # создать сессию
GET  /api/v1/chat/sessions                       # список сессий юзера
GET  /api/v1/chat/sessions/{id}                  # история сессии
POST /api/v1/chat/sessions/{id}/messages          # SSE stream
```

**Message Request:**
```json
{
  "content": "Что делали по сплавам Pd-Cu при режиме спекания?",
  "metadata": null
}
```

**Message Request (gap-click):**
```json
{
  "content": "Предложи эксперимент для этой ячейки",
  "metadata": {
    "trigger": "gap_click",
    "gap_cell": {
      "material_id": "uuid",
      "material": "IN-738",
      "property": "UTS",
      "regime_bucket": "high"
    }
  }
}
```

**Response (SSE stream):**
```json
{
  "claims": [
    {
      "text": "По сплавам Pd-Cu проведены 3 эксперимента при спекании...",
      "experiment_ids": ["uuid-1", "uuid-2", "uuid-3"],
      "confidence": "high",
      "kind": "fact",
      "gap_cell": null,
      "novelty": null,
      "risk": null,
      "value": null,
      "score_rationale": null
    }
  ],
  "summary": "Обобщённый текст ответа...",
  "tools_used": ["hybrid_search", "sql_aggregate"],
  "subgraph": null,
  "session_id": "uuid"
}
```

**Claim schema:**

| Поле | Тип | Описание | P0 | P1 | P2 |
|------|-----|----------|----|----|-----|
| `text` | string | Текст утверждения | ✓ | ✓ | ✓ |
| `experiment_ids` | UUID[] | Ссылки на эксперименты-доказательства | ✓ | ✓ | ✓ |
| `confidence` | enum | `high` / `medium` / `low` | ✓ | ✓ | ✓ |
| `kind` | enum | `fact` / `hypothesis` | — | ✓ | ✓ |
| `gap_cell` | object? | `{material, property, regime_bucket}` | — | ✓ | ✓ |
| `novelty` | float? | `1.0 - min(1.0, neighbor_count / corpus_median)` | — | — | ✓ |
| `risk` | enum? | `low` / `medium` / `high` (LLM) | — | — | ✓ |
| `value` | float? | `kpi_token_overlap(kpi, property)` [0–1] | — | — | ✓ |
| `score_rationale` | string? | LLM rationale для жюри | — | — | ✓ |

**Validator rules:**

| Kind | Правила |
|------|---------|
| `fact` | strict number validation; ≥1 experiment_id |
| `hypothesis` | ≥1 experiment_id; `gap_cell` обязателен; числа: verbatim из neighbors ИЛИ `confidence != "high"` |
| degraded | 1 retry → без numbers, `confidence: "low"`, neighbors table |

### §8.5. Ingestion (superuser only)

```
POST /api/v1/ingest/upload          # загрузка документов
POST /api/v1/ingest/reindex         # полная переиндексация
GET  /api/v1/ingest/status/{task_id} # статус задачи (9 стадий)
```

**Upload validation:**
- MIME whitelist: `application/pdf`, `application/vnd.openxmlformats-*`, `text/csv`
- Max file size: 50 MB
- Rate limit: 10 uploads / minute
- JWT + `is_superuser` required

### §8.6. Wiki

```
GET /api/v1/wiki/{entity_type}/{entity_id}    # Wiki-страница
GET /api/v1/wiki/search?q=палладий            # полнотекстовый поиск
```

### §8.7. Analytics

```
GET /api/v1/analytics/gaps?material=Pd&property=hardness    # пробелы
GET /api/v1/analytics/coverage                               # heatmap data
GET /api/v1/metrics                                          # KPI dashboard
```

### §8.8. Sources

```
GET /api/v1/sources/{doc_id}/download     # presigned MinIO URL
```

JWT required. TTL 15 min.

---

## §9. UI/UX Концепция

### Экраны по приоритетам

| Экран | Приоритет | Описание |
|-------|-----------|----------|
| `/login`, `/signup` | **P0** | Из full-stack-fastapi-template |
| `/chat` + session sidebar | **P0** | Чат с ассистентом, structured claims, provenance cards |
| `/ingest` | **P0** | Drag-and-drop загрузка, progress bar (9 стадий); только superuser |
| `/wiki` | **P1** | Wiki-страница сущности, 3 вкладки |
| `/graph` | **P1** | Граф-эксплорер |
| `/analytics/gaps` | **P1** | Gap heatmap с клик → чат |

Роуты — **файлы**, а не JSX-дерево: `frontend/src/routes/_layout/chat.tsx`, `.../wiki.tsx`, `.../graph.tsx`, `.../analytics/gaps.tsx`, `.../ingest.tsx`. `_layout.tsx` содержит защищённый layout с сайдбаром (`useAuth` редиректит неавторизованных) — новые экраны вешаются туда же. `routeTree.gen.ts` пересобирается автоматически dev-сервером/`vite build`, руками не редактируется.

Навигация — пункты сайдбара добавляются в `frontend/src/components/Sidebar`. API-вызовы с фронта — через сгенерированный `frontend/src/client/sdk.gen.ts` + TanStack Query hooks (см. `useAuth.ts` как образец паттерна); каждый новый backend-роут появляется здесь после `bash scripts/generate-client.sh`. UI-кит — shadcn/ui поверх Radix (`dialog`, `dropdown-menu`, `select`, `tabs`, `tooltip`, `scroll-area` и т.д., см. `frontend/src/components/ui`) — для чат-ленты, provenance-карточек и heatmap использовать их. Тёмная тема — уже реализована через `next-themes`. E2E-тесты — Playwright (`frontend/tests/*.spec.ts`, `playwright.config.ts`, `Dockerfile.playwright`); новые экраны покрываются по той же схеме, что `login.spec.ts`.

### §9.1. Главный экран — `/chat`

- Полноэкранный чат в стиле ChatGPT
- **Session sidebar** — список чат-сессий (персистентные, per user)
- Каждый ответ содержит блоки:
  - **Claims** — structured утверждения с `confidence` badge
  - **Источники (Provenance)** — кликабельные карточки с документом, страницей, цитатой → presigned URL
  - **Связанные сущности** — теги-чипы (материал, режим, свойство), по клику → wiki
  - **P2:** `<MiniGraph>` — визуализация подграфа (если `subgraph` present)

### §9.2. Wiki-страница — `/wiki` (P1)

| Вкладка | Содержимое |
|---------|-----------|
| **Текст** | Jinja-шаблон + LLM summary paragraph |
| **Таблица** | Все эксперименты с этой сущностью (сортировка, фильтры) |
| **Граф** | Интерактивная визуализация связей (react-force-graph) |

### §9.3. Граф-эксплорер — `/graph` (P1)

- Полноэкранная визуализация графа знаний
- Фильтры по типу сущности, дате, тегам
- Кликабельные ноды → popup с деталями + переход на wiki
- **P2:** From / To search → path highlight

### §9.4. Аналитика пробелов — `/analytics/gaps` (P1)

- Heatmap `Material × Property × Regime bucket`
- Пустые ячейки = пробелы
- **Клик на gap → redirect `/chat` с prefilled `metadata.gap_cell`**
- Фильтры по режиму, лаборатории, дате

### §9.5. Загрузка документов — `/ingest` (P0, superuser only)

- Drag-and-drop зона для PDF/DOC/CSV
- Progress bar парсинга (9 стадий pipeline)
- Превью извлечённых сущностей перед добавлением в базу

### Дизайн-принципы

- **Dark mode** — из template (shadcn/ui)
- **Минимум кликов** — чат доступен сразу после login
- **Провенанс везде** — каждый факт кликабелен и ведёт к источнику (presigned URL)
- **Responsive** — десктоп primary, планшеты — best effort

---

## §10. Роли в команде и Workflow

### Распределение (5 человек)

Единица «владения» — набор роутеров/модулей **в одном репозитории**, не отдельный контейнер.

| Роль | Зона ответственности | Модули |
|------|---------------------|---------------------------|
| **Data Engineer / Boilerplate Lead** | Docker Compose (redis/minio/worker-*/neo4j), CI, независимые uv-проекты воркеров, деплой | `workers/*/Dockerfile`, `workers/*/pyproject.toml`, `compose*.yml`, `.github/workflows/`, MinIO/Redis интеграция |
| **NLP/ML-инженер** | Парсинг, извлечение сущностей, эмбеддинги, дедупликация, кастомная метрика, UniExtract | `workers/etl/tasks/{parse,normalize,dedup_link}.py`, `workers/embed/tasks/embed.py`, `app/api/routes/analytics.py` |
| **Backend-разработчик** | LLM-агент с tools, граф-запросы, поисковый сервис, chat claims validator | `app/api/routes/{chat,search,graph}.py`, `app/services/{search,graph,agent}` |
| **Продуктовый аналитик** | Онтология, wiki-шаблоны, данные, демо, презентация, метрики, few-shot | `app/api/routes/wiki.py`, `app/services/wiki.py`, `seed/`, `eval/`, `demo_script.md` |
| **Frontend-разработчик** | UI (чат, wiki, граф-визуализация, аналитика пробелов), UX | `frontend/src/routes/_layout/*`, `frontend/src/components/*` |

### Дисциплина работы с общими файлами

- **`app/api/main.py`** (регистрация роутеров) — каждый добавляет свою строку `include_router`; не переставлять чужие строки.
- **`app/models/`** — пакет с файлом на домен (`materials.py`, `chat.py`, `experiments.py`) и реэкспортом в `__init__.py`, не единый файл.
- **`frontend/src/client/*.gen.ts`, `routeTree.gen.ts`** — не редактируются руками; при конфликте — перегенерировать, не мёржить построчно.
- **Merge windows** — 12:00, 16:00, 20:00.

### Методология работы через агентов (адаптация [CODE_WITH_AGENTS.md](wiki/CODE_WITH_AGENTS.md) под монолит)

Этот документ закрывает шаги 1–3 методологии (сырая спека → grilling → полная спецификация). Дальше команда действует так:

1. **Шаг 4 — «взять свой кусок».** В монолите физической изоляции по контейнерам нет, поэтому «свой кусок» — это **свой набор файлов + свои тесты** (см. таблицу владения выше), а не отдельный сервис. Компенсируется пунктами 2–7 ниже.
2. **Общие файлы замораживаются один раз, на старте.** Data Engineer в первый час создаёт пустые stub-роутеры для всех доменов и одним коммитом регистрирует их все в `app/api/main.py`. После этого коммита `app/api/main.py` почти никто больше не трогает.
3. **`app/models/` — пакет по доменам с самого начала**, не единый файл — самый вероятный источник построчных конфликтов, откладывать нельзя.
4. **Шаг 5 — тесты до имплементации**, по конвенции «один test-файл на роутер» (`backend/tests/api/routes/test_<module>.py`).
5. **Шаг 6 — план через `plan` mode** перед реализацией каждого модуля; готовые планы коммитятся в репозиторий.
6. **Шаг 7 — harness-петля с внешним gate-скриптом.** Petля каждого участника гоняет как gate **только свои тестовые файлы**, не весь `pytest` — так недописанный код одного человека не блокирует петлю другого. **Полный gate** (весь `pytest` + Playwright + сборка) запускается не непрерывно, а только в merge windows (12:00 / 16:00 / 20:00), одним интегратором.
7. **Один владелец на Alembic-мёрж за merge window** — при параллельной генерации миграций двумя людьми от одного `down_revision` получаются две головы истории; разруливает `alembic merge` назначенный на окно человек.
8. **Каждый участник (агент) работает в своей ветке/`git worktree`**, не редактируя общий рабочий каталог параллельно с другими.
9. **Автогенерируемые файлы фронтенда** — не мёржатся построчно; при расхождении пересобираются.
10. **Шаг 8 — мониторинг.** Особое внимание — миграциям БД/конфигурации (слабое место локальных моделей) и сложной бизнес-логике (custom distance metric, claims validator, gap analysis).
11. **Шаг 9 — сохранение скиллов.** Удачные паттерны (например, как структурирован Celery `chain` для 9 стадий, как написан claims validator) фиксируются как agent skills для следующих сессий.

### Критические зависимости

- **Boilerplate** (docker compose + миграция под pgvector + Celery skeleton) — блокирует всех, готов в первые часы
- **Онтология + словари** — блокирует парсинг и схему БД
- **API-контракты** — Pydantic/SQLModel-схемы в `app/models/`/`app/schemas.py`, замораживаются до начала параллельной разработки (Приложение D — источник правды)

### Pre-flight checklist

До начала хакатона:

- [ ] Поднять `docker compose up --build`, убедиться, что backend стартует, виден `/docs`, `/login`, зелёный CI
- [ ] Образ Postgres — `pgvector/pgvector:pg18`, накатить `CREATE EXTENSION vector`, проверить `SELECT * FROM pg_extension`
- [ ] **Прогнать `uv lock` для полного набора ML-зависимостей (spaCy ru, e5-large/sentence-transformers, hdbscan, marker-pdf, langextract) под Python 3.11 в отдельном тестовом проекте `workers/etl/`** — если резолв падает или либа не собирается: попробовать другую минорную версию (напр. 3.12) в этом worker'е или расщепить конфликтующие либы по нескольким worker-образам
- [ ] Добавить `redis`, `minio`, `worker-*` (и опционально `neo4j`) в `compose.yml` + `compose.override.yml`
- [ ] Настроить `frontend/nginx.conf` под SSE (`proxy_buffering off` для `/api/v1/chat/`)
- [ ] Завести пустые stub-роутеры на все домены, зарегистрировать разом в `app/api/main.py` одним коммитом
- [ ] Разбить `app/models.py` на пакет `app/models/<domain>.py`
- [ ] Решить судьбу демо-сущности `Item` из template (выпилить или переиспользовать) — не должна попасть в демо
- [ ] Миграции для схемы `experiments.*` (и `chat_session`/`chat_message` в `public`)
- [ ] `seed/`, `holdout/`, `dictionaries/` — заполнить
- [ ] `demo_script.md` — 3 контрольных вопроса + UniExtract budget
- [ ] Few-shot примеры для LangExtract подготовлены

### Workflow на хакатоне

```
[Первые 2 часа]
  1. Data Engineer: Docker Compose, FastAPI stubs, Celery, nginx
  2. Аналитик: зафиксировать онтологию, подготовить few-shot
  3. Все: написать тесты по своим модулям (contract + smoke_p0.py)

[Основная работа]
  4. Параллельная разработка модулей (свои файлы + свои тесты, см. выше)
  5. Агентская разработка через harness-петли
  6. Интеграция через Python-вызовы сервисного слоя внутри одного процесса

[Merge windows]
  7. Feature branches → merge 12:00, 16:00, 20:00
  8. Alembic-мёрж и uv.lock-конфликты — только через назначенного на окно человека

[Последние 2 часа]
  9. Интеграционное тестирование (полный gate: pytest + Playwright + сборка)
  10. Демо на hold-out данных (3 файла + 3 вопроса)
  11. Презентация: problem → live demo → KPI → 1 слайд tech
```

---

## §11. Риски и митигации

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| **UniExtract стоит дорого / медленно** | Высокая | Высокое | Marker → LangExtract default; UniExtract opt-in P2; max 3 API calls; pre-bake hold-out |
| **Качество NER на русском** | Средняя | Высокое | Few-shot примеры; словарь синонимов YAML; ручная валидация на hold-out |
| **Neo4j sync fail** | Средняя | Среднее | SQL subgraph fallback; Neo4j down → 503 для path |
| **Не успеваем за время хакатона** | Средняя | Критическое | Чёткий P0/P1/P2; декомпозиция на независимые модули/файлы |
| **Химические синонимы ломают дедупликацию** | Высокая | Среднее | DEDUP-LINK: правила по типу + embedding fallback; не удалять дубли, а связывать |
| **LLM галлюцинирует** | Средняя | Высокое | Structured claims + validator; provenance обязательный; rerank opt-in |
| **LLM API down** | Средняя | Высокое | Degraded mode: таблица + proof без summary; fallback provider |
| **PDF parsing slow** | Средняя | Среднее | Marker ladder; structured first, PDF не блокирует P0 |
| **GPU-ресурсы недоступны** | Низкая | Среднее | Облачные API; fallback на CPU-модели |
| **Данные хакатона нетипичны** | Средняя | Среднее | Универсальная онтология; hold-out set для адаптации; day-1 triage |
| **Ключевая ML-библиотека не собирается под Python 3.11** | Средняя | Высокое | Проверить в pre-flight; fallback — другая минорная версия Python в worker'е (напр. 3.12) или расщепление worker-образов |
| **pgvector-образ не подключён вовремя** | Низкая при выполненном pre-flight | Высокое (весь vector search не работает) | Явный пункт в pre-flight, проверка `pg_extension` на смоук-тесте |
| **SSE буферизуется nginx'ом, чат «зависает»** | Средняя | Среднее (демо выглядит сломанным) | `proxy_buffering off` в pre-flight, smoke-тест curl со `stream` |
| **Мердж-конфликты в общих файлах монолита** (`api/main.py`, `models/`, sidebar) | Средняя | Среднее | Разнесение моделей по пакету, merge windows, дисциплина «не трогай чужие строки» |
| **Синхронный DB engine + тяжёлые вычисления блокируют event loop** | Средняя | Среднее (KPI < 15 сек под нагрузкой) | `run_in_threadpool` для CPU-тяжёлых участков search/embeddings; нагрузочный smoke-тест перед демо |
| **Dependency hell в ETL/ML-стеке** (torch/spaCy/marker-pdf/langextract/hdbscan) | Высокая | Высокое (весь ETL не собирается) | `workers/*` — независимые uv-проекты со своими `uv.lock`, нарезка по факту конфликта; проверка сборки — pre-flight |
| **Безопасность** | Средняя | Высокое | JWT auth; upload validation (MIME whitelist, size limit, rate limit); RBAC на уровне API |

---

## §12. Вне скоупа (Out of Scope)

### In scope: минимальный RBAC

| Роль | Доступ | Механизм |
|------|--------|----------|
| `user` | Chat, Search, Wiki, Graph, Analytics, Sources | `Depends(get_current_user)` |
| `superuser` | Всё выше + Ingest (upload, reindex) | `Depends(get_current_active_superuser)` |

### Вне скоупа

- **DB-роли Postgres для read/write split** — RBAC полностью на уровне API
- **Инкрементальное обновление** — только полная переиндексация
- **Редактирование wiki** пользователями — только автогенерация
- **Мультитенантность** — один инстанс, одна общая база
- **Полноценный RBAC, OAuth, LDAP** — только user vs superuser
- **Мобильная версия** — только десктоп
- **Автоматическое дообучение моделей** — только pre-trained + few-shot
- **Интеграция с внешними системами** Норникеля (ERP, LIMS) — только загрузка файлов
- **Подключение внешних баз** (Materials Project, COD) — желательно, но не в MVP
- **Production-ready мониторинг** (Prometheus, Grafana) — только логирование + `/metrics`
- **Пересборка на микросервисы во время хакатона** — не часть текущего плана
- **Tensor decomposition / BMF** — только слайд «architecture ready»

---

## Приложение A. Конкурентные преимущества

| # | Фича | Почему это важно |
|---|-------|------------------|
| 1 | **Работающий развёрнутый прототип** | Проверенный шаблон + монолит снижает риск «не задеплоили вообще» |
| 2 | **Provenance** | Каждый факт со ссылкой на PDF — трейсбек до источника |
| 3 | **Кастомная метрика расстояния** | d_comp + d_reg + d_embed — domain-specific, не generic cosine |
| 4 | **Анализ пробелов** | Gap heatmap Material × Property × Regime bucket |
| 5 | **Hypothesis Factory** | LLM-генерация гипотез для пустых ячеек heatmap |
| 6 | **Hold-out + live reindex** | Демо онлайн-добавления данных |
| 7 | **Заточка под палладий** | Главный металл Норникеля = бонус от жюри |
| 8 | **Несколько подходов к поиску** | BM25 + Vector + Custom metric + RRF |
| 9 | **Structured chat с claims** | Каждое утверждение верифицировано + provenance |

---

## Приложение B. Датасеты

| Источник | Формат | Объём | Стратегия |
|----------|--------|-------|-----------|
| Корпус документов Норникеля | PDF, DOC | Предоставлен на хакатоне | Day-1 triage |
| Каталог экспериментов | XLSX/CSV | Предоставлен | Structured first — P0 |
| Справочники материалов/оборудования | Каталоги | Предоставлен | Детерминированный парсер |
| Перечень сотрудников/лабораторий | Реестр | Предоставлен | Direct import |
| Advanced Science — суперсплавы | CSV, ~90k | Открытый | Curated slice 500–1500 строк (Pd/Ni/superalloy) |
| MaterialsGenomics DB | API | Открытый (ненадёжный) | Optional, P2 |
| Nature — суперсплавы | PDF | 1 статья + supplementary | Seed / hold-out |

---

## Приложение C. Agent Tools

Полный реестр инструментов LLM-агента. Реализация — прямые вызовы функций `app/services/*` внутри одного процесса, не HTTP-запросы к соседним контейнерам.

### P0 Tools

| Tool | Описание | Input | Output |
|------|----------|-------|--------|
| `hybrid_search` | Гибридный поиск: BM25 + vector + custom → RRF | `{query, filters?, top_k?, rerank?}` | `{results: Experiment[], search_meta}` |
| `sql_filter` | Фильтрация экспериментов по SQL-шаблонам | `{template_id, params}` | `{rows: Experiment[]}` |
| `sql_aggregate` | Агрегация данных (count, avg, min, max по группам) | `{template_id, params}` | `{aggregation: {groups[], totals}}` |
| `get_experiment_details` | Полные данные эксперимента по ID | `{experiment_id}` | `{experiment: ExperimentFull}` |

### P1 Tools

| Tool | Описание | Input | Output |
|------|----------|-------|--------|
| `generate_hypothesis` | Генерация гипотезы для gap cell | `{gap_cell: {material_id, material, property, regime_bucket}}` | `{claim: Claim(kind=hypothesis)}` |
| `graph_template` | Запрос к графу по шаблону | `{template_id, params, max_depth?}` | `{nodes[], edges[]}` |

### P2 Tools

| Tool | Описание | Input | Output |
|------|----------|-------|--------|
| `get_subgraph` | Подграф вокруг сущностей для мини-графа | `{entity_ids: UUID[], depth: 1, max_nodes: 12}` | `{nodes[], edges[]}` |

### Agent Rules

| Триггер | Поведение |
|---------|-----------|
| `metadata.trigger == "gap_click"` | Обязательный вызов `generate_hypothesis` |
| Любой запрос | ≥1 tool call; каждый claim → ≥1 `experiment_id` |
| LLM timeout / error | Degraded mode: таблица + proof, без summary |

---

## Приложение D. JSON-схемы Request / Response

### D.1. Search Request

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "SearchRequest",
  "type": "object",
  "required": ["query"],
  "properties": {
    "query": {"type": "string"},
    "filters": {
      "type": "object",
      "properties": {
        "material": {"type": "string"},
        "material_type": {"type": "string", "enum": ["alloy", "compound", "pure_metal"]},
        "temperature_min": {"type": "number"},
        "temperature_max": {"type": "number"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "lab": {"type": "string"},
        "date_from": {"type": "string", "format": "date"},
        "date_to": {"type": "string", "format": "date"}
      }
    },
    "search_mode": {"type": "string", "enum": ["bm25", "vector", "hybrid", "custom"], "default": "hybrid"},
    "top_k": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
    "rerank": {"type": "boolean", "default": false}
  }
}
```

### D.2. Search Response

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "SearchResponse",
  "type": "object",
  "required": ["results", "total", "search_meta"],
  "properties": {
    "results": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["experiment_id", "score", "source"],
        "properties": {
          "experiment_id": {"type": "string", "format": "uuid"},
          "title": {"type": "string"},
          "material": {"type": "string"},
          "material_composition": {"type": "object"},
          "regime": {"type": "object"},
          "property": {"type": "string"},
          "value": {"type": "number"},
          "unit": {"type": "string"},
          "score": {"type": "number"},
          "source": {
            "type": "object",
            "properties": {
              "document_id": {"type": "string", "format": "uuid"},
              "document": {"type": "string"},
              "page": {"type": "integer"},
              "paragraph": {"type": "string"}
            }
          }
        }
      }
    },
    "total": {"type": "integer"},
    "search_meta": {
      "type": "object",
      "properties": {
        "bm25_hits": {"type": "integer"},
        "vector_hits": {"type": "integer"},
        "custom_hits": {"type": "integer"},
        "reranked": {"type": "boolean"}
      }
    }
  }
}
```

### D.3. Chat Message Request

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ChatMessageRequest",
  "type": "object",
  "required": ["content"],
  "properties": {
    "content": {"type": "string", "minLength": 1},
    "metadata": {
      "type": "object",
      "properties": {
        "trigger": {"type": "string", "enum": ["gap_click", "user_input"]},
        "gap_cell": {
          "type": "object",
          "properties": {
            "material_id": {"type": "string", "format": "uuid"},
            "material": {"type": "string"},
            "property": {"type": "string"},
            "regime_bucket": {"type": "string", "enum": ["low", "medium", "high"]}
          },
          "required": ["material_id", "material", "property", "regime_bucket"]
        }
      }
    }
  }
}
```

### D.4. Chat Message Response

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ChatMessageResponse",
  "type": "object",
  "required": ["claims", "summary", "tools_used", "session_id"],
  "properties": {
    "claims": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["text", "experiment_ids", "confidence", "kind"],
        "properties": {
          "text": {"type": "string"},
          "experiment_ids": {"type": "array", "items": {"type": "string", "format": "uuid"}},
          "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
          "kind": {"type": "string", "enum": ["fact", "hypothesis"]},
          "gap_cell": {
            "type": "object",
            "nullable": true,
            "properties": {
              "material": {"type": "string"},
              "property": {"type": "string"},
              "regime_bucket": {"type": "string"}
            }
          },
          "novelty": {"type": "number", "nullable": true, "minimum": 0, "maximum": 1},
          "risk": {"type": "string", "nullable": true, "enum": ["low", "medium", "high"]},
          "value": {"type": "number", "nullable": true, "minimum": 0, "maximum": 1},
          "score_rationale": {"type": "string", "nullable": true}
        }
      }
    },
    "summary": {"type": "string"},
    "tools_used": {"type": "array", "items": {"type": "string"}},
    "subgraph": {
      "type": "object",
      "nullable": true,
      "properties": {
        "nodes": {"type": "array"},
        "edges": {"type": "array"}
      }
    },
    "session_id": {"type": "string", "format": "uuid"}
  }
}
```

### D.5. Graph Query Request

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "GraphQueryRequest",
  "type": "object",
  "required": ["template_id", "params"],
  "properties": {
    "template_id": {"type": "string"},
    "params": {"type": "object"},
    "max_depth": {"type": "integer", "default": 3, "minimum": 1, "maximum": 5}
  }
}
```

### D.6. Ingest Upload Response

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "IngestUploadResponse",
  "type": "object",
  "required": ["task_id", "status"],
  "properties": {
    "task_id": {"type": "string", "format": "uuid"},
    "status": {"type": "string", "enum": ["queued", "parse", "normalize", "dedup_link", "load", "build_flat", "embed", "sync_neo4j", "build_wiki", "done", "error"]},
    "progress": {"type": "number", "minimum": 0, "maximum": 1},
    "stage_name": {"type": "string"},
    "error": {"type": "string", "nullable": true}
  }
}
```

---

## Приложение E. Таблица модулей и владельцев

| Модуль | Путь (backend) / Путь (frontend) | Публичный путь API | Owner | P |
|--------|-----------------------------------|---------------------|-------|---|
| **Auth/Users** (template) | `app/api/routes/{login,users}.py` | `/api/v1/login/*`, `/api/v1/users/*` | Data Engineer (интеграция) | P0 |
| **Chat + Agent** | `app/api/routes/chat.py`, `app/services/agent/` | `/api/v1/chat/*` | Backend | P0 |
| **Search** | `app/api/routes/search.py`, `app/services/search.py` | `/api/v1/search` | Backend | P0 |
| **Ingest** | `app/api/routes/ingest.py` (приём файла + постановка задачи) | `/api/v1/ingest/*` | Data Engineer | P0 |
| **Sources** | `app/api/routes/sources.py` | `/api/v1/sources/*` | Data Engineer | P0 |
| **Graph** | `app/api/routes/graph.py`, `app/services/graph.py` | `/api/v1/graph/*` | Backend | P1 |
| **Wiki** | `app/api/routes/wiki.py`, `app/services/wiki.py` | `/api/v1/wiki/*` | Аналитик | P1 |
| **Analytics** | `app/api/routes/analytics.py` | `/api/v1/analytics/*`, `/api/v1/metrics` | NLP/ML | P1 |
| **Celery Worker(ы)** | `workers/etl/`, `workers/embed/`, `workers/graph/` — независимые uv-проекты | — (без HTTP; опционально internal-only `embed` sidecar) | Data Engineer + NLP/ML | P0 |
| **`packages/schema`** (опционально) | Общие SQLModel-таблицы `experiments.*`, без тяжёлых зависимостей | — | Data Engineer | P0 |
| **Frontend** | `frontend/src/routes/_layout/*` | `/` | Frontend | P0 |
| **PostgreSQL (pgvector)** | `compose.yml: db` | — | Data Engineer (инфра) | P0 |
| **Redis** | `compose.yml: redis` | — | Data Engineer (инфра) | P0 |
| **MinIO** | `compose.yml: minio` | — | Data Engineer (инфра) | P0 |
| **Neo4j** | `compose.yml: neo4j` (опционально) | — | Data Engineer (инфра) | P1 |

---

## Приложение F. Деплой

| Среда | Как | Файлы |
|-------|-----|-------|
| **Dev (локально)** | `docker compose up -d --build` (авто-мёрж `compose.yml` + `compose.override.yml`) | Открывает порты db/adminer/backend/frontend, live-reload backend |
| **Прод (VPS, хакатон)** | `docker compose -f compose.yml -f compose.prod.yml up -d --build` | Наружу торчит только `frontend:80` |
| **Fallback** | Зеркало на локальной машине команды, переключение при проблемах с VPS | — |
| **CI (проверочный, не деплой)** | `test-backend.yml`, `playwright.yml`, `pre-commit.yml` на каждый PR | `.github/workflows/` |

---

## Приложение G. Глоссарий

| Термин | Определение |
|--------|-------------|
| **Provenance** | Происхождение факта — ссылка на конкретный документ, страницу и абзац |
| **Entity Linking** | Связывание упоминания сущности в тексте с каноническим представлением в базе |
| **Gap Analysis** | Выявление пробелов в покрытии пространства «Материал × Режим × Свойство» |
| **Hold-out set** | Данные, отложенные для демо online-добавления |
| **Reranking** | Повторное ранжирование результатов поиска с помощью более точной модели |
| **Grounding** | Привязка сгенерированного текста к конкретным фактам из источников |
| **Few-shot** | Режим работы LLM с несколькими примерами в промпте |
| **SMILES** | Simplified Molecular Input Line Entry System — текстовое представление молекулярных структур |
| **RRF** | Reciprocal Rank Fusion — метод объединения ранжированных списков |
| **Claim** | Атомарное утверждение в ответе агента, привязанное к experiment_ids |
| **Gap cell** | Пустая ячейка в heatmap Material × Property × Regime bucket |
| **Hypothesis** | Предложенный агентом эксперимент для заполнения gap cell |
| **DEDUP-LINK** | Стадия pipeline: дедупликация + связывание дублей через entity_same_as |
| **Degraded mode** | Режим работы без LLM: таблица результатов + proof, без summary |
| **BMF** | Bayesian Matrix Factorization — метод для uncertainty-aware предсказаний (P2 stretch) |
| **Модульный монолит** | Один деплоюмый backend-процесс, внутри разделённый на независимые по коду (но не по деплою) модули-роутеры |
| **`routeTree.gen.ts`** | Автогенерируемый файл маршрутизации TanStack Router; не редактируется руками |
| **`*.gen.ts` (client)** | Автогенерируемый TS-клиент API из `openapi.json`, пересобирается `scripts/generate-client.sh` |
