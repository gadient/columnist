"""Persistence for note-import sessions.

Deliberately separate from the top-level `store.py`: that module's helpers each call
`conn.commit()` internally, which is exactly what the apply path must not do.
Keeping this feature's persistence in its own module gives the apply path the caller-managed
transaction it needs: the card, an optional new member, and the idempotency record all commit
together or not at all.

Analysis mutates nothing on the board — the mutation boundary means the board is untouched until
a human approves. `apply_recommendation` is the *one* function here that crosses that boundary, and
only ever inside a single transaction, only from an explicit approval.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def board_version(conn: sqlite3.Connection, board_id: str) -> int | None:
    """The board's server-authoritative version, or None if no such board."""
    row = conn.execute("SELECT version FROM boards WHERE id = ?", (board_id,)).fetchone()
    return None if row is None else int(row["version"])


def create_session(
    conn: sqlite3.Connection,
    *,
    board_id: str,
    instance_id: str,
    actor_id: str | None,
    board_ver: int,
    input_mode: str,
    meeting_date: str,
    user_timezone: str | None,
    original_filename: str | None,
    declared_media_type: str | None,
    detected_media_type: str | None,
    byte_size: int | None,
    content_sha256: str | None,
    extracted_char_count: int,
    input_tokens_estimated: int,
    status: str,
) -> dict[str, Any]:
    """Record one analysis lifecycle.

    Note what is *not* stored: the uploaded bytes and the full extracted text. The source stays
    out of persistence — only the structured proposal, minimal excerpts, the input hash
    and metadata survive. The hash is what lets us recognise the same input later without
    keeping it.
    """
    session_id = f"imp_{uuid.uuid4().hex}"
    now = _now()
    conn.execute(
        """
        INSERT INTO note_import_sessions (
            id, instance_id, board_id, actor_id, board_version,
            input_mode, original_filename, declared_media_type, detected_media_type,
            byte_size, content_sha256,
            meeting_date, user_timezone,
            extracted_char_count, input_tokens_estimated,
            status, started_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id, instance_id, board_id, actor_id, board_ver,
            input_mode, original_filename, declared_media_type, detected_media_type,
            byte_size, content_sha256,
            meeting_date, user_timezone,
            extracted_char_count, input_tokens_estimated,
            status, now, now, now,
        ),
    )
    conn.commit()
    return {
        "id": session_id,
        "boardId": board_id,
        "boardVersion": board_ver,
        "meetingDate": meeting_date,
        "inputMode": input_mode,
        "status": status,
    }


def get_session(conn: sqlite3.Connection, session_id: str, *, board_id: str) -> dict[str, Any] | None:
    """Fetch a session, scoped to its bound board.

    `board_id` is not decoration: a session is never returned for another board, because it is
    bound permanently to the board it was created on. Scoping the
    lookup means a wrong-board request is a miss, not an access check someone can forget to write.
    """
    row = conn.execute(
        "SELECT * FROM note_import_sessions WHERE id = ? AND board_id = ?",
        (session_id, board_id),
    ).fetchone()
    return dict(row) if row else None


def insert_recommendation(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    position: int,
    model_rec: Any,
    view: Any,
) -> None:
    """Persist one recommendation. Does NOT commit — the caller owns the transaction so a
    whole set (or a streamed card) commits on its own terms.

    Two representations are stored side by side, exactly as 0012 and 0013 intend: `model_output_json`
    is the immutable model output, and the resolved parts — owners matched to the roster, the
    stamped column, the blocked verdict — are stored separately because they are code's decision and
    (for blockedReasons/column) cannot be recomputed once the note is gone."""
    now = _now()
    conn.execute(
        """
        INSERT INTO note_import_recommendations (
            id, session_id, position,
            model_output_json, evidence_json, assignees_json,
            target_column_id, blocked_reasons_json, requires_new_member_named,
            state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            view.id,
            session_id,
            position,
            model_rec.model_dump_json(),
            json.dumps([e.model_dump() for e in model_rec.evidence]),
            json.dumps([a.model_dump() for a in view.assignees]),
            view.targetColumnId,
            json.dumps(view.blockedReasons),
            view.requiresNewMemberNamed,
            view.state,
            now,
            now,
        ),
    )


def update_recommendation_column(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    rec_id: str,
    board_id: str,
    target_column_id: str,
) -> dict[str, Any]:
    """Re-target one still-pending recommendation to a different column, per card.

    The column is the one field the model never proposes and the reviewer alone decides: notes
    don't name columns, so there is nothing to suggest. It is therefore chosen per card during review
    and persisted *here*, on the stored resolution — which is what the bulk `Add the clean ones` path
    reads (see `_snapshot_of_stored`). Persisting means the card is created where the reviewer put it,
    whether it goes out one-by-one or in the batch, and the choice survives a refresh.

    Only a pending recommendation can be re-targeted; once applied, the card is moved on the board
    like any other. The column is validated against this board — never trusted from the client.
    Commits its own single-row update."""
    from .errors import ImportError as ImportRejection

    rec = get_recommendation(conn, session_id, rec_id)
    if rec is None:
        # A recommendation this session does not hold is a reference that no longer resolves, which
        # is why it reuses BOARD_REFERENCE_STALE (400, "refresh and try again"). The catalogue
        # wording names columns and members specifically and so under-describes this case; `detail`
        # carries what actually went wrong.
        raise ImportRejection("BOARD_REFERENCE_STALE", detail="That recommendation no longer exists.")
    if rec["state"] != "pending":
        raise ImportRejection("APPLY_CONFLICT", detail="This card has already been created; move it on the board instead.")
    if conn.execute(
        "SELECT 1 FROM board_columns WHERE board_id = ? AND id = ?", (board_id, target_column_id)
    ).fetchone() is None:
        raise ImportRejection("TARGET_COLUMN_REQUIRED", detail="That column is not on this board.")

    conn.execute(
        "UPDATE note_import_recommendations SET target_column_id = ?, updated_at = ? "
        "WHERE id = ? AND session_id = ?",
        (target_column_id, _now(), rec_id, session_id),
    )
    conn.commit()
    return next(v for v in get_recommendations(conn, session_id) if v["id"] == rec_id)


def set_partial_failure_count(conn: sqlite3.Connection, session_id: str, count: int) -> None:
    """Record how many items the model returned that we could not read. Does not commit."""
    conn.execute(
        "UPDATE note_import_sessions SET partial_failure_count = ?, updated_at = ? WHERE id = ?",
        (count, _now(), session_id),
    )


def get_recommendations(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    """Rebuild each stored recommendation as a `RecommendationView` dict, in display order.

    Reconstruction goes *through* the contract (`RecommendationView(...).model_dump()`), not around
    it: the note-free GET returns byte-identical shape to what `/analyze` returned, and a stored row
    that somehow violated the contract fails loudly here rather than reaching the client malformed.
    """
    from .schema import RecommendationView

    # LEFT JOIN the successful application so an applied recommendation reports the card it became
    # The join keeps createdCardId a read of what actually happened, not a second field to
    # keep in sync on the recommendation row.
    rows = conn.execute(
        """
        SELECT r.*, a.created_card_id AS applied_card_id
        FROM note_import_recommendations r
        LEFT JOIN note_import_applications a
          ON a.recommendation_id = r.id AND a.status = 'succeeded'
        WHERE r.session_id = ?
        ORDER BY r.position
        """,
        (session_id,),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        rd = dict(row)
        mo = json.loads(rd["model_output_json"])
        view = RecommendationView(
            id=rd["id"],
            action="create_card",
            title=mo["title"],
            description=mo.get("description", ""),
            targetColumnId=rd["target_column_id"],
            assignees=json.loads(rd["assignees_json"]),
            dueDate=mo["dueDate"],
            priority=mo["priority"],
            evidence=mo["evidence"],
            reason=mo["reason"],
            confidence=mo["confidence"],
            ambiguities=mo.get("ambiguities", []),
            state=rd["state"],
            blockedReasons=json.loads(rd["blocked_reasons_json"]),
            requiresNewMemberNamed=rd["requires_new_member_named"],
            createdCardId=rd["applied_card_id"],
        )
        out.append(view.model_dump())
    return out


# ---------------------------------------------------------------------------------------------
# Apply — the one board mutation
# ---------------------------------------------------------------------------------------------

from .errors import ImportError as ImportRejection  # noqa: E402  (kept beside the code that uses it)

# Deterministic presentation defaults for a board member created during apply. Editable
# afterwards wherever the existing member UI allows — these are only the starting values.
_MEMBER_COLORS = ("#ef4444", "#f59e0b", "#10b981", "#3b82f6", "#8b5cf6", "#ec4899", "#14b8a6")


def _new_id(prefix: str) -> str:
    """Match the top-level store's id shape so an applied card is indistinguishable from any other."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _member_initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _member_color(name: str) -> str:
    idx = int(hashlib.sha256(name.encode("utf-8")).hexdigest(), 16) % len(_MEMBER_COLORS)
    return _MEMBER_COLORS[idx]


def snapshot_sha256(snapshot: dict) -> str:
    """A stable hash of the exact approved values. A replay carrying the same hash gets
    the original card back; a *different* hash for an already-applied recommendation is APPLY_CONFLICT
    — the thing was created once, and this is a second, changed attempt."""
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_recommendation(conn: sqlite3.Connection, session_id: str, rec_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM note_import_recommendations WHERE id = ? AND session_id = ?",
        (rec_id, session_id),
    ).fetchone()
    return dict(row) if row else None


def _find_existing_member_by_name(conn: sqlite3.Connection, board_id: str, name: str) -> str | None:
    """An existing board member whose name matches `name` case-insensitively (trimmed), or None.

    This is what makes `Create <name>` idempotent by name: when the same unmatched owner
    appears on two flagged cards — "Priya" on both — approving the create on each must not mint two
    board members. The second apply finds the member the first one made and reuses it. Exact casefold
    match only, the same tier-2 identity the resolver uses; a mere spelling variant ("Priya" vs
    "Priya S.") is the user's judgement call, not a dedupe code should silently make."""
    key = name.strip().casefold()
    for row in conn.execute(
        "SELECT id, name FROM members WHERE board_id = ?", (board_id,)
    ).fetchall():
        if row["name"].strip().casefold() == key:
            return row["id"]
    return None


def _insert_member(conn: sqlite3.Connection, board_id: str, name: str) -> str:
    member_id = _new_id("member")
    now = _now()
    conn.execute(
        "INSERT INTO members (id, board_id, name, initials, color, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (member_id, board_id, name.strip(), _member_initials(name), _member_color(name), now, now),
    )
    return member_id


def _insert_card(
    conn: sqlite3.Connection,
    *,
    board_id: str,
    column_id: str,
    title: str,
    description: str,
    priority: str,
    due_date: str | None,
    assignee_ids: list[str],
) -> str:
    now = _now()
    position = conn.execute(
        "SELECT COUNT(*) AS n FROM cards WHERE board_id = ? AND column_id = ?", (board_id, column_id)
    ).fetchone()["n"]
    card_id = _new_id("card")
    conn.execute(
        "INSERT INTO cards (id, board_id, column_id, title, description, priority, due_date, "
        "completed, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (card_id, board_id, column_id, title.strip(), (description or "").strip(), priority,
         due_date, 0, position, now, now),
    )
    for member_id in assignee_ids:
        conn.execute(
            "INSERT OR IGNORE INTO card_assignees (card_id, member_id, created_at) VALUES (?, ?, ?)",
            (card_id, member_id, now),
        )
    return card_id


def _replay(prior: dict) -> dict:
    return {
        "recommendationId": prior["recommendation_id"],
        "createdCardId": prior["created_card_id"],
        "createdMemberId": prior["created_member_id"],
        "state": "created",
        "replayed": True,
    }


def apply_recommendation(
    conn: sqlite3.Connection,
    *,
    session: dict,
    rec_id: str,
    actor_id: str | None,
    snapshot: dict,
    idempotency_key: str,
) -> dict[str, Any]:
    """Create one real card from an approved recommendation — the single crossing of the mutation
    boundary. Everything happens in one transaction so the card, an optional new member, and
    the idempotency record commit together or not at all.

    `snapshot` is the EXACT approved values, not a re-reading of the stored recommendation —
    the user may have edited it. Order matters: an already-applied recommendation is settled first
    (replay or conflict) so a retry never creates a second card; then the board references are
    validated so a stale column or removed member is refused *before* anything is written.
    """
    session_id = session["id"]
    board_id = session["board_id"]

    rec = get_recommendation(conn, session_id, rec_id)
    if rec is None:
        # Reused like the re-target path above: a reference that no longer resolves, reported with
        # the specific reason in `detail`.
        raise ImportRejection("BOARD_REFERENCE_STALE", detail="That recommendation is not part of this session.")

    snap_hash = snapshot_sha256(snapshot)

    # Settle an already-applied recommendation before touching anything.
    prior = conn.execute(
        "SELECT * FROM note_import_applications "
        "WHERE session_id = ? AND recommendation_id = ? AND status = 'succeeded'",
        (session_id, rec_id),
    ).fetchone()
    if prior is not None:
        prior = dict(prior)
        if prior["approved_snapshot_sha256"] == snap_hash:
            return _replay(prior)  # a lost response, retried — return the original card
        raise ImportRejection("APPLY_CONFLICT")

    # The approved snapshot must still fit the board. A column or member that has since been
    # deleted is refused here, before any write, as BOARD_REFERENCE_STALE.
    target_column_id = snapshot["targetColumnId"]
    if conn.execute(
        "SELECT 1 FROM board_columns WHERE board_id = ? AND id = ?", (board_id, target_column_id)
    ).fetchone() is None:
        raise ImportRejection("BOARD_REFERENCE_STALE", detail="The destination column no longer exists.")

    assignee_ids = list(snapshot.get("assigneeMemberIds") or [])
    for member_id in assignee_ids:
        if conn.execute(
            "SELECT 1 FROM members WHERE board_id = ? AND id = ?", (board_id, member_id)
        ).fetchone() is None:
            raise ImportRejection("BOARD_REFERENCE_STALE", detail="An assignee is no longer a board member.")

    # A brand-new board member, but only with its own explicit approval, and never a PIU/SIU —
    # a board member only. Created in this transaction so the card and its owner are atomic. But if a
    # member of that name already exists — including one an earlier card in this same import just
    # created — reuse it rather than minting a duplicate "Priya" (dedupe by name, not by id).
    created_member_id = None
    if snapshot.get("memberCreationApproved") and (snapshot.get("newMemberName") or "").strip():
        name = snapshot["newMemberName"]
        existing_id = _find_existing_member_by_name(conn, board_id, name)
        if existing_id is not None:
            assignee_ids.append(existing_id)  # reuse; created_member_id stays None (nothing created)
        else:
            created_member_id = _insert_member(conn, board_id, name)
            assignee_ids.append(created_member_id)

    created_card_id = _insert_card(
        conn,
        board_id=board_id,
        column_id=target_column_id,
        title=snapshot["title"],
        description=snapshot.get("description", ""),
        priority=snapshot.get("priority", "medium"),
        due_date=snapshot.get("dueDate"),
        assignee_ids=assignee_ids,
    )

    now = _now()
    try:
        # The 'succeeded' row IS the idempotency proof, inserted in the same transaction as the card.
        # The partial unique index on (session, rec) WHERE succeeded makes a second success impossible
        # by construction — not by careful coding.
        conn.execute(
            """
            INSERT INTO note_import_applications (
                id, session_id, recommendation_id, instance_id, board_id, actor_id,
                idempotency_key, approved_snapshot_sha256, member_creation_approved,
                created_card_id, created_member_id, attempt_count, status, started_at, ended_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'succeeded', ?, ?)
            """,
            (
                _new_id("app"), session_id, rec_id, session["instance_id"], board_id, actor_id,
                idempotency_key, snap_hash, 1 if created_member_id else 0,
                created_card_id, created_member_id, now, now,
            ),
        )
    except sqlite3.IntegrityError:
        # A concurrent apply of the same recommendation won the unique index. Undo our card and
        # return the winner's result — replay if it was the same approval, conflict if it differed.
        conn.rollback()
        winner = dict(
            conn.execute(
                "SELECT * FROM note_import_applications "
                "WHERE session_id = ? AND recommendation_id = ? AND status = 'succeeded'",
                (session_id, rec_id),
            ).fetchone()
        )
        if winner["approved_snapshot_sha256"] == snap_hash:
            return _replay(winner)
        raise ImportRejection("APPLY_CONFLICT") from None

    # The recommendation now points at the card it became and records the exact snapshot.
    conn.execute(
        "UPDATE note_import_recommendations SET state = 'created', approved_snapshot_json = ?, "
        "state_actor_id = ?, state_changed_at = ?, updated_at = ? WHERE id = ?",
        (json.dumps(snapshot), actor_id, now, now, rec_id),
    )
    # Creating a card is a board mutation, so the server-authoritative version advances. That
    # advance is what makes a stale client's next snapshot write (declaring the old version) fail
    # instead of silently deleting this card. The client must adopt the returned version.
    conn.execute("UPDATE boards SET version = version + 1, updated_at = ? WHERE id = ?", (now, board_id))
    new_version = int(conn.execute("SELECT version FROM boards WHERE id = ?", (board_id,)).fetchone()["version"])

    conn.commit()
    return {
        "recommendationId": rec_id,
        "createdCardId": created_card_id,
        "createdMemberId": created_member_id,
        "state": "created",
        "replayed": False,
        "boardVersion": new_version,
    }


# ---------------------------------------------------------------------------------------------
# Bulk create + undo
# ---------------------------------------------------------------------------------------------


def _snapshot_of_stored(rd: dict) -> dict:
    """Build the approved snapshot for an unflagged recommendation from its stored resolution.

    Same shape as a per-card apply so `approved_snapshot_json` is uniform and undo can compare a
    card against it either way. Only `existing_member` owners contribute an id — but an unflagged
    recommendation has no ambiguous or unmatched owner, so this is a no-op filter, kept as a
    guardrail: a member the model could not place never reaches the board through the bulk path."""
    mo = json.loads(rd["model_output_json"])
    assignees = json.loads(rd["assignees_json"])
    assignee_ids = [
        a["memberId"] for a in assignees
        if a.get("resolution") == "existing_member" and a.get("memberId")
    ]
    return {
        "title": mo["title"],
        "description": mo.get("description", ""),
        "targetColumnId": rd["target_column_id"],
        "assigneeMemberIds": assignee_ids,
        "priority": mo["priority"]["value"],
        "dueDate": mo["dueDate"]["value"] if mo.get("dueDate") else None,
        "memberCreationApproved": False,
        "newMemberName": None,
    }


def apply_batch(
    conn: sqlite3.Connection,
    *,
    session: dict,
    actor_id: str | None,
    idempotency_key: str,
    expected_board_version: int | None = None,
) -> dict[str, Any]:
    """`Add the clean ones`. Create every unflagged, still-pending recommendation in ONE
    transaction, under one board version.

    The unflagged set is chosen *here*, from storage (`state = 'pending'` and no blocked reasons) —
    never from a client-supplied list, so a flagged card cannot be smuggled into the batch.
    All-or-nothing: a stale declared version, or any reference that no longer resolves, fails the
    whole batch rather than applying part of it. Bulk never creates a board member —
    an unmatched owner is flagged, so those recommendations are excluded by construction."""
    session_id = session["id"]
    board_id = session["board_id"]

    current_version = int(
        conn.execute("SELECT version FROM boards WHERE id = ?", (board_id,)).fetchone()["version"]
    )
    if expected_board_version is not None and int(expected_board_version) != current_version:
        raise ImportRejection("BOARD_VERSION_STALE")

    rows = conn.execute(
        "SELECT * FROM note_import_recommendations "
        "WHERE session_id = ? AND state = 'pending' AND blocked_reasons_json = '[]' "
        "ORDER BY position",
        (session_id,),
    ).fetchall()

    batch_id = _new_id("batch")
    now = _now()
    created: list[str] = []

    for row in rows:
        rd = dict(row)
        snapshot = _snapshot_of_stored(rd)

        # Referential revalidation, same as a per-card apply — the batch is a different
        # approval granularity, not a lighter validation path. A failure raises, so the caller's
        # transaction is never committed and NOTHING partially applies.
        if conn.execute(
            "SELECT 1 FROM board_columns WHERE board_id = ? AND id = ?",
            (board_id, snapshot["targetColumnId"]),
        ).fetchone() is None:
            conn.rollback()
            raise ImportRejection("BOARD_REFERENCE_STALE", detail="A destination column no longer exists.")
        for member_id in snapshot["assigneeMemberIds"]:
            if conn.execute(
                "SELECT 1 FROM members WHERE board_id = ? AND id = ?", (board_id, member_id)
            ).fetchone() is None:
                conn.rollback()
                raise ImportRejection("BOARD_REFERENCE_STALE", detail="An assignee is no longer a board member.")

        card_id = _insert_card(
            conn,
            board_id=board_id,
            column_id=snapshot["targetColumnId"],
            title=snapshot["title"],
            description=snapshot["description"],
            priority=snapshot["priority"],
            due_date=snapshot["dueDate"],
            assignee_ids=snapshot["assigneeMemberIds"],
        )
        conn.execute(
            """
            INSERT INTO note_import_applications (
                id, session_id, recommendation_id, instance_id, board_id, actor_id,
                idempotency_key, approved_snapshot_sha256, member_creation_approved,
                created_card_id, created_member_id, attempt_count, status, batch_id,
                started_at, ended_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, 1, 'succeeded', ?, ?, ?)
            """,
            (
                _new_id("app"), session_id, rd["id"], session["instance_id"], board_id, actor_id,
                idempotency_key, snapshot_sha256(snapshot), card_id, batch_id, now, now,
            ),
        )
        conn.execute(
            "UPDATE note_import_recommendations SET state = 'created', approved_snapshot_json = ?, "
            "state_actor_id = ?, state_changed_at = ?, updated_at = ? WHERE id = ?",
            (json.dumps(snapshot), actor_id, now, now, rd["id"]),
        )
        created.append(card_id)

    # One version bump for the whole batch — a single mutation from the board's point of view.
    conn.execute("UPDATE boards SET version = version + 1, updated_at = ? WHERE id = ?", (now, board_id))
    new_version = int(
        conn.execute("SELECT version FROM boards WHERE id = ?", (board_id,)).fetchone()["version"]
    )
    conn.commit()
    return {
        "batchId": batch_id,
        "createdCount": len(created),
        "createdCardIds": created,
        "boardVersion": new_version,
    }


def _card_matches_snapshot(conn: sqlite3.Connection, card: dict, snapshot: dict) -> bool:
    """True only if the card is byte-for-byte what the batch created. Any difference — a moved
    column, an edited title, a changed owner, a completion toggle — means the user touched it, and
    undo must keep it rather than silently delete their work. Conservative on purpose: unsure ⇒ keep."""
    if card["title"] != snapshot["title"].strip():
        return False
    if card["column_id"] != snapshot["targetColumnId"]:
        return False
    if (card["description"] or "") != (snapshot.get("description", "") or "").strip():
        return False
    if card["priority"] != snapshot.get("priority", "medium"):
        return False
    if (card["due_date"] or None) != (snapshot.get("dueDate") or None):
        return False
    if int(card["completed"]) != 0:
        return False
    current = {
        r["member_id"]
        for r in conn.execute(
            "SELECT member_id FROM card_assignees WHERE card_id = ?", (card["id"],)
        ).fetchall()
    }
    return current == set(snapshot.get("assigneeMemberIds") or [])


def undo_batch(
    conn: sqlite3.Connection, *, session: dict, batch_id: str, actor_id: str | None
) -> dict[str, Any]:
    """Remove exactly the cards a bulk create produced. A card the user has since edited
    or moved is **kept and reported**, not silently deleted. Undo is itself an audited, versioned
    mutation: undone applications flip to 'undone' (freeing the per-rec unique slot so the card can be
    re-approved later) and the recommendation returns to pending."""
    session_id = session["id"]
    board_id = session["board_id"]

    apps = conn.execute(
        "SELECT * FROM note_import_applications "
        "WHERE session_id = ? AND batch_id = ? AND status = 'succeeded'",
        (session_id, batch_id),
    ).fetchall()

    now = _now()
    removed: list[str] = []
    kept: list[dict[str, Any]] = []

    for app in apps:
        ad = dict(app)
        card_id = ad["created_card_id"]
        rec_id = ad["recommendation_id"]

        card_row = conn.execute(
            "SELECT * FROM cards WHERE id = ? AND board_id = ?", (card_id, board_id)
        ).fetchone()
        rec = get_recommendation(conn, session_id, rec_id)
        snapshot = json.loads(rec["approved_snapshot_json"]) if rec and rec["approved_snapshot_json"] else None

        if card_row is None:
            kept.append({"recommendationId": rec_id, "cardId": card_id, "reason": "already removed"})
            continue
        if snapshot is None or not _card_matches_snapshot(conn, dict(card_row), snapshot):
            kept.append({"recommendationId": rec_id, "cardId": card_id, "reason": "edited or moved since creation"})
            continue

        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))  # card_assignees cascade
        conn.execute(
            "UPDATE note_import_applications SET status = 'undone', ended_at = ? WHERE id = ?",
            (now, ad["id"]),
        )
        conn.execute(
            "UPDATE note_import_recommendations SET state = 'pending', state_actor_id = ?, "
            "state_changed_at = ?, updated_at = ? WHERE id = ?",
            (actor_id, now, now, rec_id),
        )
        removed.append(rec_id)

    if removed:
        conn.execute("UPDATE boards SET version = version + 1, updated_at = ? WHERE id = ?", (now, board_id))
    new_version = int(
        conn.execute("SELECT version FROM boards WHERE id = ?", (board_id,)).fetchone()["version"]
    )
    conn.commit()
    return {
        "removedCount": len(removed),
        "keptCount": len(kept),
        "kept": kept,
        "boardVersion": new_version,
    }
