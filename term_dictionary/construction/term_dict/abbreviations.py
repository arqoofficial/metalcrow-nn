"""Domain jargon / abbreviation subsystem (extends Schwartz-Hearst).

The bare Schwartz-Hearst pass finds ``acronym ↔ expansion`` pairs per document.
Russian technical prose then expands the *same* acronym in many grammatical
cases — «электроэкстракция», «электроэкстракции», «электроэкстракцию» — so the
same concept arrives as several surface strings. This module consolidates the
raw pairs into one record per acronym:

  * **declension-robust grouping** of expansion variants (a light RU
    case-ending normalizer collapses inflected forms of the same phrase);
  * a **canonical expansion** (the modal / nominative-leaning variant) plus the
    full ``expansion_variants`` list;
  * a **confidence** per acronym, from initial-letter coverage of the expansion
    and corpus support (how many documents attest it);
  * corpus support counts (``n_occurrences``, ``n_docs``).

The output feeds the ontology's ``entity_same_as(confidence, method)`` with
``method="schwartz_hearst"`` — each acronym→expansion is a same-concept link
with an honest confidence, and drives the abbreviation-aware EntityRuler
(acronym + every attested variant map to the canonical concept).

Pure-Python, RU+EN, no model load.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from . import schwartz_hearst

logger = logging.getLogger(__name__)

# Country names (a few inflected RU forms) that surface as spurious "expansions".
_COUNTRIES = frozenset({
    "канада", "перу", "армения", "армении", "россия", "россии", "китай", "китая",
    "норвегия", "норвегии", "финляндия", "австралия", "чили", "замбия", "конго",
    "индонезия", "бразилия", "юар", "сша", "canada", "peru", "armenia", "russia",
    "china", "norway", "finland", "australia", "chile", "zambia", "congo", "brazil",
})
# Math / formula markers that mean the "expansion" is an equation fragment (S/→sin α).
_MATH_CHARS = frozenset("αβγδθλμπσφω∑∫√±×÷")
_MATH_WORD_RE = re.compile(r"\b(sin|cos|tan|log|exp|lim|max|min)\b", re.I)


def _is_junk_abbrev(acronym: str, expansion: str) -> bool:
    a, e = acronym.strip(), expansion.strip()
    if a.lower() == e.lower():
        return True                                   # identity  Mining→Mining
    if e.lower() in _COUNTRIES:
        return True                                   # country name as "expansion"
    if any(c in _MATH_CHARS for c in e) or _MATH_WORD_RE.search(e):
        return True                                   # formula leak  S/→sin α
    if "/" in a and len(a.replace("/", "")) <= 1:
        return True                                   # "S/" style non-acronym
    return False

# Russian case endings, longest-first, stripped to a declension-insensitive stem
# for grouping inflected variants of one expansion. Conservative: only applied
# to tokens long enough to keep a >=4-char stem, so short words are untouched.
_RU_CASE_ENDINGS = (
    "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ах", "ях", "ов",
    "ев", "ая", "яя", "ою", "ею", "ый", "ий", "ой", "ем", "ом", "ую", "юю",
    "ы", "и", "а", "я", "у", "ю", "е", "о", "й", "ь",
)


def _stem_ru_word(w: str) -> str:
    w = w.lower()
    if len(w) < 6:
        return w
    for end in _RU_CASE_ENDINGS:
        if w.endswith(end) and len(w) - len(end) >= 4:
            return w[: -len(end)]
    return w


def _expansion_key(expansion: str) -> str:
    """Declension-insensitive grouping key for a whole expansion phrase."""
    return " ".join(_stem_ru_word(t) for t in expansion.split())


def _initial_coverage(acronym: str, expansion: str) -> float:
    """Fraction of acronym letters that begin a word of the expansion.

    A strong Schwartz-Hearst match (each acronym letter is a word initial) →
    1.0; partial → lower. Script-agnostic (RU + EN).
    """
    letters = [c for c in acronym.lower() if c.isalnum()]
    if not letters:
        return 0.0
    initials = [w[0].lower() for w in expansion.split() if w]
    hits, ii = 0, 0
    for c in letters:
        while ii < len(initials) and initials[ii] != c:
            ii += 1
        if ii < len(initials):
            hits += 1
            ii += 1
    return hits / len(letters)


@dataclass
class Abbreviation:
    acronym: str
    canonical_expansion: str
    expansion_variants: list[str] = field(default_factory=list)
    lang: str = "ru"
    confidence: float = 0.0
    n_occurrences: int = 0
    n_docs: int = 0

    def to_dict(self) -> dict:
        return {
            "acronym": self.acronym,
            "canonical_expansion": self.canonical_expansion,
            "expansion_variants": self.expansion_variants,
            "lang": self.lang,
            "confidence": self.confidence,
            "n_occurrences": self.n_occurrences,
            "n_docs": self.n_docs,
        }


def _detect_lang(text: str) -> str:
    return "ru" if any("Ѐ" <= c <= "ӿ" for c in text) else "en"


def extract_abbreviations(docs: list[str]) -> list[Abbreviation]:
    """Consolidate Schwartz-Hearst pairs across docs into abbreviation records."""
    # acronym -> expansion_surface -> [doc_indices]
    occ: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for di, doc in enumerate(docs):
        for pair in schwartz_hearst.extract_pairs(doc):
            # Collapse newline / whitespace artifacts from the parsed text.
            long_form = " ".join(pair.long_form.split())
            occ[pair.short_form][long_form].append(di)

    out: list[Abbreviation] = []
    for acronym, expansions in occ.items():
        # Group expansion surfaces by declension-insensitive key.
        by_key: dict[str, list[str]] = defaultdict(list)
        surf_docs: dict[str, set[int]] = {}
        for surf, dis in expansions.items():
            by_key[_expansion_key(surf)].append(surf)
            surf_docs[surf] = set(dis)
        # Dominant sense = key with the most attesting documents.
        best_key = max(
            by_key,
            key=lambda k: (len({d for s in by_key[k] for d in surf_docs[s]}),
                           -min(len(s) for s in by_key[k])),
        )
        variants = sorted(set(by_key[best_key]))
        # Canonical = variant seen in the most docs, tie-broken by shortest.
        canonical = max(variants, key=lambda s: (len(surf_docs[s]), -len(s)))
        docs_hit = {d for s in variants for d in surf_docs[s]}
        n_occ = sum(len(expansions[s]) for s in variants)

        if _is_junk_abbrev(acronym, canonical):
            continue  # identity / country / formula-fragment — not an abbreviation

        coverage = _initial_coverage(acronym, canonical)
        support = min(1.0, len(docs_hit) / 2.0)   # 1 doc → 0.5, ≥2 docs → 1.0
        confidence = round(min(1.0, 0.6 * coverage + 0.4 * support), 3)

        out.append(Abbreviation(
            acronym=acronym,
            canonical_expansion=canonical,
            expansion_variants=variants,
            lang=_detect_lang(canonical),
            confidence=confidence,
            n_occurrences=n_occ,
            n_docs=len(docs_hit),
        ))
    out.sort(key=lambda a: (-a.confidence, a.acronym))
    logger.info("Consolidated %d abbreviations from %d docs", len(out), len(docs))
    return out


__all__ = ["Abbreviation", "extract_abbreviations"]
