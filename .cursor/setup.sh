#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 - <<'PY'
import sys

if sys.version_info < (3, 12):
    raise SystemExit(f"Python 3.12+ required, found {sys.version}")

print(f"Python OK: {sys.version.split()[0]}")
PY

python3 -m pip install --user --upgrade pip setuptools wheel
python3 -m pip install --user -e ".[dev]"

python3 -m pytest --version
echo "AI Co-Presenter cloud environment is ready. Run tests with: python3 -m pytest"
