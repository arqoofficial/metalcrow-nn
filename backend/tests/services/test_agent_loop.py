import uuid

import pytest

from app.services import llm
from app.services.agent import loop as agent_loop
from app.services.agent.loop import LoopOutcome, Tool, run_loop


def _echo_tool(name: str, result: dict) -> Tool:
    def handler(session, chat_session_id, **kwargs):  # noqa: ANN001, ANN003
        return result

    return Tool(
        name=name,
        schema={"type": "function", "function": {"name": name, "parameters": {}}},
        handler=handler,
    )


class _ScriptedChat:
    """Returns queued ChatResults in order; records each call's tool_choice +
    metadata for assertions."""

    def __init__(self, results: list[llm.ChatResult]) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    def __call__(self, messages, *, tools=None, tool_choice=None,
                 temperature=0.2, metadata=None):  # noqa: ANN001
        self.calls.append(
            {"messages": list(messages), "tools": tools,
             "tool_choice": tool_choice, "metadata": metadata}
        )
        # Repeat the LAST queued result once the queue is down to one, so the
        # forced-answer retry (`_forced_answer` calls llm.chat up to
        # _FORCED_ANSWER_ATTEMPTS times) gets the same scripted result each
        # attempt instead of exhausting the queue.
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0]


def test_run_loop_returns_text_when_no_tool_calls(monkeypatch):
    scripted = _ScriptedChat([llm.ChatResult(content="прямой ответ", ok=True)])
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(None, uuid.uuid4(), [{"role": "user", "content": "hi"}], [], max_iters=4)

    assert isinstance(outcome, LoopOutcome)
    assert outcome.final_text == "прямой ответ"
    assert outcome.degraded is False
    assert outcome.tool_calls_made == []


def test_run_loop_executes_tool_then_returns_final_text(monkeypatch):
    sid = uuid.uuid4()
    search_tool = _echo_tool("litsearch_search", {"search_id": str(sid), "papers": []})
    scripted = _ScriptedChat(
        [
            llm.ChatResult(
                content=None,
                tool_calls=[{"id": "c1", "name": "litsearch_search", "arguments": {"query": "x"}}],
                ok=True,
            ),
            llm.ChatResult(content="ответ по аннотациям", ok=True),
        ]
    )
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(None, uuid.uuid4(), [{"role": "user", "content": "q"}], [search_tool], max_iters=4)

    assert outcome.final_text == "ответ по аннотациям"
    assert outcome.tool_calls_made == ["litsearch_search"]
    assert outcome.literature_search_id == sid
    # role:tool message was appended between the two llm.chat calls
    second_call_msgs = scripted.calls[1]["messages"]
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in second_call_msgs)
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in second_call_msgs)


def test_run_loop_first_tool_choice_only_on_iter0(monkeypatch):
    search_tool = _echo_tool("litsearch_search", {"search_id": str(uuid.uuid4())})
    scripted = _ScriptedChat(
        [
            llm.ChatResult(
                content=None,
                tool_calls=[{"id": "c1", "name": "litsearch_search", "arguments": {}}],
                ok=True,
            ),
            llm.ChatResult(content="done", ok=True),
        ]
    )
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    run_loop(None, uuid.uuid4(), [{"role": "user", "content": "q"}], [search_tool],
             max_iters=4, first_tool_choice="litsearch_search")

    assert scripted.calls[0]["tool_choice"] == {
        "type": "function", "function": {"name": "litsearch_search"}
    }
    assert scripted.calls[1]["tool_choice"] == "auto"


def test_run_loop_threads_session_metadata(monkeypatch):
    scripted = _ScriptedChat([llm.ChatResult(content="ok", ok=True)])
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)
    csid = uuid.uuid4()

    run_loop(None, csid, [{"role": "user", "content": "hi"}], [], max_iters=4)

    assert scripted.calls[0]["metadata"] == {"session_id": str(csid)}


def test_run_loop_forces_final_answer_at_max_iters(monkeypatch):
    tool = _echo_tool("t", {"ok": True})
    # Always returns a tool call; loop must stop at max_iters and force a
    # tool_choice="none" answer turn.
    loop_call = llm.ChatResult(
        content=None, tool_calls=[{"id": "c", "name": "t", "arguments": {}}], ok=True
    )
    scripted = _ScriptedChat([loop_call, loop_call, llm.ChatResult(content="forced final", ok=True)])
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(None, uuid.uuid4(), [{"role": "user", "content": "q"}], [tool], max_iters=2)

    assert outcome.final_text == "forced final"
    assert outcome.degraded is False
    assert scripted.calls[-1]["tool_choice"] is None  # forced call: tools=None, tool_choice=None


def test_run_loop_degraded_when_content_is_blank_string(monkeypatch):
    """M2: an empty (or whitespace-only) string is not a real answer either —
    it must degrade the same as `content is None`, not get persisted as an
    empty "answer"."""
    scripted = _ScriptedChat([llm.ChatResult(content="", ok=True)])
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(None, uuid.uuid4(), [{"role": "user", "content": "q"}], [], max_iters=4)

    assert outcome.degraded is True
    assert outcome.final_text is None


def test_run_loop_degraded_when_content_is_whitespace_only(monkeypatch):
    scripted = _ScriptedChat([llm.ChatResult(content="   \n\t", ok=True)])
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(None, uuid.uuid4(), [{"role": "user", "content": "q"}], [], max_iters=4)

    assert outcome.degraded is True
    assert outcome.final_text is None


def test_run_loop_degraded_on_transport_failure(monkeypatch):
    scripted = _ScriptedChat([llm.ChatResult(content=None, tool_calls=[], ok=False)])
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(None, uuid.uuid4(), [{"role": "user", "content": "q"}], [], max_iters=4)

    assert outcome.degraded is True
    assert outcome.final_text is None


def test_run_loop_degraded_when_forced_turn_yields_no_text(monkeypatch):
    tool = _echo_tool("t", {"ok": True})
    loop_call = llm.ChatResult(
        content=None, tool_calls=[{"id": "c", "name": "t", "arguments": {}}], ok=True
    )
    # forced final turn also returns no text -> degraded
    scripted = _ScriptedChat([loop_call, llm.ChatResult(content=None, tool_calls=[], ok=True)])
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(None, uuid.uuid4(), [{"role": "user", "content": "q"}], [tool], max_iters=1)

    assert outcome.degraded is True
    assert outcome.final_text is None


def _counting_tool(name: str, result: dict) -> tuple[Tool, list[int]]:
    """Like _echo_tool but records how many times the handler ran (in calls[0])."""
    calls = [0]

    def handler(session, chat_session_id, **kwargs):  # noqa: ANN001, ANN003
        calls[0] += 1
        return result

    return (
        Tool(
            name=name,
            schema={"type": "function", "function": {"name": name, "parameters": {}}},
            handler=handler,
        ),
        calls,
    )


def test_run_loop_caps_tool_calls_then_forces_abstract_answer(monkeypatch):
    """With max_tool_calls=3, a model that keeps calling litsearch_search must
    be stopped after exactly 3 executed calls, a system stop-message appended,
    and the forced tool_choice="none" reply returned as final_text."""
    tool, calls = _counting_tool("litsearch_search", {"search_id": str(uuid.uuid4())})
    search_call = llm.ChatResult(
        content=None,
        tool_calls=[{"id": "c", "name": "litsearch_search", "arguments": {"query": "x"}}],
        ok=True,
    )

    class _AlwaysSearchesThenForced:
        """Returns a tool call for every tool-enabled turn; the forced
        (tool_choice=="none") turn returns clean prose."""

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def __call__(self, messages, *, tools=None, tool_choice=None,
                     temperature=0.2, metadata=None):  # noqa: ANN001
            self.calls.append({"messages": list(messages), "tool_choice": tool_choice})
            if tools is None:  # forced no-tools answer call
                return llm.ChatResult(content="ответ по аннотациям", ok=True)
            return search_call

    scripted = _AlwaysSearchesThenForced()
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    exhausted = "Достигнут лимит. Ответь по аннотациям."
    messages = [{"role": "user", "content": "q"}]
    outcome = run_loop(
        None,
        uuid.uuid4(),
        messages,
        [tool],
        max_iters=10,
        max_tool_calls=3,
        exhausted_system_msg=exhausted,
    )

    assert calls[0] == 3  # handler executed exactly 3 times
    assert outcome.final_text == "ответ по аннотациям"
    assert outcome.degraded is False
    assert any(
        m.get("role") == "system" and m.get("content") == exhausted for m in messages
    )
    assert scripted.calls[-1]["tool_choice"] is None  # forced call: tools=None, tool_choice=None


def test_run_loop_collects_all_search_ids_in_call_order(monkeypatch):
    """Two distinct litsearch_search calls in one turn must both land in
    `literature_search_ids`, in call order — not just the last one."""
    id1, id2 = uuid.uuid4(), uuid.uuid4()

    class _TwoSearchesThenAnswer:
        def __init__(self) -> None:
            self._remaining = 2

        def __call__(self, messages, *, tools=None, tool_choice=None,
                     temperature=0.2, metadata=None):  # noqa: ANN001
            if self._remaining:
                self._remaining -= 1
                return llm.ChatResult(
                    content=None,
                    tool_calls=[
                        {"id": "c", "name": "litsearch_search", "arguments": {"query": "x"}}
                    ],
                    ok=True,
                )
            return llm.ChatResult(content="ответ по обеим выдачам", ok=True)

    scripted = _TwoSearchesThenAnswer()
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    ids_to_return = [id1, id2]

    def handler(session, chat_session_id, **kwargs):  # noqa: ANN001, ANN003
        return {"search_id": str(ids_to_return.pop(0)), "papers": []}

    search_tool = Tool(
        name="litsearch_search",
        schema={"type": "function", "function": {"name": "litsearch_search", "parameters": {}}},
        handler=handler,
    )

    outcome = run_loop(
        None, uuid.uuid4(), [{"role": "user", "content": "q"}], [search_tool], max_iters=10
    )

    assert outcome.literature_search_ids == [id1, id2]
    assert outcome.literature_search_id == id1  # back-compat alias == anchor
    assert outcome.final_text == "ответ по обеим выдачам"


def test_run_loop_max_successful_searches_ignores_empty_searches(monkeypatch):
    """Empty (no-papers) searches must NOT count toward
    max_successful_searches, only searches that returned >=1 paper do. With
    max_successful_searches=2, the loop must force an answer only after the
    2nd *papered* search, even if empty searches ran in between."""
    sids = [uuid.uuid4() for _ in range(4)]
    # papered, empty, papered, (would-be 3rd papered, must not be reached)
    results_by_call = [
        {"search_id": str(sids[0]), "papers": [{"doi": "d1"}]},
        {"search_id": str(sids[1]), "papers": []},
        {"search_id": str(sids[2]), "papers": [{"doi": "d2"}]},
        {"search_id": str(sids[3]), "papers": [{"doi": "d3"}]},
    ]
    call_count = [0]

    def handler(session, chat_session_id, **kwargs):  # noqa: ANN001, ANN003
        result = results_by_call[call_count[0]]
        call_count[0] += 1
        return result

    search_tool = Tool(
        name="litsearch_search",
        schema={"type": "function", "function": {"name": "litsearch_search", "parameters": {}}},
        handler=handler,
    )

    search_call = llm.ChatResult(
        content=None,
        tool_calls=[{"id": "c", "name": "litsearch_search", "arguments": {"query": "x"}}],
        ok=True,
    )

    class _AlwaysSearchesThenForced:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def __call__(self, messages, *, tools=None, tool_choice=None,
                     temperature=0.2, metadata=None):  # noqa: ANN001
            self.calls.append({"tool_choice": tool_choice})
            if tools is None:  # forced no-tools answer call
                return llm.ChatResult(content="ответ по аннотациям", ok=True)
            return search_call

    scripted = _AlwaysSearchesThenForced()
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(
        None,
        uuid.uuid4(),
        [{"role": "user", "content": "q"}],
        [search_tool],
        max_iters=10,
        max_successful_searches=2,
    )

    # Exactly 3 searches executed: papered, empty, papered — loop stops right
    # after the 2nd successful (papered) search, the empty one didn't count.
    assert call_count[0] == 3
    assert outcome.literature_search_ids == [sids[0], sids[1], sids[2]]
    assert outcome.final_text == "ответ по аннотациям"
    assert outcome.degraded is False


def test_run_loop_stops_at_per_tool_cap_within_single_parallel_response(monkeypatch):
    """Overshoot-bug pin: the model emits 5 PARALLEL literature_search_en
    tool-calls in a SINGLE response. With max_successful_by_tool capping
    literature_search_en at 3, only 3 may execute — the other 2 (in the same
    response) must be skipped (each still gets a matching role:"tool" note,
    since every tool_call_id must be answered), and the turn must end in the
    forced answer, not a 4th/5th search."""
    tool, calls = _counting_tool(
        "literature_search_en", {"search_id": str(uuid.uuid4()), "papers": [{"doi": "d"}]}
    )
    parallel_calls = [
        {"id": f"c{i}", "name": "literature_search_en", "arguments": {"query": f"q{i}"}}
        for i in range(5)
    ]
    search_call = llm.ChatResult(content=None, tool_calls=parallel_calls, ok=True)

    class _OneParallelBatchThenForced:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def __call__(self, messages, *, tools=None, tool_choice=None,
                     temperature=0.2, metadata=None):  # noqa: ANN001
            self.calls.append({"messages": list(messages), "tool_choice": tool_choice})
            if tools is None:  # forced no-tools answer call
                return llm.ChatResult(content="ответ по аннотациям", ok=True)
            return search_call

    scripted = _OneParallelBatchThenForced()
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    messages = [{"role": "user", "content": "q"}]
    outcome = run_loop(
        None,
        uuid.uuid4(),
        messages,
        [tool],
        max_iters=10,
        max_successful_by_tool={"literature_search_en": 3},
        exhausted_system_msg="Достигнут лимит. Ответь по аннотациям.",
    )

    assert calls[0] == 3  # handler executed exactly 3 times, not 5
    assert outcome.final_text == "ответ по аннотациям"
    assert outcome.degraded is False
    assert outcome.tool_calls_made == ["literature_search_en"] * 3
    # Every one of the 5 tool_call_ids from the single response got a
    # matching role:"tool" message (3 real results + 2 cap-tripped notes) —
    # otherwise the next request to the gateway would error.
    tool_msgs_by_id = {
        m["tool_call_id"]: m for m in messages if m.get("role") == "tool"
    }
    assert set(tool_msgs_by_id) == {f"c{i}" for i in range(5)}
    assert "Лимит поиска исчерпан" in tool_msgs_by_id["c3"]["content"]
    assert "Лимит поиска исчерпан" in tool_msgs_by_id["c4"]["content"]


def test_run_loop_en_ru_caps_are_independent(monkeypatch):
    """EN and RU per-tool caps must not share a budget: a single response
    with 3 literature_search_en + 3 literature_search_ru calls (each capped
    at 3) must execute ALL 6 — one tool reaching its cap must not block the
    other."""
    en_tool, en_calls = _counting_tool(
        "literature_search_en", {"search_id": str(uuid.uuid4()), "papers": [{"doi": "d"}]}
    )
    ru_tool, ru_calls = _counting_tool(
        "literature_search_ru", {"search_id": str(uuid.uuid4()), "papers": [{"doi": None}]}
    )
    parallel_calls = [
        {"id": f"en{i}", "name": "literature_search_en", "arguments": {"query": f"q{i}"}}
        for i in range(3)
    ] + [
        {"id": f"ru{i}", "name": "literature_search_ru", "arguments": {"query": f"q{i}"}}
        for i in range(3)
    ]
    search_call = llm.ChatResult(content=None, tool_calls=parallel_calls, ok=True)

    class _OneBatchThenForced:
        def __call__(self, messages, *, tools=None, tool_choice=None,
                     temperature=0.2, metadata=None):  # noqa: ANN001
            if tools is None:
                return llm.ChatResult(content="ответ по обеим выдачам", ok=True)
            return search_call

    monkeypatch.setattr(agent_loop.llm, "chat", _OneBatchThenForced())

    outcome = run_loop(
        None,
        uuid.uuid4(),
        [{"role": "user", "content": "q"}],
        [en_tool, ru_tool],
        max_iters=10,
        max_successful_by_tool={"literature_search_en": 3, "literature_search_ru": 3},
        exhausted_system_msg="Достигнут лимит. Ответь по аннотациям.",
    )

    assert en_calls[0] == 3
    assert ru_calls[0] == 3
    assert outcome.final_text == "ответ по обеим выдачам"
    assert outcome.degraded is False


def test_run_loop_leaked_markup_text_forces_clean_reply(monkeypatch):
    """DeepSeek leaked its tool call as TEXT (native markup): run_loop must not
    return the markup — with an exhausted_system_msg it re-invokes
    tool_choice="none" and returns the clean prose instead."""
    markup = "<｜DSML｜tool_calls><｜DSML｜invoke name=\"litsearch_search\">"
    scripted = _ScriptedChat(
        [
            llm.ChatResult(content=markup, ok=True),
            llm.ChatResult(content="чистый ответ по аннотациям", ok=True),
        ]
    )
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(
        None,
        uuid.uuid4(),
        [{"role": "user", "content": "q"}],
        [],
        max_iters=4,
        exhausted_system_msg="Ответь по аннотациям.",
    )

    assert outcome.final_text == "чистый ответ по аннотациям"
    assert outcome.final_text != markup
    assert outcome.degraded is False
    assert scripted.calls[-1]["tool_choice"] is None  # forced call: tools=None, tool_choice=None


def test_run_loop_markup_only_degrades_when_no_exhausted_msg(monkeypatch):
    """A markup-only reply whose forced turn is also markup yields
    degraded=True, final_text=None (no exhausted_system_msg injected)."""
    markup = "<｜DSML｜tool_calls>leak"
    scripted = _ScriptedChat(
        [
            llm.ChatResult(content=markup, ok=True),
            llm.ChatResult(content=markup, ok=True),
        ]
    )
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(
        None,
        uuid.uuid4(),
        [{"role": "user", "content": "q"}],
        [],
        max_iters=4,
    )

    assert outcome.degraded is True
    assert outcome.final_text is None
