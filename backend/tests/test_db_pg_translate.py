"""Unit tests for the SQLite→PostgreSQL migration-script shim in app.db.

These are pure string transforms — no database. The headline case is a regression that breaks a
Postgres deploy: a `;` *inside* a `--` comment must not split a statement. SQLite's `executescript`
is a real parser and tolerates it; a naive `split(";")` does not, and chops a CREATE TABLE in half.
"""
from app.db import _strip_sql_line_comments, _translate_script


def test_semicolon_inside_a_comment_does_not_split_the_statement():
    # This is 0008_instances.sql in miniature: the comment contains "creator; NULL".
    script = (
        "CREATE TABLE instances (\n"
        "  id TEXT PRIMARY KEY,\n"
        "  owner_id TEXT,  -- Cognito sub of the owner/creator; NULL for the backfilled default\n"
        "  name TEXT NOT NULL\n"
        ");"
    )
    stmts = _translate_script(script)
    assert len(stmts) == 1                       # NOT split at the comment's semicolon
    assert stmts[0].startswith("CREATE TABLE instances")
    assert "name TEXT NOT NULL" in stmts[0]      # the tail survived
    assert "--" not in stmts[0]                  # comment stripped
    assert "creator" not in stmts[0]


def test_line_comments_are_stripped_but_quoted_dashes_are_kept():
    assert _strip_sql_line_comments("SELECT 1;  -- a comment") == "SELECT 1;  "
    # A `--` inside a string literal is data, not a comment — must be preserved.
    kept = _strip_sql_line_comments("INSERT INTO t (v) VALUES ('a--b');  -- trailing")
    assert "'a--b'" in kept
    assert "trailing" not in kept


def test_multi_statement_script_splits_on_real_semicolons():
    script = "CREATE TABLE a (id TEXT);\nCREATE TABLE b (id TEXT);"
    stmts = _translate_script(script)
    assert len(stmts) == 2
    assert stmts[0].startswith("CREATE TABLE a")
    assert stmts[1].startswith("CREATE TABLE b")


def test_dialect_translations_still_apply():
    script = (
        "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')));\n"
        "INSERT OR IGNORE INTO t (id) VALUES (1);"
    )
    stmts = _translate_script(script)
    joined = "\n".join(stmts)
    assert "BIGSERIAL PRIMARY KEY" in joined          # AUTOINCREMENT → BIGSERIAL
    assert "now()::text" in joined                    # datetime('now') → now()::text
    assert "AUTOINCREMENT" not in joined
    assert "datetime('now')" not in joined
    ins = next(s for s in stmts if s.startswith("INSERT"))
    assert "OR IGNORE" not in ins                      # INSERT OR IGNORE → INSERT … ON CONFLICT
    assert ins.rstrip().endswith("ON CONFLICT DO NOTHING")
