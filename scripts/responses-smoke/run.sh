#!/usr/bin/env bash
set -euo pipefail
# Completed assistant replay retains native item boundaries and phases.
cd "$(dirname "$0")/../.."
exec uv run python scripts/responses-smoke/run.py "$@"
