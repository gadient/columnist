"""Resolution.

This is where the model's *claims* become a proposal a human can act on, using deterministic rules
only. So it is unit-tested, not eval'd: given the same raw output and the same roster, the same
resolution comes out every time. The properties asserted here are the security ones —

  - an owner code can't confidently place is *blocked*, never silently assigned;
  - a quote the model invented is *blocked*, never trusted;
  - the destination column is always the user's, never the model's, never empty;
  - a clean card — the only kind the bulk path may auto-add — is one no check faulted.

Verify by mutation: relax the matcher to token-match first names and
`test_first_name_is_unmatched_not_guessed` fails; drop the whitespace collapse and
`test_hard_wrapped_evidence_is_locatable` fails; trust an unlocatable quote and
`test_fabricated_evidence_is_blocked` fails.
"""
from __future__ import annotations

from app.note_imports.resolve import resolve_recommendations
from app.note_imports.schema import (
    ModelEvidence,
    ModelPriority,
    ModelRecommendation,
    ModelRecommendationSet,
)

COLUMN = "col_todo"
MEETING_DATE = "2026-07-16"  # a Thursday — the anchor relative dates resolve against
NOTE = "Alice will finalize the vendor shortlist.\nBob to send the invoice by Friday."

ROSTER = [
    {"id": "mem_alice", "name": "Alice Chen"},
    {"id": "mem_bob", "name": "Bob Ng"},
]


def rec(
    *,
    title: str = "Finalize the vendor shortlist",
    owners: list[str] | None = None,
    excerpt: str = "Alice will finalize the vendor shortlist.",
    owner_confidence: float = 0.9,
    confidence: float = 0.9,
) -> ModelRecommendation:
    """One model recommendation. Only the fields a test varies are parameters; the rest are valid
    defaults so a test reads as the one thing it is about."""
    return ModelRecommendation(
        action="create_card",
        title=title,
        assignees=[{"rawName": name, "confidence": owner_confidence} for name in (owners or [])],
        dueDate=None,
        priority=ModelPriority(value="medium", rawText=None, provenance="default", confidence=1.0),
        evidence=[ModelEvidence(excerpt=excerpt, locator="line:1")],
        reason="Explicit assignment in the note.",
        confidence=confidence,
    )


def resolve(*recs: ModelRecommendation, note: str = NOTE):
    return resolve_recommendations(
        ModelRecommendationSet(recommendations=list(recs)),
        members=ROSTER,
        target_column_id=COLUMN,
        normalized_note=note,
        meeting_date=MEETING_DATE,
    )


# --- the column is the user's, always -----------------------------------------------------------

def test_the_user_column_is_stamped_on_every_card():
    views = resolve(rec(), rec(title="A second card", excerpt="Bob to send the invoice by Friday."))
    assert [v.targetColumnId for v in views] == [COLUMN, COLUMN]


def test_a_card_is_never_blocked_on_column():
    """The old failure this replaces: the model returned a null column on 11 of 11 cards and every
    one was blocked. Column can no longer be a blocked reason, because the model never chooses it."""
    (view,) = resolve(rec())
    assert not any("column" in r.lower() for r in view.blockedReasons)


# --- owner resolution ---------------------------------------------------------------------------

def test_exact_name_resolves_automatically():
    (view,) = resolve(rec(owners=["Alice Chen"]))
    (a,) = view.assignees
    assert a.resolution == "existing_member" and a.memberId == "mem_alice"
    assert a.matchRationale is None  # an exact match needs no explanation
    assert view.blockedReasons == []


def test_at_tag_and_case_are_ignored_for_exact_match():
    (view,) = resolve(rec(owners=["@alice chen"]))
    (a,) = view.assignees
    assert a.resolution == "existing_member" and a.memberId == "mem_alice"


def test_normalized_match_resolves_but_shows_its_rationale():
    """A match that only succeeded after normalizing spacing/punctuation must say so, so the
    user can see why "A.Smith" is Alice — the resolver never hides a fuzzy-looking decision."""
    roster = [{"id": "mem_asmith", "name": "A. Smith"}]
    views = resolve_recommendations(
        ModelRecommendationSet(recommendations=[rec(owners=["A Smith"])]),
        members=roster,
        target_column_id=COLUMN,
        normalized_note=NOTE,
        meeting_date=MEETING_DATE,
    )
    (a,) = views[0].assignees
    assert a.resolution == "existing_member" and a.memberId == "mem_asmith"
    assert a.matchRationale is not None


def test_two_matching_members_block_approval():
    """>1 hit is ambiguous, offers both, and blocks — code never picks one."""
    roster = [{"id": "mem_a1", "name": "Alex Kim"}, {"id": "mem_a2", "name": "Alex Kim"}]
    views = resolve_recommendations(
        ModelRecommendationSet(recommendations=[rec(owners=["Alex Kim"])]),
        members=roster,
        target_column_id=COLUMN,
        normalized_note=NOTE,
        meeting_date=MEETING_DATE,
    )
    (a,) = views[0].assignees
    assert a.resolution == "ambiguous"
    assert set(a.alternativeMemberIds) == {"mem_a1", "mem_a2"}
    assert views[0].blockedReasons  # cannot be approved until resolved


def test_first_name_is_unmatched_not_guessed():
    """Matching is strict-equality only. "Alex" against full-name members matches no key, so it is a
    Create-member prerequisite — NOT a token-match to the nearest Alex. Relaxing the matcher to make
    this "helpful" is exactly the fuzzy match the spec forbids."""
    roster = [{"id": "mem_a1", "name": "Alex Kim"}, {"id": "mem_a2", "name": "Alex Rivera"}]
    views = resolve_recommendations(
        ModelRecommendationSet(recommendations=[rec(owners=["Alex"])]),
        members=roster,
        target_column_id=COLUMN,
        normalized_note=NOTE,
        meeting_date=MEETING_DATE,
    )
    (a,) = views[0].assignees
    assert a.resolution == "unmatched"


def test_unmatched_owner_blocks_and_offers_a_new_member():
    (view,) = resolve(rec(owners=["Priya"]))
    (a,) = view.assignees
    assert a.resolution == "unmatched"
    assert view.requiresNewMemberNamed == "Priya"
    assert view.blockedReasons


def test_a_confident_owner_off_the_board_is_still_blocked():
    """The measured reason flagging keys on server faults, not the model's confidence: the model
    returned 1.00 on cards assigned to people absent from the board. A 1.0 owner who isn't a member
    must still block, or the bulk path would auto-create precisely the cards a human needs to see."""
    (view,) = resolve(rec(owners=["Priya"], owner_confidence=1.0, confidence=1.0))
    assert view.blockedReasons


def test_no_owner_is_a_clean_unassigned_card():
    """An unassigned card is fine — the model does not invent an owner, and code does not
    block for the absence of one."""
    (view,) = resolve(rec(owners=[]))
    assert view.assignees == []
    assert view.blockedReasons == []
    assert view.requiresNewMemberNamed is None


# --- evidence must be real ----------------------------------------------------------------------

def test_fabricated_evidence_is_blocked():
    """A quote that is not in the note is the clearest hallucination signal there is. The card is
    blocked, not trusted — this is the check that stops an invented citation reaching a human as
    though the note supported it."""
    (view,) = resolve(rec(excerpt="Carol promised to rewrite the entire billing system."))
    assert any("evidence" in r.lower() for r in view.blockedReasons)


def test_hard_wrapped_evidence_is_locatable():
    """A hard-wrapped note stores a newline where the model's quote has a space. Collapsing
    whitespace on both sides lets a verbatim-correct quote match. Without the fix this card blocks
    despite a correct quote."""
    wrapped = "Bob to send\nthe invoice by Friday."
    (view,) = resolve(rec(excerpt="Bob to send the invoice by Friday.", owners=[]), note=wrapped)
    assert not any("evidence" in r.lower() for r in view.blockedReasons)


def test_evidence_match_is_case_sensitive():
    """The collapse buys whitespace tolerance only. A quote whose case differs from the note
    is not verbatim, so it does not match — case-folding here would let a reworded quote through."""
    (view,) = resolve(rec(excerpt="ALICE WILL FINALIZE THE VENDOR SHORTLIST.", owners=[]))
    assert any("evidence" in r.lower() for r in view.blockedReasons)


# --- shape --------------------------------------------------------------------------------------

def test_every_view_gets_a_server_id_and_a_fixed_action():
    (view,) = resolve(rec())
    assert view.id.startswith("rec_")
    assert view.action == "create_card"
    assert view.state == "pending"


def test_order_is_preserved_and_empty_in_is_empty_out():
    assert resolve() == []
    views = resolve(rec(title="first"), rec(title="second"), rec(title="third"))
    assert [v.title for v in views] == ["first", "second", "third"]


def test_a_clean_card_is_the_only_kind_with_no_blocked_reasons():
    """Flagging is defined right here: a bulk-addable card is one every server check passed —
    real owner, real quote. Not one the model felt good about."""
    (clean,) = resolve(rec(owners=["Bob Ng"], excerpt="Bob to send the invoice by Friday."))
    assert clean.blockedReasons == []


# --- suggestions for an unmatched name ----------------------------------------------------------
# The complaint that produced these: a note said "morgan", the board had "Morgan Blake", and the
# import reported "not on this board" — which reads as a defect even though strict matching was
# working. The fix is to SUGGEST, never to match. These tests pin that distinction, because the
# tempting "improvement" is to quietly resolve the suggestion and reintroduce the forbidden guess.

def test_a_first_name_suggests_the_full_name_member_without_matching_it():
    roster = [{"id": "mem_m1", "name": "Morgan Blake"}, {"id": "mem_k1", "name": "Alex Kim"}]
    views = resolve_recommendations(
        ModelRecommendationSet(recommendations=[rec(owners=["morgan"])]),
        members=roster,
        target_column_id=COLUMN,
        normalized_note=NOTE,
        meeting_date=MEETING_DATE,
    )
    (a,) = views[0].assignees
    assert a.resolution == "unmatched", "suggesting must not become matching"
    assert a.memberId is None, "a suggestion must never be applied as the resolved member"
    assert a.candidateMemberIds == ["mem_m1"]


def test_two_people_share_a_first_name_so_both_are_offered_and_neither_applied():
    """The exact scenario first-name matching was refused for. Two suggestions, zero assignments."""
    roster = [{"id": "mem_m1", "name": "Morgan Blake"}, {"id": "mem_m2", "name": "Morgan Reyes"}]
    views = resolve_recommendations(
        ModelRecommendationSet(recommendations=[rec(owners=["Morgan"])]),
        members=roster,
        target_column_id=COLUMN,
        normalized_note=NOTE,
        meeting_date=MEETING_DATE,
    )
    (a,) = views[0].assignees
    assert a.resolution == "unmatched"
    assert a.memberId is None
    assert sorted(a.candidateMemberIds) == ["mem_m1", "mem_m2"]


def test_a_name_with_no_near_miss_suggests_nothing():
    (view,) = resolve(rec(owners=["Priya"]))
    (a,) = view.assignees
    assert a.resolution == "unmatched"
    assert a.candidateMemberIds == []


def test_an_exact_match_carries_no_suggestions():
    (view,) = resolve(rec(owners=["Alice Chen"]))
    (a,) = view.assignees
    assert a.resolution == "existing_member"
    assert a.candidateMemberIds == []


def test_suggestions_do_not_unblock_the_card():
    """A suggestion is not a resolution: the card must still be flagged, so bulk-apply skips it and
    a human confirms. If this ever passes with an empty blockedReasons, suggesting has silently
    become matching."""
    roster = [{"id": "mem_m1", "name": "Morgan Blake"}]
    views = resolve_recommendations(
        ModelRecommendationSet(recommendations=[rec(owners=["morgan"])]),
        members=roster,
        target_column_id=COLUMN,
        normalized_note=NOTE,
        meeting_date=MEETING_DATE,
    )
    assert views[0].blockedReasons, "an unmatched owner must still block, suggestion or not"
