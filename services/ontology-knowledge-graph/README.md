# ontology-knowledge-graph — сервис онтологии

Пакет `ontology/` + Dockerfile; рабочая директория для всех команд ниже — эта папка (`services/ontology-knowledge-graph/`).

Самодостаточный пакет: схема знаний + хранилище + конвейер наполнения +
query-слой + интеграционная поверхность (роутер/тул-сервис). Спецификация —
[../../specs/ONTOLOGY_V2.md](../../specs/ONTOLOGY_V2.md).

## Быстрый старт

```bash
# 1. Postgres (dev-контейнер)
docker run -d --name onto_pg -p 56543:5432 \
  -e POSTGRES_PASSWORD=onto -e POSTGRES_DB=onto pgvector/pgvector:pg18
# адрес по умолчанию: postgresql://postgres:onto@localhost:56543/onto
# переопределить: env ONTOLOGY_DB_URL

# 2. Зависимости (лёгкие)
pip install "psycopg[binary]" pydantic rapidfuzz pypdf openai fastapi pytest

# 3. Схема + сиды + демо-данные
python -m ontology.loader ontology/seed/norilsk_pgm.json --reset

# 4. Извлечение из корпуса (rule-based, без LLM)
python -m ontology.extract.run --dir "<папка с docx/pdf>" --limit 25 --model mock --load
#    с LLM (нужны LLM_BASE_URL и LLM_API_KEY в .env, OpenAI-совместимый эндпоинт):  без --model mock

# 5. Проверки
python -m pytest ontology/tests -q          # тесты CQ (в отдельной БД onto_test)
python -m ontology.mocks.agent --demo       # эмуляция chat-контура
uvicorn ontology.tool_service:app --port 8021   # HTTP: /health /manifest /invoke + /api/v1/*
```

## Слои пакета

| Модуль | Назначение |
|---|---|
| `contracts.py` | Схема-контракт (pydantic): классы, предикаты, реестры, Gate. Единственный источник правды |
| `ddl.sql` | Та же схема в Postgres (`experiments.*`); граф = VIEW `edges`, методы = VIEW `experiment_processes` |
| `store.py` | Подключение (psycopg3), `apply_ddl()`, `reset()`; env `ONTOLOGY_DB_URL` |
| `batch.py` | `ExtractionBatch` — формат «экстрактор → загрузчик» (единственный вход в БД) |
| `loader.py` | Идемпотентная загрузка батча (uuid5 из внешних id), сиды реестров, HITL-очередь величин |
| `extract/parse.py` | DOCX/PDF → блоки с локаторами + sha256; чанкинг |
| `extract/llm.py` | LLM-экстракция по строгой JSON-схеме (OpenAI-совместимый эндпоинт) |
| `extract/mock.py` | Правило-основанный экстрактор, тот же интерфейс (dev/базовый уровень) |
| `extract/normalize.py` | Значения-строки → ValueRange; единицы → СИ; процессы → канон |
| `extract/relocate.py` | Verbatim-цитата → точный локатор (rapidfuzz) |
| `extract/run.py` | Раннер: документы → батчи JSON (`ontology/batches/`) → БД |
| `query.py` | Интерпретаторы на SQL (см. таблицу тулов ниже). LLM не используется |
| `router.py` | FastAPI APIRouter — BFF-ручки `/analytics/* /graph/* /evidence /timeline` |
| `tool_service.py` | Тул-сервис: `/health /manifest /invoke` + словарь `TOOLS` для in-process вызова |
| `mocks/agent.py` | Эмуляция chat-агента: интент → тул → structured claims с цитатами |
| `demo.py` | Автономное демо на in-memory наборе (без БД) |

## Как подключать

**Backend (BFF):**
```python
from ontology.router import router as ontology_router
app.include_router(ontology_router, prefix="/api/v1")
```

**Агент (tools):** вариант А — HTTP `/invoke` тул-сервиса; вариант Б — прямые
вызовы (тот же процесс):
```python
from ontology.tool_service import TOOLS
from ontology.store import Store
store = Store.open()
result = TOOLS["find_contradictions"]["fn"](store)
```

**Ingest-воркер (L3 / extract.llm):** очередь вызывает
```python
from ontology.extract.run import extract_document
from ontology.extract.llm import Extractor
from ontology.loader import load_batch, seed_registries
batch = extract_document(Extractor(), Path(doc))   # или MockExtractor() для L2
load_batch(store, batch)
```
Повторная загрузка того же документа не создаёт дублей (детерминированные id).

**Каталоги/справочники (золотой каркас):** собрать `ExtractionBatch` из строк
pandas (`extractor="structured_etl"`) и отдать в `load_batch` — без LLM.

## Тулы (реестр агента)

`evidence` · `evidence_profile` · `find_gaps` · `find_contradictions` ·
`compare_practice` · `compare_technologies` · `find_experts_by_topic` ·
`get_subgraph` · `lineage` · `timeline` · `literature_review` · `coverage` ·
`search_passages`.
Схемы аргументов — `GET /manifest`. Все read-only; запись — только конвейер.

`search_passages(query)` — гибридный ретрив пассажей поверх извлечённых
дословных цитат (выводы + измерения): у каждого пассажа обязательна ссылка на
документ-источник. **Гибрид BM25-семейство (Postgres `ts_rank`) + плотные
эмбеддинги (`pgvector`, косинус), слитые через Reciprocal Rank Fusion** —
лексика даёт точность по терминам, плотный поиск находит нужный пассаж по
смыслу (важно для чисел в таблицах, которые лексикой не поднять). Пассажи с
числами и измерения ранжируются выше. Ретрив-фолбэк для открытых вопросов «какие
методы/способы/технические решения» и когда типовой `evidence`/`evidence_profile`
не находит одного числа. Если большинство значимых терминов вопроса отсутствует
в корпусе — возвращает пусто (честное «данных нет», без ложного ретрива).

Индекс пассажей — `experiments.passage_index` (текст + документ + вектор 768).
Собирается `python -m ontology.hybrid_index --rebuild --embed`; при старте
сервиса — в фоне (эмбеддинги считаются `fastembed`/ONNX, модель запечена в образ;
healthcheck не ждёт — до готовности ретрив лексический). Пересобирать после
до-ингеста документов.

Маршрутизация `/api/v1/ask`: интент выбирается LLM-классификатором
(`LLM_INTENT_MODEL`, по умолчанию Gpt-oss-120b) над реестром тулов с извлечением
слотов; при недоступности LLM — keyword-фолбэк. Отключение: `ONTOLOGY_LLM_INTENT=0`.

Модель расхождений: `evidence_profile` — основной ответ («пространство решений»:
конверт значений, медиана, точки с надёжностью источников); `find_contradictions`
выдаёт «зоны расхождения» с `severity` — крайние случаи профиля, а не вердикт
«кто-то неправ». Надёжность точки = тип источника × уверенность экстракции ×
свежесть. Оба инструмента агрегируют только сопоставимые точки (Gate).

## Запуск как сервис (docker, зеркало science-knowledge-graph)

Онтология — internal-only sidecar `ontology-knowledge-graph` в общем compose:
собственный образ ([Dockerfile](Dockerfile)), собственная база `ontology` в
общем инстансе `db` (создаётся сама при старте: [service_init.py](service_init.py)
— БД → DDL → сиды → автозагрузка seed/батчей → канонизация), healthcheck
`/api/v1/health`. Backend обращается по HTTP через
`backend/app/services/ontology_client.py` (`ONTOLOGY_KG_URL`); недоступность
сервиса деградирует до пустого результата, не валит chat/graph.

```bash
docker compose up -d ontology-knowledge-graph   # в составе стека
# или standalone против любого Postgres:
docker build -t onto-kg .
docker run -d -p 8021:8000 -e ONTOLOGY_DB_URL=postgresql://... onto-kg
curl http://localhost:8021/api/v1/health
curl -X POST http://localhost:8021/invoke -H 'Content-Type: application/json' \
  -d '{"tool":"evidence","args":{"process":"хлорирование","quantity_kind":"извлечение"}}'
```

## Совместимость с основным контуром (main)

Основной backend ведёт свою схему `experiments.*` (Alembic, SPEC_V3-модель:
documents/materials/results/labs/…). Схема онтологии — параллельная реализация
с теми же именами таблиц, но расширенными колонками. **Не разворачивать обе в
одной БД**: онтология живёт в своей БД (env `ONTOLOGY_DB_URL`), синтез — через
мосты:

- **Вход из ingest-контура** (`services/parse-docling`, MinIO, OKF):
  `python -m ontology.ingest_bridge --okf-root <папка raw .md>
  [--source-db postgresql://…] [--okf-prefix …] [--doc-workers N]` — читает их
  реестр документов read-only, экстрагирует из OKF markdown, пишет в БД
  онтологии. Повторный запуск идемпотентен.
- **Выход в graph-контур** (science-kg / Neo4j): узлы и рёбра — из VIEW
  `experiments.edges` (`get_subgraph` отдаёт готовый {nodes, edges}).
- Колонки `documents.processing_level/okf_raw_path` совпадают по имени и
  смыслу с моделью backend'а — метаданные переносимы 1-в-1.

### Ингест из общего SHARED и восстановление базы (по образцу science-kg)

Данные онтологии обрабатываются той же схемой, что данные KG в общем пайплайне:
не live-пересчёт на каждый старт, а batch-ингест по общему корпусу + загрузка
предпосчитанного на деплое.

- **Build из SHARED парсера** (зеркало `science-knowledge-graph/scripts/ingest_shared_corpus.py`):
  `python -m ontology.ingest_shared [--limit N] [--doc-workers N]` — внутри
  контейнера на сети `metalcrow-net`. Обходит дерево парсера
  (`/files/tree` под RAW_DATA/UPLOAD_DATA), тянет OKF markdown (`/markdown`),
  извлекает факты, грузит в БД. Сырой путь SHARED пишется в `okf_raw_path`
  каждого факта → фронт строит wiki-диплинк `/wiki?doc=<okf_raw_path>` (тот же
  ключ, что у online-L1 `documents.okf_raw_path`).
- **Провенанс → wiki.** `okf_raw_path` протянут насквозь (batch → `documents` →
  `prov` каждого факта → `Provenance.okf_raw_path`) и возвращается в цитатах
  `evidence`/`profile`/`review` без join'ов.
- **Восстановление на деплое.** `service_init` при пустой БД автозагружает
  `seed/` + `ontology/batches/okf-*.json` (запечены в образ). Явный
  идемпотентный (пере)load в уже поднятый стек — `./scripts/load-ontology-batches.sh
  [--prod]` (зеркало `scripts/load-precomputed-facts.sh`), под капотом
  `python -m ontology.loader --dir ontology/batches`.
- **Пополнение.** Догруз новых документов — повторный `ingest_shared`/`ingest_bridge`
  (idempotent) либо коммит новых `okf-*.json` + `load-ontology-batches.sh`.

## Правила, которые держат качество

1. Факт без дословной цитаты (`snippet`) отбраковывается — валидатор + CHECK в БД.
2. Координаты у LLM не спрашиваем: цитата → `relocate` → локатор.
3. Сравнения (противоречия, gap-map) — только через Comparability Gate;
   пары внутри одного документа не считаются противоречием.
4. LLM не пишет SQL и не финализирует числа; в query-слое LLM нет вообще.
5. Неизвестная величина → авторегистрация со `status='needs_review'` (HITL),
   а не потеря и не мусор в каноне.
6. «Измерение» без числового значения — не измерение: отбраковывается
   (качественные наблюдения живут отдельным видом `qualitative_observation`).

## Стандартизация величин

Свободные имена свойств из экстракции канонизируются слоями
(`extract/quantities.py`): мусор LLM-схемы → отбраковка; качественные
наблюдения → `qualitative_observation`; точное совпадение с реестром (RU/EN);
паттерны «вид + предмет» («извлечение никеля» → `recovery_degree` +
`conditions.subject='никеля'` — subject становится осью Gate); подсказки по
единице; остаток — одним LLM-вызовом по строгой схеме. Миграция уже
загруженной БД: `python -m ontology.extract.quantities --apply [--llm]`.
В конвейере канонизация происходит автоматически (run.py, loader).

## Состояние данных (dev)

Загружено: 3 документа seed (аффинаж ПГМ/Cu, вручную выверенные цитаты) +
25 статей корпуса через mock-экстрактор. Свежий прогон с LLM: задать `LLM_BASE_URL`/`LLM_API_KEY`
в `.env` и выполнить шаг 4 без `--model mock`.
