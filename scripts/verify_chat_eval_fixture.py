#!/usr/bin/env python3
"""Ground-truth numbers for the read-only management chat's evaluation.

The chat's acceptance checks cite specific counts and card names ("52 cards", "12 overdue", "Taylor
Reyes: 4 open / 63 points"). Those must be TRUE, computed by the real query code, not typed by hand
— a test oracle with wrong numbers trains you to accept wrong answers.

Two things make the demo data an unstable oracle, and this script neutralises both:

1. **Due dates are OFFSETS resolved at load** (`src/data/demoDates.ts`). "Overdue" and "due
   tomorrow" are relative to when the demo was loaded vs. the current clock, so the counts drift
   every day. This script resolves every offset at a FROZEN anchor and freezes the query clock to
   the same day, so the numbers are reproducible.
2. **Every completed card shares one `completed_at`**, so velocity reads the load date and
   returns zero 14 days later. This script SPREADS `completed_at` across the fortnight before the
   anchor, which is also what the real demo seeder must eventually do for velocity to mean anything.

Output is a ground-truth report, plus a frozen resolved fixture written to
`backend/tests/fixtures/chat_eval_frozen_fixture.json`, which `scripts/chat_contract_eval.py` loads directly.

    python3 scripts/verify_chat_eval_fixture.py
    python3 scripts/verify_chat_eval_fixture.py --anchor 2026-07-20

No network, no login, no running stack: it builds a throwaway SQLite database in a temp dir.
Regenerate the raw input after editing the demo boards:
    npx tsx -e "import {DEMO_BOARDS} from './src/data/demoBoards.ts'; \
      process.stdout.write(JSON.stringify(DEMO_BOARDS))" > backend/tests/fixtures/demo_boards_raw.json
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

RAW_FIXTURE = BACKEND / "tests" / "fixtures" / "demo_boards_raw.json"
FROZEN_FIXTURE = BACKEND / "tests" / "fixtures" / "chat_eval_frozen_fixture.json"


def resolve_due(due_in_days, anchor: date):
    """Match `resolveDueDate` in demoDates.ts: local date math, anchor + offset."""
    if not isinstance(due_in_days, int):
        return None
    return (anchor + timedelta(days=due_in_days)).isoformat()


def build_db(conn, boards_raw, anchor: date):
    """Seed the demo boards the way the frontend's loadDemoData does, into one workspace."""
    from app import store

    ws = store.create_workspace(conn, "Demo Workspace")
    resolved = []

    # Spread completed_at deterministically across the 14 days before the anchor, so velocity has a
    # real trend instead of a single spike. Index-based, not random, so it is reproducible.
    completed_seen = 0

    for b in boards_raw:
        board = store.create_board(
            conn, b["title"], b["description"],
            [{"title": c["title"]} for c in b["columns"]],
            [{"name": m["name"], "initials": m["initials"], "color": m["color"]} for m in b["teamMembers"]],
            workspace_id=ws["id"],
        )
        col_ids = board["columnOrder"]
        member_ids = [m["id"] for m in board["teamMembers"]]

        # Deterministic within a board, so a card can name a blocker by its INDEX in the same board
        # and we resolve it to an id here. The prefix differs from the frontend's (`demo_`); what
        # matters is that it is stable, since the committed frozen fixture holds these exact ids.
        card_id_for = lambda i: f"card_{board['id']}_{i}"  # noqa: E731
        cards, cards_by_col = {}, {cid: [] for cid in col_ids}
        for idx, c in enumerate(b["cards"]):
            card_id = card_id_for(idx)
            col_id = col_ids[c["columnIndex"]]
            cards_by_col[col_id].append(card_id)
            cards[card_id] = {
                "id": card_id,
                "title": c["title"],
                "description": c.get("description", ""),
                "priority": c["priority"],
                "dueDate": resolve_due(c.get("dueInDays"), anchor),
                "assignees": [member_ids[i] for i in c.get("assigneeIndices", [])],
                "createdAt": f"{anchor.isoformat()}T00:00:00Z",
                "completed": bool(c.get("completed")),
                "storyPoints": c.get("storyPoints"),
                "labels": c.get("labels", []),
                # Resolve within-board blocker/dependency indices, as the frontend does at load.
                "blockedBy": [card_id_for(i) for i in c.get("blockedByIndices", [])],
                "dependsOn": [card_id_for(i) for i in c.get("dependsOnIndices", [])],
            }
        store.sync_board_snapshot(conn, board["id"], {
            "id": board["id"],
            "columns": {cid: {"id": cid, "title": next(col["title"] for col in b["columns"] if col_ids[b["columns"].index(col)] == cid), "cardIds": cards_by_col[cid]} for cid in col_ids},
            "cards": cards,
            "columnOrder": col_ids,
            "teamMembers": board["teamMembers"],
            "projectInfo": {"title": b["title"], "description": b["description"]},
        })
        resolved.append({"board": b["title"], "cards": cards})

    # Now spread completed_at (the snapshot set it all to load-time). Order by
    # board then card so the assignment is stable across runs.
    completed_ids = [
        cid for r in resolved for cid, c in r["cards"].items() if c["completed"]
    ]
    for i, cid in enumerate(completed_ids):
        days_ago = 1 + (i % 13)  # 1..13 days before the anchor — inside the 14-day velocity window
        ts = f"{(anchor - timedelta(days=days_ago)).isoformat()}T12:00:00Z"
        conn.execute("UPDATE cards SET completed_at = ? WHERE id = ?", (ts, cid))
    conn.commit()
    return resolved


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchor", default="2026-07-20", help="frozen 'today' the fixture is computed at")
    args = ap.parse_args()
    anchor = date.fromisoformat(args.anchor)

    boards_raw = json.loads(RAW_FIXTURE.read_text())

    tmp = Path(tempfile.mkdtemp()) / "chat_eval_fixture.sqlite"
    from app.config import settings
    settings.sqlite_path = str(tmp)

    from app import migrations, mcp_queries
    migrations.run_migrations()

    # Freeze the query clock to the anchor. Every "overdue"/"tomorrow"/"velocity window" the tools
    # compute is now relative to this fixed day, not the wall clock — the whole point of the pass.
    mcp_queries._utc_today = lambda: anchor  # type: ignore[assignment]

    from app.db import get_connection
    conn = get_connection()
    try:
        resolved = build_db(conn, boards_raw, anchor)
    finally:
        conn.close()

    # ── run the REAL tools and compute the numbers ──────────────────────────────
    q = mcp_queries
    total = sum(len(r["cards"]) for r in resolved)
    completed = sum(1 for r in resolved for c in r["cards"].values() if c["completed"])
    high_open = sum(1 for r in resolved for c in r["cards"].values() if c["priority"] == "high" and not c["completed"])
    overdue = q.overdue_tasks(active_board_id=None, user_name=None, include_completed=False, limit=500)
    tomorrow = q.tomorrows_due_dates(active_board_id=None, user_name=None, include_completed=False, limit=500) if hasattr(q, "tomorrows_due_dates") else []
    workload = q.team_workload(active_board_id=None, limit=500)
    velocity = q.velocity(active_board_id=None, days=14, limit=500)
    blocked = q.blocked_cards(active_board_id=None, limit=500)

    def loc(row):
        return f"{row.get('board_title','?')} → {row.get('column_title','?')} → {row.get('title','?')}"

    print(f"\n{'='*70}\nCHAT EVAL FIXTURE — GROUND TRUTH  (anchor = {anchor}, clock frozen)\n{'='*70}")
    print(f"boards:              {len(resolved)}")
    print(f"cards (total):       {total}")
    print(f"open:                {total - completed}")
    print(f"completed:           {completed}")
    print(f"open high-priority:  {high_open}")
    print(f"overdue open:        {len(overdue)}")
    print(f"due tomorrow:        {len(tomorrow)}")
    print(f"blocked (open):      {len(blocked)}")

    print("\n-- overdue open --")
    for r in sorted(overdue, key=lambda x: x.get("due_date") or ""):
        print(f"   {r.get('due_date')}  {loc(r)}")

    print(f"\n-- due tomorrow ({(anchor+timedelta(days=1)).isoformat()}) --")
    for r in tomorrow:
        print(f"   {loc(r)}")

    print("\n-- workload, top 5 --")
    for r in sorted(workload, key=lambda x: -(x.get("open_story_points") or 0))[:5]:
        print(f"   {r.get('member_name','?')} ({r.get('board_title','?')}): "
              f"{r.get('open_cards','?')} open / {r.get('open_story_points','?')} pts")

    print("\n-- velocity, 14d window, by board --")
    for r in sorted(velocity, key=lambda x: -(x.get("completed_story_points") or 0)):
        print(f"   {r.get('board_title','?')}: {r.get('completed_cards','?')} cards / "
              f"{r.get('completed_story_points','?')} pts")

    # per-board high-priority totals — the board overview counts ALL statuses, not just open work
    print("\n-- high-priority TOTAL per board (the overview counts all statuses) --")
    hp_total = {}
    for r in resolved:
        n = sum(1 for c in r["cards"].values() if c["priority"] == "high")
        hp_total[r["board"]] = n
    for board, n in sorted(hp_total.items(), key=lambda x: -x[1]):
        print(f"   {board}: {n}")

    FROZEN_FIXTURE.write_text(json.dumps(
        {"anchor": anchor.isoformat(), "boards": resolved}, indent=2, ensure_ascii=False
    ))
    print(f"\nfrozen fixture written: {FROZEN_FIXTURE.relative_to(REPO)}")
    print("(absolute dates + spread completed_at; load this for a stable eval, not the drifting demo)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
