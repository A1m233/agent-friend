#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
# Four real requests, at most five, through isolated usage/rollout smoke.
exec uv run python scripts/context-usage-smoke/run.py "$@"
