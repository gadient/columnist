#!/usr/bin/env bash
# Throwaway UI for watching the Notes-to-Cards agent:  http://127.0.0.1:8010
#
#   bash scripts/try-extract-ui.sh
#
# Drag in a .txt/.docx (or click a file from agent_test_suite/) and see what the agent proposes,
# what it cited, and what would block approval. Nothing touches a board.
#
# Sanity-check tool, not product. Delete scripts/try-extract-ui.* and nothing else notices.
# Needs OPENAI_API_KEY in backend/.env.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/backend/.venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "[try-extract-ui] No backend virtualenv. Run scripts/dev-up.sh once first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# The openai SDK is declared in requirements.txt; the check only covers a venv that lacks it.
if ! python -c "import openai" 2>/dev/null; then
  echo "[try-extract-ui] Installing the openai SDK into the backend venv…"
  pip install --quiet openai
fi

exec python "$ROOT_DIR/scripts/try-extract-ui.py"
