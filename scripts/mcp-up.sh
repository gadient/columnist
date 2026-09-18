#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
VENV_DIR="$BACKEND_DIR/.venv"
RUNTIME_DIR="$ROOT_DIR/data/runtime"
MCP_PID_FILE="$RUNTIME_DIR/mcp_server.pid"

if [ ! -d "$VENV_DIR" ]; then
  echo "[mcp-up] Creating backend virtualenv..."
  python3 -m venv "$VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

if ! python -c "import mcp" >/dev/null 2>&1; then
  echo "[mcp-up] Installing backend dependencies..."
  pip install -r "$BACKEND_DIR/requirements.txt"
fi

if [ ! -f "$BACKEND_DIR/.env" ] && [ -f "$BACKEND_DIR/.env.example" ]; then
  cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
fi

mkdir -p "$RUNTIME_DIR"

cleanup() {
  rm -f "$MCP_PID_FILE"
}
trap cleanup EXIT INT TERM

echo "$$" > "$MCP_PID_FILE"

echo "[mcp-up] Starting MCP server on stdio transport..."
(
  cd "$BACKEND_DIR"
  python -m app.mcp_server
)
