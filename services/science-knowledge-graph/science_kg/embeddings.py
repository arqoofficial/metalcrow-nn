"""Text embeddings via the same OpenAI-compatible proxy used for chat completions
(`rag/generator.py`) — `text-embedding-3-small`, 1536-dim, confirmed available on
`api.proxyapi.ru`. Not the SPEC_V3-mandated `multilingual-e5-large` (768-dim) — a
deliberate tradeoff for cost/simplicity, made explicit here rather than silently
diverging from the spec.
"""

import logging

import httpx
import openai
from science_kg.config import settings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


async def embed_text(text: str) -> list[float] | None:
    """Returns None (never raises) on missing API key or API error — callers
    degrade to skipping the embedding, same philosophy as the rest of this
    service's LLM-dependent paths."""
    if not settings.openai_api_key:
        logger.warning("embed_text: OPENAI_API_KEY not configured, skipping")
        return None

    client = openai.AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        http_client=httpx.AsyncClient(trust_env=False),
    )
    try:
        response = await client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    except openai.APIError as exc:
        logger.warning("embed_text failed: %s", exc)
        return None

    return response.data[0].embedding


async def embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Batch embedding call. Returns a list aligned with *texts*; items whose
    embedding failed are ``None``. Reuses the same OpenAI-compatible proxy and
    model as :func:`embed_text`."""
    if not settings.openai_api_key:
        logger.warning("embed_batch: OPENAI_API_KEY not configured, skipping")
        return [None] * len(texts)
    if not texts:
        return []

    client = openai.AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        http_client=httpx.AsyncClient(trust_env=False),
    )
    try:
        response = await client.embeddings.create(
            model=EMBEDDING_MODEL, input=list(texts)
        )
    except openai.APIError as exc:
        logger.warning("embed_batch failed: %s", exc)
        return [None] * len(texts)

    # response.data may not be in the same order as input; OpenAI returns
    # an index field per embedding object.
    indexed: dict[int, list[float]] = {}
    for item in response.data:
        indexed[item.index] = item.embedding
    return [indexed.get(i) for i in range(len(texts))]
