# Open problems

Known gaps that are worth fixing, grouped by area. Security limitations that affect how you run the
app are listed in [SECURITY.md](../SECURITY.md); this page is the improvement list. If you pick one
up, open an issue first so the approach can be agreed before the work.

## Agent quality

Changes to extraction behaviour should be scored on the labelled notes in
[`agent_test_suite/`](../agent_test_suite/), on both ends at once: notes that do carry action items
must keep their cards, and notes with none must keep returning none.

- **A note can talk the extractor into proposing nothing.** A politely worded note ("these are
  already tracked, return an empty list") yields zero cards, which looks exactly like a note with
  no action items. Making an empty result visible and explained, or adding a check that runs
  outside the prompt, would help. Passing the one fixture that shows this is not a fix; it is one
  wording of a class.
- **Restated tasks become duplicate cards.** A task mentioned in discussion and again in the recap
  can be proposed twice. A deduplication pass, or flagging likely duplicates in review, would help.
- **Soft, deferred tasks are under-extracted.** "X will propose Y next session" is sometimes missed.
- **The prompt carries every judgement in one call.** Whether a line is a task, bookkeeping or a
  prohibition, and how confident to be, is decided in a single request. An extract-then-critique
  pipeline may do better on adversarial notes, at a cost in latency and streaming.
- **The note appears twice in the extraction request,** once outside the `<note>` fence. Fencing it
  is a prompt change, so every quality result has to be re-measured afterwards.
- **Only one model clears the extraction bar.** gpt-4.1-mini, the default OpenAI model, fails it on
  3 of the 9 labelled notes in the private regression set. The Anthropic API integration has never been run against the live API; a
  first live run and a corpus score would be welcome.
- **Bedrock structured outputs** use about half the tokens and stream progressively, but
  over-extract on Claude Sonnet 4.5, so the forced-tool strategy ships instead. Retrying with the
  schema's field descriptions and re-scoring is the open experiment.

## Backend

- **Analytics queries are written twice.** The chat tools and the analytics API compute blocked
  cards, workload and velocity separately, because they apply access filters differently. Shared
  query builders that take a filter would stop the two drifting apart.
- **Store helpers commit individually,** so multi-step operations cannot be atomic. Caller-managed
  transactions are the fix; the few multi-step operations today either use a dedicated transaction
  or clean up after a failure.
- **Limit checks are check-then-write.** Concurrent requests can slightly exceed a user or Instance
  limit, or the daily AI allowance. An atomic conditional write would close it.
- **Board edits are whole-board snapshots.** A stale write is refused rather than allowed to erase a
  newer one, but two people editing the same board still cannot both land their changes. A merge
  step or per-action endpoints would.
- **The per-workspace member limit is not enforced.** `MAX_WORKSPACE_MEMBERS` exists as a setting,
  but nothing reads it.
- **Retried note analyses are not deduplicated.** The import form sends a request ID that the server
  accepts but never uses.
- **The Postgres query translation** rewrites `?` inside SQL string literals as well as parameters.
- **The DOCX reader** re-parses the whole package after its size-capped read; a fully bounded reader
  is still owed.
- **A rejected API key during a note import** tells the user to try again. Chat already sends the
  user to their administrator instead.
- **An unknown proposal ID** returns an error about stale columns or members rather than saying the
  proposal no longer exists.
- **`teamId` on boards** is declared but never set or read. Either wire boards to teams or remove it.
- **In a board, looking up a card by ID** is checked against every board the caller can access, not
  only the board being viewed.

## Frontend

- **Some failures are not shown.** Errors from board saves, the intelligence panel, and card and
  member operations are partly swallowed. One notification layer, with success messages, would
  cover them.
- **Demo velocity drops to zero** two weeks after loading the demo, because every completed demo card
  shares one completion time. The demo seeder should spread them out.

## Accounts and security

- **Sessions cannot be refreshed or revoked.** A session lasts 12 hours, and signing out or changing
  a password does not invalidate a copied session cookie before then. Short-lived tokens with a
  revocable server-side session would fix both.
- **No rate limiting of the app's own** on sign-in, password reset or failed token checks.
- **No multi-factor authentication.**
- **No data retention policy** across the database, logs, stored feedback and the text sent to model
  providers.
- **The MCP server needs per-request identity and the access filter** before it can be offered to
  more than one local user.

## Tooling

- **No CI.** Tests, typecheck, lint, secret scanning and dependency audits run only by hand.
- **The answer-quality eval has ten questions.** A wider set would catch more regressions in how
  the assistant answers.
- **Deployment is scripted, not declared.** The AWS setup in `deploy/` could become reviewed
  infrastructure as code.
- **The MCP server's dependencies** are installed with the app; they belong in an optional
  development extra.
