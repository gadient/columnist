"""Auth router — custom login via the Backend-for-Frontend pattern.

The SPA POSTs credentials here; the backend talks to Cognito (`cognito_auth`) and sets the
tokens as **httpOnly** cookies — the browser never holds a raw token. A readable CSRF cookie
is set alongside for the double-submit defence (`cognito.enforce_csrf`).

When Cognito is unconfigured every endpoint degrades safely: `/auth/config` reports
`enabled:false`, `/auth/me` reports anonymous, and the login endpoints return 503.
"""
from __future__ import annotations

import secrets
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from . import cognito_auth, store
from .cognito import enforce_csrf, require_user
from .config import settings
from .db import get_connection
from .tenancy import current_user

router = APIRouter(prefix="/auth", tags=["auth"])


# ── request models ──
# Bounds on the auth inputs, matching the product schemas: reject oversized values before they
# reach Cognito or any expensive processing.
_EMAIL_MAX = 254        # RFC 5321 practical maximum
_PW_MAX = 256           # Cognito's password maximum
_SESSION_MAX = 4096     # Cognito challenge-session tokens are long but bounded
_CODE_MAX = 32          # emailed confirmation code


class LoginInput(BaseModel):
    email: str = Field(min_length=1, max_length=_EMAIL_MAX)
    password: str = Field(min_length=1, max_length=_PW_MAX)


class NewPasswordInput(BaseModel):
    email: str = Field(min_length=1, max_length=_EMAIL_MAX)
    new_password: str = Field(min_length=1, max_length=_PW_MAX)
    session: str = Field(min_length=1, max_length=_SESSION_MAX)


class ForgotInput(BaseModel):
    email: str = Field(min_length=1, max_length=_EMAIL_MAX)


class ConfirmForgotInput(BaseModel):
    email: str = Field(min_length=1, max_length=_EMAIL_MAX)
    code: str = Field(min_length=1, max_length=_CODE_MAX)
    new_password: str = Field(min_length=1, max_length=_PW_MAX)


class ChangePasswordInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=_PW_MAX)
    new_password: str = Field(min_length=1, max_length=_PW_MAX)


# ── cookie helpers ──
def _set_session_cookies(response: Response, tokens: dict[str, Any]) -> None:
    """Set the httpOnly session + refresh cookies and a readable CSRF cookie."""
    common = {
        "secure": settings.auth_cookie_secure,
        "samesite": settings.auth_cookie_samesite,
        "path": "/",
    }
    response.set_cookie(
        settings.auth_cookie_name, tokens["id_token"],
        httponly=True, max_age=settings.auth_cookie_ttl_seconds, **common,
    )
    if tokens.get("refresh_token"):
        response.set_cookie(
            settings.auth_refresh_cookie_name, tokens["refresh_token"],
            httponly=True, max_age=30 * 24 * 3600, **common,
        )
    # CSRF token is intentionally NOT httpOnly — the SPA reads it and echoes it in a header.
    response.set_cookie(
        settings.csrf_cookie_name, secrets.token_urlsafe(32),
        httponly=False, max_age=settings.auth_cookie_ttl_seconds, **common,
    )


def _clear_session_cookies(response: Response) -> None:
    for name in (settings.auth_cookie_name, settings.auth_refresh_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(name, path="/")


# ── endpoints ──
@router.get("/config", summary="Public auth config for the SPA")
def auth_config() -> dict[str, Any]:
    """Client-safe values. In the BFF flow the SPA only needs to know auth is on and the CSRF
    header name — the pool/client IDs stay server-side."""
    if not settings.cognito_enabled:
        return {"enabled": False}
    return {"enabled": True, "csrfHeader": settings.csrf_header_name, "csrfCookie": settings.csrf_cookie_name}


@router.get("/me", summary="Current authenticated user (or anonymous)")
def auth_me(user: Optional[dict[str, Any]] = Depends(current_user)) -> dict[str, Any]:
    # `current_user` resolves identity, JIT-provisions membership, and refuses an
    # authenticated-but-uninvited caller with 403. It also stamps `instance_id`.
    if user is None:
        return {"authenticated": False, "user": None}
    out: dict[str, Any] = {"id": user.get("id"), "email": user.get("email"), "name": user.get("name")}
    instance_id = user.get("instance_id")
    if instance_id:
        conn = get_connection()
        try:
            out["instanceId"] = instance_id
            out["role"] = store.member_role(conn, instance_id, user["id"])
        finally:
            conn.close()
    return {"authenticated": True, "user": out}


@router.post("/login", summary="Sign in with email + password")
def auth_login(payload: LoginInput, response: Response) -> dict[str, Any]:
    result = cognito_auth.login(payload.email, payload.password)
    if result["status"] == "NEW_PASSWORD_REQUIRED":
        # First login: the SPA must collect a new password and call /auth/set-password.
        return {"status": "NEW_PASSWORD_REQUIRED", "session": result["session"], "email": result["email"]}
    _set_session_cookies(response, result["tokens"])
    return {"status": "OK"}


@router.post("/set-password", summary="Set a new password on first login")
def auth_set_password(payload: NewPasswordInput, response: Response) -> dict[str, Any]:
    result = cognito_auth.set_new_password(payload.email, payload.new_password, payload.session)
    _set_session_cookies(response, result["tokens"])
    return {"status": "OK"}


@router.post("/forgot-password", summary="Start a password reset (emails a code)")
def auth_forgot(payload: ForgotInput) -> dict[str, Any]:
    cognito_auth.forgot_password(payload.email)
    # Always the same response — never reveal whether the email exists.
    return {"status": "OK"}


@router.post("/confirm-forgot-password", summary="Finish a password reset with the emailed code")
def auth_confirm_forgot(payload: ConfirmForgotInput) -> dict[str, Any]:
    cognito_auth.confirm_forgot_password(payload.email, payload.code, payload.new_password)
    return {"status": "OK"}


@router.post(
    "/change-password",
    summary="Change your password while signed in",
    dependencies=[Depends(enforce_csrf)],
)
def auth_change_password(
    payload: ChangePasswordInput,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """Signed-in password change (distinct from `/set-password`, which answers the first-login
    challenge, and from the forgot-password pair, which is for users who *cannot* sign in).

    The email comes from the verified session, never from the request body — otherwise any
    signed-in user could change any other account's password by naming it.
    """
    email = user.get("email")
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No email on this account")
    cognito_auth.change_password(email, payload.current_password, payload.new_password)
    return {"status": "OK"}


@router.post(
    "/logout",
    summary="Sign out — clear cookies and revoke the refresh token",
    dependencies=[Depends(enforce_csrf)],
)
def auth_logout(request: Request, response: Response) -> dict[str, Any]:
    refresh = request.cookies.get(settings.auth_refresh_cookie_name)
    if refresh:
        cognito_auth.revoke(refresh)  # best-effort; never raises
    _clear_session_cookies(response)
    return {"status": "OK"}

# NOTE: there is deliberately no /auth/invite. Creating an invite account is only reachable via
# the properly-gated paths: POST /instances/{id}/invites (SIUs — PIU-scoped, capped, CSRF'd) and
# deploy/invite-primary.sh (PIUs — ops).
