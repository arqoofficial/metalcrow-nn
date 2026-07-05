# OTHER_REPOS

Здесь лежат **сторонние репозитории**, включённые в monorepo Metalcrow как снимки — без интеграции в общую структуру проекта.

## Содержимое

| Каталог | Описание |
|---------|----------|
| [`nornickel-2026-parser/`](nornickel-2026-parser/) | Полный исходный код отдельного репозитория Константина Ушенина для работы с корпусом документов NorNickel (см. ниже). |

## nornickel-2026-parser

Отдельный репозиторий за авторством **Константина Ушенина**. В Metalcrow добавлен **as is** — последняя на момент копирования версия исходного кода, без доработок под monorepo.

Репозиторий объединяет два самостоятельных сервиса, которые вместе покрывают путь от сырого документа до поиска по корпусу:

### OKF_PARSER

Конвейерная обработка документов в формат OKF.

- HTTP API: загрузка файлов, статус пайплайна, дерево файлов, запуск обработки.
- Воркеры по стадиям `raw → docling_raw → docling_clean00` через Redis-очереди.
- Docling + EasyOCR для конвертации PDF/DOCX и сканов (RU/EN), очистка markdown и YAML-frontmatter.
- Админ-панель, скрипты диагностики и переиндексации, Docker Compose (CPU/GPU).
- Слои `presentation` / `services` / `workers` / `data` / `config`, метрики, health-check, lock-механизмы.

Подробнее: [`nornickel-2026-parser/RU_SUMMARY_OKF_PARSER.md`](nornickel-2026-parser/RU_SUMMARY_OKF_PARSER.md).

### ADVANCE_RAG

Retrieval и question-answering поверх OKF-документов из `SHARED`.

- FastAPI API для поиска, индексации и администрирования индекса.
- Dense retrieval через ChromaDB и эмбеддинги (локальный ONNX `all-MiniLM-L6-v2` или OpenAI-compatible endpoint).
- Режимы поиска: dense, sparse, fuzzy, RRF, advance с reranker; NLP-препроцессинг (NLTK, EN/RU).
- Фоновый worker для асинхронной индексации путей, MCP-сервер для интеграции с агентами.
- Prometheus, OpenTelemetry, структурные логи.

Подробнее: [`nornickel-2026-parser/RU_SUMMARY_ADVANCE_RAG.md`](nornickel-2026-parser/RU_SUMMARY_ADVANCE_RAG.md).

## Связь с итоговым решением Metalcrow

На основании **более ранней версии** этого репозитория (модуль `OKF_PARSER`) в monorepo был выделен и доработан сервис парсинга документов — [`services/nornickel-2026-parser/`](../services/nornickel-2026-parser/). Именно он вошёл в итоговое решение: L1-слой `svc-parse-docling` ходит в него по HTTP, забирает Docling-markdown и пишет в OKF/Postgres.

Каталог `OTHER_REPOS/nornickel-2026-parser/` — архивная копия **актуальной** версии оригинала (включая `ADVANCE_RAG` и расширенный `OKF_PARSER`) для справки и сравнения с интегрированным сервисом.
