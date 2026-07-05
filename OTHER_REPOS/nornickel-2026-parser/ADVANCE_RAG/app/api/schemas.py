"""Query request and response schemas."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.config.settings import QueryConfig
from app.data.chroma_adapter import DenseEmbeddingInfo
from app.data.okf import OkfMeta


class SearchType(str, Enum):
    ADVANCE = "advance"
    FUZZY = "fuzzy"
    DENSE = "dense"
    SPARSE = "sparse"
    RRF = "RRF"


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    type: SearchType | None = None
    source_subfolder: str | None = None
    limit: int | None = Field(default=None, ge=1, le=100)

    def effective_type(self, query_config: QueryConfig) -> SearchType:
        if self.type is not None:
            return self.type
        return SearchType(query_config.default_type)

    def effective_limit(self, query_config: QueryConfig) -> int:
        return self.limit if self.limit is not None else query_config.default_limit

    def effective_source_subfolder(self, query_config: QueryConfig) -> str:
        return self.source_subfolder or query_config.default_source_subfolder


class QueryResultItem(BaseModel):
    document_id: str
    path: str
    score: float
    content: str
    okf_meta: OkfMeta


class QueryResponse(BaseModel):
    query: str
    type: str
    source_subfolder: str
    results: list[QueryResultItem]


class IndexDocRequest(BaseModel):
    path: str


class IndexDocResponse(BaseModel):
    status: str
    path: str


class IndexPathRequest(BaseModel):
    path: str = Field(..., min_length=1)


class IndexPathResponse(BaseModel):
    status: str
    job_id: str
    path: str


class QueueRuntimeInfo(BaseModel):
    backend: str
    size: int
    failed_count: int


class ChromaIndexInfo(BaseModel):
    ready: bool
    collection_name: str
    document_count: int


class AdminRuntimeResponse(BaseModel):
    queue: QueueRuntimeInfo
    chroma: ChromaIndexInfo
    dense_embedding: DenseEmbeddingInfo
