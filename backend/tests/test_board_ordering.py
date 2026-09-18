"""Board listing order — stable, and independent of when a board was last touched.

**Why this file exists.** The board grid used to shuffle on every "Load Demo". The cause was not
randomness: `list_workspace_boards` ordered by `updated_at DESC`, `_now()` truncated to whole
seconds, and `loadDemoData` creates each board and then immediately syncs it — so ten boards
landed in a handful of one-second buckets, chunked wherever the timing happened to fall, and `DESC`
returned those buckets newest-first. The result both reversed the authored order and re-grouped
differently run to run, so the board a first-time user landed on was effectively arbitrary. That
matters beyond tidiness: whether the note-import feature appeared to exist depended on which board
you happened to open.

**What is guaranteed now:** `ORDER BY created_at, id` — creation order, with a deterministic
tie-break. Two consequences worth stating plainly, because the tests below encode both:

- Boards created in *different* seconds come back in creation order.
- Boards created within the *same* second used to tie on `created_at` and fall back to `id`, which
  is arbitrary. That was left as an accepted residual here — and it was the half a user actually
  noticed: a demo load writes ten boards inside one second, so the grid came
  back in a fresh arbitrary order every time, while every test above passed because each one sets
  `created_at` by hand. **Closed** by giving `_now()` microsecond resolution, so creation order
  survives; `test_boards_created_back_to_back_keep_their_authored_order` uses the real clock and is
  the test that would have caught it.

**Why not `rowid`.** SQLite's `rowid` gives true insertion order, and every test here passes with
it — but a Postgres deployment returns zero boards, because Postgres has no `rowid` and fails the
query outright. These tests run on SQLite and *cannot* catch
that class of bug; the guard is the comment in `store.py` and the rule that SQL must run on both
backends. Hence `test_the_ordering_sql_is_portable` below, which is a crude backstop, not a
substitute for running against Postgres.
"""
from __future__ import annotations

import sqlite3

import pytest

from app import migrations, store
from app.config import settings
from app.db import get_connection


@pytest.fixture
def conn(tmp_path, monkeypatch) -> sqlite3.Connection:
    """A migrated, empty database per test."""
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "ordering.sqlite"))
    migrations.run_migrations()
    connection = get_connection()
    yield connection
    connection.close()


def _make_board(conn: sqlite3.Connection, title: str, workspace_id: str) -> str:
    board = store.create_board(
        conn,
        title=title,
        description="",
        columns=[{"title": "To Do"}],
        team_members=[],
        workspace_id=workspace_id,
    )
    return board["id"]


def _set_times(
    conn: sqlite3.Connection, board_id: str, *, created: str | None = None, updated: str | None = None
) -> None:
    if created is not None:
        conn.execute("UPDATE boards SET created_at = ? WHERE id = ?", (created, board_id))
    if updated is not None:
        conn.execute("UPDATE boards SET updated_at = ? WHERE id = ?", (updated, board_id))
    conn.commit()


def _titles(conn: sqlite3.Connection, workspace_id: str) -> list[str]:
    return [b["projectInfo"]["title"] for b in store.list_workspace_boards(conn, workspace_id)]


def test_boards_come_back_in_creation_order(conn):
    """The primary sort key. Timestamps are set explicitly so the assertion is about the ordering
    itself, independent of how finely the clock happens to tick."""
    ws = store.create_workspace(conn, "Demo Workspace")
    titles = [f"Board {i:02d}" for i in range(1, 6)]
    ids = [_make_board(conn, t, ws["id"]) for t in titles]
    for i, board_id in enumerate(ids):
        _set_times(conn, board_id, created=f"2026-07-19T10:00:{i:02d}Z")

    assert _titles(conn, ws["id"]) == titles


def test_a_recently_updated_board_does_not_jump_to_the_front(conn):
    """The regression this file exists for: `updated_at` must not influence order at all.

    Shaped like a real demo load — later boards carry later `updated_at`, because each is synced
    right after it is created. Under the old `ORDER BY updated_at DESC` this came back reversed.
    """
    ws = store.create_workspace(conn, "Demo Workspace")
    titles = [f"Board {i:02d}" for i in range(1, 6)]
    ids = [_make_board(conn, t, ws["id"]) for t in titles]
    for i, board_id in enumerate(ids):
        _set_times(
            conn, board_id, created=f"2026-07-19T10:00:{i:02d}Z", updated=f"2026-07-19T11:00:{i:02d}Z"
        )

    assert _titles(conn, ws["id"]) == titles, "order must not depend on when a board was last updated"


def test_editing_a_board_does_not_move_it_in_the_grid(conn):
    """Touching a board in the *middle*, deliberately: touching the first would assert nothing,
    since it is already first and the expectation would hold under the old buggy query too."""
    ws = store.create_workspace(conn, "Demo Workspace")
    titles = [f"Board {i:02d}" for i in range(1, 5)]
    ids = [_make_board(conn, t, ws["id"]) for t in titles]
    for i, board_id in enumerate(ids):
        _set_times(conn, board_id, created=f"2026-07-19T10:00:{i:02d}Z")

    _set_times(conn, ids[2], updated="2026-07-19T23:59:59Z")  # newest timestamp, middle position

    assert _titles(conn, ws["id"]) == titles, "an edited board must not jump to the front of the grid"


def test_boards_created_back_to_back_keep_their_authored_order(conn):
    """The residual this file used to document, now closed.

    Every test above sets `created_at` by hand, so none of them could see the real defect: with
    whole-second timestamps, ten boards written inside one second all tied and fell through to the
    `id` tie-break — a random uuid hex. Order was stable for a given load and *different on the
    next one*, so "Load Demo" dealt the grid a fresh arbitrary order each time. `_now()` now carries
    microseconds, so creation order survives.

    Deliberately uses the real clock and no `_set_times`: that is the code path a demo load takes.
    """
    ws = store.create_workspace(conn, "Demo Workspace")
    titles = [f"Board {i:02d}" for i in range(1, 11)]
    for title in titles:
        _make_board(conn, title, ws["id"])

    assert _titles(conn, ws["id"]) == titles, (
        "boards created back-to-back must come back in the order they were created"
    )


def test_timestamps_are_sub_second(conn):
    """Guards the mechanism directly, so a future 'tidy up the timestamp format' cannot quietly
    reinstate the tie without a failure that explains itself."""
    ws = store.create_workspace(conn, "Demo Workspace")
    stamps = [
        conn.execute("SELECT created_at FROM boards WHERE id = ?", (_make_board(conn, f"B{i}", ws["id"]),))
        .fetchone()["created_at"]
        for i in range(5)
    ]
    assert len(set(stamps)) == len(stamps), f"created_at must distinguish rapid inserts, got {stamps}"


def test_repeated_reads_keep_a_stable_order(conn):
    """Reading the same grid twice must return the same order. Instability was the actual
    complaint: whatever the sort key resolves to, it has to resolve to it every time."""
    ws = store.create_workspace(conn, "Demo Workspace")
    for i in range(1, 8):
        _make_board(conn, f"Board {i:02d}", ws["id"])  # back-to-back, on the real clock

    first = _titles(conn, ws["id"])
    assert len(first) == 7
    for _ in range(4):
        assert _titles(conn, ws["id"]) == first, "repeated reads must not reshuffle"


def test_the_ordering_sql_is_portable(conn):
    """Backstop for the `rowid` outage: `rowid` is SQLite-only, and Postgres deployments have none.

    Crude on purpose — it reads the source rather than the database, because the suite runs on
    SQLite and so cannot execute the Postgres path. It only catches a re-introduction of this
    specific footgun; it is not a substitute for exercising Postgres.
    """
    import inspect

    source = inspect.getsource(store.list_workspace_boards) + inspect.getsource(store.list_boards)
    sql_lines = [ln for ln in source.splitlines() if "ORDER BY" in ln]
    assert sql_lines, "expected an ORDER BY in the board listing queries"
    for line in sql_lines:
        assert "rowid" not in line.lower(), (
            "rowid is SQLite-only — on a Postgres deployment it returns zero boards"
        )
