"""Preload Docling and OCR model artifacts into a local cache."""

from __future__ import annotations

from argparse import ArgumentParser
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import easyocr

from app.config.models import DoclingConfig
from app.workers.docling import (
    REQUIRE_PRELOADED_MODELS_ENV,
    configure_model_cache_env,
    ensure_offline_model_cache_ready,
    model_cache_root,
    preload_sentinel_path,
    require_preloaded_models,
    _document_converter,
)

_MINIMAL_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT
/F1 24 Tf
72 100 Td
(Model preload) Tj
ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f 
0000000010 00000 n 
0000000065 00000 n 
0000000122 00000 n 
0000000248 00000 n 
0000000342 00000 n 
trailer
<< /Root 1 0 R /Size 6 >>
startxref
412
%%EOF
"""


def _warm_easyocr(config: DoclingConfig) -> None:
    # Instantiating Reader downloads/checks detection+recognition weights.
    easyocr.Reader(
        config.ocr_languages,
        gpu=False,
        model_storage_directory=None,
        download_enabled=True,
    )


def _warm_docling(config: DoclingConfig) -> None:
    converter = _document_converter(config.ocr_enabled, tuple(config.ocr_languages))
    with TemporaryDirectory(prefix="preload_docling_") as tmp:
        sample = Path(tmp) / "sample.pdf"
        sample.write_bytes(_MINIMAL_PDF)
        try:
            converter.convert(str(sample))
        except Exception:
            # We only need model/materialization warm-up; parse quality is irrelevant here.
            pass


def preload(config: DoclingConfig) -> Path:
    configure_model_cache_env()
    # Preload run is allowed to fetch; startup guard is for normal workers.
    os.environ[REQUIRE_PRELOADED_MODELS_ENV] = "0"
    ensure_offline_model_cache_ready.cache_clear()
    root = model_cache_root()
    root.mkdir(parents=True, exist_ok=True)
    _warm_easyocr(config)
    _warm_docling(config)
    sentinel = preload_sentinel_path()
    sentinel.write_text("ok\n", encoding="utf-8")
    return sentinel


def main() -> None:
    parser = ArgumentParser(description="Preload Docling/EasyOCR model caches for offline workers.")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate cache availability only (no downloads).",
    )
    parser.add_argument(
        "--ocr-languages",
        nargs="+",
        default=["en", "ru"],
        help="OCR language codes passed to EasyOCR/Docling preload.",
    )
    args = parser.parse_args()

    config = DoclingConfig(ocr_enabled=True, ocr_languages=list(args.ocr_languages))
    configure_model_cache_env()
    if args.check_only:
        # Always enforce guard during explicit check, regardless of environment default.
        if not require_preloaded_models():
            os.environ[REQUIRE_PRELOADED_MODELS_ENV] = "1"
        ensure_offline_model_cache_ready.cache_clear()
        ensure_offline_model_cache_ready()
        print(f"Model cache is ready at {model_cache_root()}")
        return

    sentinel = preload(config)
    print(f"Preload complete. Sentinel: {sentinel}")


if __name__ == "__main__":
    main()
