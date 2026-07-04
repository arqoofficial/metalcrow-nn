"""Docling conversion unit tests."""

from pathlib import Path

import pytest

from app.config.models import DoclingConfig
from app.workers.docling import (
    DOC_OCR_USE_GPU_ENV,
    EASYOCR_MODULE_PATH_ENV,
    HF_HOME_ENV,
    resolve_easyocr_gpu_mode,
    MIN_SUBSTANTIVE_ALNUM_CHARS,
    REQUIRE_PRELOADED_MODELS_ENV,
    build_pdf_pipeline_options,
    convert_raw_to_markdown,
    docling_version,
    ensure_offline_model_cache_ready,
    preload_sentinel_path,
    validate_substantive_markdown,
)


def test_docling_version_is_not_stub() -> None:
    assert docling_version() != "stub"


def test_default_ocr_languages_are_english_and_russian() -> None:
    config = DoclingConfig()
    assert config.ocr_enabled is True
    assert config.ocr_languages == ["en", "ru"]


def test_build_pdf_pipeline_options_enables_bilingual_ocr() -> None:
    options = build_pdf_pipeline_options(DoclingConfig())
    assert options.do_ocr is True
    assert options.ocr_options.lang == ["en", "ru"]


def test_build_pdf_pipeline_options_can_disable_ocr() -> None:
    options = build_pdf_pipeline_options(DoclingConfig(ocr_enabled=False))
    assert options.do_ocr is False


def test_validate_substantive_markdown_accepts_realistic_body() -> None:
    body = "word " * (MIN_SUBSTANTIVE_ALNUM_CHARS // 4)
    validate_substantive_markdown(f"# Title\n\n{body}\n", "sample.pdf")


def test_validate_substantive_markdown_rejects_stub_signatures() -> None:
    stub = "# Parsed PDF\n\nConverted from `sample.pdf` without OCR.\n"
    with pytest.raises(ValueError, match="stub"):
        validate_substantive_markdown(stub, "sample.pdf")


def test_validate_substantive_markdown_rejects_short_output() -> None:
    with pytest.raises(ValueError, match="substantive body|insufficient text"):
        validate_substantive_markdown("# Title\n\nshort.\n", "sample.pdf")


def test_validate_substantive_markdown_rejects_title_only() -> None:
    title = "# " + ("LongTitle " * 30) + "\n"
    with pytest.raises(ValueError, match="no substantive body"):
        validate_substantive_markdown(title, "sample.pdf")


def test_unsupported_extension_raises(tmp_path: Path) -> None:
    raw = tmp_path / "archive.zip"
    raw.write_bytes(b"PK")
    with pytest.raises(ValueError, match="unsupported format"):
        convert_raw_to_markdown(raw)


def test_invalid_pdf_bytes_raises(tmp_path: Path) -> None:
    raw = tmp_path / "broken.pdf"
    raw.write_bytes(b"not a pdf")
    with pytest.raises(Exception):
        convert_raw_to_markdown(raw, docling_config=DoclingConfig(ocr_enabled=False))


def test_offline_cache_guard_rejects_missing_preload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ensure_offline_model_cache_ready.cache_clear()
    monkeypatch.setenv(REQUIRE_PRELOADED_MODELS_ENV, "true")
    monkeypatch.setenv("MODEL_CACHE_ROOT", str(tmp_path))
    monkeypatch.setenv(EASYOCR_MODULE_PATH_ENV, str(tmp_path / "easyocr"))
    monkeypatch.setenv(HF_HOME_ENV, str(tmp_path / "hf"))
    with pytest.raises(RuntimeError, match="Preloaded model cache is required"):
        ensure_offline_model_cache_ready()


def test_offline_cache_guard_accepts_preloaded_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ensure_offline_model_cache_ready.cache_clear()
    monkeypatch.setenv(REQUIRE_PRELOADED_MODELS_ENV, "true")
    monkeypatch.setenv("MODEL_CACHE_ROOT", str(tmp_path))
    monkeypatch.setenv(EASYOCR_MODULE_PATH_ENV, str(tmp_path / "easyocr"))
    monkeypatch.setenv(HF_HOME_ENV, str(tmp_path / "hf"))

    (tmp_path / "easyocr" / "model").mkdir(parents=True, exist_ok=True)
    (tmp_path / "easyocr" / "model" / "detector.pth").write_bytes(b"x")
    (tmp_path / "hf").mkdir(parents=True, exist_ok=True)
    (tmp_path / "hf" / "cache.bin").write_bytes(b"x")
    preload_sentinel_path().write_text("ok\n", encoding="utf-8")

    ensure_offline_model_cache_ready()


def test_gpu_mode_false_forces_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve_easyocr_gpu_mode.cache_clear()
    monkeypatch.setenv(DOC_OCR_USE_GPU_ENV, "false")
    assert resolve_easyocr_gpu_mode() is False


def test_gpu_mode_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve_easyocr_gpu_mode.cache_clear()
    monkeypatch.setenv(DOC_OCR_USE_GPU_ENV, "banana")
    with pytest.raises(RuntimeError, match="Invalid DOC_OCR_USE_GPU"):
        resolve_easyocr_gpu_mode()
