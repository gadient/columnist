from __future__ import annotations

import re
import sqlite3

from .config import settings


def initialize_database_file() -> None:
    if settings.is_postgres:
        return  # nothing to create on disk; the server owns the database
    db_file = settings.sqlite_file
    db_file.parent.mkdir(parents=True, exist_ok=True)


# ── SQLite → PostgreSQL dialect shim ──────────────────────────────────────────
# The app speaks SQLite SQL everywhere (raw sqlite3, no ORM). Rather than rewrite
# every call site, we translate the handful of dialect differences on the way to
# psycopg so the same query strings run on both backends. Only these differ here:
#   • parameter placeholder:  ?              → %s
#   • upsert-ignore:          INSERT OR IGNORE … → INSERT … ON CONFLICT DO NOTHING
#   • current timestamp:      datetime('now')    → now()::text  (columns are TEXT)
#   • autoincrement PK:       INTEGER PRIMARY KEY AUTOINCREMENT → BIGSERIAL PRIMARY KEY
# The SQLite path below is left completely untouched — local dev behaviour is
# byte-for-byte what it was before this shim existed.

_DATETIME_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)
_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\b", re.IGNORECASE)
_INT_PK_AUTOINC = re.compile(
    r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE
)


def _translate(sql: str) -> str:
    """Translate a single SQLite statement to PostgreSQL."""
    s = sql
    if _INSERT_OR_IGNORE.search(s):
        s = _INSERT_OR_IGNORE.sub("INSERT", s)
        s = s.rstrip().rstrip(";")
        s = s + " ON CONFLICT DO NOTHING"
    s = _DATETIME_NOW.sub("now()::text", s)
    s = _INT_PK_AUTOINC.sub("BIGSERIAL PRIMARY KEY", s)
    s = s.replace("?", "%s")
    return s


def _strip_sql_line_comments(sql: str) -> str:
    """Drop `-- …` line comments, quote-aware, so a `;` *inside* a comment cannot split a
    statement when we naively `split(";")` below. SQLite's `executescript` is a real parser and
    tolerates this; the PG path is not, and a comment like `-- owner; NULL` was cutting a
    CREATE TABLE in half (syntax error at end of input). Per-line only — our migrations have no
    multi-line string literals — and it respects single/double quotes so a `--` inside a string
    value is preserved."""
    out_lines: list[str] = []
    for line in sql.splitlines():
        in_single = in_double = False
        cut = None
        i = 0
        while i < len(line):
            c = line[i]
            if c == "'" and not in_double:
                in_single = not in_single
            elif c == '"' and not in_single:
                in_double = not in_double
            elif c == "-" and i + 1 < len(line) and line[i + 1] == "-" and not in_single and not in_double:
                cut = i
                break
            i += 1
        out_lines.append(line if cut is None else line[:cut])
    return "\n".join(out_lines)


def _translate_script(script: str) -> list[str]:
    """Translate a multi-statement migration script into per-statement PG SQL."""
    s = _strip_sql_line_comments(script)
    s = _DATETIME_NOW.sub("now()::text", s)
    s = _INT_PK_AUTOINC.sub("BIGSERIAL PRIMARY KEY", s)
    statements: list[str] = []
    for raw in s.split(";"):
        stmt = raw.strip()
        if not stmt:
            continue
        if _INSERT_OR_IGNORE.match(stmt) or _INSERT_OR_IGNORE.search(stmt):
            stmt = _INSERT_OR_IGNORE.sub("INSERT", stmt) + " ON CONFLICT DO NOTHING"
        statements.append(stmt)
    return statements


class _PGConnection:
    """Thin sqlite3.Connection look-alike over a psycopg connection.

    Exposes only the surface the app actually uses: ``execute(sql, params)`` that
    returns a cursor (with ``fetchone``/``fetchall``/iteration and dict rows),
    ``executescript``, ``commit``, ``rollback``, ``close``. Everything else is
    delegated to the underlying psycopg connection.
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    def execute(self, sql: str, params=None):
        cur = self._conn.cursor()
        cur.execute(_translate(sql), tuple(params) if params is not None else None)
        return cur

    def executescript(self, script: str) -> None:
        for stmt in _translate_script(script):
            self._conn.execute(stmt)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _pg_connection() -> _PGConnection:
    import psycopg
    from psycopg.rows import dict_row

    url = settings.database_url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    conn = psycopg.connect(url, row_factory=dict_row)
    return _PGConnection(conn)


def get_connection():
    if settings.is_postgres:
        return _pg_connection()
    initialize_database_file()
    conn = sqlite3.connect(settings.sqlite_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
