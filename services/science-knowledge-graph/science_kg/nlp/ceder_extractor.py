"""
Ceder-style conditions extractor.

Uses rule-based parsers from conditions_extraction.py (Ceder Group, MIT licence)
to find temperatures, times, and atmospheres in synthesis sentences, then pairs
each condition with the nearest material mention to emit PROCESSED_BY Relations.
"""

import re
from science_kg.nlp.conditions_extraction import get_temperatures_toks, get_times_toks
from science_kg.models import Relation, RelationType, EntityType
from science_kg.nlp.normalizer import canonical_material, canonical_process

# ── Material detection ────────────────────────────────────────────────────────
# Named aliases: case-insensitive. Chemical formulas: case-sensitive (must start
# with uppercase) to avoid false positives like "at", "in", "as".
_MAT_NAMED_RE = re.compile(
    r"\b(Ti-6Al-4V|Ti6Al4V|Ti-6-4|Ti64|Ti\d{3,}|Ti-\d+\w*"
    r"|NiTi|nitinol|Ni-Ti|Ti-Ni"
    r"|316L|304L|Inconel\s*\d*|IN\d{3}"
    r"|AlSi10Mg|AlSiMg)\b",
    re.I,
)
# Chemical formulas must start with an uppercase letter followed by digits/elements
_MAT_FORMULA_RE = re.compile(r"\b([A-Z][a-z]?(?:\d+(?:\.\d+)?[A-Z][a-z]?)*\d*)\b")

# ── Atmosphere keywords ───────────────────────────────────────────────────────
_ATMO_RE = re.compile(
    r"\b(vacuum|argon|nitrogen|n2|air|helium|hydrogen|inert|reducing|oxidizing"
    r"|furnace\s+cool(?:ing|ed)?|air\s+cool(?:ing|ed)?|water\s+quench(?:ing|ed)?"
    r"|вакуум|аргон|азот|воздух)\b",
    re.I,
)

# Min character length for material text
_MIN_MAT_LEN = 2


def _find_material_in_sentence(sentence: str) -> str | None:
    """Return the canonical name of the first material-like token in sentence."""
    m = _MAT_NAMED_RE.search(sentence)
    if m and len(m.group()) >= _MIN_MAT_LEN:
        return canonical_material(m.group())
    # Fall back to formula pattern (case-sensitive, stricter)
    for fm in _MAT_FORMULA_RE.finditer(sentence):
        text = fm.group()
        # Must contain at least one digit to be a formula (not a plain word)
        if len(text) >= _MIN_MAT_LEN and re.search(r"\d", text):
            return canonical_material(text)
    return None


def _tokenize(sentence: str) -> list[str]:
    """Split on whitespace, strip trailing punctuation, add sentinel so the
    last real token is always covered by Ceder's zip(toks, toks[1:]) loop."""
    tokens = [tok.rstrip(".,;:!?)") for tok in sentence.split()]
    return tokens + [""]  # sentinel: prevents last token being skipped


def _fmt_temp(value: str, units: str) -> str:
    u = units if units not in ("N/A", "") else ""
    return canonical_process(f"{value}{u}".strip())


def _fmt_time(value: str, units: str) -> str:
    u = units if units not in ("N/A", "") else ""
    return canonical_process(f"{value} {u}".strip())


def extract_ceder_relations(text: str, doc_id: str) -> list[Relation]:
    """
    Extract uses_material relations from synthesis text using Ceder rule-based parsers.

    For each sentence: find temperatures, times, atmospheres → pair with
    nearest material → emit Relation(PROCESS --uses_material--> MATERIAL).
    """
    relations: list[Relation] = []
    seen: set[tuple[str, str]] = set()

    # Pre-normalize OCR artifacts before tokenizing (e.g. 900oC → 900°C)
    text = re.sub(r"(\d[\d.]*)\s*[oO][Cc]\b", r"\1°C", text)
    text = re.sub(r"(\d[\d.]*)\s*[oO][Ff]\b", r"\1°F", text)

    sentences = re.split(r"(?<=[.!?])\s+", text.strip())

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue

        material = _find_material_in_sentence(sent)
        if not material:
            continue

        tokens = _tokenize(sent)
        if len(tokens) < 2:
            continue

        def _add(process_text: str, verb: str) -> None:
            process = canonical_process(process_text)
            if not process or len(process) < 2:
                return
            key = (process, material)
            if key not in seen:
                seen.add(key)
                relations.append(
                    Relation(
                        source=process,
                        source_type=EntityType.PROCESS,
                        relation=RelationType.USES_MATERIAL,
                        target=material,
                        target_type=EntityType.MATERIAL,
                        verb=verb,
                        source_doc=doc_id,
                    )
                )

        # Temperatures
        for t in get_temperatures_toks(tokens):
            _add(_fmt_temp(t["value"], t["units"]), "ceder:temperature")

        # Times
        for t in get_times_toks(tokens):
            _add(_fmt_time(t["value"], t["units"]), "ceder:time")

        # Atmospheres (own regex — get_environment needs spaCy objects)
        for m in _ATMO_RE.finditer(sent):
            _add(m.group().strip(), "ceder:atmosphere")

    return relations
