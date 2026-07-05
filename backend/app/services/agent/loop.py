"""Generic in-process OpenAI tool-calling loop (spec §2.1). Model-driven: each
iteration asks the gateway (`llm.chat`) for a response; a tool-call response is
executed against in-process handlers and fed back as role:"tool" messages; a
text-only response is the final answer. NEVER fabricates text — an unreachable
LLM yields `degraded=True, final_text=None`, and the caller renders an explicit
degraded turn (spec §2.7)."""

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from app.services import llm

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    name: str
    schema: dict[str, Any]
    handler: Callable[..., dict[str, Any]]


@dataclass
class LoopOutcome:
    final_text: str | None = None
    tool_calls_made: list[str] = field(default_factory=list)
    literature_search_ids: list[uuid.UUID] = field(default_factory=list)
    degraded: bool = False

    @property
    def literature_search_id(self) -> uuid.UUID | None:
        """Back-compat: the anchor (first) search of the turn, or None."""
        return self.literature_search_ids[0] if self.literature_search_ids else None


# How many times to retry the forced no-tools answer before giving up and
# degrading. The DeepSeek-via-gateway markup leak is non-deterministic, so a
# couple of retries reliably recover a real answer for a turn that has readable
# full texts. Kept small — each attempt is a full LLM round-trip.
_FORCED_ANSWER_ATTEMPTS = 3


def _named_choice(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name}}


def _contains_tool_markup(text: str) -> bool:
    """True when `text` carries leaked tool-call tokens instead of prose.

    DeepSeek sometimes emits its tool call as TEXT using native tokens (e.g.
    `<｜DSML｜tool_calls>…<｜DSML｜invoke name=…>`), which the gateway does not
    parse — so `run_loop` would otherwise return that raw markup as the final
    answer. We anchor on the exact leaked tokens (fullwidth pipe U+FF5C and the
    tool-call open tags), NOT the bare words "tool"/"search", to avoid false
    positives on ordinary prose."""
    return "｜DSML｜" in text or "<｜tool" in text or "<tool_call" in text


def _forced_answer(
    messages: list[dict[str, Any]],
    schemas: list[dict[str, Any]] | None,
    metadata: dict[str, Any],
    exhausted_system_msg: str | None,
) -> str | None:
    """Force a genuine no-tools reply. Optionally injects a system message
    telling the model to stop calling tools and answer, then re-invokes.
    Returns clean prose, or None (caller marks degraded) when every attempt is
    empty/absent or leaked markup.

    Two robustness measures against the DeepSeek-via-gateway markup leak (the
    model non-deterministically emits `｜DSML｜` tool-call tokens as TEXT instead
    of prose, which the gateway does not parse):
      1. Send NO tools in the request (`tools=None`). With nothing to call, the
         model cannot emit a tool call and is far less likely to leak markup —
         the strongest way to force real prose. (Previously it passed the tool
         schemas with `tool_choice="none"`, which still tempted the leak.)
      2. Retry up to `_FORCED_ANSWER_ATTEMPTS` times, since the leak is
         non-deterministic — the same transcript yields prose on one attempt and
         markup on the next; retrying converges on a real answer instead of
         degrading a turn that had readable full texts."""
    if exhausted_system_msg is not None:
        messages.append({"role": "system", "content": exhausted_system_msg})
    for _ in range(_FORCED_ANSWER_ATTEMPTS):
        forced = llm.chat(messages, tools=None, tool_choice=None, metadata=metadata)
        if (
            forced.ok
            and forced.content
            and forced.content.strip()
            and not _contains_tool_markup(forced.content)
        ):
            return forced.content
    return None  # caller marks degraded


def run_loop(
    session: Session | None,
    chat_session_id: uuid.UUID,
    messages: list[dict[str, Any]],
    tools: list[Tool],
    *,
    max_iters: int,
    first_tool_choice: str | None = None,
    max_tool_calls: int | None = None,
    max_successful_searches: int | None = None,
    max_successful_by_tool: dict[str, int] | None = None,
    exhausted_system_msg: str | None = None,
) -> LoopOutcome:
    schemas = [t.schema for t in tools] or None
    by_name = {t.name: t for t in tools}
    metadata = {"session_id": str(chat_session_id)}
    outcome = LoopOutcome()
    tool_calls_used = 0
    successful_searches = 0
    successful_by_tool: dict[str, int] = {}

    for iteration in range(max_iters):
        if iteration == 0 and first_tool_choice is not None:
            tool_choice: Any = _named_choice(first_tool_choice)
        else:
            tool_choice = "auto" if schemas else None

        result = llm.chat(
            messages,
            tools=schemas,
            tool_choice=tool_choice,
            metadata=metadata,
        )
        if not result.ok:
            outcome.degraded = True
            return outcome

        if not result.tool_calls:
            content = result.content
            # A leaked tool call rendered as TEXT (native DeepSeek markup the
            # gateway didn't parse) is NOT an answer: drop it, inject the stop
            # message, and re-invoke with tool_choice="none" for a real reply.
            if content and _contains_tool_markup(content):
                forced_text = _forced_answer(
                    messages, schemas, metadata, exhausted_system_msg
                )
                if forced_text is not None:
                    outcome.final_text = forced_text
                    outcome.degraded = False
                else:
                    outcome.final_text = None
                    outcome.degraded = True
                return outcome
            # A blank/whitespace-only string is not a real answer either — it
            # must degrade the same as `content is None` (M2 fix), or the
            # caller would persist an empty "answer" as if the model
            # genuinely produced one instead of rendering the explicit
            # degraded turn (spec §2.7).
            if not content or not content.strip():
                outcome.final_text = None
                outcome.degraded = True
            else:
                outcome.final_text = content
                outcome.degraded = False
            return outcome

        # Cap reached: do NOT execute more tool calls — force an abstract-only
        # reply instead (spec: Phase-A loop must end in real prose). This is a
        # harmless backstop for the common single-tool-call-per-response case
        # (it saves a wasted llm.chat round-trip once a cap is already known
        # exhausted at the top of an iteration); it does NOT, by itself, stop
        # an overshoot within a single response that carries MULTIPLE parallel
        # tool-calls — that is enforced per-tool-call below, inside the `for
        # tc in result.tool_calls` loop, which is the only place that can see
        # each individual call before executing it.
        cap_hit = (
            max_tool_calls is not None and tool_calls_used >= max_tool_calls
        ) or (
            max_successful_searches is not None
            and successful_searches >= max_successful_searches
        )
        if cap_hit:
            forced_text = _forced_answer(
                messages, schemas, metadata, exhausted_system_msg
            )
            if forced_text is not None:
                outcome.final_text = forced_text
                outcome.degraded = False
            else:
                outcome.final_text = None
                outcome.degraded = True
            return outcome

        # Echo the assistant tool-call message back into the transcript.
        messages.append(
            {
                "role": "assistant",
                "content": result.content,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                        },
                    }
                    for tc in result.tool_calls
                ],
            }
        )
        # Per-tool-call cap enforcement (fixes the overshoot bug): the model
        # can emit MULTIPLE tool-calls in a SINGLE response (parallel tool
        # calls), so a cap check only at the top of the iteration is checked
        # once yet the inner loop below would otherwise execute ALL of them —
        # e.g. 8 searches in one turn against a cap of 6 attempts / 3
        # successful. Every `tc` is therefore checked against the caps BEFORE
        # it is executed; a tripped call still gets a matching role:"tool"
        # message (every tool_call_id MUST be answered or the next request
        # errors) but the handler is not invoked, so it can't consume budget.
        cap_tripped = False
        for tc in result.tool_calls:
            attempts_cap_hit = (
                max_tool_calls is not None and tool_calls_used >= max_tool_calls
            )
            tool_cap_hit = (
                max_successful_by_tool is not None
                and tc["name"] in max_successful_by_tool
                and successful_by_tool.get(tc["name"], 0)
                >= max_successful_by_tool[tc["name"]]
            )
            global_cap_hit = (
                max_successful_searches is not None
                and successful_searches >= max_successful_searches
            )
            if attempts_cap_hit or tool_cap_hit or global_cap_hit:
                cap_tripped = True
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(
                            {
                                "note": (
                                    "Лимит поиска исчерпан. Больше не вызывай "
                                    "инструменты — дай ответ пользователю."
                                )
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
                continue

            outcome.tool_calls_made.append(tc["name"])
            tool = by_name.get(tc["name"])
            if tool is None:
                tool_result: dict[str, Any] = {"error": f"unknown tool {tc['name']}"}
            else:
                tool_result = tool.handler(session, chat_session_id, **tc["arguments"])
            tool_calls_used += 1
            if isinstance(tool_result, dict) and "search_id" in tool_result:
                sid = uuid.UUID(str(tool_result["search_id"]))
                if sid not in outcome.literature_search_ids:
                    outcome.literature_search_ids.append(sid)
                if tool_result.get("papers"):
                    successful_searches += 1
                    successful_by_tool[tc["name"]] = (
                        successful_by_tool.get(tc["name"], 0) + 1
                    )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                }
            )

        if cap_tripped:
            forced_text = _forced_answer(
                messages, schemas, metadata, exhausted_system_msg
            )
            if forced_text is not None:
                outcome.final_text = forced_text
                outcome.degraded = False
            else:
                outcome.final_text = None
                outcome.degraded = True
            return outcome

    # Hit max_iters still calling tools: force one no-tools answer turn. Routes
    # through _forced_answer so it gets the same markup guard (+ optional stop
    # message). With exhausted_system_msg=None this is today's plain forced
    # answer with no injected system message.
    forced_text = _forced_answer(messages, schemas, metadata, exhausted_system_msg)
    if forced_text is not None:
        outcome.final_text = forced_text
        return outcome
    outcome.degraded = True
    outcome.final_text = None
    return outcome
