from __future__ import annotations

import hashlib
import logging
from time import perf_counter
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from .chat_logging import is_mcp_server_up, log_chat_completion, new_request_id
from .chat_presentation import build_chat_presentation
from .config import settings
from .db import get_connection
from .request_scope import accessible_boards_ctx
from .schemas import (
    AgentChatInput,
    AgentChatOutput,
    AgentChatPresentation,
    AgentChatTable,
    ChatExchange,
)
from .store import (
    accessible_board_ids,
    board_ids_in_workspace,
    user_can_access_board,
    user_can_access_workspace,
)
from .tenancy import current_user
from .usage import enforce_daily_budget, estimate_tokens, record_usage
from .validator import ChatResponseResult, validate_agent_input

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])


def _scope_signature(
    *, active_board_id: Optional[str], active_workspace_id: Optional[str], board_ids: Optional[set[str]]
) -> str:
    """A server-verified fingerprint of the scope this answer was computed under.

    It binds two things the client cannot forge together: the scope the user is standing in (board,
    workspace, or global) AND the authorization fence the server actually resolved (the board-id
    set, or `*` when unrestricted). The client tags each in-flight request with the scope it issued
    under and refuses to render a reply whose signature no longer matches the active scope — so a
    late board answer cannot paint into a workspace panel after navigation, and a reply computed
    under a fence that changed mid-conversation (access revoked) fails closed rather than showing
    stale board data. Order-independent: the fence is sorted so the same scope always hashes alike."""
    if active_board_id:
        head = f"board:{active_board_id}"
    elif active_workspace_id:
        head = f"workspace:{active_workspace_id}"
    else:
        head = "global:"
    fence = "*" if board_ids is None else ",".join(sorted(board_ids))
    return hashlib.sha256(f"{head}|{fence}".encode()).hexdigest()[:16]


#: What anyone looking at the chat panel sees when chat cannot run. Which setting is missing is the
#: operator's business, so that goes to the server log (and the startup log), never to the panel.
_CHAT_OFF_TEXT = "Chat isn't switched on. Please contact your application administrator."


def _chat_provider():
    """The `ChatProvider` for `agent_backend`. Imports are lazy so each SDK loads only when used."""
    if settings.agent_backend == "openai":
        from .agents.openai_provider import OpenAIChatProvider

        return OpenAIChatProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            temperature=settings.openai_temperature,
        )
    if settings.agent_backend == "anthropic":
        from .agents.anthropic_provider import AnthropicChatProvider

        return AnthropicChatProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            temperature=settings.openai_temperature,
            max_tokens=settings.anthropic_max_tokens,
        )
    from .agents.bedrock_provider import BedrockChatProvider

    return BedrockChatProvider(
        region=settings.bedrock_region,
        model_id=settings.bedrock_model_id,
        temperature=settings.openai_temperature,
        max_tokens=settings.bedrock_max_tokens,
        guardrail_id=settings.bedrock_guardrail_id,
        guardrail_version=settings.bedrock_guardrail_version,
    )


def _agent_chat(
    message: str, active_board_id: Optional[str], history: list[ChatExchange]
) -> ChatResponseResult:
    """Run the tool-calling loop and shape the outcome as a `ChatResponseResult`.

    Called only when `settings.agent_setup_problem()` is None. Any other backend returns an honest
    "unavailable" (there is no offline rules engine). The scope is already set by the
    caller — this function must stay inside that `try` block, because `run_agent` refuses to run
    unscoped under auth and reading the fence from somewhere else would defeat the point of having
    one owner for it.

    The input gate is `validate_agent_input` (shape only) — a keyword allowlist over natural
    language cannot gate a tool-calling model without rejecting questions it answers correctly.
    """
    from .agents.loop import HistoryExchange, run_agent

    check = validate_agent_input(message)
    if not check.valid:
        return ChatResponseResult(valid=False, response=check.reason, reason=check.reason, tool_called=None)

    # The client sends compact {question, answer} pairs; the loop turns each into a user/assistant
    # message. Only the current `message` is gated by `validate_agent_input` — prior turns already
    # passed the gate when they were asked, and their answers are our own prior output.
    prior = [HistoryExchange(question=h.question, answer=h.answer) for h in history]

    provider = _chat_provider()
    out = run_agent(
        message,
        provider=provider,
        active_board_id=active_board_id,
        history=prior,
        max_turns=settings.agent_max_turns,
        token_budget=settings.agent_token_budget,
        deadline_seconds=settings.agent_timeout_seconds,
    )

    # `tool_called` is a single name in the existing contract; the loop may use several. Report the
    # first, which is the one that decided the shape of the answer. The full trace stays on
    # `AgentResult.tool_calls` for the caller to inspect.
    tool_called = out.tool_calls[0].name if out.tool_calls else None
    return ChatResponseResult(
        valid=out.complete,
        response=out.text,
        reason=out.stopped_because,
        tool_called=tool_called,
        error=None if out.complete else out.stopped_because,
        agent=out,
    )


@router.post("/chat", response_model=AgentChatOutput, summary="Validate and respond to chat input")
def agent_chat(payload: AgentChatInput, user: Optional[dict] = Depends(current_user)) -> AgentChatOutput:
    request_id = new_request_id()
    started = perf_counter()

    # Rate limiting: meter tokens per user/day, a budget shared with doc reading. Local
    # dev (no user) is unmetered. Enforce on the way in; record actual usage after the response.
    #
    # The pre-check can only use the message estimate, because the real cost is not knowable until
    # the loop has run. So a user already near the cap can overshoot it by at most one question,
    # and is refused on the next. That is the honest trade — the alternative is charging every
    # request the worst-case budget up front, which would cut the cap from ~17 questions to 1.
    input_tokens = estimate_tokens(payload.message) if (settings.cognito_enabled and user) else 0
    if settings.cognito_enabled and user is not None:
        enforce_daily_budget(user, input_tokens)

    # Scoping happens in two independent layers, and conflating them is how a fence turns into a
    # view filter by accident:
    #
    #   1. TENANT (authorization, Cognito only) — every board in the caller's Instance. Nothing
    #      outside it is reachable, whatever the request asks for.
    #   2. WORKSPACE (view, always) — where the user is standing. The panel promises it searches
    #      "all N boards in <workspace>", so a question asked there must stop at that workspace.
    #      This narrows; it can never widen, because it is intersected with layer 1.
    #
    # Without layer 2, a workspace-level question would be fenced only to the whole tenant and
    # would read other workspaces while the label claimed otherwise.
    scope_token = None
    effective: set[str] | None = None
    conn = get_connection()
    try:
        tenant_scope: set[str] | None = None
        if settings.cognito_enabled and user is not None:
            if payload.activeBoardId and not user_can_access_board(conn, user["id"], payload.activeBoardId):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
            if payload.activeWorkspaceId and not user_can_access_workspace(
                conn, user["id"], payload.activeWorkspaceId
            ):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
            tenant_scope = accessible_board_ids(conn, user["id"])

        # A pinned board already narrows every board-filtered query on its own
        # (`_board_filter_clause` stamps `AND b.id = ?`), so the workspace narrowing only applies to
        # the workspace-level panel. The exception is `get_card_details`, which takes card ids
        # directly and is fenced by the tenant scope alone.
        effective = tenant_scope
        if payload.activeWorkspaceId and not payload.activeBoardId:
            workspace_scope = board_ids_in_workspace(conn, payload.activeWorkspaceId)
            effective = workspace_scope if tenant_scope is None else (tenant_scope & workspace_scope)

        if effective is not None:
            scope_token = accessible_boards_ctx.set(effective)
    finally:
        conn.close()

    # Bind the answer to the scope it was computed under. Derived from the same `effective`
    # fence the tools ran behind, so it cannot drift from what was actually read.
    scope_sig = _scope_signature(
        active_board_id=payload.activeBoardId,
        active_workspace_id=payload.activeWorkspaceId,
        board_ids=effective,
    )

    try:
        setup_problem = settings.agent_setup_problem()
        if setup_problem is None:
            result = _agent_chat(payload.message, payload.activeBoardId, payload.history)
        else:
            # Without a connected model the assistant cannot answer — say so plainly rather than
            # degrade to keyword matching that returns a plausible-but-wrong answer. Covers the
            # offline default and a backend missing its key.
            logger.warning("Chat request refused, the AI agents are off: %s", setup_problem)
            result = ChatResponseResult(
                valid=False,
                response=_CHAT_OFF_TEXT,
                reason="agent backend not configured",
                tool_called=None,
            )
    finally:
        if scope_token is not None:
            accessible_boards_ctx.reset(scope_token)

    presentation = build_chat_presentation(payload.message, result.response, result.tool_called)
    mcp_up = is_mcp_server_up()
    duration_ms = int((perf_counter() - started) * 1000)
    warnings: list[str] = []

    log_chat_completion(
        request_id=request_id,
        message=payload.message,
        active_board_id=payload.activeBoardId,
        valid=result.valid,
        reason=result.reason,
        tool_called=result.tool_called,
        mcp_server_up=mcp_up,
        response=result.response,
        error=result.error,
        duration_ms=duration_ms,
    )

    if settings.cognito_enabled and user is not None:
        # Meter what was actually spent when the model ran. `estimate_tokens` over the message and
        # the reply is a ~4-chars/token heuristic that misses everything the loop really pays for —
        # tool schemas resent each turn, tool results fed back, multiple turns — and understates a
        # real question by roughly two orders of magnitude (measured: ~5.4k actual vs ~100 estimated).
        # Metering the estimate would leave the daily cap decorative.
        spent = (
            result.agent.tokens_used
            if result.agent is not None
            else input_tokens + estimate_tokens(result.response)
        )
        record_usage(user["id"], spent)

    return AgentChatOutput(
        valid=result.valid,
        response=result.response,
        reason=result.reason,
        requestId=request_id,
        toolCalled=result.tool_called,
        warnings=warnings,
        scopeSignature=scope_sig,
        presentation=AgentChatPresentation(
            format=presentation.format,
            title=presentation.title,
            text=presentation.text,
            bullets=presentation.bullets or [],
            table=(
                AgentChatTable(
                    columns=presentation.table_columns or [],
                    rows=presentation.table_rows or [],
                )
                if presentation.table_columns is not None and presentation.table_rows is not None
                else None
            ),
        ),
    )
