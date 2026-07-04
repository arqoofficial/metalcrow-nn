"""Nornickel term-dictionary pipeline.

Builds a RU/EN domain gazetteer for a spaCy EntityRuler plus a cross-lingual
canonical-concept synonym map, using cheap / no-LLM methods:

- Schwartz-Hearst acronym extraction (``schwartz_hearst``)
- multi-word term extraction (``term_extract``)
- cross-lingual synonym clustering with LaBSE (``synonym_cluster``)
- orchestration + artifact emission (``pipeline``)
"""

__all__ = [
    "schwartz_hearst",
    "term_extract",
    "synonym_cluster",
    "pipeline",
]
