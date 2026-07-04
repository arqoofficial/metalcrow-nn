"""Shared FastAPI skeleton for internal tool/parser microservices."""

from tool_sdk.app import create_tool_app, passthrough_invoke
from tool_sdk.queues import QUEUE_NAMES, build_task_routes
from tool_sdk.schemas import (
    HealthResponse,
    InvokeContext,
    InvokeRequest,
    InvokeResponse,
    InvokeResultMeta,
    ProvenanceRef,
    ToolManifest,
)

__all__ = [
    "HealthResponse",
    "InvokeContext",
    "InvokeRequest",
    "InvokeResponse",
    "InvokeResultMeta",
    "ProvenanceRef",
    "ToolManifest",
    "QUEUE_NAMES",
    "build_task_routes",
    "create_tool_app",
    "passthrough_invoke",
]
