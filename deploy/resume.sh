#!/usr/bin/env bash
#
# Bring the stack back after ./pause.sh: start RDS and scale the service to 1.
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh

say "Starting RDS $RDS_ID (takes a few minutes to become 'available')"
aws rds start-db-instance --db-instance-identifier "$RDS_ID" --region "$REGION" \
  --query 'DBInstance.DBInstanceStatus' --output text || warn "RDS may already be starting/available (ok)"

say "Restoring autoscaling floor + desired count to 1"
aws application-autoscaling register-scalable-target --service-namespace ecs \
  --resource-id "service/$CLUSTER/$SERVICE" --scalable-dimension ecs:service:DesiredCount \
  --min-capacity 1 --max-capacity 1 --region "$REGION" 2>/dev/null || warn "no scalable target (ok)"
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
  --desired-count 1 --region "$REGION" >/dev/null

say "Resuming. The app stays unhealthy until RDS is 'available'. Watch with ./status.sh"
