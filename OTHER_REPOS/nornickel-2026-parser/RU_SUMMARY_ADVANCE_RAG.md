# ADVANCE_RAG: краткое описание

`ADVANCE_RAG` — сервис retrieval и question-answering поверх документов в `SHARED`, с векторным индексом в Chroma.

## Что внутри

- FastAPI API (`/health`, `/ready`, `/api/v1/...`) и admin-эндпоинты для управления индексом.
- Сервис индексации OKF-документов из `SHARED`.
- Dense retrieval через Chroma и векторные эмбеддинги.
- Фоновый worker для асинхронной индексации путей.
- MCP-сервер для интеграций с внешними агентами/инструментами.

## Ключевые особенности

- Разделение runtime-ролей: API, worker и MCP могут запускаться отдельно.
- Единая конфигурация (`config.yaml` + `.env`) с Pydantic-валидацией.
- Стабильное разрешение путей к `SHARED` относительно `base_dir`, а не CWD.
- Наблюдаемость из коробки: метрики Prometheus, tracing OpenTelemetry, структурные логи.

## Архитектура

- `app/main.py` собирает приложение, конфиг, адаптер Chroma и очередь.
- `indexing/service.py` отвечает за обход файлов, парсинг OKF и upsert в коллекцию.
- `queue/*` реализует backend очереди и worker-процессинг задач индексации.
- `retrieval/*` выполняет препроцессинг запроса и retrieval-логику.
- `api/*` предоставляет внешние HTTP-контракты для поиска, индексации и администрирования.

## ML-архитектура

- Базовый dense retrieval:
  - `cpu_local`: локальные эмбеддинги Chroma через ONNX-модель `all-MiniLM-L6-v2`.
  - `openapi`: удаленные эмбеддинги через OpenAI-compatible endpoint (`text-embedding-3-small`).
- Коллекция Chroma персистится на диск (`persist_directory`) и переиспользуется между запусками.
- Препроцессинг запросов использует NLTK (токенизация, стемминг/лемматизация, EN/RU).

## Поддержка CPU и GPU

- Основной `cpu_local` режим ориентирован на CPU и локальный ONNX inference.
- В `openapi` режиме вычисление эмбеддингов выносится во внешний сервис (локальный GPU не обязателен).
- Для `ADVANCE_RAG` нет отдельного GPU-переключателя уровня OCR; режим задается через `chroma.mode`.

## Полный стек технологий (ADVANCE_RAG)

- Язык и рантайм: `Python 3.12+`.
- API/сервер: `FastAPI`, `Uvicorn`.
- Конфиг и типизация: `Pydantic v2`, `pydantic-settings`, `PyYAML`, `python-dotenv`.
- Retrieval и векторная БД: `ChromaDB`, `langchain-core`.
- NLP-препроцессинг: `NLTK`, `fuzzysearch`, `python-frontmatter`.
- Очередь и backend: in-memory queue + `Redis` backend.
- Observability: `loguru`, `prometheus-client`, `OpenTelemetry` (API/SDK/OTLP, FastAPI instrumentation).
- Интеграции: `mcp` (MCP server).
- CLI/утилиты: `Typer`, `Rich`.
- Тестирование: `pytest`, `pytest-asyncio`, `httpx`, `fakeredis`.
- Контейнеризация: `Docker`, `Docker Compose`.

## Устройство очереди задач

- Тип задачи: `IndexPathJob` (`subfolder_path`, `source_subfolder`, `correlation_id`).
- Backend очереди:
  - In-memory (`deque` + lock) для простого режима.
  - Redis (`advance_rag:queue`, `advance_rag:failed`) для production/docker разделения API и worker.
- Жизненный цикл:
  1. API ставит задачу индексации в очередь.
  2. Worker читает задачу, перечисляет OKF-файлы и индексирует их в Chroma.
  3. При ошибке задача фиксируется в failed-очереди с текстом ошибки.
- Worker публикует метрики успеха/ошибки и длительности выполнения job.

## Прогрев моделей и ONNX перед использованием

- При старте `create_app()` задает пути к локальным артефактам:
  - `ADVANCE_RAG_ONNX_MODEL_DIR`
  - `ADVANCE_RAG_NLTK_DATA`
  - `ADVANCE_RAG_RERANKER_STOPWORDS`
- В `chroma_adapter.initialize()`:
  - настраивается директория ONNX-кэша (`ONNXMiniLM_L6_V2.DOWNLOAD_PATH`);
  - создается/открывается коллекция Chroma;
  - в `cpu_local` режиме выполняется warmup-запрос `query("warmup")` для снятия cold-start штрафа ONNX.
- В `main._warm_runtime()` дополнительно выполняются warmup-шаги:
  - `preprocess_query("warmup", ...)` — прогрев NLTK ресурсов/токенизатора;
  - `query_dense("warmup", limit=1)` — прогрев retrieval тракта.
- В результате первый пользовательский запрос не платит полную стоимость ленивой инициализации моделей.
