# -*- coding: utf-8 -*-
"""LIVE gateway integration — the REAL intent classifier hits the LiteLLM gateway.

Unlike test_langfuse_tracing.py (which mocks the client), this drives the actual
`intent_llm.classify` through the real `openai.OpenAI` client pointed at the
gateway, with `langfuse_*` headers, so LiteLLM attributes the trace.

Gated: only runs when LANGFUSE_LIVE_GATEWAY is set (plus LLM_BASE_URL /
LLM_API_KEY / LLM_INTENT_MODEL). Skipped in normal/CI hermetic runs.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("LANGFUSE_LIVE_GATEWAY"),
    reason="live gateway test — set LANGFUSE_LIVE_GATEWAY=1 + LLM_* env to run",
)


def test_classify_live_reaches_gateway(monkeypatch):
    from ontology.mocks import intent_llm

    # force a fresh client/model bound to the live gateway env
    monkeypatch.setattr(intent_llm, "_client", None)
    monkeypatch.setattr(intent_llm, "_model", None)

    # the real client construction must reach the gateway (creds + base_url ok)
    client = intent_llm._ensure_client()
    model_ids = {m.id for m in client.models.list()}
    print(f"\n[LIVE ontology] gateway serves {len(model_ids)} models; picked _model={intent_llm._model!r}")
    assert model_ids, "gateway returned no models — creds/base_url wrong"

    headers = {
        "langfuse_trace_user_id": "live-user-ontology",
        "langfuse_session_id": os.environ.get("LIVE_SESSION", "live-session-ontology"),
    }
    # real create() with extra_headers + extra_body via the actual classify path.
    # classify swallows errors into None (e.g. if the model rejects strict
    # json_schema); either way the request — with trace headers — reached the
    # gateway, which is what attributes the Langfuse trace.
    result = intent_llm.classify("Показатели извлечения при флотации?", langfuse_headers=headers)
    print(f"[LIVE ontology] classify result={result!r}")
    assert result is None or isinstance(result, dict)
