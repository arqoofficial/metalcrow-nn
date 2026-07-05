"""Query schema unit tests."""

import pytest
from pydantic import BaseModel, ValidationError

from app.api.schemas import (
    IndexDocRequest,
    IndexDocResponse,
    IndexPathRequest,
    IndexPathResponse,
    QueryRequest,
    QueryResponse,
    SearchType,
)
from app.config.settings import QueryConfig


def test_type_default_is_dense() -> None:
    req = QueryRequest(query="test")
    assert req.effective_type(QueryConfig()) == SearchType.DENSE


def test_limit_default_is_10() -> None:
    req = QueryRequest(query="test")
    assert req.effective_limit(QueryConfig()) == 10


def test_default_source_subfolder_from_config() -> None:
    req = QueryRequest(query="test")
    assert req.effective_source_subfolder(QueryConfig()) == "01_docling_clean00"


def test_invalid_type_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryRequest(query="test", type="invalid")  # type: ignore[arg-type]


def test_empty_results_response_schema() -> None:
    resp = QueryResponse(
        query="none",
        type="dense",
        source_subfolder="01_docling_clean00",
        results=[],
    )
    assert resp.results == []


def test_contract_models_are_basemodel() -> None:
    assert issubclass(QueryRequest, BaseModel)
    assert issubclass(QueryResponse, BaseModel)
    assert issubclass(IndexDocRequest, BaseModel)
    assert issubclass(IndexDocResponse, BaseModel)
    assert issubclass(IndexPathRequest, BaseModel)
    assert issubclass(IndexPathResponse, BaseModel)


def test_index_doc_request_accepts_path_only() -> None:
    req = IndexDocRequest(path="01_docling_clean00/reports/q1.okf.md")
    assert req.path.endswith("q1.okf.md")


def test_index_path_request_accepts_path_only() -> None:
    req = IndexPathRequest(path="01_docling_clean00/reports")
    assert req.path == "01_docling_clean00/reports"
