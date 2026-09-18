"""`MCP_ALLOWED_BOARD_IDS` actually narrows the MCP server.

**Why this file exists.** A pre-publication review reported the setting as advertised but never
applied. It is applied — `mcp_queries._board_filter_clause` reads it on every query and
`_enforce_board_scope` refuses a board named outside it — but nothing asserted that, which is why
the claim was plausible. A security control with no test is one refactor away from being exactly
what the review thought it already was.

**What it is, and is not.** A static fence around one process. The MCP server authenticates nobody,
so the allowlist limits blast radius; it is not per-user tenant scope, and the module's own warning
against exposing the server on a network still stands. With the variable unset, the server is
unscoped and this file asserts that too — a fence that silently switched itself on would break the
supported local use.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.request_scope import accessible_boards_ctx


@pytest.fixture(autouse=True)
def clean_scope():
    """Each test starts unscoped and leaves nothing behind for the next one."""
    token = accessible_boards_ctx.set(None)
    yield
    accessible_boards_ctx.reset(token)


def _overview_board_ids(active_board_id: str | None = None) -> set[str]:
    from app import mcp_server

    return {row["board_id"] for row in mcp_server.get_board_overview(active_board_id)["items"]}


def test_without_the_setting_the_server_sees_every_board(two_boards, monkeypatch):
    mine, theirs = two_boards
    monkeypatch.setattr(settings, "mcp_allowed_board_ids", "")

    assert _overview_board_ids() >= {mine, theirs}


def test_the_allowlist_hides_every_board_it_does_not_name(two_boards, monkeypatch):
    mine, theirs = two_boards
    monkeypatch.setattr(settings, "mcp_allowed_board_ids", mine)

    seen = _overview_board_ids()

    assert mine in seen
    assert theirs not in seen, "the allowlist is advertised as a fence; it has to be one"


def test_a_named_board_outside_the_allowlist_is_refused_outright(two_boards, monkeypatch):
    """Asking for the excluded board by id does not get round the fence — it raises."""
    mine, theirs = two_boards
    monkeypatch.setattr(settings, "mcp_allowed_board_ids", mine)

    with pytest.raises(ValueError, match="outside MCP scope"):
        _overview_board_ids(theirs)


def test_the_allowlist_applies_to_a_second_tool_as_well(two_boards, monkeypatch):
    """One tool honouring it is a coincidence; the fence has to cover the surface."""
    from app import mcp_server

    mine, theirs = two_boards
    monkeypatch.setattr(settings, "mcp_allowed_board_ids", mine)

    items = mcp_server.get_overdue_tasks()["items"]

    assert all(item["board_id"] == mine for item in items)
