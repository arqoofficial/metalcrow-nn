"""Защита от регресса состава API (SPEC_V3 plan, Фаза 6): доменные эндпоинты §8
плюс auth-эндпоинты шаблона должны присутствовать в одном openapi.json."""

from app.main import app

# SPEC_V3 §8 — доменные эндпоинты (search и analytics/gaps удалены из публичного API)
_SPEC_V3_PATHS = {
    "/api/v1/chat/sessions",
    "/api/v1/chat/sessions/{session_id}",
    "/api/v1/chat/sessions/{session_id}/messages",
    "/api/v1/graph/query",
    "/api/v1/graph/subgraph/{entity_id}",
    "/api/v1/graph/path",
    "/api/v1/wiki/tree",
    "/api/v1/wiki/search",
    "/api/v1/wiki/documents/content",
    "/api/v1/wiki/documents/download/markdown",
    "/api/v1/wiki/documents/download/raw",
    "/api/v1/analytics/coverage",
    "/api/v1/metrics",
    "/api/v1/ingest/upload",
    "/api/v1/ingest/run",
    "/api/v1/ingest/files",
    "/api/v1/ingest/files/{document_id}",
    "/api/v1/ingest/reindex",
    "/api/v1/ingest/status/{task_id}",
    "/api/v1/admin/coverage",
    "/api/v1/sources/{doc_id}/content",
}

_AUTH_PATHS = {
    "/api/v1/login/access-token",
    "/api/v1/login/test-token",
    "/api/v1/password-recovery/{email}",
    "/api/v1/reset-password/",
}

_RESPONSE_SCHEMAS = {
    "/api/v1/chat/sessions": "ChatSessionsPublic",
    "/api/v1/graph/query": "SubgraphResponse",
    "/api/v1/wiki/search": "WikiSearchResponse",
    "/api/v1/wiki/tree": "WikiTreeResponse",
    "/api/v1/wiki/documents/content": "WikiDocumentContent",
    "/api/v1/analytics/coverage": "CoverageResponse",
    "/api/v1/metrics": "MetricsResponse",
    "/api/v1/ingest/upload": "IngestUploadBatchResponse",
}


def test_openapi_contains_all_spec_v3_paths() -> None:
    schema = app.openapi()
    paths = set(schema["paths"].keys())

    missing_spec_v3 = _SPEC_V3_PATHS - paths
    assert not missing_spec_v3, f"Missing SPEC_V3 §8 paths: {missing_spec_v3}"

    missing_auth = _AUTH_PATHS - paths
    assert not missing_auth, f"Missing template auth paths: {missing_auth}"

    # Демо-сущность шаблона удалена (план, Фаза 2, todo `remove-item`)
    assert not any("item" in p.lower() for p in paths)


def test_openapi_response_models_are_typed() -> None:
    """response_model= на каждом роутере должен генерировать честную схему, а не
    произвольный dict/Any (SPEC_V3 plan, Фаза 3)."""
    schema = app.openapi()
    component_names = set(schema.get("components", {}).get("schemas", {}).keys())

    for path, expected_schema in _RESPONSE_SCHEMAS.items():
        assert any(expected_schema in name for name in component_names), (
            f"Expected response schema '{expected_schema}' for {path} "
            f"not found among components: {sorted(component_names)}"
        )
