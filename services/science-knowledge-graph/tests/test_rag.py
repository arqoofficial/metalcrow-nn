"""Tests for Graph RAG retriever, generator, and endpoint."""

import openai
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from science_kg.models import (
    GraphNode,
    GraphEdge,
    SearchResult,
    RetrievalContext,
    GeneratedAnswer,
    AnswerStatus,
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
    # retrieve() also awaits find_documents_by_content() for the full-text prose
    # channel (SPEC §B1) — AsyncMock so the RRF fusion has something to iterate.
    client.find_documents_by_content = AsyncMock(return_value=[])
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

        result = await generate_answer("hi", ctx)

    mock_client.chat.completions.create.assert_called_once()
    assert "knowledge graph" not in result.answer.lower()
    assert "not contain" not in result.answer.lower()


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

        result = await generate_answer("What is the yield strength of Ti-6Al-4V?", ctx)

    assert "Ti-6Al-4V" in result.answer
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

        result = await generate_answer("What is Ti-6Al-4V?", ctx)

    assert "generation failed" in result.answer.lower()
    assert "OPENAI_API_KEY" in result.answer


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
        mock_gen.return_value = GeneratedAnswer(
            answer="Ti-6Al-4V has high strength (source: p1.pdf).",
            status=AnswerStatus.GROUNDED,
        )

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
    assert body["grounded"] is True  # status-driven grounding flag


# ── Gap handling + ranking helpers (SPEC gap-handling+ranking) ─────────────────

from science_kg.models import RetrievalOutcome
from science_kg.rag.retriever import (
    _extract_relevant_window,
    _rrf_fuse,
    _is_junk_entity,
)
from science_kg.graph.neo4j_client import _lucene_or_query
from science_kg.rag.generator import looks_like_refusal, gap_hint


def test_relevant_window_localises_deep_passage():
    """§B2: the window is picked around query terms, not from the doc opening."""
    filler = "\n\n".join(f"обложка журнала часть {i}" for i in range(400))
    answer = "Обратный осмос удаляет сульфаты и хлориды из воды обогащения."
    text = filler + "\n\n" + answer + "\n\n" + filler
    window = _extract_relevant_window(text, ["осмос", "сульфаты", "хлориды"], 2000)
    assert "осмос" in window
    assert len(window) <= 2100


def test_relevant_window_falls_back_to_prefix_without_matches():
    text = "\n\n".join(f"параграф без совпадений {i}" for i in range(500))
    out = _extract_relevant_window(text, ["осмос", "теллур"], 500)
    assert out.startswith("параграф")  # prefix truncation, not a window marker


def test_rrf_fuse_rewards_agreement_and_weight():
    """A doc two channels agree on beats one only a single channel ranks."""
    channels = {
        "title": ["A", "B"],
        "content": ["A", "C"],
        "specificity": ["D"],
        "coverage": ["D"],
    }
    weights = {"title": 2.0, "content": 1.5, "specificity": 1.0, "coverage": 1.0}
    fused = _rrf_fuse(channels, weights)
    assert fused[0] == "A"  # top of the two strongest-weighted channels


def test_lucene_query_drops_short_and_boosts_long():
    q = _lucene_or_query(["мм", "осмос", "обессоливание"])
    assert "мм" not in q  # < 4 chars and not an element symbol → dropped
    assert "осмос" in q
    assert "обессоливание^2" in q  # long term boosted


def test_lucene_query_empty_when_all_generic():
    assert _lucene_or_query(["мм", "см", "аб"]) == ""


def test_is_junk_entity():
    assert _is_junk_entity("для")
    assert _is_junk_entity("的")
    assert not _is_junk_entity("мышьяк")
    assert not _is_junk_entity("Ti-6Al-4V")


def test_looks_like_refusal_only_at_opening():
    assert looks_like_refusal("В предоставленном контексте нет информации о X.")
    assert looks_like_refusal("В тексте нет данных о лабораториях, занимавшихся этим.")
    # a genuine answer that mentions a marker word deep in the body (past the
    # opening disclaimer zone) is NOT a refusal — this is the #3 selenium case.
    good = (
        "Селен и теллур окисляются на свинцовом аноде до неосаждающихся форм и "
        "накапливаются в циркулирующих растворах электролита, поэтому в товарной "
        "катодной меди они практически отсутствуют при высоком извлечении меди."
    )
    assert not looks_like_refusal(good)


def test_gap_hint_distinguishes_no_corpus_from_no_match():
    no_anchor = RetrievalContext(
        nodes=[], edges=[], matched_entities=[], sources=[],
        outcome=RetrievalOutcome.NO_ANCHOR,
    )
    assert "не нашлось" in gap_hint(no_anchor)
    weak = RetrievalContext(
        nodes=[], edges=[], matched_entities=["никель", "плотность тока"],
        sources=[], outcome=RetrievalOutcome.WEAK_CONTEXT,
    )
    hint = gap_hint(weak)
    assert "никель" in hint and "Онтолог" in hint


def test_gap_hint_never_echoes_filenames():
    weak = RetrievalContext(
        nodes=[], edges=[],
        matched_entities=["RAW_DATA/Обзоры/Мышьяк.pdf::chunk0", "мышьяк"],
        sources=[], outcome=RetrievalOutcome.WEAK_CONTEXT,
    )
    hint = gap_hint(weak)
    assert "::chunk" not in hint and ".pdf" not in hint
    assert "мышьяк" in hint


# ── Chemical element symbols (SPEC §B1/§B2 follow-up) ──────────────────────────

from science_kg.nlp.normalizer import is_element_symbol
from science_kg.rag.retriever import _extract_element_symbols


def test_is_element_symbol_case_sensitive():
    assert is_element_symbol("Au") and is_element_symbol("Ni") and is_element_symbol("As")
    # lowercase english stop words that collide with symbols must NOT match
    assert not is_element_symbol("as") and not is_element_symbol("in")
    assert not is_element_symbol("мышьяк")


def test_extract_element_symbols_from_question():
    syms = _extract_element_symbols("Как распределяются Au, Ag и МПГ между штейном?")
    assert syms == ["Au", "Ag"]  # order-preserving; МПГ is not a symbol
    assert _extract_element_symbols("электроэкстракция Ni") == ["Ni"]


def test_lucene_query_keeps_element_symbols():
    q = _lucene_or_query(["Ni", "плотность", "тока"])
    assert "Ni" in q  # 2-char symbol kept despite the length cutoff
    assert "плотность" in q


def test_relevant_window_localises_on_element_symbols():
    filler = "\n\n".join(f"страница {i} без темы" for i in range(300))
    target = "Коэффициент распределения Au и Ag между штейном и шлаком составляет L=5."
    text = filler + "\n\n" + target + "\n\n" + filler
    window = _extract_relevant_window(text, ["Au", "Ag", "штейн"], 1500)
    assert "Коэффициент распределения" in window


def test_relevant_window_keeps_document_opening():
    """§B2 stabilisation: the opening is always kept, so a document that leads
    with its answer is never worse off than plain prefix truncation."""
    opening = "ВВЕДЕНИЕ. Основные положения работы изложены здесь."
    deep = "\n\n".join(f"раздел {i} про осмос и сульфаты" for i in range(400))
    text = opening + "\n\n" + deep
    window = _extract_relevant_window(text, ["осмос", "сульфаты"], 3000)
    assert "ВВЕДЕНИЕ" in window  # opening preserved even though matches are deep


# ── Structured answer status (grounded flag from the model) ───────────────────

from science_kg.rag.generator import _parse_generated, _infer_status


def test_parse_generated_reads_status_from_json():
    g = _parse_generated('{"status": "no_data", "answer": "Нет данных по X."}')
    assert g.status == AnswerStatus.NO_DATA
    assert g.answer == "Нет данных по X."
    g2 = _parse_generated('{"status": "grounded", "answer": "Медь плавится при 1085 °C."}')
    assert g2.status == AnswerStatus.GROUNDED


def test_parse_generated_falls_back_on_plain_text():
    # a non-JSON reply (endpoint ignored json mode) is taken verbatim
    g = _parse_generated("Ti-6Al-4V has high strength.")
    assert g.answer == "Ti-6Al-4V has high strength."
    assert g.status == AnswerStatus.GROUNDED
    g2 = _parse_generated("В предоставленном контексте нет информации об этом.")
    assert g2.status == AnswerStatus.NO_DATA  # heuristic fallback


def test_parse_generated_unknown_status_infers():
    g = _parse_generated('{"status": "weird", "answer": "Нет данных."}')
    assert g.status == AnswerStatus.NO_DATA  # falls back to heuristic on bad status


@pytest.mark.asyncio
async def test_generator_requests_json_and_zero_temperature():
    from science_kg.rag.generator import generate_answer

    ctx = RetrievalContext(nodes=[], edges=[], matched_entities=[], sources=[])
    mock_message = MagicMock()
    mock_message.content = '{"status": "casual", "answer": "Привет!"}'
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
        result = await generate_answer("привет", ctx)

    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0
    assert kwargs["response_format"] == {"type": "json_object"}
    assert result.status == AnswerStatus.CASUAL
    assert result.answer == "Привет!"
