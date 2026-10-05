#!/usr/bin/env bash
set -euo pipefail
# Smoke exercises PDF/native tools and basic spreadsheet/DOCX file workflows offline.
cd "$(dirname "$0")/../.."
exec uv run python scripts/execution-environment/run.py "$@"
