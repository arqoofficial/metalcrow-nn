# -*- coding: utf-8 -*-
"""
Сборщик слим-артефакта РАСШИРЕНИЯ ЗАПРОСА (query-side synonym expansion).

Отдельная задача от дедупа (`build_osn_entities.py`): там сырые поверхностные
формы сводятся к канону для СЛИЯНИЯ строк материалов; здесь наоборот — по терму
запроса собираем ВСЕ его кросс-язычные синонимы, чтобы русский вопрос доставал
англоязычные пассажи (и наоборот). Поэтому источник — ЧИСТЫЕ, вручную/бенчмарк-
выверенные словари, а не 18k-эмбеддинг-кластеры (у которых RU и EN разъехались
по разным концептам и есть шумные слипания «обессоливание/обеспыливание»).

Источники (читаются на этапе сборки, вне контейнера):
  - term_dictionary/data/synonym_map.json — 500 концептов с surface_forms и
    флагом needs_review (берём только needs_review=False, лейблы
    MATERIAL/PROCESS/PROPERTY/EQUIPMENT).
  - dictionaries/synonyms_ru_en.yaml — ручные RU↔EN пары (ключ `synonyms`).
  - term_dictionary/data/abbreviations.json — 60 аббревиатур с расшифровкой.

Выход — ontology/data/query_synonyms.json (стдлиб-JSON, забирается контейнером):
  { "groups": [[form, form, ...], ...],   # взаимные синонимы, lowercase
    "abbrev": { acronym|variant: expansion } }

Рантайм (`ontology.query_expand`) читает только этот JSON — без PyYAML и без
зависимости от каталогов term_dictionary/dictionaries (они вне build-контекста
образа онтологии; артефакт коммитится под ontology/data/, как osn_entities.json).

Пересборка:
    python -m ontology.data.build_query_synonyms
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_HERE = Path(__file__).resolve()
_CYR = re.compile(r"[а-яё]", re.I)
_LAT = re.compile(r"[a-z]", re.I)
# один «содержательный» токен: буквы/цифры, длиной >=3 (короче — шум для ретрива)
_TOKEN = re.compile(r"[а-яёa-z0-9]{3,}", re.I)

_KEEP_LABELS = {"MATERIAL", "PROCESS", "PROPERTY", "EQUIPMENT"}
_MAX_GROUP = 12          # кластеры крупнее — почти всегда переслипшийся шум
_MIN_LEN = 3


def _repo_root() -> Path:
    for up in _HERE.parents:
        if (up / ".git").exists():
            return up
    return _HERE.parents[4]


def _norm(s: str) -> str:
    return " ".join((s or "").split()).strip().lower()


def _from_synonym_map(path: Path) -> list[list[str]]:
    if not path.exists():
        print("нет synonym_map:", path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    groups: list[list[str]] = []
    for c in data:
        if c.get("needs_review"):
            continue
        if c.get("label") not in _KEEP_LABELS:
            continue
        forms = {_norm(f) for f in ([c.get("canonical")] + c.get("surface_forms", [])) if f}
        forms = {f for f in forms if len(f) >= _MIN_LEN}
        if len(forms) >= 2 and len(forms) <= _MAX_GROUP:
            groups.append(sorted(forms))
    return groups


def _from_ru_en_yaml(path: Path) -> list[list[str]]:
    """Ручные RU↔EN пары. Берём только настоящие кросс-язычные (в группе есть и
    латиница, и кириллица) — отсекает пустые метаданные и заглавные-псевдосинонимы
    вида «извлечение → Извлечение»."""
    if not path.exists():
        print("нет synonyms_ru_en.yaml:", path)
        return []
    try:
        import yaml
    except ImportError:
        print("PyYAML недоступен — пропускаю ru_en.yaml")
        return []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    pairs = doc.get("synonyms", doc) if isinstance(doc, dict) else {}
    groups: list[list[str]] = []
    for key, vals in (pairs or {}).items():
        if not vals:
            continue
        forms = {_norm(str(key).replace("_", " "))} | {_norm(str(v)) for v in vals}
        forms = {f for f in forms if len(f) >= _MIN_LEN}
        blob = " ".join(forms)
        if _CYR.search(blob) and _LAT.search(blob) and 2 <= len(forms) <= _MAX_GROUP:
            groups.append(sorted(forms))
    return groups


_SAFE_SINGLE = re.compile(r"[а-яёa-z0-9]+$", re.I)


def _form_ok(form: str) -> bool:
    """Одиночная форма обязана быть чистой лексемой: `zn(ii)` в tsquery — это
    синтаксис (скобки = группировка), а не текст. Многословные/дефисные формы
    рантайм сам бьёт на безопасные токены."""
    if " " in form or "-" in form:
        return True
    return bool(_SAFE_SINGLE.fullmatch(form))


def _merge(groups: list[list[str]]) -> list[list[str]]:
    """Слить группы с общей формой (union-find): desalination из двух источников
    сходится в один кластер. Небезопасные для tsquery одиночные формы
    отбрасываются на входе."""
    groups = [kept for g in groups
              if len(kept := [f for f in g if _form_ok(f)]) >= 2]
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for g in groups:
        for f in g[1:]:
            union(g[0], f)
    clusters: dict[str, set[str]] = {}
    for g in groups:
        for f in g:
            clusters.setdefault(find(f), set()).add(f)
    out = [sorted(c) for c in clusters.values() if 2 <= len(c) <= _MAX_GROUP]
    return sorted(out)


def _abbrev(path: Path) -> dict[str, str]:
    if not path.exists():
        print("нет abbreviations.json:", path)
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for ab in data:
        if float(ab.get("confidence", 0)) < 0.5:
            continue
        canon = _norm(ab.get("canonical_expansion") or "")
        acr = _norm(ab.get("acronym") or "")
        if len(acr) < 2 or not canon:
            continue
        out[acr] = canon
        for v in ab.get("expansion_variants", []):
            vn = _norm(v)
            if vn and vn != acr:
                out[vn] = canon
    return out


def main() -> None:
    root = _repo_root()
    sm = _from_synonym_map(root / "term_dictionary" / "data" / "synonym_map.json")
    ru = _from_ru_en_yaml(root / "dictionaries" / "synonyms_ru_en.yaml")
    groups = _merge(sm + ru)
    abbrev = _abbrev(root / "term_dictionary" / "data" / "abbreviations.json")
    out_path = _HERE.parent / "query_synonyms.json"
    out_path.write_text(
        json.dumps({"groups": groups, "abbrev": abbrev},
                   ensure_ascii=False, sort_keys=True),
        encoding="utf-8")
    n_forms = sum(len(g) for g in groups)
    print(f"synonym_map групп: {len(sm)} | ru_en групп: {len(ru)}")
    print(f"итог: {len(groups)} кластеров, {n_forms} форм, {len(abbrev)} аббревиатур")
    print("записано:", out_path, f"({out_path.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
