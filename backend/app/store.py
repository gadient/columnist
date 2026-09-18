from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
import sqlite3

from fastapi import HTTPException


def _now() -> str:
    """UTC ISO-8601, microsecond resolution.

    Sub-second precision is load-bearing, not cosmetic. `ORDER BY created_at, id` is only as good as
    `created_at` is distinct: at whole-second resolution all ten boards of a "Load Demo" land in one
    bucket, ordering falls through to the `id` tie-break — a random uuid hex — and the grid comes
    back in a different order on every demo load. Pinned by `tests/test_board_ordering.py`.

    Format note: rows stamped at whole seconds by an older build end `…:00Z` and sort *after* a
    same-second `…:00.123456Z` ('.' < 'Z'). That only reorders ties inside one second.
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _trim_required(value: str, field_name: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return trimmed


def _row_exists(conn: sqlite3.Connection, query: str, args: tuple) -> bool:
    row = conn.execute(query, args).fetchone()
    return row is not None


def _get_board_row(conn: sqlite3.Connection, board_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM boards WHERE id = ?", (board_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Board not found")
    return row


def _get_column_row(conn: sqlite3.Connection, board_id: str, column_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM board_columns WHERE id = ? AND board_id = ?",
        (column_id, board_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Column not found")
    return row


def _get_card_row(conn: sqlite3.Connection, board_id: str, card_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM cards WHERE id = ? AND board_id = ?",
        (card_id, board_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return row


def _get_member_row(conn: sqlite3.Connection, board_id: str, member_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM members WHERE id = ? AND board_id = ?",
        (member_id, board_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return row


def _reindex_column_positions(conn: sqlite3.Connection, board_id: str) -> None:
    rows = conn.execute(
        "SELECT id FROM board_columns WHERE board_id = ? ORDER BY position, created_at",
        (board_id,),
    ).fetchall()
    for idx, row in enumerate(rows):
        conn.execute(
            "UPDATE board_columns SET position = ?, updated_at = ? WHERE id = ?",
            (idx, _now(), row["id"]),
        )


def _reindex_card_positions(conn: sqlite3.Connection, board_id: str, column_id: str) -> None:
    rows = conn.execute(
        "SELECT id FROM cards WHERE board_id = ? AND column_id = ? ORDER BY position, created_at",
        (board_id, column_id),
    ).fetchall()
    for idx, row in enumerate(rows):
        conn.execute(
            "UPDATE cards SET position = ?, updated_at = ? WHERE id = ?",
            (idx, _now(), row["id"]),
        )


def _touch_board(conn: sqlite3.Connection, board_id: str) -> None:
    # Every write to a board's snapshot state — title/description, columns, cards, members —
    # bumps the server-authoritative version. It is never accepted from or influenced by the client:
    # that is the whole point of it. `updated_at` is a timestamp, not a version (two writes can
    # share one, and it only orders events); `version` is.
    conn.execute(
        "UPDATE boards SET version = version + 1, updated_at = ? WHERE id = ?", (_now(), board_id)
    )


def get_board(conn: sqlite3.Connection, board_id: str) -> dict:
    board = _get_board_row(conn, board_id)

    column_rows = conn.execute(
        "SELECT * FROM board_columns WHERE board_id = ? ORDER BY position, created_at",
        (board_id,),
    ).fetchall()

    card_rows = conn.execute(
        "SELECT * FROM cards WHERE board_id = ? ORDER BY column_id, position, created_at",
        (board_id,),
    ).fetchall()

    member_rows = conn.execute(
        "SELECT * FROM members WHERE board_id = ? ORDER BY created_at",
        (board_id,),
    ).fetchall()

    assignee_rows = conn.execute(
        """
        SELECT ca.card_id, ca.member_id
        FROM card_assignees ca
        JOIN cards c ON c.id = ca.card_id
        WHERE c.board_id = ?
        ORDER BY ca.created_at
        """,
        (board_id,),
    ).fetchall()

    assignees_by_card: dict[str, list[str]] = {}
    for row in assignee_rows:
        assignees_by_card.setdefault(row["card_id"], []).append(row["member_id"])

    cards_by_column: dict[str, list[str]] = {row["id"]: [] for row in column_rows}
    cards_payload: dict[str, dict] = {}

    for row in card_rows:
        card_id = row["id"]
        cards_by_column.setdefault(row["column_id"], []).append(card_id)
        rd = dict(row)
        cards_payload[card_id] = {
            "id": card_id,
            "title": rd["title"],
            "description": rd["description"],
            "priority": rd["priority"],
            "dueDate": rd["due_date"],
            "assignees": assignees_by_card.get(card_id, []),
            "createdAt": rd["created_at"],
            "completed": bool(rd["completed"]),
            "storyPoints": rd.get("story_points"),
            "jiraKey": rd.get("jira_key"),
            "labels": json.loads(rd.get("labels_json") or "[]"),
            "blockedBy": json.loads(rd.get("blocked_by_json") or "[]"),
            "dependsOn": json.loads(rd.get("depends_on_json") or "[]"),
        }

    columns_payload: dict[str, dict] = {}
    column_order: list[str] = []

    for row in column_rows:
        column_id = row["id"]
        column_order.append(column_id)
        columns_payload[column_id] = {
            "id": column_id,
            "title": row["title"],
            "cardIds": cards_by_column.get(column_id, []),
        }

    members_payload = [
        {
            "id": row["id"],
            "name": row["name"],
            "initials": row["initials"],
            "color": row["color"],
        }
        for row in member_rows
    ]

    return {
        "id": board["id"],
        "workspaceId": board["workspace_id"],
        "projectInfo": {
            "title": board["title"],
            "description": board["description"],
        },
        "columns": columns_payload,
        "cards": cards_payload,
        "columnOrder": column_order,
        "teamMembers": members_payload,
        "createdAt": board["created_at"],
        "updatedAt": board["updated_at"],
        "version": int(board["version"]),  # the client reads this to declare it on writes
    }


def list_boards(conn: sqlite3.Connection, accessible_ids: set[str] | None = None) -> list[dict]:
    # Creation order, for the same reason as list_workspace_boards — see the note there
    # (including why this must not be `rowid`).
    board_rows = conn.execute("SELECT id FROM boards ORDER BY created_at, id").fetchall()
    ids = [row["id"] for row in board_rows]
    if accessible_ids is not None:
        ids = [i for i in ids if i in accessible_ids]
    return [get_board(conn, i) for i in ids]


def list_workspaces(conn: sqlite3.Connection, accessible_ids: set[str] | None = None) -> list[dict]:
    rows = conn.execute("SELECT * FROM workspaces ORDER BY updated_at DESC").fetchall()
    result = []
    for row in rows:
        if accessible_ids is not None and row["id"] not in accessible_ids:
            continue
        board_count = conn.execute(
            "SELECT COUNT(*) as c FROM boards WHERE workspace_id = ?", (row["id"],)
        ).fetchone()["c"]
        result.append({
            "id": row["id"],
            "name": row["name"],
            "boardCount": board_count,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        })
    return result


def create_workspace(conn: sqlite3.Connection, name: str, owner: dict | None = None) -> dict:
    clean_name = _trim_required(name, "name")
    now = _now()
    workspace_id = _new_id("workspace")
    owner_id = owner["id"] if owner else None
    # A new workspace lives in its creator's Instance so instance-mates can see it;
    # with no authenticated owner (local dev) it falls into the backfilled default.
    instance_id = (user_instance_id(conn, owner["id"]) if owner else None) or DEFAULT_INSTANCE_ID
    conn.execute(
        "INSERT INTO workspaces (id, name, created_at, updated_at, owner_id, instance_id) VALUES (?, ?, ?, ?, ?, ?)",
        (workspace_id, clean_name, now, now, owner_id, instance_id),
    )
    if owner is not None:
        upsert_user(conn, owner)
        add_workspace_member(conn, workspace_id, owner["id"], role="owner")
    conn.commit()
    return {"id": workspace_id, "name": clean_name, "boardCount": 0, "createdAt": now, "updatedAt": now}


# ── Instances / tenant boundary ─────────────────────────────────────────────────
# An Instance is the top-level isolation unit; users/workspaces/boards FK to it via
# instance_id. Enforcement lives in tenancy.py (gated on Cognito); these are pure DB.

DEFAULT_INSTANCE_ID = "default"  # holds everything that predates tenancy (see migration 0008)


def get_instance(conn: sqlite3.Connection, instance_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM instances WHERE id = ?", (instance_id,)).fetchone()
    return dict(row) if row else None


def create_instance(conn: sqlite3.Connection, name: str, owner_id: str | None = None) -> dict:
    clean_name = _trim_required(name, "name")
    now = _now()
    instance_id = _new_id("instance")
    conn.execute(
        "INSERT INTO instances (id, name, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (instance_id, clean_name, owner_id, now, now),
    )
    conn.commit()
    return {"id": instance_id, "name": clean_name, "owner_id": owner_id, "created_at": now, "updated_at": now}


def count_instances(conn: sqlite3.Connection, exclude_default: bool = True) -> int:
    """How many Instances exist. Excludes the backfill 'default' Instance so it never
    eats a slot in the operator's Instance budget (max_instances)."""
    if exclude_default:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM instances WHERE id != ?", (DEFAULT_INSTANCE_ID,)
        ).fetchone()["c"]
    return conn.execute("SELECT COUNT(*) AS c FROM instances").fetchone()["c"]


def list_instances(conn: sqlite3.Connection) -> list[dict]:
    """All Instances with their member / workspace / board counts — the ops takedown roster."""
    rows = conn.execute("SELECT * FROM instances ORDER BY created_at").fetchall()
    out = []
    for row in rows:
        rd = dict(row)
        iid = rd["id"]
        out.append({
            "id": iid,
            "name": rd["name"],
            "owner_id": rd.get("owner_id"),
            "created_at": rd["created_at"],
            "member_count": count_instance_members(conn, iid),
            "workspace_count": conn.execute(
                "SELECT COUNT(*) AS c FROM workspaces WHERE instance_id = ?", (iid,)
            ).fetchone()["c"],
            "board_count": conn.execute(
                "SELECT COUNT(*) AS c FROM boards WHERE instance_id = ?", (iid,)
            ).fetchone()["c"],
        })
    return out


def delete_instance(conn: sqlite3.Connection, instance_id: str) -> dict:
    """Ops takedown: cascade-delete an Instance's database records in one transaction.
    Refuses the backfill 'default' Instance. Returns row counts removed.

    Order matters — children before parents: card events → boards; workspace members →
    workspaces; per-user token usage → users; note-import applications/recommendations → their
    sessions; then the membership roster, the provisioned user rows, and the Instance itself. Every
    child delete is scoped by the Instance's own boards/workspaces/users/sessions so no other
    tenant is touched.

    The note-import rows and the feedback rows go with it, not because a foreign key drags them
    along (`note_import_sessions` and `feedback` declare none to `instances`) but because they hold
    the tenant's own words: note excerpts, filenames and input hashes, and feedback text.
    Children are deleted explicitly rather than left to
    `ON DELETE CASCADE`, which needs `PRAGMA foreign_keys` on and is therefore not a guarantee this
    function can make about the connection it is handed.

    NOT handled here (by design):
      • Cognito accounts — a separate, deliberate operator step (`takedown-instance.sh` prints the
        `admin-delete-user` commands).
      • Append-only JSONL logs (`data/logs/audit.jsonl`, `chat_completions.jsonl`) — retained as
        the security/audit trail (the audit log records the takedown itself).
    """
    if instance_id == DEFAULT_INSTANCE_ID:
        raise HTTPException(status_code=400, detail="Refusing to delete the default Instance")
    if get_instance(conn, instance_id) is None:
        raise HTTPException(status_code=404, detail="Instance not found")

    removed: dict[str, int] = {}
    try:
        # card_column_events references board_id (migration 0005) — wrapped in try/except at the
        # call site elsewhere because the table may predate 0005; here we guard the same way.
        try:
            cur = conn.execute(
                "DELETE FROM card_column_events WHERE board_id IN "
                "(SELECT id FROM boards WHERE instance_id = ?)",
                (instance_id,),
            )
            removed["card_events"] = cur.rowcount if cur.rowcount is not None else 0
        except Exception:  # noqa: BLE001 - table may not exist on a pre-0005 db
            removed["card_events"] = 0
        cur = conn.execute(
            "DELETE FROM workspace_members WHERE workspace_id IN "
            "(SELECT id FROM workspaces WHERE instance_id = ?)",
            (instance_id,),
        )
        removed["workspace_members"] = cur.rowcount if cur.rowcount is not None else 0
        # Per-user token meter (migration 0007) — keyed by user_id, so purge before the user rows
        # go. Guarded like card_events in case the table predates 0007.
        try:
            cur = conn.execute(
                "DELETE FROM daily_token_usage WHERE user_id IN "
                "(SELECT id FROM users WHERE instance_id = ?)",
                (instance_id,),
            )
            removed["token_usage"] = cur.rowcount if cur.rowcount is not None else 0
        except Exception:  # noqa: BLE001 - table may not exist on a pre-0007 db
            removed["token_usage"] = 0
        # Note-import sessions (migration 0012) and their children — the excerpts, filenames and
        # input hashes that must not outlive the Instance. Recommendations/applications are
        # reached through their session rather than an instance_id of their own.
        sessions_of_instance = "(SELECT id FROM note_import_sessions WHERE instance_id = ?)"
        for table in ("note_import_applications", "note_import_recommendations"):
            cur = conn.execute(
                f"DELETE FROM {table} WHERE session_id IN {sessions_of_instance}", (instance_id,)
            )
            removed[table] = cur.rowcount if cur.rowcount is not None else 0
        cur = conn.execute("DELETE FROM note_import_sessions WHERE instance_id = ?", (instance_id,))
        removed["note_import_sessions"] = cur.rowcount if cur.rowcount is not None else 0
        # Feedback (migration 0015) — the tenant's own words about the product.
        cur = conn.execute("DELETE FROM feedback WHERE instance_id = ?", (instance_id,))
        removed["feedback"] = cur.rowcount if cur.rowcount is not None else 0
        for table in ("boards", "workspaces", "instance_members", "users"):
            cur = conn.execute(f"DELETE FROM {table} WHERE instance_id = ?", (instance_id,))
            removed[table] = cur.rowcount if cur.rowcount is not None else 0
        cur = conn.execute("DELETE FROM instances WHERE id = ?", (instance_id,))
        removed["instances"] = cur.rowcount if cur.rowcount is not None else 0
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return removed


# ── Instance membership & invites ────────────────────────────────────────────────
# Roles: PIU may invite SIUs; SIU may invite no one; the operator is not stored.
# The pure helpers below do the DB work; the invite_*/delete_* service functions add
# the authorization + cap rules and raise HTTPException so endpoints and ops share them.

INSTANCE_ROLE_PIU = "piu"
INSTANCE_ROLE_SIU = "siu"


def _member_view(row) -> dict:
    rd = dict(row)
    return {
        "id": rd["id"],
        "instanceId": rd["instance_id"],
        "email": rd["email"],
        "userId": rd.get("user_id"),
        "role": rd["role"],
        "status": rd["status"],
        "invitedBy": rd.get("invited_by"),
        "createdAt": rd["created_at"],
    }


def list_instance_members(conn: sqlite3.Connection, instance_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM instance_members WHERE instance_id = ? ORDER BY created_at",
        (instance_id,),
    ).fetchall()
    return [_member_view(r) for r in rows]


def count_instance_members(conn: sqlite3.Connection, instance_id: str) -> int:
    """Total seats used in an Instance — both 'invited' and 'active' count."""
    return conn.execute(
        "SELECT COUNT(*) AS c FROM instance_members WHERE instance_id = ?", (instance_id,)
    ).fetchone()["c"]


def count_members_by_role(conn: sqlite3.Connection, instance_id: str, role: str) -> int:
    """Members of a given role in an Instance (used for the PIU cap)."""
    return conn.execute(
        "SELECT COUNT(*) AS c FROM instance_members WHERE instance_id = ? AND role = ?",
        (instance_id, role),
    ).fetchone()["c"]


def count_secondaries_by_inviter(conn: sqlite3.Connection, instance_id: str, inviter_id: str) -> int:
    """SIUs a specific PIU has invited (used for the per-PIU SIU cap)."""
    return conn.execute(
        "SELECT COUNT(*) AS c FROM instance_members WHERE instance_id = ? AND role = ? AND invited_by = ?",
        (instance_id, INSTANCE_ROLE_SIU, inviter_id),
    ).fetchone()["c"]


def get_instance_member(conn: sqlite3.Connection, member_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM instance_members WHERE id = ?", (member_id,)).fetchone()
    return dict(row) if row else None


def get_member_by_email(conn: sqlite3.Connection, instance_id: str, email: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM instance_members WHERE instance_id = ? AND email = ?",
        (instance_id, email.strip().lower()),
    ).fetchone()
    return dict(row) if row else None


def get_member_by_user(conn: sqlite3.Connection, instance_id: str, user_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM instance_members WHERE instance_id = ? AND user_id = ?",
        (instance_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def member_role(conn: sqlite3.Connection, instance_id: str, user_id: str) -> str | None:
    m = get_member_by_user(conn, instance_id, user_id)
    return m["role"] if m else None


def add_instance_invite(
    conn: sqlite3.Connection,
    instance_id: str,
    email: str,
    role: str,
    invited_by: str | None = None,
) -> dict:
    """Allowlist an email into an Instance and de-dup by email. Role-specific caps are
    enforced by the seed_primary_user / invite_secondary_user callers, not here."""
    email_clean = _trim_required(email, "email").lower()
    if get_instance(conn, instance_id) is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    if get_member_by_email(conn, instance_id, email_clean) is not None:
        raise HTTPException(status_code=409, detail="Email is already a member or invited")
    now = _now()
    member_id = _new_id("member")
    conn.execute(
        """
        INSERT INTO instance_members (id, instance_id, email, user_id, role, status, invited_by, created_at, updated_at)
        VALUES (?, ?, ?, NULL, ?, 'invited', ?, ?, ?)
        """,
        (member_id, instance_id, email_clean, role, invited_by, now, now),
    )
    conn.commit()
    return _member_view(get_instance_member(conn, member_id))


# ── Invite/delete service functions (authorization + cap; shared by API and ops) ──

def seed_primary_user(
    conn: sqlite3.Connection, instance_id: str, email: str, max_primary_users: int, invited_by: str | None = None
) -> dict:
    """Operator/ops action: allowlist a PIU. Capped at max_primary_users PIUs per Instance.
    No in-app endpoint — only the operator does this."""
    if count_members_by_role(conn, instance_id, INSTANCE_ROLE_PIU) >= max_primary_users:
        raise HTTPException(
            status_code=409, detail=f"Primary-user limit reached for this Instance ({max_primary_users})"
        )
    return add_instance_invite(conn, instance_id, email, INSTANCE_ROLE_PIU, invited_by)


def create_primary_space(
    conn: sqlite3.Connection,
    email: str,
    instance_name: str,
    max_instances: int,
    max_primary_users: int,
    owner_id: str | None = None,
) -> dict:
    """Operator/ops action: create one user's private Instance and seed them as its
    sole PIU. Capped at max_instances Instances overall. The Cognito account
    (AdminCreateUser) is created by the ops wrapper (`deploy/invite-primary.sh`), not here.

    `create_instance` commits before `seed_primary_user` runs (store helpers commit internally),
    so if seeding fails we'd leave an orphan Instance that still eats a slot
    in the cap. Compensate: delete the just-created Instance and re-raise, so this is atomic from
    the caller's view.
    """
    if count_instances(conn, exclude_default=True) >= max_instances:
        raise HTTPException(status_code=409, detail=f"Instance limit reached ({max_instances})")
    instance = create_instance(conn, instance_name, owner_id=owner_id)
    try:
        member = seed_primary_user(conn, instance["id"], email, max_primary_users)
    except Exception:
        # Roll back the orphan Instance (create_instance already committed it).
        conn.execute("DELETE FROM instances WHERE id = ?", (instance["id"],))
        conn.commit()
        raise
    return {"instance": instance, "member": member}


def invite_secondary_user(
    conn: sqlite3.Connection, instance_id: str, actor_user_id: str, email: str, max_secondary_per_primary: int
) -> dict:
    """In-app action: a PIU invites an SIU. SIUs (and non-members) may not invite, and each
    PIU may invite at most max_secondary_per_primary SIUs (a per-inviter budget)."""
    if member_role(conn, instance_id, actor_user_id) != INSTANCE_ROLE_PIU:
        raise HTTPException(status_code=403, detail="Only a primary user (PIU) can invite others")
    if count_secondaries_by_inviter(conn, instance_id, actor_user_id) >= max_secondary_per_primary:
        raise HTTPException(
            status_code=409,
            detail=f"You have reached your invite limit ({max_secondary_per_primary} members)",
        )
    return add_instance_invite(conn, instance_id, email, INSTANCE_ROLE_SIU, invited_by=actor_user_id)


def delete_secondary_user(
    conn: sqlite3.Connection, instance_id: str, actor_user_id: str, member_id: str
) -> None:
    """In-app action: a PIU removes an SIU. Deleting a PIU is ops-only."""
    if member_role(conn, instance_id, actor_user_id) != INSTANCE_ROLE_PIU:
        raise HTTPException(status_code=403, detail="Only a primary user (PIU) can remove members")
    target = get_instance_member(conn, member_id)
    if target is None or target["instance_id"] != instance_id:
        raise HTTPException(status_code=404, detail="Member not found")
    if target["role"] != INSTANCE_ROLE_SIU:
        raise HTTPException(status_code=403, detail="Only secondary users (SIU) can be removed in-app")
    conn.execute("DELETE FROM instance_members WHERE id = ?", (member_id,))
    conn.commit()


def provision_instance_membership(conn: sqlite3.Connection, user: dict) -> tuple[str | None, bool]:
    """JIT-bind an authenticated user to their Instance on first login.

    Returns ``(instance_id, activated)``: the Instance the user belongs to (or None if
    they have no membership or matching invite — authenticated but never invited, and
    must be refused), and whether THIS call freshly activated a pending invite (True only
    on the very first login, so callers can audit activation without per-request noise).
    Enforces **email binding**: the account email must equal an allowlisted invite's
    email. The seat was already reserved at invite time, so no cap check is needed here.
    """
    uid = user["id"]
    email = (user.get("email") or "").strip().lower()

    # Already a member? (user_id bound on a prior login) — idempotent refresh, not fresh.
    row = conn.execute(
        "SELECT instance_id FROM instance_members WHERE user_id = ? ORDER BY created_at LIMIT 1",
        (uid,),
    ).fetchone()
    if row is not None:
        instance_id = dict(row)["instance_id"]
        conn.execute("UPDATE users SET instance_id = ? WHERE id = ?", (instance_id, uid))
        conn.commit()
        return instance_id, False

    # Otherwise bind a pending invite whose email matches (email binding). If the same
    # email was invited to multiple Instances, the earliest invite wins (v1 = one user,
    # one Instance).
    if email:
        inv = conn.execute(
            """
            SELECT id, instance_id FROM instance_members
            WHERE email = ? AND user_id IS NULL ORDER BY created_at LIMIT 1
            """,
            (email,),
        ).fetchone()
        if inv is not None:
            invd = dict(inv)
            now = _now()
            conn.execute(
                "UPDATE instance_members SET user_id = ?, status = 'active', updated_at = ? WHERE id = ?",
                (uid, now, invd["id"]),
            )
            conn.execute("UPDATE users SET instance_id = ? WHERE id = ?", (invd["instance_id"], uid))
            conn.commit()
            return invd["instance_id"], True

    return None, False


# ── Accounts & tenancy ──────────────────────────────────────────────────────────
# All helpers are pure DB reads/writes; enforcement lives in tenancy.py and is gated on Cognito.

def upsert_user(conn: sqlite3.Connection, user: dict) -> None:
    """JIT-provision (or refresh) a user row. Idempotent on the Cognito `sub` (user id)."""
    now = _now()
    conn.execute(
        """
        INSERT INTO users (id, email, display_name, created_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            email = excluded.email,
            display_name = excluded.display_name,
            last_seen_at = excluded.last_seen_at
        """,
        (user["id"], (user.get("email") or "").lower(), user.get("name"), now, now),
    )
    conn.commit()


def add_workspace_member(
    conn: sqlite3.Connection,
    workspace_id: str,
    user_id: str,
    role: str = "member",
    invited_by: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO workspace_members (workspace_id, user_id, role, invited_by, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (workspace_id, user_id, role, invited_by, _now()),
    )
    conn.commit()


# Access is per-Instance: everyone in an Instance shares every workspace/board
# in it. Scoping compares the resource's instance_id to the caller's (users.instance_id,
# bound at provisioning).

def user_instance_id(conn: sqlite3.Connection, user_id: str) -> str | None:
    row = conn.execute("SELECT instance_id FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row)["instance_id"] if row is not None else None


def user_can_access_workspace(conn: sqlite3.Connection, user_id: str, workspace_id: str) -> bool:
    inst = user_instance_id(conn, user_id)
    if inst is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM workspaces WHERE id = ? AND instance_id = ? LIMIT 1",
        (workspace_id, inst),
    ).fetchone()
    return row is not None


def user_can_access_board(conn: sqlite3.Connection, user_id: str, board_id: str) -> bool:
    inst = user_instance_id(conn, user_id)
    if inst is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM boards WHERE id = ? AND instance_id = ? LIMIT 1",
        (board_id, inst),
    ).fetchone()
    return row is not None


def accessible_board_ids(conn: sqlite3.Connection, user_id: str) -> set[str]:
    inst = user_instance_id(conn, user_id)
    if inst is None:
        return set()
    rows = conn.execute("SELECT id FROM boards WHERE instance_id = ?", (inst,)).fetchall()
    return {dict(r)["id"] for r in rows}


def accessible_workspace_ids(conn: sqlite3.Connection, user_id: str) -> set[str]:
    inst = user_instance_id(conn, user_id)
    if inst is None:
        return set()
    rows = conn.execute("SELECT id FROM workspaces WHERE instance_id = ?", (inst,)).fetchall()
    return {dict(r)["id"] for r in rows}


def count_users(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])


def count_workspace_members(conn: sqlite3.Connection, workspace_id: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM workspace_members WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()["c"]
    )


def rename_workspace(conn: sqlite3.Connection, workspace_id: str, name: str) -> dict:
    row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    clean_name = _trim_required(name, "name")
    now = _now()
    conn.execute(
        "UPDATE workspaces SET name = ?, updated_at = ? WHERE id = ?",
        (clean_name, now, workspace_id),
    )
    conn.commit()
    board_count = conn.execute(
        "SELECT COUNT(*) as c FROM boards WHERE workspace_id = ?", (workspace_id,)
    ).fetchone()["c"]
    return {"id": workspace_id, "name": clean_name, "boardCount": board_count,
            "createdAt": row["created_at"], "updatedAt": now}


def delete_workspace(conn: sqlite3.Connection, workspace_id: str) -> None:
    row = conn.execute("SELECT id FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    conn.execute("DELETE FROM boards WHERE workspace_id = ?", (workspace_id,))
    conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
    conn.commit()


def list_workspace_boards(conn: sqlite3.Connection, workspace_id: str) -> list[dict]:
    row = conn.execute("SELECT id FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    # Creation order, NOT `updated_at DESC`, which shuffles the grid.
    #
    # Why: `updated_at` moves on every write, and "Load Demo" creates each board then immediately
    # syncs it — so `updated_at DESC` follows whichever sync happened to land last, which both
    # reverses the authored order and re-groups differently on every load (and again whenever a
    # board is edited).
    #
    # `created_at, id` — creation order, with `id` as a deterministic tie-break.
    #
    # NOT `rowid`: that is a SQLite-only pseudo-column. The suite runs on SQLite, but this query must
    # also run on Postgres (a supported deployment backend), which raises UndefinedColumn and fails
    # the query outright — returning zero boards. Any SQL here must run on BOTH backends; see `db.py`.
    #
    # `_now()` stamps microseconds, so `created_at` ties are rare; when two boards do share one they
    # fall back to `id`: arbitrary, but stable across calls, which is the property that matters.
    # Authored order would need an explicit position column.
    board_rows = conn.execute(
        "SELECT id FROM boards WHERE workspace_id = ? ORDER BY created_at, id",
        (workspace_id,),
    ).fetchall()
    return [get_board(conn, r["id"]) for r in board_rows]


def create_board(
    conn: sqlite3.Connection,
    title: str,
    description: str,
    columns: list[dict],
    team_members: list[dict],
    workspace_id: str | None = None,
) -> dict:
    clean_title = _trim_required(title, "title")
    clean_columns = [
        {"title": _trim_required(column["title"], "column title")}
        for column in columns
    ]

    if not clean_columns:
        raise HTTPException(status_code=400, detail="At least one column is required")

    now = _now()
    board_id = _new_id("board")

    # A board inherits its workspace's Instance (keeps board.instance_id == workspace's);
    # workspace-less or legacy rows fall back to the backfilled default.
    instance_id = DEFAULT_INSTANCE_ID
    if workspace_id:
        wr = conn.execute("SELECT instance_id FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        if wr is not None and dict(wr)["instance_id"]:
            instance_id = dict(wr)["instance_id"]

    conn.execute(
        """
        INSERT INTO boards (id, title, description, workspace_id, instance_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (board_id, clean_title, description.strip(), workspace_id, instance_id, now, now),
    )

    for idx, column in enumerate(clean_columns):
        column_id = _new_id("col")
        conn.execute(
            """
            INSERT INTO board_columns (id, board_id, title, position, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (column_id, board_id, column["title"], idx, now, now),
        )

    for member in team_members:
        name = _trim_required(member["name"], "member name")
        initials = _trim_required(member["initials"], "member initials")
        color = _trim_required(member["color"], "member color")
        member_id = _new_id("member")
        conn.execute(
            """
            INSERT INTO members (id, board_id, name, initials, color, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (member_id, board_id, name, initials, color, now, now),
        )

    conn.commit()
    return get_board(conn, board_id)


def update_board(conn: sqlite3.Connection, board_id: str, title: str | None, description: str | None) -> dict:
    board = _get_board_row(conn, board_id)
    new_title = board["title"]
    new_description = board["description"]

    if title is not None:
        new_title = _trim_required(title, "title")
    if description is not None:
        new_description = description.strip()

    conn.execute(
        "UPDATE boards SET title = ?, description = ? WHERE id = ?",
        (new_title, new_description, board_id),
    )
    # Renaming a board is a board mutation like any other: without the bump, a client holding the
    # pre-rename version stays "current" and its next full-snapshot PUT reverts the title.
    # `_touch_board` stamps `updated_at` too.
    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def delete_board(conn: sqlite3.Connection, board_id: str) -> None:
    _get_board_row(conn, board_id)
    conn.execute("DELETE FROM boards WHERE id = ?", (board_id,))
    conn.commit()


def create_column(conn: sqlite3.Connection, board_id: str, title: str, position: int | None) -> dict:
    _get_board_row(conn, board_id)
    clean_title = _trim_required(title, "title")
    now = _now()

    total = conn.execute(
        "SELECT COUNT(*) as count FROM board_columns WHERE board_id = ?",
        (board_id,),
    ).fetchone()["count"]

    insert_position = total if position is None else max(0, min(position, total))
    conn.execute(
        "UPDATE board_columns SET position = position + 1, updated_at = ? WHERE board_id = ? AND position >= ?",
        (now, board_id, insert_position),
    )

    column_id = _new_id("col")
    conn.execute(
        """
        INSERT INTO board_columns (id, board_id, title, position, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (column_id, board_id, clean_title, insert_position, now, now),
    )
    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def update_column(conn: sqlite3.Connection, board_id: str, column_id: str, title: str) -> dict:
    _get_column_row(conn, board_id, column_id)
    clean_title = _trim_required(title, "title")
    now = _now()

    conn.execute(
        "UPDATE board_columns SET title = ?, updated_at = ? WHERE id = ?",
        (clean_title, now, column_id),
    )
    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def delete_column(conn: sqlite3.Connection, board_id: str, column_id: str) -> dict:
    _get_column_row(conn, board_id, column_id)
    conn.execute("DELETE FROM board_columns WHERE id = ?", (column_id,))
    _reindex_column_positions(conn, board_id)
    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def reorder_columns(conn: sqlite3.Connection, board_id: str, column_ids: list[str]) -> dict:
    _get_board_row(conn, board_id)

    existing_rows = conn.execute(
        "SELECT id FROM board_columns WHERE board_id = ?",
        (board_id,),
    ).fetchall()
    existing_ids = {row["id"] for row in existing_rows}

    if set(column_ids) != existing_ids:
        raise HTTPException(status_code=400, detail="columnIds must include all columns exactly once")

    now = _now()
    for idx, column_id in enumerate(column_ids):
        conn.execute(
            "UPDATE board_columns SET position = ?, updated_at = ? WHERE id = ?",
            (idx, now, column_id),
        )

    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def create_card(
    conn: sqlite3.Connection,
    board_id: str,
    column_id: str,
    title: str,
    description: str,
    priority: str,
    due_date: str | None,
    assignees: list[str],
) -> dict:
    _get_board_row(conn, board_id)
    _get_column_row(conn, board_id, column_id)
    clean_title = _trim_required(title, "title")
    now = _now()

    position = conn.execute(
        "SELECT COUNT(*) as count FROM cards WHERE board_id = ? AND column_id = ?",
        (board_id, column_id),
    ).fetchone()["count"]

    card_id = _new_id("card")
    conn.execute(
        """
        INSERT INTO cards (
          id, board_id, column_id, title, description, priority, due_date,
          completed, position, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card_id,
            board_id,
            column_id,
            clean_title,
            description.strip(),
            priority,
            due_date,
            0,
            position,
            now,
            now,
        ),
    )

    for member_id in assignees:
        _get_member_row(conn, board_id, member_id)
        conn.execute(
            "INSERT OR IGNORE INTO card_assignees (card_id, member_id, created_at) VALUES (?, ?, ?)",
            (card_id, member_id, now),
        )

    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def _next_completed_at(was_completed: bool, now_completed: bool, prev_completed_at, now: str):
    """Completion-timestamp transition: stamp `now` only when a card first becomes
    completed, preserve the existing stamp while it stays completed, and clear it when un-completed.
    Keeps `completed_at` a true completion time that board edits don't disturb."""
    if not now_completed:
        return None
    if was_completed:
        return prev_completed_at or now  # already completed — keep (backfill if missing)
    return now                           # just completed


def update_card(
    conn: sqlite3.Connection,
    board_id: str,
    card_id: str,
    title: str | None,
    description: str | None,
    priority: str | None,
    due_date: str | None,
    completed: bool | None,
) -> dict:
    card = _get_card_row(conn, board_id, card_id)

    next_title = card["title"] if title is None else _trim_required(title, "title")
    next_description = card["description"] if description is None else description.strip()
    next_priority = card["priority"] if priority is None else priority
    next_due_date = card["due_date"] if due_date is None else due_date
    next_completed = int(bool(card["completed"])) if completed is None else int(completed)

    now = _now()
    completed_at = _next_completed_at(
        bool(card["completed"]), bool(next_completed), card["completed_at"], now
    )
    conn.execute(
        """
        UPDATE cards
        SET title = ?, description = ?, priority = ?, due_date = ?, completed = ?,
            completed_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            next_title,
            next_description,
            next_priority,
            next_due_date,
            next_completed,
            completed_at,
            now,
            card_id,
        ),
    )
    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def delete_card(conn: sqlite3.Connection, board_id: str, card_id: str) -> dict:
    card = _get_card_row(conn, board_id, card_id)
    source_column = card["column_id"]
    conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
    _reindex_card_positions(conn, board_id, source_column)
    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def move_card(
    conn: sqlite3.Connection,
    board_id: str,
    card_id: str,
    target_column_id: str,
    target_position: int | None,
) -> dict:
    card = _get_card_row(conn, board_id, card_id)
    _get_column_row(conn, board_id, target_column_id)

    source_column_id = card["column_id"]
    now = _now()

    if source_column_id == target_column_id:
        total = conn.execute(
            "SELECT COUNT(*) as count FROM cards WHERE board_id = ? AND column_id = ?",
            (board_id, target_column_id),
        ).fetchone()["count"]
        desired = max(0, min(target_position if target_position is not None else total - 1, max(total - 1, 0)))

        conn.execute(
            "UPDATE cards SET position = -1, updated_at = ? WHERE id = ?",
            (now, card_id),
        )
        conn.execute(
            "UPDATE cards SET position = position - 1, updated_at = ? WHERE board_id = ? AND column_id = ? AND position > ?",
            (now, board_id, source_column_id, card["position"]),
        )
        conn.execute(
            "UPDATE cards SET position = position + 1, updated_at = ? WHERE board_id = ? AND column_id = ? AND position >= ?",
            (now, board_id, target_column_id, desired),
        )
        conn.execute(
            "UPDATE cards SET column_id = ?, position = ?, updated_at = ? WHERE id = ?",
            (target_column_id, desired, now, card_id),
        )
    else:
        conn.execute(
            "UPDATE cards SET position = position - 1, updated_at = ? WHERE board_id = ? AND column_id = ? AND position > ?",
            (now, board_id, source_column_id, card["position"]),
        )

        destination_total = conn.execute(
            "SELECT COUNT(*) as count FROM cards WHERE board_id = ? AND column_id = ?",
            (board_id, target_column_id),
        ).fetchone()["count"]
        desired = max(0, min(target_position if target_position is not None else destination_total, destination_total))

        conn.execute(
            "UPDATE cards SET position = position + 1, updated_at = ? WHERE board_id = ? AND column_id = ? AND position >= ?",
            (now, board_id, target_column_id, desired),
        )
        conn.execute(
            "UPDATE cards SET column_id = ?, position = ?, updated_at = ? WHERE id = ?",
            (target_column_id, desired, now, card_id),
        )

    _reindex_card_positions(conn, board_id, source_column_id)
    _reindex_card_positions(conn, board_id, target_column_id)
    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def reorder_cards(conn: sqlite3.Connection, board_id: str, column_id: str, card_ids: list[str]) -> dict:
    _get_board_row(conn, board_id)
    _get_column_row(conn, board_id, column_id)

    existing_rows = conn.execute(
        "SELECT id FROM cards WHERE board_id = ? AND column_id = ? ORDER BY position",
        (board_id, column_id),
    ).fetchall()
    existing_ids = [row["id"] for row in existing_rows]

    if set(card_ids) != set(existing_ids) or len(card_ids) != len(existing_ids):
        raise HTTPException(status_code=400, detail="cardIds must include all cards in the column exactly once")

    now = _now()
    for idx, card_id in enumerate(card_ids):
        conn.execute(
            "UPDATE cards SET position = ?, updated_at = ? WHERE id = ?",
            (idx, now, card_id),
        )

    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def toggle_card(conn: sqlite3.Connection, board_id: str, card_id: str, completed: bool | None) -> dict:
    card = _get_card_row(conn, board_id, card_id)
    next_completed = int(not bool(card["completed"])) if completed is None else int(completed)
    now = _now()
    completed_at = _next_completed_at(
        bool(card["completed"]), bool(next_completed), card["completed_at"], now
    )
    conn.execute(
        "UPDATE cards SET completed = ?, completed_at = ?, updated_at = ? WHERE id = ?",
        (next_completed, completed_at, now, card_id),
    )
    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def create_member(conn: sqlite3.Connection, board_id: str, name: str, initials: str, color: str) -> dict:
    _get_board_row(conn, board_id)
    now = _now()
    member_id = _new_id("member")
    conn.execute(
        """
        INSERT INTO members (id, board_id, name, initials, color, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            member_id,
            board_id,
            _trim_required(name, "name"),
            _trim_required(initials, "initials"),
            _trim_required(color, "color"),
            now,
            now,
        ),
    )
    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def update_member(
    conn: sqlite3.Connection,
    board_id: str,
    member_id: str,
    name: str | None,
    initials: str | None,
    color: str | None,
) -> dict:
    member = _get_member_row(conn, board_id, member_id)

    next_name = member["name"] if name is None else _trim_required(name, "name")
    next_initials = member["initials"] if initials is None else _trim_required(initials, "initials")
    next_color = member["color"] if color is None else _trim_required(color, "color")

    conn.execute(
        "UPDATE members SET name = ?, initials = ?, color = ?, updated_at = ? WHERE id = ?",
        (next_name, next_initials, next_color, _now(), member_id),
    )
    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def delete_member(conn: sqlite3.Connection, board_id: str, member_id: str) -> dict:
    _get_member_row(conn, board_id, member_id)
    conn.execute("DELETE FROM members WHERE id = ?", (member_id,))
    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def assign_member_to_card(conn: sqlite3.Connection, board_id: str, card_id: str, member_id: str) -> dict:
    _get_card_row(conn, board_id, card_id)
    _get_member_row(conn, board_id, member_id)
    conn.execute(
        "INSERT OR IGNORE INTO card_assignees (card_id, member_id, created_at) VALUES (?, ?, ?)",
        (card_id, member_id, _now()),
    )
    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def unassign_member_from_card(conn: sqlite3.Connection, board_id: str, card_id: str, member_id: str) -> dict:
    _get_card_row(conn, board_id, card_id)
    _get_member_row(conn, board_id, member_id)
    conn.execute(
        "DELETE FROM card_assignees WHERE card_id = ? AND member_id = ?",
        (card_id, member_id),
    )
    _touch_board(conn, board_id)
    conn.commit()
    return get_board(conn, board_id)


def sync_board_snapshot(conn: sqlite3.Connection, board_id: str, board_snapshot: dict) -> dict:
    if board_snapshot.get("id") != board_id:
        raise HTTPException(status_code=400, detail="Snapshot id must match board_id")

    board = _get_board_row(conn, board_id)
    now = _now()

    # This write path replaces the whole board from the client's snapshot, so a client that
    # never saw a server-side creation (e.g. a card from apply) would delete it. A declared base
    # version that is not current is rejected whole — no partial write — so the stale client must
    # re-hydrate and reapply. `None` means no version was declared, which no HTTP caller can do
    # (`BoardSnapshotInput.version` is required, so a versionless body is a 422); it is the
    # in-process path — tests and other direct callers of this function — and the guard simply does
    # not apply there. The version is server-owned and is never read from the client.
    current_version = int(board["version"])
    declared_version = board_snapshot.get("version")
    if declared_version is not None and int(declared_version) != current_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "BOARD_VERSION_STALE",
                "message": "The board changed since this snapshot was loaded.",
                "action": "Refresh the board and reapply your change.",
            },
        )

    board_title = _trim_required(board_snapshot["projectInfo"]["title"], "title")
    board_description = board_snapshot["projectInfo"].get("description", "").strip()
    created_at = board["created_at"]
    updated_at = now  # child rows (columns/cards/members) are stamped server-side, not from the client

    # `updated_at` is set server-side (never taken from the client), and the
    # version is bumped: a snapshot write is a mutation like any other.
    conn.execute(
        "UPDATE boards SET title = ?, description = ?, updated_at = ?, version = version + 1 WHERE id = ?",
        (board_title, board_description, now, board_id),
    )

    # Capture current card→column mapping before delete (feeds card_column_events)
    try:
        _old_card_cols: dict[str, tuple[str, str]] = {
            row["id"]: (row["column_id"], row["col_title"])
            for row in conn.execute(
                """
                SELECT c.id, c.column_id, bc.title AS col_title
                FROM cards c
                JOIN board_columns bc ON bc.id = c.column_id
                WHERE c.board_id = ?
                """,
                (board_id,),
            ).fetchall()
        }
    except Exception:
        _old_card_cols = {}

    # Capture prior completion state before the delete, so re-inserted cards keep a true
    # completion time instead of being re-stamped to "now" on every save.
    try:
        _old_card_completion: dict[str, tuple[int, str]] = {
            row["id"]: (int(row["completed"]), row["completed_at"])
            for row in conn.execute(
                "SELECT id, completed, completed_at FROM cards WHERE board_id = ?",
                (board_id,),
            ).fetchall()
        }
    except Exception:
        _old_card_completion = {}

    conn.execute("DELETE FROM cards WHERE board_id = ?", (board_id,))
    conn.execute("DELETE FROM members WHERE board_id = ?", (board_id,))
    conn.execute("DELETE FROM board_columns WHERE board_id = ?", (board_id,))

    column_order = board_snapshot.get("columnOrder", [])
    columns = board_snapshot.get("columns", {})
    _new_col_titles: dict[str, str] = {cid: col.get("title", "") for cid, col in columns.items()}
    cards = board_snapshot.get("cards", {})
    team_members = board_snapshot.get("teamMembers", [])

    for position, column_id in enumerate(column_order):
        column = columns.get(column_id)
        if not column:
            raise HTTPException(status_code=400, detail=f"Missing column data for {column_id}")
        conn.execute(
            """
            INSERT INTO board_columns (id, board_id, title, position, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                column_id,
                board_id,
                _trim_required(column["title"], "column title"),
                position,
                created_at,
                updated_at,
            ),
        )

    for member in team_members:
        conn.execute(
            """
            INSERT INTO members (id, board_id, name, initials, color, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                member["id"],
                board_id,
                _trim_required(member["name"], "member name"),
                _trim_required(member["initials"], "member initials"),
                _trim_required(member["color"], "member color"),
                created_at,
                updated_at,
            ),
        )

    seen_cards: set[str] = set()
    for column_id in column_order:
        card_ids = columns.get(column_id, {}).get("cardIds", [])
        for position, card_id in enumerate(card_ids):
            card = cards.get(card_id)
            if not card:
                raise HTTPException(status_code=400, detail=f"Missing card data for {card_id}")
            if card_id in seen_cards:
                raise HTTPException(status_code=400, detail=f"Duplicate card id {card_id}")
            seen_cards.add(card_id)
            _now_completed = int(bool(card.get("completed", False)))
            _prev = _old_card_completion.get(card_id)
            _completed_at = _next_completed_at(
                bool(_prev[0]) if _prev else False,
                bool(_now_completed),
                _prev[1] if _prev else None,
                updated_at,
            )
            conn.execute(
                """
                INSERT INTO cards (
                  id, board_id, column_id, title, description, priority, due_date,
                  completed, completed_at, position, created_at, updated_at,
                  story_points, jira_key, labels_json, blocked_by_json, depends_on_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    card_id,
                    board_id,
                    column_id,
                    _trim_required(card["title"], "card title"),
                    card.get("description", "").strip(),
                    card.get("priority", "medium"),
                    card.get("dueDate"),
                    _now_completed,
                    _completed_at,
                    position,
                    card.get("createdAt") or created_at,
                    updated_at,
                    card.get("storyPoints"),
                    (card.get("jiraKey") or "").strip() or None,
                    json.dumps(card.get("labels", [])),
                    json.dumps(card.get("blockedBy", [])),
                    json.dumps(card.get("dependsOn", [])),
                ),
            )

            for member_id in card.get("assignees", []):
                if not _row_exists(
                    conn,
                    "SELECT 1 FROM members WHERE board_id = ? AND id = ?",
                    (board_id, member_id),
                ):
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO card_assignees (card_id, member_id, created_at) VALUES (?, ?, ?)",
                    (card_id, member_id, updated_at),
                )

    # Record column transition events for cards that moved columns
    try:
        for col_id in column_order:
            for cid in columns.get(col_id, {}).get("cardIds", []):
                old = _old_card_cols.get(cid)
                if old and old[0] != col_id:
                    conn.execute(
                        """
                        INSERT INTO card_column_events
                          (card_id, board_id, from_column_id, from_column_title,
                           to_column_id, to_column_title, moved_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (cid, board_id, old[0], old[1],
                         col_id, _new_col_titles.get(col_id, ""), now),
                    )
    except Exception:
        pass  # card_column_events table may not exist on first run

    conn.commit()
    return get_board(conn, board_id)


# ── Teams ─────────────────────────────────────────────────────────────────────

def _team_row_to_dict(row) -> dict:
    rd = dict(row)
    return {
        "id": rd["id"],
        "name": rd["name"],
        "color": rd["color"],
        "jiraPrefix": rd.get("jira_prefix"),
        "description": rd.get("description", ""),
        "createdAt": rd["created_at"],
        "updatedAt": rd["updated_at"],
    }


# The six rows seeded by migration 0004, before teams had an owner. They are shared by every
# Instance because demo boards reference them, and they are read-only for exactly the same reason.
# Membership is by id, never by "instance_id IS NULL": on a database upgraded from before 0016, a
# NULL is just as likely to be somebody's own team, and treating those as shared would hand one
# tenant's teams to every other one.
SEEDED_TEAM_IDS = frozenset(
    {"team_eng", "team_product", "team_design", "team_legal", "team_mkt", "team_sales"}
)


def _team_is_writable_by(row, instance_id: str | None) -> bool:
    """Who may change a team row.

    With sign-in off (`instance_id is None`) everything is open, like every other route in that
    mode. With sign-in on, a caller may change only rows carrying their own instance_id — the
    seeded defaults and every other Instance's teams are read-only.
    """
    if instance_id is None:
        return True
    return dict(row).get("instance_id") == instance_id


def list_teams(conn: sqlite3.Connection, instance_id: str | None = None) -> list[dict]:
    """The caller's own teams, plus the six shared defaults.

    With sign-in off there is no caller identity, so every row is returned — the same posture as
    the rest of the app in that mode.

    An unowned row that is *not* one of the defaults is invisible to everyone: it can only come
    from a database predating 0016 whose owner could not be inferred (migration 0017 backfills the
    ones that can). Fail closed — showing it to every tenant is the failure this scoping exists to
    prevent.
    """
    if instance_id is None:
        rows = conn.execute("SELECT * FROM teams ORDER BY name").fetchall()
    else:
        placeholders = ",".join("?" for _ in SEEDED_TEAM_IDS)
        rows = conn.execute(
            f"SELECT * FROM teams WHERE instance_id = ? OR id IN ({placeholders}) ORDER BY name",
            (instance_id, *sorted(SEEDED_TEAM_IDS)),
        ).fetchall()
    return [_team_row_to_dict(r) for r in rows]


def create_team(conn: sqlite3.Connection, name: str, color: str, jira_prefix: str | None, description: str,
                instance_id: str | None = None) -> dict:
    now = _now()
    team_id = _new_id("team")
    clean_name = _trim_required(name, "team name")
    conn.execute(
        "INSERT INTO teams (id, name, color, jira_prefix, description, created_at, updated_at, instance_id)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (team_id, clean_name, color, jira_prefix or None, description.strip(), now, now, instance_id),
    )
    conn.commit()
    return _team_row_to_dict(conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone())


def update_team(conn: sqlite3.Connection, team_id: str, name: str | None, color: str | None, jira_prefix: str | None, description: str | None,
                instance_id: str | None = None) -> dict:
    row = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if row is None or not _team_is_writable_by(row, instance_id):
        # 404 rather than 403: a caller learns nothing about another Instance's teams.
        raise HTTPException(status_code=404, detail="Team not found")
    now = _now()
    conn.execute(
        "UPDATE teams SET name=?, color=?, jira_prefix=?, description=?, updated_at=? WHERE id=?",
        (
            name.strip() if name else row["name"],
            color if color else row["color"],
            jira_prefix if jira_prefix is not None else row["jira_prefix"],
            description.strip() if description is not None else row["description"],
            now, team_id,
        ),
    )
    conn.commit()
    return _team_row_to_dict(conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone())


def delete_team(conn: sqlite3.Connection, team_id: str, instance_id: str | None = None) -> None:
    row = conn.execute("SELECT id, instance_id FROM teams WHERE id = ?", (team_id,)).fetchone()
    if row is None or not _team_is_writable_by(row, instance_id):
        raise HTTPException(status_code=404, detail="Team not found")
    conn.execute("UPDATE boards SET team_id = NULL WHERE team_id = ?", (team_id,))
    conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
    conn.commit()


# ── Feedback ──────────────────────────────────────────────────────────────────────────────────

FEEDBACK_CATEGORIES = ("chat", "notes", "general", "bug")


def _feedback_now() -> str:
    """Sub-second timestamp for feedback rows — the shared `_now()`, kept as a named seam.

    Feedback is read newest-first and ids are random uuids, so ordering rests on `created_at`: at
    whole-second resolution two notes written in the same second would sort arbitrarily, and the
    tie-break would make it look deliberate. Two users reacting to the same thing at once is
    exactly when that happens.
    """
    return _now()


def _feedback_row_to_dict(row) -> dict:
    rd = dict(row)  # sqlite3.Row has no .get()
    return {
        "id": rd["id"],
        "instanceId": rd.get("instance_id"),
        "userId": rd.get("user_id"),
        "userEmail": rd.get("user_email"),
        "category": rd["category"],
        "rating": rd.get("rating"),
        "message": rd["message"],
        "page": rd.get("page"),
        "createdAt": rd["created_at"],
    }


def create_feedback(
    conn: sqlite3.Connection,
    *,
    category: str,
    message: str,
    rating: int | None = None,
    page: str | None = None,
    instance_id: str | None = None,
    user_id: str | None = None,
    user_email: str | None = None,
) -> dict:
    """Record one piece of feedback. Never rejects for attribution — an anonymous note is still
    worth having, and local dev has no caller identity at all."""
    text = (message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Feedback message is required")
    if category not in FEEDBACK_CATEGORIES:
        category = "general"
    if rating is not None and not (1 <= int(rating) <= 5):
        rating = None

    row_id = _new_id("fb")
    conn.execute(
        """
        INSERT INTO feedback (id, instance_id, user_id, user_email, category, rating, message, page, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (row_id, instance_id, user_id, user_email, category, rating, text, page, _feedback_now()),
    )
    conn.commit()
    return _feedback_row_to_dict(conn.execute("SELECT * FROM feedback WHERE id = ?", (row_id,)).fetchone())


def list_feedback(conn: sqlite3.Connection, *, instance_id: str | None = None, limit: int = 200) -> list[dict]:
    """Newest first. Scoped to an Instance when one is given — a PIU reads their own space only.

    Ordered by `created_at` and then `id`, never by rowid: that pseudo-column is SQLite-only, and
    this query must also run on Postgres (a supported deployment backend), where it would fail
    outright.
    """
    capped = max(1, min(int(limit), 500))
    if instance_id:
        rows = conn.execute(
            "SELECT * FROM feedback WHERE instance_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (instance_id, capped),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM feedback ORDER BY created_at DESC, id DESC LIMIT ?", (capped,)
        ).fetchall()
    return [_feedback_row_to_dict(r) for r in rows]


def board_ids_in_workspace(conn: sqlite3.Connection, workspace_id: str) -> set[str]:
    """Board ids belonging to one workspace.

    Used to scope the assistant to the workspace the user is standing in. This is a *view* scope,
    not an authorization boundary — callers must still intersect it with the tenant fence
    (`accessible_board_ids`) when Cognito is on, never substitute it for one.
    """
    rows = conn.execute("SELECT id FROM boards WHERE workspace_id = ?", (workspace_id,)).fetchall()
    return {dict(r)["id"] for r in rows}
