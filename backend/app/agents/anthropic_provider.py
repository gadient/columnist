"""`ChatProvider` over the Anthropic Messages API — the loop's model on `agent_backend="anthropic"`.

Claude directly, with an Anthropic API key and no AWS account. The loop speaks Converse's message
shape (`loop.py`); this class translates at the edge, in both directions, so the loop, the tools,
the bounds and the tenant fence are the same code on every backend.

The shipped default model is Claude Sonnet 4.5 (`anthropic_model`), the model the published chat
and extraction results were measured on through Bedrock. The same model through a different API
should behave the same, but that has not been measured here: this provider is covered by tests
against a fake client only.

Converse and the Messages API report usage the same way — `input_tokens` excludes the cached
prefix, with cache reads and writes reported separately — so the loop's accounting carries over
unchanged.
"""
from __future__ import annotations

import json
from typing import Any

from .loop import ModelTurn, ProviderAuthError

#: Messages API stop reasons are Converse's, except a safety refusal, which maps to the guardrail
#: stop so the loop withholds whatever text arrived.
_STOP_REASONS = {"refusal": "guardrail_intervened"}

_CACHE = {"type": "ephemeral"}


def to_anthropic_tools(tool_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converse `toolSpec`s → Messages API tools, with a cache breakpoint on the last one.

    The tool schemas are byte-identical on every turn of every question, so the breakpoint caches
    them together with the system prompt. Non-spec entries (a Converse `cachePoint`) are skipped."""
    tools = [
        {
            "name": entry["toolSpec"]["name"],
            "description": entry["toolSpec"].get("description", ""),
            "input_schema": entry["toolSpec"]["inputSchema"]["json"],
        }
        for entry in tool_specs
        if "toolSpec" in entry
    ]
    if tools:
        tools[-1] = {**tools[-1], "cache_control": _CACHE}
    return tools


def _tool_result_text(result: dict[str, Any]) -> str:
    parts = []
    for block in result.get("content", []):
        if "json" in block:
            parts.append(json.dumps(block["json"]))
        elif "text" in block:
            parts.append(block["text"])
    return "\n".join(parts)


def to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Converse transcript → Messages API messages. Block for block; empty text blocks are
    dropped because the API rejects them."""
    out = []
    for message in messages:
        blocks: list[dict[str, Any]] = []
        for block in message["content"]:
            if "text" in block:
                if block["text"]:
                    blocks.append({"type": "text", "text": block["text"]})
            elif "toolUse" in block:
                use = block["toolUse"]
                blocks.append({
                    "type": "tool_use",
                    "id": use["toolUseId"],
                    "name": use["name"],
                    "input": use.get("input") or {},
                })
            elif "toolResult" in block:
                result = block["toolResult"]
                blocks.append({
                    "type": "tool_result",
                    "tool_use_id": result["toolUseId"],
                    "content": _tool_result_text(result),
                    "is_error": result.get("status") == "error",
                })
        out.append({"role": message["role"], "content": blocks})
    return out


def to_model_turn(response: Any) -> ModelTurn:
    """A Messages API response → `ModelTurn` carrying Converse-shaped content blocks. Block types
    the loop has no use for are dropped; thinking is not enabled, so none are expected."""
    content: list[dict[str, Any]] = []
    for block in response.content:
        if block.type == "text":
            content.append({"text": block.text})
        elif block.type == "tool_use":
            content.append({
                "toolUse": {"toolUseId": block.id, "name": block.name, "input": dict(block.input or {})}
            })
    stop = response.stop_reason or ""
    usage = response.usage
    return ModelTurn(
        stop_reason=_STOP_REASONS.get(stop, stop),
        content=tuple(content),
        input_tokens=int(usage.input_tokens or 0),
        output_tokens=int(usage.output_tokens or 0),
        cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    )


class AnthropicChatProvider:
    """One Messages API call per `converse()`. The loop owns the turn count, not this class."""

    _TIMEOUT_SECONDS = 60.0

    def __init__(self, *, api_key: str, model: str, temperature: float = 0.2, max_tokens: int = 4096) -> None:
        if not api_key:
            # A server misconfiguration, not a model failure: fail at construction, loudly.
            raise RuntimeError("agent_backend='anthropic' but ANTHROPIC_API_KEY is empty")
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    def _client(self):
        # Lazy, so the anthropic package stays out of module import on every other backend.
        from anthropic import Anthropic

        return Anthropic(api_key=self._api_key, timeout=self._TIMEOUT_SECONDS, max_retries=2)

    def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]],
    ) -> ModelTurn:
        from anthropic import AuthenticationError, PermissionDeniedError

        try:
            response = self._client().messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                # Temperature is accepted by Claude Sonnet 4.5. Newer Claude models reject sampling
                # parameters; pointing `anthropic_model` at one needs this line removed.
                temperature=self._temperature,
                system=[{"type": "text", "text": system, "cache_control": _CACHE}],
                messages=to_anthropic_messages(messages),
                # no tool_choice: choosing is the behaviour the eval measures
                tools=to_anthropic_tools(tool_specs),
            )
        except (AuthenticationError, PermissionDeniedError) as exc:
            raise ProviderAuthError(f"Anthropic: {exc}") from exc
        return to_model_turn(response)
