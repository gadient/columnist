# Documentation index

| Document | What it is |
|---|---|
| [`../README.md`](../README.md) | What Columnist is, how to run it, how it was built |
| [`product.md`](product.md) | What the app does, for whom, and where its edges are: the assistant, notes to cards, accounts and tenancy, known limits |
| [`decisions.md`](decisions.md) | The forks that shaped it: what was chosen, what was turned down, and why |
| [`open-problems.md`](open-problems.md) | Known gaps worth fixing, by area |
| [`design/architecture.md`](design/architecture.md) | C4 diagrams from system context down to backend components, one key runtime flow, and the AWS layout it once ran on. **Shown in-app** |
| [`design/TECH-STACK.md`](design/TECH-STACK.md) | The stack, piece by piece: frontend, backend, data, AI, auth, infrastructure. **Shown in-app** |
| [`roadmap.md`](roadmap.md) | What is built, and the directions it could grow in. **Shown in-app** |
| [`DEPLOY.md`](DEPLOY.md) | The AWS deployment runbook: every account-specific value is a variable, and `deploy/bring-up.sh` automates it |
| [`../deploy/README.md`](../deploy/README.md) | The day-to-day ops scripts: redeploy, pause/resume, status, invites |
| [`../SECURITY.md`](../SECURITY.md) | Security posture, known limitations, how to report a vulnerability |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Development checks, commit sign-off, and the gotchas worth knowing first |
| [`../backend/tests/README.md`](../backend/tests/README.md) | How the backend test suite is organised |
| [`../agent_test_suite/README.md`](../agent_test_suite/README.md) | The labelled meeting notes the extraction agent is scored against |

**Shown in-app** means the About page renders the file verbatim at build time
(`src/components/about/AboutModal.tsx`). Edit the document, and the page follows on the next build.
