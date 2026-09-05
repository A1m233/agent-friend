#!/usr/bin/env bash
set -euo pipefail
# Synthetic history, queued-to-running header, geometry/hover and activity tail; no LLM calls.
cd "$(dirname "$0")/../.."
exec uv run python scripts/assistant-phase-desktop/run.py "$@"
