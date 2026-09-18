# Security

## Posture

Columnist is a portfolio and learning project, not a hardened product. It is not deployed anywhere
by its author, and it comes with no warranty (see `LICENSE`). If you run it, you are its operator:
review the limitations below before putting it on any network you do not fully control.

What it does get right, by design:

- **Fail-closed startup.** With Cognito unconfigured the backend refuses to start unless
  `ALLOW_INSECURE_NO_AUTH=true` is set, so running without authentication is always a deliberate
  choice, never an accident.
- **No tokens in the browser.** Sign-in goes through the backend (a backend-for-frontend), which
  holds Cognito tokens in httpOnly cookies and checks a double-submit CSRF token on every
  cookie-authenticated write to the app routes.
- **Tenant isolation when auth is on.** Board and workspace routes check membership, and every
  query the chat agent can reach is filtered by a per-request allowlist of the caller's boards.
- **A read-only chat agent.** Every tool the agent can call is a `SELECT`; there is no mutating
  tool to reach. The board it is looking at is injected by the server, never chosen by the model.
- **Human approval for AI-proposed cards.** The notes-to-cards agent only recommends. Nothing is
  written to a board until a person approves it, and the write is done by deterministic code.

## Known limitations

- **No authentication in the default local setup.** With Cognito off, every `/api/v1` route is
  open and tenant isolation is a no-op. Never expose a no-auth instance beyond `localhost`.
- **The MCP server is unscoped.** `backend/app/mcp_server.py` reads every board in the database,
  across all tenants. It is meant for a single trusted user over local stdio only. Never expose it
  on a network.
- **Team names are globally unique.** Teams themselves are per-Instance, but the `teams.name`
  column carries a UNIQUE constraint from the original schema, so a name already used by another
  Instance cannot be reused and the create fails. The constraint is not widened because doing so
  needs a table rebuild whose SQL differs between SQLite and PostgreSQL.
- **API documentation is always on.** `/docs`, `/redoc` and `/openapi.json` are served
  unconditionally, including when auth is on.
- **Name searches treat `%` and `_` as wildcards.** User-supplied names reach a `LIKE` pattern
  unescaped. Matches stay inside the caller's own boards, but can be wider than intended.
- **Sessions cannot be refreshed.** A session lasts the 12-hour cookie lifetime, which must match
  the token validity configured on the Cognito app client. After that the user signs in again.
- **Login throttling is Cognito's, not the app's.** The backend adds no rate limiting of its own
  to the sign-in endpoints; it relies on Cognito's throttling and reports it as HTTP 429.
- **Chat content is logged in plain text.** Each chat question and answer is appended to
  `data/logs/chat_completions.jsonl`, with no retention policy or rotation.
- **The notes-to-cards agent can be talked into proposing nothing.** A note written to persuade the
  extractor can suppress cards that should have been proposed. Because a human reviews every
  proposal, this can hide work but cannot create or change it.

## What leaves your machine, and what is kept

If you run Columnist yourself, you are the data controller. In one place, so you do not have to
infer it from the code:

| Data | Where it goes |
|---|---|
| A note's full text, and the board's member names | Sent to whichever model provider you configure (`AGENT_BACKEND`) on every extraction. Nothing is sent while the default `offline` backend is set |
| A chat question, and the board data the tools return | Sent to the same provider on every chat turn |
| Chat questions and answers | Appended in plain text to `data/logs/chat_completions.jsonl`; falls back to stdout, and so to the container log, when that path is not writable |
| Audit events, including the actor's email address | Appended to `data/logs/audit.jsonl`, with the same stdout fallback |
| Feedback: free text, and the sender's email | Stored in the database and, when `FEEDBACK_S3_BUCKET` is set, mirrored to that bucket |
| The uploaded note file itself | Not stored. The proposal, its supporting excerpts and a fingerprint of the input are |

There is **no retention policy and no rotation**: nothing above is deleted until you delete it, and
the app shows no privacy notice to the people whose names and notes it processes. If you deploy it
for anyone but yourself, that is yours to write.

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** on this repository (the *Security* tab,
then *Report a vulnerability*) rather than opening a public issue. Include the affected file or
endpoint, the steps to reproduce, and what an attacker gains. This is a one-person project, so
expect a reply within a couple of weeks, not hours.
