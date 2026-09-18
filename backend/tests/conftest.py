"""Shared test setup.

**Why the env overriding below is not optional.** A developer's `backend/.env` may set `COGNITO_*`,
which makes `settings.cognito_enabled` true and every `/api/v1` route demand a login — locally, and
in tests. Tests that hit the API would then 401 for reasons having nothing to do with what they
assert. They pin the setting instead of relying on a developer's `.env` happening to be empty.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Pin Cognito OFF for the whole session, here at module top — before any `app` module is imported,
# so `settings` is constructed with it off and `app.main` wires the routers *without* auth deps.
# Empty env vars override the `.env` file (env > .env in pydantic-settings). This is what the
# module docstring above promises; without it the API tests 401 whenever a `.env` configures Cognito.
os.environ["COGNITO_REGION"] = ""
os.environ["COGNITO_USER_POOL_ID"] = ""
os.environ["COGNITO_APP_CLIENT_ID"] = ""

# Same reasoning for the agent backend. A developer's `backend/.env` may set AGENT_BACKEND=bedrock so
# `dev-up.sh` alone runs the real agents — and the tests asserting *offline* behaviour ("proposes
# nothing", "assistant unavailable") would then read bedrock. Pinning it keeps the free suite
# model-free (no credentials, no spend) and independent of whatever backend a machine happens to be
# configured for. Override per-test, not per-machine.
os.environ.setdefault("AGENT_BACKEND", "offline")

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fx():
    """Read an authored fixture by name: `fx("spoofed.docx")` -> bytes."""
    def _read(name: str) -> bytes:
        path = FIXTURES / name
        assert path.is_file(), f"missing fixture: {name}"
        return path.read_bytes()
    return _read


@pytest.fixture
def two_boards(tmp_path, monkeypatch):
    """Two boards, each with a high-priority card, a blocked card, and a completed card.

    Shared by the agent tool tests and the agent loop tests. Two boards rather than one is the
    whole point: with a single board, "the fence excluded it" and "the database was empty" produce
    identical assertions, and the tenant-isolation tests would pass against a broken fence.

    Only the first card on each board is high-priority and open, so counts asserted by the
    due-date/priority tool tests stay at 1; the extra cards exist for the analytics tools (blocked,
    workload, velocity) and are deliberately medium-priority or completed so they don't disturb
    them. The blocked card points at the *other* board's card as well as its own, which is what
    makes the cross-tenant blocker-title test meaningful.
    """
    from app import migrations, store
    from app.config import settings
    from app.db import get_connection

    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "tools.sqlite"))
    migrations.run_migrations()
    conn = get_connection()
    try:
        mine = store.create_board(
            conn, "Mine", "", [{"title": "To Do"}], [{"name": "Priya Sharma", "initials": "PS", "color": "#000"}]
        )
        theirs = store.create_board(
            conn, "Theirs", "", [{"title": "To Do"}], [{"name": "Priya Sharma", "initials": "PS", "color": "#000"}]
        )
        pair = ((mine, "My", theirs), (theirs, "Their", mine))
        for board, label, other in pair:
            col = board["columnOrder"][0]
            member = board["teamMembers"][0]["id"]
            base = f"card_{board['id']}"
            main_id, blocked_id, done_id = f"{base}_1", f"{base}_2", f"{base}_3"

            def card(cid, title, **over):
                return {
                    "id": cid,
                    "title": title,
                    "description": "",
                    "priority": "medium",
                    "dueDate": "2026-01-01",
                    "assignees": [member],
                    "createdAt": "2026-01-01T00:00:00Z",
                    "completed": False,
                    "storyPoints": 3,
                    "labels": [],
                    "blockedBy": [],
                    "dependsOn": [],
                    **over,
                }

            store.sync_board_snapshot(
                conn,
                board["id"],
                {
                    "id": board["id"],
                    "projectInfo": board["projectInfo"],
                    "columnOrder": board["columnOrder"],
                    "columns": {col: {**board["columns"][col], "cardIds": [main_id, blocked_id, done_id]}},
                    "cards": {
                        main_id: card(main_id, f"{label} high priority card", priority="high"),
                        # Blocked by its own board's card AND the other board's card — the second
                        # link is what a fenced blocker-title lookup must refuse to resolve.
                        blocked_id: card(
                            blocked_id,
                            f"{label} blocked card",
                            blockedBy=[main_id, f"card_{other['id']}_1"],
                        ),
                        done_id: card(done_id, f"{label} finished card", completed=True, storyPoints=5),
                    },
                    "teamMembers": board["teamMembers"],
                },
            )
    finally:
        conn.close()
    return mine["id"], theirs["id"]
