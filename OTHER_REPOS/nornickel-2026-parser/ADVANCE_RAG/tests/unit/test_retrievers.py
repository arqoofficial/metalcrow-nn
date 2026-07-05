"""Retriever unit tests."""

from app.retrieval.models import RetrievalCandidate
from app.retrieval.retrievers import reciprocal_rank_fusion


def _candidate(doc_id: str, score: float) -> RetrievalCandidate:
    return RetrievalCandidate(id=doc_id, document=doc_id, metadata={}, score=score)


def test_rrf_deterministic_order() -> None:
    dense = [_candidate("a", 0.9), _candidate("b", 0.8)]
    sparse = [_candidate("b", 0.7), _candidate("c", 0.6)]
    first = reciprocal_rank_fusion([dense, sparse], limit=3)
    second = reciprocal_rank_fusion([dense, sparse], limit=3)
    assert [item.id for item in first] == [item.id for item in second]


def test_sparse_scorer_prefers_overlap() -> None:
    from app.retrieval.retrievers import SparseRetriever

    sparse = SparseRetriever.__new__(SparseRetriever)
    high = sparse.score("nickel forecast", "nickel production forecast growth")
    low = sparse.score("nickel forecast", "unrelated copper mining")
    assert high > low
