#!/usr/bin/env bash
#
# Set the backend's CORS_ORIGINS and redeploy.
# ECS env vars live in the immutable task definition, so this registers a new
# revision and points the service at it.
#
#   ./update-cors.sh                       # -> https://$CLOUDFRONT_DOMAIN
#   ./update-cors.sh https://app.example.com   # explicit origin
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh

ORIGIN="${1:-https://$CLOUDFRONT_DOMAIN}"
say "Setting CORS_ORIGINS=$ORIGIN on $SERVICE"

TD_ARN="$(task_def_arn)"
[ "$TD_ARN" != "None" ] && [ -n "$TD_ARN" ] || die "no running task / task definition found"
say "current task def: $TD_ARN"

TD_JSON="$(mktemp)"; TD_NEW="$(mktemp)"; trap 'rm -f "$TD_JSON" "$TD_NEW"' EXIT
aws ecs describe-task-definition --task-definition "$TD_ARN" --region "$REGION" \
  --query 'taskDefinition' --output json > "$TD_JSON"

python3 - "$ORIGIN" "$TD_JSON" "$TD_NEW" <<'PY'
import json, sys
origin = sys.argv[1]
td = json.load(open(sys.argv[2]))
for k in ['taskDefinitionArn','revision','status','requiresAttributes',
          'compatibilities','registeredAt','registeredBy','deregisteredAt']:
    td.pop(k, None)
for c in td.get('containerDefinitions', []):
    env = c.setdefault('environment', [])
    if any(e.get('name') == 'CORS_ORIGINS' for e in env):
        for e in env:
            if e.get('name') == 'CORS_ORIGINS':
                e['value'] = origin
    else:
        env.append({'name': 'CORS_ORIGINS', 'value': origin})
json.dump(td, open(sys.argv[3], 'w'))
PY

NEW_TD="$(aws ecs register-task-definition --cli-input-json "file://$TD_NEW" \
  --region "$REGION" --query 'taskDefinition.taskDefinitionArn' --output text)"
say "registered: $NEW_TD"

aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
  --task-definition "$NEW_TD" --force-new-deployment --region "$REGION" >/dev/null
say "redeploying — watch with ./status.sh"
