#!/usr/bin/env bash
#
# Lock the ALB (the API front door) to CloudFront ONLY — the correct mode for the same-origin
# topology (DEPLOY.md §8f). This is what you want instead of access-open.sh.
#
# Why this exists: §8f needs CloudFront to reach the ALB, so the your-IP-only lock has to go. The
# obvious move is access-open.sh (0.0.0.0/0) — but that publishes the API to the whole internet,
# and during the §8f cutover auth is still OFF, so there is a window where anyone who finds the
# .on.aws host can read and write every board. This script closes that window: it allows exactly
# CloudFront's origin-facing ranges (an AWS-managed prefix list, kept current by AWS) and nothing
# else. No exposure window, and it stays correct after Cognito is on — it also stops anyone
# bypassing CloudFront to hit the ALB directly.
#
# Idempotent: re-running is a no-op apart from removing any world-open rules it finds.
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh

SG="$(alb_sg)"
PL_NAME="com.amazonaws.global.cloudfront.origin-facing"

say "Resolving managed prefix list $PL_NAME in $REGION"
PL_ID="$(aws ec2 describe-managed-prefix-lists --region "$REGION" \
  --filters "Name=prefix-list-name,Values=$PL_NAME" \
  --query 'PrefixLists[0].PrefixListId' --output text)"
[ "$PL_ID" != "None" ] && [ -n "$PL_ID" ] || die "could not resolve $PL_NAME in $REGION"
say "  -> $PL_ID"

say "Allowing CloudFront on $SG :443"
aws ec2 authorize-security-group-ingress --group-id "$SG" --region "$REGION" --ip-permissions \
  "IpProtocol=tcp,FromPort=443,ToPort=443,PrefixListIds=[{PrefixListId=$PL_ID,Description=cloudfront-origin-facing}]" \
  2>/dev/null || warn "CloudFront rule may already exist (ok)"

# The whole point is that the API is not world-reachable, so clear any world rule a previous
# access-open.sh left behind. Your-IP rules from the optional IP-locked stage (DEPLOY.md §7) are not
# touched: once Cognito is the gate they are unnecessary, and status.sh lists them.
say "Removing world-open :443 if present"
aws ec2 revoke-security-group-ingress --group-id "$SG" --region "$REGION" --ip-permissions \
  "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}]" \
  "IpProtocol=tcp,FromPort=443,ToPort=443,Ipv6Ranges=[{CidrIpv6=::/0}]" \
  2>/dev/null || warn "world rule already absent (ok)"

say "ALB now reachable via CloudFront only."
say "Verify:  curl -s https://\$CLOUDFRONT_DOMAIN/api/v1/health   # expect {\"status\":\"ok\"}"
say "Then:    ./status.sh"
