"""Request/response models for note imports.

Kept in the feature package rather than the shared `app/schemas.py` because they are only ever
used by this module, and `schemas.py` is already large. The Pydantic v2 convention (`model_dump`,
not `.dict()`) and the camelCase-on-the-wire convention both match the rest of the API.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class EvidenceView(BaseModel):
    """A short, immutable excerpt from the note plus where it came from."""

    excerpt: str
    locator: str


class NoteImportSessionView(BaseModel):
    """One import session and its proposal."""

    id: str
    boardId: str
    boardVersion: int
    meetingDate: str
    inputMode: str  # 'paste' | 'txt' | 'docx'
    status: str  # 'ready' | 'failed'

    # Things present in the source but deliberately not analyzed — e.g. images, text boxes.
    # Shown near the proposal so the boundary is visible rather than assumed.
    warnings: list[str] = Field(default_factory=list)

    # Recommendations the model returned that we could not read. Surfaced rather than silently
    # dropped: the user chooses to continue or re-analyze.
    partialFailureCount: int = 0

    # Empty when the offline backend is selected (it proposes nothing). An empty list is also a
    # legitimate outcome for a note with no action items in it.
    recommendations: list[dict[str, Any]] = Field(default_factory=list)


class UpdateRecommendationRequest(BaseModel):
    """Re-target one recommendation to a different column during review. The column is the
    only field the model never proposes, so the reviewer picks it per card rather than once up front.

    `extra="forbid"`: the column is the only thing this endpoint may change — other edits ride with
    the approval snapshot at apply time, not here."""

    model_config = ConfigDict(extra="forbid")

    targetColumnId: str = Field(min_length=1)


class ApplyRecommendationRequest(BaseModel):
    """The exact values a human approved for one card. Sent as JSON, not the recommendation
    id alone: the user may have edited the title, owner, column, or date during review, so the
    server creates *these* values — not a re-reading of the stored model output.

    `extra="forbid"`: an approval carries only what may be approved. Nothing else rides along."""

    model_config = ConfigDict(extra="forbid")

    # Survives a lost response so a retry does not create a second card; the (session, rec)
    # unique index is the real guarantee, this is the client's half of it.
    idempotencyKey: str = Field(min_length=1)

    title: str = Field(min_length=1)
    description: str = ""
    targetColumnId: str = Field(min_length=1)
    assigneeMemberIds: list[str] = Field(default_factory=list)  # existing board members only
    priority: Literal["low", "medium", "high"] = "medium"
    dueDate: Optional[str] = None  # ISO YYYY-MM-DD, or null

    # Creating a board member is a separate, explicit approval captured in the same UI. Off by
    # default — a card is created unassigned before a member is created without being asked for.
    memberCreationApproved: bool = False
    newMemberName: Optional[str] = None


class ApplyResultView(BaseModel):
    """The outcome of one apply."""

    sessionId: str
    recommendationId: str
    createdCardId: Optional[str]
    createdMemberId: Optional[str] = None
    state: str
    replayed: bool = False  # true when a retry returned the original card rather than creating one
    # The board's new version after this apply. The client must adopt it before its next
    # snapshot write, or that write is rejected as stale. None on a replay — the client already has it.
    boardVersion: Optional[int] = None


class ApplyBatchRequest(BaseModel):
    """`Add the clean ones`. No list of recommendations rides along: the server chooses the
    unflagged set itself, so a flagged card cannot be smuggled into the batch."""

    model_config = ConfigDict(extra="forbid")

    idempotencyKey: str = Field(min_length=1)
    # The board version the client's view is based on. A mismatch fails the whole batch.
    expectedBoardVersion: Optional[int] = None


class ApplyBatchResultView(BaseModel):
    sessionId: str
    batchId: str
    createdCount: int
    createdCardIds: list[str]
    boardVersion: int


class KeptCard(BaseModel):
    """A card undo declined to remove, and why — never silently deleted."""

    recommendationId: str
    cardId: Optional[str]
    reason: str


class UndoBatchResultView(BaseModel):
    sessionId: str
    batchId: str
    removedCount: int
    keptCount: int
    kept: list[KeptCard] = Field(default_factory=list)
    boardVersion: int
