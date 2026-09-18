"""Instance isolation — the tenant boundary.

**Why this file exists.** This is the single most important security property in the app: one
user's Instance must be completely invisible to another's. A manual two-user smoke test run once
proves nothing about the code as it stands today; this file does. Every other guarantee (auth, CSRF,
caps) is about keeping strangers out; this one is about keeping *invited users* apart, and it is the
one a refactor is most likely to break silently, because breaking it makes nothing fail loudly —
you simply start seeing rows you shouldn't.

**What is actually asserted.** The store-layer scoping functions, `accessible_workspace_ids` and
`accessible_board_ids`, which every list/read path funnels through (`api.py` list handlers,
analytics `_require_board_scope`, and the chat/MCP `accessible_boards_ctx` allowlist). Testing them
directly rather than through HTTP is deliberate: it keeps the tests fast and free of auth
scaffolding, and these two functions are where the boundary is genuinely decided. `conftest.py`
pins Cognito off, so an API-level test would exercise the *unscoped* path and prove the opposite of
what we want.

**Fail-closed matters as much as isolation.** A user with no Instance must see *nothing*, not
everything — an empty scope is the safe default and is asserted explicitly, because the natural
buggy implementation (no filter → return all) is indistinguishable from correct behaviour in a
single-tenant test.
"""
from __future__ import annotations

import sqlite3

import pytest

from app import migrations, store
from app.config import settings
from app.db import get_connection


@pytest.fixture
def conn(tmp_path, monkeypatch) -> sqlite3.Connection:
    """A migrated, empty database per test.

    Points `settings.sqlite_path` at a tmp file *before* running migrations so each test gets a
    pristine schema and cannot see another test's Instances — which would quietly defeat the
    point of an isolation test.
    """
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "isolation.sqlite"))
    migrations.run_migrations()
    connection = get_connection()
    yield connection
    connection.close()


def sign_in(conn: sqlite3.Connection, user_id: str, email: str) -> tuple[dict, str | None]:
    """Simulate a user's first authenticated request.

    Deliberately goes through the real JIT path (`upsert_user` + `provision_instance_membership`)
    rather than INSERTing a users row with `instance_id` pre-filled. An invite is created keyed by
    *email* with `user_id = NULL`; only login binds the Cognito `sub` to it and flips the row to
    'active'. Short-cutting that would produce a user who looks provisioned but holds no
    `instance_members` role — a fixture that passes the isolation assertions while quietly
    failing the invite ones.
    """
    user = {"id": user_id, "email": email}
    store.upsert_user(conn, user)
    instance_id, _activated = store.provision_instance_membership(conn, user)
    return user, instance_id


@pytest.fixture
def two_instances(conn):
    """The realistic shape: two PIUs, each with their own Instance, plus one SIU in the first.

    Built through `create_primary_space` (what `deploy/invite-primary.sh` calls) and
    `invite_secondary_user` (what the in-app "Manage members" invite calls), then each user "logs in".
    So this fixture also covers the full invite → first-login → bind sequence; if that path stops
    minting a fresh Instance per PIU, or stops binding SIUs to the inviter's Instance, these tests
    fail rather than the boundary silently changing shape.
    """
    a = store.create_primary_space(conn, "alice@example.com", "Alice Space", 10, 5)
    b = store.create_primary_space(conn, "bob@example.com", "Bob Space", 10, 5)
    instance_a, instance_b = a["instance"]["id"], b["instance"]["id"]

    alice, alice_instance = sign_in(conn, "u-alice", "alice@example.com")
    bob, bob_instance = sign_in(conn, "u-bob", "bob@example.com")

    # Alice invites Sam as an SIU, then Sam logs in for the first time.
    store.invite_secondary_user(conn, instance_a, "u-alice", "sam@example.com", 5)
    sam, sam_instance = sign_in(conn, "u-sam", "sam@example.com")

    # Guard the fixture's own premises — a broken fixture must not read as a passing test.
    assert alice_instance == instance_a
    assert bob_instance == instance_b
    assert sam_instance == instance_a

    return {
        "conn": conn,
        "instance_a": instance_a,
        "instance_b": instance_b,
        "alice": alice,
        "bob": bob,
        "sam": sam,
    }


def test_each_primary_user_gets_a_distinct_instance(conn):
    """Two PIUs must be structurally separated, not merely un-listed for each other."""
    a = store.create_primary_space(conn, "alice@example.com", "Alice Space", 10, 5)
    b = store.create_primary_space(conn, "bob@example.com", "Bob Space", 10, 5)

    assert a["instance"]["id"] != b["instance"]["id"]
    assert a["member"]["role"] == store.INSTANCE_ROLE_PIU
    assert b["member"]["role"] == store.INSTANCE_ROLE_PIU


def test_workspaces_are_invisible_across_instances(two_instances):
    """The headline property: neither primary user can see the other's workspace."""
    env = two_instances
    conn = env["conn"]

    ws_a = store.create_workspace(conn, "Alice Private", owner=env["alice"])
    ws_b = store.create_workspace(conn, "Bob Private", owner=env["bob"])

    alice_sees = store.accessible_workspace_ids(conn, "u-alice")
    bob_sees = store.accessible_workspace_ids(conn, "u-bob")

    assert ws_a["id"] in alice_sees
    assert ws_b["id"] not in alice_sees, "Alice can see Bob's workspace — tenant boundary broken"
    assert ws_b["id"] in bob_sees
    assert ws_a["id"] not in bob_sees, "Bob can see Alice's workspace — tenant boundary broken"


def test_secondary_user_shares_the_inviting_primarys_workspaces(two_instances):
    """An SIU is an instance-mate: they see the PIU's workspaces, and only those.

    This is the flip side of isolation and is easy to over-correct — a change that scoped by
    per-workspace membership instead of by Instance would pass the isolation assertions above
    while silently making every invited user see an empty app.
    """
    env = two_instances
    conn = env["conn"]

    ws_a = store.create_workspace(conn, "Alice Private", owner=env["alice"])
    ws_b = store.create_workspace(conn, "Bob Private", owner=env["bob"])

    sam_sees = store.accessible_workspace_ids(conn, "u-sam")

    assert ws_a["id"] in sam_sees, "SIU cannot see their own Instance's workspace"
    assert ws_b["id"] not in sam_sees, "SIU can see another Instance — tenant boundary broken"


def test_boards_follow_the_same_boundary_as_workspaces(two_instances):
    """Boards inherit their workspace's Instance, so board scoping must agree with workspace scoping.

    Asserted separately because `accessible_board_ids` is a *different function* with its own
    query; the two drifting apart is exactly the kind of bug that leaks a board through analytics
    or chat while the workspace list still looks correct.
    """
    env = two_instances
    conn = env["conn"]

    ws_a = store.create_workspace(conn, "Alice Private", owner=env["alice"])
    ws_b = store.create_workspace(conn, "Bob Private", owner=env["bob"])
    board_a = store.create_board(conn, "A board", "", [{"title": "To Do"}], [], workspace_id=ws_a["id"])
    board_b = store.create_board(conn, "B board", "", [{"title": "To Do"}], [], workspace_id=ws_b["id"])

    alice_boards = store.accessible_board_ids(conn, "u-alice")
    bob_boards = store.accessible_board_ids(conn, "u-bob")
    sam_boards = store.accessible_board_ids(conn, "u-sam")

    assert board_a["id"] in alice_boards
    assert board_b["id"] not in alice_boards, "Alice can see Bob's board — tenant boundary broken"
    assert board_b["id"] in bob_boards
    assert board_a["id"] not in bob_boards, "Bob can see Alice's board — tenant boundary broken"
    assert board_a["id"] in sam_boards and board_b["id"] not in sam_boards


def test_unknown_user_sees_nothing(two_instances):
    """Fail closed: a user with no Instance gets an empty scope, never the whole table.

    The dangerous bug returns *everything* for an unrecognised caller. Note the setup: there must
    be a workspace **and a board** in the database for this to mean anything. With no board,
    `accessible_board_ids` returns an empty set simply because the table is empty — and the
    assertion passes against a deliberately fail-open mutation. An emptiness assertion is only as
    strong as the data it could have leaked.
    """
    env = two_instances
    conn = env["conn"]
    workspace = store.create_workspace(conn, "Alice Private", owner=env["alice"])
    store.create_board(conn, "A board", "", [{"title": "To Do"}], [], workspace_id=workspace["id"])

    # Sanity: the rows exist and ARE visible to their owner, so an empty result below is the
    # scoping doing its job rather than an empty database.
    assert store.accessible_workspace_ids(conn, "u-alice")
    assert store.accessible_board_ids(conn, "u-alice")

    assert store.accessible_workspace_ids(conn, "u-nobody") == set()
    assert store.accessible_board_ids(conn, "u-nobody") == set()


def test_invited_secondary_lands_in_the_inviters_instance(two_instances):
    """`invite_secondary_user` must place the SIU in the actor's Instance — the mechanism that
    makes SIUs instance-mates in the first place."""
    env = two_instances
    conn = env["conn"]

    member = store.invite_secondary_user(
        conn, env["instance_a"], "u-alice", "invitee@example.com", 5
    )

    assert member["instanceId"] == env["instance_a"]
    assert member["role"] == store.INSTANCE_ROLE_SIU
    assert store.get_member_by_email(conn, env["instance_b"], "invitee@example.com") is None


def test_secondary_user_cannot_invite(two_instances):
    """Only a PIU may invite. An SIU escalating to inviter would grow another user's Instance."""
    env = two_instances
    conn = env["conn"]

    with pytest.raises(Exception) as excinfo:
        store.invite_secondary_user(conn, env["instance_a"], "u-sam", "x@example.com", 5)

    assert getattr(excinfo.value, "status_code", None) == 403


def test_primary_user_cannot_invite_into_another_instance(two_instances):
    """Alice acting against Bob's Instance must be refused — she holds no role there.

    Without this, knowing an instance id would be enough to plant a member in someone else's
    tenant, which is a privilege-escalation path into another user's data.
    """
    env = two_instances
    conn = env["conn"]

    with pytest.raises(Exception) as excinfo:
        store.invite_secondary_user(conn, env["instance_b"], "u-alice", "x@example.com", 5)

    assert getattr(excinfo.value, "status_code", None) == 403
