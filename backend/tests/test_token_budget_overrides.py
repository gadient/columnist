"""Per-user exceptions to the daily token cap.

The global cap (`max_tokens_per_user_per_day`, 100,000 tokens by default — roughly 18 agent
questions at the measured ~5.4k tokens each) is right for invited users and wrong for whoever is
testing the thing. An override raises it for named accounts only.

Deliberately configured by the operator (`TOKEN_BUDGET_OVERRIDES`) rather than stored in the
database: raising your own spending limit should not be an action the running product can perform.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.db import get_connection
from app.usage import daily_budget_for, enforce_daily_budget, record_usage
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def usage_db(tmp_path, monkeypatch):
    from app import migrations

    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "usage.sqlite"))
    migrations.run_migrations()
    monkeypatch.setattr(settings, "max_tokens_per_user_per_day", 50000)
    yield


# ── parsing ───────────────────────────────────────────────────────────────────

def test_an_override_applies_to_the_named_account_only(monkeypatch):
    monkeypatch.setattr(settings, "token_budget_overrides", "vip@example.com=200000")

    assert daily_budget_for({"id": "u1", "email": "vip@example.com"}) == 200000
    assert daily_budget_for({"id": "u2", "email": "someone@example.com"}) == 50000


def test_email_matching_ignores_case_and_padding(monkeypatch):
    """Cognito hands back whatever case the user typed at sign-up; an override that misses because
    of capitalisation would look like the override silently not working."""
    monkeypatch.setattr(settings, "token_budget_overrides", "  VIP@Example.com = 200000 ")

    assert daily_budget_for({"id": "u1", "email": "vip@example.com"}) == 200000


def test_several_overrides_can_be_configured(monkeypatch):
    monkeypatch.setattr(
        settings, "token_budget_overrides", "a@example.com=200000,b@example.com=75000"
    )

    assert daily_budget_for({"id": "1", "email": "a@example.com"}) == 200000
    assert daily_budget_for({"id": "2", "email": "b@example.com"}) == 75000


@pytest.mark.parametrize(
    "bad",
    ["garbage", "a@example.com=notanumber", "a@example.com=0", "a@example.com=-5", "=200000", ""],
)
def test_a_malformed_entry_falls_back_to_the_default_rather_than_breaking(monkeypatch, bad):
    """A typo in an ops variable must not stop the service booting, and must fail towards the
    *lower* cap — an unparseable entry silently granting unlimited spend is the bad direction."""
    monkeypatch.setattr(settings, "token_budget_overrides", bad)

    assert daily_budget_for({"id": "u1", "email": "a@example.com"}) == 50000


def test_no_email_on_the_caller_means_the_default(monkeypatch):
    monkeypatch.setattr(settings, "token_budget_overrides", "vip@example.com=200000")

    assert daily_budget_for({"id": "u1"}) == 50000
    assert daily_budget_for(None) == 50000


# ── enforcement ───────────────────────────────────────────────────────────────

def test_the_override_actually_raises_the_ceiling(monkeypatch):
    """The point of the whole thing: spend past the global cap without being refused."""
    monkeypatch.setattr(settings, "token_budget_overrides", "vip@example.com=200000")
    vip = {"id": "vip", "email": "vip@example.com"}

    record_usage("vip", 60000)  # already past the 50k global cap

    enforce_daily_budget(vip, 1000)  # must not raise


def test_a_user_without_an_override_is_still_refused(monkeypatch):
    """The exception must not become a hole. Everyone else keeps the cost guard."""
    monkeypatch.setattr(settings, "token_budget_overrides", "vip@example.com=200000")
    normal = {"id": "normal", "email": "normal@example.com"}

    record_usage("normal", 60000)

    with pytest.raises(HTTPException) as exc:
        enforce_daily_budget(normal, 1000)
    assert exc.value.status_code == 429


def test_the_override_is_a_ceiling_not_an_exemption(monkeypatch):
    """200k is a bigger budget, not an unlimited one — the guard still fires at the higher number."""
    monkeypatch.setattr(settings, "token_budget_overrides", "vip@example.com=200000")
    vip = {"id": "vip", "email": "vip@example.com"}

    record_usage("vip", 199000)

    with pytest.raises(HTTPException) as exc:
        enforce_daily_budget(vip, 5000)
    assert exc.value.status_code == 429
    assert "200000" in exc.value.detail, "the message should quote the budget actually applied"


def test_the_refusal_message_quotes_the_users_own_budget(monkeypatch):
    """A message citing the global cap to someone on an override would misdescribe why they were
    refused, and send them looking at the wrong setting."""
    monkeypatch.setattr(settings, "token_budget_overrides", "")
    normal = {"id": "n", "email": "n@example.com"}

    record_usage("n", 50000)

    with pytest.raises(HTTPException) as exc:
        enforce_daily_budget(normal, 1)
    assert "50000" in exc.value.detail
