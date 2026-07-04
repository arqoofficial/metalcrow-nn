import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import SessionDep, get_current_user
from app.schemas.graph import GraphQueryRequest, PathResponse, SubgraphResponse
from app.services import graph as graph_service

router = APIRouter(
    prefix="/graph", tags=["graph"], dependencies=[Depends(get_current_user)]
)


@router.post("/query", response_model=SubgraphResponse)
def query(_session: SessionDep, body: GraphQueryRequest) -> SubgraphResponse:
    """POST /api/v1/graph/query — запрос по шаблону, raw Cypher не принимается (§8.3).

    TODO(SPEC_V3 §5.3): библиотека Cypher-шаблонов (5-8 шт., потребуют `_session`
    для реальных запросов); заглушка — пустой результат, БД пока не используется.
    """
    return graph_service.run_template(body.template_id, body.params, body.max_depth)


@router.get("/subgraph/{entity_id}", response_model=SubgraphResponse)
def subgraph(
    session: SessionDep,
    entity_id: uuid.UUID,
    depth: Annotated[int, Query(ge=1, le=5)] = 2,
) -> SubgraphResponse:
    """GET /api/v1/graph/subgraph/{entity_id} — SQL fallback вокруг эксперимента (§8.3)."""
    return graph_service.sql_subgraph(session, str(entity_id), depth)


@router.get("/path", response_model=PathResponse)
def path(
    from_: Annotated[str, Query(alias="from")],
    to: str,
    max_depth: Annotated[int, Query(ge=1, le=5)] = 4,
) -> PathResponse:
    """GET /api/v1/graph/path (P2) — Neo4j down -> 503, без SQL fallback (§8.3).

    TODO(SPEC_V3 §8.3 P2): Neo4j `shortestPath`; заглушка возвращает пустой путь,
    когда Neo4j доступен.
    """
    if not graph_service.neo4j_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Neo4j unavailable"
        )
    nodes, edges = graph_service.path(from_, to, max_depth)
    return PathResponse(nodes=nodes, edges=edges, path_length=len(edges))
