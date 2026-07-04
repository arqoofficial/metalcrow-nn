from fastapi import APIRouter

from app.api.routes import (
    analytics,
    chat,
    graph,
    ingest,
    login,
    private,
    sources,
    users,
    utils,
    wiki,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(chat.router)
api_router.include_router(graph.router)
api_router.include_router(wiki.router)
api_router.include_router(analytics.router)
api_router.include_router(analytics.metrics_router)
api_router.include_router(ingest.router)
api_router.include_router(ingest.admin_router)
api_router.include_router(sources.router)


if settings.ENVIRONMENT == "local":
    api_router.include_router(private.router)
