# -*- coding: utf-8 -*-
"""
Query-слой онтологии: интерпретаторы поверх Postgres.

Функции названы по реестру тулов агента (SPEC_V5 §7/§16.1):
  evidence            — hero-ответ «что делали по X, какой результат Z» + цитаты
  find_gaps           — пробелы: пустые ячейки, только-RU/только-зарубежное, мало источников
  find_contradictions — расхождения среди СОПОСТАВИМЫХ измерений/выводов (через Gate)
  compare_practice    — отечественная vs зарубежная практика по процессу
  compare_technologies— таблица «метод × параметр × значение × источник»
  find_experts_by_topic — лаборатории/эксперты по теме запроса
  get_subgraph        — окрестность узла для графа-визуализации
  lineage             — цепочка derived_from («история решений»)
  timeline            — кто/когда/что по материалу или процессу
  literature_review   — секции обзора: by_method/by_geo/by_year/consensus/disagreements
  coverage / risk_zones — покрытие корпуса и зоны риска

Правило: LLM здесь нет. Вход — валидируемые слоты, выход — pydantic-контракты
или плоские dict; число всегда из БД.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Optional

from . import query_expand
from .contracts import (
    Comparability, Evidence, GapCell, Provenance,
    _t_bucket, _unit_dim,
)
from .store import Store

# ── резолв пользовательского ввода в id ─────────────────────────────────

def resolve_entity(store: Store, etype: str, text: str) -> Optional[str]:
    """Текст пользователя → UUID сущности: точный алиас, затем подстрока."""
    if not text or not text.strip():
        return None
    row = store.query(
        "SELECT entity_id FROM experiments.entity_aliases"
        " WHERE entity_type=%s AND lower(alias)=lower(%s) LIMIT 1", (etype, text))
    if row:
        return str(row[0]["entity_id"])
    row = store.query(
        "SELECT entity_id FROM experiments.entity_aliases"
        " WHERE entity_type=%s AND alias ILIKE %s ORDER BY length(alias) LIMIT 1",
        (etype, f"%{text}%"))
    return str(row[0]["entity_id"]) if row else None


def resolve_process(store: Store, text: str) -> Optional[str]:
    if not text or not text.strip():
        return None
    row = store.query(
        "SELECT name FROM experiments.process_types WHERE lower(name)=lower(%s)"
        " OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE lower(a)=lower(%s))"
        " OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE a ILIKE %s) LIMIT 1",
        (text, text, f"%{text}%"))
    return row[0]["name"] if row else None


def resolve_quantity(store: Store, text: str) -> Optional[str]:
    if not text or not text.strip():
        return None
    row = store.query(
        "SELECT name FROM experiments.quantity_kinds WHERE lower(name)=lower(%s)"
        " OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE lower(a)=lower(%s))"
        " OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE a ILIKE %s) LIMIT 1",
        (text, text, f"%{text}%"))
    return row[0]["name"] if row else None


def _prov(row_json: Any) -> Provenance:
    d = row_json if isinstance(row_json, dict) else json.loads(row_json)
    return Provenance.model_validate(d)


# ── evidence (hero) ──────────────────────────────────────────────────────

_EVIDENCE_SQL = """
SELECT r.*, m.name AS material_name, d.filename AS doc_name, d.country AS doc_country,
       d.year AS doc_year, d.doc_type AS doc_type, e.date AS exp_date, e.document_id,
       l.name AS lab_name, rg.state_hash
FROM experiments.results r
JOIN experiments.experiments e ON e.id = r.experiment_id
LEFT JOIN experiments.materials m ON m.id = r.material_id
LEFT JOIN experiments.documents d ON d.id = e.document_id
LEFT JOIN experiments.labs l ON l.id = e.lab_id
LEFT JOIN experiments.regimes rg ON rg.id = e.regime_id
WHERE r.superseded_by IS NULL
"""


def _evidence_rows(store: Store, material: str | None = None,
                   process: str | None = None, quantity_kind: str | None = None,
                   value_op: str | None = None, value: float | None = None,
                   year_from: int | None = None, country: str | None = None,
                   ) -> list[dict]:
    sql, params = _EVIDENCE_SQL, []
    if material:
        mid = resolve_entity(store, "material", material)
        if mid is None:
            return []
        sql += " AND r.material_id = %s"; params.append(mid)
    if process:
        p = resolve_process(store, process)
        if p is None:
            return []
        sql += (" AND EXISTS (SELECT 1 FROM experiments.experiment_processes ep"
                " WHERE ep.experiment_id = r.experiment_id AND ep.process_type = %s)")
        params.append(p)
    if quantity_kind:
        qk = resolve_quantity(store, quantity_kind)
        if qk is None:
            return []
        sql += " AND r.quantity_kind = %s"; params.append(qk)
    if value_op in ("<=", ">=") and value is not None:
        col = "COALESCE(r.value_nominal, r.value_max, r.value_min)"
        sql += f" AND {col} {'<=' if value_op == '<=' else '>='} %s"
        params.append(value)
    if year_from:
        sql += " AND COALESCE(EXTRACT(YEAR FROM e.date), d.year) >= %s"
        params.append(year_from)
    if country:
        sql += " AND d.country = %s"; params.append(country)
    return store.query(sql, params)


def evidence(store: Store, material: str | None = None, process: str | None = None,
             quantity_kind: str | None = None, value_op: str | None = None,
             value: float | None = None, year_from: int | None = None,
             country: str | None = None) -> Evidence:
    rows = _evidence_rows(store, material, process, quantity_kind,
                          value_op, value, year_from, country)
    if not rows:
        return Evidence(answer="данных нет", experiments=[], confidence="low",
                        agreement_flag="single",
                        gap_note=f"пробел: {material or process} × {quantity_kind}")
    exps = sorted({str(r["experiment_id"]) for r in rows})
    docs = {str(r["document_id"]) for r in rows if r["document_id"]}
    labs = sorted({r["lab_name"] for r in rows if r["lab_name"]})
    r0 = rows[0]
    val = _fmt_value(r0)
    flags = _agreement(rows)
    # источники в тексте ответа — чтобы ссылка на документ была видна всегда
    src_names = sorted({_clean_doc(r["doc_name"]) for r in rows if r.get("doc_name")})
    ans = f"{r0['quantity_kind']} = {val} {r0['unit'] or ''}".strip()
    if src_names:
        ans += " (источники: " + ", ".join(src_names[:4]) + ")"
    return Evidence(
        answer=ans,
        experiments=exps, n_experiments=len(exps), n_docs=len(docs), labs=labs,
        confidence="high" if len(docs) > 1 else "medium",
        agreement_flag=flags,
        citations=[_prov(r["prov"]) for r in rows[:5]])


def _fmt_value(r: dict) -> str:
    lo, nom, hi = r["value_min"], r["value_nominal"], r["value_max"]
    if nom is not None:
        return f"{nom:g}"
    if lo is not None and hi is not None:
        return f"{lo:g}–{hi:g}"
    return f">{lo:g}" if lo is not None else (f"<{hi:g}" if hi is not None else "?")


def _agreement(rows: list[dict]) -> str:
    if len(rows) == 1:
        return "single"
    comparable = [r for r in rows[1:] if not _gate_row(rows[0], r).blocking_dims]
    if len(comparable) < len(rows) - 1:
        return "incomparable"
    vals = [r["value_nominal"] for r in rows if r["value_nominal"] is not None]
    if len(vals) >= 2 and max(vals) > 0 and (max(vals) - min(vals)) / max(vals) > 0.3:
        return "contradictory"
    return "consistent"


# ── Comparability Gate на строках БД ─────────────────────────────────────

def _gate_row(a: dict, b: dict) -> Comparability:
    """Шесть осей шлюза на плоских строках results (без pydantic-объектов)."""
    blocking = []
    if a["quantity_kind"] != b["quantity_kind"]:
        blocking.append("quantity_kind")
    if (a.get("scale") or "none") != (b.get("scale") or "none"):
        blocking.append("scale")
    if a.get("basis") != b.get("basis"):
        blocking.append("basis")
    if _unit_dim(a.get("unit") or "") != _unit_dim(b.get("unit") or ""):
        blocking.append("unit_dim")
    if (a.get("sample_state") or "") != (b.get("sample_state") or ""):
        blocking.append("processing_state")
    ca = a.get("conditions") or {}
    cb = b.get("conditions") or {}
    ca = ca if isinstance(ca, dict) else json.loads(ca)
    cb = cb if isinstance(cb, dict) else json.loads(cb)
    # температура — бакетами; остальные оси условий (среда, нагрузка, скорость
    # деформации…) — строгое равенство: σ в разных средах несопоставимы.
    if _t_bucket(ca.get("temperature_k")) != _t_bucket(cb.get("temperature_k")):
        blocking.append("measurement_conditions")
    else:
        keys = (set(ca) | set(cb)) - {"temperature_k"}
        if any(ca.get(k) != cb.get(k) for k in keys):
            blocking.append("measurement_conditions")
    return Comparability(comparable=not blocking, blocking_dims=blocking,
                         note="OK" if not blocking else f"несопоставимо: {blocking}")


def gate_check(store: Store, result_id_a: str, result_id_b: str) -> Comparability:
    rows = store.query(
        "SELECT * FROM experiments.results WHERE id IN (%s, %s)",
        (result_id_a, result_id_b))
    if len(rows) != 2:
        return Comparability(comparable=False, blocking_dims=["not_found"],
                             note="одно из измерений не найдено")
    return _gate_row(rows[0], rows[1])


# ── профиль свидетельств и надёжность ────────────────────────────────────

_DOC_TYPE_TRUST = {"catalog": 1.0, "handbook": 0.9, "internal_report": 0.8,
                   "article": 0.7, "patent": 0.7}


def _reliability(row: dict) -> float:
    """Надёжность точки данных из уже имеющихся полей: тип источника ×
    уверенность экстракции × свежесть (+ бонус за верификацию считает вызывающий)."""
    base = _DOC_TYPE_TRUST.get(row.get("doc_type") or "", 0.6)
    p = row["prov"] if isinstance(row["prov"], dict) else json.loads(row["prov"])
    conf = float(p.get("confidence") or 0.7)
    year = row.get("doc_year")
    recency = 1.0 if (year or 0) >= 2020 else (0.95 if (year or 0) >= 2010 else 0.9)
    return round(min(1.0, base * conf * recency + 0.3), 2)


def _signature(row: dict) -> tuple:
    """Ключ группы сопоставимости (замена попарного Gate при агрегации):
    точки с одинаковой сигнатурой сопоставимы между собой по всем 6 осям."""
    c = row.get("conditions") or {}
    c = c if isinstance(c, dict) else json.loads(c)
    rest = frozenset((k, str(v)) for k, v in c.items()
                     if k != "temperature_k" and v is not None)
    return (row["quantity_kind"], row.get("scale") or "none", row.get("basis"),
            _unit_dim(row.get("unit") or ""), row.get("sample_state") or "",
            _t_bucket(c.get("temperature_k")), rest)


def evidence_profile(store: Store, quantity_kind: str,
                     material: str | None = None,
                     process: str | None = None) -> dict:
    """«Пространство решений» по величине: конверт значений, медиана, точки с
    надёжностью, выбросы. Агрегируются только сопоставимые точки (наибольшая
    группа сопоставимости); прочие группы указываются числом."""
    rows = _evidence_rows(store, material=material, process=process,
                          quantity_kind=quantity_kind)
    rows = [r for r in rows if r["value_nominal"] is not None
            or r["value_min"] is not None or r["value_max"] is not None]
    if not rows:
        return {"quantity_kind": quantity_kind, "n_points": 0, "points": [],
                "note": "данных нет"}
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(_signature(r), []).append(r)
    main = max(groups.values(), key=len)
    pts = []
    for r in main:
        val = r["value_nominal"] if r["value_nominal"] is not None else (
            (r["value_min"] if r["value_min"] is not None else r["value_max"]))
        pts.append({"value": val, "value_str": _fmt_value(r), "unit": r["unit"],
                    "doc": r["doc_name"], "year": r["doc_year"],
                    "country": r["doc_country"], "lab": r["lab_name"],
                    "reliability": _reliability(r), "snippet": _snip(r)})
    vals = sorted(p["value"] for p in pts)
    median = vals[len(vals) // 2]
    disp = (vals[-1] - vals[0]) / vals[-1] if vals[-1] else 0.0
    agreement = ("single" if len(pts) == 1 else
                 "consistent" if disp < 0.15 else
                 "dispersed" if disp < 0.5 else "divergent")
    outliers = [p for p in pts
                if median and abs(p["value"] - median) / abs(median) > 0.5]
    return {"quantity_kind": main[0]["quantity_kind"],
            "n_points": len(pts),
            "n_sources": len({p["doc"] for p in pts}),
            "n_labs": len({p["lab"] for p in pts if p["lab"]}),
            "envelope": {"min": vals[0], "max": vals[-1], "unit": main[0]["unit"]},
            "median": median, "dispersion": round(disp, 3),
            "agreement": agreement,
            "points": sorted(pts, key=lambda p: -p["reliability"]),
            "outliers": outliers,
            "other_comparability_groups": len(groups) - 1}


# ── противоречия ─────────────────────────────────────────────────────────

# Величины, у которых предмет недоспецифицирован без квалификатора (какой
# элемент? какая температура чего?) — измерения по ним не сравниваем попарно.
_UNDERSPECIFIED_KINDS = {"element_content"}


def find_contradictions(store: Store, rel_delta: float = 0.3) -> list[dict]:
    """Два детектора: (1) сопоставимые измерения одной величины на одном
    материале ИЗ РАЗНЫХ документов с расхождением > rel_delta; (2) выводы с
    одинаковыми (quantity_kind, factor) и противоположным direction.
    Пары внутри одного документа не флагуются: расхождение там — почти всегда
    разные объекты измерения, а не спор источников."""
    out: list[dict] = []
    review = {r["name"] for r in store.query(
        "SELECT name FROM experiments.quantity_kinds WHERE status='needs_review'")}
    skip_kinds = _UNDERSPECIFIED_KINDS | review

    rows = store.query(_EVIDENCE_SQL + " AND r.value_nominal IS NOT NULL"
                       " ORDER BY r.quantity_kind, r.material_id")
    by_key: dict[tuple, list[dict]] = {}
    for r in rows:
        if r["quantity_kind"] in skip_kinds:
            continue
        by_key.setdefault((r["quantity_kind"], str(r["material_id"])), []).append(r)
    for (qk, _mid), group in by_key.items():
        for i in range(len(group)):
            for k in range(i + 1, len(group)):
                a, b = group[i], group[k]
                if a["document_id"] == b["document_id"]:
                    continue
                gate = _gate_row(a, b)
                if not gate.comparable:
                    continue
                va, vb = a["value_nominal"], b["value_nominal"]
                if max(va, vb) > 0 and abs(va - vb) / max(va, vb) > rel_delta:
                    delta = abs(va - vb) / max(va, vb)
                    rel_a, rel_b = _reliability(a), _reliability(b)
                    # это не вердикт «кто-то неправ», а зона расхождения:
                    # чем надёжнее обе стороны и больше дельта — тем важнее выяснить
                    severity = ("high" if delta > 0.5 and min(rel_a, rel_b) > 0.7
                                else "medium" if min(rel_a, rel_b) > 0.6 else "low")
                    out.append({
                        "type": "measurement", "label": "зона расхождения",
                        "severity": severity, "quantity_kind": qk,
                        "material": a["material_name"],
                        "a": {"value": va, "unit": a["unit"], "doc": a["doc_name"],
                              "reliability": rel_a, "snippet": _snip(a)},
                        "b": {"value": vb, "unit": b["unit"], "doc": b["doc_name"],
                              "reliability": rel_b, "snippet": _snip(b)},
                        "delta_rel": round(delta, 3),
                        "comparability": gate.model_dump()})

    eff = store.query(
        "SELECT c.id, c.text, c.effect, c.prov, d.filename AS doc_name"
        " FROM experiments.conclusions c"
        " LEFT JOIN experiments.experiments e ON e.id = c.experiment_id"
        " LEFT JOIN experiments.documents d"
        "        ON d.id = COALESCE(e.document_id, c.document_id)"
        " WHERE c.effect IS NOT NULL AND c.superseded_by IS NULL")
    from .extract.quantities import canonize as _canonize
    by_fk: dict[tuple, list[dict]] = {}
    for r in eff:
        effd = r["effect"] if isinstance(r["effect"], dict) else json.loads(r["effect"])
        qk = effd.get("quantity_kind") or ""
        # мусор схемы и неразрешённые величины не образуют «зон расхождения»
        if qk in skip_kinds or _canonize(qk).kind is None:
            continue
        key = (qk, (effd.get("factor") or "").lower())
        by_fk.setdefault(key, []).append({**r, "_dir": effd.get("direction")})
    for (qk, factor), group in by_fk.items():
        dirs = {g["_dir"] for g in group}
        if {"increases", "decreases"} <= dirs:
            out.append({
                "type": "conclusion", "label": "зона расхождения",
                "severity": "high", "quantity_kind": qk, "factor": factor,
                "directions": sorted(dirs),
                "claims": [{"text": g["text"], "direction": g["_dir"],
                            "doc": g["doc_name"], "snippet": _snip(g)}
                           for g in group]})
    return out


def _snip(row: dict) -> str:
    p = row["prov"] if isinstance(row["prov"], dict) else json.loads(row["prov"])
    return p.get("snippet", "")[:200]


# ── пробелы и зоны риска ─────────────────────────────────────────────────

def find_gaps(store: Store, min_sources: int = 3) -> dict:
    """(a) ячейки материал × величина без данных; (b) процессы только в одной
    географии; (c) процессы с < min_sources источников."""
    cells = store.query("""
        SELECT m.id AS material_id, m.name AS material, qk.name AS quantity_kind,
               count(r.id) AS n
        FROM experiments.materials m
        CROSS JOIN (SELECT name FROM experiments.quantity_kinds
                    WHERE status <> 'needs_review') qk
        LEFT JOIN experiments.results r
               ON r.material_id = m.id AND r.quantity_kind = qk.name
        GROUP BY 1, 2, 3""")
    empty = [GapCell(material_id=str(c["material_id"]), quantity_kind=c["quantity_kind"],
                     coverage="none", n_experiments=0).model_dump()
             | {"material": c["material"]}
             for c in cells if c["n"] == 0]
    measured_kinds = {c["quantity_kind"] for c in cells if c["n"] > 0}
    empty = [c for c in empty if c["quantity_kind"] in measured_kinds or
             c["quantity_kind"] in ("energy_consumption", "specific_cost")]

    geo = store.query("""
        SELECT ep.process_type,
               count(DISTINCT d.id) FILTER (WHERE d.country = 'RU')  AS n_ru,
               count(DISTINCT d.id) FILTER (WHERE d.country <> 'RU') AS n_foreign,
               count(DISTINCT d.id) AS n_docs
        FROM experiments.experiment_processes ep
        JOIN experiments.experiments e ON e.id = ep.experiment_id
        LEFT JOIN experiments.documents d ON d.id = e.document_id
        GROUP BY 1""")
    geo_gaps = [{"process_type": g["process_type"],
                 "only": "domestic" if g["n_foreign"] == 0 else "foreign"}
                for g in geo if g["n_ru"] == 0 or g["n_foreign"] == 0]
    low = [{"process_type": g["process_type"], "n_sources": g["n_docs"]}
           for g in geo if g["n_docs"] < min_sources]
    return {"empty_cells": empty, "geo_exclusive": geo_gaps, "low_coverage": low}


def risk_zones(store: Store, min_sources: int = 3) -> list[dict]:
    gaps = find_gaps(store, min_sources)
    contras = find_contradictions(store)
    zones: dict[str, dict] = {}
    for l in gaps["low_coverage"]:
        z = zones.setdefault(l["process_type"], {"topic": l["process_type"], "reasons": []})
        z["reasons"].append(f"источников: {l['n_sources']} (<{min_sources})")
    for g in gaps["geo_exclusive"]:
        z = zones.setdefault(g["process_type"], {"topic": g["process_type"], "reasons": []})
        z["reasons"].append(f"практика только {g['only']}")
    n_contra: dict[str, int] = {}
    for c in contras:
        key = c.get("quantity_kind") or "misc"
        n_contra[key] = n_contra.get(key, 0) + 1
    for key, n in n_contra.items():
        z = zones.setdefault(key, {"topic": key, "reasons": []})
        z["reasons"].append(f"противоречий: {n}")
    return sorted(zones.values(), key=lambda z: -len(z["reasons"]))


# ── сравнения ────────────────────────────────────────────────────────────

def compare_practice(store: Store, process: str) -> dict:
    """Отечественная vs зарубежная практика по процессу."""
    p = resolve_process(store, process)
    if p is None:
        return {"process": process, "error": "процесс не найден в реестре"}
    rows = store.query("""
        SELECT COALESCE(d.country,'?') AS country, d.filename, d.year,
               c.text, c.kind, c.prov
        FROM experiments.experiment_processes ep
        JOIN experiments.experiments e ON e.id = ep.experiment_id
        LEFT JOIN experiments.documents d ON d.id = e.document_id
        LEFT JOIN experiments.conclusions c ON c.experiment_id = e.id
        WHERE ep.process_type = %s
        UNION ALL
        SELECT COALESCE(d.country,'?'), d.filename, d.year, c.text, c.kind, c.prov
        FROM experiments.conclusions c
        JOIN experiments.documents d ON d.id = c.document_id
        WHERE c.process_type = %s AND c.superseded_by IS NULL""", (p, p))
    domestic = [r for r in rows if r["country"] == "RU"]
    foreign = [r for r in rows if r["country"] not in ("RU", "?")]
    def _pack(rs):
        return [{"doc": r["filename"], "year": r["year"], "conclusion": r["text"],
                 "snippet": _snip(r) if r["prov"] else ""} for r in rs if r["text"]]
    return {"process": p, "domestic": _pack(domestic), "foreign": _pack(foreign),
            "n_domestic_docs": len({r["filename"] for r in domestic}),
            "n_foreign_docs": len({r["filename"] for r in foreign})}


def compare_technologies(store: Store, processes: list[str]) -> list[dict]:
    """Таблица {method, param, value, unit, origin, source_ref} по списку методов."""
    out = []
    for proc in processes:
        p = resolve_process(store, proc)
        if p is None:
            continue
        rows = store.query("""
            SELECT r.quantity_kind, r.value_min, r.value_nominal, r.value_max,
                   r.unit, d.country, d.filename, r.prov
            FROM experiments.experiment_processes ep
            JOIN experiments.results r ON r.experiment_id = ep.experiment_id
            LEFT JOIN experiments.experiments e ON e.id = ep.experiment_id
            LEFT JOIN experiments.documents d ON d.id = e.document_id
            WHERE ep.process_type = %s AND r.superseded_by IS NULL""", (p,))
        for r in rows:
            out.append({"method": p, "param": r["quantity_kind"],
                        "value": _fmt_value(r), "unit": r["unit"],
                        "origin": "domestic" if r["country"] == "RU" else "foreign",
                        "source_ref": r["filename"], "snippet": _snip(r)})
    return out


# ── эксперты, граф, lineage, таймлайн ────────────────────────────────────

def find_experts_by_topic(store: Store, topic: str, limit: int = 5) -> list[dict]:
    """Лаборатории/эксперты: совпадение экспертизы + число экспериментов по теме.
    Матч по значимым словам-стемам темы (падежи, сокращения): «аффинажем
    драгметаллов» ловит экспертизу «аффинаж драгоценных металлов»."""
    p = resolve_process(store, topic)
    # стемы значимых слов (>4 букв, обрезаем 2 буквы окончания)
    stems = [w[:-2] if len(w) > 5 else w
             for w in re.findall(r"\w{5,}", topic.lower())]
    patterns = [f"%{s}%" for s in stems] or [f"%{topic}%"]
    rows = store.query("""
        SELECT l.id, l.name, l.kind, l.city, l.country, l.expertise,
               count(DISTINCT e.id) AS n_experiments
        FROM experiments.labs l
        LEFT JOIN experiments.experiments e ON e.lab_id = l.id
        LEFT JOIN experiments.experiment_processes ep ON ep.experiment_id = e.id
        WHERE EXISTS (SELECT 1 FROM unnest(l.expertise) x
                      WHERE x ILIKE ANY(%s))
           OR (%s::text IS NOT NULL AND ep.process_type = %s)
        GROUP BY l.id ORDER BY n_experiments DESC, l.name LIMIT %s""",
        (patterns, p, p, limit))
    return [{"id": str(r["id"]), "name": r["name"], "kind": r["kind"],
             "city": r["city"], "country": r["country"],
             "expertise": list(r["expertise"] or []),
             "n_experiments": r["n_experiments"]} for r in rows]


_NODE_LABELS = """
SELECT id, name AS label, 'material' AS ntype FROM experiments.materials
UNION ALL SELECT id, COALESCE(title, 'эксперимент'), 'experiment' FROM experiments.experiments
UNION ALL SELECT id, name, 'lab' FROM experiments.labs
UNION ALL SELECT id, filename, 'document' FROM experiments.documents
UNION ALL SELECT id, left(text, 80), 'conclusion' FROM experiments.conclusions
UNION ALL SELECT id, quantity_kind || '=' || COALESCE(value_nominal::text,
                COALESCE(value_min::text,'')||'–'||COALESCE(value_max::text,'')),
           'measurement' FROM experiments.results
UNION ALL SELECT id, state_hash, 'regime' FROM experiments.regimes
UNION ALL SELECT id, name, 'equipment' FROM experiments.equipment
"""


def get_subgraph(store: Store, entity: str, depth: int = 1,
                 max_nodes: int = 60) -> dict:
    """Окрестность узла по edges-VIEW: {nodes, edges} для визуализации."""
    root = resolve_entity(store, "material", entity) or \
        resolve_entity(store, "lab", entity) or \
        resolve_entity(store, "document", entity) or entity
    frontier, seen_nodes, seen_edges = {root}, {root}, []
    for _ in range(max(1, depth)):
        if not frontier or len(seen_nodes) > max_nodes:
            break
        rows = store.query(
            "SELECT src::text, dst::text, predicate, attrs FROM experiments.edges"
            " WHERE src::text = ANY(%s) OR dst::text = ANY(%s)",
            (list(frontier), list(frontier)))
        frontier = set()
        for r in rows:
            edge = (r["src"], r["dst"], r["predicate"])
            if edge in {(e["src"], e["dst"], e["predicate"]) for e in seen_edges}:
                continue
            attrs = r["attrs"] if isinstance(r["attrs"], dict) else json.loads(r["attrs"] or "{}")
            seen_edges.append({"src": r["src"], "dst": r["dst"],
                               "predicate": r["predicate"], "attrs": attrs})
            for n in (r["src"], r["dst"]):
                if n not in seen_nodes:
                    seen_nodes.add(n)
                    frontier.add(n)
    labels = {str(r["id"]): r for r in store.query(_NODE_LABELS)}
    nodes = [{"id": n,
              "label": labels.get(n, {}).get("label", n[:8]),
              "ntype": labels.get(n, {}).get("ntype", "unknown")}
             for n in seen_nodes]
    return {"nodes": nodes, "edges": seen_edges}


def lineage(store: Store, entity: str) -> list[dict]:
    root = resolve_entity(store, "material", entity) or entity
    rows = store.query("""
        WITH RECURSIVE chain(src, dst, proc, depth) AS (
          SELECT src, dst, attrs->>'process', 1 FROM experiments.edges_semantic
            WHERE predicate = 'derived_from' AND src = %s::uuid
          UNION ALL
          SELECT es.src, es.dst, es.attrs->>'process', c.depth + 1
            FROM experiments.edges_semantic es JOIN chain c ON es.src = c.dst
            WHERE es.predicate = 'derived_from' AND c.depth < 12)
        SELECT c.*, m1.name AS from_label, m2.name AS to_label
        FROM chain c
        LEFT JOIN experiments.materials m1 ON m1.id = c.src
        LEFT JOIN experiments.materials m2 ON m2.id = c.dst
        ORDER BY depth""", (root,))
    return [{"from": r["from_label"] or str(r["src"]),
             "process": r["proc"],
             "to": r["to_label"] or str(r["dst"]), "depth": r["depth"]}
            for r in rows]


def timeline(store: Store, material: str | None = None,
             process: str | None = None) -> list[dict]:
    sql = """
        SELECT COALESCE(e.date::text, d.year::text) AS at, e.title, l.name AS lab,
               d.filename AS doc, ep.process_type
        FROM experiments.experiments e
        LEFT JOIN experiments.documents d ON d.id = e.document_id
        LEFT JOIN experiments.labs l ON l.id = e.lab_id
        LEFT JOIN experiments.experiment_processes ep ON ep.experiment_id = e.id
        WHERE 1=1"""
    params: list = []
    if material:
        mid = resolve_entity(store, "material", material)
        sql += (" AND EXISTS (SELECT 1 FROM experiments.experiment_materials em"
                " WHERE em.experiment_id = e.id AND em.material_id = %s)")
        params.append(mid)
    if process:
        p = resolve_process(store, process)
        sql += " AND ep.process_type = %s"; params.append(p)
    sql += " ORDER BY at NULLS LAST"
    return store.query(sql, params)


# ── литобзор и покрытие ──────────────────────────────────────────────────

def literature_review(store: Store, process: str | None = None) -> dict:
    """Секции структурированного обзора (шаблон ответа «литобзор»)."""
    p = resolve_process(store, process) if process else None
    cond = "WHERE ep.process_type = %s" if p else ""
    params = (p,) if p else ()
    claims_cond = ("AND (ep.process_type = %s OR c.process_type = %s)" if p else "")
    claims_params = (p, p) if p else ()
    base = store.query(f"""
        SELECT DISTINCT d.id, d.filename, d.year, d.country, d.lang,
               ep.process_type
        FROM experiments.experiments e
        LEFT JOIN experiments.documents d ON d.id = e.document_id
        LEFT JOIN experiments.experiment_processes ep ON ep.experiment_id = e.id
        {cond}
        UNION
        SELECT DISTINCT d.id, d.filename, d.year, d.country, d.lang, c.process_type
        FROM experiments.conclusions c
        JOIN experiments.documents d ON d.id = c.document_id
        WHERE c.experiment_id IS NULL {"AND c.process_type = %s" if p else ""}""",
        params + (params if p else ()))
    by = lambda key: _group(base, key)
    contras = [c for c in find_contradictions(store)
               if not p or c.get("quantity_kind")]
    claims = store.query(f"""
        SELECT c.text, c.kind, c.effect, c.prov, d.filename
        FROM experiments.conclusions c
        LEFT JOIN experiments.experiments e ON e.id = c.experiment_id
        LEFT JOIN experiments.documents d
               ON d.id = COALESCE(e.document_id, c.document_id)
        LEFT JOIN experiments.experiment_processes ep
               ON ep.experiment_id = c.experiment_id
        WHERE c.superseded_by IS NULL {claims_cond}""", claims_params)
    consensus, seen = [], set()
    for r in claims:
        effd = r["effect"] if isinstance(r["effect"], dict) else \
            (json.loads(r["effect"]) if r["effect"] else None)
        if not effd:
            continue
        key = (effd["quantity_kind"], effd["direction"])
        if key in seen:
            continue
        same = [x for x in claims if _eff(x) and
                (_eff(x)["quantity_kind"], _eff(x)["direction"]) == key]
        if len(same) >= 2:
            consensus.append({"quantity_kind": key[0], "direction": key[1],
                              "n_sources": len({x['filename'] for x in same})})
        seen.add(key)
    return {"by_method": by("process_type"), "by_geo": by("country"),
            "by_year": by("year"), "consensus": consensus,
            "disagreements": contras,
            "claims": [{"text": r["text"], "kind": r["kind"], "doc": r["filename"],
                        "snippet": _snip(r)} for r in claims]}


def _eff(r: dict) -> Optional[dict]:
    e = r.get("effect")
    return e if isinstance(e, dict) else (json.loads(e) if e else None)


def _group(rows: list[dict], key: str) -> list[dict]:
    g: dict[Any, set] = {}
    for r in rows:
        if r.get(key) is not None and r.get("filename"):
            g.setdefault(r[key], set()).add(r["filename"])
    return [{key: k, "n_docs": len(v), "docs": sorted(v)[:10]}
            for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))]


# ── полнотекстовый ретрив пассажей с провенансом ─────────────────────────

def _clean_doc(name: str | None) -> str:
    """Имя документа-источника: базовое имя без пути и служебных расширений."""
    if not name:
        return "источник не указан"
    base = re.split(r"[\\/]", name)[-1]
    return re.sub(r"\.(md|pdf|docx?|pptx?|txt)$", "", base, flags=re.I).strip() or base


# Общие слова-вопросы: сами по себе не про домен, встречаются везде и портят
# ранжирование («методы» матчит и горные удары, и воду). Отбрасываем по стему.
# Только слова-вопросы (интеррогативы/мета) — они не несут темы. Доменные общие
# слова (процесс, завод, производство…) НЕ перечисляем вручную: их глушит IDF при
# ранжировании (частые слова получают почти нулевой вес).
_QUERY_STOPSTEMS = (
    "метод", "способ", "существ", "техническ", "технолог", "решени", "применя",
    "значени", "диапазон", "вопрос", "привед", "перечисл", "вариант", "получ",
    "использ", "какие", "какой", "какая", "какое", "дают", "даёт", "дает",
    "между", "бывает", "бывают", "виды", "каков", "какими", "чему", "чего",
    "нужно", "можно", "также", "относят",
)


def _content_terms(text: str, max_terms: int = 14) -> list[str]:
    """Значимые (доменные) слова вопроса. Общие слова-вопросы отбрасываем —
    иначе ts_rank поднимает нерелевантные документы, где просто есть
    «методы/способы»."""
    seen: set[str] = set()
    out: list[str] = []
    for t in re.findall(r"[а-яёa-z0-9]{4,}", text.lower()):
        if t in seen or any(t.startswith(s) for s in _QUERY_STOPSTEMS):
            continue
        seen.add(t)
        out.append(t)
    if not out:  # запрос из одних общих слов — вернуть хотя бы что-то
        out = list(re.findall(r"[а-яёa-z0-9]{5,}", text.lower()))
    return out[:max_terms]


def _tsquery_or(text: str, max_terms: int = 14) -> str:
    return " | ".join(_content_terms(text, max_terms))


def _resolve_query_entities(store: Store, query: str,
                            terms: list[str]) -> dict:
    """Распознанные сущности запроса (для чипов UI и фильтров): канонический
    процесс, род величины, материалы. Точность важнее полноты: биграммы и полный
    запрос матчатся через resolve_* (там допустима подстрока — алиасы длинные),
    одиночные термы — ТОЛЬКО точным алиасом, иначе общие слова («извлечение»)
    дают ложный процесс."""
    terms = terms[:10]
    bigrams = [f"{a} {b}" for a, b in zip(terms, terms[1:])][:8]
    low = [t.lower() for t in terms]

    process = None
    for cand in bigrams:
        process = resolve_process(store, cand)
        if process:
            break
    if not process:
        row = store.query(
            "SELECT name FROM experiments.process_types"
            " WHERE lower(name) = ANY(%s)"
            "    OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE lower(a) = ANY(%s))"
            " LIMIT 1", (low, low))
        process = row[0]["name"] if row else None

    quantity = None
    for cand in bigrams:
        quantity = resolve_quantity(store, cand)
        if quantity:
            break
    if not quantity:
        row = store.query(
            "SELECT name FROM experiments.quantity_kinds"
            " WHERE status <> 'needs_review' AND (lower(name) = ANY(%s)"
            "    OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE lower(a) = ANY(%s)))"
            " LIMIT 1", (low, low))
        quantity = row[0]["name"] if row else None

    mats = store.query(
        "SELECT DISTINCT m.name FROM experiments.entity_aliases a"
        " JOIN experiments.materials m ON m.id = a.entity_id"
        " WHERE a.entity_type = 'material' AND lower(a.alias) = ANY(%s)"
        " LIMIT 4", (low + [b.lower() for b in bigrams],))
    return {"process": process, "quantity_kind": quantity,
            "materials": [r["name"] for r in mats]}


_DOC_EXT_RE = re.compile(r"\.(md|pdf|docx?|pptx?|txt)$", re.I)


def _doc_key(name: str) -> str:
    """Ключ сопоставления имён документа между таблицами: без пути, без ВСЕХ
    служебных расширений (…pdf.md → голое имя), lowercase. В passage_index имя
    уже очищено, в documents.filename — сырое (`X.pdf.md`)."""
    base = re.split(r"[\\/]", name)[-1].strip()
    while True:
        stripped = _DOC_EXT_RE.sub("", base).strip()
        if stripped == base or not stripped:
            break
        base = stripped
    return base.lower()


def _doc_meta(store: Store, raw_names: list[str]) -> dict[str, dict]:
    """Имя документа → {okf_path, year, country} (диплинк /wiki?doc=… и
    метаданные для фильтров). Таблица документов мала (сотни строк) — дешевле
    забрать её целиком и сматчить по нормализованному ключу, чем гонять ANY
    по вариантам имён."""
    keys = {_doc_key(n) for n in raw_names if n}
    if not keys:
        return {}
    rows = store.query(
        "SELECT filename, okf_raw_path, year, country, lang"
        " FROM experiments.documents")
    by_key = {_doc_key(r["filename"]): r for r in rows}
    out: dict[str, dict] = {}
    for n in raw_names:
        if not n:
            continue
        r = by_key.get(_doc_key(n))
        if r:
            out[n] = {"okf_path": r["okf_raw_path"], "year": r["year"],
                      "country": r["country"], "lang": r["lang"]}
    return out


def _corpus_has_term(store: Store, term: str) -> bool:
    """Есть ли термин в индексе пассажей (факты + сырые чанки; EXISTS —
    короткое замыкание). Индекс покрывает полный текст, поэтому гейт честности
    не отсекает вопросы, чьи термины есть в корпусе, но не среди фактов."""
    try:
        return bool(store.scalar(
            "SELECT EXISTS(SELECT 1 FROM experiments.passage_index"
            " WHERE to_tsvector('russian', coalesce(text,'') || ' '"
            "        || coalesce(snippet,'')) @@ plainto_tsquery('russian', %s))",
            (term,)))
    except Exception:
        store.rollback()
        return True  # при ошибке не отсекаем


def _idf_weights(store: Store, terms: list[str]) -> tuple[dict, int]:
    """BM25-IDF каждого термина по частоте в индексе (частые слова → ~0). df
    считается через GIN-индекс (быстро)."""
    n = store.scalar("SELECT count(*) FROM experiments.passage_index") or 1
    idf: dict[str, float] = {}
    for t in terms:
        df = store.scalar(
            "SELECT count(*) FROM experiments.passage_index"
            " WHERE to_tsvector('russian', coalesce(text,'') || ' ' || coalesce(snippet,''))"
            "       @@ plainto_tsquery('russian', %s)", (t,)) or 0
        idf[t] = math.log(1 + (n - df + 0.5) / (df + 0.5))
    return idf, n


def _term_in(blob: str, t: str) -> bool:
    return t in blob or (len(t) > 5 and t[:-2] in blob)


def _hybrid_search(store: Store, query: str, tsq: str, limit: int,
                   terms: list[str] | None = None) -> dict:
    """Гибрид: лексика с IDF-взвешиванием (BM25-lite) + плотный pgvector-косинус,
    слитые через Reciprocal Rank Fusion.

    IDF решает главную проблему качества ретрива: общие слова (процесс, завод,
    производство) частотны → почти нулевой вес и не топят специфические якоря
    запроса (SAVMIN, Stillfontein). Кандидаты отбираются по ЯКОРНЫМ (редким)
    терминам, а финальный скор — сумма IDF совпавших терминов.

    `terms` — уже расширенные синонимами/аббревиатурами лексемы (см.
    search_passages); если не передано, берём из самого запроса."""
    from . import hybrid_index as hx
    K, CAND, CAND_LEX = 60, 40, 120
    terms = terms or _content_terms(query)
    if not terms:
        return {"query": query, "n": 0, "docs": [], "passages": []}
    idf, _n = _idf_weights(store, terms)
    # якорные (редкие) термины ведут отбор кандидатов; общие частотные слова из
    # tsquery выкидываем автоматически (порог по IDF), но не меньше 3 самых редких.
    ranked_terms = sorted(terms, key=lambda t: -idf[t])
    anchors = [t for t in ranked_terms if idf[t] >= 1.5] or ranked_terms[:3]
    anchor_tsq = " | ".join(anchors)
    lex = store.query(
        "SELECT id, source, doc_name, kind, text, snippet, has_number,"
        " ts_rank(to_tsvector('russian', coalesce(text,'') || ' ' || coalesce(snippet,'')),"
        "         to_tsquery('russian', %s)) AS r"
        " FROM experiments.passage_index"
        " WHERE to_tsvector('russian', coalesce(text,'') || ' ' || coalesce(snippet,''))"
        "       @@ to_tsquery('russian', %s)"
        " ORDER BY r DESC LIMIT %s", (anchor_tsq, anchor_tsq, CAND_LEX))

    def _bm25(row: dict) -> float:
        blob = ((row.get("text") or "") + " " + (row.get("snippet") or "")).lower()
        return sum(idf[t] for t in terms if _term_in(blob, t))

    lex.sort(key=_bm25, reverse=True)
    lex = lex[:CAND]
    den: list[dict] = []
    qv = hx.embed_query(query)
    if qv is not None:
        lit = hx.vec_literal(qv)
        den = store.query(
            "SELECT id, source, doc_name, kind, text, snippet, has_number"
            " FROM experiments.passage_index WHERE embedding IS NOT NULL"
            " ORDER BY embedding <=> %s::vector LIMIT %s", (lit, CAND))
    # RRF: лексика (IDF-ранжированная) вес 1.0, плотный поиск 0.5 — дополняет
    # семантикой, но не вытесняет уверенные термические совпадения.
    scores: dict = {}
    meta: dict = {}
    for weight, lst in ((1.0, lex), (0.5, den)):
        for rank, row in enumerate(lst):
            scores[row["id"]] = scores.get(row["id"], 0.0) + weight / (K + rank)
            meta.setdefault(row["id"], row)
    if not scores:
        return {"query": query, "n": 0, "docs": [], "passages": []}
    dmeta = _doc_meta(store, [m.get("doc_name") for m in meta.values()])
    passages: list[dict] = []
    seen: set[tuple] = set()
    for pid in sorted(scores, key=lambda i: -scores[i]):
        row = meta[pid]
        body = (row.get("text") or "").strip()
        snip = (row.get("snippet") or body)[:300]
        doc = _clean_doc(row.get("doc_name"))
        key = (doc, (snip or body)[:60])
        if key in seen:
            continue
        seen.add(key)
        info = dmeta.get(row.get("doc_name") or "", {})
        passages.append({
            "kind": "measurement" if row.get("source") == "measurement"
            else (row.get("kind") or "finding"),
            "text": body, "snippet": snip, "doc": doc, "locator": None,
            "okf_path": info.get("okf_path"),
            "country": info.get("country"), "year": info.get("year"),
            "lang": info.get("lang"),
            "value": None, "unit": None,
            "rank": round(scores[pid], 4)})
        if len(passages) >= limit:
            break
    return {"query": query, "n": len(passages),
            "docs": sorted({p["doc"] for p in passages if p["doc"]}),
            "passages": passages}


def search_passages(store: Store, query: str, limit: int = 10,
                    process: str | None = None,
                    material: str | None = None) -> dict:
    """Ретрив релевантных пассажей из корпуса с привязкой к документу-источнику.

    Ищет по извлечённым дословным цитатам (выводы + измерения) — у каждой есть
    сниппет, локатор и документ. Это ретрив-фолбэк для вопросов «какие методы /
    способы / технические решения», где типовой evidence не даёт одно число, но
    в корпусе есть релевантный текст. Гарантирует ссылку на источник у каждого
    пассажа."""
    _no_entities = {"process": None, "quantity_kind": None, "materials": []}
    terms = _content_terms(query)
    if not terms:
        return {"query": query, "n": 0, "docs": [], "passages": [],
                "entities": _no_entities, "expanded_terms": []}
    # расширение синонимами/аббревиатурами: русский терм добирает англоязычные
    # формы (обессоливание→desalination) и наоборот, аббревиатуры раскрываются
    # (TDS→сухой остаток). Оригиналы идут первыми и сохраняют якорный вес.
    # Короткие (2–3 буквы) токены общий токенизатор отбрасывает, но известные
    # словарю аббревиатуры (TDS, МПГ, FCL) добавляем во вход расширения.
    short_known = [t for t in dict.fromkeys(
        re.findall(r"[а-яёa-z0-9]{2,3}", query.lower()))
        if query_expand.known(t)]
    expanded = query_expand.expand_query(terms + short_known)
    extra_terms = [t for t in expanded if t not in set(terms)]
    # распознанные сущности запроса — канонические имена из реестров/алиасов
    # (для чипов UI и фильтров)
    try:
        entities = _resolve_query_entities(store, query, terms)
    except Exception:
        store.rollback()
        entities = dict(_no_entities)

    # честность: терм считаем «отсутствующим» только если НИ ОДНА его форма
    # (сам терм + до 3 кросс-язычных синонимов, короткие/частые — первыми) не
    # встречается в корпусе — иначе RU-вопрос про EN-only концепт ложно
    # отсекался бы как «нет данных». Кап на формы и ранний выход по порогу
    # ограничивают число EXISTS-проб на запрос.
    def _present(t: str) -> bool:
        if _corpus_has_term(store, t):
            return True
        forms = sorted((f for f in query_expand.present_forms(t) if f != t),
                       key=len)[:3]
        return any(_corpus_has_term(store, f) for f in forms)

    if len(terms) >= 2:
        # большинство значимых терминов отсутствует → вопрос о том, чего в
        # корпусе нет. Порог 0.5 безопасен для реальных вопросов (их термины
        # почти все присутствуют); ниже — растут ложные «нет данных».
        need = math.ceil(len(terms) * 0.5)
        absent: list[str] = []
        for i, t in enumerate(terms):
            if len(absent) >= need:
                break
            if len(absent) + (len(terms) - i) < need:
                break  # порог уже недостижим — дальше не пробуем
            if not _present(t):
                absent.append(t)
        if len(absent) >= need:
            return {"query": query, "n": 0, "docs": [], "passages": [],
                    "entities": entities, "expanded_terms": extra_terms,
                    "note": "в корпусе нет данных по: " + ", ".join(absent[:6])}
    tsq = " | ".join(expanded)
    # гибрид (лексика + плотные эмбеддинги) — если индекс собран; иначе лексика.
    try:
        from . import hybrid_index as hx
        if hx.index_ready(store):
            res = _hybrid_search(store, query, tsq, limit, terms=expanded)
            if res["n"]:
                res["entities"] = entities
                res["expanded_terms"] = extra_terms
                return res
    except Exception:
        store.rollback()
    pf = ""
    params_extra: list = []
    if process:
        p = resolve_process(store, process)
        if p:
            pf = (" AND EXISTS (SELECT 1 FROM experiments.experiment_processes ep"
                  " WHERE ep.experiment_id = c.experiment_id AND ep.process_type = %s)")
            params_extra = [p]
    passages: list[dict] = []
    try:
        rc = store.query(f"""
            SELECT c.text, c.kind, c.prov->>'snippet' AS snippet,
                   c.prov->>'locator' AS locator, d.filename AS doc,
                   d.okf_raw_path AS okf_path, d.country, d.year, d.lang,
                   ts_rank(to_tsvector('russian',
                       coalesce(c.text,'') || ' ' || coalesce(c.prov->>'snippet','')),
                       to_tsquery('russian', %s)) AS rank
            FROM experiments.conclusions c
            LEFT JOIN experiments.experiments e ON e.id = c.experiment_id
            LEFT JOIN experiments.documents d ON d.id = COALESCE(c.document_id, e.document_id)
            WHERE c.superseded_by IS NULL {pf}
              AND to_tsvector('russian',
                    coalesce(c.text,'') || ' ' || coalesce(c.prov->>'snippet',''))
                  @@ to_tsquery('russian', %s)
            ORDER BY rank DESC LIMIT %s""",
            [tsq, *params_extra, tsq, limit * 2])
        for r in rc:
            passages.append({
                "kind": r["kind"] or "finding", "text": r["text"],
                "snippet": (r["snippet"] or "")[:300], "doc": _clean_doc(r["doc"]),
                "locator": r["locator"], "okf_path": r["okf_path"],
                "country": r["country"], "year": r["year"], "lang": r["lang"],
                "value": None, "unit": None, "rank": round(float(r["rank"] or 0), 4)})
        rr = store.query("""
            SELECT r.quantity_kind, r.value_min, r.value_nominal, r.value_max, r.unit,
                   r.prov->>'snippet' AS snippet, r.prov->>'locator' AS locator,
                   d.filename AS doc, d.okf_raw_path AS okf_path, d.country, d.year,
                   d.lang,
                   ts_rank(to_tsvector('russian', coalesce(r.prov->>'snippet','')),
                       to_tsquery('russian', %s)) AS rank
            FROM experiments.results r
            LEFT JOIN experiments.experiments e ON e.id = r.experiment_id
            LEFT JOIN experiments.documents d ON d.id = e.document_id
            WHERE r.superseded_by IS NULL AND r.prov->>'snippet' IS NOT NULL
              AND to_tsvector('russian', coalesce(r.prov->>'snippet',''))
                  @@ to_tsquery('russian', %s)
            ORDER BY rank DESC LIMIT %s""", (tsq, tsq, limit * 2))
        for r in rr:
            val = _fmt_value(r)
            passages.append({
                "kind": "measurement",
                "text": f"{r['quantity_kind']} = {val} {r['unit'] or ''}".strip(),
                "snippet": (r["snippet"] or "")[:300], "doc": _clean_doc(r["doc"]),
                "locator": r["locator"], "okf_path": r["okf_path"],
                "country": r["country"], "year": r["year"], "lang": r["lang"],
                "value": val, "unit": r["unit"], "rank": round(float(r["rank"] or 0), 4)})
    except Exception:
        store.rollback()
        like = f"%{query.strip()[:40]}%"
        rc = store.query("""
            SELECT c.text, c.kind, c.prov->>'snippet' AS snippet, d.filename AS doc
            FROM experiments.conclusions c
            LEFT JOIN experiments.experiments e ON e.id = c.experiment_id
            LEFT JOIN experiments.documents d ON d.id = COALESCE(c.document_id, e.document_id)
            WHERE c.text ILIKE %s LIMIT %s""", (like, limit))
        passages = [{"kind": r["kind"] or "finding", "text": r["text"],
                     "snippet": (r["snippet"] or "")[:300], "doc": _clean_doc(r["doc"]),
                     "locator": None, "okf_path": None, "country": None,
                     "year": None, "value": None, "unit": None, "rank": 0.0}
                    for r in rc]
    # переранжирование: пассажи с числами и измерения — вверх (в них конкретные
    # значения, которые чаще всего и спрашивают); прочее — по ts_rank.
    for p in passages:
        blob = (p.get("snippet") or "") + " " + (p.get("text") or "")
        boost = 1.6 if re.search(r"\d", blob) else 1.0
        if p.get("kind") == "measurement":
            boost *= 1.3
        p["_score"] = (p["rank"] or 0.0) * boost
    passages.sort(key=lambda p: -p["_score"])
    seen: set[tuple] = set()
    uniq: list[dict] = []
    for p in passages:
        key = (p["doc"], (p["snippet"] or p["text"] or "")[:60])
        if key in seen:
            continue
        seen.add(key)
        p.pop("_score", None)
        uniq.append(p)
    uniq = uniq[:limit]
    return {"query": query, "n": len(uniq),
            "docs": sorted({p["doc"] for p in uniq if p["doc"]}),
            "entities": entities, "expanded_terms": extra_terms,
            "passages": uniq}


# ── поиск измерений по числовому условию ────────────────────────────────

# Нормализация единиц к канонической единице величины: unit(lower) → множитель.
# Пустая единица считается канонической (контракт: SI/каноника в БД). Величины
# вне этого реестра фильтруются без конверсии — только по совпадению единиц
# было бы нечестно смешивать g/l с %, поэтому строки с непереводимой единицей
# пропускаются.
_MEAS_CANON_UNITS: dict[str, tuple[str, dict[str, float]]] = {
    "temperature": ("K", {"k": 1.0, "": 1.0}),
    "concentration": ("g/l", {"g/l": 1.0, "g/л": 1.0, "г/л": 1.0,
                              "g/dm3": 1.0, "g/dm³": 1.0, "г/дм³": 1.0,
                              "mg/l": 0.001, "мг/л": 0.001, "mg/dm3": 0.001,
                              "мг/дм³": 0.001, "": 1.0}),
    "recovery_degree": ("%", {"%": 1.0, "": 1.0}),
    "element_content": ("%", {"%": 1.0, "% масс.": 1.0, "": 1.0}),
    "current_density": ("A/m2", {"a/m2": 1.0, "a/m²": 1.0, "а/м²": 1.0, "": 1.0}),
    "particle_size": ("µm", {"um": 1.0, "µm": 1.0, "мкм": 1.0, "": 1.0}),
    "ph": ("", {"": 1.0}),
}


def search_measurements(store: Store, quantity: str,
                        value_from: float | None = None,
                        value_to: float | None = None,
                        query: str | None = None,
                        limit: int = 20) -> dict:
    """Измерения по числовому условию: величина + диапазон значений в её
    канонической единице (temperature — K, concentration — g/l, доли — %).

    Значение строки: nominal, иначе середина min–max, иначе одна из границ.
    Единицы приводятся по реестру _MEAS_CANON_UNITS; строки с единицей вне
    реестра пропускаются (несопоставимы без конверсии). Непустой `query`
    сужает выдачу по вхождению значимых термов в сниппет/документ; если
    сужение опустошает результат, условие остаётся главным — вернём без
    текстового сужения с пометкой в note."""
    qk = resolve_quantity(store, quantity)
    if qk is None:
        return {"query": query or "", "quantity_kind": None, "n": 0,
                "docs": [], "passages": [],
                "note": f"величина «{quantity}» не найдена в реестре"}
    canon_unit, conv = _MEAS_CANON_UNITS.get(qk, ("", {}))
    rows = store.query("""
        SELECT r.quantity_kind, r.value_min, r.value_nominal, r.value_max,
               r.unit, r.prov->>'snippet' AS snippet,
               r.prov->>'locator' AS locator, d.filename AS doc,
               d.okf_raw_path AS okf_path, d.country, d.year, d.lang
        FROM experiments.results r
        LEFT JOIN experiments.experiments e ON e.id = r.experiment_id
        LEFT JOIN experiments.documents d ON d.id = e.document_id
        WHERE r.superseded_by IS NULL AND r.quantity_kind = %s
          AND COALESCE(r.value_nominal, r.value_min, r.value_max) IS NOT NULL""",
        (qk,))
    hits: list[dict] = []
    for r in rows:
        unit = (r["unit"] or "").strip().lower()
        factor = conv.get(unit, None) if conv else 1.0
        if factor is None:
            continue
        v = r["value_nominal"]
        if v is None:
            lo, hi = r["value_min"], r["value_max"]
            v = (lo + hi) / 2 if lo is not None and hi is not None else (
                lo if lo is not None else hi)
        v_canon = float(v) * factor
        if value_from is not None and v_canon < value_from:
            continue
        if value_to is not None and v_canon > value_to:
            continue
        val = _fmt_value(r)
        hits.append({
            "kind": "measurement",
            "text": f"{qk} = {val} {r['unit'] or canon_unit}".strip(),
            "snippet": (r["snippet"] or "")[:300], "doc": _clean_doc(r["doc"]),
            "locator": r["locator"], "okf_path": r["okf_path"],
            "country": r["country"], "year": r["year"], "lang": r["lang"],
            "value": val, "unit": r["unit"] or canon_unit,
            "rank": 0.0, "_v": v_canon})
    note = None
    terms = _content_terms(query or "")
    if terms:
        narrowed = [h for h in hits if any(
            _term_in(((h["snippet"] or "") + " " + (h["doc"] or "")).lower(), t)
            for t in terms)]
        if narrowed:
            hits = narrowed
        elif hits:
            note = "текст запроса не сузил выдачу — показано всё по условию"
    hits.sort(key=lambda h: h["_v"])
    seen: set[tuple] = set()
    uniq: list[dict] = []
    for h in hits:
        key = (h["doc"], (h["snippet"] or h["text"])[:60])
        if key in seen:
            continue
        seen.add(key)
        h.pop("_v", None)
        uniq.append(h)
    uniq = uniq[:limit]
    return {"query": query or "", "quantity_kind": qk, "unit": canon_unit,
            "n": len(uniq), "docs": sorted({h["doc"] for h in uniq if h["doc"]}),
            "passages": uniq, "note": note}


def coverage(store: Store) -> dict:
    counts = {t: store.scalar(f"SELECT count(*) FROM experiments.{t}")
              for t in ("documents", "materials", "experiments", "results",
                        "conclusions", "labs", "edges_semantic")}
    counts["edges_total"] = store.scalar("SELECT count(*) FROM experiments.edges")
    prov_ok = store.scalar(
        "SELECT count(*) FROM experiments.results"
        " WHERE length(prov->>'snippet') > 0")
    hitl = [r["name"] for r in store.query(
        "SELECT name FROM experiments.quantity_kinds WHERE status='needs_review'")]
    docs_linked = store.scalar("""
        SELECT count(DISTINCT d.id) FROM experiments.documents d
        JOIN experiments.experiments e ON e.document_id = d.id""")
    return {"counts": counts,
            "provenance_coverage": (prov_ok or 0) == (counts["results"] or 0),
            "documents_linked": docs_linked,
            "hitl_quantity_kinds": hitl,
            "risk_zones": risk_zones(store)}
