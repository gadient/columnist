#!/usr/bin/env bash
#
# bring-up.sh — stand the WHOLE stack back up from zero, after a Full teardown.
#
# This is the inverse of docs/DEPLOY.md → "Full teardown". Pause/resume never
# delete anything; THIS is for when you did the irreversible teardown (DB + all
# resources gone) and want it all back.
#
# It is NOT a fire-and-forget one-shot, on purpose. Two steps in the runbook are
# console-first by AWS's own guidance and would be worse if scripted blind:
#   (G1) creating the ECS Express service   (DEPLOY.md §5)
#   (G2) creating the CloudFront dist + the 8f /api/* behavior  (DEPLOY.md §8c/§8f)
# At each of those the script STOPS, prints the exact settings, and tells you what
# to paste into deploy/deploy.env. Then you RE-RUN it and it continues past the
# gate. Everything is idempotent, so re-running is safe.
#
# Usage:
#   ./bring-up.sh                       # run all phases in order, stopping at gates
#   ./bring-up.sh --email you@example.com --space "Your Space"   # also invite yourself at the end
#   ./bring-up.sh <phase>               # run a single phase (see list below)
#   ./bring-up.sh list                  # list the phases, in order
#   ./bring-up.sh -y ...                # don't prompt before mutating AWS
#   ./bring-up.sh --force ...           # run even though deploy.env points at an ACTIVE service
#
# Phases:  preflight ecr rds exec-role task-role cognito-pool budget
#          GATE:ecs  frontend  GATE:cloudfront  wire  cognito-on  bedrock-on  invite
#
# Requires: aws CLI v2 (authenticated), Docker with buildx, node/npm. Run from deploy/.
#
set -euo pipefail
cd "$(dirname "$0")"

# ── This is a BOOTSTRAP script: it does NOT source config.sh. config.sh runs
#    require_config at load time and would die because SERVICE/ALB_NAME/... don't
#    exist yet when you're building from zero. We replicate the few helpers and
#    read deploy.env directly (it may be empty on the first run). ────────────────
DEPLOY_ENV="$PWD/deploy.env"
[ -f "$DEPLOY_ENV" ] && { set -a; . "$DEPLOY_ENV"; set +a; }

# Non-secret defaults — MUST match config.sh so the day-2 scripts agree with us.
: "${REGION:=us-east-1}"
: "${CLUSTER:=default}"
: "${ECR_REPO:=kanban-backend}"
: "${RDS_ID:=kanban-db}"
: "${SSM_PARAM:=/kanban/DATABASE_URL}"
: "${COGNITO_SSM_SECRET_PARAM:=/kanban/COGNITO_APP_CLIENT_SECRET}"
: "${BEDROCK_MODEL_ID:=us.anthropic.claude-sonnet-4-5-20250929-v1:0}"
: "${IMAGE_TAG:=v1}"
: "${TASK_ROLE_NAME:=kanban-task-bedrock}"
: "${EXEC_ROLE_NAME:=ecsTaskExecutionRole}"
: "${POOL_NAME:=kanban-users}"
: "${BUDGET_NAME:=kanban-monthly}"
# deploy.env-sourced IDs that don't exist until their phase runs — default them
# empty so `set -u` never trips when a gate message or check references them.
: "${SERVICE:=}"; : "${ALB_NAME:=}"; : "${API_HOST:=}"
: "${CLOUDFRONT_DIST_ID:=}"; : "${CLOUDFRONT_DOMAIN:=}"
: "${COGNITO_USER_POOL_ID:=}"; : "${COGNITO_APP_CLIENT_ID:=}"
: "${BEDROCK_TASK_ROLE_ARN:=}"; : "${ALERT_EMAIL:=}"
export AWS_EC2_METADATA_DISABLED=true   # fail fast on expired creds instead of hanging

say()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m %s\n'  "$*" >&2; }
die()  { printf '\033[31m!!\033[0m %s\n'  "$*" >&2; exit 1; }
gate() { printf '\n\033[35m######## MANUAL GATE ########\033[0m\n%s\n' "$*" >&2; exit 10; }
have() { command -v "$1" >/dev/null 2>&1; }

# Secrets reach the aws CLI as `file://` from a private (0600) temp file, never as an argument,
# so they stay out of the process list. Each call site removes the file right after; the EXIT
# trap removes one an early exit left behind. printf is a builtin and adds no trailing newline.
SECRET_TMP=""
trap 'rm -f ${SECRET_TMP:+"$SECRET_TMP"}' EXIT
secret_to_file() { SECRET_TMP="$(mktemp)"; printf '%s' "$1" > "$SECRET_TMP"; }
secret_file_done() { rm -f "$SECRET_TMP"; SECRET_TMP=""; }

ASSUME_YES=0
FORCE=0
EMAIL=""; SPACE=""

# Upsert one KEY=VALUE into deploy/deploy.env (git-ignored). Creates the file from
# the tracked template on first write so the comments/hints come along.
set_env() {
  local key="$1" val="$2" tmp
  if [ ! -f "$DEPLOY_ENV" ]; then
    cp deploy.env.example "$DEPLOY_ENV" 2>/dev/null || : > "$DEPLOY_ENV"
  fi
  if grep -qE "^${key}=" "$DEPLOY_ENV"; then
    # Fail loudly if the rewrite fails: reporting a write that did not land would lose an id
    # (a pool id, the task-role ARN) that a later phase silently needs. Remove the temp either way.
    tmp="$(mktemp)"
    sed "s|^${key}=.*|${key}=${val}|" "$DEPLOY_ENV" > "$tmp" && mv "$tmp" "$DEPLOY_ENV" \
      || { rm -f "$tmp"; die "could not write $key to $DEPLOY_ENV — record it by hand and re-run"; }
  else
    printf '%s=%s\n' "$key" "$val" >> "$DEPLOY_ENV"
  fi
  export "$key=$val"
  say "  deploy.env: $key=$val"
}

confirm() {   # confirm "message"  — skipped under -y
  [ "$ASSUME_YES" -eq 1 ] && return 0
  printf '\033[33m?\033[0m %s [y/N] ' "$*" >&2
  local a; read -r a; case "$a" in y|Y|yes) return 0 ;; *) die "aborted" ;; esac
}

account_id() { aws sts get-caller-identity --query Account --output text; }

# Refuse to run against a LIVE stack. bring-up.sh rebuilds a TORN-DOWN stack; if
# deploy.env still points at an ACTIVE ECS service, its idempotent "already exists"
# branches would fire mutating calls at live production (e.g. re-applying the
# bucket policy). Only refuse on a POSITIVE ACTIVE result, so a
# genuinely torn-down stack (service gone → describe errors) proceeds normally.
live_stack_guard() {
  [ "$FORCE" -eq 1 ] && { warn "--force: skipping the live-stack guard"; return 0; }
  [ -n "$SERVICE" ] || return 0     # no service configured → nothing live to protect
  local status
  status="$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
    --region "$REGION" --query 'services[0].status' --output text 2>/dev/null || true)"
  if [ "$status" = "ACTIVE" ]; then
    die "deploy.env points at an ACTIVE ECS service ($SERVICE) — this stack is LIVE.
    bring-up.sh rebuilds a torn-down stack and refuses to run mutating commands
    against a live deployment. For day-2 changes use redeploy-*/set-*/pause/resume.
    If you truly intend to re-run bring-up against this stack, pass --force."
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASES
# ─────────────────────────────────────────────────────────────────────────────

phase_preflight() {
  say "Preflight — tools + credentials"
  have aws    || die "aws CLI v2 not found"
  have docker || die "docker not found (needed to build the backend image)"
  have npm    || die "npm not found (needed to build the frontend)"
  docker buildx version >/dev/null 2>&1 || die "docker buildx not available"
  local acct
  acct="$(account_id 2>/dev/null)" || die "AWS credentials not working — run 'aws sso login' (or 'aws login') first"
  say "  account $acct, region $REGION"
}

phase_ecr() {
  say "ECR repo + backend image"
  local acct registry ecr
  acct="$(account_id)"; registry="$acct.dkr.ecr.$REGION.amazonaws.com"; ecr="$registry/$ECR_REPO"
  if aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$REGION" >/dev/null 2>&1; then
    say "  repo $ECR_REPO exists"
  else
    confirm "create ECR repo $ECR_REPO?"
    aws ecr create-repository --repository-name "$ECR_REPO" --region "$REGION" >/dev/null
  fi
  if aws ecr describe-images --repository-name "$ECR_REPO" --image-ids imageTag="$IMAGE_TAG" \
       --region "$REGION" >/dev/null 2>&1; then
    say "  image :$IMAGE_TAG already pushed (skipping build; delete the tag to force a rebuild)"
    return
  fi
  confirm "build + push $ecr:$IMAGE_TAG (linux/arm64)?"
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$registry"
  docker buildx build --platform linux/arm64 -t "$ecr:$IMAGE_TAG" --push "$REPO_ROOT/backend"
}

phase_rds() {
  say "RDS Postgres ($RDS_ID) + DATABASE_URL in SSM"
  if aws rds describe-db-instances --db-instance-identifier "$RDS_ID" --region "$REGION" >/dev/null 2>&1; then
    say "  $RDS_ID already exists"
  else
    confirm "create RDS instance $RDS_ID (db.t4g.micro, private)?"
    # Alphanumeric password: strong and URL-safe (no %-encoding headache in DATABASE_URL).
    local pass; pass="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32 || true)"  # tr exits 141 when head closes the pipe; pipefail would kill the phase
    secret_to_file "$pass"
    aws rds create-db-instance \
      --db-instance-identifier "$RDS_ID" --db-instance-class db.t4g.micro \
      --engine postgres --allocated-storage 20 \
      --master-username kanbanadmin --master-user-password "file://$SECRET_TMP" \
      --db-name kanban --no-publicly-accessible --backup-retention-period 1 \
      --region "$REGION" >/dev/null
    secret_file_done
    say "  waiting for $RDS_ID to become available (several minutes)…"
    aws rds wait db-instance-available --db-instance-identifier "$RDS_ID" --region "$REGION"
    local ep; ep="$(aws rds describe-db-instances --db-instance-identifier "$RDS_ID" \
      --query 'DBInstances[0].Endpoint.Address' --output text --region "$REGION")"
    say "  endpoint: $ep — storing DATABASE_URL in SSM (SecureString)"
    secret_to_file "postgresql://kanbanadmin:$pass@$ep:5432/kanban"
    aws ssm put-parameter --name "$SSM_PARAM" --type SecureString --overwrite \
      --value "file://$SECRET_TMP" --region "$REGION" >/dev/null
    secret_file_done
    unset pass
    return
  fi
  # RDS exists: make sure SSM has the URL. If not, we cannot reconstruct the password.
  if aws ssm get-parameter --name "$SSM_PARAM" --region "$REGION" >/dev/null 2>&1; then
    say "  $SSM_PARAM present"
  else
    warn "RDS exists but $SSM_PARAM is missing and the master password is not recoverable."
    warn "Either reset the RDS master password and write postgresql://…@<endpoint>:5432/kanban to"
    warn "$SSM_PARAM yourself, or delete $RDS_ID and re-run this phase to recreate it clean."
    die  "cannot continue without $SSM_PARAM"
  fi
}

phase_exec_role() {
  say "Task EXECUTION role ($EXEC_ROLE_NAME) — pull image + read SSM secrets"
  if ! aws iam get-role --role-name "$EXEC_ROLE_NAME" >/dev/null 2>&1; then
    confirm "create $EXEC_ROLE_NAME?"
    aws iam create-role --role-name "$EXEC_ROLE_NAME" --assume-role-policy-document \
      '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
    aws iam attach-role-policy --role-name "$EXEC_ROLE_NAME" \
      --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy >/dev/null
  fi
  local acct; acct="$(account_id)"
  # Inline: let the container read the two SecureStrings (DATABASE_URL + Cognito secret).
  # Default aws/ssm KMS key needs no kms:Decrypt (DEPLOY.md §4).
  aws iam put-role-policy --role-name "$EXEC_ROLE_NAME" --policy-name kanban-ssm-read \
    --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["ssm:GetParameters"],"Resource":["arn:aws:ssm:'"$REGION"':'"$acct"':parameter'"$SSM_PARAM"'","arn:aws:ssm:'"$REGION"':'"$acct"':parameter'"$COGNITO_SSM_SECRET_PARAM"'"]}]}' >/dev/null
  say "  ssm:GetParameters attached"
}

phase_task_role() {
  # ONE task role that carries everything the app calls AWS for: Bedrock (agents),
  # ECS Exec (invite/admin ops run inside the container), and Cognito admin (invites).
  # Wiring all three now prevents the documented "half-provisioned account" trap
  # (DEPLOY.md → Cognito C-2: task role created for Bedrock only → AdminCreateUser 403s).
  say "Task ROLE ($TASK_ROLE_NAME) — Bedrock + ECS-Exec + Cognito-admin"
  local acct; acct="$(account_id)"
  if ! aws iam get-role --role-name "$TASK_ROLE_NAME" >/dev/null 2>&1; then
    confirm "create task role $TASK_ROLE_NAME?"
    aws iam create-role --role-name "$TASK_ROLE_NAME" --assume-role-policy-document \
      '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  fi
  # Bedrock: the inference-profile ARN uses $BEDROCK_MODEL_ID as-is; the foundation-model ARN is
  # the same id without its geo prefix (us./eu./apac./…), i.e. the regional model the profile routes to.
  local fm_id="$BEDROCK_MODEL_ID"
  case "$fm_id" in us.*|us-gov.*|eu.*|apac.*|jp.*|au.*|global.*) fm_id="${fm_id#*.}" ;; esac
  aws iam put-role-policy --role-name "$TASK_ROLE_NAME" --policy-name bedrock-invoke \
    --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],"Resource":["arn:aws:bedrock:*:'"$acct"':inference-profile/'"$BEDROCK_MODEL_ID"'","arn:aws:bedrock:*::foundation-model/'"$fm_id"'"]}]}' >/dev/null
  # ECS Exec: ssmmessages actions do NOT support resource scoping → Resource "*" is required.
  aws iam put-role-policy --role-name "$TASK_ROLE_NAME" --policy-name ecs-exec \
    --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["ssmmessages:CreateControlChannel","ssmmessages:CreateDataChannel","ssmmessages:OpenControlChannel","ssmmessages:OpenDataChannel"],"Resource":"*"}]}' >/dev/null
  # Cognito admin — scoped to the pool if we already know it, else pool-wildcard for now
  # (re-run this phase after cognito-pool to tighten it to the real pool ARN).
  local pool_arn="arn:aws:cognito-idp:$REGION:$acct:userpool/*"
  [ -n "${COGNITO_USER_POOL_ID:-}" ] && pool_arn="arn:aws:cognito-idp:$REGION:$acct:userpool/$COGNITO_USER_POOL_ID"
  aws iam put-role-policy --role-name "$TASK_ROLE_NAME" --policy-name cognito-admin \
    --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["cognito-idp:AdminInitiateAuth","cognito-idp:AdminRespondToAuthChallenge","cognito-idp:AdminCreateUser","cognito-idp:AdminDeleteUser","cognito-idp:AdminGetUser"],"Resource":"'"$pool_arn"'"}]}' >/dev/null
  local arn; arn="$(aws iam get-role --role-name "$TASK_ROLE_NAME" --query 'Role.Arn' --output text)"
  set_env BEDROCK_TASK_ROLE_ARN "$arn"
  say "  task role ready: $arn"
}

phase_cognito_pool() {
  say "Cognito user pool + app client"
  if [ -n "${COGNITO_USER_POOL_ID:-}" ] && [ -n "${COGNITO_APP_CLIENT_ID:-}" ]; then
    say "  pool/client already in deploy.env — skipping (delete those lines to recreate)"
    return
  fi
  confirm "create Cognito pool '$POOL_NAME' (invite-only) + app client?"
  local pool; pool="$(aws cognito-idp create-user-pool --region "$REGION" --pool-name "$POOL_NAME" \
    --username-attributes email --auto-verified-attributes email \
    --admin-create-user-config AllowAdminCreateUserOnly=true \
    --policies 'PasswordPolicy={MinimumLength=12,RequireUppercase=true,RequireLowercase=true,RequireNumbers=true,RequireSymbols=false}' \
    --query 'UserPool.Id' --output text)"
  set_env COGNITO_USER_POOL_ID "$pool"
  # 12h token validity is NOT optional — must match auth_cookie_ttl_seconds=43200 (DEPLOY.md).
  local out; out="$(aws cognito-idp create-user-pool-client --region "$REGION" \
    --user-pool-id "$pool" --client-name kanban-web --generate-secret \
    --explicit-auth-flows ALLOW_ADMIN_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH \
    --id-token-validity 12 --access-token-validity 12 --refresh-token-validity 30 \
    --token-validity-units AccessToken=hours,IdToken=hours,RefreshToken=days \
    --query 'UserPoolClient.[ClientId,ClientSecret]' --output text)"
  local client_id client_secret
  client_id="$(printf '%s' "$out" | awk '{print $1}')"
  client_secret="$(printf '%s' "$out" | awk '{print $2}')"
  set_env COGNITO_APP_CLIENT_ID "$client_id"
  say "  storing app-client secret in SSM (never written to deploy.env)"
  secret_to_file "$client_secret"
  aws ssm put-parameter --name "$COGNITO_SSM_SECRET_PARAM" --type SecureString --overwrite \
    --value "file://$SECRET_TMP" --region "$REGION" >/dev/null
  secret_file_done
  unset out client_secret
  say "  pool $pool ready; re-running task-role phase to scope Cognito perms to it"
  phase_task_role
}

phase_budget() {
  say "Cost guard — \$20/mo budget alarm"
  local acct; acct="$(account_id)"
  if aws budgets describe-budget --account-id "$acct" --budget-name "$BUDGET_NAME" >/dev/null 2>&1; then
    say "  budget $BUDGET_NAME exists"; return
  fi
  local email="${EMAIL:-${ALERT_EMAIL:-}}"
  [ -n "$email" ] || { warn "no --email given; skipping budget alarm (create it later via DEPLOY.md §9)"; return; }
  confirm "create \$20/mo budget alerting $email at 80%?"
  aws budgets create-budget --account-id "$acct" \
    --budget '{"BudgetName":"'"$BUDGET_NAME"'","BudgetLimit":{"Amount":"20","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
    --notifications-with-subscribers '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"'"$email"'"}]}]' >/dev/null
}

phase_gate_ecs() {
  # G1 — the ECS Express service. Console-first by AWS guidance (DEPLOY.md §5).
  if [ -n "${SERVICE:-}" ] && aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
       --region "$REGION" --query 'services[0].status' --output text 2>/dev/null | grep -q ACTIVE; then
    say "ECS service $SERVICE is ACTIVE — gate G1 cleared"
    warn "It is PUBLIC and UNAUTHENTICATED from the moment it is healthy — continue to cognito-on,
    or lock the ALB to your IP (DEPLOY.md §7). pause.sh scales it to zero if you must stop."
    return
  fi
  local acct ecr role_arn; acct="$(account_id)"; ecr="$acct.dkr.ecr.$REGION.amazonaws.com/$ECR_REPO:$IMAGE_TAG"
  role_arn="${BEDROCK_TASK_ROLE_ARN:-<run the task-role phase first>}"
  gate "G1 — create the ECS Express service (Console: ECS → Create → Express).

  Image ............... $ecr
  CPU architecture .... ARM64            (MUST match the arm64 image)
  Container port ...... 8000             (not 8080)
  Health check path ... /api/v1/health
  Env (plain) ......... ALLOW_INSECURE_NO_AUTH=true   ← public + no sign-in until cognito-on
  Env (plain) ......... CORS_ORIGINS=https://placeholder.invalid   (real value set later)
  Secret (from SSM) ... DATABASE_URL  ←  $SSM_PARAM
  Execution role ...... $EXEC_ROLE_NAME
  Task role ........... $role_arn
  Compute ............. 0.25 vCPU / 0.5 GB
  Auto scaling ........ min 1 / max 1
  CloudWatch logs ..... enabled
  ★ Enable ECS Exec ... turn ON 'execute command' (needed for invites/admin ops)

  It will stay UNHEALTHY until the DB security group admits it — that's expected
  (DEPLOY.md §6; with the default VPC + default SG the self-referencing rule already
  allows ECS→Postgres, so usually nothing to do).

  THEN capture these into deploy/deploy.env and re-run this script:
    SERVICE=<the service name, has a random suffix>
    ALB_NAME=<the ECS Express gateway ALB name>
    API_HOST=<the *.on.aws host the ALB routes to>
  (each hint's exact 'aws …' command is in deploy/deploy.env.example)"
}

phase_frontend() {
  say "Frontend — private S3 bucket + build + upload"
  local bucket; bucket="kanban-frontend-$(account_id)"
  if aws s3api head-bucket --bucket "$bucket" 2>/dev/null; then
    say "  bucket $bucket exists"
  else
    confirm "create private bucket $bucket?"
    aws s3 mb "s3://$bucket" --region "$REGION" >/dev/null
    aws s3api put-public-access-block --bucket "$bucket" --public-access-block-configuration \
      BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  fi
  confirm "build the SPA and sync (with --delete) to $bucket?"
  say "  building SPA (same-origin, VITE_API_BASE_URL=/api/v1) and syncing"
  ( cd "$REPO_ROOT" && VITE_API_BASE_URL=/api/v1 npm run build )
  aws s3 sync "$REPO_ROOT/dist/" "s3://$bucket" --delete
}

phase_gate_cloudfront() {
  # G2 — CloudFront distribution + the 8f /api/* behavior. Console-first (DEPLOY.md §8c/§8f).
  local bucket; bucket="kanban-frontend-$(account_id)"
  if [ -n "${CLOUDFRONT_DIST_ID:-}" ] && [ -n "${CLOUDFRONT_DOMAIN:-}" ]; then
    say "CloudFront $CLOUDFRONT_DIST_ID known — will apply the S3 bucket policy (8d)"
    confirm "put-bucket-policy on $bucket for distribution $CLOUDFRONT_DIST_ID?"
    local acct; acct="$(account_id)"
    aws s3api put-bucket-policy --bucket "$bucket" --policy \
      '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"cloudfront.amazonaws.com"},"Action":"s3:GetObject","Resource":"arn:aws:s3:::'"$bucket"'/*","Condition":{"StringEquals":{"AWS:SourceArn":"arn:aws:cloudfront::'"$acct"':distribution/'"$CLOUDFRONT_DIST_ID"'"}}}]}'
    say "  bucket policy applied — gate G2 cleared"
    return
  fi
  gate "G2 — create the CloudFront distribution (Console: CloudFront → Create).

  ORIGIN 1 (SPA):
    Origin domain ....... $bucket.s3.$REGION.amazonaws.com
    Origin access ....... Origin access control (create OAC 'kanban-frontend-oac', sigv4)
    Default root object . index.html
    Viewer protocol ..... Redirect HTTP to HTTPS
    Price class ......... PriceClass_100
    Cache policy ........ CachingOptimized
    (SPA routing is handled by deploy/cloudfront/spa-router.js on the DEFAULT behavior —
     do NOT add 403/404→index.html custom error responses; they rewrite /api errors too.)

  ORIGIN 2 (API) — the 8f cutover, in the SAME distribution:
    New origin domain ... $API_HOST        (the *.on.aws service host)
    Protocol ............ HTTPS only
    New behavior ........ Path pattern /api/*   → target = that origin
      Viewer protocol ... Redirect HTTP to HTTPS
      Allowed methods ... GET HEAD OPTIONS PUT POST PATCH DELETE
      Cache policy ...... CachingDisabled                (4135ea2d-6df8-44a3-9df3-4b5a84be39ad)
      Origin req policy . AllViewerExceptHostHeader      (NOT AllViewer — see DEPLOY.md 8f)
      Compress .......... No                             (keeps NDJSON streaming intact)

  Wait for Status=Deployed (~15 min), then capture into deploy/deploy.env and re-run:
    CLOUDFRONT_DIST_ID=<the distribution Id>
    CLOUDFRONT_DOMAIN=<the dxxxx.cloudfront.net domain>
  (re-running applies the S3 bucket policy that lets ONLY this distribution read the bucket)"
}

phase_wire() {
  say "Wiring — lock the ALB to CloudFront, then rebuild the SPA same-origin"
  confirm "run access-cloudfront.sh + redeploy-frontend.sh?"
  ./access-cloudfront.sh
  ./redeploy-frontend.sh
  say "  sanity check:  curl -s https://$CLOUDFRONT_DOMAIN/api/v1/health   # expect {\"status\":\"ok\"}"
  # Locking the ALB closes the direct *.on.aws path, not the CloudFront one: the distribution is
  # public, and auth stays off until the cognito-on phase. That window is real — don't stop here.
  warn "The app is now PUBLIC and still UNAUTHENTICATED — run the cognito-on phase next."
}

phase_cognito_on() {
  say "Turn Cognito ON (auth enforced on every /api/v1 route)"
  warn "The task role's cognito-idp perms + ECS Exec were wired in the task-role phase."
  warn "set-cognito-env.sh reads the secret already stored in SSM by the cognito-pool phase."
  confirm "apply Cognito env + roll the service (auth goes live)?"
  ./set-cognito-env.sh --skip-secret
}

phase_bedrock_on() {
  say "Turn the AI agents ON (AGENT_BACKEND=bedrock, Sonnet 4.5)"
  confirm "flip agent backend to bedrock + roll the service?"
  ./set-agent-backend.sh
}

phase_invite() {
  if [ -z "$EMAIL" ]; then
    say "No --email given; skipping the invite. Later:  ./invite-primary.sh you@example.com \"Your Space\""
    return
  fi
  say "Invite the primary user (PIU): $EMAIL"
  confirm "run invite-primary.sh $EMAIL?"
  ./invite-primary.sh "$EMAIL" "${SPACE:-My Space}"
}

# ─────────────────────────────────────────────────────────────────────────────
# DRIVER
# ─────────────────────────────────────────────────────────────────────────────

ALL_PHASES="preflight ecr rds exec-role task-role cognito-pool budget ecs frontend cloudfront wire cognito-on bedrock-on invite"

run_phase() {
  case "$1" in
    preflight)    phase_preflight ;;
    ecr)          phase_ecr ;;
    rds)          phase_rds ;;
    exec-role)    phase_exec_role ;;
    task-role)    phase_task_role ;;
    cognito-pool) phase_cognito_pool ;;
    budget)       phase_budget ;;
    ecs)          phase_gate_ecs ;;
    frontend)     phase_frontend ;;
    cloudfront)   phase_gate_cloudfront ;;
    wire)         phase_wire ;;
    cognito-on)   phase_cognito_on ;;
    bedrock-on)   phase_bedrock_on ;;
    invite)       phase_invite ;;
    *) die "unknown phase: $1 (phases: $ALL_PHASES)" ;;
  esac
}

# Repo root (this script lives in deploy/, and the cd at the top already put us there).
REPO_ROOT="$(cd .. && pwd)"

main() {
  local single=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -y|--yes)    ASSUME_YES=1 ;;
      --force)     FORCE=1 ;;
      --email)     EMAIL="${2:?--email needs a value}"; shift ;;
      --space)     SPACE="${2:?--space needs a value}"; shift ;;
      list)        printf 'Phases (in order): %s\n' "$ALL_PHASES"; exit 0 ;;
      -h|--help)   sed -n '2,28p' "$(basename "$0")"; exit 0 ;;   # the header comment; cwd is deploy/
      -*)          die "unknown flag: $1" ;;
      *)           single="$1" ;;
    esac
    shift
  done

  live_stack_guard   # refuse to mutate a live stack (unless --force)

  if [ -n "$single" ]; then run_phase "$single"; exit 0; fi

  say "Full bring-up — runs each phase, STOPS (exit 10) at the two console gates."
  say "Re-run after clearing a gate; phases are idempotent."
  for p in $ALL_PHASES; do run_phase "$p"; done
  say "Stack is up. Verify: open https://\${CLOUDFRONT_DOMAIN}/ and sign in."
}

main "$@"
