"""Tenant isolation — Instance-based access, gated on Cognito.

When Cognito is disabled (local dev) everything here is a no-op: `current_user` is None and the
enforcement dependencies return immediately, so local behaviour is unchanged. When Cognito is
configured, these ensure a caller only touches boards/workspaces in their own Instance.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, status

from . import audit_log, store
from .cognito import get_current_user
from .config import settings
from .db import get_connection


def current_user(user: Optional[dict[str, Any]] = Depends(get_current_user)) -> Optional[dict[str, Any]]:
    """Resolve the caller, JIT-provision their `users` row, and bind them to their Instance.

    Invite-only: an authenticated caller with no membership or matching invite is
    refused (403) — a valid Cognito login alone does not grant access. On success the caller dict
    carries `instance_id`. All of this is inert when Cognito is off (local dev).
    """
    if user is not None and settings.cognito_enabled:
        conn = get_connection()
        try:
            store.upsert_user(conn, user)
            instance_id, activated = store.provision_instance_membership(conn, user)
            if instance_id is None:
                audit_log.record("auth.denied", actor=user)  # authenticated but not invited
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You have not been invited to this application",
                )
            if activated:
                audit_log.record("auth.activate", actor=user, instance_id=instance_id)
            user = {**user, "instance_id": instance_id}
        finally:
            conn.close()
    return user


def _not_found() -> HTTPException:
    # 404 rather than 403 so we never reveal that another tenant's resource exists.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def enforce_board_access(
    request: Request,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> None:
    """Router dependency for `/boards/{board_id}/*` — 404 unless the caller can access the board."""
    if not settings.cognito_enabled:
        return
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    board_id = request.path_params.get("board_id")
    if not board_id:  # list/create routes have no board_id — scoped inside the handler instead
        return
    conn = get_connection()
    try:
        if not store.user_can_access_board(conn, user["id"], board_id):
            raise _not_found()
    finally:
        conn.close()


def enforce_workspace_access(
    request: Request,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> None:
    """Router dependency for `/workspaces/{workspace_id}/*` — 404 unless the workspace is in the
    caller's Instance."""
    if not settings.cognito_enabled:
        return
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    workspace_id = request.path_params.get("workspace_id")
    if not workspace_id:
        return
    conn = get_connection()
    try:
        if not store.user_can_access_workspace(conn, user["id"], workspace_id):
            raise _not_found()
    finally:
        conn.close()
