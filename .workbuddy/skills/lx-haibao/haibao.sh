#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
VENV_PY="$SCRIPT_DIR/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "FAIL: lx-haibao venv Python was not found: $VENV_PY" >&2
  echo "Run first: ./check_runtime.sh --install --confirmed" >&2
  exit 1
fi

export PYTHONNOUSERSITE=1
exec "$VENV_PY" "$SCRIPT_DIR/scripts/run_poster_batch.py" "$@"
