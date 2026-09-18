"""Chat-agent tool schemas and dispatch.

No model is involved here and none should be: what is asserted is the contract the agent loop will
depend on, which is ours to keep regardless of which model sits on top.

**The property that matters most** is the last group: a tool call carrying an id the caller cannot
access must return nothing. Model-supplied identifiers (`card_ids`, `board_id`) are not trustworthy
— a model can hallucinate one, and a note or a message can talk it into naming one — so the fence
is `accessible_boards_ctx`, not the model's good behaviour. If that ever breaks it breaks *silently*:
the chat simply starts answering with rows it should not have, and every test that only checks
"does the tool return data" still passes. Hence tests that assert emptiness against a database that
genuinely contains the row.
"""
from __future__ import annotations

from app.agents import tools
from app.db import get_connection
from app.request_scope import accessible_boards_ctx

# `two_boards` lives in conftest.py — the loop tests need the same two-board setup.


# ── schema shape ──────────────────────────────────────────────────────────────

def test_every_spec_is_valid_bedrock_toolspec():
    assert tools.TOOL_SPECS
    for spec in tools.TOOL_SPECS:
        ts = spec["toolSpec"]
        assert ts["name"] and ts["description"]
        schema = ts["inputSchema"]["json"]
        assert schema["type"] == "object"
        for name in schema.get("required", []):
            assert name in schema["properties"], f"{ts['name']}: required '{name}' is not a property"


def test_every_declared_tool_can_actually_be_dispatched():
    """A spec with no dispatch entry is a tool the model will call and always fail."""
    assert tools.TOOL_NAMES == set(tools.DISPATCH)


def test_no_schema_exposes_a_server_supplied_parameter():
    """`active_board_id` must not be expressible by the model — it decides what it can see."""
    for spec in tools.TOOL_SPECS:
        props = set(spec["toolSpec"]["inputSchema"]["json"]["properties"])
        leaked = props & tools.SERVER_SUPPLIED
        assert not leaked, f"{spec['toolSpec']['name']} exposes {leaked}"


# ── dispatch behaviour ────────────────────────────────────────────────────────

def test_an_unknown_tool_is_an_error_result_not_an_exception(two_boards):
    out = tools.dispatch("drop_all_boards", {}, active_board_id=None)
    assert "error" in out and "available" in out


def test_a_missing_required_argument_is_reported_back(two_boards):
    out = tools.dispatch("get_due_dates_by_user", {}, active_board_id=None)
    assert "error" in out and "user_name" in out["error"]


def test_a_model_supplied_active_board_id_is_refused(two_boards):
    """Scope-widening should not even be expressible — and must not pass silently."""
    mine, theirs = two_boards
    out = tools.dispatch("get_high_priority_tasks", {"active_board_id": theirs}, active_board_id=mine)
    assert "error" in out
    assert "server" in out["error"].lower()
    assert "items" not in out


def test_a_successful_call_returns_a_meta_envelope(two_boards):
    mine, _ = two_boards
    out = tools.dispatch("get_high_priority_tasks", {}, active_board_id=mine)
    assert out["meta"]["tool"] == "get_high_priority_tasks"
    assert out["meta"]["scope_board_id"] == mine
    assert out["meta"]["count"] == len(out["items"]) == 1
    assert out["items"][0]["title"] == "My high priority card"


# ── the tenant fence ──────────────────────────────────────────────────────────

def test_the_fence_hides_a_board_the_caller_cannot_access(two_boards):
    """Unscoped, both boards are visible; fenced to one, the other disappears — from the same DB."""
    mine, theirs = two_boards

    unscoped = tools.dispatch("get_board_overview", {}, active_board_id=None)
    assert {b["board_title"] for b in unscoped["items"]} == {"Mine", "Theirs"}

    token = accessible_boards_ctx.set({mine})
    try:
        fenced = tools.dispatch("get_board_overview", {}, active_board_id=None)
    finally:
        accessible_boards_ctx.reset(token)

    assert {b["board_title"] for b in fenced["items"]} == {"Mine"}, "the fence must exclude other boards"


def test_a_model_supplied_board_id_cannot_escape_the_fence(two_boards):
    """The model naming someone else's board id explicitly — the id is real, the data is not theirs."""
    mine, theirs = two_boards

    token = accessible_boards_ctx.set({mine})
    try:
        out = tools.dispatch(
            "get_high_priority_due_dates_in_board", {"board_id": theirs}, active_board_id=None
        )
    finally:
        accessible_boards_ctx.reset(token)

    assert out["items"] == [], "a board id outside the fence must return nothing"


def test_a_model_supplied_card_id_cannot_escape_the_fence(two_boards):
    """Same for card ids, which the model gets to invent freely."""
    mine, theirs = two_boards
    their_card = f"card_{theirs}_1"

    unfenced = tools.dispatch("get_card_details", {"card_ids": [their_card]}, active_board_id=None)
    assert len(unfenced["items"]) == 1, "precondition: the card really does exist"

    token = accessible_boards_ctx.set({mine})
    try:
        out = tools.dispatch("get_card_details", {"card_ids": [their_card]}, active_board_id=None)
    finally:
        accessible_boards_ctx.reset(token)

    assert out["items"] == [], "a card id outside the fence must return nothing"


# ── analytics tools ───────────────────────────────────────────────────────────
# Without these, the model answers "what's at risk", "who has the most on" and "how fast are we
# moving" from the due-date tools — confidently, and from the wrong data. A live eval measured it.

def test_blocked_cards_names_the_work_that_is_blocking(two_boards):
    mine, _ = two_boards
    out = tools.dispatch("get_blocked_cards", {}, active_board_id=mine)

    assert out["meta"]["count"] == 1
    card = out["items"][0]
    assert card["title"] == "My blocked card"
    # An id is useless in an answer — the blocker has to come back named.
    assert "My high priority card" in card["blocked_by"]


def test_a_blocker_on_an_unreachable_board_is_not_named(two_boards):
    """The leak this tool introduces: resolving blocker ids to titles is a second read, and the
    blocker may live on a board the caller cannot see. The card must still be reported as blocked —
    hiding the blockage would be its own lie — but the other tenant's card title must not appear."""
    mine, theirs = two_boards

    token = accessible_boards_ctx.set({mine})
    try:
        out = tools.dispatch("get_blocked_cards", {}, active_board_id=None)
    finally:
        accessible_boards_ctx.reset(token)

    card = out["items"][0]
    assert card["title"] == "My blocked card"
    assert "My high priority card" in card["blocked_by"], "the in-fence blocker is still named"
    assert "Their high priority card" not in card["blocked_by"], "cross-tenant blocker title leaked"
    assert len(card["blocked_by"]) == 2, "the blockage itself must not be silently dropped"


def test_workload_counts_open_work_and_ignores_finished_work(two_boards):
    mine, _ = two_boards
    out = tools.dispatch("get_team_workload", {}, active_board_id=mine)

    assert out["meta"]["count"] == 1
    row = out["items"][0]
    assert row["member_name"] == "Priya Sharma"
    # Three assigned cards, one completed → two open, 3+3 points. The completed card's 5 points
    # must not appear: "on their plate" means what is left.
    assert row["open_cards"] == 2
    assert row["open_story_points"] == 6


def test_velocity_reports_finished_work_not_a_completion_ratio(two_boards):
    mine, _ = two_boards
    out = tools.dispatch("get_velocity", {}, active_board_id=mine)

    assert out["meta"]["count"] == 1
    row = out["items"][0]
    assert row["completed_cards"] == 1
    assert row["completed_story_points"] == 5
    assert row["window_days"] == 14


def test_velocity_excludes_work_finished_before_the_window(two_boards):
    """Otherwise it is a lifetime total wearing a rate's name — the exact confusion that made
    get_board_overview the wrong answer to "how fast are we moving"."""
    mine, _ = two_boards
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE cards SET completed_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00Z", f"card_{mine}_3"),
        )
        conn.commit()
    finally:
        conn.close()

    assert tools.dispatch("get_velocity", {}, active_board_id=mine)["items"] == []
    wide = tools.dispatch("get_velocity", {"days": 365}, active_board_id=mine)["items"]
    assert wide == [], "still outside a 365-day window"


def test_the_fence_applies_to_every_analytics_tool(two_boards):
    """Each new tool is a new query, and a fence is only as good as its least-covered read."""
    mine, theirs = two_boards

    for tool_name in ("get_blocked_cards", "get_team_workload", "get_velocity"):
        unscoped = tools.dispatch(tool_name, {}, active_board_id=None)
        boards_seen = {i.get("board_title") for i in unscoped["items"]}
        assert boards_seen == {"Mine", "Theirs"}, f"{tool_name}: precondition — both boards visible"

        token = accessible_boards_ctx.set({mine})
        try:
            fenced = tools.dispatch(tool_name, {}, active_board_id=None)
        finally:
            accessible_boards_ctx.reset(token)

        assert {i.get("board_title") for i in fenced["items"]} == {"Mine"}, f"{tool_name}: fence leaked"


# ── column / status tools ─────────────────────────────────────────────────────
# Without these, the count-only tools cannot answer "what's in Review" or "how is the board laid
# out" — a card's column (its workflow status) is invisible to chat.

def test_column_breakdown_counts_open_and_completed_per_column(two_boards):
    mine, _ = two_boards
    out = tools.dispatch("get_column_breakdown", {}, active_board_id=mine)

    assert out["meta"]["count"] == 1  # the fixture board has a single "To Do" column
    col = out["items"][0]
    assert col["column_title"] == "To Do"
    assert col["open_cards"] == 2       # high-priority + blocked, both open
    assert col["completed_cards"] == 1  # the finished card
    assert col["total_cards"] == 3


def test_cards_in_column_matches_loosely_and_hides_completed_by_default(two_boards):
    mine, _ = two_boards
    # 'do' must find the 'To Do' column — matching is a substring, as the description promises.
    out = tools.dispatch("get_cards_in_column", {"column_name": "do"}, active_board_id=mine)
    assert {c["title"] for c in out["items"]} == {"My high priority card", "My blocked card"}

    withdone = tools.dispatch(
        "get_cards_in_column",
        {"column_name": "To Do", "include_completed": True},
        active_board_id=mine,
    )
    assert "My finished card" in {c["title"] for c in withdone["items"]}


def test_cards_in_column_requires_a_column_name(two_boards):
    out = tools.dispatch("get_cards_in_column", {}, active_board_id=None)
    assert "error" in out and "column_name" in out["error"]
    assert "items" not in out, "a missing column must error, never fall through to all cards"


def test_the_fence_applies_to_the_column_tools(two_boards):
    """Each new tool is a new query; a fence is only as good as its least-covered read."""
    mine, theirs = two_boards

    for tool_name, arg in (("get_column_breakdown", {}), ("get_cards_in_column", {"column_name": "do"})):
        unscoped = tools.dispatch(tool_name, arg, active_board_id=None)
        assert {i.get("board_title") for i in unscoped["items"]} == {"Mine", "Theirs"}, f"{tool_name}: precondition"

        token = accessible_boards_ctx.set({mine})
        try:
            fenced = tools.dispatch(tool_name, arg, active_board_id=None)
        finally:
            accessible_boards_ctx.reset(token)

        assert {i.get("board_title") for i in fenced["items"]} == {"Mine"}, f"{tool_name}: fence leaked"


# ── a tool that fails at the database, not at its arguments ───────────────────

def test_a_database_error_becomes_an_envelope_instead_of_escaping(monkeypatch, caplog):
    """`dispatch` promised in its docstring that "a failed query" came back as an error envelope,
    but it caught only KeyError/ValueError/TypeError — argument-shaped faults. A database error is
    none of those, so it escaped `run_agent` and 500'd the chat endpoint: the user saw a bare
    status code, the model never got to say anything, and the traceback went nowhere.
    """
    import sqlite3

    from app.agents import tools

    def boom(_args, _board):
        raise sqlite3.OperationalError("no such column: c.completed_at")

    monkeypatch.setitem(tools.DISPATCH, "get_velocity", boom)

    with caplog.at_level("ERROR"):
        out = tools.dispatch("get_velocity", {}, active_board_id=None)

    assert "error" in out, "the failure must come back as an envelope, not raise"
    assert "get_velocity" in out["error"]
    # The model's next sentence is echoed to the user, so it must not carry database internals.
    assert "no such column" not in out["error"]
    # ...but the operator needs the real cause; this log is the only record of it.
    assert "no such column" in caplog.text


def test_the_loop_survives_a_failing_tool_and_still_answers(monkeypatch):
    """End-to-end proof of the same thing: one broken tool must degrade to an honest answer, not
    take down the request."""
    import sqlite3

    from app.agents import loop, tools

    def boom(_args, _board):
        raise sqlite3.OperationalError("relation \"cards\" does not exist")

    monkeypatch.setitem(tools.DISPATCH, "get_velocity", boom)

    class Provider:
        def __init__(self):
            self.turns = 0

        def converse(self, *, system, messages, tool_specs):
            self.turns += 1
            if self.turns == 1:
                return loop.ModelTurn(
                    stop_reason="tool_use",
                    content=({"toolUse": {"toolUseId": "t1", "name": "get_velocity", "input": {}}},),
                    input_tokens=100,
                    output_tokens=10,
                )
            return loop.ModelTurn(
                stop_reason="end_turn",
                content=({"text": "I can't check velocity right now."},),
                input_tokens=120,
                output_tokens=15,
            )

    out = loop.run_agent("how fast are we moving?", provider=Provider(), active_board_id=None)

    assert out.complete is True
    assert out.text == "I can't check velocity right now."
    assert out.tool_calls[0].error, "the failure should be recorded on the call, not hidden"
