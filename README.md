# MetalCrow · научный клубок

**Граф знаний R&D для горно-металлургической отрасли** — поисково-аналитическая система, которая связывает статьи, эксперименты, технологии, материалы, режимы, установки и исследовательские команды в единую карту знаний и отвечает на вопросы со ссылками на источники.

Решение для кейса **«Научный клубок»** на [NorNickel AI Science Hack 2026](https://nornickel-ai-hackathon.ru/task-2).

---

## Команда MetalCrow

| Участник | GitHub |
|----------|--------|
| Константин Ушенин | [@KonstantinUshenin](https://github.com/KonstantinUshenin) |
| Артём Голубев | [@arqoofficial](https://github.com/arqoofficial) |
| Станислав Малашкеевич | [@SuperStas22](https://github.com/SuperStas22) |
| Степан Остарков | [@OstarkovSN](https://github.com/OstarkovSN) |
| Всеволод Каримов | [@Hamyrappy](https://github.com/Hamyrappy) |

---

## О проекте

Исследователи Норникеля работают с огромным массивом неструктурированных данных: внутренние отчёты, протоколы экспериментов, обзоры, журналы «Цветные металлы», справочники материалов и оборудования. Эти знания разрознены — лежат в PDF, DOCX и таблицах, не связаны между собой.

Чтобы ответить на вопрос *«что уже делали по процессу X при режиме Y — и какой был эффект на показатель Z?»*, исследователь вынужден вручную перебирать десятки документов, полагаться на память коллег и не видит пробелов в экспериментальном покрытии.

**MetalCrow** решает эту задачу:

- **Ingestion-пайплайн** — PDF и сканы проходят Docling + OCR, многоуровневую обработку (L1→L3) и попадают в OKF-репозиторий и Postgres
- **Граф знаний** — Neo4j хранит сущности (материалы, процессы, оборудование, эксперты) и связи между ними с провенансом
- **GraphRAG-агент** — отвечает на вопросы на естественном языке, достаёт подграф и фрагменты корпуса, собирает ответ с цитатами и меткой достоверности
- **LitSearch** — если ответа нет во внутреннем корпусе, агент выходит в мировую литературу (OpenAlex, КиберЛенинка), скачивает статьи и добавляет их в граф
- **Аналитика** — покрытие корпуса, противоречия между источниками, сравнение отечественной и зарубежной практики, пробелы в данных

Система отвечает на сложные многопараметрические запросы — материал + процесс + условия + география + числовые диапазоны — и показывает, на чём держится каждое утверждение.

---

## Кейс

**Трек:** «Научный клубок» · **Хакатон:** [NorNickel AI Science Hack 2026](https://nornickel-ai-hackathon.ru/)

**Задача:** создать knowledge graph или поисково-аналитическую систему, которая связывает статьи, эксперименты, материалы, свойства, режимы, установки, исследовательские команды и выводы. Главное — чтобы система отвечала на вопросы вида *«что уже делали по сплавам X при режиме Y и какой был эффект на свойство Z»*, показывала связанные сущности, историю решений и пробелы в данных.

**Корпус:** анонимизированные внутренние отчёты и статьи, каталог экспериментов, справочники материалов и оборудования, перечень сотрудников/лабораторий, таксономия тематик. [Данные кейса](https://disk.yandex.ru/d/npigiuw4Rbe9Pg).

**Контрольные запросы**, на которые система должна отвечать:

1. Методы обессоливания воды при сульфатах/хлоридах 200–300 мг/л и сухом остатке ≤1000 мг/дм³
2. Циркуляция католита при электроэкстракции никеля — мировая практика и оптимальная скорость потока
3. Распределение Au, Ag и МПГ между штейном и шлаком за последние 5 лет
4. Закачка шахтных вод в глубокие горизонты — РФ vs мир, технико-экономические показатели

Подробнее о формулировке задачи: [`docs/TASK_EXPLANATION.md`](docs/TASK_EXPLANATION.md), [`docs/CASE.md`](docs/CASE.md).

---

## Архитектура

Monorepo на базе [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template), расширенный до микросервисной архитектуры:

```
Пользователь → nginx (frontend) → FastAPI backend (auth, chat, BFF)
                                        ↓
              ┌─────────────────────────┼─────────────────────────┐
              │                         │                         │
         Ingestion plane          Tool plane              Retrieval plane
    Docling → Clean → spaCy → LLM   search, graph,      GraphRAG + hybrid
              ↓                     analytics, wiki      search (BM25+vector)
         Neo4j + Postgres + MinIO + Redis
```

Traefik не используется — фронтенд (nginx) сам проксирует `/api`, `/docs`, `/redoc` на бэкенд внутри docker-сети. Наружу торчит **один порт**.

**Стек:** FastAPI · React · Neo4j · Postgres + pgvector · Redis · Celery · MinIO · Docling OCR · Docker Compose

Техническая спецификация: [`specs/SPEC_V5.md`](specs/SPEC_V5.md).

---

## Быстрый старт

```bash
cp .env.example .env          # один раз
make up                       # парсер (CPU) + metalcrow
```

Первый запуск скачает модели Docling/OCR (~10–20 мин). Дальше `make up` поднимает всё за пару минут.

| Команда | Что делает |
|---------|------------|
| `make up` | Локально: парсер (CPU) + metalcrow |
| `make up-gpu` | То же, парсер на CUDA |
| `make up-prod` | Сервер: наружу только фронт `:80` |
| `make up-no-parser` | Только metalcrow (L1 — stub) |
| `make down` | Остановить оба стека |

**Локально после старта:**

| Сервис | URL |
|--------|-----|
| Frontend | http://localhost:5173 |
| Backend API docs | http://localhost:8000/docs |
| Adminer | http://localhost:8080 |
| Parser API | http://localhost:8114/health |

**На сервере (хакатон):**

```bash
cp .env.example .env   # смените SECRET_KEY, POSTGRES_PASSWORD, FIRST_SUPERUSER_PASSWORD
make up-prod
```

→ `http://<ip-сервера>` — `VITE_API_URL` трогать не нужно.

---

## Документация

| Документ | Содержание |
|----------|------------|
| **[SETUP.md](SETUP.md)** | **Основное руководство:** требования, пошаговый запуск, compose-файлы, предзагрузка моделей и данных, Neo4j ingest, скрипты, типичные проблемы |
| [`docs/TASK_EXPLANATION.md`](docs/TASK_EXPLANATION.md) | Полная формулировка кейса от организаторов |
| [`specs/SPEC_V5.md`](specs/SPEC_V5.md) | Итоговая техническая спецификация |
| [`services/science-knowledge-graph/README.md`](services/science-knowledge-graph/README.md) | GraphRAG и Neo4j ingest |
| [`services/nornickel-2026-parser/README.md`](services/nornickel-2026-parser/README.md) | Парсер документов (Docling pipeline) |

---

## Лендинг

Презентационная страница проекта для жюри и участников хакатона:

**→ [arqoofficial.github.io/metalcrow-nn](https://arqoofficial.github.io/metalcrow-nn/)**

Лендинг показывает:

- **Hero** — суть продукта и примеры контрольных вопросов к агенту
- **LitSearch** — поиск по мировой литературе (OpenAlex + КиберЛенинка) с автоматическим скачиванием статей
- **Возможности** — чат-агент, гибридный поиск, граф знаний, wiki, загрузка PDF
- **Как устроено** — пайплайн от Docling/OCR до GraphRAG-ответа с провенансом
- **Достоверность** — метки уверенности, противоречия между источниками, автоматический бенчмарк

Исходники лендинга: [`docs/index.html`](docs/index.html) (GitHub Pages) и [`frontend/public/landing/`](frontend/public/landing/) (доступен в приложении по `/landing`).

---

## Структура репозитория

```
metalcrow/
├── backend/              # FastAPI-оркестратор: auth, chat, BFF, ingest API
├── frontend/             # React UI + nginx proxy
├── services/             # Микросервисы: parse-docling, science-knowledge-graph, …
├── packages/tool_sdk/    # Общий SDK для tool-сервисов
├── scripts/              # dev-up.sh, dev-down.sh, fetch-shared-yandex.sh
├── compose.yml           # Базовый Docker Compose
├── compose.override.yml  # Локальная разработка (порты, live-reload)
├── compose.prod.yml      # Прод: наружу только :80
└── SETUP.md              # Подробное руководство по запуску
```

---

## Лицензия и ссылки

- Исходный код: этот репозиторий
- Публичный репозиторий для сдачи: [github.com/arqoofficial/metalcrow-nn](https://github.com/arqoofficial/metalcrow-nn)
- Хакатон: [nornickel-ai-hackathon.ru](https://nornickel-ai-hackathon.ru/)
