# -*- coding: utf-8 -*-
"""
ExtractionBatch — формат обмена «экстрактор → загрузчик».

Это единственный вход в БД онтологии: и LLM-экстрактор, и детерминированный
ETL каталогов, и ручная разметка эмитят батчи этой формы (JSON-файл или dict).
Формат совпадает с seed/norilsk_pgm.json.

Валидация здесь лёгкая (структура); жёсткие инварианты (обязательный snippet,
scope↔material_id) применяет loader через contracts.Provenance/Measurement и
CHECK-и Postgres.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .contracts import ValueRange


class BatchDocument(BaseModel):
    doc_id: str
    title: str
    doc_type: str = "internal_report"      # article|internal_report|catalog|handbook|patent
    year: Optional[int] = None
    country: Optional[str] = None
    lang: Optional[str] = None
    source_path: Optional[str] = None
    artifact_sha256: Optional[str] = None
    okf_raw_path: Optional[str] = None     # путь к OKF raw markdown (реестр ingest-контура) → wiki


class BatchLab(BaseModel):
    id: str
    name: str
    kind: str = "lab"                      # lab|person|team
    parent_id: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    expertise: list[str] = []


class BatchMaterial(BaseModel):
    id: str
    label: str
    family: str = "other"
    grade: Optional[str] = None
    phase: Optional[str] = None
    composition: Optional[dict] = None     # contracts.Composition как dict


class BatchRegimeStep(BaseModel):
    process_type: str
    temperature: Optional[ValueRange] = None   # Kelvin
    duration_s: Optional[float] = None
    pressure_pa: Optional[float] = None
    atmosphere: Optional[str] = None
    extra: dict = {}


class BatchMaterialUse(BaseModel):
    material_id: str
    role: str = "sample"                   # sample|input|output|medium|flux|atmosphere|reference


class BatchMeasurement(BaseModel):
    quantity_kind: str                     # сырое имя — резолвится реестром
    scope: str = "material"                # material|experiment
    material_id: Optional[str] = None
    value: Optional[ValueRange] = None
    unit: str = ""
    scale: str = "none"
    basis: Optional[str] = None
    uncertainty: Optional[dict] = None
    conditions: dict = {}
    method: Optional[str] = None
    snippet: str                           # дословная цитата — ОБЯЗАТЕЛЬНА
    locator_kind: str = "docx_para"
    locator: str = "para:auto"
    confidence: float = 0.9


class BatchEffect(BaseModel):
    quantity_kind: str
    direction: str                         # increases|decreases|no_change|nonmonotonic
    factor: str
    baseline_ref: Optional[str] = None
    optimum: Optional[ValueRange] = None
    optimum_unit: str = ""


class BatchConclusion(BaseModel):
    text: str
    kind: str = "finding"                  # finding|recommendation
    effect: Optional[BatchEffect] = None
    snippet: str
    locator_kind: str = "docx_para"
    locator: str = "para:auto"
    confidence: float = 0.9


class BatchExperiment(BaseModel):
    id: str
    document_id: str
    title: Optional[str] = None
    date: Optional[str] = None             # ISO YYYY-MM-DD
    lab_id: Optional[str] = None
    equipment_id: Optional[str] = None
    site: Optional[str] = None
    tags: list[str] = []
    regime: dict = Field(default_factory=lambda: {"steps": []})   # {steps: [BatchRegimeStep]}
    materials: list[BatchMaterialUse] = []
    measurements: list[BatchMeasurement] = []
    conclusions: list[BatchConclusion] = []
    snippet: str = ""
    locator_kind: str = "docx_para"
    locator: str = "para:auto"
    confidence: float = 0.9


class BatchDocumentClaim(BatchConclusion):
    """Инженерное утверждение уровня документа — БЕЗ эксперимента (обзоры,
    доклады, опыт эксплуатации): «метод X применим при условиях Y, ТЭП Z»."""
    document_id: str
    process: Optional[str] = None          # метод, о котором утверждение (канон реестра)


class BatchSemanticEdge(BaseModel):
    """lineage: derived_from(+process); validated_by → supports(+kind);
    contradicts/refines/supports между выводами."""
    src: str
    dst: str
    process: Optional[str] = None
    kind: Optional[str] = None
    snippet: str = ""
    doc_id: Optional[str] = None


class BatchEquipment(BaseModel):
    id: str
    name: str
    equipment_type: Optional[str] = None
    lab_id: Optional[str] = None


class BatchTopic(BaseModel):
    id: str
    label: str
    parent_id: Optional[str] = None


class ExtractionBatch(BaseModel):
    """Полный батч. Все ссылки (document_id, material_id, lab_id) — строковые
    внешние id; loader детерминированно превращает их в UUID (uuid5)."""
    extractor: str = "nuextract_v3"        # contracts.ExtractorKind
    documents: list[BatchDocument] = []
    labs: list[BatchLab] = []
    equipment: list[BatchEquipment] = []
    topics: list[BatchTopic] = []
    materials: list[BatchMaterial] = []
    experiments: list[BatchExperiment] = []
    claims: list[BatchDocumentClaim] = []
    lineage: list[BatchSemanticEdge] = []
    validated_by: list[BatchSemanticEdge] = []
    contradicts: list[BatchSemanticEdge] = []
