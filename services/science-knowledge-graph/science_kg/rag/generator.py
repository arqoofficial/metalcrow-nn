"""RAG generator: serialise subgraph → LLM API → answer."""

import logging
import httpx
import openai
from science_kg.models import RetrievalContext
from science_kg.config import settings

logger = logging.getLogger(__name__)

_SYSTEM = """\
You are a materials science expert assistant backed by a structured knowledge graph.

Rules:
1. If the user's message is casual conversation — a greeting, thanks, goodbye, or a
   meta question not about materials/experiments/processes — respond naturally and
   briefly, like a normal assistant would. Do not mention the knowledge graph or lack
   of data for messages like this.
2. If the user's message IS a domain question, answer ONLY from the knowledge graph
   context provided below. If the context does not contain enough information, say so
   explicitly — do not invent facts.
3. NEVER write document filenames, file paths, or chunk identifiers (e.g.
   "...pdf.md::chunk12", "Журналы/…") in your answer. Source attribution is shown
   to the user separately in the UI as clickable links — do not add "(источник: …)"
   or "(source: …)" style citations to the prose. Just state the facts.
4. Keep the answer concise: 2-5 sentences unless the question requires more detail.
5. Use SI units and standard materials-science notation.
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


async def generate_answer(question: str, context: RetrievalContext) -> str:
    """Always calls the LLM — even with empty context — so it can tell casual
    messages ("hi") apart from real domain questions with no graph data (rule 1
    vs rule 2 in `_SYSTEM`) instead of a hardcoded non-answer for every empty
    context regardless of what the user actually said."""
    if not settings.openai_api_key:
        if context.matched_entities:
            return (
                "Knowledge graph context was found, but LLM generation is not "
                "configured (set OPENAI_API_KEY in .env). "
                f"Matched entities: {', '.join(context.matched_entities[:5])}."
            )
        return "LLM generation is not configured (set OPENAI_API_KEY in .env)."

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
        )
    except openai.APIError as exc:
        logger.warning("RAG LLM call failed: %s", exc)
        return (
            f"Answer generation failed ({exc.__class__.__name__}). Check "
            "OPENAI_API_KEY, OPENAI_BASE_URL and OPENAI_MODEL in .env."
        )

    choices = response.choices or []
    logger.info("RAG response: choices=%d", len(choices))

    if not choices:
        return f"Model returned no response. (model={settings.openai_model})"

    content = choices[0].message.content
    if not content:
        # Qwen3 thinking mode: answer lives in reasoning field
        content = (
            getattr(choices[0].message, "reasoning", None) or "No content returned."
        )
    return content
