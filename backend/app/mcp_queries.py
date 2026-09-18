from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .config import settings
from .db import get_connection
from .request_scope import accessible_boards_ctx


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _safe_limit(limit: int) -> int:
    bounded = max(1, min(int(limit), settings.mcp_max_result_limit))
    return bounded


def _enforce_board_scope(board_id: str | None) -> str | None:
    allowed = settings.allowed_mcp_board_ids
    if not board_id:
        return None
    if allowed and board_id not in allowed:
        raise ValueError("Board is outside MCP scope")
    return board_id


def _board_filter_clause(board_id: str | None, params: list[Any]) -> str:
    clauses: list[str] = []

    scoped_board_id = _enforce_board_scope(board_id)
    if scoped_board_id:
        params.append(scoped_board_id)
        clauses.append(" AND b.id = ? ")
    else:
        allowed = settings.allowed_mcp_board_ids
        if allowed:
            placeholders = ",".join("?" for _ in allowed)
            params.extend(sorted(allowed))
            clauses.append(f" AND b.id IN ({placeholders}) ")

    # Per-request tenant restriction: when the chat endpoint has set the
    # caller's accessible board ids, every card lookup is confined to them. None = unrestricted.
    accessible = accessible_boards_ctx.get()
    if accessible is not None:
        if not accessible:
            clauses.append(" AND 1=0 ")  # authenticated user with no boards → match nothing
        else:
            placeholders = ",".join("?" for _ in accessible)
            params.extend(sorted(accessible))
            clauses.append(f" AND b.id IN ({placeholders}) ")

    return "".join(clauses)


def _fetch_assignees_by_card(conn, card_ids: list[str]) -> dict[str, list[dict[str, str]]]:
    if not card_ids:
        return {}

    placeholders = ",".join("?" for _ in card_ids)
    rows = conn.execute(
        f"""
        SELECT ca.card_id, m.id, m.name, m.initials
        FROM card_assignees ca
        JOIN members m ON m.id = ca.member_id
        WHERE ca.card_id IN ({placeholders})
        ORDER BY m.name ASC
        """,
        card_ids,
    ).fetchall()

    out: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        out.setdefault(row["card_id"], []).append(
            {
                "id": row["id"],
                "name": row["name"],
                "initials": row["initials"],
            }
        )
    return out


def _normalize_card_rows(conn, rows) -> list[dict[str, Any]]:
    card_ids = [row["card_id"] for row in rows]
    assignees_by_card = _fetch_assignees_by_card(conn, card_ids)

    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "card_id": row["card_id"],
                "title": row["title"],
                "description": row["description"],
                "priority": row["priority"],
                "due_date": row["due_date"],
                "completed": bool(row["completed"]),
                "board_id": row["board_id"],
                "board_title": row["board_title"],
                "column_id": row["column_id"],
                "column_title": row["column_title"],
                "assignees": assignees_by_card.get(row["card_id"], []),
            }
        )
    return out


def _query_cards(
    *,
    board_id: str | None,
    user_name: str | None = None,
    priority: str | None = None,
    column_name: str | None = None,
    due_date_eq: str | None = None,
    due_date_lte: str | None = None,
    due_date_gte: str | None = None,
    include_completed: bool = False,
    require_due_date: bool = False,
    limit: int = 25,
) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        params: list[Any] = []
        where = ["1=1"]

        where.append(_board_filter_clause(board_id, params))

        if not include_completed:
            where.append(" AND c.completed = 0 ")

        if user_name and user_name.strip():
            params.append(f"%{user_name.strip().lower()}%")
            where.append(
                """
                AND EXISTS (
                    SELECT 1
                    FROM card_assignees ca
                    JOIN members m ON m.id = ca.member_id
                    WHERE ca.card_id = c.id
                      AND lower(m.name) LIKE ?
                )
                """
            )

        if priority:
            params.append(priority)
            where.append(" AND c.priority = ? ")

        if column_name and column_name.strip():
            params.append(f"%{column_name.strip().lower()}%")
            where.append(" AND lower(bc.title) LIKE ? ")

        if due_date_eq:
            params.append(due_date_eq)
            where.append(" AND c.due_date = ? ")

        if due_date_lte:
            params.append(due_date_lte)
            where.append(" AND c.due_date <= ? ")

        if due_date_gte:
            params.append(due_date_gte)
            where.append(" AND c.due_date >= ? ")

        if require_due_date:
            where.append(" AND c.due_date IS NOT NULL ")

        params.append(_safe_limit(limit))

        rows = conn.execute(
            f"""
            SELECT
                c.id AS card_id,
                c.title,
                c.description,
                c.priority,
                c.due_date,
                c.completed,
                b.id AS board_id,
                b.title AS board_title,
                bc.id AS column_id,
                bc.title AS column_title
            FROM cards c
            JOIN boards b ON b.id = c.board_id
            JOIN board_columns bc ON bc.id = c.column_id
            WHERE {" ".join(where)}
            ORDER BY
                CASE WHEN c.due_date IS NULL THEN 1 ELSE 0 END,
                c.due_date ASC,
                c.priority DESC,
                c.title ASC
            LIMIT ?
            """,
            params,
        ).fetchall()

        return _normalize_card_rows(conn, rows)
    finally:
        conn.close()


def due_dates_by_user(
    *,
    user_name: str,
    active_board_id: str | None,
    include_completed: bool,
    limit: int,
) -> list[dict[str, Any]]:
    return _query_cards(
        board_id=active_board_id,
        user_name=user_name,
        include_completed=include_completed,
        require_due_date=True,
        limit=limit,
    )


def high_priority_tasks(
    *,
    active_board_id: str | None,
    include_completed: bool,
    limit: int,
) -> list[dict[str, Any]]:
    return _query_cards(
        board_id=active_board_id,
        priority="high",
        include_completed=include_completed,
        limit=limit,
    )


def card_details(*, card_ids: list[str]) -> list[dict[str, Any]]:
    ids = [card_id.strip() for card_id in card_ids if card_id.strip()]
    if not ids:
        return []

    conn = get_connection()
    try:
        placeholders = ",".join("?" for _ in ids)
        params: list[Any] = []

        board_scope_clause = _board_filter_clause(None, params)

        rows = conn.execute(
            f"""
            SELECT
                c.id AS card_id,
                c.title,
                c.description,
                c.priority,
                c.due_date,
                c.completed,
                b.id AS board_id,
                b.title AS board_title,
                bc.id AS column_id,
                bc.title AS column_title
            FROM cards c
            JOIN boards b ON b.id = c.board_id
            JOIN board_columns bc ON bc.id = c.column_id
            WHERE c.id IN ({placeholders})
              {board_scope_clause}
            ORDER BY c.title ASC
            """,
            ids + params,
        ).fetchall()

        return _normalize_card_rows(conn, rows)
    finally:
        conn.close()


def tomorrows_due_dates(
    *,
    active_board_id: str | None,
    user_name: str | None,
    include_completed: bool,
    limit: int,
) -> list[dict[str, Any]]:
    tomorrow = (_utc_today() + timedelta(days=1)).isoformat()
    return _query_cards(
        board_id=active_board_id,
        user_name=user_name,
        due_date_eq=tomorrow,
        include_completed=include_completed,
        require_due_date=True,
        limit=limit,
    )


def high_priority_due_dates_in_board(
    *,
    board_id: str,
    include_completed: bool,
    limit: int,
) -> list[dict[str, Any]]:
    return _query_cards(
        board_id=board_id,
        priority="high",
        include_completed=include_completed,
        require_due_date=True,
        limit=limit,
    )


def upcoming_due_dates(
    *,
    active_board_id: str | None,
    user_name: str | None,
    days: int,
    include_completed: bool,
    limit: int,
) -> list[dict[str, Any]]:
    safe_days = max(1, min(days, 90))
    today = _utc_today().isoformat()
    end = (_utc_today() + timedelta(days=safe_days)).isoformat()

    return _query_cards(
        board_id=active_board_id,
        user_name=user_name,
        due_date_gte=today,
        due_date_lte=end,
        include_completed=include_completed,
        require_due_date=True,
        limit=limit,
    )


def overdue_tasks(
    *,
    active_board_id: str | None,
    user_name: str | None,
    include_completed: bool,
    limit: int,
) -> list[dict[str, Any]]:
    yesterday = (_utc_today() - timedelta(days=1)).isoformat()
    return _query_cards(
        board_id=active_board_id,
        user_name=user_name,
        due_date_lte=yesterday,
        include_completed=include_completed,
        require_due_date=True,
        limit=limit,
    )


def board_overview(*, active_board_id: str | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        params: list[Any] = []
        board_clause = _board_filter_clause(active_board_id, params)

        rows = conn.execute(
            f"""
            SELECT
                b.id AS board_id,
                b.title AS board_title,
                COUNT(c.id) AS total_cards,
                SUM(CASE WHEN c.completed = 1 THEN 1 ELSE 0 END) AS completed_cards,
                SUM(CASE WHEN c.priority = 'high' THEN 1 ELSE 0 END) AS high_priority_cards,
                SUM(CASE WHEN c.due_date IS NOT NULL THEN 1 ELSE 0 END) AS cards_with_due_dates
            FROM boards b
            LEFT JOIN cards c ON c.board_id = b.id
            WHERE 1=1
            {board_clause}
            GROUP BY b.id, b.title
            ORDER BY b.title ASC
            """,
            params,
        ).fetchall()

        return [
            {
                "board_id": row["board_id"],
                "board_title": row["board_title"],
                "total_cards": int(row["total_cards"] or 0),
                "completed_cards": int(row["completed_cards"] or 0),
                "high_priority_cards": int(row["high_priority_cards"] or 0),
                "cards_with_due_dates": int(row["cards_with_due_dates"] or 0),
            }
            for row in rows
        ]
    finally:
        conn.close()


# ── column / status reads ─────────────────────────────────────────────────────
# The count-only tools can say "12 open cards" but not "what's in Review right now" or "how is the
# board laid out" — a card's column (its workflow status) is invisible to them. These two expose it:
# a per-column breakdown (the status distribution) and a lookup of the cards sitting in a named column.


def column_breakdown(*, active_board_id: str | None = None) -> list[dict[str, Any]]:
    """Per column, how many cards sit in it — the board's workflow/status distribution.

    One row per column, ordered by board then the column's left-to-right position. Columns with no
    cards are included (open/total = 0) so 'nothing in Review' is answerable, not silently missing.
    """
    conn = get_connection()
    try:
        params: list[Any] = []
        board_clause = _board_filter_clause(active_board_id, params)

        rows = conn.execute(
            f"""
            SELECT
                b.id AS board_id,
                b.title AS board_title,
                bc.id AS column_id,
                bc.title AS column_title,
                bc.position AS position,
                COUNT(c.id) AS total_cards,
                SUM(CASE WHEN c.completed = 1 THEN 1 ELSE 0 END) AS completed_cards,
                SUM(CASE WHEN c.completed = 0 THEN 1 ELSE 0 END) AS open_cards
            FROM board_columns bc
            JOIN boards b ON b.id = bc.board_id
            LEFT JOIN cards c ON c.column_id = bc.id
            WHERE 1=1
            {board_clause}
            GROUP BY b.id, b.title, bc.id, bc.title, bc.position
            ORDER BY b.title ASC, bc.position ASC
            """,
            params,
        ).fetchall()

        return [
            {
                "board_id": row["board_id"],
                "board_title": row["board_title"],
                "column_id": row["column_id"],
                "column_title": row["column_title"],
                "position": int(row["position"] or 0),
                "open_cards": int(row["open_cards"] or 0),
                "completed_cards": int(row["completed_cards"] or 0),
                "total_cards": int(row["total_cards"] or 0),
            }
            for row in rows
        ]
    finally:
        conn.close()


def cards_in_column(
    *,
    column_name: str,
    active_board_id: str | None,
    include_completed: bool,
    limit: int,
) -> list[dict[str, Any]]:
    """Cards sitting in a column whose title matches column_name (substring, case-insensitive).

    Answers 'what is in Review / being evaluated / in progress right now'. Matching is fuzzy so
    'review' finds 'In Review'; if two boards share a column name, cards from both come back tagged
    with their board.
    """
    return _query_cards(
        board_id=active_board_id,
        column_name=column_name,
        include_completed=include_completed,
        limit=limit,
    )


# ── analytics reads ───────────────────────────────────────────────────────────
# Without these, a model asked "what's at risk", "who has the most on their plate" or "how fast
# are we moving" answers from due-date tools, confidently and wrongly — it substitutes the nearest
# available data rather than declining. These three expose blocked work, workload and velocity.
#
# ⚠️ These duplicate SQL that `analytics_api.py` also runs. They are not shared
# because the two fence differently: analytics_api gates on the request's user via
# `_require_board_scope`, while everything reachable from chat must gate on `accessible_boards_ctx`,
# which is set per request and is the only fence a tool call passes through. Calling the analytics
# endpoints from `dispatch` would hand the model an unfenced read — the silent failure the tool
# tests exist to catch. Keep the two in step when either changes.


def blocked_cards(
    *,
    active_board_id: str | None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Open cards that something else is holding up.

    Blocker ids are resolved to titles **through the same fence**: a blocker on a board the caller
    cannot see comes back as an opaque count, never as another tenant's card title.
    """
    conn = get_connection()
    try:
        params: list[Any] = []
        board_clause = _board_filter_clause(active_board_id, params)
        params.append(_safe_limit(limit))

        rows = conn.execute(
            f"""
            SELECT
                c.id AS card_id,
                c.title,
                c.priority,
                c.due_date,
                c.story_points,
                c.jira_key,
                c.blocked_by_json,
                c.depends_on_json,
                b.id AS board_id,
                b.title AS board_title,
                bc.title AS column_title
            FROM cards c
            JOIN boards b ON b.id = c.board_id
            JOIN board_columns bc ON bc.id = c.column_id
            WHERE c.completed = 0
              AND (c.blocked_by_json != '[]' OR c.depends_on_json != '[]')
              {board_clause}
            ORDER BY
                CASE WHEN c.due_date IS NULL THEN 1 ELSE 0 END,
                c.due_date ASC,
                c.title ASC
            LIMIT ?
            """,
            params,
        ).fetchall()

        out: list[dict[str, Any]] = []
        referenced: set[str] = set()
        parsed: list[tuple[dict, list[str], list[str]]] = []
        for row in rows:
            rd = dict(row)  # SQLite Row has no .get()
            blocked_by = json.loads(rd.get("blocked_by_json") or "[]")
            depends_on = json.loads(rd.get("depends_on_json") or "[]")
            referenced.update(blocked_by)
            referenced.update(depends_on)
            parsed.append((rd, blocked_by, depends_on))

        titles = _fetch_card_titles(conn, sorted(referenced))

        for rd, blocked_by, depends_on in parsed:
            out.append({
                "card_id": rd["card_id"],
                "title": rd["title"],
                "priority": rd["priority"],
                "due_date": rd["due_date"],
                "story_points": rd["story_points"],
                "jira_key": rd["jira_key"],
                "board_id": rd["board_id"],
                "board_title": rd["board_title"],
                "column_title": rd["column_title"],
                "blocked_by": [titles.get(cid) or "(a card you cannot access)" for cid in blocked_by],
                "depends_on": [titles.get(cid) or "(a card you cannot access)" for cid in depends_on],
            })
        return out
    finally:
        conn.close()


def _fetch_card_titles(conn, card_ids: list[str]) -> dict[str, str]:
    """Resolve card ids to titles, fenced. Ids outside the fence simply do not come back."""
    if not card_ids:
        return {}
    params: list[Any] = []
    board_clause = _board_filter_clause(None, params)
    placeholders = ",".join("?" for _ in card_ids)
    rows = conn.execute(
        f"""
        SELECT c.id, c.title
        FROM cards c
        JOIN boards b ON b.id = c.board_id
        WHERE c.id IN ({placeholders}) {board_clause}
        """,
        list(card_ids) + params,
    ).fetchall()
    return {r["id"]: r["title"] for r in rows}


def team_workload(
    *,
    active_board_id: str | None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Open cards and story points per member — the honest answer to "who has the most on".

    Counts every open assigned card, not just the dated or high-priority ones a due-date tool would
    surface. That difference is the whole reason this exists.
    """
    conn = get_connection()
    try:
        params: list[Any] = []
        board_clause = _board_filter_clause(active_board_id, params)
        params.append(_safe_limit(limit))

        rows = conn.execute(
            f"""
            SELECT
                m.name AS member_name,
                b.id AS board_id,
                b.title AS board_title,
                COUNT(DISTINCT CASE WHEN c.completed = 0 THEN ca.card_id END) AS open_cards,
                COALESCE(SUM(CASE WHEN c.completed = 0 THEN c.story_points END), 0) AS open_story_points
            FROM members m
            JOIN boards b ON b.id = m.board_id
            LEFT JOIN card_assignees ca ON ca.member_id = m.id
            LEFT JOIN cards c ON c.id = ca.card_id AND c.board_id = m.board_id
            WHERE 1=1
              {board_clause}
            GROUP BY m.id, m.name, b.id, b.title
            ORDER BY open_story_points DESC, open_cards DESC, m.name ASC
            LIMIT ?
            """,
            params,
        ).fetchall()

        return [
            {
                "member_name": r["member_name"],
                "board_id": r["board_id"],
                "board_title": r["board_title"],
                "open_cards": int(r["open_cards"] or 0),
                "open_story_points": int(r["open_story_points"] or 0),
            }
            for r in rows
        ]
    finally:
        conn.close()


def velocity(
    *,
    active_board_id: str | None,
    days: int = 14,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Cards and story points completed in the last N days, per board.

    Keys on `completed_at`, never `updated_at` — the snapshot write path rewrites `updated_at` on
    every save, which would make old completed work look newly finished.
    """
    conn = get_connection()
    try:
        window = max(1, min(int(days), 365))
        since = (_utc_today() - timedelta(days=window)).isoformat()

        params: list[Any] = []
        board_clause = _board_filter_clause(active_board_id, params)
        params.append(since)
        params.append(_safe_limit(limit))

        rows = conn.execute(
            f"""
            SELECT
                b.id AS board_id,
                b.title AS board_title,
                COUNT(*) AS completed_cards,
                COALESCE(SUM(c.story_points), 0) AS completed_story_points
            FROM cards c
            JOIN boards b ON b.id = c.board_id
            WHERE c.completed = 1
              {board_clause}
              AND c.completed_at IS NOT NULL
              AND c.completed_at >= ?
            GROUP BY b.id, b.title
            ORDER BY completed_story_points DESC, completed_cards DESC, b.title ASC
            LIMIT ?
            """,
            params,
        ).fetchall()

        return [
            {
                "board_id": r["board_id"],
                "board_title": r["board_title"],
                "completed_cards": int(r["completed_cards"] or 0),
                "completed_story_points": int(r["completed_story_points"] or 0),
                "window_days": window,
                "since": since,
            }
            for r in rows
        ]
    finally:
        conn.close()
