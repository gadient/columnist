"""The extraction provider seam.

One narrow interface, `ExtractionProvider`, with the prompt on one side and schema-validated
recommendations on the other. The caller (`api.py`) builds the prompt — that stays provider-neutral
and is already tested — and each provider owns only what is genuinely provider-specific: the network
call and that vendor's way of asking for JSON.

Why this is *not* the chat agent's `LLMProvider.complete(prompt) -> str` (app/agents):
    That seam returns prose. This agent returns a *structured, schema-validated card set*. Forcing
    both through `complete() -> str` would push JSON parsing and Pydantic validation of untrusted
    model output into every caller — exactly the security-critical step that must live in one place.
    The two agents may share a Bedrock transport later; the interface stays per-agent.

The return type is always a validated `ModelRecommendationSet`. A provider that cannot produce one
raises a catalog error (`MODEL_OUTPUT_INVALID` / `MODEL_TIMEOUT`) — it never returns a half-read
object. Validation happens here, once, against the real Pydantic contract, whatever the provider.
The one exception is server misconfiguration — a missing API key, an unknown structured-output
strategy, an unparseable extra-fields blob — which raises `RuntimeError` instead, loudly, so an
operator mistake is never dressed up as the model misbehaving.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Iterator, Protocol, runtime_checkable

from .errors import ImportError as ImportRejection
from .schema import ModelRecommendation, ModelRecommendationSet
from .stream_parse import RecommendationStreamScanner


@dataclass(frozen=True)
class StreamedItem:
    """One item off the stream. `recommendation` is None when the element could not be validated —
    a partial failure the caller counts and surfaces, not something dropped silently."""

    recommendation: ModelRecommendation | None


@runtime_checkable
class ExtractionProvider(Protocol):
    """Prompt in, validated recommendations out.

    Two shapes of the same call. `extract` waits and returns the whole set — simple, and the
    non-streaming fallback. `stream` yields each recommendation as the model finishes writing it, so
    a ~60s wait becomes cards appearing one at a time. Implementations: offline, openai, anthropic,
    bedrock.
    """

    def extract(self, *, system_prompt: str, user_message: str) -> ModelRecommendationSet:
        ...

    def stream(self, *, system_prompt: str, user_message: str) -> Iterator[StreamedItem]:
        ...


def parse_one(element_json: str) -> StreamedItem:
    """Validate one streamed array element against the contract (the per-card twin of
    `parse_model_output`). A malformed element becomes a partial failure, not an exception that
    kills the whole stream — one bad card must not lose the good ones already shown."""
    try:
        return StreamedItem(ModelRecommendation.model_validate_json(element_json))
    except ValueError:
        return StreamedItem(None)


def parse_model_output(raw: str) -> ModelRecommendationSet:
    """Validate a raw model response against the contract, or reject it.

    This is the single choke point where untrusted model text becomes a typed object. Every provider
    funnels through it, so "the model returned something we can't use" is one behaviour with one
    error code, not something each backend re-decides. A blank response, truncated JSON, or output
    that violates the schema (an extra field, a bad enum, a missing evidence array) all land here as
    `MODEL_OUTPUT_INVALID` — the code the review UI turns into "try the analysis again".
    """
    text = (raw or "").strip()
    if not text:
        raise ImportRejection("MODEL_OUTPUT_INVALID", detail="empty model response")
    try:
        return ModelRecommendationSet.model_validate_json(text)
    except ValueError as exc:
        # Never surface the raw output or the exception chain to the client — model content stays
        # out of error bodies. The detail is a category, not the payload.
        raise ImportRejection("MODEL_OUTPUT_INVALID", detail="schema validation failed") from exc


class OfflineExtractionProvider:
    """No model, so no proposals — an empty set.

    The endpoints never reach it: with no model connected they refuse an analysis up front
    (`AI_NOT_CONFIGURED`), because an empty result would read as "no action items". It remains the
    fallback for an unrecognised backend and the deterministic, network-free provider the tests use.
    Real extraction needs `agent_backend` set to `openai`, `anthropic` or `bedrock`.
    """

    def extract(self, *, system_prompt: str, user_message: str) -> ModelRecommendationSet:
        return ModelRecommendationSet(recommendations=[])

    def stream(self, *, system_prompt: str, user_message: str) -> Iterator[StreamedItem]:
        return iter(())  # no model, nothing to stream


class OpenAIExtractionProvider:
    """The OpenAI backend. Lifts the proven `scripts/try-extract.py` call behind the seam.

    OpenAI's strict JSON-schema mode is why this is the convenient dev backend — the model is far
    more likely to return schema-valid output on the first try. That schema is *this* provider's
    concern (`openai_strict_json_schema`), never the contract's: Bedrock's Converse takes ordinary
    JSON Schema and must not inherit these restrictions. The measured configuration is Bedrock
    running Claude Sonnet 4.5; quality measured there does not transfer here.
    """

    # A generous ceiling: the profiler put 45 cards near ~60s, and the write time dominates. Long
    # enough that a real large note finishes; short enough that a wedged call fails as MODEL_TIMEOUT
    # rather than hanging the request open.
    _TIMEOUT_SECONDS = 90.0

    def __init__(self, *, api_key: str, model: str, temperature: float = 0.2) -> None:
        if not api_key:
            # A server misconfiguration, not a user error: the backend is set to openai but no key
            # is present. Fail loudly at selection rather than 500-ing mid-request with a vendor
            # stack trace.
            raise RuntimeError("agent_backend='openai' but OPENAI_API_KEY is empty")
        self._api_key = api_key
        self._model = model
        self._temperature = temperature

    def _request_kwargs(self, system_prompt: str, user_message: str) -> dict:
        # Lazy import inside the callers keeps the schema helper out of module import; the request
        # shape is identical for streaming and non-streaming, so it lives in one place.
        from .schema import openai_strict_json_schema

        return {
            "model": self._model,
            # Low temperature: extraction + confidence scoring must be reproducible, not creative
            # (see config.openai_temperature). The default ~1.0 is what makes the same note score
            # differently each run and cluster confidence high.
            "temperature": self._temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "recommendation_set",
                    "strict": True,
                    "schema": openai_strict_json_schema(),
                },
            },
        }

    def extract(self, *, system_prompt: str, user_message: str) -> ModelRecommendationSet:
        # Lazy import: the package is a dev-only dependency and must never be needed to run the app
        # (or the test suite) on the offline/bedrock backends.
        from openai import APITimeoutError, OpenAI
        from openai import APIError as OpenAIAPIError

        client = OpenAI(api_key=self._api_key, timeout=self._TIMEOUT_SECONDS)
        try:
            resp = client.chat.completions.create(**self._request_kwargs(system_prompt, user_message))
        except APITimeoutError as exc:
            raise ImportRejection("MODEL_TIMEOUT") from exc
        except OpenAIAPIError as exc:
            # A provider-side failure (rate limit, 5xx, refusal). Not the user's fault and not a
            # schema problem — surfaced as the generic "didn't return usable suggestions".
            raise ImportRejection("MODEL_OUTPUT_INVALID", detail="provider error") from exc

        return parse_model_output(resp.choices[0].message.content or "")

    def stream(self, *, system_prompt: str, user_message: str) -> Iterator[StreamedItem]:
        """Same call with `stream=True`; the token deltas feed the scanner, and each recommendation
        is yielded the moment its object closes — not after the whole array does.

        A failure mid-stream (timeout, provider error) is raised as the same catalog code the caller
        maps to an in-band error event. It can raise *after* some items were already yielded — that is the
        nature of streaming, and why the endpoint reports what it got before the error."""
        from openai import APITimeoutError, OpenAI
        from openai import APIError as OpenAIAPIError

        client = OpenAI(api_key=self._api_key, timeout=self._TIMEOUT_SECONDS)
        scanner = RecommendationStreamScanner()
        try:
            stream = client.chat.completions.create(
                stream=True, **self._request_kwargs(system_prompt, user_message)
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content or ""
                if not delta:
                    continue
                for element_json in scanner.feed(delta):
                    yield parse_one(element_json)
        except APITimeoutError as exc:
            raise ImportRejection("MODEL_TIMEOUT") from exc
        except OpenAIAPIError as exc:
            raise ImportRejection("MODEL_OUTPUT_INVALID", detail="provider error") from exc


# --------------------------------------------------------------------------------------------------
# Bedrock schema projection
#
# The two Converse structured-output paths take DIFFERENT schema dialects, so each gets its own
# projection. Both inline `$defs`/`$ref` — a constrained decoder is far more reliably fed a
# self-contained schema than one with pointers — and both close every object
# (`additionalProperties: false`). They differ on value constraints:
#   • tool  (toolConfig.inputSchema.json) — ordinary JSON Schema. Constraints (min/max, enums) are
#     KEPT as a generation hint. `bedrock_tool_schema()`.
#   • json_schema (outputConfig.textFormat) — a STRICT subset, like OpenAI's strict mode: it rejects
#     validation constraints (e.g. `minimum`/`maximum` on a number → ValidationException) and wants
#     every object's properties all listed in `required`. `bedrock_json_schema()` applies the shared
#     strict projection (schema.py `_project_strict`) after inlining.
# Either way, dropped or kept, the constraints are enforced once, on our side, in `parse_model_output`.
# --------------------------------------------------------------------------------------------------


def _deref(schema: dict) -> dict:
    """Inline all `$ref`s against `$defs`/`definitions` and drop the defs block. The contract has no
    self-references, so this terminates without cycle tracking."""
    defs = {**schema.get("definitions", {}), **schema.get("$defs", {})}

    def resolve(node: object) -> object:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                merged = resolve(dict(defs.get(name, {})))
                # Preserve any sibling keys placed alongside the $ref (e.g. a local description).
                for key, value in node.items():
                    if key != "$ref":
                        merged[key] = resolve(value)  # type: ignore[index]
                return merged
            return {k: resolve(v) for k, v in node.items() if k not in ("$defs", "definitions")}
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    out = resolve(schema)
    assert isinstance(out, dict)
    return out


def _close_objects(node: object) -> object:
    """Set `additionalProperties: false` on every object node (recursively)."""
    if isinstance(node, dict):
        out = {k: _close_objects(v) for k, v in node.items()}
        if out.get("type") == "object" and isinstance(out.get("properties"), dict):
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(node, list):
        return [_close_objects(x) for x in node]
    return node


def bedrock_tool_schema() -> dict:
    """`ModelRecommendationSet` as an inlined, closed JSON Schema for a Bedrock Converse *tool*.

    The tool inputSchema is ordinary JSON Schema, so value constraints are kept as a generation hint.
    """
    return _close_objects(_deref(ModelRecommendationSet.model_json_schema()))  # type: ignore[return-value]


def bedrock_json_schema() -> dict:
    """`ModelRecommendationSet` for the `json_schema` strategy (Converse structured outputs).

    Unlike the tool path, Bedrock structured outputs (`outputConfig.textFormat`) enforces a strict
    JSON-Schema subset — it rejects validation-constraint keywords such as `minimum`/`maximum`. So we
    inline `$ref`s and then apply the shared strict projection (schema.py `_project_strict`), which
    strips those keywords and marks every object closed + all-required. The constraints stay enforced
    by Pydantic on the parsed output, so nothing is lost by dropping them from the hint.
    """
    from .schema import _project_strict

    return _project_strict(_deref(ModelRecommendationSet.model_json_schema()))  # type: ignore[return-value]


def _bedrock_debug(exc: Exception) -> None:
    """Surface the true provider error to stderr when BEDROCK_DEBUG is set — the mapped catalog code
    deliberately hides it from the client, which is right in production but blind in dev."""
    if os.environ.get("BEDROCK_DEBUG"):
        print(f"[bedrock] underlying error: {type(exc).__name__}: {exc}", file=sys.stderr)


class BedrockExtractionProvider:
    """PRODUCTION backend. Amazon Bedrock via the Converse API (boto3) — model-agnostic by design.

    Switching models is a config change (`bedrock_model_id`), not a code change: Converse normalizes
    the request across Nova, Claude, Mistral, DeepSeek, … The only per-model variable is how we force
    schema-valid JSON, selected by `bedrock_structured_output` (confirmed per model with the probe,
    scripts/bedrock-probe.sh):

      "tool"        — a single forced tool call (toolConfig + toolChoice). Nova + Claude.
      "json_schema" — native structured outputs (outputConfig.textFormat). Claude 4.5/Mistral/etc.,
                      NOT Nova/Llama.

    Both funnel through the same `parse_model_output` / `parse_one` choke point as every other
    provider, so untrusted model text becomes a typed object in exactly one place. Quality measured
    on one model does not transfer to another — re-run corpus-eval per model.
    """

    _READ_TIMEOUT = 90.0     # mirrors the OpenAI provider: long enough for a big note, short enough
    _CONNECT_TIMEOUT = 10.0  # that a wedged call fails as MODEL_TIMEOUT rather than hanging.
    _TOOL_NAME = "record_recommendations"
    _OUTPUT_NAME = "recommendation_set"
    _DESCRIPTION = "Record the set of proposed cards extracted from the meeting note."

    def __init__(self, *, region: str | None, model_id: str, strategy: str,
                 temperature: float = 0.2, max_tokens: int = 4096,
                 additional_request_fields: dict | None = None) -> None:
        self._region = region or None
        self._model_id = model_id
        self._strategy = (strategy or "tool").strip().lower()
        if self._strategy not in ("tool", "json_schema"):
            raise RuntimeError(
                f"bedrock_structured_output must be 'tool' or 'json_schema', got {strategy!r}"
            )
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._extra_fields = additional_request_fields or None

    def _client(self):
        # Lazy: keeps boto3 out of module import so the offline/openai backends (and the test suite)
        # never need it loaded.
        import boto3
        from botocore.config import Config

        return boto3.client(
            "bedrock-runtime",
            region_name=self._region,
            config=Config(
                read_timeout=self._READ_TIMEOUT,
                connect_timeout=self._CONNECT_TIMEOUT,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    def _request_kwargs(self, system_prompt: str, user_message: str) -> dict:
        kwargs: dict = {
            "modelId": self._model_id,
            "system": [{"text": system_prompt}],
            "messages": [{"role": "user", "content": [{"text": user_message}]}],
            # Low temperature for reproducible extraction/scoring (config.openai_temperature); the
            # knob is provider-agnostic even though the field is named for OpenAI.
            "inferenceConfig": {"temperature": self._temperature, "maxTokens": self._max_tokens},
        }
        if self._strategy == "tool":
            kwargs["toolConfig"] = {
                "tools": [{
                    "toolSpec": {
                        "name": self._TOOL_NAME,
                        "description": self._DESCRIPTION,
                        # Ordinary JSON Schema — value constraints kept as a generation hint.
                        "inputSchema": {"json": bedrock_tool_schema()},
                    }
                }],
                # Force this exact tool — supported by Nova + Claude.
                "toolChoice": {"tool": {"name": self._TOOL_NAME}},
            }
        else:
            kwargs["outputConfig"] = {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "name": self._OUTPUT_NAME,
                            "description": self._DESCRIPTION,
                            # Strict subset — structured outputs rejects value constraints.
                            "schema": json.dumps(bedrock_json_schema()),
                        }
                    },
                }
            }
        if self._extra_fields:
            kwargs["additionalModelRequestFields"] = self._extra_fields
        return kwargs

    def _payload_text(self, response: dict) -> str:
        """The JSON string to validate — a tool's `input`, or a structured text block."""
        blocks = response["output"]["message"]["content"]
        if self._strategy == "tool":
            for block in blocks:
                if "toolUse" in block:
                    return json.dumps(block["toolUse"]["input"])
            return ""  # empty ⇒ parse_model_output raises MODEL_OUTPUT_INVALID
        return "".join(block["text"] for block in blocks if "text" in block)

    def _param_error(self, exc: Exception) -> RuntimeError:
        """A boto3-side request-shape rejection is a server misconfiguration, not a model failure —
        surface it loudly (like the OpenAI-missing-key case) instead of the generic MODEL_OUTPUT_INVALID.
        The common case: `json_schema` needs a boto3 new enough for `outputConfig` (Bedrock structured
        outputs, Feb 2026), and the installed one is older."""
        _bedrock_debug(exc)
        if self._strategy == "json_schema" and "outputConfig" in str(exc):
            return RuntimeError(
                "bedrock_structured_output='json_schema' needs a boto3 new enough to accept "
                "`outputConfig` (Bedrock structured outputs); the installed boto3 is too old. Use "
                "BEDROCK_STRUCTURED_OUTPUT=tool, or upgrade boto3."
            )
        return RuntimeError(f"Bedrock request rejected by boto3: {exc}")

    def extract(self, *, system_prompt: str, user_message: str) -> ModelRecommendationSet:
        from botocore.exceptions import (
            BotoCoreError,
            ClientError,
            ConnectTimeoutError,
            ParamValidationError,
            ReadTimeoutError,
        )

        client = self._client()
        try:
            resp = client.converse(**self._request_kwargs(system_prompt, user_message))
        except (ReadTimeoutError, ConnectTimeoutError) as exc:
            _bedrock_debug(exc)
            raise ImportRejection("MODEL_TIMEOUT") from exc
        except ParamValidationError as exc:
            raise self._param_error(exc)
        except (ClientError, BotoCoreError) as exc:
            # Provider-side failure (validation, access, throttle, 5xx). Not the user's fault and not
            # a schema problem — the generic "didn't return usable suggestions", with no raw detail.
            _bedrock_debug(exc)
            raise ImportRejection("MODEL_OUTPUT_INVALID", detail="provider error") from exc
        return parse_model_output(self._payload_text(resp))

    def stream(self, *, system_prompt: str, user_message: str) -> Iterator[StreamedItem]:
        """`converse_stream`; each recommendation is yielded as its object closes. For the tool
        strategy the model streams the tool `input` (partial JSON) rather than text — the scanner
        consumes either the same way. A mid-stream failure raises the same catalog code the endpoint
        maps to an in-band error event, possibly after some items were already yielded."""
        from botocore.exceptions import (
            BotoCoreError,
            ClientError,
            ConnectTimeoutError,
            ParamValidationError,
            ReadTimeoutError,
        )

        client = self._client()
        scanner = RecommendationStreamScanner()
        try:
            response = client.converse_stream(**self._request_kwargs(system_prompt, user_message))
            for event in response["stream"]:
                if "contentBlockDelta" not in event:
                    continue
                delta = event["contentBlockDelta"]["delta"]
                # json_schema → delta.text ; forced-tool → delta.toolUse.input (partial JSON string)
                chunk = delta.get("text")
                if chunk is None and "toolUse" in delta:
                    chunk = delta["toolUse"].get("input")
                if not chunk:
                    continue
                for element_json in scanner.feed(chunk):
                    yield parse_one(element_json)
        except (ReadTimeoutError, ConnectTimeoutError) as exc:
            _bedrock_debug(exc)
            raise ImportRejection("MODEL_TIMEOUT") from exc
        except ParamValidationError as exc:
            raise self._param_error(exc)
        except (ClientError, BotoCoreError) as exc:
            _bedrock_debug(exc)
            raise ImportRejection("MODEL_OUTPUT_INVALID", detail="provider error") from exc


class AnthropicExtractionProvider:
    """Claude through the Anthropic Messages API, with an API key and no AWS account.

    The same forced-tool mechanism as `BedrockExtractionProvider`'s `tool` strategy, over the same
    inlined schema (`bedrock_tool_schema`), so a Claude model receives the same request shape either
    way. Covered by tests against a fake SDK only; its corpus quality has not been measured.
    """

    _TIMEOUT_SECONDS = 90.0  # mirrors the other providers
    _TOOL_NAME = BedrockExtractionProvider._TOOL_NAME
    _DESCRIPTION = BedrockExtractionProvider._DESCRIPTION

    def __init__(self, *, api_key: str, model: str, temperature: float = 0.2, max_tokens: int = 4096) -> None:
        if not api_key:
            raise RuntimeError("agent_backend='anthropic' but ANTHROPIC_API_KEY is empty")
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    def _request_kwargs(self, system_prompt: str, user_message: str) -> dict:
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            # Reproducible extraction (config.openai_temperature). Accepted by Claude Sonnet 4.5;
            # newer Claude models reject sampling parameters.
            "temperature": self._temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
            "tools": [{
                "name": self._TOOL_NAME,
                "description": self._DESCRIPTION,
                "input_schema": bedrock_tool_schema(),
            }],
            "tool_choice": {"type": "tool", "name": self._TOOL_NAME},
        }

    def _client(self):
        # Lazy import: the SDK is only needed on this backend.
        from anthropic import Anthropic

        return Anthropic(api_key=self._api_key, timeout=self._TIMEOUT_SECONDS, max_retries=2)

    def extract(self, *, system_prompt: str, user_message: str) -> ModelRecommendationSet:
        from anthropic import APIError, APITimeoutError

        try:
            resp = self._client().messages.create(**self._request_kwargs(system_prompt, user_message))
        except APITimeoutError as exc:
            raise ImportRejection("MODEL_TIMEOUT") from exc
        except APIError as exc:
            raise ImportRejection("MODEL_OUTPUT_INVALID", detail="provider error") from exc
        for block in resp.content:
            if block.type == "tool_use":
                return parse_model_output(json.dumps(block.input))
        return parse_model_output("")  # no tool call ⇒ MODEL_OUTPUT_INVALID

    def stream(self, *, system_prompt: str, user_message: str) -> Iterator[StreamedItem]:
        """The forced tool's `input` streams as partial JSON (`input_json_delta`); the scanner yields
        each recommendation as its object closes. A mid-stream failure raises the mapped catalog
        code, possibly after some items were already yielded."""
        from anthropic import APIError, APITimeoutError

        scanner = RecommendationStreamScanner()
        try:
            events = self._client().messages.create(
                stream=True, **self._request_kwargs(system_prompt, user_message)
            )
            for event in events:
                if event.type != "content_block_delta" or event.delta.type != "input_json_delta":
                    continue
                chunk = event.delta.partial_json
                if not chunk:
                    continue
                for element_json in scanner.feed(chunk):
                    yield parse_one(element_json)
        except APITimeoutError as exc:
            raise ImportRejection("MODEL_TIMEOUT") from exc
        except APIError as exc:
            raise ImportRejection("MODEL_OUTPUT_INVALID", detail="provider error") from exc


def get_extraction_provider(settings) -> ExtractionProvider:
    """Select the provider named by `settings.agent_backend`.

    `offline` is the default and the test/CI backend. `openai` and `anthropic` call those APIs with
    an API key. `bedrock` runs Converse on AWS credentials (`BedrockExtractionProvider`; the model
    comes from `bedrock_model_id`). Any other value falls back to offline.
    """
    backend = (settings.agent_backend or "offline").strip().lower()
    if backend == "openai":
        return OpenAIExtractionProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            temperature=settings.openai_temperature,
        )
    if backend == "anthropic":
        return AnthropicExtractionProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            temperature=settings.openai_temperature,
            max_tokens=settings.anthropic_max_tokens,
        )
    if backend == "bedrock":
        extra = (settings.bedrock_additional_request_fields or "").strip()
        try:
            extra_fields = json.loads(extra) if extra else None
        except ValueError as exc:
            raise RuntimeError(
                "bedrock_additional_request_fields must be a JSON object string"
            ) from exc
        return BedrockExtractionProvider(
            region=settings.bedrock_region or None,
            model_id=settings.bedrock_model_id,
            strategy=settings.bedrock_structured_output,
            # Named for OpenAI, but the value is provider-agnostic (see config comment).
            temperature=settings.openai_temperature,
            max_tokens=settings.bedrock_max_tokens,
            additional_request_fields=extra_fields,
        )
    return OfflineExtractionProvider()
