"""Per-user token rate limiting.

A single daily meter shared across chat and notes-to-cards. Only active when Cognito is
configured and a caller is resolved — local dev (no user) is unmetered, like tenancy. Chat records
the tokens the model actually reported (`agent_api.py`); `estimate_tokens` (~4 chars/token) is only
its pre-check, and understates a tool-calling turn badly. Notes-to-cards reserves the estimate up
front, because its providers do not report usage; its per-document cap lives in
`note_imports/api.py` rather than here, because it must raise a coded error the import UI can act
on instead of a bare HTTP one. A budget guard, not billing.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, status

from .config import settings
from .db import get_connection


def estimate_tokens(text: str) -> int:
    """Rough English heuristic: ~4 characters per token. Good enough for a budget guard."""
    return max(1, (len(text or "") + 3) // 4)


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def tokens_used_today(conn, user_id: str) -> int:
    row = conn.execute(
        "SELECT tokens_used FROM daily_token_usage WHERE user_id = ? AND day = ?",
        (user_id, _today()),
    ).fetchone()
    return int(row["tokens_used"]) if row else 0


def daily_budget_for(user: dict | None) -> int:
    """This caller's daily token budget: their override if one is configured, else the global cap.

    Overrides are keyed on email and set by whoever operates the deployment
    (`TOKEN_BUDGET_OVERRIDES`), not from inside the app — raising your own limit should not be a
    thing the running product can do.
    """
    email = ((user or {}).get("email") or "").strip().lower()
    if email:
        override = settings.token_budget_override_map.get(email)
        if override:
            return override
    return settings.max_tokens_per_user_per_day


def enforce_daily_budget(user: dict, incoming_tokens: int) -> None:
    """Raise 429 if this request would push the user over their daily token budget.

    Takes the whole caller dict rather than an id because the budget can depend on who they are,
    not merely that they exist.
    """
    budget = daily_budget_for(user)
    conn = get_connection()
    try:
        used = tokens_used_today(conn, user["id"])
        if used + incoming_tokens > budget:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily AI token budget exceeded ({used}/{budget} tokens used today).",
            )
    finally:
        conn.close()


def record_usage(user_id: str, tokens: int) -> None:
    """Add tokens to the user's meter for today (idempotent upsert)."""
    if tokens <= 0:
        return
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO daily_token_usage (user_id, day, tokens_used) VALUES (?, ?, ?)
            ON CONFLICT(user_id, day) DO UPDATE SET
                tokens_used = daily_token_usage.tokens_used + excluded.tokens_used
            """,
            (user_id, _today(), tokens),
        )
        conn.commit()
    finally:
        conn.close()
