#!/usr/bin/env bash
#
# Customize the Cognito invite email (subject + body) that AdminCreateUser sends to a new PIU/SIU —
# adds the product name (Columnist) and the sign-in URL, instead of the bare default.
#
# SAFE UPDATE: Cognito's update-user-pool resets any setting you OMIT to its default. So this reads
# the current pool config and passes it ALL back, injecting only the InviteMessageTemplate — and it
# re-asserts AllowAdminCreateUserOnly=true (invite-only) so that can never be lost.
#
# NOTE: this does NOT change the FROM address (still Cognito's default no-reply@verificationemail.com
# — EmailSendingAccount=COGNITO_DEFAULT). A custom sender needs Amazon SES (verify a domain/email and
# set EmailConfiguration=DEVELOPER); out of scope here.
#
# Usage:
#   ./set-invite-email.sh            # apply, using $CLOUDFRONT_DOMAIN from deploy.env
#   ./set-invite-email.sh --dry-run  # print the exact update JSON, change nothing
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh
require_cognito_config
: "${CLOUDFRONT_DOMAIN:?set CLOUDFRONT_DOMAIN in deploy.env}"

DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
URL="https://$CLOUDFRONT_DOMAIN"

CUR="$(aws cognito-idp describe-user-pool --user-pool-id "$COGNITO_USER_POOL_ID" --region "$REGION" --output json)"

# The transform lives in a temp file — a heredoc inside $() breaks on macOS bash 3.2.
PYF="$(mktemp)"; trap 'rm -f "$PYF"' EXIT
cat > "$PYF" <<'PY'
import json, os, sys
pool = json.loads(sys.argv[1])["UserPool"]
# The mutable settings update-user-pool accepts — copy each one that's currently set so nothing
# resets to a default. (Read-only fields like Id/Arn/SchemaAttributes are intentionally excluded.)
KEEP = ["Policies", "DeletionProtection", "LambdaConfig", "AutoVerifiedAttributes",
        "SmsVerificationMessage", "EmailVerificationMessage", "EmailVerificationSubject",
        "VerificationMessageTemplate", "SmsAuthenticationMessage", "UserAttributeUpdateSettings",
        "MfaConfiguration", "DeviceConfiguration", "EmailConfiguration", "SmsConfiguration",
        "UserPoolTags", "AdminCreateUserConfig", "UserPoolAddOns", "AccountRecoverySetting"]
out = {"UserPoolId": os.environ["POOL"]}
for k in KEEP:
    if pool.get(k) is not None:
        out[k] = pool[k]

acuc = out.setdefault("AdminCreateUserConfig", {})
acuc["AllowAdminCreateUserOnly"] = True   # preserve invite-only explicitly
# Deprecated field that conflicts with Policies.PasswordPolicy.TemporaryPasswordValidityDays on
# update — drop it; the password policy governs temp-password validity.
acuc.pop("UnusedAccountValidityDays", None)

url = os.environ["URL"]
acuc["InviteMessageTemplate"] = {
    "EmailSubject": "Your Columnist invitation",
    # Cognito requires both {username} and {####} (the temp password) somewhere in the body.
    "EmailMessage": (
        "<p>You have been invited to <b>Columnist</b> - a kanban board with an AI assistant.</p>"
        "<p><b>Sign in:</b> <a href=\"" + url + "\">" + url + "</a></p>"
        "<p><b>Username:</b> {username}<br><b>Temporary password:</b> {####}</p>"
        "<p>You will choose your own password the first time you sign in.</p>"
    ),
}
print(json.dumps(out))
PY

INPUT="$(URL="$URL" POOL="$COGNITO_USER_POOL_ID" python3 "$PYF" "$CUR")"

say "Invite email that will be set (FROM stays Cognito default no-reply@):"
printf '%s' "$INPUT" | python3 -c 'import json,sys; t=json.load(sys.stdin)["AdminCreateUserConfig"]["InviteMessageTemplate"]; print("  Subject:", t["EmailSubject"]); print("  Body:"); print("   ", t["EmailMessage"])'

if [ "$DRY" = "1" ]; then
  warn "--dry-run: no change made."
  exit 0
fi

say "Applying to pool $COGNITO_USER_POOL_ID (invite-only preserved)"
aws cognito-idp update-user-pool --cli-input-json "$INPUT" --region "$REGION"
say "Done. The next ./invite-primary.sh will use this email."
