"""`POST /auth/change-password` — the signed-in password change.

**Why this file exists.** This endpoint is the one place a signed-in user can alter their own
credentials, and it has two properties that are invisible in a happy-path click-through:

1. **The account changed is the *session's* account, never one named in the request body.** The
   handler reads the email off the verified user. If it ever took it from the payload, any signed-in
   user could change any other account's password by naming it — a total authorization bypass that
   looks completely normal in the UI, because your own password change would still work. There is
   deliberately no email field in `ChangePasswordInput`; the test below proves the wiring actually
   uses the session value rather than merely that the field is absent.
2. **It is gated.** `require_user` + `enforce_csrf`. An unauthenticated caller gets 401.

Cognito itself is never called here — `cognito_auth.change_password` is replaced with a spy. What
that function does against the live pool (re-auth for an access token, then `ChangePassword`) is
Cognito's behaviour to prove, not ours, and proving it needs a real pool. What *is* ours, and is
asserted, is which account we ask it to act on.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "cp.sqlite"))
    from app import migrations

    migrations.run_migrations()
    from app.main import app

    return TestClient(app)


@pytest.fixture
def spy(monkeypatch):
    """Stand in for the Cognito call and record what it was asked to do."""
    from app import cognito_auth

    calls: list[tuple[str, str, str]] = []

    def _fake(email: str, current_password: str, new_password: str) -> None:
        calls.append((email, current_password, new_password))

    monkeypatch.setattr(cognito_auth, "change_password", _fake)
    return calls


def _sign_in_as(email: str):
    """Override the auth dependency so the request looks authenticated as `email`."""
    from app.cognito import require_user
    from app.main import app

    app.dependency_overrides[require_user] = lambda: {"id": "user-1", "email": email}
    return lambda: app.dependency_overrides.clear()


def test_an_unauthenticated_caller_is_refused(client, spy):
    resp = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "old-pw", "new_password": "new-pw"},
    )
    assert resp.status_code == 401
    assert spy == [], "Cognito must not be called for an unauthenticated request"


def test_the_session_account_is_the_one_changed(client, spy):
    undo = _sign_in_as("owner@example.com")
    try:
        resp = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "old-pw", "new_password": "new-pw"},
        )
    finally:
        undo()

    assert resp.status_code == 200
    assert spy == [("owner@example.com", "old-pw", "new-pw")]


def test_an_email_in_the_body_cannot_redirect_the_change(client, spy):
    """The payload has no email field — but prove a smuggled one is ignored, not honoured."""
    undo = _sign_in_as("owner@example.com")
    try:
        resp = client.post(
            "/api/v1/auth/change-password",
            json={
                "email": "victim@example.com",
                "current_password": "old-pw",
                "new_password": "new-pw",
            },
        )
    finally:
        undo()

    assert resp.status_code == 200
    assert len(spy) == 1
    changed_email = spy[0][0]
    assert changed_email == "owner@example.com"
    assert changed_email != "victim@example.com", "body must never choose the account"


def test_an_account_with_no_email_is_rejected_before_cognito(client, spy):
    from app.cognito import require_user
    from app.main import app

    app.dependency_overrides[require_user] = lambda: {"id": "user-1"}  # no email claim
    try:
        resp = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "old-pw", "new_password": "new-pw"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 400
    assert spy == []
