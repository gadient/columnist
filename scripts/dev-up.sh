#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
VENV_DIR="$BACKEND_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "[dev-up] Creating backend virtualenv..."
  python3 -m venv "$VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

if ! python -c "import fastapi" >/dev/null 2>&1; then
  echo "[dev-up] Installing backend dependencies..."
  pip install -r "$BACKEND_DIR/requirements.txt"
fi

if [ ! -f "$BACKEND_DIR/.env" ] && [ -f "$BACKEND_DIR/.env.example" ]; then
  cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
fi

cleanup() {
  echo ""
  echo "[dev-up] Shutting down..."
  if [ -n "${BACKEND_PID:-}" ] && kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${FRONTEND_PID:-}" ] && kill -0 "$FRONTEND_PID" >/dev/null 2>&1; then
    kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
  wait >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM

echo "[dev-up] Starting backend on http://127.0.0.1:8000 ..."
(
  cd "$BACKEND_DIR"
  "${VENV_DIR}/bin/uvicorn" app.main:app --reload --host 127.0.0.1 --port 8000
) &
BACKEND_PID=$!

echo "[dev-up] Starting frontend on http://localhost:5173 ..."
(
  cd "$ROOT_DIR"
  npm run dev
) &
FRONTEND_PID=$!

# wait -n requires bash 4.3+; poll until either process exits
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done
