"""Canonical name normalization for materials and regimes."""

import json
import re
from pathlib import Path

_SYNONYM_MAP_PATH = Path(__file__).parents[1] / "data" / "synonym_map.json"

# Chemical element symbols relevant to non-ferrous metallurgy, in canonical case
# (capital first letter). These are the single most informative terms in a
# question ("Au, Ag и МПГ", "электроэкстракция Ni") yet are 2 chars, so the
# retriever's length filters drop them — this set lets them back in for the
# content channel and passage-window scoring. Deliberately curated (not the full
# periodic table) and case-SENSITIVE: matching only "As"/"In" — not the English
# stop words "as"/"in" — and omitting ambiguous symbols like No/Os/Be that
# collide with issue numbers or ordinary words in the corpus.
ELEMENT_SYMBOLS = frozenset(
    {
        "Cu", "Ni", "Co", "Fe", "Zn", "Pb", "As", "Se", "Te", "Sb", "Bi", "Sn",
        "Ag", "Au", "Pt", "Pd", "Rh", "Ru", "Ir", "Cr", "Mn", "Mg", "Ca", "Al",
        "Si", "Ti", "Mo", "Cd", "Hg", "Ga", "Ge", "Re", "Tl", "Zr", "Nb", "Ta",
        "Na", "Ca", "Mg",
    }
)


def is_element_symbol(token: str) -> bool:
    """True for a chemical element symbol written in canonical case (Au, Ni,
    As…). Case-sensitive so English stop words 'as'/'in' don't collide with the
    arsenic/indium symbols (SPEC §B1/§B2 — short but critical terms)."""
    return token in ELEMENT_SYMBOLS


def _build_synonym_lookup(label: str) -> dict[str, str]:
    """surface_form (lowercased) -> canonical, from term_dictionary's synonym_map.json
    (copied snapshot, see data/synonym_map.json), filtered to one label and to
    `needs_review: false` clusters only — term_dictionary itself flags cross-lingual
    "false friend" clusters with `needs_review: true`; using those would risk
    collapsing genuinely different terms into one canonical form."""
    if not _SYNONYM_MAP_PATH.exists():
        return {}
    concepts = json.loads(_SYNONYM_MAP_PATH.read_text(encoding="utf-8"))
    lookup: dict[str, str] = {}
    for concept in concepts:
        if concept.get("needs_review") or concept.get("label") != label:
            continue
        canonical = concept["canonical"]
        for surface in concept.get("surface_forms", []):
            lookup[surface.strip().lower()] = canonical
    return lookup


_MATERIAL_SYNONYM_LOOKUP = _build_synonym_lookup("MATERIAL")
_PROCESS_SYNONYM_LOOKUP = _build_synonym_lookup("PROCESS")


def _looks_like_formula(text: str) -> bool:
    """Chemical/alloy formulas conventionally carry case-significant internal
    capitals (TiN, AlSi10Mg, Ti-6Al-4V — same signal as ceder_extractor's
    `_MAT_FORMULA_RE`). term_dictionary's synonym_map.json isn't curated
    against this domain's formula casing (e.g. it treats "tin" as the metal,
    which lowercase-collides with "TiN" = titanium nitride) — skip the
    synonym fallback for anything that looks like a formula rather than
    silently losing case-significant identity."""
    return any(c.isupper() for c in text[1:])

# ── Material aliases ──────────────────────────────────────────────────────────
# canonical → set of known aliases (all lowercase for matching)
_MATERIAL_ALIASES: dict[str, list[str]] = {
    "Ti-6Al-4V": [
        "ti6al4v",
        "ti-6-4",
        "ti64",
        "grade 5",
        "grade5",
        "vt6",
        "вт6",
    ],
    "NiTi": [
        "tini",
        "nitinol",
        "ni-ti",
        "ti-ni",
    ],
    "316L": [
        "316l stainless",
        "aisi 316l",
        "ss316l",
    ],
    "AlSi10Mg": [
        "alsi10mg",  # already canonical, keeps it in map
    ],
}

# Reverse index: lowercase alias → canonical
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canon, _aliases in _MATERIAL_ALIASES.items():
    _ALIAS_TO_CANONICAL[_canon.lower()] = _canon
    for _a in _aliases:
        _ALIAS_TO_CANONICAL[_a.lower()] = _canon


def canonical_material(text: str) -> str:
    """Return the canonical material name: hand-written alias map first, then
    term_dictionary's synonym_map.json as a fallback (skipped for
    formula-looking text, see `_looks_like_formula`), else the original text."""
    stripped = text.strip()
    key = stripped.lower()
    if key in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[key]
    if _looks_like_formula(stripped):
        return stripped
    return _MATERIAL_SYNONYM_LOOKUP.get(key, stripped)


# ── Process normalization ──────────────────────────────────────────────────────
_RE_OCR_OC = re.compile(r"(\d[\d.]*)\s*[oO][Cc]\b")
_RE_OCR_OF = re.compile(r"(\d[\d.]*)\s*[oO][Ff]\b")
_RE_DEG_SPC = re.compile(r"\s*°\s*([CFKcfk])\b")
_RE_MULTI_SP = re.compile(r" {2,}")

# Aliases for atmosphere / cooling method → canonical
_PROCESS_ALIASES: dict[str, str] = {
    "vacuum": "vacuum",
    "in vacuum": "vacuum",
    "vac": "vacuum",
    "вакуум": "vacuum",
    "argon": "argon",
    "ar": "argon",
    "аргон": "argon",
    "nitrogen": "nitrogen",
    "n2": "nitrogen",
    "азот": "nitrogen",
    "air": "air",
    "воздух": "air",
    "furnace cool": "furnace cooling",
    "furnace cooled": "furnace cooling",
    "fc": "furnace cooling",
    "air cool": "air cooling",
    "air cooled": "air cooling",
    "ac": "air cooling",
    "water quench": "water quenching",
    "water quenched": "water quenching",
    "wq": "water quenching",
}


def canonical_process(text: str) -> str:
    """Normalize a process string: fix OCR, normalise °C spacing, lowercase."""
    t = text.strip()
    # OCR artefacts
    t = _RE_OCR_OC.sub(r"\1°C", t)
    t = _RE_OCR_OF.sub(r"\1°F", t)
    # "600 ° C" → "600°C"
    t = _RE_DEG_SPC.sub(lambda m: "°" + m.group(1).upper(), t)
    t = _RE_MULTI_SP.sub(" ", t)
    # Lowercase everything, then restore degree unit letter
    t_lower = t.lower()
    t_lower = re.sub(r"°([cf])", lambda m: "°" + m.group(1).upper(), t_lower)
    # Check alias table (after lowercasing), then term_dictionary's synonym map
    if t_lower in _PROCESS_ALIASES:
        return _PROCESS_ALIASES[t_lower]
    if _looks_like_formula(t):
        return t_lower
    return _PROCESS_SYNONYM_LOOKUP.get(t_lower, t_lower)
