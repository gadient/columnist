"""Resolution — raw model output → a review-ready proposal.

The model reports *language*: a name as written, a date phrase, an excerpt it claims is a quote.
This module turns each of those claims into something a human can act on, using deterministic rules
only — never a second model call:

  - a `rawName` is matched against the board roster → existing / ambiguous / unmatched
  - each excerpt is checked against the note it claims to come from → locatable or blocked
  - the destination column chosen for the import — the user's pick on the import form, defaulting
    to the board's leftmost — is stamped on every card; the reviewer re-targets each one per card
    during review — the model never proposes a column
  - a `blockedReasons` list is computed so Approve & create can be disabled with a stated reason

Everything here runs without a model, which is the point: identity and policy are code, so owner
resolution is unit-tested rather than evaluated. It is also where "clean vs flagged" is decided for
the bulk path: a recommendation with an empty `blockedReasons` is one no server-side check faulted —
never one the model was merely confident about.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date

from .date_resolve import deterministic_due
from .schema import (
    ModelDueDate,
    ModelRecommendation,
    ModelRecommendationSet,
    RecommendationView,
    ResolvedAssignee,
)

_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]")

_log = logging.getLogger(__name__)


def _collapse_whitespace(text: str) -> str:
    """One space between tokens, trimmed. Nothing else.

    This is the *whole* of the evidence-locatability tolerance. A hard-wrapped note stores
    "collect quotes\nfrom three"; the model quotes it correctly as one sentence with a space. The
    newline and the space are the same separator, so collapsing both sides lets a verbatim-correct
    quote match. **No case-folding, no punctuation stripping, no fuzzy match** — the model is told
    to quote verbatim (prompt §Evidence), and every extra tolerance here buys a fabricated quote a
    way past the check that is supposed to catch it.
    """
    return _WHITESPACE.sub(" ", text).strip()


def _name_key_exact(name: str) -> str:
    """Tier-2 key: strip a leading @, trim, casefold. Spacing/punctuation preserved."""
    return name.lstrip("@").strip().casefold()


def _name_key_normalized(name: str) -> str:
    """Tier-3 key: also collapse whitespace and strip punctuation.

    This is the *only* normalization allowed ("differ only by spacing or punctuation"). It is
    deliberately not token- or first-name-aware: "Alex" against a roster of "Alex Kim"/"Alex Rivera"
    matches neither key, so it resolves to `unmatched`, not `ambiguous`. That is deliberate —
    a first-name reference is a Create-member prerequisite the human resolves, not a guess code makes.
    """
    stripped = _PUNCTUATION.sub("", name.lstrip("@"))
    return _collapse_whitespace(stripped).casefold()


def _suggest_members(raw_name: str, members: list[dict]) -> list[dict]:
    """Near-misses to *offer* for an unmatched name. A suggestion, never a match.

    The rule is deliberately narrow and explainable: the raw name's tokens must be a subset of a
    member's tokens. "morgan" ⊂ {morgan, blake} suggests Morgan Blake; "morgan" against
    {alex, kim} suggests nothing. No fuzzy distance, no initials, no nicknames — every extra
    cleverness here is a wrong suggestion the reviewer has to notice and reject.

    This does NOT weaken matching. Matching above stays strict and still returns `unmatched`; a
    suggestion only changes what the review surface can *show*, and a human still confirms it.
    Two Morgans on a board yield two suggestions and no auto-assignment — which is the whole
    reason first-name matching was refused in the first place.
    """
    tokens = set(_name_key_normalized(raw_name).split())
    if not tokens:
        return []
    return [m for m in members if tokens < set(_name_key_normalized(m["name"]).split())]


def _resolve_assignee(raw_name: str, confidence: float, members: list[dict]) -> ResolvedAssignee:
    """The owner matching algorithm. Exact tier first; normalized tier only if exact misses."""
    # Tier 2: unique case-insensitive exact match → auto-resolved.
    exact = [m for m in members if m["name"].strip().casefold() == _name_key_exact(raw_name)]
    if len(exact) == 1:
        return ResolvedAssignee(
            rawName=raw_name,
            resolution="existing_member",
            memberId=exact[0]["id"],
            confidence=confidence,
        )
    if len(exact) > 1:
        return ResolvedAssignee(
            rawName=raw_name,
            resolution="ambiguous",
            alternativeMemberIds=[m["id"] for m in exact],
            confidence=confidence,
        )

    # Tier 3: unique normalized match → resolved, but must show *why*.
    key = _name_key_normalized(raw_name)
    normalized = [m for m in members if _name_key_normalized(m["name"]) == key]
    if len(normalized) == 1:
        match = normalized[0]
        return ResolvedAssignee(
            rawName=raw_name,
            resolution="existing_member",
            memberId=match["id"],
            matchRationale=f'Matched "{raw_name}" to "{match["name"]}" ignoring spacing and punctuation.',
            confidence=confidence,
        )
    if len(normalized) > 1:
        return ResolvedAssignee(
            rawName=raw_name,
            resolution="ambiguous",
            alternativeMemberIds=[m["id"] for m in normalized],
            confidence=confidence,
        )

    # No match at either tier → a Create-member prerequisite the human decides on.
    #
    # This is the outcome with nothing to say about *why* the name missed — the reviewer gets a
    # stated reason and any near-miss candidates, but no match rationale — so it can look like a bug
    # (a person who appears to be on the board resolves as unmatched). Two very different
    # causes land here:
    #   1. working-as-designed — "Morgan" vs a roster entry "Morgan Lastname" (no first-name match),
    #   2. a real defect — wrong board scoping, or a case/whitespace/encoding mismatch.
    # The log carries counts only — never the name or the roster, which are note and board content.
    # An empty roster here points at the query in api.py `_prepare_analysis`, not at this
    # algorithm; a suggestion present means a partial-name match, i.e. case 1.
    suggestions = _suggest_members(raw_name, members)
    _log.debug(
        "note_imports.assignee unmatched: roster_size=%d has_suggestion=%s",
        len(members),
        bool(suggestions),
    )
    return ResolvedAssignee(
        rawName=raw_name,
        resolution="unmatched",
        candidateMemberIds=[m["id"] for m in suggestions],
        confidence=confidence,
    )


def _resolve_due(due: ModelDueDate | None, meeting_date: str) -> ModelDueDate | None:
    """Correct the model's date arithmetic for the narrow, unambiguous phrases code can resolve
    exactly (date_resolve.deterministic_due) — everything else the model resolved is kept as-is.

    `rawText` is never touched, so the review surface still shows the source phrase next to the
    date; only the arithmetic is code's. `provenance` follows the outcome: a resolved relative
    phrase is `inferred`, a vague phrase forced to null is `default`. When code agrees with the model
    (or does not recognize the phrase) the object is returned unchanged."""
    if due is None:
        return None
    handled, value = deterministic_due(due.rawText, date.fromisoformat(meeting_date))
    if not handled or value == due.value:
        return due
    return due.model_copy(update={"value": value, "provenance": "inferred" if value else "default"})


def resolve_one(
    rec: ModelRecommendation,
    *,
    members: list[dict],
    target_column_id: str,
    normalized_note: str,
    meeting_date: str,
) -> RecommendationView:
    """Resolve a single recommendation. Public so the streaming path can resolve each card
    as it arrives, rather than waiting for the whole set."""
    assignees = [_resolve_assignee(a.rawName, a.confidence, members) for a in rec.assignees]

    blocked: list[str] = []

    # An owner that resolved to more than one member, or to none, keeps its uncertainty visible and
    # stops approval until the human resolves it. Confidence never enters this — a card
    # the model was sure about but whose owner is not on the board is exactly one to surface.
    for a in assignees:
        if a.resolution == "ambiguous":
            blocked.append(f'Owner "{a.rawName}" matches more than one board member — pick one.')
        elif a.resolution == "unmatched":
            blocked.append(f'Owner "{a.rawName}" is not a board member — add them or leave it unassigned.')

    # requiresNewMemberNamed surfaces the Create-member offer. One field, so the first
    # unmatched owner names it; each unmatched owner already has its own blocked reason above.
    requires_new_member = next((a.rawName for a in assignees if a.resolution == "unmatched"), None)

    # Every excerpt must be findable in the note it claims to come from. A quote the model
    # invented is the clearest hallucination signal there is, so a card carrying one is blocked, not
    # silently trusted. Checked with whitespace collapsed on both sides and nothing more.
    note_haystack = _collapse_whitespace(normalized_note)
    unlocatable = [e for e in rec.evidence if _collapse_whitespace(e.excerpt) not in note_haystack]
    if unlocatable:
        blocked.append("Evidence for this card was not found in the note.")

    return RecommendationView(
        id=f"rec_{uuid.uuid4().hex}",
        action="create_card",
        title=rec.title,
        description=rec.description,
        # The import's column, stamped here (the reviewer re-targets per card). Never the
        # model's, never unresolved — a card's column is never what blocks it, whatever else is.
        targetColumnId=target_column_id,
        assignees=assignees,
        dueDate=_resolve_due(rec.dueDate, meeting_date),
        priority=rec.priority,
        evidence=rec.evidence,
        reason=rec.reason,
        confidence=rec.confidence,
        ambiguities=rec.ambiguities,
        blockedReasons=blocked,
        requiresNewMemberNamed=requires_new_member,
    )


def resolve_recommendations(
    model_set: ModelRecommendationSet,
    *,
    members: list[dict],
    target_column_id: str,
    normalized_note: str,
    meeting_date: str,
) -> list[RecommendationView]:
    """Every model recommendation, resolved for review.

    `members` is the board roster (`{"id", "name", ...}`); `target_column_id` is the destination the
    user picked on the import form — the board's leftmost column when they picked none —
    already validated against the board by the caller;
    `normalized_note` is the extracted text the model saw, against which evidence is checked;
    `meeting_date` is the ISO anchor relative dates resolve against.

    Order is preserved. An empty set in yields an empty list out — a valid outcome.
    """
    return [
        resolve_one(
            rec,
            members=members,
            target_column_id=target_column_id,
            normalized_note=normalized_note,
            meeting_date=meeting_date,
        )
        for rec in model_set.recommendations
    ]
