"""Docling conversion with English and Russian OCR support."""

from __future__ import annotations

import importlib.metadata
import logging
import os
import re
from functools import lru_cache
from pathlib import Path

from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from app.config.models import DoclingConfig
from app.paths import is_docling_input_path

_DEFAULT_DOCLING_CONFIG = DoclingConfig()
MIN_SUBSTANTIVE_ALNUM_CHARS = 200
MODEL_CACHE_ROOT_ENV = "MODEL_CACHE_ROOT"
REQUIRE_PRELOADED_MODELS_ENV = "REQUIRE_PRELOADED_MODELS"
EASYOCR_MODULE_PATH_ENV = "EASYOCR_MODULE_PATH"
HF_HOME_ENV = "HF_HOME"
TRANSFORMERS_CACHE_ENV = "TRANSFORMERS_CACHE"
TORCH_HOME_ENV = "TORCH_HOME"
XDG_CACHE_HOME_ENV = "XDG_CACHE_HOME"
DEFAULT_MODEL_CACHE_ROOT = "/models"
PRELOAD_SENTINEL = ".preload_done"
DOC_OCR_USE_GPU_ENV = "DOC_OCR_USE_GPU"

_STUB_SIGNATURES = (
    "Converted from",
    "without OCR",
    "# Parsed PDF",
    "# Parsed Document",
    "Test conversion body",
)

_SUCCESS_STATUSES = frozenset({ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS})
_GPU_MODE_LOGGER = logging.getLogger("app.workers.docling.gpu")


def model_cache_root() -> Path:
    configured = os.environ.get(MODEL_CACHE_ROOT_ENV, DEFAULT_MODEL_CACHE_ROOT).strip()
    return Path(configured or DEFAULT_MODEL_CACHE_ROOT)


def configure_model_cache_env() -> None:
    cache_root = model_cache_root()
    os.environ.setdefault(MODEL_CACHE_ROOT_ENV, str(cache_root))
    os.environ.setdefault(EASYOCR_MODULE_PATH_ENV, str(cache_root / "easyocr"))
    os.environ.setdefault(HF_HOME_ENV, str(cache_root / "huggingface"))
    os.environ.setdefault(TRANSFORMERS_CACHE_ENV, str(cache_root / "huggingface" / "transformers"))
    os.environ.setdefault(TORCH_HOME_ENV, str(cache_root / "torch"))
    os.environ.setdefault(XDG_CACHE_HOME_ENV, str(cache_root / "xdg"))


def preload_sentinel_path() -> Path:
    return model_cache_root() / PRELOAD_SENTINEL


def require_preloaded_models() -> bool:
    value = os.environ.get(REQUIRE_PRELOADED_MODELS_ENV, "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _cache_issues() -> list[str]:
    issues: list[str] = []
    sentinel = preload_sentinel_path()
    if not sentinel.is_file():
        issues.append(f"missing preload sentinel: {sentinel}")

    easyocr_model_dir = Path(os.environ[EASYOCR_MODULE_PATH_ENV]) / "model"
    if not easyocr_model_dir.is_dir():
        issues.append(f"missing EasyOCR model directory: {easyocr_model_dir}")
    elif not any(path.suffix == ".pth" for path in easyocr_model_dir.iterdir()):
        issues.append(f"EasyOCR model directory has no .pth files: {easyocr_model_dir}")

    hf_home = Path(os.environ[HF_HOME_ENV])
    if not hf_home.is_dir():
        issues.append(f"missing HuggingFace cache directory: {hf_home}")
    elif not any(hf_home.iterdir()):
        issues.append(f"HuggingFace cache directory is empty: {hf_home}")
    return issues


@lru_cache(maxsize=1)
def ensure_offline_model_cache_ready() -> None:
    configure_model_cache_env()
    if not require_preloaded_models():
        return

    issues = _cache_issues()
    if issues:
        detail = "; ".join(issues)
        raise RuntimeError(
            "Preloaded model cache is required but missing. "
            "Run 'python scripts/preload_models.py' (or './rerun.sh preload-models') "
            f"before starting workers. Details: {detail}"
        )


def docling_version() -> str:
    try:
        version = importlib.metadata.version("docling")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(
            "docling is required for this project but is not installed"
        ) from exc
    if version == "stub":
        raise RuntimeError("docling reports stub version; real docling package is required")
    return version


def _detect_cuda_available() -> bool:
    try:
        import torch
    except Exception:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:
        return False


@lru_cache(maxsize=1)
def resolve_easyocr_gpu_mode() -> bool:
    mode = os.environ.get(DOC_OCR_USE_GPU_ENV, "auto").strip().lower()
    if mode in {"auto", ""}:
        use_gpu = _detect_cuda_available()
        _GPU_MODE_LOGGER.info("DOC_OCR_USE_GPU=auto -> use_gpu=%s", use_gpu)
        return use_gpu
    if mode in {"1", "true", "yes", "on"}:
        if not _detect_cuda_available():
            raise RuntimeError(
                "DOC_OCR_USE_GPU=true but CUDA is not available inside container. "
                "Attach GPU to worker container or set DOC_OCR_USE_GPU=auto/false."
            )
        _GPU_MODE_LOGGER.info("DOC_OCR_USE_GPU=true -> use_gpu=True")
        return True
    if mode in {"0", "false", "no", "off"}:
        _GPU_MODE_LOGGER.info("DOC_OCR_USE_GPU=false -> use_gpu=False")
        return False
    raise RuntimeError(
        f"Invalid DOC_OCR_USE_GPU value: {mode!r}. Expected auto|true|false."
    )


def build_pdf_pipeline_options(config: DoclingConfig) -> PdfPipelineOptions:
    if config.ocr_enabled:
        return PdfPipelineOptions(
            do_ocr=True,
            ocr_options=EasyOcrOptions(
                lang=list(config.ocr_languages),
                use_gpu=resolve_easyocr_gpu_mode(),
            ),
        )
    return PdfPipelineOptions(do_ocr=False)


def validate_substantive_markdown(markdown: str, source_name: str) -> None:
    """Reject stub/placeholder output and require at least one page of text."""
    normalized = markdown.strip()
    if not normalized:
        raise ValueError(f"docling produced empty markdown for {source_name}")

    lowered = normalized.lower()
    for signature in _STUB_SIGNATURES:
        if signature.lower() in lowered:
            raise ValueError(
                f"docling output for {source_name} looks like a stub (matched {signature!r})"
            )

    body_without_heading = re.sub(r"^#+\s+[^\n]+\n?", "", normalized, count=1).strip()
    body_alnum = sum(character.isalnum() for character in body_without_heading)
    if body_alnum < MIN_SUBSTANTIVE_ALNUM_CHARS // 2:
        raise ValueError(
            f"docling output for {source_name} has no substantive body beyond a title"
        )

    alnum_count = sum(character.isalnum() for character in normalized)
    if alnum_count < MIN_SUBSTANTIVE_ALNUM_CHARS:
        raise ValueError(
            f"docling output for {source_name} has insufficient text "
            f"({alnum_count} alnum chars, need {MIN_SUBSTANTIVE_ALNUM_CHARS})"
        )


@lru_cache(maxsize=4)
def _document_converter(ocr_enabled: bool, ocr_languages: tuple[str, ...]) -> DocumentConverter:
    ensure_offline_model_cache_ready()
    config = DoclingConfig(ocr_enabled=ocr_enabled, ocr_languages=list(ocr_languages))
    pdf_options = build_pdf_pipeline_options(config)
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
        }
    )


def convert_raw_to_markdown(
    raw_path: Path,
    *,
    docling_config: DoclingConfig | None = None,
) -> str:
    if not is_docling_input_path(raw_path.as_posix()):
        raise ValueError(
            f"unsupported format for docling conversion: {raw_path.suffix.lower() or '(no extension)'} "
            f"({raw_path.name})"
        )

    settings = docling_config or _DEFAULT_DOCLING_CONFIG
    converter = _document_converter(
        settings.ocr_enabled,
        tuple(settings.ocr_languages),
    )
    result = converter.convert(str(raw_path))
    if result.status not in _SUCCESS_STATUSES:
        errors = "; ".join(item.error_message for item in result.errors if item.error_message)
        detail = errors or result.status.value
        raise ValueError(f"docling conversion failed for {raw_path.name}: {detail}")

    markdown = result.document.export_to_markdown().strip()
    validate_substantive_markdown(markdown, raw_path.name)
    return markdown + "\n"
