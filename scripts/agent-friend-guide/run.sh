#!/usr/bin/env bash
set -euo pipefail
# verify is read-only; non-default --guide-repo requires --guide-commit.
# Success and failure include the checkout identity; no fetch or update is performed.
cd "$(dirname "$0")/../.."

exec uv run python scripts/agent-friend-guide/run.py "$@"
