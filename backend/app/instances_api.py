"""Instance membership & invite endpoints.

These are meaningful only when Cognito is on (they need an authenticated caller to
resolve the actor's role). With Cognito off there is no caller identity, so they
return 401; the invite/role/cap logic itself lives in `store` and is tested against
the store directly. Seeding a PIU is an operator/ops action
(`store.seed_primary_user`), deliberately with no in-app endpoint.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status

from . import audit_log, cognito_auth, store
from .config import settings
from .db import get_connection
from .schemas import InstanceMemberView, InviteInput
from .tenancy import current_user

instances_router = APIRouter(prefix="/instances", tags=["instances"])


def _require_caller(user: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Invites/membership require an authenticated (Cognito) caller."""
    if not (settings.cognito_enabled and user):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


@instances_router.get(
    "/{instance_id}/members",
    response_model=list[InstanceMemberView],
    summary="List members of an Instance",
)
def list_members_endpoint(
    instance_id: str,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> list[InstanceMemberView]:
    caller = _require_caller(user)
    conn = get_connection()
    try:
        # Only a member of the Instance may see its roster.
        if store.member_role(conn, instance_id, caller["id"]) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return store.list_instance_members(conn, instance_id)
    finally:
        conn.close()


@instances_router.post(
    "/{instance_id}/invites",
    response_model=InstanceMemberView,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a secondary user (PIU only)",
)
def invite_secondary_endpoint(
    instance_id: str,
    payload: InviteInput,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> InstanceMemberView:
    caller = _require_caller(user)
    conn = get_connection()
    try:
        # 1) DB membership first — this enforces PIU-ownership of *this* Instance and the
        #    per-PIU SIU cap, so we never reach Cognito for an unauthorized / over-cap invite.
        member = store.invite_secondary_user(
            conn, instance_id, caller["id"], payload.email, settings.max_secondary_per_primary
        )
        # 2) Cognito account (emails a temp password) — the other half of the invite. Roll the
        #    membership row back if it fails so the invite stays atomic (no dangling allowlist
        #    entry that the UI counts but nobody can sign in against). Idempotent on re-invite.
        try:
            cognito_auth.admin_create_user(member["email"])
        except Exception:
            store.delete_secondary_user(conn, instance_id, caller["id"], member["id"])
            raise
        audit_log.record(
            "member.invite", actor=caller, instance_id=instance_id,
            target=member["email"], detail={"member_id": member["id"], "role": member["role"]},
        )
        return member
    finally:
        conn.close()


@instances_router.delete(
    "/{instance_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a secondary user (PIU only)",
)
def delete_member_endpoint(
    instance_id: str,
    member_id: str,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> Response:
    caller = _require_caller(user)
    conn = get_connection()
    try:
        store.delete_secondary_user(conn, instance_id, caller["id"], member_id)
        audit_log.record("member.delete", actor=caller, instance_id=instance_id, target=member_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        conn.close()
