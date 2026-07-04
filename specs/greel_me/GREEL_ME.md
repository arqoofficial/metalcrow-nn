# Grilling Session — Научный клубок (вопросы 1–50)

> Свод решений по темам: [DECISIONS.md](DECISIONS.md)

---

## TL;DR — все 50 вопросов

| # | Решение |
|---|---------|
| 1 | **B** — нормализованный Postgres + flat view + Neo4j-проекция |
| 2 | **C** — гибридная граница эксперимента |
| 3 | **B** — типизированный Material |
| 4 | **A** — микросервисы |
| 5 | **A** — shared `contracts/` |
| 6 | P0/P1/P2 — согласованы |
| 7 | **C** — structured chat с `claims[]` + валидатор |
| 8 | **B → C** — словари, потом автоканонизация |
| 9 | **A** — hold-out на уровне файлов + demo script |
| 10 | **B** — BM25, vector и custom metric в Search через pipeline |
| 11 | **B → C** — Graph Service на шаблонах, задел под LLM→Cypher |
| 12 | **C** — write-изоляция: DB roles + internal network + валидация upload |
| 13 | **B** — линейный reindex pipeline (9 стадий) |
| 14 | **C** — DEDUP-LINK: правила по типу сущности + embedding fallback |
| 15 | **B** — P0 Frontend: `/chat` + `/ingest`; P1-экраны — заглушки |
| 16 | **C** — деплой: VPS + локальный fallback |
| 17 | **C** — полный pre-flight checklist до хакатона |
| 18 | **C** — triage при получении данных + structured first, PDF не блокирует P0 |
| 19 | **C** — metrics dashboard + eval-набор для KPI |
| 20 | **B** — Gap heatmap: Material × Property × Regime bucket |
| 21 | **C** — Wiki: Jinja-шаблоны + LLM-абзац (кэш) |
| 22 | **B** — Chat tools: search + SQL templates; graph — P1 |
| 23 | **C** — LLM: primary + fallback + degraded mode |
| 24′ | **uv workspace** monorepo + Docker Compose primary + frozen lock |
| 25 | **C** — git: feature branches + integration windows 12/16/20 |
| 26 | **C** — Neo4j: full wipe + batch; SQL fallback |
| 27 | **B** — PDF: Marker → LangExtract; UniExtract opt-in |
| 28 | **B** — contract tests + smoke P0 |
| 29 | **C** — embeddings: API batch reindex + CPU query + Redis cache |
| 30 | **C** — презентация: problem → live demo → KPI → 1 слайд tech |
| 31 | **C** — regime buckets в YAML; default: low / medium / high |
| 32 | **C+** — `d_total = w_comp·d_comp + w_reg·d_reg + w_emb·d_embed` |
| 33 | **B** — Advanced Science: curated slice ~500–1500 строк |
| 34 | **A** — API Gateway на nginx; SSE без buffering |
| 35 | **D** — Postgres + auth из full-stack-fastapi-template; чаты per user |
| 36 | **A** — template = Chat Service (auth, users, chat) + frontend |
| 37 | **C** — Ingest только для `is_superuser`; JWT единый |
| 38 | **C** — `packages/db/` + Alembic; schemas auth/chat/experiments/staging |
| 39 | **B** — presigned MinIO URL для источников; JWT required |
| 40 | **C** — greel_me/ + DECISIONS.md + SPEC_PATCH.md |
| 41 | **B+** — Hypothesis Factory: Chat tool `generate_hypothesis` (P1 core) |
| 42 | **A** — Extended `claims[]`: `kind: "hypothesis"` + optional `gap_cell` |
| 43 | **A** — `metadata.trigger = "gap_click"` + `gap_cell` в POST message body |
| 44 | **D** — Hybrid scoring: heuristic novelty/value + LLM risk |
| 45 | **C** — Split validator: hypothesis soft; fact strict (Q7) |
| 46 | **B** — Opt-in mini-graph: tool `get_subgraph(depth=1, max_nodes=12)` |
| 47 | **C** — UniExtract: hold-out pre-bake + live 1 PDF; max 3 API calls |
| 48 | **A** — Path finding: Neo4j `shortestPath` template + `/graph` From/To UI |
| 49 | **A** — BMF/tensor слайд: «architecture ready», heuristic MVP live |
| 50 | — сводка блока 41–50 (см. конец документа) |

---

## UV Policy — командные договорённости (Q24′)

### Инфраструктура

- **Monorepo:** uv workspace — корневой `pyproject.toml` + единый `uv.lock`
- **Primary path:** все сервисы через **Docker Compose** (dev + VPS/prod)
- **Local dev (optional):** `uv sync --locked` + `uv run --package <service>`

### pyproject.toml (корень)

```toml
[tool.uv]
exclude-newer = "7 days"

[tool.uv.workspace]
members = ["packages/*", "services/*", "worker"]
```

### Правила команды

| Правило | Детали |
|---------|--------|
| Lockfile в git | `uv.lock` — единственный источник версий |
| Установка | `uv sync --locked` / Docker: `uv sync --frozen --no-dev` |
| Запрещено | `pip install` в рабочих окружениях |
| Добавление deps | `uv add --package <service> <pkg>` → PR + lockfile |
| Owner | **Data Engineer** — единственный мержит `uv.lock` |
| Frontend | `package-lock.json` в git, тот же принцип |

---

# Вопросы 1–10

## Вопрос 1: Что является канонической моделью данных?

| Вариант | Суть |
|---------|------|
| **A** | PostgreSQL `experiments` — канон. Neo4j — проекция для визуализации |
| **B** | Нормализованная схема в Postgres (таблицы `experiments`, `materials`, `results` + FK). Плоская таблица — materialized view для поиска |
| **C** | Neo4j — канон. Postgres — только search index (BM25 + vectors) |

**Выбранный вариант:** **B**

---

## Вопрос 2: Что считается одним `Experiment`?

| Вариант | Правило |
|---------|---------|
| **A — Документо-центричный** | Один эксперимент = один явный блок в источнике (строка каталога, секция протокола) |
| **B — Сессионный** | Один эксперимент = `(дата, лаборатория, исследователь, материал, режим)` |
| **C — Гибридный** | Структурированные источники → A; свободный текст → B с эвристиками + `source_anchor` + `grouping_key` |

**Выбранный вариант:** **C**

---

## Вопрос 3: Как представляем материал в канонической модели?

| Вариант | Представление |
|---------|---------------|
| **A — Только текст** | `name` + `aliases[]`, эмбеддинг e5, distance = cosine текста |
| **B — Типизированный** | `material_type`: alloy \| compound \| pure_metal; для alloy — `composition` JSONB; SMILES только для молекул |
| **C — Универсальный вектор** | Всё как текст «Pd₀.₆Cu₀.₄», эмбеддинг e5 |

**Выбранный вариант:** **B**

---

## Вопрос 4: Сколько сервисов поднимаем на хакатоне?

| Вариант | Архитектура |
|---------|-------------|
| **A — Как в SPEC** | 6+ FastAPI-микросервисов, каждый со своим `/docs` |
| **B — Modular monolith** | Один `api` контейнер с Python-модулями |
| **C — 2 сервиса** | `api` (read) + `worker` (ingestion/reindex) |

**Выбранный вариант:** **A** (договорённость команды)

---

## Вопрос 5: Как фиксируем API-контракты между сервисами?

| Вариант | Подход |
|---------|--------|
| **A — Shared package** | Monorepo, пакет `contracts/` с Pydantic-моделями; все сервисы импортируют |
| **B — OpenAPI-first** | YAML-спеки в `contracts/openapi/`, код генерируется/валидируется в CI |
| **C — Устная договорённость** | Каждый копирует JSON из SPEC §8 |

**Выбранный вариант:** **A** (+ API Gateway для frontend)

---

## Вопрос 6: Что входит в P0 / P1 / P2?

| Приоритет | Фичи |
|-----------|------|
| **P0** | Ingestion CSV/XLSX, hybrid search + provenance, read-only chat, live reindex hold-out, 100% provenance в ответах |
| **P1** | Wiki, gap heatmap (без tensor decomposition), graph subgraph, кастомная метрика |
| **P2** | Tensor decomposition, hypothesis factory, UniExtract PDF, мини-граф в чате, path finding |

**Выбранный вариант:** **Согласовано** (P0/P1/P2 как предложено)

---

## Вопрос 7: Как Chat-агент гарантирует provenance?

| Вариант | Механизм |
|---------|----------|
| **A — Free-form + sources** | Свободный текст + отдельный блок `sources[]` |
| **B — Citation-only** | Каждое предложение = `[exp_id]`, постпроцессинг подставляет proof |
| **C — Structured answer** | JSON `claims[]` с `{text, experiment_ids[]}` + валидатор чисел |

**Выбранный вариант:** **C**

---

## Вопрос 8: Как нормализуем `Regime` и `Property`?

| Вариант | Подход |
|---------|--------|
| **A — As-is** | Храним как в источнике |
| **B — Словарь синонимов** | Ручной YAML, конвертация единиц, canonical names |
| **C — Словарь + автоканонизация** | B + embedding-кластеризация / LLM-маппинг неизвестных |

**Выбранный вариант:** **B → C** (сначала словари, автоканонизация по мере необходимости)

---

## Вопрос 9: Как устроен hold-out set и live reindex demo?

| Вариант | Hold-out |
|---------|----------|
| **A — Документо-уровень** | 2–3 целых файла не индексируются при старте; загрузка на сцене |
| **B — Строко-уровень** | 10–15% строк каталога в отдельном CSV |
| **C — Вопросо-уровень** | Индексируем всё, hold-out = файлы для контрольных вопросов (рискованно) |

**Выбранный вариант:** **A** (+ фиксированный demo script, 3 контрольных вопроса)

---

## Вопрос 10: Как объединяем BM25, vector и кастомную метрику?

| Вариант | Fusion |
|---------|--------|
| **A — RRF вслепую** | Равные веса → RRF → LLM rerank top-20 всегда |
| **B — Условный pipeline** | SQL pre-filter → BM25 + vector внутри candidates → custom если есть composition → rerank по threshold |
| **C — Custom-first** | Custom metric главный при составе; иначе BM25 + vector |

**Выбранный вариант:** **B** (`rerank=false` по умолчанию для P0 latency)

---

# Вопросы 11–20

## Вопрос 11: Как Graph Service выполняет запросы к Neo4j?

| Вариант | Подход | Безопасность | Гибкость |
|--------|--------|--------------|----------|
| **A — LLM → Cypher** | Агент генерирует произвольный Cypher | Низкая | Высокая |
| **B — Только шаблоны** | 5–8 фиксированных шаблонов + параметры | Высокая | Средняя |
| **C — Гибрид** | Intent classifier → шаблон; fallback LLM→Cypher с whitelist | Средняя | Высокая |

**Выбранный вариант:** **B → C** (шаблоны для P0/P1, LLM→Cypher — P2)

---

## Вопрос 12: Как изолируем Write-агент (Ingestion)?

| Вариант | Изоляция |
|--------|----------|
| **A — Логическое разделение** | Отдельный контейнер, те же DB credentials |
| **B — DB-роли** | `api` → reader; `worker` → writer на ingest-таблицы |
| **C — B + сеть + валидация** | Internal network, MIME whitelist, rate limit, max file size |

**Выбранный вариант:** **C**

---

## Вопрос 13: Какие шаги в полном reindex pipeline (worker)?

| Вариант | Стратегия |
|--------|-----------|
| **A — Inline** | Upload → parse + write + rebuild в одной задаче |
| **B — Линейный pipeline** | Фиксированные стадии с progress API |
| **C — DAG** | Параллель: normalize ∥ embed |

**Выбранный вариант:** **B** (PARSE → NORMALIZE → DEDUP-LINK → LOAD → BUILD-FLAT → EMBED → SYNC-NEO4J → BUILD-WIKI → DONE)

---

## Вопрос 14: Как работает DEDUP-LINK?

| Вариант | Стратегия |
|--------|-----------|
| **A — Только embedding** | HDBSCAN по e5 для всех сущностей |
| **B — Типо-зависимая** | Material: composition/alias; Property/Regime: словарь; Researcher: fuzzy ФИО |
| **C — B + embedding fallback** | Правила B; для unknown — HDBSCAN с высоким порогом |

**Выбранный вариант:** **C** (P0: словарь + exact alias; HDBSCAN/fuzzy — P1)

---

## Вопрос 15: Какой минимальный Frontend для P0?

| Вариант | Экраны |
|--------|--------|
| **A — Только чат** | Chat + source cards |
| **B — Чат + Ingest** | `/chat` + `/ingest` с progress bar |
| **C — Все экраны stub** | 4 экрана §9 с mock-данными |

**Выбранный вариант:** **B** (+ навигационные заглушки P1: wiki, graph, analytics)

---

## Вопрос 16: Где деплоим прототип для жюри?

| Вариант | Деплой |
|--------|--------|
| **A — Локально** | `docker compose` на ноутбуке |
| **B — VPS** | VM + публичный URL |
| **C — B + локальный fallback** | Основной URL на VPS; ноутбук — зеркало |

**Выбранный вариант:** **C**

---

## Вопрос 17: Что готово до начала хакатона?

| Вариант | Подготовка |
|--------|------------|
| **A — Минимум** | SPEC + Docker skeleton |
| **B — Данные + словари** | A + YAML словари + seed CSV + hold-out |
| **C — Полный pre-flight** | B + few-shot + demo script + contracts v0.1 + smoke queries |

**Выбранный вариант:** **C**

---

## Вопрос 18: Что делаем в первый час с данными Норникеля?

| Вариант | Стратегия |
|--------|-----------|
| **A — Big bang** | Весь корпус сразу через pipeline |
| **B — Triage + приоритеты** | 30 мин разведка → ingest по очереди |
| **C — Structured first** | CSV/каталоги сразу; PDF параллельно, не блокирует P0 |

**Выбранный вариант:** **C** (внутри B: inventory → col_map → smoke на ≥1 файле)

---

## Вопрос 19: Как измеряем KPI?

| Вариант | Подход |
|--------|--------|
| **A — На словах** | Ручной замер |
| **B — Логи + скриншоты** | Таймеры в коде |
| **C — Dashboard + eval set** | `GET /metrics` + `eval/` + `run_eval.py` |

**Выбранный вариант:** **C** (целимся на dashboard; F1/graph — скрипт, если UI не успеем)

---

## Вопрос 20: Как реализуем Gap heatmap (P1)?

| Вариант | Реализация |
|--------|------------|
| **A — Material × Property** | SQL GROUP BY, пустые ячейки = gap |
| **B — Material × Property × Regime bucket** | T ∈ {low, medium, high} как третье измерение |
| **C — Полный тензор 3D** | Sparse Material × Regime × Property |

**Выбранный вариант:** **B** (regime buckets; LLM-предложение эксперимента на gap — без байесовской факторизации)

---

# Вопросы 21–30

## Вопрос 21: Как генерируем Wiki-страницы?

| Вариант | Генерация |
|---------|-----------|
| **A — LLM per entity** | GPT пишет описание при каждом reindex |
| **B — Jinja-шаблоны** | Детерминированный MD из SQL |
| **C — B + LLM summary** | Шаблон + LLM-абзац «обзор», кэшируется |

**Выбранный вариант:** **C**

---

## Вопрос 22: Какой набор tools у Chat-агента?

| Вариант | Tools |
|---------|-------|
| **A — Минимум** | `hybrid_search` только |
| **B — Search + SQL templates** | + `sql_filter`, `sql_aggregate`, `get_experiment_details` |
| **C — B + Graph** | + `graph_template` |

**Выбранный вариант:** **B** (graph — P1)

---

## Вопрос 23: LLM провайдер и fallback?

| Вариант | Стратегия |
|---------|-----------|
| **A — Один провайдер** | Только OpenAI или Claude |
| **B — Primary + fallback** | Два провайдера |
| **C — B + degraded mode** | + режим без LLM (таблица + proof) |

**Выбранный вариант:** **C**

---

## Вопрос 24′: Структура monorepo и зависимости?

| Вариант | Структура |
|---------|-----------|
| **A — Flat** | Отдельные requirements.txt, contracts копируется |
| **B — Monorepo + shared package** | `packages/contracts/`, pip install -e |
| **C′ — uv workspace** | B + uv + единый lockfile + Docker primary |

**Выбранный вариант:** **C′** (см. UV Policy выше)

---

## Вопрос 25: Git и интеграция на хакатоне?

| Вариант | Модель |
|---------|--------|
| **A — Trunk-based** | Все в main |
| **B — Feature branches** | Merge каждые 1–2 ч |
| **C — B + integration windows** | Stubs +2ч; merge 12:00, 16:00, 20:00 |

**Выбранный вариант:** **C**

---

## Вопрос 26: Postgres → Neo4j sync?

| Вариант | Стратегия |
|---------|-----------|
| **A — Full wipe + batch** | Каждый reindex |
| **B — Incremental MERGE** | Только изменённые |
| **C — A + SQL fallback** | Full sync; Graph API fallback из SQL |

**Выбранный вариант:** **C**

---

## Вопрос 27: Fallback для PDF/DOC?

| Вариант | Pipeline |
|---------|----------|
| **A — UniExtract only** | Только API |
| **B — Лестница** | Marker → LangExtract → manual CSV |
| **C — Skip PDF** | PDF только для provenance |

**Выбранный вариант:** **B**

---

## Вопрос 28: Минимум тестов?

| Вариант | Подход |
|---------|--------|
| **A — Без тестов** | Ручной smoke |
| **B — Contract + smoke** | pytest contracts + smoke_p0.py |
| **C — Full coverage** | Unit + e2e |

**Выбранный вариант:** **B**

---

## Вопрос 29: Где считаем embeddings?

| Вариант | Inference |
|---------|-----------|
| **A — CPU в worker** | Локально при reindex и search |
| **B — API only** | HTTP batch |
| **C — Гибрид** | API batch reindex; CPU query в Search |

**Выбранный вариант:** **C**

---

## Вопрос 30: Структура презентации?

| Вариант | Фокус |
|---------|-------|
| **A — Tech-first** | Архитектура, стек |
| **B — Problem-first** | Боль → демо → KPI |
| **C — B + 1 слайд tech** | Баланс |

**Выбранный вариант:** **C**

---

# Вопросы 31–40

## Вопрос 31: Regime buckets для Gap heatmap (Q20B)?

| Вариант | Buckets |
|---------|---------|
| **A — Универсальные** | `<400°C`, `400–800°C`, `>800°C` |
| **B — Domain (металлургия)** | 4 bucket: `<200`, `200–600`, `600–1000`, `>1000°C` |
| **C — Config YAML** | Пороги в `dictionaries/regime_buckets.yaml`, default = A |

**Выбранный вариант:** **C** (default A)

---

## Вопрос 32: Кастомная метрика расстояния для сплавов?

| Вариант | Формула |
|---------|---------|
| **A — Composition only** | L1 по composition |
| **B — Composition + regime** | α·L1(comp) + β·L1(regime) |
| **C — B + fallback** | + d_embed (cosine) при неполном composition |

**Выбранный вариант:** **C+** (три компоненты)

```text
d_total = w_comp · d_comp + w_reg · d_reg + w_emb · d_embed

d_comp  — L1 на симплексе composition, норм. [0,1]
d_reg   — L1 по regime (temperature_k, pressure_pa, duration_s), min-max по корпусу
d_embed — 1 − cosine(e5_query, e5_experiment)

Default weights (P0):
  full:      w_comp=0.5, w_reg=0.3, w_emb=0.2
  no comp:   w_comp=0,   w_reg=0.2, w_emb=0.8
  comp only: w_comp=0.7, w_reg=0.3, w_emb=0

RRF custom channel weight: 1.5
P1: weights → dictionaries/distance_weights.yaml
```

---

## Вопрос 33: Advanced Science (~90k записей)?

| Вариант | Стратегия |
|---------|-----------|
| **A — Не используем** | Только NorNickel + seed |
| **B — Curated slice** | Фильтр Pd/Ni/superalloy → 500–1500 строк |
| **C — Full ingest** | 90k в Postgres |

**Выбранный вариант:** **B**

---

## Вопрос 34: API Gateway?

| Вариант | Реализация |
|---------|------------|
| **A — nginx** | reverse proxy, SSE proxy_buffering off |
| **B — Traefik** | labels в docker-compose |
| **C — FastAPI gateway** | Python-proxy |

**Выбранный вариант:** **A**

---

## Вопрос 35: История чат-сессий?

| Вариант | Хранение |
|---------|----------|
| **A — Stateless** | session_id только для tracing |
| **B — Redis TTL** | 24h ephemeral |
| **C — Postgres** | chat_sessions + messages без auth |
| **D — C + auth template** | JWT + users; персистентные чаты per user |

**Выбранный вариант:** **D**

*(Перекрывает SPEC §12 «без auth» — осознанное решение команды.)*

---

## Вопрос 36: full-stack-fastapi-template в микросервисах?

| Вариант | Распределение |
|---------|---------------|
| **A — Template = Chat Service** | Auth, users, chat DB, frontend из template |
| **B — Template = skeleton** | Auth в packages/auth/ |
| **C — Template = gateway backend** | Auth proxy |

**Выбранный вариант:** **A**

---

## Вопрос 37: Кто может загружать документы?

| Вариант | Авторизация |
|---------|-------------|
| **A — Static token** | X-Upload-Token |
| **B — JWT + role** | is_superuser |
| **C — B + ingest page guard** | /ingest только superuser |

**Выбранный вариант:** **C**

---

## Вопрос 38: Postgres и миграции?

| Вариант | Миграции |
|---------|----------|
| **A — Alembic per service** | Конфликты revision |
| **B — packages/db/ centralized** | Один Alembic |
| **C — B + Postgres schemas** | auth, chat, experiments, staging + DB roles |

**Выбранный вариант:** **C**

---

## Вопрос 39: Доступ к PDF из proof card?

| Вариант | Доступ |
|---------|--------|
| **A — Public MinIO** | Прямая ссылка |
| **B — Presigned URL** | GET /sources/{doc_id}/download, JWT, TTL 15 min |
| **C — Proxy через gateway** | nginx stream PDF |

**Выбранный вариант:** **B**

---

## Вопрос 40: Оформление итога grilling?

| Вариант | Артефакты |
|---------|-----------|
| **A — Только greel_me/** | SPEC не обновляется |
| **B — greel_me + DECISIONS.md** | Краткий log |
| **C — B + SPEC_PATCH.md** | Changelog для SPEC v1.1 |

**Выбранный вариант:** **C**

---

# Вопросы 41–50

> Тема блока: P2 backlog — Hypothesis Factory, mini-graph, UniExtract, path finding

## Вопрос 41: Граница P1 gap-click и P2 Hypothesis Factory?

| Вариант | Суть |
|---------|------|
| **A — Gap++** | Batch endpoint в Analytics Service |
| **B — Chat tool** | `generate_hypothesis` в agent registry |
| **C — Full side quest** | Отдельный flow + BMF + expert edit |
| **D — Demo stub** | Только слайд, без кода |

**Выбранный вариант:** **B+**

```text
P1 (core):
  Gap heatmap → клик → чат с gap_context → tool generate_hypothesis
  Pipeline: sql_aggregate → hybrid_search → LLM → structured claim

P2 (opt-in, +6ч после P0+P1):
  Hybrid scoring (Q44D)

P2 stretch (не кодим):
  Tensor / BMF — 1 слайд в презентации (Q49)

KPI side quest: через текст чата, без POST /analytics/hypotheses
Owner: Chat Service (tool); Analytics — только heatmap SQL
```

---

## Вопрос 42: Формат ответа `generate_hypothesis`?

| Вариант | Формат |
|---------|--------|
| **A — Extended claims** | `kind: "hypothesis"` + optional P2 fields |
| **B — Отдельный блок** | `{ claims[], hypothesis: {...} }` |
| **C — Summary-only** | Всё в summary, claims только proof |

**Выбранный вариант:** **A**

```json
{
  "claims": [{
    "text": "...",
    "experiment_ids": ["uuid-1"],
    "confidence": "medium",
    "kind": "hypothesis",
    "gap_cell": {"material": "IN-738", "property": "UTS", "regime_bucket": "high"},
    "novelty": null,
    "risk": "medium",
    "value": null
  }],
  "summary": "...",
  "tools_used": ["generate_hypothesis", "hybrid_search"]
}
```

Contract: расширить `Claim` в `packages/contracts/` — `kind: fact | hypothesis`.

---

## Вопрос 43: Как фронт передаёт gap-click в Chat?

| Вариант | Протокол |
|---------|----------|
| **A — metadata в body** | Typed JSON metadata |
| **B — Magic string** | Regex в content |
| **C — System inject** | Hidden system turn |
| **D — Direct tool call** | Обход agent |

**Выбранный вариант:** **A**

```json
POST /api/v1/chat/sessions/{id}/messages
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

Agent rule: `metadata.trigger == "gap_click"` → обязательный вызов `generate_hypothesis`.

---

## Вопрос 44: P2 scoring — откуда `novelty` / `risk` / `value`?

| Вариант | Источник |
|---------|----------|
| **A — LLM self-score** | LLM inventing numbers |
| **B — Heuristic only** | SQL coverage, без LLM risk |
| **C — Не делаем** | Только confidence |
| **D — Hybrid** | Heuristic novelty/value + LLM risk + rationale |

**Выбранный вариант:** **D**

```text
novelty = 1.0 - min(1.0, neighbor_count / corpus_median)
value   = kpi_token_overlap(kpi, gap_cell.property)  # 0–1
risk    = LLM enum {low, medium, high} + domain rationale
score_rationale = string для жюри

P2 opt-in (+6ч после P0+P1). P1: novelty/value = null.
Frontend: badge только если поле заполнено.
```

---

## Вопрос 45: Валидатор для `kind: "hypothesis"`?

| Вариант | Правила |
|---------|---------|
| **A — Strict** | ≥2 experiment_ids, strict numbers |
| **B — Soft** | ≥1 id, numbers = low confidence |
| **C — Split types** | Hypothesis soft; fact strict (Q7) |
| **D — No delta** | Тот же validator |

**Выбранный вариант:** **C**

```text
hypothesis:
  - experiment_ids.length >= 1
  - gap_cell обязателен
  - числа: verbatim из neighbors ИЛИ confidence != "high"

fact (Q7 as-is):
  - strict number validation

Degraded (Q23): 1 retry → без numbers, confidence low, neighbors table
```

---

## Вопрос 46: Inline mini-graph в чате (P2)?

| Вариант | Реализация |
|---------|------------|
| **A — Always-on** | Subgraph на каждый ответ |
| **B — Opt-in tool** | Agent вызывает `get_subgraph` |
| **C — Link-only** | Entity chips → /graph |
| **D — Static PNG** | Snapshot без интерактива |

**Выбранный вариант:** **B**

```text
Tool: get_subgraph(entity_ids, depth=1, max_nodes=12)
Response: optional subgraph { nodes[], edges[] }
Frontend: <MiniGraph> если subgraph present
P2 stretch после P0/P1
Owner: Backend (tool) + Frontend (component)
```

---

## Вопрос 47: UniExtract PDF (P2 opt-in)?

| Вариант | Триггер |
|---------|---------|
| **A — Manual checkbox** | Per-file на /ingest |
| **B — Auto ladder** | Marker confidence → UniExtract |
| **C — Hold-out demo** | Pre-bake + 1 live PDF |
| **D — Slide only** | Offline JSON |

**Выбранный вариант:** **C**

```text
Pre-hackathon: 1–2 hard PDF → UniExtract → seed JSON
Live demo: 1 known-hard PDF, parser=uniextract flag
Budget cap: max 3 API calls на весь хакатон (demo_script.md)
Worker flag: parser: marker | uniextract
Owner: NLP/ML
```

---

## Вопрос 48: Path finding между сущностями (P2)?

| Вариант | Реализация |
|---------|------------|
| **A — Neo4j shortestPath** | Cypher template |
| **B — BFS SQL** | Recursive CTE |
| **C — Chat tool only** | Без graph UI |
| **D — Skip** | Subgraph only |

**Выбранный вариант:** **A**

```text
GET /api/v1/graph/path?from={id}&to={id}&max_depth=4
→ { nodes[], edges[], path_length }

/graph: From / To search + path highlight
Neo4j down → 503 (без SQL fallback для path)
P2 stretch (+4ч)
Owner: Backend Graph Service + Frontend
```

---

## Вопрос 49: Tensor / BMF слайд — narrative для жюри?

| Вариант | Narrative |
|---------|-----------|
| **A — Architecture ready** | Live heuristic MVP + BMF plug-in seam |
| **B — Research roadmap** | Формулы, papers |
| **C — Skip slide** | Только live demo |
| **D — Honest gap** | «Не успели за 24ч» |

**Выбранный вариант:** **A**

```text
Сейчас (live): gap heatmap → chat hypothesis → heuristic scores (Q44D)
Следующий шаг: sparse tensor → BMF → uncertainty-aware ranking
Diagram: тот же gap_cell contract → BMF slot без refactor Chat/Graph
Owner слайда: Продуктовый аналитик (Q30 flow)
```

---

## Вопрос 50: Сводка блока 41–50

### P1 / P2 по блоку

| Фича | Приоритет | Решение |
|------|-----------|---------|
| Gap heatmap + gap-click → chat | **P1** | Q20 + Q43 |
| `generate_hypothesis` tool | **P1** | Q41 B+ |
| Extended claims `kind: hypothesis` | **P1** | Q42 A |
| Split validator | **P1** | Q45 C |
| Hybrid scoring (novelty/value/risk) | **P2** | Q44 D (+6ч) |
| Opt-in mini-graph | **P2** | Q46 B |
| UniExtract (hold-out) | **P2** | Q47 C |
| Path finding (Neo4j) | **P2** | Q48 A (+4ч) |
| BMF/tensor | **P2 stretch** | Q49 A — слайд only |

### Новые agent tools (дополнение Q22)

| Tool | Приоритет | Когда |
|------|-----------|-------|
| `generate_hypothesis` | P1 | `metadata.trigger == "gap_click"` или KPI-запрос |
| `get_subgraph` | P2 | Agent решает, depth=1, max 12 nodes |
