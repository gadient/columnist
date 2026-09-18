"""Model-free scorer for the chat assistant's response contract.

The tool-selection eval measures *which tool* the model calls. It says nothing about whether the
answer reads like a management briefing or a database dump — which is the entire complaint the
contract exists to fix. This grades the answer TEXT against the contract, deterministically, so a
prompt change can be measured instead of eyeballed.

It is pure and importable so it can be unit-tested against real transcripts with no model and no
spend (see `tests/test_contract_score.py`), exactly like `note_imports/corpus.py`. The paid model
run lives only in the eval runner; scoring is free and reproducible.

Each dimension returns a `Check(ok, detail)`. A dimension that does not apply to a question (e.g.
location format when no card is named) passes vacuously rather than penalising.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ContractFacts:
    """Ground truth for the fixture the question was asked against."""

    card_titles: set[str] = field(default_factory=set)
    board_titles: set[str] = field(default_factory=set)
    # Cards that constitute "risk this week": overdue OR (high-priority AND due within 7 days) OR
    # blocked. Used to catch the "nothing is at risk" overreach.
    risk_card_titles: set[str] = field(default_factory=set)


# Field-label lines that mark a raw record dump — the "database report" the contract forbids.
_FIELD_LABELS = ("assigned to:", "due:", "priority:", "status:", "description:", "board:", "column:")

# Phrases that assert an all-clear. Flagged only when the fixture actually has risk.
_ALL_CLEAR = re.compile(
    r"(nothing (is |isn'?t |currently )?(at risk|blocked|overdue|due)"
    r"|no (risk|risks|issues|overdue|blockers?|blocked cards?)"
    r"|all clear|good news|you'?re all caught up|everything (is )?on track)",
    re.IGNORECASE,
)

_RISK_QUESTION = re.compile(r"\b(risk|at risk|attention|worry|concern|behind|slipping|focus)\b", re.IGNORECASE)


def _meaningful_lines(answer: str) -> list[str]:
    return [ln.strip() for ln in answer.splitlines() if ln.strip()]


def _looks_like_prose(line: str) -> bool:
    """A bottom-line opener: a sentence, not a bullet, heading, or field label.

    `**Taylor Reyes** has the most…` IS a bottom line — leading bold is emphasis, not a bullet. Only
    a bullet marker (`- `, `* `, `• `), a heading (`#`), a table row (`|`), or a quote (`>`) fails.
    """
    if not line:
        return False
    if re.match(r"^\s*([-•]\s|\*\s|#|>|\|)", line):
        return False
    if any(line.lower().startswith(lbl) for lbl in _FIELD_LABELS):
        return False
    # A bold-only heading like "**High-Priority Tasks:**" is a title, not a bottom line.
    stripped = line.strip("*_ ").rstrip(":")
    return len(stripped.split()) >= 4


def _field_dump_lines(answer: str) -> int:
    return sum(
        1 for ln in _meaningful_lines(answer)
        if any(ln.lower().lstrip("*-• ").startswith(lbl) for lbl in _FIELD_LABELS)
    )


def _mentions_card(answer: str, facts: ContractFacts) -> list[str]:
    return [t for t in facts.card_titles if t and t.lower() in answer.lower()]


def check_bottom_line(answer: str) -> Check:
    """Lead with the answer in a sentence, not a list or a field."""
    lines = _meaningful_lines(answer)
    if not lines:
        return Check("bottom_line", False, "empty answer")
    ok = _looks_like_prose(lines[0])
    return Check("bottom_line", ok, "" if ok else f"opens with: {lines[0][:60]!r}")


def check_no_field_dump(answer: str) -> Check:
    """'Not a database report'. Two or more `Field:` lines is a record dump."""
    n = _field_dump_lines(answer)
    ok = n < 2
    return Check("no_field_dump", ok, "" if ok else f"{n} raw field-label lines")


def check_location_format(answer: str, facts: ContractFacts) -> Check:
    """A card named at workspace scope carries its Board -> Column -> Card location.

    Applies only when a known card is actually named; otherwise vacuously true. The concrete signal
    is an arrow chain, which is how the contract renders location.
    """
    named = _mentions_card(answer, facts)
    if not named:
        return Check("location_format", True, "no card named")
    has_arrow = "→" in answer or "->" in answer
    # A board name appearing near the card is the weaker acceptable form.
    board_named = any(b.lower() in answer.lower() for b in facts.board_titles)
    ok = has_arrow or board_named
    return Check("location_format", ok, "" if ok else f"named {named[:1]} without a board/location")


def check_no_risk_overreach(question: str, answer: str, facts: ContractFacts) -> Check:
    """Do not answer 'nothing is at risk' when the fixture has risk. The single most
    misleading failure — it waves away exactly what the user asked to see."""
    if not _RISK_QUESTION.search(question):
        return Check("no_risk_overreach", True, "not a risk question")
    if not facts.risk_card_titles:
        return Check("no_risk_overreach", True, "fixture genuinely has no risk")
    # An answer that NAMES a risk card is surfacing risk — a "no blockers recorded" aside inside it
    # is the hedge the contract asks for, not an overreach. The overreach is claiming all-clear
    # while pointing at nothing. So the violation is: all-clear phrasing AND no risk card named.
    names_risk = bool(_mentions_card(answer, ContractFacts(card_titles=facts.risk_card_titles)))
    overreaches = bool(_ALL_CLEAR.search(answer)) and not names_risk
    return Check(
        "no_risk_overreach", not overreaches,
        "" if not overreaches else f"claimed all-clear while {len(facts.risk_card_titles)} risk cards exist and named none",
    )


def check_grounding(answer: str, facts: ContractFacts) -> Check:
    """No invented cards: any title presented as a card (after an arrow) must exist in the fixture.

    Conservative — only inspects arrow-tail card names, which is where the contract puts real cards,
    so ordinary prose that happens to quote a phrase is not penalised.
    """
    invented = []
    for line in _meaningful_lines(answer):
        if "→" not in line and "->" not in line:
            continue
        # A location is Board → Column → Card: the CARD is the segment after the LAST arrow. Split
        # the whole chain and take the tail, so a two-arrow chain is not mis-read at the middle.
        tail = re.split(r"→|->", line)[-1]
        # Trim trailing prose that follows the card on the same line ("— High; due …").
        name = re.split(r"\s[—–]\s|\s-\s|\|", tail)[0].strip("*_ :").strip()
        if len(name) < 4:
            continue
        if not any(name.lower() in t.lower() or t.lower() in name.lower() for t in facts.card_titles):
            invented.append(name)
    ok = not invented
    return Check("grounding", ok, "" if ok else f"card(s) not in fixture: {invented[:2]}")


DIMENSIONS = ("bottom_line", "no_field_dump", "location_format", "no_risk_overreach", "grounding")


def score(question: str, answer: str, facts: ContractFacts) -> list[Check]:
    return [
        check_bottom_line(answer),
        check_no_field_dump(answer),
        check_location_format(answer, facts),
        check_no_risk_overreach(question, answer, facts),
        check_grounding(answer, facts),
    ]


def passed(checks: list[Check]) -> bool:
    return all(c.ok for c in checks)
