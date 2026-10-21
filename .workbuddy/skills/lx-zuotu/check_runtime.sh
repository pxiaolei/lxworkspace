#!/usr/bin/env sh
set -eu
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd "$SCRIPT_DIR/../../.." && pwd)
export PYTHONDONTWRITEBYTECODE=1
PYTHON_BIN=${PYTHON_BIN:-python3}
exec "$PYTHON_BIN" -B "$PROJECT_ROOT/tools/run_skill.py" init "$@"
