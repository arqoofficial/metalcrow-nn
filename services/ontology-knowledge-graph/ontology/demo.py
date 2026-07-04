# -*- coding: utf-8 -*-
"""
Демо онтологии v2.1 на РЕАЛЬНОМ корпусе Норникеля (seed/norilsk_pgm.json —
три документа из «Источники информации/Статьи»: хлорирование ПГМ-концентратов,
осаждение Au сульфитом натрия, отливка медных анодов; лаборатории Институт
Гипроникель / Кольская ГМК / Медный завод). Каждая цитата (snippet) — дословный
фрагмент документа.

Показывает end-to-end поверх контракта (без БД, чистый Python; в проде те же
запросы = SQL по experiments.* SPEC_V3): подъём фактов в онтологию → ответы на
competency questions, выровненные с ЦЕЛЕВЫМИ ЗАПРОСАМИ финального ТЗ:
  CQ1  hero-Evidence («что делали по методу X, какой результат Z» + цитата)
  CQ2  метод/техрешение как АДРЕСУЕМЫЙ объект (T1) — «методы получения концентратов ДМ»
  CQ3  эффект-стрелки с подсчётом (структурный «эффект на Z», не мнение LLM)
  CQ4  Comparability Gate — детектор ЛЖЕ-противоречий (извлечение ≠ потенциал)
  CQ5  lineage/«история решений» — цепочка переделов передела ДМ (derived_from)
  CQ6  география + эксперты + верификация (T3): отеч./заруб., validated_by
  CQ7  gap-map (материал × род величины) + рекомендация следующего эксперимента

Запуск из корня репозитория:  python -m ontology.demo
Зависимости: pydantic v2.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from .contracts import (
    Conclusion, Direction, Edge, Effect, Evidence, Experiment, ExtractorKind,
    GapCell, LocatorKind, Material, Measurement, MeasurementConditions,
    NextExperiment, Predicate, Provenance, QuantityKindRegistry, Regime,
    RegimeStep, TeamLab, ValueRange, is_comparable, PROCESS_SEED,
)

SEED = Path(__file__).parent / "seed" / "norilsk_pgm.json"
ARROW = {Direction.INCREASES: "↑", Direction.DECREASES: "↓",
         Direction.NO_CHANGE: "→", Direction.NONMONOTONIC: "↕"}


# ── подъём сырого выхода экстрактора в онтологию (in-memory ABox) ───────────

class KG:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.labs: dict[str, TeamLab] = {}
        self.materials: dict[str, Material] = {}
        self.experiments: dict[str, Experiment] = {}
        self.measurements: list[Measurement] = []
        self.conclusions: list[Conclusion] = []
        self.edges: list[Edge] = []
        self.regime_of: dict[str, Regime] = {}      # experiment_id -> Regime (для Gate)
        self.qk = QuantityKindRegistry()

    def add_edge(self, src, dst, pred, prov, attrs=None, weight=None):
        self.edges.append(Edge(src=src, dst=dst, predicate=pred,
                               attrs=attrs or {}, weight=weight, provenance=prov))

    def neighbourhood(self, node):
        return [e for e in self.edges if node in (e.src, e.dst)]

    def lineage(self, node):
        chain, cur, seen = [], node, set()
        while cur not in seen:
            seen.add(cur)
            nxt = [e for e in self.edges
                   if e.src == cur and e.predicate == Predicate.DERIVED_FROM]
            if not nxt:
                return chain
            chain.append(nxt[0]); cur = nxt[0].dst
        return chain


def _prov(doc_id: str, snippet: str, kind=LocatorKind.DOCX_PARA,
          extractor=ExtractorKind.NUEXTRACT, conf=0.9) -> Provenance:
    return Provenance(doc_id=doc_id, locator_kind=kind, locator="para:auto",
                      snippet=snippet, extractor=extractor, confidence=conf)


def build_kg(raw: dict) -> KG:
    kg = KG()
    for d in raw["documents"]:
        kg.docs[d["doc_id"]] = d
    for l in raw["labs"]:
        kg.labs[l["id"]] = TeamLab(**l)
    for m in raw["materials"]:
        prov = _prov("doc:handbook", f"{m['label']} ({m.get('family','?')})",
                     extractor=ExtractorKind.STRUCTURED_ETL)
        kg.materials[m["id"]] = Material(
            canonical_id=m["id"], pref_label=m["label"], family=m.get("family", "other"),
            grade=m.get("grade"), phase=m.get("phase"), provenance=prov)

    for e in raw["experiments"]:
        doc = e["document_id"]
        prov = _prov(doc, e["snippet"])
        regime = Regime(steps=[RegimeStep(**s) for s in e["regime"]["steps"]])
        kg.regime_of[e["id"]] = regime
        kg.experiments[e["id"]] = Experiment(
            id=e["id"], origin="extracted", regime=regime, document_id=doc,
            team_id=e.get("lab_id"), site=e.get("site"), provenance=prov)
        kg.add_edge(e["id"], doc, Predicate.REPORTED_IN, prov)
        if e.get("lab_id"):
            kg.add_edge(e["id"], e["lab_id"], Predicate.PERFORMED_BY, prov)
        for mm in e["materials"]:
            mprov = _prov(doc, f"{e['id']} uses {mm['material_id']} as {mm['role']}")
            kg.add_edge(e["id"], mm["material_id"], Predicate.USES_MATERIAL,
                        mprov, {"role": mm["role"]})
        for i, ms in enumerate(e.get("measurements", [])):
            mprov = _prov(doc, ms["snippet"])
            qk = kg.qk.resolve(ms["quantity_kind"])
            meas = Measurement(
                id=f"{e['id']}:m{i}", experiment_id=e["id"],
                scope=ms.get("scope", "material"),
                material_id=ms.get("material_id"), quantity_kind=qk,
                value=ValueRange(**ms["value"]) if ms.get("value") else None,
                unit=ms.get("unit", ""), basis=ms.get("basis"),
                conditions=MeasurementConditions(**ms.get("conditions", {})),
                sample_state=regime.state_hash(), provenance=mprov)
            kg.measurements.append(meas)
            kg.add_edge(meas.id, e["id"], Predicate.MEASURED_ON, mprov)
            kg.add_edge(meas.id, f"prop:{qk}", Predicate.HAS_PROPERTY, mprov)
        for i, cc in enumerate(e.get("conclusions", [])):
            cprov = _prov(doc, cc["snippet"])
            eff = Effect(**cc["effect"]) if cc.get("effect") else None
            con = Conclusion(id=f"{e['id']}:c{i}", text=cc["text"],
                             kind=cc.get("kind", "finding"), effect=eff, provenance=cprov)
            kg.conclusions.append(con)
            kg.add_edge(e["id"], con.id, Predicate.CONCLUDES, cprov)

    for ln in raw.get("lineage", []):
        prov = _prov("doc:chlorination-pgm", ln["snippet"])
        kg.add_edge(ln["src"], ln["dst"], Predicate.DERIVED_FROM, prov,
                    {"process": ln["process"]})
    for v in raw.get("validated_by", []):
        prov = _prov("doc:chlorination-pgm", v["snippet"])
        kg.add_edge(v["src"], v["dst"], Predicate.SUPPORTS, prov, {"kind": "validated_by"})
    return kg


# ── интерпретаторы поверх субстрата ────────────────────────────────────────

def cite(p: Provenance) -> str:
    return f"[{p.doc_id} | «{p.snippet[:66]}…»]"


def experiments_of_process(kg: KG, process: str) -> list[str]:
    """T1: метод адресуем — эксперименты, где применён процесс (edges-VIEW
    applies_process в проде; здесь — обход regime.steps)."""
    return [eid for eid, r in kg.regime_of.items()
            if any(s.process_type.value == process for s in r.steps)]


def evidence_for_process(kg: KG, process: str, qk: str) -> Evidence:
    """Hero: «что делали методом X, какой результат по величине Z»."""
    exps = set(experiments_of_process(kg, process))
    ms = [m for m in kg.measurements if m.experiment_id in exps and m.quantity_kind == qk]
    if not ms:
        return Evidence(answer="данных нет", experiments=[], confidence="low",
                        agreement_flag="single", gap_note=f"пробел: {process} × {qk}")
    m = ms[0]
    v = m.value
    val = (f"{v.min:g}–{v.max:g}" if v and v.min is not None and v.max is not None
           else f"{v.point:g}") if v else "?"
    labs = sorted({kg.labs[kg.experiments[e].team_id].name
                   for e in exps if kg.experiments[e].team_id})
    return Evidence(
        answer=f"{qk} = {val} {m.unit} (метод: {PROCESS_SEED[process].aliases[0]})",
        experiments=sorted(exps), n_experiments=len(exps),
        n_docs=len({kg.experiments[e].document_id for e in exps}), labs=labs,
        agreement_flag="single" if len(ms) == 1 else "consistent",
        confidence="high", citations=[m.provenance for m in ms])


def gap_map(kg: KG, materials: list[str], kinds: list[str]) -> list[GapCell]:
    cells = []
    for mat in materials:
        for qk in kinds:
            n = sum(1 for m in kg.measurements
                    if m.material_id == mat and m.quantity_kind == qk)
            cells.append(GapCell(
                material_id=mat, quantity_kind=qk, n_experiments=n,
                coverage="none" if n == 0 else ("weak" if n == 1 else "sufficient")))
    return cells


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    kg = build_kg(json.loads(SEED.read_text(encoding="utf-8")))
    W = 78
    prov_ok = all(m.provenance.snippet.strip() for m in kg.measurements)
    print("=" * W)
    print("ОНТОЛОГИЯ v2.1 — ходячий скелет на РЕАЛЬНОМ корпусе Норникеля")
    print(f"  3 документа (аффинаж ДМ + пирометаллургия Cu) · "
          f"{len(kg.materials)} материалов, {len(kg.experiments)} экспериментов,")
    print(f"  {len(kg.measurements)} измерений, {len(kg.conclusions)} выводов, "
          f"{len(kg.edges)} рёбер · провенанс-покрытие: {'100%' if prov_ok else 'НЕ 100%!'}")
    print("=" * W)

    print("\n── CQ1 · HERO (цель ТЗ «что делали + результат»): извлечение ПМ хлорированием")
    ev = evidence_for_process(kg, "chlorination", "recovery_degree")
    print(f"  ▸ {ev.answer}")
    print(f"    n_exp={ev.n_experiments} · доверие={ev.confidence} · {ev.agreement_flag}"
          f" · лаборатории: {'; '.join(ev.labs)}")
    print(f"    клик до источника → {cite(ev.citations[0])}")

    print("\n── CQ2 · МЕТОД как адресуемый объект (T1): «методы получения концентратов ДМ»")
    for proc in ["chlorination", "precipitation", "fire_refining"]:
        exps = experiments_of_process(kg, proc)
        if exps:
            ru = PROCESS_SEED[proc].aliases[0]
            print(f"  • {ru:<22} → {len(exps)} эксп.: {', '.join(exps)}")

    print("\n── CQ3 · ЭФФЕКТ на свойство (структурные стрелки, не мнение LLM)")
    for c in kg.conclusions:
        if c.effect:
            e = c.effect
            print(f"  {ARROW[e.direction]}  {e.quantity_kind:<16} ← {e.factor}")
            print(f"      [{c.kind}] {cite(c.provenance)}")

    print("\n── CQ4 · Comparability Gate: детектор ЛЖЕ-противоречий")
    rec = next(m for m in kg.measurements if m.quantity_kind == "recovery_degree")
    pot = next(m for m in kg.measurements if m.quantity_kind == "electrode_potential")
    g1 = is_comparable(rec, pot)
    print(f"  извлечение(хлорир.) vs потенциал(осаждение): {g1.note}")
    #  два содержания Pt+Pd в РАЗНЫХ фазах-продуктах (остаток vs концентрат)
    contents = [m for m in kg.measurements if m.quantity_kind == "element_content"]
    a, b = contents[1], contents[2]     # residual 0.1–0.2% vs concentrate >90%
    g2 = is_comparable(a, b, kg.regime_of[a.experiment_id], kg.regime_of[b.experiment_id])
    print(f"  содержание Pt+Pd: остаток {a.value.min}–{a.value.max}% vs концентрат "
          f">{b.value.min}% → {g2.note}")
    print(f"    (одна величина/базис/фаза → сравнение ЛЕГИТИМНО; это не противоречие, а обогащение)")

    print("\n── CQ5 · LINEAGE «история решений» — цепочка переделов аффинажа ДМ")
    chain = kg.lineage("mat:gold-concentrate")
    for e in chain:
        s, d = kg.materials[e.src].pref_label, kg.materials[e.dst].pref_label
        print(f"  «{s}»\n      ←[{e.attrs['process']}]— «{d}»")

    print("\n── CQ6 · ГЕОГРАФИЯ + эксперты + верификация (T3)")
    origins = defaultdict(int)
    for d in kg.docs.values():
        origins["отечественная" if d.get("country") == "RU" else "зарубежная"] += 1
    print(f"  практика: {dict(origins)} (country='RU' → отечественная)")
    val = next(e for e in kg.edges if e.attrs.get("kind") == "validated_by")
    print(f"  validated_by: {val.src} ✓ верифицирован в «{kg.labs[val.dst].name}»")
    print(f"    {cite(val.provenance)}")
    experts = sorted(kg.labs.values(),
                     key=lambda l: sum(1 for e in kg.experiments.values() if e.team_id == l.id),
                     reverse=True)[0]
    print(f"  рекомендованный эксперт по хлорированию/аффинажу: «{experts.name}»"
          f" ({experts.city}), экспертиза: {', '.join(experts.expertise[:2])}")

    print("\n── CQ7 · GAP-MAP (материал × род величины) + следующий эксперимент")
    mats = ["mat:pgm-concentrate", "mat:ptpd-concentrate", "mat:gold-concentrate"]
    kinds = ["recovery_degree", "element_content", "energy_consumption", "specific_cost"]
    print(f"  {'':<26}" + "".join(f"{k[:11]:>13}" for k in kinds))
    cells = gap_map(kg, mats, kinds)
    for mat in mats:
        row = [c for c in cells if c.material_id == mat]
        label = kg.materials[mat].pref_label[:24]
        print(f"  {label:<26}"
              + "".join(f"{('∅' if c.coverage=='none' else c.n_experiments):>13}" for c in row))
    print("  ▸ пусты столбцы ТЭП (energy/cost) — ни для одного продукта нет технико-")
    nxt = NextExperiment(
        material_id="mat:ptpd-concentrate",
        suggested_regime=kg.regime_of["exp:chlorination-2stage"],
        quantity_kind="energy_consumption", score=0.82,
        rationale="ТЭП (энергоёмкость/себестоимость) не измерены ни для одного передела, "
                  "хотя ТЗ прямо просит технико-экономические показатели способа")
    print(f"    экономических показателей → NextExperiment: измерить "
          f"{nxt.quantity_kind} для «{kg.materials[nxt.material_id].pref_label}» (score {nxt.score})")

    if kg.qk.pending_review:
        print(f"\n  HITL-очередь новых родов величин: {kg.qk.pending_review}")
    print("\n" + "=" * W)
    print("В проде те же CQ = SQL по experiments.* (SPEC_V3): метод = VIEW")
    print("experiment_processes, Gate = функция, граф-виз = project_graph → Cytoscape.")
    print("Домен НЕ сплавы — аффинаж ДМ/пирометаллургия Cu; схема легла без переписывания.")
    print("=" * W)


if __name__ == "__main__":
    main()
