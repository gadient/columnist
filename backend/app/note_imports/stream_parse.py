"""Incremental extraction of array elements from a JSON document as it streams.

The model returns one object: ``{"recommendations": [ {…}, {…}, … ]}``. To show a card the moment
it is finished rather than after the whole array closes, we scan the token stream and emit each
top-level object once its closing brace arrives. The first unquoted ``[`` starts the scan, and the
document is assumed to hold that one array and nothing structural after it — true of the contract
this parses, and the reason the scanner need not track the array's close.

This is a deliberately tiny, single-purpose scanner — not a general JSON parser. It tracks exactly
what it needs to find object boundaries safely:

  - **string state**, so a ``{`` or ``}`` inside a quoted value (an excerpt, a title) never moves
    the depth counter;
  - **escape state**, so a ``\\"`` inside a string does not end the string early;
  - **brace depth** *within the array*, so nested objects (``dueDate``, ``priority``,
    ``evidence[]``) are spanned correctly and an element is emitted only when it fully closes.

It emits the raw JSON substring of each element; validating that substring against the contract is
the caller's job (`provider.parse_one`), keeping "find the boundary" and "trust the content"
separate. A malformed element is still a complete brace span — it is emitted, then rejected in
validation, which is how a partial failure is surfaced rather than swallowed.
"""
from __future__ import annotations


class RecommendationStreamScanner:
    """Feed it text deltas; get back the JSON of each array element as it completes.

    Stateful and single-pass. `feed(delta)` returns a list of completed element strings found in
    that delta (usually zero or one, occasionally more if a delta spans a boundary)."""

    def __init__(self) -> None:
        self._buf: list[str] = []
        self._in_string = False
        self._escaped = False
        self._entered_array = False  # have we seen the array's opening '['?
        self._depth = 0              # object-brace depth inside the array
        self._start: int | None = None  # index in _buf where the current element began

    def feed(self, delta: str) -> list[str]:
        completed: list[str] = []
        for ch in delta:
            self._buf.append(ch)
            i = len(self._buf) - 1

            # String/escape state is tracked first and unconditionally: a brace inside a quoted
            # value must never be mistaken for structure.
            if self._in_string:
                if self._escaped:
                    self._escaped = False
                elif ch == "\\":
                    self._escaped = True
                elif ch == '"':
                    self._in_string = False
                continue
            if ch == '"':
                self._in_string = True
                continue

            if not self._entered_array:
                # Everything up to and including the array's '[' is preamble: the outer '{', the
                # "recommendations" key. The first unquoted '[' opens the array we care about.
                if ch == "[":
                    self._entered_array = True
                continue

            if ch == "{":
                if self._depth == 0:
                    self._start = i
                self._depth += 1
            elif ch == "}":
                if self._depth > 0:
                    self._depth -= 1
                    if self._depth == 0 and self._start is not None:
                        completed.append("".join(self._buf[self._start : i + 1]))
                        self._start = None
        return completed
