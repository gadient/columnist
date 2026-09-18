#!/usr/bin/env bash
# Score the notes-to-cards extractor against the labelled corpus gate (90% precision / 80% recall).
# See corpus-eval.py.
#
#   bash scripts/corpus-eval.sh                  # all fixtures, 5 runs each (cached)
#   bash scripts/corpus-eval.sh --fixture text3 --verbose
#   bash scripts/corpus-eval.sh --refresh        # ignore cache, re-call the model
#
# Runs on AGENT_BACKEND: openai / anthropic need their API key in backend/.env, bedrock needs AWS
# credentials. Costs money on a cold cache; free once cached.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/backend/.venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "[corpus-eval] No backend virtualenv. Run scripts/dev-up.sh once first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# The openai SDK is declared in requirements.txt; the check only covers a venv that lacks it.
# PyYAML is dev-only — it reads the corpus labels, and the product never needs it.
for pkg in openai anthropic yaml; do
  if ! python -c "import $pkg" 2>/dev/null; then
    dep=$([ "$pkg" = "yaml" ] && echo pyyaml || echo "$pkg")
    echo "[corpus-eval] Installing $dep into the backend venv…"
    pip install --quiet "$dep"
  fi
done

# Scoring on Bedrock: boto3 is pinned in requirements.txt already; awscrt is only needed for the
# SSO/Identity Center credential provider, so install it lazily and only for the Bedrock backend.
if [ "${AGENT_BACKEND:-}" = "bedrock" ] && ! python -c "import awscrt" 2>/dev/null; then
  echo "[corpus-eval] Installing awscrt into the backend venv (Bedrock SSO credential dependency)…"
  pip install --quiet awscrt
fi

exec python "$ROOT_DIR/scripts/corpus-eval.py" "$@"
