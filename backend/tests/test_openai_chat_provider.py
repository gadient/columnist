"""The chat `ChatProvider` over OpenAI — translation in both directions, not model behaviour.

No network and no SDK: `_client` is patched to a fake that records the exact kwargs and replays
canned Chat Completions responses. What is asserted is that the loop's Converse-shaped transcript
arrives at OpenAI intact, that OpenAI's reply comes back as the blocks the loop reads, and that the
token accounting means the same thing it does on Bedrock. Which tool a real model picks is measured
by the paid eval.
"""
from __future__ import annotations

import json
import types
from typing import Any

import pytest

from app.agents import loop
from app.agents.openai_provider import OpenAIChatProvider


def _response(*, content=None, tool_calls=(), finish="stop", prompt=100, completion=10, cached=0, refusal=None):
    calls = [
        types.SimpleNamespace(id=call_id, type="function", function=types.SimpleNamespace(name=name, arguments=args))
        for call_id, name, args in tool_calls
    ]
    message = types.SimpleNamespace(content=content, tool_calls=calls or None, refusal=refusal)
    usage = types.SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=types.SimpleNamespace(cached_tokens=cached),
    )
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message, finish_reason=finish)], usage=usage)


class _FakeClient:
    """Records every request and replays the canned responses in order."""

    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kwargs: Any):
        self.requests.append(kwargs)
        return self._responses.pop(0)


def _provider(*responses) -> tuple[OpenAIChatProvider, _FakeClient]:
    provider = OpenAIChatProvider(api_key="sk-test-not-real", model="gpt-4.1-mini", temperature=0.2)
    fake = _FakeClient(responses)
    provider._client = lambda: fake  # type: ignore[method-assign]
    return provider, fake


SCHEMA = {"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []}
TOOLS = [
    {"toolSpec": {"name": "get_high_priority_tasks", "description": "High priority.", "inputSchema": {"json": SCHEMA}}},
    {"cachePoint": {"type": "default"}},
]


# ── the request ─────────────────────────────────────────────────────────────────

def test_tool_specs_become_function_tools_and_the_cache_marker_is_dropped():
    provider, fake = _provider(_response(content="ok"))
    provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert fake.requests[0]["tools"] == [{
        "type": "function",
        "function": {"name": "get_high_priority_tasks", "description": "High priority.", "parameters": SCHEMA},
    }]


def test_tool_choice_stays_free_and_the_configured_model_and_temperature_are_sent():
    provider, fake = _provider(_response(content="ok"))
    provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    request = fake.requests[0]
    assert "tool_choice" not in request
    assert request["model"] == "gpt-4.1-mini"
    assert request["temperature"] == 0.2


def test_the_transcript_translates_with_tool_calls_matched_to_their_results():
    """The loop's shape: a question, an assistant turn that calls a tool, then the tool result.
    OpenAI needs the call on the assistant message and the result as a `tool` message with the same
    id — a mismatch is a 400 from the API."""
    messages = [
        {"role": "user", "content": [{"text": "what is urgent?"}]},
        {"role": "assistant", "content": [
            {"text": "Checking."},
            {"toolUse": {"toolUseId": "call_1", "name": "get_high_priority_tasks", "input": {"limit": 5}}},
        ]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "call_1", "content": [{"json": {"items": [1]}}]}},
        ]},
    ]
    provider, fake = _provider(_response(content="ok"))
    provider.converse(system="the rules", messages=messages, tool_specs=list(TOOLS))

    sent = fake.requests[0]["messages"]
    assert sent[0] == {"role": "system", "content": "the rules"}
    assert sent[1] == {"role": "user", "content": "what is urgent?"}
    assert sent[2]["role"] == "assistant" and sent[2]["content"] == "Checking."
    call = sent[2]["tool_calls"][0]
    assert call["id"] == "call_1" and call["function"]["name"] == "get_high_priority_tasks"
    assert json.loads(call["function"]["arguments"]) == {"limit": 5}
    assert sent[3]["role"] == "tool" and sent[3]["tool_call_id"] == "call_1"
    assert json.loads(sent[3]["content"]) == {"items": [1]}


# ── the response ────────────────────────────────────────────────────────────────

def test_a_tool_call_comes_back_as_a_tool_use_block():
    provider, _ = _provider(_response(tool_calls=[("call_9", "get_high_priority_tasks", '{"limit": 3}')], finish="tool_calls"))
    turn = provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert turn.stop_reason == "tool_use"
    assert turn.tool_uses == (loop.ToolUse(id="call_9", name="get_high_priority_tasks", input={"limit": 3}),)


def test_a_plain_answer_is_an_end_turn_with_its_text():
    provider, _ = _provider(_response(content="Nothing is overdue."))
    turn = provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert turn.stop_reason == "end_turn"
    assert turn.text == "Nothing is overdue."
    assert turn.tool_uses == ()


@pytest.mark.parametrize("kwargs", [{"finish": "content_filter"}, {"refusal": "I can't help with that."}])
def test_a_filtered_or_refused_turn_maps_to_the_guardrail_stop(kwargs):
    """The loop withholds a guardrail turn's text; a filtered or refused OpenAI turn must get the
    same treatment rather than be presented as an answer."""
    provider, _ = _provider(_response(content="partial", **kwargs))
    turn = provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert turn.stop_reason in loop.GUARDRAIL_STOP_REASONS


def test_cached_prompt_tokens_are_split_out_of_the_input_count():
    """OpenAI counts cached tokens inside `prompt_tokens`; the loop expects them separately, so a
    warm turn must not be metered as if nothing were cached."""
    provider, _ = _provider(_response(content="ok", prompt=4200, completion=30, cached=4000))
    turn = provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert turn.input_tokens == 200
    assert turn.cache_read_tokens == 4000
    assert turn.cache_write_tokens == 0
    assert turn.output_tokens == 30


def test_malformed_tool_arguments_raise_rather_than_guess():
    provider, _ = _provider(_response(tool_calls=[("c", "get_high_priority_tasks", "{not json")], finish="tool_calls"))
    with pytest.raises(ValueError):
        provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))


def test_a_missing_key_fails_at_construction():
    with pytest.raises(RuntimeError):
        OpenAIChatProvider(api_key="", model="gpt-4.1-mini")


# ── inside the real loop ────────────────────────────────────────────────────────

def test_the_loop_runs_a_tool_and_feeds_its_result_back_under_the_same_id(two_boards):
    provider, fake = _provider(
        _response(tool_calls=[("call_1", "get_high_priority_tasks", "{}")], finish="tool_calls"),
        _response(content="One card is high priority."),
    )
    out = loop.run_agent("what is urgent?", provider=provider, active_board_id=None)

    assert out.complete and out.text == "One card is high priority."
    assert [c.name for c in out.tool_calls] == ["get_high_priority_tasks"]
    second = fake.requests[1]["messages"]
    tool_messages = [m for m in second if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["call_1"]


def test_malformed_arguments_inside_the_loop_fail_closed(two_boards):
    provider, _ = _provider(_response(tool_calls=[("c", "get_high_priority_tasks", "[1, 2]")], finish="tool_calls"))
    out = loop.run_agent("what is urgent?", provider=provider, active_board_id=None)
    assert out.stopped_because == loop.PROVIDER_ERROR
    assert out.complete is False


def test_a_rejected_api_key_becomes_a_provider_auth_error():
    import openai

    class _Rejected(openai.AuthenticationError):
        def __init__(self):
            Exception.__init__(self, "Incorrect API key provided")

    provider, fake = _provider()

    def _raise(**kwargs):
        raise _Rejected()

    fake.create = _raise
    with pytest.raises(loop.ProviderAuthError):
        provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
