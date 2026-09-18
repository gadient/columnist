#!/usr/bin/env bash
# Run the backend test suite.
#
#   bash scripts/test.sh                 # everything
#   bash scripts/test.sh -k schema       # one file's worth
#   bash scripts/test.sh -x -q           # stop at the first failure
#
# Fast, deterministic, free — nothing here calls a model. Model behaviour is graded against the
# labelled corpus gate (90% precision / 80% recall), which is a separate, scored, paid run.
# See backend/tests/README.md.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/backend/.venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "[test] No backend virtualenv. Run scripts/dev-up.sh once first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Dev-only: the product doesn't need a test runner to serve requests.
for pkg in pytest httpx; do
  if ! python -c "import $pkg" 2>/dev/null; then
    echo "[test] Installing $pkg into the backend venv (dev-only dependency)…"
    pip install --quiet "$pkg"
  fi
done

cd "$ROOT_DIR/backend"
exec python -m pytest tests "$@"
