"""Worker test helpers."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.config.models import (
    ApiConfig,
    AppConfig,
    LocksConfig,
    PipelineConfig,
    QueuesConfig,
    RuntimeConfig,
    WorkersConfig,
)
from tests.raw_data_samples import SAMPLE_RAW_PDF


def make_config(shared_root: Path) -> AppConfig:
    return AppConfig(
        shared_root=str(shared_root),
        queues=QueuesConfig(
            raw2docling_raw="parser:jobs:raw2docling_raw",
            docling_raw2docling_clean00="parser:jobs:docling_raw2docling_clean00",
        ),
        api=ApiConfig(host="127.0.0.1", port=8114),
        workers=WorkersConfig(raw2docling_raw=1, docling_raw2docling_clean00=1),
        locks=LocksConfig(upload_suffix=".upload.lock", worker_suffix=".worker.lock"),
        pipeline=PipelineConfig(stages=["docling_raw", "docling_clean00"]),
        runtime=RuntimeConfig(process_timeout_seconds=600),
    )


def seed_raw(
    shared_root: Path,
    relative: str,
    content: bytes | None = None,
    *,
    source_pdf: Path | None = None,
) -> Path:
    target = shared_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if content is not None:
        target.write_bytes(content)
    elif Path(relative).suffix.lower() == ".pdf":
        shutil.copy(source_pdf or SAMPLE_RAW_PDF, target)
    else:
        target.write_bytes(b"raw")
    return target
