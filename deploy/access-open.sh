#!/usr/bin/env bash
#
# Open the ALB (API front door) to the whole internet — public mode.
# ONLY safe once app auth is on (Cognito configured / ALLOW_INSECURE_NO_AUTH removed).
# See DEPLOY.md -> "Access modes".
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh
SG="$(alb_sg)"

# In the same-origin topology (DEPLOY.md 8f) you almost certainly want access-cloudfront.sh
# instead: CloudFront is the only thing that needs to reach the ALB, so world-open buys nothing
# and exposes the API host directly.
warn "Prefer ./access-cloudfront.sh — it allows CloudFront only, with no public exposure."

# Fail closed. A bare prompt is not enough: opening first and turning Cognito on afterwards leaves
# the API world-open with no auth for the length of the rollout, so refuse unless auth is enforced.
# The check reads the LIVE task def, not deploy.env: what is actually deployed is what matters.
if auth_is_on; then
  say "Auth check: Cognito configured on the deployed task def — auth is enforced."
  insecure_flag_set && warn "note: ALLOW_INSECURE_NO_AUTH is still set (inert now, but stale — worth clearing)"
else
  warn "Auth is NOT enforced on the deployed service. Cognito env on the live task def:"
  warn "  COGNITO_REGION        = '$(backend_env COGNITO_REGION)'"
  warn "  COGNITO_USER_POOL_ID  = '$(backend_env COGNITO_USER_POOL_ID)'"
  warn "  COGNITO_APP_CLIENT_ID = '$(backend_env COGNITO_APP_CLIENT_ID)'"
  warn "(all three must be set — that is exactly the backend's cognito_enabled rule)"
  warn "Opening now would publish every board to the internet, readable AND writable."
  warn "Turn auth on first:  ./set-cognito-env.sh   (then re-run this)"
  [ "${I_ACCEPT_PUBLIC_NO_AUTH:-}" = "true" ] \
    || die "refusing to open a no-auth service (override: I_ACCEPT_PUBLIC_NO_AUTH=true)"
  warn "I_ACCEPT_PUBLIC_NO_AUTH=true set — proceeding with an UNAUTHENTICATED public API."
fi

warn "This opens the API to the ENTIRE internet."
printf 'Type OPEN to continue: '; read -r ans
[ "$ans" = "OPEN" ] || die "aborted"

say "Adding world-open :443 to $SG"
aws ec2 authorize-security-group-ingress --group-id "$SG" --region "$REGION" --ip-permissions \
  "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0,Description=world}]" \
  "IpProtocol=tcp,FromPort=443,ToPort=443,Ipv6Ranges=[{CidrIpv6=::/0,Description=world}]" \
  2>/dev/null || warn "world rule may already exist (ok)"

say "Open to the world. (Your narrow rules are left in place — harmless.) Verify with ./status.sh"
