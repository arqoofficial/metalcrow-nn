"""Retrieval mode implementations."""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.data.chroma_adapter import ChromaAdapter
from app.retrieval.models import RetrievalCandidate


@lru_cache(maxsize=1)
def _reranker_stopwords() -> set[str]:
    path = os.getenv("ADVANCE_RAG_RERANKER_STOPWORDS")
    if not path:
        return set()
    file_path = Path(path)
    if not file_path.is_file():
        return set()
    lines = file_path.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip()}


class Retriever(Protocol):
    def retrieve(
        self, query: str, limit: int, source_subfolder: str
    ) -> list[RetrievalCandidate]: ...


class DenseRetriever:
    def __init__(self, chroma: ChromaAdapter) -> None:
        self._chroma = chroma

    def retrieve(self, query: str, limit: int, source_subfolder: str) -> list[RetrievalCandidate]:
        results = self._chroma.query_dense(query, limit=limit)
        filtered = [
            r
            for r in results
            if (r.get("metadata") or {}).get("source_subfolder") == source_subfolder
        ]
        return [
            RetrievalCandidate(
                id=row["id"],
                document=row.get("document") or "",
                metadata=row.get("metadata") or {},
                score=float(row.get("score", 0.0)),
            )
            for row in filtered[:limit]
        ]


class SparseRetriever:
    def __init__(self, chroma: ChromaAdapter) -> None:
        self._chroma = chroma

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens = re.findall(r"[\w\u0400-\u04FF]+", text.lower())
        blocked = _reranker_stopwords()
        if not blocked:
            return tokens
        return [token for token in tokens if token not in blocked]

    def score(self, query: str, document: str) -> float:
        query_tokens = self._tokenize(query)
        doc_tokens = self._tokenize(document)
        if not query_tokens or not doc_tokens:
            return 0.0
        query_counts = Counter(query_tokens)
        doc_counts = Counter(doc_tokens)
        dot = sum(query_counts[t] * doc_counts.get(t, 0) for t in query_counts)
        query_norm = math.sqrt(sum(v * v for v in query_counts.values()))
        doc_norm = math.sqrt(sum(v * v for v in doc_counts.values()))
        if query_norm == 0 or doc_norm == 0:
            return 0.0
        return dot / (query_norm * doc_norm)

    def retrieve(self, query: str, limit: int, source_subfolder: str) -> list[RetrievalCandidate]:
        docs = self._chroma.get_all_documents()
        scored: list[tuple[float, RetrievalCandidate]] = []
        for item in docs:
            meta = item.get("metadata") or {}
            if meta.get("source_subfolder") != source_subfolder:
                continue
            document = item.get("document") or ""
            score = self.score(query, document)
            if score <= 0:
                continue
            candidate = RetrievalCandidate(
                id=item["id"],
                document=document,
                metadata=meta,
                score=score,
            )
            scored.append((score, candidate))
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [c for _, c in scored[:limit]]


class FuzzyRetriever:
    def __init__(self, chroma: ChromaAdapter) -> None:
        self._chroma = chroma

    def retrieve(self, query: str, limit: int, source_subfolder: str) -> list[RetrievalCandidate]:
        from fuzzysearch import find_near_matches

        docs = self._chroma.get_all_documents()
        scored: list[tuple[float, RetrievalCandidate]] = []
        max_l_dist = max(1, len(query) // 4)
        for item in docs:
            meta = item.get("metadata") or {}
            if meta.get("source_subfolder") != source_subfolder:
                continue
            document = (item.get("document") or "").lower()
            query_lower = query.lower()
            matches = find_near_matches(query_lower, document, max_l_dist=max_l_dist)
            if not matches:
                continue
            best = min(matches, key=lambda m: m.dist)
            score = 1.0 - (best.dist / max(len(query_lower), 1))
            candidate = RetrievalCandidate(
                id=item["id"],
                document=item.get("document") or "",
                metadata=meta,
                score=score,
            )
            scored.append((score, candidate))
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [c for _, c in scored[:limit]]


def reciprocal_rank_fusion(
    ranked_lists: list[list[RetrievalCandidate]],
    k: int = 60,
    limit: int = 10,
) -> list[RetrievalCandidate]:
    scores: dict[str, float] = {}
    items: dict[str, RetrievalCandidate] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            doc_id = item.id
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            items[doc_id] = item
    ordered = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    result: list[RetrievalCandidate] = []
    for doc_id, score in ordered[:limit]:
        candidate = items[doc_id].model_copy(update={"score": score})
        result.append(candidate)
    return result


class RrfRetriever:
    def __init__(self, dense: DenseRetriever, sparse: SparseRetriever) -> None:
        self._dense = dense
        self._sparse = sparse

    def retrieve(self, query: str, limit: int, source_subfolder: str) -> list[RetrievalCandidate]:
        dense = self._dense.retrieve(query, limit=limit * 2, source_subfolder=source_subfolder)
        sparse = self._sparse.retrieve(query, limit=limit * 2, source_subfolder=source_subfolder)
        return reciprocal_rank_fusion([dense, sparse], limit=limit)


class Reranker:
    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        limit: int,
    ) -> list[RetrievalCandidate]:
        sparse = SparseRetriever.__new__(SparseRetriever)
        scored = []
        for item in candidates:
            score = SparseRetriever.score(sparse, query, item.document)
            combined = 0.6 * float(item.score) + 0.4 * score
            candidate = item.model_copy(update={"score": combined})
            scored.append((combined, candidate))
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [c for _, c in scored[:limit]]


class AdvanceRetriever:
    def __init__(
        self,
        dense: DenseRetriever,
        sparse: SparseRetriever,
        reranker: Reranker | None = None,
    ) -> None:
        self._dense = dense
        self._sparse = sparse
        self._reranker = reranker or Reranker()

    def retrieve(self, query: str, limit: int, source_subfolder: str) -> list[RetrievalCandidate]:
        dense = self._dense.retrieve(query, limit=limit * 2, source_subfolder=source_subfolder)
        sparse = self._sparse.retrieve(query, limit=limit * 2, source_subfolder=source_subfolder)
        merged: dict[str, RetrievalCandidate] = {}
        for item in dense + sparse:
            merged[item.id] = item
        candidates = list(merged.values())
        return self._reranker.rerank(query, candidates, limit)
