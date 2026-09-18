"""The note-import API, end to end — `/analyze` and its NDJSON stream, the note-free restore,
per-card re-targeting, apply as the one mutation, the board version guard, bulk create + undo, and
the daily usage meter.

Two things only an API test can prove, which the unit tests deliberately don't:

1. **The check order is real.** The endpoint docstring promises cheapest-and-most-likely-wrong
   first, and — load-bearing — that *nothing reaches the model until every input check passes*. A
   spy provider that flags if it is called turns "runs before inference" into an assertion instead
   of a comment.
2. **The pieces are actually wired together.** The unit tests each pass in isolation; this drives a
   real request through validate → session → provider → resolve → response and checks a card comes
   out with the user's column stamped and its owner matched.

The provider is a *fake* — no network, no key. Model quality is graded by the corpus, never
here. `conftest.py` pins Cognito off so these routes are open; without that they would 401.
"""
from __future__ import annotations

from types import SimpleNamespace

import json

import pytest
from fastapi.testclient import TestClient
from app.note_imports import api as note_api
from app.note_imports.errors import ImportError as ImportRejection
from app.note_imports.provider import StreamedItem
from app.note_imports.schema import (
    ModelEvidence,
    ModelPriority,
    ModelRecommendation,
    ModelRecommendationSet,
)

NOTE = "Alice Chen will finalize the vendor shortlist by Friday."


def one_card(*, owner: str = "Alice Chen", excerpt: str = NOTE) -> ModelRecommendationSet:
    return ModelRecommendationSet(
        recommendations=[
            ModelRecommendation(
                action="create_card",
                title="Finalize the vendor shortlist",
                assignees=[{"rawName": owner, "confidence": 0.95}],
                dueDate=None,
                priority=ModelPriority(value="medium", rawText=None, provenance="default", confidence=1.0),
                evidence=[ModelEvidence(excerpt=excerpt, locator="line:1")],
                reason="Explicit assignment in the note.",
                confidence=0.95,
            )
        ]
    )


class _Provider:
    """A fake extraction provider. Records whether it was reached and returns a fixed set — or
    raises if the test wants to prove the model is never called."""

    def __init__(self, result=None, *, boom=False):
        self.called = False
        self._result = result if result is not None else ModelRecommendationSet(recommendations=[])
        self._boom = boom

    def extract(self, *, system_prompt: str, user_message: str) -> ModelRecommendationSet:
        self.called = True
        if self._boom:
            raise AssertionError("the model was called when it should not have been")
        return self._result


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A live app over a seeded temp DB. Returns the client, the board, and a knob to inject a
    provider (default: a provider that proposes nothing)."""
    from app.config import settings
    from app.note_imports.provider import OfflineExtractionProvider

    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "test.sqlite"))
    # Analysis is refused up front unless a model backend is connected (AI_NOT_CONFIGURED), so the
    # fixture "connects" one — and replaces the provider, so no test here can reach a network.
    monkeypatch.setattr(settings, "agent_backend", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-real")
    monkeypatch.setattr(note_api, "get_extraction_provider", lambda _s: OfflineExtractionProvider())

    from app import migrations, store
    from app.db import get_connection

    migrations.run_migrations()

    conn = get_connection()
    try:
        board = store.create_board(
            conn,
            "Test board",
            "",
            [{"title": "To Do"}, {"title": "Doing"}],
            [
                {"name": "Alice Chen", "initials": "AC", "color": "#ef4444"},
                {"name": "Bob Ng", "initials": "BN", "color": "#3b82f6"},
            ],
        )
    finally:
        conn.close()

    from app.main import app

    client = TestClient(app)
    board_id = board["id"]
    leftmost = board["columnOrder"][0]
    members = {m["name"]: m["id"] for m in board["teamMembers"]}

    def _post(path, *, provider=None, files=None, **form):
        if provider is not None:
            monkeypatch.setattr(note_api, "get_extraction_provider", lambda _s: provider)
        data = {"meetingDate": "2026-07-15", "clientRequestId": "req-1"}
        data.update(form)  # pastedText, targetColumnId, a meetingDate override, etc.
        return client.post(f"/api/v1/boards/{board_id}/note-imports/{path}", data=data, files=files)

    def analyze(**kw):
        return _post("analyze", **kw)

    def stream(**kw):
        return _post("analyze/stream", **kw)

    def get(session_id, board=None):
        return client.get(f"/api/v1/boards/{board or board_id}/note-imports/{session_id}")

    def apply(session_id, rec_id, *, idempotencyKey="idem-1", **body):
        payload = {"idempotencyKey": idempotencyKey, "title": "A card", "targetColumnId": leftmost}
        payload.update(body)
        return client.post(
            f"/api/v1/boards/{board_id}/note-imports/{session_id}/recommendations/{rec_id}/apply",
            json=payload,
        )

    def board_state():
        return client.get(f"/api/v1/boards/{board_id}").json()

    def snapshot(board_dict):
        return client.put(f"/api/v1/boards/{board_id}/snapshot", json=board_dict)

    def patch_column(session_id, rec_id, target_column_id):
        return client.patch(
            f"/api/v1/boards/{board_id}/note-imports/{session_id}/recommendations/{rec_id}",
            json={"targetColumnId": target_column_id},
        )

    def apply_batch(session_id, *, idempotencyKey="batch-1", **body):
        return client.post(
            f"/api/v1/boards/{board_id}/note-imports/{session_id}/apply-batch",
            json={"idempotencyKey": idempotencyKey, **body},
        )

    def undo(session_id, batch_id):
        return client.post(
            f"/api/v1/boards/{board_id}/note-imports/{session_id}/batches/{batch_id}/undo"
        )

    def toggle_complete(card_id, completed=True):
        return client.post(
            f"/api/v1/boards/{board_id}/cards/{card_id}/toggle-complete",
            json={"completed": completed},
        )

    return SimpleNamespace(
        client=client, board=board, board_id=board_id, leftmost=leftmost, members=members,
        analyze=analyze, stream=stream, get=get, apply=apply, board_state=board_state,
        snapshot=snapshot, patch_column=patch_column, apply_batch=apply_batch, undo=undo,
        toggle_complete=toggle_complete,
    )


def code_of(resp) -> str:
    return resp.json()["detail"]["code"]


# --- it is actually wired together --------------------------------------------------------------

def test_a_note_becomes_a_resolved_card(api):
    provider = _Provider(one_card())
    resp = api.analyze(provider=provider, pastedText=NOTE)
    assert resp.status_code == 200, resp.text
    assert provider.called

    body = resp.json()
    (rec,) = body["recommendations"]
    assert rec["action"] == "create_card"
    assert rec["id"].startswith("rec_")
    # the user's column, stamped by code — defaulted to leftmost since none was sent
    assert rec["targetColumnId"] == api.leftmost
    # the owner, matched to a real member by code
    (assignee,) = rec["assignees"]
    assert assignee["resolution"] == "existing_member"
    assert assignee["memberId"] == api.members["Alice Chen"]
    # nothing faulted → a clean, bulk-addable card
    assert rec["blockedReasons"] == []


def test_the_chosen_column_is_stamped_when_supplied(api):
    doing = api.board["columnOrder"][1]
    resp = api.analyze(provider=_Provider(one_card()), pastedText=NOTE, targetColumnId=doing)
    assert resp.status_code == 200, resp.text
    assert resp.json()["recommendations"][0]["targetColumnId"] == doing


def test_no_column_supplied_defaults_every_card_to_the_first_column(api):
    """The model never proposes a column, so with none chosen up front every card defaults to
    the board's first column — the reviewer re-targets each one per card during review."""
    resp = api.analyze(provider=_Provider(one_card()), pastedText=NOTE)  # no targetColumnId
    assert resp.status_code == 200, resp.text
    assert resp.json()["recommendations"][0]["targetColumnId"] == api.leftmost


def test_re_targeting_a_recommendation_persists(api):
    """The per-card column choice is stored on the recommendation and survives the note-free
    GET — it is a durable decision, not transient UI state."""
    doing = api.board["columnOrder"][1]
    sid, rec = analyzed(api)
    assert rec["targetColumnId"] == api.leftmost  # defaulted

    resp = api.patch_column(sid, rec["id"], doing)
    assert resp.status_code == 200, resp.text
    assert resp.json()["targetColumnId"] == doing
    assert api.get(sid).json()["recommendations"][0]["targetColumnId"] == doing


def test_the_bulk_path_honors_a_per_card_column_change(api):
    """Why re-targeting persists: `Add the clean ones` reads the stored column, so a card the reviewer
    moved is bulk-created where they put it, not in the up-front default. This is the
    guarantee that makes per-card columns and one-click bulk coexist."""
    doing = api.board["columnOrder"][1]
    sid, rec = analyzed(api)  # clean card, defaulted to the leftmost column
    api.patch_column(sid, rec["id"], doing)

    resp = api.apply_batch(sid)
    assert resp.status_code == 200, resp.text
    (card_id,) = resp.json()["createdCardIds"]
    board = api.board_state()
    assert card_id in board["columns"][doing]["cardIds"]
    assert card_id not in board["columns"][api.leftmost]["cardIds"]


def test_re_targeting_an_applied_card_is_a_conflict(api):
    """Once the card exists it is moved on the board, not on the spent recommendation (APPLY_CONFLICT)."""
    doing = api.board["columnOrder"][1]
    sid, rec = analyzed(api)
    api.apply(
        sid, rec["id"], title=rec["title"], targetColumnId=rec["targetColumnId"],
        assigneeMemberIds=[rec["assignees"][0]["memberId"]],
    )
    resp = api.patch_column(sid, rec["id"], doing)
    assert resp.status_code == 409
    assert code_of(resp) == "APPLY_CONFLICT"


def test_re_targeting_to_a_bogus_column_is_rejected(api):
    """The column is never trusted from the client: an off-board target is refused, nothing changes."""
    sid, rec = analyzed(api)
    resp = api.patch_column(sid, rec["id"], "col_not_on_this_board")
    assert resp.status_code == 400
    assert code_of(resp) == "TARGET_COLUMN_REQUIRED"
    assert api.get(sid).json()["recommendations"][0]["targetColumnId"] == api.leftmost  # unchanged


def test_an_off_board_owner_is_flagged_not_silently_assigned(api):
    """End-to-end proof that resolution's blocking survives the wire: an owner who isn't on the
    board comes back flagged, with a Create-member offer — never a clean card."""
    resp = api.analyze(provider=_Provider(one_card(owner="Priya")), pastedText=NOTE)
    assert resp.status_code == 200, resp.text
    (rec,) = resp.json()["recommendations"]
    assert rec["assignees"][0]["resolution"] == "unmatched"
    assert rec["requiresNewMemberNamed"] == "Priya"
    assert rec["blockedReasons"]


def test_a_model_that_proposes_nothing_returns_an_empty_proposal(api):
    """A connected model that finds no action items → an empty set. That is a correct, non-erroring
    outcome. With no model connected at all, analysis is refused instead (AI_NOT_CONFIGURED)."""
    resp = api.analyze(pastedText=NOTE)
    assert resp.status_code == 200, resp.text
    assert resp.json()["recommendations"] == []


# --- the check order: nothing reaches the model until the input is clean ------------------------

def test_two_inputs_are_rejected_before_the_model(api):
    spy = _Provider(boom=True)
    resp = api.analyze(
        provider=spy,
        pastedText=NOTE,
        files={"file": ("note.txt", b"also a note", "text/plain")},
    )
    assert resp.status_code == 400
    assert code_of(resp) == "MULTIPLE_INPUTS"
    assert not spy.called


def test_no_input_is_rejected_before_the_model(api):
    spy = _Provider(boom=True)
    resp = api.analyze(provider=spy)
    assert resp.status_code == 400
    assert code_of(resp) == "EMPTY_INPUT"
    assert not spy.called


def test_a_stale_column_is_rejected_before_the_model(api):
    spy = _Provider(boom=True)
    resp = api.analyze(provider=spy, pastedText=NOTE, targetColumnId="col_does_not_exist")
    assert resp.status_code == 400
    assert code_of(resp) == "TARGET_COLUMN_REQUIRED"
    assert not spy.called


def test_an_oversized_note_is_rejected_before_the_model(api):
    """The size gate fires on a length check, before the note is read *or* sent. Proven
    by the spy staying untouched — the whole point of ordering size ahead of inference.

    Starlette caps every form field at 1 MB (multipart always; urlencoded since 1.x), the same as
    `MAX_RAW_BYTES`, so an oversized paste is refused by the framework with a plain 400 before the
    handler's own FILE_TOO_LARGE check can run. Either gate satisfies the requirement."""
    spy = _Provider(boom=True)
    resp = api.analyze(provider=spy, pastedText="x" * (1024 * 1024 + 1))
    assert resp.status_code in (400, 413)
    assert not spy.called


def test_a_bad_meeting_date_is_rejected_before_the_model(api):
    spy = _Provider(boom=True)
    resp = api.analyze(provider=spy, pastedText=NOTE, meetingDate="2026-02-30")
    assert resp.status_code == 400
    assert code_of(resp) == "INVALID_MEETING_DATE"
    assert not spy.called


def test_a_missing_board_is_a_clean_error(api):
    resp = api.client.post(
        "/api/v1/boards/board_nope/note-imports/analyze",
        data={"meetingDate": "2026-07-15", "clientRequestId": "req-1", "pastedText": NOTE},
    )
    assert resp.status_code == 400
    assert code_of(resp) == "BOARD_HAS_NO_COLUMNS"


# --- the streaming endpoint (NDJSON) ------------------------------------------------------------

def card(*, owner="Alice Chen", excerpt=NOTE, title="Finalize the vendor shortlist", confidence=0.95) -> ModelRecommendation:
    return ModelRecommendation(
        action="create_card",
        title=title,
        assignees=[{"rawName": owner, "confidence": confidence}],
        dueDate=None,
        priority=ModelPriority(value="medium", rawText=None, provenance="default", confidence=1.0),
        evidence=[ModelEvidence(excerpt=excerpt, locator="line:1")],
        reason="Explicit assignment.",
        confidence=confidence,
    )


class _StreamProvider:
    """Yields StreamedItems for the streaming endpoint. `nones` interleaves partial failures at the
    end; `raise_after` raises once the good items are out (a mid-stream model failure)."""

    def __init__(self, *recs, nones=0, raise_after=None):
        self._recs = recs
        self._nones = nones
        self._raise_after = raise_after

    def extract(self, **_kw):
        raise AssertionError("the streaming endpoint must call stream(), not extract()")

    def stream(self, *, system_prompt, user_message):
        for r in self._recs:
            yield StreamedItem(r)
        for _ in range(self._nones):
            yield StreamedItem(None)
        if self._raise_after is not None:
            raise self._raise_after


def events(resp) -> list[dict]:
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_stream_emits_session_then_a_card_then_done(api):
    resp = api.stream(provider=_StreamProvider(card(), card(title="Second thing", excerpt=NOTE)), pastedText=NOTE)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")

    evs = events(resp)
    assert evs[0]["type"] == "session"
    assert evs[0]["session"]["id"].startswith("imp_")

    recs = [e for e in evs if e["type"] == "recommendation"]
    assert [e["found"] for e in recs] == [1, 2]  # an honest running count
    assert recs[0]["recommendation"]["targetColumnId"] == api.leftmost
    assert recs[0]["recommendation"]["assignees"][0]["memberId"] == api.members["Alice Chen"]

    assert evs[-1] == {"type": "done", "found": 2, "partialFailureCount": 0}


def test_stream_resolves_each_card_as_it_arrives(api):
    """The off-board owner is flagged in its own event — resolution runs per card on the stream, not
    in a batch at the end."""
    resp = api.stream(provider=_StreamProvider(card(owner="Priya")), pastedText=NOTE)
    (rec,) = [e for e in events(resp) if e["type"] == "recommendation"]
    assert rec["recommendation"]["assignees"][0]["resolution"] == "unmatched"
    assert rec["recommendation"]["blockedReasons"]


def test_stream_counts_partial_failures_without_dropping_good_cards(api):
    resp = api.stream(provider=_StreamProvider(card(), nones=2), pastedText=NOTE)
    evs = events(resp)
    assert len([e for e in evs if e["type"] == "recommendation"]) == 1
    assert evs[-1] == {"type": "done", "found": 1, "partialFailureCount": 2}


def test_stream_reports_a_mid_stream_failure_in_band(api):
    """A model timeout after a card was already sent can't change the 200 status, so it arrives as a
    trailing error event — after the card, never instead of it."""
    provider = _StreamProvider(card(), raise_after=ImportRejection("MODEL_TIMEOUT"))
    resp = api.stream(provider=provider, pastedText=NOTE)
    assert resp.status_code == 200  # headers already sent before the failure
    evs = events(resp)
    assert [e["type"] for e in evs] == ["session", "recommendation", "error"]
    assert evs[-1]["code"] == "MODEL_TIMEOUT"
    assert "action" in evs[-1]  # an error names a next action


def test_stream_with_nothing_proposed_streams_a_session_and_an_empty_done(api):
    """A model that proposes nothing → the stream still opens and closes cleanly with zero cards."""
    resp = api.stream(pastedText=NOTE)
    evs = events(resp)
    assert evs[0]["type"] == "session"
    assert evs[-1] == {"type": "done", "found": 0, "partialFailureCount": 0}
    assert not [e for e in evs if e["type"] == "recommendation"]


def test_stream_still_validates_input_up_front(api):
    """A bad input is a normal HTTP error *before* the stream opens — not a 200 with an error event.
    The spy proves the model is never reached."""
    spy = _StreamProvider(card())
    spy.stream = lambda **_kw: (_ for _ in ()).throw(AssertionError("model reached on bad input"))
    resp = api.stream(provider=spy, pastedText=NOTE, targetColumnId="col_nope")
    assert resp.status_code == 400
    assert code_of(resp) == "TARGET_COLUMN_REQUIRED"


# --- persistence: a proposal survives a refresh -------------------------------------------------

def test_analyze_then_get_returns_the_identical_proposal(api):
    """The strongest reconstruction test: GET must return byte-identical recommendations to what
    /analyze returned, rebuilt from storage without the note."""
    posted = api.analyze(provider=_Provider(one_card()), pastedText=NOTE)
    session_id = posted.json()["id"]

    got = api.get(session_id)
    assert got.status_code == 200
    assert got.json()["recommendations"] == posted.json()["recommendations"]
    assert got.json()["recommendations"][0]["targetColumnId"] == api.leftmost


def test_stream_then_get_returns_the_streamed_cards(api):
    provider = _StreamProvider(card(), card(title="Second thing"), nones=1)
    resp = api.stream(provider=provider, pastedText=NOTE)
    evs = events(resp)
    session_id = evs[0]["session"]["id"]

    got = api.get(session_id).json()
    assert len(got["recommendations"]) == 2
    assert [r["found"] for r in evs if r["type"] == "recommendation"] == [1, 2]
    assert got["partialFailureCount"] == 1  # persisted at done, not just streamed


def test_get_reconstructs_a_flagged_card_faithfully(api):
    """The server-derived verdict must survive the note it was computed against: an off-board owner
    comes back from storage still unmatched, still blocked, still offering a new member."""
    posted = api.analyze(provider=_Provider(one_card(owner="Priya")), pastedText=NOTE)
    (rec,) = api.get(posted.json()["id"]).json()["recommendations"]
    assert rec["assignees"][0]["resolution"] == "unmatched"
    assert rec["requiresNewMemberNamed"] == "Priya"
    assert rec["blockedReasons"]


def test_get_an_empty_session_has_no_recommendations(api):
    posted = api.analyze(pastedText=NOTE)  # the fixture's default provider proposes nothing
    got = api.get(posted.json()["id"]).json()
    assert got["recommendations"] == []


def test_get_is_scoped_to_its_board(api):
    """A session is never returned for another board. A wrong-board GET is a miss, not a leak."""
    posted = api.analyze(provider=_Provider(one_card()), pastedText=NOTE)
    resp = api.get(posted.json()["id"], board="board_someone_elses")
    assert resp.status_code == 400
    assert code_of(resp) == "BOARD_REFERENCE_STALE"


def test_get_unknown_session_is_a_clean_error(api):
    resp = api.get("imp_does_not_exist")
    assert resp.status_code == 400
    assert code_of(resp) == "BOARD_REFERENCE_STALE"


# --- apply: the one board mutation --------------------------------------------------------------

def analyzed(api, **card_kw):
    """Analyze one card and return (session_id, the resolved recommendation dict)."""
    posted = api.analyze(provider=_Provider(one_card(**card_kw)), pastedText=NOTE).json()
    return posted["id"], posted["recommendations"][0]


def test_apply_creates_a_real_card_on_the_board(api):
    sid, rec = analyzed(api)
    resp = api.apply(
        sid, rec["id"],
        title=rec["title"], targetColumnId=rec["targetColumnId"],
        assigneeMemberIds=[rec["assignees"][0]["memberId"]],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["createdCardId"] and body["replayed"] is False

    board = api.board_state()
    created = board["cards"][body["createdCardId"]]
    assert created["title"] == rec["title"]
    assert api.members["Alice Chen"] in created["assignees"]
    assert body["createdCardId"] in board["columns"][rec["targetColumnId"]]["cardIds"]


def test_apply_is_idempotent_a_retry_returns_the_same_card(api):
    """A lost response, retried with the same approval, must not create a second card."""
    sid, rec = analyzed(api)
    first = api.apply(sid, rec["id"], title="Same", idempotencyKey="k1")
    again = api.apply(sid, rec["id"], title="Same", idempotencyKey="k1")

    assert first.json()["createdCardId"] == again.json()["createdCardId"]
    assert again.json()["replayed"] is True
    assert len(api.board_state()["cards"]) == 1  # exactly one card, not two


def test_apply_a_changed_approval_after_creation_is_a_conflict(api):
    """The recommendation was already created; a second, *different* approval is refused rather
    than creating a divergent duplicate."""
    sid, rec = analyzed(api)
    api.apply(sid, rec["id"], title="Original")
    resp = api.apply(sid, rec["id"], title="Rewritten")
    assert resp.status_code == 409
    assert code_of(resp) == "APPLY_CONFLICT"
    assert len(api.board_state()["cards"]) == 1


def test_apply_to_a_deleted_column_is_stale_not_a_crash(api):
    sid, rec = analyzed(api)
    resp = api.apply(sid, rec["id"], targetColumnId="col_deleted_since_review")
    assert resp.status_code == 400
    assert code_of(resp) == "BOARD_REFERENCE_STALE"
    assert api.board_state()["cards"] == {}  # nothing created


def test_apply_with_a_non_member_assignee_is_stale(api):
    sid, rec = analyzed(api)
    resp = api.apply(sid, rec["id"], assigneeMemberIds=["member_not_on_this_board"])
    assert resp.status_code == 400
    assert code_of(resp) == "BOARD_REFERENCE_STALE"


def test_apply_creates_an_approved_new_member_in_the_same_transaction(api):
    """An unmatched owner becomes a board member only with explicit approval, created atomically
    with the card and assigned to it."""
    sid, rec = analyzed(api, owner="Priya")
    assert rec["requiresNewMemberNamed"] == "Priya"  # flagged at analysis
    resp = api.apply(sid, rec["id"], memberCreationApproved=True, newMemberName="Priya")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["createdMemberId"]

    board = api.board_state()
    assert body["createdMemberId"] in [m["id"] for m in board["teamMembers"]]
    assert body["createdMemberId"] in board["cards"][body["createdCardId"]]["assignees"]


def test_the_same_unmatched_owner_on_two_cards_creates_one_member_not_two(api):
    """Dedupe: 'Priya' owns two flagged cards and the reviewer approves the create on each. The
    first apply mints the member; the second finds it by name and reuses it — one 'Priya' on the
    board, both cards assigned to it, never a duplicate person per card."""
    posted = api.analyze(
        provider=_Provider(ModelRecommendationSet(recommendations=[
            card(owner="Priya", title="First Priya card"),
            card(owner="Priya", title="Second Priya card"),
        ])),
        pastedText=NOTE,
    ).json()
    sid = posted["id"]
    rec_a, rec_b = posted["recommendations"]

    r1 = api.apply(sid, rec_a["id"], title="First Priya card",
                   memberCreationApproved=True, newMemberName="Priya", idempotencyKey="p1")
    r2 = api.apply(sid, rec_b["id"], title="Second Priya card",
                   memberCreationApproved=True, newMemberName="Priya", idempotencyKey="p2")
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)

    # The first apply created the member; the second reused it (created nothing).
    assert r1.json()["createdMemberId"]
    assert r2.json()["createdMemberId"] is None

    board = api.board_state()
    priyas = [m for m in board["teamMembers"] if m["name"] == "Priya"]
    assert len(priyas) == 1, board["teamMembers"]

    member_id = priyas[0]["id"]
    assert member_id in board["cards"][r1.json()["createdCardId"]]["assignees"]
    assert member_id in board["cards"][r2.json()["createdCardId"]]["assignees"]


def test_apply_records_created_state_and_the_card_id_on_the_recommendation(api):
    sid, rec = analyzed(api)
    created_card = api.apply(sid, rec["id"]).json()["createdCardId"]
    got = api.get(sid).json()["recommendations"][0]
    assert got["state"] == "created"
    assert got["createdCardId"] == created_card


def test_apply_to_a_missing_session_is_a_clean_error(api):
    resp = api.apply("imp_nope", "rec_nope")
    assert resp.status_code == 400
    assert code_of(resp) == "BOARD_REFERENCE_STALE"


def test_apply_to_a_missing_recommendation_is_a_clean_error(api):
    sid, _rec = analyzed(api)
    resp = api.apply(sid, "rec_not_in_this_session")
    assert resp.status_code == 400
    assert code_of(resp) == "BOARD_REFERENCE_STALE"


# --- the board version guard --------------------------------------------------------------------

def test_the_board_exposes_a_server_authoritative_version(api):
    assert api.board_state()["version"] == 0  # a fresh board starts at 0


def test_a_snapshot_declaring_the_current_version_succeeds_and_bumps_it(api):
    before = api.board_state()
    resp = api.snapshot(before)  # declares the current version
    assert resp.status_code == 200, resp.text
    assert api.board_state()["version"] == before["version"] + 1


def test_a_snapshot_without_a_version_is_rejected(api):
    """A snapshot WRITE must declare its base version: omitting it would skip the staleness
    guard, letting a hand-rolled client clobber the board. The endpoint 422s a versionless
    snapshot. (The real SPA always declares it.)"""
    board = api.board_state()
    board.pop("version")
    assert api.snapshot(board).status_code == 422


def test_apply_advances_the_board_version(api):
    sid, rec = analyzed(api)
    before = api.board_state()["version"]
    result = api.apply(sid, rec["id"]).json()
    assert result["boardVersion"] == before + 1


def test_a_stale_snapshot_cannot_delete_an_applied_card(api):
    """The guard's headline. A client hydrates the empty board, the agent applies a card (advancing
    the version), and the client's stale snapshot — which never saw the card — is rejected whole
    rather than deleting it. This is the data-loss the guard exists to prevent."""
    stale_client_view = api.board_state()  # version 0, no cards

    sid, rec = analyzed(api)
    api.apply(sid, rec["id"])  # agent creates a card; board version advances to 1
    assert len(api.board_state()["cards"]) == 1

    resp = api.snapshot(stale_client_view)  # still declares version 0
    assert resp.status_code == 409
    assert "BOARD_VERSION_STALE" in str(resp.json())
    assert len(api.board_state()["cards"]) == 1  # the applied card survived, whole


def test_renaming_the_board_advances_the_version(api):
    """A title/description edit is a write to the board's snapshot state, so it bumps the version
    like every other mutation — otherwise a client that missed the rename stays 'current'."""
    before = api.board_state()["version"]
    resp = api.client.patch(f"/api/v1/boards/{api.board_id}", json={"title": "Renamed board"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == before + 1


def test_a_stale_snapshot_cannot_revert_a_rename(api):
    """The rename counterpart of that headline: one client renames the board, another saves a
    snapshot it loaded beforehand, and the old title must not come back."""
    stale_client_view = api.board_state()  # still carries the original title

    renamed = api.client.patch(f"/api/v1/boards/{api.board_id}", json={"title": "Renamed board"})
    assert renamed.status_code == 200, renamed.text

    resp = api.snapshot(stale_client_view)
    assert resp.status_code == 409
    assert "BOARD_VERSION_STALE" in str(resp.json())
    assert api.board_state()["projectInfo"]["title"] == "Renamed board"


# --- bulk create + undo -------------------------------------------------------------------------

def mixed_set() -> ModelRecommendationSet:
    """One clean recommendation (a real member, a locatable quote) and one the server flags (an
    unmatched owner) — the flagged one self-reports 1.00 confidence, to prove confidence can't
    promote it."""
    return ModelRecommendationSet(
        recommendations=[
            card(title="Clean card", owner="Alice Chen"),
            card(title="Flagged card", owner="Priya", confidence=1.0),
        ]
    )


def analyzed_mixed(api):
    posted = api.analyze(provider=_Provider(mixed_set()), pastedText=NOTE).json()
    return posted["id"], posted["recommendations"]


def test_bulk_creates_only_the_unflagged_and_leaves_flagged_pending(api):
    """`Add the clean ones` covers the unflagged only; the flagged one is untouched and stays
    pending for per-card review."""
    sid, recs = analyzed_mixed(api)
    flagged = next(r for r in recs if r["blockedReasons"])

    resp = api.apply_batch(sid)
    assert resp.status_code == 200, resp.text
    assert resp.json()["createdCount"] == 1  # only the clean one

    board = api.board_state()
    assert len(board["cards"]) == 1
    titles = {c["title"] for c in board["cards"].values()}
    assert titles == {"Clean card"}

    after = {r["id"]: r for r in api.get(sid).json()["recommendations"]}
    assert after[flagged["id"]]["state"] == "pending"  # flagged one never created


def test_confidence_1_0_does_not_promote_a_flagged_card_into_the_batch(api):
    """The flagged recommendation self-reported 1.00 and is still excluded — a server-side
    finding is never overridden by the model asserting its own trustworthiness."""
    sid, recs = analyzed_mixed(api)
    flagged = next(r for r in recs if r["blockedReasons"])
    assert flagged["confidence"] == 1.0

    api.apply_batch(sid)
    board = api.board_state()
    assert "Flagged card" not in {c["title"] for c in board["cards"].values()}


def test_bulk_is_all_or_nothing_on_a_stale_version(api):
    """A stale declared version fails the whole batch — nothing is created."""
    sid, _ = analyzed_mixed(api)
    resp = api.apply_batch(sid, expectedBoardVersion=999)
    assert resp.status_code == 409
    assert code_of(resp) == "BOARD_VERSION_STALE"
    assert api.board_state()["cards"] == {}


def test_bulk_advances_the_board_version_once(api):
    sid, _ = analyzed_mixed(api)
    before = api.board_state()["version"]
    result = api.apply_batch(sid).json()
    assert result["boardVersion"] == before + 1  # one bump for the whole batch, not one per card


def test_undo_removes_exactly_what_the_batch_created(api):
    """Undo removes exactly the batch's cards and returns the recommendations to pending."""
    sid, _ = analyzed_mixed(api)
    batch = api.apply_batch(sid).json()
    assert batch["createdCount"] == 1

    undone = api.undo(sid, batch["batchId"]).json()
    assert undone["removedCount"] == 1
    assert undone["keptCount"] == 0
    assert api.board_state()["cards"] == {}

    # the recommendation is pending again, re-appliable
    clean = next(r for r in api.get(sid).json()["recommendations"] if not r["blockedReasons"])
    assert clean["state"] == "pending"


def test_undo_keeps_a_card_the_user_changed_and_reports_it(api):
    """A card the user has since acted on is kept and reported, never silently deleted."""
    sid, _ = analyzed_mixed(api)
    batch = api.apply_batch(sid).json()
    (created_card,) = batch["createdCardIds"]

    api.toggle_complete(created_card)  # the user acts on the card

    undone = api.undo(sid, batch["batchId"]).json()
    assert undone["removedCount"] == 0
    assert undone["keptCount"] == 1
    assert undone["kept"][0]["cardId"] == created_card
    assert "edited or moved" in undone["kept"][0]["reason"]
    assert created_card in api.board_state()["cards"]  # still there


def test_undo_frees_the_recommendation_to_be_applied_again(api):
    """Undo flips the application to 'undone', freeing the per-rec unique slot so the card can be
    re-created rather than replayed as a phantom of the deleted one."""
    sid, _ = analyzed_mixed(api)
    batch = api.apply_batch(sid).json()
    api.undo(sid, batch["batchId"])

    clean = next(r for r in api.get(sid).json()["recommendations"] if not r["blockedReasons"])
    reapplied = api.apply(sid, clean["id"], title="Clean card", targetColumnId=api.leftmost)
    assert reapplied.status_code == 200, reapplied.text
    assert reapplied.json()["replayed"] is False  # a fresh card, not the deleted one replayed
    assert len(api.board_state()["cards"]) == 1


# --- daily-meter reservation for document analysis ----------------------------------------------

def test_reserve_usage_records_for_an_authed_user_on_a_paid_backend(monkeypatch):
    """A notes path that read the meter but never recorded would leave paid document analyses
    unmetered. `_reserve_usage` charges the estimate for an authenticated user on a real
    backend."""
    import types

    from app.note_imports import api

    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(api, "record_usage", lambda uid, tok: calls.append((uid, tok)))
    monkeypatch.setattr(api, "settings", types.SimpleNamespace(cognito_enabled=True, agent_backend="bedrock"))

    api._reserve_usage({"id": "u1"}, 500)
    assert calls == [("u1", 500)]


def test_reserve_usage_skips_the_free_offline_backend_and_anonymous_callers(monkeypatch):
    import types

    from app.note_imports import api

    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(api, "record_usage", lambda uid, tok: calls.append((uid, tok)))

    # offline backend is free — no charge even for an authed user
    monkeypatch.setattr(api, "settings", types.SimpleNamespace(cognito_enabled=True, agent_backend="offline"))
    api._reserve_usage({"id": "u1"}, 500)
    # no authenticated user to meter
    monkeypatch.setattr(api, "settings", types.SimpleNamespace(cognito_enabled=True, agent_backend="bedrock"))
    api._reserve_usage(None, 500)

    assert calls == []


# --- no model connected ------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["analyze", "analyze/stream"])
@pytest.mark.parametrize(
    "backend, key, log_names",
    [("offline", "sk-test", "AGENT_BACKEND"), ("openai", "", "OPENAI_API_KEY"), ("anthropic", "", "ANTHROPIC_API_KEY")],
)
def test_analysis_is_refused_up_front_when_no_model_is_connected(api, monkeypatch, caplog, path, backend, key, log_names):
    """With no model connected, a note used to come back as zero cards — which the review screen
    shows as "no action items were found", a wrong answer. It must be refused before any other
    check, tell the user to contact their administrator, and tell the administrator (in the log)
    what to set."""
    from app.config import settings

    monkeypatch.setattr(settings, "agent_backend", backend)
    monkeypatch.setattr(settings, "openai_api_key", key)
    monkeypatch.setattr(settings, "anthropic_api_key", key)

    post = api.analyze if path == "analyze" else api.stream
    r = post(pastedText="Bob will send the invoice by Friday.")

    assert r.status_code == 503
    body = r.json()["detail"]
    assert body["code"] == "AI_NOT_CONFIGURED"
    assert "administrator" in body["message"]
    assert "AGENT_BACKEND" not in body["message"] and "API_KEY" not in body["message"]
    assert log_names in caplog.text


def test_no_model_is_refused_before_the_input_is_even_checked(api, monkeypatch):
    """First check, not last: an empty note on an unconfigured app gets the setup message, not an
    input error the user would fix only to hit the setup message next."""
    from app.config import settings

    monkeypatch.setattr(settings, "agent_backend", "offline")
    r = api.analyze(pastedText="")
    assert r.json()["detail"]["code"] == "AI_NOT_CONFIGURED"
