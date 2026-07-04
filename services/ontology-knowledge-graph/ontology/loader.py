# -*- coding: utf-8 -*-
"""
Загрузчик: ExtractionBatch → Postgres (schema experiments.*).

Гарантии:
  - Идемпотентность: внешние строковые id (doc:..., mat:...) детерминированно
    отображаются в UUID (uuid5) → повторная загрузка того же батча не создаёт
    дублей (ON CONFLICT DO NOTHING по PK).
  - Инвариант провенанса: узел/ребро без snippet не проходит (валидатор
    contracts.Provenance + CHECK в БД).
  - Неизвестный род величины не теряется: регистрируется в quantity_kinds со
    status='needs_review' (очередь HITL).

CLI:  python -m ontology.loader <batch.json> [--reset]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .batch import ExtractionBatch
from .contracts import (
    PROCESS_SEED, QUANTITY_KINDS_SEED, Provenance, ExtractorKind, LocatorKind,
)
from .store import Store

NS = uuid.uuid5(uuid.NAMESPACE_URL, "metalcrow/ontology")


def ext_uuid(ext_id: str) -> str:
    """Детерминированный UUID из внешнего строкового id."""
    return str(uuid.uuid5(NS, ext_id))


@dataclass
class LoadReport:
    counts: dict = field(default_factory=dict)
    hitl_quantity_kinds: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    def add(self, table: str, n: int = 1) -> None:
        self.counts[table] = self.counts.get(table, 0) + n


# ── реестры ──────────────────────────────────────────────────────────────

def seed_registries(store: Store) -> None:
    """Сиды реестров величин и процессов из contracts.py (идемпотентно)."""
    store.executemany(
        "INSERT INTO experiments.quantity_kinds(name, unit_dim, aliases, status)"
        " VALUES (%s,%s,%s,'seed') ON CONFLICT (name) DO NOTHING",
        [(d.name, d.unit_dim, d.aliases) for d in QUANTITY_KINDS_SEED.values()])
    store.executemany(
        "INSERT INTO experiments.process_types(name, aliases, description, status)"
        " VALUES (%s,%s,%s,'seed') ON CONFLICT (name) DO NOTHING",
        [(d.name.value, d.aliases, d.description) for d in PROCESS_SEED.values()])
    store.commit()


def resolve_quantity_kind(store: Store, raw: str, report: LoadReport) -> str:
    """Имя/алиас → канон (реестр, затем канонизатор); неизвестное —
    авторегистрация в needs_review (HITL)."""
    key = raw.strip().lower()
    row = store.query(
        "SELECT name FROM experiments.quantity_kinds"
        " WHERE lower(name)=%s OR %s ILIKE ANY(SELECT lower(unnest(aliases)))"
        " LIMIT 1", (key, key))
    if row:
        return row[0]["name"]
    from .extract.quantities import ALL_KINDS, canonize
    c = canonize(raw)
    if c.kind:
        d = ALL_KINDS[c.kind]
        store.execute(
            "INSERT INTO experiments.quantity_kinds(name, unit_dim, aliases, status)"
            " VALUES (%s,%s,%s,'seed') ON CONFLICT (name) DO NOTHING",
            (d.name, d.unit_dim, d.aliases))
        return c.kind
    name = key.replace(" ", "_").replace("%", "pct")[:64]
    store.execute(
        "INSERT INTO experiments.quantity_kinds(name, unit_dim, aliases, status)"
        " VALUES (%s,'unknown',%s,'needs_review') ON CONFLICT (name) DO NOTHING",
        (name, [raw]))
    report.hitl_quantity_kinds.append(name)
    return name


# ── провенанс ────────────────────────────────────────────────────────────

def _prov_json(store: Store, doc_ext: str, snippet: str, locator_kind: str,
               locator: str, extractor: str, confidence: float) -> str:
    p = Provenance(
        doc_id=doc_ext, locator_kind=LocatorKind(locator_kind), locator=locator,
        snippet=snippet, extractor=ExtractorKind(extractor),
        confidence=confidence, ingested_at=_dt.datetime.now(_dt.timezone.utc))
    return store.jsondump(json.loads(p.model_dump_json()))


# ── основная загрузка ────────────────────────────────────────────────────

def load_batch(store: Store, batch: ExtractionBatch | dict) -> LoadReport:
    if isinstance(batch, dict):
        batch = ExtractionBatch.model_validate(
            {k: v for k, v in batch.items() if not k.startswith("_")})
    rep = LoadReport()
    ext = batch.extractor

    for d in batch.documents:
        store.execute(
            "INSERT INTO experiments.documents(id, minio_key, filename, doc_type,"
            " year, country, lang, artifact_sha256)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
            (ext_uuid(d.doc_id), d.source_path or d.doc_id,
             Path(d.source_path).name if d.source_path else d.title[:120],
             d.doc_type, d.year, d.country, d.lang, d.artifact_sha256))
        _alias(store, "document", d.doc_id, d.title)
        rep.add("documents")

    for l in batch.labs:
        store.execute(
            "INSERT INTO experiments.labs(id, name, kind, parent_id, country, city, expertise)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
            (ext_uuid(l.id), l.name, l.kind,
             ext_uuid(l.parent_id) if l.parent_id else None,
             l.country, l.city, l.expertise))
        _alias(store, "lab", l.id, l.name)
        rep.add("labs")

    for eq in batch.equipment:
        store.execute(
            "INSERT INTO experiments.equipment(id, name, equipment_type, lab_id)"
            " VALUES (%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
            (ext_uuid(eq.id), eq.name, eq.equipment_type,
             ext_uuid(eq.lab_id) if eq.lab_id else None))
        rep.add("equipment")

    for t in batch.topics:
        store.execute(
            "INSERT INTO experiments.topics(id, label, parent_id)"
            " VALUES (%s,%s,%s) ON CONFLICT (id) DO NOTHING",
            (ext_uuid(t.id), t.label, ext_uuid(t.parent_id) if t.parent_id else None))
        rep.add("topics")

    doc_of_mat = _material_doc_hints(batch)
    for m in batch.materials:
        prov = _prov_json(store, doc_of_mat.get(m.id, "handbook"),
                          f"{m.label} ({m.family})", "xlsx_row", "row:auto",
                          "structured_etl", 1.0)
        store.execute(
            "INSERT INTO experiments.materials(id, name, family, grade, phase,"
            " composition, prov) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (id) DO NOTHING",
            (ext_uuid(m.id), m.label, m.family, m.grade, m.phase,
             store.jsondump(m.composition) if m.composition else None, prov))
        _alias(store, "material", m.id, m.label)
        rep.add("materials")

    for e in batch.experiments:
        prov = _prov_json(store, e.document_id, e.snippet or e.title or e.id,
                          e.locator_kind, e.locator, ext, e.confidence)
        regime_id = _insert_regime(store, e)
        store.execute(
            "INSERT INTO experiments.experiments(id, title, date, origin, regime_id,"
            " equipment_id, lab_id, site, document_id, tags, prov)"
            " VALUES (%s,%s,%s,'extracted',%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (id) DO NOTHING",
            (ext_uuid(e.id), e.title, e.date, regime_id,
             ext_uuid(e.equipment_id) if e.equipment_id else None,
             ext_uuid(e.lab_id) if e.lab_id else None,
             e.site, ext_uuid(e.document_id), e.tags, prov))
        rep.add("experiments")

        for mu in e.materials:
            store.execute(
                "INSERT INTO experiments.experiment_materials(experiment_id,"
                " material_id, role, prov) VALUES (%s,%s,%s,%s)"
                " ON CONFLICT (experiment_id, material_id, role) DO NOTHING",
                (ext_uuid(e.id), ext_uuid(mu.material_id), mu.role, prov))
            rep.add("experiment_materials")

        for i, ms in enumerate(e.measurements):
            try:
                mprov = _prov_json(store, e.document_id, ms.snippet,
                                   ms.locator_kind, ms.locator, ext, ms.confidence)
            except Exception as err:                    # snippet пуст → отбраковка
                rep.rejected.append(f"{e.id}:m{i}: {err}")
                continue
            qk = resolve_quantity_kind(store, ms.quantity_kind, rep)
            v = ms.value
            unit = "" if (ms.unit or "").lower() in ("null", "none", "-", "—", "n/a") \
                else ms.unit
            store.execute(
                "INSERT INTO experiments.results(id, experiment_id, scope, material_id,"
                " quantity_kind, value_min, value_nominal, value_max, unit, scale,"
                " basis, uncertainty, conditions, sample_state, method, prov)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (id) DO NOTHING",
                (ext_uuid(f"{e.id}:m{i}"), ext_uuid(e.id), ms.scope,
                 ext_uuid(ms.material_id) if ms.material_id else None, qk,
                 v.min if v else None, v.nominal if v else None, v.max if v else None,
                 unit, ms.scale, ms.basis,
                 store.jsondump(ms.uncertainty) if ms.uncertainty else None,
                 store.jsondump(ms.conditions), _state_hash(e), ms.method, mprov))
            rep.add("results")

        for i, c in enumerate(e.conclusions):
            try:
                cprov = _prov_json(store, e.document_id, c.snippet,
                                   c.locator_kind, c.locator, ext, c.confidence)
            except Exception as err:
                rep.rejected.append(f"{e.id}:c{i}: {err}")
                continue
            store.execute(
                "INSERT INTO experiments.conclusions(id, experiment_id, text, kind,"
                " effect, prov) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                (ext_uuid(f"{e.id}:c{i}"), ext_uuid(e.id), c.text, c.kind,
                 store.jsondump(c.effect.model_dump()) if c.effect else None, cprov))
            rep.add("conclusions")

    for i, c in enumerate(batch.claims):
        try:
            cprov = _prov_json(store, c.document_id, c.snippet,
                               c.locator_kind, c.locator, ext, c.confidence)
        except Exception as err:
            rep.rejected.append(f"claim:{i}: {err}")
            continue
        store.execute(
            "INSERT INTO experiments.conclusions(id, document_id, process_type,"
            " text, kind, effect, prov) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (id) DO NOTHING",
            (ext_uuid(f"{c.document_id}:claim{i}"), ext_uuid(c.document_id),
             c.process, c.text, c.kind,
             store.jsondump(c.effect.model_dump()) if c.effect else None, cprov))
        rep.add("claims")

    _load_semantic(store, batch.lineage, "derived_from", ext, rep,
                   attrs_key="process")
    _load_semantic(store, batch.validated_by, "supports", ext, rep,
                   attrs_fixed={"kind": "validated_by"})
    _load_semantic(store, batch.contradicts, "contradicts", ext, rep)

    store.commit()
    return rep


# ── внутреннее ───────────────────────────────────────────────────────────

def _alias(store: Store, etype: str, ext_id: str, label: str) -> None:
    """Внешний id и человекочитаемое имя → entity_aliases (для ER и отладки)."""
    store.executemany(
        "INSERT INTO experiments.entity_aliases(entity_type, entity_id, alias, source)"
        " VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        [(etype, ext_uuid(ext_id), ext_id, "ext_id"),
         (etype, ext_uuid(ext_id), label, "label")])


def _insert_regime(store: Store, e) -> str:
    steps = e.regime.get("steps", [])
    rid = ext_uuid(f"{e.id}:regime")
    temps = []
    for s in steps:
        t = s.get("temperature")
        if t:
            temps.extend(x for x in
                         (t.get("min"), t.get("nominal"), t.get("max")) if x)
    store.execute(
        "INSERT INTO experiments.regimes(id, steps, max_temperature_k, state_hash)"
        " VALUES (%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
        (rid, store.jsondump(steps), max(temps) if temps else None, _state_hash(e)))
    return rid


def _state_hash(e) -> str:
    return "+".join(s.get("process_type", "?") for s in e.regime.get("steps", []))


def _load_semantic(store: Store, edges, predicate: str, ext: str,
                   rep: LoadReport, attrs_key: str | None = None,
                   attrs_fixed: dict | None = None) -> None:
    for ed in edges:
        attrs = dict(attrs_fixed or {})
        if attrs_key and getattr(ed, attrs_key, None):
            attrs[attrs_key] = getattr(ed, attrs_key)
        try:
            prov = _prov_json(store, ed.doc_id or "corpus", ed.snippet or "-",
                              "docx_para", "para:auto", ext, 0.9)
        except Exception as err:
            rep.rejected.append(f"edge {ed.src}->{ed.dst}: {err}")
            continue
        store.execute(
            "INSERT INTO experiments.edges_semantic(src, dst, predicate, attrs, prov)"
            " VALUES (%s,%s,%s,%s,%s) ON CONFLICT (src, dst, predicate) DO NOTHING",
            (ext_uuid(ed.src), ext_uuid(ed.dst), predicate,
             store.jsondump(attrs), prov))
        rep.add("edges_semantic")


def _material_doc_hints(batch: ExtractionBatch) -> dict[str, str]:
    """Материал → первый документ, где он использован (для провенанса)."""
    hints: dict[str, str] = {}
    for e in batch.experiments:
        for mu in e.materials:
            hints.setdefault(mu.material_id, e.document_id)
    return hints


# ── CLI ──────────────────────────────────────────────────────────────────

def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="ExtractionBatch JSON → Postgres")
    ap.add_argument("batch", type=Path)
    ap.add_argument("--reset", action="store_true", help="пересоздать схему")
    ap.add_argument("--db", default=None, help="postgresql://... (или ONTOLOGY_DB_URL)")
    args = ap.parse_args()

    store = Store.open(args.db)
    if args.reset:
        store.reset()
    seed_registries(store)
    raw = json.loads(args.batch.read_text(encoding="utf-8"))
    rep = load_batch(store, raw)
    print("загружено:", json.dumps(rep.counts, ensure_ascii=False))
    if rep.hitl_quantity_kinds:
        print("HITL (новые величины):", rep.hitl_quantity_kinds)
    if rep.rejected:
        print("отбраковано:", *rep.rejected, sep="\n  ")
    store.close()


if __name__ == "__main__":
    main()
