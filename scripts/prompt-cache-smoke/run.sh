#!/usr/bin/env bash
# Real prompt-cache A/B. Run only after explicit authorization.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python scripts/prompt-cache-smoke/run.py "$@"
