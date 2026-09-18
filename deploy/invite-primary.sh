#!/usr/bin/env bash
#
# Invite someone as a Primary user (PIU): each invitee gets their own
# private Instance. One command does both sides:
#
#   1. DB   — seed a fresh Instance + this email as its sole PIU (inside the running
#             container, via ECS Exec, so it reaches RDS). Idempotent + cap-guarded.
#   2. Cognito — AdminCreateUser so they can log in; Cognito emails a temp password.
#
# Usage:
#   ./invite-primary.sh user@example.com                 # space name derived from email
#   ./invite-primary.sh user@example.com "Ada's Space"   # explicit space name
#
# Prereqs: deploy/deploy.env has COGNITO_USER_POOL_ID + COGNITO_APP_CLIENT_ID, the
# service was created with --enable-execute-command, and the SSM Session Manager
# plugin is installed locally (see deploy/README.md).
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh
require_cognito_config

EMAIL="${1:-}"; SPACE="${2:-}"
[ -n "$EMAIL" ] || die "usage: ./invite-primary.sh <email> [\"Space Name\"]"

# backend_exec ships a single command STRING to the container, where it is parsed as a shell
# command. The arguments below are wrapped in single quotes there, so a literal apostrophe in a
# space name ("O'Brien's Space") closes the quote early and the remote parser fails with
# "EOF found when expecting closing quote" — with no hint that the name was the cause.
# Escape by the standard single-quote trick: ' becomes '\'' . sed, not bash ${//}, for bash 3.2.
_shq() { printf "%s" "$1" | sed "s/'/'\\\\''/g"; }

say "1/2 · Seeding private Instance + PIU for $EMAIL (in-container)"
if [ -n "$SPACE" ]; then
  backend_exec "python -m app.admin invite-primary '$(_shq "$EMAIL")' --space '$(_shq "$SPACE")'"
else
  backend_exec "python -m app.admin invite-primary '$(_shq "$EMAIL")'"
fi

say "2/2 · Creating the Cognito account (emails a temporary password)"
# Idempotent: a re-invite of an existing username is not an error we want to fail on.
ERR_FILE="$(mktemp)"; trap 'rm -f "$ERR_FILE"' EXIT
if aws cognito-idp admin-create-user \
    --user-pool-id "$COGNITO_USER_POOL_ID" \
    --username "$EMAIL" \
    --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
    --desired-delivery-mediums EMAIL \
    --region "$REGION" >/dev/null 2>"$ERR_FILE"; then
  say "Cognito account created — temp password emailed to $EMAIL"
else
  if grep -q 'UsernameExistsException' "$ERR_FILE"; then
    warn "Cognito account already exists for $EMAIL — left as-is (no new email sent)"
  else
    cat "$ERR_FILE" >&2
    die "admin-create-user failed"
  fi
fi
rm -f "$ERR_FILE"

say "Done. $EMAIL can sign in at https://$CLOUDFRONT_DOMAIN with the temp password."
