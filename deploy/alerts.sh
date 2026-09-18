#!/usr/bin/env bash
#
# alerts.sh up|down — the PUSH-alerting layer: get emailed when errors spike or
# spend crosses the budget, instead of having to run monitor.sh to look.
#
# Everything it creates is a named, deletable resource. `down` removes exactly
# what `up` created, idempotently — so this whole layer comes down in one command
# at project end (it is step 1 of the Full teardown in docs/DEPLOY.md).
#
#   ALERT_EMAIL=you@example.com ./alerts.sh up     # create + subscribe (confirm the email once)
#   ./alerts.sh down                               # remove all of it
#   ./alerts.sh status                             # show what exists
#
# Design choices:
#   * A *paused* stack emits no logs/metrics, so the error alarm would go
#     INSUFFICIENT_DATA. `--treat-missing-data notBreaching` keeps pause SILENT.
#   * The budget notifies by EMAIL directly (no SNS topic policy needed); the
#     error alarm needs SNS (alarms cannot email on their own).
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh

ALERT_EMAIL="${ALERT_EMAIL:-}"
TOPIC_NAME="${ALERT_TOPIC_NAME:-kanban-alerts}"
FILTER_NAME="kanban-backend-errors"
METRIC_NS="Kanban/Backend"
METRIC_NAME="ErrorCount"
ALARM_NAME="kanban-backend-error-rate"
BUDGET_NAME="${BUDGET_NAME:-kanban-monthly}"
ERROR_THRESHOLD="${ERROR_THRESHOLD:-10}"   # errors in a 5-min window before it pages you
# Tracebacks (Python exceptions) OR any 5xx access-log line. Over-inclusive is fine —
# the threshold sits above the noise floor. Validate changes with `aws logs test-metric-filter`.
FILTER_PATTERN='?Traceback ?"HTTP/1.1\" 5"'

# awslogs group is ECS-Express-generated (…-<suffix>) — read it off the live container definition.
log_group() {
  aws ecs describe-task-definition --task-definition "$(effective_task_def)" --region "$REGION" \
    --query "taskDefinition.containerDefinitions[0].logConfiguration.options.\"awslogs-group\"" \
    --output text 2>/dev/null | sed 's/^None$//'
}

topic_arn() { echo "arn:aws:sns:$REGION:$(account_id):$TOPIC_NAME"; }

_budget_note() {  # $1 = threshold percent — the notification key used by both create and delete
  echo "NotificationType=ACTUAL,ComparisonOperator=GREATER_THAN,Threshold=$1,ThresholdType=PERCENTAGE"
}

up() {
  [ -n "$ALERT_EMAIL" ] || die "set ALERT_EMAIL=you@example.com  (AWS emails it a one-time confirmation link)"
  local lg arn acct
  lg="$(log_group)"; acct="$(account_id)"
  [ -n "$lg" ] || die "could not resolve the backend log group — is the service created?"

  say "SNS topic ($TOPIC_NAME) + email subscription"
  arn="$(aws sns create-topic --name "$TOPIC_NAME" --region "$REGION" --query 'TopicArn' --output text)"
  # Subscribe only if this email isn't already on the topic (subscribe re-sends a confirmation).
  if ! aws sns list-subscriptions-by-topic --topic-arn "$arn" --region "$REGION" \
        --query 'Subscriptions[].Endpoint' --output text 2>/dev/null | tr '\t' '\n' | grep -qxF "$ALERT_EMAIL"; then
    aws sns subscribe --topic-arn "$arn" --protocol email --notification-endpoint "$ALERT_EMAIL" \
      --region "$REGION" >/dev/null
    warn "confirm the subscription: AWS just emailed $ALERT_EMAIL a 'Confirm subscription' link — click it, or alarms won't reach you."
  else
    echo "   $ALERT_EMAIL already subscribed"
  fi

  say "Log metric filter ($FILTER_NAME on $lg)"
  aws logs put-metric-filter --log-group-name "$lg" --filter-name "$FILTER_NAME" \
    --filter-pattern "$FILTER_PATTERN" \
    --metric-transformations \
      "metricName=$METRIC_NAME,metricNamespace=$METRIC_NS,metricValue=1,defaultValue=0" \
    --region "$REGION"

  say "Alarm ($ALARM_NAME: >$ERROR_THRESHOLD errors / 5 min)"
  aws cloudwatch put-metric-alarm --alarm-name "$ALARM_NAME" \
    --alarm-description "Backend tracebacks / 5xx spiked — see CloudWatch Logs $lg" \
    --namespace "$METRIC_NS" --metric-name "$METRIC_NAME" \
    --statistic Sum --period 300 --evaluation-periods 1 --threshold "$ERROR_THRESHOLD" \
    --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching \
    --alarm-actions "$arn" --ok-actions "$arn" --region "$REGION"

  say "Budget email notifications ($BUDGET_NAME at 80% + 100% of \$$(_budget_limit))"
  local t
  for t in 80 100; do
    aws budgets create-notification --account-id "$acct" --budget-name "$BUDGET_NAME" \
      --notification "$(_budget_note "$t")" \
      --subscribers "SubscriptionType=EMAIL,Address=$ALERT_EMAIL" --region "$REGION" 2>/dev/null \
      && echo "   added $t% notification" \
      || echo "   $t% notification already present (or budget '$BUDGET_NAME' missing)"
  done

  echo
  say "Done. Pull view: ./monitor.sh   ·   Remove everything: ./alerts.sh down"
}

down() {
  local arn acct lg t
  arn="$(topic_arn)"; acct="$(account_id)"; lg="$(log_group || true)"

  say "Deleting alarm ($ALARM_NAME)"
  aws cloudwatch delete-alarms --alarm-names "$ALARM_NAME" --region "$REGION" 2>/dev/null || true

  if [ -n "$lg" ]; then
    say "Deleting metric filter ($FILTER_NAME)"
    aws logs delete-metric-filter --log-group-name "$lg" --filter-name "$FILTER_NAME" --region "$REGION" 2>/dev/null || true
  fi

  say "Removing budget notifications ($BUDGET_NAME)"
  for t in 80 100; do
    aws budgets delete-notification --account-id "$acct" --budget-name "$BUDGET_NAME" \
      --notification "$(_budget_note "$t")" --region "$REGION" 2>/dev/null \
      && echo "   removed $t%" || echo "   no $t% notification"
  done

  say "Deleting SNS topic (removes the email subscription too)"
  aws sns delete-topic --topic-arn "$arn" --region "$REGION" 2>/dev/null || true

  echo; say "alerts torn down."
}

status() {
  local arn lg
  arn="$(topic_arn)"; lg="$(log_group || true)"
  say "SNS topic $TOPIC_NAME"
  aws sns list-subscriptions-by-topic --topic-arn "$arn" --region "$REGION" \
    --query 'Subscriptions[].{endpoint:Endpoint,confirmed:SubscriptionArn}' --output table 2>/dev/null \
    || echo "   (no topic)"
  say "Alarm $ALARM_NAME"
  aws cloudwatch describe-alarms --alarm-names "$ALARM_NAME" --region "$REGION" \
    --query 'MetricAlarms[0].{state:StateValue,threshold:Threshold}' --output table 2>/dev/null || echo "   (none)"
  say "Metric filter $FILTER_NAME"
  [ -n "$lg" ] && aws logs describe-metric-filters --log-group-name "$lg" \
    --filter-name-prefix "$FILTER_NAME" --region "$REGION" \
    --query 'metricFilters[].filterName' --output text 2>/dev/null || echo "   (none)"
  say "Budget $BUDGET_NAME notifications"
  aws budgets describe-notifications-for-budget --account-id "$(account_id)" --budget-name "$BUDGET_NAME" \
    --region "$REGION" --query 'Notifications[].Threshold' --output text 2>/dev/null || echo "   (none)"
}

_budget_limit() {
  aws budgets describe-budget --account-id "$(account_id)" --budget-name "$BUDGET_NAME" \
    --query 'Budget.BudgetLimit.Amount' --output text 2>/dev/null || echo "?"
}

case "${1:-}" in
  up)     up ;;
  down)   down ;;
  status) status ;;
  *)      die "usage: [ALERT_EMAIL=you@example.com] ./alerts.sh up|down|status" ;;
esac
