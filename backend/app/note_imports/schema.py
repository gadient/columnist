"""The recommendation contract.

Two model families, and the split between them *is* the security design:

  ``Model*``      what the model is allowed to say. Deliberately missing every field it isn't
                  allowed to decide — no member ids, no recommendation id, no approval flag, and
                  an ``action`` that can only take one value.
  ``Resolved*``   what the server persists and shows, after code has done the deciding.

IDs proposed by the model are not trusted as existing board IDs, and model output cannot mark
itself approved. The weak way to honour that is to validate what comes back. The way
taken here is to give it nowhere to put the lie:

  - an injected "update card 5" cannot be expressed: ``action`` is ``Literal["create_card"]``
  - a hallucinated member id cannot be expressed: ``ModelAssignee`` has no id field at all
  - self-approval cannot be expressed: no model type has an approval field
  - smuggled extra fields are rejected: ``extra="forbid"`` everywhere

Prompt injection fails because of this file, not because the prompt asked nicely.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Priority = Literal["low", "medium", "high"]

# A default must never claim to be an inference. `default` means "the note said nothing and
# we used the product default"; `inferred` means "the note supported this". Keeping them distinct
# is what makes the UI able to tell the truth about where a value came from.
Provenance = Literal["explicit", "inferred", "default"]

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

# Existing card limits. Enforced here, on our side, and it has to be: some
# providers enforce schema constraints, some ignore them, some reject the schema for containing
# them. A limit is only real where we check it.
MAX_TITLE = 200
MAX_DESCRIPTION = 5_000


# ---------------------------------------------------------------------------------------------
# What the model is allowed to say
# ---------------------------------------------------------------------------------------------


class ModelEvidence(BaseModel):
    """A verbatim excerpt and where it came from.

    Checked against the normalized note afterwards: excerpts must be locatable. A model
    that paraphrases instead of quoting fails that check — which is the point of requiring it.
    """

    model_config = ConfigDict(extra="forbid")

    excerpt: str = Field(min_length=1)
    locator: str = Field(min_length=1)  # "line:5" | "paragraph:12" | "table:1:row:3:cell:2"


class ModelAssignee(BaseModel):
    """A name the model saw in the note. **Deliberately not an identity.**

    There is no `memberId` here and there never should be. The model reports the text it read;
    matching that text to a board member is deterministic code, which is why owner matching is
    unit-tested rather than evaluated on a model. Adding an id field here would hand the
    model a decision it cannot be held to.
    """

    model_config = ConfigDict(extra="forbid")

    rawName: str = Field(min_length=1)
    confidence: Confidence


class ModelDueDate(BaseModel):
    """A date the model resolved, plus the phrase it resolved *from*.

    `rawText` is not decoration. The review UI shows "by Friday → 2026-07-17" side by side, so a
    human checks the model's arithmetic against the source before anything is created. That
    display is what makes it safe to let the model do the date maths at all.
    """

    model_config = ConfigDict(extra="forbid")

    value: Optional[str]  # ISO YYYY-MM-DD, or null when the note supports no date
    rawText: Optional[str]  # the source phrase, e.g. "by Friday"
    provenance: Provenance
    confidence: Confidence

    @field_validator("value")
    @classmethod
    def _real_calendar_date(cls, v: Optional[str]) -> Optional[str]:
        """Dates must be valid ISO calendar dates or null. Catches 2026-02-31.

        An empty string is normalized to null. "No date" is legitimately expressed two ways by
        different models — `null` and `""` — and treating `""` as null is a benign normalization.
        Without it, one model emitting `""` for a
        dateless card fails validation and, on the one-shot path, takes every other card down with
        it. A *non-empty* malformed date still raises — 2026-02-31 is a real error, "" is not."""
        if not v:  # None or ""
            return None
        try:
            return date.fromisoformat(v).isoformat()
        except ValueError as exc:
            raise ValueError(f"{v!r} is not a valid ISO calendar date") from exc


class ModelPriority(BaseModel):
    """Priority plus where it came from."""

    model_config = ConfigDict(extra="forbid")

    value: Priority
    rawText: Optional[str]  # the phrase supporting it, e.g. "urgent"; null when defaulted
    provenance: Provenance
    confidence: Confidence


class ModelRecommendation(BaseModel):
    """One proposed new card, as the model may express it.

    Note what is absent: no `id` (the server generates it), no `memberId`, no approval.
    """

    model_config = ConfigDict(extra="forbid")

    # Single-valued on purpose. This is the line that makes injection defence structural: a note
    # saying "mark the card complete" or "delete the column" has no representation in the output,
    # so it cannot survive validation no matter how persuasive the note is.
    action: Literal["create_card"]

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    description: str = Field(default="", max_length=MAX_DESCRIPTION)

    # No `targetColumnId` — the same reason there is no `memberId`. Columns are
    # arbitrary: `Q3`, `Waiting on Legal`, `Priya's stuff`. Notes never name one, and a column
    # title carries no reliable signal about where new work belongs, so the model can only guess —
    # and it guesses confidently — or, asked for one, returns a null column on every card, blocking
    # all of them. So the user picks one destination for the whole import (the board's leftmost
    # column when they pick none), code stamps it on every card, and the reviewer re-targets each
    # card during review (the column is never the model's to decide; it's the one field with no
    # suggestion to make).

    assignees: list[ModelAssignee] = Field(default_factory=list)
    dueDate: Optional[ModelDueDate]
    priority: ModelPriority

    # At least one excerpt, always. Evidence before confidence — a
    # recommendation with no source cannot be reviewed, so it cannot exist.
    evidence: list[ModelEvidence] = Field(min_length=1)

    reason: str = Field(min_length=1)
    confidence: Confidence
    ambiguities: list[str] = Field(default_factory=list)


class ModelRecommendationSet(BaseModel):
    """Exactly what we ask the model to return. An empty list is a valid answer."""

    model_config = ConfigDict(extra="forbid")

    recommendations: list[ModelRecommendation] = Field(default_factory=list)


# ---------------------------------------------------------------------------------------------
# What the server decides
# ---------------------------------------------------------------------------------------------

# `ambiguous` and `unmatched` both block Approve & create until the user resolves them —
# uncertainty stays visible rather than being guessed away.
AssigneeResolution = Literal["existing_member", "ambiguous", "unmatched"]


class ResolvedAssignee(BaseModel):
    """A `ModelAssignee` after code matched it against the board roster."""

    rawName: str
    resolution: AssigneeResolution
    memberId: Optional[str] = None  # set only when resolution == existing_member
    alternativeMemberIds: list[str] = Field(default_factory=list)  # shown when ambiguous
    # Near-misses offered alongside an `unmatched` result — a *suggestion*, never a match. Matching
    # stays strict: code must not decide that "morgan" is "Morgan Blake". But leaving the
    # reviewer staring at "not on this board" while that person is visibly on the board is a bad
    # experience, so we name the likely candidates and let the human confirm in one click.
    # Empty whenever resolution != unmatched.
    candidateMemberIds: list[str] = Field(default_factory=list)
    # A match that only succeeded after normalizing (spacing/punctuation) must show the match
    # rationale — so the user can see *why* we think "A. Smith" is Alice Smith.
    matchRationale: Optional[str] = None
    confidence: Confidence


class RecommendationView(BaseModel):
    """One recommendation as the review surface sees it."""

    id: str  # server-generated
    action: Literal["create_card"]

    title: str
    description: str
    # Not optional, and not the model's: the user chose one destination column for the whole import
    # (the board's leftmost by default) and code stamped it here. A card never arrives with
    # its column unresolved.
    targetColumnId: str
    assignees: list[ResolvedAssignee]
    dueDate: Optional[ModelDueDate]
    priority: ModelPriority

    evidence: list[ModelEvidence]  # immutable — user edits never touch these
    reason: str
    confidence: Confidence
    ambiguities: list[str]

    state: Literal["pending", "rejected", "created", "failed"] = "pending"

    # Why Approve & create is disabled, in words.
    # Empty means approvable. Computed server-side so the button's state and the server's opinion
    # cannot drift apart.
    blockedReasons: list[str] = Field(default_factory=list)

    # Set when an unmatched owner would require creating a board member. Requires its
    # own separate approval inside this recommendation — never creates a licensed PIU/SIU.
    requiresNewMemberNamed: Optional[str] = None

    createdCardId: Optional[str] = None  # set once applied


# ---------------------------------------------------------------------------------------------
# Strict structured-output projection
#
# ⚠️ **This section is provider-support, not contract, and does not belong here long term.**
# Everything above is the contract and knows nothing about any provider; this projects that contract
# into the STRICT subset of JSON Schema that some providers' structured-output modes demand — they
# reject validation-constraint keywords (min/max, patterns, formats) and require every object closed
# with all properties listed in `required`. It belongs in a shared provider-support module beside
# the adapters in provider.py.
#
# It is deliberately provider-NEUTRAL. Two consumers need exactly this subset: OpenAI's strict mode
# (`openai_strict_json_schema` below) and Amazon Bedrock's structured outputs
# (`outputConfig.textFormat` — see provider.py `bedrock_json_schema`). Bedrock's Converse *tool*
# inputSchema takes ordinary JSON Schema and keeps constraints (see `bedrock_tool_schema`), but
# structured outputs does NOT — it rejects e.g. `minimum`/`maximum` on a number with a
# ValidationException.
#
# The principle is the point: the provider schema is a **hint** that shapes generation; the Pydantic
# model is the **contract** that decides what we accept. Enforcement is on our side, so limits
# are communicated in the prompt and enforced here — never delegated to a provider's schema.
# ---------------------------------------------------------------------------------------------

# Keywords a strict structured-output mode rejects. They stay in the Pydantic models — that is where
# they are *enforced* — and are stripped on the way out to a strict provider schema.
_STRICT_UNSUPPORTED = frozenset(
    {
        "maxLength", "minLength", "minimum", "maximum",
        "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minItems", "maxItems",
        "pattern", "format", "default",
        # Pydantic emits our class docstrings as `description`, and strict paths (OpenAI, Bedrock
        # json_schema) drop them here. The forced-tool path (`bedrock_tool_schema`, also used by
        # the Anthropic provider) does NOT strip them, so on that path the `Model*` docstrings
        # reach the model: editing them is a prompt change and needs a PROMPT_VERSION bump.
        "title", "description",
    }
)


# Keys in these maps are *names we chose* (field names, model names) — never schema keywords. A
# blind filter cannot tell `{"title": {...}}`-the-field from `"title": "Rawname"`-the-annotation,
# and will happily delete a field called `title` or `description` from the contract.
_NAME_KEYED = frozenset({"properties", "$defs", "definitions"})


def _project_strict(node: object) -> object:
    """Project a JSON Schema into the strict structured-output subset (see the section note)."""
    if isinstance(node, dict):
        out: dict[str, object] = {}
        for key, value in node.items():
            if key in _STRICT_UNSUPPORTED:
                continue
            if key in _NAME_KEYED and isinstance(value, dict):
                # Recurse into the values; leave the keys exactly as they are.
                out[key] = {name: _project_strict(sub) for name, sub in value.items()}
            else:
                out[key] = _project_strict(value)
        if out.get("type") == "object" and isinstance(out.get("properties"), dict):
            # Strict mode demands every property be listed in `required` and forbids extras.
            # Pydantic leaves defaulted fields out of `required`, so we put them back: the model
            # must always emit the key, even if the value is empty.
            out["additionalProperties"] = False
            out["required"] = list(out["properties"].keys())
        return out
    if isinstance(node, list):
        return [_project_strict(x) for x in node]
    return node


def openai_strict_json_schema() -> dict:
    """`ModelRecommendationSet` projected into what OpenAI's strict mode will accept — the shared
    strict structured-output subset (see `_project_strict`).

    Lossy on purpose — see `_STRICT_UNSUPPORTED`. Nothing dropped here is a hole: the response is
    validated against the real Pydantic model regardless, so the constraints are enforced once, on
    our side, rather than twice. Bedrock's structured-outputs path needs the same subset; it has its
    own entry point (`bedrock_json_schema`) that inlines `$ref`s first.
    """
    return _project_strict(ModelRecommendationSet.model_json_schema())  # type: ignore[return-value]


def confidence_band(value: float) -> Literal["high", "medium", "low"]:
    """Display bands: High >= 0.80, Medium 0.60-0.79, Low < 0.60.

    Advisory only. Confidence never gates approval and never auto-creates anything — it is
    shown next to the evidence, not instead of it.
    """
    if value >= 0.80:
        return "high"
    if value >= 0.60:
        return "medium"
    return "low"
