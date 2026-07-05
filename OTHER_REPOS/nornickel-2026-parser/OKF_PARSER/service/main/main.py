"""Main API service - singleton. See docs/LAYER_SERVICES.md."""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

import redis
from fastapi import FastAPI, Request
from fastapi.responses import Response

from app.config.loader import load_config, log_worker_counts
from app.observability.logging import setup_logging
from app.observability.middleware import MetricsMiddleware
from app.observability.metrics import metrics_payload
from app.observability.otel import setup_langfuse, setup_otel
from app.presentation.health_router import router as health_router
from app.presentation.openapi_meta import API_DESCRIPTION, OPENAPI_TAGS, TAG_OBSERVABILITY
from app.presentation.router import router
from app.queue.job import QueueStage
from app.queue.redis_queue import MAIN_LEADER_KEY, JobQueue

app = FastAPI(
    title="Nornickel Parser API",
    version="1.0.0",
    description=API_DESCRIPTION,
    openapi_tags=OPENAPI_TAGS,
    swagger_ui_parameters={"docExpansion": "list", "filter": True},
)
app.add_middleware(MetricsMiddleware)
LEADER_TTL_SEC = 30
LEADER_RENEW_SEC = 10


def _leader_value() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def acquire_leader_lock(redis_url: str) -> redis.Redis:
    client = redis.from_url(redis_url)
    if not client.set(MAIN_LEADER_KEY, _leader_value(), nx=True, ex=LEADER_TTL_SEC):
        print("Another main instance holds the leader lock; exiting.", file=sys.stderr)
        sys.exit(1)
    return client


def _config_paths() -> tuple[Path, Path]:
    project_root = Path(__file__).resolve().parents[2]
    config_path = Path(os.environ.get("CONFIG_PATH", project_root / "config.yaml"))
    env_path = Path(os.environ.get("ENV_PATH", project_root / ".env"))
    return config_path, env_path


@app.on_event("startup")
def startup() -> None:
    setup_logging("service/main")
    config_path, env_path = _config_paths()
    config = load_config(config_path, env_path)
    app.state.config = config
    setup_otel("service/main", enabled=config.observability.otel_enabled)
    setup_langfuse(
        enabled=config.observability.langfuse_enabled,
        public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
    )
    log_worker_counts(config)

    redis_url = os.environ["REDIS_URL"]
    client = acquire_leader_lock(redis_url)
    app.state.redis = client
    app.state.stage0_queue = JobQueue.for_stage(
        client,
        QueueStage.raw2docling_raw,
        config.queues.raw2docling_raw,
        retry_attempts=config.runtime.redis_retry_attempts,
        retry_base_delay_seconds=config.runtime.retry_base_delay_seconds,
    )
    app.state.stage1_queue = JobQueue.for_stage(
        client,
        QueueStage.docling_raw2docling_clean00,
        config.queues.docling_raw2docling_clean00,
        retry_attempts=config.runtime.redis_retry_attempts,
        retry_base_delay_seconds=config.runtime.retry_base_delay_seconds,
    )


@app.on_event("shutdown")
def shutdown() -> None:
    client: redis.Redis = app.state.redis
    if client.get(MAIN_LEADER_KEY) == _leader_value().encode():
        client.delete(MAIN_LEADER_KEY)


app.include_router(health_router)
app.include_router(router)


@app.get(
    "/metrics",
    tags=[TAG_OBSERVABILITY],
    summary="Prometheus metrics",
    description="Returns Prometheus text exposition format when `observability.metrics_enabled` is true.",
    responses={404: {"description": "Metrics export disabled in configuration"}},
)
def metrics(request: Request) -> Response:
    config = request.app.state.config
    if not config.observability.metrics_enabled:
        return Response(status_code=404, content="metrics disabled")
    return Response(content=metrics_payload(), media_type="text/plain; version=0.0.4; charset=utf-8")


def renew_leader_loop(client: redis.Redis) -> None:
    while True:
        client.set(MAIN_LEADER_KEY, _leader_value(), ex=LEADER_TTL_SEC)
        time.sleep(LEADER_RENEW_SEC)
