"""AWS Cognito auth — JWT verification, cookie/Bearer resolution, and the CSRF check.

Built and verified end to end against a real Cognito user pool (not a scaffold).

- **Gated on config**: when Cognito is not configured (`settings.cognito_enabled` is False —
  local dev), nothing here enforces and every route stays open. When it is configured (deploy),
  every `/api/v1` route requires a valid session except `/health` and the `/auth/*` bootstrap
  endpoints (config, me, and the login/reset calls — you cannot require a session to sign in).
- **Real verification**: `verify_jwt` checks RS256 signature, issuer, audience, expiry and
  `token_use` against the pool's JWKS (cached, certifi-pinned). `_claims_to_user` requires a
  `sub`, a usable email, and `email_verified`.
- **Lazy dependencies**: `PyJWT[crypto]` and `boto3` are imported *inside* functions so the
  offline/local path never loads them.
"""
from __future__ import annotations

import hmac
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, status

from .config import settings

# ── JWKS client ───────────────────────────────────────────────────────────────
# PyJWKClient fetches and caches the pool's signing keys itself, so we cache the *client*
# (one per process) rather than rebuild it every request. TLS verification is pinned to
# certifi's CA bundle so JWKS fetch never depends on the host's system cert store — this
# avoids the macOS "unable to get local issuer certificate" gap and behaves identically in
# the Linux container and CI.
_JWKS_CLIENT: Any = None


def _jwks_client() -> Any:
    global _JWKS_CLIENT
    if _JWKS_CLIENT is None:
        import ssl
        import certifi
        from jwt import PyJWKClient

        ctx = ssl.create_default_context(cafile=certifi.where())
        _JWKS_CLIENT = PyJWKClient(settings.cognito_jwks_url, ssl_context=ctx)
    return _JWKS_CLIENT


def verify_jwt(token: str) -> dict[str, Any]:
    """Verify a Cognito-issued JWT and return its claims. Raises 401 on any failure."""
    try:
        import jwt  # PyJWT — lazy import so the no-auth path never loads it
    except ImportError as exc:  # pragma: no cover - optional-dependency guard
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Auth backend unavailable — PyJWT[crypto] is pinned in requirements.txt but not "
                   "importable; reinstall the backend dependencies",
        ) from exc

    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.cognito_app_client_id,
            issuer=settings.cognito_issuer,
        )
    except Exception as exc:  # noqa: BLE001 - surface any verification failure as 401
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    # Defence in depth: we verify the *id* token (carries the user's email); reject an access
    # token presented in its place (it lacks the identity claims we scope on).
    if claims.get("token_use") != "id":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    return claims


def _claims_to_user(claims: dict[str, Any]) -> dict[str, Any]:
    """Map verified JWT claims to the app user, requiring the identity claims the app actually
    relies on. Instance activation is bound to a verified email, so a token
    missing `sub`, without a usable email, or with `email_verified` not true is rejected rather
    than silently mapped to a null id / empty email. Invited users get `email_verified=true` from
    `admin_create_user`, so this rejects only malformed or unexpected tokens."""
    sub = claims.get("sub")
    email = (claims.get("email") or "").strip().lower()
    verified = claims.get("email_verified")
    verified = verified is True or str(verified).lower() == "true"  # Cognito sends bool or "true"
    if not sub or not isinstance(sub, str) or "@" not in email or not verified:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return {
        "id": sub,
        "email": email,
        "name": claims.get("name") or claims.get("cognito:username") or email,
    }


def get_current_user(request: Request) -> Optional[dict[str, Any]]:
    """FastAPI dependency — resolve the caller from the httpOnly session cookie (BFF).

    The browser never holds a raw token; the backend set it as an httpOnly cookie at login.
    For backwards-compat / tooling, an ``Authorization: Bearer`` header is still accepted.
    NON-ENFORCING: when Cognito is disabled we return ``None`` (anonymous) so open routes keep
    working.
    """
    if not settings.cognito_enabled:
        return None
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        authorization = request.headers.get("authorization")
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None
    return _claims_to_user(verify_jwt(token))


# ── CSRF (double-submit cookie) ───────────────────────────────────────────────
# Session auth rides on a cookie the browser sends automatically, so a cross-site page could
# trigger state-changing requests. Defence: the SPA echoes the readable CSRF cookie back in a
# header; a forged cross-site request can read neither, so it can't match. Enforced on unsafe
# methods only, and only when Cognito is on (cookies exist).

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def enforce_csrf(request: Request) -> None:
    """Router dependency — reject unsafe requests whose CSRF header doesn't match the cookie."""
    if not settings.cognito_enabled or request.method in _SAFE_METHODS:
        return
    # Only enforce for cookie-authenticated calls; pure Bearer (no cookie) tooling is exempt.
    if not request.cookies.get(settings.auth_cookie_name):
        return
    cookie = request.cookies.get(settings.csrf_cookie_name)
    header = request.headers.get(settings.csrf_header_name)
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF check failed")


def require_user(user: Optional[dict[str, Any]] = Depends(get_current_user)) -> dict[str, Any]:
    """Enforcing variant — use on routes that must have an authenticated caller."""
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user

# NOTE: the invite-account creation (AdminCreateUser) lives in `cognito_auth.admin_create_user`,
# called directly from the gated invite paths (instances_api / deploy scripts). There is no
# generic `invite_user` wrapper.
