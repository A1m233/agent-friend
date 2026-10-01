#!/usr/bin/env bash
set -euo pipefail
# prepare/scroll/lock-compare observe; real/isolated serve/long-runtime require separate LLM authorization.
# offline sends only on disposable fixture copies and blocks network connections.
cd "$(dirname "$0")/../.."
exec uv run python scripts/session-latency/run.py "$@"
