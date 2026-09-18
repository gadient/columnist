# DEPLOY.md — AWS deployment runbook

This runbook stands Columnist up on AWS, in your own account, as a small invite-only deployment:

```
Browser ──HTTPS──▶ CloudFront ($CLOUDFRONT_DOMAIN)
                     ├─ default behavior ─▶ S3 (the React build, private bucket)
                     └─ /api/*  behavior ─▶ ECS Express (Fargate, ARM64) ──▶ RDS PostgreSQL (private)
                                              secrets: SSM Parameter Store
                                              sign-in: Cognito (backend-for-frontend, httpOnly cookies)
                                              AI agents: Amazon Bedrock
```

It is sized for a handful of users: one container, one `db.t4g.micro` database. Set the budget
alarm in §9 before you invite anyone.

**Two ways to run it.** `deploy/bring-up.sh` performs this runbook as a sequence of idempotent
phases. It stops at the two steps AWS recommends doing in the console (creating the ECS Express
service, and creating the CloudFront distribution), prints the exact settings, and continues when you
re-run it. Or follow this document by hand. Read it either way: it explains every setting and the
traps that are not obvious from AWS's documentation.

```bash
cd deploy && ./bring-up.sh list        # phases, and which are already done
```

**Order of the runbook.** Steps 1–8e bring the infrastructure up with sign-in still off and the
load balancer reachable only from your own IP, so you can test the plumbing privately. §8f then
switches to the final, same-origin topology, and the Cognito section turns sign-in on. The
scripted path skips the IP-locked stage and goes straight to the CloudFront-only lock (§8f-2).

---

## Configuration

Every account-specific value in this runbook is a shell variable. They come from three places.

**1. Values you choose.** Put them in `deploy/deploy.env` (git-ignored; start from the template):

| Variable | Meaning |
|---|---|
| `REGION` | AWS region for everything (default `us-east-1`). Must have Bedrock access to Claude Sonnet 4.5 |
| `ALERT_EMAIL` | Where the budget alarm and error alerts are sent |
| `DEPLOY_ALLOWED_IPV6_CIDR`, `DEPLOY_ALLOWED_IPV4_CIDR` | Optional. Your own address, only for the IP-locked stage (§7) |

**2. Resource names, with defaults.** Override any of them in `deploy/deploy.env`. The defaults live
in `deploy/config.sh` and `deploy/bring-up.sh`, and must agree between the two.

| Variable | Default |
|---|---|
| `CLUSTER` | `default` (the cluster ECS Express creates) |
| `ECR_REPO` | `kanban-backend` |
| `IMAGE_TAG` | `v1` |
| `RDS_ID` | `kanban-db` |
| `SSM_PARAM` | `/kanban/DATABASE_URL` |
| `COGNITO_SSM_SECRET_PARAM` | `/kanban/COGNITO_APP_CLIENT_SECRET` |
| `EXEC_ROLE_NAME` | `ecsTaskExecutionRole` |
| `TASK_ROLE_NAME` | `kanban-task-bedrock` |
| `POOL_NAME` | `kanban-users` |
| `BUDGET_NAME` | `kanban-monthly` |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |

**3. Values AWS hands back.** Record each in `deploy/deploy.env` at the step that creates it; the
day-to-day scripts in `deploy/` read them from there.

| Variable | Recorded at |
|---|---|
| `SERVICE`, `ALB_NAME`, `API_HOST` | §5, after the ECS Express service exists |
| `CLOUDFRONT_DIST_ID`, `CLOUDFRONT_DOMAIN` | §8c, after the distribution exists |
| `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID` | *Cognito*, after the pool exists |
| `BEDROCK_TASK_ROLE_ARN` | *Bedrock*, only if you create a new task role |

**Set up each shell** you run commands from (repo root):

```bash
cp -n deploy/deploy.env.example deploy/deploy.env      # once; then fill it in as you go
set -a; . deploy/deploy.env; set +a                    # re-run after every edit to deploy.env
: "${REGION:=us-east-1}" "${CLUSTER:=default}" "${ECR_REPO:=kanban-backend}" "${IMAGE_TAG:=v1}" \
  "${RDS_ID:=kanban-db}" "${SSM_PARAM:=/kanban/DATABASE_URL}" \
  "${COGNITO_SSM_SECRET_PARAM:=/kanban/COGNITO_APP_CLIENT_SECRET}" \
  "${EXEC_ROLE_NAME:=ecsTaskExecutionRole}" "${TASK_ROLE_NAME:=kanban-task-bedrock}" \
  "${POOL_NAME:=kanban-users}" "${BUDGET_NAME:=kanban-monthly}"
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR=$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$ECR_REPO
export FRONTEND_BUCKET=kanban-frontend-$ACCOUNT_ID
export AWS_EC2_METADATA_DISABLED=true    # expired credentials fail fast instead of hanging
```

No command below needs hand-editing. Where a value only exists once an earlier command has run, that
command captures it into a variable for the next one.

---

## 0. Prerequisites

- An AWS account, and the AWS CLI v2 signed in to it.
- Docker with `buildx` (the image is built for ARM64), and Node.js for the frontend build.
- Bedrock model access to Claude Sonnet 4.5 in `$REGION`, if you want the AI agents (see *Bedrock*).

Everything below uses ARM64 (Graviton) to match the image and keep costs down.

---

## 1. ECR — container registry

```bash
aws ecr create-repository --repository-name "$ECR_REPO" --region "$REGION"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
```

## 2. Build + push the ARM64 image

```bash
docker buildx build --platform linux/arm64 -t "$ECR:$IMAGE_TAG" --push backend
```

> **Architecture must match.** The image is `linux/arm64`, so the ECS task in §5 **must** set
> `cpuArchitecture=ARM64`. A mismatch fails at container start with `exec format error`.

## 3. RDS PostgreSQL (`db.t4g.micro` — Graviton, free-tier eligible)

Create the instance with no public access. Generate a strong password and keep it out of shell
history where possible.

```bash
export DB_PASS=$(openssl rand -base64 24 | tr -d '/+=')   # RDS rejects / @ " and spaces

aws rds create-db-instance \
  --db-instance-identifier "$RDS_ID" \
  --db-instance-class db.t4g.micro \
  --engine postgres \
  --allocated-storage 20 \
  --master-username kanbanadmin \
  --master-user-password "$DB_PASS" \
  --db-name kanban \
  --no-publicly-accessible \
  --backup-retention-period 1 \
  --region "$REGION"
```

Wait for it, then read the endpoint:
```bash
aws rds wait db-instance-available --db-instance-identifier "$RDS_ID" --region "$REGION"
export DB_HOST=$(aws rds describe-db-instances --db-instance-identifier "$RDS_ID" \
  --query 'DBInstances[0].Endpoint.Address' --output text --region "$REGION")
```

> The RDS security group must allow inbound 5432 **from the ECS service's security group only**
> (not the internet). Set that once both exist (§6).

## 4. SSM Parameter Store — secrets (free, no static keys in the image)

```bash
aws ssm put-parameter --name "$SSM_PARAM" --type SecureString \
  --value "postgresql://kanbanadmin:$DB_PASS@$DB_HOST:5432/kanban" --region "$REGION"
```

The ECS task injects this as the `DATABASE_URL` environment variable at start (§5). The Cognito
app-client secret is added the same way later, by `deploy/set-cognito-env.sh`.

The **execution role** needs to read it. Create `$EXEC_ROLE_NAME` if your account does not have it
(attach the AWS-managed `AmazonECSTaskExecutionRolePolicy`), then add:

```bash
aws iam put-role-policy --role-name "$EXEC_ROLE_NAME" --policy-name kanban-ssm-read \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
    "Action":"ssm:GetParameters",
    "Resource":["arn:aws:ssm:'"$REGION"':'"$ACCOUNT_ID"':parameter'"$SSM_PARAM"'",
                "arn:aws:ssm:'"$REGION"':'"$ACCOUNT_ID"':parameter'"$COGNITO_SSM_SECRET_PARAM"'"]}]}'
```

`kms:Decrypt` is **only** needed if the parameter uses a customer-managed KMS key. With the default
`alias/aws/ssm` key (what the command above uses), `ssm:GetParameters` alone is enough.

## 5. ECS Express service (Fargate, ARM64)

> **The unauthenticated window opens here, not at §8f.** Express Mode gives the service its own
> public `*.on.aws` URL, and the task definition in this section carries
> `ALLOW_INSECURE_NO_AUTH=true` — so from the moment the service reports healthy until you turn
> sign-in on (*Cognito*, below), anyone who has that URL can read and write every board. Two manual
> console gates sit in between. Either do §7 first and keep the load balancer locked to your own
> address until sign-in is on, or treat §5 → §8f → *Cognito* as one uninterrupted sitting. If you
> have to stop, `deploy/pause.sh` scales the service to zero.

ECS Express Mode provisions the cluster, task definition, load balancer, autoscaling, HTTPS and a
public URL from the container image. Use the console for this step (`bring-up.sh` stops here and
prints the same table). The CLI for Express is new; confirm it against current AWS documentation
before scripting it.

| Setting | Value |
|---------|-------|
| Image | `$ECR:$IMAGE_TAG` |
| **CPU architecture** | **ARM64** (must match the image — see §2) |
| **Container port** | **`8000`** (the `Dockerfile` exposes 8000. A wrong port means the health check never passes and ECS restarts the task forever) |
| Health check path | `/api/v1/health` |
| Env var (plain) | `ALLOW_INSECURE_NO_AUTH=true` (lowercase `true`; it is a Pydantic bool). Removed when Cognito is turned on |
| Env var (plain) | `CORS_ORIGINS=https://placeholder.invalid` for now; the real value is set in §8e |
| Secret (from SSM) | `DATABASE_URL` ← `$SSM_PARAM` (the bare name works for a same-region parameter; the full ARN also works) |
| Execution role | `$EXEC_ROLE_NAME` — the one given `ssm:GetParameters` in §4 |
| Task role | Empty for now (the app makes no AWS calls until Cognito and Bedrock are on). See *Bedrock* |
| Compute | `0.25 vCPU` / `0.5 GB` |
| Auto scaling | min `1` / max `1` |
| CloudWatch logs | **Enabled** (the default) — essential for diagnosing a failed boot |

**Console wizard notes:**
- **Private registry authentication** — leave **unchecked**. ECR authenticates through the execution
  role.
- **Infrastructure role** — leave the default (`ecsInfrastructureRoleForExpressServices`); Express uses
  it to create the load balancer and networking.
- **Command** — leave **blank**; the image's `CMD` starts uvicorn.
- **Networking** — put the task in the **same VPC as RDS** (the default VPC, if you created RDS
  without a custom subnet group), in **two subnets in different availability zones** (the load
  balancer requires two). The default security group is fine. Default-VPC subnets assign a public
  IP, so the task can reach ECR and SSM.

Migrations run automatically on first boot.

**Record the three values** in `deploy/deploy.env`, then re-source it:

```bash
# SERVICE — the service name (it has a random suffix)
aws ecs list-services --cluster "$CLUSTER" --region "$REGION" --query 'serviceArns' --output text
# ALB_NAME — the load balancer Express created
aws elbv2 describe-load-balancers --region "$REGION" --query 'LoadBalancers[].LoadBalancerName' --output text
# API_HOST — the *.on.aws hostname the service answers on (shown on the service page in the console)
```

`deploy/deploy.env.example` shows a CLI route to `API_HOST` as well.

> **Expect the service NOT to go healthy until §6.** The app runs migrations on startup, but the RDS
> security group does not admit the task yet, so the task boots, fails to reach Postgres, and
> restarts. That is normal; §6 fixes it and the service settles.

## 6. Wire RDS ↔ ECS security groups

Find the two security groups. The task's is easiest to read off its network interface (Express
does not expose it on the service object):
```bash
# ECS task SG
TASK=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" --region "$REGION" \
  --query 'taskArns[0]' --output text)
ENI=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK" --region "$REGION" \
  --query "tasks[0].attachments[0].details[?name=='networkInterfaceId'].value | [0]" --output text)
export TASK_SG=$(aws ec2 describe-network-interfaces --network-interface-ids "$ENI" --region "$REGION" \
  --query 'NetworkInterfaces[0].Groups[0].GroupId' --output text)

# RDS SG
export RDS_SG=$(aws rds describe-db-instances --db-instance-identifier "$RDS_ID" --region "$REGION" \
  --query 'DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId' --output text)

echo "task: $TASK_SG   rds: $RDS_SG"
```

These take the first group of each, which is all there is in the default setup. If either carries
several, drop the `[0]` to list them.

> **Common case — nothing to do.** If you accepted the default VPC and default security group for
> both, `$TASK_SG` and `$RDS_SG` are **the same group**, and the default group ships with a
> self-referencing rule that allows all traffic between its members. Verify:
> ```bash
> aws ec2 describe-security-groups --group-ids "$RDS_SG" --region "$REGION" \
>   --query 'SecurityGroups[0].IpPermissions' --output json
> ```
> A rule whose `UserIdGroupPairs` references that same `sg-…` means you are done.

Only if they are in **different** groups (or the self-referencing rule was removed), allow the ECS
group to reach Postgres and nothing else:
```bash
aws ec2 authorize-security-group-ingress \
  --group-id "$RDS_SG" --protocol tcp --port 5432 \
  --source-group "$TASK_SG" --region "$REGION"
```

## 7. Lock the load balancer to your IP (recommended until sign-in is on)

Sign-in is still off at this point and the service already has a public URL (§5), so this is the
only thing standing between the internet and every board until *Cognito* below. Skip it only if you
are going straight through §8f to sign-in in one sitting — which is what `bring-up.sh` assumes.

```bash
export ALB_SG=$(aws elbv2 describe-load-balancers --names "$ALB_NAME" --region "$REGION" \
  --query 'LoadBalancers[0].SecurityGroups[0]' --output text)

# Allow only you. An IPv6 /64 prefix is stable across a home router's address changes.
aws ec2 authorize-security-group-ingress --group-id "$ALB_SG" --region "$REGION" --ip-permissions \
  "IpProtocol=tcp,FromPort=443,ToPort=443,Ipv6Ranges=[{CidrIpv6=$DEPLOY_ALLOWED_IPV6_CIDR}]"

# Remove the default open-to-the-world rule
aws ec2 revoke-security-group-ingress \
  --group-id "$ALB_SG" --protocol tcp --port 443 --cidr 0.0.0.0/0 --region "$REGION"
```

To reach it over IPv4 instead, add `$DEPLOY_ALLOWED_IPV4_CIDR` the same way. That address can
change; `curl -s https://checkip.amazonaws.com` prints the current one.

## 8. Frontend — build + S3 + CloudFront

The frontend is a static build served by CloudFront from a private S3 bucket.

> ⚠️ **8a–8e build a cross-origin setup for the IP-locked stage** (the browser calls the API
> directly on `$API_HOST`). It works while sign-in is off, but it is **incompatible with sign-in**:
> the session cookie is `SameSite=Lax` (not sent on a cross-site `fetch`) and the CSRF cookie would
> live on the API's origin, unreadable by the app. **Before turning Cognito on, do §8f.** If you
> skipped §7, build with `VITE_API_BASE_URL=/api/v1` in 8a and go to §8f after 8d.

**8a. Build, pointing the app at the API** (baked in at build time):
```bash
VITE_API_BASE_URL="https://$API_HOST/api/v1" npm run build   # -> dist/
```

**8b. Private bucket + upload:**
```bash
aws s3 mb "s3://$FRONTEND_BUCKET" --region "$REGION"
aws s3 sync dist/ "s3://$FRONTEND_BUCKET" --delete
```
Leave *Block all public access* on; CloudFront reads the bucket privately through Origin Access
Control.

**8c. CloudFront over the private bucket.** Create the Origin Access Control:
```bash
OAC_ID=$(aws cloudfront create-origin-access-control \
  --origin-access-control-config '{"Name":"kanban-frontend-oac","OriginAccessControlOriginType":"s3","SigningBehavior":"always","SigningProtocol":"sigv4"}' \
  --query 'OriginAccessControl.Id' --output text)
```
Then create the distribution (console, or `aws cloudfront create-distribution`) with: default root
object `index.html`, *Redirect HTTP to HTTPS*, `PriceClass_100`, the managed *CachingOptimized*
policy, and the S3 origin using `$OAC_ID`.

**Deep links: use the `spa-router` function, not custom error responses.** A single-page app needs
`/board/abc` to return `index.html`. The common recipe, a `403/404 → 200 /index.html` custom error
response, is **distribution-wide**: it also rewrites every `/api/*` error into `200 text/html`, so
a failed API call looks like a success and the real error disappears. Instead, create a CloudFront
Function from [`deploy/cloudfront/spa-router.js`](../deploy/cloudfront/spa-router.js) (its header
has the steps), publish it, and attach it as a **viewer request** function on the **default
behavior only**. Add no custom error responses.

**Record** the distribution's `Id` as `CLOUDFRONT_DIST_ID` and its `DomainName` as
`CLOUDFRONT_DOMAIN` in `deploy/deploy.env`, then re-source it.

**8d. Let only this distribution read the bucket:**
```bash
cat > bucket-policy.json <<JSON
{ "Version":"2012-10-17","Statement":[{
  "Effect":"Allow","Principal":{"Service":"cloudfront.amazonaws.com"},
  "Action":"s3:GetObject","Resource":"arn:aws:s3:::$FRONTEND_BUCKET/*",
  "Condition":{"StringEquals":{"AWS:SourceArn":"arn:aws:cloudfront::$ACCOUNT_ID:distribution/$CLOUDFRONT_DIST_ID"}}}]}
JSON
aws s3api put-bucket-policy --bucket "$FRONTEND_BUCKET" --policy file://bucket-policy.json
aws cloudfront wait distribution-deployed --id "$CLOUDFRONT_DIST_ID"
```

**8e. Point the backend's CORS at CloudFront** (cross-origin stage only):
```bash
deploy/update-cors.sh    # sets CORS_ORIGINS=https://$CLOUDFRONT_DOMAIN and rolls the service
```
Environment variables live in the task definition, which is immutable, so changing one means
registering a new revision and redeploying. `update-cors.sh` and the other `deploy/set-*.sh` scripts
do that read-modify-register-roll cycle for you.

Redeploy the frontend at any time with `deploy/redeploy-frontend.sh` (build, sync, invalidate).

### 8f. Same-origin cutover (required before Cognito)

> **Do not stop part-way through this section.** The service has been publicly reachable since §5
> (see the warning there); 8f-1 adds a second public door via CloudFront, and the backend still runs
> with `ALLOW_INSECURE_NO_AUTH=true` until you turn sign-in on in 8f-4. Run 8f-1 through 8f-4 in one
> sitting, and if you have to stop, scale the service to zero (`deploy/pause.sh`) rather than
> leaving it up.

**Why:** sign-in rides on cookies, and cookies only work first-party — `SameSite=Lax` sends them
and the app can read the CSRF cookie — when the app **and** the API answer on the *same origin*. So
`/api/*` is routed **through the same CloudFront distribution** to the load balancer, as a second
origin. That means the load balancer must accept CloudFront's traffic instead of only yours, which
is fine: sign-in replaces the network lock as the gate.

**8f-1. Add the load balancer as a second origin + an `/api/*` behavior.** In the console
(CloudFront → your distribution → *Origins* / *Behaviors*):

- **New origin** → domain `$API_HOST`, **protocol HTTPS only**, origin path empty, id `alb-api`.
- **New behavior** → **path pattern `/api/*`**, origin `alb-api`,
  **viewer protocol policy: Redirect HTTP to HTTPS**,
  **allowed methods: GET, HEAD, OPTIONS, PUT, POST, PATCH, DELETE** (writes must pass),
  **cache policy: `CachingDisabled`** (managed id `4135ea2d-6df8-44a3-9df3-4b5a84be39ad`),
  **origin request policy: `AllViewerExceptHostHeader`** (managed id
  `b689b0a8-53d0-40ab-baf2-68738e2966ac`),
  **compress objects automatically: No**.

  The origin request policy and `CachingDisabled` together forward **all cookies, headers
  (`Cookie`, `Authorization`, `X-CSRF-Token`) and query strings** and **never cache** API
  responses. Without them CloudFront strips `Set-Cookie` or serves one user's response to another.

  > **Use `AllViewerExceptHostHeader`, not `AllViewer`.** The ECS Express load balancer is a
  > gateway that routes by `Host`: it forwards to your service only when `Host` is the `*.on.aws`
  > name, and returns a bare `404` otherwise. `AllViewer` forwards the *viewer's* `Host`
  > (`$CLOUDFRONT_DOMAIN`), so every `/api/*` request 404s at the load balancer.
  >
  > **The symptom misleads.** The failure surfaces as `HTTP 200`, `content-type: text/html` and
  > `server: AmazonS3`, which looks like the behavior did not match. The tells are
  > **`x-cache: Error from cloudfront`** and an identical `etag` on every path. Confirm by replaying
  > the `Host` directly against the load balancer:
  >
  > ```bash
  > curl -s -i -H "Host: $CLOUDFRONT_DOMAIN" "https://$API_HOST/api/v1/health" | head -6
  > # 404 + server: awselb/2.0  ->  it is the Host header, not routing, caching or a firewall
  > ```

  **Compression is off on purpose:** gzip buffers the response, which collapses the streamed
  notes-to-cards results (`/analyze/stream`, one card per line) into a single late flush.

  CLI equivalent (read-modify-write of the whole distribution config):
  ```bash
  ETAG=$(aws cloudfront get-distribution-config --id "$CLOUDFRONT_DIST_ID" --query ETag --output text)
  aws cloudfront get-distribution-config --id "$CLOUDFRONT_DIST_ID" --query DistributionConfig > cf.json
  # Edit cf.json: append the origin to .Origins.Items (+1 Quantity), and prepend the /api/* entry
  # to .CacheBehaviors.Items (+1 Quantity) with the settings above. Then:
  aws cloudfront update-distribution --id "$CLOUDFRONT_DIST_ID" \
    --distribution-config file://cf.json --if-match "$ETAG"
  aws cloudfront wait distribution-deployed --id "$CLOUDFRONT_DIST_ID"
  ```

**8f-2. Let only CloudFront reach the load balancer:**
```bash
deploy/access-cloudfront.sh    # allows CloudFront's origin-facing prefix list on :443, removes world-open rules
```

> **Do not open the load balancer to the world here** (`deploy/access-open.sh`). Only CloudFront
> needs to reach it, and sign-in is still off at this point, so a world-open load balancer would let
> anyone who finds `$API_HOST` read and write every board until 8f-4 lands. `access-cloudfront.sh`
> admits exactly CloudFront's AWS-managed ranges, and stays correct after sign-in is on because it
> also stops anyone bypassing CloudFront. `access-open.sh` refuses to run while sign-in is off
> unless you set `I_ACCEPT_PUBLIC_NO_AUTH=true`.

Verify:
```bash
curl -s "https://$CLOUDFRONT_DOMAIN/api/v1/health"     # {"status":"ok"}, through CloudFront
```

**8f-3. Rebuild the frontend same-origin** (a relative API base, the script's default):
```bash
deploy/redeploy-frontend.sh    # builds with VITE_API_BASE_URL=/api/v1, syncs S3, invalidates
```

**8f-4. Turn sign-in on** — see *Cognito* below, and do it immediately. 8f-2 closes the *direct*
path: the load balancer is never reachable without auth. The distribution itself is public from
8f-1 onward, and the backend still carries `ALLOW_INSECURE_NO_AUTH=true` until this step — so
between 8f-2 and 8f-4 anyone who knows the CloudFront domain can read and write every board. Keep
that window to minutes, not hours. Then load `https://$CLOUDFRONT_DOMAIN`, sign in and create a
card: that exercises the session cookie **and** the CSRF header end to end.

**8f-5. CORS no longer matters.** Same-origin requests are not subject to CORS; you can leave
`CORS_ORIGINS` or clear it.

## Access modes

Two **independent** layers gate the app:

| Layer | Private | Open |
|------|---------------|-----------|
| **Network** (load balancer security group) | your IP only (§7), or CloudFront only (§8f-2) | `0.0.0.0/0` |
| **Sign-in** (Cognito) | off: `ALLOW_INSECURE_NO_AUTH=true` | on: every `/api/v1` route needs a session |

- **Private testing:** your IP + sign-in off. Only you can reach it.
- **Invite-only (the intended end state):** CloudFront only + sign-in on. Anyone can reach the
  front door; only invited users get in, including you.
- **Never:** open network + sign-in off. Anyone could read and write everything.

Turn sign-in on **before** widening the network, or you land in the "never" row.

## 9. Cost guard — budget alarm

```bash
aws budgets create-budget --account-id "$ACCOUNT_ID" \
  --budget '{"BudgetName":"'"$BUDGET_NAME"'","BudgetLimit":{"Amount":"20","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"'"$ALERT_EMAIL"'"}]}]'
```

Adjust `"Amount"` to taste; the alert fires at 80% of it. `deploy/alerts.sh up` adds an alarm on
backend error rate, emailed to the same address.

---

## Verify

```bash
curl -s "https://$CLOUDFRONT_DOMAIN/api/v1/health"     # {"status":"ok"}
deploy/status.sh                                        # service, database, CloudFront, who can reach the API
```
Then open `https://$CLOUDFRONT_DOMAIN`, sign in, and click **Load Demo Workspace**. The data lands
in RDS.

## Bedrock — turning on the AI agents

With `AGENT_BACKEND` unset, chat reports itself unavailable and notes-to-cards proposes nothing. To
run them, the container calls Amazon Bedrock, which needs two things: an IAM **task role** allowed to invoke the
model, and the `AGENT_BACKEND` and `BEDROCK_*` environment. boto3 in the container takes its
credentials from the task role (not the execution role, and never static keys).

Use **Claude Sonnet 4.5** on the `tool` structured-output strategy. It is the model that passed the
extraction quality bar (90% precision / 80% recall on the labelled notes in `agent_test_suite/`);
the Amazon Nova models did not. The `json_schema` strategy runs but over-extracts on Sonnet.

**B-1. Enable model access** (one-time, per account and region): Bedrock console → *Model access*
→ Claude Sonnet 4.5 in `$REGION`. Check from your shell:
```bash
bash scripts/bedrock-probe.sh --region "$REGION" list      # the model id should appear
```

**B-2. Create the task role.** A task has exactly **one** task role. If yours already has one,
attach the policy below to it and leave `BEDROCK_TASK_ROLE_ARN` blank. Otherwise:
```bash
# Trust policy: ECS tasks may assume it.
aws iam create-role --role-name "$TASK_ROLE_NAME" \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
    "Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

# Least-privilege invoke. A us.* inference profile routes across regions, so allow BOTH the profile
# ARN and the regional foundation-model ARNs it can land on.
aws iam put-role-policy --role-name "$TASK_ROLE_NAME" --policy-name bedrock-invoke \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
    "Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],
    "Resource":[
      "arn:aws:bedrock:*:'"$ACCOUNT_ID"':inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
      "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0"]}]}'
```
Record the role's ARN as `BEDROCK_TASK_ROLE_ARN` in `deploy/deploy.env`.

The same role also needs the ECS Exec and Cognito permissions described under *Cognito*; one role
can hold all three policies.

**B-3. Switch the backend on** (registers a new task-definition revision with the environment and
the task role, then rolls the service):
```bash
deploy/set-agent-backend.sh                  # AGENT_BACKEND=bedrock, model + region from deploy.env
deploy/set-agent-backend.sh --backend offline   # to switch it off again
```

**B-4. Verify** once the service is healthy: import a note (exercises `InvokeModel`) and use the
streaming import (exercises `InvokeModelWithResponseStream`). An `AccessDenied` in the CloudWatch
logs means the role is missing the policy or model access is not enabled; locally, `BEDROCK_DEBUG=1`
shows the provider's error. Model calls are billed per request, so have the §9 budget alarm in place
first.

## Cognito — turning on sign-in

Sign-in is invite-only and runs through the backend: the browser posts credentials to the API, which
calls Cognito and sets the tokens as httpOnly cookies. The app enforces sign-in on every `/api/v1`
route once `COGNITO_REGION`, `COGNITO_USER_POOL_ID` and `COGNITO_APP_CLIENT_ID` are all set. See
`SECURITY.md` for what that does and does not protect.

### C-1. Create the pool and app client

The settings are not arbitrary; they are what `backend/app/cognito_auth.py` requires.

```bash
aws cognito-idp create-user-pool --region "$REGION" --pool-name "$POOL_NAME" \
  --username-attributes email --auto-verified-attributes email \
  --admin-create-user-config AllowAdminCreateUserOnly=true \
  --policies 'PasswordPolicy={MinimumLength=12,RequireUppercase=true,RequireLowercase=true,RequireNumbers=true,RequireSymbols=false}'
# Record the pool's Id as COGNITO_USER_POOL_ID in deploy/deploy.env, re-source it, then:

aws cognito-idp create-user-pool-client --region "$REGION" \
  --user-pool-id "$COGNITO_USER_POOL_ID" --client-name kanban-web \
  --generate-secret \
  --explicit-auth-flows ALLOW_ADMIN_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH \
  --id-token-validity 12 --access-token-validity 12 --refresh-token-validity 30 \
  --token-validity-units AccessToken=hours,IdToken=hours,RefreshToken=days
# Record the client's ClientId as COGNITO_APP_CLIENT_ID. The secret is never written to deploy.env.
```

- `--generate-secret` is **required**: the backend computes `SECRET_HASH` on every call.
- `--username-attributes email` **cannot be changed** after creation. Wrong here means delete and redo.
- `AllowAdminCreateUserOnly=true` disables public sign-up. This is what makes it invite-only.
- **The 12-hour token validity is not optional.** The session cookie lasts 12 hours
  (`AUTH_COOKIE_TTL_SECONDS`, default 43200) and the two must agree. Cognito's own default is
  **60 minutes**: leave it and users stay *looking* signed in while every request fails an hour
  after login.

### C-2. Give the task role what sign-in needs

The backend calls Cognito from Python, so those calls run as the **task role**. Attach to it
(`$TASK_ROLE_NAME`, from *Bedrock* B-2):

- `cognito-idp:AdminInitiateAuth`, `AdminRespondToAuthChallenge`, `AdminCreateUser`, and
  `AdminDeleteUser`, scoped to the pool's ARN. Without them, sign-in fails with `AccessDenied`
  while everything else looks healthy. Password reset and sign-out use unauthenticated Cognito APIs
  and need no permission.
- `ssmmessages:CreateControlChannel`, `CreateDataChannel`, `OpenControlChannel`,
  `OpenDataChannel` on `Resource: "*"` (these actions do not support resource scoping), for ECS
  Exec — the invite scripts run their database step inside the container, because RDS is only
  reachable from the VPC.

`deploy/bring-up.sh` (phase `task-role`) creates the role with all of these at once.

### C-3. Turn it on

```bash
deploy/set-cognito-env.sh    # stores the app-client secret in SSM, sets the COGNITO_* env,
                             # removes ALLOW_INSECURE_NO_AUTH, and rolls the service
```

### C-4. Invite yourself

```bash
deploy/invite-primary.sh "$ALERT_EMAIL" "Your Space"
```

This creates an Instance (the tenant boundary), makes you its primary user, and sends the Cognito
invitation email with a temporary password. Three prerequisites, all described in
`deploy/README.md` → *ECS Exec prereqs*: the service updated with `--enable-execute-command` *and*
rolled (it applies only to newly launched tasks), the `ssmmessages` policy above, and the AWS
**Session Manager plugin** installed locally. Missing any of them fails at the exec step with an
error that does not name the cause.

---

## Pause to save cost

You do not have to destroy the stack to stop the bill:
```bash
deploy/pause.sh     # scales the service to 0 and stops the database (AWS restarts a stopped DB after 7 days)
deploy/resume.sh
```
The database and all data survive. While paused, the frontend still loads from CloudFront and shows
a "Columnist is offline" screen instead of the app.

## Full teardown (deletes the database and all data)

**Irreversible.** It destroys the Postgres database and every user's data. For a temporary stop use
*Pause* above. Run `deploy/status.sh` first and confirm every name below is yours.

Delete in this order, from the front of the request path backwards, so nothing routes to a
half-deleted resource:

1. **Alerts and budget** — `deploy/alerts.sh down`, then
   `aws budgets delete-budget --account-id "$ACCOUNT_ID" --budget-name "$BUDGET_NAME"`.
2. **CloudFront** — an enabled distribution cannot be deleted, so disable it first (the wait is
   about 15 minutes). The `spa-router` function outlives the distribution; delete it too.
   ```bash
   ETAG=$(aws cloudfront get-distribution-config --id "$CLOUDFRONT_DIST_ID" --query ETag --output text)
   aws cloudfront get-distribution-config --id "$CLOUDFRONT_DIST_ID" --query DistributionConfig > cf.json
   python3 -c "import json; c=json.load(open('cf.json')); c['Enabled']=False; json.dump(c, open('cf.json','w'))"
   aws cloudfront update-distribution --id "$CLOUDFRONT_DIST_ID" --distribution-config file://cf.json --if-match "$ETAG"
   aws cloudfront wait distribution-deployed --id "$CLOUDFRONT_DIST_ID"
   aws cloudfront delete-distribution --id "$CLOUDFRONT_DIST_ID" \
     --if-match "$(aws cloudfront get-distribution --id "$CLOUDFRONT_DIST_ID" --query ETag --output text)"
   aws cloudfront delete-function --name spa-router \
     --if-match "$(aws cloudfront describe-function --name spa-router --query ETag --output text)"
   ```
3. **S3** — `$FRONTEND_BUCKET`, and the feedback bucket if you set `FEEDBACK_S3_BUCKET`. A
   versioned bucket must be emptied of *all versions and delete markers* (`list-object-versions` →
   `delete-objects`) before `aws s3 rb`.
4. **ECS service** — use `delete-express-gateway-service`, **not** `delete-service --force`, which
   fails with `InvalidParameterException: ResourceManagementType=ECS use DeleteExpressGatewayService`.
   It stops the tasks and removes the managed infrastructure: the load balancer (if no other Express
   service uses it), the autoscaling target and its CPU alarms.
   ```bash
   SERVICE_ARN=$(aws ecs list-services --cluster "$CLUSTER" --region "$REGION" \
     --query "serviceArns[?ends_with(@, '/$SERVICE')] | [0]" --output text)
   aws ecs delete-express-gateway-service --service-arn "$SERVICE_ARN" --region "$REGION"
   ```
5. **RDS** — `aws rds delete-db-instance --db-instance-identifier "$RDS_ID" --skip-final-snapshot
   --delete-automated-backups`. To keep a parting copy, replace `--skip-final-snapshot` with
   `--final-db-snapshot-identifier "$RDS_ID-final"`.
6. **CloudWatch log group** — find it, then `delete-log-group`:
   `aws logs describe-log-groups --log-group-name-prefix "/aws/ecs/$CLUSTER/$SERVICE" --query 'logGroups[].logGroupName'`.
7. **SSM** — `aws ssm delete-parameters --names "$SSM_PARAM" "$COGNITO_SSM_SECRET_PARAM"`.
8. **ECR** — `aws ecr delete-repository --repository-name "$ECR_REPO" --force` (removes the images).
9. **Cognito** — `aws cognito-idp delete-user-pool --user-pool-id "$COGNITO_USER_POOL_ID"` (the app
   client goes with it). This ends every sign-in, so do it last. Finish with
   `aws cognito-idp list-user-pools --max-results 20` to catch any stray pool from an earlier attempt.
10. **Autoscaling leftovers** — if step 4 left them, `deregister-scalable-target` for
    `service/$CLUSTER/$SERVICE` (this also removes its CPU alarms).
11. **IAM** (optional; roles cost nothing) — detach policies from `$TASK_ROLE_NAME` and delete it;
    remove the `kanban-ssm-read` inline policy from `$EXEC_ROLE_NAME`.

**Do not delete:**
- **The `default` ECS cluster** — Express manages it, and an empty cluster is free.
- **The Express gateway load balancer, if other Express services exist.** It routes by `Host` and
  can be shared; step 4 removes it only when yours was its last service. Check with
  `aws ecs list-services --cluster "$CLUSTER"`. An orphaned load balancer bills about $16–18 a
  month, so confirm it is gone when it should be.
- **Anything this runbook did not create.** Other projects in the account can have look-alike names.
- **Bedrock model access** — an account setting, not billed.

Afterwards `deploy/status.sh` errors on the missing service; that is the expected "it's gone"
signal. Then sweep for the things that bill quietly and are easy to orphan: NAT gateways,
unattached Elastic IPs, VPC endpoints, EBS volumes, load balancers, and **RDS snapshots** (automated
ones can list for a few minutes after the instance is gone — recheck until
`describe-db-snapshots` is empty).
