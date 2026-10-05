#!/usr/bin/env bash
# 151: lookup_fixture returns the synthetic image; no native attachment upload.
set -euo pipefail
# Uses the current deepseek-flash model; requires explicit --allow-llm.
cd "$(dirname "$0")/../.."
exec uv run python scripts/assistant-phase-smoke/run.py "$@"
