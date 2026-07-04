"""FastAPI application — lifecycle, middleware, router registration."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from science_kg.config import settings
from science_kg.graph.neo4j_client import Neo4jClient
from science_kg.nlp.pipeline import get_nlp
from science_kg.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up both language models so the first request doesn't pay load cost
    import asyncio

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, get_nlp, settings.spacy_model_ru)
    await loop.run_in_executor(None, get_nlp, settings.spacy_model_en)

    app.state.graph = Neo4jClient(
        uri=settings.neo4j_uri,
        user=settings.neo4j_user,
        password=settings.neo4j_password,
        database=settings.neo4j_database,
    )
    await app.state.graph.bootstrap_schema()

    yield

    await app.state.graph.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Science Knowledge Graph",
        description="NLP-powered knowledge graph for materials science research",
        version="0.2.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    return app


app = create_app()
