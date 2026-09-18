"""Columnist ops admin CLI.

The operator is not an in-app admin — there is deliberately no admin console or
backdoor endpoint. Instead these privileged, DB-side actions live here and are run
directly against the database the environment points at:

  • locally / on a bastion:   `DATABASE_URL=… python -m app.admin …`  (or SQLite via .env)
  • in production:            `aws ecs execute-command … --command "python -m app.admin …"`
                              (the deploy/ wrappers do exactly this)

Subcommands:
  invite-primary <email> [--space NAME]   seed a user's private Instance + its sole PIU
  delete-primary <email> [--yes]          delete a PIU: tear down their Instance AND every member's
                                          Cognito login, in one shot. Re-invite with invite-primary.
  takedown-instance <instance_id> [--yes] cascade-delete an Instance's DB records (Cognito untouched)
  list-instances                          roster of Instances with member/workspace/board counts
  list-members <instance_id>              members of one Instance

`invite-primary` seeds only the DB; its shell wrapper creates the Cognito account. `delete-primary`
is the exception — it *does* delete Cognito accounts (via `cognito_auth.admin_delete_user`, needs
`cognito-idp:AdminDeleteUser` on the task role), so "remove this user" is one command, not two.
Caps come from settings (MAX_INSTANCES / MAX_PRIMARY_USERS / MAX_SECONDARY_PER_PRIMARY).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import cognito_auth, store
from .config import settings
from .db import get_connection
from .migrations import run_migrations


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_invite_primary(args: argparse.Namespace) -> int:
    email = args.email.strip().lower()
    space = args.space or f"{email.split('@')[0].title()}'s Space"
    conn = get_connection()
    try:
        # Idempotency: if this email is already a member anywhere, don't create a second space.
        existing = conn.execute(
            "SELECT instance_id, role, status FROM instance_members WHERE lower(email) = ?", (email,)
        ).fetchone()
        if existing:
            rd = dict(existing)
            print(f"already a member — instance={rd['instance_id']} role={rd['role']} status={rd['status']}")
            print("(no change made; run takedown-instance first if you want a fresh space)")
            return 0
        result = store.create_primary_space(
            conn, email, space, settings.max_instances, settings.max_primary_users
        )
        print(f"seeded primary space for {email}")
        _print({"instance": result["instance"], "member": result["member"]})
        print("\nNext: create the Cognito account so they can log in "
              "(the deploy/invite-primary.sh wrapper does this for you):")
        print(f"  aws cognito-idp admin-create-user --user-pool-id <POOL> --username {email} "
              f"--user-attributes Name=email,Value={email} Name=email_verified,Value=true")
        return 0
    finally:
        conn.close()


def cmd_takedown_instance(args: argparse.Namespace) -> int:
    conn = get_connection()
    try:
        inst = store.get_instance(conn, args.instance_id)
        if inst is None:
            print(f"no such Instance: {args.instance_id}", file=sys.stderr)
            return 1
        members = store.list_instance_members(conn, args.instance_id)
        print(f"About to permanently delete Instance {args.instance_id!r} ({inst.get('name')}):")
        print(f"  members: {len(members)}")
        for m in members:
            print(f"    - {m['email']} ({m['role']}, {m['status']})")
        if not args.yes:
            reply = input("Type the instance id to confirm: ").strip()
            if reply != args.instance_id:
                print("aborted — confirmation did not match.")
                return 1
        removed = store.delete_instance(conn, args.instance_id)
        print("deleted (database records). rows removed:")
        _print(removed)
        print("\nNOT removed (by design):")
        print("  • Append-only JSONL logs (data/logs/audit.jsonl, chat_completions.jsonl) —")
        print("    retained as the audit/security trail.")
        print("  • Cognito accounts — to remove them too, delete each user:")
        for m in members:
            print(f"      aws cognito-idp admin-delete-user --user-pool-id <POOL> --username {m['email']}")
        return 0
    finally:
        conn.close()


def cmd_delete_primary(args: argparse.Namespace) -> int:
    """Delete a PIU by email in one shot: tear down their Instance (all data) AND delete every
    member's Cognito login. The one operation the operator reaches for — "remove this user". Re-invite
    afterwards with `invite-primary`. Only acts on a PIU; an SIU removal goes through the app or a
    plain `takedown-instance`."""
    email = args.email.strip().lower()
    conn = get_connection()
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT instance_id, email, role, status FROM instance_members WHERE lower(email) = ?",
                (email,),
            ).fetchall()
        ]
        piu = next((r for r in rows if r["role"] == store.INSTANCE_ROLE_PIU), None)
        if piu is None:
            if rows:
                print(
                    f"{email} is a member but not a PIU (role={rows[0]['role']}). delete-primary only "
                    "tears down a PIU's Instance — remove an SIU via the app's Manage members.",
                    file=sys.stderr,
                )
            else:
                print(f"no PIU found for {email}", file=sys.stderr)
            return 1

        instance_id = piu["instance_id"]
        inst = store.get_instance(conn, instance_id)
        members = store.list_instance_members(conn, instance_id)
        print(f"About to DELETE PIU {email} — tears down Instance {instance_id!r} ({(inst or {}).get('name')}):")
        print(f"  {len(members)} member(s) lose access; each Cognito login is deleted too:")
        for m in members:
            print(f"    - {m['email']} ({m['role']}, {m['status']})")
        if not args.yes:
            reply = input("Type the email to confirm: ").strip().lower()
            if reply != email:
                print("aborted — confirmation did not match.")
                return 1

        removed = store.delete_instance(conn, instance_id)
        print("Instance database records deleted. rows removed:")
        _print(removed)

        # Delete each member's Cognito login. Best-effort and reported per-email — the DB takedown
        # already succeeded, so a Cognito failure (e.g. missing AdminDeleteUser IAM perm) must not
        # abort or leave the operator thinking nothing happened. No-op when Cognito is off (local).
        cognito: dict[str, str] = {}
        for m in members:
            try:
                cognito_auth.admin_delete_user(m["email"])
                cognito[m["email"]] = "deleted"
            except Exception as exc:  # noqa: BLE001 - report, never abort
                cognito[m["email"]] = f"FAILED: {getattr(exc, 'detail', exc)}"
        print("Cognito accounts:")
        _print(cognito)
        print(f"\nDone. Re-invite with:  invite-primary {email}")
        return 0
    finally:
        conn.close()


def cmd_list_instances(_args: argparse.Namespace) -> int:
    conn = get_connection()
    try:
        _print(store.list_instances(conn))
        return 0
    finally:
        conn.close()


def cmd_list_members(args: argparse.Namespace) -> int:
    conn = get_connection()
    try:
        if store.get_instance(conn, args.instance_id) is None:
            print(f"no such Instance: {args.instance_id}", file=sys.stderr)
            return 1
        _print(store.list_instance_members(conn, args.instance_id))
        return 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.admin", description="Columnist ops admin CLI")
    p.add_argument("--skip-migrations", action="store_true",
                   help="don't run DB migrations first (they run by default, idempotently)")
    sub = p.add_subparsers(dest="cmd", required=True)

    ip = sub.add_parser("invite-primary", help="seed a user's private Instance + PIU")
    ip.add_argument("email")
    ip.add_argument("--space", help="Instance display name (default: derived from the email)")
    ip.set_defaults(func=cmd_invite_primary)

    td = sub.add_parser("takedown-instance", help="cascade-delete an Instance and all its data")
    td.add_argument("instance_id")
    td.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    td.set_defaults(func=cmd_takedown_instance)

    dp = sub.add_parser("delete-primary", help="delete a PIU by email: tear down their Instance + Cognito logins")
    dp.add_argument("email")
    dp.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    dp.set_defaults(func=cmd_delete_primary)

    li = sub.add_parser("list-instances", help="roster of all Instances")
    li.set_defaults(func=cmd_list_instances)

    lm = sub.add_parser("list-members", help="members of one Instance")
    lm.add_argument("instance_id")
    lm.set_defaults(func=cmd_list_members)
    return p


def main(argv: list[str] | None = None) -> int:
    from fastapi import HTTPException

    args = build_parser().parse_args(argv)
    if not args.skip_migrations:
        # Idempotent — makes the CLI safe to run against a fresh bastion/db too.
        run_migrations()
    try:
        return args.func(args)
    except HTTPException as exc:
        # The store helpers raise HTTPException (shared with the API); render it as a clean
        # CLI error rather than a traceback (e.g. cap hit, not-found, default-Instance guard).
        print(f"error ({exc.status_code}): {exc.detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
