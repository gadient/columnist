"""The chat `ChatProvider` over the Anthropic Messages API — translation, caching and accounting.

No network and no API key: `_client` is patched to a fake that records the exact kwargs and replays
canned Messages API responses. This provider has never been run against the live API from this
repository, so these tests are the whole of its verification.
"""
from __future__ import annotations

import types
from typing import Any

import pytest

from app.agents import loop
from app.agents.anthropic_provider import AnthropicChatProvider


def _text(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool_use(block_id, name, tool_input):
    return types.SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


def _response(*blocks, stop="end_turn", input_tokens=100, output_tokens=10, cache_read=0, cache_write=0):
    usage = types.SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )
    return types.SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage)


class _FakeClient:
    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any):
        self.requests.append(kwargs)
        return self._responses.pop(0)


def _provider(*responses) -> tuple[AnthropicChatProvider, _FakeClient]:
    provider = AnthropicChatProvider(api_key="sk-ant-test-not-real", model="claude-sonnet-4-5", temperature=0.2)
    fake = _FakeClient(responses)
    provider._client = lambda: fake  # type: ignore[method-assign]
    return provider, fake


SCHEMA = {"type": "object", "properties": {}, "required": []}
TOOLS = [
    {"toolSpec": {"name": "get_board_overview", "description": "Boards.", "inputSchema": {"json": SCHEMA}}},
    {"toolSpec": {"name": "get_high_priority_tasks", "description": "High priority.", "inputSchema": {"json": SCHEMA}}},
    {"cachePoint": {"type": "default"}},
]


# ── the request ─────────────────────────────────────────────────────────────────

def test_the_system_prompt_is_a_cached_block():
    provider, fake = _provider(_response(_text("ok")))
    provider.converse(system="the rules", messages=[], tool_specs=list(TOOLS))
    assert fake.requests[0]["system"] == [{"type": "text", "text": "the rules", "cache_control": {"type": "ephemeral"}}]


def test_tools_translate_and_only_the_last_carries_the_cache_breakpoint():
    provider, fake = _provider(_response(_text("ok")))
    provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    tools = fake.requests[0]["tools"]
    assert [t["name"] for t in tools] == ["get_board_overview", "get_high_priority_tasks"]
    assert tools[0]["input_schema"] == SCHEMA and "cache_control" not in tools[0]
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}


def test_tool_choice_stays_free_and_the_configured_model_is_sent():
    provider, fake = _provider(_response(_text("ok")))
    provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert "tool_choice" not in fake.requests[0]
    assert fake.requests[0]["model"] == "claude-sonnet-4-5"
    assert fake.requests[0]["temperature"] == 0.2


def test_the_transcript_translates_block_for_block():
    messages = [
        {"role": "user", "content": [{"text": "what is urgent?"}]},
        {"role": "assistant", "content": [
            {"text": ""},
            {"toolUse": {"toolUseId": "tu_1", "name": "get_high_priority_tasks", "input": {}}},
        ]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "tu_1", "content": [{"json": {"error": "boom"}}], "status": "error"}},
        ]},
    ]
    provider, fake = _provider(_response(_text("ok")))
    provider.converse(system="sys", messages=messages, tool_specs=list(TOOLS))

    sent = fake.requests[0]["messages"]
    assert sent[0] == {"role": "user", "content": [{"type": "text", "text": "what is urgent?"}]}
    # the empty text block is dropped — the API rejects empty text
    assert sent[1] == {"role": "assistant", "content": [
        {"type": "tool_use", "id": "tu_1", "name": "get_high_priority_tasks", "input": {}},
    ]}
    result = sent[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1"
    assert result["is_error"] is True


# ── the response ────────────────────────────────────────────────────────────────

def test_a_tool_use_block_comes_back_for_the_loop():
    provider, _ = _provider(_response(_text("Let me look."), _tool_use("tu_2", "get_board_overview", {}), stop="tool_use"))
    turn = provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert turn.stop_reason == "tool_use"
    assert turn.tool_uses == (loop.ToolUse(id="tu_2", name="get_board_overview", input={}),)
    assert turn.text == "Let me look."


def test_a_refusal_maps_to_the_guardrail_stop():
    provider, _ = _provider(_response(_text("partial"), stop="refusal"))
    turn = provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert turn.stop_reason in loop.GUARDRAIL_STOP_REASONS


def test_cache_accounting_is_read_back_unchanged():
    provider, _ = _provider(_response(_text("ok"), input_tokens=300, output_tokens=40, cache_read=4000, cache_write=0))
    turn = provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert (turn.input_tokens, turn.output_tokens, turn.cache_read_tokens, turn.cache_write_tokens) == (300, 40, 4000, 0)


def test_a_missing_key_fails_at_construction():
    with pytest.raises(RuntimeError):
        AnthropicChatProvider(api_key="", model="claude-sonnet-4-5")


# ── inside the real loop ────────────────────────────────────────────────────────

def test_the_loop_runs_a_tool_and_feeds_its_result_back_under_the_same_id(two_boards):
    provider, fake = _provider(
        _response(_tool_use("tu_1", "get_high_priority_tasks", {}), stop="tool_use"),
        _response(_text("One card is high priority.")),
    )
    out = loop.run_agent("what is urgent?", provider=provider, active_board_id=None)

    assert out.complete and out.text == "One card is high priority."
    last = fake.requests[1]["messages"][-1]
    assert last["role"] == "user"
    assert [b["tool_use_id"] for b in last["content"]] == ["tu_1"]


def test_a_rejected_api_key_becomes_a_provider_auth_error():
    import anthropic

    class _Rejected(anthropic.AuthenticationError):
        def __init__(self):
            Exception.__init__(self, "invalid x-api-key")

    provider, fake = _provider()

    def _raise(**kwargs):
        raise _Rejected()

    fake.create = _raise
    with pytest.raises(loop.ProviderAuthError):
        provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
