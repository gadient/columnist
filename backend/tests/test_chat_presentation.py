"""How a chat answer is shaped for the SPA.

This layer was first written for the (now retired) offline rules engine, whose replies were
machine-generated in a fixed `Title | Status: … | Due: …` pipe format. The tool-calling agent writes
prose. The tool name alone used to decide the format — so a model answering in bullets was rendered
as a six-column table with five empty columns per row: a table that looks broken rather than an
answer that reads. The pipe→table shaping is kept as defensive handling for any pipe-delimited
content; the tests below still pin it.

The rule these tests pin: the *tool* may propose a table, but only the *content* can justify one.
"""
from __future__ import annotations

from app.chat_presentation import build_chat_presentation

# One of the tool names that request a table (chat_presentation._heuristic_format).
TABLE_TOOL = "get_high_priority_tasks"

RULES_ENGINE_REPLY = (
    "High priority tasks\n"
    "- Fix login | Status: In Progress | Due: 2026-07-22 | Priority: High | Board: Eng | Assignees: Priya"
)

MODEL_PROSE_REPLY = (
    "Three cards are high priority right now.\n"
    "- Fix the login redirect (ENG-402), due Friday, assigned to Priya\n"
    "- Migrate the billing table (ENG-411), overdue by two days"
)


def test_the_rules_engine_pipe_format_still_becomes_a_table():
    """Pipe-delimited content still shapes into a table — retained as defensive handling even though
    the rules engine that produced this format has been retired."""
    p = build_chat_presentation("what is high priority?", RULES_ENGINE_REPLY, TABLE_TOOL)

    assert p.format == "table"
    assert p.table_rows == [["Fix login", "In Progress", "2026-07-22", "High", "Eng", "Priya"]]


def test_model_prose_is_not_forced_into_the_table_the_tool_asked_for():
    """The regression this file exists for. `get_high_priority_tasks` requests a table, but these
    bullets carry no pipe-delimited fields — every column but the first would be blank."""
    p = build_chat_presentation("what is high priority?", MODEL_PROSE_REPLY, TABLE_TOOL)

    assert p.format == "bullets"
    assert p.bullets == [
        "Fix the login redirect (ENG-402), due Friday, assigned to Priya",
        "Migrate the billing table (ENG-411), overdue by two days",
    ]


def test_no_answer_text_is_lost_when_the_table_is_declined():
    """Falling back must not silently truncate. Every bullet from the reply has to survive into
    whatever format is chosen instead — dropping rows would turn a formatting bug into a wrong
    answer, which is far worse than an ugly one."""
    p = build_chat_presentation("what is high priority?", MODEL_PROSE_REPLY, TABLE_TOOL)

    rendered = " ".join(p.bullets or []) + (p.text or "")
    assert "ENG-402" in rendered
    assert "ENG-411" in rendered


def test_plain_prose_with_no_list_survives_intact():
    """The agent often answers in a sentence or two with no bullets at all."""
    reply = "Nothing is blocked at the moment, and no card is overdue."
    p = build_chat_presentation("anything blocked?", reply, "get_blocked_cards")

    assert p.format == "text"
    assert p.text == reply


def test_a_tool_that_never_requested_a_table_is_unaffected():
    p = build_chat_presentation("how is the team doing?", "The team is on track.", "get_team_workload")

    assert p.format == "text"
