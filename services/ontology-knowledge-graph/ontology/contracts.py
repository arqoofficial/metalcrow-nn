"""
Онтология «Научного клубка» v2.1 — контракт интеграции.

Единый источник правды для всех модулей: pydantic-схема == таблицы Postgres ==
целевая схема экстракции. Спецификация — specs/ONTOLOGY_V2.md.

Принципы:
  - TBox (эти классы/энумы) отделён от ABox (строки в Postgres).
  - Провенанс + span обязателен на КАЖДОМ узле и ребре; узел без спана отбраковывается.
  - Закрытый словарь предикатов; открытые реестры родов величин и процессов.
  - Measurement привязан к (experiment_id, material_id|scope) — не теряет, чьё значение.
  - Conclusion.effect {quantity_kind, direction, factor} — эффект представим структурно;
    детектор противоречий = SQL GROUP BY, а не сравнение текстов LLM.
  - Comparability Gate — сравниваем только сопоставимое (6 осей).
  - Значения-диапазоны (ValueRange), многостадийный режим (steps[]), SI в БД (K/Pa/s).

Python 3.11+, pydantic v2. Запуск smoke-теста: python -m ontology.contracts
"""
from __future__ import annotations

import datetime as _dt
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

# ──────────────────────────────────────────────────────────────────────────
# 1. ЗАКРЫТЫЙ словарь предикатов (единственный закрытый словарь схемы).
#    Расширять — только явным решением команды, не в рантайме.
# ──────────────────────────────────────────────────────────────────────────

class Predicate(str, Enum):
    # структурные (из факта-источника)
    USES_MATERIAL = "uses_material"          # Experiment -> Material (+attrs.role!)
    APPLIES_PROCESS = "applies_process"      # Experiment -> Process
    MEASURED_ON = "measured_on"              # Measurement -> Experiment (ТОЛЬКО Experiment)
    HAS_PROPERTY = "has_property"            # Measurement -> Property/QuantityKind
    RUN_ON_EQUIPMENT = "run_on_equipment"    # Experiment -> Equipment
    PERFORMED_BY = "performed_by"            # Experiment -> Lab/Person
    REPORTED_IN = "reported_in"              # Experiment | Conclusion -> Document
    CONCLUDES = "concludes"                  # Experiment -> Conclusion
    TAGGED_AS = "tagged_as"                  # Experiment | Document -> Topic
    # семантические / вычисляемые
    DERIVED_FROM = "derived_from"            # lineage; +attrs.process_id («каким переделом»)
    SUPPORTS = "supports"                    # Conclusion -> Conclusion (только через Gate)
    CONTRADICTS = "contradicts"              # Conclusion -> Conclusion (только через Gate)
    REFINES = "refines"                      # Conclusion -> Conclusion
    SIMILAR_CONDITIONS = "similar_conditions"  # Experiment -> Experiment (вычисляемое)
    CANONICAL_FOR = "canonical_for"          # ER: skos:exactMatch (НЕ owl:sameAs)


class MaterialRole(str, Enum):
    """Квалификатор ребра uses_material (Edge.attrs['role']).
    Выражает n-арность «шихта+флюс+дутьё -> штейн+шлак+газ» и
    «Pd — образец, электролит — среда» БЕЗ нового класса."""
    SAMPLE = "sample"          # исследуемый образец (дефолт)
    INPUT = "input"            # вход передела (шихта, концентрат)
    OUTPUT = "output"          # продукт (штейн, шлак) — заменяет предикат "produces"
    MEDIUM = "medium"          # среда/электролит
    FLUX = "flux"              # флюс/реагент
    ATMOSPHERE = "atmosphere"  # дутьё/атмосфера
    REFERENCE = "reference"    # электрод сравнения / эталон


# ──────────────────────────────────────────────────────────────────────────
# 2. Реестр родов величин — ОТКРЫТЫЙ (seed + HITL-пополнение на ингесте).
#    Урок разведки: в первом же реальном файле «average grain diameter»,
#    которого нет в исходном перечне, — уехало бы в OTHER и Gate ослеп бы.
# ──────────────────────────────────────────────────────────────────────────

class QuantityKindDef(BaseModel):
    name: str                 # канон. имя: "yield_strength"
    unit_dim: str             # размерность: "pressure" | "length" | "ratio" | ...
    aliases: list[str] = []   # "0.2 % proof stress", "предел текучести", "σ0.2"


QUANTITY_KINDS_SEED: dict[str, QuantityKindDef] = {
    d.name: d for d in [
        QuantityKindDef(name="hardness", unit_dim="hardness_scale",
                        aliases=["твёрдость", "твердость", "hardness"]),
        QuantityKindDef(name="yield_strength", unit_dim="pressure",
                        aliases=["0.2 % proof stress", "предел текучести", "σ0.2", "YS"]),
        QuantityKindDef(name="tensile_strength", unit_dim="pressure",
                        aliases=["ultimate tensile strength", "предел прочности", "UTS", "σв"]),
        QuantityKindDef(name="elongation", unit_dim="ratio",
                        aliases=["total elongation to failure", "относительное удлинение", "At"]),
        QuantityKindDef(name="grain_size", unit_dim="length",
                        aliases=["average grain diameter", "размер зерна"]),
        QuantityKindDef(name="creep_rate", unit_dim="strain_rate", aliases=["скорость ползучести"]),
        QuantityKindDef(name="electrode_potential", unit_dim="voltage", aliases=["потенциал"]),
        QuantityKindDef(name="current_density", unit_dim="current_density",
                        aliases=["плотность тока"]),
        QuantityKindDef(name="recovery_degree", unit_dim="ratio",
                        aliases=["степень извлечения", "извлечение"]),
        QuantityKindDef(name="phase_fraction", unit_dim="ratio", aliases=["доля фазы"]),
        QuantityKindDef(name="element_content", unit_dim="ratio",
                        aliases=["содержание элемента", "содержание"]),
        QuantityKindDef(name="melting_point", unit_dim="temperature",
                        aliases=["температура плавления"]),
        QuantityKindDef(name="viscosity", unit_dim="viscosity", aliases=["вязкость"]),
        # ── водный домен (целевые запросы ТЗ 1, 2, 4) ──
        # «сухой остаток» и «минерализация» метрологически НЕ тождественны —
        # два канона с перекрёстной пометкой в description, не один.
        QuantityKindDef(name="dry_residue", unit_dim="mass_concentration",
                        aliases=["сухой остаток", "TDS", "total dissolved solids"]),
        QuantityKindDef(name="salinity", unit_dim="mass_concentration",
                        aliases=["минерализация", "общая минерализация", "солесодержание"]),
        QuantityKindDef(name="flow_rate", unit_dim="volumetric_flow",
                        aliases=["скорость потока", "расход", "flow rate", "циркуляция"]),
        QuantityKindDef(name="ph", unit_dim="dimensionless", aliases=["pH", "рН", "кислотность"]),
        # ── ТЭП: технико-экономические показатели (целевой запрос ТЗ 4) ──
        QuantityKindDef(name="energy_consumption", unit_dim="specific_energy",
                        aliases=["удельный расход энергии", "энергоёмкость", "kWh/t"]),
        QuantityKindDef(name="specific_cost", unit_dim="specific_cost",
                        aliases=["себестоимость", "удельные затраты"]),
        QuantityKindDef(name="capex", unit_dim="currency", aliases=["капитальные затраты", "CAPEX"]),
        QuantityKindDef(name="opex", unit_dim="currency", aliases=["операционные затраты", "OPEX"]),
        QuantityKindDef(name="current_efficiency", unit_dim="ratio",
                        aliases=["выход по току", "current efficiency"]),
    ]
}


class QuantityKindRegistry:
    """Реестр родов величин: exact/alias-резолв + HITL-пополнение unknown'ов."""

    def __init__(self, seed: dict[str, QuantityKindDef] | None = None):
        self.kinds: dict[str, QuantityKindDef] = dict(seed or QUANTITY_KINDS_SEED)
        self._alias_index: dict[str, str] = {}
        self.pending_review: list[str] = []   # кандидаты на HITL-подтверждение
        for d in self.kinds.values():
            for a in [d.name, *d.aliases]:
                self._alias_index[a.strip().lower()] = d.name

    def resolve(self, raw: str, unit_dim: str = "unknown") -> str:
        key = raw.strip().lower()
        if key in self._alias_index:
            return self._alias_index[key]
        # unknown -> авторегистрация как черновик + очередь на review (паттерн Wikontic)
        name = key.replace(" ", "_").replace("%", "pct")
        self.kinds[name] = QuantityKindDef(name=name, unit_dim=unit_dim, aliases=[raw])
        self._alias_index[key] = name
        self.pending_review.append(name)
        return name


class HardnessScale(str, Enum):
    """HV/HRC/HB — РАЗНЫЕ неконвертируемые величины (ASTM E140, нелинейно)."""
    HV = "HV"; HV30 = "HV30"; HRC = "HRC"; HRB = "HRB"; HB = "HB"; HK = "HK"
    NONE = "none"


class CompositionBasis(str, Enum):
    WT_PCT = "wt%"; AT_PCT = "at%"; MOL_PCT = "mol%"


class ProcessType(str, Enum):
    # пирометаллургия (профиль Норникеля)
    SMELTING = "smelting"; CONVERTING = "converting"; ROASTING = "roasting"
    FLOTATION = "flotation"; LEACHING = "leaching"
    FIRE_REFINING = "fire_refining"                  # огневое рафинирование
    # гидрометаллургия / аффинаж ДМ / водный домен (целевые запросы ТЗ 1–4)
    DESALINATION = "desalination"; WATER_TREATMENT = "water_treatment"
    ELECTROEXTRACTION = "electroextraction"; INJECTION = "injection"
    CHLORINATION = "chlorination"                    # хлорирование (вскрытие концентратов ПМ)
    PRECIPITATION = "precipitation"                  # осаждение (аффинаж ДМ)
    # металлообработка (виденные данные: Mg-Zn, стали, Inconel)
    HEAT_TREATMENT = "heat_treatment"; QUENCHING = "quenching"; ANNEALING = "annealing"
    AGING = "aging"; EXTRUSION = "extrusion"; ROLLING = "rolling"; WELDING = "welding"
    CASTING = "casting"; SURFACE_TREATMENT = "surface_treatment"
    ELECTROCHEMICAL = "electrochemical"
    OTHER = "other"


class ProcessDef(BaseModel):
    """Процесс/метод — АДРЕСУЕМЫЙ объект (строка открытого реестра), а не
    только значение enum внутри Regime. «Методы обессоливания при параметрах X»
    из ТЗ = найти процессы реестра + агрегировать их эксперименты/выводы/ТЭП
    (edges-VIEW даёт applies_process: Experiment -> process_type). В БД —
    таблица process_types по образцу quantity_kinds."""
    name: ProcessType
    aliases: list[str] = []                  # RU/EN — синонимы для NLP-резолва
    description: str = ""


PROCESS_SEED: dict[str, ProcessDef] = {
    d.name.value: d for d in [
        ProcessDef(name=ProcessType.DESALINATION,
                   aliases=["обессоливание", "опреснение", "desalination", "деминерализация"]),
        ProcessDef(name=ProcessType.WATER_TREATMENT,
                   aliases=["водоподготовка", "очистка воды", "water treatment"]),
        ProcessDef(name=ProcessType.ELECTROEXTRACTION,
                   aliases=["электроэкстракция", "электролиз", "electrowinning",
                            "циркуляция католита"]),
        ProcessDef(name=ProcessType.INJECTION,
                   aliases=["закачка", "закачка шахтных вод", "обратная закачка", "injection"]),
        ProcessDef(name=ProcessType.SMELTING, aliases=["плавка", "smelting"]),
        ProcessDef(name=ProcessType.CONVERTING, aliases=["конвертирование", "converting"]),
        ProcessDef(name=ProcessType.FLOTATION, aliases=["флотация", "flotation"]),
        ProcessDef(name=ProcessType.LEACHING, aliases=["выщелачивание", "leaching"]),
        ProcessDef(name=ProcessType.CHLORINATION,
                   aliases=["хлорирование", "гидрохлорирование", "chlorination"]),
        ProcessDef(name=ProcessType.PRECIPITATION,
                   aliases=["осаждение", "соосаждение", "precipitation"]),
        ProcessDef(name=ProcessType.FIRE_REFINING,
                   aliases=["огневое рафинирование", "fire refining"]),
        ProcessDef(name=ProcessType.CASTING, aliases=["литьё", "литье", "розлив", "casting"]),
        ProcessDef(name=ProcessType.ANNEALING, aliases=["отжиг", "annealing"]),
        ProcessDef(name=ProcessType.EXTRUSION, aliases=["экструзия", "extrusion"]),
    ]
}


class ExtractorKind(str, Enum):
    STRUCTURED_ETL = "structured_etl"   # каталоги/справочники XLSX/CSV — золотой каркас
    NUEXTRACT = "nuextract_v3"
    LANGEXTRACT = "langextract"
    LLM = "llm_v1"                      # универсальная LLM по строгой схеме
    SPACY = "spacy_ruler"
    MOCK = "mock_rule"                  # правило-основанный (dev/базовый уровень)
    MANUAL = "manual"


# ──────────────────────────────────────────────────────────────────────────
# 3. Провенанс — обязателен ВЕЗДЕ; переживает structured и unstructured.
# ──────────────────────────────────────────────────────────────────────────

class LocatorKind(str, Enum):
    XLSX_ROW = "xlsx_row"     # "sheet:Каталог:r42"
    CSV_ROW = "csv_row"       # "r42"
    PDF_CHAR = "pdf_char"     # "char:1024-1090" — в MD-артефакте Marker'а
    PDF_TABLE = "pdf_table"   # "table3:r12:c4"
    PDF_PAGE = "pdf_page"     # "p7" — fallback, если relocate не нашёл спан
    DOCX_PARA = "docx_para"   # "para:118"
    MD_PARA = "md_para"       # "para:12" — OKF raw markdown (выход docling)
    FIGURE = "figure"         # "fig2:a"


class Provenance(BaseModel):
    """Инвариант: узел/ребро без снippet отбраковывается.
    Для STRUCTURED_ETL snippet = автосериализованная строка каталога
    (детерминированно, не подделка) — «золотой каркас» проходит контракт.
    Для текстовых экстракторов snippet = ДОСЛОВНЫЙ фрагмент; char-спан НЕ
    берём у LLM, а вычисляем сами relocate-поиском snippet по MD-артефакту
    (rapidfuzz, score>=90; иначе locator_kind=pdf_page + needs_review)."""
    doc_id: str
    locator_kind: LocatorKind
    locator: str
    snippet: str
    extractor: ExtractorKind
    confidence: float = Field(ge=0.0, le=1.0)
    artifact_sha256: Optional[str] = None   # хэш MD-артефакта, к которому привязан спан
    ingested_at: Optional[_dt.datetime] = None  # «дата актуализации»;
                                                # проставляется пайплайном автоматически

    @model_validator(mode="after")
    def _span_required(self):
        if not self.snippet or not self.snippet.strip():
            raise ValueError("Provenance без snippet запрещён — узел отбраковывается")
        return self


# ──────────────────────────────────────────────────────────────────────────
# 4. Значения и составы: диапазоны first-class (марка = диапазон состава,
#    hero-запрос = диапазон температур).
# ──────────────────────────────────────────────────────────────────────────

class ValueRange(BaseModel):
    """min/nominal/max — хотя бы одно. Точечное значение = nominal."""
    min: Optional[float] = None
    nominal: Optional[float] = None
    max: Optional[float] = None

    @model_validator(mode="after")
    def _any(self):
        if self.min is None and self.nominal is None and self.max is None:
            raise ValueError("пустой ValueRange")
        return self

    @property
    def point(self) -> float:
        if self.nominal is not None:
            return self.nominal
        if self.min is not None and self.max is not None:
            return (self.min + self.max) / 2
        return self.min if self.min is not None else self.max  # type: ignore


class Composition(BaseModel):
    """basis ОДИН на весь состав."""
    basis: CompositionBasis = CompositionBasis.WT_PCT
    elements: dict[str, ValueRange] = {}     # {"Zn": {nominal: 0.4}, "Cr": {min:17,max:19}}
    balance_element: Optional[str] = None    # "Mg" в «Mg-0.4Zn»: Mg — остальное


class Material(BaseModel):
    canonical_id: str
    pref_label: str
    family: str = "other"                    # alloy|steel|matte|slag|concentrate|...
    grade: Optional[str] = None              # марка (12Х18Н10Т)
    composition: Optional[Composition] = None
    phase: Optional[str] = None
    external_ids: dict[str, str] = {}        # {"pubchem_cid","gost","uns","aisi","wikidata_qid"}
    provenance: Provenance
    # алиасы НЕ здесь: единый механизм ER = таблицы entity_aliases + entity_same_as (V3)


# ──────────────────────────────────────────────────────────────────────────
# 5. Режим обработки: многостадийный, SI в БД (K/Pa/s), бакеты для gap-map.
# ──────────────────────────────────────────────────────────────────────────

REGIME_BUCKETS_K = {"low": (None, 673.15), "medium": (673.15, 1073.15), "high": (1073.15, None)}
# = regime_buckets.yaml SPEC_V3: low <400°C, medium 400–800°C, high >800°C


class RegimeStep(BaseModel):
    process_type: ProcessType
    temperature: Optional[ValueRange] = None   # Kelvin (в UI показываем °C)
    duration_s: Optional[float] = None
    pressure_pa: Optional[float] = None
    atmosphere: Optional[str] = None
    extra: dict = {}                           # pH, флюс, дутьё, скорость охлаждения...


class Regime(BaseModel):
    """Упорядоченная цепочка стадий: закалка+двухступенчатое старение (Inconel),
    «extruded + nital-pickled» (Mg-Zn из parsed.json) — одна запись."""
    steps: list[RegimeStep] = Field(min_length=1)

    @property
    def max_temperature_k(self) -> Optional[float]:
        ts = [s.temperature.point for s in self.steps if s.temperature is not None]
        return max(ts) if ts else None

    @property
    def bucket(self) -> Optional[str]:
        t = self.max_temperature_k
        if t is None:
            return None
        for name, (lo, hi) in REGIME_BUCKETS_K.items():
            if (lo is None or t >= lo) and (hi is None or t < hi):
                return name
        return None

    def state_hash(self) -> str:
        """processing_state для Comparability Gate: литое vs деформированное
        vs экструдированное — разные состояния одного материала."""
        return "+".join(s.process_type.value for s in self.steps)


# ──────────────────────────────────────────────────────────────────────────
# 6. Эксперимент-хаб: БЕЗ массивов *_ids[] (связи живут в edges-проекции,
#    единственный источник истины — нормализованные таблицы).
# ──────────────────────────────────────────────────────────────────────────

class Experiment(BaseModel):
    id: str
    date: Optional[_dt.date] = None          # таймлайн = дешёвая «история решений»
    origin: Literal["catalog", "extracted"] = "extracted"   # золотой каркас vs LLM
    regime: Regime
    equipment_id: Optional[str] = None
    team_id: Optional[str] = None            # -> TeamLab (география через performed_by)
    site: Optional[str] = None               # площадка/объект, если названа в тексте
    document_id: Optional[str] = None
    tags: list[str] = []
    provenance: Provenance


class MeasurementConditions(BaseModel):
    """Условия ИЗМЕРЕНИЯ (не обработки!): σ₀.₂ при 20 °C и при 650 °C —
    разные числа; ползучесть определена только при T испытания; HV30 —
    нагрузка. Шестая ось Comparability Gate."""
    temperature_k: Optional[float] = None
    load: Optional[str] = None               # "30 kgf" (HV30)
    strain_rate: Optional[float] = None
    medium: Optional[str] = None             # электролит/среда испытания
    extra: dict = {}


class Measurement(BaseModel):
    """≡ таблица results SPEC_V3 + material_id. Реифицированное n-арное
    отношение: значение × шкала × basis × метод × неопределённость × условия.

    scope. Свойство образца (σ₀.₂, твёрдость) -> scope='material',
    material_id ОБЯЗАТЕЛЕН (гарантия «чьё это σ₀.₂» сохранена жёстко).
    ТЭП (энергоёмкость, себестоимость, выход по току) характеризует
    эксперимент/процесс -> scope='experiment', material_id пуст; к методу
    агрегируется транзитивно через applies_process."""
    id: str
    experiment_id: str                       # ОБЯЗАТЕЛЬНО (только -> Experiment)
    scope: Literal["material", "experiment"] = "material"
    material_id: Optional[str] = None        # обязателен при scope='material'
    quantity_kind: str                       # из QuantityKindRegistry
    value: Optional[ValueRange] = None       # число или диапазон
    unit: str = ""
    scale: HardnessScale = HardnessScale.NONE
    basis: Optional[CompositionBasis] = None
    uncertainty: Optional[dict] = None       # {"sd": 7.0, "n": 5}
    conditions: MeasurementConditions = MeasurementConditions()
    sample_state: Optional[str] = None       # as_cast|wrought|extruded|... (вместо класса Sample)
    method: Optional[str] = None
    superseded_by: Optional[str] = None      # append-only версионирование фактов
    provenance: Provenance

    @model_validator(mode="after")
    def _scope_material(self):
        if (self.scope == "material") != (self.material_id is not None):
            raise ValueError("scope='material' требует material_id (и наоборот)")
        return self


class Direction(str, Enum):
    INCREASES = "increases"
    DECREASES = "decreases"
    NO_CHANGE = "no_change"
    NONMONOTONIC = "nonmonotonic"


class Effect(BaseModel):
    """Структурный «эффект на свойство Z» из ТЗ. Противоречие = два Effect
    с одинаковыми (материал, factor, quantity_kind), прошедшие Gate, с
    противоположным direction — SQL-запрос, а не LLM-мнение."""
    quantity_kind: str
    direction: Direction
    factor: str                              # что варьировали: "содержание Zn", "T отжига"
    baseline_ref: Optional[str] = None       # experiment_id базы сравнения («до/после»)
    optimum: Optional[ValueRange] = None     # рекомендованный оптимум (диапазон значений
    optimum_unit: str = ""                   #     диапазон значений factor'а


class Conclusion(BaseModel):
    """Вывод: риторика автора (text) + структурный эффект (effect), раздельно."""
    id: str
    text: str
    kind: Literal["finding", "recommendation"] = "finding"   # вывод или рекомендация
    effect: Optional[Effect] = None
    superseded_by: Optional[str] = None      # append-only версионирование фактов
    provenance: Provenance


class Document(BaseModel):
    doc_id: str
    title: str
    doc_type: Literal["article", "internal_report", "catalog", "handbook", "patent"] = "article"
    year: Optional[int] = None
    country: Optional[str] = None            # ISO-код; отечественная/зарубежная практика
    lang: Optional[Literal["ru", "en"]] = None  # автодетект кириллицы на ингесте
    source_path: Optional[str] = None


class TeamLab(BaseModel):
    """Носитель географии и экспертизы.
    Грузится детерминированно из «перечня сотрудников и лабораторий с областями
    экспертизы» (даётся в данных кейса). Экспертизу НЕ выдумывать руками:
    derived-экспертиза = топ topics/tags экспериментов лаборатории."""
    id: str
    name: str
    kind: Literal["lab", "person", "team"] = "lab"
    parent_id: Optional[str] = None          # person -> lab
    country: Optional[str] = None            # ISO-код ("RU")
    city: Optional[str] = None
    expertise: list[str] = []                # из справочника + derived из tagged_as


class Topic(BaseModel):
    """Тег с иерархией — «таксономия тематических тегов» из данных кейса
    грузится как есть (parent_id), теги на экспериментах остаются денорм-кэшем."""
    id: str
    label: str
    parent_id: Optional[str] = None


class Edge(BaseModel):
    """Ребро ленивой граф-проекции. В БД это VIEW поверх FK нормализованных
    таблиц (build_flat), НЕ отдельно наполняемая таблица — двойная
    бухгалтерия устранена. Прямо в таблицу пишутся только семантические
    рёбра (derived_from, supports/contradicts, similar_conditions)."""
    src: str
    dst: str
    predicate: Predicate
    attrs: dict = {}                         # {"role": MaterialRole, "process_id": ...}
    weight: Optional[float] = None
    provenance: Provenance


# ──────────────────────────────────────────────────────────────────────────
# 7. Comparability Gate v2 — ШЕСТЬ осей (было четыре с половиной).
# ──────────────────────────────────────────────────────────────────────────

COMPARABILITY_AXES = (
    "quantity_kind",            # предел текучести ≠ твёрдость
    "scale",                    # HV30 ≠ HRC ≠ HB (ASTM E140)
    "basis",                    # wt% ≠ at% ≠ mol%
    "unit_dim",                 # MPa сравнимо с GPa (Pint), но не с μm
    "processing_state",         # литое ≠ деформированное ≠ экструдированное
    "measurement_conditions",   # σ₀.₂(20 °C) ≠ σ₀.₂(650 °C); HV30 ≠ HV10
)

_UNIT_DIM = {
    "MPa": "pressure", "GPa": "pressure", "Pa": "pressure", "kPa": "pressure",
    "°C": "temperature", "K": "temperature",
    "%": "ratio", "min": "time", "h": "time", "s": "time",
    "μm": "length", "um": "length", "nm": "length", "mm": "length",
    "V": "voltage", "mV": "voltage", "A/cm2": "current_density",
}


def _unit_dim(unit: str) -> str:
    """В проде — pint.Unit(unit).dimensionality; Pint ТОЛЬКО для истинно
    размерных величин, шкальные (твёрдость) не конвертируются никогда."""
    return _UNIT_DIM.get(unit.strip(), unit.strip().lower())


def _t_bucket(t_k: Optional[float]) -> str:
    """Комнатная vs повышенная T испытания; None трактуем как комнатную."""
    if t_k is None or t_k < 323.15:
        return "room"
    return f">{int((t_k - 273.15) // 100) * 100}C"


class Comparability(BaseModel):
    comparable: bool
    blocking_dims: list[str] = []
    note: str = ""


def is_comparable(a: Measurement, b: Measurement,
                  regime_a: Optional[Regime] = None,
                  regime_b: Optional[Regime] = None) -> Comparability:
    """Вызывается ПЕРЕД детектором противоречий, similarity и gap-map.
    «Несопоставимо по осям [...]» — это тоже ответ, и сильный."""
    blocking: list[str] = []
    if a.quantity_kind != b.quantity_kind:
        blocking.append("quantity_kind")
    if a.scale != b.scale:
        blocking.append("scale")
    if a.basis != b.basis:
        blocking.append("basis")
    if _unit_dim(a.unit) != _unit_dim(b.unit):
        blocking.append("unit_dim")
    state_a = regime_a.state_hash() if regime_a else a.sample_state
    state_b = regime_b.state_hash() if regime_b else b.sample_state
    if state_a != state_b:
        blocking.append("processing_state")
    if (_t_bucket(a.conditions.temperature_k) != _t_bucket(b.conditions.temperature_k)
            or a.conditions.load != b.conditions.load):
        blocking.append("measurement_conditions")
    return Comparability(
        comparable=not blocking, blocking_dims=blocking,
        note="OK" if not blocking else f"несопоставимо по осям {blocking}")


# ──────────────────────────────────────────────────────────────────────────
# 8. Интерпретаторы (выходы поверх субстрата) — контракты ответов.
# ──────────────────────────────────────────────────────────────────────────

AgreementFlag = Literal["consistent", "contradictory", "single", "incomparable"]
Confidence = Literal["high", "medium", "low"]
Coverage = Literal["sufficient", "weak", "none", "conflicting"]


class Evidence(BaseModel):
    """Hero-ответ на «X / Y / Z». Надёжность через подсчёт — видимые числа."""
    answer: str
    experiments: list[str]
    n_experiments: int = 0
    n_docs: int = 0
    labs: list[str] = []
    regime_range: Optional[str] = None
    effect: Optional[Effect] = None
    confidence: Confidence = "medium"
    agreement_flag: AgreementFlag = "single"
    citations: list[Provenance] = []
    gap_note: Optional[str] = None


class GapCell(BaseModel):
    material_id: str
    regime_bucket: Optional[str] = None      # low|medium|high — ось heatmap V3
    quantity_kind: str
    coverage: Coverage
    n_experiments: int = 0


class ContradictionFlag(BaseModel):
    """Показывается за 10 секунд: Лаб А 229 МПа vs Лаб Б 207 МПа, Δ=10%."""
    a_measurement: str
    b_measurement: str
    a_span: Provenance
    b_span: Provenance
    delta_value: Optional[float] = None
    labs: list[str] = []
    comparability: Comparability             # фиксируем легитимность сравнения


class NextExperiment(BaseModel):
    material_id: str
    suggested_regime: Regime
    quantity_kind: str
    rationale: str                           # со ссылками-спанами на соседей
    score: float


# ──────────────────────────────────────────────────────────────────────────
# 9. Маппинги: (а) на схему SPEC_V3 (один контракт, не два);
#    (б) на стандарты PROV-O/PMDco/QUDT (URI-alignment, не импорт).
# ──────────────────────────────────────────────────────────────────────────

SPEC_V3_MAPPING = {
    # онтология                  ->  SPEC_V3 (schema experiments.*) / Neo4j-проекция
    "Material":                    "materials (+ external_ids JSONB — добавить колонку)",
    "Experiment":                  "experiments (+ date, origin — добавить колонки)",
    "Measurement":                 "results (+ material_id, conditions JSONB, scale, basis, sample_state)",
    "Regime":                      "regimes (temperature K, pressure Pa, duration s, steps JSONB)",
    "Conclusion":                  "колонка description/conclusion -> отдельная таблица conclusions (+effect JSONB)",
    "Document":                    "documents",
    "Provenance":                  "proof_ref/source_page/source_paragraph -> prov JSONB",
    "canonical_for":               "entity_same_as (confidence, method)",
    "алиасы":                      "entity_aliases",
    Predicate.USES_MATERIAL:       ":USED_IN (реверс) + attrs.role",
    Predicate.APPLIES_PROCESS:     ":UNDER_REGIME",
    Predicate.HAS_PROPERTY:        ":MEASURES",
    Predicate.MEASURED_ON:         ":HAS_RESULT (реверс)",
    Predicate.PERFORMED_BY:        ":PERFORMED_AT / :BY",
    Predicate.RUN_ON_EQUIPMENT:    ":ON_EQUIPMENT",
    Predicate.REPORTED_IN:         ":SOURCED_FROM",
    Predicate.CONCLUDES:           ":LEADS_TO",
    Predicate.SIMILAR_CONDITIONS:  ":RELATED_TO (+weight)",
}

# Требуемые ТЗ отношения -> наши предикаты.
TZ_PREDICATE_ALIGNMENT = {
    "uses_material":         "uses_material (дословно; + attrs.role)",
    "produces_output":       "uses_material c role=output",
    "operates_at_condition": "applies_process + Regime.steps (диапазоны, SI)",
    "described_in":          "reported_in",
    "validated_by":          "supports (+ reported_in + provenance.confidence)",
    "contradicts":           "contradicts (дословно; строже ТЗ — только через Comparability Gate)",
}

PROV_O = {
    "fact": "http://www.w3.org/ns/prov#Entity",
    "extraction": "http://www.w3.org/ns/prov#Activity",
    "extractor": "http://www.w3.org/ns/prov#Agent",
}

PMDCO_ALIGNMENT = {
    "Material": "https://w3id.org/pmd/co/Object",
    "Process": "https://w3id.org/pmd/co/Process",
    "Property": "https://w3id.org/pmd/co/ValueObject",
    "Measurement": "https://w3id.org/pmd/co/Value",
    Predicate.PERFORMED_BY: "http://www.w3.org/ns/prov#wasAssociatedWith",
    Predicate.DERIVED_FROM: "http://www.w3.org/ns/prov#wasDerivedFrom",
}

QUDT_UNITS = {
    "MPa": "https://qudt.org/vocab/unit/MegaPA",
    "GPa": "https://qudt.org/vocab/unit/GigaPA",
    "°C": "https://qudt.org/vocab/unit/DEG_C",
    "K": "https://qudt.org/vocab/unit/K",
    "%": "https://qudt.org/vocab/unit/PERCENT",
    "μm": "https://qudt.org/vocab/unit/MicroM",
    # HV/HRC/HB сознательно БЕЗ конверсии (ASTM E140) — distinct scales.
}


if __name__ == "__main__":
    # smoke: HV30 vs HRC -> несопоставимо [scale]; σ0.2(20°C) vs σ0.2(650°C) -> [conditions]
    p = Provenance(doc_id="d1", locator_kind=LocatorKind.PDF_TABLE, locator="table3:r1:c2",
                   snippet="твёрдость составила 42 HRC", extractor=ExtractorKind.NUEXTRACT,
                   confidence=0.9)
    m1 = Measurement(id="m1", experiment_id="e1", material_id="mat1",
                     quantity_kind="hardness", value=ValueRange(nominal=42), unit="HRC",
                     scale=HardnessScale.HRC, provenance=p)
    m2 = Measurement(id="m2", experiment_id="e2", material_id="mat1",
                     quantity_kind="hardness", value=ValueRange(nominal=420), unit="HV30",
                     scale=HardnessScale.HV30, provenance=p)
    print("HRC vs HV30:", is_comparable(m1, m2).note)
    m3 = Measurement(id="m3", experiment_id="e3", material_id="mat1",
                     quantity_kind="yield_strength", value=ValueRange(nominal=229), unit="MPa",
                     conditions=MeasurementConditions(temperature_k=293), provenance=p)
    m4 = m3.model_copy(update={"id": "m4", "conditions": MeasurementConditions(temperature_k=923)})
    print("σ0.2(20°C) vs σ0.2(650°C):", is_comparable(m3, m4).note)
    reg = QuantityKindRegistry()
    print("resolve('0.2 % proof stress') ->", reg.resolve("0.2 % proof stress"))
    print("resolve('оптическая плотность шлака') ->", reg.resolve("оптическая плотность шлака"),
          "| pending_review:", reg.pending_review)
