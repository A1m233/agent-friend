#!/usr/bin/env bash
# Real-LLM context compaction smoke. Run only after explicit authorization.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python scripts/context-compaction-smoke/run.py "$@"
