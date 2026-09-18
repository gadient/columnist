"""Validate the response-contract scorer against REAL model output — no model, no spend.

The transcripts below are real model output captured against the demo workspace.
Using them as fixtures proves the scorer flags the failures a human already flagged by eye, before
a single new token is spent tuning the prompt. If the scorer cannot catch these, it cannot grade a
fix to them.
"""
from __future__ import annotations

from app.agents.contract_score import (
    ContractFacts,
    check_bottom_line,
    check_grounding,
    check_location_format,
    check_no_field_dump,
    check_no_risk_overreach,
    score,
    passed,
)

# Facts for the demo at the time these were captured: the two high-priority cards were at risk this
# week — both already past their due dates — so they ARE risk even though nothing was blocked.
DEMO = ContractFacts(
    card_titles={
        "Fix race condition in job queue", "GlobeX — health score drop",
        "Refactor auth middleware", "Unit tests for billing service", "Terms of Service update",
    },
    board_titles={"Engineering", "Customer Success", "PM × Legal"},
    risk_card_titles={"Fix race condition in job queue", "GlobeX — health score drop"},
)


# ── the real failures, which the scorer must catch ────────────────────────────

RISK_ANSWER = (
    "Good news! Nothing is currently at risk this week:\n"
    "**No overdue tasks** – all past due dates have been met or there are no late cards\n"
    "**No blocked cards** – no work is stuck waiting on dependencies"
)

FIELD_DUMP_ANSWER = (
    "Based on the upcoming due dates this week, here are the high priority tasks:\n"
    "Assigned to: Marcus Chen\n"
    "Due: 2026-07-27\n"
    "Priority: High\n"
    "Status: In Review\n"
    "Description: Concurrent dispatch causing duplicate processing"
)


def test_scorer_catches_the_risk_overreach():
    """The most misleading real answer: 'nothing at risk' with high-priority work due this week."""
    c = check_no_risk_overreach("What's at risk this week?", RISK_ANSWER, DEMO)
    assert c.ok is False, "scorer missed the all-clear overreach"


def test_scorer_catches_the_field_dump():
    c = check_no_field_dump(FIELD_DUMP_ANSWER)
    assert c.ok is False, "scorer missed the raw Field: dump"


def test_scorer_catches_missing_location_on_a_named_card():
    """The field-dump answer describes 'Fix race condition' but never gives Board → Column → Card."""
    ans = FIELD_DUMP_ANSWER + "\nFix race condition in job queue"
    c = check_location_format(ans, DEMO)
    assert c.ok is False, "a named card with no board/location should fail location_format"


def test_the_real_risk_answer_fails_overall():
    assert passed(score("What's at risk this week?", RISK_ANSWER, DEMO)) is False


# ── the scorer must not fire on a compliant answer (guards against a scorer that fails everything) ──

GOOD_ANSWER = (
    "Two high-priority items need attention this week.\n"
    "1. **Engineering → In Review → Fix race condition in job queue** — High; due Jul 18; "
    "Marcus Chen. It is overdue and still open.\n"
    "2. **Customer Success → At Risk → GlobeX — health score drop** — High; due Jul 17; "
    "Omar Hassan. DAU fell 40% and the remediation is late.\n"
    "Scope: Demo Workspace. No blockers are recorded, which does not prove the work is unblocked."
)


def test_a_contract_compliant_answer_passes_every_dimension():
    checks = score("What's at risk this week?", GOOD_ANSWER, DEMO)
    failed = [c for c in checks if not c.ok]
    assert not failed, f"good answer wrongly flagged: {[(c.name, c.detail) for c in failed]}"


def test_bottom_line_accepts_a_sentence_and_rejects_a_bare_list():
    assert check_bottom_line("Two items need attention this week. Here they are:").ok is True
    assert check_bottom_line("- Fix race condition\n- GlobeX drop").ok is False


def test_grounding_flags_an_invented_card():
    ans = "Top item: **Engineering → Done → Deploy the flux capacitor** — High."
    assert check_grounding(ans, DEMO).ok is False


def test_grounding_accepts_a_real_card():
    ans = "**Engineering → In Review → Fix race condition in job queue** — High; Marcus Chen."
    assert check_grounding(ans, DEMO).ok is True


def test_no_risk_overreach_is_silent_when_there_is_genuinely_no_risk():
    """A true all-clear must not be penalised — only a false one."""
    empty = ContractFacts(card_titles={"x"}, board_titles={"b"}, risk_card_titles=set())
    assert check_no_risk_overreach("what's at risk?", "Nothing is at risk right now.", empty).ok is True
