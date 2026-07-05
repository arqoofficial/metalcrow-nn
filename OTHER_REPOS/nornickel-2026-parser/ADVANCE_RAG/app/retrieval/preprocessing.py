"""NLTK query preprocessing pipeline."""

from __future__ import annotations

import os
import re
from pathlib import Path

from app.config.settings import QueryPreprocessingConfig

_nltk_ready = False


def _ensure_nltk() -> None:
    global _nltk_ready
    if _nltk_ready:
        return
    import nltk

    nltk_data = os.getenv("ADVANCE_RAG_NLTK_DATA") or os.getenv("NLTK_DATA")
    if nltk_data:
        data_dir = str(Path(nltk_data).resolve())
        if data_dir not in nltk.data.path:
            nltk.data.path.insert(0, data_dir)
    else:
        data_dir = None

    resources = [
        ("tokenizers/punkt", "punkt"),
        ("tokenizers/punkt_tab", "punkt_tab"),
        ("corpora/wordnet", "wordnet"),
        ("corpora/omw-1.4", "omw-1.4"),
        ("corpora/stopwords", "stopwords"),
    ]
    for lookup, resource in resources:
        try:
            nltk.data.find(lookup)
        except LookupError:
            nltk.download(resource, quiet=True, download_dir=data_dir)
    _nltk_ready = True


def _detect_language(text: str, languages: list[str]) -> str:
    if "ru" in languages and re.search(r"[\u0400-\u04FF]", text):
        return "ru"
    return "en"


def preprocess_query(text: str, config: QueryPreprocessingConfig) -> str:
    _ensure_nltk()
    from nltk.stem import SnowballStemmer
    from nltk.tokenize import word_tokenize

    lang = _detect_language(text, config.languages)
    tokens = word_tokenize(text.lower())
    processed: list[str] = []
    stemmer = SnowballStemmer("russian" if lang == "ru" else "english") if config.stemming else None
    for token in tokens:
        word = token
        if config.lemmatization and lang == "en":
            from nltk.stem import WordNetLemmatizer

            word = WordNetLemmatizer().lemmatize(word)
        if stemmer is not None:
            word = stemmer.stem(word)
        if re.match(r"[\w\u0400-\u04FF]+", word):
            processed.append(word)
    return " ".join(processed) if processed else text.strip()
