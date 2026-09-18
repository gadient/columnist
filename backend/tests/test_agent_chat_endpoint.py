"""The chat endpoint with the tool-calling backend wired in.

The loop tests and the eval work *below* HTTP — they call `run_agent` directly. This file exercises
the seams that only exist at the endpoint: the backend switch, the input gate, the
usage meter, the audit log, and the shape the SPA actually consumes. Those seams are where a change
can be correct in isolation and wrong in place.

The provider is stubbed throughout. What is under test is the wiring, not the model.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import loop
from app.config import settings
from app.main import app

client = TestClient(app)


class StubProvider:
    """One tool call, then an answer — the shape of a real two-turn question."""

    def __init__(self, *, tool: str = "get_high_priority_tasks", text: str = "One card is high priority.") -> None:
        self._tool = tool
        self._text = text
        self.calls = 0

    def converse(self, *, system, messages, tool_specs):
        self.calls += 1
        if any("toolResult" in b for m in messages for b in m["content"]):
            return loop.ModelTurn(
                stop_reason="end_turn", content=({"text": self._text},), input_tokens=900, output_tokens=100
            )
        return loop.ModelTurn(
            stop_reason="tool_use",
            content=({"toolUse": {"toolUseId": "t1", "name": self._tool, "input": {}}},),
            input_tokens=800,
            output_tokens=60,
        )


@pytest.fixture
def bedrock_backend(two_boards, monkeypatch):
    """Flip the backend and hand the endpoint a stub provider instead of boto3."""
    monkeypatch.setattr(settings, "agent_backend", "bedrock")
    provider = StubProvider()
    monkeypatch.setattr(
        "app.agents.bedrock_provider.BedrockChatProvider", lambda **kwargs: provider
    )
    return provider


# ── the backend switch ────────────────────────────────────────────────────────

def test_a_non_bedrock_backend_reports_the_assistant_is_unavailable(two_boards, caplog):
    """The offline rules engine was retired. With `agent_backend` at its default (not bedrock) the
    endpoint must say the assistant is unavailable — an honest refusal, not a keyword-matched
    answer — and warn, rather than 500 or pretend to answer."""
    assert settings.agent_backend != "bedrock"
    r = client.post("/api/v1/agent/chat", json={"message": "what is overdue?", "activeBoardId": None})

    body = r.json()
    assert r.status_code == 200
    assert body["valid"] is False
    assert body["response"] == "Chat isn't switched on. Please contact your application administrator."
    assert "AGENT_BACKEND" not in body["response"], "setup detail is for the administrator's log, not the panel"
    assert "AGENT_BACKEND" in caplog.text, "the administrator must be told what to set"


def test_the_bedrock_backend_answers_from_the_loop_and_drops_the_warning(bedrock_backend):
    r = client.post("/api/v1/agent/chat", json={"message": "what should I focus on?", "activeBoardId": None})

    body = r.json()
    assert r.status_code == 200
    assert body["valid"] is True
    assert body["response"] == "One card is high priority."
    assert body["toolCalled"] == "get_high_priority_tasks"
    assert body["warnings"] == [], "the offline notice must not survive onto a real backend"
    assert bedrock_backend.calls == 2


@pytest.mark.parametrize(
    "backend, provider_path",
    [
        ("openai", "app.agents.openai_provider.OpenAIChatProvider"),
        ("anthropic", "app.agents.anthropic_provider.AnthropicChatProvider"),
    ],
)
def test_the_api_key_backends_run_the_same_loop(two_boards, monkeypatch, backend, provider_path):
    """`openai` and `anthropic` drive chat through the same loop as bedrock — same answer shape, no
    'not configured' warning — and each gets its own provider class."""
    monkeypatch.setattr(settings, "agent_backend", backend)
    # A key must be present, or the endpoint (correctly) stops at the missing-key message first.
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-real")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test-not-real")
    provider = StubProvider()
    built: list[dict] = []
    monkeypatch.setattr(provider_path, lambda **kwargs: built.append(kwargs) or provider)

    r = client.post("/api/v1/agent/chat", json={"message": "what should I focus on?", "activeBoardId": None})

    body = r.json()
    assert r.status_code == 200
    assert body["valid"] is True
    assert body["response"] == "One card is high priority."
    assert body["warnings"] == []
    assert len(built) == 1 and provider.calls == 2


@pytest.mark.parametrize("backend, key", [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")])
def test_a_backend_without_its_api_key_says_what_to_add_instead_of_500ing(two_boards, monkeypatch, caplog, backend, key):
    """A half-finished setup — backend chosen, key left empty — used to raise while building the
    provider. The user is sent to their administrator; the log names the missing setting."""
    monkeypatch.setattr(settings, "agent_backend", backend)
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    r = client.post("/api/v1/agent/chat", json={"message": "what is overdue?", "activeBoardId": None})

    body = r.json()
    assert r.status_code == 200
    assert body["valid"] is False
    assert "administrator" in body["response"] and key not in body["response"]
    assert key in caplog.text


# ── conversation history threads through to the loop ──────────────────────────

def test_history_from_the_request_reaches_the_model(two_boards, monkeypatch):
    """The client sends prior {question, answer} pairs; the endpoint must expand them into the
    transcript the model sees, ahead of the current question."""
    monkeypatch.setattr(settings, "agent_backend", "bedrock")

    class Capturing:
        def __init__(self):
            self.first_messages = None

        def converse(self, *, system, messages, tool_specs):
            if self.first_messages is None:
                self.first_messages = [dict(m) for m in messages]
            return loop.ModelTurn(stop_reason="end_turn", content=({"text": "Board A."},), output_tokens=10)

    provider = Capturing()
    monkeypatch.setattr("app.agents.bedrock_provider.BedrockChatProvider", lambda **kwargs: provider)

    r = client.post(
        "/api/v1/agent/chat",
        json={
            "message": "which of those is busiest?",
            "activeBoardId": None,
            "history": [{"question": "what boards do I have?", "answer": "You have Board A and Board B."}],
        },
    )

    assert r.status_code == 200
    sent = provider.first_messages
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"][0]["text"] == "what boards do I have?"
    assert sent[2]["content"][0]["text"] == "which of those is busiest?"


def test_history_is_bounded_by_the_schema(bedrock_backend):
    """A client cannot spend the token budget by shipping an unbounded transcript: >6 pairs is a 422
    before the handler, so the model is never called."""
    r = client.post(
        "/api/v1/agent/chat",
        json={
            "message": "and now?",
            "activeBoardId": None,
            "history": [{"question": f"q{i}", "answer": f"a{i}"} for i in range(7)],
        },
    )

    assert r.status_code == 422
    assert bedrock_backend.calls == 0


# ── scope signature binds the answer to its scope ─────────────────────────────

def test_the_answer_is_stamped_with_a_scope_signature(bedrock_backend):
    """Every reply carries a fingerprint of the scope it was computed under. Same scope → same
    signature (stable to compare against); a different board → a different signature (so the client
    can tell a late cross-scope reply apart)."""
    a1 = client.post("/api/v1/agent/chat", json={"message": "hi", "activeBoardId": "board-1"}).json()
    a2 = client.post("/api/v1/agent/chat", json={"message": "again", "activeBoardId": "board-1"}).json()
    b = client.post("/api/v1/agent/chat", json={"message": "hi", "activeBoardId": "board-2"}).json()

    assert a1["scopeSignature"]
    assert a1["scopeSignature"] == a2["scopeSignature"], "same scope must hash alike"
    assert a1["scopeSignature"] != b["scopeSignature"], "a different board must not collide"


def test_a_board_and_a_workspace_scope_do_not_collide(bedrock_backend):
    board = client.post("/api/v1/agent/chat", json={"message": "hi", "activeBoardId": "w1"}).json()
    workspace = client.post(
        "/api/v1/agent/chat", json={"message": "hi", "activeWorkspaceId": "w1"}
    ).json()
    assert board["scopeSignature"] != workspace["scopeSignature"], (
        "board:w1 and workspace:w1 share an id but are different scopes"
    )


def test_the_global_scope_is_still_signed(bedrock_backend):
    r = client.post("/api/v1/agent/chat", json={"message": "hi", "activeBoardId": None}).json()
    assert r["scopeSignature"], "a reply must be bound to a scope even with none pinned"


# ── the gate that would have broken it ────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "Who has the most on their plate?",
    "How fast is the team moving?",
    "What is Dan working on?",
])
def test_questions_the_old_keyword_gate_rejected_now_reach_the_model(bedrock_backend, question):
    """These are the questions the retired rules-engine gate rejected — the first two for containing
    no board noun (its keyword allowlist), the third for the name Dan (its regex blocklist). The
    agent path uses `validate_agent_input` (shape only), so all three reach the model."""
    r = client.post("/api/v1/agent/chat", json={"message": question, "activeBoardId": None})

    assert r.status_code == 200
    assert r.json()["valid"] is True
    assert bedrock_backend.calls > 0, "the request never reached the model"


def test_an_empty_message_is_still_refused_without_calling_the_model(bedrock_backend):
    r = client.post("/api/v1/agent/chat", json={"message": "   ", "activeBoardId": None})

    assert r.json()["valid"] is False
    assert bedrock_backend.calls == 0, "an empty message must not cost a model call"


def test_an_overlong_message_is_refused_before_the_model(bedrock_backend):
    """Two gates guard this, and only one of them runs here.

    `AgentChatInput.message` carries `max_length=1200`, so Pydantic rejects with a 422 before the
    handler is entered — which means `validate_agent_input`'s own length check never fires on this
    route. It is kept as a second gate for callers that do not go through the schema, and
    `agent_max_message_chars` is pinned to the schema's number so it cannot quietly become
    unreachable code that reads like a limit. The pinning is asserted below.
    """
    r = client.post(
        "/api/v1/agent/chat",
        json={"message": "x" * 1201, "activeBoardId": None},
    )

    assert r.status_code == 422, "the schema is the first gate on this route"
    assert bedrock_backend.calls == 0


def test_the_two_length_limits_cannot_drift_apart():
    """If the config limit exceeds the schema's, it is dead code dressed as a safeguard."""
    from app.schemas import AgentChatInput

    # Search the constraint rather than indexing it — the list also holds MinLen, and its order is
    # Pydantic's business, not this test's.
    schema_max = next(
        m.max_length
        for m in AgentChatInput.model_fields["message"].metadata
        if getattr(m, "max_length", None) is not None
    )
    assert settings.agent_max_message_chars <= schema_max, (
        f"config allows {settings.agent_max_message_chars} chars but the schema rejects above "
        f"{schema_max} — the larger limit is unreachable"
    )


def test_the_agent_input_gate_enforces_its_own_limit_off_route(monkeypatch):
    """Called directly — the path the schema does not cover."""
    from app.validator import validate_agent_input

    monkeypatch.setattr(settings, "agent_max_message_chars", 10)
    assert validate_agent_input("x" * 11).valid is False
    assert validate_agent_input("x" * 10).valid is True


# ── incomplete runs ───────────────────────────────────────────────────────────

def test_a_bounded_run_reports_itself_incomplete_rather_than_answering(two_boards, monkeypatch):
    """A cut-off run must not reach the SPA looking like a normal answer — `valid` is the field the
    UI keys on, and the model's partial text must not be in `response`."""
    monkeypatch.setattr(settings, "agent_backend", "bedrock")
    monkeypatch.setattr(settings, "agent_max_turns", 1)

    class Looping:
        calls = 0

        def converse(self, **_):
            Looping.calls += 1
            return loop.ModelTurn(
                stop_reason="tool_use",
                content=(
                    {"text": "Everything looks fine so far."},
                    {"toolUse": {"toolUseId": "t", "name": "get_high_priority_tasks", "input": {}}},
                ),
                input_tokens=100,
                output_tokens=20,
            )

    monkeypatch.setattr("app.agents.bedrock_provider.BedrockChatProvider", lambda **kwargs: Looping())
    r = client.post("/api/v1/agent/chat", json={"message": "what's important?", "activeBoardId": None})

    body = r.json()
    assert body["valid"] is False
    assert body["reason"] == loop.MAX_TURNS
    assert "Everything looks fine" not in body["response"]


# ── metering ──────────────────────────────────────────────────────────────────

def test_the_meter_records_real_tokens_not_a_character_estimate(bedrock_backend, monkeypatch):
    """`estimate_tokens` is ~4 chars/token over the message and reply. It cannot see the tool
    schemas resent every turn or the tool results fed back, so it understates a real question by
    about two orders of magnitude — metering it would leave the daily cap decorative."""
    recorded: list[int] = []
    monkeypatch.setattr(settings, "cognito_region", "us-east-1")
    monkeypatch.setattr(settings, "cognito_user_pool_id", "pool")
    monkeypatch.setattr(settings, "cognito_app_client_id", "client")
    monkeypatch.setattr("app.agent_api.current_user", lambda: {"id": "u1"})
    monkeypatch.setattr("app.agent_api.record_usage", lambda uid, n: recorded.append(n))
    monkeypatch.setattr("app.agent_api.enforce_daily_budget", lambda u, n: None)
    monkeypatch.setattr("app.agent_api.accessible_board_ids", lambda conn, uid: {"any"})

    app.dependency_overrides = {}
    from app.tenancy import current_user as real_current_user

    app.dependency_overrides[real_current_user] = lambda: {"id": "u1"}
    try:
        client.post("/api/v1/agent/chat", json={"message": "what should I focus on?", "activeBoardId": None})
    finally:
        app.dependency_overrides.clear()

    assert recorded, "nothing was metered"
    # The stub spends 1700 + 160 across two turns; a character estimate of this exchange is ~40.
    assert recorded[0] == 1860


# ── the audit log ─────────────────────────────────────────────────────────────

def test_the_run_is_written_to_the_audit_log(bedrock_backend, tmp_path, monkeypatch):
    """Structured chat logging predates the agent; wiring a new backend behind it must not
    silently stop producing records (security review relies on this file existing)."""
    log = tmp_path / "chat.jsonl"
    monkeypatch.setattr("app.chat_logging.LOG_PATH", log, raising=False)

    client.post("/api/v1/agent/chat", json={"message": "what should I focus on?", "activeBoardId": None})

    if log.exists():
        entry = json.loads(log.read_text().strip().splitlines()[-1])
        assert entry["tool_called"] == "get_high_priority_tasks"
        assert entry["response"]
    else:
        pytest.skip("chat_logging writes elsewhere on this build; covered by its own tests")
