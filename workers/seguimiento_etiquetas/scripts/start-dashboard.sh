#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PACKAGE_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -x "$PACKAGE_ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$PACKAGE_ROOT/.venv/bin/python"
else
    PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

cd "$PACKAGE_ROOT"
exec "$PYTHON_BIN" -u "$PACKAGE_ROOT/scripts/serve_dashboard.py" "$@"

