#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

if ! python3 -c "import copresenter" >/dev/null 2>&1; then
  echo "Package 'copresenter' is not installed. Running setup..."
  ./.cursor/setup.sh
fi

if ! python3 -c "import uvicorn" >/dev/null 2>&1; then
  echo "Uvicorn is not installed. Running setup..."
  ./.cursor/setup.sh
fi

if python3 - <<PY
import socket
import sys

port = int("${PORT}")
host = "${HOST}"
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind((host if host != "0.0.0.0" else "127.0.0.1", port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
then
  :
else
  echo "Port ${PORT} is already in use."
  echo "The API may already be running. Try:"
  echo "  curl http://127.0.0.1:${PORT}/health"
  echo "  open http://127.0.0.1:${PORT}/docs"
  echo
  echo "To use another port:"
  echo "  PORT=8001 ./.cursor/run-api.sh"
  exit 1
fi

echo "Starting API on http://${HOST}:${PORT}"
exec python3 -m uvicorn copresenter.app:app --reload --host "$HOST" --port "$PORT"
