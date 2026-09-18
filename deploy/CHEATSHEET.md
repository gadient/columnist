# Columnist — Ops Cheat Sheet

Quick reference for running the deployed stack. Full per-script detail is in
[`README.md`](README.md); the step-by-step provisioning runbook is [`../docs/DEPLOY.md`](../docs/DEPLOY.md).
**All commands run from this `deploy/` directory** and need AWS credentials. If creds act stale,
prefix with `AWS_EC2_METADATA_DISABLED=true`.

## Two separate systems

- **Cognito (who can log in)** — a managed user directory + login service. Stores users, checks
  passwords, issues the login tokens. **Invite-only**: the only way an account exists is that you
  create it (`AdminCreateUser` → emails a temporary password → they set a real one on first login).
  Cognito is **always up** — pausing the stack does not affect it.
- **RDS + ECS (the app + its data)** — the FastAPI backend and the Postgres database. This is what
  you **pause** to stop paying when nobody's using it.

## Commands

| Action | Command | Needs | When |
|--------|---------|-------|------|
| **Check state** | `./status.sh` | AWS creds | Anytime — RDS / ECS / CloudFront + who can reach the API |
| **Usage report** | `./monitor.sh` (`DAYS=30 ./monitor.sh`) | AWS creds | Anytime — who's using it, how hard, cost. Read-only |
| **Set up alerting** | `ALERT_EMAIL=you@… ./alerts.sh up` | AWS creds | Once — email on error spikes / budget. `down` to remove, `status` to check |
| **Pause** | `./pause.sh` | AWS creds | You're done. Stops RDS + scales the app to 0 → stops the bill |
| **Resume** | `./resume.sh`, then wait for RDS `available` (`./status.sh`) | AWS creds | Before *any* real use (see below) |
| **Redeploy backend** | `./redeploy-backend.sh` | **Docker running** | Ship backend code (~10–15 min drain; works paused — the new revision starts on resume) |
| **Redeploy frontend** | `./redeploy-frontend.sh` | Node/npm + AWS creds | Ship UI changes (fast; app only works once backend is up) |
| **Invite a PIU** | `./invite-primary.sh <email>` | stack **resumed** | Add a user — fresh private Instance + emailed temp password |
| **Delete a PIU** | `./delete-primary.sh <email>` | stack **resumed** | Remove a user — tears down their Instance **and** login |
| **Brand invite email** | `./set-invite-email.sh` | AWS creds only | Anytime — **Cognito, works even while paused** (`--dry-run` to preview) |

## Monitoring — pull + push

- **Pull** — `./monitor.sh` prints an on-demand report: service health, autoscaling bounds, Cognito
  roster, logins / agent-calls / errors per day, CPU-mem peaks, Bedrock token totals, budget. All
  read-only; run it whenever. `DAYS=` widens the window (default 7).
- **Push** — `ALERT_EMAIL=you@… ./alerts.sh up` sets up **email alerts** (once): a CloudWatch alarm
  on backend error spikes + budget notices at 80/100%. A **paused stack stays silent** (no metrics →
  `notBreaching`). `./alerts.sh status` shows what exists; **`./alerts.sh down` removes all of it** —
  it's step 1 of the [Full teardown](../docs/DEPLOY.md). *After `up`, click the SNS confirmation
  email once* or the error alarm can't reach you.

## When to resume vs. pause

### The three rules (this is the part that trips people up)

1. **Resume** — *only when the stack is currently **paused**.* It starts RDS and takes a few minutes.
   If it's already up, **don't resume** — you'd just stop-then-start for nothing.
2. **Redeploy** — *while the stack is **up**.* (Frontend can technically deploy while paused, but keep
   it up so you can eyeball the result.)
3. **Pause** — ***last**, when you're done*, to stop the bill.

> ⚠️ **`pause → resume → redeploy` is NOT a sequence.** Pausing then immediately resuming cancels out
> and wastes ~5 minutes. **Pause is the *last* step, never the first.**

**So the only question is: is the stack up or paused right now?** (`./status.sh` tells you.)

- **It's already up** → just do your thing → pause when done:
  `./redeploy-frontend.sh` … then `./pause.sh`
- **It's paused** → resume first, wait, do your thing, pause when done:
  `./resume.sh` → wait for RDS `available` → `./redeploy-frontend.sh` → `./pause.sh`

Resume when someone wants to **use** the app, and when you **invite/delete a PIU** (that half needs
the database). A backend redeploy does not need it — but resume anyway if you want to see the new
build running. Pause whenever nobody's using it — that's the default "at rest" state.

## Pause vs. teardown — temporary vs. permanent

- **Pause** (`./pause.sh`) is the everyday "stop the bill": stops RDS + scales ECS to 0. **The
  database and all data survive** — resume any time. This is the default at-rest state.
- **Teardown** is the end-of-project **delete-everything**, including the **database and all user
  data — irreversible**. It is *not* a one-button script, on purpose. The full ordered steps (what
  to delete, in what order, and what to leave alone) live in
  [`../docs/DEPLOY.md`](../docs/DEPLOY.md) → **Full teardown**.

> ⚠️ **When tearing down, do not delete the `default` ECS cluster or the Express gateway ALB by
> hand.** Deleting your ECS *service* (`delete-express-gateway-service`) removes the ALB itself when
> yours was its last service; if other Express services use it, it is shared and must stay. Check
> with `aws ecs list-services --cluster "$CLUSTER"`. Full do-not-delete list in DEPLOY.md.

## Dependency rules that trip people up

- **Anything touching the database** (invite/delete a PIU, a healthy backend) needs RDS
  **`available`** — so `resume` and wait for `status.sh` to confirm it *before* those.
- **`redeploy-backend` needs Docker Desktop running** (it builds the image). It does *not* need the
  service up: it reads the newest ACTIVE task-def revision, so it works paused and the new revision
  starts on the next `resume`.
- **Cognito actions are independent of the pause** — `set-invite-email`, and the login/email half of
  invite/delete, work regardless. The *data* half of invite/delete still needs RDS up.
- **Frontend redeploy** doesn't need RDS/backend to *deploy*, but the app won't function until the
  backend is resumed.
- **A paused URL is safe to hand out.** The SPA is static (CloudFront/S3, always up), so it loads
  even while the stack is paused — but the boot gate **fails closed**: if the backend can't answer
  `/auth/config`, the app shows a "Columnist is offline" maintenance screen instead of the open,
  no-auth shell. No data or auth exposure (every API route is Cognito-enforced server-side). So you
  can share the URL and pause; visitors just see the offline screen until you `resume`.
- A **paused RDS auto-restarts after ~7 days** (AWS behavior) — "paused" isn't forever; re-pause if
  it wakes itself.

## Typical sequences

```bash
# --- Stack is ALREADY UP (you were just using it) ---

# Ship a frontend-only change (e.g. the About pages), then rest:
./redeploy-frontend.sh
./pause.sh                            # done → stop the bill

# --- Stack is PAUSED (starting a fresh session) ---

# Bring it up, then invite someone:
./resume.sh && ./status.sh            # wait for RDS: available
./invite-primary.sh user@example.com
./pause.sh                            # when done

# Ship everything on main to prod (Docker must be running):
./resume.sh && ./status.sh            # wait for RDS: available
./redeploy-backend.sh                 # ~10–15 min (backend)
./redeploy-frontend.sh                # frontend
./pause.sh                            # when done
```
