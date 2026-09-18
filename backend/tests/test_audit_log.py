"""Per-user audit log — that it degrades to stdout instead of vanishing.

Sibling of `test_chat_logging`. In the container image the non-root app user cannot create
`/data/logs`, so every membership/auth event hits `PermissionError: [Errno 13] '/data'`. A sink that
only swallows that error — without falling back — makes each event disappear with nothing but a
per-event traceback. Invites still return 201, so nothing looks broken while the audit trail — a
security-review artefact — is empty.

These tests make the file sink fail on purpose and assert the record survives to stdout.
"""
from __future__ import annotations

import json

import pytest

from app import audit_log
from app.config import settings


@pytest.fixture(autouse=True)
def reset_sink_state(monkeypatch):
    """`_FILE_SINK_BROKEN` is process-wide, so one failing test would otherwise change the next."""
    monkeypatch.setattr(audit_log, "_FILE_SINK_BROKEN", False)


def _record_once(**overrides):
    kwargs = dict(
        event="member.invite",
        actor={"id": "u1", "email": "piu@example.com"},
        instance_id="instance_abc",
        target="siu@example.com",
        detail={"role": "member"},
    )
    kwargs.update(overrides)
    audit_log.record(**kwargs)


def test_an_unwritable_log_directory_does_not_raise(monkeypatch):
    """The container failure, reproduced: a path whose parent cannot be created."""
    def deny(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", "/data")

    monkeypatch.setattr(audit_log, "_append_jsonl", deny)

    _record_once()  # must not raise — that is the whole test


def test_the_record_survives_to_stdout_when_the_file_cannot_be_written(monkeypatch, caplog):
    """Degrading is not the same as dropping. An empty audit trail that looks healthy is worse than
    a loud failure."""
    def deny(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", "/data")

    monkeypatch.setattr(audit_log, "_append_jsonl", deny)

    with caplog.at_level("INFO"):
        _record_once(target="rescued@example.com")

    emitted = [r.getMessage() for r in caplog.records if r.getMessage().startswith("audit_event {")]
    assert emitted, "the audit event was not recorded anywhere"
    payload = json.loads(emitted[0].split("audit_event ", 1)[1])
    assert payload["event"] == "member.invite"
    assert payload["target"] == "rescued@example.com"
    assert payload["actor_email"] == "piu@example.com"


def test_the_unwritable_warning_is_emitted_once_not_per_event(monkeypatch, caplog):
    """A per-event warning would bury the log stream it is trying to be visible in — exactly what
    raw per-event tracebacks do."""
    def deny(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", "/data")

    monkeypatch.setattr(audit_log, "_append_jsonl", deny)

    with caplog.at_level("WARNING"):
        for _ in range(5):
            _record_once()

    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "not writable" in r.getMessage()]
    assert len(warnings) == 1


def test_the_normal_path_still_writes_a_real_file(monkeypatch, tmp_path):
    """The local behaviour everything else depends on must be unchanged."""
    target = tmp_path / "logs" / "audit.jsonl"
    monkeypatch.setattr(type(settings), "audit_log_file", property(lambda _self: target))

    _record_once(target="filed@example.com")

    written = json.loads(target.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert written["event"] == "member.invite"
    assert written["target"] == "filed@example.com"


def test_application_logging_lets_the_fallback_record_through():
    """The fallback writes at INFO. uvicorn leaves the root logger at WARNING, so without `main`'s
    logging config the rescue record is emitted into a sink that drops it."""
    import logging

    import app.main  # noqa: F401  (importing installs the logging config)

    assert logging.getLogger("app.audit_log").isEnabledFor(logging.INFO), (
        "app INFO logs are suppressed — the audit fallback would be silently discarded"
    )
