# -*- coding: utf-8 -*-
"""
Relocate: verbatim-цитата экстрактора → точный локатор в документе.

LLM не спрашивают о координатах (выдумает) — экстрактор отдаёт дословный
snippet, а позицию находим сами: точное вхождение в блок, затем fuzzy
(rapidfuzz partial_ratio). Ниже порога — локатор первого блока чанка и
пониженная уверенность (needs_review решает загрузчик/HITL).
"""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from .parse import Block

FUZZY_THRESHOLD = 85.0


@dataclass
class Located:
    locator_kind: str
    locator: str
    confidence_factor: float     # 1.0 точное; 0.9 fuzzy; 0.5 не найдено


def relocate(snippet: str, blocks: list[Block]) -> Located:
    s = " ".join(snippet.split())
    if not s:
        return Located("pdf_page", "p1", 0.5)
    for b in blocks:
        if s in b.text:
            return Located(b.locator_kind, b.locator, 1.0)
    best, best_score = None, 0.0
    for b in blocks:
        score = fuzz.partial_ratio(s, b.text)
        if score > best_score:
            best, best_score = b, score
    if best is not None and best_score >= FUZZY_THRESHOLD:
        return Located(best.locator_kind, best.locator, 0.9)
    fallback = blocks[0] if blocks else None
    return Located(fallback.locator_kind if fallback else "pdf_page",
                   fallback.locator if fallback else "p1", 0.5)
