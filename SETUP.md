# SETUP — запуск Metalcrow

Подробное руководство по развёртыванию monorepo: локально, на сервере и с предзагрузкой данных/моделей.

Краткий обзор — в [README.md](README.md). Здесь — требования, пошаговые сценарии, что именно нужно скачать заранее и справочник по `scripts/`.

---

## Содержание

1. [Требования](#требования)
2. [Первый запуск (локально)](#первый-запуск-локально)
3. [Запуск на сервере](#запуск-на-сервере)
4. [Compose-файлы и архитектура](#compose-файлы-и-архитектура)
5. [Сервисы и порты (локально)](#сервисы-и-порты-локально)
6. [Предзагрузка данных и моделей](#предзагрузка-данных-и-моделей)
7. [Наполнение Neo4j и онтологии](#наполнение-neo4j-и-онтологии)
8. [Скрипты в `scripts/`](#скрипты-в-scripts)
9. [Разработка без Docker (опционально)](#разработка-без-docker-опционально)
10. [Отдельные сервисы (не входят в `make up`)](#отдельные-сервисы-не-входят-в-make-up)
11. [Типичные проблемы](#типичные-проблемы)

---

## Требования

### Обязательно для основного стека

| Компонент | Версия / примечание |
|-----------|---------------------|
| **Docker** | Engine 20+ (BuildKit для сборки Python-сервисов) |
| **Docker Compose** | v2 (`docker compose`, не legacy `docker-compose`) |
| **Make** | опционально — обёртка над `./scripts/dev-up.sh` |
| **Git** | клонирование репозитория |
| **Свободное место на диске** | ~15–20 GB на первый запуск (образы + модели Docling/OCR + опционально SHARED) |
| **RAM** | 8 GB минимум локально; 16 GB+ комфортно с парсером и Neo4j |

### Для разработки вне Docker

| Компонент | Где используется |
|-----------|------------------|
| **[uv](https://docs.astral.sh/uv/)** | Python workspace (lockfile в корне: `uv.lock`) |
| **[Bun](https://bun.sh/)** | фронтенд, генерация OpenAPI-клиента |
| **curl, python3, unzip** | `./scripts/fetch-shared-yandex.sh` |

### Сеть и ключи API

Многие функции **деградируют gracefully** без ключей, но для полного функционала в `.env` нужны:

- `OPENAI_API_KEY` — GraphRAG (`science-knowledge-graph`), эмбеддинги
- `LLM_API_KEY` — ReAct-агент чата, litsearch, онтология (если включён LLM)
- `LITSEARCH_API_KEY` — litsearch tool loop (может совпадать с `LLM_API_KEY`)

Шаблон переменных: `.env.example` (корень) и `.env.example` в `services/nornickel-2026-parser/`.

**Не открывайте и не коммитьте `.env`** — только `.env.example`.

---

## Первый запуск (локально)

### 1. Клонирование и конфигурация

```bash
git clone <url> metalcrow
cd metalcrow
cp .env.example .env
# при необходимости отредактируйте SECRET_KEY, POSTGRES_PASSWORD, API-ключи
```

Скрипт `make up` автоматически создаст `.env` парсера из `services/nornickel-2026-parser/.env.example`, если его нет.

### 2. Поднять стек

```bash
make up
```

Эквивалент: `./scripts/dev-up.sh`.

**Первый запуск может занять 30–60+ минут:**

1. Сборка Docker-образов (Python через `uv sync` с BuildKit-кэшем).
2. Скачивание моделей Docling/OCR в `services/nornickel-2026-parser/SHARED/MODELS` (~10–20 мин).
3. Миграции Postgres, healthcheck'и всех сервисов.

Не прерывайте процесс — дождитесь `✓ stack is up`.

### 3. Повторные запуски

После первого раза:

```bash
make up          # обычный старт (~2–5 мин)
make up-fast     # без проверки/скачивания моделей (модели уже в SHARED/MODELS)
```

### Варианты запуска

| Команда | Описание |
|---------|----------|
| `make up` | Парсер (CPU) + весь metalcrow |
| `make up-gpu` | Парсер на CUDA (`docker-compose.gpu.yml`) |
| `make up-no-parser` | Только metalcrow; L1 ходит в stub, если парсер недоступен |
| `make up-fast` | Пропустить preload моделей |
| `make parser-models` | Только предзагрузка моделей, без старта стеков |
| `make down` | Остановить парсер + metalcrow |

Флаги для shell-скриптов: `--gpu`, `--prod`, `--no-parser`, `--skip-models`, `--models-only`.

### Сборка образов без старта

```bash
make build           # parser (CPU) + metalcrow
make build-gpu       # parser CUDA + metalcrow
make build-no-parser # только metalcrow
make build && make up-fast   # пересборка, затем быстрый старт
```

Эквивалент: `./scripts/dev-build.sh` (те же флаги + `--parser-only`).

### Остановка

```bash
make down
# с удалением томов Postgres/Redis/MinIO:
./scripts/dev-down.sh -v
```

Neo4j данные в `./neo4j-data/` (bind mount) **не** удаляются флагом `-v`.

---

## Запуск на сервере

### Подготовка

```bash
cd /path/to/metalcrow
cp .env.example .env
```

**Обязательно смените** в `.env`:

- `SECRET_KEY`
- `POSTGRES_PASSWORD`
- `FIRST_SUPERUSER_PASSWORD`
- `NEO4J_PASSWORD`, `MINIO_ROOT_PASSWORD` (при необходимости)

На сервере `FRONTEND_HOST` можно задать как `http://<ip-сервера>` (см. комментарий в `.env.example`).

### Prod-деploy

```bash
make up-prod
# или: ./scripts/dev-up.sh --prod
```

Compose: `compose.yml` + `compose.prod.yml` (без auto-merge `compose.override.yml`).

**Наружу публикуется только фронтенд на порту 80.** Nginx внутри контейнера проксирует `/api`, `/docs`, `/redoc` на backend. Traefik не используется.

В браузере: `http://<ip-сервера>/` — `VITE_API_URL` менять не нужно (относительные пути `/api/v1/...`).

### Долгие операции на сервере

Ingest корпуса, загрузка Neo4j и скачивание SHARED — запускайте в `tmux` или `screen`: SSH-сессия может оборваться.

```bash
make down -- --prod    # остановка prod-стека
./scripts/dev-down.sh --prod
```

---

## Compose-файлы и архитектура

```
metalcrow (compose.yml)
├── db, redis, minio, neo4j
├── backend, frontend, workers (Celery)
├── science-knowledge-graph  → Neo4j GraphRAG
├── ontology-knowledge-graph → Postgres (БД ontology)
├── svc-parse-docling        → L1, очередь парсинга
└── article-fetcher          → litsearch PDF fetch

nornickel-2026-parser (отдельный compose в services/)
├── main (API :8114)         → alias parser-main в metalcrow-net
├── redis
└── raw2docling_raw, docling_raw2docling_clean00  (profile parsing)
```

| Файл | Назначение |
|------|------------|
| `compose.yml` | База: все сервисы metalcrow, `restart: always` |
| `compose.override.yml` | **Только локально** (auto-merge): порты наружу, live-reload backend |
| `compose.prod.yml` | Сервер: только `:80` фронта, mount SHARED для загрузчиков |
| `services/nornickel-parser.override.yml` | Подключение парсера к `metalcrow-net`, profile `parsing` для воркеров |

Общая Docker-сеть: **`metalcrow-net`** (external). Парсер доступен как `http://parser-main:8114`.

---

## Сервисы и порты (локально)

При `make up` (с `compose.override.yml`):

| Сервис | URL |
|--------|-----|
| Frontend (+ прокси `/api`) | http://localhost:5173 |
| Backend OpenAPI | http://localhost:8000/docs |
| Adminer (Postgres UI) | http://localhost:8080 |
| Parser API | http://localhost:8114/health |
| Neo4j Browser | http://localhost:7474 |
| MinIO API / Console | http://localhost:9000 / :9001 |
| Redis | localhost:6379 |

На prod наружу — только `:80` фронта.

---

## Предзагрузка данных и моделей

`docker compose up` **не наполняет** Neo4j, не качает корпус документов и не всегда качает тяжёлые ML-модели. Ниже — что где нужно и как это сделать.

### 1. Модели Docling / EasyOCR (парсер)

**Где:** `services/nornickel-2026-parser/SHARED/MODELS/`  
**Зачем:** конвертация PDF/DOCX → markdown (стадии `docling_raw`, `docling_clean00`)  
**Размер:** несколько GB

**Автоматически** при `make up` (если кэш пуст):

```bash
make parser-models
# или: ./scripts/dev-up.sh --models-only
```

Ручной путь (внутри образа парсера):

```bash
cd services/nornickel-2026-parser
docker compose -f docker-compose.yml \
  -f ../nornickel-parser.override.yml \
  --profile parsing \
  run --rm raw2docling_raw \
  python -u scripts/preload_models.py
```

Проверка без скачивания: `python scripts/preload_models.py --check-only`.

После preload повторный `make up` использует кэш; для пропуска проверки — `make up-fast`.

> `make up` поднимает API `main`, Redis и **Docling-воркеры** (`raw2docling_raw`, `docling_raw2docling_clean00`, compose-profile `parsing`) — они нужны для загрузки новых файлов через Ingest (L1). Без воркеров задачи зависают в `queued` и через ~15 мин падают с `pipeline timed out`.

### 2. spaCy / scispaCy (science-knowledge-graph)

**Где:** `services/science-knowledge-graph/models/` (vendored tarball)  
**Зачем:** NER при ingest в Neo4j  
**Как обновить** (с машины с доступом к S3 Allen AI):

```bash
cd services/science-knowledge-graph
./scripts/fetch_spacy_models.sh
# затем commit tarball + SHA256SUMS
```

В Docker-образ модель **вшита при сборке** — отдельного runtime-download нет.

### 3. Корпус SHARED (сырые файлы, OKF, facts, vectors)

**Где:** `services/nornickel-2026-parser/SHARED/`  
**Структура (основное):**

| Путь | Содержимое |
|------|------------|
| `RAW_DATA/`, `UPLOAD_DATA/` | исходные документы |
| `00_docling_raw/`, `01_docling_clean00/` | markdown после Docling |
| `MODELS/` | кэш Docling/OCR |
| `facts/` | предрасчитанные JSON-факты (spaCy) |
| `vectors/` | `entities.npy`, `entities.jsonl` (эмбеддинги) |

**Скачать готовый архив** (хакатон, ~GB):

```bash
./scripts/fetch-shared-yandex.sh
# другой URL: --url 'https://disk.yandex.ru/d/...'
```

Требует: `curl`, `python3`, `unzip`.

Без SHARED/chat и GraphRAG работают в ограниченном режиме; ingest и RAG по корпусу — нет.

### 4. Предрасчитанные facts + vectors (offline pipeline)

Если нужно **собрать** facts/vectors самостоятельно (не только скачать):

```bash
# внутри science-knowledge-graph, после OKF-markdown в SHARED
docker compose exec science-knowledge-graph \
  uv run python scripts/build_facts_db.py ...
docker compose exec science-knowledge-graph \
  uv run python scripts/embed_facts.py ...
```

Подробности: [services/science-knowledge-graph/README.md](services/science-knowledge-graph/README.md).

### 5. Онтология (батчи OKF)

**Где:** `services/ontology-knowledge-graph/ontology/batches/` (внутри образа)  
**Автозагрузка:** при **пустой** БД `ontology` сервис сам грузит батчи (`ONTOLOGY_AUTOLOAD=1`, по умолчанию).

Если БД уже не пустая, а батчи обновились:

```bash
./scripts/load-ontology-batches.sh
# на сервере: ./scripts/load-ontology-batches.sh --prod
```

### 6. Neo4j — пустой при старте

`docker compose up` поднимает Neo4j **без данных**. Варианты наполнения — [следующий раздел](#наполнение-neo4j-и-онтологии).

### 7. LLM / embeddings (runtime, не файлы)

Не «модели на диске», а API:

- `OPENAI_API_KEY` + `OPENAI_BASE_URL` — RAG и эмбедdings в Neo4j
- `LLM_API_KEY` + `LLM_BASE_URL` — чат-агент, litsearch, онтология

Без ключей: ingest/search по графу частично работают; `/rag/query` и vector search деградируют.

---

## Наполнение Neo4j и онтологии

После `make up` / `make up-prod` и наличия SHARED.

### Вариант A — быстрая загрузка предрасчитанных facts (рекомендуется для демо/прода)

Требует `SHARED/facts/` и `SHARED/vectors/` (скачать через `fetch-shared-yandex.sh` или собрать offline).

```bash
# локально
./scripts/load-precomputed-facts.sh

# smoke test
./scripts/load-precomputed-facts.sh --limit 10

# сервер
./scripts/load-precomputed-facts.sh --prod
```

Скрипт поднимает `neo4j` + `science-knowledge-graph` и запускает `load_precomputed_facts.py`. **Новых вызовов OpenAI нет** — только чтение `.npy`/JSON.

Проверка:

```bash
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "MATCH (e:Entity) RETURN count(e);"
```

### Вариант B — ingest из SHARED через API парсера (online spaCy)

Нужен работающий парсер и OKF-markdown (`00_docling_raw`). Только файлы с готовым выводом Docling; сырой PDF без стадии 0 пропускается.

Проверка coverage:

```bash
curl -s http://localhost:8114/api/v1/statistics | python3 -m json.tool
# поле stage0_done — сколько файлов можно ingest'ить
```

```bash
docker compose build science-knowledge-graph
docker compose up -d science-knowledge-graph

# smoke
docker compose exec science-knowledge-graph \
  uv run python scripts/ingest_shared_corpus.py --limit 10

# полный прогон (resumable: scripts/.ingest_shared_progress.json)
docker compose exec science-knowledge-graph \
  uv run python scripts/ingest_shared_corpus.py
```

На сервере добавьте `-f compose.yml -f compose.prod.yml` ко всем `docker compose` командам.

Ожидаемо: ~2700 путей в дереве, ~300 с OKF попадут в граф.

### Вариант C — скопировать готовую Neo4j

Остановите `neo4j`, скопируйте каталог `neo4j-data/` (bind mount в корне репо), запустите снова.

### Демо-данные (минимальный граф)

```bash
docker compose exec science-knowledge-graph \
  uv run python -m scripts.load_sample load
```

---

## Скрипты в `scripts/`

Корневой каталог `scripts/` — обёртки для деплоя, данных и CI. **Не путать** с `backend/scripts/`, `services/*/scripts/` (скрипты внутри сервисов).

### Стек Docker (основные)

| Скрипт | Назначение |
|--------|------------|
| **`dev-up.sh`** | Поднять metalcrow + парсер: сеть, `.env`, preload моделей, `docker compose up --build`. Флаги: `--gpu`, `--prod`, `--no-parser`, `--skip-models`, `--models-only`. |
| **`dev-down.sh`** | Остановить оба стека. Флаги: `--gpu`, `--prod`, `--no-parser`, `-v` (удалить volumes). |
| **`dev-build.sh`** | Собрать образы без запуска. Флаги: `--gpu`, `--prod`, `--no-parser`, `--parser-only`. |
| **`stack-common.sh`** | Общие функции для трёх скриптов выше (не запускать напрямую): пути, `metalcrow-net`, preload Docling через `preload_models.py`. |

### Данные и knowledge graph

| Скрипт | Назначение |
|--------|------------|
| **`fetch-shared-yandex.sh`** | Скачать `SHARED/` с публичного Yandex Disk в `services/nornickel-2026-parser/SHARED/`. |
| **`load-precomputed-facts.sh`** | Bulk-load `SHARED/facts` + `SHARED/vectors` → Neo4j (offline, без spaCy/OpenAI). Флаги: `--prod`, `--shared`, `--limit`, `--skip-existing`. |
| **`load-ontology-batches.sh`** | Явная перезагрузка `ontology/batches/*.json` в Postgres (ontology DB). Флаг `--prod`. |

### Разработка и утилиты

| Скрипт | Назначение |
|--------|------------|
| **`generate-client.sh`** | Экспорт OpenAPI из backend → `frontend/openapi.json`, генерация TS-клиента (`bun run generate-client`), lint. |
| **`test.sh`** | CI-style: `docker compose build`, поднять стек, прогнать backend-тесты в контейнере, `down -v`. |
| **`test-local.sh`** | То же для локальной машины (`docker-compose`, очистка `__pycache__` на Linux). |
| **`okf_bootstrap.py`** | Одноразовый bootstrap: `local_files/` → `okf/raw/*.md` (Docling или stub). Для offline-треков без полного парсера. |
| **`extract_terms.py`** | Эвристика (+ опционально LLM): термины RU/EN из `okf/raw/` → `dictionaries/synonyms_ru_en.yaml`. |
| **`add_latest_release_date.py`** | Добавить дату к последнему заголовку в `release-notes.md` (release tooling). |
| **`sync-skills.sh`** | Синхронизация agent skills: `.agents/skills` → `.claude/skills`, `.cursor/skills`. |
| **`langfuse_trace_probe.sh`** | Пробный запрос к LiteLLM gateway с Langfuse-заголовками (проверка трейсинга). Нужен `LITELLM_MASTER_KEY`. |

### Связанные скрипты в сервисах (не в корневом `scripts/`)

| Путь | Назначение |
|------|------------|
| `services/nornickel-2026-parser/scripts/preload_models.py` | Docling/OCR → `SHARED/MODELS` |
| `services/science-knowledge-graph/scripts/ingest_shared_corpus.py` | OKF из парсера → Neo4j |
| `services/science-knowledge-graph/scripts/load_precomputed_facts.py` | Загрузчик facts/vectors (вызывается из `load-precomputed-facts.sh`) |
| `services/science-knowledge-graph/scripts/fetch_spacy_models.sh` | Обновление vendored spaCy-моделей |
| `backend/scripts/prestart.sh` | Миграции Alembic перед стартом backend (в Docker) |

---

## Разработка без Docker (опционально)

Основной путь — **`make up`**. Для точечной разработки:

### Backend

```bash
cd backend
uv sync
source .venv/bin/activate
# нужны Postgres, Redis, MinIO — поднимите через docker compose только инфра:
# docker compose up -d db redis minio neo4j
```

См. [backend/README.md](backend/README.md).

### Frontend

```bash
cd frontend
bun install
bun run dev    # http://localhost:5173, API проксируется или через VITE_API_URL
```

См. [frontend/README.md](frontend/README.md).

### uv workspace

Lockfile один на весь monorepo: **`uv.lock` в корне**. Команды `uv add` / `uv sync` из подкаталога (`backend/`, `services/...`) обновляют корневой lockfile.

---

## Отдельные сервисы (не входят в `make up`)

Поднимаются своим compose при необходимости:

| Сервис | Путь | Назначение |
|--------|------|------------|
| **llm-gateway** | `services/llm-gateway/` | LiteLLM proxy (:4100), Yandex + OpenRouter fallback |
| **langfuse** | `services/langfuse/` | Self-hosted observability для LLM |
| **article-fetcher** | в `compose.yml` metalcrow | litsearch; стартует с основным стеком |

Каталог `OTHER_REPOS/` — архивные копии сторонних репозиториев, **не** рабочая интеграция. Рабочий парсер: `services/nornickel-2026-parser/`.

---

## Типичные проблемы

| Симптом | Что проверить |
|---------|----------------|
| Первый `make up` «висит» | Нормально: сборка + модели. Смотрите логи preload. |
| Parser health fail | `docker compose -f services/nornickel-2026-parser/... logs main`; сеть `metalcrow-net`. |
| RAG «нет данных» | Neo4j пустой → `load-precomputed-facts.sh` или ingest. |
| `stage0_done: 0` | Нет OKF в SHARED → скачать SHARED или прогнать Docling (`--profile parsing`). |
| Chat без LLM | Пустой `LLM_API_KEY` — детерминированный fallback, не полный агент. |
| Out of disk | `SHARED/MODELS`, Docker images, `neo4j-data/`. |
| Prod: API 404 | Заходите через фронт `:80`, не напрямую на backend (порт не опубликован). |

---

## Чеклист «полный демо-стенд»

1. `cp .env.example .env` — секреты и API-ключи  
2. `./scripts/fetch-shared-yandex.sh` — корпус + facts/vectors (опционально, но рекомендуется)  
3. `make up` — стек + модели Docling  
4. `./scripts/load-precomputed-facts.sh` — Neo4j  
5. `./scripts/load-ontology-batches.sh` — если онтология не автозагрузилась  
6. Открыть http://localhost:5173 — логин `FIRST_SUPERUSER` / пароль из `.env`

Дополнительно: [README.md](README.md), [services/science-knowledge-graph/README.md](services/science-knowledge-graph/README.md), [services/nornickel-2026-parser/README.md](services/nornickel-2026-parser/README.md).
