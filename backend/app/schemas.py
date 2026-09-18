from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Priority = Literal["low", "medium", "high"]

# ── Input size limits ───────────────────────────────────────────────────────────
# Generous caps — well above anything a real board hits — so no legitimate user is rejected,
# but an authenticated caller can't submit an absurd snapshot/string/list that the snapshot
# write path would then delete-and-reinsert wholesale. Over-limit input is rejected as 422.
MAX_TINY = 64          # initials, colours, priority-ish short codes
MAX_SHORT = 200        # titles, names, labels, jira keys
MAX_DESC = 5_000       # descriptions
MAX_ID = 128           # ids
MAX_EMAIL = 320        # RFC-ish upper bound
MAX_CARDS = 2_000      # cards per board
MAX_COLUMNS = 100      # columns per board
MAX_MEMBERS = 200      # team members per board
MAX_ID_LIST = 2_000    # cardIds / columnOrder-style lists
MAX_SMALL_LIST = 100   # assignees, labels, blockedBy, dependsOn


class ProjectInfo(BaseModel):
    title: str = Field(max_length=MAX_SHORT)
    description: str = Field(default="", max_length=MAX_DESC)


class ColumnView(BaseModel):
    id: str = Field(max_length=MAX_ID)
    title: str = Field(max_length=MAX_SHORT)
    cardIds: list[str] = Field(default_factory=list, max_length=MAX_ID_LIST)


class CardView(BaseModel):
    id: str = Field(max_length=MAX_ID)
    title: str = Field(max_length=MAX_SHORT)
    description: str = Field(default="", max_length=MAX_DESC)
    priority: Priority = "medium"
    dueDate: str | None = Field(default=None, max_length=MAX_TINY)
    assignees: list[str] = Field(default_factory=list, max_length=MAX_SMALL_LIST)
    createdAt: str = Field(max_length=MAX_TINY)
    completed: bool = False
    storyPoints: int | None = None
    jiraKey: str | None = Field(default=None, max_length=MAX_SHORT)
    labels: list[str] = Field(default_factory=list, max_length=MAX_SMALL_LIST)
    blockedBy: list[str] = Field(default_factory=list, max_length=MAX_SMALL_LIST)
    dependsOn: list[str] = Field(default_factory=list, max_length=MAX_SMALL_LIST)


class TeamMemberView(BaseModel):
    id: str = Field(max_length=MAX_ID)
    name: str = Field(max_length=MAX_SHORT)
    initials: str = Field(max_length=MAX_TINY)
    color: str = Field(max_length=MAX_TINY)


class WorkspaceView(BaseModel):
    id: str
    name: str
    boardCount: int
    createdAt: str
    updatedAt: str


class CreateWorkspaceInput(BaseModel):
    name: str = Field(max_length=MAX_SHORT)


class RenameWorkspaceInput(BaseModel):
    name: str = Field(max_length=MAX_SHORT)


# ── Instance membership & invites ──
class InviteInput(BaseModel):
    email: str = Field(max_length=MAX_EMAIL)


class InstanceMemberView(BaseModel):
    id: str
    instanceId: str
    email: str
    userId: str | None = None
    role: Literal["piu", "siu"]
    status: Literal["invited", "active"]
    invitedBy: str | None = None
    createdAt: str


class TeamView(BaseModel):
    id: str
    name: str
    color: str
    jiraPrefix: str | None = None
    description: str = ""
    createdAt: str
    updatedAt: str


class CreateTeamInput(BaseModel):
    name: str = Field(max_length=MAX_SHORT)
    color: str = Field(default="bg-indigo-500", max_length=MAX_TINY)
    jiraPrefix: str | None = Field(default=None, max_length=MAX_TINY)
    description: str = Field(default="", max_length=MAX_DESC)


class UpdateTeamInput(BaseModel):
    name: str | None = Field(default=None, max_length=MAX_SHORT)
    color: str | None = Field(default=None, max_length=MAX_TINY)
    jiraPrefix: str | None = Field(default=None, max_length=MAX_TINY)
    description: str | None = Field(default=None, max_length=MAX_DESC)


class BoardView(BaseModel):
    id: str = Field(max_length=MAX_ID)
    workspaceId: str | None = Field(default=None, max_length=MAX_ID)
    teamId: str | None = Field(default=None, max_length=MAX_ID)
    projectInfo: ProjectInfo
    columns: dict[str, ColumnView] = Field(max_length=MAX_COLUMNS)
    cards: dict[str, CardView] = Field(max_length=MAX_CARDS)
    columnOrder: list[str] = Field(default_factory=list, max_length=MAX_COLUMNS)
    teamMembers: list[TeamMemberView] = Field(default_factory=list, max_length=MAX_MEMBERS)
    createdAt: str
    updatedAt: str
    # Server-authoritative, monotonic. Always set on the way out; optional here only because
    # this model also describes responses. A snapshot WRITE must declare it — see
    # `BoardSnapshotInput` below, which makes it required.
    version: int | None = None


class BoardSnapshotInput(BoardView):
    # `version` is optional on BoardView (responses, where it's always set), but a snapshot WRITE
    # must declare its base version — otherwise the staleness guard is silently skipped and
    # any authenticated client can clobber the board with a stale full-snapshot.
    # The real SPA already reads and declares it; this closes the bypass for a hand-rolled client.
    version: int = Field(ge=0)


class CreateBoardColumnInput(BaseModel):
    title: str = Field(max_length=MAX_SHORT)


class CreateBoardMemberInput(BaseModel):
    name: str = Field(max_length=MAX_SHORT)
    initials: str = Field(max_length=MAX_TINY)
    color: str = Field(max_length=MAX_TINY)


class CreateBoardInput(BaseModel):
    title: str = Field(max_length=MAX_SHORT)
    description: str = Field(default="", max_length=MAX_DESC)
    columns: list[CreateBoardColumnInput] = Field(max_length=MAX_COLUMNS)
    teamMembers: list[CreateBoardMemberInput] = Field(default_factory=list, max_length=MAX_MEMBERS)
    workspaceId: str | None = Field(default=None, max_length=MAX_ID)


class UpdateBoardInput(BaseModel):
    title: str | None = Field(default=None, max_length=MAX_SHORT)
    description: str | None = Field(default=None, max_length=MAX_DESC)


class CreateColumnInput(BaseModel):
    title: str = Field(max_length=MAX_SHORT)
    position: int | None = None


class UpdateColumnInput(BaseModel):
    title: str = Field(max_length=MAX_SHORT)


class ReorderColumnsInput(BaseModel):
    columnIds: list[str] = Field(max_length=MAX_COLUMNS)


class CreateCardInput(BaseModel):
    columnId: str = Field(max_length=MAX_ID)
    title: str = Field(max_length=MAX_SHORT)
    description: str = Field(default="", max_length=MAX_DESC)
    priority: Priority = "medium"
    dueDate: str | None = Field(default=None, max_length=MAX_TINY)
    assignees: list[str] = Field(default_factory=list, max_length=MAX_SMALL_LIST)


class UpdateCardInput(BaseModel):
    title: str | None = Field(default=None, max_length=MAX_SHORT)
    description: str | None = Field(default=None, max_length=MAX_DESC)
    priority: Priority | None = None
    dueDate: str | None = Field(default=None, max_length=MAX_TINY)
    completed: bool | None = None


class MoveCardInput(BaseModel):
    targetColumnId: str = Field(max_length=MAX_ID)
    targetPosition: int | None = None


class ReorderCardsInput(BaseModel):
    cardIds: list[str] = Field(max_length=MAX_ID_LIST)


class ToggleCardInput(BaseModel):
    completed: bool | None = None


class CreateMemberInput(BaseModel):
    name: str = Field(max_length=MAX_SHORT)
    initials: str = Field(max_length=MAX_TINY)
    color: str = Field(max_length=MAX_TINY)


class UpdateMemberInput(BaseModel):
    name: str | None = Field(default=None, max_length=MAX_SHORT)
    initials: str | None = Field(default=None, max_length=MAX_TINY)
    color: str | None = Field(default=None, max_length=MAX_TINY)


class ChatExchange(BaseModel):
    """One prior question and the answer it got — the compact unit the client buffers and replays.

    A *pair*, not a flat turn, on purpose: it structurally guarantees the user/assistant alternation
    Converse requires, so a client cannot send two questions in a row or lead with an answer. And it
    carries only the distilled answer text — never the tool scaffolding (tool_use/tool_result blocks)
    the loop used to reach it. That working scaffolding is large, per-turn, and worthless after the
    answer lands; replaying it would blow the token budget conversation memory competes for."""

    question: str = Field(min_length=1, max_length=1200)
    answer: str = Field(min_length=1, max_length=4000)


class AgentChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=1200)
    activeBoardId: str | None = Field(default=None, max_length=MAX_ID)
    # Where the user is standing when there is no board pinned. The panel tells them it searches
    # "all N boards in <workspace>", so it must actually stop there — without this the fence fell
    # back to every board in the tenant and the label understated what was read.
    activeWorkspaceId: str | None = Field(default=None, max_length=MAX_ID)
    # Prior exchanges in THIS conversation, oldest first — client-owned (the browser buffers them
    # per scope and replays a short window), so the backend stays stateless. Bounded here as the
    # fail-closed cap: a client cannot spend the token budget by shipping an unbounded transcript.
    # This is text the model reads, never authorization — every live tool call is still fenced by
    # `accessible_boards_ctx`, so history can describe but never widen scope.
    history: list[ChatExchange] = Field(default_factory=list, max_length=6)


class AgentChatTable(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class AgentChatPresentation(BaseModel):
    format: Literal["text", "bullets", "table"] = "text"
    title: str | None = None
    text: str | None = None
    bullets: list[str] = Field(default_factory=list)
    table: AgentChatTable | None = None


class AgentChatOutput(BaseModel):
    valid: bool
    response: str
    reason: str
    requestId: str | None = None
    toolCalled: str | None = None
    presentation: AgentChatPresentation | None = None
    warnings: list[str] = Field(default_factory=list)
    # A server-verified fingerprint of the scope+fence this answer was computed under.
    # The client binds each in-flight request to the scope it was issued in and refuses to render a
    # reply whose signature no longer matches the active scope — so a late answer cannot paint into
    # a panel the user has since navigated away from.
    scopeSignature: str | None = None


# ── Feedback ──────────────────────────────────────────────────────────────────

class FeedbackInput(BaseModel):
    """What an invited user sends. Bounded like every other request body."""

    category: Literal["chat", "notes", "general", "bug"] = "general"
    message: str = Field(min_length=1, max_length=4000)
    rating: int | None = Field(default=None, ge=1, le=5)
    page: str | None = Field(default=None, max_length=200)


class FeedbackView(BaseModel):
    id: str
    instanceId: str | None = None
    userId: str | None = None
    userEmail: str | None = None
    category: str
    rating: int | None = None
    message: str
    page: str | None = None
    createdAt: str
