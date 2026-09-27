#!/usr/bin/env bash
set -euo pipefail
# Pass through --scenario long-session for the bounded 144 observation.
cd "$(dirname "$0")/../.."
exec uv run python scripts/context-recovery-smoke/run.py "$@"
