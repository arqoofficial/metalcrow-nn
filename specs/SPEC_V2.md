# Научный клубок — Техническая спецификация V2

> **Проект:** Knowledge Graph / поисково-аналитическая система для НИОКР
> **Команда:** 5 человек
> **Формат:** хакатон, time-boxed delivery
> **Версия:** 2.0 (адаптация под реально накатанный `fastapi-fullstack-template`)
> **Предыдущие:** [SPEC_V0.md](SPEC_V0.md), [SPEC_V1.md](SPEC_V1.md) (не менялся)
> **Что нового:** §0 ниже. Всё остальное — переработка V1 под факт: в репозитории уже стоит форк [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) (не абстрактный «шаблон, который встроим», а конкретный монолит с конкретными версиями зависимостей).

---

## §0. Что изменилось относительно V1 и почему

V1 был написан «сверху вниз» — от идеальной микросервисной архитектуры, в которую *потом* встраивается full-stack-fastapi-template как один из сервисов (Chat Service). Реальность оказалась обратной: в репозиторий закатан **весь** template целиком — это готовый монолит (один backend-контейнер, один frontend-контейнер, Postgres, Adminer), со своими зафиксированными версиями (Python 3.14, FastAPI, React 19, TanStack Router/Query, SQLModel, Alembic, uv-workspace на один пакет `backend`), своим CI (`.github/workflows`), своим nginx во фронтенд-контейнере, своим `is_superuser`-гейтингом и т.д.

Пересобирать это в 6 независимых FastAPI-контейнеров с отдельным API Gateway, отдельным uv-workspace на `packages/{contracts,db,common}` и отдельными `/docs` на каждый — это не «встраивание в шаблон», это **отказ от шаблона** и стройка микросервисной платформы с нуля поверх него, что на хакатоне с командой из 5 человек радикально увеличивает риск не успеть к демо. V2 фиксирует прагматичный путь: **углубляем монолит**, а не разбираем его на сервисы.

### Таблица ключевых пересмотров

| # | V1 | V2 | Почему |
|---|----|----|--------|
| 1 | 6+ независимых FastAPI-контейнеров, каждый со своим `/docs` | **Один backend, модульный монолит**: домены — это `APIRouter`-модули (`app/api/routes/chat.py`, `search.py`, `graph.py`, `wiki.py`, `analytics.py`, `ingest.py`, `sources.py`), один `/docs`, одна OpenAPI-схема | Уже так устроен template; отдельные контейнеры = HTTP-звонки между собой, дублирование auth/DB-кода, N Dockerfile'ов вместо одного. Ничего из этого не нужно для нагрузки хакатона (один Postgres, десятки одновременных пользователей) |
| 2 | nginx как отдельный API Gateway на `:8080`, единственный публичный порт | **nginx уже есть — это entrypoint фронтенд-контейнера** (`frontend/nginx.conf`), который отдаёт статику и проксирует `/api/*` на `backend:8000` внутри docker-сети. Отдельный gateway-контейнер не нужен | Функция та же (единый публичный порт), инфраструктура уже реализована и в `compose.prod.yml` наружу торчит только `frontend:80`. **Нужно дополнить конфиг под SSE** (см. §0.2, п.3) |
| 3 | uv workspace: `packages/{contracts,db,common}` + `services/*` + `worker/` | uv workspace **уже существует** (`pyproject.toml` → `[tool.uv.workspace] members = ["backend"]`) с одним пакетом для *онлайн*-API (chat/search/graph/wiki/analytics/ingest/sources). Celery worker(ы) — **отдельные от этого workspace uv-проекты** в `workers/*` (см. пункт 13 и §3 «Изоляция ML-зависимостей»), не модуль внутри `app` | Для лёгкого онлайн-стека (FastAPI/LangChain/JWT/Postgres-клиент) один пакет — не проблема, конфликтов там существенно меньше, чем в V1. Но ML/ETL-стек (torch, spaCy, marker-pdf, langextract) — отдельная история с реальным риском версионных конфликтов, для него один общий workspace-lock — это самое узкое место, не оптимизация |
| 4 | 4 Postgres-схемы (`auth`, `chat`, `experiments`, `staging`) + 4 роли (`reader`/`writer`/`chat_app`/`migrator`) | **2 схемы**: `public` (то, что уже создаёт template — `user`, плюс новые `chat_session`/`chat_message`) и `experiments` (доменные таблицы). **Роли не разводим** — вся система живёт в одном backend-процессе с одним DB-пользователем; RBAC — на уровне API (`is_superuser`), не на уровне грантов Postgres | Роли `reader/writer/chat_app/migrator` имеют смысл, когда их используют *разные процессы с разными доверием*. При одном backend-процессе разделение прав внутри одного и того же пула соединений ничего не изолирует — это просто лишняя DBA-работа и лишняя точка отказа при мердж-конфликтах в SQL |
| 5 | Celery + Redis — как часть базового плана микросервисов | Celery + Redis — **осознанное добавление** поверх template (в нём этого нет). Остаётся нужным: ETL на 9 стадий (~2.5 мин) нельзя гонять в `BackgroundTasks` синхронного FastAPI-процесса, это забьёт event loop и всех остальных пользователей на 2.5 минуты | Без выделенного воркера KPI «< 15 сек на ответ» неизбежно нарушится во время reindex |
| 6 | MinIO — часть базового плана | MinIO — **осознанное добавление**, в template отсутствует | Нужен реальный object storage для provenance-PDF; альтернатива (хранить файлы в volume backend-контейнера) не даёт presigned URL и плохо переживает пересборку контейнера |
| 7 | Neo4j — часть базового плана (P1) | Neo4j — **опциональное добавление**, кандидат на самый первый отрез, если не успеваем (см. §11) | В template его нет, поднимать имеет смысл только когда P0 полностью стабилен |
| 8 | PostgreSQL 16 (стек, §6 V1) vs PostgreSQL 18 (архитектура и `compose.yml`, §3 V1) — **внутреннее противоречие V1** | **PostgreSQL 18** everywhere — это то, что уже в `compose.yml` (`image: postgres:18`) | Явная опечатка/рассинхрон в V1, фиксируем по факту того, что реально поднято |
| 9 | «Backend: FastAPI (Python 3.11+)» | **Python 3.14** — уже зафиксировано в `backend/Dockerfile`, `backend/pyproject.toml` (`requires-python = ">=3.14,<4.0"`) и `.python-version` | V1 писался до того, как накатили конкретный template; 3.14 — это facts on the ground, не наш выбор. См. риск в §0.2 (совместимость ML-либ) |
| 10 | Frontend: «React + TypeScript (из template)», без деталей | Уточнено: **React 19 + TanStack Router (file-based) + TanStack Query + Tailwind v4 + shadcn/ui (Radix) + Biome + автогенерируемый API-клиент (`@hey-api/openapi-ts`) + Playwright** | Это конкретный (не «ванильный» Chakra UI) форк template'а; workflow добавления фичи иначе устроен, см. §9 |
| 11 | `/ingest`, `/wiki`, `/graph`, `/analytics/gaps` как «отдельные экраны» без указания как их завести в роутинг | Роуты — это файлы в `frontend/src/routes/_layout/*.tsx` (защищённый layout, где уже сидят `/settings`, `/items`), подхватываются генератором `routeTree.gen.ts` автоматически | TanStack Router file-based — надо знать конвенцию, иначе люди начнут руками писать `<Routes>`, которых в этом стеке просто нет |
| 12 | Демо-CRUD `Item`/`items` не упомянут | **Явное решение**: демо-сущность `Item` (модель, роут, тесты, фронтенд-страница `/items`, пункт в сайдбаре) — это скаффолдинг-пример из template, не часть домена «Научного клубка». Решаем на старте: **выпилить** (и роут, и таблицу, и Alembic-миграцию для неё останется в истории — это ок) или **переиспользовать под что-то реальное** | Иначе она провисит в сайдбаре до презентации и будет путать жюри/пользователей |
| 13 | Один Worker-контейнер на весь ETL (п.5 выше) | **Один или несколько Worker-образов**, каждый со своим независимым `pyproject.toml`/`uv.lock` (не workspace-member backend'а) — см. §3, «Изоляция ML-зависимостей» | ML-стек (torch, spaCy, marker-pdf, langextract, hdbscan) — типичный источник dependency hell даже внутри одного пакета; uv-workspace с общим `uv.lock` **не решает** конфликт версий, только распределяет установку по контейнерам — при реальном конфликте нужны отдельные, не связанные workspace'ом uv-проекты |

### §0.1. Что не поменялось

Всё, что не является инфраструктурой/деплоем, из V1 переносится в V2 почти без изменений: постановка проблемы (§1), KPI (§2), доменная модель сущностей Material/Experiment/Regime/Property/Equipment/Result/Document (§4, кроме имён схем), гибридный поиск и кастомная метрика (§5.2), граф-запросы через шаблоны, а не raw Cypher (§5.3), gap-анализ (§5.4), provenance-first подход (§5.5), wiki (§5.6), Hypothesis Factory (§5.7), контракт `Claim` и его валидатор (§8.4, Приложение D), реестр agent tools (Приложение C). Монолит vs микросервисы — это вопрос **транспорта** между модулями (Python-вызов вместо HTTP), а не вопрос доменной логики.

Более того, у монолита тут есть чистый плюс: agent tools (`hybrid_search`, `sql_aggregate`, `graph_template`, `generate_hypothesis`, ...) вызываются как обычные Python-функции сервисного слоя внутри одного процесса, а не HTTP-запросами к соседним контейнерам — меньше сетевых точек отказа, ниже latency, не нужен internal service discovery/retry.

### §0.2. Тонкие места и уже найденные ошибки (actionable)

Ниже — конкретные вещи, найденные при чтении текущего кода репозитория (не гипотетические), которые надо решить **до** начала параллельной разработки:

| # | Находка | Где | Что делать |
|---|---------|-----|-----------|
| 1 | **Синтаксическая ошибка, бэкенд не импортируется** — `except InvalidTokenError, ValidationError:` (Python 2 синтаксис, в Python 3 это `SyntaxError`) | `backend/app/api/deps.py:36` | Заменить на `except (InvalidTokenError, ValidationError):`. Это блокер номер один — без этого `uv run fastapi` не стартует вообще |
| 2 | **pgvector не будет установиться** — `compose.yml` использует `image: postgres:18` (ванильный), в нём нет скомпилированного расширения `vector` | `compose.yml` | Сменить образ на `pgvector/pgvector:pg18`, либо собрать свой образ `FROM postgres:18` + `CREATE EXTENSION vector`. Без этого `CREATE EXTENSION IF NOT EXISTS vector;` в миграции упадёт |
| 3 | **SSE отдаст буферизованный ответ** — `frontend/nginx.conf`, `location /api/` не отключает буферизацию и не поднимает read timeout | `frontend/nginx.conf` | Добавить для `/api/v1/chat/`: `proxy_buffering off; proxy_cache off; proxy_read_timeout 300s; chunked_transfer_encoding on;`. Без этого chat SSE будет приходить пачками, а не по токену/чанку |
| 4 | **Python 3.14 — риск для ML-стека** | `backend/Dockerfile`, `.python-version` | До хакатона (не во время!) проверить, что под 3.14 ставятся: `spacy` + `ru_core_news_lg`, `sentence-transformers`/e5-large, `hdbscan`, `marker-pdf`, `langextract`, `torch`. Если хотя бы один без wheel — **не пытаться собирать из исходников под давлением дедлайна**, а вынести ETL/NLP-воркер в отдельный Dockerfile с Python 3.11/3.12 (он и так отдельный контейнер, общаться с backend будет только через Postgres/MinIO, а не через общий интерпретатор) |
| 5 | **Alembic не настроен на несколько схем** — `env.py` не выставляет `include_schemas=True` и `version_table_schema`, `target_metadata = SQLModel.metadata` соберёт все таблицы в одну миграцию без явного `schema=` | `backend/app/alembic/env.py` | Если оставляем схему `experiments` — на каждой доменной модели прописать `__table_args__ = {"schema": "experiments"}` и добавить `include_schemas=True` в `env.py`. Если это добавляет риска — упростить до одной схемы `public` (ничего в доменной логике от этого не сломается, только предложение из V1 «схемы = контур ответственности» становится декларативным, без DB-enforcement, что и так уже верно после отказа от ролей, см. п.4 таблицы выше) |
| 6 | **Автогенерируемые файлы фронтенда — источник мердж-конфликтов** | `frontend/src/routeTree.gen.ts`, `frontend/src/client/*.gen.ts` | Не редактировать руками. `routeTree.gen.ts` пересобирается dev-сервером/`vite build` при изменении файлов в `src/routes/`; `client/*.gen.ts` — командой `bash scripts/generate-client.sh` (требует запущенный backend, читает `openapi.json`). **Каждый merge с новым backend-роутом обязан включать перегенерацию клиента**, иначе фронт будет несинхронизирован с API и TS не поймает несоответствие рантайма |
| 7 | **Синхронный SQLAlchemy `Session` + `uvicorn --workers 4`** | `backend/app/core/db.py` | Текущий engine — синхронный (`create_engine`, не `create_async_engine`). Тяжёлые операции (vector similarity на больших выборках, CPU-инференс эмбеддинга запроса) в обработчике запроса блокируют worker-процесс целиком. Для P0 (`/api/v1/search`) — либо держать вычисление query-эмбеддинга и тяжёлые SQL внутри `run_in_threadpool`, либо заранее нагрузочно проверить, что 4 sync worker'а вытягивают целевые < 15 сек при демо-нагрузке |
| 8 | **Демо-сущность `Item`** висит в сайдбаре, роутере и тестах | `backend/app/models.py`, `backend/app/api/routes/items.py`, `frontend/src/components/Items`, `frontend/src/routes/_layout/items*` | Решить на старте: выпилить или переиспользовать (см. таблицу выше, пункт 12) |
| 9 | **`.env` уже существует и не закоммичен** — корректно (проверено: в `.gitignore` есть `.env`, `git ls-files` его не находит) | `.env`, `.gitignore` | Просто дисциплина: не открывать/не коммитить `.env`, использовать `.env.example` как источник правды по списку переменных; секреты хакатона (LLM-ключи, MinIO, Neo4j) добавлять по той же схеме |
| 10 | **CI уже частично живой** — `test-backend.yml`, `playwright.yml`, `pre-commit.yml` реально гоняются на PR; `deploy-production.yml`/`deploy-staging.yml` отключены (`if: false`) | `.github/workflows/*` | V1 писал «CI/CD — ручной деплой, вне скоупа». Уточняем: **проверочный CI (тесты/линт) уже есть и должен остаться зелёным** — это бесплатная защита от регрессий на хакатоне; лишь автодеплой остаётся ручным (`docker compose -f compose.yml -f compose.prod.yml up -d --build` на VPS) |

---

## §1. Постановка проблемы

*(без изменений по существу — доменная задача не зависит от архитектуры бэкенда, см. [SPEC_V1.md §1](SPEC_V1.md#1-постановка-проблемы))*

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

*(без изменений — KPI не зависят от того, один backend-процесс их считает или шесть)*

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

**Инструментирование KPI:** `GET /api/v1/metrics` (роут внутри того же backend) + `eval/` директория с `run_eval.py`.

---

## §3. Архитектура системы

### Высокоуровневая схема

```
┌────────────────────────────────────────────────────────────────────┐
│         OFFLINE — один или несколько Celery Worker-образов         │
│    (отдельные контейнеры, БЕЗ HTTP-порта, каждый — свой uv-проект  │
│     с независимым uv.lock, если ML-зависимости конфликтуют между   │
│     собой; см. §3 «Изоляция ML-зависимостей»)                      │
│                                                                     │
│  worker-etl:   PARSE → NORMALIZE → DEDUP-LINK → LOAD → BUILD-FLAT  │
│                (pandas, marker-pdf, langextract, spaCy, hdbscan)   │
│  worker-embed: EMBED (e5-large / CPU или batch API)                │
│  worker-graph: SYNC-NEO4J, BUILD-WIKI                               │
│  (набор образов — по факту найденных конфликтов, не заранее)       │
└───────────────────────────────┬─────────────────────────────────────┘
                                │  (читает/пишет через SQLModel в тот же Postgres)
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│                    STORAGE — общий для всех                        │
│                                                                     │
│  ┌──────────────────┐  ┌───────────┐  ┌──────────────────────────┐│
│  │ pgvector/pgvector │  │ Neo4j     │  │ MinIO (S3-compatible)    ││
│  │ :pg18             │  │ (опция,   │  │ PDF / DOC исходники      ││
│  │ schemas: public / │  │  P1, см.  │  │                          ││
│  │ experiments        │  │  §0.1)   │  │ Redis (Celery broker +   ││
│  │                    │  └───────────┘  │ embed cache)            ││
│  └──────────────────┘                   └──────────────────────────┘│
└────────────────────────────────────────────┬─────────────────────────┘
                                             │
                                             ▼
┌────────────────────────────────────────────────────────────────────┐
│         ONLINE — один backend-контейнер (FastAPI, Python 3.14)      │
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

| # | Решение | Статус относительно V1 |
|---|---------|------------------------|
| 1 | **Модульный монолит** — один FastAPI-процесс, домены = `APIRouter`-модули | Пересмотрено (было: 6+ контейнеров) |
| 2 | **nginx фронтенд-контейнера — единственный публичный порт**, проксирует `/api/*` на backend по внутренней сети; SSE требует доп. настройки (§0.2 п.3) | Пересмотрено (было: отдельный gateway-контейнер на `:8080`) |
| 3 | **RBAC на уровне API** (`Depends(get_current_active_superuser)`, уже есть в `app/api/deps.py`), без разделения ролей на уровне Postgres | Пересмотрено (было: `reader`/`writer`/`chat_app`/`migrator`) |
| 4 | **uv workspace с одним пакетом `backend`** (внутреннее имя `app`) | Пересмотрено (было: `packages/{contracts,db,common}` + `services/*`) |
| 5 | **full-stack-fastapi-template — не «база Chat Service», а база всего проекта** | Пересмотрено (было: только Chat Service) |
| 6 | **Celery + Redis — добавлены поверх template** для ETL (иначе блокируется event loop, см. §0.2 п.7) | Сохранено по сути, изменено позиционирование |
| 7 | **MinIO — добавлен поверх template** для provenance-хранилища | Сохранено |
| 8 | **Neo4j — опциональное добавление**, кандидат №1 на отрез при нехватке времени | Сохранено, явно понижен приоритет |
| 9 | **Полная переиндексация** вместо инкрементальных миграций (~2.5 мин), выполняется в Celery worker | Сохранено |
| 10 | **Провенанс по умолчанию** — каждый факт хранит ссылку на PDF-источник | Сохранено |
| 11 | **Один или несколько Worker-образов с независимыми `uv.lock`** — деление не по «домену» (parse vs embed), а по факту найденных конфликтов зависимостей; ни один Worker не получает публичный HTTP-порт | Уточнено (V1 предполагал один `worker/` без обсуждения версионных конфликтов) |

### Структура репозитория (актуальная, а не целевая)

```
metalcrow/
├── compose.yml                    # добавить: redis, minio, worker-*, (опц.) neo4j
├── compose.override.yml           # dev overrides (уже есть)
├── compose.prod.yml               # прод-оверлей, публикует только frontend:80 (уже есть)
├── pyproject.toml                 # uv workspace root, members = ["backend"] (уже есть)
├── uv.lock                        # lockfile backend-workspace (уже есть) — НЕ включает workers/*
├── packages/
│   └── schema/                    # NEW, опционально: общие SQLModel-таблицы (experiments.*),
│                                   # почти без зависимостей (sqlmodel, pydantic) — path-dependency
│                                   # для backend И для каждого worker'а по отдельности, чтобы не
│                                   # дублировать определения таблиц, не тратя один uv.lock на всех
├── backend/
│   ├── Dockerfile                 # уже есть, Python 3.14 + uv
│   ├── alembic.ini
│   ├── pyproject.toml             # package name "app", deps бэкенда
│   └── app/
│       ├── main.py
│       ├── api/
│       │   ├── main.py            # регистрация роутеров — сюда добавляем chat/search/graph/...
│       │   ├── deps.py            # CurrentUser, get_current_active_superuser (уже есть — переиспользуем)
│       │   └── routes/
│       │       ├── login.py, users.py, utils.py, private.py   # из template
│       │       ├── items.py       # демо — решить (§0.2 п.8)
│       │       ├── chat.py        # NEW: sessions + SSE messages
│       │       ├── search.py      # NEW: hybrid search
│       │       ├── graph.py       # NEW: template-based Cypher / SQL fallback
│       │       ├── wiki.py        # NEW
│       │       ├── analytics.py   # NEW: gaps, coverage, /metrics
│       │       ├── ingest.py      # NEW: upload, reindex, status (superuser)
│       │       └── sources.py     # NEW: presigned MinIO URL
│       ├── models.py              # SQLModel — добавляем доменные модели (или models/ пакет)
│       ├── services/              # NEW: бизнес-логика вне роутеров
│       │   ├── search.py          # BM25 + vector + custom + RRF
│       │   ├── graph.py
│       │   ├── wiki.py
│       │   ├── analytics.py
│       │   ├── agent/             # LLM-агент, tools, claims validator
│       │   └── embeddings.py
│       └── alembic/versions/      # миграции для experiments.* + chat.*
├── workers/                       # NEW: КАЖДЫЙ — отдельный uv-проект (свой pyproject.toml + uv.lock),
│   │                               # НЕ workspace-member backend'а; ставит только свои deps
│   ├── etl/                       # Celery worker: PARSE/NORMALIZE/DEDUP-LINK/LOAD/BUILD-FLAT
│   │   ├── Dockerfile             # своя база (напр. Python 3.11/3.12, если 3.14 не тянет ML)
│   │   ├── pyproject.toml         # pandas, marker-pdf, langextract, spacy, hdbscan, packages/schema
│   │   ├── uv.lock                # независимый резолв
│   │   └── tasks/                 # parse.py, normalize.py, dedup_link.py, load.py, build_flat.py
│   ├── embed/                     # Celery worker: EMBED (или internal-only HTTP sidecar, см. ниже)
│   │   ├── Dockerfile
│   │   ├── pyproject.toml         # sentence-transformers/torch, packages/schema
│   │   └── uv.lock
│   └── graph/                     # Celery worker: SYNC-NEO4J, BUILD-WIKI (обычно лёгкий)
│       ├── Dockerfile
│       ├── pyproject.toml         # neo4j-driver, jinja2, packages/schema
│       └── uv.lock
│                                   # ↑ конкретная нарезка — по факту найденных конфликтов, не заранее;
│                                   # возможно, всё это уместится в один worker, если конфликтов не будет
├── frontend/                      # уже есть, React 19 + TanStack Router/Query
│   └── src/routes/_layout/        # сюда: chat.tsx, wiki.tsx, graph.tsx,
│                                   # analytics/gaps.tsx, ingest.tsx
├── dictionaries/                  # NEW: regime_buckets.yaml, distance_weights.yaml, synonyms.yaml
├── eval/                          # NEW: queries.json, run_eval.py
├── seed/                          # NEW: CSV/JSON для начальной загрузки
├── holdout/                       # NEW: файлы для live demo
└── .github/workflows/             # уже есть test-backend/playwright/pre-commit — держать зелёными
```

### Dependency policy (uv, один пакет)

| Правило | Детали |
|---------|--------|
| Lockfile в git | `uv.lock` в корне — единственный источник версий (уже так) |
| Локальная разработка | `uv sync` в `backend/` или через workspace-команду из корня |
| Docker build | как в текущем `backend/Dockerfile`: `uv sync --frozen --package app` |
| Добавление deps | `uv add <pkg>` в `backend/pyproject.toml` → PR + lockfile. Один владелец конфликтов не нужен — конфликтов `uv.lock` в разы меньше, чем при N пакетах |
| Worker | Каждый worker в `workers/*` — **свой** `pyproject.toml`/`uv.lock`, не workspace member backend'а (подробности ниже) |
| Frontend | `bun.lock` в git (уже есть), тот же принцип |

### Изоляция ML-зависимостей — несколько worker-образов и internal-only sidecar

**Проблема реальная, и она типична** («dependency hell») — но у неё две отдельные причины, которые лечатся по-разному, и обе не требуют превращать домены онлайн-API (chat/search/graph/wiki/analytics/ingest) в отдельные публичные сервисы за гейтвеем:

**Причина 1 — тяжёлый ML/ETL-стек живёт своей жизнью.** `torch`, `sentence-transformers`/e5-large, `spacy` + `ru_core_news_lg`, `marker-pdf`, `langextract`, `hdbscan` — это концентрированно ETL/NLP-пайплайн (стадии PARSE/NORMALIZE/DEDUP-LINK/EMBED), а не онлайн-API-домены. Онлайн-домены (chat/graph/wiki/analytics/ingest-как-приём-файла) сами по себе довольно лёгкие (FastAPI, LangChain, JWT, Postgres/Neo4j/MinIO-клиенты) и вряд ли будут конфликтовать друг с другом или с ML-стеком, если их физически не смешивать в одном резолве зависимостей.

**Причина 2 — у uv-workspace один общий `uv.lock` на все члены.** `uv sync --package <name>` выбирает, что *установить* в конкретный контейнер, но версии резолвятся **один раз на весь workspace**. Если у двух пакетов реально несовместимые ограничения на общий транзитивный пакет (например, разные версии `numpy`/`protobuf`/`huggingface_hub` от связки torch+spacy+langchain) — `uv lock` упадёт для **всего** workspace, независимо от того, в скольких контейнерах вы это потом разложите. Значит, настоящая изоляция версий — это не «несколько member'ов одного workspace», а **несколько независимых uv-проектов** (свой `pyproject.toml` + свой `uv.lock` каждый), которые не обязаны ничего резолвить совместно.

**Итоговая схема:**

- **Backend** (`backend/`) — один uv-проект (как сейчас), только лёгкий онлайн-стек. ML/ETL-библиотеки в него не добавляются вообще.
- **Worker(ы)** (`workers/etl/`, `workers/embed/`, `workers/graph/` — см. дерево репозитория выше) — каждый **свой** uv-проект, свой `Dockerfile` (при необходимости — свой Python, напр. 3.11/3.12, если что-то из ML-стека не соберётся под 3.14, см. §0.2 п.4), свой `uv.lock`. Конкретная нарезка (один worker на весь ETL или несколько) определяется **по факту**: сначала пробуете собрать всё в один `workers/etl/pyproject.toml` (`uv add marker-pdf langextract spacy sentence-transformers hdbscan ...`); если `uv lock` падает на несовместимых constraints — расщепляете конфликтующие библиотеки по разным worker-образам/очередям Celery. Не нарезайте заранее «на всякий случай» — каждый лишний образ это лишний Dockerfile, лишняя точка отказа в compose и лишнее CI-время.
- **Ни один worker не получает публичный HTTP-порт** — они читают задачи из Redis (Celery) и пишут прямо в Postgres/MinIO/Neo4j. Никакого дублирования auth, никакого nginx-роутинга, никакого нового пункта в публичном API-контракте — с точки зрения фронтенда и nginx ничего не меняется.
- **Общие таблицы без общего lockfile** — чтобы worker'ы не дублировали ручными руками SQLModel-определения `experiments.*` (и не тянули из-за этого весь `backend`-пакет со всеми его зависимостями), можно вынести только сами table-классы в отдельный маленький пакет `packages/schema/` (только `sqlmodel`+`pydantic`, без FastAPI/LangChain/ML). Backend и каждый worker подключают его как **path-dependency в своём собственном** `pyproject.toml` — это не превращает их в один workspace: у `packages/schema` своих тяжёлых зависимостей нет, поэтому конфликтовать там особо нечему, а `backend` и каждый `workers/*` всё равно резолвят *свои* deps независимо.
- **Серая зона — онлайн CPU-инференс эмбеддинга запроса в `/api/v1/search`.** Если torch не должен попадать в лёгкий backend вообще — два варианта: (а) считать query-эмбеддинг через внешний API (тот же провайдер, что уже используется для батчей при reindex), тогда backend вообще не видит ML-стек; (б) если обязателен именно CPU-инференс на месте — вынести это в **internal-only sidecar**-контейнер без публичного порта (`http://embed-internal:8010/embed`, дергается backend'ом по внутренней docker-сети). Для фронтенда/nginx/публичного контракта это невидимо — единый интерфейс наружу сохраняется, как и хотелось изначально.

### nginx (frontend-контейнер) — что доработать

`frontend/nginx.conf` уже проксирует `/api/` на backend. Для чата (SSE) добавить отдельный `location`:

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

Rate limit на `/api/v1/ingest/` — по желанию, через `limit_req_zone` в основном `nginx.conf` (не критично для 5-человечной команды и закрытого демо, но дёшево добавить).

---

## §4. Модель данных / Онтология

Доменная модель сущностей **не меняется** относительно V1 — меняется только физическое размещение (схемы Postgres) и то, что таблицы описываются как SQLModel-классы внутри `backend/app/models.py` (или пакета `app/models/`), а не в отдельном `packages/db`.

### Каноническая модель (см. [SPEC_V1.md §4](SPEC_V1.md#4-модель-данных--онтология) для диаграммы связей — без изменений)

`Experiment` ↔ `Material`, `Regime`, `Property`/`Result`, `Equipment`, `Lab`, `Researcher`, `Document` — граф связей и назначение полей идентичны V1.

### Postgres-схемы (пересмотрено — 2 схемы вместо 4, без DB-ролей)

| Схема | Таблицы | Источник |
|-------|---------|----------|
| `public` | `user` (template), `item` (демо — решить), `chat_session`, `chat_message` | template + расширение |
| `experiments` | `materials`, `experiments`, `results`, `regimes`, `properties`, `equipment`, `labs`, `researchers`, `documents`, `entity_aliases`, `entity_same_as` | Domain |

> ⚠️ Если `include_schemas=True` в `alembic/env.py` и `__table_args__ = {"schema": "experiments"}` на каждой модели покажутся лишней возней в первые часы хакатона — валидный fallback: держать всё в `public`. Ничего в API/бизнес-логике от этого не зависит, это чисто вопрос namespace'а в БД (см. §0.2 п.5).

`staging.*` из V1 (temp-таблицы воркера) — по факту не нужна как отдельная схема: Celery-таск может держать промежуточное состояние в самих строках `experiments.*` с колонкой-флагом (`status: draft|committed`) либо в JSON-поле `staging_payload` на `documents`, без отдельной схемы. Упрощение, не меняющее пайплайн по стадиям.

### Нормализованные таблицы, Search Projection (MATERIALIZED VIEW), граница `Experiment`, граф Neo4j, мета-сущности, словари

Без изменений — см. [SPEC_V1.md §4](SPEC_V1.md#4-модель-данных--онтология) полностью (SQL DDL, `experiments_flat`, индексы `ivfflat`/GIN, синонимы Q2-C/Q3-B/Q14-C и т.д. остаются в силе). Единственная правка: `embedding vector(768)` потребует `CREATE EXTENSION IF NOT EXISTS vector;` — доступно только если образ Postgres пересобран на `pgvector/pgvector:pg18` (§0.2 п.2).

---

## §5. Ключевые функции

Функциональность (P0/P1/P2, Entity Linking, Hybrid Retrieval, Graph Traversal, Gap Analysis, Provenance, Wiki, Hypothesis Factory) переносится из V1 **без изменений по содержанию** — это доменная логика, независимая от разбиения на сервисы. Единственная смысловая правка: везде, где V1 говорит «Search Service» / «Graph Service» / «Wiki Service» / «Analytics Service» / «Ingestion Service» / «Chat Service», в V2 это **модуль внутри одного backend** (`app/api/routes/<domain>.py` + `app/services/<domain>.py`), а не отдельный контейнер. Таблица приоритетов, pipeline гибридного поиска, формула кастомной метрики, Cypher-шаблоны, gap heatmap, claim schema с `kind: fact/hypothesis` — см. [SPEC_V1.md §5](SPEC_V1.md#5-ключевые-функции) целиком.

Явное следствие монолита: agent tools в §5.7 и Приложении C вызывают `app.services.search.hybrid_search(...)`, `app.services.graph.run_template(...)` и т.д. напрямую как Python-функции — не через `httpx` к соседнему контейнеру. Это упрощает degraded mode (нет отдельного сетевого failure mode «сервис недоступен» между Chat и Search — есть только «LLM недоступен» и «Neo4j недоступен», которые уже описаны в V1).

---

## §6. Технологический стек

### Основной стек (обновлено по факту репозитория)

| Слой | Технология | Статус |
|------|-----------|--------|
| **Base template** | [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) (форк с shadcn/ui вместо Chakra) | Уже раскатан целиком, не только Chat |
| **Backend** | FastAPI, **Python 3.14** | Зафиксировано в `Dockerfile`/`pyproject.toml`, не 3.11+ как в V1 |
| **ORM / миграции** | SQLModel + Alembic, один `alembic/versions/` на весь backend | Уже есть, расширяем доменными моделями |
| **Auth** | JWT (`pyjwt`), `pwdlib[argon2,bcrypt]`, `is_superuser`-гейтинг | Уже реализовано в template — переиспользуем как есть |
| **Frontend** | React 19, TanStack Router (file-based routing) + TanStack Query, Tailwind v4, shadcn/ui (Radix), Biome (lint/format вместо eslint/prettier) | Уточнено по факту `package.json` |
| **API-клиент фронтенда** | Автогенерируемый (`@hey-api/openapi-ts`, `scripts/generate-client.sh`) из `openapi.json` backend'а | Каждый новый роут → перегенерация (§0.2 п.6) |
| **Gateway / edge** | nginx **внутри frontend-контейнера** (`frontend/nginx.conf`) | Не отдельный сервис |
| **Основная БД** | **pgvector/pgvector:pg18** (не ванильный `postgres:18`) | Правка образа обязательна |
| **Task Queue** | Celery + Redis — **добавляем** | Отсутствует в template |
| **Хранилище файлов** | MinIO (S3-compatible) — **добавляем** | Отсутствует в template |
| **Графовая БД** | Neo4j Community — **опционально**, P1, первый кандидат на отрез | Отсутствует в template |
| **Полнотекстовый поиск** | PostgreSQL FTS (tsvector) | Без изменений |
| **Парсинг PDF/DOC** | Marker → LangExtract (default); UniExtract — opt-in P2 | Без изменений, см. риск Python 3.14 (§0.2 п.4) |
| **Извлечение сущностей** | LangExtract + spaCy (ru) | Без изменений, см. риск Python 3.14 |
| **Эмбеддинги текста** | `intfloat/multilingual-e5-large` | Без изменений |
| **Эмбеддинги молекул** | MatBERT / MolFormer (P1) | Без изменений |
| **LLM-агент** | LangChain + primary LLM + fallback LLM | Без изменений |
| **Контейнеризация** | Docker + Docker Compose (`compose.yml` + `compose.override.yml` dev + `compose.prod.yml` server) | Уже есть, паттерн трёх файлов сохраняем, только добавляем сервисы |
| **CI** | GitHub Actions: `test-backend.yml`, `playwright.yml`, `pre-commit.yml` (уже активны); `deploy-*.yml` отключены (`if: false`) | Держать зелёным — бесплатная защита от регрессий |

### Embeddings — стратегия инференса, LLM-провайдер, pipeline моделей

Без изменений — см. [SPEC_V1.md §6](SPEC_V1.md#6-технологический-стек) (таблицы Reindex/Query/Cache, Normal/Fallback/Degraded).

---

## §7. Ingestion & ETL Pipeline

Пайплайн из 9 стадий (PARSE → NORMALIZE → DEDUP-LINK → LOAD → BUILD-FLAT → EMBED → SYNC-NEO4J → BUILD-WIKI → DONE) переносится **без изменений по содержанию** — см. [SPEC_V1.md §7](SPEC_V1.md#7-ingestion--etl-pipeline) целиком (детали по парсерам, day-1 triage, hold-out, UniExtract budget).

Единственное изменение — исполнение: каждая стадия становится Celery-таском в одном из независимых worker-проектов (`workers/etl/tasks/`, `workers/embed/tasks/`, `workers/graph/tasks/` — конкретная нарезка по факту конфликтов зависимостей, см. §3 «Изоляция ML-зависимостей»), оркестрируется цепочкой (`chain`/`chord`) через общий Redis-брокер. Прогресс по 9 стадиям пишется в таблицу (например `experiments.ingest_tasks`), которую опрашивает `GET /api/v1/ingest/status/{task_id}` — это тот же контракт, что в V1 (Приложение D.6), просто источник данных не Redis/Celery result backend напрямую, а Postgres-таблица, которую обновляет каждый таск (надёжнее для polling-эндпоинта, не завязано на TTL result backend'а).

---

## §8. API Design

Контракты эндпоинтов, request/response-схемы и JSON Schema (Приложение D) **не меняются** — см. [SPEC_V1.md §8](SPEC_V1.md#8-api-design) целиком. Всё это — HTTP-уровень, видимый клиенту (фронтенду, eval-скриптам); он одинаков что при 6 контейнерах, что при одном backend, потому что nginx в любом случае маршрутизирует по префиксу пути на единственный upstream.

Единственное реальное отличие от V1: `POST /api/v1/auth/login`, `POST /api/v1/auth/register` — в текущем template эндпоинты называются `POST /api/v1/login/access-token` и `POST /api/v1/users/signup` (см. `backend/app/api/routes/login.py`, `users.py`). V1 использовал условные "auth/login" / "auth/register" для описания намерения — в V2 фиксируем **реальные** пути template'а, чтобы фронтенд и eval-скрипты не разъезжались с бэкендом:

```
POST /api/v1/login/access-token   # JWT token (OAuth2 password flow)
POST /api/v1/login/test-token     # проверка текущего токена
POST /api/v1/users/signup         # самостоятельная регистрация
GET  /api/v1/users/me             # текущий пользователь
```

Остальные группы (`/chat/*`, `/search`, `/graph/*`, `/wiki/*`, `/analytics/*`, `/ingest/*`, `/sources/*`) — новые, добавляются по контрактам V1 без изменений.

---

## §9. UI/UX Концепция

Экраны и приоритеты (P0/P1/P2) — без изменений, см. [SPEC_V1.md §9](SPEC_V1.md#9-uiux-концепция). Уточнение по факту стека фронтенда:

- Роуты — **файлы**, а не JSX-дерево: `frontend/src/routes/_layout/chat.tsx`, `.../wiki.tsx`, `.../graph.tsx`, `.../analytics/gaps.tsx`, `.../ingest.tsx`. `_layout.tsx` уже содержит защищённый layout с сайдбаром (`useAuth` редиректит неавторизованных) — новые экраны вешаются туда же, не поверх.
- Навигация — пункты сайдбара добавляются в `frontend/src/components/Sidebar` (там же, где сейчас `Items`/`Admin`/`Settings`; `Items` — решить, см. §0.2 п.8).
- API-вызовы с фронта — **не** руками через `fetch`/`axios`, а через сгенерированный `frontend/src/client/sdk.gen.ts` + TanStack Query hooks (см. существующий `useAuth.ts` как образец паттерна). Каждый новый backend-роут должен появиться здесь после `bash scripts/generate-client.sh`.
- UI-кит — shadcn/ui поверх Radix, уже установлен нужный набор примитивов (`dialog`, `dropdown-menu`, `select`, `tabs`, `tooltip`, `scroll-area` и т.д. — см. `frontend/src/components/ui`); для чат-ленты, provenance-карточек и heatmap использовать их, а не тащить новую UI-библиотеку.
- Тёмная тема — уже реализована через `next-themes` (`theme-provider.tsx`), ничего доделывать не нужно.
- E2E-тесты — Playwright уже настроен (`frontend/tests/*.spec.ts`, `playwright.config.ts`, отдельный `Dockerfile.playwright`); новые экраны стоит покрывать по той же схеме, что и `login.spec.ts`/`items.spec.ts`.

---

## §10. Роли в команде и Workflow

### Распределение (5 человек) — пересмотрено под монолит

Роли и зоны ответственности из V1 сохраняются по домену, но «владение сервисом» меняется на «владение набором роутеров/модулей **в одном репозитории**» — это значит больше кода в общих файлах (`app/api/main.py` — регистрация роутеров, `app/models.py` — все SQLModel-модели сразу, `frontend/src/components/Sidebar` — общая навигация), а значит **выше риск мердж-конфликтов** на этих конкретных файлах, чем было в V1 с физически разными сервисами.

| Роль | Зона ответственности | Модули (не контейнеры!) |
|------|---------------------|---------------------------|
| **Data Engineer / Boilerplate Lead** | Docker Compose (добавить redis/minio/worker-*/neo4j), CI, независимые uv-проекты воркеров, деплой | `workers/*/Dockerfile`, `workers/*/pyproject.toml`, `compose*.yml`, `.github/workflows/`, MinIO/Redis интеграция |
| **NLP/ML-инженер** | Парсинг, извлечение сущностей, эмбеддинги, дедупликация, кастомная метрика, UniExtract; при конфликте зависимостей — решение, как расщепить `workers/*` | `workers/etl/tasks/{parse,normalize,dedup_link}.py`, `workers/embed/tasks/embed.py`, `app/api/routes/analytics.py` |
| **Backend-разработчик** | LLM-агент с tools, граф-запросы, поисковый сервис, chat claims validator | `app/api/routes/{chat,search,graph}.py`, `app/services/{search,graph,agent}` |
| **Продуктовый аналитик** | Онтология, wiki-шаблоны, данные, демо, презентация, метрики, few-shot | `app/api/routes/wiki.py`, `app/services/wiki.py`, `seed/`, `eval/`, `demo_script.md` |
| **Frontend-разработчик** | UI (чат, wiki, граф-визуализация, аналитика пробелов), UX | `frontend/src/routes/_layout/*`, `frontend/src/components/*` |

### Смягчение риска мердж-конфликтов на общих файлах

- **`app/api/main.py`** (регистрация роутеров) — каждый добавляет свою строку `include_router`, конфликты почти всегда тривиальны (git разрулит сам при `git rebase`); главное — **не переставлять чужие строки**.
- **`app/models.py`** — по возможности разнести на `app/models/` пакет с файлом на домен (`materials.py`, `chat.py`, `experiments.py`) и реэкспортом в `__init__.py`, а не копить всё в одном файле, как в исходном template (там модели штатно лежат в одном файле, потому что сущностей было всего 2). Дописать в `alembic/env.py` импорт из `app.models` (пакета) вместо `app.models` (модуля) — тривиальная правка при переходе.
- **`frontend/src/client/*.gen.ts`, `routeTree.gen.ts`** — не редактируются руками вообще (см. §0.2 п.6); при конфликте — перегенерировать заново, не мёржить построчно.
- **Merge windows** (12:00, 16:00, 20:00) из V1 — сохраняем, они снижают частоту конфликтов на общих файлах независимо от архитектуры.

### Адаптация методологии `CODE_WITH_AGENTS.md` под монолит

[CODE_WITH_AGENTS.md](wiki/CODE_WITH_AGENTS.md) описывает workflow разработки через агента (grilling → спека → **«разбить на сервисы и взять свой кусок»** → тесты-до-кода → plan mode → harness-петля с внешним gate-скриптом → мониторинг → сохранение skills). Шаги 1–3 и 5–9 не зависят от архитектуры и переносятся в V2 без изменений. **Шаг 4** написан из расчёта на физическую изоляцию (отдельный контейнер/сервис на человека) — в монолите такой изоляции по умолчанию нет, поэтому «свой кусок» переопределяется как «свой набор файлов + свои тесты», и это надо явно компенсировать, иначе агенты разных участников начнут задевать общие файлы и тереть работу друг друга. Конкретные правила:

1. **Общие файлы замораживаются один раз, на старте.** Data Engineer в первый час создаёт пустые stub-роутеры для всех доменов и одним коммитом регистрирует их все в `app/api/main.py`. После этого коммита `app/api/main.py` почти никто больше не трогает — каждый добавляет код только в уже существующий свой файл.
2. **`app/models.py` дробится на пакет `app/models/<domain>.py` на старте**, а не «когда будет время» — это самый вероятный источник построчных конфликтов, откладывать нельзя.
3. **Harness-петля каждого участника гоняет как gate только свои тестовые файлы**, а не весь `pytest` — благо шаблон уже даёт конвенцию «один test-файл на роутер» (`backend/tests/api/routes/test_<module>.py`). Так недописанный код одного человека не блокирует петлю обратной связи другого — то же свойство изоляции, которое в V1 давали отдельные сервисы, здесь достигается на уровне тестовых файлов.
4. **Полный gate (весь `pytest` + Playwright + сборка) запускается не непрерывно каждым агентом, а только в merge windows (12:00 / 16:00 / 20:00), и только одним интегратором.** Это прямой аналог «harness loop → external gate» из шага 7, просто проверка «всё работает вместе» смещена на контрольные точки, а не идёт параллельно от пяти агентов на общем `main`.
5. **Один владелец на Alembic-мёрж за merge window** (по аналогии с уже существующим правилом «`uv.lock` — владелец Data Engineer»): при параллельной генерации миграций двумя людьми от одного `down_revision` почти гарантированно получаются две головы истории, которые надо разруливать `alembic merge` вручную — дешевле один раз в день назначить, кто это делает.
6. **Каждый участник (агент) работает в своей ветке/`git worktree`**, не редактируя общий рабочий каталог параллельно с другими — это устраняет не только git-конфликты, но и риск, что два агента одновременно перезапишут один и тот же файл на уровне файловой системы раньше, чем успеют закоммититься.
7. **Автогенерируемые файлы фронтенда** (`routeTree.gen.ts`, `client/*.gen.ts`) — не мёржатся построчно никогда; при расхождении просто пересобираются.

Итог: архитектурное решение (монолит) не отменяется, но единица «своего куска» из шага 4 методологии сужается с «сервис» до «файлы + тесты», а функцию, которую в V1 выполняла физическая изоляция сервисов, в V2 берут на себя пункты 1–7 выше.

### Критические зависимости

- **Boilerplate** (docker compose + миграция под pgvector + Celery skeleton) — блокирует всех, должен быть готов в первые часы
- **Онтология + словари** — блокирует парсинг и схему БД
- **API-контракты** — теперь это Pydantic/SQLModel-схемы прямо в `app/models.py`/`app/schemas.py` одного репозитория, а не отдельный публикуемый пакет `packages/contracts` — фиксируются тем же способом (заморозка интерфейса до начала параллельной разработки), но без необходимости версионировать/паблишить отдельный пакет

### Pre-flight checklist (обновлено)

До начала хакатона:

- [ ] **Починить `except InvalidTokenError, ValidationError:` в `deps.py`** (иначе бэкенд не стартует) — см. §0.2 п.1
- [ ] Сменить образ Postgres на `pgvector/pgvector:pg18`, накатить `CREATE EXTENSION vector`
- [ ] **Прогнать `uv lock` для полного набора ML-зависимостей (spaCy ru, e5-large/sentence-transformers, hdbscan, marker-pdf, langextract) под Python 3.14 в отдельном тестовом проекте `workers/etl/`** — если резолв падает или конкретная либа не собирается, сразу решить: (а) понизить Python в этом worker'е до 3.11/3.12, (б) расщепить конфликтующие либы по нескольким worker-образам (`workers/etl/`, `workers/embed/`, ...) — см. §3 «Изоляция ML-зависимостей». Делать это до хакатона, а не в первый час
- [ ] Добавить `redis`, `minio`, `worker` (и опционально `neo4j`) в `compose.yml` + `compose.override.yml`
- [ ] Доработать `frontend/nginx.conf` под SSE (`proxy_buffering off` для `/api/v1/chat/`)
- [ ] **Завести пустые stub-роутеры на все домены и зарегистрировать их разом в `app/api/main.py`** одним коммитом (см. «Адаптация методологии `CODE_WITH_AGENTS.md`» выше) — чтобы этот файл перестал быть точкой конфликта после первого часа
- [ ] **Разбить `app/models.py` на пакет `app/models/<domain>.py`** до начала параллельной разработки, не откладывать
- [ ] Решить судьбу демо-сущности `Item` (выпилить или переиспользовать)
- [ ] Миграции для схемы `experiments.*` (и `chat_session`/`chat_message` в `public`)
- [ ] `seed/`, `holdout/`, `dictionaries/` — как в V1
- [ ] `demo_script.md` — 3 контрольных вопроса + UniExtract budget
- [ ] Каждый прогнал `docker compose up --build` и увидел живой `/docs`, `/login`, зелёный CI

### Workflow на хакатоне

Порядок фаз (pre-flight → P0 → P1 → P2), merge windows, декомпозиция по трекам — без изменений относительно V1 и [PLAN_V1.md](wiki/PLAN_V1.md); меняется только единица декомпозиции (роутер/модуль вместо контейнера/сервиса).

---

## §11. Риски и митигации

Риски из V1 остаются в силе (UniExtract, качество NER, Neo4j sync fail, не успеваем, химические синонимы, LLM галлюцинирует/down, безопасность, PDF parsing slow, GPU, данные хакатона нетипичны) — см. [SPEC_V1.md §11](SPEC_V1.md#11-риски-и-митигации). Ниже — риски, специфичные для V2/монолита:

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| **Python 3.14 несовместим с ключевой ML-библиотекой** | Средняя | Высокое | Проверить до хакатона (pre-flight); fallback — отдельный Dockerfile воркера на 3.11/3.12 |
| **pgvector-образ не подключён вовремя** | Низкая, если исправлено в pre-flight | Высокое (весь vector search не работает) | Явный пункт в pre-flight checklist, проверка `SELECT * FROM pg_extension` на смоук-тесте |
| **SSE буферизуется nginx'ом, чат «зависает» до конца ответа** | Средняя | Среднее (демо выглядит сломанным) | Явный конфиг `proxy_buffering off` в pre-flight, smoke-тест на реальный curl со `stream` |
| **Мердж-конфликты в общих файлах монолита** (`api/main.py`, `models.py`, sidebar) чаще, чем при раздельных сервисах | Средняя | Среднее | Разнесение моделей по пакету `app/models/`, merge windows, дисциплина «не трогай чужие строки» |
| **Синхронный DB engine + тяжёлые вычисления блокируют event loop** | Средняя | Среднее (KPI < 15 сек под нагрузкой) | `run_in_threadpool` для CPU-тяжёлых участков search/embeddings; нагрузочный smoke-тест перед демо |
| **Team по инерции пытается воссоздать микросервисы из V1** (лишние контейнеры, лишний Gateway) | Средняя | Высокое (потеря времени в первые часы) | Этот документ + явный пункт в kickoff-созвоне: «монолит — не временное решение, а финальная архитектура хакатона» |
| **Dependency hell в ETL/ML-стеке** (torch/spaCy/marker-pdf/langextract/hdbscan конфликтуют друг с другом или с Python 3.14) | Высокая (по опыту команды) | Высокое (весь ETL не собирается) | `workers/*` — независимые uv-проекты со своими `uv.lock` (не workspace backend'а), нарезка по факту конфликта, а не заранее; проверка сборки — pre-flight, не первый час хакатона |

---

## §12. Вне скоупа (Out of Scope)

Совпадает с V1, с одной правкой:

### Убрано из out of scope (теперь IN SCOPE)

- ~~Авторизация и управление правами~~ → **JWT auth из template**, уже реализовано (`login.py`, `users.py`, `deps.py`)

### Минимальный RBAC (in scope) — уточнено

| Роль | Доступ | Механизм |
|------|--------|----------|
| `user` | Chat, Search, Wiki, Graph, Analytics, Sources | `Depends(get_current_user)` (уже есть) |
| `superuser` | Всё выше + Ingest (upload, reindex) | `Depends(get_current_active_superuser)` (уже есть) |

Разделение прав на уровне Postgres-ролей (`reader`/`writer`/`chat_app`/`migrator` из V1) — **сознательно не делаем** (см. §0, п.4 таблицы) и добавляем явно в out of scope этой версии:

### Дополнительно вне скоупа в V2

- **DB-роли Postgres для read/write split** — RBAC полностью на уровне API
- **Пересборка на микросервисы во время хакатона** — если после P0 останется время и энергия, это осознанный post-hackathon рефакторинг, не часть текущего плана
- Всё остальное (инкрементальное обновление, редактирование wiki пользователями, мультитенантность, полноценный RBAC/OAuth/LDAP, мобильная версия, дообучение моделей, внешние ERP/LIMS, внешние базы данных, prod-мониторинг, tensor decomposition/BMF) — как в V1

---

## Приложение A. Конкурентные преимущества

Без изменений, см. [SPEC_V1.md Приложение A](SPEC_V1.md#приложение-a-конкурентные-преимущества). Добавление: работающий монолит на проверенном шаблоне — это ниже риск «не задеплоили вообще» по сравнению с 6 независимыми контейнерами, что само по себе конкурентное преимущество для time-boxed формата.

## Приложение B. Датасеты

Без изменений, см. [SPEC_V1.md Приложение B](SPEC_V1.md#приложение-b-датасеты).

## Приложение C. Agent Tools

Реестр инструментов — без изменений в контрактах (input/output), см. [SPEC_V1.md Приложение C](SPEC_V1.md#приложение-c-agent-tools). Реализация — прямые вызовы функций `app/services/*` внутри одного процесса, не HTTP к соседним сервисам (см. §0.1).

## Приложение D. JSON-схемы Request / Response

Без изменений, см. [SPEC_V1.md Приложение D](SPEC_V1.md#приложение-d-json-схемы-request--response) целиком (Search, Chat, Graph, Ingest). Единственная точечная правка — реальные пути auth-эндпоинтов, см. §8.

## Приложение E. Таблица модулей и владельцев (заменяет «Таблица сервисов и владельцев» из V1)

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
| **Celery Worker(ы)** | `workers/etl/`, `workers/embed/`, `workers/graph/` — независимые uv-проекты, нарезка по факту конфликтов зависимостей (см. §3) | — (без HTTP; опционально internal-only `embed` sidecar для онлайн-инференса) | Data Engineer + NLP/ML | P0 |
| **`packages/schema`** (опционально) | Общие SQLModel-таблицы `experiments.*`, без тяжёлых зависимостей | — | Data Engineer | P0 |
| **Frontend** | `frontend/src/routes/_layout/*` | `/` | Frontend | P0 |
| **PostgreSQL (pgvector)** | `compose.yml: db` | — | Data Engineer (инфра) | P0 |
| **Redis** | `compose.yml: redis` (NEW) | — | Data Engineer (инфра) | P0 |
| **MinIO** | `compose.yml: minio` (NEW) | — | Data Engineer (инфра) | P0 |
| **Neo4j** | `compose.yml: neo4j` (NEW, опционально) | — | Data Engineer (инфра) | P1 |

## Приложение F. Деплой

Совпадает с реальностью репозитория (README.md уже это описывает):

| Среда | Как | Файлы |
|-------|-----|-------|
| **Dev (локально)** | `docker compose up -d --build` (авто-мёрж `compose.yml` + `compose.override.yml`) | Открывает порты db/adminer/backend/frontend, live-reload backend |
| **Прод (VPS, хакатон)** | `docker compose -f compose.yml -f compose.prod.yml up -d --build` | Наружу торчит только `frontend:80` |
| **Fallback** | Зеркало на локальной машине команды, переключение при проблемах с VPS | — |
| **CI (проверочный, не деплой)** | `test-backend.yml`, `playwright.yml`, `pre-commit.yml` на каждый PR | `.github/workflows/` |

## Приложение G. Глоссарий

Всё из [SPEC_V1.md Приложение G](SPEC_V1.md#приложение-g-глоссарий) плюс:

| Термин | Определение |
|--------|-------------|
| **Модульный монолит** | Один деплоюмый backend-процесс, внутри разделённый на независимые по коду (но не по деплою) модули-роутеры |
| **`routeTree.gen.ts`** | Автогенерируемый файл маршрутизации TanStack Router; не редактируется руками |
| **`*.gen.ts` (client)** | Автогенерируемый TS-клиент API из `openapi.json`, пересобирается `scripts/generate-client.sh` |
