"""Query orchestration and response assembly."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from app.api.schemas import QueryRequest, QueryResponse, QueryResultItem, SearchType
from app.config.settings import RuntimeConfig
from app.data.chroma_adapter import ChromaAdapter
from app.data.okf import OkfMeta
from app.data.paths import PathValidationError, resolve_source_subfolder
from app.retrieval.preprocessing import preprocess_query
from app.retrieval.retrievers import (
    AdvanceRetriever,
    DenseRetriever,
    FuzzyRetriever,
    Retriever,
    RrfRetriever,
    SparseRetriever,
)


class QueryService:
    def __init__(self, runtime: RuntimeConfig, chroma: ChromaAdapter, base_dir: Path) -> None:
        self._runtime = runtime
        self._chroma = chroma
        self._base_dir = base_dir
        dense = DenseRetriever(chroma)
        sparse = SparseRetriever(chroma)
        self._retrievers = {
            SearchType.DENSE: dense,
            SearchType.SPARSE: sparse,
            SearchType.FUZZY: FuzzyRetriever(chroma),
            SearchType.RRF: RrfRetriever(dense, sparse),
            SearchType.ADVANCE: AdvanceRetriever(dense, sparse),
        }

    def execute(self, request: QueryRequest) -> QueryResponse | PathValidationError:
        subfolder_result = resolve_source_subfolder(
            self._runtime.query,
            request.source_subfolder,
        )
        if isinstance(subfolder_result, PathValidationError):
            return subfolder_result

        effective_type = request.effective_type(self._runtime.query)
        effective_limit = request.effective_limit(self._runtime.query)
        processed_query = preprocess_query(request.query, self._runtime.query.preprocessing)

        retriever = cast(Retriever, self._retrievers[effective_type])
        candidates = retriever.retrieve(
            processed_query,
            limit=effective_limit,
            source_subfolder=subfolder_result,
        )

        results: list[QueryResultItem] = []
        for candidate in candidates:
            meta = candidate.metadata
            path = meta.get("path", "")
            okf_meta = OkfMeta(
                type=str(meta.get("okf_type", "unknown")),
                title=str(meta.get("okf_title") or "") or None,
            )
            results.append(
                QueryResultItem(
                    document_id=candidate.id,
                    path=path,
                    score=float(candidate.score),
                    content=candidate.document,
                    okf_meta=okf_meta,
                )
            )

        return QueryResponse(
            query=request.query,
            type=effective_type.value,
            source_subfolder=subfolder_result,
            results=results,
        )
