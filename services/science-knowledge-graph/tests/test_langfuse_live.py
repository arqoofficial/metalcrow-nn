"""LIVE gateway integration — the REAL generator code hits the LiteLLM gateway.

Unlike test_langfuse_tracing.py (which mocks the openai SDK), this drives the
actual `generate_answer` through the real `openai.AsyncOpenAI` client pointed at
the gateway, with `langfuse_*` headers. It proves the app's own client emits a
gateway-ACCEPTED request carrying the trace fields — stronger than the curl
probe, which only sent a hand-built request.

Gated: only runs when LANGFUSE_LIVE_GATEWAY is set (plus LLM_BASE_URL /
LLM_API_KEY / LITSEARCH_LLM_MODEL). Skipped in normal/CI hermetic runs.
Confirm the trace's User Id / Session in the Langfuse UI afterwards.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("LANGFUSE_LIVE_GATEWAY"),
    reason="live gateway test — set LANGFUSE_LIVE_GATEWAY=1 + LLM_* env to run",
)


@pytest.mark.asyncio
async def test_generate_answer_live_reaches_gateway(monkeypatch):
    from science_kg.models import RetrievalContext
    from science_kg.rag import generator

    monkeypatch.setattr(generator.settings, "openai_api_key", os.environ["LLM_API_KEY"])
    monkeypatch.setattr(generator.settings, "openai_base_url", os.environ["LLM_BASE_URL"])
    monkeypatch.setattr(
        generator.settings, "openai_model", os.environ["LITSEARCH_LLM_MODEL"]
    )

    ctx = RetrievalContext(nodes=[], edges=[], matched_entities=[], sources=[])
    headers = {
        "langfuse_trace_user_id": "live-user-science-kg",
        "langfuse_session_id": os.environ.get("LIVE_SESSION", "live-session-science-kg"),
    }

    answer = await generator.generate_answer(
        "Reply with the single word: ok.", ctx, langfuse_headers=headers
    )

    print(f"\n[LIVE science-kg] answer={answer!r}")
    assert isinstance(answer, str) and answer.strip()
    # generate_answer swallows APIError into a helpful string; a real answer means
    # the gateway accepted our extra_headers + extra_body metadata.
    assert "generation failed" not in answer.lower()
    assert "not configured" not in answer.lower()
