"""spaCy pipeline setup — bilingual (RU/EN), thread-safe singleton cache.

EN pipeline (en_core_sci_sm) — execution order:
  tok2vec → tagger → parser → EntityRuler → ner

EntityRuler fires before the model's own NER with overwrite_ents=True, so our
explicit domain patterns (MATERIAL/REGIME/PROPERTY/VALUE/EQUIPMENT) always win.
The scispaCy NER adds generic ENTITY labels for anything else — those are ignored
in extractor.py since ENTITY is not in our EntityType enum.

en_core_sci_sm vs en_core_web_sm:
  • Trained on 360k+ full-text PubMed / scientific papers
  • Better tokenization of chemical formulas (Al₂O₃, TiN), hyphenated alloy names
    (Ti-6Al-4V), unit expressions (wt%, °C/min), and parenthetical abbreviations
  • Same CNN architecture → same speed, same memory footprint (~12 MB)
"""

import threading

import spacy
from spacy.language import Language

from science_kg.nlp.patterns import ALL_PATTERNS

_cache: dict[str, Language] = {}
_lock = threading.Lock()

_CYRILLIC_THRESHOLD = 0.25


def detect_language(text: str) -> str:
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return "en"
    cyrillic = sum(1 for c in alpha_chars if "\u0400" <= c <= "\u04ff")
    return "ru" if cyrillic / len(alpha_chars) >= _CYRILLIC_THRESHOLD else "en"


def build_pipeline(model_name: str) -> Language:
    """Load spaCy model and inject EntityRuler with domain patterns before NER."""
    nlp = spacy.load(model_name)
    if "entity_ruler" not in nlp.pipe_names:
        # Place ruler before NER so our patterns have priority;
        # overwrite_ents=True makes the ruler win over any earlier NER output too.
        before = "ner" if "ner" in nlp.pipe_names else None
        kwargs = {"config": {"overwrite_ents": True}}
        if before:
            kwargs["before"] = before
        ruler = nlp.add_pipe("entity_ruler", **kwargs)
        ruler.add_patterns(ALL_PATTERNS)
    return nlp


def get_nlp(model_name: str) -> Language:
    """Return a cached pipeline instance. Thread-safe via double-checked locking."""
    if model_name not in _cache:
        with _lock:
            if model_name not in _cache:
                _cache[model_name] = build_pipeline(model_name)
    return _cache[model_name]


def get_nlp_for_text(text: str) -> Language:
    """Detect language and return the appropriate cached pipeline."""
    from science_kg.config import settings

    lang = detect_language(text)
    model = settings.spacy_model_ru if lang == "ru" else settings.spacy_model_en
    return get_nlp(model)
