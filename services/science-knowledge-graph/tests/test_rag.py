"""Tests for Graph RAG retriever, generator, and endpoint."""

import openai
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from science_kg.models import (
    GraphNode,
    GraphEdge,
    SearchResult,
    RetrievalContext,
)
from science_kg.rag.retriever import GraphRetriever, _extract_terms


# ── Term extraction ───────────────────────────────────────────────────────────


def test_extract_terms_basic():
    terms = _extract_terms("What is the yield strength of Ti-6Al-4V?")
    assert "Ti-6Al-4V" in terms or any("Ti" in t for t in terms)
    assert "yield" in terms or "strength" in terms


def test_extract_terms_drops_stopwords():
    terms = _extract_terms("What does the material do?")
    assert "What" not in terms
    assert "the" not in terms
    assert "does" not in terms


def test_extract_terms_deduplication():
    terms = _extract_terms("Ti-6Al-4V Ti-6Al-4V strength")
    assert terms.count("Ti-6Al-4V") == 1


def test_extract_terms_min_length():
    terms = _extract_terms("Ti to at in on")
    assert "to" not in terms
    assert "at" not in terms


# ── Retriever ─────────────────────────────────────────────────────────────────


def _make_client(nodes=None, edges=None):
    """Build a mock Neo4jClient that returns preset nodes/edges."""
    nodes = nodes or [
        GraphNode(text="Ti-6Al-4V", type="MATERIAL", sources=["paper1.pdf"]),
        GraphNode(text="yield strength", type="PROPERTY", sources=["paper1.pdf"]),
    ]
    edges = edges or [
        GraphEdge(
            source="Ti-6Al-4V",
            target="yield strength",
            relation="produces_output",
            verb="show",
            sources=["paper1.pdf"],
        )
    ]

    # Mock session for _find_nodes
    mock_result = AsyncMock()
    mock_result.data = AsyncMock(
        return_value=[
            {"n": {"text": n.text, "type": n.type, "sources": n.sources}} for n in nodes
        ]
    )
    mock_session = AsyncMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_driver = MagicMock()
    mock_driver.session = MagicMock(return_value=mock_session)

    client = MagicMock()
    client._driver = mock_driver
    client._database = "neo4j"
    client.get_entity_neighbourhood = AsyncMock(
        return_value=SearchResult(nodes=nodes, edges=edges)
    )
    # retrieve() awaits vector_search() unconditionally once embed_text()
    # returns a vector (real network call when OPENAI_API_KEY is configured,
    # e.g. in this environment) — must be an AsyncMock, not plain MagicMock.
    client.vector_search = AsyncMock(return_value=[])
    # retrieve() also awaits get_document_texts() to pull raw chunk prose for
    # hybrid graph+text RAG — likewise an AsyncMock.
    client.get_document_texts = AsyncMock(return_value={})
    # retrieve() awaits find_documents_by_title() for the title-match source
    # channel — must be an AsyncMock so the union ranking has something to
    # iterate (empty list = channel contributes nothing, other channels stand).
    client.find_documents_by_title = AsyncMock(return_value=[])
    return client


@pytest.mark.asyncio
async def test_retriever_returns_context():
    client = _make_client()
    retriever = GraphRetriever(client)
    ctx = await retriever.retrieve("What is the yield strength of Ti-6Al-4V?")
    assert isinstance(ctx, RetrievalContext)
    assert len(ctx.nodes) > 0
    assert len(ctx.edges) > 0


@pytest.mark.asyncio
async def test_retriever_matched_entities():
    client = _make_client()
    retriever = GraphRetriever(client)
    ctx = await retriever.retrieve("Ti-6Al-4V strength")
    assert len(ctx.matched_entities) > 0


@pytest.mark.asyncio
async def test_retriever_sources_populated():
    client = _make_client()
    retriever = GraphRetriever(client)
    ctx = await retriever.retrieve("Ti-6Al-4V")
    assert "paper1.pdf" in ctx.sources


@pytest.mark.asyncio
async def test_retriever_empty_graph_returns_empty():
    client = _make_client(nodes=[], edges=[])
    client.get_entity_neighbourhood = AsyncMock(
        return_value=SearchResult(nodes=[], edges=[])
    )
    # _find_nodes returns nothing
    mock_result = AsyncMock()
    mock_result.data = AsyncMock(return_value=[])
    mock_session = AsyncMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    client._driver.session = MagicMock(return_value=mock_session)

    retriever = GraphRetriever(client)
    ctx = await retriever.retrieve("unknown material xyz")
    assert ctx.nodes == []
    assert ctx.edges == []


@pytest.mark.asyncio
async def test_retriever_max_nodes_limit():
    many_nodes = [
        GraphNode(text=f"mat{i}", type="MATERIAL", sources=[]) for i in range(100)
    ]
    client = _make_client(nodes=many_nodes, edges=[])
    retriever = GraphRetriever(client, max_nodes=10)
    ctx = await retriever.retrieve("material")
    assert len(ctx.nodes) <= 10


@pytest.mark.asyncio
async def test_retriever_finds_vector_only_match():
    """A node with no CONTAINS-matchable term in the question (e.g. a synonym
    or paraphrase) must still surface via the vector-search channel — this is
    the whole point of adding it alongside CONTAINS."""
    # _find_nodes (CONTAINS) returns nothing for this question's terms.
    client = _make_client(nodes=[], edges=[])
    mock_result = AsyncMock()
    mock_result.data = AsyncMock(return_value=[])
    mock_session = AsyncMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    client._driver.session = MagicMock(return_value=mock_session)

    vector_node = GraphNode(text="desalination", type="REGIME", sources=["paper9.pdf"])
    client.vector_search = AsyncMock(return_value=[vector_node])
    client.get_entity_neighbourhood = AsyncMock(
        return_value=SearchResult(nodes=[vector_node], edges=[])
    )

    with patch(
        "science_kg.rag.retriever.embed_text", AsyncMock(return_value=[0.1] * 1536)
    ):
        retriever = GraphRetriever(client)
        ctx = await retriever.retrieve("what does water desalting involve?")

    client.vector_search.assert_called_once()
    assert "desalination" in ctx.matched_entities
    assert any(n.text == "desalination" for n in ctx.nodes)


@pytest.mark.asyncio
async def test_retriever_skips_vector_search_when_embedding_unavailable():
    """embed_text returning None (no API key / API error) must not raise or
    call vector_search — same graceful-degradation contract as elsewhere."""
    client = _make_client()

    with patch(
        "science_kg.rag.retriever.embed_text", AsyncMock(return_value=None)
    ):
        retriever = GraphRetriever(client)
        await retriever.retrieve("Ti-6Al-4V")

    client.vector_search.assert_not_called()


# ── Generator ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generator_empty_context_still_calls_llm_for_casual_message():
    """A greeting with no graph context must reach the LLM (which replies
    naturally per _SYSTEM rule 1) instead of a hardcoded "no data" non-answer —
    that hardcoded short-circuit used to fire for every empty-context message,
    including plain "hi"."""
    from science_kg.rag.generator import generate_answer

    ctx = RetrievalContext(nodes=[], edges=[], matched_entities=[], sources=[])

    mock_message = MagicMock()
    mock_message.content = "Hi there! How can I help you with materials science today?"
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with (
        patch("science_kg.rag.generator.settings.openai_api_key", "test-key"),
        patch("science_kg.rag.generator.openai.AsyncOpenAI") as mock_cls,
    ):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        answer = await generate_answer("hi", ctx)

    mock_client.chat.completions.create.assert_called_once()
    assert "knowledge graph" not in answer.lower()
    assert "not contain" not in answer.lower()


@pytest.mark.asyncio
async def test_generator_empty_context_domain_question_reaches_llm():
    """A real domain question with no graph context must still reach the LLM
    (with an explicit "no context found" marker in the prompt) rather than
    short-circuit before ever calling it — the LLM itself decides how to say
    "insufficient data" per _SYSTEM rule 2."""
    from science_kg.rag.generator import generate_answer

    ctx = RetrievalContext(nodes=[], edges=[], matched_entities=[], sources=[])

    mock_message = MagicMock()
    mock_message.content = "I don't have data on unobtainium in the knowledge graph."
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with (
        patch("science_kg.rag.generator.settings.openai_api_key", "test-key"),
        patch("science_kg.rag.generator.openai.AsyncOpenAI") as mock_cls,
    ):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        await generate_answer("What is the yield strength of unobtainium?", ctx)

    mock_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    user_message = call_kwargs["messages"][1]["content"]
    assert "no graph context found" in user_message.lower()


@pytest.mark.asyncio
async def test_generator_calls_claude_api():
    from science_kg.rag.generator import generate_answer

    ctx = RetrievalContext(
        nodes=[GraphNode(text="Ti-6Al-4V", type="MATERIAL", sources=["p1.pdf"])],
        edges=[
            GraphEdge(
                source="Ti-6Al-4V",
                target="yield strength",
                relation="produces_output",
                verb="show",
                sources=["p1.pdf"],
            )
        ],
        matched_entities=["Ti-6Al-4V"],
        sources=["p1.pdf"],
    )

    mock_message = MagicMock()
    mock_message.content = "Ti-6Al-4V shows high yield strength according to p1.pdf."
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with (
        patch("science_kg.rag.generator.settings.openai_api_key", "test-key"),
        patch("science_kg.rag.generator.openai.AsyncOpenAI") as mock_cls,
    ):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        answer = await generate_answer("What is the yield strength of Ti-6Al-4V?", ctx)

    assert "Ti-6Al-4V" in answer
    mock_client.chat.completions.create.assert_called_once()


@pytest.mark.asyncio
async def test_generator_sends_system_prompt_and_graph_context():
    """generator.py has no Anthropic-style prompt caching — instead verify the system
    instructions and serialized graph context actually reach the API call."""
    from science_kg.rag.generator import generate_answer

    ctx = RetrievalContext(
        nodes=[GraphNode(text="NiTi", type="MATERIAL", sources=["p2.pdf"])],
        edges=[],
        matched_entities=["NiTi"],
        sources=["p2.pdf"],
    )

    mock_message = MagicMock()
    mock_message.content = "NiTi is a shape memory alloy."
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with (
        patch("science_kg.rag.generator.settings.openai_api_key", "test-key"),
        patch("science_kg.rag.generator.openai.AsyncOpenAI") as mock_cls,
    ):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        await generate_answer("What is NiTi?", ctx)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "materials science" in messages[0]["content"].lower()
        assert messages[1]["role"] == "user"
        assert "NiTi" in messages[1]["content"]
        assert "What is NiTi?" in messages[1]["content"]


@pytest.mark.asyncio
async def test_generator_api_error_returns_helpful_message():
    from science_kg.rag.generator import generate_answer

    ctx = RetrievalContext(
        nodes=[GraphNode(text="Ti-6Al-4V", type="MATERIAL", sources=["p1.pdf"])],
        edges=[],
        matched_entities=["Ti-6Al-4V"],
        sources=["p1.pdf"],
    )

    with (
        patch("science_kg.rag.generator.settings.openai_api_key", "test-key"),
        patch("science_kg.rag.generator.openai.AsyncOpenAI") as mock_cls,
    ):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=openai.AuthenticationError(
                "invalid key", response=MagicMock(), body=None
            )
        )
        mock_cls.return_value = mock_client

        answer = await generate_answer("What is Ti-6Al-4V?", ctx)

    assert "generation failed" in answer.lower()
    assert "OPENAI_API_KEY" in answer


def test_expand_search_terms_canonicalizes_vt6():
    from science_kg.rag.retriever import _expand_search_terms

    terms = _expand_search_terms(["ВТ6", "закалка"])
    assert "ВТ6" in terms
    assert "Ti-6Al-4V" in terms


# ── API endpoint ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rag_endpoint_returns_response():
    from httpx import AsyncClient, ASGITransport
    from fastapi import FastAPI
    from science_kg.api.routes import router

    ctx = RetrievalContext(
        nodes=[GraphNode(text="Ti-6Al-4V", type="MATERIAL", sources=["p1.pdf"])],
        edges=[
            GraphEdge(
                source="Ti-6Al-4V",
                target="strength",
                relation="produces_output",
                verb="show",
                sources=["p1.pdf"],
            )
        ],
        matched_entities=["Ti-6Al-4V"],
        sources=["p1.pdf"],
    )

    with (
        patch("science_kg.api.routes.GraphRetriever") as mock_ret_cls,
        patch(
            "science_kg.api.routes.generate_answer", new_callable=AsyncMock
        ) as mock_gen,
    ):
        mock_retriever = AsyncMock()
        mock_retriever.retrieve = AsyncMock(return_value=ctx)
        mock_ret_cls.return_value = mock_retriever
        mock_gen.return_value = "Ti-6Al-4V has high strength (source: p1.pdf)."

        app = FastAPI()
        app.include_router(router)
        app.state.graph = MagicMock()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/v1/rag/query", json={"question": "What is Ti-6Al-4V strength?"}
            )

    assert resp.status_code == 200
    body = resp.json()
    assert "answer" in body
    assert "context_nodes" in body
    assert "sources" in body
    assert body["answer"] == "Ti-6Al-4V has high strength (source: p1.pdf)."
