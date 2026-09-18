#!/usr/bin/env bash
#
# Rebuild the backend image, push it to ECR under a new tag, and roll the ECS
# service onto it (new task-def revision + force deploy). Existing env vars and
# secrets (CORS_ORIGINS, DATABASE_URL, ...) are preserved — only the image changes.
#
#   ./redeploy-backend.sh v2      # build+push :v2 and deploy it
#   ./redeploy-backend.sh         # auto-tag from the git short sha
#
# Reads the current task def via effective_task_def (config.sh), so it also works while the
# service is paused; the new revision starts when the service is next scaled up.
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh

TAG="${1:-$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || date +%s)}"
ACCOUNT="$(account_id)"
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
ECR="$REGISTRY/$ECR_REPO"

say "Logging Docker in to ECR"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

say "Building + pushing $ECR:$TAG (linux/arm64)"
docker buildx build --platform linux/arm64 -t "$ECR:$TAG" --push "$REPO_ROOT/backend"

say "Reading current task definition"
# effective_task_def (config.sh) handles the no-running-task case: paused, or recovering from a
# rolled-back deploy. It does not rely on the SERVICE's taskDefinition (ECS Express always reports
# it as null); it falls back to the newest ACTIVE revision of the task family.
TD_ARN="$(effective_task_def)"
TD_JSON="$(mktemp)"; TD_NEW="$(mktemp)"; trap 'rm -f "$TD_JSON" "$TD_NEW"' EXIT
aws ecs describe-task-definition --task-definition "$TD_ARN" --region "$REGION" \
  --query 'taskDefinition' --output json > "$TD_JSON"

say "Swapping image -> :$TAG (env/secrets preserved)"
python3 - "$ECR:$TAG" "$TD_JSON" "$TD_NEW" <<'PY'
import json, sys
image = sys.argv[1]
td = json.load(open(sys.argv[2]))
for k in ['taskDefinitionArn','revision','status','requiresAttributes',
          'compatibilities','registeredAt','registeredBy','deregisteredAt']:
    td.pop(k, None)
for c in td.get('containerDefinitions', []):
    c['image'] = image
# ECS Express exposes no CPU-architecture field and defaults task launches to
# X86_64, but our image is arm64 (built --platform linux/arm64 above). Pin the
# platform so Fargate launches arm64 directly, instead of failing on X86_64 and
# auto-"overriding" to ARM64 in a slow retry loop.
td['runtimePlatform'] = {'cpuArchitecture': 'ARM64', 'operatingSystemFamily': 'LINUX'}
json.dump(td, open(sys.argv[3], 'w'))
PY

NEW_TD="$(aws ecs register-task-definition --cli-input-json "file://$TD_NEW" \
  --region "$REGION" --query 'taskDefinition.taskDefinitionArn' --output text)"
say "registered: $NEW_TD"

aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
  --task-definition "$NEW_TD" --force-new-deployment --region "$REGION" >/dev/null
say "rolling out image :$TAG — watch with ./status.sh"
