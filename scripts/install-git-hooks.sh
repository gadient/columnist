#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$ROOT_DIR/.githooks/pre-commit" ]; then
  echo "[hooks] Missing $ROOT_DIR/.githooks/pre-commit"
  exit 1
fi

chmod +x "$ROOT_DIR/.githooks/pre-commit"
git -C "$ROOT_DIR" config core.hooksPath .githooks

echo "[hooks] Installed git hooks path: .githooks"
echo "[hooks] pre-commit secret scan is now enabled."
