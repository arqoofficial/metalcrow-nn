"""OpenTelemetry bootstrap (non-blocking)."""

from __future__ import annotations

import os

from loguru import logger


def setup_otel(service_name: str, *, enabled: bool) -> None:
    if not enabled:
        logger.debug("OpenTelemetry disabled for {}", service_name)
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter(endpoint=endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("OpenTelemetry OTLP exporter configured for {}", service_name)
        else:
            logger.info(
                "OpenTelemetry tracer provider initialized without exporter for {}",
                service_name,
            )

        trace.set_tracer_provider(provider)
    except Exception as exc:
        logger.warning("OpenTelemetry setup skipped for {}: {}", service_name, exc)


def setup_langfuse(*, enabled: bool, public_key: str | None = None) -> None:
    if not enabled:
        logger.debug("Langfuse integration disabled")
        return

    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        logger.warning("Langfuse enabled but LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY is missing")
        return

    try:
        from langfuse import Langfuse

        client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
        if not client.auth_check():
            logger.warning("Langfuse auth check failed")
            return
        logger.info("Langfuse client initialized host={}", host)
    except ImportError:
        logger.warning("Langfuse enabled but langfuse package is not installed")
    except Exception as exc:
        logger.warning("Langfuse setup skipped: {}", exc)
