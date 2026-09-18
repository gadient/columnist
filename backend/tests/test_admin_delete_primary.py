"""`python -m app.admin delete-primary <email>` — tear down a PIU's Instance in one shot.

The operator's "remove this user" action: delete the PIU, their whole Instance comes down, and every
member's Cognito login is deleted (a no-op here — the suite pins Cognito off). Re-invite afterwards.
The coupling that matters: you cannot delete a PIU *without* the Instance coming down, and an SIU is
never mistaken for a PIU.
"""
from __future__ import annotations

import argparse
import sqlite3

import pytest

from app import admin, migrations, store
from app.config import settings
from app.db import get_connection
from app.note_imports import store as import_store


@pytest.fixture
def conn(tmp_path, monkeypatch) -> sqlite3.Connection:
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "delete_primary.sqlite"))
    migrations.run_migrations()
    connection = get_connection()
    yield connection
    connection.close()


def _args(email: str) -> argparse.Namespace:
    return argparse.Namespace(email=email, yes=True)


def test_delete_primary_tears_down_the_instance_and_allows_re_invite(conn):
    space = store.create_primary_space(conn, "primary@example.com", "Primary Space", 10, 5)
    instance_id = space["instance"]["id"]
    assert store.get_instance(conn, instance_id) is not None

    rc = admin.cmd_delete_primary(_args("primary@example.com"))
    assert rc == 0

    # A fresh read (the command committed on its own connection) — Instance and membership are gone.
    fresh = get_connection()
    try:
        assert store.get_instance(fresh, instance_id) is None
        assert fresh.execute(
            "SELECT 1 FROM instance_members WHERE lower(email) = ?", ("primary@example.com",)
        ).fetchone() is None
        # Re-invite works: a fresh space can be created for the same email.
        again = store.create_primary_space(fresh, "primary@example.com", "Primary Space 2", 10, 5)
        assert again["instance"]["id"] != instance_id
    finally:
        fresh.close()


def test_delete_primary_refuses_an_siu(conn):
    """An SIU is not a PIU — delete-primary must not tear down the Instance for a secondary member."""
    space = store.create_primary_space(conn, "owner@example.com", "Owner Space", 10, 5)
    instance_id = space["instance"]["id"]
    owner, _ = _sign_in(conn, "u-owner", "owner@example.com")
    store.invite_secondary_user(conn, instance_id, owner["id"], "sidekick@example.com", 5)

    rc = admin.cmd_delete_primary(_args("sidekick@example.com"))
    assert rc == 1

    fresh = get_connection()
    try:
        assert store.get_instance(fresh, instance_id) is not None  # Instance untouched
    finally:
        fresh.close()


def test_delete_primary_unknown_email_is_a_clean_error(conn):
    assert admin.cmd_delete_primary(_args("nobody@example.com")) == 1


def _seed_note_import_and_feedback(conn: sqlite3.Connection, instance_id: str, tag: str) -> str:
    """Give an Instance the two kinds of row that hold the tenant's own words: a note-import
    session (its excerpt/filename/hash metadata, plus a recommendation and an application) and a
    piece of feedback. Children go in with raw SQL so the test pins the cascade, not the
    note-import model shapes."""
    board = store.create_board(
        conn, title=f"Board {tag}", description="", columns=[{"title": "To Do"}], team_members=[]
    )
    conn.execute("UPDATE boards SET instance_id = ? WHERE id = ?", (instance_id, board["id"]))
    session = import_store.create_session(
        conn,
        board_id=board["id"],
        instance_id=instance_id,
        actor_id=None,
        board_ver=0,
        input_mode="paste",
        meeting_date="2026-01-05",
        user_timezone="UTC",
        original_filename=f"{tag}-standup.txt",
        declared_media_type="text/plain",
        detected_media_type="text/plain",
        byte_size=42,
        content_sha256="0" * 64,
        extracted_char_count=42,
        input_tokens_estimated=11,
        status="ready",
    )
    now = "2026-01-05T00:00:00Z"
    conn.execute(
        """
        INSERT INTO note_import_recommendations (
            id, session_id, position, model_output_json, evidence_json,
            state, created_at, updated_at
        ) VALUES (?, ?, 0, '{}', '[{"excerpt": "ship the thing", "locator": "L1"}]',
                  'pending', ?, ?)
        """,
        (f"rec_{tag}", session["id"], now, now),
    )
    conn.execute(
        """
        INSERT INTO note_import_applications (
            id, session_id, recommendation_id, instance_id, board_id,
            idempotency_key, approved_snapshot_sha256, status, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'succeeded', ?)
        """,
        (f"app_{tag}", session["id"], f"rec_{tag}", instance_id, board["id"], tag, "0" * 64, now),
    )
    store.create_feedback(
        conn, category="general", message=f"note from {tag}", instance_id=instance_id
    )
    conn.commit()
    return session["id"]


def test_delete_primary_takes_the_note_excerpts_and_feedback_with_it(conn):
    """Takedown means the tenant's words are gone, not just their boards.

    Note-import sessions keep excerpts, the original filename and the input hash, and they must
    die with the Instance; feedback is the user's own prose. Neither table has a foreign key
    to `instances`, so nothing deletes them unless `delete_instance` says so — and a second
    Instance's rows must survive the same takedown.
    """
    doomed = store.create_primary_space(conn, "leaving@example.com", "Leaving", 10, 5)["instance"]["id"]
    kept = store.create_primary_space(conn, "staying@example.com", "Staying", 10, 5)["instance"]["id"]
    doomed_session = _seed_note_import_and_feedback(conn, doomed, "doomed")
    kept_session = _seed_note_import_and_feedback(conn, kept, "kept")

    assert admin.cmd_delete_primary(_args("leaving@example.com")) == 0

    fresh = get_connection()
    try:
        def one(sql: str, args: tuple) -> int:
            return int(fresh.execute(sql, args).fetchone()["c"])

        assert one("SELECT COUNT(*) AS c FROM note_import_sessions WHERE instance_id = ?", (doomed,)) == 0
        assert one("SELECT COUNT(*) AS c FROM note_import_recommendations WHERE session_id = ?", (doomed_session,)) == 0
        assert one("SELECT COUNT(*) AS c FROM note_import_applications WHERE session_id = ?", (doomed_session,)) == 0
        assert one("SELECT COUNT(*) AS c FROM feedback WHERE instance_id = ?", (doomed,)) == 0

        # The other tenant is untouched — a takedown is not a purge.
        assert one("SELECT COUNT(*) AS c FROM note_import_sessions WHERE instance_id = ?", (kept,)) == 1
        assert one("SELECT COUNT(*) AS c FROM note_import_recommendations WHERE session_id = ?", (kept_session,)) == 1
        assert one("SELECT COUNT(*) AS c FROM note_import_applications WHERE session_id = ?", (kept_session,)) == 1
        assert one("SELECT COUNT(*) AS c FROM feedback WHERE instance_id = ?", (kept,)) == 1
    finally:
        fresh.close()


def _sign_in(conn: sqlite3.Connection, user_id: str, email: str):
    user = {"id": user_id, "email": email}
    store.upsert_user(conn, user)
    instance_id, _ = store.provision_instance_membership(conn, user)
    return user, instance_id
