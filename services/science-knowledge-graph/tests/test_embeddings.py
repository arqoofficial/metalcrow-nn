"""Tests for embeddings.embed_text — mocked client, no real network calls."""

import openai
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from science_kg.embeddings import embed_batch, embed_text, EMBEDDING_DIMENSIONS


@pytest.mark.asyncio
async def test_embed_text_returns_vector():
    mock_embedding = MagicMock()
    mock_embedding.embedding = [0.1] * EMBEDDING_DIMENSIONS
    mock_response = MagicMock()
    mock_response.data = [mock_embedding]

    with (
        patch("science_kg.embeddings.settings.openai_api_key", "test-key"),
        patch("science_kg.embeddings.openai.AsyncOpenAI") as mock_cls,
    ):
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        vec = await embed_text("Ti-6Al-4V")

    assert vec is not None
    assert len(vec) == EMBEDDING_DIMENSIONS
    mock_client.embeddings.create.assert_called_once()


@pytest.mark.asyncio
async def test_embed_text_no_api_key_returns_none():
    with patch("science_kg.embeddings.settings.openai_api_key", ""):
        vec = await embed_text("Ti-6Al-4V")
    assert vec is None


@pytest.mark.asyncio
async def test_embed_batch_returns_aligned_vectors():
    mock_response = MagicMock()
    mock_response.data = [
        MagicMock(index=1, embedding=[0.1] * EMBEDDING_DIMENSIONS),
        MagicMock(index=0, embedding=[0.2] * EMBEDDING_DIMENSIONS),
    ]

    with (
        patch("science_kg.embeddings.settings.openai_api_key", "test-key"),
        patch("science_kg.embeddings.openai.AsyncOpenAI") as mock_cls,
    ):
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        vectors = await embed_batch(["first", "second"])

    assert len(vectors) == 2
    assert vectors[0] == [0.2] * EMBEDDING_DIMENSIONS
    assert vectors[1] == [0.1] * EMBEDDING_DIMENSIONS
    assert all(v is not None for v in vectors)


@pytest.mark.asyncio
async def test_embed_text_api_error_returns_none():
    with (
        patch("science_kg.embeddings.settings.openai_api_key", "test-key"),
        patch("science_kg.embeddings.openai.AsyncOpenAI") as mock_cls,
    ):
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(
            side_effect=openai.AuthenticationError(
                "invalid key", response=MagicMock(), body=None
            )
        )
        mock_cls.return_value = mock_client

        vec = await embed_text("Ti-6Al-4V")

    assert vec is None
