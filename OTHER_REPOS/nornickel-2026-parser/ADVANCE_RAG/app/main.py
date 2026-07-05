"""Application state and FastAPI factory."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from loguru import logger
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.api.admin import router as admin_router
from app.api.health import router as health_router
from app.api.v1 import router as v1_router
from app.config.settings import RuntimeConfig, SecretsSettings, get_settings
from app.data.chroma_adapter import ChromaAdapter, create_chroma_adapter
from app.indexing.service import IndexingService
from app.observability.logging import create_logger, log_config_summary
from app.observability.metrics import configure_metrics, init_tracing
from app.queue.jobs import JobQueue, QueueBackend, Worker, make_index_path_handler
from app.queue.redis_queue import RedisJobQueue
from app.retrieval.preprocessing import preprocess_query


class AppState:
    def __init__(
        self,
        runtime: RuntimeConfig,
        secrets: SecretsSettings,
        chroma: ChromaAdapter | None = None,
        chroma_ready: bool = False,
        queue: QueueBackend | None = None,
        worker: Worker | None = None,
    ) -> None:
        self.runtime = runtime
        self.secrets = secrets
        self.chroma = chroma
        self.chroma_ready = chroma_ready
        self.queue = queue
        self.worker = worker


def _runtime_summary(runtime: RuntimeConfig, base_dir: Path) -> dict[str, Any]:
    return {
        "api_version": runtime.api.version,
        "api_port": runtime.api.port,
        "shared_root": str(runtime.shared.resolve_root(base_dir)),
        "default_query_type": runtime.query.default_type,
        "default_query_limit": runtime.query.default_limit,
        "default_source_subfolder": runtime.query.default_source_subfolder,
        "chroma_mode": runtime.chroma.mode.value,
        "chroma_collection": runtime.chroma.collection_name,
    }


def create_app(
    config_path: Path | None = None,
    base_dir: Path | None = None,
    start_worker: bool = True,
) -> FastAPI:
    base = base_dir or Path(__file__).resolve().parents[1]
    assets = base / "assets"
    os.environ.setdefault(
        "ADVANCE_RAG_ONNX_MODEL_DIR",
        str((assets / "models/chroma/onnx_models/all-MiniLM-L6-v2").resolve()),
    )
    os.environ.setdefault("ADVANCE_RAG_NLTK_DATA", str((assets / "nltk_data").resolve()))
    os.environ.setdefault("NLTK_DATA", os.environ["ADVANCE_RAG_NLTK_DATA"])
    os.environ.setdefault(
        "ADVANCE_RAG_RERANKER_STOPWORDS",
        str((assets / "reranker/stopwords.txt").resolve()),
    )
    cfg_path = config_path or base / "config.yaml"
    runtime, secrets = get_settings(str(cfg_path), str(base))

    create_logger(runtime.observability)
    log_config_summary(_runtime_summary(runtime, base))
    configure_metrics(runtime.observability.metrics_enabled)

    if runtime.observability.tracing_enabled:
        init_tracing(otlp_endpoint=secrets.otel_exporter_otlp_endpoint)

    chroma: ChromaAdapter | None = None
    chroma_ready = False
    try:
        chroma = create_chroma_adapter(runtime, base, secrets=secrets)
        chroma_ready = chroma.is_ready
    except Exception:
        logger.exception("chroma_initialization_failed")
        chroma_ready = False

    queue: QueueBackend
    if runtime.queue.backend == "redis":
        if not secrets.redis_url:
            raise ValueError("Queue backend 'redis' requires REDIS_URL in .env")
        queue = RedisJobQueue(secrets.redis_url)
    else:
        queue = JobQueue()
    worker: Worker | None = None
    if start_worker and chroma is not None and chroma_ready:
        indexing = IndexingService(runtime, chroma, base)
        worker = Worker(
            queue,
            make_index_path_handler(indexing),
            poll_interval_sec=runtime.queue.poll_interval_sec,
        )
        worker.start()

    app = FastAPI(title="ADVANCE_RAG", version=runtime.api.version)
    if runtime.observability.tracing_enabled:
        FastAPIInstrumentor.instrument_app(app)
    app.state.base_dir = base
    app.state.app_state = AppState(
        runtime=runtime,
        secrets=secrets,
        chroma=chroma,
        chroma_ready=chroma_ready,
        queue=queue,
        worker=worker,
    )
    _warm_runtime(app.state.app_state)
    app.include_router(health_router)
    app.include_router(admin_router)
    app.include_router(v1_router)
    return app


def create_api_app() -> FastAPI:
    return create_app(start_worker=False)


def _warm_runtime(state: AppState) -> None:
    try:
        preprocess_query("warmup", state.runtime.query.preprocessing)
    except Exception as exc:  # pragma: no cover - startup guard
        logger.warning("query_preprocessing_warmup_failed error={}", str(exc))

    if state.chroma_ready and state.chroma is not None:
        try:
            state.chroma.query_dense("warmup", limit=1)
        except Exception as exc:  # pragma: no cover - startup guard
            logger.warning("dense_query_warmup_failed error={}", str(exc))
