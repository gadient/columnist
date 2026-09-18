"""The chat agent — a bounded tool-calling loop, in Converse's message shape, over three providers.

Selected by ``settings.agent_backend`` being ``openai``, ``anthropic`` or ``bedrock``; any other
backend makes chat report itself unavailable (``agent_api.py``).

- ``tools.py``: the 13 read-only tool specs and ``dispatch()`` over ``mcp_queries``.
- ``loop.py``: the bounded loop — max turns, per-request token budget, wall clock, all fail-closed.
- ``bedrock_provider.py``: the ``ChatProvider`` over ``client.converse``.
- ``openai_provider.py`` / ``anthropic_provider.py``: ``ChatProvider``s that translate the loop's
  Converse-shaped messages to and from those APIs.
- ``contract_score.py``: a model-free scorer for answer quality, used by tests and evals only.

The notes-to-cards agent lives separately in ``app/note_imports/``. ``LLMProvider`` below is the
original prose-completion seam; neither agent uses it (see ``note_imports/provider.py`` for why).
"""
from __future__ import annotations

from typing import Protocol


class LLMProvider(Protocol):
    """Unused. Kept as the original prose-completion seam; neither agent calls it (see above)."""

    def complete(self, prompt: str, *, max_tokens: int) -> str:  # pragma: no cover - scaffold
        ...
