#!/usr/bin/env bash
#
# Set (or clear) plain environment variables on the running ECS service. Registers a new
# task-def revision with the changes applied and rolls the service onto it. Everything
# else on the task — image, secrets, other env, task role — is preserved.
#
#   ./set-env.sh FEEDBACK_S3_BUCKET=kanban-feedback-123456789012
#   ./set-env.sh FOO=bar BAZ=qux            # several at once, one deploy
#   ./set-env.sh --unset FOO                # remove a variable entirely
#   ./set-env.sh --show                     # print current env, change nothing
#
# WHY THIS EXISTS. `set-agent-backend.sh` knows only the Bedrock knobs, so every other
# variable meant editing a task definition by hand — which is how a stack ends up with
# settings nobody can account for. This is the general case; set-agent-backend.sh stays
# as the documented path for the agent switch specifically, because it also checks the
# task role and warns about model access.
#
# NOT FOR SECRETS. These land in the task definition in plain text, readable by anyone
# with ecs:DescribeTaskDefinition. Secrets belong in SSM Parameter Store and are wired
# through `secrets` rather than `environment` — see docs/DEPLOY.md §4.
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh

SHOW_ONLY=false
UNSET_MODE=false
PAIRS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --show)  SHOW_ONLY=true ;;
    --unset) UNSET_MODE=true ;;
    -*)      die "unknown flag: $1" ;;
    *)       PAIRS+=("$1") ;;
  esac
  shift
done

say "Reading current task definition"
TD_ARN="$(effective_task_def)"
[ -n "$TD_ARN" ] && [ "$TD_ARN" != "None" ] || die "no task definition found — if paused, run ./resume.sh first"
TD_JSON="$(mktemp)"; TD_NEW="$(mktemp)"; trap 'rm -f "$TD_JSON" "$TD_NEW"' EXIT
aws ecs describe-task-definition --task-definition "$TD_ARN" --region "$REGION" \
  --query 'taskDefinition' --output json > "$TD_JSON"

if [ "$SHOW_ONLY" = true ]; then
  TD_JSON="$TD_JSON" python3 -c "
import json, os
td = json.load(open(os.environ['TD_JSON']))
for c in td.get('containerDefinitions', []):
    for e in sorted(c.get('environment', []), key=lambda x: x.get('name','')):
        print(f\"  {e['name']}={e['value']}\")
    names = [s.get('name') for s in c.get('secrets', [])]
    if names:
        print('  (from SSM: ' + ', '.join(names) + ')')
"
  exit 0
fi

[ ${#PAIRS[@]} -gt 0 ] || die "nothing to do — pass KEY=VALUE (or --unset KEY, or --show)"

# Validate before touching anything: a typo'd pair should not produce a half-applied revision.
for p in "${PAIRS[@]}"; do
  if [ "$UNSET_MODE" = true ]; then
    case "$p" in *=*) die "--unset takes bare names, not KEY=VALUE: $p" ;; esac
  else
    case "$p" in *=*) ;; *) die "expected KEY=VALUE, got: $p" ;; esac
  fi
done

if [ "$UNSET_MODE" = true ]; then
  say "Removing: ${PAIRS[*]}"
else
  say "Setting: ${PAIRS[*]}"
fi

PAIRS_JOINED="$(printf '%s\n' "${PAIRS[@]}")" UNSET_MODE="$UNSET_MODE" \
TD_JSON="$TD_JSON" TD_NEW="$TD_NEW" python3 - <<'PY'
import json, os

td = json.load(open(os.environ['TD_JSON']))
for k in ['taskDefinitionArn','revision','status','requiresAttributes',
          'compatibilities','registeredAt','registeredBy','deregisteredAt']:
    td.pop(k, None)

pairs = [p for p in os.environ['PAIRS_JOINED'].splitlines() if p.strip()]
unset = os.environ['UNSET_MODE'] == 'true'

for c in td.get('containerDefinitions', []):
    env = c.setdefault('environment', [])
    if unset:
        names = set(pairs)
        c['environment'] = [e for e in env if e.get('name') not in names]
        continue
    for pair in pairs:
        name, value = pair.split('=', 1)
        for e in env:
            if e.get('name') == name:
                e['value'] = value
                break
        else:
            env.append({'name': name, 'value': value})

json.dump(td, open(os.environ['TD_NEW'], 'w'))
PY

NEW_TD="$(aws ecs register-task-definition --cli-input-json "file://$TD_NEW" \
  --region "$REGION" --query 'taskDefinition.taskDefinitionArn' --output text)"
say "registered: $NEW_TD"

aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
  --task-definition "$NEW_TD" --force-new-deployment --region "$REGION" >/dev/null
say "redeploying — takes 10-15 min to drain. Watch with ./status.sh"
warn "Both the old and new revision serve traffic during the drain, so a request in that"
warn "window may still hit the previous settings."
