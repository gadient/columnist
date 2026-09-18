# deploy/ — operational scripts

> **In a hurry?** See [`CHEATSHEET.md`](CHEATSHEET.md) — pause / resume / redeploy / invite / delete
> at a glance, with what each needs and when to run it.

> **Provisioning leaves the app public and unauthenticated until its last phases.** The ECS
> service created in the `ecs` phase has its own public URL and runs with
> `ALLOW_INSECURE_NO_AUTH=true`; `wire` then adds CloudFront; only `cognito-on` closes it. Anyone
> with either address can read and write every board in between, and two manual console gates sit
> in that window. Run `ecs` → `cognito-on` in one sitting, or lock the load balancer to your own IP
> first (`docs/DEPLOY.md` §7). If a run is interrupted, `pause.sh` the service until you can
> finish.

Small, reusable scripts for the day-to-day running of the AWS deploy (same-origin,
Cognito-enforced, ALB reachable via CloudFront only).
They cover the things you actually repeat; the one-time *provisioning* (creating
ECR / RDS / the ECS service / CloudFront) is documented step-by-step in
[`../docs/DEPLOY.md`](../docs/DEPLOY.md) and scripted by `bring-up.sh`. Two steps
stay in the console on purpose — creating the ECS Express **service** and the
CloudFront **distribution**: `bring-up.sh` stops at each, prints the exact settings,
and continues when you re-run it.

## Setup (once)

The scripts don't hardcode any account-specific IDs. Copy the template and fill
in your deployment's identifiers:

```bash
cp deploy/deploy.env.example deploy/deploy.env   # git-ignored; each line has a hint command
```

`config.sh` refuses to run until `SERVICE`, `ALB_NAME`, `CLOUDFRONT_DIST_ID`,
`CLOUDFRONT_DOMAIN`, and `API_HOST` are set (via `deploy.env` or exported inline).

## Prerequisites

- AWS CLI v2, logged in (`aws sts get-caller-identity` works). Region is handled
  by the scripts (`--region "$REGION"`, default `us-east-1`); no need to export it.
- `python3` (the scripts that change the task definition use it to edit the JSON).
- Node + `npm` for the frontend build (`redeploy-frontend.sh`).

Run them from this directory, e.g. `bash status.sh`.

## config.sh

Sourced by every script. Holds the stable resource **names** and derives
instance-specific IDs (account, ALB security group, task definition) at runtime
so nothing goes stale. Override anything via the environment:

```bash
SERVICE=some-other-svc bash status.sh
```

## Scripts

| Script | What it does |
|--------|--------------|
| `bring-up.sh [phase]` | Stand the whole stack up from zero (the inverse of DEPLOY.md → *Full teardown*). Runs its phases in order, stops at the two console gates (ECS Express service, CloudFront distribution) with the exact settings, and continues when re-run; idempotent. `list` prints the phases; `-y` skips prompts. Refuses to run against an ACTIVE service unless `--force`. Does not need `deploy.env` filled in first. |
| `status.sh` | One-glance health: ECS service, RDS, CloudFront, and who can reach the API. Start here. |
| `monitor.sh` | One-glance usage + capacity report: service health, autoscaling bounds, Cognito roster, logins / agent calls / errors per day, CPU-memory peaks, Bedrock token totals, budget. Read-only. `DAYS=` sets the window (default 7). |
| `alerts.sh up\|down\|status` | Push alerting: an SNS email subscription, a CloudWatch alarm on backend error spikes (tracebacks / 5xx), and budget email notices at 80% + 100%. `down` removes exactly what `up` created (step 1 of the Full teardown). `up` needs `ALERT_EMAIL`. |
| `redeploy-frontend.sh` | Rebuild the SPA → sync to S3 → invalidate the CloudFront cache. Run after any frontend change. |
| `redeploy-backend.sh [tag]` | Rebuild the backend image → push to ECR → roll the ECS service onto it (env/secrets preserved). Run after any backend change. Works while paused too — it reads the newest ACTIVE task-def revision, and the new one starts on the next `resume.sh`. |
| `update-cors.sh [origin]` | Set the backend `CORS_ORIGINS` (default: the CloudFront domain) by registering a new task-def revision and redeploying. |
| `access-cloudfront.sh` | **The access model** — allow only CloudFront's origin-facing prefix list on :443, and drop any world rule. This is the intended access model: the ALB is unreachable except via CloudFront, so all traffic passes the SPA/CloudFront edge and then Cognito at the backend. (Your-IP rules from the optional IP-locked stage, DEPLOY.md §7, are left in place.) |
| `access-open.sh` | **Break-glass public mode** — open the ALB to the whole internet. Not part of normal operation (traffic goes through CloudFront). **Refuses to run when the deployed service has auth off** (override: `I_ACCEPT_PUBLIC_NO_AUTH=true`). See DEPLOY.md → *Access modes*. |
| `pause.sh` | Save cost — scale the service to 0 and stop RDS. Nothing is destroyed. |
| `resume.sh` | Undo `pause.sh` — start RDS and scale the service back to 1. |

### AI agents / Bedrock

| Script | What it does |
|--------|--------------|
| `set-agent-backend.sh` | Switch the deployed AI backend. Default turns **Bedrock ON** (`AGENT_BACKEND=bedrock` + model/region/strategy) by registering a new task-def revision and redeploying; `--backend offline` rolls back to the stub. **Prereq:** the task role must carry `bedrock:InvokeModel` + `…WithResponseStream` and model access must be enabled — one-time IAM/console steps in DEPLOY.md → *Bedrock*. Billable per call. |
| `set-env.sh` | Set/clear plain env vars on the running service (`KEY=VALUE`, `--unset KEY`, `--show`). Registers a task-def revision and rolls. **Not for secrets** — those go in SSM and ride `secrets`, not `environment`. Note `--show` reads the *running* task, so mid-drain it still shows the previous revision. |

### Auth / Cognito

| Script | What it does |
|--------|--------------|
| `set-cognito-env.sh` | Turn auth **ON**: store the app-client secret in SSM (SecureString, read from a hidden prompt — never an argument) and redeploy the task def with the Cognito env + secret reference. `--skip-secret` re-applies env without re-storing the secret. |
| `invite-primary.sh <email> ["Space"]` | Invite someone as a Primary user (PIU): seeds their private Instance + PIU in the DB **and** creates their Cognito account (emails a temp password). Idempotent. |
| `delete-primary.sh <email>` | **The inverse of invite-primary.** Deletes a PIU by email in one shot: tears down their whole Instance (all data) **and** deletes the Cognito logins, so they can't sign in. Typed-email confirmation. Re-invite afterwards with `invite-primary.sh` (a fresh Instance). Only acts on a PIU — remove a secondary user (SIU) via the app's *Manage members*. |
| `takedown-instance.sh [instance_id]` | Cascade-delete an Instance and all its data (hard delete) **by instance id**. With no id, lists Instances so you can find it. Cognito logins are left alone; the delete commands are printed if you want them gone too. (Use `delete-primary` when you're removing a PIU by email.) |
| `set-invite-email.sh [--dry-run]` | Customize the Cognito invite email (subject + body) with the product name and sign-in URL. Passes the pool's current settings back so nothing resets, and keeps invite-only on. The FROM address stays Cognito's default. Cognito only, so it works while paused. |

The DB-side work (`invite-primary`, `delete-primary`, `takedown-instance`) runs
**inside the live container via ECS Exec** — see below — because the RDS database
is in the VPC. The underlying CLI is `python -m app.admin` (also runnable directly
on a bastion or locally with `DATABASE_URL` set).

> **IAM:** `delete-primary` deletes Cognito accounts. The in-container command deletes each member's
> account if the **task role** carries `cognito-idp:AdminDeleteUser`; regardless, the wrapper deletes
> the PIU's account here with **your** credentials, so the PIU is always removed. Grant the task role
> that permission if you also want SIU logins deleted automatically (else delete them by hand — the
> command prints which failed).

## Notes

- **The intended access model is `access-cloudfront.sh`:** the ALB accepts only CloudFront's prefix
  list, so every request passes the edge and then Cognito at the backend. Don't reach for
  `access-open.sh` — it publishes the ALB to the internet and is break-glass only. It still fails
  closed if the deployed service has auth off (it inspects the live task def), because world-open +
  `ALLOW_INSECURE_NO_AUTH=true` would give anyone read/write on every board.
- After `resume.sh`, the app stays unhealthy until RDS reports `available`.
- These scripts change live infrastructure. Read one before running it if unsure.

### ECS Exec prereqs (for `invite-primary.sh` / `delete-primary.sh` / `takedown-instance.sh`)

These run a command inside the running task, which requires:

1. The service was created/updated with **`--enable-execute-command`** (one-time:
   `aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --enable-execute-command --force-new-deployment`).
2. The **task role** allows `ssmmessages:*` (ECS Exec channel); the standard
   ECS-Exec task-role policy covers it.
3. The **Session Manager plugin** is installed locally (`session-manager-plugin`).

For `set-cognito-env.sh`, the **task execution role** additionally needs
`ssm:GetParameters` on the `COGNITO_SSM_SECRET_PARAM` so the container can resolve
the secret at launch. `kms:Decrypt` is needed only if the parameter uses a
customer-managed KMS key (see DEPLOY.md §4); the default `aws/ssm` key needs none.
