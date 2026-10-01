#!/usr/bin/env bash
set -euo pipefail
# prepare/scroll/lock-compare observe; real/isolated serve/long-runtime require separate LLM authorization.
# offline sends on disposable copies, blocks network, and reports derived storage costs.
cd "$(dirname "$0")/../.."
exec uv run python scripts/session-latency/run.py "$@"
