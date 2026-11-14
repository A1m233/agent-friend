#!/usr/bin/env bash
# Real prompt-cache A/B. Run only after explicit authorization.
set -euo pipefail
# Completed assistant replay retains native item boundaries and phases.
cd "$(dirname "$0")/../.."
exec uv run python scripts/prompt-cache-smoke/run.py "$@"
