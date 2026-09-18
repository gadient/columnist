# Shared config for the kanban deploy ops scripts.
# SOURCE this (don't run it):  . ./config.sh
#
# Deployment-specific IDs (service name, ALB name, CloudFront id/domain, API
# host) are NOT hardcoded here — they live in a git-ignored deploy.env. Copy the
# template once:
#
#     cp deploy/deploy.env.example deploy/deploy.env   # then fill it in
#
# Any value can also be overridden inline, e.g.  REGION=us-west-2 ./status.sh

# ---- non-sensitive defaults (generic resource names; safe to commit) ---------
: "${REGION:=us-east-1}"
: "${CLUSTER:=default}"
: "${ECR_REPO:=kanban-backend}"
: "${RDS_ID:=kanban-db}"
: "${SSM_PARAM:=/kanban/DATABASE_URL}"
# SSM SecureString that holds the confidential Cognito app-client secret.
: "${COGNITO_SSM_SECRET_PARAM:=/kanban/COGNITO_APP_CLIENT_SECRET}"
# Bedrock agent backend — used by set-agent-backend.sh. Model + strategy default to the
# combination that passed the extraction quality bar (Nova failed it); override per deploy.env.
# Not secret.
: "${BEDROCK_MODEL_ID:=us.anthropic.claude-sonnet-4-5-20250929-v1:0}"
: "${BEDROCK_STRUCTURED_OUTPUT:=tool}"

# Repo root (this file lives in deploy/).
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$_HERE/.." && pwd)"

# ---- tiny logging helpers (defined early; used by require_config) -------------
say()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m!!\033[0m %s\n' "$*" >&2; exit 1; }

# ---- load deployment-specific IDs (git-ignored) ------------------------------
if [ -f "$_HERE/deploy.env" ]; then
  set -a; . "$_HERE/deploy.env"; set +a
fi

# Fail early and clearly if the deployment-specific IDs aren't set anywhere.
require_config() {
  local v missing=()
  for v in SERVICE ALB_NAME CLOUDFRONT_DIST_ID CLOUDFRONT_DOMAIN API_HOST; do
    [ -n "${!v:-}" ] || missing+=("$v")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    die "Missing config: ${missing[*]}
    -> copy deploy/deploy.env.example to deploy/deploy.env and fill it in
       (or export the values inline). See deploy/README.md."
  fi
}

# ---- derived lookups (never hardcoded) ---------------------------------------
account_id() { aws sts get-caller-identity --query Account --output text; }
bucket()     { echo "kanban-frontend-$(account_id)"; }

alb_sg() {
  aws elbv2 describe-load-balancers --names "$ALB_NAME" --region "$REGION" \
    --query 'LoadBalancers[0].SecurityGroups[0]' --output text
}

# ECS Express hides these on the service object, so read them off the running task.
running_task() {
  aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" --region "$REGION" \
    --query 'taskArns[0]' --output text
}
task_def_arn() {
  aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$(running_task)" --region "$REGION" \
    --query 'tasks[0].taskDefinitionArn' --output text
}
container_name() {
  aws ecs describe-task-definition --task-definition "$(task_def_arn)" --region "$REGION" \
    --query 'taskDefinition.containerDefinitions[0].name' --output text
}

# Task-definition family. ECS Express names it "<cluster>-<service>" (e.g.
# default-kanban-backend-ab12). Overridable in deploy.env if that convention ever changes.
: "${TASK_FAMILY:=$CLUSTER-$SERVICE}"

# The task def to read config from, whether or not a task is currently running.
#
# Three tiers, because each of the first two has a real blind spot:
#   1. the running task            — nothing running when paused or after a rolled-back deploy
#   2. the service object          — ECS Express does NOT expose taskDefinition here; it is
#                                    always null, so this tier silently yields nothing on Express
#   3. newest ACTIVE revision of the FAMILY — works with zero tasks and no service data
#
# Tier 3 is family-scoped on purpose. A global "latest task definition" lookup
# (`list-task-definitions --sort DESC` with no --family-prefix) returns the most recently
# registered task def in the ACCOUNT, which can easily belong to something else entirely —
# rolling the service onto an unrelated container is far worse than failing loudly. The family
# is verified against the resolved ARN below, since --family-prefix is a prefix match and would
# also accept "<family>-v2".
effective_task_def() {
  local td resolved_family
  td="$(task_def_arn 2>/dev/null || true)"

  if [ "$td" = "None" ] || [ -z "$td" ]; then
    td="$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
      --region "$REGION" --query 'services[0].taskDefinition' --output text 2>/dev/null || true)"
  fi

  if [ "$td" = "None" ] || [ -z "$td" ]; then
    td="$(aws ecs list-task-definitions --region "$REGION" --status ACTIVE \
      --family-prefix "$TASK_FAMILY" --sort DESC --max-results 1 \
      --query 'taskDefinitionArns[0]' --output text 2>/dev/null || true)"
    # Guard the prefix match: the ARN tail is "<family>:<revision>".
    resolved_family="${td##*/}"; resolved_family="${resolved_family%:*}"
    if [ "$td" != "None" ] && [ -n "$td" ] && [ "$resolved_family" != "$TASK_FAMILY" ]; then
      die "refusing task def '$td' — family '$resolved_family' != expected '$TASK_FAMILY'"
    fi
  fi

  [ "$td" != "None" ] && [ -n "$td" ] \
    || die "no task def found (running task, service, or family '$TASK_FAMILY') — is the service created?"
  echo "$td"
}

# Read one plain env var off the deployed container definition. Empty if unset.
# Only reads `environment` — never `secrets`, so this cannot print an SSM value.
backend_env() {
  aws ecs describe-task-definition --task-definition "$(effective_task_def)" --region "$REGION" \
    --query "taskDefinition.containerDefinitions[0].environment[?name=='$1'].value | [0]" \
    --output text 2>/dev/null | sed 's/^None$//'
}

# Is app-level auth actually enforced on the DEPLOYED service?
#
# Mirrors the backend's real rule exactly (main.py): enforcement keys on `cognito_enabled`, which
# is region + pool id + client id ALL set (config.py). ALLOW_INSECURE_NO_AUTH is deliberately NOT
# consulted -- it only gates the *startup* guard in main.py, letting the app boot with Cognito
# unset. Once Cognito is configured, auth is enforced whether or not that flag is still hanging
# around. Treating it as a kill switch here would wrongly report auth as off on a service where
# the flag was left behind (set-cognito-env.sh removes it; a hand-edited task def may not).
#
# Reads the live task def rather than deploy.env: deploy.env is what you intend, the task def is
# what is actually running.
auth_is_on() {
  local v
  for v in COGNITO_REGION COGNITO_USER_POOL_ID COGNITO_APP_CLIENT_ID; do
    [ -n "$(backend_env "$v")" ] || return 1
  done
  return 0
}

# True if the no-auth startup opt-out is still set on the deployed service. Harmless once Cognito
# is configured, but stale and misleading -- worth clearing.
insecure_flag_set() {
  case "$(printf '%s' "$(backend_env ALLOW_INSECURE_NO_AUTH)" | tr '[:upper:]' '[:lower:]')" in
    true|1|yes) return 0 ;;
  esac
  return 1
}

# ---- run an admin command inside the live backend container ------------------
# The application database (RDS) lives in the VPC, so DB-side ops run *inside* the
# running task via ECS Exec — the container already has DATABASE_URL and network
# reach. Requires the service to have been created / updated with
# `--enable-execute-command` and the Session Manager plugin installed locally
# (one-time; see deploy/README.md). `python -m app.admin` is the ops CLI.
#
#   backend_exec "python -m app.admin list-instances"
# NOTE ON FAILURE DETECTION. `aws ecs execute-command` exits 0 when the SESSION opened, even if the
# command inside the container never ran — e.g. a quoting error that makes the remote shell fail
# with "EOF found when expecting closing quote". `set -e` then sees success, and a wrapper such as
# invite-primary.sh would go on to create a Cognito account for a user it has NOT seeded in the
# database — a half-provisioned account whose only symptom is a 403 at login. So inspect the
# transcript for the failure banners ECS Exec prints, and fail loudly.
_EXEC_FAILURE_PATTERNS='Unable to start command|Failed to parse commands input|SessionManagerPlugin is not found|execute command was not enabled|The execute command failed|Traceback \(most recent call last\)'

backend_exec() {
  local task container out rc
  task="$(running_task)"
  [ "$task" != "None" ] && [ -n "$task" ] || die "no running task — if paused, run ./resume.sh first"
  container="$(container_name)"
  say "ecs exec -> ${task##*/} ($container)"

  out="$(mktemp "${TMPDIR:-/tmp}/columnist-exec.XXXXXX")"
  # The transcript holds whatever the session printed (rosters, member emails), so clean it up on
  # Ctrl-C as well. INT/TERM only: an EXIT trap here would clobber the caller's own (invite-primary
  # and delete-primary each set one).
  trap 'rm -f "$out"; exit 130' INT TERM
  # tee keeps the session visible while capturing it; PIPESTATUS is bash-3.2 safe.
  set +e
  aws ecs execute-command --cluster "$CLUSTER" --task "$task" --container "$container" \
    --region "$REGION" --interactive --command "$*" 2>&1 | tee "$out"
  rc=${PIPESTATUS[0]}
  set -e
  trap - INT TERM

  if [ "$rc" -ne 0 ]; then
    rm -f "$out"
    die "ecs execute-command failed (exit $rc)"
  fi
  if grep -qE "$_EXEC_FAILURE_PATTERNS" "$out"; then
    warn "the remote command did not run cleanly — see the transcript above"
    rm -f "$out"
    die "aborting before any follow-up step (this is what half-provisions an account)"
  fi
  rm -f "$out"
}

# Cognito scripts need the pool + app-client ids (non-secret). The secret itself is
# never read here — it lives only in SSM (set once via set-cognito-env.sh).
require_cognito_config() {
  local v missing=()
  for v in COGNITO_USER_POOL_ID COGNITO_APP_CLIENT_ID; do
    [ -n "${!v:-}" ] || missing+=("$v")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    die "Missing Cognito config: ${missing[*]}
    -> add them to deploy/deploy.env (see deploy.env.example)."
  fi
}

require_config
