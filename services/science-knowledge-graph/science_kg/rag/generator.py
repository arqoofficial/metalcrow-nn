"""RAG generator: serialise subgraph → LLM API → answer."""

import json
import logging
import httpx
import openai
from science_kg.models import (
    AnswerStatus,
    GeneratedAnswer,
    RetrievalContext,
    RetrievalOutcome,
)
from science_kg.config import settings

logger = logging.getLogger(__name__)

# Phrases a grounded-only model uses when it declines for lack of context. Used
# to tell "answered from the graph" apart from "refused" (SPEC §A2/§A5) — a
# heuristic, deliberately broad, matched case-insensitively as substrings.
_REFUSAL_MARKERS = (
    "нет информ",
    "отсутств",
    "не могу ответ",
    "не содержит",
    "нет данных",
    "не указан",
    "недостаточно",
    "не предоставл",
    "не нашл",
    "no information",
    "not contain",
    "cannot answer",
    "insufficient",
    "no relevant",
)


def looks_like_refusal(answer: str) -> bool:
    """True if the answer is a "I can't find this" non-answer (SPEC §A2/§A5).

    Only the OPENING of the answer is inspected: a grounded refusal always leads
    with the disclaimer ("В предоставленном контексте нет информации…"), whereas
    a real answer may legitimately contain a marker word mid-sentence ("селен
    отсутствует в осадке") — matching the whole body flagged those as refusals."""
    head = answer.strip().lower()[:150]
    return any(m in head for m in _REFUSAL_MARKERS)


def gap_hint(context: RetrievalContext) -> str:
    """A short honest-degradation suffix for a refusal, so the user gets more
    than a mute "no information" (SPEC §A2/§A3): distinguishes "nothing in the
    corpus matched" from "found related material but not the answer", and points
    at the nearest entities. Never echoes filenames (rule 3) — those surface as
    citation chips."""
    if context.outcome == RetrievalOutcome.NO_ANCHOR:
        return (
            "\n\nВ корпусе не нашлось материалов по этому запросу — возможно, "
            "темы нет в загруженных документах, либо стоит переформулировать "
            "(материал + процесс + условия)."
        )
    ents = [
        e
        for e in context.matched_entities
        if "::chunk" not in e
        and "/" not in e
        and not e.lower().endswith((".pdf", ".docx", ".pptx"))
    ][:4]
    lead = "Ближе всего в графе: " + ", ".join(ents) + ". " if ents else ""
    return (
        f"\n\n{lead}Прямого ответа в найденных документах нет — уточните "
        "формулировку или включите режим «Онтология»."
    )

_SYSTEM = """\
You are a materials science expert assistant backed by a structured knowledge graph.

Return ONLY a JSON object: {"status": "<status>", "answer": "<text>"}.

"status" — classify honestly (this drives the UI's confidence badge and is more
reliable coming from you than guessed from your wording):
- "grounded": a materials/experiments/processes question whose answer is
  SUPPORTED BY / CONSISTENT WITH the context excerpts below — even if the facts
  are also textbook knowledge. If the excerpts discuss the topic and back your
  answer, use "grounded". This is the normal case for on-topic context.
- "ungrounded": a domain question where the context is off-topic or empty, so
  you answered purely from your own general knowledge. Use this ONLY when you
  did NOT rely on the excerpts.
- "no_data": a domain question you cannot answer at all. Put a brief, honest
  "not enough information" sentence in "answer". Prefer this over inventing facts.
- "casual": greeting, thanks, goodbye, or meta small-talk not about materials.

"answer" rules:
1. For "casual", reply naturally and briefly; do not mention the knowledge graph
   or lack of data.
2. For domain questions, answer from the context; never invent facts.
3. NEVER write document filenames, file paths, or chunk identifiers (e.g.
   "...pdf.md::chunk12", "Журналы/…"). Provenance is shown separately as clickable
   links — no "(источник: …)"/"(source: …)" citations in the prose.
4. Concise: 2-5 sentences unless the question requires more detail.
5. Use SI units and standard materials-science notation.
6. Answer in the SAME language as the question.
"""


def _serialize_context(context: RetrievalContext) -> str:
    if not context.nodes and not context.edges:
        return "=== Knowledge Graph Context ===\n\n(no graph context found for this question)"

    # Deliberately omits raw source doc_ids / chunk paths — the LLM doesn't
    # need them to answer, and feeding them only tempts it to echo ugly
    # "…pdf.md::chunk12" strings into the prose. Provenance is returned
    # separately in RAGResponse.sources and surfaced as clickable UI links.
    lines: list[str] = ["=== Knowledge Graph Context ===", ""]

    if context.nodes:
        lines.append("Nodes:")
        for n in context.nodes:
            lines.append(f"  [{n.type}] {n.text}")

    if context.edges:
        lines.append("")
        lines.append("Relationships:")
        for e in context.edges[:100]:
            verb = f" ({e.verb})" if e.verb else ""
            lines.append(f"  {e.source}  --[{e.relation}]-->  {e.target}{verb}")

    if context.source_texts:
        lines.append("")
        lines.append("=== Source excerpts ===")
        for i, txt in enumerate(context.source_texts, 1):
            lines.append(f"\n--- excerpt {i} ---\n{txt}")

    return "\n".join(lines)


def _trace_kwargs(langfuse_headers: dict[str, str] | None) -> dict:
    """Turn forwarded `langfuse_*` headers into openai `create()` kwargs the
    LiteLLM gateway understands: send them as request headers AND mirror
    user/session into `metadata` (extra_body) so attribution lands regardless of
    the gateway's LiteLLM version. See
    docs.litellm.ai/docs/observability/langfuse_integration."""
    if not langfuse_headers:
        return {}
    kwargs: dict = {"extra_headers": dict(langfuse_headers)}
    metadata: dict[str, str] = {}
    if "langfuse_trace_user_id" in langfuse_headers:
        metadata["trace_user_id"] = langfuse_headers["langfuse_trace_user_id"]
    if "langfuse_session_id" in langfuse_headers:
        metadata["session_id"] = langfuse_headers["langfuse_session_id"]
    if metadata:
        kwargs["extra_body"] = {"metadata": metadata}
    return kwargs


def _infer_status(text: str) -> AnswerStatus:
    """Fallback status when the model didn't return the JSON envelope (or an
    OpenAI-compatible endpoint that ignores response_format): reuse the opening-
    disclaimer heuristic just to separate a refusal from a real answer."""
    return AnswerStatus.NO_DATA if looks_like_refusal(text) else AnswerStatus.GROUNDED


def _parse_generated(content: str) -> GeneratedAnswer:
    """Parse the model's JSON envelope into a GeneratedAnswer, degrading
    gracefully: a non-JSON reply (older model, or json mode unsupported) is taken
    verbatim as the answer with a heuristically-inferred status."""
    content = (content or "").strip()
    try:
        data = json.loads(content)
        answer = (data.get("answer") or "").strip()
        if answer:
            raw = str(data.get("status") or "").strip().lower()
            status = next(
                (s for s in AnswerStatus if s.value == raw), _infer_status(answer)
            )
            return GeneratedAnswer(answer=answer, status=status)
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    text = content or "No content returned."
    return GeneratedAnswer(answer=text, status=_infer_status(text))


async def generate_answer(
    question: str,
    context: RetrievalContext,
    *,
    langfuse_headers: dict[str, str] | None = None,
) -> GeneratedAnswer:
    """Always calls the LLM — even with empty context — so it can tell casual
    messages ("hi") apart from real domain questions with no graph data instead
    of a hardcoded non-answer. The model returns a JSON envelope with a
    self-reported `status` (grounded / ungrounded / no_data / casual): far more
    stable than guessing groundedness from its wording. temperature=0 keeps the
    same context from flipping between answering and hedging across runs."""
    if not settings.openai_api_key:
        if context.matched_entities:
            return GeneratedAnswer(
                answer=(
                    "Knowledge graph context was found, but LLM generation is not "
                    "configured (set OPENAI_API_KEY in .env). "
                    f"Matched entities: {', '.join(context.matched_entities[:5])}."
                ),
                status=AnswerStatus.UNGROUNDED,
            )
        return GeneratedAnswer(
            answer="LLM generation is not configured (set OPENAI_API_KEY in .env).",
            status=AnswerStatus.UNGROUNDED,
        )

    client = openai.AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        http_client=httpx.AsyncClient(trust_env=False),
    )
    ctx_text = _serialize_context(context)

    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": f"{ctx_text}\n\n=== Question ===\n{question}",
                },
            ],
            max_tokens=1024,
            temperature=0,
            response_format={"type": "json_object"},
            **_trace_kwargs(langfuse_headers),
        )
    except openai.APIError as exc:
        logger.warning("RAG LLM call failed: %s", exc)
        return GeneratedAnswer(
            answer=(
                f"Answer generation failed ({exc.__class__.__name__}). Check "
                "OPENAI_API_KEY, OPENAI_BASE_URL and OPENAI_MODEL in .env."
            ),
            status=AnswerStatus.UNGROUNDED,
        )

    choices = response.choices or []
    logger.info("RAG response: choices=%d", len(choices))

    if not choices:
        return GeneratedAnswer(
            answer=f"Model returned no response. (model={settings.openai_model})",
            status=AnswerStatus.UNGROUNDED,
        )

    content = choices[0].message.content
    if not content:
        # Qwen3 thinking mode: answer lives in reasoning field
        content = getattr(choices[0].message, "reasoning", None) or ""
    return _parse_generated(content)
