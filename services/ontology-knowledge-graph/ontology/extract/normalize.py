# -*- coding: utf-8 -*-
"""
Нормализация сырого выхода экстрактора (стадия «Normalize» конвейера).

- parse_value: строка значения → (ValueRange, uncertainty). Понимает
  «95–97», «≤1000», «>90», «229 ± 7», «0,62», «1 В (НВЭ)».
- normalize_unit: единица → каноническая запись + СИ-конверсия ТОЛЬКО для
  истинно размерных (°C→K, мин→с, ГПа→МПа); шкалы твёрдости не конвертируются
  никогда — уходят в scale.
- map_process: свободное имя операции → канон из PROCESS_SEED (алиасы RU/EN),
  иначе 'other' (текст сохраняется в extra).
"""
from __future__ import annotations

import re
from typing import Optional

from ..contracts import PROCESS_SEED, HardnessScale, ValueRange

_NUM = r"[-+]?\d+(?:[.,]\d+)?"
_RE_RANGE = re.compile(rf"({_NUM})\s*[–\-−…]\s*({_NUM})")
_RE_PM = re.compile(rf"({_NUM})\s*±\s*({_NUM})")
_RE_LE = re.compile(rf"[≤<]\s*=?\s*({_NUM})")
_RE_GE = re.compile(rf"(?:[≥>]\s*=?|более|не менее|свыше)\s*({_NUM})")
_RE_NUM = re.compile(_NUM)


def _f(s: str) -> float:
    return float(s.replace(",", "."))


def parse_value(raw: str | float | int | None) -> tuple[Optional[ValueRange], Optional[dict]]:
    """Строка из текста → (ValueRange, uncertainty|None). None — если чисел нет."""
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)):
        return ValueRange(nominal=float(raw)), None
    s = str(raw).strip()
    m = _RE_PM.search(s)
    if m:
        return ValueRange(nominal=_f(m.group(1))), {"sd": _f(m.group(2))}
    m = _RE_RANGE.search(s)
    if m:
        lo, hi = _f(m.group(1)), _f(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return ValueRange(min=lo, max=hi), None
    m = _RE_LE.search(s)
    if m:
        return ValueRange(max=_f(m.group(1))), None
    m = _RE_GE.search(s)
    if m:
        return ValueRange(min=_f(m.group(1))), None
    m = _RE_NUM.search(s)
    if m:
        return ValueRange(nominal=_f(m.group(0))), None
    return None, None


# ── единицы ──────────────────────────────────────────────────────────────

_HARDNESS = {s.value.lower(): s.value for s in HardnessScale if s.value != "none"}

# каноническое имя + аффинная конверсия в СИ-подобный канон (k, b): x_canon = k*x + b
_UNITS: dict[str, tuple[str, float, float]] = {
    "°c": ("K", 1.0, 273.15), "с": ("K", 1.0, 273.15), "c": ("K", 1.0, 273.15),
    "к": ("K", 1.0, 0.0), "k": ("K", 1.0, 0.0),
    "мпа": ("MPa", 1.0, 0.0), "mpa": ("MPa", 1.0, 0.0),
    "гпа": ("MPa", 1000.0, 0.0), "gpa": ("MPa", 1000.0, 0.0),
    "па": ("Pa", 1.0, 0.0), "pa": ("Pa", 1.0, 0.0),
    "%": ("%", 1.0, 0.0), "проц": ("%", 1.0, 0.0),
    "г/л": ("g/l", 1.0, 0.0), "g/l": ("g/l", 1.0, 0.0),
    "мг/л": ("mg/l", 1.0, 0.0), "mg/l": ("mg/l", 1.0, 0.0),
    "мг/дм3": ("mg/l", 1.0, 0.0), "мг/дм³": ("mg/l", 1.0, 0.0),
    "м3/ч": ("m3/h", 1.0, 0.0), "м³/ч": ("m3/h", 1.0, 0.0), "m3/h": ("m3/h", 1.0, 0.0),
    "м/ч": ("m/h", 1.0, 0.0), "m/h": ("m/h", 1.0, 0.0),
    "квт·ч/т": ("kWh/t", 1.0, 0.0), "квтч/т": ("kWh/t", 1.0, 0.0), "kwh/t": ("kWh/t", 1.0, 0.0),
    "в": ("V", 1.0, 0.0), "v": ("V", 1.0, 0.0), "мв": ("V", 0.001, 0.0),
    "а/м2": ("A/m2", 1.0, 0.0), "а/м²": ("A/m2", 1.0, 0.0), "a/m2": ("A/m2", 1.0, 0.0),
    "мин": ("s", 60.0, 0.0), "min": ("s", 60.0, 0.0),
    "ч": ("h", 1.0, 0.0), "h": ("h", 1.0, 0.0),
    "мкм": ("um", 1.0, 0.0), "um": ("um", 1.0, 0.0), "µм": ("um", 1.0, 0.0),
    "г/т": ("g/t", 1.0, 0.0), "g/t": ("g/t", 1.0, 0.0),
}


def normalize_unit(raw_unit: str | None, value: Optional[ValueRange]
                   ) -> tuple[str, str, Optional[ValueRange]]:
    """→ (unit_канон, hardness_scale, value_в_каноне). Твёрдость не конвертируем."""
    u = (raw_unit or "").strip()
    if u.lower() in ("null", "none", "-", "—", "n/a"):
        u = ""                                  # LLM возвращает литерал «null»
    key = u.lower().replace(" ", "")
    if key in _HARDNESS:
        return u, _HARDNESS[key], value
    hit = _UNITS.get(key)
    if hit is None or value is None:
        return u, "none", value
    canon, k, b = hit
    conv = lambda x: None if x is None else k * x + b
    return canon, "none", ValueRange(
        min=conv(value.min), nominal=conv(value.nominal), max=conv(value.max))


# ── процессы ─────────────────────────────────────────────────────────────

_PROCESS_ALIASES: dict[str, str] = {}
for _d in PROCESS_SEED.values():
    for _a in [_d.name.value, *_d.aliases]:
        _PROCESS_ALIASES[_a.lower()] = _d.name.value


def map_process(raw: str | None) -> str:
    if not raw:
        return "other"
    key = raw.strip().lower()
    if key in _PROCESS_ALIASES:
        return _PROCESS_ALIASES[key]
    for alias, canon in _PROCESS_ALIASES.items():
        if len(alias) > 4 and (alias in key or key in alias):
            return canon
    return "other"
