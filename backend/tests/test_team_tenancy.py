"""Teams are per-Instance.

**Why this file exists.** Teams used to be global: the `/api/v1/teams` routes checked that a caller
was signed in and nothing else, so any invited user could list, rename or delete every other
Instance's teams, and a board's team label was shared state between tenants. That was a real
authorization defect, disclosed in SECURITY.md and now fixed. This file is what keeps it fixed.

**What is asserted.** The store-layer functions the routes call — the same approach as
`test_instance_isolation.py`, and for the same reason: `conftest.py` pins Cognito off, so an
API-level test would exercise the unscoped path and prove the opposite of what we want. The
`instance_id` argument these functions take is exactly what `analytics_api._caller_instance`
supplies from the session.

**Four rules, and the reason for each.**

- A team belongs to the Instance that created it, and is invisible to every other one.
- The six seeded defaults from migration 0004, identified **by id** (`store.SEEDED_TEAM_IDS`), stay
  *visible* to everyone because demo boards reference them, and are *read-only* once sign-in is
  configured. Nobody should be able to rename a row another tenant's boards point at.
- An unowned row that is not one of those six can only come from a database predating 0016. It is
  invisible to everyone: reading NULL as "shared" would publish one tenant's teams to all the
  others, which is the defect this scoping exists to prevent. Migration 0017 recovers the owner
  where the boards make it unambiguous.
- A refused write answers 404, not 403, so a caller cannot probe for the existence of another
  Instance's teams by watching the status code.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import migrations, store
from app.config import settings
from app.db import get_connection


@pytest.fixture
def conn(tmp_path, monkeypatch) -> sqlite3.Connection:
    """A migrated, empty database per test, so one test's teams cannot be seen by another."""
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "teams.sqlite"))
    migrations.run_migrations()
    connection = get_connection()
    yield connection
    connection.close()


@pytest.fixture
def two_instances(conn):
    """Two Instances, each with a team of its own."""
    a = store.create_primary_space(conn, "alice@example.com", "Alice Space", 10, 5)
    b = store.create_primary_space(conn, "bob@example.com", "Bob Space", 10, 5)
    instance_a, instance_b = a["instance"]["id"], b["instance"]["id"]

    team_a = store.create_team(conn, "Alice Platform", "bg-blue-500", "APLAT", "", instance_a)
    team_b = store.create_team(conn, "Bob Platform", "bg-green-500", "BPLAT", "", instance_b)

    return {
        "conn": conn,
        "instance_a": instance_a,
        "instance_b": instance_b,
        "team_a": team_a,
        "team_b": team_b,
    }


def _seeded_default(conn) -> dict:
    """One of the six shared rows created by migration 0004."""
    row = conn.execute("SELECT * FROM teams WHERE id = ?", ("team_eng",)).fetchone()
    assert row is not None, "migration 0004 should have seeded the shared defaults"
    return dict(row)


def _insert_legacy_team(conn, team_id: str, name: str) -> None:
    """A team from before 0016: no owner, and not one of the seeded ids."""
    conn.execute(
        "INSERT INTO teams (id, name, color, jira_prefix, description, created_at, updated_at, instance_id)"
        " VALUES (?,?,?,?,?,?,?,NULL)",
        (team_id, name, "bg-gray-500", None, "", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.commit()


# ── Visibility ────────────────────────────────────────────────────────────────────────────────

def test_a_team_is_invisible_to_another_instance(two_instances):
    conn, instance_b = two_instances["conn"], two_instances["instance_b"]

    names = {t["name"] for t in store.list_teams(conn, instance_b)}

    assert "Bob Platform" in names
    assert "Alice Platform" not in names


def test_the_seeded_defaults_stay_visible_to_everyone(two_instances):
    conn = two_instances["conn"]
    default_name = _seeded_default(conn)["name"]

    for instance in (two_instances["instance_a"], two_instances["instance_b"]):
        names = {t["name"] for t in store.list_teams(conn, instance)}
        assert default_name in names, "demo boards reference these rows; hiding them breaks them"


def test_with_sign_in_off_every_team_is_listed(two_instances):
    """The local posture: no caller identity, nothing scoped — the same as every other route."""
    conn = two_instances["conn"]

    names = {t["name"] for t in store.list_teams(conn, None)}

    assert {"Alice Platform", "Bob Platform"} <= names


# ── Writes ────────────────────────────────────────────────────────────────────────────────────

def test_another_instance_cannot_rename_a_team(two_instances):
    conn, team_a, instance_b = two_instances["conn"], two_instances["team_a"], two_instances["instance_b"]

    with pytest.raises(HTTPException) as exc:
        store.update_team(conn, team_a["id"], "Renamed by Bob", None, None, None, instance_b)

    assert exc.value.status_code == 404, "404, not 403: a refusal must not confirm the team exists"
    assert store.list_teams(conn, two_instances["instance_a"])[0]["name"] == "Alice Platform"


def test_another_instance_cannot_delete_a_team(two_instances):
    conn, team_a, instance_b = two_instances["conn"], two_instances["team_a"], two_instances["instance_b"]

    with pytest.raises(HTTPException) as exc:
        store.delete_team(conn, team_a["id"], instance_b)

    assert exc.value.status_code == 404
    assert [t["name"] for t in store.list_teams(conn, two_instances["instance_a"])
            if t["id"] == team_a["id"]] == ["Alice Platform"]


def test_an_instance_can_still_change_its_own_team(two_instances):
    conn, team_a, instance_a = two_instances["conn"], two_instances["team_a"], two_instances["instance_a"]

    updated = store.update_team(conn, team_a["id"], "Alice Core", None, None, None, instance_a)

    assert updated["name"] == "Alice Core"
    store.delete_team(conn, team_a["id"], instance_a)
    assert team_a["id"] not in {t["id"] for t in store.list_teams(conn, instance_a)}


def test_the_seeded_defaults_are_read_only_when_signed_in(two_instances):
    """Shared rows belong to no Instance, and other tenants' boards point at them."""
    conn, instance_a = two_instances["conn"], two_instances["instance_a"]
    default = _seeded_default(conn)

    with pytest.raises(HTTPException) as exc:
        store.update_team(conn, default["id"], "Hijacked", None, None, None, instance_a)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        store.delete_team(conn, default["id"], instance_a)
    assert exc.value.status_code == 404

    assert conn.execute("SELECT name FROM teams WHERE id = ?", (default["id"],)).fetchone()["name"] == default["name"]


def test_a_new_team_is_stamped_with_its_creator_instance(two_instances):
    conn, instance_b = two_instances["conn"], two_instances["instance_b"]

    created = store.create_team(conn, "Bob Design", "bg-pink-500", "BDES", "", instance_b)

    row = conn.execute("SELECT instance_id FROM teams WHERE id = ?", (created["id"],)).fetchone()
    assert row["instance_id"] == instance_b, "an unstamped row would be shared with every tenant"


# ── Upgraded databases ────────────────────────────────────────────────────────────────────────
#
# 0016 left every pre-existing team NULL. The first rule read NULL as "shared default", which on a
# database upgraded from before 0016 would have handed one tenant's own teams to every other one.
# Ownership is now decided by the seeded ids, and 0017 backfills what it can from the boards.

def test_an_unowned_legacy_team_is_invisible_to_everyone(two_instances):
    conn = two_instances["conn"]
    _insert_legacy_team(conn, "team_legacy", "Legacy Team")

    for instance in (two_instances["instance_a"], two_instances["instance_b"]):
        names = {t["name"] for t in store.list_teams(conn, instance)}
        assert "Legacy Team" not in names, "an unowned row must not be shown to every tenant"


def test_an_unowned_legacy_team_cannot_be_written(two_instances):
    conn, instance_a = two_instances["conn"], two_instances["instance_a"]
    _insert_legacy_team(conn, "team_legacy", "Legacy Team")

    with pytest.raises(HTTPException) as exc:
        store.update_team(conn, "team_legacy", "Claimed", None, None, None, instance_a)
    assert exc.value.status_code == 404


def _run_backfill(conn) -> None:
    """Apply 0017's SQL to the current database — the upgrade path, not a fresh install."""
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "0017_team_backfill.sql").read_text()
    conn.executescript(sql)
    conn.commit()


def test_the_backfill_gives_a_legacy_team_to_the_instance_whose_boards_use_it(two_instances):
    conn, instance_a = two_instances["conn"], two_instances["instance_a"]
    _insert_legacy_team(conn, "team_legacy", "Legacy Team")
    conn.execute(
        "INSERT INTO boards (id, title, created_at, updated_at, instance_id, team_id)"
        " VALUES (?,?,?,?,?,?)",
        ("board-a", "Alice Board", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", instance_a, "team_legacy"),
    )
    conn.commit()

    _run_backfill(conn)

    assert "Legacy Team" in {t["name"] for t in store.list_teams(conn, instance_a)}
    assert "Legacy Team" not in {t["name"] for t in store.list_teams(conn, two_instances["instance_b"])}
    store.update_team(conn, "team_legacy", "Alice Legacy", None, None, None, instance_a)


def test_the_backfill_leaves_an_ambiguous_team_unowned(two_instances):
    """Two Instances' boards point at the same legacy team: guessing an owner would be a leak."""
    conn = two_instances["conn"]
    _insert_legacy_team(conn, "team_shared_legacy", "Shared Legacy")
    for suffix, instance in (("a", two_instances["instance_a"]), ("b", two_instances["instance_b"])):
        conn.execute(
            "INSERT INTO boards (id, title, created_at, updated_at, instance_id, team_id)"
            " VALUES (?,?,?,?,?,?)",
            (f"board-{suffix}", f"Board {suffix}", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
             instance, "team_shared_legacy"),
        )
    conn.commit()

    _run_backfill(conn)

    row = conn.execute("SELECT instance_id FROM teams WHERE id = ?", ("team_shared_legacy",)).fetchone()
    assert row["instance_id"] is None
    assert "Shared Legacy" not in {t["name"] for t in store.list_teams(conn, two_instances["instance_a"])}


def test_the_backfill_leaves_the_seeded_defaults_shared(two_instances):
    conn = two_instances["conn"]

    _run_backfill(conn)

    row = conn.execute("SELECT instance_id FROM teams WHERE id = ?", ("team_eng",)).fetchone()
    assert row["instance_id"] is None
    assert "Engineering" in {t["name"] for t in store.list_teams(conn, two_instances["instance_b"])}
