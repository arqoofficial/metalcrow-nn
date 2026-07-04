import time
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, status

from tool_sdk.schemas import (
    HealthResponse,
    InvokeHandler,
    InvokeRequest,
    InvokeResponse,
    InvokeResultMeta,
    ToolManifest,
)


def create_tool_app(
    *,
    name: str,
    version: str,
    manifest: ToolManifest,
    invoke_handler: InvokeHandler,
    title: str | None = None,
) -> FastAPI:
    """Factory for internal tool/parser FastAPI apps (SPEC_V5 §3)."""
    app = FastAPI(title=title or f"{name} tool service", version=version)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(name=name, version=version)

    @app.get("/manifest", response_model=ToolManifest)
    def get_manifest() -> ToolManifest:
        return manifest

    @app.post("/invoke", response_model=InvokeResponse)
    def invoke(request: InvokeRequest) -> InvokeResponse:
        if request.tool != manifest.name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Expected tool '{manifest.name}', got '{request.tool}'",
            )
        started = time.perf_counter()
        try:
            response = invoke_handler(request)
        except Exception as exc:  # noqa: BLE001 — tool boundary returns envelope
            latency_ms = int((time.perf_counter() - started) * 1000)
            return InvokeResponse(
                ok=False,
                tool=manifest.name,
                error=str(exc),
                meta=InvokeResultMeta(latency_ms=latency_ms, degraded=True),
            )
        if response.meta.latency_ms is None:
            response.meta.latency_ms = int((time.perf_counter() - started) * 1000)
        return response

    return app


def passthrough_invoke(
    handler: Callable[[dict[str, Any]], dict[str, Any]],
) -> InvokeHandler:
    """Wrap a simple params->result function into the standard invoke envelope."""

    def _invoke(request: InvokeRequest) -> InvokeResponse:
        return InvokeResponse(
            ok=True,
            tool=request.tool,
            result=handler(request.params),
        )

    return _invoke
