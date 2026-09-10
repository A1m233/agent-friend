#!/usr/bin/env bash
set -euo pipefail
# --scenario selects tool-loop (default) or nonstream-json; arguments pass through.
cd "$(dirname "$0")/../.."
exec uv run python scripts/responses-smoke/run.py "$@"

# --custom: generic Responses; --custom-chat: generic Chat tool loop (Kimi K2.6).
