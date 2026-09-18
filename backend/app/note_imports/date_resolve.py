"""Deterministic correction of the model's date arithmetic.

The design keeps date resolution with the model — natural-language dates ("the Friday after the
offsite", "before EOQ") are a language task, and a parser dies on the long tail — to be revisited if
the corpus shows date errors concentrating. The labeled corpus on Sonnet 4.5 shows exactly that:
explicit written dates ("by July 20") resolve at 100%, but a single relative weekday phrase — the
same "by Friday" against the same Thursday anchor — comes back as six different dates across runs
(07-17, -18, -19, -23, -24, -25). The error is not a steady +1; the model's *relative-weekday
arithmetic* is simply unreliable, while the phrase itself is reported faithfully in `rawText`.

So this is a **hybrid**, not a parser swap. The model still parses every phrase — code only overrides
the value for the narrow, unambiguous, high-frequency patterns where the math is trivial and the
model is provably shaky: a bare weekday ("Friday", "by Monday"), "tomorrow"/"today", "end of week",
and a curated set of vague phrases that must resolve to null ("asap", "soon", …). Anything code does
not recognize — a compound phrase, "next Tuesday" (genuinely ambiguous), "before EOQ" — is returned
unchanged, so the long tail stays with the model exactly as the design intends.

Two guardrails keep this from re-introducing the "parser dies on the long tail" failure:
  1. **Whole-phrase match only.** The normalized `rawText` must be *exactly* a recognized pattern
     (optional plain preposition + token). "the useful Friday" or "Tuesday or before planning" has
     extra words, so it is not recognized and the model's value stands.
  2. **Monotonic on recognized input.** For a recognized phrase code computes the calendar date
     deterministically; the human still sees `rawText → value`, now checking correct arithmetic
     instead of the model's. It never fabricates a phrase and never touches an unset date.

Pure and deterministic — unit-tested (test_date_resolve.py), no model call, no I/O.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

# Monday=0 … Sunday=6, matching date.weekday(). Full names plus the common informal short forms.
_WEEKDAYS: dict[str, int] = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "weds": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

# Phrases with no resolvable date. The model is *told* these must be null (prompt §Dates) and mostly
# obeys; code makes it deterministic. Every entry is unambiguously a non-date — urgency or an open
# deferral — never a calendar reference. Deliberately excludes "this week" / "next sprint" and other
# genuinely ambiguous phrases: those stay with the model.
_VAGUE_NULL: frozenset[str] = frozenset({
    "asap", "soon", "sometime", "some time", "eventually", "later", "whenever",
    "tbd", "to be determined", "at some point", "some point", "down the line",
    "in the future", "at the next session", "next session", "in due course",
})

# A leading preposition we can strip without changing which date is meant. "by"/"before"/"due"/"on"
# all point at the same day; "next"/"this"/"coming" do NOT (they shift the week and are ambiguous),
# so they are absent here on purpose — a phrase carrying one is left to the model.
_PREP = r"(?:by|before|due(?:\s+by)?|on|no\s+later\s+than)"

_WEEKDAY_RE = re.compile(rf"^(?:{_PREP}\s+)?(?P<day>[a-z]+)$")
_TOMORROW_RE = re.compile(rf"^(?:{_PREP}\s+)?tomorrow$")
_TODAY_RE = re.compile(rf"^(?:{_PREP}\s+)?today$")
_END_OF_WEEK_RE = re.compile(
    rf"^(?:{_PREP}\s+)?(?:the\s+)?end\s+of\s+(?:the\s+)?week$|^eow$"
)


def _normalize(raw_text: str) -> str:
    """Collapse whitespace, casefold, and trim surrounding quotes/terminal punctuation. Enough to
    recognize "By Friday." / "asap?" / '"Monday"'; nothing that would let a compound phrase pass."""
    return re.sub(r"\s+", " ", raw_text).strip().casefold().strip(" \t\"'.!?,;:")


def _next_weekday(anchor: date, target_wd: int) -> date:
    """The soonest date on or after `anchor` that falls on `target_wd`. "by Friday" from a Thursday
    is the next day; a weekday equal to the anchor's resolves to the anchor itself (today)."""
    return anchor + timedelta(days=(target_wd - anchor.weekday()) % 7)


def deterministic_due(raw_text: Optional[str], anchor: date) -> tuple[bool, Optional[str]]:
    """Try to resolve `raw_text` deterministically against `anchor`.

    Returns `(handled, value)`:
      - `(False, None)` — not a recognized pattern; the caller keeps the model's own value.
      - `(True, "YYYY-MM-DD")` — a recognized relative phrase; use this ISO date.
      - `(True, None)` — a recognized vague phrase; the date must be null.
    """
    if not raw_text:
        return (False, None)
    p = _normalize(raw_text)
    if not p:
        return (False, None)

    if p in _VAGUE_NULL:
        return (True, None)

    if _TOMORROW_RE.match(p):
        return (True, (anchor + timedelta(days=1)).isoformat())
    if _TODAY_RE.match(p):
        return (True, anchor.isoformat())
    if _END_OF_WEEK_RE.match(p):
        return (True, _next_weekday(anchor, 4).isoformat())  # Friday of this week

    m = _WEEKDAY_RE.match(p)
    if m and m.group("day") in _WEEKDAYS:
        return (True, _next_weekday(anchor, _WEEKDAYS[m.group("day")]).isoformat())

    return (False, None)
