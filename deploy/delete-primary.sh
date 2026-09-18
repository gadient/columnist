#!/usr/bin/env bash
#
# Delete a PIU (Primary user) — the inverse of invite-primary.sh. In one command:
#
#   1. DB   — tear down the PIU's entire Instance (all boards/workspaces/members/data) inside the
#             running container via ECS Exec (so it reaches RDS). Also deletes each member's Cognito
#             login if the task role permits (best-effort, reported).
#   2. Cognito — delete the PIU's login here with the operator's own credentials, so removal does
#             not depend on the task role having cognito-idp:AdminDeleteUser.
#
# The PIU can no longer sign in. Re-invite later with ./invite-primary.sh (a fresh Instance).
#
# Usage:  ./delete-primary.sh user@example.com
#
# Destructive + irreversible — requires a typed-email confirmation.
# Prereqs: same as invite-primary.sh (deploy.env Cognito ids, --enable-execute-command, SSM plugin).
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh
require_cognito_config

EMAIL="${1:-}"
[ -n "$EMAIL" ] || die "usage: ./delete-primary.sh <email>"

warn "This PERMANENTLY deletes the Instance owned by $EMAIL — every board, card, workspace and"
warn "member in it — and deletes the Cognito logins. This cannot be undone."
printf "Type the email to confirm: " >&2
read -r reply
[ "$reply" = "$EMAIL" ] || die "aborted — confirmation did not match."

# Escape a literal apostrophe for the single-quoted in-container command (bash 3.2; see invite-primary).
_shq() { printf "%s" "$1" | sed "s/'/'\\\\''/g"; }

say "1/2 · Tearing down the Instance + members (in-container)"
backend_exec "python -m app.admin delete-primary '$(_shq "$EMAIL")' --yes"

say "2/2 · Deleting the PIU's Cognito login (idempotent)"
ERR_FILE="$(mktemp)"; trap 'rm -f "$ERR_FILE"' EXIT
if aws cognito-idp admin-delete-user \
    --user-pool-id "$COGNITO_USER_POOL_ID" --username "$EMAIL" \
    --region "$REGION" >/dev/null 2>"$ERR_FILE"; then
  say "Cognito account deleted for $EMAIL"
else
  if grep -q 'UserNotFoundException' "$ERR_FILE"; then
    say "Cognito account already gone for $EMAIL (ok)"
  else
    cat "$ERR_FILE" >&2
    warn "admin-delete-user failed for $EMAIL — delete it manually if it persists"
  fi
fi
rm -f "$ERR_FILE"

say "Done. Re-invite with:  ./invite-primary.sh $EMAIL"
