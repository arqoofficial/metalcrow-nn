# -*- coding: utf-8 -*-
"""
Сборщик слим-словаря канонизации сущностей из OSN-концептов.

Источник — services/science-knowledge-graph/data/synonym_map.json (список
~18k концептов вида {concept_id, canonical, label, surface_forms, ...}).
Оставляем только концепты material/process и разворачиваем их в плоский
lookup, который забирается контейнером онтологии из ontology/data/:

Концепты с needs_review=True пропускаем: это переслипшиеся embedding-блобы
(десятки–сотни разнородных поверхностных форм под одним каноном, напр.
«медных руд» с 215 формами от медного до цинкового концентрата). Их канон
объединил бы разные материалы в один id — прямой вред дедупликации.

    { surface_form_lower: {"canonical": <str>, "kind": "material"|"process",
                           "concept_id": <str>} }

Каждая поверхностная форма (и сам canonical) отображается в свой канон.
При коллизии surface_form побеждает более короткий canonical (обычно более
общий термин), затем material над process — детерминированно.

Запуск:
    python -m ontology.data.build_osn_entities \
        [--src <synonym_map.json>] [--out <osn_entities.json>]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

_LABEL_KIND = {"MATERIAL": "material", "PROCESS": "process"}

# Концепты-блобы: не помечены needs_review в источнике, но объединяют химически
# РАЗНЫЕ материалы под один канон (embedding-переслипы) — прямой вред
# дедупликации. Отбрасываем их так же, как needs_review.
_BLOCK_CONCEPTS = frozenset({
    "C00165",  # Co + CuO + BaSO4 + CuSO4 + CoO + CoSO4 (кобальт/оксид меди/сульфаты)
    "C00970",  # CaF2 + CuCl2 + FeCl3 + NiAl + BaCl2 ... (фториды/хлориды/алюминиды)
    "C04819",  # H2SO4 + SO4 (кислота ≠ ион)
    "C08447",  # sodium sulfate + sulfide + sulfite (разные анионы)
    "C04596",  # copper matte + nickel matte + copper-lead matte (разные штейны)
    "C10014",  # NCM + NCAM (разные катодные химии)
})

# путь по умолчанию: репо-корень определяем по .git вверх по дереву
_HERE = Path(__file__).resolve()


def _repo_root() -> Path:
    for up in _HERE.parents:
        if (up / ".git").exists():
            return up
    return _HERE.parents[4]


def _norm(s: str) -> str:
    """Лёгкая нормализация ключа: casefold + схлопывание пробелов."""
    return " ".join((s or "").split()).casefold()


def build(src: Path) -> dict[str, dict]:
    concepts = json.loads(src.read_text(encoding="utf-8"))
    lookup: dict[str, dict] = {}

    def offer(surface: str, canonical: str, kind: str, cid: str) -> None:
        key = _norm(surface)
        if not key:
            return
        cur = lookup.get(key)
        if cur is not None:
            # разрешение коллизии: короче canonical → общее; material > process
            better = (len(canonical), 0 if kind == "material" else 1)
            worse = (len(cur["canonical"]), 0 if cur["kind"] == "material" else 1)
            if better >= worse:
                return
        lookup[key] = {"canonical": canonical, "kind": kind, "concept_id": cid}

    dropped = 0
    for c in concepts:
        kind = _LABEL_KIND.get(c.get("label"))
        if kind is None:
            continue
        if c.get("needs_review") or c.get("concept_id") in _BLOCK_CONCEPTS:
            dropped += 1
            continue
        canonical = (c.get("canonical") or "").strip()
        cid = c.get("concept_id") or ""
        if not canonical:
            continue
        offer(canonical, canonical, kind, cid)
        for sf in c.get("surface_forms", []):
            offer(sf, canonical, kind, cid)
    if dropped:
        print(f"пропущено needs_review-концептов: {dropped}")
    return lookup


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=None,
                    help="synonym_map.json (по умолчанию из science-knowledge-graph)")
    ap.add_argument("--out", type=Path, default=None,
                    help="целевой osn_entities.json (по умолчанию ontology/data/)")
    args = ap.parse_args()

    root = _repo_root()
    src = args.src or (root / "services" / "science-knowledge-graph"
                       / "data" / "synonym_map.json")
    out = args.out or (_HERE.parent / "osn_entities.json")

    lookup = build(src)
    out.write_text(json.dumps(lookup, ensure_ascii=False, sort_keys=True),
                   encoding="utf-8")
    n_mat = sum(1 for v in lookup.values() if v["kind"] == "material")
    n_proc = len(lookup) - n_mat
    size_kb = out.stat().st_size / 1024
    print(f"источник: {src}")
    print(f"записано: {out}  ({size_kb:.0f} KB)")
    print(f"поверхностных форм: {len(lookup)}  (material={n_mat}, process={n_proc})")


if __name__ == "__main__":
    main()
