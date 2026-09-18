"""Notes-to-Cards import.

Turns one pasted note / TXT / DOCX into proposed **new cards** on the board the user is viewing.
Create-only: no reconciliation, no updates, no completion inference, no board design.

The module boundary mirrors the pipeline's stages:

- ``errors``   — the error catalog. Every failure carries a code AND a user action.
- ``extract``  — one source in, normalized text + stable locators out. Rejects everything
  it cannot read *before* any model call.
- ``prompt``   — the extraction prompt: the rules in the system message, everything derived
  from the note below them in the user message.
- ``provider`` — the model seam. One interface, prompt in and schema-validated recommendations out.
- ``schema``   — the ``Model*`` / ``Resolved*`` contract. The split is the security design:
  the model is given nowhere to put an id, an approval, or any action but "create".
- ``resolve``  — deterministic resolution: owner matching against the roster, column stamping,
  and the evidence and date checks that turn model claims into a reviewable proposal.
- ``store``    — session/recommendation/application persistence.
- ``api``      — the endpoints.

**The mutation boundary** is the point of this whole module: the model may recommend, it
may not write. Only deterministic application code mutates the board, and only after an explicit
human action tied to the exact approved snapshot.
"""
