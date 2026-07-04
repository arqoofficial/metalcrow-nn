"""Multi-word domain-term extraction.

Cheap, unsupervised candidate-term mining with YAKE! (no training, no LLM).
YAKE scores n-grams by statistical features (casing, position, term
frequency, dispersion); a *lower* score is more keyword-like. We run it per
language so RU and EN stopword lists apply correctly, then normalize scores to
a [0,1] confidence.

This surfaces multi-word terms («хлоридное выщелачивание», "pressure
oxidation") that the seed glossary and acronym pass miss. Output feeds the
same synonym clusterer as every other term source.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)


@dataclass(frozen=True)
class TermCandidate:
    """A mined multi-word term with a normalized keyword-ness score."""

    term: str
    score: float  # 0..1, higher = stronger candidate
    lang: str     # "ru" | "en"


def detect_lang(text: str) -> str:
    """Crude RU/EN split by Cyrillic character share — sufficient here."""
    cyr = len(_CYRILLIC_RE.findall(text))
    letters = sum(c.isalpha() for c in text)
    return "ru" if letters and cyr / letters > 0.3 else "en"


def extract_terms(
    text: str,
    lang: str | None = None,
    max_ngram: int = 3,
    top_k: int = 40,
) -> list[TermCandidate]:
    """Mine up-to-``max_ngram``-word candidate terms from a single document."""
    try:
        import yake
    except Exception:  # pragma: no cover
        logger.warning("yake not installed; skipping multi-word extraction")
        return []

    lang = lang or detect_lang(text)
    extractor = yake.KeywordExtractor(
        lan=lang,
        n=max_ngram,
        dedupLim=0.9,
        top=top_k,
        features=None,
    )
    raw = extractor.extract_keywords(text)
    if not raw:
        return []
    # YAKE: lower score = better. Invert + min-max normalize to [0,1].
    scores = [s for _, s in raw]
    lo, hi = min(scores), max(scores)
    span = (hi - lo) or 1.0
    out = []
    for kw, sc in raw:
        conf = 1.0 - (sc - lo) / span
        # Keep genuine multi-word terms or capitalized single tokens.
        if " " in kw or kw[:1].isupper():
            out.append(TermCandidate(term=kw.strip(), score=round(conf, 4), lang=lang))
    logger.debug("YAKE mined %d terms (%s)", len(out), lang)
    return out


def extract_terms_from_docs(
    docs: list[str],
    max_ngram: int = 3,
    top_k: int = 40,
    min_score: float = 0.3,
) -> list[TermCandidate]:
    """Union of candidate terms across docs, best score per surface form."""
    best: dict[str, TermCandidate] = {}
    for doc in docs:
        for cand in extract_terms(doc, max_ngram=max_ngram, top_k=top_k):
            if cand.score < min_score:
                continue
            key = cand.term.casefold()
            if key not in best or cand.score > best[key].score:
                best[key] = cand
    logger.info("YAKE: %d unique multi-word terms across %d docs",
                len(best), len(docs))
    return sorted(best.values(), key=lambda c: c.score, reverse=True)


__all__ = ["TermCandidate", "extract_terms", "extract_terms_from_docs", "detect_lang"]
