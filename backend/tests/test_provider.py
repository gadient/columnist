"""The extraction provider seam.

Two things are load-bearing and tested here:

1. **Validation is one choke point.** Every provider funnels untrusted model text through
   `parse_model_output`, so "the model returned garbage" is one behaviour with one error code. This
   is the security-critical step — where model output first becomes a typed object.
2. **The offline default proposes nothing, honestly.** No model configured → empty set, not a crash
   and not invented cards. The suite runs on this backend; real extraction is opt-in.

The OpenAI path is exercised against a *fake* `openai` module, so the risky code (timeout mapping,
provider-error mapping, parsing the response) is covered without a network call or an API key.
"""
from __future__ import annotations

import json
import sys
import types

import pytest
from app.note_imports.errors import ImportError as ImportRejection
from app.note_imports.provider import (
    AnthropicExtractionProvider,
    BedrockExtractionProvider,
    ExtractionProvider,
    OfflineExtractionProvider,
    OpenAIExtractionProvider,
    StreamedItem,
    bedrock_tool_schema,
    get_extraction_provider,
    parse_model_output,
    parse_one,
)
from app.note_imports.schema import (
    ModelEvidence,
    ModelPriority,
    ModelRecommendation,
    ModelRecommendationSet,
)


def code_of(exc_info) -> str:
    return exc_info.value.detail["code"]


def one_valid_set() -> ModelRecommendationSet:
    return ModelRecommendationSet(
        recommendations=[
            ModelRecommendation(
                action="create_card",
                title="Send the invoice",
                dueDate=None,
                priority=ModelPriority(value="medium", rawText=None, provenance="default", confidence=1.0),
                evidence=[ModelEvidence(excerpt="Bob to send the invoice.", locator="line:1")],
                reason="Explicit assignment.",
                confidence=0.9,
            )
        ]
    )


# --- the validation choke point (parse_model_output) --------------------------------------------

def test_valid_output_parses_to_the_contract():
    parsed = parse_model_output(one_valid_set().model_dump_json())
    assert len(parsed.recommendations) == 1
    assert parsed.recommendations[0].title == "Send the invoice"


def test_an_empty_list_is_valid_output():
    """A note with no action items is a correct empty answer, not a failure."""
    parsed = parse_model_output('{"recommendations": []}')
    assert parsed.recommendations == []


@pytest.mark.parametrize("raw", ["", "   ", "\n"])
def test_a_blank_response_is_rejected(raw):
    with pytest.raises(ImportRejection) as e:
        parse_model_output(raw)
    assert code_of(e) == "MODEL_OUTPUT_INVALID"


@pytest.mark.parametrize("raw", ["not json at all", '{"recommendations": [', '{"recommendations": {}}'])
def test_unparseable_output_is_rejected(raw):
    with pytest.raises(ImportRejection) as e:
        parse_model_output(raw)
    assert code_of(e) == "MODEL_OUTPUT_INVALID"


def test_schema_violating_output_is_rejected():
    """An extra field is exactly what an injected "also do X" would try to smuggle. `extra=forbid`
    means it fails validation here, not somewhere downstream that trusted it."""
    with pytest.raises(ImportRejection) as e:
        parse_model_output('{"recommendations": [], "alsoDelete": "column_5"}')
    assert code_of(e) == "MODEL_OUTPUT_INVALID"


def test_the_error_body_never_leaks_the_model_output():
    """Model content stays out of error bodies. The detail is a category, never the payload."""
    secret = "PROPRIETARY MEETING CONTENT THAT MUST NOT ECHO"
    with pytest.raises(ImportRejection) as e:
        parse_model_output(f'{{"recommendations": "{secret}"}}')
    body = e.value.detail
    assert secret not in str(body)


# --- offline: no model, no proposals ------------------------------------------------------------

def test_offline_proposes_nothing_regardless_of_input():
    provider = OfflineExtractionProvider()
    result = provider.extract(system_prompt="anything", user_message="anything at all")
    assert result.recommendations == []


def test_offline_satisfies_the_protocol():
    assert isinstance(OfflineExtractionProvider(), ExtractionProvider)


# --- selection (get_extraction_provider) --------------------------------------------------------

class _Settings:
    def __init__(self, backend, key="", model="gpt-4.1-mini", temperature=0.2,
                 bedrock_model_id="us.amazon.nova-2-lite-v1:0", bedrock_structured_output="tool",
                 bedrock_region="", bedrock_max_tokens=4096, bedrock_additional_request_fields="",
                 anthropic_api_key="", anthropic_model="claude-sonnet-4-5", anthropic_max_tokens=4096):
        self.agent_backend = backend
        self.openai_api_key = key
        self.openai_model = model
        self.anthropic_api_key = anthropic_api_key
        self.anthropic_model = anthropic_model
        self.anthropic_max_tokens = anthropic_max_tokens
        self.openai_temperature = temperature
        self.bedrock_model_id = bedrock_model_id
        self.bedrock_structured_output = bedrock_structured_output
        self.bedrock_region = bedrock_region
        self.bedrock_max_tokens = bedrock_max_tokens
        self.bedrock_additional_request_fields = bedrock_additional_request_fields


def test_offline_is_the_default():
    assert isinstance(get_extraction_provider(_Settings("offline")), OfflineExtractionProvider)


def test_an_unknown_backend_falls_back_to_offline_not_to_a_model():
    """Fail safe, not open: a typo'd backend proposes nothing rather than reaching for a model."""
    assert isinstance(get_extraction_provider(_Settings("gpt5-turbo-9000")), OfflineExtractionProvider)


def test_openai_backend_selects_the_openai_provider():
    provider = get_extraction_provider(_Settings("openai", key="sk-test-not-real"))
    assert isinstance(provider, OpenAIExtractionProvider)


def test_openai_backend_without_a_key_fails_loudly():
    """A server misconfiguration (backend set, key missing) should fail at selection, not 500
    mid-request with a vendor stack trace."""
    with pytest.raises(RuntimeError):
        get_extraction_provider(_Settings("openai", key=""))


def test_extraction_runs_at_the_configured_temperature():
    """Calibration depends on reproducibility: the same note must score the same run to run, so the
    request carries the configured (low) temperature rather than the provider default (~1.0)."""
    provider = get_extraction_provider(_Settings("openai", key="sk-test", temperature=0.15))
    assert provider._request_kwargs("sys", "user")["temperature"] == 0.15


def test_bedrock_backend_selects_the_bedrock_provider():
    assert isinstance(get_extraction_provider(_Settings("bedrock")), BedrockExtractionProvider)


def test_bedrock_defaults_to_the_forced_tool_strategy_for_nova():
    """Nova can't use native structured outputs (json_schema), so the default is a forced tool call.
    The request carries the model ID, the forced toolChoice, the inlined contract schema, and the
    reproducible (configured) temperature — the things the probe confirmed Nova needs."""
    provider = get_extraction_provider(_Settings("bedrock"))
    kwargs = provider._request_kwargs("sys", "user")
    assert kwargs["modelId"] == "us.amazon.nova-2-lite-v1:0"
    assert kwargs["toolConfig"]["toolChoice"] == {"tool": {"name": BedrockExtractionProvider._TOOL_NAME}}
    schema = kwargs["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert schema["properties"]["recommendations"]["type"] == "array"
    assert "outputConfig" not in kwargs
    assert kwargs["inferenceConfig"]["temperature"] == 0.2


def test_bedrock_json_schema_strategy_uses_output_config_not_a_tool():
    """When pointed at a model that supports native structured outputs (Claude/Mistral/…), the same
    provider switches mechanism by config alone — no tool, an outputConfig instead."""
    provider = get_extraction_provider(_Settings("bedrock", bedrock_structured_output="json_schema"))
    kwargs = provider._request_kwargs("sys", "user")
    assert "toolConfig" not in kwargs
    assert kwargs["outputConfig"]["textFormat"]["type"] == "json_schema"


def test_bedrock_rejects_an_unknown_strategy():
    with pytest.raises(RuntimeError):
        get_extraction_provider(_Settings("bedrock", bedrock_structured_output="magic"))


def test_bedrock_bad_additional_request_fields_fail_loudly():
    """A malformed additionalModelRequestFields JSON is a server misconfiguration — fail at
    selection, not mid-request."""
    with pytest.raises(RuntimeError):
        get_extraction_provider(_Settings("bedrock", bedrock_additional_request_fields="{not json"))


def test_bedrock_tool_schema_is_self_contained_and_closed():
    """Constrained decoders want an inlined schema (no $ref) with closed objects — see the projection."""
    schema = bedrock_tool_schema()
    assert "$defs" not in schema and "definitions" not in schema
    assert schema["additionalProperties"] is False
    item = schema["properties"]["recommendations"]["items"]
    assert item["additionalProperties"] is False
    assert "$ref" not in json.dumps(schema)


# --- the OpenAI network path, against a fake SDK ------------------------------------------------

def _install_fake_openai(monkeypatch, *, content=None, raise_kind=None, stream_deltas=None):
    """A stand-in `openai` module. Mirrors the real exception hierarchy the provider catches
    (APITimeoutError is a subclass of APIError) so the except-ordering is exercised for real. The
    fake raises from its *own* classes — the same ones the provider imports from this module — so
    there is no class-identity mismatch between what's raised and what's caught.

    With `stream=True`, `create` returns an iterator of chunks carrying `stream_deltas`; if
    `raise_kind` is also set it raises *after* the deltas, exercising a mid-stream failure."""
    mod = types.ModuleType("openai")

    class APIError(Exception):
        pass

    class APITimeoutError(APIError):
        pass

    def _chunk(delta):
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=delta))]
        )

    class _FakeClient:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=self)

        def create(self, **kwargs):
            if kwargs.get("stream"):
                def gen():
                    for d in stream_deltas or []:
                        yield _chunk(d)
                    if raise_kind == "timeout":
                        raise APITimeoutError("slow")
                    if raise_kind == "api":
                        raise APIError("rate limited")
                return gen()
            if raise_kind == "timeout":
                raise APITimeoutError("slow")
            if raise_kind == "api":
                raise APIError("rate limited")
            message = types.SimpleNamespace(content=content)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    mod.OpenAI = _FakeClient
    mod.APITimeoutError = APITimeoutError
    mod.APIError = APIError
    monkeypatch.setitem(sys.modules, "openai", mod)


def _provider() -> OpenAIExtractionProvider:
    return OpenAIExtractionProvider(api_key="sk-test-not-real", model="gpt-4.1-mini")


def test_openai_happy_path_returns_validated_recommendations(monkeypatch):
    _install_fake_openai(monkeypatch, content=one_valid_set().model_dump_json())
    result = _provider().extract(system_prompt="rules", user_message="<note>Bob to send the invoice.</note>")
    assert [r.title for r in result.recommendations] == ["Send the invoice"]


def test_openai_garbage_response_becomes_model_output_invalid(monkeypatch):
    _install_fake_openai(monkeypatch, content="I'm sorry, I can't do that.")
    with pytest.raises(ImportRejection) as e:
        _provider().extract(system_prompt="rules", user_message="note")
    assert code_of(e) == "MODEL_OUTPUT_INVALID"


def test_openai_timeout_becomes_model_timeout(monkeypatch):
    _install_fake_openai(monkeypatch, raise_kind="timeout")
    with pytest.raises(ImportRejection) as e:
        _provider().extract(system_prompt="rules", user_message="note")
    assert code_of(e) == "MODEL_TIMEOUT"


def test_openai_provider_error_becomes_model_output_invalid(monkeypatch):
    _install_fake_openai(monkeypatch, raise_kind="api")
    with pytest.raises(ImportRejection) as e:
        _provider().extract(system_prompt="rules", user_message="note")
    assert code_of(e) == "MODEL_OUTPUT_INVALID"


# --- the streaming path -------------------------------------------------------------------------

def two_card_set() -> ModelRecommendationSet:
    def card(title, excerpt):
        return ModelRecommendation(
            action="create_card",
            title=title,
            dueDate=None,
            priority=ModelPriority(value="medium", rawText=None, provenance="default", confidence=1.0),
            evidence=[ModelEvidence(excerpt=excerpt, locator="line:1")],
            reason="Explicit assignment.",
            confidence=0.9,
        )

    return ModelRecommendationSet(
        recommendations=[card("First card", "do the first thing"), card("Second card", "do the second")]
    )


def chunked(text: str, size: int = 8) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def test_parse_one_validates_a_single_element():
    rec = two_card_set().recommendations[0]
    item = parse_one(rec.model_dump_json())
    assert item.recommendation is not None
    assert item.recommendation.title == "First card"


def test_parse_one_turns_a_bad_element_into_a_partial_failure():
    """A malformed element must not raise — it becomes a counted partial failure so the good cards
    already streamed survive it."""
    assert parse_one('{"action": "create_card", "title": "no evidence"}') == StreamedItem(None)


def test_offline_streams_nothing():
    assert list(OfflineExtractionProvider().stream(system_prompt="x", user_message="y")) == []


def test_offline_stream_satisfies_the_protocol():
    assert isinstance(OfflineExtractionProvider(), ExtractionProvider)


def test_openai_streams_each_card_as_it_closes(monkeypatch):
    """The whole point of the feature: two cards arrive as two items, reassembled from token deltas
    that split across object boundaries."""
    payload = two_card_set().model_dump_json()
    _install_fake_openai(monkeypatch, stream_deltas=chunked(payload, size=7))
    items = list(_provider().stream(system_prompt="rules", user_message="note"))
    titles = [i.recommendation.title for i in items if i.recommendation]
    assert titles == ["First card", "Second card"]


def test_openai_stream_surfaces_a_malformed_card_as_a_partial_failure(monkeypatch):
    valid = two_card_set().recommendations[0].model_dump_json()
    payload = '{"recommendations": [' + valid + ', {"action": "create_card", "title": "broken"}]}'
    _install_fake_openai(monkeypatch, stream_deltas=chunked(payload, size=9))
    items = list(_provider().stream(system_prompt="rules", user_message="note"))
    assert len(items) == 2
    assert items[0].recommendation is not None and items[0].recommendation.title == "First card"
    assert items[1].recommendation is None  # counted, not dropped


def test_openai_stream_timeout_after_partial_output_still_raises(monkeypatch):
    """A failure mid-stream raises the mapped code even though items were already yielded — the
    endpoint reports what it got, then the error."""
    valid = two_card_set().recommendations[0].model_dump_json()
    partial = '{"recommendations": [' + valid + ", "  # array left open, then the stream dies
    _install_fake_openai(monkeypatch, stream_deltas=chunked(partial, size=9), raise_kind="timeout")
    stream = _provider().stream(system_prompt="rules", user_message="note")
    collected = []
    with pytest.raises(ImportRejection) as e:
        for item in stream:
            collected.append(item)
    assert code_of(e) == "MODEL_TIMEOUT"
    assert [i.recommendation.title for i in collected if i.recommendation] == ["First card"]


# --- the Anthropic path, against a fake SDK -----------------------------------------------------

def test_anthropic_backend_selects_the_anthropic_provider():
    provider = get_extraction_provider(_Settings("anthropic", anthropic_api_key="sk-ant-test-not-real"))
    assert isinstance(provider, AnthropicExtractionProvider)


def test_anthropic_backend_without_a_key_fails_loudly():
    with pytest.raises(RuntimeError):
        get_extraction_provider(_Settings("anthropic"))


def test_anthropic_forces_the_recommendation_tool_at_the_configured_temperature():
    """Same mechanism and schema as Bedrock's `tool` strategy: one forced tool over the inlined contract."""
    provider = get_extraction_provider(
        _Settings("anthropic", anthropic_api_key="sk-ant-test", temperature=0.15)
    )
    kwargs = provider._request_kwargs("sys", "user")
    assert kwargs["model"] == "claude-sonnet-4-5"
    assert kwargs["temperature"] == 0.15
    assert kwargs["tool_choice"] == {"type": "tool", "name": AnthropicExtractionProvider._TOOL_NAME}
    assert kwargs["tools"][0]["input_schema"] == bedrock_tool_schema()


def _install_fake_anthropic(monkeypatch, *, tool_input=None, text=None, raise_kind=None, stream_chunks=None):
    """A stand-in `anthropic` module mirroring the exception hierarchy the provider catches
    (APITimeoutError is an APIError). With `stream=True`, `create` yields `input_json_delta` events;
    if `raise_kind` is also set it raises after them, exercising a mid-stream failure."""
    mod = types.ModuleType("anthropic")

    class APIError(Exception):
        pass

    class APITimeoutError(APIError):
        pass

    def _raise():
        if raise_kind == "timeout":
            raise APITimeoutError("slow")
        if raise_kind == "api":
            raise APIError("overloaded")

    def _delta_event(chunk):
        return types.SimpleNamespace(
            type="content_block_delta",
            delta=types.SimpleNamespace(type="input_json_delta", partial_json=chunk),
        )

    class _FakeClient:
        def __init__(self, **kwargs):
            self.messages = self

        def create(self, **kwargs):
            if kwargs.get("stream"):
                def gen():
                    yield types.SimpleNamespace(type="message_start")
                    for chunk in stream_chunks or []:
                        yield _delta_event(chunk)
                    _raise()
                return gen()
            _raise()
            blocks = []
            if text is not None:
                blocks.append(types.SimpleNamespace(type="text", text=text))
            if tool_input is not None:
                blocks.append(types.SimpleNamespace(type="tool_use", id="t", name="record_recommendations", input=tool_input))
            return types.SimpleNamespace(content=blocks)

    mod.Anthropic = _FakeClient
    mod.APIError = APIError
    mod.APITimeoutError = APITimeoutError
    monkeypatch.setitem(sys.modules, "anthropic", mod)


def _anthropic_provider() -> AnthropicExtractionProvider:
    return AnthropicExtractionProvider(api_key="sk-ant-test-not-real", model="claude-sonnet-4-5")


def test_anthropic_happy_path_returns_validated_recommendations(monkeypatch):
    _install_fake_anthropic(monkeypatch, tool_input=json.loads(one_valid_set().model_dump_json()))
    result = _anthropic_provider().extract(system_prompt="rules", user_message="note")
    assert [r.title for r in result.recommendations] == ["Send the invoice"]


def test_anthropic_answer_without_the_tool_call_is_model_output_invalid(monkeypatch):
    _install_fake_anthropic(monkeypatch, text="I'd rather not.")
    with pytest.raises(ImportRejection) as e:
        _anthropic_provider().extract(system_prompt="rules", user_message="note")
    assert code_of(e) == "MODEL_OUTPUT_INVALID"


def test_anthropic_timeout_becomes_model_timeout(monkeypatch):
    _install_fake_anthropic(monkeypatch, raise_kind="timeout")
    with pytest.raises(ImportRejection) as e:
        _anthropic_provider().extract(system_prompt="rules", user_message="note")
    assert code_of(e) == "MODEL_TIMEOUT"


def test_anthropic_provider_error_becomes_model_output_invalid(monkeypatch):
    _install_fake_anthropic(monkeypatch, raise_kind="api")
    with pytest.raises(ImportRejection) as e:
        _anthropic_provider().extract(system_prompt="rules", user_message="note")
    assert code_of(e) == "MODEL_OUTPUT_INVALID"


def test_anthropic_streams_each_card_from_the_tool_input_deltas(monkeypatch):
    _install_fake_anthropic(monkeypatch, stream_chunks=chunked(two_card_set().model_dump_json(), size=7))
    items = list(_anthropic_provider().stream(system_prompt="rules", user_message="note"))
    assert [i.recommendation.title for i in items if i.recommendation] == ["First card", "Second card"]


def test_anthropic_stream_timeout_after_partial_output_still_raises(monkeypatch):
    valid = two_card_set().recommendations[0].model_dump_json()
    partial = '{"recommendations": [' + valid + ", "
    _install_fake_anthropic(monkeypatch, stream_chunks=chunked(partial, size=9), raise_kind="timeout")
    collected = []
    with pytest.raises(ImportRejection) as e:
        for item in _anthropic_provider().stream(system_prompt="rules", user_message="note"):
            collected.append(item)
    assert code_of(e) == "MODEL_TIMEOUT"
    assert [i.recommendation.title for i in collected if i.recommendation] == ["First card"]
