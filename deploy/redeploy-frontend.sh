#!/usr/bin/env bash
#
# Rebuild the SPA, push it to S3, and bust the CloudFront cache.
# Run after any frontend change.
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh
BUCKET="$(bucket)"

# API base is same-origin by default: the SPA and the API are served from ONE
# public origin (CloudFront: default behavior -> S3, `/api/*` behavior -> the ALB).
# A relative base keeps every request on the CloudFront domain, so the BFF
# session/CSRF cookies are first-party (SameSite=Lax works, and JS can read the
# CSRF cookie). This is REQUIRED once Cognito is on. It presumes the CloudFront
# `/api/*` behavior exists — see docs/DEPLOY.md §8f (same-origin cutover).
#
# The cross-origin IP-locked stage (DEPLOY.md §7 + 8a–8e, no auth) has no `/api/*` behavior, so the SPA
# must call the ALB directly. For that only, override with the cross-origin host:
#   API_BASE=https://$API_HOST/api/v1 ./redeploy-frontend.sh
API_BASE="${API_BASE:-/api/v1}"
say "Building frontend (API -> $API_BASE)"
( cd "$REPO_ROOT" && VITE_API_BASE_URL="$API_BASE" npm run build )

say "Syncing dist/ -> s3://$BUCKET"
aws s3 sync "$REPO_ROOT/dist/" "s3://$BUCKET" --delete --region "$REGION"

say "Invalidating CloudFront cache ($CLOUDFRONT_DIST_ID)"
aws cloudfront create-invalidation --distribution-id "$CLOUDFRONT_DIST_ID" \
  --paths '/*' --query 'Invalidation.Status' --output text

say "Done. https://$CLOUDFRONT_DOMAIN"
