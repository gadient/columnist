"""Cognito auth operations for the Backend-for-Frontend flow.

The browser never calls Cognito — these run server-side via boto3 and back the custom
login form. All user-facing calls include the SECRET_HASH the confidential app client
requires. boto3 is lazy-imported and everything is gated on `cognito_enabled`, so the
server still runs with neither boto3 nor Cognito configured.

Username model: the pool uses email as the sign-in attribute, so we pass the (lowercased)
email as USERNAME everywhere and compute SECRET_HASH over that same value — the two must
match.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

from fastapi import HTTPException, status

from .config import settings


def _require_configured() -> None:
    if not settings.cognito_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Cognito not configured")
    if not settings.cognito_app_client_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Cognito client secret not set")


def _client():
    try:
        import boto3  # lazy — only needed once auth is live
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            detail="boto3 is pinned in requirements.txt but not importable — reinstall the backend "
                   "dependencies",
        ) from exc
    return boto3.client("cognito-idp", region_name=settings.cognito_region)


def secret_hash(username: str) -> str:
    """base64( HMAC-SHA256( key=client_secret, msg=username + client_id ) )."""
    msg = (username + settings.cognito_app_client_id).encode("utf-8")
    key = settings.cognito_app_client_secret.encode("utf-8")
    return base64.b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode("utf-8")


def _tokens(auth_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "id_token": auth_result["IdToken"],
        "access_token": auth_result["AccessToken"],
        "refresh_token": auth_result.get("RefreshToken"),
        "expires_in": auth_result.get("ExpiresIn"),
    }


def login(email: str, password: str) -> dict[str, Any]:
    """AdminInitiateAuth (ADMIN_USER_PASSWORD_AUTH). Returns one of:
    {'status':'OK','tokens':{...}} or {'status':'NEW_PASSWORD_REQUIRED','session':..,'email':..}."""
    _require_configured()
    email = email.strip().lower()
    client = _client()
    try:
        resp = client.admin_initiate_auth(
            UserPoolId=settings.cognito_user_pool_id,
            ClientId=settings.cognito_app_client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": email,
                "PASSWORD": password,
                "SECRET_HASH": secret_hash(email),
            },
        )
    except (client.exceptions.NotAuthorizedException, client.exceptions.UserNotFoundException):
        # Same message either way — don't reveal whether the account exists.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    except client.exceptions.PasswordResetRequiredException:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Password reset required — use Forgot password")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Login failed upstream") from exc

    challenge = resp.get("ChallengeName")
    if challenge == "NEW_PASSWORD_REQUIRED":
        return {"status": "NEW_PASSWORD_REQUIRED", "session": resp["Session"], "email": email}
    if "AuthenticationResult" in resp:
        return {"status": "OK", "tokens": _tokens(resp["AuthenticationResult"])}
    raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unsupported challenge: {challenge}")


def set_new_password(email: str, new_password: str, session: str) -> dict[str, Any]:
    """Answer the first-login NEW_PASSWORD_REQUIRED challenge; returns fresh tokens."""
    _require_configured()
    email = email.strip().lower()
    client = _client()
    try:
        resp = client.admin_respond_to_auth_challenge(
            UserPoolId=settings.cognito_user_pool_id,
            ClientId=settings.cognito_app_client_id,
            ChallengeName="NEW_PASSWORD_REQUIRED",
            Session=session,
            ChallengeResponses={
                "USERNAME": email,
                "NEW_PASSWORD": new_password,
                "SECRET_HASH": secret_hash(email),
            },
        )
    except client.exceptions.InvalidPasswordException as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.response["Error"]["Message"])
    except client.exceptions.NotAuthorizedException:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Session expired — sign in again")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Could not set password") from exc
    if "AuthenticationResult" in resp:
        return {"status": "OK", "tokens": _tokens(resp["AuthenticationResult"])}
    raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Password set but no tokens returned")


def forgot_password(email: str) -> None:
    """Start a password reset — Cognito emails a code. Swallows 'user not found' to avoid
    revealing which emails exist."""
    _require_configured()
    email = email.strip().lower()
    client = _client()
    try:
        client.forgot_password(
            ClientId=settings.cognito_app_client_id,
            SecretHash=secret_hash(email),
            Username=email,
        )
    except client.exceptions.UserNotFoundException:
        return  # do not reveal existence
    except client.exceptions.LimitExceededException:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts — try later")
    except Exception:  # noqa: BLE001 — never leak upstream detail here
        return


def confirm_forgot_password(email: str, code: str, new_password: str) -> None:
    _require_configured()
    email = email.strip().lower()
    client = _client()
    try:
        client.confirm_forgot_password(
            ClientId=settings.cognito_app_client_id,
            SecretHash=secret_hash(email),
            Username=email,
            ConfirmationCode=code,
            Password=new_password,
        )
    except (client.exceptions.CodeMismatchException, client.exceptions.ExpiredCodeException):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset code")
    except client.exceptions.InvalidPasswordException as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.response["Error"]["Message"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Could not reset password") from exc


def change_password(email: str, current_password: str, new_password: str) -> None:
    """Change the password of an already signed-in user.

    Cognito's `ChangePassword` needs an **AccessToken**, and this BFF does not have one to hand:
    `_set_session_cookies` stores only the id and refresh tokens, so the access token from login
    is gone by the time the user asks to change their password.

    So we re-authenticate with the password they just typed to mint a fresh access token. That is
    not a workaround for the missing token so much as the security property we want anyway — a
    change-password form must prove the caller knows the current password, otherwise a hijacked
    session is a free account takeover. The alternative, `AdminSetUserPassword`, needs no token
    but performs no such check (and would need a new IAM action on the task role).

    Deliberately does NOT touch the session cookies. Cognito does not invalidate existing tokens
    on a password change, so the caller's session stays valid and no re-login is forced. Other
    sessions also survive — revoking them would mean `revoke()` on the refresh token, which is a
    product decision, not a security requirement here.
    """
    _require_configured()
    email = email.strip().lower()
    client = _client()

    try:
        auth = client.admin_initiate_auth(
            UserPoolId=settings.cognito_user_pool_id,
            ClientId=settings.cognito_app_client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": email,
                "PASSWORD": current_password,
                "SECRET_HASH": secret_hash(email),
            },
        )
    except (client.exceptions.NotAuthorizedException, client.exceptions.UserNotFoundException):
        # The caller is authenticated, so this is not an enumeration risk — say what is wrong.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Could not verify current password") from exc

    result = auth.get("AuthenticationResult")
    if not result:
        # A pending NEW_PASSWORD_REQUIRED challenge — first login was never completed.
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Finish setting your first password before changing it"
        )

    try:
        client.change_password(
            AccessToken=result["AccessToken"],
            PreviousPassword=current_password,
            ProposedPassword=new_password,
        )
    except client.exceptions.InvalidPasswordException as exc:
        # Surface Cognito's own policy text — it always matches what the pool actually enforces,
        # which a message we invent here would drift from.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.response["Error"]["Message"])
    except client.exceptions.LimitExceededException:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts — try later")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Could not change password") from exc


def admin_create_user(email: str) -> None:
    """Invite-only account creation. Cognito emails a temporary password. Idempotent — an
    already-invited email is a no-op (so re-inviting doesn't error)."""
    _require_configured()
    email = email.strip().lower()
    client = _client()
    try:
        client.admin_create_user(
            UserPoolId=settings.cognito_user_pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
    except client.exceptions.UsernameExistsException:
        return  # already invited — idempotent
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Could not create the Cognito account") from exc


def admin_delete_user(email: str) -> None:
    """Delete a Cognito account (deprovisioning). Idempotent — a missing user is a no-op — and a
    **no-op entirely when Cognito is not configured** (local dev), so the DB takedown can run
    standalone. Needs `cognito-idp:AdminDeleteUser` on the task role (see deploy notes); an
    AccessDenied surfaces as a 502 the caller reports rather than crashing the takedown."""
    if not settings.cognito_enabled:
        return
    email = email.strip().lower()
    client = _client()
    try:
        client.admin_delete_user(UserPoolId=settings.cognito_user_pool_id, Username=email)
    except client.exceptions.UserNotFoundException:
        return  # already gone — idempotent
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Could not delete Cognito user {email}") from exc


def revoke(refresh_token: str) -> None:
    """Best-effort logout — revoke the refresh token (and its access tokens) server-side."""
    if not (settings.cognito_enabled and settings.cognito_app_client_secret and refresh_token):
        return
    client = _client()
    try:
        client.revoke_token(
            Token=refresh_token,
            ClientId=settings.cognito_app_client_id,
            ClientSecret=settings.cognito_app_client_secret,
        )
    except Exception:  # noqa: BLE001 — logout must not fail on a revoke hiccup
        return
