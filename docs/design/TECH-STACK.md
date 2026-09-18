<!-- Rendered verbatim in the app's About → Tech Stack page; this file is the source for it. -->
# Columnist — Tech Stack

## Frontend

| Piece | Choice |
|-------|--------|
| Language | TypeScript |
| Framework | React 18 |
| Build tool | Vite 7 |
| Styling | Tailwind CSS, with a CSS-variable token layer for light/dark themes |
| Icons | lucide-react |
| Docs rendering | marked (Markdown) + mermaid (diagrams, lazy-loaded) |

## Backend

| Piece | Choice |
|-------|--------|
| Language | Python 3.11+ (3.12 in the container image) |
| Framework | FastAPI (Uvicorn) |
| Data models | Pydantic v2 |
| Database access | Raw SQL, no ORM — a thin adapter over SQLite / PostgreSQL |
| Migrations | Plain SQL migrations applied in order at startup |

## Data

| Piece | Choice |
|-------|--------|
| Local dev | SQLite (local file) |
| Deployed (AWS) | Amazon RDS PostgreSQL |
| Selection | Configuration selects the engine — same code path, same migrations, both engines |

## AI

| Piece | Choice |
|-------|--------|
| Provider | One setting for both agents: Amazon Bedrock (Converse API), the OpenAI API, or the Anthropic API |
| Model | Claude Sonnet 4.5 on Bedrock is the measured configuration; each provider's model is configurable |
| Chat agent | A bounded, read-only tool-calling loop (13 tenant-fenced tools) |
| Notes → cards | A structured extraction call over the same provider |
| Cost guards | Per-user daily token meter; per-request bounds (turns / tokens / wall-clock) |

## Authentication

| Piece | Choice |
|-------|--------|
| Identity provider | Amazon Cognito (invite-only, no public signup) |
| Pattern | Backend-for-Frontend — the server holds tokens, the browser gets httpOnly cookies |
| Token verification | RS256 / JWKS (PyJWT), issuer + audience pinned |
| CSRF | Double-submit cookie + header on state-changing routes |

## Infrastructure (AWS)

The AWS deployment path; nothing is currently deployed.

| Piece | Choice |
|-------|--------|
| Compute | Amazon ECS Express Mode on Fargate (ARM64) |
| Edge / static | Amazon CloudFront + S3 (same-origin; `/api/*` routed to the ALB) |
| Registry | Amazon ECR |
| Secrets | SSM Parameter Store (SecureString) |
| Logs | Amazon CloudWatch |
| Network | ALB reachable only via CloudFront's origin-facing prefix list |

## Dev tooling

| Piece | Choice |
|-------|--------|
| Backend tests | pytest (SQLite) |
| Frontend gate | `tsc --noEmit` runs before `vite build` |
| Secret scanning | gitleaks pre-commit hook |
