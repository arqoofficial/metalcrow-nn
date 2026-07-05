# -*- coding: utf-8 -*-
"""
Расширение запроса синонимами и аббревиатурами (query-side expansion).

Проблема ретрива: лексический канал (BM25/ts_rank) матчит термы буквально —
русский вопрос «обессоливание» не достаёт англоязычные пассажи «desalination»,
а «TDS» не находит «сухой остаток». Плотный канал (multilingual-mpnet) частично
кросс-язычен, но лексика тянет ранжирование и якоря, и именно она мажет по
кросс-язычным и жаргонным вопросам.

Здесь — чистое, только читающее расширение: терм запроса → он сам + его
кросс-язычные синонимы (кластеры `data/query_synonyms.json`, собранные из
выверенного словаря) + расшифровка аббревиатур. Многословные синонимы бьются на
содержательные токены под tsquery (OR по одиночным лексемам). Всё с потолками,
чтобы не раздувать запрос и не уводить семантику.

Артефакт готовится офлайн (`python -m ontology.data.build_query_synonyms`) и
коммитится под ontology/data/ — рантайм зависит только от stdlib. Файла нет →
модуль деградирует в тождество (никогда не ломает поиск).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_ARTIFACT = Path(__file__).resolve().parent / "data" / "query_synonyms.json"

# содержательный токен многословной формы (согласовано с query._content_terms)
_TOKEN = re.compile(r"[а-яёa-z0-9]{4,}", re.I)
# безопасная лексема для to_tsquery: только буквы/цифры. Форма с иными
# символами (`zn(ii)`, `kwh/t`) в tsquery — синтаксис, а не текст: одна такая
# форма роняет ОБА канала ретрива тихой деградацией до пустого ILIKE.
_SAFE_LEXEME = re.compile(r"[а-яёa-z0-9]+", re.I)
# похоже на химформулу/марку (Ti-6Al-4V, 316L, ВТ6) — расширять не надо
_FORMULA = re.compile(r"^[a-zа-яё]*\d|[a-z]\d|\d[a-z]", re.I)

_MAX_PER_TERM = 8       # терм + до 7 расширений
_MAX_QUERY_TERMS = 24   # общий потолок термов в запросе после расширения


def _load() -> tuple[dict[str, frozenset[str]], dict[str, str]]:
    """form(lower) → множество форм кластера; acronym/variant(lower) → расшифровка.
    При любой ошибке — пустые структуры (деградация в тождество)."""
    try:
        d = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}
    form2grp: dict[str, frozenset[str]] = {}
    for grp in d.get("groups", []):
        fs = frozenset(f.lower() for f in grp)
        for f in fs:
            form2grp[f] = fs
    abbrev = {k.lower(): v.lower() for k, v in d.get("abbrev", {}).items()}
    return form2grp, abbrev


_FORM2GRP, _ABBREV = _load()


def _content_tokens(text: str) -> set[str]:
    """Форма → одиночные БЕЗОПАСНЫЕ лексемы для tsquery. Одно слово отдаём как
    есть (даже 3 буквы: аббревиатуры, символы элементов), но только если это
    чистая лексема; многословную и «грязную» (`zn(ii)`) бьём на токены >=4."""
    text = text.strip().lower()
    if not text:
        return set()
    if " " not in text and "-" not in text and _SAFE_LEXEME.fullmatch(text):
        return {text} if len(text) >= 3 else set()
    return set(_TOKEN.findall(text))


def _looks_like_formula(t: str) -> bool:
    return bool(_FORMULA.search(t))


def expand_term(term: str) -> set[str]:
    """Терм → {терм} + синонимы (кросс-язык) + расшифровка аббревиатуры,
    одиночными лексемами. Формулы/марки не расширяем."""
    t = term.strip().lower()
    if not t:
        return set()
    out: set[str] = {t}
    if _looks_like_formula(t):
        return out
    exp = _ABBREV.get(t)
    if exp:
        out |= _content_tokens(exp)
    grp = _FORM2GRP.get(t)
    if grp:
        for form in grp:
            if form != t:
                out |= _content_tokens(form)
    if len(out) <= _MAX_PER_TERM:
        return out
    # оставить сам терм + самые длинные (обычно самые специфичные) расширения
    rest = sorted(out - {t}, key=len, reverse=True)[: _MAX_PER_TERM - 1]
    return {t, *rest}


def expand_query(terms: list[str], cap: int = _MAX_QUERY_TERMS) -> list[str]:
    """Список содержательных термов запроса → он же + расширения, без дублей,
    оригиналы первыми (сохраняют якорный вес), с общим потолком."""
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        tl = t.lower()
        if tl and tl not in seen:
            seen.add(tl)
            out.append(tl)
    for t in terms:
        for e in sorted(expand_term(t)):
            if e not in seen:
                seen.add(e)
                out.append(e)
                if len(out) >= cap:
                    return out
    return out


def known(term: str) -> bool:
    """Есть ли терм в словаре (аббревиатура или форма синоним-кластера).
    Нужен вызывающему для коротких (2–3 буквы) токенов запроса: общий
    токенизатор их отбрасывает, но известные аббревиатуры (TDS, МПГ, FCL)
    должны попадать в расширение."""
    t = term.strip().lower()
    return t in _ABBREV or t in _FORM2GRP


def present_forms(term: str) -> set[str]:
    """Формы, любую из которых достаточно найти в корпусе, чтобы считать терм
    «покрытым» (гейт честности не должен отсекать RU-вопрос про EN-only концепт)."""
    return expand_term(term)


def ready() -> bool:
    return bool(_FORM2GRP or _ABBREV)
