#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Project: Columnist"
echo
echo "Top-level:"
ls -1 | sed 's/^/  - /'
echo

echo "Key application structure:"
if command -v tree >/dev/null 2>&1; then
  tree -L 3 -I 'node_modules|dist|.git|__pycache__|.venv|data' \
    backend src docs scripts 2>/dev/null || true
else
  find backend src docs scripts \
    \( -name node_modules -o -name dist -o -name .git -o -name __pycache__ -o -name .venv -o -name data \) -prune -o \
    -type f \
    ! -name '*.pyc' \
    ! -name '.env' \
    -print | sort
fi
echo

echo "Reference docs:"
echo "  - README.md"
echo "  - CONTRIBUTING.md"
echo "  - docs/README.md"
echo "  - docs/design/architecture.md"
