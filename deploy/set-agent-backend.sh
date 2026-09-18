#!/usr/bin/env bash
#
# Switch the deployed AI backend. Rolls the ECS service onto a task-def
# revision that sets AGENT_BACKEND + the Bedrock knobs, so the notes-to-cards and
# chat agents run on Amazon Bedrock in production instead of the offline stub.
#
#   ./set-agent-backend.sh                       # turn Bedrock ON (model from deploy.env / default)
#   ./set-agent-backend.sh --model <id>          # override the model / inference-profile id inline
#   ./set-agent-backend.sh --backend offline     # roll BACK to the offline stub (proposes nothing)
#
# What it changes on the container:
#   AGENT_BACKEND=bedrock  BEDROCK_MODEL_ID=<id>  BEDROCK_REGION=<region>
#   BEDROCK_STRUCTURED_OUTPUT=<strategy>          (all plain env — no secrets)
# and, if BEDROCK_TASK_ROLE_ARN is set in deploy.env, the task's taskRoleArn.
#
# NOT scripted (one-time IAM, run deliberately): the task role must carry
# bedrock:InvokeModel + bedrock:InvokeModelWithResponseStream on the model, or
# every extraction 500s with AccessDenied. See docs/DEPLOY.md → "Bedrock —
# turning on the AI agents" for the exact create-role / policy commands, and enable
# model access for the model in the Bedrock console (one-time, per account+region).
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh

BACKEND="bedrock"
while [ $# -gt 0 ]; do
  case "$1" in
    --backend) BACKEND="${2:?--backend needs a value (bedrock|offline)}"; shift ;;
    --model)   BEDROCK_MODEL_ID="${2:?--model needs an id}"; shift ;;
    --region)  BEDROCK_REGION="${2:?--region needs a value}"; shift ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done
case "$BACKEND" in bedrock|offline) ;; *) die "backend must be 'bedrock' or 'offline', got: $BACKEND" ;; esac

# Region boto3 should target for Bedrock. Default to the deploy region so a task
# whose AWS_REGION isn't injected still reaches Bedrock; overridable for cross-region
# inference profiles that live in a different region than the ECS service.
: "${BEDROCK_REGION:=$REGION}"

say "Reading current task definition"
TD_ARN="$(task_def_arn)"
[ "$TD_ARN" != "None" ] && [ -n "$TD_ARN" ] || die "no running task/def found — if paused, run ./resume.sh first"
TD_JSON="$(mktemp)"; TD_NEW="$(mktemp)"; trap 'rm -f "$TD_JSON" "$TD_NEW"' EXIT
aws ecs describe-task-definition --task-definition "$TD_ARN" --region "$REGION" \
  --query 'taskDefinition' --output json > "$TD_JSON"

if [ "$BACKEND" = "bedrock" ]; then
  say "Switching agent backend -> bedrock  (model=$BEDROCK_MODEL_ID  region=$BEDROCK_REGION  strategy=$BEDROCK_STRUCTURED_OUTPUT)"
else
  say "Switching agent backend -> offline  (the stub; agents will propose nothing)"
fi

BACKEND="$BACKEND" \
BEDROCK_MODEL_ID="$BEDROCK_MODEL_ID" \
BEDROCK_REGION="$BEDROCK_REGION" \
BEDROCK_STRUCTURED_OUTPUT="$BEDROCK_STRUCTURED_OUTPUT" \
TASK_ROLE_ARN="${BEDROCK_TASK_ROLE_ARN:-}" \
TD_JSON="$TD_JSON" TD_NEW="$TD_NEW" \
python3 - <<'PY'
import json, os
td = json.load(open(os.environ['TD_JSON']))
for k in ['taskDefinitionArn','revision','status','requiresAttributes',
          'compatibilities','registeredAt','registeredBy','deregisteredAt']:
    td.pop(k, None)

backend = os.environ['BACKEND']
if backend == 'bedrock':
    env_updates = {
        'AGENT_BACKEND': 'bedrock',
        'BEDROCK_MODEL_ID': os.environ['BEDROCK_MODEL_ID'],
        'BEDROCK_REGION': os.environ['BEDROCK_REGION'],
        'BEDROCK_STRUCTURED_OUTPUT': os.environ['BEDROCK_STRUCTURED_OUTPUT'],
    }
else:
    # Roll back to the stub. Flip the switch only; the Bedrock knobs are inert when
    # AGENT_BACKEND=offline, so leaving them costs nothing and keeps the diff small.
    env_updates = {'AGENT_BACKEND': 'offline'}

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

# A task has exactly one task role. Only override it if the operator pinned one in
# deploy.env; otherwise keep whatever the task already uses (e.g. the ECS-Exec role)
# and rely on the operator having attached the Bedrock policy to it.
role = os.environ.get('TASK_ROLE_ARN') or ''
if role:
    td['taskRoleArn'] = role

json.dump(td, open(os.environ['TD_NEW'], 'w'))
PY

# The task role that will be in effect after this deploy — for the reminder below.
EFFECTIVE_ROLE="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('taskRoleArn','') or '')" "$TD_NEW")"

NEW_TD="$(aws ecs register-task-definition --cli-input-json "file://$TD_NEW" \
  --region "$REGION" --query 'taskDefinition.taskDefinitionArn' --output text)"
say "registered: $NEW_TD"

aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
  --task-definition "$NEW_TD" --force-new-deployment --region "$REGION" >/dev/null
say "redeploying — watch with ./status.sh"

if [ "$BACKEND" = "bedrock" ]; then
  if [ -n "$EFFECTIVE_ROLE" ]; then
    warn "Task role in effect: $EFFECTIVE_ROLE"
    warn "It MUST allow bedrock:InvokeModel + bedrock:InvokeModelWithResponseStream on"
    warn "  $BEDROCK_MODEL_ID (and, for a us.* inference profile, the regional model ARNs it spans)."
  else
    warn "This task has NO task role — Bedrock calls will fail with AccessDenied."
    warn "Create one and set BEDROCK_TASK_ROLE_ARN in deploy.env (docs/DEPLOY.md → Bedrock), then re-run."
  fi
  warn "Bedrock InvokeModel is BILLABLE per note/chat. Confirm the budget alarm is live (DEPLOY.md §9)."
fi
