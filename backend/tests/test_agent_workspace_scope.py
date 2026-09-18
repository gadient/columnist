"""What the assistant can actually see when it is opened at workspace level.

The UI labels the workspace-level panel "all N boards in <workspace>", and the onboarding explainer
says it "answers across every board in it". This file checks whether that is true, because a scope
claim a user reads and relies on is a promise, not a description.

It is deliberately about the *boundary*, not about whether an answer is good:
  1. Does a workspace-level question really reach more than one board? (the feature)
  2. Does it stop at the workspace, as the label says? (the claim)
  3. Does it stop at the tenant, whatever the label says? (the thing that must never break)
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.request_scope import accessible_boards_ctx


@pytest.fixture
def two_workspaces(tmp_path, monkeypatch):
    """Two workspaces in ONE instance, each with a board carrying a distinctive overdue card."""
    from app import migrations, store
    from app.config import settings
    from app.db import get_connection

    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "scope.sqlite"))
    migrations.run_migrations()

    conn = get_connection()
    made = {}
    try:
        member = {"name": "Priya Sharma", "initials": "PS", "color": "#000"}
        for ws_name, board_title, card_title in (
            ("Alpha Workspace", "Alpha Board", "ALPHA-CARD overdue thing"),
            ("Beta Workspace", "Beta Board", "BETA-CARD overdue thing"),
        ):
            ws = store.create_workspace(conn, ws_name)
            board = store.create_board(
                conn, board_title, "", [{"title": "To Do"}], [member], workspace_id=ws["id"]
            )
            col_id = board["columnOrder"][0]
            # cards.id is globally unique, not per-board — "c1" on both collides.
            card_id = f"card_{board['id']}"
            store.sync_board_snapshot(
                conn,
                board["id"],
                {
                    "id": board["id"],
                    "columns": {col_id: {"id": col_id, "title": "To Do", "cardIds": [card_id]}},
                    "cards": {
                        card_id: {
                            "id": card_id,
                            "title": card_title,
                            "description": "",
                            "priority": "high",
                            "dueDate": "2020-01-01",   # safely in the past ⇒ overdue
                            "assignees": [board["teamMembers"][0]["id"]],
                            "createdAt": "2020-01-01T00:00:00Z",
                            "completed": False,
                            "storyPoints": 3,
                            "labels": [],
                            "blockedBy": [],
                            "dependsOn": [],
                        }
                    },
                    "columnOrder": [col_id],
                    "teamMembers": board["teamMembers"],
                    "projectInfo": {"title": board_title, "description": ""},
                },
            )
            made[ws_name] = {"workspace_id": ws["id"], "board_id": board["id"], "card": card_title}
    finally:
        conn.close()
    return made


def _titles(rows):
    return {r.get("title") or r.get("card_title") or "" for r in rows}


# ── 1. the feature: does workspace level really span boards? ──────────────────

def test_workspace_level_reaches_every_board_not_just_one(two_workspaces):
    """`activeBoardId=None` is what the workspace-level panel sends. With no board pinned, a
    lookup must span boards — otherwise the workspace panel is just a board panel."""
    from app.mcp_queries import overdue_tasks

    rows = overdue_tasks(active_board_id=None, user_name=None, include_completed=False, limit=50)
    found = _titles(rows)

    assert any("ALPHA-CARD" in t for t in found), f"Alpha board not reached: {found}"
    assert any("BETA-CARD" in t for t in found), f"Beta board not reached: {found}"


def test_pinning_a_board_narrows_to_that_board(two_workspaces):
    """The board-level panel sends a board id, and the answer must not leak in the other board."""
    from app.mcp_queries import overdue_tasks

    alpha = two_workspaces["Alpha Workspace"]
    found = _titles(overdue_tasks(active_board_id=alpha["board_id"], user_name=None, include_completed=False, limit=50))

    assert any("ALPHA-CARD" in t for t in found)
    assert not any("BETA-CARD" in t for t in found), f"board scope leaked: {found}"


# ── 2. the claim on the label ─────────────────────────────────────────────────

def test_the_tenant_fence_alone_is_instance_wide_by_design(two_workspaces):
    """The tenant fence is deliberately the whole Instance — it answers "may this user read this
    board at all", not "is this the board they are looking at".

    That is correct for an authorization boundary, and it was also the entire scope until
    `activeWorkspaceId` was added: a workspace-level question was fenced to the tenant and quietly
    read other workspaces while the panel claimed to search one. The view narrowing now sits on top
    (see the endpoint tests below); this pins the layer underneath so the two do not get merged.
    """
    from app.mcp_queries import overdue_tasks

    alpha = two_workspaces["Alpha Workspace"]
    beta = two_workspaces["Beta Workspace"]

    # Exactly what agent_api does under auth: fence to every board in the instance.
    token = accessible_boards_ctx.set({alpha["board_id"], beta["board_id"]})
    try:
        found = _titles(overdue_tasks(active_board_id=None, user_name=None, include_completed=False, limit=50))
    finally:
        accessible_boards_ctx.reset(token)

    assert any("ALPHA-CARD" in t for t in found)
    assert any("BETA-CARD" in t for t in found), (
        "the tenant fence should span the whole Instance; workspace narrowing belongs one layer up"
    )


# ── 3. the boundary that must never move ──────────────────────────────────────

def test_the_tenant_fence_holds_at_workspace_level(two_workspaces):
    """Workspace level is the widest the assistant ever runs, so it is where a fence failure would
    show first. A board outside the fence must be invisible even with no board pinned."""
    from app.mcp_queries import overdue_tasks

    alpha = two_workspaces["Alpha Workspace"]

    token = accessible_boards_ctx.set({alpha["board_id"]})  # Beta not accessible
    try:
        found = _titles(overdue_tasks(active_board_id=None, user_name=None, include_completed=False, limit=50))
    finally:
        accessible_boards_ctx.reset(token)

    assert any("ALPHA-CARD" in t for t in found)
    assert not any("BETA-CARD" in t for t in found), f"TENANT FENCE BREACH: {found}"


def test_a_user_with_no_boards_sees_nothing_at_workspace_level(two_workspaces):
    """The empty fence must mean 'nothing', never 'unrestricted'. `None` is the unrestricted
    sentinel and an empty set is a real, empty allowlist — confusing the two would open everything
    to a user entitled to nothing."""
    from app.mcp_queries import overdue_tasks

    token = accessible_boards_ctx.set(set())
    try:
        rows = overdue_tasks(active_board_id=None, user_name=None, include_completed=False, limit=50)
    finally:
        accessible_boards_ctx.reset(token)

    assert rows == [], f"empty allowlist behaved as unrestricted: {rows}"


# ── 4. the fix: the endpoint now honours where you are standing ───────────────

def _post_chat(client, **body):
    return client.post("/api/v1/agent/chat", json={"message": "what is overdue?", **body})


def test_the_endpoint_narrows_to_the_workspace_you_are_standing_in(two_workspaces, monkeypatch):
    """The panel says it searches "all N boards in <workspace>". This asserts the endpoint
    actually stops there, which it did not before `activeWorkspaceId` existed."""
    from fastapi.testclient import TestClient

    from app.main import app

    seen: dict = {}

    monkeypatch.setattr(settings, "agent_backend", "bedrock")

    def capture(message, active_board_id, history):
        seen["scope"] = accessible_boards_ctx.get()
        from app.validator import ChatResponseResult

        return ChatResponseResult(valid=True, response="ok", reason="ok", tool_called=None)

    monkeypatch.setattr("app.agent_api._agent_chat", capture)

    alpha = two_workspaces["Alpha Workspace"]
    beta = two_workspaces["Beta Workspace"]

    r = _post_chat(TestClient(app), activeBoardId=None, activeWorkspaceId=alpha["workspace_id"])

    assert r.status_code == 200
    assert seen["scope"] == {alpha["board_id"]}, (
        f"expected only Alpha's board in scope, got {seen['scope']} — Beta's board is "
        f"{beta['board_id']}"
    )


def test_omitting_the_workspace_leaves_the_previous_behaviour(two_workspaces, monkeypatch):
    """Older clients (and the board-level panel) send no workspace. Nothing should narrow, and with
    Cognito off nothing should be fenced at all."""
    from fastapi.testclient import TestClient

    from app.main import app

    seen: dict = {}

    monkeypatch.setattr(settings, "agent_backend", "bedrock")

    def capture(message, active_board_id, history):
        seen["scope"] = accessible_boards_ctx.get()
        from app.validator import ChatResponseResult

        return ChatResponseResult(valid=True, response="ok", reason="ok", tool_called=None)

    monkeypatch.setattr("app.agent_api._agent_chat", capture)

    r = _post_chat(TestClient(app), activeBoardId=None)

    assert r.status_code == 200
    assert seen["scope"] is None, "no workspace and no auth should mean no fence, as before"


def test_a_pinned_board_is_not_disturbed_by_the_workspace_value(two_workspaces, monkeypatch):
    """A board id already scopes every query on its own. The workspace narrowing must not also
    apply, or a board in one workspace viewed from another would silently return nothing."""
    from fastapi.testclient import TestClient

    from app.main import app

    seen: dict = {}

    monkeypatch.setattr(settings, "agent_backend", "bedrock")

    def capture(message, active_board_id, history):
        seen["scope"] = accessible_boards_ctx.get()
        seen["board"] = active_board_id
        from app.validator import ChatResponseResult

        return ChatResponseResult(valid=True, response="ok", reason="ok", tool_called=None)

    monkeypatch.setattr("app.agent_api._agent_chat", capture)

    alpha = two_workspaces["Alpha Workspace"]
    beta = two_workspaces["Beta Workspace"]

    r = _post_chat(
        TestClient(app), activeBoardId=beta["board_id"], activeWorkspaceId=alpha["workspace_id"]
    )

    assert r.status_code == 200
    assert seen["board"] == beta["board_id"]
    assert seen["scope"] is None, "the pinned board scopes on its own; workspace must not also fence"
