"""Per-request context shared without import cycles.

`accessible_boards_ctx` holds the set of board ids the current caller may see, or ``None`` for
"unrestricted" (local dev / unscoped). It is set by the chat endpoint and read by `mcp_queries`
so every chat/MCP card lookup is transparently limited to the caller's boards.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

accessible_boards_ctx: ContextVar[Optional[set[str]]] = ContextVar("accessible_boards", default=None)
