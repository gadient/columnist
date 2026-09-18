"""The error catalog.

The catalog is data, not scattered `raise` statements with ad-hoc strings, so that "every failure
tells the user what to do next" is checkable rather than aspirational.
"""
from __future__ import annotations

import pytest
from app.note_imports import errors as errors_module
from app.note_imports.errors import ImportError as ImportRejection

CATALOG = errors_module._CATALOG


def test_every_error_gives_the_user_something_to_do():
    """Enforced structurally: an ImportError *cannot be raised* without a user action,
    because the catalog has no entry without one. A failure the user can't act on is a dead end
    wearing an error message."""
    for code, entry in CATALOG.items():
        message, action = entry[0], entry[1]
        assert message.strip(), f"{code} has no message"
        assert action.strip(), f"{code} has no user action"


def test_an_unknown_code_cannot_be_raised():
    """Guards against a typo'd code becoming a 500 with no catalog entry."""
    with pytest.raises(KeyError):
        ImportRejection("NOT_A_REAL_CODE")


def test_every_documented_error_code_exists():
    """Drift between the documented catalog and the code is how a spec quietly becomes fiction."""
    required = {
        "BOARD_HAS_NO_COLUMNS", "EMPTY_INPUT", "MULTIPLE_INPUTS", "INVALID_MEETING_DATE",
        "FILE_TOO_LARGE", "UNSUPPORTED_FILE_TYPE", "UNSUPPORTED_ENCODING", "DOCX_INVALID",
        "DOCX_TRACKED_CHANGES", "NO_EXTRACTABLE_TEXT", "DOCUMENT_TOKEN_LIMIT", "DAILY_AI_BUDGET",
        "CONTENT_GUARDRAIL", "MODEL_TIMEOUT", "MODEL_OUTPUT_INVALID", "ASSIGNEE_UNRESOLVED",
        "TARGET_COLUMN_REQUIRED", "BOARD_REFERENCE_STALE", "BOARD_VERSION_STALE",
        "APPLY_CONFLICT", "APPLY_FAILED",
    }
    assert required <= set(CATALOG), f"missing from the catalog: {sorted(required - set(CATALOG))}"


def test_invalid_meeting_date_has_its_own_code():
    """This one exists because a test lied. A malformed meeting date reported MULTIPLE_INPUTS —
    nonsense — and the check *passed*, because it asserted the HTTP status and not the code.
    Assert the thing you actually care about."""
    exc = ImportRejection("INVALID_MEETING_DATE")
    assert exc.detail["code"] == "INVALID_MEETING_DATE"
    assert exc.status_code == 400


def test_the_error_body_carries_code_message_and_action():
    exc = ImportRejection("EMPTY_INPUT")
    assert {"code", "message", "action"} <= set(exc.detail)


def test_detail_is_optional_and_additive():
    exc = ImportRejection("DOCX_INVALID", detail="The file is corrupt and cannot be opened.")
    assert exc.detail["code"] == "DOCX_INVALID"
    assert "corrupt" in exc.detail["detail"]
