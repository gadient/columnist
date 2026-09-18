"""Static guards for SQL that is valid on SQLite and invalid on Postgres.

The suite runs on SQLite; a deployment can run Postgres. `db.py` is a thin shim, not a
translation layer, so a green suite says nothing about whether a query works on Postgres — this
has caused two incidents (`ORDER BY rowid` returning zero boards, and the one below).

The upsert case:

    ON CONFLICT(user_id, day) DO UPDATE SET tokens_used = tokens_used + excluded.tokens_used

SQLite resolves the bare `tokens_used` on the right to the target table. Postgres refuses:

    psycopg.errors.AmbiguousColumn: column reference "tokens_used" is ambiguous

because it could mean the target row or the proposed (`excluded`) row. It has to be written
`daily_token_usage.tokens_used + excluded.tokens_used`, which both engines accept.

No test that executes SQL can catch this without a live Postgres, so this reads the source
instead. It is a lint, and lints are worth their keep only when they cover a class — so it scans
every upsert in `app/`, not the one that broke.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"

# Bare words that are legitimately unqualified on the right-hand side of an assignment.
SQL_LITERALS = {
    "null", "true", "false", "default", "current_timestamp", "current_date", "excluded",
    "case", "when", "then", "else", "end", "and", "or", "not", "is", "in",
}


def _sql_strings(source: str) -> list[str]:
    """Every triple-quoted string in a module — where this codebase keeps its SQL."""
    return re.findall(r'"""(.*?)"""', source, re.DOTALL)


def _unqualified_refs_in_do_update(sql: str) -> list[str]:
    """Bare column references on the right of a `DO UPDATE SET` assignment."""
    match = re.search(r"DO\s+UPDATE\s+SET\s+(.*)", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return []

    offenders: list[str] = []
    for assignment in match.group(1).split(","):
        if "=" not in assignment:
            continue
        rhs = assignment.split("=", 1)[1]
        # Identifiers not preceded by a dot and not followed by one — i.e. neither `x.y` nor a
        # qualifier itself. Skip function calls (`COALESCE(`) by requiring no trailing paren.
        for token in re.finditer(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)(?![\w.(])", rhs):
            word = token.group(1)
            if word.lower() not in SQL_LITERALS:
                offenders.append(word)
    return offenders


def _iter_app_sql():
    for path in sorted(APP_DIR.rglob("*.py")):
        for sql in _sql_strings(path.read_text(encoding="utf-8")):
            if re.search(r"DO\s+UPDATE\s+SET", sql, re.IGNORECASE):
                yield path, sql


def test_every_upsert_qualifies_its_column_references():
    """The Postgres bug, as a rule over the whole codebase."""
    failures = []
    for path, sql in _iter_app_sql():
        bare = _unqualified_refs_in_do_update(sql)
        if bare:
            failures.append(f"{path.name}: {', '.join(sorted(set(bare)))}")

    assert not failures, (
        "unqualified column reference(s) in a DO UPDATE SET — SQLite resolves these to the target "
        "table, Postgres raises AmbiguousColumn and the query fails outright there. "
        "Qualify with the table name (`mytable.col + excluded.col`):\n  " + "\n  ".join(failures)
    )


def test_at_least_one_upsert_was_actually_scanned():
    """A lint that silently matches nothing passes forever. If the SQL moves out of triple-quoted
    strings, or `rglob` stops finding app/, this fails instead of going quietly green."""
    assert list(_iter_app_sql()), "the scanner found no upserts at all — it is no longer checking anything"


@pytest.mark.parametrize(
    "sql,expected",
    [
        # The exact shape that fails on Postgres.
        ("ON CONFLICT(user_id, day) DO UPDATE SET tokens_used = tokens_used + excluded.tokens_used",
         ["tokens_used"]),
        # The fix.
        ("ON CONFLICT(user_id, day) DO UPDATE SET "
         "tokens_used = daily_token_usage.tokens_used + excluded.tokens_used", []),
        # store.py's form: every reference qualified.
        ("ON CONFLICT(id) DO UPDATE SET email = excluded.email, display_name = excluded.display_name", []),
        # Literals are fine unqualified.
        ("ON CONFLICT(id) DO UPDATE SET archived = true, note = NULL", []),
    ],
)
def test_the_scanner_itself_detects_the_pattern(sql, expected):
    """A lint nobody has watched fail is a lint nobody should trust."""
    assert _unqualified_refs_in_do_update(sql) == expected
