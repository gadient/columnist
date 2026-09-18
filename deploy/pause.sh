#!/usr/bin/env bash
#
# Pause the stack to save cost: scale the service to 0 and stop RDS.
# Nothing is destroyed. Bring it back with ./resume.sh.
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh

say "Lowering autoscaling floor to 0 (so desired-count 0 sticks)"
aws application-autoscaling register-scalable-target --service-namespace ecs \
  --resource-id "service/$CLUSTER/$SERVICE" --scalable-dimension ecs:service:DesiredCount \
  --min-capacity 0 --max-capacity 1 --region "$REGION" 2>/dev/null || warn "no scalable target (ok)"

say "Scaling $SERVICE to 0"
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
  --desired-count 0 --region "$REGION" >/dev/null

say "Stopping RDS $RDS_ID"
aws rds stop-db-instance --db-instance-identifier "$RDS_ID" --region "$REGION" \
  --query 'DBInstance.DBInstanceStatus' --output text || warn "RDS may already be stopped (ok)"

say "Paused. (RDS auto-restarts after ~7 days if left stopped.) Resume with ./resume.sh"
