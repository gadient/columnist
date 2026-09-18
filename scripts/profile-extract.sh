#!/usr/bin/env bash
# Profile one extraction — where the time goes, and where the model's writing goes.
#
#   bash scripts/profile-extract.sh agent_test_suite/testcase2.txt
#   bash scripts/profile-extract.sh my-notes.docx --runs 3
#
# Separates the model reading your note from the model writing the answer (only visible by
# streaming), breaks the output down by which part of a card it went into, and projects the
# wall clock out to 45 cards. The decision to stream results came from here — re-run it before
# re-deciding that, and after any change to the prompt, the model, or the contract.
#
# Development tool. Needs OPENAI_API_KEY in backend/.env. Costs a fraction of a cent per run.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/backend/.venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "[profile-extract] No backend virtualenv. Run scripts/dev-up.sh once first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# The openai SDK is declared in requirements.txt; the check only covers a venv that lacks it.
# tiktoken is dev-only — an exact token count is the whole point of a profiler, and the product
# never needs it, so it stays out of requirements.txt.
for pkg in openai tiktoken; do
  if ! python -c "import $pkg" 2>/dev/null; then
    echo "[profile-extract] Installing $pkg into the backend venv…"
    pip install --quiet "$pkg"
  fi
done

exec python "$ROOT_DIR/scripts/profile-extract.py" "$@"
