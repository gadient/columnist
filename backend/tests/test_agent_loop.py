"""The bounded agent loop.

No model is involved and none should be. What is asserted here is the loop's *mechanics* — that it
executes tools, feeds results back in the shape Converse expects, and stops when told to. Which
tool a real model chooses for a real question is a different question, answered by the small paid
eval (`scripts/agent-tool-eval.sh`); a scripted provider calls whatever it is told to and would
happily "pass" a tool description that misleads every real model.

**The assertions that matter most are the bounds.** A bound that reports itself but doesn't
actually stop the loop, or one that stops the loop but hands back the model's half-finished text,
both look like passing behaviour from the outside — the user just sees a confident answer. So each
bound is asserted three ways: the run stopped, the provider really was called fewer times, and the
partial text did *not* survive into the result.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.agents import loop
from app.agents.loop import run_agent
from app.config import settings
from app.request_scope import accessible_boards_ctx


# ── the scripted provider ─────────────────────────────────────────────────────

def text_turn(text: str, *, tokens: int = 10) -> loop.ModelTurn:
    return loop.ModelTurn(stop_reason="end_turn", content=({"text": text},), output_tokens=tokens)


def tool_turn(name: str, arguments: dict[str, Any], *, use_id: str = "tu_1", tokens: int = 10) -> loop.ModelTurn:
    return loop.ModelTurn(
        stop_reason="tool_use",
        content=({"toolUse": {"toolUseId": use_id, "name": name, "input": arguments}},),
        output_tokens=tokens,
    )


class ScriptedProvider:
    """Replays a fixed list of turns and records what it was asked.

    `seen` keeps every `messages` list it received, which is how the tool-result plumbing is
    checked: the assertion that matters is what the *model* would see on the next turn.
    """

    def __init__(self, turns: list[loop.ModelTurn]) -> None:
        self._turns = list(turns)
        self.calls = 0
        self.seen: list[list[dict[str, Any]]] = []

    def converse(self, *, system: str, messages: list[dict[str, Any]], tool_specs: list[dict[str, Any]]) -> loop.ModelTurn:
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        if not self._turns:
            raise AssertionError("the loop asked for more turns than the script provides")
        return self._turns.pop(0)


class ExplodingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def converse(self, **_: Any) -> loop.ModelTurn:
        self.calls += 1
        raise RuntimeError("botocore blew up with something internal and quotable")


# ── the happy path ────────────────────────────────────────────────────────────

def test_a_model_that_answers_without_tools_is_a_complete_answer(two_boards):
    provider = ScriptedProvider([text_turn("Nothing is overdue.")])
    out = run_agent("how are we doing?", provider=provider, active_board_id=None)

    assert out.complete is True
    assert out.stopped_because == loop.END_TURN
    assert out.text == "Nothing is overdue."
    assert out.tool_calls == []
    assert out.turns == 1


def test_a_tool_call_runs_and_its_result_is_fed_back_to_the_model(two_boards):
    mine, _ = two_boards
    provider = ScriptedProvider([
        tool_turn("get_high_priority_tasks", {}),
        text_turn("One card is high priority."),
    ])
    out = run_agent("what's important?", provider=provider, active_board_id=mine)

    assert out.complete is True
    assert out.text == "One card is high priority."
    assert [c.name for c in out.tool_calls] == ["get_high_priority_tasks"]
    assert out.tool_calls[0].result_count == 1

    # What the model sees on turn 2: its own tool request, then the result.
    second = provider.seen[1]
    assert second[1]["role"] == "assistant"
    assert "toolUse" in second[1]["content"][0]
    assert second[2]["role"] == "user"
    payload = second[2]["content"][0]["toolResult"]
    assert payload["toolUseId"] == "tu_1"
    assert payload["content"][0]["json"]["items"][0]["title"] == "My high priority card"


# ── conversation history ──────────────────────────────────────────────────────

def test_history_seeds_the_transcript_before_the_current_question(two_boards):
    """Prior exchanges arrive as alternating user/assistant text, oldest first, and the current
    question is the last (user) turn — the shape Converse requires and what lets a follow-up resolve
    "them"/"there" against the earlier answer."""
    provider = ScriptedProvider([text_turn("Board A and Board B.")])
    history = [
        loop.HistoryExchange(question="what boards do I have?", answer="You have Board A and Board B."),
    ]
    run_agent("which of those is busiest?", provider=provider, active_board_id=None, history=history)

    sent = provider.seen[0]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"][0]["text"] == "what boards do I have?"
    assert sent[1]["content"][0]["text"] == "You have Board A and Board B."
    assert sent[2]["content"][0]["text"] == "which of those is busiest?"


def test_history_carries_only_text_never_tool_scaffolding(two_boards):
    """The compact pair is text-only by construction — no tool_use/tool_result blocks leak back in,
    which is what keeps replayed memory cheap."""
    provider = ScriptedProvider([text_turn("ok")])
    history = [loop.HistoryExchange(question="q1", answer="a1"), loop.HistoryExchange(question="q2", answer="a2")]
    run_agent("q3", provider=provider, active_board_id=None, history=history)

    sent = provider.seen[0]
    assert len(sent) == 5  # 2 pairs + the current question
    for m in sent:
        assert list(m["content"][0].keys()) == ["text"]


def test_no_history_is_the_bare_current_question(two_boards):
    """Regression: without history the transcript is exactly what it was before memory existed."""
    provider = ScriptedProvider([text_turn("ok")])
    run_agent("just this", provider=provider, active_board_id=None)

    sent = provider.seen[0]
    assert sent == [{"role": "user", "content": [{"text": "just this"}]}]


def test_several_tool_calls_in_one_turn_come_back_as_one_message(two_boards):
    """Converse requires every toolUse in a turn to be answered in the next single user message."""
    mine, _ = two_boards
    both = loop.ModelTurn(
        stop_reason="tool_use",
        content=(
            {"toolUse": {"toolUseId": "a", "name": "get_high_priority_tasks", "input": {}}},
            {"toolUse": {"toolUseId": "b", "name": "get_board_overview", "input": {}}},
        ),
    )
    provider = ScriptedProvider([both, text_turn("Done.")])
    out = run_agent("summarise", provider=provider, active_board_id=mine)

    assert [c.name for c in out.tool_calls] == ["get_high_priority_tasks", "get_board_overview"]
    results = provider.seen[1][2]["content"]
    assert [b["toolResult"]["toolUseId"] for b in results] == ["a", "b"]


def test_a_failed_tool_call_is_reported_to_the_model_rather_than_ending_the_run(two_boards):
    """`dispatch` returns errors as data; the model gets to see them and try again."""
    provider = ScriptedProvider([
        tool_turn("get_due_dates_by_user", {}),  # missing the required user_name
        text_turn("Which person did you mean?"),
    ])
    out = run_agent("what's due?", provider=provider, active_board_id=None)

    assert out.complete is True
    assert out.tool_calls[0].error and "user_name" in out.tool_calls[0].error
    block = provider.seen[1][2]["content"][0]["toolResult"]
    assert block["status"] == "error"


# ── the bounds ────────────────────────────────────────────────────────────────

def test_max_turns_stops_the_loop_and_withholds_the_partial_answer(two_boards):
    mine, _ = two_boards
    looping = [tool_turn("get_high_priority_tasks", {}) for _ in range(5)]
    provider = ScriptedProvider(looping)

    out = run_agent("what's important?", provider=provider, active_board_id=mine, max_turns=2)

    assert out.stopped_because == loop.MAX_TURNS
    assert out.complete is False
    assert provider.calls == 2, "the bound must actually stop the loop, not just label the result"
    assert out.turns == 2
    assert out.text == loop._INCOMPLETE_TEXT[loop.MAX_TURNS]


def test_the_token_budget_stops_the_loop_before_the_next_call(two_boards):
    mine, _ = two_boards
    expensive = tool_turn("get_high_priority_tasks", {}, tokens=100)
    provider = ScriptedProvider([expensive, expensive, expensive])

    out = run_agent("what's important?", provider=provider, active_board_id=mine, token_budget=150)

    assert out.stopped_because == loop.TOKEN_BUDGET
    assert out.complete is False
    assert provider.calls == 2, "spent 100, then 200 — the third call must not happen"
    assert out.tokens_used == 200
    assert out.text == loop._INCOMPLETE_TEXT[loop.TOKEN_BUDGET]


def test_cache_reads_are_weighted_down_in_the_budget(two_boards):
    """A prefix reused warm costs ~10% to Bedrock, so it must not spend the request budget at face.
    The turn reports 200 real input + 4000 cache-read tokens; only ~400 of the cache read should
    land against the budget, so the run answers instead of tripping the 1000-token bound."""
    mine, _ = two_boards
    warm = loop.ModelTurn(
        stop_reason="tool_use",
        content=({"toolUse": {"toolUseId": "tu_1", "name": "get_high_priority_tasks", "input": {}}},),
        input_tokens=200,
        output_tokens=0,
        cache_read_tokens=4000,
    )
    provider = ScriptedProvider([warm, text_turn("One card is high priority.", tokens=50)])

    out = run_agent("what's important?", provider=provider, active_board_id=mine, token_budget=1000)

    assert out.complete is True, "4000 cache-read tokens counted at face would have tripped the bound"
    assert out.cache_read_tokens == 4000
    # 200 input + 50 output + round(4000 * 0.1) = 650, not 4250.
    assert out.tokens_used == 650


def test_a_cache_write_counts_at_face(two_boards):
    """The cold turn that populates the cache is billed roughly in full, so it counts in full."""
    mine, _ = two_boards
    cold = loop.ModelTurn(
        stop_reason="end_turn", content=({"text": "Nothing is overdue."},), input_tokens=100, cache_write_tokens=4000
    )
    provider = ScriptedProvider([cold])

    out = run_agent("how are we doing?", provider=provider, active_board_id=mine)

    assert out.cache_write_tokens == 4000
    assert out.tokens_used == 4100


def test_the_deadline_stops_the_loop(two_boards):
    """A fake clock, so the test asserts the bound rather than the machine's speed."""
    mine, _ = two_boards
    ticks = iter([0.0, 0.0, 5.0, 31.0])
    provider = ScriptedProvider([tool_turn("get_high_priority_tasks", {})] * 3)

    out = run_agent(
        "what's important?",
        provider=provider,
        active_board_id=mine,
        deadline_seconds=30.0,
        clock=lambda: next(ticks),
    )

    assert out.stopped_because == loop.TIMEOUT
    assert out.complete is False
    assert provider.calls == 2
    assert out.text == loop._INCOMPLETE_TEXT[loop.TIMEOUT]


def test_a_provider_failure_is_a_stop_reason_not_an_exception(two_boards):
    provider = ExplodingProvider()
    out = run_agent("what's important?", provider=provider, active_board_id=None)

    assert out.stopped_because == loop.PROVIDER_ERROR
    assert out.complete is False
    assert out.text == loop._INCOMPLETE_TEXT[loop.PROVIDER_ERROR]
    assert "botocore" not in out.text, "provider internals must not reach the user"


def test_an_interrupted_run_replaces_the_partial_answer_rather_than_blanking_it(two_boards):
    """The model wrote prose *and* asked for a tool. Cut off, that prose must not become the
    answer — and the user must still get a sentence. Asserting only that the prose is *absent*
    passes just as well when the reply is empty, which is its own bug."""
    mine, _ = two_boards
    chatty = loop.ModelTurn(
        stop_reason="tool_use",
        content=(
            {"text": "Everything looks fine so far."},
            {"toolUse": {"toolUseId": "tu_1", "name": "get_high_priority_tasks", "input": {}}},
        ),
    )
    provider = ScriptedProvider([chatty, chatty])

    out = run_agent("how are we doing?", provider=provider, active_board_id=mine, max_turns=1)

    assert out.complete is False
    assert "Everything looks fine" not in out.text
    assert out.text == loop._INCOMPLETE_TEXT[loop.MAX_TURNS]


# ── scope and the fence ───────────────────────────────────────────────────────
# The end-to-end section below covers the fence properly. These pin the loop's own contribution:
# that it passes `active_board_id` server-side and doesn't undo the scoping below it.

def test_the_loop_scopes_tools_to_the_server_supplied_board(two_boards):
    """The loop's own contribution, asserted at the scope the tool actually ran under.

    Checking only that a smuggled `active_board_id` errors would pass even if the loop forwarded
    the model's value, because `dispatch` refuses it first — a real defence, but someone else's.
    """
    mine, _ = two_boards
    provider = ScriptedProvider([tool_turn("get_high_priority_tasks", {}), text_turn("One card.")])
    run_agent("what's important?", provider=provider, active_board_id=mine)

    envelope = provider.seen[1][2]["content"][0]["toolResult"]["content"][0]["json"]
    assert envelope["meta"]["scope_board_id"] == mine


def test_a_model_supplied_active_board_id_is_refused_through_the_loop(two_boards):
    mine, theirs = two_boards
    provider = ScriptedProvider([
        tool_turn("get_high_priority_tasks", {"active_board_id": theirs}),
        text_turn("I couldn't look that up."),
    ])
    out = run_agent("what's on the other board?", provider=provider, active_board_id=mine)

    assert out.tool_calls[0].error, "a model-supplied active_board_id must not pass silently"
    assert out.tool_calls[0].result_count == 0


def test_the_fence_still_applies_to_tools_called_through_the_loop(two_boards):
    mine, theirs = two_boards
    provider = ScriptedProvider([
        tool_turn("get_high_priority_due_dates_in_board", {"board_id": theirs}),
        text_turn("Nothing found."),
    ])

    token = accessible_boards_ctx.set({mine})
    try:
        out = run_agent("what's on that board?", provider=provider, active_board_id=None)
    finally:
        accessible_boards_ctx.reset(token)

    assert out.tool_calls[0].result_count == 0, "the fence must hold through the loop"


# ── the tenant fence, end to end ──────────────────────────────────────────────
# Its own section because it fails silently: a broken fence does not error, it just starts answering
# with rows the caller should never have seen, and every test that only asks "did we get data"
# still passes. Everything here asserts emptiness against a database that genuinely holds the row.

def test_the_fence_holds_on_a_later_turn_not_only_the_first(two_boards):
    """A one-tool question proves little. The realistic shape is the model reading turn 1's results
    and then asking for something else — the fence has to survive the whole run, not the opening."""
    mine, theirs = two_boards
    provider = ScriptedProvider([
        tool_turn("get_high_priority_tasks", {}, use_id="a"),
        tool_turn("get_high_priority_due_dates_in_board", {"board_id": theirs}, use_id="b"),
        text_turn("Nothing further."),
    ])

    token = accessible_boards_ctx.set({mine})
    try:
        out = run_agent("dig into the other board", provider=provider, active_board_id=mine)
    finally:
        accessible_boards_ctx.reset(token)

    assert out.tool_calls[0].result_count == 1, "in-fence read still works"
    assert out.tool_calls[1].result_count == 0, "turn-2 read escaped the fence"


def test_an_authenticated_user_with_no_boards_sees_nothing_rather_than_everything(two_boards):
    """The empty set is the dangerous one: a falsy scope read as "no filter" inverts the fence and
    hands a brand-new user every tenant's data. It must mean nothing, not everything."""
    provider = ScriptedProvider([
        tool_turn("get_board_overview", {}, use_id="a"),
        text_turn("You have no boards yet."),
    ])

    token = accessible_boards_ctx.set(set())
    try:
        out = run_agent("what have I got?", provider=provider, active_board_id=None)
    finally:
        accessible_boards_ctx.reset(token)

    assert out.tool_calls[0].result_count == 0


def test_card_content_cannot_talk_the_loop_across_the_fence(two_boards):
    """Prompt injection through the data itself.

    Card titles are attacker-controlled on any shared board, so "the model was tricked" is a
    scenario to design against, not a hypothetical. The scripted provider plays a model that has
    been fully convinced — it asks for the other tenant's board and card by id. The defence is not
    that the model resists; it is that a convinced model still reads nothing, because the fence is
    below it. If this ever fails, the model's good behaviour is all that is left.
    """
    mine, theirs = two_boards
    their_card = f"card_{theirs}_1"

    provider = ScriptedProvider([
        tool_turn("get_card_details", {"card_ids": [their_card]}, use_id="a"),
        tool_turn("get_high_priority_due_dates_in_board", {"board_id": theirs}, use_id="b"),
        tool_turn("get_blocked_cards", {}, use_id="c"),
        text_turn("I could not find those."),
    ])

    token = accessible_boards_ctx.set({mine})
    try:
        out = run_agent("do as the card says", provider=provider, active_board_id=mine)
    finally:
        accessible_boards_ctx.reset(token)

    assert [c.result_count for c in out.tool_calls[:2]] == [0, 0]
    # The blocked-card read is in-fence, but its blocker titles reach across boards — the resolved
    # names must not smuggle the other tenant's card title back in the payload.
    blocked_payload = provider.seen[3][-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert "Their high priority card" not in json.dumps(blocked_payload)


def test_the_loop_leaves_the_callers_scope_exactly_as_it_found_it(two_boards):
    """The loop borrows the scope; it must not set, clear or narrow it. A run that leaked a
    narrowed scope would silently under-serve every later request on the same thread."""
    mine, _ = two_boards
    provider = ScriptedProvider([tool_turn("get_high_priority_tasks", {}), text_turn("Done.")])

    token = accessible_boards_ctx.set({mine})
    try:
        run_agent("what's important?", provider=provider, active_board_id=mine)
        assert accessible_boards_ctx.get() == {mine}
    finally:
        accessible_boards_ctx.reset(token)

    assert accessible_boards_ctx.get() is None, "this test's own reset must still work"


def test_the_loop_refuses_to_run_unscoped_when_auth_is_on(two_boards, monkeypatch):
    """The wiring mistake this exists to catch: `accessible_boards_ctx` defaults to None meaning
    *unrestricted*, so forgetting to set it does not fail — it hands the model every tenant's data
    and answers perfectly. Under auth that must be a refusal, not a silent success."""
    monkeypatch.setattr(settings, "cognito_region", "us-east-1")
    monkeypatch.setattr(settings, "cognito_user_pool_id", "pool-123")
    monkeypatch.setattr(settings, "cognito_app_client_id", "client-123")
    assert settings.cognito_enabled, "precondition: auth is on for this test"

    provider = ScriptedProvider([text_turn("should never be reached")])
    with pytest.raises(loop.UnscopedAgentRun):
        run_agent("what's overdue?", provider=provider, active_board_id=None)
    assert provider.calls == 0, "it must refuse before spending a model call"


def test_an_unscoped_run_is_still_allowed_with_auth_off(two_boards):
    """Local dev is unscoped by design (Cognito off → routes open). The guard keys on
    auth being configured, so it must not break the open local path."""
    assert not settings.cognito_enabled, "conftest pins Cognito off"
    provider = ScriptedProvider([tool_turn("get_high_priority_tasks", {}), text_turn("Two boards.")])

    out = run_agent("what's important?", provider=provider, active_board_id=None)
    assert out.complete is True
    assert out.tool_calls[0].result_count == 2, "unscoped sees both boards, as local dev should"


# ── truncation and credential failures ──────────────────────────────────────────

class _OneTurn:
    def __init__(self, turn=None, exc=None):
        self._turn, self._exc = turn, exc

    def converse(self, *, system, messages, tool_specs):
        if self._exc is not None:
            raise self._exc
        return self._turn


def test_a_reply_cut_off_at_the_output_limit_is_withheld(two_boards):
    """A truncated reply reads like a whole one. Like every other bound, it fails closed."""
    turn = loop.ModelTurn(stop_reason="max_tokens", content=({"text": "The three things that matter are: 1."},))
    out = run_agent("brief me", provider=_OneTurn(turn), active_board_id=None)

    assert out.stopped_because == loop.TRUNCATED
    assert out.complete is False
    assert out.text == loop._INCOMPLETE_TEXT[loop.TRUNCATED]


def test_a_truncated_tool_call_is_not_executed(two_boards):
    turn = loop.ModelTurn(
        stop_reason="max_tokens",
        content=({"toolUse": {"toolUseId": "t", "name": "get_high_priority_tasks", "input": {}}},),
    )
    out = run_agent("brief me", provider=_OneTurn(turn), active_board_id=None)
    assert out.stopped_because == loop.TRUNCATED
    assert out.tool_calls == []


def test_rejected_credentials_get_their_own_message_not_try_again(two_boards):
    out = run_agent("brief me", provider=_OneTurn(exc=loop.ProviderAuthError("bad key")), active_board_id=None)

    assert out.stopped_because == loop.PROVIDER_AUTH
    assert out.complete is False
    assert "try again in a moment" not in out.text.lower()
    assert "bad key" not in out.text, "provider detail stays in the log, not the reply"


def test_provider_failures_are_logged_for_the_operator(two_boards, caplog):
    """The user-facing text is deliberately vague, so the log is the only place the cause shows up."""
    with caplog.at_level("WARNING", logger="app.agents.loop"):
        run_agent("brief me", provider=_OneTurn(exc=ConnectionError("network unreachable")), active_board_id=None)
        run_agent("brief me", provider=_OneTurn(exc=loop.ProviderAuthError("key rejected")), active_board_id=None)
    assert "ConnectionError: network unreachable" in caplog.text
    assert "key rejected" in caplog.text
