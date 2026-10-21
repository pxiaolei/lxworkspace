#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN=python
else
  echo "FAIL: Python 3 was not found. Install Python 3, then rerun this command." >&2
  exit 127
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/scripts/check_runtime.py" "$@"
