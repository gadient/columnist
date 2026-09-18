#!/usr/bin/env bash
# Probe Bedrock for model access + structured-output support. See bedrock-probe.py for the details.
#
#   bash scripts/bedrock-probe.sh --region us-east-1 list
#   bash scripts/bedrock-probe.sh --region us-east-1 probe --model us.anthropic.claude-sonnet-4-5-20250929-v1:0
#   bash scripts/bedrock-probe.sh --region us-east-1 probe --model <id> --strategy both --raw
#
# READ-ONLY against AWS: this only lists models and runs inference. It never mutates your account.
# Credentials/region come from your environment the normal boto3 way (AWS_PROFILE / AWS_REGION /
# `aws sso login`). Nothing is hardcoded.
#
# Why its own venv: the probe wants a boto3 recent enough for the Feb-2026 structured-outputs API
# (an older one rejects `outputConfig`), independent of the version the app pins in
# requirements.txt. This installs a *recent* boto3 into an isolated, git-ignored venv so your app's
# pinned dependency is never touched.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROBE_VENV="$ROOT_DIR/scripts/.bedrock-probe-venv"

if [ ! -d "$PROBE_VENV" ]; then
  echo "[bedrock-probe] Creating an isolated venv with a recent boto3 (one-time)…" >&2
  python3 -m venv "$PROBE_VENV"
  # shellcheck disable=SC1091
  source "$PROBE_VENV/bin/activate"
  pip install --quiet --upgrade pip
  # [crt] pulls awscrt, required by the SSO/Identity Center "login" credential provider.
  pip install --quiet --upgrade "boto3[crt]"
else
  # shellcheck disable=SC1091
  source "$PROBE_VENV/bin/activate"
fi

# Ensure awscrt is present (required by the SSO/Identity Center "login" credential provider).
# The boto3[crt] extra doesn't always resolve it, so install the concrete package directly.
# Idempotent: no-op once installed, self-heals venvs created before this line existed.
if ! python -c "import awscrt" 2>/dev/null; then
  echo "[bedrock-probe] Installing awscrt (SSO credential provider dependency)…" >&2
  pip install --quiet awscrt
fi

echo "[bedrock-probe] boto3 $(python -c 'import boto3; print(boto3.__version__)') / botocore $(python -c 'import botocore; print(botocore.__version__)')" >&2

exec python "$ROOT_DIR/scripts/bedrock-probe.py" "$@"
