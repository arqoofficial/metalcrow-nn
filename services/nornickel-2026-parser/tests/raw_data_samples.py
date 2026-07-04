"""RAW_DATA PDF samples for Docling tests — missing corpus is fatal."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_ROOT = REPO_ROOT / "SHARED" / "RAW_DATA"


def discover_raw_data_pdfs(limit: int = 3) -> list[Path]:
    if not RAW_DATA_ROOT.is_dir():
        raise RuntimeError(
            f"SHARED/RAW_DATA is required for Docling tests but missing at {RAW_DATA_ROOT}"
        )
    pdfs = [path for path in RAW_DATA_ROOT.rglob("*.pdf") if path.is_file()]
    if not pdfs:
        raise RuntimeError(f"No PDF files found under {RAW_DATA_ROOT}")
    pdfs.sort(key=lambda path: path.stat().st_size)
    return pdfs[:limit]


SAMPLE_RAW_PDF = discover_raw_data_pdfs(1)[0]
