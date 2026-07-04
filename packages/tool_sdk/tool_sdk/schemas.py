from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    name: str
    version: str


class ToolManifest(BaseModel):
    name: str
    description: str
    version: str = "0.1.0"
    priority: str = "P0"
    queue: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    degraded_behavior: str | None = None
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    deps: list[str] = Field(default_factory=list)
    mcp: bool = True


class InvokeContext(BaseModel):
    request_id: str | None = None
    user_role: str | None = None
    locale: str = "ru"


class InvokeRequest(BaseModel):
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    context: InvokeContext = Field(default_factory=InvokeContext)


class ProvenanceRef(BaseModel):
    document_id: str | None = None
    page: int | None = None
    paragraph: str | None = None


class InvokeResultMeta(BaseModel):
    latency_ms: int | None = None
    degraded: bool = False


class InvokeResponse(BaseModel):
    ok: bool
    tool: str
    result: dict[str, Any] = Field(default_factory=dict)
    provenance: list[ProvenanceRef] = Field(default_factory=list)
    meta: InvokeResultMeta = Field(default_factory=InvokeResultMeta)
    error: str | None = None


InvokeHandler = Callable[[InvokeRequest], InvokeResponse]
