from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from .config import settings
from .db import get_connection
from .schemas import CreateTeamInput, TeamView, UpdateTeamInput
from .store import (
    accessible_board_ids, create_team, delete_team, list_teams, update_team, user_can_access_board,
)
from .tenancy import current_user


def _require_board_scope(conn, user: Optional[dict], board_id: Optional[str]) -> None:
    """When Cognito is on, analytics is scoped to a single board the caller can access.

    (The cross-board aggregate view — no board_id — is disallowed under auth: it would need to be
    restricted to the caller's accessible boards, which it does not yet do.)
    """
    if not (settings.cognito_enabled and user):
        return
    if not board_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="board_id is required")
    if not user_can_access_board(conn, user["id"], board_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


router = APIRouter(prefix="/analytics", tags=["analytics"])
teams_router = APIRouter(prefix="/teams", tags=["teams"])


# ── Teams CRUD ────────────────────────────────────────────────────────────────

def _caller_instance(user: Optional[dict]) -> str | None:
    """The caller's Instance, or None when sign-in is not configured (local, everything open)."""
    return user.get("instance_id") if user else None


@teams_router.get("", response_model=list[TeamView])
def list_teams_endpoint(user: Optional[dict] = Depends(current_user)) -> list[dict]:
    conn = get_connection()
    try:
        return list_teams(conn, _caller_instance(user))
    finally:
        conn.close()


@teams_router.post("", response_model=TeamView, status_code=201)
def create_team_endpoint(payload: CreateTeamInput, user: Optional[dict] = Depends(current_user)) -> dict:
    conn = get_connection()
    try:
        return create_team(conn, payload.name, payload.color, payload.jiraPrefix, payload.description,
                           _caller_instance(user))
    finally:
        conn.close()


@teams_router.patch("/{team_id}", response_model=TeamView)
def update_team_endpoint(team_id: str, payload: UpdateTeamInput,
                         user: Optional[dict] = Depends(current_user)) -> dict:
    conn = get_connection()
    try:
        return update_team(conn, team_id, payload.name, payload.color, payload.jiraPrefix, payload.description,
                           _caller_instance(user))
    finally:
        conn.close()


@teams_router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team_endpoint(team_id: str, user: Optional[dict] = Depends(current_user)) -> Response:
    conn = get_connection()
    try:
        delete_team(conn, team_id, _caller_instance(user))
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        conn.close()


# ── Analytics endpoints ────────────────────────────────────────────────────────

@router.get("/workload")
def get_workload(board_id: str | None = None, user: Optional[dict] = Depends(current_user)) -> list[dict]:
    """Per-member open card count + total story points."""
    conn = get_connection()
    try:
        _require_board_scope(conn, user, board_id)
        params: list[Any] = []
        where = "WHERE 1=1"
        if board_id:
            params.append(board_id)
            where += " AND m.board_id = ?"

        rows = conn.execute(
            f"""
            SELECT
                m.id AS member_id, m.name, m.initials, m.color,
                b.id AS board_id, b.title AS board_title,
                COUNT(DISTINCT CASE WHEN c.completed = 0 THEN ca.card_id END) AS open_cards,
                COALESCE(SUM(CASE WHEN c.completed = 0 THEN c.story_points END), 0) AS open_story_points,
                COUNT(DISTINCT CASE WHEN c.completed = 1 THEN ca.card_id END) AS completed_cards,
                COALESCE(SUM(CASE WHEN c.completed = 1 THEN c.story_points END), 0) AS completed_story_points
            FROM members m
            JOIN boards b ON b.id = m.board_id
            LEFT JOIN card_assignees ca ON ca.member_id = m.id
            LEFT JOIN cards c ON c.id = ca.card_id AND c.board_id = m.board_id
            {where}
            GROUP BY m.id, b.id
            ORDER BY open_story_points DESC, open_cards DESC
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/flow")
def get_flow(board_id: str | None = None, user: Optional[dict] = Depends(current_user)) -> list[dict]:
    """Column transition counts — proxy for flow / cycle time."""
    conn = get_connection()
    try:
        _require_board_scope(conn, user, board_id)
        params: list[Any] = []
        where = ""
        if board_id:
            params.append(board_id)
            where = "WHERE cce.board_id = ?"

        try:
            rows = conn.execute(
                f"""
                SELECT
                    cce.board_id, b.title AS board_title,
                    cce.from_column_title, cce.to_column_title,
                    COUNT(*) AS transitions,
                    COUNT(DISTINCT cce.card_id) AS unique_cards
                FROM card_column_events cce
                JOIN boards b ON b.id = cce.board_id
                {where}
                GROUP BY cce.board_id, cce.from_column_title, cce.to_column_title
                ORDER BY b.title, transitions DESC
                """,
                params,
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
    finally:
        conn.close()


@router.get("/dependencies")
def get_dependencies(board_id: str | None = None, user: Optional[dict] = Depends(current_user)) -> dict:
    """Cards with blocked_by / depends_on relationships, including cross-board links."""
    conn = get_connection()
    try:
        _require_board_scope(conn, user, board_id)
        params: list[Any] = []
        extra = ""
        if board_id:
            params.append(board_id)
            extra = "AND c.board_id = ?"

        rows = conn.execute(
            f"""
            SELECT c.id, c.title, c.jira_key, c.blocked_by_json, c.depends_on_json,
                   c.story_points, c.board_id, b.title AS board_title, bc.title AS column_title
            FROM cards c
            JOIN boards b ON b.id = c.board_id
            JOIN board_columns bc ON bc.id = c.column_id
            WHERE c.completed = 0
              AND (c.blocked_by_json != '[]' OR c.depends_on_json != '[]')
              {extra}
            """,
            params,
        ).fetchall()

        # Resolve blocker cards → boards, but only across boards the caller can access, so the
        # cross-board section never leaks another tenant's card/board ids.
        cb_query = "SELECT id, board_id FROM cards WHERE completed = 0"
        cb_params: list[Any] = []
        if settings.cognito_enabled and user:
            acc = accessible_board_ids(conn, user["id"])
            if acc:
                cb_query += f" AND board_id IN ({','.join('?' for _ in acc)})"
                cb_params = sorted(acc)
            else:
                cb_query += " AND 1=0"
        card_to_board: dict[str, str] = {
            r["id"]: r["board_id"] for r in conn.execute(cb_query, cb_params).fetchall()
        }

        blocked_cards: list[dict] = []
        cross_board: list[dict] = []

        for row in rows:
            blocked_by = json.loads(row["blocked_by_json"] or "[]")
            depends_on = json.loads(row["depends_on_json"] or "[]")

            entry: dict[str, Any] = {
                "card_id": row["id"],
                "title": row["title"],
                "jira_key": row["jira_key"],
                "board_title": row["board_title"],
                "column_title": row["column_title"],
                "story_points": row["story_points"],
                "blocked_by": blocked_by,
                "depends_on": depends_on,
            }
            if blocked_by or depends_on:
                blocked_cards.append(entry)

            for blocker_id in blocked_by:
                blocker_board = card_to_board.get(blocker_id)
                if blocker_board and blocker_board != row["board_id"]:
                    cross_board.append({
                        "blocked_card_id": row["id"],
                        "blocked_title": row["title"],
                        "blocked_board": row["board_title"],
                        "blocker_card_id": blocker_id,
                        "blocker_board_id": blocker_board,
                    })

        return {
            "cards_with_dependencies": blocked_cards,
            "cross_board_dependencies": cross_board,
            "total": len(blocked_cards),
        }
    finally:
        conn.close()


# ── Intelligence narrative ─────────────────────────────────────────────────────

class IntelligenceRequest(BaseModel):
    board_id: str | None = None


@router.post("/intelligence")
def get_intelligence(payload: IntelligenceRequest, user: Optional[dict] = Depends(current_user)) -> dict:
    """Summarise live board data — overdue high-priority cards, blocked work, velocity, workload,
    column flow — as facts plus a short written narrative.

    The narrative comes from a fixed template (`_template_narrative`); no model writes it, which is
    why `has_ai` is always False. Scoped to one board whenever Cognito is on — the cross-board
    aggregate (`board_id` omitted) is refused there and only reachable in open local dev.
    """
    conn = get_connection()
    try:
        _require_board_scope(conn, user, payload.board_id)
        facts = _gather_facts(conn, payload.board_id)
        narrative = _build_narrative(facts)
        return {
            "narrative": narrative,
            "facts": facts,
            "generated_at": datetime.now(UTC).isoformat(),
            # Kept for API compatibility; always False because no model writes the narrative.
            "has_ai": False,
        }
    finally:
        conn.close()


def _gather_facts(conn, board_id: str | None) -> dict[str, Any]:
    today = datetime.now(UTC).date().isoformat()
    two_weeks_ago = (datetime.now(UTC).date() - timedelta(days=14)).isoformat()

    scope = [board_id] if board_id else []
    board_clause = "AND c.board_id = ?" if board_id else ""

    overdue = conn.execute(
        f"""
        SELECT c.id, c.title, c.priority, c.due_date, c.jira_key, c.story_points,
               b.title AS board_title, bc.title AS column_title
        FROM cards c
        JOIN boards b ON b.id = c.board_id
        JOIN board_columns bc ON bc.id = c.column_id
        WHERE c.completed = 0 AND c.due_date < ? AND c.priority = 'high'
        {board_clause}
        ORDER BY c.due_date ASC LIMIT 8
        """,
        [today] + scope,
    ).fetchall()

    velocity = conn.execute(
        f"""
        SELECT b.title AS board_title,
               COUNT(*) AS completed_cards,
               COALESCE(SUM(c.story_points), 0) AS completed_points
        FROM cards c
        JOIN boards b ON b.id = c.board_id
        WHERE c.completed = 1 AND c.completed_at >= ?
        {board_clause}
        GROUP BY b.id ORDER BY completed_points DESC LIMIT 5
        """,
        [two_weeks_ago] + scope,
    ).fetchall()

    blocked = conn.execute(
        f"""
        SELECT c.id, c.title, c.jira_key, c.blocked_by_json, c.story_points, b.title AS board_title
        FROM cards c
        JOIN boards b ON b.id = c.board_id
        WHERE c.completed = 0 AND c.blocked_by_json != '[]'
        {board_clause} LIMIT 10
        """,
        scope,
    ).fetchall()

    workload = conn.execute(
        f"""
        SELECT m.name, m.board_id,
               COUNT(DISTINCT ca.card_id) AS open_cards,
               COALESCE(SUM(c.story_points), 0) AS open_points
        FROM members m
        JOIN boards b ON b.id = m.board_id
        LEFT JOIN card_assignees ca ON ca.member_id = m.id
        LEFT JOIN cards c ON c.id = ca.card_id AND c.completed = 0
        WHERE 1=1 {board_clause}
        GROUP BY m.id ORDER BY open_points DESC LIMIT 8
        """,
        scope,
    ).fetchall()

    try:
        flow = conn.execute(
            f"""
            SELECT cce.to_column_title, b.title AS board_title, COUNT(*) AS transitions
            FROM card_column_events cce
            JOIN boards b ON b.id = cce.board_id
            {"WHERE cce.board_id = ?" if board_id else ""}
            GROUP BY cce.board_id, cce.to_column_title
            ORDER BY transitions DESC LIMIT 10
            """,
            scope,
        ).fetchall()
        flow_data = [dict(r) for r in flow]
    except Exception:
        flow_data = []

    return {
        "overdue_high_priority": [dict(r) for r in overdue],
        "velocity_last_14_days": [dict(r) for r in velocity],
        "blocked_cards": [dict(r) for r in blocked],
        "top_workload": [dict(r) for r in workload],
        "column_flow": flow_data,
    }


def _build_narrative(facts: dict) -> str:
    # The narrative is always the deterministic template below; no model generates it.
    return _template_narrative(facts)


def _template_narrative(facts: dict) -> str:
    parts: list[str] = []
    overdue = facts["overdue_high_priority"]
    blocked = facts["blocked_cards"]
    velocity = facts["velocity_last_14_days"]
    workload = facts["top_workload"]

    if overdue:
        keys = [c["jira_key"] for c in overdue[:3] if c.get("jira_key")]
        ref = f" ({', '.join(keys)})" if keys else ""
        parts.append(
            f"⚠️ {len(overdue)} high-priority task{'s are' if len(overdue) > 1 else ' is'} overdue{ref} — "
            "immediate action required to prevent cross-team cascade delays."
        )

    if blocked:
        held_sp = sum(c.get("story_points") or 0 for c in blocked)
        parts.append(
            f"🔒 {len(blocked)} card{'s are' if len(blocked) > 1 else ' is'} blocked"
            + (f" ({held_sp} story points held up)" if held_sp else "")
            + ". Unblocking these should be the team's first focus to restore delivery flow."
        )

    if velocity:
        top = velocity[0]
        parts.append(
            f"📈 '{top['board_title']}' leads velocity with {int(top['completed_points'])} points completed "
            f"across {top['completed_cards']} cards in the last 14 days."
        )
    else:
        parts.append(
            "📈 No story points recorded in the past 14 days — add estimates to cards to enable velocity tracking."
        )

    if workload:
        top = workload[0]
        avg = sum(w.get("open_points", 0) for w in workload) / max(len(workload), 1)
        if (top.get("open_points") or 0) > avg * 1.6 and avg > 0:
            parts.append(
                f"⚖️ {top['name']} carries {int(top['open_points'])} open story points "
                f"vs a team average of {avg:.0f} — consider rebalancing to prevent bottlenecks."
            )

    return " ".join(parts) if parts else "✅ No critical issues detected. Team is operating at healthy capacity."
