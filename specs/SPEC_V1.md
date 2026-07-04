# Научный клубок — Техническая спецификация V1

> **Проект:** Knowledge Graph / поисково-аналитическая система для НИОКР  
> **Команда:** 5 человек  
> **Формат:** хакатон, time-boxed delivery  
> **Версия:** 1.1 (post-grilling, 49 вопросов)  
> **Предыдущая:** SPEC_V0.md  
> **Changelog:** SPEC_PATCH.md, greel_me/DECISIONS.md

---

## §1. Постановка проблемы

### Боль

Исследователи Норникеля работают с большим объёмом неструктурированных данных: внутренние отчёты, протоколы экспериментов, справочники материалов и оборудования, реестры сотрудников и лабораторий. Эти данные разрозненны — хранятся в PDF, DOC, таблицах, каталогах — и не связаны между собой. Чтобы ответить на вопрос *«что уже делали по сплавам X при режиме Y и какой был эффект на свойство Z?»*, исследователь вынужден:

- Вручную перебирать десятки документов
- Полагаться на память коллег и субъективный опыт
- Не видеть пробелов в экспериментальном покрытии
- Не иметь прозрачной истории решений с источниками

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

**Инструментирование KPI (Q19):** `GET /api/v1/metrics` + `eval/` директория с `run_eval.py`. Dashboard с реальными цифрами; F1/graph coverage — скрипт, если UI не успеем.

---

## §3. Архитектура системы

### Высокоуровневая схема

```
┌─────────────────────────────────────────────────────────────────────┐
│                        OFFLINE — Парсинг и ETL                      │
│                                                                     │
│  ┌──────────┐   ┌───────────────┐   ┌──────────────┐               │
│  │ PDF/DOC  │──▶│ Парсеры:      │──▶│ Нормализация │               │
│  │ Каталоги │   │ • Детерминир. │   │ Дедупликация │               │
│  │ Таблицы  │   │ • LangExtract │   │ Embedding    │               │
│  └──────────┘   │ • Marker      │   └──────┬───────┘               │
│                 └───────────────┘          │                        │
└────────────────────────────────────────────┼────────────────────────┘
                                             │
                                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    STORAGE — Промежуточный слой                      │
│                                                                     │
│  ┌──────────────────┐  ┌───────────┐  ┌──────────────────────────┐ │
│  │ PostgreSQL 18    │  │ Neo4j     │  │ MinIO (S3-compatible)    │ │
│  │ + pgvector       │  │ (проекция │  │ PDF / DOC исходники      │ │
│  │ schemas:         │  │  из PG,   │  │                          │ │
│  │ auth / chat /    │  │  визуал.) │  │ Redis (embed cache)      │ │
│  │ experiments /    │  └───────────┘  └──────────────────────────┘ │
│  │ staging          │                                              │
│  └──────────────────┘                                              │
└─────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│              ONLINE — nginx Gateway (:8080)                         │
│                                                                     │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────────────┐ │
│  │ Chat Service   │  │ Search Service │  │ Graph Service         │ │
│  │ (template-     │  │ (BM25+Vector   │  │ (Cypher templates,    │ │
│  │  based, auth,  │  │  +Custom+RRF)  │  │  subgraph, path)     │ │
│  │  users, SSE)   │  └────────────────┘  └───────────────────────┘ │
│  └───────┬────────┘                                                │
│          │           ┌────────────────┐  ┌───────────────────────┐ │
│          │           │ Wiki Service   │  │ Analytics Service     │ │
│          │           │ (Jinja+LLM)    │  │ (gaps, coverage, KPI) │ │
│          │           └────────────────┘  └───────────────────────┘ │
│          │                                                         │
│  ┌────────────────┐  ┌────────────────┐                            │
│  │ Ingestion Svc  │  │ Worker         │                            │
│  │ (upload API)   │  │ (Celery/Redis) │                            │
│  │ superuser only │  │ no public port │                            │
│  └────────────────┘  └────────────────┘                            │
└─────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          FRONTEND                                   │
│  React + TypeScript (из full-stack-fastapi-template)                │
│                                                                     │
│  ┌──────────┐  ┌──────────────┐  ┌─────────────┐  ┌────────────┐  │
│  │ /login   │  │ /chat +      │  │ /wiki       │  │ /analytics │  │
│  │ /register│  │  session bar │  │ (P1 stub)   │  │ /gaps      │  │
│  │ (P0)     │  │  (P0)        │  │             │  │ (P1 stub)  │  │
│  └──────────┘  └──────────────┘  └─────────────┘  └────────────┘  │
│                ┌──────────────┐  ┌─────────────┐                   │
│                │ /ingest      │  │ /graph      │                   │
│                │ (superuser)  │  │ (P1 stub)   │                   │
│                │ (P0)         │  │             │                   │
│                └──────────────┘  └─────────────┘                   │
└─────────────────────────────────────────────────────────────────────┘
```

### Ключевые архитектурные решения

| # | Решение | Grilling ref |
|---|---------|-------------|
| 1 | **Микросервисная архитектура** — 6+ FastAPI-контейнеров, каждый со своим `/docs` | Q4-A |
| 2 | **API Gateway — nginx** — единственный public port `:8080`; SSE `proxy_buffering off`; rate limit на `/ingest/` | Q34-A |
| 3 | **Read/Write split** — Postgres roles `reader` / `writer` / `chat_app` / `migrator`; Worker без public HTTP port | Q12-C |
| 4 | **Monorepo с shared packages** — `packages/contracts/`, `packages/db/`, `packages/common/` | Q5-A |
| 5 | **uv workspace** — единый `uv.lock`, owner: Data Engineer | Q24′ |
| 6 | **Chat Service на базе [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)** — auth, users, chat DB, frontend | Q36-A |
| 7 | **Neo4j — проекция из Postgres** — full wipe + batch при reindex; SQL fallback для subgraph | Q26-C |
| 8 | **Полная переиндексация** вместо инкрементальных миграций (~2.5 мин) | Q13-B |
| 9 | **Провенанс по умолчанию** — каждый факт хранит ссылку на PDF-источник | — |

### Структура monorepo

```
metalcrow/
├── compose.yml                    # Docker Compose (primary path)
├── compose.override.yml           # dev overrides
├── nginx.conf                     # API Gateway
├── pyproject.toml                 # uv workspace root
├── uv.lock                        # единый lockfile (owner: DE)
├── packages/
│   ├── contracts/                 # Pydantic-модели (shared)
│   │   └── pyproject.toml
│   ├── db/                        # Alembic + SQLModel-модели
│   │   └── pyproject.toml
│   └── common/                    # утилиты, логгер, settings
│       └── pyproject.toml
├── services/
│   ├── chat/                      # ← full-stack-fastapi-template
│   ├── search/
│   ├── graph/
│   ├── wiki/
│   ├── analytics/
│   └── ingest/
├── worker/                        # Celery worker (no HTTP)
├── frontend/                      # React + TS (из template)
├── dictionaries/
│   ├── regime_buckets.yaml
│   ├── distance_weights.yaml
│   └── synonyms.yaml
├── eval/
│   ├── queries.json
│   └── run_eval.py
├── seed/                          # CSV/JSON для начальной загрузки
└── holdout/                       # файлы для live demo
```

### UV Policy

**Принцип:** единый `uv.lock` в корне monorepo, но каждый Docker-контейнер ставит **только свои** зависимости через `--package <name>`. Это гарантирует одинаковые версии shared-пакетов (`contracts`, `db`, `common`) во всех сервисах, при этом в контейнере — только то, что нужно конкретному сервису.

| Правило | Детали |
|---------|--------|
| Lockfile в git | `uv.lock` — единственный источник версий |
| Локальная разработка | `uv sync --locked` (все пакеты) или `uv run --package <service> ...` |
| Docker build | `uv sync --frozen --no-dev --package <service-name>` |
| Запрещено | `pip install` в рабочих окружениях |
| Добавление deps | `uv add --package <service> <pkg>` → PR + lockfile |
| Owner `uv.lock` | **Data Engineer** |
| Frontend | `package-lock.json` в git, тот же принцип |
| Корневой pyproject.toml | `exclude-newer = "7 days"` |

### Паттерн Dockerfile (per-service)

```dockerfile
FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 1. Копируем только pyproject.toml файлы → Docker layer cache
COPY pyproject.toml uv.lock ./
COPY packages/contracts/pyproject.toml packages/contracts/
COPY packages/db/pyproject.toml packages/db/
COPY packages/common/pyproject.toml packages/common/
COPY services/search/pyproject.toml services/search/

# 2. Ставим ТОЛЬКО зависимости этого сервиса (+ его shared deps)
RUN uv sync --frozen --no-dev --package search-service

# 3. Копируем исходники
COPY packages/ packages/
COPY services/search/ services/search/

CMD ["uv", "run", "--package", "search-service", "uvicorn", "search.main:app", "--host", "0.0.0.0"]
```

Каждый сервис получает свой Dockerfile по этому шаблону — меняется только имя пакета и путь.

---

## §4. Модель данных / Онтология

### Каноническая модель — нормализованный Postgres (Q1-B)

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
      │                  │ material_type  │  ← alloy | compound | pure_metal (Q3-B)
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

### Postgres Schemas (Q38-C)

| Schema | Таблицы | Источник |
|--------|---------|----------|
| `auth.*` | `users` | full-stack-fastapi-template |
| `chat.*` | `chat_sessions`, `chat_messages` | full-stack-fastapi-template + расширение |
| `experiments.*` | `materials`, `experiments`, `results`, `regimes`, `properties`, `equipment`, `labs`, `researchers`, `documents`, `entity_aliases`, `entity_same_as` | Domain |
| `staging.*` | Worker temp tables | Worker pipeline |

### Нормализованные таблицы (schema `experiments.*`)

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
    source_anchor   TEXT,               -- идентификатор блока в источнике (Q2-C)
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

### Граница `Experiment` (Q2-C — гибридная)

| Тип источника | Правило |
|---------------|---------|
| Структурированный каталог | 1 строка = 1 `Experiment` (документо-центричный) |
| Свободный текст | `(дата, лаборатория, исследователь, материал, режим)` + `source_anchor` + `grouping_key` |

### Граф (Neo4j) — проекция для визуализации (Q26-C)

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

Синхронизация: **full wipe + batch** при каждом reindex. Neo4j down → SQL fallback для subgraph API.

### Мета-сущности

| Сущность | Описание |
|----------|----------|
| **Источник (Document / Proof)** | Ссылка на конкретный документ в MinIO: файл, страница, абзац |
| **Тег/Тематика** | Классификационная метка (напр. «суперсплавы», «коррозия», «палладий») |
| **Вывод (Conclusion)** | Текстовая интерпретация результатов эксперимента с proof_ref |

### Словари и конфиги

| Файл | Содержимое |
|------|-----------|
| `dictionaries/regime_buckets.yaml` | Пороги bucket: `low: <400°C`, `medium: 400–800°C`, `high: >800°C` (Q31-C) |
| `dictionaries/distance_weights.yaml` | Веса custom distance: `w_comp`, `w_reg`, `w_emb` (Q32-C+) |
| `dictionaries/synonyms.yaml` | Ручной словарь синонимов материалов, свойств, режимов (Q8-B→C) |

---

## §5. Ключевые функции

### P0 / P1 / P2 Приоритизация (Q6)

| Приоритет | Фичи |
|-----------|------|
| **P0** | CSV/XLSX ingest, hybrid search + provenance, read-only chat со structured claims, live reindex hold-out, 100% provenance, JWT auth (login/register) |
| **P1** | Wiki (Jinja + LLM summary), gap heatmap (Material × Property × Regime bucket), graph subgraph, custom distance metric, `generate_hypothesis` tool, extended claims `kind: hypothesis`, split validator |
| **P2** | Hybrid scoring (novelty/value/risk), opt-in mini-graph в чате, UniExtract hold-out PDF, path finding (Neo4j `shortestPath`), LLM→Cypher |
| **P2 stretch** | Tensor / BMF — слайд «architecture ready», без кода (Q49-A) |

### §5.1. Связывание сущностей (Entity Linking)

- Извлечение именованных сущностей из текста (материалы, режимы, свойства)
- Нормализация: приведение синонимов к каноническим формам (Cu-Ni-сплав → купроникель)
- **DEDUP-LINK (Q14-C):**
  - P0: словарь + exact alias match
  - P1: правила по типу сущности + embedding fallback (HDBSCAN)
    - Material: `composition` / `alias` match
    - Property / Regime: словарь YAML
    - Researcher: fuzzy ФИО
  - Не удалять дубли, а связывать через `entity_same_as`

### §5.2. Гибридный поиск (Hybrid Retrieval) — Q10-B

Четырёхступенчатый pipeline:

```
1. SQL pre-filter        — WHERE material_name, temperature_min, tags
2. Candidate retrieval   — BM25 + vector + custom metric (если есть composition)
3. Reciprocal Rank Fusion — custom channel weight: 1.5
4. LLM rerank            — opt-in, default: false (P0 latency)
```

**Кастомная метрика расстояния (Q32-C+):**

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

### §5.3. Граф-запросы (Graph Traversal) — Q11-B→C

- **P0/P1:** Cypher template library (5–8 шаблонов) + параметры
- **P2:** LLM → Cypher с whitelist операций
- Multi-hop запросы: «Какие свойства измерялись на сплавах, содержащих Pd, при температурах выше 800°C?»
- Визуализация подграфов в интерфейсе
- **Graph Service принимает `template_id` + `params`** (не raw Cypher от клиента)
- Neo4j down → `503`; SQL fallback для subgraph (не для path)

### §5.4. Детекция пробелов (Gap Analysis) — Q20-B

**P1 scope:**
- Heatmap `Material × Property × Regime bucket` (buckets из YAML config)
- Клик на gap → чат с `metadata.gap_cell` → tool `generate_hypothesis`
- Tensor / BMF — **не кодим**; слайд «architecture ready» для жюри (Q49-A)

### §5.5. История решений и Provenance

- Каждый факт в системе имеет `proof_ref` — ссылку на документ, страницу и абзац
- Timeline: хронология экспериментов по конкретному материалу/свойству
- Цепочки выводов: от эксперимента к заключению
- PDF-доступ: presigned MinIO URL через `GET /api/v1/sources/{doc_id}/download` (Q39-B)

### §5.6. Wiki-страницы — Q21-C

- **Jinja-шаблоны** — детерминированный Markdown из SQL
- **+ LLM summary paragraph** — кэшируется, генерируется при reindex
- Три режима отображения: текст (Markdown), таблица, граф
- Полнотекстовый поиск по wiki

### §5.7. Генерация гипотез (Hypothesis Factory) — Q41-B+

| Приоритет | Scope |
|-----------|-------|
| **P1 (core)** | Chat tool `generate_hypothesis`: `sql_aggregate → hybrid_search → LLM → structured claim` |
| **P2 (+6ч)** | Hybrid scoring: heuristic `novelty`/`value` + LLM `risk` + `score_rationale` (Q44-D) |
| **P2 stretch** | BMF — только слайд в презентации |

**Pipeline `generate_hypothesis`:**
1. `sql_aggregate` — статистика по gap cell (соседи, coverage)
2. `hybrid_search` — поиск ближайших экспериментов
3. LLM — генерация гипотезы с structured claim

**Claim расширение (Q42-A):**
- `kind: "hypothesis"` + optional `gap_cell`
- P2: `novelty`, `risk`, `value`, `score_rationale`
- Validator split (Q45-C): hypothesis — soft (≥1 experiment_id, gap_cell required); fact — strict

**KPI side quest:** через текст чата, без отдельного Analytics endpoint.

---

## §6. Технологический стек

### Основной стек

| Слой | Технология | Обоснование |
|------|-----------|-------------|
| **Base template** | [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) | Auth, users, React frontend, Docker Compose, JWT, SQLModel |
| **Gateway** | nginx | Единый public port, SSE proxy, rate limiting |
| **Dependencies** | uv workspace + `uv.lock` | Monorepo lockfile, reproducible builds |
| **Migrations** | Alembic в `packages/db/` | Централизованные миграции, 4 schema |
| **Парсинг PDF/DOC** | Marker → LangExtract (default); UniExtract — opt-in P2 (Q27-B) | Marker бесплатный; UniExtract для hard PDF |
| **Извлечение сущностей** | LangExtract + spaCy (ru) | LLM-экстракция с grounding; spaCy для структурированных данных |
| **Эмбеддинги текста** | `intfloat/multilingual-e5-large` | 768-мерные вектора, русский язык |
| **Эмбеддинги молекул** | MatBERT / MolFormer | Специализированные модели для материалов и SMILES |
| **Основная БД** | PostgreSQL 16 + pgvector | SQL, vectorный поиск, GIN-индексы, 4 schema |
| **Графовая БД** | Neo4j Community | Визуализация, multi-hop, path finding |
| **Полнотекстовый поиск** | PostgreSQL FTS (tsvector) | Не нужен отдельный Elasticsearch для MVP |
| **LLM-агент** | LangChain + primary LLM + fallback LLM (Q23-C) | Tool-use; degraded mode без LLM |
| **Backend** | FastAPI (Python 3.11+) | Async, автодокументация, Pydantic |
| **Task Queue** | Celery + Redis | Очереди для тяжёлых операций парсинга |
| **Chat sessions** | PostgreSQL (не Redis) (Q35-D) | Персистентные чаты per user |
| **Frontend** | React + TypeScript (из template) | SPA с Tailwind + shadcn/ui |
| **Визуализация графа** | react-force-graph / D3.js | Интерактивные графы в браузере |
| **Контейнеризация** | Docker + Docker Compose | Primary path для dev и deploy |
| **Хранилище файлов** | MinIO (S3-compatible) | Хранение исходных PDF, presigned URL |
| **GPU-инференс** | Fireworks AI / Selectel / Cloud.ru | Внешний API для тяжёлых моделей |
| **Embed cache** | Redis | Кэш query embeddings (Q29-C) |

### Embeddings — стратегия инференса (Q29-C)

| Операция | Метод | Где |
|----------|-------|-----|
| Reindex (batch) | API batch embed | Worker (Celery) |
| Query (online) | CPU `e5-large` | Search Service |
| Query cache | Redis (TTL) | Search Service |

### LLM провайдер (Q23-C)

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
LLM-реранкинг ──▶ GPT-4o / Claude 3.5 через API (opt-in)
```

---

## §7. Ingestion & ETL Pipeline

### Общий поток — 9 стадий (Q13-B)

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
│  3. DEDUP-LINK (Q14-C)        │
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
│  7. SYNC-NEO4J (Q26-C)        │
│  • Full wipe + batch create   │
│  • Fail → warning, not error  │
└───────────┬───────────────────┘
            │
            ▼
┌───────────────────────────────┐
│  8. BUILD-WIKI (Q21-C)        │
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

### Детали по парсерам

| Тип источника | Парсер | Выход | Приоритет |
|---------------|--------|-------|-----------|
| Структурированный каталог (таблицы) | Детерминированный парсер (pandas + regex) | CSV с типизированными полями | P0 |
| Свободный текст (отчёты, статьи) | LangExtract с few-shot примерами | JSON с сущностями + grounding | P0 |
| PDF с таблицами и формулами | Marker → LangExtract | Markdown + JSON | P0 |
| Сложный PDF (hard tables) | UniExtract / ColPali (opt-in flag) | Markdown + JSON с bounding boxes | P2 |
| Справочники материалов | spaCy NER + словарь синонимов | Нормализованные записи материалов | P0 |

### Стратегия обработки данных (Q18-C)

**Day-1 triage при получении данных:**
1. 30 мин inventory: что за файлы, форматы, объём
2. Structured first: CSV/каталоги → ingest сразу
3. PDF параллельно — не блокирует P0
4. Column mapping → smoke на ≥1 файле

### Стратегия обновления

- **Полная переиндексация** — CSV-файлы перезаписываются, скрипт пересоздаёт все стадии (~2.5 мин)
- **Hold-out (Q9-A):** 2–3 целых файла не индексируются при старте; загрузка на сцене + фиксированный demo script (3 контрольных вопроса)
- Нет инкрементальных миграций

### UniExtract budget (Q47-C)

- Pre-hackathon: 1–2 hard PDF → UniExtract → seed JSON (pre-bake)
- Live demo: 1 known-hard PDF, `parser=uniextract` flag
- **Max 3 API calls** на весь хакатон (зафиксировать в `demo_script.md`)

---

## §8. API Design

Каждый микросервис — отдельный FastAPI-контейнер с автодокументацией на `/docs`. Все эндпоинты за nginx Gateway на `:8080`. JWT auth на всех endpoints (кроме `/api/v1/auth/*`).

### §8.1. Auth & Users (из template)

```
POST /api/v1/auth/login          # JWT token
POST /api/v1/auth/register       # создание пользователя
GET  /api/v1/users/me            # текущий пользователь
```

### §8.2. Search Service

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

### §8.3. Graph Service

```
POST /api/v1/graph/query
```

Запросы к графу по шаблонам (Q11-B→C). **Не принимает raw Cypher.**

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
GET /api/v1/graph/path?from={id}&to={id}&max_depth=4       # P2 (Q48-A)
```

**Response (path):**
```json
{
  "nodes": [...],
  "edges": [...],
  "path_length": 3
}
```

Neo4j down → `503` (без SQL fallback для path).

### §8.4. Chat / Agent Service

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

**Message Request (gap-click, Q43-A):**
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

**Response (SSE stream, Q7-C + Q42-A):**
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

**Validator rules (Q45-C):**

| Kind | Правила |
|------|---------|
| `fact` | strict number validation; ≥1 experiment_id |
| `hypothesis` | ≥1 experiment_id; `gap_cell` обязателен; числа: verbatim из neighbors ИЛИ `confidence != "high"` |
| degraded (Q23) | 1 retry → без numbers, `confidence: "low"`, neighbors table |

### §8.5. Ingestion Service (superuser only, Q37-C)

```
POST /api/v1/ingest/upload          # загрузка документов
POST /api/v1/ingest/reindex         # полная переиндексация
GET  /api/v1/ingest/status/{task_id} # статус задачи (9 стадий)
```

**Upload validation (Q12-C):**
- MIME whitelist: `application/pdf`, `application/vnd.openxmlformats-*`, `text/csv`
- Max file size: 50 MB
- Rate limit: 10 uploads / minute
- JWT + `is_superuser` required

### §8.6. Wiki Service

```
GET /api/v1/wiki/{entity_type}/{entity_id}    # Wiki-страница
GET /api/v1/wiki/search?q=палладий            # полнотекстовый поиск
```

### §8.7. Analytics Service

```
GET /api/v1/analytics/gaps?material=Pd&property=hardness    # пробелы
GET /api/v1/analytics/coverage                               # heatmap data
GET /api/v1/metrics                                          # KPI dashboard (Q19-C)
```

### §8.8. Sources Service

```
GET /api/v1/sources/{doc_id}/download     # presigned MinIO URL (Q39-B)
```

JWT required. TTL 15 min.

---

## §9. UI/UX Концепция

### Экраны по приоритетам

| Экран | Приоритет | Описание |
|-------|-----------|----------|
| `/login`, `/register` | **P0** | Из full-stack-fastapi-template |
| `/chat` + session sidebar | **P0** | Чат с ассистентом, structured claims, provenance cards |
| `/ingest` | **P0** | Drag-and-drop загрузка, progress bar (9 стадий); только superuser |
| `/wiki` | **P1 stub** | Wiki-страница сущности, 3 вкладки |
| `/graph` | **P1 stub** | Граф-эксплорер |
| `/analytics/gaps` | **P1 stub** | Gap heatmap с клик → чат |

### §9.1. Главный экран — `/chat`

- Полноэкранный чат в стиле ChatGPT
- **Session sidebar** — список чат-сессий (персистентные, per user)
- Каждый ответ содержит блоки:
  - **Claims** — structured утверждения с `confidence` badge
  - **Источники (Provenance)** — кликабельные карточки с документом, страницей, цитатой → presigned URL
  - **Связанные сущности** — теги-чипы (материал, режим, свойство), по клику → wiki (P1)
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
- **P2:** From / To search → path highlight (Q48-A)

### §9.4. Аналитика пробелов — `/analytics/gaps` (P1)

- Heatmap `Material × Property × Regime bucket`
- Пустые ячейки = пробелы
- **Клик на gap → redirect `/chat` с prefilled `metadata.gap_cell`** (Q43-A)
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

| Роль | Зона ответственности | Сервисы / Владение |
|------|---------------------|---------------------|
| **Data Engineer / Boilerplate Lead** | Скелет проекта, Docker Compose, nginx, uv workspace, CI, Task Queue, интеграция, деплой | Ingestion Service, Worker, инфраструктура, `uv.lock` owner |
| **NLP/ML-инженер** | Парсинг, извлечение сущностей, эмбеддинги, дедупликация, кастомная метрика, UniExtract | ETL pipeline, Analytics Service |
| **Backend-разработчик** | API, LLM-агент с tools, граф-запросы, поисковый сервис, chat claims validator | Search Service, Chat Service (agent), Graph Service |
| **Продуктовый аналитик** | Онтология, wiki-шаблоны, данные, демо, презентация, метрики, few-shot | Wiki Service, подготовка данных, demo script, `eval/` |
| **Frontend-разработчик** | UI (чат, wiki, граф-визуализация, аналитика пробелов), UX | Frontend приложение |

### Критические зависимости

- **Boilerplate** — блокирует всех. Должен быть готов в первые часы хакатона
- **Онтология + словари** — блокирует парсинг и схему БД. Согласовано до начала
- **API-контракты** — `packages/contracts/` зафиксированы до начала реализации

### Pre-flight checklist (Q17-C)

До начала хакатона:

- [ ] `packages/contracts/` v0.1 — Pydantic-модели всех request/response
- [ ] `uv.lock` frozen
- [ ] `seed/` — CSV + JSON для начальной загрузки
- [ ] `holdout/` — 2–3 файла для live demo
- [ ] `dictionaries/` — synonyms, regime_buckets, distance_weights
- [ ] `demo_script.md` — 3 контрольных вопроса + UniExtract budget
- [ ] full-stack-fastapi-template поднят и работает
- [ ] Каждый изучил свою технологию
- [ ] Few-shot примеры для LangExtract

### Workflow на хакатоне

```
[Первые 2 часа]
  1. Data Engineer: Docker Compose, FastAPI stubs, Celery, nginx
  2. Аналитик: зафиксировать онтологию, подготовить few-shot
  3. Все: написать тесты по своим сервисам (contract + smoke_p0.py)

[Основная работа]
  4. Параллельная разработка сервисов
  5. Агентская разработка через harness-петли
  6. Интеграция через REST API + packages/contracts/

[Merge windows (Q25-C)]
  7. Feature branches → merge 12:00, 16:00, 20:00
  8. uv.lock конфликты → только через Data Engineer

[Последние 2 часа]
  9. Интеграционное тестирование
  10. Демо на hold-out данных (3 файла + 3 вопроса)
  11. Презентация: problem → live demo → KPI → 1 слайд tech (Q30-C)
```

---

## §11. Риски и митигации

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| **UniExtract стоит дорого / медленно** | Высокая | Высокое | Marker → LangExtract default; UniExtract opt-in P2; max 3 API calls; pre-bake hold-out |
| **Качество NER на русском** | Средняя | Высокое | Few-shot примеры; словарь синонимов YAML; ручная валидация на hold-out |
| **Neo4j sync fail** | Средняя | Среднее | SQL subgraph fallback; Neo4j down → 503 для path |
| **Не успеваем за время хакатона** | Средняя | Критическое | Чёткий P0/P1/P2; декомпозиция на независимые сервисы |
| **Химические синонимы ломают дедупликацию** | Высокая | Среднее | DEDUP-LINK: правила по типу + embedding fallback; не удалять дубли, а связывать |
| **LLM галлюцинирует** | Средняя | Высокое | Structured claims + validator; provenance обязательный; rerank opt-in |
| **LLM API down** | Средняя | Высокое | Degraded mode: таблица + proof без summary; fallback provider (Q23-C) |
| **Безопасность** | Средняя | Высокое | JWT auth; write isolation (DB roles + internal network); upload validation; rate limiting |
| **uv.lock конфликты** | Средняя | Среднее | Один owner (Data Engineer); merge windows |
| **PDF parsing slow** | Средняя | Среднее | Marker ladder (Q27-B); structured first, PDF не блокирует P0 |
| **GPU-ресурсы недоступны** | Низкая | Среднее | Облачные API; fallback на CPU-модели |
| **Данные хакатона нетипичны** | Средняя | Среднее | Универсальная онтология; hold-out set для адаптации; day-1 triage |
| **UniExtract API cost** | Средняя | Среднее | Max 3 API calls; pre-bake hold-out PDFs (demo_script.md) |

---

## §12. Вне скоупа (Out of Scope)

### Убрано из out of scope (теперь IN SCOPE)

- ~~Авторизация и управление правами~~ → **JWT auth из template** (login/register); `is_superuser` для `/ingest`

### Минимальный RBAC (in scope)

| Роль | Доступ |
|------|--------|
| `user` | Chat, Search, Wiki, Graph, Analytics, Sources |
| `superuser` | Всё выше + Ingest (upload, reindex) |

### Остаётся out of scope

- **Инкрементальное обновление** — только полная переиндексация
- **Редактирование wiki** пользователями — только автогенерация
- **Мультитенантность** — один инстанс, одна общая база
- **Полноценный RBAC, OAuth, LDAP** — только user vs superuser
- **Мобильная версия** — только десктоп
- **Автоматическое дообучение моделей** — только pre-trained + few-shot
- **Интеграция с внешними системами** Норникеля (ERP, LIMS) — только загрузка файлов
- **Подключение внешних баз** (Materials Project, COD) — желательно, но не в MVP
- **Production-ready мониторинг** (Prometheus, Grafana) — только логирование + `/metrics`
- **CI/CD pipeline** — ручной деплой через Docker Compose
- **Tensor decomposition / BMF** — только слайд «architecture ready»

---

## Приложение A. Конкурентные преимущества

| # | Фича | Почему это важно |
|---|-------|------------------|
| 1 | **Работающий развёрнутый прототип** | Единственная команда, деплоившая на прошлом хакатоне — и победила |
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
| Корпус документов Норникеля | PDF, DOC | Предоставлен на хакатоне | Day-1 triage (Q18-C) |
| Каталог экспериментов | XLSX/CSV | Предоставлен | Structured first — P0 |
| Справочники материалов/оборудования | Каталоги | Предоставлен | Детерминированный парсер |
| Перечень сотрудников/лабораторий | Реестр | Предоставлен | Direct import |
| Advanced Science — суперсплавы | CSV, ~90k | Открытый | **Curated slice 500–1500 строк (Pd/Ni/superalloy)** (Q33-B) |
| MaterialsGenomics DB | API | Открытый (ненадёжный) | Optional, P2 |
| Nature — суперсплавы | PDF | 1 статья + supplementary | Seed / hold-out |

---

## Приложение C. Agent Tools

Полный реестр инструментов LLM-агента Chat Service.

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

## Приложение E. Таблица сервисов и владельцев

| Сервис | Port (internal) | Public (nginx) | Описание | Owner | P |
|--------|----------------|----------------|----------|-------|---|
| **nginx** (Gateway) | — | `:8080` | Reverse proxy, SSE, rate limit | Data Engineer | P0 |
| **Chat Service** | `:8000` | `/api/v1/chat/*`, `/api/v1/auth/*`, `/api/v1/users/*` | Auth, users, chat sessions + LLM agent | Backend | P0 |
| **Search Service** | `:8001` | `/api/v1/search` | Hybrid search (BM25 + vector + custom + RRF) | Backend | P0 |
| **Graph Service** | `:8002` | `/api/v1/graph/*` | Cypher templates, subgraph, path | Backend | P1 |
| **Wiki Service** | `:8003` | `/api/v1/wiki/*` | Jinja templates + LLM summary | Аналитик | P1 |
| **Analytics Service** | `:8004` | `/api/v1/analytics/*`, `/api/v1/metrics` | Gap heatmap, coverage, KPI | NLP/ML | P1 |
| **Ingestion Service** | `:8005` | `/api/v1/ingest/*` | Upload API (superuser) | Data Engineer | P0 |
| **Sources Service** | `:8006` | `/api/v1/sources/*` | Presigned MinIO URL | Data Engineer | P0 |
| **Worker** (Celery) | — | — (no HTTP) | ETL pipeline (9 stages), reindex | Data Engineer + NLP/ML | P0 |
| **Frontend** | `:5173` (dev) | `/` | React + TS SPA | Frontend | P0 |
| **PostgreSQL** | `:5432` | — | 4 schemas: auth, chat, experiments, staging | Data Engineer (infra) | P0 |
| **Neo4j** | `:7687` | — | Graph projection from Postgres | Data Engineer (infra) | P1 |
| **Redis** | `:6379` | — | Celery broker + embed cache | Data Engineer (infra) | P0 |
| **MinIO** | `:9000` | — | S3-compatible file storage | Data Engineer (infra) | P0 |

### Postgres DB Roles (Q12-C)

| Role | Access |
|------|--------|
| `reader` | SELECT на `experiments.*`, `chat.*` (read) |
| `writer` | INSERT/UPDATE/DELETE на `experiments.*`, `staging.*` |
| `chat_app` | CRUD на `chat.*`, `auth.*`; SELECT на `experiments.*` |
| `migrator` | DDL на все schemas |

---

## Приложение F. Деплой (Q16-C)

| Среда | Описание |
|-------|----------|
| **Primary: VPS** | VM с публичным URL; Docker Compose; nginx + HTTPS |
| **Fallback: ноутбук** | Зеркало на локальной машине; переключение при проблемах с VPS |
| **Dev** | `docker compose up` локально; `uv sync --locked` для IDE; `uv run --package <svc> uvicorn ...` для запуска одного сервиса |

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
