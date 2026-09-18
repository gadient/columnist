"""The recommendation contract.

The contract is the security boundary. Most of this file asserts what the model *cannot say* —
because "make invalid states unrepresentable" is only true if something checks that they stayed
unrepresentable. A field added back in a careless refactor would hand the model a decision it
cannot be held to, and nothing else in the system would notice.

**On matching by property name rather than by grepping the JSON.** A no-column check that
searches the serialized schema for the substring "column" fails — on a *docstring explaining that
there is no column field*. Prose that says "this doesn't exist" reads identically to the thing
existing, if you match text. The `prop_names` walk below exists because of that, and because a
blind key filter can delete `title`/`description` from the contract (they are JSON Schema keywords
*and* our field names). **Text isn't structure.**
"""
from __future__ import annotations

import json

import pytest
from app.note_imports.schema import (
    ModelAssignee,
    ModelDueDate,
    ModelRecommendation,
    ModelRecommendationSet,
    RecommendationView,
    openai_strict_json_schema,
)
from pydantic import ValidationError


def prop_names(schema: dict) -> set[str]:
    """Every property NAME the schema exposes — never prose that mentions one."""
    out: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, val in node.items():
                if key == "properties" and isinstance(val, dict):
                    out.update(val.keys())
                    for sub in val.values():
                        walk(sub)
                elif key in ("$defs", "definitions") and isinstance(val, dict):
                    for sub in val.values():
                        walk(sub)
                elif isinstance(val, (dict, list)):
                    walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return out


# --- what the model may not say -----------------------------------------------------------------

def test_model_cannot_name_a_column():
    """Columns are arbitrary (`Q3`, `Waiting on Legal`), so there is nothing to infer.

    Asking anyway produced a null column on 11 of 11 cards and blocked every one. The field is
    absent, not validated: the user picks a destination at import and code stamps it.
    """
    assert not {p for p in prop_names(ModelRecommendationSet.model_json_schema()) if "olumn" in p}


def test_model_cannot_name_a_member_id():
    """The model reports the text it read; code matches it to a roster.

    With no id field there is no id to hallucinate — owner matching stays a unit test, not an eval.
    """
    assert "memberId" not in prop_names(ModelRecommendationSet.model_json_schema())
    assert "memberId" not in ModelAssignee.model_fields


def test_model_cannot_approve_its_own_output():
    """Approval is a human action; there is no field for the model to set."""
    props = prop_names(ModelRecommendationSet.model_json_schema())
    assert not {p for p in props if "approv" in p.lower()}


@pytest.mark.parametrize("action", ["update_card", "delete_column", "mark_complete", "create_board"])
def test_only_create_card_is_representable(action):
    """Structurally, an injected "mark the cards done" has nowhere to go in the output.

    This is the line that makes the prompt's untrusted-data rule a backstop rather than the
    defence. Note the limit of what it proves: it stops the model doing something it shouldn't.
    It says nothing about the model doing *too little* — a note can talk the extractor
    into returning an empty list, and every structural defence sits it out.
    """
    with pytest.raises(ValidationError):
        ModelRecommendation.model_validate(
            {"action": action, "title": "x", "priority": {"value": "high", "provenance": "explicit"},
             "evidence": [{"excerpt": "x", "locator": "line:1"}], "reason": "x", "confidence": 1.0,
             "dueDate": None}
        )


def test_unknown_fields_are_rejected():
    """`extra="forbid"`. A model inventing `targetColumnId` or `memberId` fails validation rather
    than having it silently dropped — silence would make a re-added field invisible."""
    with pytest.raises(ValidationError):
        ModelRecommendation.model_validate(
            {"action": "create_card", "title": "x", "targetColumnId": "col_1",
             "priority": {"value": "high", "provenance": "explicit"},
             "evidence": [{"excerpt": "x", "locator": "line:1"}], "reason": "x", "confidence": 1.0,
             "dueDate": None}
        )


def test_a_recommendation_without_evidence_cannot_exist():
    """"Evidence before confidence" — a card with no source can't be reviewed."""
    with pytest.raises(ValidationError):
        ModelRecommendation.model_validate(
            {"action": "create_card", "title": "x",
             "priority": {"value": "high", "provenance": "explicit"},
             "evidence": [], "reason": "x", "confidence": 1.0, "dueDate": None}
        )


def test_empty_string_due_date_normalizes_to_null():
    """Some models emit "" instead of null for a dateless card (Nova Pro on Bedrock does). "" is
    normalized to null so it can't fail validation — and can't take the whole one-shot set down with
    it — while a genuinely malformed date (2026-02-31) still raises."""
    dd = ModelDueDate.model_validate(
        {"value": "", "rawText": None, "provenance": "default", "confidence": 0.0}
    )
    assert dd.value is None
    with pytest.raises(ValidationError):
        ModelDueDate.model_validate(
            {"value": "2026-02-31", "rawText": "end of Feb", "provenance": "inferred", "confidence": 0.5}
        )


@pytest.mark.parametrize("bad", [-0.1, 1.1, 2])
def test_confidence_out_of_range_is_rejected(bad):
    """Weak by design — the model self-reports 1.00 on nearly everything, so this
    bounds the number's range and nothing more. Confidence must never gate a safety decision."""
    with pytest.raises(ValidationError):
        ModelRecommendation.model_validate(
            {"action": "create_card", "title": "x",
             "priority": {"value": "high", "provenance": "explicit"},
             "evidence": [{"excerpt": "x", "locator": "line:1"}], "reason": "x",
             "confidence": bad, "dueDate": None}
        )


# --- what the model may say ---------------------------------------------------------------------

def test_a_minimal_valid_recommendation_round_trips():
    rec = ModelRecommendation.model_validate(
        {"action": "create_card", "title": "Send the invoice", "description": "",
         "assignees": [{"rawName": "@Alice", "confidence": 0.9}],
         "dueDate": {"value": "2026-07-17", "rawText": "by Friday", "provenance": "inferred",
                     "confidence": 0.8},
         "priority": {"value": "high", "rawText": "urgent", "provenance": "explicit",
                      "confidence": 0.95},
         "evidence": [{"excerpt": "@Alice, send the invoice by Friday. Urgent.",
                       "locator": "line:4"}],
         "reason": "Explicit assignment with a deadline.", "confidence": 0.95,
         "ambiguities": []}
    )
    assert rec.assignees[0].rawName == "@Alice"
    assert not hasattr(rec, "targetColumnId")


def test_an_empty_recommendation_set_is_valid():
    """A note with no action items must be answerable with "none" — the whole point of the
    negative corpus (`text3/4/6/7/10`).

    This is also, uncomfortably, the shape a polite injection exploits: it asks for exactly this
    legitimate answer. Any fix for it must keep this test passing.
    """
    assert ModelRecommendationSet.model_validate({"recommendations": []}).recommendations == []


# --- the server's half --------------------------------------------------------------------------

def test_resolved_view_requires_a_column():
    """Code stamps the user's import-form choice, so a card can never arrive unresolved on
    column — the inverse of the model contract, and the reason the blocker disappeared."""
    assert RecommendationView.model_fields["targetColumnId"].is_required()


# --- provider projection ------------------------------------------------------------------------

def test_openai_projection_exposes_the_same_fields_as_the_contract():
    """The contract is provider-agnostic; a provider's schema is a projection of it, never a
    second source of truth.

    This caught a real bug: the projection stripped JSON Schema annotation keywords to satisfy
    OpenAI's strict mode, and `title`/`description` are *both* keywords and field names of ours —
    so a blind filter deleted them and the model was never asked for a title. Only a round-trip
    against the live API found it. This test is that round-trip, for free.
    """
    assert prop_names(openai_strict_json_schema()) == prop_names(
        ModelRecommendationSet.model_json_schema()
    )


def test_openai_projection_is_valid_json():
    json.dumps(openai_strict_json_schema())


# --- Bedrock projections: tool keeps constraints, json_schema strips them -----------------------

_STRICT_BANNED = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "minItems", "maxItems", "pattern", "format",
}


def _all_keys(node) -> set[str]:
    keys: set[str] = set()
    if isinstance(node, dict):
        # Skip the *names* under properties/$defs — they are field names, not schema keywords.
        for key, val in node.items():
            keys.add(key)
            if key in ("properties", "$defs", "definitions") and isinstance(val, dict):
                for sub in val.values():
                    keys |= _all_keys(sub)
            else:
                keys |= _all_keys(val)
    elif isinstance(node, list):
        for item in node:
            keys |= _all_keys(item)
    return keys


def test_bedrock_json_schema_drops_value_constraints_and_inlines():
    """Bedrock structured outputs (`outputConfig.textFormat`) rejects validation-constraint keywords
    like `minimum`/`maximum` — the failure that surfaced scoring the real schema on json_schema. The
    strict projection must drop them (Pydantic still enforces them on the parsed output), inline all
    `$ref`s, and still expose the fields themselves (e.g. `confidence`)."""
    from app.note_imports.provider import bedrock_json_schema

    schema = bedrock_json_schema()
    keys = _all_keys(schema)
    assert not (_STRICT_BANNED & keys), f"strict schema still carries {_STRICT_BANNED & keys}"
    assert "$ref" not in keys and "$defs" not in keys and "definitions" not in keys  # fully inlined
    assert "confidence" in prop_names(schema)  # the constrained field is still asked for
    json.dumps(schema)  # serializable — it is sent as a JSON string


def test_bedrock_tool_schema_keeps_constraints():
    """The tool path is ordinary JSON Schema and must NOT be strained through the strict projection —
    keeping `minimum`/`maximum` is a useful generation hint and the tool inputSchema accepts them.
    This guards against a refactor collapsing the two Bedrock schemas into one."""
    from app.note_imports.provider import bedrock_tool_schema

    assert _STRICT_BANNED & _all_keys(bedrock_tool_schema())  # confidence's 0..1 bounds survive
