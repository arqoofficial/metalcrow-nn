# -*- coding: utf-8 -*-
"""
Тесты query-слоя на живом Postgres (контейнер onto_pg).
Каждый тест-класс работает на свежей схеме: seed norilsk_pgm.json + синтетический
батч с (а) настоящим противоречием среди сопоставимых измерений, (б) несопоставимой
парой, которую Gate обязан НЕ пометить противоречием.

Запуск: python -m pytest ontology/tests -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontology import query as q
from ontology.loader import load_batch, seed_registries
from ontology.store import Store

SEED = Path(__file__).parent.parent / "seed" / "norilsk_pgm.json"

# Синтетика: тот же материал/процесс/состояние → сопоставимо; извлечение 96 vs 60
# (противоречие) + твёрдость HV30 vs HRC (несопоставимо, НЕ противоречие) +
# выводы с противоположным direction по одному (величина, фактор).
SYNTH = {
    "extractor": "manual",
    "documents": [
        {"doc_id": "doc:synth-a", "title": "Отчёт А", "doc_type": "internal_report",
         "country": "RU", "lang": "ru", "year": 2024},
        {"doc_id": "doc:synth-b", "title": "Report B", "doc_type": "article",
         "country": "AU", "lang": "en", "year": 2025},
    ],
    "materials": [
        {"id": "mat:synth-conc", "label": "Синтетический концентрат", "family": "concentrate"},
    ],
    "claims": [
        {"document_id": "doc:synth-b", "process": "injection",
         "text": "Injection into deep horizons is applicable for saline mine water.",
         "kind": "recommendation",
         "snippet": "injection into deep horizons is applicable"},
    ],
    "experiments": [
        {
            "id": "exp:synth-a", "document_id": "doc:synth-a",
            "regime": {"steps": [{"process_type": "leaching"}]},
            "materials": [{"material_id": "mat:synth-conc", "role": "input"}],
            "snippet": "выщелачивание концентрата, серия А",
            "measurements": [
                {"quantity_kind": "recovery_degree", "material_id": "mat:synth-conc",
                 "value": {"nominal": 96}, "unit": "%",
                 "snippet": "извлечение составило 96 %"},
                {"quantity_kind": "hardness", "material_id": "mat:synth-conc",
                 "value": {"nominal": 420}, "unit": "HV30", "scale": "HV30",
                 "snippet": "твёрдость осадка 420 HV30"},
            ],
            "conclusions": [
                {"text": "Повышение температуры увеличивает извлечение.",
                 "kind": "finding",
                 "effect": {"quantity_kind": "recovery_degree",
                            "direction": "increases", "factor": "температура"},
                 "snippet": "с ростом температуры извлечение растёт"},
            ],
        },
        {
            "id": "exp:synth-b", "document_id": "doc:synth-b",
            "regime": {"steps": [{"process_type": "leaching"}]},
            "materials": [{"material_id": "mat:synth-conc", "role": "input"}],
            "snippet": "leaching of concentrate, series B",
            "measurements": [
                {"quantity_kind": "recovery_degree", "material_id": "mat:synth-conc",
                 "value": {"nominal": 60}, "unit": "%",
                 "snippet": "recovery was only 60 %"},
                {"quantity_kind": "hardness", "material_id": "mat:synth-conc",
                 "value": {"nominal": 43}, "unit": "HRC", "scale": "HRC",
                 "snippet": "hardness of residue 43 HRC"},
            ],
            "conclusions": [
                {"text": "Higher temperature decreases recovery.",
                 "kind": "finding",
                 "effect": {"quantity_kind": "recovery_degree",
                            "direction": "decreases", "factor": "температура"},
                 "snippet": "recovery drops as temperature rises"},
            ],
        },
    ],
}


@pytest.fixture(scope="module")
def store():
    s = Store.open()
    s.reset()
    seed_registries(s)
    load_batch(s, json.loads(SEED.read_text(encoding="utf-8")))
    load_batch(s, SYNTH)
    yield s
    s.close()


# ── CQ1: hero-evidence ───────────────────────────────────────────────────

def test_evidence_hero_by_process(store):
    ev = q.evidence(store, process="хлорирование", quantity_kind="извлечение")
    assert ev.n_experiments == 1
    assert "95" in ev.answer and "97" in ev.answer
    assert ev.citations and ev.citations[0].snippet   # цитата дословная


def test_evidence_gap_when_no_data(store):
    ev = q.evidence(store, process="флотация", quantity_kind="вязкость")
    assert ev.answer == "данных нет" and ev.gap_note


def test_evidence_numeric_filter(store):
    ev = q.evidence(store, quantity_kind="recovery_degree", value_op=">=", value=90)
    assert ev.n_experiments >= 1          # 96% попадает, 60% отфильтровано
    ev2 = q.evidence(store, quantity_kind="recovery_degree", value_op="<=", value=50)
    assert ev2.answer == "данных нет"


# ── ретрив пассажей: всегда с ссылкой на источник ────────────────────────

def test_search_passages_returns_source(store):
    r = q.search_passages(store, "хлорирование извлечение платины")
    assert r["n"] >= 1
    p = r["passages"][0]
    assert p["doc"] and (p["snippet"] or p["text"])   # источник + текст цитаты
    assert r["docs"], "список документов-источников не должен быть пуст"


def test_search_passages_empty_query(store):
    assert q.search_passages(store, "!!! ??? …")["n"] == 0


def test_hybrid_index_rows_and_fallback(store):
    # индекс собирается из выводов/измерений; без эмбеддингов (модель не грузим)
    # поиск деградирует на лексический путь без ошибок
    from ontology import hybrid_index as hx
    hx.ensure_index(store)
    n = hx.rebuild_rows(store)
    assert n > 0
    assert hx.index_ready(store) is False   # эмбеддинги не считали
    r = q.search_passages(store, "хлорирование извлечение платины")
    assert r["n"] >= 1 and r["passages"][0]["doc"]


# ── CQ2: метод как объект ────────────────────────────────────────────────

def test_processes_addressable(store):
    rows = store.query("SELECT DISTINCT process_type FROM experiments.experiment_processes")
    procs = {r["process_type"] for r in rows}
    assert {"chlorination", "precipitation", "leaching"} <= procs


# ── CQ3: противоречия только среди сопоставимого ─────────────────────────

def test_contradiction_found_for_comparable(store):
    flags = q.find_contradictions(store)
    meas = [f for f in flags if f["type"] == "measurement"]
    assert any(f["quantity_kind"] == "recovery_degree" and f["delta_rel"] > 0.3
               for f in meas), "96% vs 60% на одном материале должно быть флагом"


def test_hardness_scales_not_contradiction(store):
    flags = q.find_contradictions(store)
    assert not any(f.get("quantity_kind") == "hardness" and f["type"] == "measurement"
                   for f in flags), "HV30 vs HRC несопоставимы — Gate обязан блокировать"


def test_conclusion_level_contradiction(store):
    flags = q.find_contradictions(store)
    concl = [f for f in flags if f["type"] == "conclusion"]
    assert any(f["quantity_kind"] == "recovery_degree" for f in concl)


def test_gate_check_api(store):
    rows = store.query(
        "SELECT id::text, scale FROM experiments.results WHERE quantity_kind='hardness'")
    assert len(rows) == 2
    g = q.gate_check(store, rows[0]["id"], rows[1]["id"])
    assert not g.comparable and "scale" in g.blocking_dims


# ── CQ4/CQ5: подграф и провенанс ─────────────────────────────────────────

def test_subgraph_neighbourhood(store):
    g = q.get_subgraph(store, "Золотой концентрат", depth=1)
    assert g["nodes"] and g["edges"]
    assert any(e["predicate"] == "derived_from" for e in g["edges"])


def test_full_provenance_coverage(store):
    n = store.scalar("SELECT count(*) FROM experiments.results"
                     " WHERE prov->>'snippet' IS NULL OR prov->>'snippet' = ''")
    assert n == 0


# ── CQ6: lineage ─────────────────────────────────────────────────────────

def test_lineage_chain(store):
    chain = q.lineage(store, "Золотой концентрат")
    assert len(chain) == 2
    assert chain[0]["process"] == "precipitation"
    assert chain[1]["process"] == "chlorination"


# ── CQ7: география и эксперты ────────────────────────────────────────────

def test_compare_practice_geo(store):
    cmp = q.compare_practice(store, "выщелачивание")
    assert cmp["n_domestic_docs"] >= 1 and cmp["n_foreign_docs"] >= 1


def test_experts_by_topic(store):
    experts = q.find_experts_by_topic(store, "хлорирование")
    assert experts and "Гипроникель" in experts[0]["name"]


# ── CQ8: пробелы и зоны риска ────────────────────────────────────────────

def test_gaps_structure(store):
    gaps = q.find_gaps(store)
    assert gaps["empty_cells"], "ТЭП-столбцы должны быть пустыми ячейками"
    assert any(c["quantity_kind"] in ("energy_consumption", "specific_cost")
               for c in gaps["empty_cells"])
    assert gaps["low_coverage"]


def test_risk_zones(store):
    zones = q.risk_zones(store)
    assert zones and all(z["reasons"] for z in zones)


# ── CQ11: сравнение технологий ───────────────────────────────────────────

def test_compare_technologies_table(store):
    table = q.compare_technologies(store, ["хлорирование", "осаждение", "выщелачивание"])
    assert table
    row = table[0]
    assert {"method", "param", "value", "unit", "origin", "source_ref"} <= set(row)


# ── литобзор (P0.1) и покрытие ───────────────────────────────────────────

def test_literature_review_sections(store):
    lit = q.literature_review(store)
    assert {"by_method", "by_geo", "by_year", "consensus",
            "disagreements", "claims"} <= set(lit)
    assert lit["by_method"] and lit["claims"]
    assert lit["disagreements"], "синтетическое противоречие должно попасть в обзор"


def test_coverage_report(store):
    cov = q.coverage(store)
    assert cov["provenance_coverage"] is True
    assert cov["counts"]["documents"] == 5      # 3 seed + 2 synth
    assert cov["risk_zones"]


# ── таймлайн ─────────────────────────────────────────────────────────────

def test_timeline_by_process(store):
    tl = q.timeline(store, process="выщелачивание")
    assert len(tl) >= 2


# ── doc-level claims (инженерные утверждения без эксперимента) ───────────

def test_document_claim_loaded_without_experiment(store):
    n = store.scalar("SELECT count(*) FROM experiments.conclusions"
                     " WHERE experiment_id IS NULL AND document_id IS NOT NULL")
    assert n >= 1


def test_document_claim_in_review_and_compare(store):
    lit = q.literature_review(store, "injection")
    assert any("deep horizons" in c["text"] for c in lit["claims"])
    cmp = q.compare_practice(store, "закачка")
    assert cmp["n_foreign_docs"] >= 1


# ── evidence_profile: пространство решений + надёжность ──────────────────

def test_evidence_profile_envelope(store):
    prof = q.evidence_profile(store, "recovery_degree")
    assert prof["n_points"] >= 2
    assert prof["envelope"]["min"] <= 60 and prof["envelope"]["max"] >= 96
    assert prof["agreement"] in ("dispersed", "divergent")
    assert all(0 < p["reliability"] <= 1 for p in prof["points"])
    # точки отсортированы по надёжности
    rels = [p["reliability"] for p in prof["points"]]
    assert rels == sorted(rels, reverse=True)


def test_profile_respects_comparability_groups(store):
    prof = q.evidence_profile(store, "hardness")
    # HV30 и HRC — разные группы: в главной только одна точка
    assert prof["n_points"] == 1
    assert prof["other_comparability_groups"] >= 1


def test_divergence_zone_has_severity(store):
    flags = q.find_contradictions(store)
    meas = [f for f in flags if f["type"] == "measurement"]
    assert all(f.get("label") == "зона расхождения" and "severity" in f
               for f in meas)
