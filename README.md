# metalcrow

## Быстрый старт

```bash
cp .env.example .env          # один раз
make up                       # парсер (CPU) + metalcrow
```

Первый запуск скачает модели Docling/OCR в `services/nornickel-2026-parser/SHARED/MODELS` — это может занять 10–20 минут. Дальше `make up` поднимает всё за пару минут.

| Команда | Что делает |
|---------|------------|
| `make up` | Локально: парсер (CPU) + metalcrow |
| `make up-gpu` | То же, парсер на CUDA |
| `make up-prod` | Сервер: парсер + metalcrow, наружу только фронт `:80` |
| `make up-no-parser` | Только metalcrow (L1 — stub, без Docling) |
| `make up-fast` | Без проверки/скачивания моделей |
| `make parser-models` | Только предзагрузка моделей |
| `make down` | Остановить оба стека |

Эквивалент без Make: `./scripts/dev-up.sh`, `./scripts/dev-down.sh` (флаги `--gpu`, `--prod`, `--no-parser`).

### Сборка образов (без старта)

Python-сервисы собираются через `uv sync` с BuildKit-кэшем (зависимости не перекачиваются при каждом билде). Скрипты `make up` / `make build` включают BuildKit автоматически.

Пересобрать образы отдельно от запуска — удобно перед `make up-fast`:

| Команда | Что делает |
|---------|------------|
| `make build` | Образы парсера (CPU) + metalcrow |
| `make build-gpu` | Парсер CUDA + metalcrow |
| `make build-prod` | metalcrow prod overlay + парсер CPU |
| `make build-no-parser` | Только metalcrow |
| `make build-parser` | Только парсер (CPU) |
| `make build-parser-gpu` | Только парсер (CUDA) |

```bash
make build      # пересборка
make up-fast    # поднять без повторной сборки и без скачивания моделей
```

Эквивалент: `./scripts/dev-build.sh` (флаги `--gpu`, `--prod`, `--no-parser`, `--parser-only`).

### Сервисы (локально)

- Frontend: http://localhost:5173 (сюда же `/api/*`, `/docs`, `/redoc`)
- Backend: http://localhost:8000/docs
- Adminer: http://localhost:8080
- Parser API: http://localhost:8114/health

Остановить с удалением данных БД: `make down` с аргументом `-v` → `./scripts/dev-down.sh -v`.

## Запуск вручную (без make)

Если нужен только metalcrow без парсера (stub L1):

```bash
cp .env.example .env
docker compose up -d --build
```

`docker compose` без `-f` мёржит `compose.yml` + `compose.override.yml` — порты наружу и live-reload бэкенда.

## Запуск на сервере (хакатон)

```bash
cp .env.example .env   # смените SECRET_KEY, POSTGRES_PASSWORD, FIRST_SUPERUSER_PASSWORD
make up-prod
```

Наружу публикуется **только фронтенд** на порту `80` — в браузере достаточно `http://<ip-сервера>`. `VITE_API_URL` трогать не нужно.

## Ingest SHARED → knowledge graph

Разовая загрузка корпуса парсера (`SHARED/RAW_DATA` + `UPLOAD_DATA`) в Neo4j через
`science-knowledge-graph`. Скрипт: `services/science-knowledge-graph/scripts/ingest_shared_corpus.py`.

Берёт только файлы с готовым OKF-markdown (стадия `00_docling_raw`); сырой PDF без
Docling пропускается. Прогресс resumable — `scripts/.ingest_shared_progress.json` внутри
контейнера. Парсер должен быть доступен как `http://parser-main:8114` (сеть `metalcrow-net`).

Проверить coverage OKF (локально, если парсер слушает `:8114`):

```bash
curl -s http://localhost:8114/api/v1/statistics | python3 -m json.tool
# поле stage0_done — сколько файлов реально ingestable
```

### Локально

```bash
make up   # парсер + metalcrow

docker compose build science-knowledge-graph
docker compose up -d science-knowledge-graph

# smoke test
docker compose exec science-knowledge-graph \
  uv run python scripts/ingest_shared_corpus.py --limit 10

# полный прогон
docker compose exec science-knowledge-graph \
  uv run python scripts/ingest_shared_corpus.py
```

### На сервере

Из корня репозитория (тот же каталог, где `make up-prod`). Compose-файлы как при prod-деплое:

```bash
cd /path/to/metalcrow
git pull   # если обновляли скрипт

docker compose -f compose.yml -f compose.prod.yml build science-knowledge-graph
docker compose -f compose.yml -f compose.prod.yml up -d science-knowledge-graph

# опционально: парсер жив
docker compose -f compose.yml -f compose.prod.yml exec science-knowledge-graph \
  curl -sf http://parser-main:8114/health

docker compose -f compose.yml -f compose.prod.yml exec science-knowledge-graph \
  uv run python scripts/ingest_shared_corpus.py
```

Долгий прогон — в `tmux`/`screen` (SSH может оборваться). Ожидаемо: ~2700 raw paths в tree,
~300 с OKF попадут в граф, остальные — `skipped (no OKF output yet)`.

Подробнее: [services/science-knowledge-graph/README.md](services/science-knowledge-graph/README.md).

## Парсер документов (nornickel-2026-parser)

L1-слой (`svc-parse-docling`) ходит по HTTP в автономный стек парсера
(`services/nornickel-2026-parser`, API `:8114`), забирает сырой Docling-markdown
(стадия `docling_raw`) и пишет в OKF/Postgres. Если парсер недоступен — stub-fallback.

Связь через external-сеть `metalcrow-net` (alias `parser-main`), см.
[services/nornickel-parser.override.yml](services/nornickel-parser.override.yml).
`make up` создаёт сеть, копирует `.env` и предзагружает модели автоматически.

Ручной запуск (если нужен контроль):

```bash
docker network create metalcrow-net
docker compose -f services/nornickel-2026-parser/docker-compose.yml \
               -f services/nornickel-parser.override.yml up -d --build
docker compose up -d --build
```

GPU: добавьте `-f services/nornickel-2026-parser/docker-compose.gpu.yml` к команде парсера или используйте `make up-gpu`.

## Структура compose-файлов

- `compose.yml` — база: сборка из исходников, `restart: always`, без единого опубликованного порта (сервисы видны только друг другу внутри сети).
- `compose.override.yml` — подключается автоматически при локальном `docker compose up` (без `-f`): открывает порты db/adminer/backend/frontend, включает live-reload бэкенда.
- `compose.prod.yml` — явный оверлей для сервера (`-f compose.yml -f compose.prod.yml`): публикует наружу только порт `80` фронтенда.
