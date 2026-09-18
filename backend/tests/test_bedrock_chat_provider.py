"""The chat `ChatProvider` over Bedrock Converse — request shaping, not model behaviour.

No network and no boto3 client: `_client` is patched to a fake that records the exact kwargs the
provider builds and returns a canned Converse response. What is asserted is the shape of the
request (where the prompt-cache checkpoints land, that tool choice stays free) and that the cache
accounting in the response is read back onto `ModelTurn`. Which tool a real model picks is a
different question, measured by the paid eval.
"""
from __future__ import annotations

from typing import Any

from app.agents.bedrock_provider import BedrockChatProvider


class _FakeClient:
    """Records the kwargs it was called with and replays one canned Converse response."""

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.kwargs: dict[str, Any] | None = None

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.kwargs = kwargs
        return self._response


def _canned(usage: dict[str, Any]) -> dict[str, Any]:
    return {
        "stopReason": "end_turn",
        "output": {"message": {"content": [{"text": "Nothing is overdue."}]}},
        "usage": usage,
    }


def _provider_with(response: dict[str, Any]) -> tuple[BedrockChatProvider, _FakeClient]:
    provider = BedrockChatProvider(
        region="us-east-1", model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    )
    fake = _FakeClient(response)
    provider._client = lambda: fake  # type: ignore[method-assign]
    return provider, fake


TOOLS = [{"toolSpec": {"name": "get_board_overview"}}, {"toolSpec": {"name": "get_high_priority_tasks"}}]


# ── the cache checkpoints ───────────────────────────────────────────────────────

def test_a_cache_point_closes_the_system_prefix():
    """The system prompt is a stable prefix reused every turn, so it ends with a cache checkpoint."""
    provider, fake = _provider_with(_canned({"inputTokens": 50, "outputTokens": 10}))
    provider.converse(system="you are an assistant", messages=[], tool_specs=list(TOOLS))

    system = fake.kwargs["system"]
    assert system[0] == {"text": "you are an assistant"}
    assert system[-1] == {"cachePoint": {"type": "default"}}


def test_a_cache_point_closes_the_tool_schemas_and_keeps_them_all():
    """The tool schemas are the larger stable prefix. The checkpoint is appended AFTER the real
    tools — dropping or reordering one would change tool selection, so the originals come first."""
    provider, fake = _provider_with(_canned({"inputTokens": 50, "outputTokens": 10}))
    provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))

    tools = fake.kwargs["toolConfig"]["tools"]
    assert tools[: len(TOOLS)] == TOOLS
    assert tools[-1] == {"cachePoint": {"type": "default"}}


def test_tool_choice_stays_free():
    """Prompt caching must not smuggle in a forced tool choice — which tool to call is the behaviour
    the eval measures, so the request offers the menu and lets the model choose."""
    provider, fake = _provider_with(_canned({"inputTokens": 50, "outputTokens": 10}))
    provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert "toolChoice" not in fake.kwargs["toolConfig"]


# ── reading the cache accounting back ───────────────────────────────────────────

def test_cache_tokens_are_read_onto_the_turn():
    """A warm turn reports its reused prefix as cacheReadInputTokens, separate from inputTokens."""
    provider, _ = _provider_with(
        _canned({"inputTokens": 200, "outputTokens": 30, "cacheReadInputTokens": 4000, "cacheWriteInputTokens": 0})
    )
    turn = provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert turn.input_tokens == 200
    assert turn.cache_read_tokens == 4000
    assert turn.cache_write_tokens == 0


def test_the_cold_turn_reports_a_cache_write():
    provider, _ = _provider_with(
        _canned({"inputTokens": 200, "outputTokens": 30, "cacheWriteInputTokens": 4000})
    )
    turn = provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert turn.cache_write_tokens == 4000
    assert turn.cache_read_tokens == 0


def test_the_count_suffixed_field_names_are_also_read():
    """boto3 has shipped both `cacheReadInputTokens` and `cacheReadInputTokenCount`. The provider
    reads either so a version bump doesn't silently zero the cache meter."""
    provider, _ = _provider_with(
        _canned({"inputTokens": 200, "outputTokens": 30, "cacheReadInputTokenCount": 4000})
    )
    turn = provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert turn.cache_read_tokens == 4000


def test_absent_cache_fields_mean_zero_not_a_crash():
    """A model without prompt caching returns no cache fields at all — that is zero, not an error."""
    provider, _ = _provider_with(_canned({"inputTokens": 200, "outputTokens": 30}))
    turn = provider.converse(system="sys", messages=[], tool_specs=list(TOOLS))
    assert turn.cache_read_tokens == 0 and turn.cache_write_tokens == 0


# ── credential failures ─────────────────────────────────────────────────────────

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from app.agents.loop import ProviderAuthError


class _RaisingClient:
    def __init__(self, exc):
        self._exc = exc

    def converse(self, **kwargs):
        raise self._exc


def _provider_raising(exc) -> BedrockChatProvider:
    provider = BedrockChatProvider(region="us-east-1", model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    provider._client = lambda: _RaisingClient(exc)  # type: ignore[method-assign]
    return provider


@pytest.mark.parametrize("code", ["AccessDeniedException", "UnrecognizedClientException", "ExpiredTokenException"])
def test_permission_and_credential_codes_become_a_provider_auth_error(code):
    exc = ClientError({"Error": {"Code": code, "Message": "no"}}, "Converse")
    with pytest.raises(ProviderAuthError):
        _provider_raising(exc).converse(system="sys", messages=[], tool_specs=list(TOOLS))


def test_no_credentials_at_all_is_a_provider_auth_error():
    with pytest.raises(ProviderAuthError):
        _provider_raising(NoCredentialsError()).converse(system="sys", messages=[], tool_specs=list(TOOLS))


def test_a_throttle_stays_an_ordinary_failure():
    """Throttling clears on retry, so it must keep the generic try-again path."""
    exc = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse")
    with pytest.raises(ClientError):
        _provider_raising(exc).converse(system="sys", messages=[], tool_specs=list(TOOLS))
