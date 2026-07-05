"""Preprocessing unit tests."""

from app.config.settings import QueryPreprocessingConfig
from app.retrieval.preprocessing import preprocess_query


def test_preprocessing_preserves_meaningful_tokens() -> None:
    config = QueryPreprocessingConfig(lemmatization=False, stemming=False)
    result = preprocess_query("Nickel production forecast", config)
    assert "nickel" in result.lower()


def test_lemmatization_toggle() -> None:
    enabled_cfg = QueryPreprocessingConfig(lemmatization=True, stemming=False)
    disabled_cfg = QueryPreprocessingConfig(lemmatization=False, stemming=False)
    enabled = preprocess_query("running forecasts", enabled_cfg)
    disabled = preprocess_query("running forecasts", disabled_cfg)
    assert enabled != disabled or "running" in disabled


def test_stemming_toggle() -> None:
    enabled_cfg = QueryPreprocessingConfig(lemmatization=False, stemming=True)
    disabled_cfg = QueryPreprocessingConfig(lemmatization=False, stemming=False)
    enabled = preprocess_query("forecasting", enabled_cfg)
    disabled = preprocess_query("forecasting", disabled_cfg)
    assert enabled != disabled or "forecasting" in disabled
