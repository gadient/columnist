"""Chat audit logging — specifically, that it cannot take the chat down with it.

`log_chat_completion` runs on the last line of a request that has already succeeded. In the
container image `chat_log_path` resolves to `/data/logs/...`, and the non-root app user cannot
create `/data` — so a sink that raised would throw away a computed answer (a 500 on every chat
request) in the attempt to record it.

Nothing local catches this, because the path is always writable when you run the app locally, and
the suite runs only there. So these tests make the sink fail on purpose.
"""
from __future__ import annotations

import json

import pytest

from app import chat_logging
from app.config import settings


@pytest.fixture(autouse=True)
def reset_sink_state(monkeypatch):
    """`_FILE_SINK_BROKEN` is process-wide, so one failing test would otherwise change the next."""
    monkeypatch.setattr(chat_logging, "_FILE_SINK_BROKEN", False)


def _log_once(**overrides):
    kwargs = dict(
        request_id="r1",
        message="what is overdue?",
        active_board_id=None,
        valid=True,
        reason="ok",
        tool_called="get_overdue_tasks",
        mcp_server_up=False,
        response="Two cards are overdue.",
        error=None,
        duration_ms=12,
    )
    kwargs.update(overrides)
    chat_logging.log_chat_completion(**kwargs)


def test_an_unwritable_log_directory_does_not_raise(monkeypatch):
    """The container failure, reproduced: a path whose parent cannot be created."""
    def deny(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", "/data")

    monkeypatch.setattr(chat_logging, "_append_jsonl", deny)

    _log_once()  # must not raise — that is the whole test


def test_the_record_survives_to_stdout_when_the_file_cannot_be_written(monkeypatch, caplog):
    """Degrading is not the same as dropping. This file is a security-review artefact, so a
    fallback that silently discarded it would trade a loud bug for a quiet one."""
    def deny(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", "/data")

    monkeypatch.setattr(chat_logging, "_append_jsonl", deny)

    with caplog.at_level("INFO"):
        _log_once(request_id="r-fallback")

    # Match the record line specifically: the warning above it names `chat_completions.jsonl`,
    # which contains the same substring.
    emitted = [r.getMessage() for r in caplog.records if r.getMessage().startswith("chat_completion {")]
    assert emitted, "the completion was not recorded anywhere"
    payload = json.loads(emitted[0].split("chat_completion ", 1)[1])
    assert payload["request_id"] == "r-fallback"
    assert payload["tool_called"] == "get_overdue_tasks"


def test_the_unwritable_warning_is_emitted_once_not_per_request(monkeypatch, caplog):
    """A per-message warning would bury the log stream it is trying to be visible in."""
    def deny(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", "/data")

    monkeypatch.setattr(chat_logging, "_append_jsonl", deny)

    with caplog.at_level("WARNING"):
        for _ in range(5):
            _log_once()

    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "not writable" in r.getMessage()]
    assert len(warnings) == 1


def test_the_normal_path_still_writes_a_real_file(monkeypatch, tmp_path):
    """The local behaviour everything else depends on must be unchanged."""
    target = tmp_path / "logs" / "chat_completions.jsonl"
    monkeypatch.setattr(type(settings), "chat_log_file", property(lambda _self: target))

    _log_once(request_id="r-file")

    written = json.loads(target.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert written["request_id"] == "r-file"
    assert written["response"] == "Two cards are overdue."


def test_application_logging_lets_the_fallback_record_through():
    """The fallback writes at INFO. uvicorn leaves the root logger at WARNING, so without logging
    config the rescue record is emitted into a sink that drops it — the warning about the
    unwritable file arrives, the audit line it was rescuing does not. `main` configures logging so
    INFO survives; without that, the stdout fallback is decorative.
    """
    import logging

    import app.main  # noqa: F401  (importing installs the logging config)

    assert logging.getLogger("app.chat_logging").isEnabledFor(logging.INFO), (
        "app INFO logs are suppressed — the chat audit fallback would be silently discarded"
    )
