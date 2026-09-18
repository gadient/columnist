"""Read-only analytics MCP server (stdio).

SECURITY BOUNDARY — runs UNSCOPED. This process does not authenticate a caller and never sets
`accessible_boards_ctx`, so its queries default to *all boards across all tenants*
(`mcp_queries` treats an unset scope as unrestricted). That is fine for its only supported use
today: a local stdio tool run by a single trusted user against their own DB.

`MCP_ALLOWED_BOARD_IDS` is the one fence that does apply here: when it is set, every query this
server runs is limited to those board ids, and naming a board outside the list is refused
(`mcp_queries._enforce_board_scope` / `_board_filter_clause`). It is static, not per-caller — it
limits blast radius, it does not isolate tenants — and with the variable unset the server is
unscoped, as above.

**Do NOT connect this to shared/multi-user infrastructure (e.g. Bedrock AgentCore)
without first adding authenticated per-user scope** — resolve the caller's identity and set
`accessible_boards_ctx` per request (as the HTTP chat endpoint does in `agent_api.py`), and wire
`settings.mcp_shared_secret` (currently unused) or an equivalent auth mechanism.

NOTE: this module must NOT use `from __future__ import annotations` — it makes annotations lazy
strings and breaks FastMCP tool registration (`issubclass()`).
"""
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

from .config import settings
from .mcp_queries import (
    board_overview,
    card_details,
    due_dates_by_user,
    high_priority_due_dates_in_board,
    high_priority_tasks,
    overdue_tasks,
    tomorrows_due_dates,
    upcoming_due_dates,
)

mcp = FastMCP(name="columnist-board-insights")


def _meta(scope_board_id: str | None, count: int) -> dict:
    return {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "scope": "board" if scope_board_id else "all_boards",
        "active_board_id": scope_board_id,
        "count": count,
        "max_limit": settings.mcp_max_result_limit,
    }


@mcp.tool()
def get_due_dates_by_user(
    user_name: str,
    active_board_id: str | None = None,
    include_completed: bool = False,
    limit: int = 25,
) -> dict:
    """Use case #1: action items (with due dates) for a user across boards or within the active board."""
    items = due_dates_by_user(
        user_name=user_name,
        active_board_id=active_board_id,
        include_completed=include_completed,
        limit=limit,
    )
    return {
        "query": {
            "user_name": user_name,
            "include_completed": include_completed,
            "limit": limit,
        },
        "meta": _meta(active_board_id, len(items)),
        "items": items,
    }


@mcp.tool()
def get_high_priority_tasks(
    active_board_id: str | None = None,
    include_completed: bool = False,
    limit: int = 25,
) -> dict:
    """Use case #2: high priority tasks and due dates, optionally scoped to the active board."""
    items = high_priority_tasks(
        active_board_id=active_board_id,
        include_completed=include_completed,
        limit=limit,
    )
    return {
        "query": {
            "priority": "high",
            "include_completed": include_completed,
            "limit": limit,
        },
        "meta": _meta(active_board_id, len(items)),
        "items": items,
    }


@mcp.tool()
def get_card_details(card_ids: list[str]) -> dict:
    """Use case #3: detailed information for one or more card IDs."""
    items = card_details(card_ids=card_ids)
    return {
        "query": {
            "card_ids": card_ids,
        },
        "meta": _meta(None, len(items)),
        "items": items,
    }


@mcp.tool()
def get_tomorrows_due_dates(
    active_board_id: str | None = None,
    user_name: str | None = None,
    include_completed: bool = False,
    limit: int = 25,
) -> dict:
    """Use case #4: tasks due tomorrow, across all boards or within active board."""
    items = tomorrows_due_dates(
        active_board_id=active_board_id,
        user_name=user_name,
        include_completed=include_completed,
        limit=limit,
    )
    return {
        "query": {
            "user_name": user_name,
            "include_completed": include_completed,
            "limit": limit,
        },
        "meta": _meta(active_board_id, len(items)),
        "items": items,
    }


@mcp.tool()
def get_high_priority_due_dates_in_board(
    board_id: str,
    include_completed: bool = False,
    limit: int = 25,
) -> dict:
    """Use case #5: high-priority tasks with due dates in one board."""
    items = high_priority_due_dates_in_board(
        board_id=board_id,
        include_completed=include_completed,
        limit=limit,
    )
    return {
        "query": {
            "board_id": board_id,
            "include_completed": include_completed,
            "limit": limit,
        },
        "meta": _meta(board_id, len(items)),
        "items": items,
    }


@mcp.tool()
def get_upcoming_due_dates(
    days: int = 7,
    active_board_id: str | None = None,
    user_name: str | None = None,
    include_completed: bool = False,
    limit: int = 25,
) -> dict:
    """Additional: upcoming due dates over a configurable window (max 90 days)."""
    items = upcoming_due_dates(
        active_board_id=active_board_id,
        user_name=user_name,
        days=days,
        include_completed=include_completed,
        limit=limit,
    )
    return {
        "query": {
            "days": days,
            "user_name": user_name,
            "include_completed": include_completed,
            "limit": limit,
        },
        "meta": _meta(active_board_id, len(items)),
        "items": items,
    }


@mcp.tool()
def get_overdue_tasks(
    active_board_id: str | None = None,
    user_name: str | None = None,
    include_completed: bool = False,
    limit: int = 25,
) -> dict:
    """Additional: overdue tasks, optionally filtered by user and active board."""
    items = overdue_tasks(
        active_board_id=active_board_id,
        user_name=user_name,
        include_completed=include_completed,
        limit=limit,
    )
    return {
        "query": {
            "user_name": user_name,
            "include_completed": include_completed,
            "limit": limit,
        },
        "meta": _meta(active_board_id, len(items)),
        "items": items,
    }


@mcp.tool()
def get_board_overview(active_board_id: str | None = None) -> dict:
    """Additional: board-level workload overview for quick summaries."""
    items = board_overview(active_board_id=active_board_id)
    return {
        "query": {
            "active_board_id": active_board_id,
        },
        "meta": _meta(active_board_id, len(items)),
        "items": items,
    }


if __name__ == "__main__":
    # UNSCOPED, single-trusted-user stdio only. Not safe to expose to multi-user/shared infra
    # without per-user auth + accessible_boards_ctx scoping — see the module docstring.
    mcp.run(transport="stdio")
