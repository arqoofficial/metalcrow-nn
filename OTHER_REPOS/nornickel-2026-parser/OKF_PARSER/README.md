# NorNickel 2026 Parser

Пайплайн парсинга документов: API (`main`) + воркеры Docling/OCR через Redis.

## Где запускаются модели

Отдельного URL у inference **нет** — модель работает в фоновых воркерах без HTTP-порта.

| Что | Адрес |
|-----|-------|
| API (точка входа) | http://localhost:8114 |
| Swagger / OpenAPI | http://localhost:8114/docs |
| Health | http://localhost:8114/health |

Запуск обработки (модель стартует **асинхронно** в воркерах):

- `POST http://localhost:8114/api/v1/files/upload` — загрузить файл
- `POST http://localhost:8114/api/v1/files/process` — поставить задачу в очередь (Docling/OCR)
- `GET http://localhost:8114/api/v1/files/status?path=...` — статус пайплайна

`main` только принимает HTTP и кладёт задачи в Redis; Docling/EasyOCR выполняются в контейнерах `raw2docling_raw` и `docling_raw2docling_clean00`. Кеш моделей: `./SHARED/MODELS` → `/models` в воркере.

## Пайплайн работы

Система **file-first**: источник правды — файлы в `SHARED/`, Redis только транспортирует задачи между стадиями. Отдельной БД метаданных нет.

### Стадии

| Стадия | ID | Воркер | Вход | Выход |
|--------|----|--------|------|-------|
| 0 | `docling_raw` | `raw2docling_raw` | сырой файл (PDF, DOCX, …) | `SHARED/00_docling_raw/<путь>.md` |
| 1 | `docling_clean00` | `docling_raw2docling_clean00` | OKF markdown стадии 0 | `SHARED/01_docling_clean00/<путь>.md` |

Стадия 0 конвертирует документ через **Docling** (OCR для сканов, `en` + `ru`). Стадия 1 — быстрая очистка тела markdown и обновление YAML-frontmatter. Успешное завершение стадии 0 автоматически ставит задачу в очередь стадии 1.

Архивы (`zip`, `rar`, `tar`, …) сохраняются на диск, но **не** проходят через Docling.

### Поток данных

```
Клиент
  │  POST /files/upload
  ▼
main ──► SHARED/UPLOAD_DATA/reports/q1.pdf
  │       (повторная загрузка → q1__v02.pdf, q1__v03.pdf, …)
  │  POST /files/process
  ▼
Redis: parser:jobs:raw2docling_raw
  │
  ▼
raw2docling_raw ──► SHARED/00_docling_raw/UPLOAD_DATA/reports/q1.pdf.md
  │
  ▼
Redis: parser:jobs:docling_raw2docling_clean00
  │
  ▼
docling_raw2docling_clean00 ──► SHARED/01_docling_clean00/UPLOAD_DATA/reports/q1.pdf.md
  │
  │  GET /files/status, GET /markdown
  ▼
Клиент читает результат из NFS
```

Помимо загрузок, сырые файлы могут лежать в `SHARED/RAW_DATA/` (bootstrap-данные, управляются вручную).

### Типичный сценарий

```bash
# 1. Загрузить PDF по логическому пути
curl -F "file=@report.pdf" -F "path=reports/q1.pdf" \
  http://localhost:8114/api/v1/files/upload

# 2. Поставить в очередь (логический путь → UPLOAD_DATA, затем RAW_DATA)
curl -X POST http://localhost:8114/api/v1/files/process \
  -H "Content-Type: application/json" \
  -d '{"path": "reports/q1.pdf"}'

# 3. Проверить статус (polling)
curl "http://localhost:8114/api/v1/files/status?path=reports/q1.pdf"

# 4. Скачать markdown (стадия 0 или явный OKF-путь)
curl "http://localhost:8114/api/v1/markdown?okf_path=reports/q1.pdf"
```

Дополнительно:

- `GET /api/v1/statistics` — покрытие пайплайна по всем raw-файлам
- `GET /api/v1/files/tree` — дерево каталогов в `SHARED/`
- `POST /api/v1/reindex` — массовая постановка необработанных файлов в очередь

### Статусы

Каждая стадия и `overall_status` принимают значения:

| Статус | Значение |
|--------|----------|
| `pending` | выход ещё не создан, задача не в очереди |
| `queued` | задача в Redis |
| `processing` | воркер держит lock-файл |
| `done` | OKF-файл на диске |
| `failed` | маркер ошибки в `SHARED/.pipeline_errors/<stage>/` |

`overall_status` — «худшее» состояние среди стадий (`failed` < `processing` < `queued` < `pending` < `done`).

### Версионирование и `enforce`

- **Первая загрузка** по логическому пути `reports/q1.pdf` → `UPLOAD_DATA/reports/q1.pdf`.
- **Повторная загрузка** того же ключа → следующий суффикс `__vNN`: `q1__v02.pdf`, `q1__v03.pdf`, …
- Логический путь без версии ищет **точное** совпадение в `UPLOAD_DATA/`, затем в `RAW_DATA/`; fallback на «последнюю версию» нет.
- `POST /files/process` с `enforce=false` (по умолчанию) вернёт **409**, если выход стадии 0 уже есть; для переработки передайте `"enforce": true`.

Подробнее: [docs/SPECIFICATION.md](docs/SPECIFICATION.md), [docs/LAYER_SERVICES.md](docs/LAYER_SERVICES.md).

## Требования

- Docker и Docker Compose
- Для GPU: [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html), `nvidia-smi` на хосте

## Быстрый старт (CPU, прод по умолчанию)

Образ **~600 MB** — torch CPU-only, без CUDA-библиотек.

```bash
cp .env.example .env   # при необходимости отредактировать
docker compose up -d --build
```

API: http://localhost:8114

### Предзагрузка моделей (обязательно для воркеров)

Модели **не входят в образ** — лежат в `./SHARED/MODELS` и монтируются volume'ом.

```bash
mkdir -p SHARED/MODELS
./rerun.sh preload-models
```

Проверка кеша без скачивания:

```bash
docker compose run --rm raw2docling_raw python scripts/preload_models.py --check-only
```

## Запуск с GPU

CPU-only образ **не умеет CUDA** — для GPU нужен отдельный образ (~9 GB) через `docker-compose.gpu.yml`.

Проверка GPU на хосте:

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

Сборка и запуск:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml build
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

Переменные (`.env` или shell):

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DOC_OCR_USE_GPU` | `auto` | `auto` — GPU если CUDA доступна, иначе CPU; `true` — только GPU; `false` — только CPU |
| `DOCKER_GPU_COUNT` | `1` | Сколько GPU резервировать на воркер (в `docker-compose.gpu.yml`) |

Пример — принудительно GPU:

```bash
DOC_OCR_USE_GPU=true docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

На CPU-кластере оставьте обычный `docker compose up` — `DOC_OCR_USE_GPU=auto` автоматически работает на CPU.

## Образы

| Тег | Сборка | Размер | Назначение |
|-----|--------|--------|------------|
| `nornickel-2026-parser:local` | `docker compose build` | ~600 MB | Прод на CPU |
| `nornickel-2026-parser:local-gpu` | `docker compose -f docker-compose.yml -f docker-compose.gpu.yml build` | ~9 GB | Воркеры с CUDA |

Ручная сборка:

```bash
docker build -t nornickel-2026-parser:local --build-arg TORCH_VARIANT=cpu .
docker build -t nornickel-2026-parser:local-gpu --build-arg TORCH_VARIANT=gpu .
```

## Полезные команды

```bash
# Перезапуск сервисов
./rerun.sh

# Реиндекс после обновления
./reindex.sh

# Observability (Prometheus, Grafana, OTel)
docker compose --profile observability up -d

# Локальная разработка без Docker
uv sync
uv run uvicorn service.main.main:app --host 0.0.0.0 --port 8114
```

## Lock-файлы зависимостей

- `uv.lock` — CPU torch (`download.pytorch.org/whl/cpu`)
- `uv.lock.gpu` — CUDA torch (PyPI, с `nvidia-*`)

После изменения `pyproject.toml`:

```bash
./scripts/sync_lockfiles.sh
```

## Структура volumes

| Хост | Контейнер | Назначение |
|------|-----------|------------|
| `./SHARED` | `/mnt/nfs/SHARED` | Данные пайплайна (upload, raw, docling) |
| `./SHARED/MODELS` | `/models` | Кеш Docling / EasyOCR / HuggingFace |

Подробнее: [docs/DOCLING.md](docs/DOCLING.md), [docs/LAYER_INFRASTRUCTURE.md](docs/LAYER_INFRASTRUCTURE.md).
