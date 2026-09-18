"""In-app feedback: permissive to write, restricted to read.

The asymmetry is the whole design. Anyone who reaches the app can leave a note — an unattributed
one still beats silence — but feedback is other people's candid words about the product, so only the
Instance's primary user reads it back.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_connection
from app.main import app
from app import store

client = TestClient(app)


@pytest.fixture(autouse=True)
def feedback_db(tmp_path, monkeypatch):
    """A database of this module's own.

    `TestClient` does not fire FastAPI's startup event unless it is used as a context manager, so
    migrations never run during tests — a brand-new table simply would not exist. Pointing at a
    temp file and migrating it explicitly also keeps these tests from writing rows into the
    developer's real `data/columnist.sqlite`.
    """
    from app import migrations

    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "feedback.sqlite"))
    migrations.run_migrations()
    yield


# ── writing ───────────────────────────────────────────────────────────────────

def test_anyone_can_leave_feedback_and_it_comes_back_with_an_id():
    r = client.post("/api/v1/feedback", json={"category": "chat", "message": "The chat is slow but useful."})

    assert r.status_code == 201
    body = r.json()
    assert body["message"] == "The chat is slow but useful."
    assert body["category"] == "chat"
    assert body["id"]


def test_feedback_records_even_with_no_caller_identity():
    """Local dev has no Cognito and therefore no user. Refusing to record would throw away the
    only feedback channel the app has when running that way."""
    r = client.post("/api/v1/feedback", json={"message": "Anonymous but real."})

    assert r.status_code == 201
    assert r.json()["userId"] is None


def test_an_empty_message_is_refused():
    r = client.post("/api/v1/feedback", json={"message": "   "})
    assert r.status_code in (400, 422)


def test_an_overlong_message_is_refused_by_the_schema():
    r = client.post("/api/v1/feedback", json={"message": "x" * 4001})
    assert r.status_code == 422


def test_an_unknown_category_degrades_instead_of_failing():
    """A category we do not recognise is not worth losing the note over — but the schema is the
    first gate, so this is really about `store.create_feedback` being called directly."""
    conn = get_connection()
    try:
        row = store.create_feedback(conn, category="wat", message="still useful")
        assert row["category"] == "general"
    finally:
        conn.close()


def test_a_rating_outside_the_scale_is_dropped_not_stored():
    conn = get_connection()
    try:
        assert store.create_feedback(conn, category="chat", message="m", rating=9)["rating"] is None
        assert store.create_feedback(conn, category="chat", message="m", rating=4)["rating"] == 4
    finally:
        conn.close()


# ── reading ───────────────────────────────────────────────────────────────────

def test_reading_is_open_when_cognito_is_off():
    """Local dev is already a single-operator open app; a role check with no identities to check
    would be theatre."""
    client.post("/api/v1/feedback", json={"message": "one"})
    r = client.get("/api/v1/feedback")

    assert r.status_code == 200
    assert len(r.json()) == 1


def test_newest_feedback_comes_first():
    for msg in ("first", "second", "third"):
        client.post("/api/v1/feedback", json={"message": msg})

    messages = [row["message"] for row in client.get("/api/v1/feedback").json()]
    assert messages[0] == "third", f"expected newest first, got {messages}"


def test_a_non_primary_user_cannot_read_the_inbox(monkeypatch):
    """With Cognito on, only the PIU reads. A secondary user gets 404, not 403 — they have no
    business learning the inbox exists."""
    monkeypatch.setattr(settings, "cognito_region", "us-east-1")
    monkeypatch.setattr(settings, "cognito_user_pool_id", "pool")
    monkeypatch.setattr(settings, "cognito_app_client_id", "client")
    monkeypatch.setattr("app.feedback_api.store.member_role", lambda *a, **k: "siu")

    from app.tenancy import current_user as real_current_user

    app.dependency_overrides[real_current_user] = lambda: {"id": "u2", "instance_id": "inst1"}
    try:
        r = client.get("/api/v1/feedback")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 404


def test_the_primary_user_reads_only_their_own_instance(monkeypatch):
    monkeypatch.setattr(settings, "cognito_region", "us-east-1")
    monkeypatch.setattr(settings, "cognito_user_pool_id", "pool")
    monkeypatch.setattr(settings, "cognito_app_client_id", "client")
    monkeypatch.setattr("app.feedback_api.store.member_role", lambda *a, **k: "piu")

    conn = get_connection()
    try:
        store.create_feedback(conn, category="chat", message="mine", instance_id="inst1")
        store.create_feedback(conn, category="chat", message="someone elses", instance_id="inst2")
    finally:
        conn.close()

    from app.tenancy import current_user as real_current_user

    app.dependency_overrides[real_current_user] = lambda: {"id": "u1", "instance_id": "inst1"}
    try:
        rows = client.get("/api/v1/feedback").json()
    finally:
        app.dependency_overrides.clear()

    assert [r["message"] for r in rows] == ["mine"], "feedback leaked across the Instance boundary"


# ── the durable copy ──────────────────────────────────────────────────────────

def test_feedback_is_mirrored_to_s3_when_a_bucket_is_configured(monkeypatch):
    """On an RDS deployment, `deploy/pause.sh` stops RDS — so a database-only design makes
    feedback readable only while the product being complained about is running."""
    from app import feedback_sink

    puts = []

    class FakeS3:
        def put_object(self, **kwargs):
            puts.append(kwargs)

    monkeypatch.setattr(feedback_sink, "_SINK_BROKEN", False)
    monkeypatch.setattr(settings, "feedback_s3_bucket", "kanban-feedback-test")
    monkeypatch.setitem(__import__("sys").modules, "boto3", type("m", (), {"client": staticmethod(lambda _s: FakeS3())}))

    r = client.post("/api/v1/feedback", json={"category": "chat", "message": "mirrored"})

    assert r.status_code == 201
    assert len(puts) == 1, "the note was not mirrored"
    assert puts[0]["Bucket"] == "kanban-feedback-test"
    assert puts[0]["Key"].startswith("feedback/")
    assert "__chat__" in puts[0]["Key"], "category should be visible in a bucket listing"
    import json as _json
    assert _json.loads(puts[0]["Body"].decode())["message"] == "mirrored"


def test_a_broken_s3_mirror_does_not_lose_the_feedback(monkeypatch):
    """Bookkeeping after a successful write must never be able to fail the request it is
    recording."""
    from app import feedback_sink

    class ExplodingS3:
        def put_object(self, **kwargs):
            raise RuntimeError("AccessDenied")

    monkeypatch.setattr(feedback_sink, "_SINK_BROKEN", False)
    monkeypatch.setattr(settings, "feedback_s3_bucket", "kanban-feedback-test")
    monkeypatch.setitem(__import__("sys").modules, "boto3", type("m", (), {"client": staticmethod(lambda _s: ExplodingS3())}))

    r = client.post("/api/v1/feedback", json={"message": "survives a broken mirror"})

    assert r.status_code == 201, "a failed mirror must not fail the submission"
    assert client.get("/api/v1/feedback").json()[0]["message"] == "survives a broken mirror"


def test_no_bucket_configured_means_no_s3_call(monkeypatch):
    """Local dev has no bucket and must not try — nor pay the boto3 import."""
    from app import feedback_sink

    monkeypatch.setattr(feedback_sink, "_SINK_BROKEN", False)
    monkeypatch.setattr(settings, "feedback_s3_bucket", "")

    def explode(_name):
        raise AssertionError("boto3 must not be touched when no bucket is configured")

    monkeypatch.setitem(__import__("sys").modules, "boto3", type("m", (), {"client": staticmethod(explode)}))

    assert client.post("/api/v1/feedback", json={"message": "local"}).status_code == 201
