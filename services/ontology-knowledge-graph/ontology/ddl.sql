-- Онтология v2.1 → Postgres (schema experiments.*, выравнено со SPEC_V3 §4).
-- Канон = нормализованные таблицы; edges = VIEW (граф-проекция, НЕ вторая копия).
-- Дельты против SPEC_V3 помечены -- Δ; дельты v2.1 под финальное ТЗ помечены -- Т.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS experiments;

CREATE TABLE experiments.documents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    minio_key     TEXT NOT NULL,
    filename      TEXT NOT NULL,
    doc_type      TEXT CHECK (doc_type IN ('article','internal_report','catalog','handbook','patent')),
    year          INT,
    country       TEXT,                      -- Т3: ISO-код; отечественная(RU)/зарубежная практика
    lang          TEXT CHECK (lang IN ('ru','en')),  -- Т3: автодетект кириллицы на ингесте
    mime_type     TEXT,
    artifact_sha256 TEXT,                    -- Δ Д5: хэш MD-артефакта Marker'а (relocate спанов)
    processing_level TEXT,                   -- совместимость с ingest-контуром (L0..L3)
    okf_raw_path  TEXT,                      -- совместимость: путь к OKF raw markdown
    uploaded_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE experiments.labs (              -- Т3: география + эксперты (из перечня кейса)
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'lab' CHECK (kind IN ('lab','person','team')),
    parent_id     UUID REFERENCES experiments.labs(id),   -- person -> lab
    country       TEXT,
    city          TEXT,
    expertise     TEXT[] DEFAULT '{}'        -- из справочника + derived (топ topics лаборатории)
);

CREATE TABLE experiments.equipment (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    equipment_type TEXT,
    lab_id        UUID REFERENCES experiments.labs(id)
);

CREATE TABLE experiments.topics (            -- Т5: таксономия тегов кейса, как есть
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label         TEXT NOT NULL,
    parent_id     UUID REFERENCES experiments.topics(id)
);

CREATE TABLE experiments.process_types (     -- Т1: ОТКРЫТЫЙ реестр процессов/методов
    name          TEXT PRIMARY KEY,          -- 'desalination', 'electroextraction', ...
    aliases       TEXT[] DEFAULT '{}',       -- RU/EN: «обессоливание», «опреснение»
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'seed' CHECK (status IN ('seed','confirmed','needs_review'))
);

CREATE TABLE experiments.materials (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    family        TEXT NOT NULL DEFAULT 'other',   -- alloy|steel|matte|slag|concentrate|...
    grade         TEXT,
    -- Δ Д7: basis один на состав; элементы с диапазонами {el: {min,nominal,max}}
    composition   JSONB,                    -- {"basis":"wt%","balance":"Mg","elements":{"Zn":{"nominal":0.4}}}
    phase         TEXT,
    external_ids  JSONB DEFAULT '{}',       -- Δ Д11: {pubchem_cid, gost, uns, aisi, wikidata_qid}
    embedding     VECTOR(768),
    prov          JSONB NOT NULL,           -- Δ Д5: {doc_id, locator_kind, locator, snippet, extractor, confidence}
    CONSTRAINT prov_has_snippet CHECK (prov ? 'snippet' AND length(prov->>'snippet') > 0)
);

CREATE TABLE experiments.quantity_kinds (   -- Δ Д6: ОТКРЫТЫЙ реестр родов величин
    name          TEXT PRIMARY KEY,
    unit_dim      TEXT NOT NULL,
    aliases       TEXT[] DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'seed' CHECK (status IN ('seed','confirmed','needs_review'))
);

CREATE TABLE experiments.regimes (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    steps         JSONB NOT NULL,           -- Δ Д8: [{process_type, temperature:{min,nominal,max} K, duration_s, ...}]
    -- Δ Д8: generated columns для hero-диапазонов и бакетов (SI!)
    max_temperature_k DOUBLE PRECISION,     -- заполняется на ingest из steps
    regime_bucket TEXT GENERATED ALWAYS AS (
        CASE WHEN max_temperature_k IS NULL THEN NULL
             WHEN max_temperature_k <  673.15 THEN 'low'
             WHEN max_temperature_k < 1073.15 THEN 'medium'
             ELSE 'high' END) STORED,       -- ≡ dictionaries/regime_buckets.yaml
    state_hash    TEXT                      -- 'extrusion+surface_treatment' → ось Gate
);
CREATE INDEX idx_regimes_t ON experiments.regimes (max_temperature_k);
CREATE INDEX idx_regimes_bucket ON experiments.regimes (regime_bucket);

CREATE TABLE experiments.experiments (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title         TEXT,
    date          DATE,                     -- Δ Д10: таймлайн = «история решений»
    origin        TEXT NOT NULL DEFAULT 'extracted'
                  CHECK (origin IN ('catalog','extracted')),  -- Δ Д10: золотой каркас vs LLM
    regime_id     UUID REFERENCES experiments.regimes(id),
    equipment_id  UUID REFERENCES experiments.equipment(id),      -- Т: FK вместо висячего UUID
    lab_id        UUID REFERENCES experiments.labs(id),           -- Т3: география через performed_by
    researcher_id UUID REFERENCES experiments.labs(id),
    site          TEXT,                      -- Т3: площадка/объект, если названа в тексте
    document_id   UUID REFERENCES experiments.documents(id),
    tags          TEXT[],
    cond_vector   VECTOR(64),               -- similar_conditions / gap-map
    embedding     VECTOR(768),
    prov          JSONB NOT NULL,
    status        TEXT NOT NULL DEFAULT 'committed' CHECK (status IN ('draft','committed','needs_review'))
);

-- Δ Д9: материалы эксперимента с РОЛЯМИ (вместо material_ids[] и вместо предиката produces)
CREATE TABLE experiments.experiment_materials (
    experiment_id UUID REFERENCES experiments.experiments(id),
    material_id   UUID REFERENCES experiments.materials(id),
    role          TEXT NOT NULL DEFAULT 'sample'
                  CHECK (role IN ('sample','input','output','medium','flux','atmosphere','reference')),
    prov          JSONB NOT NULL,
    PRIMARY KEY (experiment_id, material_id, role)
);

CREATE TABLE experiments.results (          -- ≡ Measurement онтологии; append-only (Т4)
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id UUID NOT NULL REFERENCES experiments.experiments(id),
    -- Т2: scope='material' -> свойство образца, material_id обязателен (Д1 сохранена);
    --     scope='experiment' -> ТЭП передела (энергоёмкость, себестоимость, выход по току)
    scope         TEXT NOT NULL DEFAULT 'material' CHECK (scope IN ('material','experiment')),
    material_id   UUID REFERENCES experiments.materials(id),
    quantity_kind TEXT NOT NULL REFERENCES experiments.quantity_kinds(name),
    value_min     DOUBLE PRECISION,         -- Δ Д7: диапазоны first-class
    value_nominal DOUBLE PRECISION,
    value_max     DOUBLE PRECISION,
    unit          TEXT,
    scale         TEXT NOT NULL DEFAULT 'none',   -- HV30/HRC/... НЕ конвертировать (ASTM E140)
    basis         TEXT,                     -- wt%/at%/mol%
    uncertainty   JSONB,                    -- {"sd": 7, "n": 5}
    conditions    JSONB DEFAULT '{}',       -- Δ Д3: {temperature_k, load, strain_rate, medium}
    sample_state  TEXT,                     -- Δ Д3: as_cast|wrought|extruded|... (ось Gate)
    method        TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),   -- Т4: «дата актуализации»
    superseded_by UUID REFERENCES experiments.results(id),  -- Т4: UPDATE запрещён, только новая версия
    prov          JSONB NOT NULL,
    CONSTRAINT prov_has_snippet CHECK (prov ? 'snippet' AND length(prov->>'snippet') > 0),
    CONSTRAINT scope_material CHECK ((scope = 'material') = (material_id IS NOT NULL))
);
CREATE INDEX idx_results_qk ON experiments.results (quantity_kind, material_id);

CREATE TABLE experiments.conclusions (      -- Δ Д2: вывод + структурный эффект; append-only (Т4)
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id UUID REFERENCES experiments.experiments(id),   -- NULL = утверждение уровня документа
    document_id   UUID REFERENCES experiments.documents(id),     -- для doc-level claims (обзоры, доклады)
    process_type  TEXT,                      -- метод, о котором утверждение (для doc-level claims)
    text          TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'finding' CHECK (kind IN ('finding','recommendation')),
    effect        JSONB,                    -- {quantity_kind, direction, factor, baseline_ref,
                                            --  optimum:{min,max}, optimum_unit}  (Т5)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),   -- Т4
    superseded_by UUID REFERENCES experiments.conclusions(id),  -- Т4
    prov          JSONB NOT NULL,
    CONSTRAINT prov_has_snippet CHECK (prov ? 'snippet' AND length(prov->>'snippet') > 0)
);
-- Т4: актуальный срез = WHERE superseded_by IS NULL; снапшот «на дату» =
-- WHERE created_at <= $t AND (superseded_by IS NULL OR ...) — не демонстрировать, но возможно.
-- детектор противоречий = SQL: пары effect с одинаковыми (factor, quantity_kind),
-- прошедшие Gate, с противоположным direction. Никакого LLM в рантайме.

CREATE TABLE experiments.entity_aliases (   -- единый механизм ER (SPEC_V3)
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type TEXT NOT NULL, entity_id UUID NOT NULL,
    alias TEXT NOT NULL, source TEXT
);
CREATE TABLE experiments.entity_same_as (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type TEXT NOT NULL, source_id UUID NOT NULL, canonical_id UUID NOT NULL,
    confidence FLOAT DEFAULT 1.0, method TEXT   -- exact_alias|embedding|manual
);

-- Δ Д4: только СЕМАНТИЧЕСКИЕ рёбра пишутся напрямую (структурные живут в FK)
CREATE TABLE experiments.edges_semantic (
    src UUID NOT NULL, dst UUID NOT NULL,
    predicate TEXT NOT NULL CHECK (predicate IN
        ('derived_from','supports','contradicts','refines','similar_conditions','canonical_for')),
    attrs JSONB DEFAULT '{}',               -- Δ Д9: {process: '...'} на derived_from
    weight DOUBLE PRECISION,
    prov  JSONB NOT NULL,
    PRIMARY KEY (src, dst, predicate)
);

-- Δ Д4: ГРАФ = ленивая проекция. Единственный источник истины — таблицы выше.
CREATE OR REPLACE VIEW experiments.edges AS
    SELECT em.experiment_id AS src, em.material_id AS dst,
           'uses_material' AS predicate,
           jsonb_build_object('role', em.role) AS attrs, em.prov
    FROM experiments.experiment_materials em
  UNION ALL
    SELECT r.id, r.experiment_id, 'measured_on', '{}'::jsonb, r.prov
    FROM experiments.results r
  UNION ALL
    SELECT e.id, e.regime_id, 'applies_process', '{}'::jsonb, e.prov
    FROM experiments.experiments e WHERE e.regime_id IS NOT NULL
  UNION ALL
    SELECT e.id, e.document_id, 'reported_in', '{}'::jsonb, e.prov
    FROM experiments.experiments e WHERE e.document_id IS NOT NULL
  UNION ALL
    SELECT e.id, e.lab_id, 'performed_by', '{}'::jsonb, e.prov
    FROM experiments.experiments e WHERE e.lab_id IS NOT NULL
  UNION ALL
    SELECT e.id, e.equipment_id, 'run_on_equipment', '{}'::jsonb, e.prov
    FROM experiments.experiments e WHERE e.equipment_id IS NOT NULL
  UNION ALL
    SELECT c.experiment_id, c.id, 'concludes', '{}'::jsonb, c.prov
    FROM experiments.conclusions c WHERE c.experiment_id IS NOT NULL
  UNION ALL
    SELECT c.id, c.document_id, 'reported_in', '{}'::jsonb, c.prov
    FROM experiments.conclusions c WHERE c.document_id IS NOT NULL
  UNION ALL
    SELECT s.src, s.dst, s.predicate, s.attrs, s.prov
    FROM experiments.edges_semantic s;

-- Т1: метод/техрешение адресуем. «Все эксперименты метода обессоливания» =
-- SELECT * FROM experiments.experiment_processes WHERE process_type = 'desalination';
CREATE OR REPLACE VIEW experiments.experiment_processes AS
    SELECT e.id AS experiment_id, (step->>'process_type') AS process_type
    FROM experiments.experiments e
    JOIN experiments.regimes rg ON rg.id = e.regime_id
    CROSS JOIN LATERAL jsonb_array_elements(rg.steps) AS step
    GROUP BY e.id, step->>'process_type';
-- ТЭП метода = JOIN этой VIEW c results(scope='experiment'); группировка
-- источников «по методу» из ТЗ = GROUP BY process_type.

-- «История решений» = рекурсия по derived_from:
-- WITH RECURSIVE lineage(src,dst,proc,depth) AS (
--   SELECT src,dst,attrs->>'process',1 FROM experiments.edges_semantic
--     WHERE predicate='derived_from' AND src=$1
--   UNION ALL
--   SELECT e.src,e.dst,e.attrs->>'process',l.depth+1
--     FROM experiments.edges_semantic e JOIN lineage l ON e.src=l.dst
--     WHERE e.predicate='derived_from' AND l.depth<10)
-- SELECT * FROM lineage;
