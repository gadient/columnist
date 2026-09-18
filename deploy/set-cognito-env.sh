#!/usr/bin/env bash
#
# Turn Cognito ON in production: store the confidential app-client secret in SSM
# (SecureString) and roll the ECS service onto a task-def revision that carries the
# Cognito settings. Once this deploys, the backend enforces auth on every /api/v1
# route. Non-secret ids come from deploy/deploy.env.
#
#   ./set-cognito-env.sh                 # prompt for the secret (hidden), store + redeploy
#   ./set-cognito-env.sh --skip-secret   # secret already in SSM; just (re)apply env + redeploy
#   ./set-cognito-env.sh --secret-file <path>   # read the secret from a file instead of prompting
#
# The secret is read from a hidden prompt (or --secret-file) — never passed as an
# argument, so it stays out of shell history and the process list. It reaches the aws
# CLI through a private (0600) temp file via `--value file://…`, deleted right after.
#
# One-time prereq: the ECS *task execution role* must be allowed to read the SSM
# parameter (ssm:GetParameters on the param; kms:Decrypt only if it uses a
# customer-managed KMS key — see docs/DEPLOY.md §4). See deploy/README.md.
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh
require_cognito_config

SKIP_SECRET=0; SECRET_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-secret) SKIP_SECRET=1 ;;
    --secret-file) SECRET_FILE="${2:?--secret-file needs a path}"; shift ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

ACCOUNT="$(account_id)"
SECRET_ARN="arn:aws:ssm:$REGION:$ACCOUNT:parameter$COGNITO_SSM_SECRET_PARAM"

# Private (0600) temp files, removed on any exit.
SECRET_TMP="$(mktemp)"; TD_JSON="$(mktemp)"; TD_NEW="$(mktemp)"
trap 'rm -f "$SECRET_TMP" "$TD_JSON" "$TD_NEW"' EXIT

if [ "$SKIP_SECRET" -eq 0 ]; then
  if [ -n "$SECRET_FILE" ]; then
    SECRET_VALUE="$(cat "$SECRET_FILE")"
  else
    printf 'Paste the Cognito app-client secret (hidden): '
    read -rs SECRET_VALUE; echo
  fi
  [ -n "$SECRET_VALUE" ] || die "empty secret — aborting"
  say "Storing secret in SSM: $COGNITO_SSM_SECRET_PARAM (SecureString)"
  # The value goes to put-parameter as file:// from the 0600 temp file, never as an argument,
  # so it does not appear in the process list. printf is a builtin (no argv) and adds no newline.
  printf '%s' "$SECRET_VALUE" > "$SECRET_TMP"
  unset SECRET_VALUE
  aws ssm put-parameter --name "$COGNITO_SSM_SECRET_PARAM" --type SecureString \
    --value "file://$SECRET_TMP" --overwrite --region "$REGION" >/dev/null
  rm -f "$SECRET_TMP"
  say "secret stored"
fi

say "Reading current task definition"
TD_ARN="$(task_def_arn)"
[ "$TD_ARN" != "None" ] && [ -n "$TD_ARN" ] || die "no running task/def found — if paused, run ./resume.sh first"
aws ecs describe-task-definition --task-definition "$TD_ARN" --region "$REGION" \
  --query 'taskDefinition' --output json > "$TD_JSON"

say "Applying Cognito env + secret reference"
COGNITO_REGION="$REGION" \
COGNITO_USER_POOL_ID="$COGNITO_USER_POOL_ID" \
COGNITO_APP_CLIENT_ID="$COGNITO_APP_CLIENT_ID" \
SECRET_ARN="$SECRET_ARN" \
TD_JSON="$TD_JSON" TD_NEW="$TD_NEW" \
python3 - <<'PY'
import json, os
td = json.load(open(os.environ['TD_JSON']))
for k in ['taskDefinitionArn','revision','status','requiresAttributes',
          'compatibilities','registeredAt','registeredBy','deregisteredAt']:
    td.pop(k, None)

# Non-secret env: pool/client ids + cookie hardening (Secure cookies once we're on HTTPS).
env_updates = {
    'COGNITO_REGION': os.environ['COGNITO_REGION'],
    'COGNITO_USER_POOL_ID': os.environ['COGNITO_USER_POOL_ID'],
    'COGNITO_APP_CLIENT_ID': os.environ['COGNITO_APP_CLIENT_ID'],
    'AUTH_COOKIE_SECURE': 'true',
}
secret_ref = {'name': 'COGNITO_APP_CLIENT_SECRET', 'valueFrom': os.environ['SECRET_ARN']}

def upsert(items, key, name, value):
    for it in items:
        if it.get('name') == name:
            it[key] = value
            return
    items.append({'name': name, key: value})

for c in td.get('containerDefinitions', []):
    env = c.setdefault('environment', [])
    for name, value in env_updates.items():
        upsert(env, 'value', name, value)
    # Drop the no-auth startup opt-out (DEPLOY.md → Cognito says to remove it). It is
    # inert once Cognito is set -- enforcement keys on cognito_enabled alone -- but leaving it
    # behind misreports the security posture to anyone reading the task def, and it would let a
    # later config mistake that unsets Cognito boot wide open instead of failing closed.
    c['environment'] = [e for e in env if e.get('name') != 'ALLOW_INSECURE_NO_AUTH']
    secrets = c.setdefault('secrets', [])
    upsert(secrets, 'valueFrom', secret_ref['name'], secret_ref['valueFrom'])

json.dump(td, open(os.environ['TD_NEW'], 'w'))
PY

NEW_TD="$(aws ecs register-task-definition --cli-input-json "file://$TD_NEW" \
  --region "$REGION" --query 'taskDefinition.taskDefinitionArn' --output text)"
say "registered: $NEW_TD"

aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
  --task-definition "$NEW_TD" --force-new-deployment --region "$REGION" >/dev/null
say "redeploying with Cognito ON — watch with ./status.sh"
warn "Auth is now ENFORCED on all /api/v1 routes once this deployment goes healthy."
