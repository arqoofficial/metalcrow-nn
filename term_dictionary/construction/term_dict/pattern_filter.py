"""Precision filter for EntityRuler patterns.

The cheap term sources (YAKE keyphrases, Wikidata alias dumps) inject a tail of
junk that fires false-positive entity matches at corpus scale. The critical
review named the categories concretely; this module removes them with
high-precision, auditable rules (every drop carries a reason, nothing is
silently truncated):

  * Russian **verb-phrase fragments** — a multiword term whose leading token is a
    process/verbal noun (``получения``/``извлечения``/``производство`` …) is a
    YAKE fragment, not a term (``получения сульфата никеля``).
  * **Dangling-modifier truncations** — a multiword RU term ending in a bare
    adjectival inflection (``сульфата никеля высокой``, ``… батарейного``).
  * **Smart-quote / stray-punctuation** fragments (``'top service'``).
  * **Over-generic English** singletons (``extraction``/``purification`` …) that
    match everything.
  * **Bare 2-letter YAKE tokens** (``ЭР``/``ПМ``) with no dictionary provenance —
    real element symbols (Ni) and Schwartz-Hearst acronyms are kept because they
    carry a trusted source.
  * **Declension duplicates** — ``раствор``/``раствора``, ``извлечение``/
    ``извлечения`` collapse to one surface form.

Kept deliberately conservative: when in doubt we keep the term (recall), and we
never drop anything that came from a trusted source (seed/wikidata/wikipedia/
schwartz_hearst) other than the generic-English and declension passes.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Trademark / model-code aliases that leak from Wikidata ("Tatum-T" as a copper
# alias): a capitalized Latin word + hyphen + a short code tail. Real hyphenated
# domain terms are Cyrillic (фильтр-пресс) or lower-case, so this is safe.
_BRAND_CODE_RE = re.compile(r"^[A-Z][a-zA-Z]+-[A-Z0-9]{1,3}$")

# Leading tokens that mark a Russian verbal/process-noun fragment when the term
# is multiword ("получения сульфата никеля", "извлечения МПГ").
_RU_VERBAL_SUFFIXES = ("ения", "ания", "ования", "ение", "ание", "енного",
                       "енной", "ения,")

# Final-token adjectival inflections that signal a truncated phrase when they
# trail a noun ("сульфата никеля высокой", "…батарейного").
_RU_DANGLING_ADJ_SUFFIXES = ("ой", "ого", "его", "ым", "ыми", "ую", "ая", "ое",
                             "ых", "ому", "ей")

# Over-generic English single words: real but so broad they mismatch at scale.
# Domain-specific multiword forms (pressure oxidation, solvent extraction) are
# unaffected — this only blocks the bare singleton.
_GENERIC_EN = frozenset({
    "extraction", "refinement", "purification", "refining", "treatment",
    "process", "processing", "method", "material", "separation", "production",
    "converter", "sterilizer", "solution", "recovery", "leaching", "reduction",
    "oxidation", "product", "system", "device", "unit", "plant",
})

# Sources we trust enough to never prune (except the generic/declension passes).
_TRUSTED = frozenset({"seed", "wikidata", "wikipedia", "schwartz_hearst", "contract"})


def _is_cyr(text: str) -> bool:
    return any("Ѐ" <= c <= "ӿ" for c in text)


def junk_reason(term: str, label: str, sources: set[str] | None = None) -> str | None:
    """Return a reason string if ``term`` is junk, else ``None``."""
    sources = sources or set()
    t = term.strip()
    if not t:
        return "empty"

    # Smart quotes / stray non-term punctuation.
    if any(ch in t for ch in "‘’“”«»"):
        return "smart_quote_fragment"

    # Trademark/model-code alias (applies regardless of source — this is exactly
    # the Wikidata alias-dump noise, e.g. copper→"Tatum-T").
    if _BRAND_CODE_RE.match(t):
        return "brand_code"

    tokens = t.split()

    # Bare single-character token (element symbols B/C/I/S/U…): matches far too
    # much text ("C", "I") to be a useful gazetteer entry — drop even if it came
    # from Wikidata's element sweep.
    if len(tokens) == 1 and len(t) == 1:
        return "single_char"

    # Bare 2-char token with no trusted dictionary provenance (ЭР, ПМ). Element
    # symbols / SH acronyms carry a trusted source and survive.
    if len(tokens) == 1 and len(t) <= 2 and not (sources & _TRUSTED):
        return "bare_short_token"

    # Over-generic English singleton (only when not multiword).
    if len(tokens) == 1 and t.lower() in _GENERIC_EN:
        return "generic_english"

    if len(tokens) >= 2 and _is_cyr(t):
        first = tokens[0].lower()
        if first.endswith(_RU_VERBAL_SUFFIXES) and len(first) >= 6:
            return "ru_verbal_fragment"
        last = tokens[-1].lower()
        # Dangling adjective as the final token after a noun → truncation.
        if last.endswith(_RU_DANGLING_ADJ_SUFFIXES) and len(last) >= 5 \
                and not last.endswith(("ость", "ство")):
            return "dangling_modifier"
    return None


def is_junk_surface_form(term: str) -> bool:
    """Source-independent 'always junk' check for synonym-map surface forms.

    Only the unconditional categories (brand/model codes, smart-quote
    fragments) — NOT the source-dependent pattern rules — so element symbols
    (Ni) and short acronyms are never stripped from a concept's surface set.
    """
    t = term.strip()
    if not t:
        return True
    if any(ch in t for ch in "‘’“”"):
        return True
    return bool(_BRAND_CODE_RE.match(t))


def _declension_key(term: str) -> str | None:
    """A coarse RU stem key: term minus up to 3 trailing inflection chars.

    Only used to collapse near-identical single-token RU declension variants
    that share a long common prefix; returns ``None`` for terms it should not
    touch (multiword, Latin, or too short to stem safely).
    """
    t = term.strip().lower()
    if " " in t or not _is_cyr(t) or len(t) < 6:
        return None
    # Strip a short inflectional tail; keep a >=5-char stem so distinct words
    # (медь vs медный) are never merged.
    for k in (3, 2, 1):
        if len(t) - k >= 5:
            return t[: len(t) - k]
    return None


def dedup_declension(terms: list[tuple[str, str, set]]) -> tuple[list, list]:
    """Collapse RU declension duplicates within the same label.

    ``terms`` = list of ``(term, label, sources)``. Returns ``(kept, dropped)``
    where a dropped item is ``(term, label, reason)``. The surviving variant is
    the shortest (nominative-ish), tie-broken by more sources.
    """
    groups: dict[tuple[str, str], list[tuple[str, str, set]]] = {}
    passthrough: list[tuple[str, str, set]] = []
    for term, label, sources in terms:
        key = _declension_key(term)
        if key is None:
            passthrough.append((term, label, sources))
        else:
            groups.setdefault((label, key), []).append((term, label, sources))

    kept: list[tuple[str, str, set]] = list(passthrough)
    dropped: list[tuple[str, str, str]] = []
    for (label, _key), variants in groups.items():
        if len(variants) == 1:
            kept.append(variants[0])
            continue
        # Winner: shortest term, then most sources.
        winner = min(variants, key=lambda v: (len(v[0]), -len(v[2])))
        kept.append(winner)
        for v in variants:
            if v is not winner:
                dropped.append((v[0], v[1], f"declension_of:{winner[0]}"))
    return kept, dropped


__all__ = ["junk_reason", "dedup_declension"]
