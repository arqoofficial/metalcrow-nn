"""Schwartz-Hearst abbreviation / acronym extraction.

Extracts ``long form (short form)`` and ``short form (long form)`` pairs from
free text and validates them with the Schwartz & Hearst (2003) matching rule:
every character of the short form must appear, in order, in the long form.

Near-zero cost (pure Python, regex + string scan), works on Russian and
English alike — the character-subsequence test is script-agnostic, so it
handles «Пирометаллургическая плавка (ПМП)» as readily as
"pressure oxidation (POX)".

Reference: A. Schwartz & M. Hearst, "A simple algorithm for identifying
abbreviation definitions in biomedical text", PSB 2003.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# A candidate short form: 2-10 chars, containing at least one letter, mostly
# upper-case / digits. Covers RU (ПВП, АНОФ) and EN (POX, SX/EW) acronyms.
_SHORT_FORM_RE = re.compile(r"^[\w\-/.]{2,10}$", re.UNICODE)
# Sentence-ish boundary used to bound the left-context search for a long form.
_SENT_SPLIT_RE = re.compile(r"[.;:!?]\s|\n")
# Parenthetical: capture what is inside ( ), [ ] or « » adjacent parens.
_PAREN_RE = re.compile(r"[\(\[]\s*([^\(\)\[\]]{2,120}?)\s*[\)\]]", re.UNICODE)


@dataclass(frozen=True)
class AcronymPair:
    """A validated abbreviation-definition pair."""

    short_form: str
    long_form: str

    def as_tuple(self) -> tuple[str, str]:
        return (self.short_form, self.long_form)


# Element-symbol sequence (Latin), e.g. SO4, SiO4, Fe2O3, CuO. Used only in
# combination with a digit for short forms (so Latin acronyms POX/PGM/SX, which
# also look like element sequences, are NOT rejected).
_CHEM_FORMULA_SF = re.compile(r"^(?:[A-Z][a-z]?\d*){2,}$")
# Characters that mark a chemical reaction / equation / formula rather than a
# definition (incl. the middot used in hydrate formulas, e.g. CuCO3·Cu(OH)2).
_EQUATION_CHARS = frozenset("={}*→·•∙")
# A charge / ion tail such as "4 2-" or "(CN)4 2-".
_CHARGE_RE = re.compile(r"\d\s*[+-](?:\s|,|;|$)")
# A single whitespace-delimited token that is a chemical formula (has a digit
# and reads as element symbols), tolerating surrounding brackets/punctuation and
# internal parenthesised subscript groups, e.g. Co(OH)3, Cu(OH)2, Fe2(SO4)3.
_FORMULA_TOKEN_RE = re.compile(
    r"^[\(\[{]?(?:[A-Z][a-z]?\d*|\([A-Za-z0-9]+\)\d*)+[\)\]}]?[,;.]?$")
# Alphabetic runs (any script) of length >= 3 — the "real words" of a long form.
_WORD_RUN_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def _is_formula_token(tok: str) -> bool:
    core = tok.strip("()[]{},;:.«»")
    return bool(core) and any(ch.isdigit() for ch in core) \
        and bool(_FORMULA_TOKEN_RE.match(tok))


def _looks_like_chemistry(short_form: str, long_form: str) -> bool:
    """Reject reaction-equation / chemical-formula false positives.

    Real domain data ("...белый матт (богатый штейн)...") sits next to reaction
    formulas ("H2SO4 + 1/2 O2 = Fe2..."), and the subsequence test happily
    matches ``SO4`` against ``H2SO4``. A genuine acronym definition is a short
    natural-language phrase, so we drop candidates whose "long form" reads as
    chemistry: it carries equation operators, a charge tail, opens on a formula,
    is formula-token dominated, or lacks two real words.
    """
    # Short form that is itself a digit-bearing formula (SO4, SiO4, Fe2O3).
    if any(ch.isdigit() for ch in short_form) and _CHEM_FORMULA_SF.match(short_form):
        return True
    lf = long_form
    if any(c in _EQUATION_CHARS for c in lf) or "+" in lf:
        return True
    if _CHARGE_RE.search(lf):
        return True
    toks = lf.split()
    if not toks or _is_formula_token(toks[0]):  # definitions don't open on a formula
        return True
    # A real definition contains at least one natural-language word (single-word
    # expansions like "электроэкстракции (ЭЭ)" are valid); formula soup does not.
    n_words = len(_WORD_RUN_RE.findall(lf))
    if n_words == 0:
        return True
    if sum(_is_formula_token(t) for t in toks) >= max(n_words, 1) + 1:
        return True
    return False


def _is_candidate_short_form(token: str) -> bool:
    """Short form heuristic: compact, has a letter, not a plain lowercase word."""
    if not token or not _SHORT_FORM_RE.match(token):
        return False
    if not any(ch.isalpha() for ch in token):
        return False
    # Reject ordinary all-lowercase words like "около" — a genuine acronym has
    # at least one uppercase letter or a digit (POX, SX/EW, Fe2O3, ПВП).
    if token.islower() and not any(ch.isdigit() for ch in token):
        return False
    return True


def _first_letters_match(short_form: str, long_form: str) -> str | None:
    """Schwartz-Hearst validity test.

    Scan the short form right-to-left; every alphanumeric short-form char must
    be found (case-insensitively) as a subsequence walking the long form
    right-to-left, and the leftmost matched char must begin a long-form word.
    Returns the trimmed long form on success, else ``None``.
    """
    s = [c for c in short_form.lower() if c.isalnum()]
    if not s:
        return None
    long_lower = long_form.lower()

    s_idx = len(s) - 1
    l_idx = len(long_lower) - 1
    first_match_pos = -1

    while s_idx >= 0:
        cur = s[s_idx]
        # The first (rightmost) short-form char may match anywhere; subsequent
        # chars must match at a word start to keep the pairing tight.
        while l_idx >= 0:
            if long_lower[l_idx] == cur:
                break
            l_idx -= 1
        if l_idx < 0:
            return None
        first_match_pos = l_idx
        s_idx -= 1
        l_idx -= 1

    # The matched long form starts at first_match_pos; require it to fall on a
    # word boundary so we don't grab a mid-word slice.
    if first_match_pos > 0 and not long_lower[first_match_pos - 1].isspace():
        # Walk left to the start of the word containing first_match_pos.
        start = first_match_pos
        while start > 0 and not long_form[start - 1].isspace():
            start -= 1
        first_match_pos = start
    trimmed = long_form[first_match_pos:].strip()
    # Guardrail: long form should be longer than the short form and not absurd.
    if len(trimmed) < len(short_form):
        return None
    n_words = len(trimmed.split())
    if n_words > len(short_form) + 5 or n_words == 0:
        return None
    return trimmed


def _left_context(text: str, paren_start: int) -> str:
    """The candidate long-form window: text before ``(`` back to a boundary."""
    left = text[:paren_start]
    parts = _SENT_SPLIT_RE.split(left)
    return parts[-1].strip() if parts else left.strip()


def extract_pairs(text: str) -> list[AcronymPair]:
    """Extract validated acronym/definition pairs from ``text``.

    Handles both orderings:
    - ``long form (SF)`` — the common case, SF inside the parens.
    - ``SF (long form)`` — SF precedes, definition inside the parens.
    """
    pairs: dict[str, AcronymPair] = {}

    for m in _PAREN_RE.finditer(text):
        inner = m.group(1).strip()
        # --- Case A: parens hold the short form; long form is to the left. ---
        if _is_candidate_short_form(inner):
            window = _left_context(text, m.start())
            long_form = _first_letters_match(inner, window)
            if long_form and not _looks_like_chemistry(inner, long_form):
                pairs.setdefault(inner, AcronymPair(inner, long_form))
                continue

        # --- Case B: parens hold the long form; short form precedes them. ---
        window = _left_context(text, m.start())
        cand_sf = window.split()[-1] if window.split() else ""
        cand_sf = cand_sf.strip(",;:")
        if _is_candidate_short_form(cand_sf) and len(inner.split()) >= 2:
            long_form = _first_letters_match(cand_sf, inner)
            if long_form and not _looks_like_chemistry(cand_sf, long_form):
                pairs.setdefault(cand_sf, AcronymPair(cand_sf, long_form))

    logger.debug("Schwartz-Hearst extracted %d pairs", len(pairs))
    return list(pairs.values())


def extract_pairs_from_docs(docs: list[str]) -> list[AcronymPair]:
    """Union of pairs across many documents (first long form per SF wins)."""
    merged: dict[str, AcronymPair] = {}
    for doc in docs:
        for pair in extract_pairs(doc):
            merged.setdefault(pair.short_form, pair)
    logger.info("Schwartz-Hearst: %d unique acronyms across %d docs",
                len(merged), len(docs))
    return list(merged.values())


__all__ = ["AcronymPair", "extract_pairs", "extract_pairs_from_docs"]
