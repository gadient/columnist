"""`ChatProvider` over Amazon Bedrock's Converse API — the real model behind the agent loop.

Sibling to `note_imports/provider.py`'s `BedrockExtractionProvider`, and deliberately not shared
with it: that one forces a *single* tool call to shape one JSON answer, this one offers a menu and
lets the model choose and iterate. They differ in the part that matters (`toolChoice`), so folding
them together would mean a flag that changes the whole meaning of the call.

Model-agnostic by construction: Converse normalises the request across Nova, Claude, Mistral and
the rest, so switching models is `bedrock_model_id`, not a code change. What does *not* transfer
across models is tool-selection quality — which tool a model picks for a given question is a
per-model property, measured by `scripts/agent-tool-eval.py`, exactly as extraction quality is
measured per-model by the corpus.

Used by `agent_api` when `agent_backend="bedrock"`.
"""
from __future__ import annotations

from typing import Any

from .loop import ModelTurn, ProviderAuthError

#: Converse error codes that mean the credentials or permissions are wrong — not worth a retry.
_AUTH_ERROR_CODES = frozenset({
    "AccessDeniedException",
    "UnrecognizedClientException",
    "ExpiredTokenException",
    "InvalidSignatureException",
})

#: botocore exceptions raised when no usable credentials could be resolved at all.
_CREDENTIAL_ERRORS = (
    "NoCredentialsError",
    "PartialCredentialsError",
    "TokenRetrievalError",
    "SSOTokenLoadError",
    "UnauthorizedSSOTokenError",
    "CredentialRetrievalError",
)


class BedrockChatProvider:
    """One Converse call per `converse()`. The loop owns the turn count, not this class."""

    _READ_TIMEOUT = 60.0
    _CONNECT_TIMEOUT = 10.0

    def __init__(
        self,
        *,
        region: str | None,
        model_id: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        guardrail_id: str = "",
        guardrail_version: str = "",
    ) -> None:
        self._region = region or None
        self._model_id = model_id
        self._temperature = temperature
        self._max_tokens = max_tokens
        # Bedrock Guardrails is an AWS-side resource that must be created in the account first;
        # unset (the default) simply omits the config. **Wired but not exercised against a live
        # guardrail** — treat the first run with one configured as the real test.
        # It is an addition to the system prompt, not a replacement: the prompt covers the
        # domain-specific rules (tool output is data, do not answer a nearby question) that a
        # generic content filter has no opinion about.
        self._guardrail_id = guardrail_id.strip()
        self._guardrail_version = guardrail_version.strip() or "DRAFT"

    def _client(self):
        # Lazy, so boto3 stays out of module import and the offline backend never loads it.
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

    def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]],
    ) -> ModelTurn:
        # A `cachePoint` marks the end of a cacheable prefix. The system prompt (~1.8k tokens) and
        # the tool schemas (~2.5k) are byte-identical on every turn of every question, so Bedrock
        # stores them once and bills reuse at ~10% for the 5-min TTL. Everything AFTER these markers
        # — the history, the question, the tool scaffolding — changes per turn and stays uncached.
        # Two checkpoints (system, tools) is well under Claude's limit of four; each comfortably
        # clears the per-checkpoint minimum. Models without prompt caching (e.g. Nova) ignore the
        # markers, so this is safe to send unconditionally on the shipped Sonnet model.
        kwargs: dict[str, Any] = {
            "modelId": self._model_id,
            "system": [{"text": system}, {"cachePoint": {"type": "default"}}],
            "messages": messages,
            # no toolChoice: choosing is the behaviour tested
            "toolConfig": {"tools": [*tool_specs, {"cachePoint": {"type": "default"}}]},
            "inferenceConfig": {"temperature": self._temperature, "maxTokens": self._max_tokens},
        }
        if self._guardrail_id:
            kwargs["guardrailConfig"] = {
                "guardrailIdentifier": self._guardrail_id,
                "guardrailVersion": self._guardrail_version,
            }
        from botocore import exceptions as boto_errors

        credential_errors = tuple(getattr(boto_errors, name) for name in _CREDENTIAL_ERRORS if hasattr(boto_errors, name))
        try:
            resp = self._client().converse(**kwargs)
        except boto_errors.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _AUTH_ERROR_CODES:
                raise ProviderAuthError(f"Bedrock: {exc}") from exc
            raise
        except credential_errors as exc:
            raise ProviderAuthError(f"Bedrock: {exc}") from exc
        usage = resp.get("usage") or {}
        return ModelTurn(
            stop_reason=resp.get("stopReason", ""),
            content=tuple(resp["output"]["message"]["content"]),
            input_tokens=int(usage.get("inputTokens", 0)),
            output_tokens=int(usage.get("outputTokens", 0)),
            # Field names drift across boto3 versions (…InputTokens vs …InputTokenCount); read both
            # so the meter still sees cache activity if the SDK is bumped. Absent = 0 = no caching.
            cache_read_tokens=int(
                usage.get("cacheReadInputTokens", usage.get("cacheReadInputTokenCount", 0)) or 0
            ),
            cache_write_tokens=int(
                usage.get("cacheWriteInputTokens", usage.get("cacheWriteInputTokenCount", 0)) or 0
            ),
        )
