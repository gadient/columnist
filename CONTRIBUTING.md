# Contributing

Thanks for looking. This is a one-person project, so a small, focused pull request with a clear
reason is the easiest thing to review. For anything larger, open an issue first so we can agree on
the shape before you spend time on it. Security issues go through `SECURITY.md`, not an issue.
Known gaps worth working on are listed in [`docs/open-problems.md`](docs/open-problems.md).

## Setup

See the README for running the app. For development, also:

```bash
bash scripts/install-git-hooks.sh   # once: blocks home-directory paths and runs gitleaks on commit
```

The pre-commit hook needs [gitleaks](https://github.com/gitleaks/gitleaks) installed.

## Before you open a pull request

```bash
bash scripts/test.sh          # backend suite: fast, free, never calls a model
npm run typecheck             # frontend
npm run build                 # typecheck + production build
```

All three must pass. Add or update a test for any behaviour change; `backend/tests/README.md`
explains how the suite is organised.

## Sign your commits (DCO)

Contributions are accepted under the [Developer Certificate of Origin](https://developercertificate.org/):
by signing off, you certify that you wrote the change or otherwise have the right to submit it
under the project's licence (Apache-2.0). Add a sign-off line to every commit with `-s`:

```bash
git commit -s -m "fix(chat): handle an empty tool result"
```

which appends `Signed-off-by: Your Name <you@example.com>`. Commit messages follow the
`type(scope): summary` convention.

## Things that will bite you

None of these are obvious from the code.

**Database**

- **Every query must run on both SQLite and PostgreSQL.** Tests run on SQLite; a deployment can run
  Postgres (`DATABASE_URL`). `db.py` is a thin shim, not a translator. Avoid `rowid`,
  `AUTOINCREMENT` semantics, `strftime` and other SQLite-isms, and qualify every column in an
  `ON CONFLICT … DO UPDATE SET` (it is ambiguous on Postgres). `backend/tests/test_sql_portability.py`
  catches the known cases; a green suite does not prove Postgres compatibility on its own.
- **Convert a `sqlite3.Row` to a dict before calling `.get()`** on it: `rd = dict(row)`.

**Board writes**

- **Board writes are full-snapshot `PUT`s guarded by a server-owned `version`.** They must be
  serialised per board on the client (`boardWriteChain` in `src/App.tsx`), or two overlapping
  writes both send the same stale version, the second gets a 409, and a just-created card silently
  disappears. Never go back to a bare optimistic-then-replace write.
- **Card ids are `crypto.randomUUID()`**, not timestamps: `cards.id` is globally unique, and two
  cards created in the same millisecond would collide.

**Backend**

- **`backend/app/mcp_server.py` must not use `from __future__ import annotations`.** Lazy string
  annotations break FastMCP's tool registration.
- **The chat agent's tools run sequentially in one thread on purpose.** The tenant fence is a
  `ContextVar`, and ContextVars do not propagate into executor threads. Parallelising tool calls
  would drop the fence in every worker while every test still passed. Use
  `contextvars.copy_context()` if you ever change this.
- **Pydantic v2:** `model_dump()`, not `.dict()`.
- **Empty 204 responses:** return `Response(status_code=204)` from the handler; don't rely on a
  `response_model` on the decorator.
- **Tests pin Cognito off and `AGENT_BACKEND=offline`** (`backend/tests/conftest.py`), so the suite
  never needs credentials and never spends money, whatever your local `.env` says.

**Model behaviour**

- **Change prompts against the evals, not by eye.** Model quality is measured, not unit-tested:
  `scripts/corpus-eval.sh` scores notes-to-cards extraction against the labelled notes in
  `agent_test_suite/`, `scripts/chat_contract_eval.py` scores chat answers, and
  `scripts/agent-tool-eval.sh` checks tool selection and prompt-injection resistance. These call a
  real model and cost money. Model responses are cached, so re-scoring is free; the chat and
  tool-selection evals also have an `--offline` self-check, and the scorers themselves are
  unit-tested in the free suite.
- **Bump `PROMPT_VERSION`** in `backend/app/note_imports/prompt.py` whenever you change the
  extraction prompt. The corpus cache keys on it. (The chat eval hashes its prompt automatically.)
- **Quality does not transfer between models.** A score measured on one model says nothing about
  another; re-run the eval on the model you actually configure.

**Frontend and docs**

- **The About page renders three docs verbatim** (`docs/roadmap.md`,
  `docs/design/architecture.md`, `docs/design/TECH-STACK.md`). Edit the doc, never the component.
- **Demo due dates are offsets from today** (`src/data/demoDates.ts`), not fixed dates, so the demo
  never rots into "everything is overdue".
- **Scripts must run on macOS's bash 3.2**, so no `wait -n`, associative arrays or other bash 4
  features.
