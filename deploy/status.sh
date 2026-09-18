#!/usr/bin/env bash
#
# One-glance health of the whole stack: service, RDS, CloudFront, and who can
# reach the API.
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh

say "ECS service ($SERVICE)"
aws ecs describe-services --cluster "$CLUSTER" --service "$SERVICE" --region "$REGION" \
  --query 'services[0].{desired:desiredCount,running:runningCount,pending:pendingCount,rollout:deployments[0].rolloutState}' \
  --output table

say "RDS ($RDS_ID)"
aws rds describe-db-instances --db-instance-identifier "$RDS_ID" --region "$REGION" \
  --query 'DBInstances[0].{status:DBInstanceStatus,endpoint:Endpoint.Address}' --output table

say "CloudFront ($CLOUDFRONT_DIST_ID)"
aws cloudfront get-distribution --id "$CLOUDFRONT_DIST_ID" \
  --query 'Distribution.{status:Status,domain:DomainName}' --output table

say "ALB inbound on :443 — who can reach the API"
# Two deliberate choices here:
#   1. Prefix-list rules are included. Querying only IpRanges/Ipv6Ranges silently omits the
#      CloudFront rule (access-cloudfront.sh) — the entry that matters most once /api/* is
#      same-origin — and reads as "only the operator's IP can reach the API", which invites an
#      unnecessary access-open.sh.
#   2. Raw CIDRs are NOT printed. This output gets pasted into issues, chats and screenshares, and
#      a home IP does not belong in any of them. Rules created by these scripts carry a Description
#      (cloudfront-origin-facing, world), which says who far more clearly than the address does.
#      Set STATUS_SHOW_IPS=1 when you genuinely need the values.
_sg_rules="$(aws ec2 describe-security-group-rules --region "$REGION" \
  --filters "Name=group-id,Values=$(alb_sg)" \
  --query 'SecurityGroupRules[?IsEgress==`false` && FromPort==`443`].{desc:Description,v4:CidrIpv4,v6:CidrIpv6,pl:PrefixListId}' \
  --output json)"

# The verdict comes back as the exit code (10/11/12), so it MUST be captured: a bare non-zero
# status here would trip `set -e` and kill the script before the mode line and the URLs below.
MODE_RC=0
SHOW_IPS="${STATUS_SHOW_IPS:-0}" python3 - "$_sg_rules" <<'PY' || MODE_RC=$?
import json, os, sys
rules = json.loads(sys.argv[1] or "[]")
show = os.environ.get("SHOW_IPS") == "1"
world = False
if not rules:
    print("      (no inbound :443 rules — nothing can reach the API)")
for r in rules:
    v4, v6, pl = r.get("v4"), r.get("v6"), r.get("pl")
    if v4 in ("0.0.0.0/0",) or v6 in ("::/0",):
        world = True
    label = r.get("desc") or "(no description)"
    if pl:
        kind, value = "prefix list", pl          # not an address; safe to show
    elif v4:
        kind, value = "IPv4", (v4 if show else "hidden")
    elif v6:
        kind, value = "IPv6", (v6 if show else "hidden")
    else:
        kind, value = "rule", ""
    print(f"      {label:<28} {kind:<12} {value}")
if not show and any(r.get("v4") or r.get("v6") for r in rules):
    print("      (addresses hidden — STATUS_SHOW_IPS=1 to reveal)")
sys.exit(10 if world else (11 if any(r.get("pl") for r in rules) else 12))
PY
case $MODE_RC in
  10) warn "mode: PUBLIC — open to the entire internet" ;;
  11) say  "mode: CloudFront-only (same-origin). Your own IP rules, if any, are extra and harmless." ;;
  *)  say  "mode: private — your IP only. NOTE: CloudFront cannot reach the API in this mode, so the"
      say  "      deployed app's /api/* calls will fail until you run ./access-cloudfront.sh" ;;
esac

echo
say "app  https://$CLOUDFRONT_DOMAIN"
say "api  https://$API_HOST/api/v1/health"
