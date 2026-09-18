"""`ChatProvider` over OpenAI's Chat Completions API — the loop's model on `agent_backend="openai"`.

The loop speaks Converse's message shape (`loop.py`): `text`, `toolUse` and `toolResult` blocks.
This class translates at the edge, in both directions, so the loop, the tools, the bounds and the
tenant fence stay exactly as they are on Bedrock. Nothing about scope lives here — `active_board_id`
is injected by `dispatch()` below the model, on every backend alike.

What does not transfer is quality. Which tool a model picks and how it writes the answer are
per-model properties, measured by `scripts/agent-tool-eval.py` and `scripts/chat_contract_eval.py`;
a result on Claude Sonnet 4.5 says nothing about a GPT model.

Two accounting differences from Converse, both handled here so `AgentResult.tokens_used` means the
same thing on every backend:
- OpenAI counts cached prompt tokens *inside* `prompt_tokens`. The loop expects `input_tokens`
  exclusive of the cached prefix, so the cached part is split out into `cache_read_tokens`.
- OpenAI caches automatically and reports no cache writes, so `cache_write_tokens` is always 0.
"""
from __future__ import annotations

import json
from typing import Any

from .loop import ModelTurn, ProviderAuthError

#: OpenAI `finish_reason` → the Converse stop reason the loop understands. A content-filtered turn
#: maps to the guardrail stop, so its partial text is withheld exactly like a Bedrock intervention.
_STOP_REASONS = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "guardrail_intervened",
}


def to_openai_tools(tool_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converse `toolSpec`s → OpenAI function tools. Non-spec entries (a Converse `cachePoint`) have
    no OpenAI equivalent and are skipped."""
    tools = []
    for entry in tool_specs:
        spec = entry.get("toolSpec")
        if spec is None:
            continue
        tools.append({
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec.get("description", ""),
                "parameters": spec["inputSchema"]["json"],
            },
        })
    return tools


def _tool_result_text(result: dict[str, Any]) -> str:
    parts = []
    for block in result.get("content", []):
        if "json" in block:
            parts.append(json.dumps(block["json"]))
        elif "text" in block:
            parts.append(block["text"])
    return "\n".join(parts)


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Converse transcript → Chat Completions messages.

    An assistant turn's `toolUse` blocks become `tool_calls`; the following user turn's `toolResult`
    blocks become one `role: "tool"` message each, matched by id. OpenAI has no per-result error
    flag, so an error result travels as its JSON body, which already carries the `error` key."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        text = "".join(b["text"] for b in message["content"] if "text" in b)
        if message["role"] == "assistant":
            calls = [
                {
                    "id": b["toolUse"]["toolUseId"],
                    "type": "function",
                    "function": {
                        "name": b["toolUse"]["name"],
                        "arguments": json.dumps(b["toolUse"].get("input") or {}),
                    },
                }
                for b in message["content"]
                if "toolUse" in b
            ]
            turn: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                turn["tool_calls"] = calls
            out.append(turn)
            continue
        for block in message["content"]:
            if "toolResult" in block:
                result = block["toolResult"]
                out.append({
                    "role": "tool",
                    "tool_call_id": result["toolUseId"],
                    "content": _tool_result_text(result),
                })
        if text:
            out.append({"role": "user", "content": text})
    return out


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    """A tool call's arguments arrive as a JSON string the model wrote. Malformed or non-object
    arguments raise, which the loop turns into a fail-closed PROVIDER_ERROR — never a guess."""
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("tool call arguments are not a JSON object")
    return parsed


def to_model_turn(response: Any) -> ModelTurn:
    """A Chat Completions response → `ModelTurn` carrying Converse-shaped content blocks."""
    choice = response.choices[0]
    message = choice.message
    content: list[dict[str, Any]] = []
    if message.content:
        content.append({"text": message.content})
    for call in message.tool_calls or []:
        function = getattr(call, "function", None)
        if function is None:
            raise ValueError(f"unsupported tool call type: {getattr(call, 'type', None)!r}")
        content.append({
            "toolUse": {
                "toolUseId": call.id,
                "name": function.name,
                "input": _parse_arguments(function.arguments),
            }
        })

    # A structured refusal is the model declining on safety grounds, not a normal answer.
    if getattr(message, "refusal", None):
        stop_reason = "guardrail_intervened"
    else:
        stop_reason = _STOP_REASONS.get(choice.finish_reason or "", choice.finish_reason or "")

    usage = response.usage
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    details = getattr(usage, "prompt_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0)
    return ModelTurn(
        stop_reason=stop_reason,
        content=tuple(content),
        input_tokens=prompt_tokens - cached,
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        cache_read_tokens=cached,
        cache_write_tokens=0,
    )


class OpenAIChatProvider:
    """One Chat Completions call per `converse()`. The loop owns the turn count, not this class."""

    _TIMEOUT_SECONDS = 60.0

    def __init__(self, *, api_key: str, model: str, temperature: float = 0.2, max_tokens: int = 4096) -> None:
        if not api_key:
            # A server misconfiguration, not a model failure: fail at construction, loudly.
            raise RuntimeError("agent_backend='openai' but OPENAI_API_KEY is empty")
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    def _client(self):
        # Lazy, so the openai package stays out of module import on every other backend.
        from openai import OpenAI

        return OpenAI(api_key=self._api_key, timeout=self._TIMEOUT_SECONDS, max_retries=2)

    def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]],
    ) -> ModelTurn:
        from openai import AuthenticationError, PermissionDeniedError

        try:
            response = self._client().chat.completions.create(
                model=self._model,
                messages=to_openai_messages(system, messages),
                # no tool_choice: choosing is the behaviour the eval measures
                tools=to_openai_tools(tool_specs),
                temperature=self._temperature,
                max_completion_tokens=self._max_tokens,
            )
        except (AuthenticationError, PermissionDeniedError) as exc:
            raise ProviderAuthError(f"OpenAI: {exc}") from exc
        return to_model_turn(response)
