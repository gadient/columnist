#!/usr/bin/env bash
# See what the Notes-to-Cards agent proposes for a note. Development tool — see try-extract.py.
#
#   bash scripts/try-extract.sh                     # built-in sample note
#   bash scripts/try-extract.sh my-notes.docx       # your own note (.txt or .docx)
#   bash scripts/try-extract.sh --board board_abc   # use a real board's columns/members
#   bash scripts/try-extract.sh --show-prompt       # print the system prompt, call nothing
#
# Needs OPENAI_API_KEY in backend/.env. Costs a fraction of a cent per run.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/backend/.venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "[try-extract] No backend virtualenv. Run scripts/dev-up.sh once first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# The openai SDK is declared in requirements.txt; the check only covers a venv that lacks it.
if ! python -c "import openai" 2>/dev/null; then
  echo "[try-extract] Installing the openai SDK into the backend venv…"
  pip install --quiet openai
fi

exec python "$ROOT_DIR/scripts/try-extract.py" "$@"
