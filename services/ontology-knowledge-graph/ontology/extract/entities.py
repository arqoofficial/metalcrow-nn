# -*- coding: utf-8 -*-
"""
Канонизация сущностей поверх OSN-словаря (стадия дедупликации).

Идентификаторы материалов раньше строились из сырого поверхностного имени
per-document (`mat:{slug}:{...}`), из-за чего один и тот же материал плодил
десятки вариантов. Здесь имя приводится к ГЛОБАЛЬНОМУ канону:

  - canonical_material / canonical_process — сырое имя → каноническое (по
    OSN-словарю ontology/data/osn_entities.json), иначе — лёгкая стабильная
    нормализация исходного имени.
  - material_ext_id — глобальный внешний id `mat:{slugify(canonical)}`. Это
    ЕДИНЫЙ ключ дедупликации: материалы с одинаковым каноном сходятся в один
    id и, благодаря идемпотентному loader'у (ON CONFLICT DO NOTHING по id),
    переиспользуют существующую строку вместо создания варианта.

Инвариант БЕЗОПАСНОСТИ (не переслипать разные материалы): OSN кластеризован
эмбеддингами и иногда стягивает под один канон материалы, различающиеся
уточняющим определением («медный», «никелевый», «сульфидный», «анодный»...).
Поэтому OSN-канон принимается ТОЛЬКО если он сохраняет все уточнители исходного
имени; иначе — лёгкая нормализация исходного имени (уточнитель остаётся).
Так «медно-никелевый файнштейн» и «никелевый файнштейн» держат РАЗНЫЕ id, а
«файнштейн»/«файнштейна» (склонение) — один.

Словарь загружается лениво один раз на процесс.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Optional

from .normalize import map_process

_DATA = Path(__file__).resolve().parents[1] / "data" / "osn_entities.json"

_OSN: Optional[dict[str, dict]] = None

# пунктуация по краям для нормализации ключа (не трогаем внутренние дефисы слов)
_EDGE_PUNCT = re.compile(r"^[\s\"'«»“”().,;:—–\-]+|[\s\"'«»“”().,;:—–\-]+$")
_WS = re.compile(r"\s+")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

# Уточнители-стемы: определения, различающие материалы одного рода. Если
# исходное имя несёт такой уточнитель, а OSN-канон его теряет — канон отвергаем
# (иначе слили бы разные материалы). Стем сравниваем по префиксу (склонения).
_QUALIFIER_STEMS = (
    "медн", "никелев", "кобальтов", "железн", "цинков", "свинцов", "оловян",
    "сульфидн", "оксидн", "окисленн", "хлоридн", "сульфатн", "карбонатн",
    "анодн", "катодн", "чернов", "рафинирован", "элементн", "металлическ",
    "магнитн", "немагнитн", "силикатн", "шлаков", "штейнов", "колчеданн",
    "полиметаллическ", "молибден", "серосульфидн", "автоклавн", "богат",
    "бедн", "упорн", "низкосортн", "высокосортн", "рудн", "концентратн",
)


def _qualifier_stems(text: str) -> set[str]:
    """Стемы уточнителей, присутствующие в тексте (по префиксному совпадению)."""
    low = text.casefold()
    return {q for q in _QUALIFIER_STEMS if q in low}


def _load() -> dict[str, dict]:
    global _OSN
    if _OSN is None:
        try:
            _OSN = json.loads(_DATA.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _OSN = {}
    return _OSN


def _norm_key(name: str) -> str:
    """Ключ поиска в словаре: NFKC + casefold + схлоп пробелов + чистка краёв."""
    t = unicodedata.normalize("NFKC", name or "")
    t = _WS.sub(" ", t).strip()
    t = _EDGE_PUNCT.sub("", t)
    return t.casefold()


def _light_norm(name: str) -> str:
    """Стабильная лёгкая нормализация исходного имени (когда OSN не знает его).
    Не переводит регистр отображаемого имени — только чистит края/пробелы."""
    t = unicodedata.normalize("NFKC", name or "")
    t = _WS.sub(" ", t).strip()
    t = _EDGE_PUNCT.sub("", t)
    return t


def canonical_material(name: str) -> str:
    """Сырое имя материала → канон OSN (label MATERIAL) с защитой уточнителей;
    иначе — лёгкая нормализация исходного имени."""
    key = _norm_key(name)
    if not key:
        return _light_norm(name)
    hit = _load().get(key)
    if hit is not None and hit.get("kind") == "material":
        canonical = hit["canonical"]
        # защита: OSN-канон не должен терять уточнитель исходного имени
        lost = _qualifier_stems(name) - _qualifier_stems(canonical)
        if not lost:
            return canonical
    return _light_norm(name)


def canonical_process(name: str) -> str:
    """Сырое имя процесса → канон.

    Приоритет: существующий реестр процессов (map_process, если он уже
    канонизировал в что-то кроме 'other') → OSN (label PROCESS) → лёгкий норм.
    """
    mp = map_process(name)
    if mp and mp != "other":
        return mp
    key = _norm_key(name)
    if key:
        hit = _load().get(key)
        if hit is not None and hit.get("kind") == "process":
            return hit["canonical"]
    # процесс — контролируемый словарь: неразрешённое сырьё (предложения,
    # уравнения реакций) не попадает в атрибут ребра как есть — это 'other',
    # как и в исходном map_process.
    return "other"


# химическая формула/символ как ОДИН токен без пробелов, где регистр значим:
# CO (моноксид) ≠ Co (кобальт) ≠ CuO (оксид меди). Обычный slug приводит к
# нижнему регистру и слил бы их в один id.
_FORMULA_CHARSET = re.compile(r"[0-9A-Za-z()·.+\-]+")
_NON_SLUG = re.compile(r"[^0-9A-Za-z]+")


def _is_formula_token(canonical: str) -> bool:
    """True для химической формулы/символа (регистр значим). Обычные словоформы,
    включая Title-case «Zinc», сюда не попадают — их id остаётся lower-case."""
    s = canonical.strip()
    if not s or " " in s or not _FORMULA_CHARSET.fullmatch(s):
        return False
    n_upper = sum(1 for ch in s if ch.isupper())
    has_digit = any(ch.isdigit() for ch in s)
    # формула: есть цифра, ИЛИ ≥2 заглавных (CuO, BaSO4), ИЛИ короткий символ
    # элемента ≤2 символов с заглавной (Co, Ni, Zn, CO).
    return has_digit or n_upper >= 2 or (len(s) <= 2 and s[0].isupper())


def material_ext_id(name: str) -> str:
    """Глобальный канонический внешний id материала — ЕДИНЫЙ ключ дедупа.

    Для химических формул/символов регистр сохраняется (иначе CO/Co/CuO слились
    бы в один id при lower-case-слаге); для остальных имён — обычный slug.
    Функция от canonical: варианты одного материала (общий канон) всегда дают
    один id; регистр-сохранение лишь разводит РАЗНЫЕ вещества с одинаковым
    slug'ом в нижнем регистре."""
    # локальный импорт: run.py импортирует entities → цикл на уровне модуля
    from .run import slugify
    canonical = canonical_material(name)
    if _is_formula_token(canonical):
        t = unicodedata.normalize("NFKD", canonical)          # ₄ → 4
        t = _NON_SLUG.sub("-", t).strip("-")[:48].strip("-")
        if t:
            return f"mat:{t}"
    return f"mat:{slugify(canonical)}"
