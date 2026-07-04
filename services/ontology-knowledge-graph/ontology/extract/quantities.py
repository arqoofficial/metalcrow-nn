# -*- coding: utf-8 -*-
"""
Канонизация имён величин (стандартизация свойств).

Экстракторы выдают свободные имена («извлечение никеля в медный концентрат»,
«nickel extraction», «коэффициенты запаса устойчивости», «appearance of
sinters», «string»). Слои канонизации:

  0. мусор схемы LLM → отбраковка измерения (kind=None);
  1. качественные наблюдения → kind='qualitative_observation'
     (не участвуют в числовой агрегации/Gate по значениям);
  2. точное совпадение с реестром (имя/алиас, RU/EN);
  3. паттерны «вид + предмет»: «извлечение X», «содержание X», «X content» →
     канон + subject; subject уходит в conditions.subject и становится осью
     Gate (извлечение Ni несопоставимо с извлечением Cu);
  4. паттерны-синонимы новых доменных видов (геомеханика, электрохимия...);
  5. подсказка по единице (HV→твёрдость, мг/л→концентрация);
  остальное — needs_review (HITL), как и раньше.

CLI-миграция уже загруженной БД:
    python -m ontology.extract.quantities --apply [--db ...]
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from typing import Optional

from ..contracts import QUANTITY_KINDS_SEED, QuantityKindDef

# ── новые канонические виды (дополнение сида под реальный корпус) ─────────

EXTRA_KINDS: dict[str, QuantityKindDef] = {
    d.name: d for d in [
        QuantityKindDef(name="temperature", unit_dim="temperature",
                        aliases=["температура", "temperature", "поле температуры"]),
        QuantityKindDef(name="pressure", unit_dim="pressure",
                        aliases=["давление", "pressure"]),
        QuantityKindDef(name="compressive_strength", unit_dim="pressure",
                        aliases=["предел прочности при сжатии",
                                 "прочность при одноосном сжатии",
                                 "compressive strength", "прочность на сжатие"]),
        QuantityKindDef(name="concentration", unit_dim="mass_concentration",
                        aliases=["концентрация", "concentration"]),
        QuantityKindDef(name="corrosion_rate", unit_dim="speed",
                        aliases=["скорость коррозии", "corrosion rate"]),
        QuantityKindDef(name="safety_factor", unit_dim="dimensionless",
                        aliases=["коэффициент запаса", "коэффициент запаса устойчивости",
                                 "коэффициенты запаса устойчивости", "safety factor"]),
        QuantityKindDef(name="stress", unit_dim="pressure",
                        aliases=["напряжение", "напряжения", "stress",
                                 "maximum principal stress", "главное напряжение"]),
        QuantityKindDef(name="strain", unit_dim="ratio",
                        aliases=["деформация", "деформации", "strain"]),
        QuantityKindDef(name="amplitude", unit_dim="length",
                        aliases=["амплитуда", "amplitude", "входная амплитуда",
                                 "выходная амплитуда"]),
        QuantityKindDef(name="period", unit_dim="time", aliases=["период", "period"]),
        QuantityKindDef(name="area", unit_dim="area", aliases=["площадь", "area"]),
        QuantityKindDef(name="diameter", unit_dim="length",
                        aliases=["диаметр", "diameter"]),
        QuantityKindDef(name="distance", unit_dim="length",
                        aliases=["расстояние", "distance"]),
        QuantityKindDef(name="particle_size", unit_dim="length",
                        aliases=["размер частиц", "средний размер частиц",
                                 "particle size", "d50", "крупность"]),
        QuantityKindDef(name="discharge_capacity", unit_dim="charge_capacity",
                        aliases=["разрядная ёмкость", "разрядная емкость",
                                 "discharge capacity", "discharge capacities"]),
        QuantityKindDef(name="gas_flow", unit_dim="volumetric_flow",
                        aliases=["расход газа", "gas flow", "дутьё", "blast velocity"]),
        QuantityKindDef(name="yield_fraction", unit_dim="ratio",
                        aliases=["выход", "yield"]),
        QuantityKindDef(name="efficiency", unit_dim="ratio",
                        aliases=["эффективность", "efficiency", "степень очистки"]),
        QuantityKindDef(name="solubility", unit_dim="mass_concentration",
                        aliases=["растворимость", "solubility"]),
        QuantityKindDef(name="redox_potential", unit_dim="voltage",
                        aliases=["овп", "окислительно-восстановительный потенциал",
                                 "redox potential", "orp"]),
        QuantityKindDef(name="frequency", unit_dim="frequency",
                        aliases=["частота", "frequency", "fluctuation frequency"]),
        QuantityKindDef(name="mass", unit_dim="mass", aliases=["масса", "mass"]),
        QuantityKindDef(name="time_duration", unit_dim="time",
                        aliases=["время", "длительность", "time", "duration"]),
        QuantityKindDef(name="qualitative_observation", unit_dim="none",
                        aliases=["наблюдение", "observation"]),
    ]
}

ALL_KINDS: dict[str, QuantityKindDef] = {**QUANTITY_KINDS_SEED, **EXTRA_KINDS}

# ── слои правил ───────────────────────────────────────────────────────────

_JUNK = {"string", "verbatim-string", "value", "число", "unknown", "n/a", "none",
         "property", "quantity", "параметр", "показатель", "performance"}

_QUALITATIVE = re.compile(
    r"appearance|внешний вид|форм[аы]|локализация|presence|наличие|foaming|"
    r"вспенивание|характер|pattern|distortion|осадок$|поведение", re.I)

# «вид + предмет»: канон + subject
_KIND_SUBJECT = [
    # специфичные — раньше общих (иначе «при сжатии» утечёт в tensile_strength)
    (re.compile(r".*(сжати|compressive).*", re.I), "compressive_strength"),
    (re.compile(r"^(?P<s>.*?)[_\s]*(давление|pressure)[_\s]*(?P<s2>.*)$", re.I),
     "pressure"),
    (re.compile(r"^(?:степень\s+)?извлечени[еяю]\s+(?P<s>.+?)(?:\s+в\s+.+)?$", re.I),
     "recovery_degree"),
    (re.compile(r"^(?P<s>\w+?)[_\s]extraction(?:[_\s].*)?$", re.I), "recovery_degree"),
    (re.compile(r"^extraction[_\s]of[_\s](?P<s>.+)$", re.I), "recovery_degree"),
    (re.compile(r"^содержани[ея]\s+(?P<s>.+?)(?:\s+в\s+.+)?$", re.I), "element_content"),
    (re.compile(r"^(?P<s>[a-zа-яё\d]+?)[_\s]content(?:[_\s].*)?$", re.I), "element_content"),
    (re.compile(r"^концентраци[яи]\s+(?P<s>.+)$", re.I), "concentration"),
    (re.compile(r"^(?:массовая\s+доля|доля)\s+(?P<s>.+)$", re.I), "phase_fraction"),
    (re.compile(r"^расход\s+(?P<s>.+)$", re.I), "flow_rate"),
    (re.compile(r"^(?P<s>.+?)[_\s]+(?:pulp[_\s])?flow$", re.I), "flow_rate"),
    (re.compile(r"^скорость\s+(?:потока\s+)?(?P<s>.+)$", re.I), "flow_rate"),
    (re.compile(r"^(?:потери|loss(?:es)?[_\s]of)\s*(?P<s>.+)$", re.I), "yield_fraction"),
    (re.compile(r"^выход\s+(?P<s>.+?)(?:,.*)?$", re.I), "yield_fraction"),
    (re.compile(r"^эффективность\s+(?P<s>.+)$", re.I), "efficiency"),
    (re.compile(r"^растворимость\s+(?P<s>.+)$", re.I), "solubility"),
    (re.compile(r"^температура\s+(?P<s>.+)$", re.I), "temperature"),
    (re.compile(r"^амплитуда\s+(?P<s>.+)$", re.I), "amplitude"),
    (re.compile(r"^(?:масса|mass[_\s]of)\s+(?P<s>.+)$", re.I), "mass"),
    (re.compile(r"^time[_\s]to[_\s](?P<s>.+)$", re.I), "time_duration"),
    (re.compile(r"^время\s+(?P<s>.+)$", re.I), "time_duration"),
]

_UNIT_HINTS = [
    (re.compile(r"^HV\d*$|^HRC$|^HR[AB]$|^HB$|^HK$", re.I), "hardness"),
    (re.compile(r"^(мг|г|kg|кг)/(л|дм3|дм³|m3|м3|м³)$", re.I), "concentration"),
    (re.compile(r"^(м3|м³|m3)/(ч|час|h)$", re.I), "flow_rate"),
    (re.compile(r"^°?[CС]$|^K$|^К$", re.I), "temperature"),
    # МПа/ГПа сознательно НЕ мапим: stress vs pressure vs прочность неразличимы
    (re.compile(r"^(мкм|um|µm|нм|nm|мм)$", re.I), "particle_size"),
]

_alias_index: dict[str, str] = {}
for _d in ALL_KINDS.values():
    for _a in [_d.name, *_d.aliases]:
        _alias_index[_a.strip().lower().replace("_", " ")] = _d.name


@dataclass
class Canon:
    kind: Optional[str]          # None = отбраковать измерение
    subject: Optional[str] = None
    method: str = "exact"        # exact|pattern|unit|qualitative|junk|unresolved
    confidence: float = 1.0


# ── нормализация предмета (subject): элементы и группы металлов ───────────
# Стемы покрывают падежи: «никеля/никелем/nickel» → Ni. Порядок не важен —
# берётся самый длинный подошедший стем внутри токена.

_SUBJECT_STEMS: dict[str, str] = {
    "никел": "Ni", "nickel": "Ni", "ni": "Ni",
    "мед": "Cu", "copper": "Cu", "cu": "Cu",
    "кобальт": "Co", "cobalt": "Co", "co": "Co",
    "желез": "Fe", "iron": "Fe", "fe": "Fe",
    "мышьяк": "As", "arsenic": "As", "as": "As",
    "сер": "S", "sulfur": "S", "sulphur": "S", "s": "S",
    "кислород": "O", "oxygen": "O", "o2": "O",
    "золот": "Au", "gold": "Au", "au": "Au",
    "серебр": "Ag", "silver": "Ag", "ag": "Ag",
    "платин": "Pt", "platinum": "Pt", "pt": "Pt",
    "паллад": "Pd", "palladium": "Pd", "pd": "Pd",
    "род": "Rh", "rhodium": "Rh", "rh": "Rh",
    "цинк": "Zn", "zinc": "Zn", "zn": "Zn",
    "свинц": "Pb", "свинец": "Pb", "lead": "Pb", "pb": "Pb",
    "магни": "Mg", "magnesium": "Mg", "mgo": "MgO", "mg": "Mg",
    "кремни": "Si", "silicon": "Si", "sio2": "SiO2", "si": "Si",
    "кальци": "Ca", "calcium": "Ca", "ca": "Ca",
    "углерод": "C", "carbon": "C",
    "хром": "Cr", "chromium": "Cr", "cr": "Cr",
    "мпг": "PGM", "pgm": "PGM", "платиноид": "PGM",
    "пм": "PGM",                       # «сумма ПМ» в аффинажных текстах
    "дм": "precious", "драгоценн": "precious",
    "цм": "non-ferrous", "цветн": "non-ferrous",
}
_SUBJECT_STEMS_ORDERED = sorted(_SUBJECT_STEMS, key=len, reverse=True)


def normalize_subject(raw: Optional[str]) -> Optional[str]:
    """«никеля» → Ni, «драгоценных металлов» → precious, «sulfuric acid» —
    как есть (не металл-предмет). Многословный subject: нормализуются токены,
    известные словарю; служебные слова («металлов», «в», «раствор») опускаются,
    если остался значимый канон."""
    if not raw:
        return None
    tokens = re.split(r"[\s,_/]+", raw.strip().lower())
    canon: list[str] = []
    other: list[str] = []
    for t in tokens:
        t = t.strip(" .()[]")
        if not t or t in ("металл", "металла", "металлов", "metals", "metal",
                          "в", "of", "и", "суммы", "сумма"):
            continue
        hit = next((sym for stem in _SUBJECT_STEMS_ORDERED
                    if (t.startswith(stem) and len(t) - len(stem) <= 3)
                    for sym in (_SUBJECT_STEMS[stem],)), None)
        (canon if hit else other).append(hit or t)
    if canon:
        # уникальные, порядок сохранён: «Au, Ag и МПГ» → «Au+Ag+PGM»
        seen: list[str] = []
        for c in canon:
            if c not in seen:
                seen.append(c)
        return "+".join(seen)
    return " ".join(other)[:60] or None


def _clean(raw: str) -> str:
    s = raw.strip().lower().replace("_", " ").replace("‑", "-")
    s = re.sub(r"\s*\(.*?\)\s*", " ", s)          # скобки: «(d50)», «(co2)» → отдельно
    return re.sub(r"\s+", " ", s).strip(" .,;")


# Виды, у которых предмет — элемент/металл: subject нормализуется к символу.
# Для остальных (flow_rate «серной кислоты», temperature «обжига») предмет —
# произвольное вещество/операция, сведение к элементу исказило бы смысл.
_ELEMENT_SUBJECT_KINDS = {
    "recovery_degree", "element_content", "phase_fraction", "concentration",
    "solubility", "yield_fraction", "mass",
}


def canonize(raw: str, unit: str = "") -> Canon:
    if not raw:
        return Canon(None, method="junk", confidence=0.0)
    s = _clean(raw)
    if s in _JUNK or len(s) < 3:
        return Canon(None, method="junk", confidence=0.0)

    if s in _alias_index:
        return Canon(_alias_index[s], method="exact")

    if _QUALITATIVE.search(s):
        return Canon("qualitative_observation", subject=raw.strip()[:80],
                     method="qualitative", confidence=0.8)

    for rx, kind in _KIND_SUBJECT:
        m = rx.match(s)
        if m:
            subj = (m.groupdict().get("s") or "").strip()[:60] or None
            # предмет, совпадающий с самим видом («содержание элемента») — без subject
            if subj in ("элемента", "элементов", "компонентов"):
                subj = None
            if subj and kind in _ELEMENT_SUBJECT_KINDS:
                subj = normalize_subject(subj)
            return Canon(kind, subject=subj, method="pattern", confidence=0.9)

    # вид упомянут внутри длинного имени («коэффициент запаса устойчивости борта»)
    for alias, kind in sorted(_alias_index.items(), key=lambda kv: -len(kv[0])):
        if len(alias) > 6 and alias in s:
            rest = s.replace(alias, "").strip(" -—:") or None
            return Canon(kind, subject=rest[:60] if rest else None,
                         method="pattern", confidence=0.8)

    for rx, kind in _UNIT_HINTS:
        if unit and rx.match(unit.strip()):
            return Canon(kind, subject=raw.strip()[:60], method="unit",
                         confidence=0.7)

    return Canon(None, method="unresolved", confidence=0.0)


# ── LLM-слой: канонизация остатка одним батч-вызовом ─────────────────────

_LLM_PROMPT = (
    "You are canonicalizing measured-quantity names from technical texts "
    "(Russian/English). For EACH input name pick the best canonical kind from "
    "the provided list, or \"unknown\" if none fits. If the name mentions a "
    "specific substance/object (e.g. 'извлечение никеля' → subject 'никель'), "
    "put it into 'subject', else empty string. Do not invent kinds outside "
    "the list.\n\nCANONICAL KINDS:\n{kinds}\n\nINPUT NAMES:\n{names}"
)


def llm_canonize(names: list[str], extractor=None) -> dict[str, Canon]:
    """Остаток после правил → LLM (один вызов на ~80 имён, строгая схема)."""
    if not names:
        return {}
    if extractor is None:
        from .llm import Extractor
        extractor = Extractor()
    schema = {
        "type": "object",
        "properties": {"mapping": {"type": "array", "items": {
            "type": "object",
            "properties": {"raw": {"type": "string"},
                           "kind": {"type": "string"},
                           "subject": {"type": "string"}},
            "required": ["raw", "kind", "subject"],
            "additionalProperties": False}}},
        "required": ["mapping"], "additionalProperties": False}
    kinds_desc = "\n".join(
        f"- {d.name}: {', '.join(d.aliases[:3])}" for d in ALL_KINDS.values())
    prompt = _LLM_PROMPT.format(kinds=kinds_desc, names="\n".join(names))
    import json as _json
    r = extractor.client.chat.completions.create(
        model=extractor.model, temperature=0, max_tokens=6000,
        response_format={"type": "json_schema", "json_schema": {
            "name": "canon", "schema": schema, "strict": True}},
        messages=[{"role": "user", "content": prompt}],
        **({"reasoning_effort": "low"} if "Gpt-oss" in extractor.model else {}))
    out: dict[str, Canon] = {}
    try:
        mapping = _json.loads(r.choices[0].message.content or "{}").get("mapping", [])
    except _json.JSONDecodeError:
        return {}
    valid = set(ALL_KINDS)
    for m in mapping:
        kind = (m.get("kind") or "").strip()
        if kind in valid:
            subj = (m.get("subject") or "").strip()[:60] or None
            if subj and kind in _ELEMENT_SUBJECT_KINDS:
                subj = normalize_subject(subj)
            out[m["raw"]] = Canon(kind, subject=subj, method="llm", confidence=0.75)
    return out


# ── миграция уже загруженной БД ───────────────────────────────────────────

def migrate_db(store, apply: bool = False, use_llm: bool = False) -> dict:
    """Переканонизировать величины со status='needs_review': перевесить
    results на канон (+conditions.subject → ось Gate), слить реестр.
    Без --apply — отчёт. use_llm: остаток после правил — батч-вызовом LLM."""
    from ..loader import seed_registries
    seed_registries(store)
    store.executemany(
        "INSERT INTO experiments.quantity_kinds(name, unit_dim, aliases, status)"
        " VALUES (%s,%s,%s,'seed') ON CONFLICT (name) DO NOTHING",
        [(d.name, d.unit_dim, d.aliases) for d in EXTRA_KINDS.values()])

    rows = store.query(
        "SELECT qk.name, count(r.id) AS n, min(r.unit) AS unit"
        " FROM experiments.quantity_kinds qk"
        " LEFT JOIN experiments.results r ON r.quantity_kind = qk.name"
        " WHERE qk.status = 'needs_review' GROUP BY qk.name")
    rep = {"resolved": 0, "junk_dropped": 0, "qualitative": 0,
           "left_for_review": 0, "results_moved": 0, "details": []}

    def _move(row: dict, c: Canon) -> None:
        rep["resolved"] += 1
        rep["qualitative"] += c.method == "qualitative"
        rep["results_moved"] += row["n"]
        rep["details"].append(
            {"raw": row["name"], "kind": c.kind, "subject": c.subject,
             "method": c.method, "n_results": row["n"]})
        if not apply:
            return
        if c.kind == row["name"]:
            # сырое имя само оказалось каноном — подтвердить строку реестра
            store.execute(
                "UPDATE experiments.quantity_kinds SET status='confirmed',"
                " unit_dim=%s WHERE name=%s",
                (ALL_KINDS[c.kind].unit_dim, c.kind))
            return
        if c.subject:
            store.execute(
                "UPDATE experiments.results SET quantity_kind=%s,"
                " conditions = conditions || jsonb_build_object('subject', %s::text)"
                " WHERE quantity_kind=%s", (c.kind, c.subject, row["name"]))
        else:
            store.execute(
                "UPDATE experiments.results SET quantity_kind=%s"
                " WHERE quantity_kind=%s", (c.kind, row["name"]))
        store.execute(
            "UPDATE experiments.quantity_kinds SET aliases ="
            " array_append(aliases, %s) WHERE name=%s AND NOT (%s = ANY(aliases))",
            (row["name"], c.kind, row["name"]))
        store.execute("DELETE FROM experiments.quantity_kinds WHERE name=%s",
                      (row["name"],))

    unresolved: list[dict] = []
    for row in rows:
        c = canonize(row["name"], row["unit"] or "")
        if c.method == "junk":
            rep["junk_dropped"] += 1
            if apply:
                store.execute("DELETE FROM experiments.results WHERE quantity_kind=%s",
                              (row["name"],))
                store.execute("DELETE FROM experiments.quantity_kinds WHERE name=%s",
                              (row["name"],))
            continue
        if c.kind is None:
            unresolved.append(row)
            continue
        _move(row, c)

    if use_llm and unresolved:
        mapping = llm_canonize([r["name"] for r in unresolved])
        still = []
        for row in unresolved:
            c = mapping.get(row["name"])
            if c:
                _move(row, c)
            else:
                still.append(row)
        unresolved = still

    rep["left_for_review"] = len(unresolved)
    if apply:
        store.commit()
    return rep


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    from ..store import Store
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--llm", action="store_true",
                    help="остаток после правил канонизировать LLM-вызовом")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    store = Store.open(args.db)
    rep = migrate_db(store, apply=args.apply, use_llm=args.llm)
    mode = "ПРИМЕНЕНО" if args.apply else "dry-run"
    print(f"[{mode}] разрешено: {rep['resolved']} (кач.: {rep['qualitative']})"
          f" · мусор: {rep['junk_dropped']} · осталось HITL: {rep['left_for_review']}"
          f" · перевешено измерений: {rep['results_moved']}")
    for d in rep["details"][:40]:
        subj = f" [{d['subject']}]" if d["subject"] else ""
        print(f"  {d['raw'][:44]:<46} → {d['kind']}{subj} ({d['method']}, n={d['n_results']})")
    store.close()


if __name__ == "__main__":
    main()
