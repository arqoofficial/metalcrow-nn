"""Prometheus metrics and OpenTelemetry tracing helpers."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Iterator

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram

QUERY_REQUESTS = Counter(
    "advance_rag_query_requests_total",
    "Total query requests",
    ["search_type"],
)
INDEX_DOC_REQUESTS = Counter(
    "advance_rag_index_doc_requests_total",
    "Total index_doc requests",
)
INDEX_PATH_JOBS = Counter(
    "advance_rag_index_path_jobs_total",
    "Total index_path jobs enqueued",
)
WORKER_JOBS_TOTAL = Counter(
    "advance_rag_worker_jobs_total",
    "Total queue worker jobs by status",
    ["status"],
)
QUERY_LATENCY = Histogram(
    "advance_rag_query_latency_seconds",
    "Query request latency",
    ["search_type"],
)
WORKER_JOB_DURATION = Histogram(
    "advance_rag_worker_job_duration_seconds",
    "Queue worker job duration in seconds",
    ["status"],
)

_tracer_initialized = False
_metrics_enabled = True


def configure_metrics(enabled: bool) -> None:
    global _metrics_enabled
    _metrics_enabled = enabled


def metrics_enabled() -> bool:
    return _metrics_enabled


def init_tracing(service_name: str = "advance_rag", otlp_endpoint: str | None = None) -> None:
    global _tracer_initialized
    if _tracer_initialized:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer_initialized = True


def get_tracer() -> trace.Tracer:
    init_tracing()
    return trace.get_tracer("advance_rag")


def new_correlation_id() -> str:
    return str(uuid.uuid4())


@contextmanager
def span(name: str, **attributes: str) -> Iterator[None]:
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            current.set_attribute(key, value)
        yield
