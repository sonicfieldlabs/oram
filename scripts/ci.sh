#!/usr/bin/env bash
# oram CI — lint + test
# §6.3: run this locally or in CI to verify the codebase

set -euo pipefail

echo "═══ oram ci ═══"

# lint (if ruff is installed)
if command -v ruff &>/dev/null; then
  echo "── ruff check ──"
  ruff check src tests
  echo "✓ lint clean"
else
  echo "⚠ ruff not installed, skipping lint"
fi

# tests
echo "── pytest ──"
python -m pytest tests -q --tb=short

echo "═══ ci passed ═══"
