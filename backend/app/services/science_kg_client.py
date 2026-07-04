"""Синхронный HTTP-клиент к `science-knowledge-graph` — отдельному internal-only
сервису (spaCy NER + Neo4j GraphRAG, services/science-knowledge-graph/README.md),
не части этого uv-workspace.

Все функции глушат `httpx.HTTPError` и возвращают `None`/`False` — сервис
опционален (как и сам Neo4j, см. `services/graph.neo4j_available()`), недоступность
не должна валить chat/graph, только деградировать до пустого результата.

`trust_env=False` на каждом вызове: это internal-only вызов по docker-сети,
системный HTTP(S)_PROXY/ALL_PROXY подхватывать не нужно — как и в
`services/science-knowledge-graph/science_kg/rag/generator.py`, который так же
явно отключает trust_env для своего OpenAI-совместимого LLM-клиента.
"""

import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
# rag_query round-trips through an actual LLM call on the
# science-knowledge-graph side — the short default timeout above is fine for
# graph reads but times out real generation, especially on a cold provider
# connection.
_RAG_TIMEOUT = httpx.Timeout(60.0, connect=3.0)


def available() -> bool:
    try:
        resp = httpx.get(
            f"{settings.SCIENCE_KG_URL}/api/v1/health",
            timeout=_TIMEOUT,
            trust_env=False,
        )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def search(
    *,
    material: str | None = None,
    regime: str | None = None,
    property_: str | None = None,
    limit: int = 20,
) -> dict[str, Any] | None:
    """GET /api/v1/search — subgraph filtered by material/regime/property.
    Response shape: {"nodes": [{"text","type","sources"}], "edges": [{"source",
    "target","relation","verb","sources"}], "gaps": [...]}."""
    params = {
        k: v
        for k, v in {
            "material": material,
            "regime": regime,
            "prop": property_,
            "limit": limit,
        }.items()
        if v is not None
    }
    try:
        resp = httpx.get(
            f"{settings.SCIENCE_KG_URL}/api/v1/search",
            params=params,
            timeout=_TIMEOUT,
            trust_env=False,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data
    except httpx.HTTPError as exc:
        logger.warning("science-knowledge-graph search failed: %s", exc)
        return None


def rag_query(
    question: str, *, max_hops: int = 2, max_nodes: int = 20
) -> dict[str, Any] | None:
    """POST /api/v1/rag/query — graph-grounded LLM answer with sources.
    Response shape: {"answer","context_nodes","context_edges","sources",
    "matched_entities"}."""
    try:
        resp = httpx.post(
            f"{settings.SCIENCE_KG_URL}/api/v1/rag/query",
            json={"question": question, "max_hops": max_hops, "max_nodes": max_nodes},
            timeout=_RAG_TIMEOUT,
            trust_env=False,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data
    except httpx.HTTPError as exc:
        logger.warning("science-knowledge-graph rag_query failed: %s", exc)
        return None


def get_document(doc_id: str) -> dict[str, Any] | None:
    """GET /api/v1/documents/{doc_id} — raw text + meta (incl. meta.source_path
    for SHARED-sourced articles, see scripts/ingest_shared_corpus.py) of a
    previously ingested document. Used to resolve a RAGResponse.sources
    doc-id into a downloadable/viewable article (app/services/chat.py).

    `/` is preserved unescaped in the doc_id — it's meaningful path structure
    (doc_id == f"{source_path}::chunk{i}"), matched server-side by
    `GET /documents/{doc_id:path}`; only unsafe characters get percent-encoded."""
    try:
        resp = httpx.get(
            f"{settings.SCIENCE_KG_URL}/api/v1/documents/{quote(doc_id, safe='/')}",
            timeout=_TIMEOUT,
            trust_env=False,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data
    except httpx.HTTPError as exc:
        logger.warning("science-knowledge-graph get_document failed: %s", exc)
        return None
