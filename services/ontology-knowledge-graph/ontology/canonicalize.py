# -*- coding: utf-8 -*-
"""
Бэкфилл-канонизация материалов существующей БД по OSN-канону.

Каждая строка experiments.materials приводится к глобальному каноническому id
(entities.material_ext_id по имени). Строки с одним каноном — варианты одного
материала. Два режима бэкфилла, оба идемпотентны:

  SOFT (по умолчанию, обратимо, безопасно для live):
    только записывает связи entity_same_as(variant -> canonical). Строки НЕ
    удаляются и НЕ репойнтятся — ретрив и ссылки не затрагиваются. Метод связи:
    'normalize' для точной морфологической копии (регистр/пробелы/пунктуация/
    склонение) и 'osn_dict' для синонима из OSN-словаря.

  HARD (--hard, деструктивно): физически сливает ТОЛЬКО точные морфологические
    дубли (тот же материал по модулю регистра/пробелов/пунктуации/склонения) и
    только если grade/phase/composition не конфликтуют. Синонимы OSN (osn_dict)
    в hard-merge НЕ участвуют — они остаются раздельными строками, связанными
    лишь soft-ссылкой. Репойнтит experiment_materials/results/edges_semantic на
    канонический UUID, пишет entity_same_as, удаляет осиротевшие строки. Всё в
    одной транзакции. На live --hard применять НЕ следует — только DRY-RUN.

Инвариант: разные материалы (различающиеся уточнителем — «медный»/«никелевый»/
«медно-никелевый» и т.п.) НИКОГДА не сливаются: entities.canonical_material
сохраняет уточнители (см. защиту OSN-канона), а hard-merge дополнительно
ограничен точными морфологическими дублями.

Отдельный офлайн-режим --rewrite-batches канонизирует УЖЕ СОХРАНЁННЫЕ батчи
ontology/batches/okf-*.json (materials[].id и все ссылки на них) — иначе на
чистом деплое service_init восстановил бы из них старые per-document id и
дубли вернулись бы. Детерминированно, без LLM, идемпотентно. Переписанные
батчи предназначены ТОЛЬКО для чистого деплоя (reset + автозагрузка): в живую
БД их грузить не нужно (uuid5 новых id продублирует существующие строки).

    python -m ontology.canonicalize                  # DRY-RUN (обзор групп)
    python -m ontology.canonicalize --soft-apply      # записать entity_same_as (обратимо)
    python -m ontology.canonicalize --hard            # DRY-RUN hard-merge
    python -m ontology.canonicalize --hard --apply    # применить hard-merge (НЕ на live)
    python -m ontology.canonicalize --rewrite-batches # канонизировать батчи okf-*.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from .batch import ExtractionBatch
from .extract import entities
from .loader import ext_uuid
from .store import Store

# окончания склонений/числа RU + мн.ч. EN для сравнения «по модулю морфологии»
# (только для КЛАССИФИКАЦИИ точного дубля, не для канонизации).
_DECL_RU = re.compile(r"(ами|ями|ов|ев|ей|ах|ях|ам|ям|ье|ья|а|я|ы|и|у|ю|е|о|ь|й)$")
_DECL_EN = re.compile(r"s$")
_NONWORD = re.compile(r"[^\w]+", re.UNICODE)


def _norm_stem(name: str) -> str:
    """Морфологический ключ: casefold + NFKC, слова без окончаний числа/падежа,
    порядок сохранён. Служит ТОЛЬКО для классификации точного морф. дубля."""
    t = unicodedata.normalize("NFKC", name or "").casefold()
    words = [w for w in _NONWORD.split(t) if w]
    stems = []
    for w in words:
        if any("а" <= ch <= "я" or ch == "ё" for ch in w):
            s = _DECL_RU.sub("", w)
        else:
            s = _DECL_EN.sub("", w)
        stems.append(s if len(s) >= 3 else w)   # не срезаем короткие слова в пыль
    return " ".join(stems)


def _is_exact_norm_dup(name: str, canonical_name: str) -> bool:
    """True, если name — точная копия канона по модулю регистра/пунктуации/
    пробелов/склонения (кандидат на hard-merge)."""
    return _norm_stem(name) == _norm_stem(canonical_name)


def _method(name: str) -> str:
    """Источник соответствия для entity_same_as."""
    key = entities._norm_key(name)
    hit = entities._load().get(key)
    return "osn_dict" if hit and hit.get("kind") == "material" else "normalize"


def _prov(canonical_name: str) -> str:
    """Минимальный валидный provenance для авто-созданной канонической строки."""
    return json.dumps({
        "doc_id": "canonicalize", "locator_kind": "xlsx_row",
        "locator": "row:auto", "snippet": f"{canonical_name} (canonical)",
        "extractor": "structured_etl", "confidence": 1.0,
        "ingested_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }, ensure_ascii=False)


def _canonical_uuid(name: str) -> tuple[str, str, str]:
    ext = entities.material_ext_id(name)
    return ext, ext_uuid(ext), entities.canonical_material(name)


def plan(store: Store) -> dict:
    """Материалы, сгруппированные по каноническому ext_id. Для каждого члена —
    флаг exact (точный морфологический дубль канона → кандидат hard-merge)."""
    rows = store.query(
        "SELECT id::text AS id, name, family, grade, phase, composition"
        " FROM experiments.materials")
    groups: dict[str, dict] = {}
    for r in rows:
        ext, cuid, cname = _canonical_uuid(r["name"])
        g = groups.setdefault(ext, {"ext": ext, "canonical_uuid": cuid,
                                    "canonical_name": cname, "members": []})
        r["exact"] = _is_exact_norm_dup(r["name"], cname)
        g["members"].append(r)
    return groups


def _hard_variants(g: dict) -> list[dict]:
    """Члены группы, допустимые к hard-merge: точные морфологические дубли без
    конфликта grade/phase/composition между собой."""
    exact = [m for m in g["members"] if m["exact"]]
    if len(exact) < 2:
        return []
    # конфликт различающих атрибутов → не сливаем физически
    def norm(v):
        return v if v not in (None, "", {}, []) else None
    grades = {norm(m["grade"]) for m in exact} - {None}
    phases = {norm(m["phase"]) for m in exact} - {None}
    comps = {json.dumps(m["composition"], sort_keys=True, ensure_ascii=False)
             for m in exact if norm(m["composition"])}
    if len(grades) > 1 or len(phases) > 1 or len(comps) > 1:
        return []
    return exact


# ── DRY-RUN отчёт ──────────────────────────────────────────────────────────

def report_dryrun(store: Store, hard: bool) -> None:
    groups = plan(store)
    total = sum(len(g["members"]) for g in groups.values())
    merge_groups = {k: g for k, g in groups.items() if len(g["members"]) > 1}
    soft_links = sum(len(g["members"]) - 1 for g in merge_groups.values())

    hard_groups = {k: g for k, g in groups.items() if len(_hard_variants(g)) >= 2}
    hard_away = sum(len(_hard_variants(g)) - 1 for g in hard_groups.values())

    print(f"материалов всего:            {total}")
    print(f"канонических групп:           {len(groups)}")
    print(f"групп с >1 членом:            {len(merge_groups)}")
    print(f"soft-ссылок entity_same_as:   {soft_links}")
    print(f"hard-merge групп (точн.дубли):{len(hard_groups)}")
    print(f"hard-merge удалит строк:      {hard_away}")

    if hard:
        print("топ-10 hard-merge групп (точные морфологические дубли):")
        top = sorted(hard_groups.values(), key=lambda g: -len(_hard_variants(g)))[:10]
        for g in top:
            hv = _hard_variants(g)
            head = Counter(m["name"] for m in hv).most_common(1)[0][0]
            print(f"  {head[:46]:<48} {len(hv)} → 1   ({g['ext']})")
    else:
        print("топ-10 групп по числу членов (soft-ссылки; физически НЕ сливаются):")
        top = sorted(merge_groups.values(), key=lambda g: -len(g["members"]))[:10]
        for g in top:
            head = Counter(m["name"] for m in g["members"]).most_common(1)[0][0]
            hv = len(_hard_variants(g))
            print(f"  {head[:44]:<46} {len(g['members'])} членов"
                  f" (hard-дублей: {hv})   ({g['ext']})")

    # диагностика: файнштейн и его уточнённые варианты держат РАЗНЫЕ id
    fs = {k: g for k, g in groups.items() if "фаи-нштеи-н" in k}
    if fs:
        print("файнштейн-семейство (уточнители НЕ сливаются):")
        for k, g in sorted(fs.items()):
            names = sorted({m["name"] for m in g["members"]})
            print(f"  {k:<38} n={len(g['members'])}  {names[:3]}")


# ── SOFT: только entity_same_as ────────────────────────────────────────────

def apply_soft(store: Store) -> None:
    groups = plan(store)
    merge_groups = [g for g in groups.values() if len(g["members"]) > 1]
    n = 0
    try:
        for g in merge_groups:
            cuid = g["canonical_uuid"]
            for m in g["members"]:
                if m["id"] == cuid:
                    continue
                # идемпотентность: не дублируем существующую связь
                exists = store.scalar(
                    "SELECT 1 FROM experiments.entity_same_as"
                    " WHERE entity_type='material' AND source_id=%s AND canonical_id=%s",
                    (m["id"], cuid))
                if exists:
                    continue
                store.execute(
                    "INSERT INTO experiments.entity_same_as"
                    "(entity_type, source_id, canonical_id, confidence, method)"
                    " VALUES ('material',%s,%s,1.0,%s)",
                    (m["id"], cuid, _method(m["name"])))
                n += 1
        store.commit()
    except Exception:
        store.rollback()
        raise
    print(f"soft-apply: записано entity_same_as-связей {n}"
          f" (по {len(merge_groups)} группам). Строки не изменены.")


# ── HARD: физическое слияние точных морфологических дублей ──────────────────

def apply_hard(store: Store) -> None:
    groups = plan(store)
    var2canon: dict[str, str] = {}
    var_name: dict[str, str] = {}
    n_created = 0
    try:
        for g in groups.values():
            hv = _hard_variants(g)
            if len(hv) < 2:
                continue
            cuid = g["canonical_uuid"]
            exists = store.scalar(
                "SELECT 1 FROM experiments.materials WHERE id=%s", (cuid,))
            if not exists:
                family = Counter(m["family"] for m in hv).most_common(1)[0][0]
                store.execute(
                    "INSERT INTO experiments.materials(id, name, family, prov)"
                    " VALUES (%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                    (cuid, g["canonical_name"], family, _prov(g["canonical_name"])))
                n_created += 1
            for m in hv:
                if m["id"] != cuid:
                    var2canon[m["id"]] = cuid
                    var_name[m["id"]] = m["name"]

        if not var2canon:
            store.commit()
            print("hard-apply: нечего сливать (уже канонизировано)")
            return

        variants = list(var2canon)

        store.executemany(
            "INSERT INTO experiments.entity_same_as"
            "(entity_type, source_id, canonical_id, confidence, method)"
            " VALUES ('material',%s,%s,1.0,%s)",
            [(v, c, _method(var_name[v])) for v, c in var2canon.items()])

        store.execute("CREATE TEMP TABLE _remap(variant UUID PRIMARY KEY, canonical UUID)"
                      " ON COMMIT DROP")
        store.executemany("INSERT INTO _remap(variant, canonical) VALUES (%s,%s)",
                          list(var2canon.items()))

        # experiment_materials: PK (experiment_id, material_id, role)
        em_touched = store.query(
            "SELECT em.experiment_id, r.canonical AS material_id, em.role, em.prov"
            "  FROM experiments.experiment_materials em"
            "  JOIN _remap r ON r.variant = em.material_id")
        store.execute(
            "DELETE FROM experiments.experiment_materials em USING _remap r"
            " WHERE em.material_id = r.variant")
        store.executemany(
            "INSERT INTO experiments.experiment_materials(experiment_id, material_id, role, prov)"
            " VALUES (%s,%s,%s,%s) ON CONFLICT (experiment_id, material_id, role) DO NOTHING",
            [(t["experiment_id"], t["material_id"], t["role"],
              store.jsondump(t["prov"])) for t in em_touched])

        # results.material_id (PK=id, конфликтов нет)
        store.execute(
            "UPDATE experiments.results res SET material_id=r.canonical"
            " FROM _remap r WHERE res.material_id = r.variant")

        # edges_semantic: PK (src, dst, predicate) — собрать/удалить/вставить
        touched = store.query(
            "SELECT COALESCE(rs.canonical, e.src) AS nsrc,"
            "       COALESCE(rd.canonical, e.dst) AS ndst,"
            "       e.predicate, e.attrs, e.weight, e.prov"
            "  FROM experiments.edges_semantic e"
            "  LEFT JOIN _remap rs ON rs.variant=e.src"
            "  LEFT JOIN _remap rd ON rd.variant=e.dst"
            "  WHERE e.src IN (SELECT variant FROM _remap)"
            "     OR e.dst IN (SELECT variant FROM _remap)")
        store.execute(
            "DELETE FROM experiments.edges_semantic e"
            " WHERE e.src IN (SELECT variant FROM _remap)"
            "    OR e.dst IN (SELECT variant FROM _remap)")
        store.executemany(
            "INSERT INTO experiments.edges_semantic(src, dst, predicate, attrs, weight, prov)"
            " VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (src, dst, predicate) DO NOTHING",
            [(t["nsrc"], t["ndst"], t["predicate"],
              store.jsondump(t["attrs"]) if t["attrs"] is not None else None,
              t["weight"],
              store.jsondump(t["prov"]) if t["prov"] is not None else None)
             for t in touched if t["nsrc"] != t["ndst"]])

        store.execute(
            "DELETE FROM experiments.materials m USING _remap r WHERE m.id=r.variant")
        store.commit()
    except Exception:
        store.rollback()
        raise
    print(f"hard-apply: создано канонических {n_created}, слито вариантов {len(var2canon)}")


# ── офлайн-канонизация сохранённых батчей (для чистого деплоя) ──────────────

def rewrite_batch_dict(data: dict) -> tuple[dict, dict]:
    """Канонизировать материалы одного батча (dict, in place-семантика).

    materials[].id → entities.material_ext_id(label); все ссылки (material_id
    в использованиях/измерениях, src/dst семантических рёбер) ремапятся.
    Коллизии id внутри батча сливаются: label первого, family — самый частый.
    → (переписанный dict, stats)."""
    idmap: dict[str, str] = {}
    fam_votes: dict[str, Counter] = {}
    merged: dict[str, dict] = {}          # new_id → материал (первый label)
    order: list[str] = []
    for m in data.get("materials", []):
        new_id = entities.material_ext_id(m.get("label") or "")
        idmap[m["id"]] = new_id
        fam_votes.setdefault(new_id, Counter())[m.get("family") or "other"] += 1
        if new_id not in merged:
            merged[new_id] = {**m, "id": new_id}
            order.append(new_id)
    for new_id in order:
        merged[new_id]["family"] = fam_votes[new_id].most_common(1)[0][0]
    n_before = len(data.get("materials", []))
    data["materials"] = [merged[i] for i in order]

    def remap(old: str | None) -> str | None:
        return idmap.get(old, old) if old else old

    for e in data.get("experiments", []):
        seen_use: set[tuple[str, str]] = set()
        uses = []
        for mu in e.get("materials", []):
            mu["material_id"] = remap(mu.get("material_id"))
            key = (mu["material_id"], mu.get("role") or "sample")
            if key in seen_use:
                continue                   # схлоп дублей использования после ремапа
            seen_use.add(key)
            uses.append(mu)
        e["materials"] = uses
        for ms in e.get("measurements", []):
            if ms.get("material_id"):
                ms["material_id"] = remap(ms["material_id"])
    for key in ("lineage", "validated_by", "contradicts"):
        for ed in data.get(key, []):
            ed["src"] = remap(ed.get("src"))
            ed["dst"] = remap(ed.get("dst"))
            # process — контролируемый словарь: приводим к канону/'other', чтобы
            # в сохранённых батчах не оставалось сырьё (уравнения, предложения).
            if ed.get("process"):
                ed["process"] = entities.canonical_process(ed["process"])
    return data, {"materials_before": n_before,
                  "materials_after": len(data["materials"])}


def rewrite_batches(batch_dir: Path, glob_pat: str) -> None:
    files = sorted(batch_dir.glob(glob_pat))
    tot_b = tot_a = n_files = n_err = 0
    for f in files:
        if f.stem.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data, st = rewrite_batch_dict(data)
            ExtractionBatch.model_validate(          # схема должна остаться валидной
                {k: v for k, v in data.items() if not k.startswith("_")})
            f.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                         encoding="utf-8")
            tot_b += st["materials_before"]
            tot_a += st["materials_after"]
            n_files += 1
        except Exception as e:
            n_err += 1
            print(f"  FAIL {f.name}: {type(e).__name__}: {str(e)[:120]}")
    print(f"переписано батчей: {n_files} (ошибок {n_err})")
    print(f"материалов в батчах: {tot_b} → {tot_a}"
          f" (слито {tot_b - tot_a})")


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="OSN-канонизация материалов: БД (soft/hard) и сохранённые батчи")
    ap.add_argument("--soft-apply", action="store_true",
                    help="записать entity_same_as-связи (обратимо, безопасно для live)")
    ap.add_argument("--hard", action="store_true",
                    help="режим физического слияния точных морфологических дублей")
    ap.add_argument("--apply", action="store_true",
                    help="с --hard: применить слияние (иначе DRY-RUN). НЕ на live.")
    ap.add_argument("--rewrite-batches", action="store_true",
                    help="канонизировать сохранённые батчи (офлайн, без БД)")
    ap.add_argument("--dir", type=Path,
                    default=Path(__file__).parent / "batches",
                    help="папка батчей для --rewrite-batches")
    ap.add_argument("--glob", default="okf-*.json",
                    help="маска файлов для --rewrite-batches")
    ap.add_argument("--db", default=None, help="postgresql://... (или ONTOLOGY_DB_URL)")
    args = ap.parse_args()

    if args.rewrite_batches:
        rewrite_batches(args.dir, args.glob)
        return

    store = Store.open(args.db)
    try:
        if args.soft_apply:
            apply_soft(store)
        elif args.hard and args.apply:
            apply_hard(store)
        else:
            report_dryrun(store, hard=args.hard)
    finally:
        store.close()


if __name__ == "__main__":
    main()
