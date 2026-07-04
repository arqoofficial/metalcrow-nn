# Онтология «Научного клубка» — техническая спецификация

> **Статус:** v2.1
> **Что это:** единый контракт данных системы. Задаёт, какими сущностями и связями
> описывается знание, как их хранить, чем наполнять и как запрашивать.
> **Код = источник правды:** [`ontology/contracts.py`](../ontology/contracts.py) —
> pydantic-схема (её импортируют экстракторы, ETL, API, фронтенд);
> [`ontology/ddl.sql`](../ontology/ddl.sql) — та же схема в Postgres;
> [`ontology/demo.py`](../ontology/demo.py) — исполняемый скелет.
> **Запуск демо:** `python -m ontology.demo` из корня репозитория (зависимость — pydantic).
>
> Если правишь схему — правь `contracts.py` и синхронно этот файл.

---

## 1. Назначение

**Онтология ≠ база фактов. Онтология — это договор о языке:** список типов объектов
(материал, эксперимент, измерение, метод…) и типов связей между ними. Конкретные факты
(«извлечение = 95–97 %») живут отдельно и обязаны подчиняться договору.

Формально: **TBox** (схема — классы, предикаты, правила; `contracts.py`) отделён от
**ABox** (данные — строки в Postgres). Аналогия: `CREATE TABLE` против строк в таблице.

Онтология — семантический слой над таблицами `experiments.*`, а не вторая база.
`contracts.py` — единая точка правды; модули обмениваются данными только в терминах этой
схемы, через её pydantic-типы и FastAPI-эндпоинты. Всё остальное — проекции.

```
             ontology/contracts.py  ── единый контракт (pydantic == таблицы == промпты) ──┐
                     │                                                                     │
   наполнение ───────┼─────────────────────────────┐                                      │
   каталоги/pandas   документы word/pdf → NuExtract  вычисляемое (similar, gap, lineage)   │
        │                    │                             │                               │
        └──── валидация по контракту (факт без цитаты отбраковывается) ──── HITL-очередь ───┤
                     │                                                                      │
              PostgreSQL (канон: нормализованные таблицы experiments.*)                     │
                     │                                                                      │
        edges = VIEW (граф-проекция, не вторая БД)   +   pgvector (семантика)               │
                     │                                                                      │
   использование ────┼──────────────────────────────────────────────────────────── импорт ┘
   инструменты агента (hybrid_search, gate_check, gap_map, lineage, generate_hypothesis)
                     │
   API (/search /graph /analytics /chat) → фронтенд (Evidence-карточка, heatmap, граф)
```

---

## 2. Модель данных (TBox)

### 2.1 Классы (типы узлов)

Каждый класс — таблица в Postgres; общее у всех — `id`, `provenance` (обязателен),
доменные поля.

| Класс | Роль | Ключевые поля |
|---|---|---|
| **Experiment** | узел-хаб: связывает всё | `id, date, origin{catalog\|extracted}, regime, equipment_id, team_id, site, document_id, tags` |
| **Material** | вещество: марка/сплав ИЛИ интермедиат (концентрат, раствор, штейн, реагент) | `canonical_id, pref_label, family, grade, composition, phase, external_ids` |
| **Regime** | режим/метод обработки, многостадийный | `steps[]{process_type, temperature(K), duration_s, pressure_pa, atmosphere, extra}` |
| **Measurement** | реифицированный результат (значение + весь контекст) | `experiment_id, scope, material_id, quantity_kind, value, unit, scale, basis, uncertainty, conditions, sample_state, method, superseded_by` |
| **Conclusion** | вывод/рекомендация + структурный эффект | `text, kind{finding\|recommendation}, effect, superseded_by` |
| **Equipment** | установка/оборудование | `name, equipment_type, lab_id` |
| **TeamLab** | лаборатория/сотрудник/команда — носитель географии и экспертизы | `name, kind{lab\|person\|team}, parent_id, country, city, expertise[]` |
| **Document** | источник (статья/отчёт/патент/справочник) | `doc_id, title, doc_type, year, country, lang, source_path` |
| **Topic** | тема/тег с иерархией | `label, parent_id` |
| **Process** | *метод как адресуемый объект* (открытый реестр, не класс-таблица узлов) | `process_types(name, aliases[], description)` |
| **Provenance** | паспорт происхождения — обязателен на каждом узле и ребре | `doc_id, locator_kind, locator, snippet, extractor, confidence, artifact_sha256, ingested_at` |

**Почему Experiment — хаб.** Центральный вопрос («материал X при режиме Y → эффект на Z»)
ложится на «достать эксперименты, где material≈X и regime≈Y, показать их measurements по
Z» — один JOIN и «надёжность через подсчёт» (сколько независимых экспериментов подтверждают
эффект).

**Почему Measurement — отдельный узел, а не число на ребре.** Результат — пакет: значение
± погрешность, единица, шкала, базис, метод, условия измерения, *на каком именно материале*
и цитата. В эксперименте с несколькими материалами каждое измерение обязано указывать свой
`material_id` (`scope='material'`) — иначе неизвестно, чьё это значение. Технико-экономические
показатели характеризуют передел, а не образец → `scope='experiment'`, `material_id` пуст
(гарантируется CHECK-ом).

### 2.2 Предикаты (закрытый словарь, 15)

**Закрытый** = типов связей ровно 15, новый добавляется только явным изменением схемы, не в
рантайме. Это дисциплина: экстрактор заполняет готовые слоты, а не изобретает язык (иначе в
корпусе «влияет_на» = «воздействует» = «изменяет» станут тремя несводимыми связями). Это же
делает поведение предсказуемым для SQL и UI.

| Предикат | src → dst | Смысл |
|---|---|---|
| `uses_material` | Experiment → Material | + квалификатор `role` (см. 2.3) |
| `applies_process` | Experiment → Process | метод/режим эксперимента |
| `measured_on` | Measurement → Experiment | только на Experiment |
| `has_property` | Measurement → Property (quantity_kind) | что за величина |
| `run_on_equipment` | Experiment → Equipment | на чём |
| `performed_by` | Experiment → TeamLab | кто, где (география) |
| `reported_in` | Experiment \| Conclusion → Document | источник |
| `concludes` | Experiment → Conclusion | вывод |
| `tagged_as` | Experiment \| Document → Topic | тема |
| `derived_from` | Material→Material \| Experiment→Experiment | lineage; + `process` на стрелке |
| `supports` | Conclusion → Conclusion \| TeamLab | подтверждение / верификация |
| `contradicts` | Conclusion → Conclusion | **только через Comparability Gate** |
| `refines` | Conclusion → Conclusion | уточнение (версия знания) |
| `similar_conditions` | Experiment → Experiment | вычисляемое (condition-vector KNN) |
| `canonical_for` | Entity → Entity | ER: `skos:exactMatch` (не `owl:sameAs`) |

### 2.3 Квалификаторы рёбер

N-арность выражается атрибутами на ребре, а не новыми классами:

- **`role` на `uses_material`**: `sample` (образец) · `input` (вход/шихта/концентрат) ·
  `output` (продукт/штейн/шлак — заменяет отдельный предикат `produces`) · `medium`
  (среда/электролит) · `flux` (флюс/реагент) · `atmosphere` (дутьё) · `reference` (эталон).
  Так одна схема выражает «Pd — образец, электролит — среда» и «шихта + флюс + дутьё →
  штейн + шлак + газ».
- **`process` на `derived_from`**: каким переделом получено. Из этого собирается lineage с
  подписями на стрелках.

### 2.4 Открытые реестры: роды величин и процессы

Закрыт **только** словарь предикатов. Два справочника — **открытые**, потому что состав
корпуса заранее неизвестен и приносит величины/процессы, которых нет в исходном перечне:

- **`quantity_kinds(name, unit_dim, aliases[])`** — роды измеряемых величин. Seed покрывает
  металловедение (yield_strength, hardness, grain_size…), гидрометаллургию/аффинаж
  (recovery_degree, element_content, electrode_potential), водный домен (dry_residue/TDS,
  salinity, flow_rate, pH), ТЭП (energy_consumption, specific_cost, capex, opex,
  current_efficiency). Неизвестная величина авторегистрируется как черновик и попадает в
  **очередь на подтверждение человеком (HITL)**, а не уезжает в OTHER (что ослепило бы
  Comparability Gate).
- **`process_types(name, aliases[], description)`** — методы/техрешения. Seed: пирометаллургия
  (smelting, converting, roasting, fire_refining), гидро/аффинаж (leaching, chlorination,
  precipitation), водный домен (desalination, water_treatment, electroextraction, injection),
  металлообработка. RU/EN-алиасы для резолва. **Метод — адресуемый объект:** «методы X при
  параметрах Y» = найти процессы реестра и агрегировать к ним эксперименты, выводы и ТЭП
  (VIEW `experiment_processes`).

### 2.5 Значения и диапазоны

- **`ValueRange{min, nominal, max}`** (first-class) — «отжиг 1050–1100 °C», «Cr 17–19 wt%»,
  «извлечение >95 %» — нормальные значения. Точечное = `nominal`.
- **`Composition{basis, elements: {el: ValueRange}, balance_element}`** — состав; `basis`
  один на весь состав (wt%/at%/mol%); «Mg-0.4Zn» → Zn 0.4, `balance='Mg'`.
- **`Regime.steps[]`** — упорядоченная цепочка стадий (закалка + 2×старение; extruded +
  pickled; fire_refining + casting). В БД температуры в SI (K), с generated-колонками и
  `regime_bucket` (low <400 / medium / high >800 °C) для тепловой карты.

---

## 3. Три инварианта

### И1. Каждый факт несёт дословную цитату (провенанс + span)

У любого узла и ребра обязателен `Provenance{doc_id, locator, snippet, extractor,
confidence}`. **Факт без непустого `snippet` отбраковывается** (валидатор pydantic + Postgres
CHECK). Это защита от галлюцинаций LLM и основа функции «от числа → исходный абзац/ячейка».
Спаны у LLM не берём: экстрактор возвращает verbatim-фрагмент, а точную позицию (`locator`)
вычисляем relocate-поиском (rapidfuzz по MD-артефакту, score ≥ 90; иначе fallback до страницы
+ `needs_review`). `artifact_sha256` привязывает спан к конкретной версии распарсенного
документа.

### И2. Сравниваем только сопоставимое (Comparability Gate)

`is_comparable(a, b) → {comparable, blocking_dims[]}` вызывается **перед** детектором
противоречий, поиском похожести и gap-map. Шесть блокирующих осей:

1. `quantity_kind` — предел текучести ≠ твёрдость;
2. `scale` — HV30 ≠ HRC ≠ HB (ASTM E140: конверсия нелинейна и материал-зависима);
3. `basis` — wt% ≠ at% ≠ mol%;
4. `unit_dim` — MPa↔GPa конвертируем (Pint), MPa↔μm — нет;
5. `processing_state` — hash цепочки режима: литое ≠ деформированное ≠ экструдированное;
6. `measurement_conditions` — σ₀.₂ при 20 °C ≠ при 650 °C; HV30 ≠ HV10.

«Несопоставимо по осям [scale, conditions]» — это тоже результат: система не объявляет
конфликтом два разных числа, а возвращает причину несопоставимости. Без шлюза детектор
противоречий давал бы ложные срабатывания (Виккерс против Роквелла, литое против кованого).

### И3. Открыто ровно то, что должно быть открытым

Предикаты закрыты (предсказуемость для SQL и UI). Реестры величин и процессов открыты (состав
корпуса непредсказуем). Провенанс и привязка `Measurement → (experiment, material)` —
first-class с первого дня, потому что их нельзя добавить ретроактивно дёшево.

---

## 4. Хранение и архитектура

### 4.1 Один Postgres, граф — проекция

**Канон** = нормализованные таблицы `experiments.*` (полный DDL — `ontology/ddl.sql`).
**Граф не хранится отдельно** — это VIEW:

```sql
CREATE VIEW experiments.edges AS
  SELECT experiment_id AS src, material_id AS dst, 'uses_material', jsonb_build_object('role',role), prov
    FROM experiments.experiment_materials
  UNION ALL SELECT id, experiment_id, 'measured_on', '{}', prov FROM experiments.results
  UNION ALL ... (applies_process, reported_in, concludes из FK)
  UNION ALL SELECT src, dst, predicate, attrs, prov FROM experiments.edges_semantic;
```

Структурные рёбра выводятся из FK; напрямую в таблицу `edges_semantic` пишутся только
семантические/вычисляемые (`derived_from`, `supports`, `contradicts`, `refines`,
`similar_conditions`, `canonical_for`). Плюсы: граф всегда консистентен таблицам (нет двойной
бухгалтерии и рассинхрона), нет второй БД и её синхронизации. Neo4j — опциональная
экспорт-проекция для визуализации, не источник истины; лейблы спроектированы
(`USED_IN/UNDER_REGIME/MEASURES/…`).

«Связанные сущности» = окрестность узла в один hop по VIEW. «История решений» = рекурсивный
CTE по `derived_from`. Тепловая карта пробелов = `GROUP BY` по (material, regime_bucket,
quantity_kind) — сетка нигде не хранится, она вычисляется, потому что оси типизированы.

### 4.2 Таблицы (обзор; полный DDL в файле)

`documents · labs · equipment · topics · process_types · materials · quantity_kinds · regimes
· experiments · experiment_materials · results · conclusions · entity_aliases · entity_same_as
· edges_semantic` + VIEW `edges`, `experiment_processes`.

Дисциплина на уровне БД: `CONSTRAINT prov_has_snippet CHECK (prov ? 'snippet' AND
length(prov->>'snippet')>0)` на факт-таблицах; `CHECK (scope='material') = (material_id IS NOT
NULL)`; append-only `created_at + superseded_by` (версионирование фактов без битемпоральности;
актуальный срез = `WHERE superseded_by IS NULL`).

### 4.3 Слой выравнивания со стандартами (alignment)

Чужие онтологии не импортируются, reasoner не используется. В `contracts.py` лежат словари
соответствий: `PMDCO_ALIGNMENT` (класс → URI PMDco), `PROV_O`, `QUDT_UNITS` (единица → QUDT
URI), `TZ_PREDICATE_ALIGNMENT` (требуемые отношения → наши предикаты). Это ~30 строк, ничего
не исполняющих в рантайме; назначение — сверка имён классов со стандартом при проектировании и
возможность экспорта в JSON-LD/RDF для обмена. Твёрдость (HV/HRC) сознательно без
QUDT-конверсии — это разные шкалы (ASTM E140).

---

## 5. Наполнение (population)

### 5.1 Источники и конвейер

Источники разнородны: внутренние отчёты и статьи (word/pdf), каталоги экспериментов и
справочники (xlsx/csv, если доступны), перечни сотрудников/лабораторий, таксономия тегов.
Наиболее ценная информация — квадруплет `материал × режим × свойство × значение(±unc, unit)`
плюс кто/когда/где и выводы-эффекты.

Конвейер (порядок = приоритет):

1. **Каталоги/справочники (XLSX/CSV):** pandas → pydantic → БД. Детерминированный join по
   ID/датам, **ноль LLM**, `origin=catalog`, `locator=xlsx_row`, snippet = сериализованная
   строка. Отсюда же канонические материалы с синонимами — источник алиасов для ER
   (справочник содержит ровно те обозначения, что встречаются в документах).
2. **DOCX** → текст (pandoc/zip); `locator=docx_para`.
3. **PDF** → MD (Marker, +page-map, sha256); таблицы, где конвертер крошит структуру —
   `pdfplumber`.
4. **Экстракция прозы:** чанк MD по секциям → **NuExtract3 / LangExtract со схемой =
   `contracts.py`** (schema-based population: модель заполняет заданную схему, а не изобретает
   свою). Few-shot из каталога. Экстрактор обязан вернуть verbatim `snippet`.
5. **Relocate:** snippet → точный `locator` (rapidfuzz по MD; score ≥ 90 или `needs_review`).
6. **Normalize:** единицы → SI (Pint, только размерные; шкалы не трогаем); «229 ± 7» → value
   + sd; роды величин → `QuantityKindRegistry.resolve` (unknown → HITL).
7. **Dedup-link (ER):** алиасы из справочников → `entity_aliases`; слияния →
   `entity_same_as(confidence, method)`; спорные → HITL; spaCy EntityRuler вторым дешёвым
   проходом (словарь авто-собран из выхода NuExtract).
8. **Валидация** pydantic → статус `committed | needs_review`.

### 5.2 Как контракт «правит» экстракцией

`contracts.py` сериализуется в JSON-схему, которая подаётся NuExtract/LangExtract как целевая
структура. Модель не может вернуть поле вне схемы или предикат вне словаря. Всё, что вернула,
проходит валидатор: нет цитаты — отбраковка; неизвестная величина — в HITL-очередь. Это
нейро-символический паттерн: LLM даёт охват, схема + валидаторы дают корректность.

### 5.3 Подключение внешних баз

`Material.external_ids JSONB {pubchem_cid, gost, uns, aisi, wikidata_qid}` + ручной
crosswalk-CSV марок (12Х18Н10Т ↔ AISI 321 ↔ UNS S32100); PubChem PUG REST (name → CID, без
ключа) для реагентов. Внешние ID — ключи идентификации, не классификации.

---

## 6. Использование (запросы)

Граница схемы и одновременно её тест — **competency questions**. Каждый CQ → SQL.

| # | Вопрос | Как отвечается |
|---|---|---|
| CQ1 | что делали методом/по материалу X при режиме Y (диапазон) и какой направленный эффект на Z, в скольких экспериментах? | поиск по experiment_processes/materials + regime + results → **Evidence** + Effect |
| CQ2 | какие процессы применялись и с каким результатом? | `experiment_processes` GROUP BY process_type (метод как объект) |
| CQ3 | какие измерения Z сопоставимы и есть ли среди них противоречие по направлению? | Comparability Gate → `GROUP BY effect.direction` |
| CQ4 | покажи всё, связанное с E (материал, режим, установка, лаб, документ, вывод) | окрестность в 1 hop по `edges` VIEW |
| CQ5 | дословное место в источнике, откуда взято число | `prov` до ячейки/спана (100% coverage) |
| CQ6 | lineage: из чего получен интермедиат, какими переделами, что мерили на каждой ступени? | рекурсивный CTE по `derived_from` (+process на стрелках) |
| CQ7 | кто/когда/где работал с X — по годам, лабораториям, географии? | таймлайн по `date` + `performed_by → TeamLab.country` |
| CQ8 | где пробелы (материал × режим × свойство без данных) и где данные есть, но несопоставимы? | gap-map |
| CQ9 | эксперименты на близких по составу материалах при близком режиме? | condition-vector KNN + composition-similarity |
| CQ10 | что поставить следующим? | **NextExperiment** из разреженных ячеек + rationale со спанами |
| CQ11 | сравни технологии A и B по параметру Z на сопоставимых измерениях | SQL поверх existing + Gate |

**Форматы ответов** (интерпретаторы, `contracts.py`): `Evidence{answer, experiments,
n_experiments, n_docs, labs, regime_range, effect, confidence, agreement_flag, citations,
gap_note}` · `GapCell` · `ContradictionFlag{a_span, b_span, delta_value, labs, comparability}`
· `NextExperiment`.

---

## 7. Подключение инструментов (tools / агент)

Онтология живёт внутри backend-монолита, поэтому инструменты агента — обычные Python-функции
сервисного слоя, вызывающие SQL по схеме, а не HTTP к соседним контейнерам. Каждый tool имеет
pydantic-контракт вход/выход из `contracts.py`.

**Правило: LLM не пишет исполняемый SQL/Cypher и не финализирует число.** LLM делает две
вещи: (а) офлайн-экстракция в схему; (б) заполнение валидируемых слотов (constrained query
builder: вопрос → слоты {материал, диапазон режима, свойство, тема} → код строит SQL). Число
всегда из БД.

| Tool | Вход → выход | Что делает |
|---|---|---|
| `hybrid_search(query, filters)` | текст + слоты → `[Evidence-строки]` | SQL pre-filter + BM25 + pgvector + RRF |
| `sql_aggregate(slots)` | слоты → числа/агрегаты | статистика по ячейке (соседи, coverage) |
| `gate_check(a, b)` | два measurement → `Comparability` | шлюз сопоставимости перед сравнением |
| `contradictions(family)` | семейство → `[ContradictionFlag]` | пары effect с обратным direction, через Gate |
| `gap_map(filter)` | оси → `[GapCell]` | тепловая карта покрытия |
| `lineage(entity_id)` | узел → цепочка `derived_from` | история переделов |
| `project_graph(filter)` | фильтр → `{nodes, edges}` | подграф для Cytoscape |
| `generate_hypothesis(gap_cell)` | ячейка → `Conclusion{kind:hypothesis}` | `sql_aggregate → hybrid_search → LLM` |

**Как добавить новый tool.** (1) Определи вход/выход pydantic-моделями в `contracts.py`;
(2) реализуй функцию в `app/services/<domain>.py`, читающую БД по схеме; (3) зарегистрируй как
tool агента (описание + JSON-схема параметров из pydantic) и/или FastAPI-роут; (4) фронт
получает типы через автогенерируемый клиент. Инструмент не принимает raw SQL от клиента —
только валидируемые слоты либо `template_id + params`.

**Экспортные инструменты:** `export_jsonld(entity)` (@context из alignment-словарей); экспорт
в Neo4j для граф-визуализации.

---

## 8. Соответствие требованиям ТЗ

Требуемые типы сущностей и отношения ложатся на схему:

| Требование ТЗ | Реализация |
|---|---|
| Material, Process, Equipment, Property, Experiment, Publication, Expert, Facility | Material, Process(реестр), Equipment, quantity_kinds, Experiment, Document, TeamLab, Equipment.family/site |
| `uses_material` | `uses_material` |
| `produces_output` | `uses_material` role=output |
| `operates_at_condition` | `applies_process` + Regime.steps |
| `described_in` | `reported_in` |
| `validated_by` | `supports` + `reported_in` + confidence |
| `contradicts` | `contradicts` (через Comparability Gate) |

Полная таблица — `TZ_PREDICATE_ALIGNMENT` в `contracts.py`. Вне онтологии (слой приложения):
роли доступа (`users.role`), аудит действий (`audit_log`), NL-интерфейс и семантический поиск
(hybrid search + chat; онтология даёт им субстрат — embedding, cond_vector, алиасы).

---

## 9. План развития

### 9.1 Приоритеты

- **P0 (ядро):** классы + предикаты + провенанс; каталог/справочники → `origin=catalog`;
  NuExtract по схеме + relocate; Evidence по CQ1/CQ4/CQ5; Comparability Gate; edges-VIEW.
  Минимальный отвечающий набор — шесть таблиц (documents, materials, experiments, results,
  labs, edges).
- **P1:** gap-map (CQ8) + heatmap; детектор противоречий (CQ3); lineage (CQ6); реестр
  процессов как объект (CQ2); география/эксперты (CQ7); `generate_hypothesis`.
- **P2:** similar_conditions/NextExperiment (CQ9/CQ10); сравнение технологий (CQ11);
  JSON-LD/Neo4j-экспорт; версионные снапшоты «на дату».

### 9.2 Порядок работ

Схема (`contracts.py` + `ddl.sql`) замораживается до начала параллельной работы модулей —
это контракт интеграции. Экстракция и вычисляемые связи выполняются офлайн, результат грузится
в БД. Онлайн-путь — детерминированное чтение из готовой БД.

### 9.3 Границы модели (вне области)

Сознательно не включено (расширяемо аддитивно, без переписывания модулей): SHACL (замена —
pydantic + Postgres CHECK); отдельный класс Sample/Specimen (пара material×regime +
`sample_state` достаточно); полная битемпоральность; полный материальный баланс переделов;
OWL/reasoner; Neo4j как хранилище (только экспорт).

### 9.4 Расширяемость на другой домен

Онтология доменно-конфигурируема: смена профиля предметной области меняет только seed реестров
(`quantity_kinds`, `process_types`) и таблицы алиасов, но не схему и не код модулей.

---

## Приложение. Демо

`python -m ontology.demo` поднимает в онтологию три документа из `seed/norilsk_pgm.json`
(хлорирование ПГМ-концентратов, осаждение Au сульфитом натрия, отливка Cu-анодов) и отвечает
на 7 CQ: hero-Evidence с цитатой, метод-как-объект, эффект-стрелки, Comparability Gate,
lineage переделов, география + `supports`/верификация, gap-map + NextExperiment. Провенанс —
100%.
