from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import settings


@dataclass
class ValidationResult:
    valid: bool
    reason: str


@dataclass
class ChatResponseResult:
    valid: bool
    response: str
    reason: str
    tool_called: str | None
    error: str | None = None
    #: The full `AgentResult` when the tool-calling backend produced this, else None. Carries the
    #: real token counts and the whole tool trace, so the endpoint can meter and log what actually
    #: happened instead of re-estimating it from the text.
    agent: Any = None


# ── input gate for the tool-calling agent ─────────────────────────────────────

def validate_agent_input(message: str) -> ValidationResult:
    """Shape checks only — nothing about the message's *content*.

    A keyword allowlist plus a regex blocklist is the natural gate to reach for, and it is the wrong
    one in front of a tool-calling model: an allowlist rejects most of the questions the agent
    answers correctly, and a blocklist pattern like ``\\bdan\\b`` (for the "DAN" jailbreak) refuses
    "What is Dan working on?" at every scope. Such a gate only works where the keyword match *is*
    the router — no keyword, no query to run — which is not this design.

    What takes its place is not a better list. It is the structure — every tool is a SELECT, the
    tenant fence sits below the model, ``active_board_id`` is server-injected — plus the system
    prompt for the part structure cannot enforce. See ``agents/loop.py``.

    So this checks only what the *transport* needs: something to send, and not so much of it that
    one message dominates the token budget.
    """
    text = (message or "").strip()
    if not text:
        return ValidationResult(False, "Message is empty")
    if len(text) > settings.agent_max_message_chars:
        return ValidationResult(False, "Message too long")
    return ValidationResult(True, "Accepted")
