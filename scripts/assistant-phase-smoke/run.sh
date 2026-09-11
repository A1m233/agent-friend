#!/usr/bin/env bash
set -euo pipefail
# Uses the current deepseek-flash model; requires explicit --allow-llm.
cd "$(dirname "$0")/../.."
exec uv run python scripts/assistant-phase-smoke/run.py "$@"
