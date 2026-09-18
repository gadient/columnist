from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .config import settings

logger = logging.getLogger(__name__)

_WRITE_LOCK = Lock()

# Whether the log file has already proven unwritable. Checked to keep the fallback warning to one
# line per process instead of one per chat message.
_FILE_SINK_BROKEN = False


def new_request_id() -> str:
    return uuid4().hex


def is_mcp_server_up() -> bool:
    pid_file = settings.mcp_pid_file
    if not pid_file.exists():
        return False
    try:
        raw_pid = pid_file.read_text(encoding="utf-8").strip()
        pid = int(raw_pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False


def _truncate(value: str | None, max_chars: int = 2000) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=True)
    with _WRITE_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")


def log_chat_completion(
    *,
    request_id: str,
    message: str,
    active_board_id: str | None,
    valid: bool,
    reason: str,
    tool_called: str | None,
    mcp_server_up: bool,
    response: str,
    error: str | None,
    duration_ms: int,
) -> None:
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "message": _truncate(message),
        "active_board_id": active_board_id,
        "valid": valid,
        "reason": reason,
        "tool_called": tool_called,
        "mcp_server_up": mcp_server_up,
        "response": _truncate(response),
        "error": _truncate(error),
        "duration_ms": duration_ms,
    }
    _write(payload)


def _write(payload: dict) -> None:
    """Record one completion. Never raises — logging must not decide whether chat works.

    In the container `chat_log_path` resolves to `/data/logs/...`, which the non-root app user
    cannot create. An exception here would turn a request that already succeeded into a 500, and
    the failure is invisible locally because the path is always writable there. `audit_log.py`
    mirrors this function; keep the two in step.

    Falling back to stdout rather than dropping the record: this is a security-review artefact, a
    container platform typically captures stdout (e.g. CloudWatch), and a silently missing audit
    trail is its own incident.
    """
    global _FILE_SINK_BROKEN

    if not _FILE_SINK_BROKEN:
        try:
            _append_jsonl(settings.chat_log_file, payload)
            return
        except OSError as exc:
            _FILE_SINK_BROKEN = True
            logger.warning(
                "chat audit log %s is not writable (%s); falling back to stdout for this process. "
                "Set CHAT_LOG_PATH to a writable location to restore file logging.",
                settings.chat_log_file,
                exc,
            )

    # The fallback sink. Same JSON, one line, so it stays greppable wherever stdout is collected.
    try:
        logger.info("chat_completion %s", json.dumps(payload, ensure_ascii=True))
    except Exception:  # pragma: no cover - a logging sink that itself fails is not recoverable here
        pass
