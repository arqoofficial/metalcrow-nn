# -*- coding: utf-8 -*-
"""
MockExtractor — правило-основанный экстрактор с интерфейсом Extractor.

Назначение: (1) разработка/тесты конвейера без LLM-эндпоинта; (2) дешёвый
базовый уровень извлечения (аналог spaCy-прохода): предложения с числом и
единицей → измерение; предложения с маркером направления → вывод; операция —
по алиасам реестра процессов; вещества — по словарю доменных слов.

Качество заведомо ниже LLM: берёт только явные паттерны. Цитата всегда
дословная (само предложение), поэтому провенанс-инвариант соблюдается.
"""
from __future__ import annotations

import re

from .normalize import _PROCESS_ALIASES

_SENT = re.compile(r"(?<=[.!?;])\s+(?=[А-ЯA-ZЁ])")
_NUM_UNIT = re.compile(
    r"(\d[\d\s.,–\-±]*)\s*(%|°C|К\b|МПа|ГПа|мг/л|мг/дм3|мг/дм³|г/л|г/т|кВт·ч/т|"
    r"м3/ч|м³/ч|м/ч|мин\b|HV\d*|HRC|HB\b|В\b|А/м2|А/м²|мкм)", re.IGNORECASE)
_PROP_HINTS = [
    ("извлечени", "степень извлечения"), ("recovery", "recovery"),
    ("содержани", "содержание элемента"), ("твёрдост", "твёрдость"),
    ("твердост", "твёрдость"), ("прочност", "предел прочности"),
    ("текучест", "предел текучести"), ("сухой остаток", "сухой остаток"),
    ("минерализац", "минерализация"), ("скорост", "скорость потока"),
    ("потенциал", "потенциал"), ("плотность тока", "плотность тока"),
    ("температур", "температура"), ("расход", "расход"),
    ("выход по току", "выход по току"), ("энерго", "энергоёмкость"),
]
_DIR = [
    (re.compile(r"увелич|повыша|растёт|возраста|улучша", re.I), "increases"),
    (re.compile(r"сниж|уменьша|падает|ухудша", re.I), "decreases"),
    (re.compile(r"не (изменя|влия)|стабильн", re.I), "no_change"),
]
_MATERIAL_WORDS = re.compile(
    r"\b(концентрат\w*|штейн\w*|файнштейн\w*|шлак\w*|раствор\w*|электролит\w*|"
    r"руд[аыу]\w*|анод\w*|катод\w*|кек\w*|осадок|осадк\w*|пульп\w*|"
    r"сульфит натрия|кислот[аы]|вод[аы]|уголь|графит)\b", re.IGNORECASE)


class MockExtractor:
    model = "mock-rule-based"

    def warmup(self) -> None:
        pass

    def extract_chunk(self, text: str) -> dict:
        sentences = _SENT.split(text)
        process, proc_sent = "", ""
        for s in sentences:
            low = s.lower()
            for alias, canon in _PROCESS_ALIASES.items():
                if len(alias) > 4 and alias in low:
                    process, proc_sent = alias, s
                    break
            if process:
                break

        materials, seen = [], set()
        for s in sentences:
            for m in _MATERIAL_WORDS.finditer(s):
                name = m.group(0).lower()
                if name not in seen:
                    seen.add(name)
                    materials.append({"name": name, "role": "sample"})
        materials = materials[:6]

        measurements = []
        for s in sentences:
            m = _NUM_UNIT.search(s)
            if not m:
                continue
            prop = next((canon for hint, canon in _PROP_HINTS
                         if hint in s.lower()), "")
            if not prop:
                continue
            mat = _MATERIAL_WORDS.search(s)
            measurements.append({
                "property": prop, "value": m.group(1).strip(),
                "unit": m.group(2), "material": mat.group(0).lower() if mat else "",
                "quote": s.strip()[:400]})

        conclusions = []
        for s in sentences:
            direction = next((d for rx, d in _DIR if rx.search(s)), "")
            if not direction:
                continue
            prop = next((canon for hint, canon in _PROP_HINTS
                         if hint in s.lower()), "")
            if not prop:
                continue
            conclusions.append({
                "text": s.strip()[:300], "kind": "finding", "property": prop,
                "direction": direction, "factor": "по тексту",
                "quote": s.strip()[:400]})

        if not (measurements or conclusions):
            return {"experiments": [], "claims": []}
        if not process and not measurements:
            # нет ни операции, ни чисел — это утверждения уровня документа
            return {"experiments": [], "claims": [
                {**c, "process": ""} for c in conclusions[:8]]}
        return {"experiments": [{
            "label": (proc_sent or sentences[0])[:80].strip(),
            "process": process, "materials": materials,
            "temperature": "", "duration": "",
            "measurements": measurements[:15],
            "conclusions": conclusions[:8],
            "quote": (proc_sent or sentences[0]).strip()[:400]}],
            "claims": []}
