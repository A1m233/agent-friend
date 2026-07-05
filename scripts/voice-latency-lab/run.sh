#!/usr/bin/env bash
# Voice latency lab automation.
set -euo pipefail
cd "$(dirname "$0")/../.."

exec uv run python scripts/voice-latency-lab/run.py "$@"
