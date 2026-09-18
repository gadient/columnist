"""Unit tests for the deterministic due-date corrector (date_resolve.deterministic_due).

Anchor throughout is Thursday 2026-07-16, the corpus anchor, so these read against the same
calendar as the labeled fixtures: Fri 07-17, Sat -18, Sun -19, Mon -20, Tue -21, Wed -22, Thu -23.
"""
from datetime import date

import pytest

from app.note_imports.date_resolve import deterministic_due

ANCHOR = date(2026, 7, 16)  # Thursday


# --- the bug this fixes: the model's relative-weekday arithmetic ---------------------------------

@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("by Friday", "2026-07-17"),      # the corpus phrase Sonnet spread across six dates
        ("Friday", "2026-07-17"),
        ("by Monday", "2026-07-20"),      # gating transcript: right only 2/3 on the model
        ("Monday", "2026-07-20"),
        ("by Tuesday", "2026-07-21"),
        ("by Wednesday", "2026-07-22"),
        ("by Thursday", "2026-07-16"),    # same weekday as the anchor → today
        ("by Saturday", "2026-07-18"),
        ("by Sunday", "2026-07-19"),
    ],
)
def test_bare_weekday_resolves_to_the_next_occurrence(phrase, expected):
    assert deterministic_due(phrase, ANCHOR) == (True, expected)


def test_weekday_is_case_and_punctuation_insensitive():
    assert deterministic_due("  By FRIDAY.  ", ANCHOR) == (True, "2026-07-17")
    assert deterministic_due('"Monday"', ANCHOR) == (True, "2026-07-20")


@pytest.mark.parametrize("short, expected", [("fri", "2026-07-17"), ("tues", "2026-07-21"),
                                             ("thurs", "2026-07-16"), ("weds", "2026-07-22")])
def test_common_short_forms_resolve(short, expected):
    assert deterministic_due(short, ANCHOR) == (True, expected)


def test_tomorrow_today_and_end_of_week():
    assert deterministic_due("tomorrow", ANCHOR) == (True, "2026-07-17")
    assert deterministic_due("by tomorrow", ANCHOR) == (True, "2026-07-17")
    assert deterministic_due("today", ANCHOR) == (True, "2026-07-16")
    assert deterministic_due("end of the week", ANCHOR) == (True, "2026-07-17")  # Friday of this week
    assert deterministic_due("by end of week", ANCHOR) == (True, "2026-07-17")
    assert deterministic_due("EOW", ANCHOR) == (True, "2026-07-17")


# --- the second half: vague phrases must resolve to null ----------------------------------------

@pytest.mark.parametrize("phrase", ["asap", "ASAP?", "soon", "eventually", "at some point",
                                    "whenever", "tbd", "at the next session"])
def test_vague_phrases_force_null(phrase):
    assert deterministic_due(phrase, ANCHOR) == (True, None)


# --- the guardrail: anything not unambiguous is left to the model (returns handled=False) --------

@pytest.mark.parametrize("phrase", [
    "next Tuesday",                       # week-shift is genuinely ambiguous — not code's to decide
    "this Friday",
    "the useful Friday",                  # extra words → not a whole-phrase match
    "Tuesday or before planning",         # compound / garbled
    "before scope lock",                  # references a milestone, not a calendar
    "before EOQ",                         # the long tail the design leaves with the model
    "July 20th",                          # explicit written date — the model is already 100% here
    "2026-07-20",
    "this week",                          # ambiguous: null vs end-of-week — left to the model
    "from next sprint",
    "",
    None,
])
def test_unrecognized_phrases_are_left_to_the_model(phrase):
    assert deterministic_due(phrase, ANCHOR) == (False, None)


def test_anchor_on_a_different_weekday_still_computes_forward():
    # Tuesday 2026-07-14: "by Friday" is three days out (matches inject1's Tuesday anchor logic).
    assert deterministic_due("by Friday", date(2026, 7, 14)) == (True, "2026-07-17")
