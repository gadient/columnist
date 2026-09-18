from __future__ import annotations

from pathlib import Path

from .db import get_connection


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _ensure_migrations_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version TEXT PRIMARY KEY,
          applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )


def run_migrations() -> list[str]:
    conn = get_connection()
    try:
        _ensure_migrations_table(conn)

        applied_versions = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations")
        }

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        newly_applied: list[str] = []

        for migration_file in migration_files:
            version = migration_file.name
            if version in applied_versions:
                continue

            script = migration_file.read_text(encoding="utf-8")
            conn.executescript(script)
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
            newly_applied.append(version)

        conn.commit()
        return newly_applied
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
