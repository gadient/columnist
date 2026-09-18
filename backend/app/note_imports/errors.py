"""The import error catalog.

Errors are categorized and displayed with a user action, not a generic failure alone.

That requirement is met structurally rather than by remembering to write good messages: an
`ImportError` cannot be raised without an action, because the action comes from the catalog.
Raising a code the catalog does not carry is a KeyError inside `ImportError.__init__`, not a bad
UX in production.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

# code -> (what the user is told, what they can do about it).
_CATALOG: dict[str, tuple[str, str]] = {
    # --- Input boundary. All of these fire BEFORE any model call. -------------------
    "BOARD_HAS_NO_COLUMNS": (
        "Cards need an existing destination column.",
        "Create a column on this board, then try the import again.",
    ),
    "EMPTY_INPUT": (
        "No usable note text was supplied.",
        "Paste some text or choose a different file.",
    ),
    "MULTIPLE_INPUTS": (
        "Paste and upload cannot be analyzed together.",
        "Choose one source: either paste text or upload a file.",
    ),
    # The meeting date is a required input and can be malformed; without its own code, that failure
    # would have to borrow a neighbouring code and report something unrelated.
    "INVALID_MEETING_DATE": (
        "The meeting date isn't a valid date.",
        "Pick a meeting date — it's what 'by Friday' and 'next week' are measured from.",
    ),
    "FILE_TOO_LARGE": (
        "Input exceeds 1 MB.",
        "Reduce the file, or paste just the relevant section.",
    ),
    "UNSUPPORTED_FILE_TYPE": (
        "The file is not a TXT or DOCX.",
        "Convert it to .txt or .docx and try again.",
    ),
    "UNSUPPORTED_ENCODING": (
        "The TXT file is not valid UTF-8.",
        "Re-save the file as UTF-8 and try again.",
    ),
    "DOCX_INVALID": (
        "The DOCX is corrupt, encrypted, or is not really a DOCX.",
        "Open it in Word and re-save it as a standard .docx.",
    ),
    "DOCX_TRACKED_CHANGES": (
        "The document has unresolved tracked changes, which could make us read the wrong text.",
        "Accept or reject the tracked changes, re-save, and try again.",
    ),
    "NO_EXTRACTABLE_TEXT": (
        "The file contains no supported body text.",
        "Supply notes as text — images and scans are not read.",
    ),
    "DOCUMENT_TOKEN_LIMIT": (
        "The note is longer than we can analyze in one go.",
        "Remove non-actionable sections, or import it in smaller parts.",
    ),
    "DAILY_AI_BUDGET": (
        "This would exceed your daily AI allowance.",
        "Try again after the daily reset, or import a smaller note.",
    ),
    # With no model connected, the offline backend would otherwise answer every note
    # with zero cards, which the review screen presents as "no action items were found" — a wrong
    # answer rather than an honest one. Refused before any other check; the setup detail goes to the
    # server log for the operator.
    "AI_NOT_CONFIGURED": (
        "Importing notes isn't switched on. Please contact your application administrator.",
        "Ask your application administrator to connect an AI model.",
    ),
    # --- Analysis ----------------------------------------------------------------------------
    "CONTENT_GUARDRAIL": (
        "The content could not be processed safely.",
        "Remove unsafe or instruction-like content and try again.",
    ),
    "MODEL_TIMEOUT": (
        "Analysis did not finish in time.",
        "Try the analysis again.",
    ),
    "MODEL_OUTPUT_INVALID": (
        "The assistant did not return a usable set of suggestions.",
        "Try the analysis again.",
    ),
    # --- Review ------------------------------------------------------------------------------
    "ASSIGNEE_UNRESOLVED": (
        "An owner matches more than one board member, or none.",
        "Pick an existing member, approve adding a new one, or leave it unassigned.",
    ),
    "TARGET_COLUMN_REQUIRED": (
        "No valid destination column is selected.",
        "Choose a column for this card.",
    ),
    # --- Apply -------------------------------------------------------------------------------
    "BOARD_REFERENCE_STALE": (
        "A column or member this card refers to no longer exists.",
        "Refresh the board and pick a current value.",
    ),
    "BOARD_VERSION_STALE": (
        "The board changed since this view of it was loaded.",
        "Refresh the board and make the change again.",
    ),
    "APPLY_CONFLICT": (
        "This suggestion was already created, and has since been changed.",
        "Open the card that was created, or start a new import.",
    ),
    "APPLY_FAILED": (
        "The card was not created.",
        "Correct the issue shown and try again — nothing was partially saved.",
    ),
}

# Codes that are the caller's fault vs ours. Everything defaults to 400.
_STATUS: dict[str, int] = {
    "FILE_TOO_LARGE": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    "DOCUMENT_TOKEN_LIMIT": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    "DAILY_AI_BUDGET": status.HTTP_429_TOO_MANY_REQUESTS,
    "AI_NOT_CONFIGURED": status.HTTP_503_SERVICE_UNAVAILABLE,
    "MODEL_TIMEOUT": status.HTTP_504_GATEWAY_TIMEOUT,
    "MODEL_OUTPUT_INVALID": status.HTTP_502_BAD_GATEWAY,
    "BOARD_VERSION_STALE": status.HTTP_409_CONFLICT,
    "APPLY_CONFLICT": status.HTTP_409_CONFLICT,
    "APPLY_FAILED": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


class ImportError(HTTPException):
    """A categorized import failure. The user always gets a code, a meaning, and a next action."""

    def __init__(self, code: str, *, detail: str | None = None, **extra: Any) -> None:
        meaning, action = _CATALOG[code]  # KeyError here = a code with no user action; fix the catalog
        self.code = code
        body: dict[str, Any] = {"code": code, "message": meaning, "action": action}
        if detail:
            # Extra specificity where we have it (e.g. which limit, by how much). Never a stack
            # trace, never raw model output, never note content — content is kept out of logs
            # and the same reasoning applies to error bodies.
            body["detail"] = detail
        if extra:
            body.update(extra)
        super().__init__(status_code=_STATUS.get(code, status.HTTP_400_BAD_REQUEST), detail=body)
