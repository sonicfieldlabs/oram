#!/usr/bin/env bash
# oram development check script
# runs lint and tests in the correct environment

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=== oram check ==="

if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
else
    PYTHON_BIN="python"
fi

# lint
echo ""
echo "--- ruff check ---"
"$PYTHON_BIN" -m ruff check src tests
echo "ruff: ok"

# tests
echo ""
echo "--- pytest ---"
"$PYTHON_BIN" -m pytest -q
echo "pytest: ok"

# optional: pip-audit
if "$PYTHON_BIN" -m pip_audit --help &>/dev/null; then
    echo ""
    echo "--- pip-audit ---"
    "$PYTHON_BIN" -m pip_audit
    echo "pip-audit: ok"
fi

echo ""
echo "=== all checks passed ==="
