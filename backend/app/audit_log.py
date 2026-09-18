"""Per-user audit trail.

Board-level, per-user events plus authentication and membership changes, appended as
JSONL to `data/logs/audit.jsonl` (git-ignored, like the chat log). Granularity is
deliberately coarse — "who saved which board / who invited/removed whom / who was
refused" — which fits the snapshot write path without per-action endpoints. Only
called when Cognito is on (there are no real users to attribute otherwise).

Mirrors `chat_logging.py`: a process-wide lock guards appends; failures are swallowed
so auditing can never break a request.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Optional

from .config import settings

logger = logging.getLogger(__name__)

_WRITE_LOCK = Lock()

# Whether the log file has already proven unwritable. Checked to keep the fallback warning to one
# line per process instead of one per audit event (see chat_logging._write).
_FILE_SINK_BROKEN = False


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=True)
    with _WRITE_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")


def _write(payload: dict) -> None:
    """Persist one audit event, falling back to stdout when the file sink is unwritable.

    Mirrors `chat_logging._write` exactly. In the container `audit_log_file` resolves to
    `/data/logs/audit.jsonl`, which the non-root app user cannot create, so writing it raises
    `PermissionError`. The audit trail is a security artefact; a silently missing one is its own
    incident, and a container platform typically captures stdout (e.g. CloudWatch). So on the
    first OSError we flip to stdout for the life of the process.
    """
    global _FILE_SINK_BROKEN

    if not _FILE_SINK_BROKEN:
        try:
            _append_jsonl(settings.audit_log_file, payload)
            return
        except OSError as exc:
            _FILE_SINK_BROKEN = True
            logger.warning(
                "audit log %s is not writable (%s); falling back to stdout for this process. "
                "Set AUDIT_LOG_PATH to a writable location to restore file logging.",
                settings.audit_log_file,
                exc,
            )

    # The fallback sink. Same JSON, one line, so it stays greppable wherever stdout is collected.
    try:
        logger.info("audit_event %s", json.dumps(payload, ensure_ascii=True))
    except Exception:  # pragma: no cover - a logging sink that itself fails is not recoverable here
        pass


def record(
    event: str,
    *,
    actor: Optional[dict[str, Any]] = None,
    instance_id: Optional[str] = None,
    target: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Append one audit event. `actor` is the caller dict (id/email); never raises."""
    try:
        payload = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "actor_id": (actor or {}).get("id"),
            "actor_email": (actor or {}).get("email"),
            "instance_id": instance_id,
            "target": target,
            "detail": detail or {},
        }
        _write(payload)
    except Exception:
        # Auditing must never break the request it is recording. `_write` already handles the
        # unwritable-`/data` case (stdout fallback); this guards only against an unexpected failure
        # while building the payload, and still leaves a trace rather than a bare `pass`.
        logger.warning("audit event %r was not recorded", event, exc_info=True)
