"""Note-import endpoints.

`/analyze` validates an input, records the session, then calls the extraction provider and resolves
its output into review-ready recommendations. It performs **no board mutation** — the
board is untouched until a human approves a card. The provider is selected by
`agent_backend`: offline (the default) proposes nothing; openai, anthropic and bedrock call a model.

The ordering of the checks in `analyze_endpoint` is the design. Every rejection happens before
inference, so an unreadable or oversized document costs nothing, and
nothing reaches a model that shouldn't.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterator, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from ..config import settings
from ..db import get_connection
from ..tenancy import current_user
from ..usage import estimate_tokens, record_usage, tokens_used_today
from . import store as import_store
from .errors import ImportError as ImportRejection
from .extract import MAX_RAW_BYTES, Extraction, extract_docx, extract_pasted, extract_txt
from .prompt import build_locator_hint, build_system_prompt, build_user_message
from .provider import get_extraction_provider
from .resolve import resolve_one, resolve_recommendations
from .schema import RecommendationView
from .schemas import (
    ApplyBatchRequest,
    ApplyBatchResultView,
    ApplyRecommendationRequest,
    ApplyResultView,
    NoteImportSessionView,
    UndoBatchResultView,
    UpdateRecommendationRequest,
)

router = APIRouter(prefix="/boards/{board_id}/note-imports", tags=["note-imports"])
logger = logging.getLogger(__name__)


def _require_model_backend() -> None:
    """Refuse an analysis before anything else when no AI model is connected.

    Without this the offline backend answers every note with zero cards, and the review screen
    reads that as "no action items were found" — a confident wrong answer. The user is told to
    contact their administrator; what to fix goes to the server log."""
    problem = settings.agent_setup_problem()
    if problem is not None:
        logger.warning("Note import refused, the AI agents are off: %s", problem)
        raise ImportRejection("AI_NOT_CONFIGURED")


_TXT_EXTS = {".txt"}
_DOCX_EXTS = {".docx"}
# Named so the error can say "this is a .doc" rather than "unsupported" (an error names an
# action). Anything not listed anywhere still lands on UNSUPPORTED_FILE_TYPE.
_KNOWN_UNSUPPORTED = {".doc", ".docm", ".pdf", ".rtf", ".odt", ".pages"}


def _meeting_date(raw: str) -> str:
    """The anchor for relative dates. Must be a real calendar date."""
    try:
        return date.fromisoformat(raw).isoformat()
    except (ValueError, TypeError) as exc:
        raise ImportRejection(
            "INVALID_MEETING_DATE",
            detail=f"'{raw}' is not a date in YYYY-MM-DD form.",
        ) from exc


def _extract(
    *, pasted: Optional[str], upload_bytes: Optional[bytes], filename: Optional[str]
) -> tuple[Extraction, str, Optional[str]]:
    """Route one source to its reader. Returns (extraction, input_mode, detected_media_type)."""
    if pasted is not None:
        return extract_pasted(pasted), "paste", "text/plain"

    assert upload_bytes is not None  # guaranteed by the exclusivity check in the endpoint
    ext = os.path.splitext(filename or "")[1].lower()

    if ext in _TXT_EXTS:
        return extract_txt(upload_bytes), "txt", "text/plain"
    if ext in _DOCX_EXTS:
        return (
            extract_docx(upload_bytes, declared_ext=ext),
            "docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    if ext in _KNOWN_UNSUPPORTED:
        raise ImportRejection(
            "UNSUPPORTED_FILE_TYPE",
            detail=f"{ext} files are not supported. Save the notes as .txt or .docx.",
        )
    raise ImportRejection(
        "UNSUPPORTED_FILE_TYPE",
        detail="Only .txt and .docx files can be imported.",
    )


def _enforce_token_budget(text: str, user: Optional[dict[str, Any]]) -> int:
    """Per-document cap, then the caller's remaining daily allowance.

    Never truncates: an oversized note is refused so the user can decide what to cut, rather than
    silently analyzed in part.

    We measure with `usage.estimate_tokens` but raise our own errors, because `usage`'s exceptions
    are bare HTTP errors and every import error needs a code plus a user action.

    Note the boundary honestly: the daily meter is keyed by authenticated user, so with Cognito
    off there is nobody to meter and `DAILY_AI_BUDGET` cannot fire. The
    per-document cap is auth-independent and always applies, so an unauthenticated caller is still
    bounded — just per-request rather than per-day.
    """
    tokens = estimate_tokens(text)
    if tokens > settings.max_tokens_per_document:
        raise ImportRejection(
            "DOCUMENT_TOKEN_LIMIT",
            detail=(
                f"This note is about {tokens:,} tokens; the limit is "
                f"{settings.max_tokens_per_document:,}."
            ),
        )

    if settings.cognito_enabled and user:
        conn = get_connection()
        try:
            used = tokens_used_today(conn, user["id"])
        finally:
            conn.close()
        if used + tokens > settings.max_tokens_per_user_per_day:
            raise ImportRejection(
                "DAILY_AI_BUDGET",
                detail=(
                    f"{used:,} of {settings.max_tokens_per_user_per_day:,} tokens used today; "
                    f"this note needs about {tokens:,} more."
                ),
            )
    return tokens


async def _read_bounded(file: UploadFile, limit: int) -> bytes:
    """Read at most `limit + 1` bytes so an oversized upload is not materialized whole into memory
    before the size check. Starlette has already received the body (an overall request-body cap
    belongs at the reverse proxy in front of the app), but this bounds our in-memory copy and
    rejects early when the parser recorded a size."""
    size = getattr(file, "size", None)
    if size is not None and size > limit:
        raise ImportRejection("FILE_TOO_LARGE", detail=f"{size:,} bytes; the limit is {limit:,}.")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ImportRejection("FILE_TOO_LARGE", detail=f"The upload exceeds the {limit:,}-byte limit.")
        chunks.append(chunk)
    return b"".join(chunks)


@dataclass
class _Prepared:
    """Everything both endpoints need once the input has passed every check and the session is
    recorded. Assembled once so the check *order* lives in one place, not duplicated per endpoint."""

    session_id: str
    board_id: str
    board_version: int
    meeting_date: str
    input_mode: str
    warnings: list[str]
    members: list[dict]
    destination_column_id: str
    system_prompt: str
    user_message: str
    normalized_note: str
    estimated_tokens: int


async def _prepare_analysis(
    *,
    board_id: str,
    meeting_date_raw: str,
    pasted_text: Optional[str],
    file: Optional[UploadFile],
    timezone: Optional[str],
    target_column_id: Optional[str],
    user: Optional[dict[str, Any]],
) -> _Prepared:
    """Checks 1-6, session creation, and prompt assembly — shared by both endpoints. Raises an
    `ImportRejection` (a categorized HTTP error) on any bad input, always *before* the model is reached.
    Manages its own connection, so no endpoint holds a DB handle open across a streaming model call.

    Check order is load-bearing — cheapest and most-likely-wrong first, so a bad request is refused
    before we spend anything on it, and nothing reaches a model that shouldn't:

      1. board exists / is in scope
      2. exactly one input source        (free)
      3. raw size                        (free)
      4. a valid destination column      (one query — no point reading a file we can't act on)
      5. type, container integrity, encoding, extractable text   (CPU only)
      6. token budget                    (needs the extracted text)
    """
    conn = get_connection()
    try:
        # 1. Board scope. When Cognito is on, `enforce_board_access` on the router has already
        #    rejected another tenant's board; this also covers a board that simply doesn't exist,
        #    and gives us the board version.
        board_ver = import_store.board_version(conn, board_id)
        if board_ver is None:
            raise ImportRejection("BOARD_HAS_NO_COLUMNS", detail="That board does not exist.")

        # 2. Exactly one source. A whitespace-only paste counts as no input.
        has_paste = pasted_text is not None and pasted_text.strip() != ""
        has_file = file is not None and (file.filename or "") != ""
        if has_paste and has_file:
            raise ImportRejection("MULTIPLE_INPUTS")
        if not has_paste and not has_file:
            raise ImportRejection("EMPTY_INPUT")

        meeting_date = _meeting_date(meeting_date_raw)

        # 3. Raw size, before reading anything into a parser. Bounded read: reading the
        #    whole upload and *then* checking the size would spool an oversized body fully into
        #    memory before rejecting it.
        upload_bytes: Optional[bytes] = None
        if has_file:
            upload_bytes = await _read_bounded(file, MAX_RAW_BYTES)
        elif pasted_text is not None and len(pasted_text.encode("utf-8")) > MAX_RAW_BYTES:
            raise ImportRejection("FILE_TOO_LARGE", detail="The pasted text is over 1 MB.")

        # 4. A card needs somewhere to go, and the user's chosen destination must be a real
        #    column on this board. Creating a column would be board design, which this
        #    feature does not do — so an empty board blocks, and a stale/absent choice asks for a
        #    valid one.
        column_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM board_columns WHERE board_id = ? ORDER BY position, created_at",
                (board_id,),
            ).fetchall()
        ]
        if not column_ids:
            raise ImportRejection("BOARD_HAS_NO_COLUMNS")

        # The user picks one destination for the whole import, defaulted to the leftmost column.
        # The model is never asked and code stamps this on every card during resolution.
        if target_column_id is None:
            destination_column_id = column_ids[0]
        elif target_column_id in column_ids:
            destination_column_id = target_column_id
        else:
            raise ImportRejection("TARGET_COLUMN_REQUIRED", detail="That column is not on this board.")

        # 5. Read it. Everything unreadable dies here, still before any model call.
        extraction, input_mode, detected_media_type = _extract(
            pasted=pasted_text if has_paste else None,
            upload_bytes=upload_bytes,
            filename=file.filename if has_file else None,
        )

        # 6. Budget.
        tokens = _enforce_token_budget(extraction.text, user)

        raw_for_hash = upload_bytes if upload_bytes is not None else (pasted_text or "").encode("utf-8")
        session = import_store.create_session(
            conn,
            board_id=board_id,
            instance_id=(user or {}).get("instance_id") or "default",
            actor_id=(user or {}).get("id"),
            board_ver=board_ver,
            input_mode=input_mode,
            meeting_date=meeting_date,
            user_timezone=timezone,
            original_filename=(file.filename if has_file else None),
            declared_media_type=(file.content_type if has_file else "text/plain"),
            detected_media_type=detected_media_type,
            byte_size=len(raw_for_hash),
            content_sha256=hashlib.sha256(raw_for_hash).hexdigest(),
            extracted_char_count=len(extraction.text),
            input_tokens_estimated=tokens,
            status="ready",
        )

        # The board roster goes to the model as *context only* — names so it can tell a person from
        # a product; it has no field for a member id.
        members = [
            {"id": row["id"], "name": row["name"]}
            for row in conn.execute(
                "SELECT id, name FROM members WHERE board_id = ? ORDER BY created_at",
                (board_id,),
            ).fetchall()
        ]
    finally:
        conn.close()

    return _Prepared(
        session_id=session["id"],
        board_id=board_id,
        board_version=board_ver,
        meeting_date=meeting_date,
        input_mode=input_mode,
        warnings=list(extraction.warnings),
        members=members,
        destination_column_id=destination_column_id,
        system_prompt=build_system_prompt(members=members, meeting_date=meeting_date),
        user_message=build_user_message(
            extraction.text, locator_hint=build_locator_hint(list(extraction.segments))
        ),
        normalized_note=extraction.text,
        estimated_tokens=tokens,
    )


def _reserve_usage(user: Optional[dict[str, Any]], tokens: int) -> None:
    """Charge a document analysis against the daily meter BEFORE the paid model call.

    Checking `tokens_used_today` without recording would leave repeated imports unmetered — an
    authenticated user could run unbounded paid analyses. We reserve the estimate up front
    (conservative: a failed model call never *under*-charges).
    Output tokens are not metered because the extraction providers don't report them — the same
    limitation as chat's estimate. Skipped for the free offline backend and when there is no
    authenticated user to meter (mirrors `_enforce_token_budget`)."""
    if user and settings.cognito_enabled and settings.agent_backend != "offline":
        record_usage(user["id"], tokens)


@router.post("/analyze", response_model=NoteImportSessionView, summary="Analyze one note into proposed cards")
async def analyze_endpoint(
    board_id: str,
    meetingDate: str = Form(..., description="Anchor for relative dates like 'by Friday'"),
    clientRequestId: str = Form(..., description="Suppresses accidental duplicate submissions"),
    pastedText: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    timezone: Optional[str] = Form(None),
    targetColumnId: Optional[str] = Form(
        None, description="Destination column for every card. Defaults to the leftmost."
    ),
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> NoteImportSessionView:
    """Validate one note, open a session, and propose cards in a single response. Mutates nothing on
    the board. The blocking model call runs off the event loop.

    This is the non-streaming form; `/analyze/stream` returns the same cards progressively. It stays
    as the fallback for clients or proxies that cannot hold a long streaming response open."""
    _require_model_backend()
    prepared = await _prepare_analysis(
        board_id=board_id,
        meeting_date_raw=meetingDate,
        pasted_text=pastedText,
        file=file,
        timezone=timezone,
        target_column_id=targetColumnId,
        user=user,
    )

    # Reserve the daily-meter charge before the paid call.
    _reserve_usage(user, prepared.estimated_tokens)

    # The provider is chosen by agent_backend and validates its own output against the contract
    # (openai, anthropic or bedrock; `_require_model_backend` has already refused offline).
    provider = get_extraction_provider(settings)
    model_set = await run_in_threadpool(
        provider.extract, system_prompt=prepared.system_prompt, user_message=prepared.user_message
    )

    # Resolution is deterministic code: match each name to the roster, stamp the chosen column,
    # verify every quote is in the note — turning model *claims* into a reviewable proposal.
    recommendations = resolve_recommendations(
        model_set,
        members=prepared.members,
        target_column_id=prepared.destination_column_id,
        normalized_note=prepared.normalized_note,
        meeting_date=prepared.meeting_date,
    )

    # Persist so the proposal survives a refresh. The whole set commits together.
    conn = get_connection()
    try:
        for position, (model_rec, view) in enumerate(zip(model_set.recommendations, recommendations)):
            import_store.insert_recommendation(
                conn, session_id=prepared.session_id, position=position, model_rec=model_rec, view=view
            )
        conn.commit()
    finally:
        conn.close()

    return NoteImportSessionView(
        id=prepared.session_id,
        boardId=prepared.board_id,
        boardVersion=prepared.board_version,
        meetingDate=prepared.meeting_date,
        inputMode=prepared.input_mode,
        status="ready",
        warnings=prepared.warnings,
        partialFailureCount=0,
        recommendations=[r.model_dump() for r in recommendations],
    )


def _ndjson(event: dict) -> bytes:
    """One NDJSON event: a JSON object, one per line. `default=str` is a backstop — the views
    serialize cleanly, but a stray serialization error must not abort a stream mid-flight."""
    return (json.dumps(event, default=str) + "\n").encode("utf-8")


def _stream_body(prepared: _Prepared) -> Iterator[bytes]:
    """The NDJSON event stream: the session first, then one event per resolved card as the model
    finishes writing it, then a `done` count — or an in-band `error` if the model fails mid-stream.

    Sync generator on purpose: Starlette iterates it in a threadpool, so the blocking provider call
    never touches the event loop. Resolution runs per card, so each card is checked and flagged
    the instant it arrives rather than after the whole set is in."""
    yield _ndjson(
        {
            "type": "session",
            "session": {
                "id": prepared.session_id,
                "boardId": prepared.board_id,
                "boardVersion": prepared.board_version,
                "meetingDate": prepared.meeting_date,
                "inputMode": prepared.input_mode,
                "warnings": prepared.warnings,
            },
        }
    )

    found = 0
    partial_failures = 0
    provider = get_extraction_provider(settings)
    # Persist each card as it lands, so a stream that dies mid-way still leaves durable what it found
    # — GET returns exactly the cards the user watched arrive.
    conn = get_connection()
    try:
        for item in provider.stream(
            system_prompt=prepared.system_prompt, user_message=prepared.user_message
        ):
            if item.recommendation is None:
                partial_failures += 1
                continue
            view = resolve_one(
                item.recommendation,
                members=prepared.members,
                target_column_id=prepared.destination_column_id,
                normalized_note=prepared.normalized_note,
                meeting_date=prepared.meeting_date,
            )
            import_store.insert_recommendation(
                conn, session_id=prepared.session_id, position=found, model_rec=item.recommendation, view=view
            )
            conn.commit()
            found += 1
            yield _ndjson({"type": "recommendation", "found": found, "recommendation": view.model_dump()})
        import_store.set_partial_failure_count(conn, prepared.session_id, partial_failures)
        conn.commit()
        yield _ndjson({"type": "done", "found": found, "partialFailureCount": partial_failures})
    except ImportRejection as exc:
        # A model failure after headers are sent can't change the HTTP status, so it becomes an
        # in-band error event — after everything already produced. The body is the catalog entry
        # (code + message + action), never raw model output. What was persisted stays.
        import_store.set_partial_failure_count(conn, prepared.session_id, partial_failures)
        conn.commit()
        body = exc.detail if isinstance(exc.detail, dict) else {"code": "MODEL_OUTPUT_INVALID"}
        yield _ndjson({"type": "error", "found": found, **body})
    finally:
        conn.close()


@router.post("/analyze/stream", summary="Analyze one note, streaming cards as they are produced")
async def analyze_stream_endpoint(
    board_id: str,
    meetingDate: str = Form(..., description="Anchor for relative dates like 'by Friday'"),
    clientRequestId: str = Form(..., description="Suppresses accidental duplicate submissions"),
    pastedText: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    timezone: Optional[str] = Form(None),
    targetColumnId: Optional[str] = Form(
        None, description="Destination column for every card. Defaults to the leftmost."
    ),
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> StreamingResponse:
    """The same analysis as `/analyze`, but the cards stream back as NDJSON the moment each is
    written — a ~60s wait for a large note becomes cards appearing one at a time with an honest
    running count, not a spinner.

    Input validation still happens up front: every input error is a normal HTTP error *before*
    the stream opens. Only failures during generation (timeout, provider error) arrive in-band, as a
    trailing `error` event, because the 200 headers are already sent by then."""
    _require_model_backend()
    prepared = await _prepare_analysis(
        board_id=board_id,
        meeting_date_raw=meetingDate,
        pasted_text=pastedText,
        file=file,
        timezone=timezone,
        target_column_id=targetColumnId,
        user=user,
    )
    # Reserve the daily-meter charge before the paid streaming call.
    _reserve_usage(user, prepared.estimated_tokens)
    return StreamingResponse(_stream_body(prepared), media_type="application/x-ndjson")


@router.get("/{session_id}", response_model=NoteImportSessionView, summary="Retrieve an import session")
def get_session_endpoint(
    board_id: str,
    session_id: str,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> NoteImportSessionView:
    """Restore a proposal after a refresh. Never returns another board's session.

    The note is not retained, so this reconstructs each recommendation from its stored
    resolution — not by re-reading or re-analyzing anything. It is a read of what was decided."""
    conn = get_connection()
    try:
        row = import_store.get_session(conn, session_id, board_id=board_id)
        if row is None:
            raise ImportRejection("BOARD_REFERENCE_STALE", detail="That import session no longer exists.")
        return NoteImportSessionView(
            id=row["id"],
            boardId=row["board_id"],
            boardVersion=int(row["board_version"]),
            meetingDate=row["meeting_date"],
            inputMode=row["input_mode"],
            status=row["status"],
            warnings=[],
            partialFailureCount=int(row["partial_failure_count"]),
            recommendations=import_store.get_recommendations(conn, session_id),
        )
    finally:
        conn.close()


@router.patch(
    "/{session_id}/recommendations/{recommendation_id}",
    response_model=RecommendationView,
    summary="Re-target one recommendation to a different column",
)
def update_recommendation_endpoint(
    board_id: str,
    session_id: str,
    recommendation_id: str,
    body: UpdateRecommendationRequest,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> Any:
    """Change which column a still-pending card will be created in. Columns aren't the
    model's to propose — notes don't name them — so the reviewer chooses per card, and the choice is
    persisted so the bulk `apply-batch` path (which reads the stored column) puts the card where the
    reviewer put it, not in a single up-front default. Mutates no board state."""
    conn = get_connection()
    try:
        session = import_store.get_session(conn, session_id, board_id=board_id)
        if session is None:
            raise ImportRejection("BOARD_REFERENCE_STALE", detail="That import session no longer exists.")
        return import_store.update_recommendation_column(
            conn,
            session_id=session_id,
            rec_id=recommendation_id,
            board_id=board_id,
            target_column_id=body.targetColumnId,
        )
    finally:
        conn.close()


@router.post(
    "/{session_id}/recommendations/{recommendation_id}/apply",
    response_model=ApplyResultView,
    summary="Approve one recommendation and create the card",
)
def apply_recommendation_endpoint(
    board_id: str,
    session_id: str,
    recommendation_id: str,
    body: ApplyRecommendationRequest,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> ApplyResultView:
    """Create one real card from an approved recommendation — the single place the agent touches the
    board. The request body is the exact approved snapshot, which the store applies in
    one transaction: card + optional new member + the idempotency record, together or not at all.

    A retry with the same approval returns the original card (`replayed: true`); a retry with a
    *changed* approval after the card already exists is APPLY_CONFLICT; a column or member deleted
    since review is BOARD_REFERENCE_STALE — all before or without a second card being created."""
    conn = get_connection()
    try:
        session = import_store.get_session(conn, session_id, board_id=board_id)
        if session is None:
            raise ImportRejection("BOARD_REFERENCE_STALE", detail="That import session no longer exists.")

        snapshot = body.model_dump(exclude={"idempotencyKey"})
        result = import_store.apply_recommendation(
            conn,
            session=session,
            rec_id=recommendation_id,
            actor_id=(user or {}).get("id"),
            snapshot=snapshot,
            idempotency_key=body.idempotencyKey,
        )
        return ApplyResultView(sessionId=session_id, **result)
    finally:
        conn.close()


@router.post(
    "/{session_id}/apply-batch",
    response_model=ApplyBatchResultView,
    summary="Add the clean ones — bulk create every unflagged recommendation",
)
def apply_batch_endpoint(
    board_id: str,
    session_id: str,
    body: ApplyBatchRequest,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> ApplyBatchResultView:
    """Create every unflagged, still-pending recommendation in one action. The server chooses
    the set from storage — a flagged card can never be included — and applies it as one
    transaction under one board version: a stale version or a vanished reference fails the whole batch,
    never part of it. Same referential and idempotency guarantees as a per-card apply."""
    conn = get_connection()
    try:
        session = import_store.get_session(conn, session_id, board_id=board_id)
        if session is None:
            raise ImportRejection("BOARD_REFERENCE_STALE", detail="That import session no longer exists.")
        result = import_store.apply_batch(
            conn,
            session=session,
            actor_id=(user or {}).get("id"),
            idempotency_key=body.idempotencyKey,
            expected_board_version=body.expectedBoardVersion,
        )
        return ApplyBatchResultView(sessionId=session_id, **result)
    finally:
        conn.close()


@router.post(
    "/{session_id}/batches/{batch_id}/undo",
    response_model=UndoBatchResultView,
    summary="Undo a bulk create — remove exactly the cards it created",
)
def undo_batch_endpoint(
    board_id: str,
    session_id: str,
    batch_id: str,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> UndoBatchResultView:
    """Remove every card the batch created, for as long as the session is open. A card
    the user has since edited or moved is kept and reported, never silently deleted — cheap approval
    is only safe when retraction is equally cheap, and equally honest about what it will not touch."""
    conn = get_connection()
    try:
        session = import_store.get_session(conn, session_id, board_id=board_id)
        if session is None:
            raise ImportRejection("BOARD_REFERENCE_STALE", detail="That import session no longer exists.")
        result = import_store.undo_batch(
            conn, session=session, batch_id=batch_id, actor_id=(user or {}).get("id")
        )
        return UndoBatchResultView(sessionId=session_id, batchId=batch_id, **result)
    finally:
        conn.close()
