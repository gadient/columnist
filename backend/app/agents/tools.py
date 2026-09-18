"""Tool definitions for the chat agent — Bedrock Converse `toolSpec`s over `mcp_queries`.

**Schemas and dispatch only.** Nothing here calls a model — `loop.py` does — so this module is
deliberately free to build and free to test.

**One behaviour, two front doors.** These tools are thin descriptions of the same `mcp_queries`
functions `mcp_server.py` serves over stdio (it exposes 8 of these 13). The query logic stays in
`mcp_queries`; if the two ever grow their own implementations they will drift, and the stdio server
and the in-app agent will start disagreeing about the same board.

**Read-only, and that is structural.** Every function reachable from `DISPATCH` is a SELECT. There
is no mutating tool to omit — the mutation boundary the note-import agent established (the model
recommends, deterministic code writes) holds here by construction.

## The two parameter classes, and why the split matters

- **Model-supplied**: `user_name`, `column_name`, `days`, `limit`, `include_completed`, `card_ids`,
  `board_id`. The model chooses these. Identifiers among them (`card_ids`, `board_id`) are *not*
  trusted: a model can hallucinate or be talked into naming a card on someone else's board. They
  are safe only because every read passes through `accessible_boards_ctx` (`request_scope.py`),
  which the caller sets per request — a fabricated id returns nothing rather than someone else's
  data. `test_agent_tools.py` pins that, because it is the failure that is silent when it breaks.

- **Server-supplied**: `active_board_id`. Deliberately **absent from every schema** and injected by
  `dispatch`. The board you are looking at is a fact about the request, not a judgement call, and
  letting the model set it would hand it the ability to widen its own scope — asking for a board
  the user is not on, or dropping the filter entirely to sweep everything. The fence would still
  hold, but scope-widening should not be expressible in the first place.

Tool *descriptions* are load-bearing: they are the only thing the model reads when choosing. They
state what a tool is for and — where it differs from a sibling — when to prefer it, because the
overlap between "upcoming", "tomorrow" and "overdue" is exactly where a model picks wrong.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from ..config import settings
from ..mcp_queries import (
    blocked_cards,
    board_overview,
    card_details,
    cards_in_column,
    column_breakdown,
    due_dates_by_user,
    high_priority_due_dates_in_board,
    high_priority_tasks,
    overdue_tasks,
    team_workload,
    tomorrows_due_dates,
    upcoming_due_dates,
    velocity,
)

logger = logging.getLogger(__name__)

# Injected by `dispatch`, never accepted from the model. See the module docstring.
SERVER_SUPPLIED = frozenset({"active_board_id"})

_LIMIT = {
    "type": "integer",
    "description": f"Maximum cards to return (1-{settings.mcp_max_result_limit}). Defaults to 25.",
    "minimum": 1,
    "maximum": settings.mcp_max_result_limit,
}

_INCLUDE_COMPLETED = {
    "type": "boolean",
    "description": "Include cards already marked complete. Defaults to false — ask for open work unless the user wants history.",
}

_USER_NAME = {
    "type": "string",
    "description": "Full or partial name of a board member, as the user said it (e.g. 'Priya' or 'Priya Sharma').",
}

_COLUMN_NAME = {
    "type": "string",
    "description": "Column / workflow-status name as the user said it (e.g. 'Review', 'In Progress', 'Done'). Matched loosely, so a partial name works.",
}


def _spec(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "toolSpec": {
            "name": name,
            "description": description,
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": properties,
                    "required": required or [],
                }
            },
        }
    }


TOOL_SPECS: list[dict[str, Any]] = [
    _spec(
        "get_due_dates_by_user",
        "Action items with due dates for ONE named person. Use when the user asks what someone is "
        "working on or owns. For questions about everyone, use get_high_priority_tasks or "
        "get_upcoming_due_dates instead.",
        {"user_name": _USER_NAME, "include_completed": _INCLUDE_COMPLETED, "limit": _LIMIT},
        required=["user_name"],
    ),
    _spec(
        "get_high_priority_tasks",
        "Open high-priority cards. Use for 'what is most important' or 'what should I focus on'. "
        "Returns cards whether or not they have a due date — prefer get_overdue_tasks when the "
        "user asks specifically about lateness.",
        {"include_completed": _INCLUDE_COMPLETED, "limit": _LIMIT},
    ),
    _spec(
        "get_card_details",
        "Full detail for specific cards, by id. Only useful once you have card ids from another "
        "tool — never invent ids, as unknown ids simply return nothing.",
        {
            "card_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Card ids exactly as returned by another tool.",
            }
        },
        required=["card_ids"],
    ),
    _spec(
        "get_tomorrows_due_dates",
        "Cards due specifically tomorrow. Use only for 'tomorrow' — for a range use "
        "get_upcoming_due_dates, which covers today onward.",
        {"user_name": _USER_NAME, "include_completed": _INCLUDE_COMPLETED, "limit": _LIMIT},
    ),
    _spec(
        "get_high_priority_due_dates_in_board",
        "High-priority cards WITH due dates in one named board. Requires a board_id, which you can "
        "get from get_board_overview. Use when the user names a board other than the one they are "
        "viewing; otherwise the other tools already scope correctly.",
        {
            "board_id": {
                "type": "string",
                "description": "Board id from get_board_overview. Not the board's title.",
            },
            "include_completed": _INCLUDE_COMPLETED,
            "limit": _LIMIT,
        },
        required=["board_id"],
    ),
    _spec(
        "get_upcoming_due_dates",
        "Cards due within the next N days, starting today. The general-purpose 'what is coming up' "
        "tool — use it for 'this week', 'next two weeks', or any range.",
        {
            "days": {
                "type": "integer",
                "description": "Days ahead to look, 1-90. Defaults to 7.",
                "minimum": 1,
                "maximum": 90,
            },
            "user_name": _USER_NAME,
            "include_completed": _INCLUDE_COMPLETED,
            "limit": _LIMIT,
        },
    ),
    _spec(
        "get_overdue_tasks",
        "Cards whose due date has already passed and are not complete. Use for 'late', 'overdue', "
        "'behind', or 'what has slipped'.",
        {"user_name": _USER_NAME, "include_completed": _INCLUDE_COMPLETED, "limit": _LIMIT},
    ),
    _spec(
        "get_board_overview",
        "Per-board totals: card count, completed, high-priority, and how many have due dates. Use "
        "to answer 'how are things going overall', to compare boards, or to look up a board_id. "
        "These are standing totals, not a rate — for how much work is getting finished over time, "
        "use get_velocity.",
        {},
    ),
    _spec(
        "get_blocked_cards",
        "Open cards that something else is holding up, with the blocking work named. Use for "
        "'blocked', 'stuck', 'waiting on', 'dependencies' — and ALWAYS include this when asked "
        "what is at risk, since a card can be blocked without being late. Due-date tools cannot "
        "see this.",
        {"limit": _LIMIT},
    ),
    _spec(
        "get_team_workload",
        "Open card count and story points per team member. Use for 'who is busiest', 'who has "
        "capacity', 'is anyone overloaded', or any question comparing people. Counts ALL open "
        "assigned work — prefer it over get_due_dates_by_user, which only sees one named person "
        "and only their dated cards.",
        {"limit": _LIMIT},
    ),
    _spec(
        "get_velocity",
        "Cards and story points COMPLETED in a recent window, per board. Use for 'how fast are we "
        "moving', 'are we speeding up', 'what did we finish' — anything about throughput over "
        "time. get_board_overview gives a completion total, which is not a rate.",
        {
            "days": {
                "type": "integer",
                "description": "How far back to look, 1-365. Defaults to 14 (about a sprint).",
                "minimum": 1,
                "maximum": 365,
            },
            "limit": _LIMIT,
        },
    ),
    _spec(
        "get_column_breakdown",
        "How many cards sit in each column — the board's workflow/status distribution (e.g. how many "
        "in To Do vs In Progress vs Review vs Done). Use for 'how is the board laid out', 'where is "
        "everything', 'how many are in review', or 'what's the status spread'. This is the counts-by-"
        "status tool; to LIST the actual cards in one status use get_cards_in_column.",
        {},
    ),
    _spec(
        "get_cards_in_column",
        "The cards currently in a named column / workflow status. Use for 'what's in Review', 'what "
        "is being evaluated', 'what's in progress', 'what's in the <column> column right now'. Match "
        "the column by the name the user said — it is matched loosely, so 'review' finds 'In Review'. "
        "For just the counts per column use get_column_breakdown instead.",
        {"column_name": _COLUMN_NAME, "include_completed": _INCLUDE_COMPLETED, "limit": _LIMIT},
        required=["column_name"],
    ),
]

TOOL_NAMES: frozenset[str] = frozenset(s["toolSpec"]["name"] for s in TOOL_SPECS)


# ── dispatch ──────────────────────────────────────────────────────────────────
# Each entry adapts model-supplied arguments to the query function's keyword-only signature and
# applies defaults. `active_board_id` arrives from the caller, never from `args`.

def _due_by_user(args: dict, board: str | None) -> list[dict]:
    return due_dates_by_user(
        user_name=args["user_name"],
        active_board_id=board,
        include_completed=bool(args.get("include_completed", False)),
        limit=int(args.get("limit", 25)),
    )


def _high_priority(args: dict, board: str | None) -> list[dict]:
    return high_priority_tasks(
        active_board_id=board,
        include_completed=bool(args.get("include_completed", False)),
        limit=int(args.get("limit", 25)),
    )


def _card_details(args: dict, board: str | None) -> list[dict]:
    return card_details(card_ids=list(args.get("card_ids") or []))


def _tomorrow(args: dict, board: str | None) -> list[dict]:
    return tomorrows_due_dates(
        active_board_id=board,
        user_name=args.get("user_name"),
        include_completed=bool(args.get("include_completed", False)),
        limit=int(args.get("limit", 25)),
    )


def _high_priority_in_board(args: dict, board: str | None) -> list[dict]:
    return high_priority_due_dates_in_board(
        board_id=args["board_id"],
        include_completed=bool(args.get("include_completed", False)),
        limit=int(args.get("limit", 25)),
    )


def _upcoming(args: dict, board: str | None) -> list[dict]:
    return upcoming_due_dates(
        active_board_id=board,
        user_name=args.get("user_name"),
        days=int(args.get("days", 7)),
        include_completed=bool(args.get("include_completed", False)),
        limit=int(args.get("limit", 25)),
    )


def _overdue(args: dict, board: str | None) -> list[dict]:
    return overdue_tasks(
        active_board_id=board,
        user_name=args.get("user_name"),
        include_completed=bool(args.get("include_completed", False)),
        limit=int(args.get("limit", 25)),
    )


def _overview(args: dict, board: str | None) -> list[dict]:
    return board_overview(active_board_id=board)


def _blocked(args: dict, board: str | None) -> list[dict]:
    return blocked_cards(active_board_id=board, limit=int(args.get("limit", 25)))


def _workload(args: dict, board: str | None) -> list[dict]:
    return team_workload(active_board_id=board, limit=int(args.get("limit", 25)))


def _velocity(args: dict, board: str | None) -> list[dict]:
    return velocity(active_board_id=board, days=int(args.get("days", 14)), limit=int(args.get("limit", 25)))


def _column_breakdown(args: dict, board: str | None) -> list[dict]:
    return column_breakdown(active_board_id=board)


def _cards_in_column(args: dict, board: str | None) -> list[dict]:
    return cards_in_column(
        column_name=args["column_name"],
        active_board_id=board,
        include_completed=bool(args.get("include_completed", False)),
        limit=int(args.get("limit", 25)),
    )


DISPATCH: dict[str, Callable[[dict, str | None], list[dict]]] = {
    "get_due_dates_by_user": _due_by_user,
    "get_high_priority_tasks": _high_priority,
    "get_card_details": _card_details,
    "get_tomorrows_due_dates": _tomorrow,
    "get_high_priority_due_dates_in_board": _high_priority_in_board,
    "get_upcoming_due_dates": _upcoming,
    "get_overdue_tasks": _overdue,
    "get_board_overview": _overview,
    "get_blocked_cards": _blocked,
    "get_team_workload": _workload,
    "get_velocity": _velocity,
    "get_column_breakdown": _column_breakdown,
    "get_cards_in_column": _cards_in_column,
}


def dispatch(name: str, arguments: dict[str, Any] | None, *, active_board_id: str | None) -> dict[str, Any]:
    """Run one tool call and return a result envelope the model can read.

    Never raises for bad model output. An unknown tool, a missing required argument or a failed
    query all come back as ``{"error": ...}`` so the agent loop can show the model what went wrong
    and let it retry — an exception here would kill the turn and lose the conversation instead.
    """
    args = dict(arguments or {})

    if name not in DISPATCH:
        return {"error": f"Unknown tool '{name}'.", "available": sorted(TOOL_NAMES)}

    # Scope is the server's to decide. Silently dropping a model-supplied `active_board_id` would
    # hide a model trying to widen its own reach, so say so plainly instead.
    smuggled = sorted(SERVER_SUPPLIED & args.keys())
    if smuggled:
        return {"error": f"{', '.join(smuggled)} is set by the server and cannot be passed as an argument."}

    try:
        items = DISPATCH[name](args, active_board_id)
    except KeyError as exc:  # a required argument the model omitted
        return {"error": f"Missing required argument: {exc.args[0]}"}
    except (ValueError, TypeError) as exc:
        return {"error": f"Invalid arguments for '{name}': {exc}"}
    except Exception:
        # The docstring above promises "a failed query" comes back as an envelope. A database error
        # is none of the argument-shaped types above; without this catch it would escape
        # `run_agent` and 500 the chat endpoint — a bare status code for the user, and no chance
        # for the model to say anything.
        #
        # Logged with the traceback because this is a server fault, not model misbehaviour, and it
        # is the only record of it. The envelope stays generic on purpose: the model does not need
        # a database error string, and whatever it says next is echoed to the user.
        logger.exception("tool %s failed", name)
        return {"error": f"The '{name}' lookup failed. Do not retry it; report that it is unavailable."}

    return {
        "meta": {"tool": name, "scope_board_id": active_board_id, "count": len(items)},
        "items": items,
    }
